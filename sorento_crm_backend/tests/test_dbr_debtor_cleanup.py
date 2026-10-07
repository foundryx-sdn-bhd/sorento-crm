"""RED tests for DBR-DEBTOR-CLEANUP (UAC AC-1 to AC-13).

A DO with a blank debtor code keeps its debtor NAME as text and has NO customer: no `DBR-<md5>`
customer is minted, an existing one is cleared on update, the customer filter and dealer scope
match by customer id only, the outstanding report / DO lines report / export search read the
debtor name when there is no customer, and `scripts/cleanup_dbr_customers.py` removes the
existing DBR customers.

Script contract (the coder matches this):
    from scripts.cleanup_dbr_customers import run
    run(db, apply: bool) -> dict with keys
        dbr_customers (int), orders_nulled (int), stock_asks_nulled (int), chat_links (int),
        contacts_left_unlinked (list of {"contact_id", "dbr_customer_name"})
    CLI: python -m scripts.cleanup_dbr_customers [--apply]   (dry run without --apply)

Postgres only (tests/_pg_fixture.blank_session); every test seeds its own chain.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402,F401

from app.models.access import RespondContactCustomer
from app.models.base import set_company_scope
from app.models.integration_reference import IntegrationReference
from app.models.order import Customer, Order
from app.models.stock_ask import StockAsk
from app.schemas.list_query import ListExportRequest
from app.schemas.order import OrderCreate, OrderUpdate
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.list_query_export_service import ListQueryExportService
from app.services.order_service import OrderService
from app.services.outstanding_report_service import outstanding_report

from tests._mc_lookup_seed import customer as seed_customer
from tests._mc_lookup_seed import order as seed_order
from tests._mc_lookup_seed import order_line, product, warehouse
from tests._ask_todo_seed import contact as seed_contact
from tests._pg_fixture import blank_session

SORENTO = DEFAULT_COMPANY_ID


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({SORENTO}))
        yield session


def _num(stem: str) -> str:
    return f"ZZDBR-{stem}-{uuid.uuid4().hex[:8]}"


def _customers(db) -> int:
    return db.query(Customer).count()


def _do(db, *, debtor_name, customer_id=None, debtor_code=None, **extra) -> Order:
    o = seed_order(db, company_id=SORENTO, customer_id=customer_id, number=_num("DO"))
    o.debtor_name = debtor_name
    o.debtor_code = debtor_code
    for k, v in extra.items():
        setattr(o, k, v)
    db.flush()
    return o


# ===================================================== AC-1 / AC-3 create and import sync
class TestCreateSync:
    def test_ac1_blank_code_create_makes_no_customer(self, db):
        order = OrderService(db).create_order(
            OrderCreate(order_number=_num("C"), debtor_name="SAMPLE", debtor_code=None), created_by=None
        )
        assert order.debtor_name == "SAMPLE"
        assert order.customer_id is None
        assert _customers(db) == 0

    def test_ac1_whitespace_code_create_makes_no_customer(self, db):
        order = OrderService(db).create_order(
            OrderCreate(order_number=_num("W"), debtor_name="SAMPLE", debtor_code="  "), created_by=None
        )
        assert order.customer_id is None
        assert _customers(db) == 0

    def test_ac1_import_sync_blank_code_leaves_customer_id_null(self, db):
        out = OrderService(db)._sync_order_master_refs({"debtor_name": "SAMPLE", "debtor_code": ""})
        assert out.get("customer_id") is None
        assert _customers(db) == 0

    def test_ac1_upsert_helper_returns_none_for_blank_code(self, db):
        svc = OrderService(db)
        assert svc._upsert_customer_from_debtor("SAMPLE", None) is None
        assert svc._upsert_customer_from_debtor("SAMPLE", "") is None
        assert _customers(db) == 0

    def test_ac3_non_blank_code_still_creates_by_code(self, db):
        order = OrderService(db).create_order(
            OrderCreate(order_number=_num("N"), debtor_name="ACME SDN BHD", debtor_code="300-A001"),
            created_by=None,
        )
        cust = db.get(Customer, order.customer_id)
        assert cust.customer_code == "300-A001" and cust.customer_name == "ACME SDN BHD"

    def test_ac3_non_blank_code_links_existing_by_code(self, db):
        existing = seed_customer(db, company_id=SORENTO, name="ACME SDN BHD")
        existing.customer_code = "300-A002"
        db.flush()
        out = OrderService(db)._sync_order_master_refs({"debtor_name": "Other Name", "debtor_code": "300-A002"})
        assert out["customer_id"] == str(existing.id)


# ===================================================== AC-2 / AC-4 update
class TestUpdateSync:
    def test_ac2_blank_code_update_clears_a_dbr_customer(self, db):
        dbr = seed_customer(db, company_id=SORENTO, name="SAMPLE")
        dbr.customer_code = "DBR-5e8ff9bf55"
        db.flush()
        o = _do(db, debtor_name="SAMPLE", customer_id=dbr.id)
        db.commit()
        before = _customers(db)
        OrderService(db).update_order(str(o.id), OrderUpdate(remarks="touched"), updated_by=None)
        db.refresh(o)
        assert o.customer_id is None
        assert o.debtor_name == "SAMPLE"
        assert _customers(db) == before

    def test_ac3_non_blank_code_update_relinks_by_code(self, db):
        real = seed_customer(db, company_id=SORENTO, name="REAL CO")
        real.customer_code = "300-R001"
        db.flush()
        o = _do(db, debtor_name="REAL CO", debtor_code="300-R001")
        db.commit()
        OrderService(db).update_order(str(o.id), OrderUpdate(remarks="touched"), updated_by=None)
        db.refresh(o)
        assert str(o.customer_id) == str(real.id)

    def test_ac4_autocount_owned_do_is_untouched(self, db):
        real = seed_customer(db, company_id=SORENTO, name="AC OWNED CO")
        o = _do(db, debtor_name="AC OWNED CO", customer_id=real.id, doc_key=uuid.uuid4().int % 10**12,
                source_book="SORENTO")
        db.commit()
        OrderService(db).update_order(str(o.id), OrderUpdate(lorry_plate="PLATE-1"), updated_by=None)
        db.refresh(o)
        assert str(o.customer_id) == str(real.id)


# ===================================================== AC-5 / AC-6 filter by customer only
@pytest.fixture()
def sample_world(db):
    """A customer literally named SAMPLE with one DO, plus a customer-less DO whose debtor_name is SAMPLE."""
    sample = seed_customer(db, company_id=SORENTO, name="SAMPLE")
    prod = product(db, company_id=SORENTO)
    wh = warehouse(db, company_id=SORENTO)
    linked = _do(db, debtor_name="SAMPLE", customer_id=sample.id)
    loose = _do(db, debtor_name="SAMPLE", customer_id=None)
    for o in (linked, loose):
        order_line(db, company_id=SORENTO, order_id=o.id, product_id=prod.id, warehouse_id=wh.id, quantity=3)
    db.commit()
    return {"sample": sample, "linked": linked, "loose": loose, "prod": prod}


def _ids(result) -> set[str]:
    items = result["data"] if isinstance(result, dict) and "data" in result else result["items"]
    return {str(getattr(i, "id", None) or i["id"]) for i in items}


class TestFilterByCustomerOnly:
    def test_ac5_list_orders_excludes_customerless_same_name(self, db, sample_world):
        w = sample_world
        got = _ids(OrderService(db).list_orders(customer_ids=[str(w["sample"].id)]))
        assert got == {str(w["linked"].id)}

    def test_ac5_list_orders_by_product_excludes_customerless_same_name(self, db, sample_world):
        w = sample_world
        got = _ids(OrderService(db).list_orders_by_product(
            customer_ids=[str(w["sample"].id)], product_ids=[str(w["prod"].id)]))
        assert got == {str(w["linked"].id)}

    def test_ac5_analytics_excludes_customerless_same_name(self, db, sample_world):
        w = sample_world
        res = OrderService(db).order_analytics(metric="count", customer_ids=[str(w["sample"].id)])
        assert sum(g["order_count"] for g in res["groups"]) == 1

    def test_ac6_dealer_scope_does_not_see_customerless_do(self, db, sample_world):
        """A contact linked to the SAMPLE customer: the scope's ids feed the same filter."""
        from app.api.v1.order_management._contact_scope import enforce_customer_scope

        w = sample_world
        contact_id = seed_contact(db, "Ah Seng")
        db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=contact_id,
                                      customer_id=str(w["sample"].id), company_id=SORENTO))
        db.flush()
        ids = enforce_customer_scope(db, contact_id=contact_id, space_id="zzt-space",
                                     customer_ids=None, customer_query=None)
        assert ids == [str(w["sample"].id)]
        got = _ids(OrderService(db).list_orders(customer_ids=ids))
        assert got == {str(w["linked"].id)}


# ===================================================== AC-7 outstanding report, DO side
class TestOutstandingReportDo:
    @pytest.fixture()
    def world(self, db):
        prod = product(db, company_id=SORENTO)
        wh = warehouse(db, company_id=SORENTO)
        real = seed_customer(db, company_id=SORENTO, name="REAL CO")
        loose = _do(db, debtor_name="SAMPLE MARKETING", customer_id=None)
        linked = _do(db, debtor_name="REAL CO", customer_id=real.id)
        for o in (loose, linked):
            order_line(db, company_id=SORENTO, order_id=o.id, product_id=prod.id, warehouse_id=wh.id, quantity=5)
        db.commit()
        return {"prod": prod, "real": real, "loose": loose}

    def test_ac7_customerless_do_shows_debtor_name_and_buckets_under_it(self, db, world):
        res = outstanding_report(db, product_code=world["prod"].product_code, scope="do")
        names = {r["customer_name"] for r in res["do_rows"]}
        assert "SAMPLE MARKETING" in names
        assert "SAMPLE MARKETING" in {r["customer_name"] for r in res["do_by_customer"]}

    def test_ac7_customer_query_finds_it_by_debtor_name(self, db, world):
        res = outstanding_report(db, product_code=world["prod"].product_code, scope="do",
                                 customer_query="SAMPLE MARKET")
        assert [r["do_number"] for r in res["do_rows"]] == [world["loose"].order_number]

    def test_ac7_customer_id_filter_excludes_it(self, db, world):
        res = outstanding_report(db, product_code=world["prod"].product_code, scope="do",
                                 customer_ids=[str(world["real"].id)])
        assert world["loose"].order_number not in {r["do_number"] for r in res["do_rows"]}
        assert len(res["do_rows"]) == 1


# ===================================================== AC-8 DO lines report dataset
def test_ac8_dataset_customer_column_falls_back_to_debtor_name(db):
    from app.services.reports.datasets.delivery_order_lines import DATASET

    loose = _do(db, debtor_name="SAMPLE")
    real = seed_customer(db, company_id=SORENTO, name="REAL NAME")
    linked = _do(db, debtor_name="OTHER TEXT", customer_id=real.id)
    db.flush()
    col = next(c for c in DATASET.columns if c.key == "customer")
    stmt = (
        sa.select(Order.id, col.expr(None).label("customer"))
        .select_from(Order)
        .outerjoin(Customer, Customer.id == Order.customer_id)
        .where(Order.id.in_([loose.id, linked.id]))
    )
    got = {str(r.id): r.customer for r in db.execute(stmt)}
    assert got[str(loose.id)] == "SAMPLE"
    assert got[str(linked.id)] == "REAL NAME"


# ===================================================== AC-9 export quick search
def test_ac9_export_quick_search_finds_customerless_do_by_debtor_name(db):
    loose = _do(db, debtor_name="UNIQUE PLACEHOLDER DEBTOR")
    db.flush()
    svc = ListQueryExportService(db)
    hit = svc._export_orders([], None, ListExportRequest(
        resource="orders", fields=[{"field_key": "order_number"}], quick_search="PLACEHOLDER DEBTOR",
        record_ids=[str(loose.id)]))
    miss = svc._export_orders([], None, ListExportRequest(
        resource="orders", fields=[{"field_key": "order_number"}], quick_search="NOT THERE AT ALL",
        record_ids=[str(loose.id)]))
    assert len(hit) == 1
    assert len(miss) == 0


# ===================================================== AC-10 to AC-13 data script
@pytest.fixture()
def cleanup_world(db):
    real = seed_customer(db, company_id=SORENTO, name="REAL CO")
    real.customer_code = "300-R100"

    def dbr(code: str, name: str) -> Customer:
        c = seed_customer(db, company_id=SORENTO, name=name)
        c.customer_code = code
        return c

    d1 = dbr("DBR-aaaaaaaaaa", "SAMPLE")
    d2 = dbr("DBR-bbbbbbbbbb", "DELUXE HOME CENTRE (KTN)")
    kept = dbr("DBR-cccccccccc", "HAS INTEGRATION REF")
    db.flush()
    db.add(IntegrationReference(entity_type="customers", entity_id=str(kept.id), company_id=SORENTO,
                                source_system="autocount", source_ref=f"DK-{uuid.uuid4().hex[:8]}"))
    o1 = _do(db, debtor_name="SAMPLE", customer_id=d1.id)
    o_real = _do(db, debtor_name="REAL CO", customer_id=real.id)
    o_kept = _do(db, debtor_name="HAS INTEGRATION REF", customer_id=kept.id)
    c_only = seed_contact(db, "Only DBR")
    c_both = seed_contact(db, "DBR and real")
    for cid, cust in ((c_only, d2), (c_both, d1), (c_both, real)):
        db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=cid, customer_id=str(cust.id),
                                      company_id=SORENTO))
    ask = StockAsk(id=str(uuid.uuid4()), company_id=SORENTO, customer_id=d2.id, contact_id=c_only,
                   product_code="SRT1", quantity=1, branch="in_stock", answer_summary="x", state="open",
                   created_at=datetime(2026, 10, 6, 9, 0, 0))
    db.add(ask)
    db.commit()
    return dict(real=real, d1=d1, d2=d2, kept=kept, o1=o1, o_real=o_real, o_kept=o_kept,
                c_only=c_only, c_both=c_both, ask=ask)


def _run(db, apply, **kw):
    from scripts.cleanup_dbr_customers import run

    return run(db, apply=apply, **kw)


class TestCleanupScript:
    def test_ac10_dry_run_changes_nothing_and_reports_counts(self, db, cleanup_world):
        w = cleanup_world
        before = _customers(db)
        res = _run(db, False)
        db.expire_all()
        assert _customers(db) == before
        assert str(db.get(Order, w["o1"].id).customer_id) == str(w["d1"].id)
        assert db.get(StockAsk, w["ask"].id).customer_id is not None
        assert db.query(RespondContactCustomer).count() == 3
        assert res["dbr_customers"] == 2
        assert res["orders_nulled"] == 1
        assert res["stock_asks_nulled"] == 1
        assert res["chat_links"] == 2
        assert res["blockers"] == []

    def test_ac11_apply_nulls_orders_and_asks_and_deletes_dbr_customers(self, db, cleanup_world):
        w = cleanup_world
        _run(db, True, allow_unlink=True)
        db.expire_all()
        o1 = db.get(Order, w["o1"].id)
        assert o1.customer_id is None and o1.debtor_name == "SAMPLE"
        ask = db.get(StockAsk, w["ask"].id)
        assert ask is not None and ask.customer_id is None
        assert db.get(Customer, w["d1"].id) is None
        assert db.get(Customer, w["d2"].id) is None

    def test_ac12_real_and_integration_referenced_customers_are_left_alone(self, db, cleanup_world):
        w = cleanup_world
        _run(db, True, allow_unlink=True)
        db.expire_all()
        assert db.get(Customer, w["real"].id) is not None
        assert db.get(Customer, w["kept"].id) is not None
        assert str(db.get(Order, w["o_real"].id).customer_id) == str(w["real"].id)
        assert str(db.get(Order, w["o_kept"].id).customer_id) == str(w["kept"].id)

    def test_ac13_apply_drops_chat_links_and_lists_contacts_left_unlinked(self, db, cleanup_world):
        w = cleanup_world
        res = _run(db, True, allow_unlink=True)
        db.expire_all()
        remaining = db.query(RespondContactCustomer).all()
        assert [str(r.customer_id) for r in remaining] == [str(w["real"].id)]
        assert res["chat_links"] == 2
        assert res["contacts_left_unlinked"] == [
            {"contact_id": w["c_only"], "dbr_customer_name": "DELUXE HOME CENTRE (KTN)"}
        ]

    def test_ac13_dry_run_reports_the_same_unlinked_list(self, db, cleanup_world):
        w = cleanup_world
        res = _run(db, False)
        assert res["contacts_left_unlinked"] == [
            {"contact_id": w["c_only"], "dbr_customer_name": "DELUXE HOME CENTRE (KTN)"}
        ]

    def _snapshot(self, db, w):
        db.expire_all()
        return (
            db.query(Customer).count(),
            db.query(RespondContactCustomer).count(),
            str(db.get(Order, w["o1"].id).customer_id),
            str(db.get(StockAsk, w["ask"].id).customer_id),
        )

    def test_ac14_apply_refuses_when_a_contact_would_be_left_unlinked(self, db, cleanup_world):
        w = cleanup_world
        before = self._snapshot(db, w)
        with pytest.raises(Exception) as exc:
            _run(db, True)
        assert w["c_only"] in str(exc.value)
        assert self._snapshot(db, w) == before

    def test_ac14_allow_unlink_succeeds_and_lists_the_contact(self, db, cleanup_world):
        w = cleanup_world
        res = _run(db, True, allow_unlink=True)
        assert [u["contact_id"] for u in res["contacts_left_unlinked"]] == [w["c_only"]]

    def test_ac14_dry_run_still_succeeds_and_lists_it(self, db, cleanup_world):
        w = cleanup_world
        res = _run(db, False)
        assert [u["contact_id"] for u in res["contacts_left_unlinked"]] == [w["c_only"]]

    def test_abort_when_another_table_references_a_dbr_customer(self, db, cleanup_world):
        from app.models.order import CustomerContact

        w = cleanup_world
        db.add(CustomerContact(id=str(uuid.uuid4()), customer_id=str(w["d1"].id), full_name="Ah Person",
                               company_id=SORENTO))
        db.flush()
        before = self._snapshot(db, w)
        with pytest.raises(Exception):
            _run(db, True, allow_unlink=True)
        assert self._snapshot(db, w) == before

    def test_dry_run_reports_blockers_instead_of_raising(self, db, cleanup_world):
        from app.models.order import CustomerContact

        w = cleanup_world
        db.add(CustomerContact(id=str(uuid.uuid4()), customer_id=str(w["d1"].id), full_name="Ah Person",
                               company_id=SORENTO))
        db.flush()
        res = _run(db, False)
        assert res["blockers"] and "customer_contacts" in " ".join(res["blockers"])


# ===================================================== relinks before the DBR link is deleted
class TestRelinks:
    @pytest.fixture()
    def rw(self, db):
        dbr = seed_customer(db, company_id=SORENTO, name="DELUXE (KTN)")
        dbr.customer_code = "DBR-dddddddddd"
        r1 = seed_customer(db, company_id=SORENTO, name="REAL ONE")
        r2 = seed_customer(db, company_id=SORENTO, name="REAL TWO")
        contact = seed_contact(db, "Deluxe contact")
        db.flush()
        db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=contact, customer_id=str(dbr.id),
                                      company_id=SORENTO))
        db.commit()
        return dict(dbr=dbr, r1=r1, r2=r2, contact=contact)

    def _links(self, db, contact):
        db.expire_all()
        return sorted(
            (str(r.customer_id), str(r.company_id))
            for r in db.query(RespondContactCustomer).filter_by(contact_id=contact)
        )

    def test_relinks_add_then_apply_leaves_exactly_those_links(self, db, rw):
        c = rw["contact"]
        rel = {c: [str(rw["r1"].id), str(rw["r2"].id)]}
        res = _run(db, False, relinks=rel)
        assert sorted(r["status"] for r in res["relinks"]) == ["add", "add"]
        assert {r["customer_name"] for r in res["relinks"]} == {"REAL ONE", "REAL TWO"}
        assert res["contacts_left_unlinked"] == []
        assert len(self._links(db, c)) == 1  # dry run wrote nothing
        _run(db, True, relinks=rel)
        assert self._links(db, c) == sorted(
            [(str(rw["r1"].id), SORENTO), (str(rw["r2"].id), SORENTO)]
        )
        row = db.query(RespondContactCustomer).filter_by(contact_id=c).first()
        assert row.is_primary is False and row.source == "dbr_cleanup"

    def test_existing_relink_is_not_duplicated(self, db, rw):
        c = rw["contact"]
        db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=c, customer_id=str(rw["r1"].id),
                                      company_id=SORENTO))
        db.commit()
        rel = {c: [str(rw["r1"].id)]}
        res = _run(db, True, relinks=rel)
        assert [r["status"] for r in res["relinks"]] == ["exists"]
        assert self._links(db, c) == [(str(rw["r1"].id), SORENTO)]

    def test_missing_relink_is_not_inserted_and_apply_still_refuses(self, db, rw):
        c = rw["contact"]
        rel = {c: [str(uuid.uuid4())]}
        res = _run(db, False, relinks=rel)
        assert [r["status"] for r in res["relinks"]] == ["missing"]
        assert [u["contact_id"] for u in res["contacts_left_unlinked"]] == [c]
        with pytest.raises(Exception) as exc:
            _run(db, True, relinks=rel)
        assert c in str(exc.value)
        assert len(self._links(db, c)) == 1


def test_relinks_constant_matches_the_prod_mapping():
    from scripts.cleanup_dbr_customers import RELINKS

    assert RELINKS == {
        "e84b7e3c-9a3d-432e-b81b-ab5c5c2cb586": [
            "1aff9ef4-7685-4b0f-9caf-194dd39e1cac",
            "d69fc206-98ae-4fee-b550-bf1938ebe867",
            "229130f3-0a78-46f2-bc98-880a0b362cb5",
            "1fd2f9a9-977e-4c85-b9fb-2abcb2da4f16",
        ]
    }
