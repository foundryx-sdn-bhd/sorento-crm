"""Phase 2 RED tests - `GET /api/v1/order-management/top-selling`.

`documentation/plans/chatbot/PLAN-chatbot-top-x-hot-selling-24sep.md` (SQL source of
truth for sales by item, Backend contract, slice S2) and
`chatbot-top-x-hot-selling-24sep-acceptance-criteria.md` AC-1920 to AC-1930, as
amended by the owner's 26 Sep 2026 rulings on PR #1175:

* `rank_by` (quantity | amount) is REQUIRED, 422 when missing: no default metric.
* `basis` ordered | delivered, default delivered.
* `group` item | category, default item.
* `n` 1 to 1000 (TOP-N-UNCAP, 30 Sep 2026: was 1 to 100), optional; absent = every ranked row plus `total_count` (no paging,
  no page / page_size params, no has_more).
* date default = the current calendar year, echoed back resolved.
* a dealer caller is forced to its own customers; naming another customer is 403.
* zero-value lines are included.

Postgres only (`tests/_pg_fixture.py`), every row seeded here; CI's database has none.
Seed helpers are the same shape as `tests/test_sales_report.py`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import (
    ContactAccessType,
    RespondContact,
    RespondContactCustomer,
    respond_contact_access_types,
)
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.integration import Integration
from app.models.order import SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory
from app.models.sales_agent import SalesAgent
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.integration_key_service import IntegrationKeyService

from tests._mc_lookup_seed import customer, product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/top-selling"
PERMISSION_SLUG = "order_management.orders.view"
Y2026 = {"date_from": "2026-01-01", "date_to": "2026-12-31"}


def _money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _seed_superadmin(db) -> dict:
    user = User(id=str(uuid.uuid4()), email=f"{unique_code('SA')}@test.com", name="ZZT Superadmin", status="ACTIVE")
    role = UserRole(id=str(uuid.uuid4()), slug="superadmin", name="Superadmin")
    db.add_all([user, role])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.flush()
    return {"id": user.id, "email": user.email}


@pytest.fixture
def client(db):
    principal = _seed_superadmin(db)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------- seed helpers


def _category(db, name: str) -> ProductCategory:
    row = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("CAT")[:50],
        category_name=name,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _product(db, code: str | None = None, *, category: ProductCategory | None = None) -> Product:
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code or unique_code("SKU"))
    row.product_name = f"Name of {row.product_code}"
    if category is not None:
        row.category_id = category.id
    db.flush()
    return row


def _agent(db, code: str) -> SalesAgent:
    row = SalesAgent(id=str(uuid.uuid4()), sales_agent=code, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _line(
    db,
    *,
    product_id,
    ordered,
    delivered,
    line_total=None,
    customer_id=None,
    sales_agent_id=None,
    line_status="open",
    header_status="open",
    demand_class=None,
    order_date=date(2026, 6, 1),
    required_date=None,
):
    """One SO with ONE line."""
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=unique_code("SO"),
        customer_id=customer_id,
        sales_agent_id=sales_agent_id,
        order_date=order_date,
        status=header_status,
        demand_class=demand_class,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()),
            sales_order_id=so.id,
            product_id=product_id,
            qty_ordered=ordered,
            qty_delivered=delivered,
            line_total=line_total,
            line_status=line_status,
            required_date=required_date,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    return so


def _get(client, **params):
    merged = {**Y2026, **params}
    return client.get(BASE, params={k: v for k, v in merged.items() if v is not None})


def _codes(body) -> list[str]:
    return [r["code"] for r in body["rows"]]


def _contact(db, *, granted=True) -> RespondContact:
    from app.services.contact_field_reveal_service import set_granted_keys

    contact = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6{unique_code('PH')[:10]}")
    db.add(contact)
    db.flush()
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    if granted:
        set_granted_keys(db, contact.id, ["sales_orders.sales_report"], actor_id=None)
    db.flush()
    return contact


def _link(db, contact, cust) -> None:
    db.add(
        RespondContactCustomer(
            id=str(uuid.uuid4()), contact_id=contact.id, customer_id=cust.id,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()


def _access(db, contact, name: str, *, active: bool = True) -> None:
    """Give `contact` one access type named `name` (a fresh catalog row)."""
    code = unique_code("zzt_at").lower().replace("-", "_")[:50]
    db.add(ContactAccessType(code=code, name=name, is_active=active))
    db.flush()
    db.execute(respond_contact_access_types.insert().values(contact_id=contact.id, access_type_code=code))
    db.flush()


def _as_contact(contact) -> dict:
    return {"contact_id": contact.id, "space_id": "zzt-space"}


# --------------------------------------------------------------------- ranking


def test_rank_by_quantity_and_amount_with_tiebreak(client, db):
    """AC-1920: quantity ranks by summed qty desc, ties by amount desc, then code
    asc; amount ranks by summed amount desc, ties by qty desc, then code asc."""
    a = _product(db, "ZZTRANK-A")
    b = _product(db, "ZZTRANK-B")
    c = _product(db, "ZZTRANK-C")
    d = _product(db, "ZZTRANK-D")
    _line(db, product_id=a.id, ordered=10, delivered=10, line_total=Decimal("100.00"))
    _line(db, product_id=b.id, ordered=10, delivered=10, line_total=Decimal("300.00"))
    _line(db, product_id=c.id, ordered=5, delivered=5, line_total=Decimal("300.00"))
    _line(db, product_id=d.id, ordered=5, delivered=5, line_total=Decimal("300.00"))
    db.commit()

    by_qty = _get(client, rank_by="quantity")
    assert by_qty.status_code == 200, by_qty.text
    body = by_qty.json()
    assert _codes(body) == ["ZZTRANK-B", "ZZTRANK-A", "ZZTRANK-C", "ZZTRANK-D"]
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3, 4]
    assert "name" not in body["rows"][0]
    assert body["rows"][0]["quantity"] == 10
    assert _money(body["rows"][0]["amount"]) == Decimal("300.00")
    assert body["rank_by"] == "quantity"

    by_amount = _get(client, rank_by="amount").json()
    assert _codes(by_amount) == ["ZZTRANK-B", "ZZTRANK-C", "ZZTRANK-D", "ZZTRANK-A"]
    assert by_amount["rank_by"] == "amount"


def test_rank_by_is_required(client, db):
    """Owner ruling: no default metric. Missing rank_by is 422, not a guess."""
    resp = _get(client)
    assert resp.status_code == 422, resp.text
    assert "rank_by" in resp.text


@pytest.mark.parametrize(
    "params",
    [
        {"rank_by": "qty"},
        {"rank_by": "value"},
        {"rank_by": "quantity", "basis": "confirmed"},
        {"rank_by": "quantity", "group": "customer"},
        {"rank_by": "quantity", "channel": "retail"},
        {"rank_by": "quantity", "date_from": "not-a-date"},
    ],
)
def test_unknown_values_422(client, db, params):
    resp = _get(client, **params)
    assert resp.status_code == 422, (params, resp.text)


def test_both_bases_default_delivered(client, db):
    """basis=delivered (default) counts LEAST(qty_delivered, qty_ordered) and its
    share of line_total; basis=ordered counts the whole line."""
    a = _product(db, "ZZTBASIS-A")
    b = _product(db, "ZZTBASIS-B")
    # A: ordered 100, delivered 10 (open line). B: ordered 20, delivered 20.
    _line(db, product_id=a.id, ordered=100, delivered=10, line_total=Decimal("1000.00"))
    _line(db, product_id=b.id, ordered=20, delivered=25, line_total=Decimal("400.00"))
    db.commit()

    default = _get(client, rank_by="quantity").json()
    assert default["basis"] == "delivered"
    assert _codes(default) == ["ZZTBASIS-B", "ZZTBASIS-A"]
    rows = {r["code"]: r for r in default["rows"]}
    assert rows["ZZTBASIS-B"]["quantity"] == 20, "delivered is capped at ordered"
    assert _money(rows["ZZTBASIS-B"]["amount"]) == Decimal("400.00")
    assert rows["ZZTBASIS-A"]["quantity"] == 10
    assert _money(rows["ZZTBASIS-A"]["amount"]) == Decimal("100.00")

    ordered = _get(client, rank_by="quantity", basis="ordered").json()
    assert ordered["basis"] == "ordered"
    assert _codes(ordered) == ["ZZTBASIS-A", "ZZTBASIS-B"]
    rows = {r["code"]: r for r in ordered["rows"]}
    assert rows["ZZTBASIS-A"]["quantity"] == 100
    assert _money(rows["ZZTBASIS-A"]["amount"]) == Decimal("1000.00")


def test_ordered_basis_counts_a_closed_short_delivered_line_in_full(client, db):
    """AC-1931 as grilled: basis=ordered is `qty_ordered` / `line_total`. A closed
    line that delivered 2 of 7 still counts 7 / RM 700.00 ordered (the sales
    report's own ordered pair would count only the 2 delivered), and a null
    line_total counts RM 0.00. The delivered basis is unchanged."""
    a = _product(db, "ZZTCLOSED-A")
    b = _product(db, "ZZTCLOSED-B")
    _line(
        db, product_id=a.id, ordered=7, delivered=2, line_total=Decimal("700.00"),
        line_status="closed", header_status="closed",
    )
    _line(db, product_id=b.id, ordered=4, delivered=4, line_total=None)
    db.commit()

    ordered = _get(client, rank_by="quantity", basis="ordered").json()
    rows = {r["code"]: r for r in ordered["rows"]}
    assert rows["ZZTCLOSED-A"]["quantity"] == 7
    assert _money(rows["ZZTCLOSED-A"]["amount"]) == Decimal("700.00")
    assert rows["ZZTCLOSED-B"]["quantity"] == 4
    assert _money(rows["ZZTCLOSED-B"]["amount"]) == Decimal("0.00")
    assert _codes(ordered) == ["ZZTCLOSED-A", "ZZTCLOSED-B"]
    assert ordered["totals"]["quantity"] == 11
    assert _money(ordered["totals"]["amount"]) == Decimal("700.00")

    delivered = _get(client, rank_by="quantity").json()
    rows = {r["code"]: r for r in delivered["rows"]}
    assert rows["ZZTCLOSED-A"]["quantity"] == 2
    assert _money(rows["ZZTCLOSED-A"]["amount"]) == Decimal("200.00")


def test_delivered_basis_leaves_out_undelivered_items(client, db):
    """An item nothing was delivered of has no delivered sale to rank."""
    a = _product(db, "ZZTUNDEL-A")
    b = _product(db, "ZZTUNDEL-B")
    _line(db, product_id=a.id, ordered=5, delivered=0, line_total=Decimal("50.00"))
    _line(db, product_id=b.id, ordered=5, delivered=5, line_total=Decimal("50.00"))
    db.commit()

    assert _codes(_get(client, rank_by="quantity").json()) == ["ZZTUNDEL-B"]
    assert _codes(_get(client, rank_by="quantity", basis="ordered").json()) == ["ZZTUNDEL-A", "ZZTUNDEL-B"]


# --------------------------------------------------------------------- n, no paging


def test_absent_n_returns_every_row_with_total_count(client, db):
    """Owner ruling (26 Sep 01:50Z): no number = no cut-off, every ranked row, and
    the full count so the chatbot can ask how many to show. No paging keys."""
    for i in range(13):
        p = _product(db, f"ZZTALL-{i:02d}")
        _line(db, product_id=p.id, ordered=100 - i, delivered=100 - i, line_total=Decimal("1.00"))
    db.commit()

    body = _get(client, rank_by="quantity").json()
    assert body["n"] is None
    assert body["total_count"] == 13
    assert len(body["rows"]) == 13
    assert body["rows"][-1]["rank"] == 13
    for banned in ("has_more", "page", "page_size", "next"):
        assert banned not in body, banned


def test_n_cuts_the_list_and_totals_cover_the_whole_set(client, db):
    for i in range(12):
        p = _product(db, f"ZZTN-{i:02d}")
        _line(db, product_id=p.id, ordered=10 + i, delivered=10 + i, line_total=Decimal("10.00"))
    db.commit()

    body = _get(client, rank_by="quantity", n=5).json()
    assert body["n"] == 5
    assert body["total_count"] == 12
    assert _codes(body) == ["ZZTN-11", "ZZTN-10", "ZZTN-09", "ZZTN-08", "ZZTN-07"]
    assert body["totals"]["quantity"] == sum(10 + i for i in range(12))
    assert _money(body["totals"]["amount"]) == Decimal("120.00")


def test_count_only_returns_the_full_count_and_no_rows(client, db):
    """S4 (owner, PR #1258 05:32Z): with no N named the chatbot states the full count
    and asks how many. `count_only` is how the lane asks the route for that count
    WITHOUT the whole ranked book (review N1: a full year was 13,230 rows, about 1 MB):
    no rows, the true `total_count` and whole-set `totals`, `n` null."""
    for i in range(7):
        p = _product(db, f"ZZTCNT-{i:02d}")
        _line(db, product_id=p.id, ordered=10 + i, delivered=10 + i, line_total=Decimal("2.00"))
    db.commit()

    body = _get(client, rank_by="quantity", count_only="true").json()
    assert body["rows"] == []
    assert body["total_count"] == 7
    assert body["n"] is None
    assert body["totals"]["quantity"] == sum(10 + i for i in range(7))
    assert _money(body["totals"]["amount"]) == Decimal("14.00")


def test_count_only_keeps_a_single_row(client, db):
    """One ranked row is sent as is (nothing to choose between), so `count_only`
    returns it rather than a count to ask about."""
    p = _product(db, "ZZTCNT-ONE")
    _line(db, product_id=p.id, ordered=3, delivered=3, line_total=Decimal("9.00"))
    db.commit()

    body = _get(client, rank_by="quantity", count_only="true").json()
    assert body["total_count"] == 1
    assert _codes(body) == ["ZZTCNT-ONE"]


def test_count_only_asks_the_service_for_one_row(client, db, monkeypatch):
    """Reviewer kill R4 (PR #1273): `count_only` reads the count off ONE row, never the
    whole ranked book (#1263 N1). Output-equivalent otherwise, so pin the call."""
    import app.services.sales_report_service as service

    seen: list = []
    real = service.top_selling

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("n"))
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "top_selling", _spy)
    _get(client, rank_by="quantity", count_only="true")
    assert seen == [1]


def test_count_only_with_no_sales_is_an_empty_miss(client, db):
    body = _get(client, rank_by="quantity", count_only="true").json()
    assert body["total_count"] == 0
    assert body["rows"] == []


@pytest.mark.parametrize(
    "n,status", [(0, 422), (-1, 422), (1, 200), (100, 200), (101, 200), (1000, 200), (1001, 422)]
)
def test_n_cap(client, db, n, status):
    """TOP-N-UNCAP (owner, 30 Sep 2026): no 100 cap; only the 1000 safety ceiling 422s."""
    p = _product(db)
    _line(db, product_id=p.id, ordered=1, delivered=1, line_total=Decimal("1.00"))
    db.commit()
    resp = _get(client, rank_by="quantity", n=n)
    assert resp.status_code == status, (n, resp.text)


def test_n_past_the_ceiling_names_the_ceiling(client, db):
    resp = _get(client, rank_by="quantity", n=1001)
    assert resp.status_code == 422
    assert "1000" in resp.json()["message"]


def test_top_200_returns_200_rows_and_renders_as_ordered_parts(client, db):
    """TOP-N-UNCAP end to end from the route: n=200 returns 200 rows, and the real MCP
    presenter prints every one of them across ordered WhatsApp-sized parts."""
    import re

    for i in range(205):
        p = _product(db, f"ZZTN{i:04d}")
        _line(db, product_id=p.id, ordered=1000 - i, delivered=1000 - i, line_total=Decimal("10.00"))
    db.commit()
    body = _get(client, rank_by="quantity", n=200).json()
    assert body["total_count"] == 205
    assert [r["rank"] for r in body["rows"]] == list(range(1, 201))
    assert _codes(body)[:3] == ["ZZTN0000", "ZZTN0001", "ZZTN0002"]

    body5 = _get(client, rank_by="quantity", n=5).json()
    assert _codes(body5) == [f"ZZTN{i:04d}" for i in range(5)]

    from tests.chatbot.test_outstanding_lane import _present_response

    import json

    rendered = json.loads(_present_response()("crm_top_selling_report", json.dumps(body, default=str)))["response"]
    parts = re.split(r"\n\n(?=\(\d+/\d+\)\n)", rendered)
    assert len(parts) > 1
    assert all(len(p) <= 3900 for p in parts)
    assert [p.split("\n", 1)[0] for p in parts] == [f"({k}/{len(parts)})" for k in range(1, len(parts) + 1)]
    printed = [line.split(":", 1)[0] for p in parts for line in p.split("\n") if re.match(r"^\d+\. ZZTN", line)]
    assert printed == [f"{i + 1}. ZZTN{i:04d}" for i in range(200)]


# --------------------------------------------------------------------- dates


def test_date_window_on_bucket_date(client, db):
    """AC-1922: the window filters COALESCE(required_date, order_date)."""
    a = _product(db, "ZZTDATE-A")
    b = _product(db, "ZZTDATE-B")
    c = _product(db, "ZZTDATE-C")
    _line(db, product_id=a.id, ordered=1, delivered=1, order_date=date(2026, 1, 1), required_date=date(2026, 3, 15))
    _line(db, product_id=b.id, ordered=1, delivered=1, order_date=date(2026, 3, 20))
    _line(db, product_id=c.id, ordered=1, delivered=1, order_date=date(2026, 3, 1), required_date=date(2026, 5, 1))
    db.commit()

    body = _get(client, rank_by="quantity", date_from="2026-03-01", date_to="2026-03-31").json()
    assert sorted(_codes(body)) == ["ZZTDATE-A", "ZZTDATE-B"]
    assert body["date_from"] == "2026-03-01"
    assert body["date_to"] == "2026-03-31"


def test_date_default_is_current_calendar_year(client, db):
    year = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).year
    this_year = _product(db, "ZZTYEAR-NOW")
    last_year = _product(db, "ZZTYEAR-OLD")
    _line(db, product_id=this_year.id, ordered=1, delivered=1, order_date=date(year, 1, 2))
    _line(db, product_id=last_year.id, ordered=1, delivered=1, order_date=date(year - 1, 12, 31))
    db.commit()

    resp = client.get(BASE, params={"rank_by": "quantity"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _codes(body) == ["ZZTYEAR-NOW"]
    assert body["date_from"] == f"{year}-01-01"
    assert body["date_to"] == f"{year}-12-31"


# --------------------------------------------------------------------- group / filters


def test_category_grain(client, db):
    """group=category ranks categories, code = category_code (owner ruling 26 Sep
    ~07:40Z: no name field on rows, code will do)."""
    sinks = _category(db, "ZZT Kitchen Sink")
    taps = _category(db, "ZZT Kitchen Tap")
    s1 = _product(db, category=sinks)
    s2 = _product(db, category=sinks)
    t1 = _product(db, category=taps)
    _line(db, product_id=s1.id, ordered=3, delivered=3, line_total=Decimal("300.00"))
    _line(db, product_id=s2.id, ordered=4, delivered=4, line_total=Decimal("400.00"))
    _line(db, product_id=t1.id, ordered=5, delivered=5, line_total=Decimal("900.00"))
    db.commit()

    by_qty = _get(client, rank_by="quantity", group="category").json()
    assert by_qty["group"] == "category"
    assert _codes(by_qty) == [sinks.category_code, taps.category_code]
    top = by_qty["rows"][0]
    assert "name" not in top
    assert top["quantity"] == 7
    assert _money(top["amount"]) == Decimal("700.00")
    assert by_qty["total_count"] == 2

    by_amount = _get(client, rank_by="amount", group="category").json()
    assert _codes(by_amount) == [taps.category_code, sinks.category_code]


def test_category_filter(client, db):
    """AC-1924: category_ids filters products.category_id (NOT NULL in this schema,
    so every product has one); the echo names the category."""
    sinks = _category(db, "ZZT Sink Filter")
    inside = _product(db, "ZZTCATF-IN", category=sinks)
    outside = _product(db, "ZZTCATF-OUT")
    for p in (inside, outside):
        _line(db, product_id=p.id, ordered=1, delivered=1, line_total=Decimal("1.00"))
    db.commit()

    body = _get(client, rank_by="quantity", category_ids=sinks.id).json()
    assert _codes(body) == ["ZZTCATF-IN"]
    assert body["filters"]["category_name"] == "ZZT Sink Filter"

    unfiltered = _get(client, rank_by="quantity").json()
    assert sorted(_codes(unfiltered)) == ["ZZTCATF-IN", "ZZTCATF-OUT"]
    assert unfiltered["filters"]["category_name"] is None


def test_customer_filter_ids_and_query(client, db):
    """AC-1923: customer_ids filters sales_orders.customer_id; customer_query
    ILIKEs the name and needs 3 characters; the echo is the customer's name."""
    hanlim = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT HANLIM TRADING")
    other = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT OTHER DEALER")
    a = _product(db, "ZZTCUST-A")
    b = _product(db, "ZZTCUST-B")
    _line(db, product_id=a.id, ordered=1, delivered=1, customer_id=hanlim.id)
    _line(db, product_id=b.id, ordered=9, delivered=9, customer_id=other.id)
    db.commit()

    body = _get(client, rank_by="quantity", customer_ids=hanlim.id).json()
    assert _codes(body) == ["ZZTCUST-A"]
    assert body["filters"]["customer_name"] == "ZZT HANLIM TRADING"

    assert _codes(_get(client, rank_by="quantity", customer_query="hanlim").json()) == ["ZZTCUST-A"]
    assert _get(client, rank_by="quantity", customer_query="ha").status_code == 422


def test_channel_filter(client, db):
    """AC-1925: dealer = demand_class retail, project = project, absent = all."""
    r = _product(db, "ZZTCH-R")
    p = _product(db, "ZZTCH-P")
    n = _product(db, "ZZTCH-N")
    _line(db, product_id=r.id, ordered=1, delivered=1, demand_class="retail")
    _line(db, product_id=p.id, ordered=1, delivered=1, demand_class="project")
    _line(db, product_id=n.id, ordered=1, delivered=1, demand_class=None)
    db.commit()

    dealer = _get(client, rank_by="quantity", channel="dealer").json()
    assert _codes(dealer) == ["ZZTCH-R"]
    assert dealer["filters"]["channel"] == "dealer"
    assert _codes(_get(client, rank_by="quantity", channel="project").json()) == ["ZZTCH-P"]
    assert sorted(_codes(_get(client, rank_by="quantity").json())) == ["ZZTCH-N", "ZZTCH-P", "ZZTCH-R"]


def test_sales_agent_filter_and_fill_rate(client, db):
    """Filter on sales_orders.sales_agent_id (owner ruling); the response carries the
    agent code and the share of the window's SOs that carry any agent at all."""
    sean = _agent(db, "ZZT SEAN I")
    other = _agent(db, "ZZT OTHER")
    a = _product(db, "ZZTAGENT-A")
    b = _product(db, "ZZTAGENT-B")
    _line(db, product_id=a.id, ordered=1, delivered=1, sales_agent_id=sean.id)
    _line(db, product_id=b.id, ordered=5, delivered=5, sales_agent_id=other.id)
    _line(db, product_id=b.id, ordered=5, delivered=5, sales_agent_id=None)
    _line(db, product_id=b.id, ordered=5, delivered=5, sales_agent_id=None)
    db.commit()

    body = _get(client, rank_by="quantity", sales_agent_ids=sean.id).json()
    assert _codes(body) == ["ZZTAGENT-A"]
    assert body["filters"]["sales_agent"] == "ZZT SEAN I"
    assert body["sales_agent_fill_rate"] == pytest.approx(0.5)

    unfiltered = _get(client, rank_by="quantity").json()
    assert unfiltered["sales_agent_fill_rate"] is None
    assert unfiltered["filters"]["sales_agent"] is None


def test_cancelled_and_undated_excluded(client, db):
    """AC-1926."""
    ok = _product(db, "ZZTCAN-OK")
    so_cancel = _product(db, "ZZTCAN-SO")
    line_cancel = _product(db, "ZZTCAN-LINE")
    undated = _product(db, "ZZTCAN-UNDATED")
    _line(db, product_id=ok.id, ordered=1, delivered=1)
    _line(db, product_id=so_cancel.id, ordered=9, delivered=9, header_status="cancelled")
    _line(db, product_id=line_cancel.id, ordered=9, delivered=9, line_status="cancelled")
    _line(db, product_id=undated.id, ordered=9, delivered=9, order_date=None)
    db.commit()

    body = _get(client, rank_by="quantity", basis="ordered", date_from="2000-01-01", date_to="2100-12-31").json()
    assert _codes(body) == ["ZZTCAN-OK"]


def test_zero_value_lines_included(client, db):
    """AC-1930 / Q9 ruling: a line_total = 0 line still ranks by quantity and adds RM 0."""
    zero = _product(db, "ZZTZERO-A")
    priced = _product(db, "ZZTZERO-B")
    _line(db, product_id=zero.id, ordered=50, delivered=50, line_total=Decimal("0"))
    _line(db, product_id=priced.id, ordered=1, delivered=1, line_total=Decimal("10.00"))
    db.commit()

    by_qty = _get(client, rank_by="quantity").json()
    assert _codes(by_qty) == ["ZZTZERO-A", "ZZTZERO-B"]
    assert _money(by_qty["rows"][0]["amount"]) == Decimal("0.00")

    by_amount = _get(client, rank_by="amount").json()
    assert _codes(by_amount) == ["ZZTZERO-B", "ZZTZERO-A"]


def test_and_of_all_filters(client, db):
    """AC-1927: one foil per axis; only the row passing every filter survives."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT AND CUSTOMER")
    foil_cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT AND FOIL")
    cat = _category(db, "ZZT And Category")
    agent = _agent(db, "ZZT AND AGENT")
    hit = _product(db, "ZZTAND-HIT", category=cat)
    other_cat = _product(db, "ZZTAND-CAT")
    base = dict(ordered=1, delivered=1, customer_id=cust.id, demand_class="retail", sales_agent_id=agent.id)
    _line(db, product_id=hit.id, **base)
    _line(db, product_id=other_cat.id, **base)
    _line(db, product_id=hit.id, **{**base, "customer_id": foil_cust.id, "ordered": 9, "delivered": 9})
    _line(db, product_id=hit.id, **{**base, "demand_class": "project", "ordered": 9, "delivered": 9})
    _line(db, product_id=hit.id, **{**base, "sales_agent_id": None, "ordered": 9, "delivered": 9})
    _line(db, product_id=hit.id, **{**base, "order_date": date(2025, 6, 1), "ordered": 9, "delivered": 9})
    db.commit()

    body = _get(
        client, rank_by="quantity", customer_ids=cust.id, category_ids=cat.id,
        channel="dealer", sales_agent_ids=agent.id,
    ).json()
    assert _codes(body) == ["ZZTAND-HIT"]
    assert body["rows"][0]["quantity"] == 1


def test_response_model_keeps_every_field(client, db):
    """AC-1928: every declared field reaches the HTTP body."""
    p = _product(db)
    _line(db, product_id=p.id, ordered=1, delivered=1, line_total=Decimal("1.00"))
    db.commit()

    body = _get(client, rank_by="amount", n=3).json()
    for key in (
        "rank_by", "basis", "group", "n", "date_from", "date_to", "filters",
        "total_count", "rows", "totals", "sales_agent_fill_rate",
    ):
        assert key in body, key
    assert "detail" in body
    for key in ("customer_name", "category_name", "sales_agent", "channel", "dealer_scoped"):
        assert key in body["filters"], key
    assert set(body["rows"][0]) == {"rank", "code", "quantity", "amount"}
    assert set(body["totals"]) == {"quantity", "amount"}


def test_rows_have_no_name_key(client, db):
    """Owner ruling 26 Sep ~07:40Z: "don't need to show name, just show code will
    do". A ranked row carries code, never name, whichever grain is asked."""
    cat = _category(db, "ZZT No Name Cat")
    p = _product(db, "ZZTNONAME-P", category=cat)
    _line(db, product_id=p.id, ordered=1, delivered=1, line_total=Decimal("1.00"))
    db.commit()

    by_item = _get(client, rank_by="quantity", group="item").json()
    assert by_item["rows"][0]["code"] == "ZZTNONAME-P"
    assert "name" not in by_item["rows"][0]

    by_category = _get(client, rank_by="quantity", group="category").json()
    assert by_category["rows"][0]["code"] == cat.category_code
    assert "name" not in by_category["rows"][0]


# --------------------------------------------------------------------- detail offer


def test_detail_code_customers_and_months(client, db):
    """AC-1935: `detail_code` returns that code's `by_customer` and `by_month`
    under the same filters and basis, each sorted by the metric desc (ties by
    the other metric desc, then name / month asc). `rows` / `totals` narrow to
    that one code; other codes and filtered-out lines contribute nothing."""
    cat = _category(db, "ZZT DETAIL CAT")
    other_cat = _category(db, "ZZT DETAIL OTHER")
    a = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT DETAIL ALPHA")
    b = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT DETAIL BRAVO")
    p = _product(db, "ZZTDETAIL-P", category=cat)
    q = _product(db, "ZZTDETAIL-Q", category=other_cat)
    _line(db, product_id=p.id, ordered=4, delivered=4, line_total=Decimal("400.00"), customer_id=a.id,
          order_date=date(2026, 3, 5))
    _line(db, product_id=p.id, ordered=6, delivered=6, line_total=Decimal("300.00"), customer_id=b.id,
          order_date=date(2026, 3, 20))
    _line(db, product_id=p.id, ordered=5, delivered=5, line_total=Decimal("900.00"), customer_id=a.id,
          order_date=date(2026, 7, 1))
    # Foils: another code, a cancelled line, a line outside the window, a project line.
    _line(db, product_id=q.id, ordered=50, delivered=50, line_total=Decimal("50.00"), customer_id=a.id)
    _line(db, product_id=p.id, ordered=70, delivered=70, customer_id=a.id, line_status="cancelled")
    _line(db, product_id=p.id, ordered=80, delivered=80, customer_id=b.id, order_date=date(2025, 5, 1))
    _line(db, product_id=p.id, ordered=90, delivered=90, customer_id=b.id, demand_class="project")
    db.commit()

    resp = _get(client, rank_by="quantity", detail_code="zztdetail-p")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    detail = body["detail"]
    assert detail["code"] == "ZZTDETAIL-P"
    assert detail["name"] == "Name of ZZTDETAIL-P"
    by_customer = [(r["customer_name"], r["quantity"], _money(r["amount"])) for r in detail["by_customer"]]
    assert by_customer[0] == ("ZZT DETAIL BRAVO", 96, Decimal("300.00"))
    assert by_customer[1] == ("ZZT DETAIL ALPHA", 9, Decimal("1300.00"))
    by_month = [(r["month"], r["quantity"]) for r in detail["by_month"]]
    assert by_month == [("2026-06", 90), ("2026-03", 10), ("2026-07", 5)]
    assert _codes(body) == ["ZZTDETAIL-P"]
    assert body["total_count"] == 1
    assert body["totals"]["quantity"] == 105

    # The same filters narrow the detail: dealer channel drops the project line,
    # and ranking by amount re-sorts both lists.
    dealer = _get(client, rank_by="amount", detail_code="ZZTDETAIL-P", channel="dealer").json()
    assert dealer["detail"] is None and dealer["rows"] == []  # no line is demand_class retail
    by_amount = _get(client, rank_by="amount", detail_code="ZZTDETAIL-P").json()["detail"]
    assert [r["customer_name"] for r in by_amount["by_customer"]] == ["ZZT DETAIL ALPHA", "ZZT DETAIL BRAVO"]
    assert [r["month"] for r in by_amount["by_month"]][:2] == ["2026-07", "2026-03"]

    # Category grain takes a category code.
    by_cat = _get(client, rank_by="quantity", group="category", detail_code=cat.category_code).json()
    assert by_cat["detail"]["code"] == cat.category_code
    assert by_cat["detail"]["name"] == "ZZT DETAIL CAT"
    assert by_cat["totals"]["quantity"] == 105

    # A code with no sales is a miss, not an error; no detail_code, no detail.
    miss = _get(client, rank_by="quantity", detail_code="ZZTDETAIL-NONE").json()
    assert miss["detail"] is None and miss["rows"] == [] and miss["total_count"] == 0
    assert _get(client, rank_by="quantity").json()["detail"] is None


def test_detail_code_too_long_is_422(client, db):
    resp = _get(client, rank_by="quantity", detail_code="X" * 101)
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------- contact gates


def test_contact_without_the_key_is_refused(client, db):
    p = _product(db)
    _line(db, product_id=p.id, ordered=1, delivered=1)
    contact = _contact(db, granted=False)
    db.commit()

    refused = _get(client, rank_by="quantity", **_as_contact(contact))
    assert refused.status_code == 403, refused.text
    assert "sales_report_not_enabled" in refused.text


@pytest.mark.parametrize("params", [{"contact_id": "zzt-contact"}, {"space_id": "zzt-space"}])
def test_contact_identity_is_both_or_neither(client, db, params):
    resp = _get(client, rank_by="quantity", **params)
    assert resp.status_code == 422, (params, resp.text)


def test_staff_contact_sees_every_customer(client, db):
    """Staff is positive: a granted contact holding an office access type (the
    tier `tier_gate.parse_level` reads, e.g. "Sorento Office") and no link."""
    a = customer(db, company_id=DEFAULT_COMPANY_ID)
    b = customer(db, company_id=DEFAULT_COMPANY_ID)
    pa = _product(db, "ZZTSTAFF-A")
    pb = _product(db, "ZZTSTAFF-B")
    _line(db, product_id=pa.id, ordered=1, delivered=1, customer_id=a.id)
    _line(db, product_id=pb.id, ordered=2, delivered=2, customer_id=b.id)
    contact = _contact(db)
    _access(db, contact, "Sorento Office")
    db.commit()

    body = _get(client, rank_by="quantity", **_as_contact(contact)).json()
    assert _codes(body) == ["ZZTSTAFF-B", "ZZTSTAFF-A"]
    assert body["filters"]["dealer_scoped"] is False

    named = _get(client, rank_by="quantity", customer_ids=a.id, **_as_contact(contact)).json()
    assert _codes(named) == ["ZZTSTAFF-A"]


def test_dealer_is_forced_to_its_own_customers(client, db):
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT OWN DEALER")
    rival = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT RIVAL DEALER")
    po = _product(db, "ZZTDEAL-OWN")
    pr = _product(db, "ZZTDEAL-RIVAL")
    _line(db, product_id=po.id, ordered=1, delivered=1, customer_id=own.id)
    _line(db, product_id=pr.id, ordered=99, delivered=99, customer_id=rival.id)
    contact = _contact(db)
    _link(db, contact, own)
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _codes(body) == ["ZZTDEAL-OWN"]
    assert body["filters"]["dealer_scoped"] is True
    assert body["filters"]["customer_name"] == "ZZT OWN DEALER"
    assert body["totals"]["quantity"] == 1

    # Naming its own ledger is fine.
    assert _codes(_get(client, rank_by="quantity", customer_ids=own.id, **_as_contact(contact)).json()) == [
        "ZZTDEAL-OWN"
    ]


def test_dealer_naming_another_customer_is_refused(client, db):
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT OWN LEDGER")
    rival = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT RIVAL LEDGER")
    pr = _product(db)
    _line(db, product_id=pr.id, ordered=99, delivered=99, customer_id=rival.id)
    contact = _contact(db)
    _link(db, contact, own)
    db.commit()

    by_id = _get(client, rank_by="quantity", customer_ids=rival.id, **_as_contact(contact))
    assert by_id.status_code == 403, by_id.text
    assert "customer_not_permitted" in by_id.text
    assert "RIVAL" not in by_id.text

    mixed = _get(client, rank_by="quantity", customer_ids=f"{own.id},{rival.id}", **_as_contact(contact))
    assert mixed.status_code == 403, mixed.text

    by_query = _get(client, rank_by="quantity", customer_query="rival ledger", **_as_contact(contact))
    assert by_query.status_code == 403, by_query.text
    assert "customer_not_permitted" in by_query.text


def test_dealer_customer_query_is_no_name_oracle(client, db):
    """S1: a dealer's `customer_query` that matches only other customers and one
    that matches nobody get the SAME answer, so the route never tells a dealer
    whether a name exists in the book. A query matching its own ledger answers."""
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT ORACLE OWN")
    rival = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT ORACLE RIVAL")
    po = _product(db, "ZZTORACLE-OWN")
    pr = _product(db, "ZZTORACLE-RIVAL")
    _line(db, product_id=po.id, ordered=1, delivered=1, customer_id=own.id)
    _line(db, product_id=pr.id, ordered=9, delivered=9, customer_id=rival.id)
    contact = _contact(db)
    _link(db, contact, own)
    db.commit()

    other = _get(client, rank_by="quantity", customer_query="oracle rival", **_as_contact(contact))
    nobody = _get(client, rank_by="quantity", customer_query="zzt no such name", **_as_contact(contact))
    assert other.status_code == nobody.status_code == 403, (other.text, nobody.text)
    assert other.json() == nobody.json()
    assert "customer_not_permitted" in nobody.text
    assert "RIVAL" not in other.text

    mine = _get(client, rank_by="quantity", customer_query="oracle own", **_as_contact(contact))
    assert mine.status_code == 200, mine.text
    assert _codes(mine.json()) == ["ZZTORACLE-OWN"]
    both = _get(client, rank_by="quantity", customer_query="zzt oracle", **_as_contact(contact))
    assert both.status_code == 200, both.text
    assert _codes(both.json()) == ["ZZTORACLE-OWN"]


def test_dealer_access_type_without_a_linked_customer_is_refused(client, db):
    """Fail closed: a contact typed as a dealer but linked to no customer has no
    ledgers to be scoped to, so it never falls through to the whole book."""
    p = _product(db)
    _line(db, product_id=p.id, ordered=1, delivered=1)
    code = unique_code("dealer").lower().replace("-", "_")[:40] + "_dealer"
    db.add(ContactAccessType(code=code, name="ZZT Sorento Dealer"))
    contact = _contact(db)
    db.execute(respond_contact_access_types.insert().values(contact_id=contact.id, access_type_code=code))
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 403, resp.text
    assert "customer_not_permitted" in resp.text


@pytest.mark.parametrize(
    "types",
    [
        pytest.param([], id="no-access-type"),
        pytest.param([("End User", True)], id="end-user"),
        pytest.param([("ZZT Sorento Warehouse", True)], id="unknown-type"),
        pytest.param([("Sorento Dealer", True)], id="dealer-type-no-link"),
        pytest.param([("Sorento Office", False)], id="inactive-office"),
        pytest.param([("Sorento Office", False), ("Sorento Dealer", True)], id="inactive-office-plus-dealer"),
        pytest.param([("Office", True)], id="brandless-office-name"),
    ],
)
def test_granted_contact_that_is_neither_staff_nor_linked_is_refused(client, db, types):
    """S2, fail closed: holding the reveal key is not enough to see the whole book.
    A contact with no customer link that is not positively staff is refused with
    the same 403 a dealer gets, and no figures."""
    a = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT MATRIX A")
    p = _product(db, "ZZTMATRIX-A")
    _line(db, product_id=p.id, ordered=5, delivered=5, customer_id=a.id)
    contact = _contact(db)
    for name, active in types:
        _access(db, contact, name, active=active)
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 403, (types, resp.text)
    assert "customer_not_permitted" in resp.text
    assert "ZZTMATRIX" not in resp.text and "MATRIX A" not in resp.text


@pytest.mark.parametrize("office", ["Sorento Office", "Mocha Office", "cabana  office"])
def test_office_contact_is_staff(client, db, office):
    """S2: every brand's office tier is staff and sees every customer."""
    a = customer(db, company_id=DEFAULT_COMPANY_ID)
    b = customer(db, company_id=DEFAULT_COMPANY_ID)
    pa = _product(db, "ZZTOFFICE-A")
    pb = _product(db, "ZZTOFFICE-B")
    _line(db, product_id=pa.id, ordered=1, delivered=1, customer_id=a.id)
    _line(db, product_id=pb.id, ordered=2, delivered=2, customer_id=b.id)
    contact = _contact(db)
    _access(db, contact, office)
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 200, resp.text
    assert _codes(resp.json()) == ["ZZTOFFICE-B", "ZZTOFFICE-A"]
    assert resp.json()["filters"]["dealer_scoped"] is False


#: The owner's hand-test contact Mr Loo (PR #1273, 27 Sep 2026): every office
#: tier, every dealer tier and End User at once.
MIXED_STAFF_TYPES = [
    "Sorento Office", "Mocha Office", "Cabana Office",
    "Sorento Dealer", "Mocha Dealer", "Cabana Dealer", "End User",
]


def test_active_office_type_is_staff_whatever_else_it_holds(client, db):
    """Fix round 3 (owner hand test, 27 Sep 2026): any ACTIVE office access type
    makes the contact staff for this report, whatever dealer or end user types or
    customer links it also holds. Staff sees every ledger and may name any."""
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT MIXED OWN")
    rival = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT MIXED RIVAL")
    po = _product(db, "ZZTMIXED-OWN")
    pr = _product(db, "ZZTMIXED-RIVAL")
    _line(db, product_id=po.id, ordered=1, delivered=1, customer_id=own.id)
    _line(db, product_id=pr.id, ordered=9, delivered=9, customer_id=rival.id)
    contact = _contact(db)
    for name in MIXED_STAFF_TYPES:
        _access(db, contact, name)
    _link(db, contact, own)
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 200, resp.text
    assert _codes(resp.json()) == ["ZZTMIXED-RIVAL", "ZZTMIXED-OWN"]
    assert resp.json()["filters"]["dealer_scoped"] is False
    named = _get(client, rank_by="quantity", customer_ids=rival.id, **_as_contact(contact))
    assert named.status_code == 200, named.text
    assert _codes(named.json()) == ["ZZTMIXED-RIVAL"]


def test_active_office_type_is_the_scope_function_staff(db):
    """The rule itself: `_top_selling_dealer_scope` answers None (staff) for the
    mixed contact, linked or not."""
    from app.api.v1.order_management.orders import _top_selling_dealer_scope

    own = customer(db, company_id=DEFAULT_COMPANY_ID)
    contact = _contact(db)
    for name in MIXED_STAFF_TYPES:
        _access(db, contact, name)
    assert _top_selling_dealer_scope(db, contact.id) is None
    _link(db, contact, own)
    assert _top_selling_dealer_scope(db, contact.id) is None


@pytest.mark.parametrize(
    "types",
    [
        pytest.param(["Sorento Dealer", "Mocha Dealer", "End User"], id="dealer-types"),
        pytest.param([], id="no-type"),
    ],
)
def test_linked_contact_without_an_active_office_type_sees_only_its_ledgers(client, db, types):
    """No active office type keeps today's rule: a customer link forces the
    contact to its own ledgers, and naming another is refused."""
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT LINKED OWN")
    rival = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT LINKED RIVAL")
    po = _product(db, "ZZTLINKED-OWN")
    pr = _product(db, "ZZTLINKED-RIVAL")
    _line(db, product_id=po.id, ordered=1, delivered=1, customer_id=own.id)
    _line(db, product_id=pr.id, ordered=9, delivered=9, customer_id=rival.id)
    contact = _contact(db)
    for name in types:
        _access(db, contact, name)
    _access(db, contact, "Cabana Office", active=False)
    _link(db, contact, own)
    db.commit()

    body = _get(client, rank_by="quantity", **_as_contact(contact)).json()
    assert _codes(body) == ["ZZTLINKED-OWN"]
    assert body["filters"]["dealer_scoped"] is True
    refused = _get(client, rank_by="quantity", customer_ids=rival.id, **_as_contact(contact))
    assert refused.status_code == 403, refused.text
    assert "customer_not_permitted" in refused.text


def test_contact_with_no_access_type_and_no_link_is_refused(client, db):
    """No type and no link: the 403, no figures (the reveal alone is not enough)."""
    p = _product(db, "ZZTNOTYPE-A")
    _line(db, product_id=p.id, ordered=1, delivered=1)
    contact = _contact(db)
    db.commit()

    resp = _get(client, rank_by="quantity", **_as_contact(contact))
    assert resp.status_code == 403, resp.text
    assert "customer_not_permitted" in resp.text
    assert "ZZTNOTYPE" not in resp.text


def test_sales_report_serves_the_mixed_staff_contact(client, db):
    """The sales report agrees: its only contact gate is the Sales report reveal, so
    the same mixed contact is answered there too, never refused as a dealer."""
    own = customer(db, company_id=DEFAULT_COMPANY_ID)
    p = _product(db, "ZZTSRMIXED-A")
    _line(db, product_id=p.id, ordered=1, delivered=1, customer_id=own.id)
    contact = _contact(db)
    for name in MIXED_STAFF_TYPES:
        _access(db, contact, name)
    _link(db, contact, own)
    db.commit()

    resp = client.get(
        "/api/v1/order-management/sales-report",
        params={"product_code": "ZZTSRMIXED-A", **Y2026, **_as_contact(contact)},
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------- auth


def test_auth_401_403_apikey_and_company_scope(db, monkeypatch):
    """AC-1929: no credential 401; no permission 403; API key + act-as 200; a
    contact-scoped key never sees another company's lines."""
    from app.config import settings

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    p = _product(db)
    _line(db, product_id=p.id, ordered=1, delivered=1)

    no_grant_user = User(
        id=str(uuid.uuid4()), email=f"{unique_code('NG')}@test.com", name="ZZT No Grant", status="ACTIVE"
    )
    db.add(no_grant_user)
    db.flush()
    permission = UserPermission(id=str(uuid.uuid4()), slug=PERMISSION_SLUG, name=PERMISSION_SLUG)
    role = UserRole(id=str(uuid.uuid4()), slug="zzt_top_selling_view_role", name="ZZT Top Selling View")
    granted_user = User(
        id=str(uuid.uuid4()), email=f"{unique_code('OK')}@test.com", name="ZZT Granted", status="ACTIVE"
    )
    db.add_all([permission, role, granted_user])
    db.flush()
    db.add(UserRoleAssignment(user_id=granted_user.id, role_id=role.id))
    db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()
    integration = Integration(
        id=str(uuid.uuid4()), name="zzt-top-selling-key", type="automation",
        act_as_user_id=granted_user.id, is_active=True,
    )
    db.add(integration)
    db.flush()
    plaintext_key = IntegrationKeyService(db).issue_key(integration)
    db.commit()

    params = {"rank_by": "quantity", **Y2026}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        assert TestClient(app).get(BASE, params=params).status_code == 401

        app.dependency_overrides[get_current_user] = lambda: {"id": no_grant_user.id, "email": no_grant_user.email}
        app.dependency_overrides[get_current_user_or_api_key] = lambda: {
            "id": no_grant_user.id, "email": no_grant_user.email
        }
        assert TestClient(app).get(BASE, params=params).status_code == 403

        del app.dependency_overrides[get_current_user_or_api_key]
        del app.dependency_overrides[get_current_user]
        allowed = TestClient(app).get(BASE, params=params, headers={"X-API-Key": plaintext_key})
        assert allowed.status_code == 200, allowed.text
    finally:
        app.dependency_overrides.clear()

    mocha = seed_mocha(db)
    shared_code = unique_code("SKU2")
    prod_a = product(db, company_id=DEFAULT_COMPANY_ID, code=shared_code)
    prod_b = product(db, company_id=mocha.id, code=shared_code)
    _line(db, product_id=prod_a.id, ordered=10, delivered=10)
    so_b = SalesOrder(
        id=str(uuid.uuid4()), so_number=unique_code("SO"), status="open", company_id=mocha.id,
        order_date=date(2026, 6, 1),
    )
    db.add(so_b)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=so_b.id, product_id=prod_b.id,
            qty_ordered=500, qty_delivered=500, line_total=Decimal("5000.00"), line_status="open",
            company_id=mocha.id,
        )
    )
    contact = _contact(db)
    _access(db, contact, "Sorento Office")
    superadmin = _seed_superadmin(db)
    scope_integration = Integration(
        id=str(uuid.uuid4()), name="zzt-top-selling-scope-key", type="automation",
        act_as_user_id=superadmin["id"], is_active=True,
    )
    db.add(scope_integration)
    db.flush()
    scope_key = IntegrationKeyService(db).issue_key(scope_integration)
    db.commit()
    monkeypatch.setattr(settings, "external_api_key", scope_key)

    app.dependency_overrides[get_db] = _override_db
    try:
        resp = TestClient(app).get(
            BASE, params={**params, **_as_contact(contact)}, headers={"X-API-Key": scope_key},
        )
        assert resp.status_code == 200, resp.text
        rows = [r for r in resp.json()["rows"] if r["code"] == shared_code]
        assert len(rows) == 1 and rows[0]["quantity"] == 10, resp.text
    finally:
        app.dependency_overrides.clear()


def test_top_selling_is_not_enabled_for_the_in_app_assistant():
    """Security B1 of the sales report, same money, same reason."""
    from pathlib import Path

    import app.main as main_mod

    assert "top_selling" not in Path(main_mod.__file__).read_text()


# --------------------------------------------------------------------- fix lane round 4
# Owner retest of top selling, 27 Sep 2026 (PR #1273): narrowing by brand (F6) and the
# least sold ranking (F7).


def _brand(db, name: str, code: str):
    from app.models.product import Brand

    row = Brand(id=str(uuid.uuid4()), brand_code=code, brand_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def test_brand_filter_narrows_and_echoes_the_brand(client, db):
    """F6: `brand_ids` keeps products of those brands only (`Product.brand_id`, the way
    PR #1301 narrows the outstanding report) and echoes the brand name."""
    sorento = _brand(db, "Sorento", unique_code("SRT")[:20])
    cabana = _brand(db, "Cabana", unique_code("CBN")[:20])
    a = _product(db, "ZZTBRAND-A")
    b = _product(db, "ZZTBRAND-B")
    a.brand_id = sorento.id
    b.brand_id = cabana.id
    _line(db, product_id=a.id, ordered=5, delivered=5, line_total=Decimal("50.00"))
    _line(db, product_id=b.id, ordered=9, delivered=9, line_total=Decimal("90.00"))
    db.commit()

    body = _get(client, rank_by="quantity", brand_ids=sorento.id).json()
    assert _codes(body) == ["ZZTBRAND-A"]
    assert body["filters"]["brand_name"] == "Sorento"
    assert _get(client, rank_by="quantity").json()["filters"]["brand_name"] is None


def test_brand_ids_must_be_uuids(client, db):
    assert _get(client, rank_by="quantity", brand_ids="sorento").status_code == 400


def test_direction_bottom_ranks_ascending(client, db):
    """F7: `direction=bottom` ranks the least sold first (the metric ascending, ties by
    the other metric ascending, then code). Items with no sale in the window are not
    ranked on either direction (the route ranks sales), and the body says which way
    it ranked."""
    a = _product(db, "ZZTDIR-A")
    b = _product(db, "ZZTDIR-B")
    c = _product(db, "ZZTDIR-C")
    _product(db, "ZZTDIR-NOSALE")
    _line(db, product_id=a.id, ordered=10, delivered=10, line_total=Decimal("100.00"))
    _line(db, product_id=b.id, ordered=2, delivered=2, line_total=Decimal("20.00"))
    _line(db, product_id=c.id, ordered=5, delivered=5, line_total=Decimal("50.00"))
    db.commit()

    top = _get(client, rank_by="quantity").json()
    assert _codes(top) == ["ZZTDIR-A", "ZZTDIR-C", "ZZTDIR-B"]
    assert top["direction"] == "top"
    bottom = _get(client, rank_by="quantity", direction="bottom", n=2).json()
    assert _codes(bottom) == ["ZZTDIR-B", "ZZTDIR-C"]
    assert bottom["direction"] == "bottom"
    assert bottom["total_count"] == 3
    assert _get(client, rank_by="quantity", direction="sideways").status_code == 422
