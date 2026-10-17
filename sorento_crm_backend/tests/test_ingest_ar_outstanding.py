"""AC-BILLING S5: outstanding per invoice and per customer, pushed as `ar_outstanding` (contract 2.10).

UAC: documentation/plans/finance/finance-billing-documents-27sep-acceptance-criteria.md, S5.

  S5-1  listed documents + the customer get the figures; `created` first, `updated` after
  S5-2  a stored non-zero document of that customer absent from the next push goes to 0;
        another customer's documents are never touched
  S5-3  a listed ref that is not in the CRM, or is another customer's or company's: not created,
        not touched, the rest applies, `documents_unknown` in the verdict's warnings
  S5-4  an older `as_of`: nothing written, `unchanged` + `stale_ignored`; an identical push: `unchanged`
  S5-5  customer unresolvable (`customer_ref` then `customer_code`, anchor company): `failed`
        with `errors.customer_ref`, no customer created
  S5-6  the three maps (edit pushes, view reads), the guard over them, contract 2.10 listing the entity
  S5-7  a document never covered keeps `outstanding` NULL
  (S5-9, the project SO draft credit finding, is not re-tested here: it already reads
  `customer.ar_outstanding` and `tests/test_project_so_draft.py::test_the_credit_warning_reads_...`
  proves the text; S5-1 below proves the push writes that column.)

Reuses the seeding and `env` fixture of `test_ingest_billing_documents.py`: company A holds customer
`ZZFIN-C1` (ref `SRT_DB:ZZFIN-C1`); this file adds a second customer and the billing documents it
pushes through the existing billing door. Nothing of `ar_outstanding` exists on this branch yet, so
the push tests fail on the entity being unsupported (the door answers 4xx instead of 200) or on the
missing `outstanding` columns, never on a fixture bug.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select, text

from app.models.base import set_company_scope
from app.models.order import Customer

from tests.test_ingest_billing_documents import (  # noqa: F401 - env is a fixture
    MARKER,
    _minimal,
    _record,
    env,
)
from tests.test_ingest_billing_documents_permissions import (  # noqa: F401 - fixtures
    client,
    guard_db,
    keys,
)

AR = "/api/v1/external/ingest/ar_outstanding"
READ = "/api/v1/external/read/ar_outstanding"
DEBTOR = "SRT_DB:debtor:1234"
DEBTOR2 = "SRT_DB:debtor:5678"
C1_REF, C1_CODE = "SRT_DB:ZZFIN-C1", "ZZFIN-C1"
C2_REF, C2_CODE = "SRT_DB:ZZFIN-C2", "ZZFIN-C2"
T1 = "2026-10-10T09:00:00"
T2 = "2026-10-11T09:00:00"
IV1, IV2, CN1, IV_C2 = "SRT_DB:IV:88213", "SRT_DB:IV:88214", "SRT_DB:CN:4410", "SRT_DB:IV:99001"


# ------------------------------------------------------------------ seeding / calls
def _bill(env, ref: str, *, customer: tuple[str, str] | None, document_type="invoice", **kw):
    extra = {}
    if customer:
        extra = {"customer_ref": customer[0], "customer_code": customer[1]}
    record = _minimal(ref, document_type=document_type, **extra, **kw)
    res = env.push([record])
    assert _record(res, ref)["outcome"] == "created", res.json()


def _seed(env) -> None:
    """Two customers; C1 has two invoices and a credit note, C2 one invoice; plus a document of
    company B and a customer-less document. Nothing has outstanding yet."""
    env.seed_customer(C2_CODE, env.company_a, ref=C2_REF)
    env.db.commit()
    _bill(env, IV1, customer=(C1_REF, C1_CODE))
    _bill(env, IV2, customer=(C1_REF, C1_CODE))
    _bill(env, CN1, customer=(C1_REF, C1_CODE), document_type="credit_note")
    _bill(env, IV_C2, customer=(C2_REF, C2_CODE))
    _bill(env, "SRT_DB:IV:NOCUST", customer=None)
    record = _minimal("SRT_DB:IV:B1")
    res = env.push([record], company_code=env.company_b_code)
    assert _record(res, "SRT_DB:IV:B1")["outcome"] == "created", res.json()
    _unscoped(env)


def _debtor(
    *,
    ref: str = DEBTOR,
    customer: tuple[str, str] | None = (C1_REF, C1_CODE),
    as_of: str = T1,
    total: float = 15230.40,
    documents: list[dict] | None = None,
) -> dict:
    record = {"source_ref": ref, "as_of": as_of, "total_outstanding": total}
    if customer:
        record["customer_ref"], record["customer_code"] = customer
    record["documents"] = (
        documents
        if documents is not None
        else [
            {"source_ref": IV1, "outstanding": 1100.00, "due_date": "2026-11-25"},
            {"source_ref": CN1, "outstanding": -200.00, "due_date": None},
        ]
    )
    return record


def _push(env, *records, dry_run: bool = False, company_code=None):
    url = f"{AR}?dry_run=true" if dry_run else AR
    return env.client.post(
        url, json={"companyCode": company_code or env.company_a_code, "records": list(records)}
    )


def _ok(res, ref: str = DEBTOR) -> dict:
    assert res.status_code == 200, res.text
    return _record(res, ref)


def _unscoped(env) -> None:
    """A push under company B leaves the session scoped to B (the test override of the scope
    dependency does not reset it); reads below look at every company, as `env.doc` assumes."""
    set_company_scope(env.db, None)


def _doc(env, source_ref: str, **kw):
    _unscoped(env)
    doc = env.doc(source_ref, **kw)
    assert doc is not None, source_ref
    return doc


def _customer(env, code: str = C1_CODE) -> Customer:
    _unscoped(env)
    env.db.expire_all()
    return env.db.execute(
        select(Customer).where(Customer.customer_code == code, Customer.company_id == env.company_a)
    ).scalar_one()


def _figures(env) -> list[tuple]:
    """Every billing document's (source_ref, outstanding, due_date, outstanding_as_of), plus every
    customer's AR pair: the 'nothing was written' equality."""
    _unscoped(env)
    env.db.expire_all()
    docs = env.dump()
    customers = env.db.execute(
        select(Customer.customer_code, Customer.ar_outstanding, Customer.ar_as_of).order_by(
            Customer.customer_code
        )
    ).all()
    return docs + [tuple(c) for c in customers]


# ===================================================================== S5-1
class TestPushWritesTheFigures:
    def test_documents_and_customer_get_the_figures_and_the_verdict_is_created(self, env):
        _seed(env)
        res = _push(env, _debtor())
        out = _ok(res)
        assert out["outcome"] == "created", res.json()
        assert res.json()["summary"]["created"] == 1 and res.json()["summary"]["failed"] == 0

        iv1, cn1 = _doc(env, IV1), _doc(env, CN1)
        assert iv1.outstanding == Decimal("1100.00")
        assert iv1.due_date == date(2026, 11, 25)
        assert iv1.outstanding_as_of == datetime(2026, 10, 10, 9, 0, 0)
        assert cn1.outstanding == Decimal("-200.00")  # an unapplied credit note is negative
        assert cn1.due_date is None
        assert cn1.outstanding_as_of == datetime(2026, 10, 10, 9, 0, 0)

        customer = _customer(env)
        assert customer.ar_outstanding == Decimal("15230.40")
        assert customer.ar_as_of == datetime(2026, 10, 10, 9, 0, 0)

    def test_a_later_changed_push_for_the_same_debtor_is_updated(self, env):
        _seed(env)
        assert _ok(_push(env, _debtor()))["outcome"] == "created"
        later = _debtor(
            as_of=T2,
            total=900.00,
            documents=[{"source_ref": IV1, "outstanding": 900.00, "due_date": "2026-11-30"}],
        )
        assert _ok(_push(env, later))["outcome"] == "updated"
        iv1 = _doc(env, IV1)
        assert iv1.outstanding == Decimal("900.00") and iv1.due_date == date(2026, 11, 30)
        assert iv1.outstanding_as_of == datetime(2026, 10, 11, 9, 0, 0)
        assert _customer(env).ar_outstanding == Decimal("900.00")
        assert _customer(env).ar_as_of == datetime(2026, 10, 11, 9, 0, 0)

    def test_customer_resolves_by_code_when_the_ref_is_unknown(self, env):
        _seed(env)
        record = _debtor(customer=("SRT_DB:NO-SUCH-REF", C1_CODE))
        assert _ok(_push(env, record))["outcome"] == "created"
        assert _customer(env).ar_outstanding == Decimal("15230.40")

    def test_a_push_does_not_touch_the_documents_amounts(self, env):
        _seed(env)
        before = _doc(env, IV1)
        totals = (before.net_total, before.tax_total, before.total, before.status)
        assert _ok(_push(env, _debtor()))["outcome"] == "created"
        after = _doc(env, IV1)
        assert (after.net_total, after.tax_total, after.total, after.status) == totals


# ===================================================================== S5-2
class TestAbsentMeansPaid:
    def test_a_stored_document_missing_from_the_next_push_goes_to_zero(self, env):
        _seed(env)
        _push(env, _debtor())
        second = _debtor(
            as_of=T2,
            total=200.00,
            documents=[{"source_ref": CN1, "outstanding": -200.00, "due_date": None}],
        )
        assert _ok(_push(env, second))["outcome"] == "updated"
        iv1 = _doc(env, IV1)
        assert iv1.outstanding == Decimal("0.00")
        assert iv1.outstanding_as_of == datetime(2026, 10, 11, 9, 0, 0)
        assert _doc(env, CN1).outstanding == Decimal("-200.00")

    def test_another_customers_documents_never_change(self, env):
        _seed(env)
        # C2 is pushed first, with its own invoice open.
        c2 = _debtor(
            ref=DEBTOR2,
            customer=(C2_REF, C2_CODE),
            total=50.00,
            documents=[{"source_ref": IV_C2, "outstanding": 50.00, "due_date": "2026-12-01"}],
        )
        assert _ok(_push(env, c2), DEBTOR2)["outcome"] == "created"
        c2_before = [
            (d.outstanding, d.due_date, d.outstanding_as_of) for d in [_doc(env, IV_C2)]
        ]
        c2_customer = (_customer(env, C2_CODE).ar_outstanding, _customer(env, C2_CODE).ar_as_of)

        # C1's push, later, lists nothing of C2's.
        _push(env, _debtor(as_of=T2, total=0, documents=[]))
        assert [
            (d.outstanding, d.due_date, d.outstanding_as_of) for d in [_doc(env, IV_C2)]
        ] == c2_before
        assert (_customer(env, C2_CODE).ar_outstanding, _customer(env, C2_CODE).ar_as_of) == c2_customer

    def test_an_empty_documents_list_zeroes_every_open_document_of_the_customer(self, env):
        _seed(env)
        _push(env, _debtor())
        _push(env, _debtor(as_of=T2, total=0, documents=[]))
        assert _doc(env, IV1).outstanding == Decimal("0.00")
        assert _doc(env, CN1).outstanding == Decimal("0.00")
        assert _customer(env).ar_outstanding == Decimal("0.00")


    def test_a_cancelled_document_is_left_untouched_when_absent(self, env):
        _seed(env)
        _push(env, _debtor())
        _unscoped(env)
        env.db.execute(
            text("UPDATE billing_documents SET status = 'cancelled' WHERE source_ref = :r"),
            {"r": IV1},
        )
        env.db.commit()
        assert _ok(_push(env, _debtor(as_of=T2, total=0, documents=[])))["outcome"] == "updated"
        iv1 = _doc(env, IV1)
        assert iv1.outstanding == Decimal("1100.00")
        assert iv1.outstanding_as_of == datetime(2026, 10, 10, 9, 0, 0)
        assert _doc(env, CN1).outstanding == Decimal("0.00")


# ===================================================================== S5-3
class TestUnknownDocuments:
    def test_unknown_foreign_customer_and_foreign_company_refs_are_kept_out(self, env):
        _seed(env)
        docs_before = env.counts()["docs"]
        record = _debtor(
            documents=[
                {"source_ref": IV1, "outstanding": 1100.00, "due_date": "2026-11-25"},
                {"source_ref": "SRT_DB:IV:NOT-IN-CRM", "outstanding": 5.00, "due_date": None},
                {"source_ref": IV_C2, "outstanding": 77.00, "due_date": None},  # C2's invoice
                {"source_ref": "SRT_DB:IV:B1", "outstanding": 88.00, "due_date": None},  # company B's
            ]
        )
        res = _push(env, record)
        out = _ok(res)
        assert out["outcome"] in ("created", "updated"), res.json()
        assert "documents_unknown" in out["warnings"], out

        # The rest of the record applied...
        assert _doc(env, IV1).outstanding == Decimal("1100.00")
        assert _customer(env).ar_outstanding == Decimal("15230.40")
        # ...and nothing was created or touched for the unknown three.
        assert env.counts()["docs"] == docs_before
        assert env.doc("SRT_DB:IV:NOT-IN-CRM") is None
        assert _doc(env, IV_C2).outstanding is None
        assert _doc(env, "SRT_DB:IV:B1", company_id=env.company_b).outstanding is None


# ===================================================================== S5-4
class TestStaleAndIdentical:
    def test_an_older_as_of_writes_nothing(self, env):
        _seed(env)
        _push(env, _debtor(as_of=T2))
        before = _figures(env)
        older = _debtor(
            as_of=T1,
            total=1.00,
            documents=[{"source_ref": IV1, "outstanding": 1.00, "due_date": None}],
        )
        out = _ok(_push(env, older))
        assert out["outcome"] == "unchanged", out
        assert "stale_ignored" in out["warnings"], out
        assert _figures(env) == before
        assert _customer(env).ar_as_of == datetime(2026, 10, 11, 9, 0, 0)

    def test_an_identical_repush_is_unchanged_and_writes_nothing(self, env):
        _seed(env)
        _push(env, _debtor())
        before = _figures(env)
        res = _push(env, _debtor())
        assert _ok(res)["outcome"] == "unchanged", res.json()
        assert res.json()["summary"]["unchanged"] == 1
        assert _figures(env) == before


# ===================================================================== S5-5
class TestCustomerMustResolve:
    def test_an_unknown_customer_fails_by_customer_ref_and_creates_nothing(self, env):
        _seed(env)
        _unscoped(env)
        customers_before = env.db.execute(select(func.count()).select_from(Customer.__table__)).scalar()
        before = _figures(env)
        record = _debtor(customer=("SRT_DB:NOBODY", "NOBODY-1"))
        out = _ok(_push(env, record))
        assert out["outcome"] == "failed", out
        assert "customer_ref" in out["errors"], out
        assert (
            env.db.execute(select(func.count()).select_from(Customer.__table__)).scalar()
            == customers_before
        )
        assert _figures(env) == before

    def test_a_customer_of_another_company_does_not_resolve(self, env):
        _seed(env)
        env.seed_customer("ZZFIN-BONLY", env.company_b, ref="SRT_DB:ZZFIN-BONLY")
        env.db.commit()
        record = _debtor(customer=("SRT_DB:ZZFIN-BONLY", "ZZFIN-BONLY"))
        out = _ok(_push(env, record))
        assert out["outcome"] == "failed" and "customer_ref" in out["errors"], out

    def test_a_record_with_neither_ref_nor_code_fails(self, env):
        _seed(env)
        out = _ok(_push(env, _debtor(customer=None)))
        assert out["outcome"] == "failed" and out["errors"], out

    def test_one_bad_record_does_not_stop_the_batch(self, env):
        _seed(env)
        res = _push(env, _debtor(ref="SRT_DB:debtor:bad", customer=("SRT_DB:NOBODY", "NOBODY-1")), _debtor())
        assert res.status_code == 200, res.text
        assert _record(res, "SRT_DB:debtor:bad")["outcome"] == "failed"
        assert _record(res, DEBTOR)["outcome"] == "created"
        assert _doc(env, IV1).outstanding == Decimal("1100.00")

    def test_an_as_of_in_the_future_fails_by_as_of(self, env):
        _seed(env)
        before = _figures(env)
        res = _push(env, _debtor(as_of="2099-01-01T00:00:00"))
        assert res.status_code == 200, res.text
        out = _record(res, DEBTOR)
        assert out["outcome"] == "failed" and "as_of" in out["errors"]
        assert _figures(env) == before

    def test_validation_names_the_field(self, env):
        _seed(env)
        no_as_of = _debtor()
        del no_as_of["as_of"]
        bad_total = _debtor(ref="SRT_DB:debtor:t")
        bad_total["total_outstanding"] = "plenty"
        res = _push(env, no_as_of, bad_total)
        assert res.status_code == 200, res.text
        assert _record(res, DEBTOR)["outcome"] == "failed"
        assert "as_of" in _record(res, DEBTOR)["errors"]
        assert _record(res, "SRT_DB:debtor:t")["outcome"] == "failed"
        assert "total_outstanding" in _record(res, "SRT_DB:debtor:t")["errors"]


# ===================================================================== S5-7
class TestNeverCoveredStaysNull:
    def test_documents_outside_every_push_keep_outstanding_null(self, env):
        _seed(env)
        assert _ok(_push(env, _debtor()))["outcome"] == "created"
        # C2's invoice, the customer-less one and company B's were never covered.
        for ref in (IV_C2, "SRT_DB:IV:NOCUST"):
            doc = _doc(env, ref)
            assert doc.outstanding is None and doc.due_date is None
            assert doc.outstanding_as_of is None
        assert _doc(env, "SRT_DB:IV:B1", company_id=env.company_b).outstanding is None
        assert _customer(env, C2_CODE).ar_outstanding is None
        assert _customer(env, C2_CODE).ar_as_of is None

    def test_a_billing_document_push_never_overwrites_outstanding(self, env):
        _seed(env)
        _push(env, _debtor())
        record = _minimal(IV1, customer_ref=C1_REF, customer_code=C1_CODE, description="EDITED")
        record["source_modified_at"] = "2026-12-01T00:00:00"
        res = env.push([record])
        assert _record(res, IV1)["outcome"] == "updated", res.json()
        doc = _doc(env, IV1)
        assert doc.description == "EDITED"
        assert doc.outstanding == Decimal("1100.00")
        assert doc.outstanding_as_of == datetime(2026, 10, 10, 9, 0, 0)


# ===================================================================== dry run
class TestDryRun:
    def test_dry_run_reports_and_writes_nothing(self, env):
        _seed(env)
        before = _figures(env)
        res = _push(env, _debtor(), dry_run=True)
        assert res.status_code == 200, res.text
        assert res.json()["dry_run"] is True
        assert _record(res, DEBTOR)["outcome"] == "created"
        assert _figures(env) == before
        assert _doc(env, IV1).outstanding is None
        assert _customer(env).ar_outstanding is None


# ===================================================================== read-back
class TestReadBack:
    def _read(self, env, refs, *, company_code=None):
        return env.client.post(
            READ, json={"companyCode": company_code or env.company_a_code, "source_refs": refs}
        )

    def test_read_back_answers_per_debtor_with_its_open_documents(self, env):
        _seed(env)
        _push(env, _debtor())
        res = self._read(env, [DEBTOR, "SRT_DB:debtor:nope"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == ["SRT_DB:debtor:nope"]
        rec = next(r for r in body["records"] if r["source_ref"] == DEBTOR)
        customer = _customer(env)
        assert rec["customer_id"] == str(customer.id)
        assert rec["customer_code"] == C1_CODE
        assert Decimal(str(rec["ar_outstanding"])) == Decimal("15230.40")
        assert str(rec["ar_as_of"]).startswith("2026-10-10T09:00:00")
        docs = {d["source_ref"]: d for d in rec["documents"]}
        # Only this customer's documents with a non-null outstanding: IV2 was never covered.
        assert set(docs) == {IV1, CN1}
        assert Decimal(str(docs[IV1]["outstanding"])) == Decimal("1100.00")
        assert docs[IV1]["due_date"] == "2026-11-25"
        assert Decimal(str(docs[CN1]["outstanding"])) == Decimal("-200.00")
        assert docs[CN1]["due_date"] is None

    def test_read_back_under_the_other_company_is_not_found(self, env):
        _seed(env)
        _push(env, _debtor())
        res = self._read(env, [DEBTOR], company_code=env.company_b_code)
        assert res.status_code == 200, res.text
        assert res.json()["records"] == []
        assert res.json()["not_found"] == [DEBTOR]


# ===================================================================== S5-6 contract + gates
class TestContract:
    def test_contract_is_2_10_and_lists_the_entity(self, env):
        res = env.client.get("/api/v1/external/contract")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["version"] == "2.10"
        assert "ar_outstanding" in body["entities"]


def test_the_three_maps_name_the_finance_slugs():
    from app.api.v1.external import ingest as ingest_module

    assert "ar_outstanding" in ingest_module.SUPPORTED_ENTITIES
    assert ingest_module.INGEST_PERMISSIONS["ar_outstanding"] == "finance.billing_documents.edit"
    assert ingest_module.READ_PERMISSIONS["ar_outstanding"] == "finance.billing_documents.view"


def _post(client, path, key):
    return client.post(path, headers={"X-API-Key": key})


GATE_INGEST = "/ingest/ar_outstanding"
GATE_READ = "/read/ar_outstanding"


def test_view_alone_reads_but_never_pushes(client, keys):
    assert _post(client, GATE_INGEST, keys["viewer"]).status_code == 403
    assert _post(client, GATE_READ, keys["viewer"]).status_code == 200


def test_edit_alone_pushes_but_never_reads(client, keys):
    res = _post(client, GATE_INGEST, keys["editor_only"])
    assert res.status_code == 200, res.text
    assert _post(client, GATE_READ, keys["editor_only"]).status_code == 403


def test_another_modules_edit_slug_cannot_push_outstanding(client, keys):
    res = _post(client, GATE_INGEST, keys["so_editor"])
    assert res.status_code == 403
    assert "finance.billing_documents.edit" in res.text
    assert _post(client, GATE_READ, keys["so_editor"]).status_code == 403


def test_export_alone_opens_no_door(client, keys):
    for path in (GATE_INGEST, GATE_READ):
        assert _post(client, path, keys["exporter"]).status_code == 403, path


def test_the_feed_role_opens_both(client, keys):
    for path in (GATE_INGEST, GATE_READ):
        assert _post(client, path, keys["feed"]).status_code == 200, path
