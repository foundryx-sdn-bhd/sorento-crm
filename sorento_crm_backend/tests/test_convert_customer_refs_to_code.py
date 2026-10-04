"""RED tests for scripts/convert_customer_refs_to_code.py (DEBTOR-CODE-LINK, rule R9).

Card: documentation/plans/autocount/CARD-debtor-code-link-04oct.md R9.

The script does not exist yet. It rewrites every customer integration reference
`<prefix>:<AutoKey>` to `<prefix>:<debtor code>` (prefix of EACH ref kept), with a
CSV written before any change, idempotent, collision-safe and revertible.

Substrate: `blank_session()`; every test seeds its own customers and refs.
The script module is imported inside each test (importlib) so a missing script
fails each test with ModuleNotFoundError rather than breaking collection.
"""
from __future__ import annotations

import csv
import importlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZCNV"
MODULE = "scripts.convert_customer_refs_to_code"
HEADER = ["ref_id", "company_id", "old_source_ref", "new_source_ref"]

OLD = datetime(2025, 1, 1, 9, 0, 0)
NEW = datetime(2026, 9, 1, 9, 0, 0)


def _script():
    return importlib.import_module(MODULE)


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@pytest.fixture()
def company_b(db) -> str:
    company = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
    db.add(company)
    db.flush()
    return str(company.id)


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _customer(db, code: str, name: str = "NAME-A", created_at=OLD, company_id=DEFAULT_COMPANY_ID, id=None) -> str:
    row = Customer(
        id=id or str(uuid.uuid4()),
        customer_code=code,
        customer_name=name,
        created_at=created_at,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _ref(
    db,
    entity_id: str,
    source_ref: str,
    *,
    entity_type: str = "customers",
    company_id: str | None = DEFAULT_COMPANY_ID,
    source_system: str = "autocount",
) -> str:
    row = IntegrationReference(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        company_id=company_id,
        source_system=source_system,
        source_ref=source_ref,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _stored(db, ref_id: str) -> str:
    return db.execute(
        text("SELECT source_ref FROM integration_references WHERE id = :i"), {"i": ref_id}
    ).scalar()


def _change_for(plan, ref_id: str):
    return next((c for c in plan.changes if str(c.ref_id) == ref_id), None)


def _skip_for(plan, ref_id: str):
    return next((s for s in plan.skipped if str(s.ref_id) == ref_id), None)


class _KeepOpenSession:
    """Hands the test session to the script's `SessionLocal()` call. close/rollback
    are no-ops and commit only flushes, so the outer test transaction survives."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def commit(self):
        self._s.flush()

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def patched_session(db, monkeypatch):
    mod = _script()
    monkeypatch.setattr(mod, "SessionLocal", lambda: _KeepOpenSession(db))
    return mod


class TestPlan:
    def test_s1_autokey_ref_becomes_prefix_colon_code(self, db):
        mod = _script()
        code = _code("S1")
        cid = _customer(db, code)
        rid = _ref(db, cid, "AED_SORENTO:1001")

        plan = mod.plan_conversion(db)

        change = _change_for(plan, rid)
        assert change is not None, (plan.changes, plan.skipped)
        assert change.old_source_ref == "AED_SORENTO:1001"
        assert change.new_source_ref == f"AED_SORENTO:{code}"
        assert str(change.company_id) == DEFAULT_COMPANY_ID

    def test_s3_ref_already_in_code_form_is_counted_not_changed(self, db):
        mod = _script()
        code = _code("S3")
        cid = _customer(db, code)
        rid = _ref(db, cid, f"AED_SORENTO:{code}")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        assert _skip_for(plan, rid) is None
        assert plan.already_code == 1

    def test_s4_two_same_code_rows_older_converts_newer_is_a_collision(self, db):
        mod = _script()
        code = _code("S4")
        older = _customer(db, code, "NAME-A", OLD)
        newer = _customer(db, code, "NAME-B", NEW)
        # the newer row's ref is inserted first so insertion order cannot decide
        r_newer = _ref(db, newer, "AED_SORENTO:2")
        r_older = _ref(db, older, "AED_SORENTO:1")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, r_older) is not None
        assert _change_for(plan, r_newer) is None
        skip = _skip_for(plan, r_newer)
        assert skip is not None and skip.reason == "collision"
        assert skip.old_source_ref == "AED_SORENTO:2"

    def test_s5_existing_code_form_ref_on_a_sibling_makes_the_candidate_a_collision(self, db):
        mod = _script()
        code = _code("S5")
        sibling = _customer(db, code, "NAME-A", OLD)
        other = _customer(db, code, "NAME-B", NEW)
        _ref(db, sibling, f"AED_SORENTO:{code}")
        r_other = _ref(db, other, "AED_SORENTO:55")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, r_other) is None
        skip = _skip_for(plan, r_other)
        assert skip is not None and skip.reason == "collision"
        assert plan.already_code == 1

    def test_s6_each_refs_own_prefix_is_kept_and_same_code_in_two_companies_converts_in_both(
        self, db, company_b
    ):
        mod = _script()
        code = _code("S6")
        a = _customer(db, code, company_id=DEFAULT_COMPANY_ID)
        b = _customer(db, code, company_id=company_b)
        r_a = _ref(db, a, "AED_SORENTO:11", company_id=DEFAULT_COMPANY_ID)
        r_b = _ref(db, b, "OTHERDB:55", company_id=company_b)

        plan = mod.plan_conversion(db)

        change_a, change_b = _change_for(plan, r_a), _change_for(plan, r_b)
        assert change_a is not None and change_a.new_source_ref == f"AED_SORENTO:{code}"
        assert change_b is not None and change_b.new_source_ref == f"OTHERDB:{code}"
        assert str(change_b.company_id) == company_b
        assert _skip_for(plan, r_a) is None and _skip_for(plan, r_b) is None

    def test_s9_product_refs_are_untouched(self, db):
        mod = _script()
        pid = str(uuid.uuid4())
        rid = _ref(db, pid, "AED_SORENTO:9001", entity_type="products")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None and _skip_for(plan, rid) is None
        mod.apply_conversion  # exists
        assert _stored(db, rid) == "AED_SORENTO:9001"

    def test_s11_orphan_customer_ref_is_ignored_entirely(self, db):
        mod = _script()
        rid = _ref(db, str(uuid.uuid4()), "AED_SORENTO:404")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None and _skip_for(plan, rid) is None
        assert plan.already_code == 0

    def test_s12_code_with_surrounding_spaces_is_trimmed_in_the_new_ref(self, db):
        mod = _script()
        code = _code("S12")
        cid = _customer(db, f"  {code}  ")
        rid = _ref(db, cid, "AED_SORENTO:12")

        plan = mod.plan_conversion(db)

        change = _change_for(plan, rid)
        assert change is not None, (plan.changes, plan.skipped)
        assert change.new_source_ref == f"AED_SORENTO:{code}"

    def test_s13_ref_without_a_colon_is_skipped_unparseable(self, db):
        mod = _script()
        cid = _customer(db, _code("S13"))
        rid = _ref(db, cid, "NOCOLONKEY")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        skip = _skip_for(plan, rid)
        assert skip is not None and skip.reason == "unparseable"


class TestApplyRevert:
    def test_s2_second_plan_after_apply_has_no_changes(self, db, tmp_path):
        mod = _script()
        code = _code("S2")
        cid = _customer(db, code)
        rid = _ref(db, cid, "AED_SORENTO:1001")

        plan = mod.plan_conversion(db)
        count = mod.apply_conversion(db, plan, str(tmp_path / "out.csv"))

        assert count == 1
        assert _stored(db, rid) == f"AED_SORENTO:{code}"
        again = mod.plan_conversion(db)
        assert again.changes == []
        assert again.already_code == 1

    def test_s7_planning_alone_changes_nothing(self, db):
        mod = _script()
        cid = _customer(db, _code("S7"))
        rid = _ref(db, cid, "AED_SORENTO:70")

        mod.plan_conversion(db)

        assert _stored(db, rid) == "AED_SORENTO:70"

    def test_s7_cli_without_apply_is_a_dry_run(self, db, patched_session, capsys):
        mod = patched_session
        cid = _customer(db, _code("S7C"))
        rid = _ref(db, cid, "AED_SORENTO:71")

        mod.main([])

        assert _stored(db, rid) == "AED_SORENTO:71"
        assert capsys.readouterr().out.strip() != "", "a dry run must print its counts"

    def test_s8_revert_restores_old_refs_and_is_idempotent(self, db, tmp_path):
        mod = _script()
        code = _code("S8")
        cid = _customer(db, code)
        rid = _ref(db, cid, "AED_SORENTO:80")
        path = str(tmp_path / "out.csv")
        mod.apply_conversion(db, mod.plan_conversion(db), path)
        assert _stored(db, rid) == f"AED_SORENTO:{code}"

        assert mod.revert(db, path) == 1
        assert _stored(db, rid) == "AED_SORENTO:80"
        assert mod.revert(db, path) == 0
        assert _stored(db, rid) == "AED_SORENTO:80"

    def test_s10_csv_has_the_header_and_one_row_per_change(self, db, tmp_path):
        mod = _script()
        code = _code("S10")
        cid = _customer(db, code)
        rid = _ref(db, cid, "AED_SORENTO:100")
        path = tmp_path / "out.csv"

        mod.apply_conversion(db, mod.plan_conversion(db), str(path))

        with open(path, newline="") as fh:
            reader = csv.reader(fh)
            assert next(reader) == HEADER
            rows = list(reader)
        assert rows == [[rid, DEFAULT_COMPANY_ID, "AED_SORENTO:100", f"AED_SORENTO:{code}"]]

    def test_s14_cli_apply_then_revert_round_trip(self, db, patched_session, tmp_path):
        mod = patched_session
        code = _code("S14")
        cid = _customer(db, code)
        rid = _ref(db, cid, "AED_SORENTO:140")
        path = str(tmp_path / "cli.csv")

        mod.main(["--apply", "--csv", path])
        assert _stored(db, rid) == f"AED_SORENTO:{code}"

        mod.main(["--revert", path])
        assert _stored(db, rid) == "AED_SORENTO:140"


class TestChainedRefs:
    def test_s15_new_ref_freed_only_by_another_change_waits_for_a_second_run(self, db, tmp_path):
        """Row A (code C1) holds a stale `P:<C2>`; row B (code C2) holds `P:99`. B's new ref is
        A's current one, so this run converts A only; the next run converts B. No UPDATE in a
        run ever meets the unique index."""
        mod = _script()
        c1, c2 = _code("S15A"), _code("S15B")
        a = _customer(db, c1, "NAME-A", OLD)
        b = _customer(db, c2, "NAME-B", NEW)
        r_a = _ref(db, a, f"AED_SORENTO:{c2}")
        r_b = _ref(db, b, "AED_SORENTO:99")

        first = mod.plan_conversion(db)
        assert _change_for(first, r_a) is not None
        assert _skip_for(first, r_b).reason == "collision"
        mod.apply_conversion(db, first, str(tmp_path / "1.csv"))

        second = mod.plan_conversion(db)
        assert [c.ref_id for c in second.changes] == [r_b]
        mod.apply_conversion(db, second, str(tmp_path / "2.csv"))
        assert (_stored(db, r_a), _stored(db, r_b)) == (f"AED_SORENTO:{c1}", f"AED_SORENTO:{c2}")


class TestReviewRound1Script:
    def test_s16_apply_never_replaces_an_existing_change_list(self, db, tmp_path):
        """Security should-fix: the CSV may be the only way back, so a second --apply to the
        same path refuses before touching anything."""
        mod = _script()
        cid = _customer(db, _code("S16"))
        rid = _ref(db, cid, "AED_SORENTO:160")
        path = tmp_path / "out.csv"
        path.write_text("earlier run\n")

        with pytest.raises(FileExistsError):
            mod.apply_conversion(db, mod.plan_conversion(db), str(path))

        assert path.read_text() == "earlier run\n"
        assert _stored(db, rid) == "AED_SORENTO:160"

    def test_s17_blank_code_is_skipped_not_turned_into_a_bare_prefix(self, db):
        mod = _script()
        cid = _customer(db, "   ")
        rid = _ref(db, cid, "AED_SORENTO:170")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        assert _skip_for(plan, rid).reason == "blank_code"
