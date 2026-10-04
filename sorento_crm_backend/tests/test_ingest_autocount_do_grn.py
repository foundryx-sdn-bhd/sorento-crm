"""AutoCount Delivery Orders and Goods Receive Notes on the ingest surface (#1354 S2, contract 2.7).

UAC: documentation/plans/autocount/autocount-grn-do-ingest-29sep-acceptance-criteria.md.
Each test names the AC ids it pins. The fixtures recreate the live payloads' field lists with
fake values; this file seeds every master they name, because CI's database holds no data.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.autocount_branch import Branch
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.order import Customer, Order, OrderLine, OrderStatus, SalesOrder, SalesOrderLine
from app.models.procurement import (
    PickingHeader,
    PickingLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZAC"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "autocount"

DO_INGEST = "/api/v1/external/ingest/delivery_orders"
GRN_INGEST = "/api/v1/external/ingest/goods_receive_notes"
BR_INGEST = "/api/v1/external/ingest/branches"
DO_DELETE = "/api/v1/external/ingest/delivery_orders/deletions"
GRN_DELETE = "/api/v1/external/ingest/goods_receive_notes/deletions"
DO_READ = "/api/v1/external/read/delivery_orders"
GRN_READ = "/api/v1/external/read/goods_receive_notes"

_USER_ID = "6c8b9c10-1111-4222-8333-4444555566a1"
_ROLE_ID = "6c8b9c10-2222-4222-8333-4444555566a2"


def load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["records"]


def do_records() -> list[dict]:
    return load("do_live_sample.json")


def grn_records() -> list[dict]:
    return load("grn_live_sample.json")


def _seed_principal(db) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(UserRole(id=_ROLE_ID, slug="superadmin", name=f"{MARKER} Superadmin",
                    description="", is_protected=True, is_default=False))
    db.flush()
    db.add(User(id=_USER_ID, name=f"{MARKER} admin", email=f"{MARKER.lower()}-admin@test.com",
                password="x", status="active"))
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.flush()


class _Env:
    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company = DEFAULT_COMPANY_ID
        self.company_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company}
        ).scalar()
        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZA{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_b_code = other.code

        category = ProductCategory(category_code=unique_code(MARKER), category_name="c")
        uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name="u")
        db.add_all([category, uom])
        db.flush()
        self._category_id, self._uom_id = category.id, uom.id
        self.p1 = self.product("ZZAC-P1")
        self.p2 = self.product("ZZAC-P2")
        wh = Warehouse(warehouse_code="ZZAC-WH1", warehouse_name="wh", company_id=self.company)
        db.add(wh)
        db.flush()
        self.wh1 = str(wh.id)
        cust = Customer(customer_code="300-ZZAC01", customer_name="ZZAC Customer One",
                        company_id=self.company)
        db.add(cust)
        db.flush()
        self.customer = str(cust.id)
        status = OrderStatus(status_code=f"new", status_name="New Order")
        existing = db.execute(
            select(OrderStatus).where(OrderStatus.status_code.in_(["new", "NEW"]))
        ).scalars().first()
        if existing is None:
            db.add(status)
            db.flush()
            self.new_status = str(status.id)
        else:
            self.new_status = str(existing.id)
        db.commit()

    def product(self, code: str, company_id: str | None = None) -> str:
        row = Product(product_code=code, product_name=code, category_id=self._category_id,
                      base_uom_id=self._uom_id, list_price=10,
                      company_id=company_id or self.company)
        self.db.add(row)
        self.db.flush()
        return str(row.id)

    # ----------------------------------------------------------------- calls
    def post(self, url, records, *, book="db1", dry_run=False, company_code=None):
        body = {"companyCode": company_code or self.company_code, "records": records}
        if book is not None:
            body["book"] = book
        return self.client.post(f"{url}?dry_run=true" if dry_run else url, json=body)

    def push_do(self, records=None, **kw):
        return self.post(DO_INGEST, do_records() if records is None else records, **kw)

    def push_grn(self, records=None, **kw):
        return self.post(GRN_INGEST, grn_records() if records is None else records, **kw)

    def delete(self, url, doc_keys, date_from="2026-01-01", date_to="2026-12-31", **extra):
        body = {"companyCode": self.company_code, "book": "db1", "doc_date_from": date_from,
                "doc_date_to": date_to, "doc_keys": doc_keys, **extra}
        return self.client.post(url, json=body)

    # ----------------------------------------------------------------- reads
    def order(self, doc_key: int) -> Order | None:
        self.db.expire_all()
        return self.db.execute(
            select(Order).where(Order.company_id == self.company, Order.doc_key == doc_key)
        ).scalars().one_or_none()

    def order_lines(self, order_id) -> list[OrderLine]:
        self.db.expire_all()
        return self.db.execute(
            select(OrderLine).where(OrderLine.order_id == order_id)
            .order_by(OrderLine.line_sequence)
        ).scalars().all()

    def grn(self, doc_key: int) -> PickingHeader | None:
        self.db.expire_all()
        return self.db.execute(
            select(PickingHeader).where(PickingHeader.company_id == self.company,
                                        PickingHeader.doc_key == doc_key)
        ).scalars().one_or_none()

    def grn_lines(self, header_id) -> list[PickingLine]:
        self.db.expire_all()
        return self.db.execute(
            select(PickingLine).where(PickingLine.picking_header_id == header_id)
            .order_by(PickingLine.seq)
        ).scalars().all()


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


def _records(res) -> dict[str, dict]:
    assert res.status_code == 200, res.text
    return {r["source_ref"]: r for r in res.json()["records"]}


def _outcomes(res) -> dict[str, str]:
    return {ref: r["outcome"] for ref, r in _records(res).items()}


# ======================================================================= endpoints and verdicts
def test_do_sample_creates_orders_and_lines(env):
    """AC-AG001, AC-AG030."""
    res = env.push_do()
    recs = _records(res)
    assert {ref: r["outcome"] for ref, r in recs.items()} == {
        "db1:DO:900001": "created", "db1:DO:900002": "created"}
    first = recs["db1:DO:900001"]
    assert first["lines"]["created"] == 2
    assert first["lines"]["unlinked"] == 2 and first["lines"]["linked"] == 0
    order = env.order(900001)
    assert order is not None and first["entity_id"] == str(order.id)
    assert len(env.order_lines(order.id)) == 2
    assert all(line.sales_order_line_id is None for line in env.order_lines(order.id))
    assert len(env.order_lines(env.order(900002).id)) == 1


def test_grn_sample_creates_picking_rows(env):
    """AC-AG002."""
    res = env.push_grn()
    assert _outcomes(res) == {"db1:GRN:800001": "created"}
    grn = env.grn(800001)
    assert grn.picking_type == "goods_received"
    assert grn.picking_status == "approved"
    assert grn.source_system == "autocount"
    assert grn.picking_number == "ZZGRN-0001"
    lines = env.grn_lines(grn.id)
    assert len(lines) == 2
    assert all(line.po_line_id is None and line.spo_allocation_id is None for line in lines)


def test_repush_unchanged_writes_nothing(env):
    """AC-AG003."""
    env.push_do()
    env.push_grn()
    before_do = (env.order(900001).updated_at, env.order(900001).last_synced_at)
    before_grn = (env.grn(800001).updated_at, env.grn(800001).last_synced_at)
    assert set(_outcomes(env.push_do()).values()) == {"unchanged"}
    assert set(_outcomes(env.push_grn()).values()) == {"unchanged"}
    assert (env.order(900001).updated_at, env.order(900001).last_synced_at) == before_do
    assert (env.grn(800001).updated_at, env.grn(800001).last_synced_at) == before_grn


def test_later_last_modified_updates_and_replaces_lines(env):
    """AC-AG004."""
    env.push_do()
    order = env.order(900001)
    kept_id = next(l.id for l in env.order_lines(order.id) if l.dtl_key == 910001)
    rec = copy.deepcopy(do_records()[0])
    rec["LastModified"] = "2026-09-28T08:00:00.000"
    rec["Remark1"] = "Changed remark"
    rec["Details"][0]["Qty"] = 12.0
    rec["Details"].pop(1)  # DtlKey 910002 dropped
    new_line = copy.deepcopy(rec["Details"][0])
    new_line.update({"DtlKey": 910009, "Seq": 48, "ItemCode": "ZZAC-P2", "Qty": 1.0})
    rec["Details"].append(new_line)
    res = env.push_do([rec])
    r = _records(res)["db1:DO:900001"]
    assert r["outcome"] == "updated"
    assert r["lines"]["updated"] == 1 and r["lines"]["deleted"] == 1 and r["lines"]["created"] == 1
    order = env.order(900001)
    assert order.remarks == "Changed remark"
    lines = {l.dtl_key: l for l in env.order_lines(order.id)}
    assert set(lines) == {910001, 910009}
    assert lines[910001].id == kept_id
    assert lines[910001].quantity == Decimal("12")


def test_older_last_modified_is_stale(env):
    """AC-AG005."""
    env.push_do()
    rec = copy.deepcopy(do_records()[0])
    rec["LastModified"] = "2026-09-26T08:00:00.000"
    rec["Remark1"] = "Older content"
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert r["outcome"] == "unchanged"
    assert "stale_ignored" in r["warnings"]
    assert env.order(900001).remarks == "Deliver before noon"


def test_same_last_modified_new_content_updates(env):
    """AC-AG006."""
    env.push_do()
    rec = copy.deepcopy(do_records()[0])
    rec["ShipVia"] = "COURIER"
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "updated"
    assert env.order(900001).ship_via == "COURIER"


def test_cancelled_document_is_an_update(env):
    """AC-AG007."""
    env.push_do()
    env.push_grn()
    do = copy.deepcopy(do_records()[0])
    do["Cancelled"] = "T"
    do["LastModified"] = "2026-09-28T08:00:00.000"
    grn = copy.deepcopy(grn_records()[0])
    grn["Cancelled"] = "T"
    grn["LastModified"] = "2026-09-28T08:00:00.000"
    assert _outcomes(env.push_do([do]))["db1:DO:900001"] == "updated"
    assert _outcomes(env.push_grn([grn]))["db1:GRN:800001"] == "updated"
    order = env.order(900001)
    assert order.is_cancelled is True
    assert len(env.order_lines(order.id)) == 2
    header = env.grn(800001)
    assert header.is_cancelled is True and header.picking_status == "cancelled"
    assert len(env.grn_lines(header.id)) == 2


def test_dry_run_writes_nothing(env):
    """AC-AG008."""
    res = env.push_do(dry_run=True)
    assert res.json()["dry_run"] is True
    assert set(_outcomes(res).values()) == {"created"}
    assert env.order(900001) is None
    res = env.push_grn(dry_run=True)
    assert set(_outcomes(res).values()) == {"created"}
    assert env.grn(800001) is None


def test_per_record_validation(env):
    """AC-AG009."""
    good = do_records()[0]
    no_key = copy.deepcopy(good)
    no_key.pop("DocKey")
    no_no = copy.deepcopy(good)
    no_no["DocKey"] = 900101
    no_no["DocNo"] = ""
    bad_date = copy.deepcopy(good)
    bad_date["DocKey"] = 900102
    bad_date["DocNo"] = "ZZDO-0102"
    bad_date["DocDate"] = "not a date"
    no_dtl = copy.deepcopy(good)
    no_dtl["DocKey"] = 900103
    no_dtl["DocNo"] = "ZZDO-0103"
    no_dtl["Details"][0].pop("DtlKey")
    dup = copy.deepcopy(good)
    dup["DocKey"] = 900104
    dup["DocNo"] = "ZZDO-0104"
    dup["Details"][1]["DtlKey"] = dup["Details"][0]["DtlKey"]
    res = env.push_do([no_key, no_no, bad_date, no_dtl, dup, good])
    body = res.json()["records"]
    assert [r["outcome"] for r in body] == ["failed"] * 5 + ["created"]
    assert "DocKey" in body[0]["errors"]
    assert "DocNo" in body[1]["errors"]
    assert "DocDate" in body[2]["errors"]
    assert "Details.0.DtlKey" in body[3]["errors"]
    assert "Details" in body[4]["errors"]


def test_envelope_errors(env):
    """AC-AG010."""
    res = env.post(DO_INGEST, do_records(), book=None)
    assert res.status_code == 422 and res.json()["code"] == "INVALID_BODY"
    res = env.post(DO_INGEST, do_records(), book="db 1; drop")
    assert res.status_code == 422 and res.json()["code"] == "INVALID_BODY"
    res = env.post(DO_INGEST, [do_records()[0]] * 1001)
    assert res.status_code == 413
    res = env.client.post(DO_INGEST, json={"book": "db1", "records": do_records()})
    assert res.status_code == 422
    assert res.json()["code"] == "COMPANY_ANCHOR_REQUIRED"


def test_unresolved_item_or_location_is_retryable(env):
    """AC-AG011."""
    rec = copy.deepcopy(do_records()[0])
    rec["Details"][1]["ItemCode"] = "ZZAC-NOPE"
    loc = copy.deepcopy(do_records()[1])
    loc["Details"][0]["Location"] = "ZZAC-NOWH"
    recs = _records(env.push_do([rec, loc]))
    assert recs["db1:DO:900001"]["outcome"] == "retryable"
    assert "Details.1.ItemCode" in recs["db1:DO:900001"]["errors"]
    assert recs["db1:DO:900002"]["outcome"] == "retryable"
    assert "Details.0.Location" in recs["db1:DO:900002"]["errors"]
    assert env.order(900001) is None and env.order(900002) is None


def test_line_without_item_is_skipped(env):
    """AC-AG012."""
    rec = copy.deepcopy(do_records()[0])
    note = copy.deepcopy(rec["Details"][0])
    note.update({"DtlKey": 910050, "Seq": 64, "ItemCode": None, "Description": "NOTE ROW",
                 "Location": None, "Qty": 0})
    rec["Details"].append(note)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert r["outcome"] == "created"
    assert r["lines"]["skipped"] == 1 and r["lines"]["created"] == 2
    assert "line_without_item" in r["warnings"]
    order = env.order(900001)
    assert len(env.order_lines(order.id)) == 2
    assert order.source_record["Details"][2]["Description"] == "NOTE ROW"


def test_read_back(env):
    """AC-AG013."""
    env.push_do()
    env.push_grn()
    res = env.client.post(DO_READ, json={"companyCode": env.company_code,
                                         "source_refs": ["db1:DO:900001", "db1:DO:1", "junk"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body["not_found"]) == ["db1:DO:1", "junk"]
    (rec,) = body["records"]
    assert rec["doc_no"] == "ZZDO-0001" and rec["doc_key"] == 900001
    assert len(rec["lines"]) == 2 and rec["lines"][0]["dtl_key"] == 910001
    res = env.client.post(GRN_READ, json={"companyCode": env.company_code,
                                          "source_refs": ["db1:GRN:800001"]})
    assert res.json()["records"][0]["doc_no"] == "ZZGRN-0001"
    # Another company's anchor does not see it.
    res = env.client.post(DO_READ, json={"companyCode": env.company_b_code,
                                         "source_refs": ["db1:DO:900001"]})
    assert res.json()["not_found"] == ["db1:DO:900001"]


def test_contract_lists_2_7_entities(env):
    """AC-AG014."""
    body = env.client.get("/api/v1/external/contract").json()
    assert body["version"] == "2.7"
    for entity in ("delivery_orders", "goods_receive_notes", "branches"):
        assert entity in body["entities"]


def test_slugs_and_branch_read_door(env):
    """AC-AG015."""
    from app.api.v1.external.ingest import DELETE_PERMISSIONS, INGEST_PERMISSIONS, READ_PERMISSIONS

    assert INGEST_PERMISSIONS["delivery_orders"] == "order_management.orders.edit"
    assert INGEST_PERMISSIONS["goods_receive_notes"] == "procurement.grn.edit"
    assert INGEST_PERMISSIONS["branches"] == "order_management.branches.edit"
    assert READ_PERMISSIONS["delivery_orders"] == "order_management.orders.view"
    assert DELETE_PERMISSIONS["goods_receive_notes"] == "procurement.grn.delete"
    res = env.client.post("/api/v1/external/read/branches",
                          json={"companyCode": env.company_code, "source_refs": []})
    assert res.status_code == 404
    res = env.client.post("/api/v1/external/ingest/branches/deletions",
                          json={"companyCode": env.company_code, "source_refs": []})
    assert res.status_code == 404


# ======================================================================= column groups
def test_do_header_columns(env):
    """AC-AG020, AC-AG021, AC-AG022."""
    env.push_do()
    o = env.order(900001)
    sent = do_records()[0]
    assert o.order_number == "ZZDO-0001"
    assert o.order_date == date(2026, 9, 27)
    assert o.created_time == datetime(2026, 9, 27, 9, 12, 44, 120000)
    assert (o.debtor_code, o.debtor_name, o.customer_id) == (
        "300-ZZAC01", "ZZAC Customer One Sdn Bhd", env.customer)
    assert o.agent == "ZZAC-AG"
    assert (o.ship_via, o.ship_info, o.ref, o.ref_doc_no) == ("LORRY", None, "Yu's iP-000001", None)
    assert o.remarks == "Deliver before noon"
    assert o.description == "DELIVERY ORDER"
    assert o.doc_status == "F"
    assert o.currency_code == "MYR" and o.currency_rate == Decimal("1")
    assert o.subtotal_amount == Decimal("245.00")
    assert o.tax_amount == Decimal("0.00")
    assert o.total_amount == Decimal("245.00")
    assert o.local_net_total == Decimal("245.00")
    assert o.deliver_address == "No 1, Jalan Contoh\nTaman Sampel\n47000 Sungai Buloh"
    assert (o.deliver_contact, o.deliver_phone) == ("Mr Contoh", "012-0000000")
    assert o.source_book == "db1" and o.doc_key == 900001
    # 15:37:53.367 Malaysia time is 07:37:53.367 UTC.
    assert o.source_modified_at == datetime(2026, 9, 27, 7, 37, 53, 367000, tzinfo=timezone.utc)
    assert o.source_record == sent
    assert o.is_cancelled is False
    assert str(o.order_status_id) == env.new_status


def test_do_line_columns(env):
    """AC-AG023."""
    env.push_do()
    line = env.order_lines(env.order(900001).id)[0]
    assert line.dtl_key == 910001 and line.line_sequence == 16
    assert line.item_code == "ZZAC-P1" and line.product_id == env.p1
    assert line.location_code == "ZZAC-WH1" and line.warehouse_id == env.wh1
    assert line.description == "Item ZZAC-P1"
    assert line.quantity == Decimal("10") and line.foc_qty == Decimal("0")
    assert line.uom == "PCS"
    assert line.unit_price == Decimal("12.5")
    assert line.discount_text is None and line.discount == Decimal("0")
    assert line.total == Decimal("125")
    assert line.batch_no is None and line.proj_no is None
    assert line.delivery_date == date(2026, 9, 27)
    assert line.your_po_no is None and line.your_po_date is None


def test_grn_header_and_line_columns(env):
    """AC-AG024, AC-AG025."""
    env.push_grn()
    h = env.grn(800001)
    assert (h.creditor_code, h.creditor_name) == ("400-ZZAC01", "ZZAC Supplier One Co Ltd")
    assert h.supplier_do_no == "SDO-000001" and h.purchase_agent == "ZZAC-PA"
    assert h.ref is None and h.remarks is None and h.description == "GOODS RECEIVED NOTE"
    assert h.picking_date == date(2026, 7, 27)
    assert h.total_amount == Decimal("745.00")
    assert h.source_book == "db1" and h.doc_key == 800001
    assert h.source_record == grn_records()[0]
    line = env.grn_lines(h.id)[0]
    assert line.dtl_key == 810001 and line.seq == 16
    assert line.item_code == "ZZAC-P1" and line.product_id == env.p1
    assert line.qty == Decimal("100")
    assert line.quantity_picked == 100 and line.quantity_expected == 100
    assert line.unit_cost == Decimal("5.25") and line.line_total == Decimal("525.00")
    assert line.destination_warehouse_id == env.wh1
    assert line.batch_number_picked == "B-01"
    assert line.our_po_no is None and line.our_po_date is None
    assert line.spo_number_raw is None


def test_unresolved_customer_keeps_code(env):
    """AC-AG026."""
    rec = copy.deepcopy(do_records()[0])
    rec["DebtorCode"] = "300-ZZNOPE"
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert r["outcome"] == "created" and "customer_unresolved" in r["warnings"]
    o = env.order(900001)
    assert o.customer_id is None and o.debtor_code == "300-ZZNOPE"


# ======================================================================= link rules
def _seed_so(env, so_number="ZZSO-0001", dtl_ref="SRT_DB:4001:5001") -> tuple[str, str]:
    so = SalesOrder(so_number=so_number, company_id=env.company, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    line = SalesOrderLine(sales_order_id=so.id, product_id=env.p1, qty_ordered=10,
                          source_ref=dtl_ref, company_id=env.company)
    env.db.add(line)
    env.db.commit()
    return str(so.id), str(line.id)


def _with_from(rec: dict, idx: int, doc_type: str, doc_no: str, dtl: int) -> dict:
    rec["Details"][idx].update({"FromDocType": doc_type, "FromDocNo": doc_no, "FromDocDtlKey": dtl})
    return rec


def test_exact_so_line_link(env):
    """AC-AG031."""
    _, so_line = _seed_so(env)
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 5001)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert r["lines"]["linked"] == 1 and r["lines"]["unlinked"] == 1
    lines = {l.dtl_key: l for l in env.order_lines(env.order(900001).id)}
    assert lines[910001].sales_order_line_id == so_line
    assert (lines[910001].from_doc_type, lines[910001].from_doc_no, lines[910001].from_dtl_key) == (
        "SO", "ZZSO-0001", 5001)


def test_exact_so_line_unresolved(env):
    """AC-AG032."""
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0404", 5404)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert r["outcome"] == "created" and "so_line_unresolved" in r["warnings"]
    line = env.order_lines(env.order(900001).id)[0]
    assert line.sales_order_line_id is None and line.from_dtl_key == 5404


def test_exact_po_and_spo_line_links(env):
    """AC-AG033."""
    po = PurchaseOrder(po_number="ZZPO-0001", company_id=env.company)
    env.db.add(po)
    env.db.flush()
    po_line = PurchaseOrderLine(purchase_order_id=po.id, product_id=env.p1, qty_ordered=100,
                                source_ref="SRT_DB:7001:7101", company_id=env.company)
    spo = SPOAllocation(spo_number="SPO-ZZ-0001", product_id=env.p2, allocated_quantity=20,
                        source_ref="SRT_DB:7002:7201", company_id=env.company)
    env.db.add_all([po_line, spo])
    env.db.commit()
    rec = copy.deepcopy(grn_records()[0])
    _with_from(rec, 0, "PO", "ZZPO-0001", 7101)
    _with_from(rec, 1, "PO", "SPO-ZZ-0001", 7201)
    r = _records(env.push_grn([rec]))["db1:GRN:800001"]
    assert r["lines"]["linked"] == 2
    lines = {l.dtl_key: l for l in env.grn_lines(env.grn(800001).id)}
    assert lines[810001].po_line_id == str(po_line.id)
    assert lines[810002].spo_allocation_id == str(spo.id)


def test_do_ref_doc_no_links_the_sales_order_only(env):
    """AC-AG034."""
    so_id, _ = _seed_so(env)
    rec = copy.deepcopy(do_records()[0])
    rec["RefDocNo"] = "ZZSO-0001"
    other = copy.deepcopy(do_records()[1])
    other["RefDocNo"] = "ZZSO-9999"
    recs = _records(env.push_do([rec, other]))
    o = env.order(900001)
    assert o.sales_order_id == so_id
    assert all(l.sales_order_line_id is None for l in env.order_lines(o.id))
    assert "sales_order_unresolved" in recs["db1:DO:900002"]["warnings"]
    assert env.order(900002).sales_order_id is None


def test_grn_our_po_no_links_the_purchase_order_only(env):
    """AC-AG035."""
    po = PurchaseOrder(po_number="ZZPO-0002", company_id=env.company)
    env.db.add(po)
    env.db.commit()
    rec = copy.deepcopy(grn_records()[0])
    rec["Details"][0]["OurPONo"] = "ZZPO-0002"
    rec["Details"][1]["OurPONo"] = "ZZPO-NONE"
    r = _records(env.push_grn([rec]))["db1:GRN:800001"]
    assert "purchase_order_unresolved" in r["warnings"]
    lines = {l.dtl_key: l for l in env.grn_lines(env.grn(800001).id)}
    assert lines[810001].purchase_order_id == str(po.id)
    assert lines[810001].from_doc_type == "PO"
    assert lines[810001].po_line_id is None and lines[810001].spo_number_raw is None
    assert lines[810002].purchase_order_id is None


def test_waiting_link_filled_when_so_arrives_later(env):
    """AC-AG036."""
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 5001)
    env.push_do([rec])
    line = env.order_lines(env.order(900001).id)[0]
    assert line.sales_order_line_id is None
    _, so_line = _seed_so(env)
    # Any later non-dry DO batch fills it, here one for a different document.
    env.push_do([do_records()[1]])
    line = env.order_lines(env.order(900001).id)[0]
    assert line.sales_order_line_id == so_line


def test_later_payload_with_link_fills_it(env):
    """AC-AG037."""
    _, so_line = _seed_so(env)
    env.push_do([do_records()[0]])
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 5001)
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "updated"
    line = env.order_lines(env.order(900001).id)[0]
    assert line.sales_order_line_id == so_line


# ======================================================================= FromDocDtlKey 0 (DO-SO-LINE-LINK)
# AutoCount sends FromDocDtlKey 0 on every DO line (3,841 of 3,841 in the 01-03 Sep snapshot).
def test_from_dtl_key_zero_links_by_doc_no_and_item(env):
    """AC-DSL001."""
    _, so_line = _seed_so(env)
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 0)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "so_line_unresolved" not in r.get("warnings", [])
    assert r["lines"]["linked"] == 1
    line = {l.dtl_key: l for l in env.order_lines(env.order(900001).id)}[910001]
    assert line.sales_order_line_id == so_line
    assert (line.from_doc_type, line.from_doc_no, line.from_dtl_key) == ("SO", "ZZSO-0001", None)


@pytest.mark.parametrize("absent", ["missing", "null"])
def test_missing_from_dtl_key_links_by_doc_no_and_item(env, absent):
    """AC-DSL002."""
    _, so_line = _seed_so(env)
    rec = copy.deepcopy(do_records()[0])
    rec["Details"][0].update({"FromDocType": "SO", "FromDocNo": "ZZSO-0001"})
    if absent == "missing":
        rec["Details"][0].pop("FromDocDtlKey", None)
    else:
        rec["Details"][0]["FromDocDtlKey"] = None
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "so_line_unresolved" not in r.get("warnings", [])
    line = {l.dtl_key: l for l in env.order_lines(env.order(900001).id)}[910001]
    assert line.sales_order_line_id == so_line


def test_from_dtl_key_zero_without_a_matching_so_line_warns(env):
    """AC-DSL003: the SO holds no line of this product, or the SO is not here."""
    _seed_so(env)  # one line, product P1
    rec = copy.deepcopy(do_records()[0])
    _with_from(rec, 1, "SO", "ZZSO-0001", 0)  # line 1 is product P2
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "so_line_unresolved" in r["warnings"]
    assert all(l.sales_order_line_id is None for l in env.order_lines(env.order(900001).id))

    other = _with_from(copy.deepcopy(do_records()[1]), 0, "SO", "ZZSO-0404", 0)
    r = _records(env.push_do([other]))["db1:DO:900002"]
    assert "so_line_unresolved" in r["warnings"]


def test_from_dtl_key_zero_with_two_candidate_so_lines_warns(env):
    """AC-DSL004: the product sits on two lines of the SO; no guess."""
    so_id, _ = _seed_so(env)
    env.db.add(SalesOrderLine(sales_order_id=so_id, product_id=env.p1, qty_ordered=3,
                              source_ref="SRT_DB:4001:5002", company_id=env.company))
    env.db.commit()
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 0)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "so_line_unresolved" in r["warnings"]
    assert all(l.sales_order_line_id is None for l in env.order_lines(env.order(900001).id))


def test_from_dtl_key_zero_waiting_link_filled_when_so_arrives(env):
    """AC-DSL005, including a row the old code stored with from_dtl_key 0."""
    # 900001 is stored as this ingest stores it (NULL); 900002 as the pre-fix ingest did (0).
    env.push_do([_with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 0),
                 _with_from(copy.deepcopy(do_records()[1]), 0, "SO", "ZZSO-0001", 0)])
    fresh = env.order_lines(env.order(900001).id)[0]
    legacy = env.order_lines(env.order(900002).id)[0]
    assert fresh.from_dtl_key is None
    assert fresh.sales_order_line_id is None and legacy.sales_order_line_id is None
    legacy.from_dtl_key = 0
    env.db.commit()
    _, so_line = _seed_so(env)
    # Any later non-dry DO batch fills both, here one for a third document.
    env.push_do([_another(do_records()[1], 900003, "ZZDO-0003", 910009)])
    assert env.order_lines(env.order(900001).id)[0].sales_order_line_id == so_line
    assert env.order_lines(env.order(900002).id)[0].sales_order_line_id == so_line


def test_from_dtl_key_zero_never_links_to_company_b(env):
    """AC-DSL001 scope: the same SO number and product in another company is not a match."""
    so = SalesOrder(so_number="ZZSO-0001", company_id=env.company_b, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    env.db.add(SalesOrderLine(sales_order_id=so.id, product_id=env.p1, qty_ordered=10,
                              source_ref="SRT_DB:4001:5001", company_id=env.company_b))
    env.db.commit()
    rec = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 0)
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "so_line_unresolved" in r["warnings"]
    assert all(l.sales_order_line_id is None for l in env.order_lines(env.order(900001).id))


def test_grn_from_dtl_key_zero_falls_back_to_our_po_no(env):
    """AC-DSL006."""
    po = PurchaseOrder(po_number="ZZPO-0002", company_id=env.company)
    env.db.add(po)
    env.db.commit()
    rec = copy.deepcopy(grn_records()[0])
    _with_from(rec, 0, "PO", "ZZPO-0002", 0)
    rec["Details"][0]["OurPONo"] = "ZZPO-0002"
    r = _records(env.push_grn([rec]))["db1:GRN:800001"]
    assert "po_line_unresolved" not in r.get("warnings", [])
    line = {l.dtl_key: l for l in env.grn_lines(env.grn(800001).id)}[810001]
    assert line.purchase_order_id == str(po.id)
    assert line.from_dtl_key is None


def test_grn_stored_from_dtl_key_zero_heals_by_our_po_no(env):
    """AC-DSL006: a GRN line the pre-fix ingest stored with from_dtl_key 0 links its purchase
    order by OurPONo once the PO arrives."""
    rec = copy.deepcopy(grn_records()[0])
    _with_from(rec, 0, "PO", "ZZPO-0002", 0)
    rec["Details"][0]["OurPONo"] = "ZZPO-0002"
    env.push_grn([rec])
    line = {l.dtl_key: l for l in env.grn_lines(env.grn(800001).id)}[810001]
    assert line.purchase_order_id is None
    line.from_dtl_key = 0
    env.db.commit()
    po = PurchaseOrder(po_number="ZZPO-0002", company_id=env.company)
    env.db.add(po)
    env.db.commit()
    env.push_grn([_another(grn_records()[0], 800009, "ZZGRN-0009", 810090)])
    line = {l.dtl_key: l for l in env.grn_lines(env.grn(800001).id)}[810001]
    assert line.purchase_order_id == str(po.id)


def _another(rec: dict, doc_key: int, doc_no: str, first_dtl: int) -> dict:
    """A copy of `rec` as a different document, so a batch can run without touching `rec`."""
    out = copy.deepcopy(rec)
    out.update({"DocKey": doc_key, "DocNo": doc_no})
    for offset, detail in enumerate(out["Details"]):
        detail["DtlKey"] = first_dtl + offset
    return out


# ======================================================================= ownership
TRACKING_COLUMNS = (
    "actual_delivery_date", "pickup_time", "transporter", "transporter_id", "driver_name",
    "lorry_plate", "checker", "trips", "delivery_days", "kpi_warning", "customer_ref",
    "salesman", "warehouse", "delivery_remarks", "delivery_remarks_cs", "remarks_cs",
    "order_status_id", "order_type", "estimated_delivery_date",
)


def _tracking_row(env) -> Order:
    row = Order(
        order_number="ZZDO-0001", company_id=env.company, order_date=date(2026, 9, 26),
        actual_delivery_date=date(2026, 9, 28), pickup_time="14:30", transporter="ZZ TRANS",
        driver_name="Ali", lorry_plate="PLATE-41", checker="Chong", trips=2, delivery_days=1,
        kpi_warning=False, customer_ref="iPad ref", salesman="SEAN", warehouse="BRW",
        delivery_remarks="dr", delivery_remarks_cs="drcs", remarks_cs="rcs",
        order_status_id=env.new_status, debtor_code="OLD", debtor_name="Old name",
        order_type="TRUCK", estimated_delivery_date=date(2026, 9, 28),
    )
    env.db.add(row)
    env.db.flush()
    env.db.add(OrderLine(order_id=row.id, line_sequence=1, product_id=env.p1,
                         warehouse_id=env.wh1, quantity=Decimal("10")))
    env.db.add(OrderLine(order_id=row.id, line_sequence=2, product_id=env.p2,
                         warehouse_id=env.wh1, quantity=Decimal("99")))
    env.db.commit()
    return row


def _tracking_snapshot(order: Order) -> dict:
    return {c: getattr(order, c) for c in TRACKING_COLUMNS}


def test_adopt_tracking_row_by_number(env):
    """AC-AG040, AC-AG041."""
    row = _tracking_row(env)
    row_id = str(row.id)
    env.db.expire_all()
    old_lines = {l.product_id: l.id for l in env.order_lines(row_id)}
    before = _tracking_snapshot(env.db.get(Order, row_id))
    r = _records(env.push_do([do_records()[0]]))["db1:DO:900001"]
    assert r["outcome"] == "updated" and "adopted_by_doc_no" in r["warnings"]
    assert r["entity_id"] == row_id
    assert r["lines"]["adopted"] == 1 and r["lines"]["deleted"] == 1
    o = env.order(900001)
    assert str(o.id) == row_id
    assert _tracking_snapshot(o) == before
    assert o.debtor_name == "ZZAC Customer One Sdn Bhd"
    lines = env.order_lines(o.id)
    assert {l.dtl_key for l in lines} == {910001, 910002}
    # P1 x 10 at ZZAC-WH1 matched the old line and kept its id; the P2 x 99 line went.
    assert next(l for l in lines if l.dtl_key == 910001).id == old_lines[env.p1]
    assert next(l for l in lines if l.dtl_key == 910002).id != old_lines[env.p2]


def test_update_after_adopt_leaves_tracking_columns(env):
    """AC-AG042."""
    _tracking_row(env)
    env.push_do([do_records()[0]])
    before = _tracking_snapshot(env.order(900001))
    rec = copy.deepcopy(do_records()[0])
    rec["LastModified"] = "2026-09-29T08:00:00.000"
    rec["Ref"] = "new ref"
    rec["SalesAgent"] = "OTHER"
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "updated"
    o = env.order(900001)
    assert _tracking_snapshot(o) == before
    assert o.ref == "new ref" and o.agent == "OTHER"


def test_ingest_header_write_stays_in_autocount_columns(env, monkeypatch):
    """DO-OWNERSHIP-GUARD AC-OG04: a header carrying an Order Tracking column fails the record
    and writes nothing, so the ingest can never overwrite a tracking field."""
    from app.services.autocount_doc_ingest_service import AutocountDocIngestService

    row = _tracking_row(env)
    row_id = str(row.id)
    real = AutocountDocIngestService._identity

    def leaky(self, doc):
        return {**real(self, doc), "driver_name": "FROM AUTOCOUNT"}

    monkeypatch.setattr(AutocountDocIngestService, "_identity", leaky)
    r = _records(env.push_do([do_records()[0]]))["db1:DO:900001"]
    assert r["outcome"] == "failed"
    env.db.expire_all()
    o = env.db.get(Order, row_id)
    assert o.driver_name == "Ali" and o.doc_key is None


def test_master_reupload_after_adoption_keeps_autocount_values(env):
    """DO-OWNERSHIP-GUARD AC-OG06: a Master sheet re-upload after adoption keeps every
    AutoCount value and still writes Remarks CS and Type."""
    from io import BytesIO

    from openpyxl import Workbook

    from app.services.order_service import OrderService

    _tracking_row(env)
    env.push_do([do_records()[0]])
    o = env.order(900001)
    ac = {c: getattr(o, c) for c in ("order_date", "created_time", "debtor_code",
                                      "debtor_name", "agent", "is_cancelled", "customer_id")}
    wb = Workbook()
    wb.remove(wb.active)
    master = wb.create_sheet("Master")
    master.append(["Doc. No.", "Date", "Created Time", "Debtor Code", "Debtor Name", "Agent",
                   "Cancel", "Remarks CS", "Type"])
    master.append(["ZZDO-0001", date(2026, 1, 2), "02/01/2026 09:00", "999-SHEET",
                   "Sheet Debtor", "SHEET-AGENT", "Y", "cs after adopt", "RMA"])
    tracking = wb.create_sheet("Overall Tracking")
    tracking.append(["Doc Number", "Date", "Driver Name"])
    tracking.append(["ZZDO-0001", date(2026, 9, 30), "Bala"])
    buf = BytesIO()
    wb.save(buf)
    result = OrderService(env.db).import_excel_tracking(buf.getvalue(), _USER_ID)
    assert not result.get("errors"), result.get("errors")

    o = env.order(900001)
    assert {c: getattr(o, c) for c in ac} == ac
    assert o.remarks_cs == "cs after adopt" and o.order_type == "RMA"
    assert o.driver_name == "Bala" and o.actual_delivery_date == date(2026, 9, 30)


def test_doc_no_held_by_another_doc_key_fails(env):
    """AC-AG044."""
    env.push_do([do_records()[0]])
    clash = copy.deepcopy(do_records()[1])
    clash["DocNo"] = "ZZDO-0001"
    r = _records(env.push_do([clash]))["db1:DO:900002"]
    assert r["outcome"] == "failed" and "DocNo" in r["errors"]


def test_adopt_excel_grn_keeps_linked_line(env):
    """AC-AG045."""
    spo = SPOAllocation(spo_number="SPO-ZZ-0009", product_id=env.p1, allocated_quantity=100,
                        company_id=env.company)
    env.db.add(spo)
    env.db.flush()
    header = PickingHeader(picking_number="ZZGRN-0001", picking_type="goods_received",
                           picking_date=date(2026, 7, 27), picking_status="approved",
                           source_system="import", company_id=env.company)
    env.db.add(header)
    env.db.flush()
    legacy = PickingLine(picking_header_id=header.id, product_id=env.p1, quantity_expected=100,
                         quantity_picked=100, spo_allocation_id=spo.id,
                         spo_number_raw="SPO-ZZ-0009", company_id=env.company)
    env.db.add(legacy)
    env.db.commit()
    legacy_id = str(legacy.id)
    r = _records(env.push_grn())["db1:GRN:800001"]
    assert r["outcome"] == "updated" and "adopted_by_doc_no" in r["warnings"]
    h = env.grn(800001)
    assert str(h.id) == str(header.id)
    assert h.source_system == "import"  # provenance is written once
    lines = {l.dtl_key: l for l in env.grn_lines(h.id)}
    assert str(lines[810001].id) == legacy_id
    assert lines[810001].spo_allocation_id == str(spo.id)


# ======================================================================= deletions
def test_deletion_cancels_do_in_range(env):
    """AC-AG050, AC-AG053."""
    env.push_do()
    res = env.delete(DO_DELETE, [900001], "2026-09-01", "2026-09-30", )
    assert res.status_code == 200, res.text
    rec = res.json()["records"][0]
    assert rec["outcome"] == "deactivated" and rec["source_ref"] == "db1:DO:900001"
    o = env.order(900001)
    assert o.is_cancelled is True and o.source_vanished_at is not None
    assert len(env.order_lines(o.id)) == 2
    stamp = o.source_vanished_at
    res = env.delete(DO_DELETE, [900001], "2026-09-01", "2026-09-30")
    assert res.json()["records"][0]["outcome"] == "deactivated"
    assert env.order(900001).source_vanished_at == stamp
    res = env.client.post(f"{DO_DELETE}?dry_run=true", json={
        "companyCode": env.company_code, "book": "db1", "doc_date_from": "2026-09-01",
        "doc_date_to": "2026-09-30", "doc_keys": [900002]})
    assert res.json()["records"][0]["outcome"] == "deactivated"
    assert env.order(900002).is_cancelled is False


def test_deletion_cancels_grn(env):
    """AC-AG051."""
    env.push_grn()
    res = env.delete(GRN_DELETE, [800001], "2026-07-01", "2026-07-31")
    assert res.json()["records"][0]["outcome"] == "deactivated"
    h = env.grn(800001)
    assert h.picking_status == "cancelled" and h.is_cancelled is True
    assert len(env.grn_lines(h.id)) == 2


def test_deletion_not_found_and_out_of_range(env):
    """AC-AG052."""
    env.push_do()
    res = env.delete(DO_DELETE, [123, 900001], "2026-10-01", "2026-10-31")
    out = {r["source_ref"]: r for r in res.json()["records"]}
    assert out["db1:DO:123"]["outcome"] == "not_found"
    assert out["db1:DO:900001"]["outcome"] == "failed"
    assert "doc_date" in out["db1:DO:900001"]["errors"]
    assert env.order(900001).is_cancelled is False


def test_deletion_never_touches_unowned_row(env):
    """DO-OWNERSHIP-GUARD AC-OG07: the vanished sweep only ever finds rows by DocKey, so an
    Order Tracking row (doc_key NULL) in the swept range is never cancelled."""
    row = _tracking_row(env)
    row_id = str(row.id)
    res = env.delete(DO_DELETE, [900001], "2026-09-01", "2026-09-30")
    assert res.json()["records"][0]["outcome"] == "not_found"
    env.db.expire_all()
    o = env.db.get(Order, row_id)
    assert o.is_cancelled is False and o.source_vanished_at is None and o.doc_key is None


def test_vanished_document_restored_by_push(env):
    """AC-AG054."""
    env.push_do()
    env.delete(DO_DELETE, [900001], "2026-09-01", "2026-09-30")
    r = _records(env.push_do([do_records()[0]]))["db1:DO:900001"]
    assert r["outcome"] == "updated" and "restored" in r["warnings"]
    o = env.order(900001)
    assert o.source_vanished_at is None and o.is_cancelled is False


def test_deletion_body_errors(env):
    """AC-AG055."""
    base = {"companyCode": env.company_code, "book": "db1"}
    for body in (
        {**base, "doc_date_from": "2026-01-01", "doc_date_to": "2026-01-31"},
        {**base, "doc_keys": [1]},
        {**base, "doc_keys": [1], "doc_date_from": "2026-02-01", "doc_date_to": "2026-01-01"},
    ):
        res = env.client.post(DO_DELETE, json=body)
        assert res.status_code == 422, body
        assert res.json()["code"] == "INVALID_BODY"


# ======================================================================= branches
def _branch(code="KL01", acc="300-ZZAC01", name="KLCC SITE OFFICE", **extra):
    return {"AutoKey": 1, "AccNo": acc, "BranchCode": code, "BranchName": name,
            "Address1": "x", "IsActive": "T", **extra}


def test_branch_upsert(env):
    """AC-AG060, AC-AG061, AC-AG064."""
    res = env.post(BR_INGEST, [_branch(), {"BranchName": "no code"}])
    body = res.json()["records"]
    assert body[0]["outcome"] == "created" and body[0]["source_ref"] == "db1:BR:300-ZZAC01:KL01"
    assert body[1]["outcome"] == "failed" and "BranchCode" in body[1]["errors"]
    env.db.expire_all()
    row = env.db.execute(select(Branch).where(Branch.company_id == env.company,
                                              Branch.branch_code == "KL01")).scalar_one()
    assert (row.source_book, row.acc_no, row.branch_name) == ("db1", "300-ZZAC01", "KLCC SITE OFFICE")
    assert row.source_record["Address1"] == "x"
    assert _outcomes(env.post(BR_INGEST, [_branch()])) == {"db1:BR:300-ZZAC01:KL01": "unchanged"}
    assert _outcomes(env.post(BR_INGEST, [_branch(name="KLCC NEW")])) == {
        "db1:BR:300-ZZAC01:KL01": "updated"}


def test_do_branch_resolution(env):
    """AC-AG062."""
    env.post(BR_INGEST, [_branch()])
    known = copy.deepcopy(do_records()[0])
    known["BranchCode"] = "KL01"
    unknown = copy.deepcopy(do_records()[1])
    unknown["BranchCode"] = "ZZ99"
    recs = _records(env.push_do([known, unknown]))
    o = env.order(900001)
    assert (o.branch_code, o.branch_name) == ("KL01", "KLCC SITE OFFICE")
    assert "branch_unresolved" not in recs["db1:DO:900001"].get("warnings", [])
    o2 = env.order(900002)
    assert (o2.branch_code, o2.branch_name) == ("ZZ99", None)
    assert "branch_unresolved" in recs["db1:DO:900002"]["warnings"]
    # The live shape: BranchCode empty resolves nothing and warns nothing.
    env.db.expire_all()
    plain = _records(env.push_do([do_records()[0]]))["db1:DO:900001"]
    assert "branch_unresolved" not in plain.get("warnings", [])


def test_branch_after_do_fills_name(env):
    """AC-AG063."""
    rec = copy.deepcopy(do_records()[0])
    rec["BranchCode"] = "KL01"
    env.push_do([rec])
    assert env.order(900001).branch_name is None
    env.post(BR_INGEST, [_branch()])
    assert env.order(900001).branch_name == "KLCC SITE OFFICE"
    env.post(BR_INGEST, [_branch(name="KLCC RENAMED")])
    assert env.order(900001).branch_name == "KLCC RENAMED"


def test_cross_repo_contract_carries_section_13():
    """AC-AG014: the contract of record the shared-service lane implements."""
    doc = (Path(__file__).resolve().parents[2] / "documentation" / "plans" / "autocount"
           / "PLAN-autocount-cross-repo-contract.md").read_text()
    heading = "## 13. delivery_orders, goods_receive_notes, branches (contract 2.7)"
    assert heading in doc
    section = doc.split(heading, 1)[1]
    for needle in ("/api/v1/external/ingest/delivery_orders",
                   "/api/v1/external/ingest/goods_receive_notes/deletions",
                   "/api/v1/external/ingest/branches", "doc_keys", "`book`",
                   "stale_ignored", "adopted_by_doc_no", "FromDocDtlKey", "Never deleted"):
        assert needle in section, needle


# ======================================================================= company isolation
def test_push_to_a_never_adopts_company_b_row(env):
    """AC-AG080: a company B DO with the same DocNo is not adopted by a push anchored to A."""
    b_row = Order(order_number="ZZDO-0001", company_id=env.company_b, debtor_code="B-ONLY")
    env.db.add(b_row)
    env.db.commit()
    r = _records(env.push_do([do_records()[0]]))["db1:DO:900001"]
    assert r["outcome"] == "created" and r["entity_id"] != str(b_row.id)
    env.db.expire_all()
    b_row = env.db.get(Order, b_row.id)
    assert b_row.doc_key is None and b_row.debtor_code == "B-ONLY"


def test_deletions_under_a_do_not_reach_company_b(env):
    """AC-AG081: B's DocKey is `not_found` under A's anchor and B's row is untouched."""
    b_row = Order(order_number="ZZDO-B9", company_id=env.company_b, source_book="db1",
                  doc_key=900001, order_date=date(2026, 9, 27))
    env.db.add(b_row)
    env.db.commit()
    res = env.delete(DO_DELETE, [900001], "2026-09-01", "2026-09-30")
    assert res.json()["records"][0]["outcome"] == "not_found"
    env.db.expire_all()
    b_row = env.db.get(Order, b_row.id)
    assert b_row.is_cancelled is False and b_row.source_vanished_at is None


def test_links_never_resolve_to_company_b(env):
    """AC-AG082: an SO / PO of company B never links a company A line."""
    so = SalesOrder(so_number="ZZSO-0001", company_id=env.company_b)
    env.db.add(so)
    env.db.flush()
    env.db.add(SalesOrderLine(sales_order_id=so.id, product_id=env.p1, qty_ordered=1,
                              source_ref="SRT_DB:4001:5001", company_id=env.company_b))
    env.db.add(PurchaseOrder(po_number="ZZPO-B", company_id=env.company_b))
    env.db.commit()
    do = _with_from(copy.deepcopy(do_records()[0]), 0, "SO", "ZZSO-0001", 5001)
    do["RefDocNo"] = "ZZSO-0001"
    r = _records(env.push_do([do]))["db1:DO:900001"]
    assert {"so_line_unresolved", "sales_order_unresolved"} <= set(r["warnings"])
    o = env.order(900001)
    assert o.sales_order_id is None
    assert env.order_lines(o.id)[0].sales_order_line_id is None
    grn = copy.deepcopy(grn_records()[0])
    grn["Details"][0]["OurPONo"] = "ZZPO-B"
    r = _records(env.push_grn([grn]))["db1:GRN:800001"]
    assert "purchase_order_unresolved" in r["warnings"]


def test_branch_push_to_a_never_renames_company_b_orders(env):
    """AC-AG083."""
    b_order = Order(order_number="ZZDO-B1", company_id=env.company_b, source_book="db1",
                    doc_key=777001, debtor_code="300-ZZAC01", branch_code="KL01",
                    branch_name="B NAME")
    env.db.add(b_order)
    env.db.commit()
    env.post(BR_INGEST, [_branch()])
    env.db.expire_all()
    assert env.db.get(Order, b_order.id).branch_name == "B NAME"


def test_oversized_branch_and_doc_key_are_refused_per_record(env):
    """AC-AG084: a branch row over its cap fails alone; a DocKey beyond BIGINT is a
    per-key `failed` on /deletions, never a 500."""
    big = _branch(code="BIG1", Notes="x" * 40_000)
    body = env.post(BR_INGEST, [big, _branch()]).json()["records"]
    assert body[0]["outcome"] == "failed" and "record" in body[0]["errors"]
    assert body[1]["outcome"] == "created"
    res = env.delete(DO_DELETE, [10 ** 30])
    assert res.status_code == 200
    assert res.json()["records"][0]["outcome"] == "failed"


# ======================================================================= review round
def test_branch_refresh_agrees_with_do_replay(env):
    """AC-AG085: a DO whose branch code is ambiguous stays unresolved after a branch push,
    so its identical replay answers `unchanged`."""
    env.post(BR_INGEST, [_branch(acc="", name="A"), _branch(acc="300-OTHER", name="B")])
    rec = copy.deepcopy(do_records()[0])
    rec["BranchCode"] = "KL01"
    r = _records(env.push_do([rec]))["db1:DO:900001"]
    assert "branch_unresolved" in r["warnings"] and env.order(900001).branch_name is None
    env.post(BR_INGEST, [_branch(acc="", name="RENAMED")])
    assert env.order(900001).branch_name is None
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "unchanged"


def test_seq_swap_between_lines(env):
    """AC-AG086: two lines swapping Seq on an update land without a unique conflict."""
    env.push_do([do_records()[0]])
    rec = copy.deepcopy(do_records()[0])
    rec["LastModified"] = "2026-09-28T08:00:00.000"
    rec["Details"][0]["Seq"], rec["Details"][1]["Seq"] = 32, 16
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "updated"
    lines = {l.dtl_key: l.line_sequence for l in env.order_lines(env.order(900001).id)}
    assert lines == {910001: 32, 910002: 16}


def test_line_without_seq_gets_a_free_sequence(env):
    """AC-AG087: a line with no Seq never collides with an explicit Seq."""
    rec = copy.deepcopy(do_records()[0])
    rec["Details"][0]["Seq"] = None
    rec["Details"][1]["Seq"] = 1
    assert _outcomes(env.push_do([rec]))["db1:DO:900001"] == "created"
    seqs = sorted(l.line_sequence for l in env.order_lines(env.order(900001).id))
    assert seqs == [1, 2]


def test_grn_receipt_follows_the_exact_spo_link_and_the_cancel(env):
    """AC-AG088: an AutoCount GRN line linked to an SPO allocation counts as its receipt;
    cancelling the GRN by the deletion sweep gives the receipt back."""
    spo = SPOAllocation(spo_number="SPO-ZZ-0101", product_id=env.p2, allocated_quantity=20,
                        source_ref="SRT_DB:7002:7301", company_id=env.company)
    env.db.add(spo)
    env.db.commit()
    rec = _with_from(copy.deepcopy(grn_records()[0]), 1, "PO", "SPO-ZZ-0101", 7301)
    assert _outcomes(env.push_grn([rec]))["db1:GRN:800001"] == "created"
    env.db.expire_all()
    assert env.db.get(SPOAllocation, spo.id).quantity_received == 20
    env.delete(GRN_DELETE, [800001], "2026-07-01", "2026-07-31")
    env.db.expire_all()
    assert env.db.get(SPOAllocation, spo.id).quantity_received == 0
