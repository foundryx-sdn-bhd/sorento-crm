"""Phase C - the ingest/read HTTP surface itself.

``test_master_ingest.py`` exercises ``MasterIngestService`` directly. That left
the *route* untested, and the gap hid a real defect: every ``AppException`` in
``ingest.py`` passed its message positionally into ``status_code`` and then
supplied ``status_code=`` again, so each guard raised ``TypeError: got multiple
values for argument 'status_code'`` and surfaced as a bare 500. The ESB was
therefore told "Sorento is broken" for what were plain client mistakes -- an
oversized batch and a malformed body are things the caller can fix, and a 500
tells it to retry forever instead.

These tests assert the contract at the HTTP boundary, where the ESB meets it:

  unknown entity      -> 404
  malformed body      -> 422
  batch over the cap  -> 413
  no company anchor   -> 422
  a good batch        -> 200 with a per-record verdict

Every body carries a ``companyCode`` since group A1: the six masters are
partitioned per company, so a push that names none has no correct destination.
The order of the guards is part of the contract and is asserted here -- a
malformed body is still 422 ``INVALID_BODY`` and an oversized batch still 413,
because a caller cannot fix an anchor in a request that never parsed.

Plus ``?dry_run=true``: a preview that resolves every record exactly as a real
ingest would -- adoption matching included -- and writes nothing. The assertion
that matters there is the *absence* of writes, not the presence of the flag.
"""
from __future__ import annotations

import inspect
import uuid
from decimal import Decimal

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.main  # noqa: F401  isort:skip  (registers models/handlers; see test_integration_auth_dependencies)

from app.api.v1.external import ingest as ingest_module
from app.api.v1.external.permissions import require_external_permission_for_path
from app.database import get_db
from app.dependencies import get_external_api_user
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import _value_changed
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import unique_code

from app.database import engine


@pytest.fixture()
def db():
    """A Postgres session whose work is discarded at teardown.

    Postgres and not sqlite for the same reason ``test_master_ingest`` gives:
    the behaviour under test is SAVEPOINT and rollback semantics, which sqlite
    does not share.

    **Not ``_pg_fixture.pg_session``, and the difference is load-bearing here.**
    That helper builds its Session with SQLAlchemy's default
    ``join_transaction_mode="conditional_savepoint"``. Because the fixture's
    connection is in a transaction but not in a SAVEPOINT, that resolves to
    ``rollback_only`` -- under which ``session.rollback()`` rolls back the
    *entire outer transaction*, test setup included.

    The code under test calls ``session.rollback()``: it is how the dry run
    writes nothing. Under ``rollback_only`` the tests below would still have
    passed, but for the wrong reason -- "the dry run wrote nothing" would be
    indistinguishable from "the fixture discarded the whole test". For the
    single most important assertion in this feature, that ambiguity is
    unacceptable.

    ``create_savepoint`` gives the Session its own SAVEPOINT, so ``rollback()``
    discards exactly the session's own work and leaves the surrounding
    transaction intact -- which is what happens in production, where ``get_db``
    hands out a Session that owns its transaction outright.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db):
    """The two routers mounted exactly as ``external/__init__.py`` mounts them.

    Mounting the real guard rather than a bare router keeps the entity-permission
    lookup in the path the test exercises; ``external_permissions_granted``
    then states plainly that *authorization* is out of scope here (it has its own
    suite) without silently removing the check.
    """
    api = FastAPI()
    api.include_router(
        ingest_module.ingest_router,
        prefix="/ingest",
        dependencies=[
            Depends(require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS))
        ],
    )
    api.include_router(
        ingest_module.read_router,
        prefix="/read",
        dependencies=[
            Depends(require_external_permission_for_path(ingest_module.READ_PERMISSIONS))
        ],
    )

    def _override_db():
        yield db

    api.dependency_overrides[get_db] = _override_db
    api.dependency_overrides[get_external_api_user] = lambda: {
        "id": str(uuid.uuid4()),
        "integration_id": None,
        "integration_name": "test-esb",
    }

    # raise_server_exceptions=False so an unhandled error arrives as a 500
    # response instead of blowing up the test -- which is how the argument-order
    # bug presented, and what these tests must be able to observe.
    with external_permissions_granted():
        yield TestClient(api, raise_server_exceptions=False)


@pytest.fixture()
def company_code(db):
    """The company anchor every ingest/read body now carries (group A1).

    Read from the database, not spelled out. The incumbent's code is ``SRT`` on
    this checkout's copy of production, which is data rather than contract - a
    test that hardcoded it would fail on a database seeded any other way, and
    would be asserting about the seed instead of about the route.
    """
    return db.execute(
        text("SELECT code FROM companies WHERE id = :id"), {"id": DEFAULT_COMPANY_ID}
    ).scalar()


def _wh(code=None, name="Main", ref=None, **extra):
    code = code or unique_code("WH")
    return {"source_ref": ref or f"ZZT-DK-{code}", "code": code, "name": name, **extra}


def _warehouse_count(db, code):
    return db.execute(
        text("SELECT count(*) FROM warehouses WHERE warehouse_code = :c"), {"c": code}
    ).scalar()


def _reference_count(db, source_ref):
    return db.execute(
        text("SELECT count(*) FROM integration_references WHERE source_ref = :r"),
        {"r": source_ref},
    ).scalar()


class TestEntityRouting:
    def test_unknown_entity_is_404(self, client, company_code):
        res = client.post("/ingest/unicorns", json={"companyCode": company_code, "records": []})
        assert res.status_code == 404

    def test_unknown_read_entity_is_404(self, client, company_code):
        assert client.post(
            "/read/unicorns",
            json={"companyCode": company_code, "source_refs": []},
        ).status_code == 404

    def test_the_handler_guard_itself_raises_a_404(self):
        """``_entity`` direct, not through the route.

        The mount-level permission guard also 404s an unmapped entity, and it
        runs first -- so a route test alone cannot tell whether ``_entity``
        works. Since it is the last line of defence should the permission map
        and ``ENTITY_SPECS`` ever drift apart, assert it on its own.
        """
        from app.services.error_handler import AppException

        with pytest.raises(AppException) as excinfo:
            ingest_module._entity("unicorns")
        assert excinfo.value.status_code == 404

    def test_a_supported_entity_passes_the_guard(self, client):
        assert ingest_module._entity("warehouses") == "warehouses"


class TestRequestValidation:
    def test_missing_records_array_is_422(self, client):
        # A client mistake, and 500 would tell the ESB to retry a body that can
        # never succeed.
        res = client.post("/ingest/warehouses", json={})
        assert res.status_code == 422

    def test_non_array_records_is_422(self, client, company_code):
        assert client.post(
            "/ingest/warehouses",
            json={"companyCode": company_code, "records": "nope"},
        ).status_code == 422

    def test_oversized_batch_is_413(self, client, company_code):
        # AC-AC-20: refused outright rather than silently truncated.
        batch = [_wh(code=f"ZZT-BULK-{i}") for i in range(ingest_module.MAX_BATCH + 1)]
        res = client.post(
            "/ingest/warehouses",
            json={"companyCode": company_code, "records": batch},
        )
        assert res.status_code == 413

    def test_a_batch_at_the_cap_is_not_refused(self, client):
        # The boundary is inclusive; off-by-one here would reject a batch the
        # ESB was told it could send. The batch carries no company anchor, so
        # the request stops at the anchor guard, the check that runs right
        # AFTER the size check: reaching it proves the size check let a full
        # batch through, without ingesting 1,000 warehouses to show it (59 s
        # on a 4-core runner; CI-LEAN).
        batch = [_wh(code=f"ZZT-EDGE-{i}") for i in range(ingest_module.MAX_BATCH)]
        res = client.post("/ingest/warehouses", json={"records": batch})
        assert res.status_code == 422, res.text[:300]
        assert res.json()["detail"]["code"] == "COMPANY_ANCHOR_REQUIRED"

    def test_an_ingest_with_no_company_anchor_is_422(self, client, db):
        # AC-A1-1 at the route: the body parses, so the anchor guard is what
        # refuses it, and nothing is written.
        record = _wh()
        res = client.post("/ingest/warehouses", json={"records": [record]})

        assert res.status_code == 422
        # These routers are mounted on a bare FastAPI here, so an AppException
        # surfaces through Starlette's default handler as {"detail": {...}}. The
        # real app registers its own handler and returns that inner object flat;
        # tests/test_external_company_anchor_scope.py asserts on THAT shape.
        assert res.json()["detail"]["code"] == "COMPANY_ANCHOR_REQUIRED"
        assert _warehouse_count(db, record["code"]) == 0

    def test_a_body_that_never_parsed_is_refused_before_the_anchor(self, client):
        # Both guards answer 422 and only the code tells them apart. Order
        # matters to the caller: it cannot supply an anchor for a request whose
        # records array is missing, so the body complaint has to come first.
        res = client.post("/ingest/warehouses", json={})
        assert res.json()["detail"]["code"] == "INVALID_BODY"

    def test_a_read_with_no_company_anchor_is_422(self, client):
        res = client.post("/read/warehouses", json={"source_refs": ["ZZT-DK-NOPE"]})
        assert res.status_code == 422
        assert res.json()["detail"]["code"] == "COMPANY_ANCHOR_REQUIRED"

    def test_missing_source_refs_array_is_422(self, client):
        assert client.post("/read/warehouses", json={}).status_code == 422

    def test_oversized_read_batch_is_413(self, client, company_code):
        refs = [f"ZZT-DK-{i}" for i in range(ingest_module.MAX_BATCH + 1)]
        assert client.post(
            "/read/warehouses",
            json={"companyCode": company_code, "source_refs": refs},
        ).status_code == 413


class TestHappyPath:
    def test_a_good_batch_is_200_with_a_per_record_verdict(self, client, db, company_code):
        record = _wh(name="Depot")
        res = client.post(
            "/ingest/warehouses",
            json={"companyCode": company_code, "records": [record]},
        )

        assert res.status_code == 200
        body = res.json()
        assert body["summary"]["created"] == 1
        assert body["records"][0]["source_ref"] == record["source_ref"]
        assert body["records"][0]["outcome"] == "created"
        assert _warehouse_count(db, record["code"]) == 1

    def test_an_invalid_record_is_still_200_with_a_failed_verdict(self, client, company_code):
        # AC-AC-15: a batch is not a transaction. A non-2xx would leave the ESB
        # unable to tell which records landed.
        res = client.post(
            "/ingest/warehouses",
            json={
                "companyCode": company_code,
                "records": [{"source_ref": "ZZT-DK-BAD", "code": "ZZT-BAD"}],
            },
        )
        assert res.status_code == 200
        assert res.json()["summary"]["failed"] == 1

    def test_read_returns_current_state_for_a_known_reference(self, client, db, company_code):
        record = _wh(name="Depot")
        client.post("/ingest/warehouses", json={"companyCode": company_code, "records": [record]})

        res = client.post(
            "/read/warehouses",
            json={"companyCode": company_code, "source_refs": [record["source_ref"]]},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["records"][0]["name"] == "Depot"
        assert body["not_found"] == []


def _called_from(func_name: str, filename_suffix: str) -> bool:
    """True when the immediate caller is `func_name` in a file ending
    `filename_suffix` - lets a monkeypatched `db.flush()` fail only for the
    ONE explicit call a fix targets, not an unrelated autoflush or the
    route's own final `db.commit()`. Mirrors the identical helper in
    `tests/test_ingest_review_fixes.py`."""
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    if caller is None:
        return False
    code = caller.f_code
    return code.co_name == func_name and code.co_filename.endswith(filename_suffix)


class TestSEC3GenericMasterExceptionsAreSanitized:
    """Fix round 2: `master_ingest_service`'s generic exception path never
    echoed `str(exc)` to the caller, unlike the documents/shipping-order
    services SEC3 already sanitized in review round 1 - grepped for
    `str(exc)` and found this one still leaking."""

    def test_a_forced_insert_failure_never_leaks_sql_or_uuids_into_the_verdict(
        self, client, db, company_code, monkeypatch
    ):
        # D18 (ingest-parity-standardisation UAC AC-P0-6) moved the CREATE
        # path from a raw `db.execute(INSERT ...)` to an ORM
        # `db.add(row); db.flush()` (`_insert`), so the company-stamp, audit
        # and embedding listeners fire. The surgical target follows: `flush`,
        # scoped to the one call `_insert` itself makes so the route's own
        # reads/writes elsewhere in the request are unaffected.
        leaking = (
            "INSERT INTO customers (id, customer_code) VALUES ('x') duplicate "
            "key value violates unique constraint; Key "
            "(id)=(5b8b9c10-3333-4222-8333-4444555566d1) already exists."
        )
        real_flush = db.flush

        def _boom(*args, **kwargs):
            if _called_from("_insert", "master_ingest_service.py"):
                raise RuntimeError(leaking)
            return real_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", _boom)

        res = client.post(
            "/ingest/customers",
            json={
                "companyCode": company_code,
                "records": [_wh(code="ZZT-CUST-BOOM", name="Boom Sdn Bhd")],
            },
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "INSERT" not in res.text
        assert "5b8b9c10-3333-4222-8333-4444555566d1" not in res.text
        assert entry["errors"]["_"] == "internal error; see server logs"


class TestDryRun:
    """The safety gate. An ESB previews a sync against real hand-entered data.

    Every assertion here is about the absence of writes. A dry run that reports
    correctly but persists anyway is the worst available outcome, so the row
    counts are checked directly rather than trusting the response.
    """

    def test_dry_run_reports_what_would_be_created_without_creating_it(self, client, db, company_code):
        record = _wh(name="Would Be Created")

        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [record]},
        )

        assert res.status_code == 200
        body = res.json()
        assert body["dry_run"] is True
        assert body["summary"]["created"] == 1
        assert body["records"][0]["outcome"] == "created"
        # The whole point:
        assert _warehouse_count(db, record["code"]) == 0
        assert _reference_count(db, record["source_ref"]) == 0

    def test_dry_run_performs_adoption_matching_and_diffs_the_existing_row(self, client, db, company_code):
        """The case the feature exists for.

        An unclaimed local row matched by business code would be *adopted* and
        overwritten on a real sync. The operator needs to see, before that
        happens, exactly which hand-entered values are about to be replaced.
        """
        code = unique_code("WH")
        # is_active set explicitly: it has a Python-side default only, so a raw
        # INSERT leaves it NULL on a schema built from the ORM models
        # (bootstrap_env -- CI and fresh installs).
        db.execute(
            text(
                "INSERT INTO warehouses (id, warehouse_code, warehouse_name, location, is_active) "
                "VALUES (:i, :c, 'Hand Entered', 'Level 3', true)"
            ),
            {"i": str(uuid.uuid4()), "c": code},
        )
        # Commit so the dry run's rollback cannot take this setup with it --
        # the session's savepoint restarts here.
        db.commit()

        record = _wh(code=code, name="From AutoCount", location="Level 9")
        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [record]},
        )

        assert res.status_code == 200
        entry = res.json()["records"][0]
        # Adoption matched: an existing row, so updated rather than created.
        assert entry["outcome"] == "updated"
        diff = entry["diff"]
        assert diff["warehouse_name"] == {"current": "Hand Entered", "incoming": "From AutoCount"}
        assert diff["location"] == {"current": "Level 3", "incoming": "Level 9"}
        # ...and the code itself is unchanged, so it must not appear as a change.
        assert "warehouse_code" not in diff

        # Nothing was written: the row still holds the operator's values and no
        # reference claimed it.
        assert (
            db.execute(
                text("SELECT warehouse_name FROM warehouses WHERE warehouse_code = :c"), {"c": code}
            ).scalar()
            == "Hand Entered"
        )
        assert _warehouse_count(db, code) == 1
        assert _reference_count(db, record["source_ref"]) == 0

        # The control. Without it, "nothing was written" could equally mean the
        # request never reached the database at all -- a dry run that is broken
        # rather than safe would pass every assertion above. The same payload
        # without the flag must overwrite the row and claim it.
        client.post("/ingest/warehouses", json={"companyCode": company_code, "records": [record]})
        assert (
            db.execute(
                text("SELECT warehouse_name FROM warehouses WHERE warehouse_code = :c"), {"c": code}
            ).scalar()
            == "From AutoCount"
        )
        assert _reference_count(db, record["source_ref"]) == 1

    def test_dry_run_writes_nothing_for_a_mixed_batch(self, client, db, company_code):
        # Counted across the whole batch, because a partial write is exactly the
        # failure mode a per-record savepoint design could produce.
        good = _wh(code=unique_code("WH"))
        other = _wh(code=unique_code("WH"))
        bad = {"source_ref": "ZZT-DK-BAD", "code": "ZZT-BAD-MIX"}  # no name

        before_refs = db.execute(
            text("SELECT count(*) FROM integration_references WHERE source_ref LIKE 'ZZT-DK-%'")
        ).scalar()

        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [good, bad, other]},
        )

        assert res.status_code == 200
        summary = res.json()["summary"]
        assert summary["created"] == 2
        assert summary["failed"] == 1

        assert _warehouse_count(db, good["code"]) == 0
        assert _warehouse_count(db, other["code"]) == 0
        assert (
            db.execute(
                text("SELECT count(*) FROM integration_references WHERE source_ref LIKE 'ZZT-DK-%'")
            ).scalar()
            == before_refs
        )

    def test_dry_run_reports_retryable_exactly_as_a_real_ingest_would(self, client, company_code):
        # The verdict vocabulary must be identical, or a preview cannot be used
        # to predict the sync it is previewing.
        #
        # A supplier's `payment_terms_code` no longer causes this (ingest-parity-
        # standardisation UAC AC-P0-4/D15: accepted-and-warned, never retryable
        # - see tests/test_master_ingest.py::TestRetryableVsFatal). A sales
        # order naming a `customer_ref` that does not resolve stays retryable
        # on both dry-run and real ingest (`MasterRefResolver._resolve_ref` /
        # `_resolve_master`) and is untouched by S1's product/master rules, so
        # it is the stable choice here.
        res = client.post(
            "/ingest/sales_orders?dry_run=true",
            json={
                "companyCode": company_code,
                "records": [
                    {
                        "source_ref": "ZZT-DK-SO1",
                        "so_number": unique_code("SO"),
                        "status": "open",
                        "customer_ref": "ZZT-DK-NOSUCHCUSTOMER",
                        "lines": [],
                    }
                ]
            },
        )
        assert res.status_code == 200
        assert res.json()["summary"]["retryable"] == 1

    def test_a_created_record_carries_no_diff(self, client, company_code):
        # There is nothing to overwrite, so a diff would be noise -- and an
        # empty one would read as "adopted, no changes", which is a different
        # and much more important statement.
        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [_wh()]},
        )
        assert "diff" not in res.json()["records"][0]

    def test_dry_run_defaults_to_false(self, client, db, company_code):
        # Existing callers must be unaffected: no parameter means a real ingest.
        record = _wh()
        res = client.post(
            "/ingest/warehouses",
            json={"companyCode": company_code, "records": [record]},
        )

        assert res.json()["dry_run"] is False
        assert _warehouse_count(db, record["code"]) == 1

    def test_dry_run_false_explicitly_still_writes(self, client, db, company_code):
        record = _wh()
        client.post(
            "/ingest/warehouses?dry_run=false",
            json={"companyCode": company_code, "records": [record]},
        )
        assert _warehouse_count(db, record["code"]) == 1

    def test_a_resync_of_an_already_linked_record_also_diffs(self, client, db, company_code):
        # Adoption is the dangerous case, but a repeat sync overwrites live
        # values too -- and by then the record is linked, so it takes the other
        # update path. A preview blind to the commonest case would be a trap.
        record = _wh(name="First Sync")
        client.post("/ingest/warehouses", json={"companyCode": company_code, "records": [record]})
        db.commit()

        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [{**record, "name": "Second Sync"}]},
        )

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated"
        assert entry["diff"]["warehouse_name"] == {
            "current": "First Sync",
            "incoming": "Second Sync",
        }
        assert (
            db.execute(
                text("SELECT warehouse_name FROM warehouses WHERE warehouse_code = :c"),
                {"c": record["code"]},
            ).scalar()
            == "First Sync"
        )

    def test_an_identical_repush_reports_an_empty_diff_not_a_noisy_one(self, client, db, company_code):
        # "Matched an existing row and changes nothing" is a useful answer, and
        # it must not arrive dressed up as a list of edits.
        record = _wh(name="Unchanged")
        client.post("/ingest/warehouses", json={"companyCode": company_code, "records": [record]})
        db.commit()

        res = client.post(
            "/ingest/warehouses?dry_run=true",
            json={"companyCode": company_code, "records": [record]},
        )
        assert res.json()["records"][0]["diff"] == {}


class TestValueComparison:
    """The diff's changed/unchanged decision, in isolation.

    Reported changes that are not changes are not a cosmetic problem: an
    operator who learns the diff is noisy stops reading it, which removes the
    only protection this feature provides.
    """

    @pytest.mark.parametrize(
        "current, incoming",
        [
            (Decimal("0.00"), Decimal("0")),  # database scale vs payload scale
            (Decimal("12.50"), Decimal("12.5")),
            (Decimal("30"), 30),  # numeric column vs int payload
            (None, None),
            ("same", "same"),
            (True, True),
        ],
    )
    def test_equal_values_are_not_reported_as_changes(self, current, incoming):
        assert _value_changed(current, incoming) is False

    @pytest.mark.parametrize(
        "current, incoming",
        [
            (Decimal("12.50"), Decimal("12.51")),
            ("Hand Entered", "From AutoCount"),
            (None, "now set"),  # filling a blank is a change worth seeing
            ("was set", None),  # and so is clearing one
            (True, False),
            (30, 60),
        ],
    )
    def test_real_changes_are_reported(self, current, incoming):
        assert _value_changed(current, incoming) is True

    def test_a_bool_never_reaches_the_numeric_comparison(self):
        # bool subclasses int, so an unguarded numeric branch would evaluate
        # Decimal(str(True)) -> Decimal("True") -> InvalidOperation. Pinned
        # because is_active is on every canonical shape, so this is the one
        # column guaranteed to hit the comparison on every single record.
        assert _value_changed(True, 1) is False  # equal by Python semantics
        assert _value_changed(True, 0) is True
