"""Phase 2 RED tests - REPORTS-STEP2 S4: the remaining measures behind `/report` (AC-19 to AC-23).

UAC `documentation/plans/chatbot/reports-step2-acceptance-criteria.md`, section S4. Postgres only
(blank schema), every row seeded here.

Pinned by the tester (the UAC leaves them open):
* `/report` returns the kind's native body (the old route's JSON) plus `report_kind`; for the
  four new kinds the rest of the body equals today's route body for the same inputs.
* `order_outstanding` and `low_stock` need no period; `delivered_sales` and `sales_value` take
  `date_from` / `date_to` like the old routes. `sales_value` also takes `basis` (ordered | delivered | invoiced, required as the old route); it takes `group_by` = the row axis
  (month | year | channel) with the columns fixed to `year`, i.e. today's `rows=<group_by>&cols=year`;
  its body compares on `columns`, `rows`, `totals`, `total_count` (the old route's file is not part of it).
* the low stock body is `build_low_stock_view(...)` (JSON round-tripped) plus `report_kind` ==
  "low_stock" and `run_date` (ISO date string); with no run: `status` "no_snapshot" and a `message`
  containing "no low stock snapshot yet". `so_count` is kind "outstanding"; its count is `body["so"]["so_count"]`.
* an unsupported param is 422 whose text names the param.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _contact,
    _link,
    client,
    db,
    line,
    product,
    seed_agent,
    seed_customer,
    seed_do,
    warehouse,
)

D = Decimal
REPORT = "/api/v1/order-management/report"
OUTSTANDING = "/api/v1/order-management/outstanding-report"
SALES_REPORT = "/api/v1/order-management/sales-report"
ANALYSIS = "/api/v1/sales/analysis"
KEY = {"X-API-Key": "k"}
OUT_GRANT, SALES_GRANT, LOW_GRANT, SUPPLIER_GRANT = (
    "sales_orders.outstanding", "sales_orders.sales_report", "scm.low_stock_report", "purchase_orders.supplier",
)


def _staff(db, *grants):
    from app.services.contact_field_reveal_service import set_granted_keys

    contact = _contact(db)
    set_granted_keys(db, contact.id, list(grants) or [SALES_GRANT], actor_id=None)
    db.commit()
    return contact


def _get(client, path, contact, **params):
    q = {**_as_contact(contact), **params}
    return client.get(path, params={k: v for k, v in q.items() if v is not None}, headers=KEY)


def _without_kind(body):
    return {k: v for k, v in body.items() if k != "report_kind"}


# ============================================================================ outstanding


def _outstanding_world(db):
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    seed_so_line(db, cust=cust, prod=prod, qty=10, total="100.00", wh=wh, delivered=3)
    seed_so_line(db, cust=cust, prod=prod, qty=5, total="50.00", wh=wh, delivered=0)
    db.commit()
    return cust, prod


def test_ac19_order_outstanding_equals_the_outstanding_report_route(client, db):
    cust, _prod = _outstanding_world(db)
    contact = _staff(db, SALES_GRANT, OUT_GRANT)
    old = _get(client, OUTSTANDING, contact, customer_ids=cust.id, scope="both")
    new = _get(client, REPORT, contact, measure="order_outstanding", customer_ids=cust.id)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    assert new.json()["report_kind"] == "outstanding", new.json()
    assert _without_kind(new.json()) == old.json()
    assert new.json()["so"]["outstanding_qty"] == 12, new.json()


def test_ac19_order_outstanding_scope_so_leaves_the_do_block_out(client, db):
    cust, _prod = _outstanding_world(db)
    contact = _staff(db, SALES_GRANT, OUT_GRANT)
    body = _get(client, REPORT, contact, measure="order_outstanding", customer_ids=cust.id, scope="so").json()
    assert "so" in body and "do" not in body, body


def test_ac20_order_outstanding_needs_a_subject(client, db):
    resp = _get(client, REPORT, _staff(db, SALES_GRANT, OUT_GRANT), measure="order_outstanding")
    assert resp.status_code == 422 and resp.json().get("code") == "subject_required", resp.text


def test_ac20_order_outstanding_needs_the_outstanding_grant(client, db):
    cust, _prod = _outstanding_world(db)
    resp = _get(client, REPORT, _staff(db, SALES_GRANT), measure="order_outstanding", customer_ids=cust.id)
    assert resp.status_code == 403, resp.text


def test_ac20_order_outstanding_dealer_sees_only_its_own_customers(client, db):
    own, prod = _outstanding_world(db)
    other = seed_customer(db, name=unique_code("Other"))
    seed_so_line(db, cust=other, prod=prod, qty=99, total="990.00", delivered=0)
    contact = _staff(db, SALES_GRANT, OUT_GRANT)
    _link(db, contact, own)
    db.commit()
    body = _get(client, REPORT, contact, measure="order_outstanding").json()
    assert body["so"]["so_count"] == 2 and body["so"]["outstanding_qty"] == 12, body
    resp = _get(client, REPORT, contact, measure="order_outstanding", customer_ids=other.id)
    assert resp.status_code == 403 and resp.json().get("code") == "customer_not_permitted", resp.text


def test_ac23_so_count_is_served_by_one_registry_row(client, db):
    from app.services.reports import measures

    assert "so_count" in measures.keys()
    cust, _prod = _outstanding_world(db)
    contact = _staff(db, SALES_GRANT, OUT_GRANT)
    resp = _get(client, REPORT, contact, measure="so_count", customer_ids=cust.id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_kind"] == "outstanding" and body["so"]["so_count"] == 2, body


# ============================================================================ delivered_sales


def _delivered_world(db):
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    agent = seed_agent(db, code=unique_code("AG"))
    del agent
    seed_do(db, customer_id=cust.id, order_date=date(2026, 9, 10), number=unique_code("DO"),
            lines=[line(prod.id, wh.id, 5, price=D("10"), total=D("50.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 9, 12), number=unique_code("DO"),
            lines=[line(prod.id, wh.id, 7, price=D("10"), total=D("70.00"))])
    db.commit()
    return cust


@pytest.mark.parametrize("group_by", [None, "delivery_order", "product"])
def test_ac19_delivered_sales_equals_the_sales_report_route(client, db, group_by):
    cust = _delivered_world(db)
    contact = _staff(db, SALES_GRANT)
    kw = dict(customer_ids=cust.id, date_from="2026-09-01", date_to="2026-09-30", group_by=group_by)
    old = _get(client, SALES_REPORT, contact, **kw)
    new = _get(client, REPORT, contact, measure="delivered_sales", **kw)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    assert new.json()["report_kind"] == "sales_report"
    assert _without_kind(new.json()) == old.json()
    assert new.json()["total"]["qty"] == 12, new.json()


def test_ac20_delivered_sales_needs_the_sales_grant(client, db):
    cust = _delivered_world(db)
    resp = _get(client, REPORT, _staff(db, OUT_GRANT), measure="delivered_sales", customer_ids=cust.id,
                date_from="2026-09-01", date_to="2026-09-30")
    assert resp.status_code == 403, resp.text


def test_ac20_delivered_sales_dealer_may_not_group_by_sales_agent(client, db):
    cust = _delivered_world(db)
    contact = _staff(db, SALES_GRANT)
    _link(db, contact, cust)
    db.commit()
    resp = _get(client, REPORT, contact, measure="delivered_sales", group_by="sales_agent",
                date_from="2026-09-01", date_to="2026-09-30")
    old = _get(client, SALES_REPORT, contact, group_by="sales_agent", date_from="2026-09-01", date_to="2026-09-30")
    assert old.status_code == 403
    assert resp.status_code == old.status_code, resp.text


def test_ac20_delivered_sales_group_by_outside_its_axes_is_422(client, db):
    cust = _delivered_world(db)
    resp = _get(client, REPORT, _staff(db, SALES_GRANT), measure="delivered_sales", customer_ids=cust.id,
                group_by="month", top_n=3, date_from="2026-09-01", date_to="2026-09-30")
    assert resp.status_code == 422 and "Unknown measure" not in resp.text, resp.text
    assert "group_by" in resp.text, resp.text


# ============================================================================ sales_value


@pytest.fixture
def analysis_harness(monkeypatch):
    """The old route's file path stubbed (nothing sleeps, nothing is enqueued or uploaded)."""
    from app.api.v1.sales import analysis
    from app.services import queue_service, storage_router

    async def _no_wait(download_id):
        return None

    class _Cdn:
        def get_cdn_base_url(self, key):
            return f"https://cdn.test.invalid/{key}"

        get_cloudfront_base_url = get_cdn_base_url

        def get_signed_url(self, key, expires_in=0):
            return f"https://cdn.test.invalid/{key}?signed"

    monkeypatch.setattr(analysis, "_await_download", _no_wait)
    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: type("J", (), {"id": "job"})())
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _Cdn())


def _sale(db, amount, when, demand_class="retail"):
    from app.models.order import SalesOrder, SalesOrderLine

    so = SalesOrder(id=str(uuid.uuid4()), so_number=unique_code("SO"), order_date=when, status="open",
                    demand_class=demand_class, company_id=DEFAULT_COMPANY_ID)
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id,
                          product_id=product(db, company_id=DEFAULT_COMPANY_ID).id, qty_ordered=1,
                          qty_delivered=1, line_total=Decimal(amount), line_status="open",
                          company_id=DEFAULT_COMPANY_ID))
    db.flush()


def test_ac19_sales_value_equals_the_sales_analysis_read(client, db, analysis_harness):
    _sale(db, "100.00", date(2026, 9, 5), "retail")
    _sale(db, "250.00", date(2026, 9, 6), "project")
    contact = _staff(db, SALES_GRANT)
    window = dict(date_from="2026-01-01", date_to="2026-09-30")
    old = _get(client, ANALYSIS, contact, rows="channel", cols="year", basis="ordered", **window)
    new = _get(client, REPORT, contact, measure="sales_value", group_by="channel", basis="ordered", **window)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    o, n = old.json(), new.json()
    assert n["report_kind"] == "sales_analysis", n
    for key in ("columns", "rows", "totals", "total_count"):
        assert n[key] == o[key], key
    assert n["total_count"] >= 2


def test_ac20_sales_value_needs_the_sales_grant(client, db, analysis_harness):
    resp = _get(client, REPORT, _staff(db, OUT_GRANT), measure="sales_value", group_by="channel",
                basis="ordered", date_from="2026-01-01", date_to="2026-09-30")
    assert resp.status_code == 403, resp.text


def test_ac20_sales_value_refuses_a_contact_linked_to_a_customer(client, db, analysis_harness):
    contact = _staff(db, SALES_GRANT)
    _link(db, contact, seed_customer(db, name=unique_code("Own")))
    db.commit()
    old = _get(client, ANALYSIS, contact, rows="channel", cols="year", basis="ordered", date_from="2026-01-01", date_to="2026-09-30")
    new = _get(client, REPORT, contact, measure="sales_value", group_by="channel", basis="ordered",
               date_from="2026-01-01", date_to="2026-09-30")
    assert old.json()["status"] == "refused"
    assert new.status_code == 200 and new.json()["status"] == "refused", new.text
    assert new.json()["message"] == old.json()["message"]


def test_ac20_sales_value_keeps_the_analysis_rate_limit(client, db, analysis_harness, monkeypatch):
    from app.services import rate_limit

    monkeypatch.setattr(rate_limit, "hit", lambda *a, **k: rate_limit.RateResult(allowed=False))
    body = _get(client, REPORT, _staff(db, SALES_GRANT), measure="sales_value", group_by="channel",
                basis="ordered", date_from="2026-01-01", date_to="2026-09-30").json()
    assert body["status"] == "busy", body


# ============================================================================ low_stock


def _low_world(db, *, grants=(SALES_GRANT, LOW_GRANT, SUPPLIER_GRANT)):
    from tests.scm.test_low_stock_report import _product, _run, _summary_row

    run = _run(db, company_id=DEFAULT_COMPANY_ID)
    for stem, on_hand, level, supplier in (("L1", 2, 20, "ZZT Supplier A"), ("L2", 90, 20, "ZZT Supplier B")):
        _summary_row(db, run, _product(db, stem=stem), pool_on_hand=on_hand, reorder_level=level,
                     supplier_name=supplier)
    contact = _staff(db, *grants)
    return run, contact


def _counts(db):
    from app.models.base import company_scope
    from app.models.download import UserDownload
    from app.models.scm import ReorderRun

    with company_scope(db, None):
        return {"reorder_runs": db.query(ReorderRun).count(), "downloads": db.query(UserDownload).count()}


@pytest.fixture
def no_jobs(monkeypatch):
    from app.services import queue_service

    def _boom(*a, **k):
        raise AssertionError("a low stock read must never enqueue a job")

    monkeypatch.setattr(queue_service, "enqueue_job", _boom)


@pytest.fixture
def pick_run(monkeypatch):
    """`today_or_latest_run` reads `scm.reorder_run` by raw SQL, which the blank test schema cannot
    resolve: patch the pick, in its own return shape (`{"row": <run mapping>, "is_today": bool}`)."""
    from app.services.scm import reorder_run_service

    def _pick(run=None, *, status="completed", is_today=True):
        if run is None:
            picked = None
        else:
            picked = {
                "row": {"id": run.id, "status": status, "started_at": datetime(2026, 10, 10, 22, 0),
                        "finished_at": None},
                "is_today": is_today,
            }
        monkeypatch.setattr(reorder_run_service, "today_or_latest_run", lambda db, today=None: picked)

    return _pick


def test_ac19_low_stock_is_the_read_view_of_the_latest_run(client, db, no_jobs, pick_run):
    from app.services.scm.low_stock_report_service import build_low_stock_view

    run, contact = _low_world(db)
    pick_run(run)
    expected = json.loads(json.dumps(build_low_stock_view(db, run_id=run.id, include_supplier=True), default=str))
    resp = _get(client, REPORT, contact, measure="low_stock")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_kind"] == "low_stock" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(body["run_date"])), body
    assert {k: v for k, v in body.items() if k not in ("report_kind", "run_date")} == expected


def test_ac20_low_stock_without_the_supplier_grant_has_no_supplier_column(client, db, no_jobs, pick_run):
    run, contact = _low_world(db, grants=(SALES_GRANT, LOW_GRANT))
    pick_run(run)
    body = _get(client, REPORT, contact, measure="low_stock").json()
    assert "Supplier" not in body["columns"], body["columns"]


def test_ac20_low_stock_needs_its_grant(client, db, no_jobs, pick_run):
    run, contact = _low_world(db, grants=(SALES_GRANT,))
    pick_run(run)
    assert _get(client, REPORT, contact, measure="low_stock").status_code == 403


def test_ac21_low_stock_writes_nothing(client, db, no_jobs, pick_run):
    run, contact = _low_world(db)
    pick_run(run)
    db.commit()
    before = _counts(db)
    assert _get(client, REPORT, contact, measure="low_stock").status_code == 200
    assert _counts(db) == before


def test_ac21_with_no_run_the_reply_says_there_is_no_snapshot_and_writes_nothing(client, db, no_jobs, pick_run):
    contact = _staff(db, SALES_GRANT, LOW_GRANT)
    pick_run(None)
    before = _counts(db)
    resp = _get(client, REPORT, contact, measure="low_stock")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "no_snapshot" and "no low stock snapshot yet" in body["message"].lower(), body
    assert _counts(db) == before


@pytest.mark.parametrize("status", ["queued", "running"])
def test_ac21_a_run_still_being_built_with_no_completed_one_says_preparing(client, db, no_jobs, pick_run, status):
    run, contact = _low_world(db)
    pick_run(run, status=status)
    before = _counts(db)
    body = _get(client, REPORT, contact, measure="low_stock").json()
    assert body["status"] == "preparing" and "still being prepared" in body["message"], body
    assert _counts(db) == before


# ============================================================================ AC-22


@pytest.mark.parametrize(
    "measure, extra, param",
    [
        ("order_outstanding", {"group_by": "customer", "top_n": 3}, "group_by"),
        ("low_stock", {"date_from": "2026-09-01", "date_to": "2026-09-30"}, "date_from"),
        ("sales_value", {"product_ids": [str(uuid.uuid4())], "group_by": "channel", "basis": "ordered",
                         "date_from": "2026-01-01", "date_to": "2026-09-30"}, "product_ids"),
        ("delivered_sales", {"brand_ids": [str(uuid.uuid4())], "date_from": "2026-09-01",
                             "date_to": "2026-09-30"}, "brand_ids"),
    ],
)
def test_ac22_a_param_the_kind_does_not_support_is_422_naming_it(client, db, measure, extra, param):
    contact = _staff(db, SALES_GRANT, OUT_GRANT, LOW_GRANT)
    resp = _get(client, REPORT, contact, measure=measure, customer_ids=str(uuid.uuid4()), **extra)
    assert resp.status_code == 422, resp.text
    assert param in resp.text, resp.text


# ============================================================================ AC-23


def test_ac23_the_mcp_measure_description_lists_every_registry_key():
    """The MCP package is a sibling of the backend: appended to sys.path the repo way."""
    mcp_root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.catalog import CATALOG
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    from app.services.reports import measures

    spec = next((s for s in CATALOG if s.name == "crm_report"), None)
    assert spec is not None
    missing = [k for k in measures.keys() if k not in spec.description]
    assert not missing, f"crm_report description lacks {missing}"
    for key in ("order_outstanding", "delivered_sales", "sales_value", "low_stock", "so_count"):
        assert key in measures.keys(), key
    listed = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", spec.description))
    stale = {k for k in listed if re.fullmatch(r"(ordered|delivered|outstanding)_(amount|qty)", k)} - set(measures.keys())
    assert not stale, stale


def test_s4_known_grants_gain_the_two_new_grants():
    from app.services.reports import measures

    assert {OUT_GRANT, LOW_GRANT} <= set(measures.KNOWN_GRANTS)
