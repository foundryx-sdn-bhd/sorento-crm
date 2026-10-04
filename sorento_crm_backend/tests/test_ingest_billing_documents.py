"""Finance S0 - billing documents on the ingest surface (#1309, contract 2.6).

UAC: documentation/plans/finance/finance-billing-documents-27sep-acceptance-criteria.md, S0.

  S0-4   the replayable fixture lands: six `created`, rows match field for field
  S0-5   replaying it changes nothing: six `unchanged`, no `updated_at` moves
  S0-6   an edited document replaces: the kept line keeps its id, a dropped line goes
  S0-7   an IV and a CN sharing a DocKey number land as two documents
  S0-8   a cancel is an update: the row and its lines stay, status `cancelled`
  S0-9   an older `source_modified_at` is ignored: `unchanged` + `stale_ignored`
  S0-10  deletions: hard delete, or `cancelled` + `deactivated` when a CN points at it
  S0-11  unresolved customer / agent / product land NULL with the code kept, never retryable
  S0-12  a CN whose invoice arrives later is linked by the invoice's own ingest
  S0-13  an SO line ref links the line; an unknown one stays NULL with the refs kept
  S0-14  a master of the other company is never linked; read-back under it is not_found
  S0-15  no companyCode and no binding: 422 COMPANY_ANCHOR_REQUIRED
  S0-17  read-back answers the canonical shape with entity ids
  S0-19  per-record validation: failed with the field named, the rest lands
  S0-22  no date floor: 2019, 2022 and 2023 documents all land

The fixture (`tests/fixtures/finance/billing_documents_v1*.json`) names masters by code
and ref only; this file seeds them, because CI's database holds no data.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.company import Company
from app.models.finance import BillingDocument, BillingDocumentLine
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import IntegrationReferenceService

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZFIN"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "finance"

INGEST = "/api/v1/external/ingest/billing_documents"
DELETE = "/api/v1/external/ingest/billing_documents/deletions"
READ = "/api/v1/external/read/billing_documents"

_USER_ID = "5b8b9c10-1111-4222-8333-4444555566f1"
_ROLE_ID = "5b8b9c10-2222-4222-8333-4444555566f2"

SO_LINE_REF = "SRT_DB:SO:9001:1"


def load_fixture(name: str = "billing_documents_v1.json") -> dict:
    return json.loads((FIXTURES / name).read_text())


def _seed_principal(db) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(
        UserRole(
            id=_ROLE_ID,
            slug="superadmin",
            name=f"{MARKER} Superadmin",
            description="",
            is_protected=True,
            is_default=False,
        )
    )
    db.flush()
    db.add(
        User(
            id=_USER_ID,
            name=f"{MARKER} admin",
            email=f"{MARKER.lower()}-admin@test.com",
            password="x",
            status="active",
        )
    )
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.flush()


class _Env:
    """Company A (the default company) holds every master the fixture names; company B
    holds a customer of its own, for S0-14."""

    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        self.refs = IntegrationReferenceService(db, company_id=self.company_a)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZF{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_b_code = other.code

        category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([category, uom])
        db.flush()
        self._category_id = category.id
        self._uom_id = uom.id

        self.customer_id = self.seed_customer("ZZFIN-C1", self.company_a, ref="SRT_DB:ZZFIN-C1")
        self.product1_id = self.seed_product("ZZFIN-P1", self.company_a, ref="SRT_DB:ZZFIN-P1")
        self.product2_id = self.seed_product("ZZFIN-P2", self.company_a, ref="SRT_DB:ZZFIN-P2")
        agent = SalesAgent(sales_agent="ZZFIN-AG1")
        db.add(agent)
        db.flush()
        self.agent_id = str(agent.id)

        order = SalesOrder(
            so_number="SO-2608/0001", company_id=self.company_a, source_system="autocount"
        )
        db.add(order)
        db.flush()
        so_line = SalesOrderLine(
            sales_order_id=order.id,
            product_id=self.product1_id,
            qty_ordered=10,
            source_ref=SO_LINE_REF,
            company_id=self.company_a,
        )
        db.add(so_line)
        db.flush()
        self.so_line_id = str(so_line.id)
        # Committed, as in test_ingest_documents: a dry run rolls back to the session's
        # transaction start, which must not take the seeds with it.
        db.commit()

    def seed_customer(self, code: str, company_id: str, *, ref: str | None = None) -> str:
        row = Customer(customer_code=code, customer_name=f"{MARKER} {code}", company_id=company_id)
        self.db.add(row)
        self.db.flush()
        if ref:
            IntegrationReferenceService(self.db, company_id=company_id).link(
                entity_type="customers", entity_id=str(row.id), source_ref=ref
            )
        return str(row.id)

    def seed_product(self, code: str, company_id: str, *, ref: str | None = None) -> str:
        row = Product(
            product_code=code,
            product_name=f"{MARKER} {code}",
            category_id=self._category_id,
            base_uom_id=self._uom_id,
            list_price=10,
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        if ref:
            IntegrationReferenceService(self.db, company_id=company_id).link(
                entity_type="products", entity_id=str(row.id), source_ref=ref
            )
        return str(row.id)

    # ------------------------------------------------------------------ calls
    def push(self, records, *, company_code=None, dry_run=False):
        url = f"{INGEST}?dry_run=true" if dry_run else INGEST
        return self.client.post(
            url, json={"companyCode": company_code or self.company_a_code, "records": records}
        )

    def push_fixture(self, name: str = "billing_documents_v1.json", **kw):
        return self.push(load_fixture(name)["records"], **kw)

    def read(self, source_refs, *, company_code=None):
        return self.client.post(
            READ,
            json={"companyCode": company_code or self.company_a_code, "source_refs": source_refs},
        )

    def delete(self, source_refs, *, company_code=None):
        return self.client.post(
            DELETE,
            json={"companyCode": company_code or self.company_a_code, "source_refs": source_refs},
        )

    # ------------------------------------------------------------------ reads
    def doc(self, source_ref: str, document_type: str | None = None, *, company_id=None):
        query = select(BillingDocument).where(
            BillingDocument.company_id == (company_id or self.company_a),
            BillingDocument.source_ref == source_ref,
        )
        if document_type:
            query = query.where(BillingDocument.document_type == document_type)
        self.db.expire_all()
        return self.db.execute(query).scalars().one_or_none()

    def lines(self, document_id: str):
        self.db.expire_all()
        return (
            self.db.execute(
                select(BillingDocumentLine)
                .where(BillingDocumentLine.document_id == document_id)
                .order_by(BillingDocumentLine.line_no, BillingDocumentLine.source_ref)
            )
            .scalars()
            .all()
        )

    def counts(self) -> dict[str, int]:
        return {
            "docs": self.db.execute(
                select(func.count()).select_from(BillingDocument.__table__)
            ).scalar(),
            "lines": self.db.execute(
                select(func.count()).select_from(BillingDocumentLine.__table__)
            ).scalar(),
            "refs": self.db.execute(
                text(
                    "SELECT count(*) FROM integration_references "
                    "WHERE entity_type = 'billing_documents'"
                )
            ).scalar(),
        }

    def dump(self) -> list[tuple]:
        """A canonical dump of both tables, every column, ordered - the S0-5 equality."""
        self.db.expire_all()
        docs = self.db.execute(
            select(BillingDocument.__table__).order_by(BillingDocument.source_ref)
        ).all()
        lines = self.db.execute(
            select(BillingDocumentLine.__table__).order_by(BillingDocumentLine.source_ref)
        ).all()
        return [tuple(r) for r in docs] + [tuple(r) for r in lines]


@pytest.fixture
def env():
    from app.dependencies import (  # safe: app.main is already loaded
        get_current_user,
        get_current_user_or_api_key,
        get_db,
        get_external_api_user,
    )
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        _seed_principal(db)

        def _override_get_db():
            yield db

        def _override_user():
            return {"id": _USER_ID, "email": f"{MARKER.lower()}-admin@test.com"}

        def _override_company_scope():
            set_company_scope(db, None)
            return None

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_user
        app.dependency_overrides[get_external_api_user] = _override_user
        app.dependency_overrides[apply_company_scope] = _override_company_scope
        try:
            with TestClient(app) as client:
                yield _Env(client, db)
        finally:
            app.dependency_overrides.clear()


def _outcomes(res) -> dict[str, str]:
    assert res.status_code == 200, res.text
    return {r["source_ref"]: r["outcome"] for r in res.json()["records"]}


def _record(res, source_ref: str) -> dict:
    for r in res.json()["records"]:
        if r["source_ref"] == source_ref:
            return r
    raise AssertionError(f"{source_ref} not in {res.json()}")


def _minimal(ref: str, *, document_type="invoice", doc_no=None, **extra) -> dict:
    record = {
        "source_ref": ref,
        "document_type": document_type,
        "doc_no": doc_no or f"{MARKER}-{uuid.uuid4().hex[:6]}",
        "doc_date": "2026-09-10",
        "status": "posted",
        "net_total": 10,
        "tax_total": 0,
        "total": 10,
        "local_net_total": 10,
        "lines": [
            {
                "source_ref": f"{ref}:1",
                "line_number": 1,
                "product_code": "ZZFIN-P1",
                "quantity": 1,
                "unit_price": 10,
                "net_amount": 10,
                "tax_amount": 0,
                "line_total": 10,
            }
        ],
    }
    record.update(extra)
    return record


# ===================================================================== S0-4
class TestFixtureLands:
    def test_six_created_and_rows_match_the_fixture(self, env):
        res = env.push_fixture()
        body = res.json()
        assert res.status_code == 200, res.text
        assert body["summary"]["created"] == 6, body
        assert body["summary"]["failed"] == 0
        assert set(_outcomes(res).values()) == {"created"}

        fixture = load_fixture()
        for record in fixture["records"]:
            doc = env.doc(record["source_ref"], record["document_type"])
            assert doc is not None, record["source_ref"]
            assert str(doc.company_id) == env.company_a
            assert doc.document_type == record["document_type"]
            assert doc.doc_no == record["doc_no"]
            assert doc.doc_date == date.fromisoformat(record["doc_date"])
            assert doc.status == record["status"]
            assert doc.currency_code == record["currency_code"]
            assert doc.currency_rate == Decimal(str(record["currency_rate"]))
            for money in ("net_total", "tax_total", "total", "local_net_total"):
                assert getattr(doc, money) == Decimal(str(record[money])).quantize(
                    Decimal("0.01")
                ), (record["source_ref"], money)
            assert doc.debtor_code == record["customer_code"]
            assert str(doc.customer_id) == env.customer_id
            assert str(doc.sales_agent_id) == env.agent_id
            assert doc.agent_code == record["agent_code"]
            assert doc.source_system == "autocount"
            assert doc.source_modified_at is not None
            assert _record(res, record["source_ref"])["entity_id"] == str(doc.id)

            lines = env.lines(doc.id)
            assert len(lines) == len(record["lines"])
            by_ref = {line.source_ref: line for line in lines}
            for sent in record["lines"]:
                line = by_ref[sent["source_ref"]]
                assert str(line.company_id) == env.company_a
                assert line.line_no == sent["line_number"]
                assert line.quantity == Decimal(str(sent["quantity"]))
                assert line.net_amount == Decimal(str(sent["net_amount"])).quantize(
                    Decimal("0.01")
                )
                assert line.tax_amount == Decimal(str(sent["tax_amount"])).quantize(
                    Decimal("0.01")
                )
                assert line.line_total == Decimal(str(sent["line_total"])).quantize(
                    Decimal("0.01")
                )
                assert line.tax_code == sent.get("tax_code")
                assert line.item_code == sent.get("product_code")

    def test_usd_invoice_keeps_rate_and_ringgit_net(self, env):
        env.push_fixture()
        doc = env.doc("SRT_DB:IV:1003")
        assert doc.currency_code == "USD"
        assert doc.currency_rate == Decimal("4.2")
        assert doc.net_total == Decimal("100.00")
        assert doc.local_net_total == Decimal("420.00")

    def test_cancelled_invoice_is_stored_cancelled(self, env):
        env.push_fixture()
        doc = env.doc("SRT_DB:IV:1002")
        assert doc.status == "cancelled"
        assert len(env.lines(doc.id)) == 1

    def test_one_integration_reference_per_document(self, env):
        env.push_fixture()
        assert env.counts()["refs"] == 6
        doc = env.doc("SRT_DB:IV:1001")
        assert env.refs.resolve(entity_type="billing_documents", source_ref="SRT_DB:IV:1001") == str(
            doc.id
        )

    def test_dry_run_writes_nothing(self, env):
        before = env.counts()
        res = env.push_fixture(dry_run=True)
        assert res.json()["dry_run"] is True
        assert set(_outcomes(res).values()) == {"created"}
        assert env.counts() == before


# ===================================================================== S0-5
class TestReplay:
    def test_replay_is_unchanged_and_writes_nothing(self, env):
        env.push_fixture()
        before = env.dump()
        counts = env.counts()

        res = env.push_fixture()
        assert set(_outcomes(res).values()) == {"unchanged"}, res.json()
        assert res.json()["summary"]["unchanged"] == 6
        assert res.json()["summary"]["created"] == 0
        assert res.json()["summary"]["updated"] == 0
        assert env.counts() == counts
        # Every column of every row, `updated_at` and `last_synced_at` included.
        assert env.dump() == before

    def test_duplicate_inside_one_batch_is_created_then_unchanged(self, env):
        record = load_fixture()["records"][0]
        res = env.push([record, copy.deepcopy(record)])
        outcomes = [r["outcome"] for r in res.json()["records"]]
        assert outcomes == ["created", "unchanged"]
        assert env.counts()["docs"] == 1


# ===================================================================== S0-6
class TestEdit:
    def test_edit_replaces_lines_and_totals(self, env):
        env.push_fixture()
        doc = env.doc("SRT_DB:IV:1001")
        kept = {line.source_ref: line.id for line in env.lines(doc.id)}

        changes = load_fixture("billing_documents_v1_changes.json")["records"]
        res = env.push([changes[0]])
        record = _record(res, "SRT_DB:IV:1001")
        assert record["outcome"] == "updated", res.json()
        assert record["lines"]["updated"] == 1
        assert record["lines"]["deleted"] == 1

        doc = env.doc("SRT_DB:IV:1001")
        lines = env.lines(doc.id)
        assert [line.source_ref for line in lines] == ["SRT_DB:IV:1001:1"]
        assert lines[0].id == kept["SRT_DB:IV:1001:1"]
        assert lines[0].quantity == Decimal("8")
        assert doc.net_total == Decimal("800.00")
        assert doc.tax_total == Decimal("80.00")
        assert doc.total == Decimal("880.00")
        assert doc.local_net_total == Decimal("800.00")


# ===================================================================== S0-7
class TestTypedKey:
    def test_iv_and_cn_with_the_same_dockey_are_two_documents(self, env):
        env.push_fixture()
        iv = env.doc("SRT_DB:IV:1001")
        cn = env.doc("SRT_DB:CN:1001")
        assert iv is not None and cn is not None
        assert iv.id != cn.id
        assert (iv.document_type, cn.document_type) == ("invoice", "credit_note")

    def test_a_ref_already_naming_another_type_is_refused(self, env):
        env.push([_minimal(f"{MARKER}:X:1", document_type="invoice")])
        res = env.push([_minimal(f"{MARKER}:X:1", document_type="credit_note")])
        record = _record(res, f"{MARKER}:X:1")
        assert record["outcome"] == "failed"
        assert "document_type" in record["errors"]
        assert env.counts()["docs"] == 1


# ===================================================================== S0-8
class TestCancel:
    def test_cancel_is_an_update_and_keeps_the_rows(self, env):
        env.push_fixture()
        cs = env.doc("SRT_DB:CS:2001")
        line_ids = [line.id for line in env.lines(cs.id)]

        cancel = load_fixture("billing_documents_v1_changes.json")["records"][1]
        res = env.push([cancel])
        assert _record(res, "SRT_DB:CS:2001")["outcome"] == "updated"
        cs = env.doc("SRT_DB:CS:2001")
        assert cs.status == "cancelled"
        assert [line.id for line in env.lines(cs.id)] == line_ids


# ===================================================================== S0-9
class TestStaleGuard:
    def test_an_older_version_is_ignored(self, env):
        env.push_fixture()
        before = env.dump()
        older = copy.deepcopy(load_fixture()["records"][0])
        older["source_modified_at"] = "2026-08-01T00:00:00"
        older["net_total"] = 1.00
        older["tax_total"] = 0
        older["total"] = 1.00
        res = env.push([older])
        record = _record(res, "SRT_DB:IV:1001")
        assert record["outcome"] == "unchanged"
        assert "stale_ignored" in record.get("warnings", [])
        assert env.dump() == before

    def test_a_newer_version_applies(self, env):
        env.push_fixture()
        newer = copy.deepcopy(load_fixture()["records"][0])
        newer["source_modified_at"] = "2026-09-20T00:00:00"
        newer["ref"] = "CHANGED REF"
        res = env.push([newer])
        assert _record(res, "SRT_DB:IV:1001")["outcome"] == "updated"
        assert env.doc("SRT_DB:IV:1001").ref == "CHANGED REF"


# ===================================================================== S0-10
class TestDeletions:
    def test_unreferenced_document_is_hard_deleted_with_its_lines(self, env):
        env.push_fixture()
        dn = env.doc("SRT_DB:DN:3001")
        res = env.delete(["SRT_DB:DN:3001"])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "deleted"
        assert env.doc("SRT_DB:DN:3001") is None
        assert env.lines(dn.id) == []
        assert env.refs.resolve(entity_type="billing_documents", source_ref="SRT_DB:DN:3001") is None

    def test_referenced_invoice_is_cancelled_and_deactivated(self, env):
        env.push_fixture()
        res = env.delete(["SRT_DB:IV:1001"])
        assert res.json()["records"][0]["outcome"] == "deactivated"
        iv = env.doc("SRT_DB:IV:1001")
        assert iv is not None
        assert iv.status == "cancelled"
        assert len(env.lines(iv.id)) == 2

    def test_unknown_ref_is_not_found(self, env):
        res = env.delete([f"{MARKER}:nope"])
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_dry_run_deletes_nothing(self, env):
        env.push_fixture()
        before = env.counts()
        res = env.client.post(
            f"{DELETE}?dry_run=true",
            json={"companyCode": env.company_a_code, "source_refs": ["SRT_DB:DN:3001"]},
        )
        assert res.json()["records"][0]["outcome"] == "deleted"
        assert env.counts() == before


# ===================================================================== S0-11
class TestUnresolvedMasters:
    def test_unknown_customer_agent_product_land_null_with_codes_kept(self, env):
        record = _minimal(
            f"{MARKER}:IV:U1",
            customer_ref="SRT_DB:NOBODY",
            customer_code="NOBODY-1",
            customer_name="Walk-in",
            agent_code="NO-AGENT",
        )
        record["lines"][0]["product_ref"] = "SRT_DB:NO-ITEM"
        record["lines"][0]["product_code"] = "NO-ITEM"
        before_customers = env.db.execute(select(func.count()).select_from(Customer.__table__)).scalar()
        before_agents = env.db.execute(select(func.count()).select_from(SalesAgent.__table__)).scalar()

        res = env.push([record])
        out = _record(res, f"{MARKER}:IV:U1")
        assert out["outcome"] == "created", res.json()
        assert set(out["warnings"]) >= {
            "customer_unresolved",
            "agent_unresolved",
            "product_unresolved",
        }
        doc = env.doc(f"{MARKER}:IV:U1")
        assert doc.customer_id is None and doc.debtor_code == "NOBODY-1"
        assert doc.customer_name == "Walk-in"
        assert doc.sales_agent_id is None and doc.agent_code == "NO-AGENT"
        line = env.lines(doc.id)[0]
        assert line.product_id is None and line.item_code == "NO-ITEM"
        # Never back-created: a billing document never makes a master.
        assert env.db.execute(select(func.count()).select_from(Customer.__table__)).scalar() == before_customers
        assert env.db.execute(select(func.count()).select_from(SalesAgent.__table__)).scalar() == before_agents

    def test_product_by_code_alone_resolves(self, env):
        env.push_fixture()
        cs = env.doc("SRT_DB:CS:2001")
        assert str(env.lines(cs.id)[0].product_id) == env.product1_id

    def test_line_with_no_product_at_all_lands_without_warning(self, env):
        env.push_fixture()
        dn = env.doc("SRT_DB:DN:3001")
        line = env.lines(dn.id)[0]
        assert line.product_id is None and line.item_code is None


# ===================================================================== S0-12
class TestLateAgainstLink:
    def test_cn_before_iv_is_filled_when_the_iv_lands(self, env):
        records = {r["source_ref"]: r for r in load_fixture()["records"]}
        res = env.push([records["SRT_DB:CN:1001"]])
        assert _record(res, "SRT_DB:CN:1001")["outcome"] == "created"
        cn = env.doc("SRT_DB:CN:1001")
        assert cn.against_document_id is None
        assert cn.against_doc_no == "IV-2609/0001"

        env.push([records["SRT_DB:IV:1001"]])
        iv = env.doc("SRT_DB:IV:1001")
        cn = env.doc("SRT_DB:CN:1001")
        assert str(cn.against_document_id) == str(iv.id)

    def test_fixture_order_links_cn_and_dn_on_first_write(self, env):
        env.push_fixture()
        iv = env.doc("SRT_DB:IV:1001")
        assert str(env.doc("SRT_DB:CN:1001").against_document_id) == str(iv.id)
        assert str(env.doc("SRT_DB:DN:3001").against_document_id) == str(iv.id)

    def test_against_source_ref_wins(self, env):
        env.push_fixture()
        cn = _minimal(
            f"{MARKER}:CN:S1",
            document_type="credit_note",
            against_source_ref="SRT_DB:IV:1003",
            against_doc_no="IV-2609/0001",
        )
        env.push([cn])
        assert str(env.doc(f"{MARKER}:CN:S1").against_document_id) == str(
            env.doc("SRT_DB:IV:1003").id
        )


# ===================================================================== S0-13
class TestSalesOrderLineLink:
    def test_known_so_line_ref_links(self, env):
        env.push_fixture()
        iv = env.doc("SRT_DB:IV:1001")
        by_ref = {line.source_ref: line for line in env.lines(iv.id)}
        linked = by_ref["SRT_DB:IV:1001:1"]
        assert str(linked.sales_order_line_id) == env.so_line_id
        assert linked.from_line_ref == SO_LINE_REF
        assert by_ref["SRT_DB:IV:1001:2"].sales_order_line_id is None

    def test_unknown_so_line_ref_stays_null_with_refs_kept(self, env):
        record = _minimal(f"{MARKER}:IV:SO2")
        record["lines"][0].update(
            from_doc_type="DO", from_doc_no="DO-1/1", from_line_ref="SRT_DB:SO:404:1"
        )
        env.push([record])
        line = env.lines(env.doc(f"{MARKER}:IV:SO2").id)[0]
        assert line.sales_order_line_id is None
        assert (line.from_doc_type, line.from_doc_no, line.from_line_ref) == (
            "DO",
            "DO-1/1",
            "SRT_DB:SO:404:1",
        )


# ===================================================================== S0-14
class TestCompanyIsolation:
    def test_other_companys_customer_is_not_linked(self, env):
        env.seed_customer("ZZFIN-BONLY", env.company_b, ref="SRT_DB:ZZFIN-BONLY")
        env.db.commit()
        record = _minimal(
            f"{MARKER}:IV:B1", customer_ref="SRT_DB:ZZFIN-BONLY", customer_code="ZZFIN-BONLY"
        )
        res = env.push([record])
        out = _record(res, f"{MARKER}:IV:B1")
        assert out["outcome"] == "created"
        assert "customer_unresolved" in out["warnings"]
        assert env.doc(f"{MARKER}:IV:B1").customer_id is None

    def test_read_back_under_the_other_company_is_not_found(self, env):
        env.push_fixture()
        res = env.read(["SRT_DB:IV:1001"], company_code=env.company_b_code)
        assert res.status_code == 200, res.text
        assert res.json()["records"] == []
        assert res.json()["not_found"] == ["SRT_DB:IV:1001"]

    def test_the_same_ref_under_b_is_its_own_document(self, env):
        record = load_fixture()["records"][1]
        env.push([record])
        res = env.push([record], company_code=env.company_b_code)
        assert _record(res, "SRT_DB:CS:2001")["outcome"] == "created"
        b_doc = env.doc("SRT_DB:CS:2001", company_id=env.company_b)
        assert b_doc is not None and str(b_doc.company_id) == env.company_b
        # Company A's masters are not B's.
        assert b_doc.customer_id is None
        assert env.lines(b_doc.id)[0].product_id is None


# ===================================================================== S0-15
class TestAnchor:
    def test_no_company_code_and_no_binding_is_422(self, env):
        res = env.client.post(INGEST, json={"records": [_minimal(f"{MARKER}:IV:A1")]})
        assert res.status_code == 422, res.text
        assert "COMPANY_ANCHOR_REQUIRED" in res.text


# ===================================================================== S0-17
class TestReadBack:
    def test_read_back_is_the_canonical_shape(self, env):
        env.push_fixture()
        res = env.read(["SRT_DB:IV:1001", "SRT_DB:IV:1003", f"{MARKER}:missing"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == [f"{MARKER}:missing"]
        by_ref = {r["source_ref"]: r for r in body["records"]}
        sent = load_fixture()["records"][0]
        iv = by_ref["SRT_DB:IV:1001"]
        assert iv["entity_id"] == str(env.doc("SRT_DB:IV:1001").id)
        for key in (
            "document_type",
            "doc_no",
            "doc_date",
            "status",
            "customer_ref",
            "customer_code",
            "agent_code",
            "currency_code",
            "ref",
        ):
            assert iv[key] == sent[key], key
        for money in ("net_total", "tax_total", "total", "local_net_total"):
            assert isinstance(iv[money], (int, float)), money
            assert iv[money] == sent[money], money
        assert len(iv["lines"]) == 2
        line = next(x for x in iv["lines"] if x["source_ref"] == "SRT_DB:IV:1001:1")
        assert line["entity_id"]
        assert line["product_ref"] == "SRT_DB:ZZFIN-P1"
        assert line["quantity"] == 10
        assert line["line_total"] == 1100
        assert line["from_line_ref"] == SO_LINE_REF
        assert by_ref["SRT_DB:IV:1003"]["currency_rate"] == 4.2


# ===================================================================== S0-19
class TestValidation:
    def test_bad_records_fail_by_field_and_the_rest_lands(self, env):
        good = _minimal(f"{MARKER}:IV:G1")
        extra = _minimal(f"{MARKER}:IV:E1", surprise="x")
        negative = _minimal(f"{MARKER}:IV:N1")
        negative["lines"][0]["quantity"] = -1
        mismatch = _minimal(f"{MARKER}:IV:T1", total=11)
        too_many = _minimal(f"{MARKER}:IV:M1")
        too_many["lines"] = [
            dict(too_many["lines"][0], source_ref=f"{MARKER}:IV:M1:{i}") for i in range(2001)
        ]
        bad_type = _minimal(f"{MARKER}:IV:Y1", document_type="receipt")
        bad_status = _minimal(f"{MARKER}:IV:S1", status="paid")
        dup_lines = _minimal(f"{MARKER}:IV:D1")
        dup_lines["lines"] = dup_lines["lines"] * 2

        res = env.push(
            [good, extra, negative, mismatch, too_many, bad_type, bad_status, dup_lines]
        )
        out = {r["source_ref"]: r for r in res.json()["records"]}
        assert out[f"{MARKER}:IV:G1"]["outcome"] == "created"
        # Owner decision, 1 Oct 2026: an unknown key is dropped (name logged), never
        # refused, so the record carrying `surprise` lands like the good one.
        assert out[f"{MARKER}:IV:E1"]["outcome"] == "created", out[f"{MARKER}:IV:E1"]
        for ref in (
            f"{MARKER}:IV:N1",
            f"{MARKER}:IV:T1",
            f"{MARKER}:IV:M1",
            f"{MARKER}:IV:Y1",
            f"{MARKER}:IV:S1",
            f"{MARKER}:IV:D1",
        ):
            assert out[ref]["outcome"] == "failed", ref
            assert out[ref]["errors"], ref
        assert any("quantity" in k for k in out[f"{MARKER}:IV:N1"]["errors"])
        assert any("total" in k for k in out[f"{MARKER}:IV:T1"]["errors"]) or any(
            "total" in v for v in out[f"{MARKER}:IV:T1"]["errors"].values()
        )
        assert "lines" in out[f"{MARKER}:IV:M1"]["errors"]
        assert "document_type" in out[f"{MARKER}:IV:Y1"]["errors"]
        assert "status" in out[f"{MARKER}:IV:S1"]["errors"]
        assert env.counts()["docs"] == 2

    def test_negative_quantity_is_allowed_on_a_credit_note(self, env):
        cn = _minimal(f"{MARKER}:CN:NEG", document_type="credit_note")
        cn["lines"][0]["quantity"] = -1
        assert _record(env.push([cn]), f"{MARKER}:CN:NEG")["outcome"] == "created"

    def test_total_within_one_sen_is_accepted(self, env):
        record = _minimal(f"{MARKER}:IV:R1", net_total=10.00, tax_total=0.60, total=10.61)
        assert _record(env.push([record]), f"{MARKER}:IV:R1")["outcome"] == "created"


# ===================================================================== S0-22
class TestNoDateFloor:
    def test_2019_2022_and_2023_documents_all_land(self, env):
        res = env.push_fixture("billing_documents_v1_pre2023.json")
        assert set(_outcomes(res).values()) == {"created"}, res.json()
        dates = sorted(
            env.doc(r["source_ref"]).doc_date
            for r in load_fixture("billing_documents_v1_pre2023.json")["records"]
        )
        assert dates == [date(2019, 6, 30), date(2022, 12, 31), date(2023, 1, 1)]


# ============================================ security review round 1 (production shape)
class TestProductionSearchPath:
    """`finance` is not on the production `search_path` ("$user", public). A reference
    existence check that named `billing_documents` bare would fail there and abort the
    transaction, breaking every update, delete and read-back; the blank schema hid it by
    putting `{name}_finance` on the path. Here the path is set the production way."""

    def _production_path(self, env):
        from ._pg_fixture import _BLANK

        name = _BLANK["name"]
        env.db.execute(
            text(
                f'SET LOCAL search_path TO "{name}", "{name}_scm", "{name}_dealer_kit", '
                f'"{name}_chatbot", "{name}_sales", "{name}_projects", "public"'
            )
        )

    def test_replay_cancel_read_and_delete_work_without_finance_on_the_path(self, env):
        env.push_fixture()
        self._production_path(env)

        replay = env.push_fixture()
        assert set(_outcomes(replay).values()) == {"unchanged"}, replay.json()

        cancel = load_fixture("billing_documents_v1_changes.json")["records"][1]
        assert _record(env.push([cancel]), "SRT_DB:CS:2001")["outcome"] == "updated"

        read = env.read(["SRT_DB:IV:1001"])
        assert read.status_code == 200, read.text
        assert read.json()["records"][0]["entity_id"]

        cn = _minimal(
            f"{MARKER}:CN:P1", document_type="credit_note", against_source_ref="SRT_DB:IV:1003"
        )
        assert _record(env.push([cn]), f"{MARKER}:CN:P1")["outcome"] == "created"

        deleted = env.delete(["SRT_DB:DN:3001"])
        assert deleted.json()["records"][0]["outcome"] == "deleted"


class TestOverflowIsOneRecord:
    def test_a_huge_exponent_fails_its_record_and_the_rest_lands(self, env):
        huge = _minimal(
            f"{MARKER}:IV:HUGE",
            net_total="1e999999999",
            tax_total="1e999999999",
            total="1e999999999",
        )
        too_big = _minimal(f"{MARKER}:IV:BIG", net_total=1e14, tax_total=0, total=1e14)
        good = _minimal(f"{MARKER}:IV:OK")
        res = env.push([huge, too_big, good])
        assert res.status_code == 200, res.text
        out = {r["source_ref"]: r for r in res.json()["records"]}
        assert out[f"{MARKER}:IV:HUGE"]["outcome"] == "failed"
        assert "net_total" in out[f"{MARKER}:IV:HUGE"]["errors"]
        assert out[f"{MARKER}:IV:BIG"]["outcome"] == "failed"
        assert "net_total" in out[f"{MARKER}:IV:BIG"]["errors"]
        assert out[f"{MARKER}:IV:OK"]["outcome"] == "created"


# ============================================ review round 1: the survived mutants
class TestReviewRound1:
    def test_another_companys_sales_agent_is_never_linked(self, env):
        env.db.add(SalesAgent(sales_agent="ZZFIN-BAGENT", company_id=env.company_b))
        env.db.commit()
        res = env.push([_minimal(f"{MARKER}:IV:AG", agent_code="ZZFIN-BAGENT")])
        out = _record(res, f"{MARKER}:IV:AG")
        assert out["outcome"] == "created"
        assert "agent_unresolved" in out["warnings"]
        doc = env.doc(f"{MARKER}:IV:AG")
        assert doc.sales_agent_id is None and doc.agent_code == "ZZFIN-BAGENT"

    def test_the_anchors_own_agent_links(self, env):
        agent = SalesAgent(sales_agent="ZZFIN-AAGENT", company_id=env.company_a)
        env.db.add(agent)
        env.db.commit()
        env.push([_minimal(f"{MARKER}:IV:AA", agent_code="zzfin-aagent ")])
        assert str(env.doc(f"{MARKER}:IV:AA").sales_agent_id) == str(agent.id)

    def test_a_push_that_only_adds_a_line_is_an_update(self, env):
        record = _minimal(f"{MARKER}:IV:ADD")
        env.push([record])
        grown = copy.deepcopy(record)
        grown["lines"].append(
            dict(record["lines"][0], source_ref=f"{MARKER}:IV:ADD:2", net_amount=0, line_total=0)
        )
        out = _record(env.push([grown]), f"{MARKER}:IV:ADD")
        assert out["outcome"] == "updated"
        assert out["lines"] == {"created": 1, "updated": 0, "deleted": 0}
        assert len(env.lines(env.doc(f"{MARKER}:IV:ADD").id)) == 2

    def test_late_fill_never_repoints_a_linked_note(self, env):
        env.push_fixture()
        iv3 = env.doc("SRT_DB:IV:1003")
        cn = _minimal(
            f"{MARKER}:CN:L1",
            document_type="credit_note",
            against_source_ref="SRT_DB:IV:1003",
            against_doc_no="IV-LATE/1",
        )
        env.push([cn])
        assert str(env.doc(f"{MARKER}:CN:L1").against_document_id) == str(iv3.id)
        env.push([_minimal(f"{MARKER}:IV:LATE", doc_no="IV-LATE/1")])
        assert str(env.doc(f"{MARKER}:CN:L1").against_document_id) == str(iv3.id)

    def test_an_ambiguous_number_is_never_guessed(self, env):
        env.push([_minimal(f"{MARKER}:IV:D1", doc_no="IV-DUP/1")])
        env.push([_minimal(f"{MARKER}:IV:D2", doc_no="IV-DUP/1")])
        env.push(
            [_minimal(f"{MARKER}:CN:AMB", document_type="credit_note", against_doc_no="IV-DUP/1")]
        )
        # Two invoices carry the number: the note is not linked to either on its own write...
        assert env.doc(f"{MARKER}:CN:AMB").against_document_id is None
        # ...nor by the late fill when one of them is pushed again with a change.
        changed = _minimal(f"{MARKER}:IV:D2", doc_no="IV-DUP/1", description="CHANGED")
        assert _record(env.push([changed]), f"{MARKER}:IV:D2")["outcome"] == "updated"
        assert env.doc(f"{MARKER}:CN:AMB").against_document_id is None

    def test_against_source_ref_naming_another_note_is_not_a_link(self, env):
        env.push_fixture()
        cn = _minimal(
            f"{MARKER}:CN:X", document_type="credit_note", against_source_ref="SRT_DB:CN:1001"
        )
        env.push([cn])
        assert env.doc(f"{MARKER}:CN:X").against_document_id is None
        # So deleting that other note is still a plain delete.
        assert env.delete(["SRT_DB:CN:1001"]).json()["records"][0]["outcome"] == "deleted"

    def test_equal_timestamp_with_new_content_applies(self, env):
        env.push_fixture()
        same = copy.deepcopy(load_fixture()["records"][0])
        same["ref"] = "SAME TIME NEW REF"
        assert _record(env.push([same]), "SRT_DB:IV:1001")["outcome"] == "updated"

    def test_an_offset_timestamp_is_stored_as_naive_utc(self, env):
        record = _minimal(f"{MARKER}:IV:TZ", source_modified_at="2026-09-10T17:00:00+08:00")
        env.push([record])
        from datetime import datetime

        assert env.doc(f"{MARKER}:IV:TZ").source_modified_at == datetime(2026, 9, 10, 9, 0, 0)
        older = dict(record, source_modified_at="2026-09-10T08:59:59Z", ref="OLDER")
        out = _record(env.push([older]), f"{MARKER}:IV:TZ")
        assert out["outcome"] == "unchanged" and "stale_ignored" in out["warnings"]

    def test_more_decimals_than_the_column_replays_unchanged(self, env):
        record = _minimal(
            f"{MARKER}:IV:DP", net_total="10.004", tax_total="0", total="10.004",
            local_net_total="10.004",
        )
        record["lines"][0]["quantity"] = "1.00004"
        assert _record(env.push([record]), f"{MARKER}:IV:DP")["outcome"] == "created"
        assert env.doc(f"{MARKER}:IV:DP").net_total == Decimal("10.00")
        assert _record(env.push([record]), f"{MARKER}:IV:DP")["outcome"] == "unchanged"

    def test_the_largest_column_value_lands_and_one_sen_more_fails(self, env):
        top = "9999999999999.99"
        ok = _minimal(f"{MARKER}:IV:TOP", net_total=top, tax_total="0", total=top, local_net_total=top)
        over = _minimal(
            f"{MARKER}:IV:OVER", net_total="9999999999999.995", tax_total="0",
            total="9999999999999.995", local_net_total="0",
        )
        out = {r["source_ref"]: r for r in env.push([ok, over]).json()["records"]}
        assert out[f"{MARKER}:IV:TOP"]["outcome"] == "created"
        assert out[f"{MARKER}:IV:OVER"]["outcome"] == "failed"
        assert "net_total" in out[f"{MARKER}:IV:OVER"]["errors"]
