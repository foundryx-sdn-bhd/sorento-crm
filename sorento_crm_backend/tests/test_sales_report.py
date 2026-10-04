"""Route tests for `GET /api/v1/order-management/sales-report` that SURVIVE the delivered-basis
rewrite (lane SALES-REPORT, PR #1401, `documentation/plans/chatbot/selfref-scope-acceptance-criteria.md`
"Sales report v4").

The route's body is now the delivered report by DO date (`total`, `periods`, `rows`, `options`,
see `tests/test_sales_report_delivered.py`, which owns the new contract). What stays here is
the behaviour that did not change: the subject rule, the prefix / length / LIKE rules on
`product_code` and `customer_query`, the contact gate and identity pair, the customer echo,
the in-app assistant exclusion, auth and company scope. Tests that pinned the retired SO
basis (ordered / confirmed / outstanding months, `by_product`, `so_rows`, `detail=so`) are
gone; the reasons are in the lane report.

Postgres only (`tests/_pg_fixture.py`), every row seeded here; CI's database has none.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.integration import Integration
from app.models.order import SalesOrder, SalesOrderLine
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
from app.services.outstanding_report_service import _customer_echo

from tests._mc_lookup_seed import customer, product, seed_mocha, warehouse
from tests._sales_report_do_seed import line, seed_customer, seed_do
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/sales-report"
PERMISSION_SLUG = "order_management.orders.view"


def _money(v) -> Decimal:
    """Money-agnostic read: the coder may serialize a money figure as a JSON
    number or as a string - both parse cleanly through str(), a raw float does
    not lose precision doing so for the small numbers this file seeds."""
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _delivered_world(db):
    """One account, one product, one warehouse (the delivered basis reads DOs)."""
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    return cust, prod, wh


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _seed_superadmin(db) -> dict:
    """A principal that clears `require_permission_with_api_key` via role bypass,
    so every test EXCEPT the permission-focused AC-1632 can ignore RBAC plumbing."""
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


def _so_line(
    db,
    *,
    product_id,
    ordered,
    delivered,
    line_total=None,
    customer_id=None,
    warehouse_id=None,
    line_status="open",
    header_status="open",
    demand_class=None,
    order_date=None,
    required_date=None,
    so_number=None,
):
    """One SO with ONE line - the common case."""
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=so_number or unique_code("SO"),
        customer_id=customer_id,
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
            warehouse_id=warehouse_id,
            qty_ordered=ordered,
            qty_delivered=delivered,
            line_total=line_total,
            line_status=line_status,
            required_date=required_date,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    return so












def test_date_window_is_on_the_do_date(client, db):
    """AC-SR-20: the window filters on the DO's own `order_date`. A 2026-07 window keeps only
    the July DO. A month-only `2026-07` input for both bounds gives the identical result."""
    cust, prod, wh = _delivered_world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 20),
            lines=[line(prod.id, wh.id, 3, price=Decimal("10"), total=Decimal("30.00"))])
    seed_do(db, customer_id=cust.id, order_date=date(2026, 7, 10),
            lines=[line(prod.id, wh.id, 7, price=Decimal("10"), total=Decimal("70.00"))])
    db.commit()

    resp = client.get(
        BASE, params={"customer_ids": cust.id, "date_from": "2026-07-01", "date_to": "2026-07-31"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"]["qty"] == 7, resp.json()

    month_only = client.get(
        BASE, params={"customer_ids": cust.id, "date_from": "2026-07", "date_to": "2026-07"}
    )
    assert month_only.status_code == 200, month_only.text
    assert month_only.json()["total"]["qty"] == 7, month_only.json()


def test_bad_date_422(client, db):
    """An unrecognised `date_from` is 422 (`_parse_flex_date`'s own error). With no dates at
    all, every delivery is counted."""
    cust, prod, wh = _delivered_world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 1),
            lines=[line(prod.id, wh.id, 1, price=Decimal("10"), total=Decimal("10.00"))])
    db.commit()

    bad = client.get(BASE, params={"customer_ids": cust.id, "date_from": "notadate"})
    assert bad.status_code == 422, bad.text

    unfiltered = client.get(BASE, params={"customer_ids": cust.id})
    assert unfiltered.status_code == 200, unfiltered.text
    assert len(unfiltered.json()["periods"]) == 1, unfiltered.json()




def test_an_unknown_channel_value_is_422(client, db):
    """Any `channel` other than dealer / project is 422. (The channel FILTER itself, now read
    off the account's market segment, is covered in `test_sales_report_delivered.py`.)"""
    cust, _prod, _wh = _delivered_world(db)
    db.commit()

    bad = client.get(BASE, params={"customer_ids": cust.id, "channel": "wholesale"})
    assert bad.status_code == 422, bad.text
    ok = client.get(BASE, params={"customer_ids": cust.id, "channel": "project"})
    assert ok.status_code == 200, ok.text


# --------------------------------------------------------------------- AC-1626


def test_subject_required(client, db):
    """Neither `product_code` nor `customer_ids` nor `customer_query` is 422
    `subject_required`. Product only, customer only and both each return 200."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=unique_code("Cust"))
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0, line_total=Decimal("10.00"),
        customer_id=cust.id, order_date=date(2026, 6, 1),
    )
    db.commit()

    neither = client.get(BASE, params={})
    assert neither.status_code == 422, neither.text
    assert "subject_required" in neither.text, neither.text

    product_only = client.get(BASE, params={"product_code": prod.product_code})
    assert product_only.status_code == 200, product_only.text

    customer_only = client.get(BASE, params={"customer_ids": cust.id})
    assert customer_only.status_code == 200, customer_only.text

    both = client.get(BASE, params={"product_code": prod.product_code, "customer_ids": cust.id})
    assert both.status_code == 200, both.text


def test_product_prefix_and_warehouse_filter(client, db):
    """`product_code=zzt-abc1` (lowercase) matches `ZZT-ABC1`, `ZZT-ABC10` and `ZZT-ABC1-N`
    case-insensitively (S19's prefix rule) but NEVER `ZZT-XABC1`. `product_codes` echoes every
    code the prefix COVERS, sorted ascending, whether or not it was delivered. `warehouse_codes`
    still filters lines to those exact codes, and never narrows the echoed family. Same
    behaviour as before the delivered basis, seeded as DOs now."""
    w1_code = "ZZT-SR-W1"
    cust = seed_customer(db, name=unique_code("Cust"))
    abc1 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABC1")
    abc10 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABC10")
    abc1n = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABC1-N")
    xabc1 = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-XABC1")
    product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABC1-NOSALE")
    wh1 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=w1_code)
    wh2 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SR-W2")
    for prod, wh, qty in (
        (abc1, wh1, 10), (abc1, wh2, 20), (abc10, wh1, 5), (abc1n, wh1, 7), (xabc1, wh1, 99),
    ):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 1),
                lines=[line(prod.id, wh.id, qty, price=Decimal("10"), total=Decimal(qty * 10))])
    db.commit()

    family = ["ZZT-ABC1", "ZZT-ABC1-N", "ZZT-ABC1-NOSALE", "ZZT-ABC10"]
    unfiltered = client.get(BASE, params={"product_code": "zzt-abc1"})
    assert unfiltered.status_code == 200, unfiltered.text
    ubody = unfiltered.json()
    assert ubody["product_code"] == "ZZT-ABC1", ubody
    assert ubody["product_codes"] == family, ubody
    assert ubody["total"]["qty"] == 42, ubody  # 10 + 20 + 5 + 7, XABC1 (99) excluded

    filtered = client.get(BASE, params={"product_code": "zzt-abc1", "warehouse_codes": w1_code})
    assert filtered.status_code == 200, filtered.text
    fbody = filtered.json()
    assert fbody["total"]["qty"] == 22, fbody  # 10 + 5 + 7
    assert fbody["product_codes"] == family, fbody


def test_product_code_needs_three_characters(client, db):
    """A stripped `product_code` shorter than 3 characters is 422
    `product_code_too_short` - a 1-2 character prefix would LIKE-scan the
    whole product master, company-wide (SEC-B2's own reasoning for
    `customer_query_too_short`, reused here)."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=unique_code("Cust"))

    too_short = client.get(BASE, params={"product_code": " ab "})
    assert too_short.status_code == 422, too_short.text
    assert "product_code_too_short" in too_short.text, too_short.text

    # A 2-char product_code alongside a customer subject is STILL rejected -
    # the check applies whenever product_code is given, not only when it is
    # the sole subject.
    with_customer = client.get(BASE, params={"product_code": "ab", "customer_ids": cust.id})
    assert with_customer.status_code == 422, with_customer.text
    assert "product_code_too_short" in with_customer.text, with_customer.text


def test_like_metacharacters_in_product_code_are_literal(client, db):
    """`%` and `_` typed in `product_code` are LITERAL characters, never SQL
    LIKE wildcards - `AB%` must not match `ABCD` (which it would if `%` were
    left unescaped) and `AB_` must not match `ABC` (ditto for `_`, which
    matches any single character unescaped)."""
    percent_literal = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-AB%LIT")
    percent_victim = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABCD")
    underscore_literal = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-AB_LIT")
    underscore_victim = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-ABC")
    for p in (percent_literal, percent_victim, underscore_literal, underscore_victim):
        _so_line(
            db, product_id=p.id, ordered=1, delivered=0, line_total=Decimal("10.00"),
            order_date=date(2026, 6, 1),
        )
    db.commit()

    percent_resp = client.get(BASE, params={"product_code": "ZZT-AB%"})
    assert percent_resp.status_code == 200, percent_resp.text
    assert percent_resp.json()["product_codes"] == ["ZZT-AB%LIT"], percent_resp.json()

    underscore_resp = client.get(BASE, params={"product_code": "ZZT-AB_"})
    assert underscore_resp.status_code == 200, underscore_resp.text
    assert underscore_resp.json()["product_codes"] == ["ZZT-AB_LIT"], underscore_resp.json()


def test_unknown_prefix_404(client, db):
    """No product code starts with the typed prefix - 404, the same shape the
    exact-match rule raised before S19."""
    resp = client.get(BASE, params={"product_code": "ZZT-NOSUCHPREFIX"})
    assert resp.status_code == 404, resp.text












def test_a_real_delivered_body_renders_through_the_real_presenter(client, db):
    """R-B2's end-to-end seam, kept for the delivered basis: a REAL route body fed through
    `sorento_crm_mcp.presenters.present_response("crm_sales_report", ...)` renders the v4 reply
    (`*Total:*`, a period line, `*Drill down:*`) and an envelope whose `options` the lane arms
    the open question from. Needs the real HTTP route, which only the backend TestClient can call."""
    import json as _json
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.presenters import present_response
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")

    cust = seed_customer(db, name="ZZT SR Presenter Customer")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 1),
            lines=[line(prod.id, wh.id, 4, price=Decimal("25"), total=Decimal("100.00"))])
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id})
    assert resp.status_code == 200, resp.text
    envelope = _json.loads(present_response("crm_sales_report", resp.text))
    text = envelope.get("response") or ""
    assert "*Customer:* ZZT SR Presenter Customer" in text, text
    assert "*Total:* Qty 4, RM 100.00" in text, text
    assert "Jun 2026: Qty 4, RM 100.00" in text, text
    assert "*Drill down:*" in text, text
    assert envelope["has_result"] is True, envelope
    assert [(o["idx"], o["value"]) for o in envelope["options"]] == [(1, "delivery_order")], envelope
    assert "*SO Number:*" not in text and "*_By product_*" not in text, text


# --------------------------------------------------------------------- AC-1630


def test_customer_echo_shared(client, db):
    """The header echo `customer_name` is built by the outstanding report's own
    `_customer_echo` (distinct ledger names, first-seen order) - imported, not
    reimplemented."""
    c1 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT SR Echo One")
    c2 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT SR Echo Two")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0, line_total=Decimal("10.00"),
        customer_id=c1.id, order_date=date(2026, 6, 1),
    )
    db.commit()

    ids = f"{c1.id},{c2.id}"
    resp = client.get(BASE, params={"customer_ids": ids})
    assert resp.status_code == 200, resp.text
    expected = _customer_echo(db, None, [c1.id, c2.id])
    assert expected == "ZZT SR Echo One, ZZT SR Echo Two", expected
    assert resp.json()["customer_name"] == expected, resp.json()








def test_a_do_with_no_order_date_is_excluded_everywhere(client, db):
    """A DO with no `order_date` has no day, week or month to file under, so it is in no
    period AND not in the Total (a Total that included it would disagree with the periods)."""
    cust, prod, wh = _delivered_world(db)
    seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 1),
            lines=[line(prod.id, wh.id, 4, price=Decimal("10"), total=Decimal("40.00"))])
    seed_do(db, customer_id=cust.id, order_date=None,
            lines=[line(prod.id, wh.id, 900, price=Decimal("10"), total=Decimal("9000.00"))])
    db.commit()

    body = client.get(BASE, params={"customer_ids": cust.id}).json()
    assert body["total"]["qty"] == 4, body
    assert sum(p["qty"] for p in body["periods"]) == 4, body["periods"]


# --------------------------------------------------------------------- SEC-B2
# security review, Phase 3 fix round (captain ruling S17): `customer_query`
# needs at least 3 characters (after strip) - the route today only checks it
# is non-empty (`subject_required`), so a single letter ILIKE scans and returns
# every customer whose name contains it, company-wide.


@pytest.mark.parametrize("query", ["a", "ab", " a "])
def test_customer_query_needs_three_characters(client, db, query):
    """A WHOLLY BLANK query ("  ") is deliberately excluded from this
    parametrization: it strips to "" and already 422s as `subject_required`
    (no subject named at all) - a pre-existing, unrelated check this test must
    not conflate with the NEW minimum-length rule."""
    resp = client.get(BASE, params={"customer_query": query})
    assert resp.status_code == 422, (query, resp.text)


def test_customer_query_of_three_characters_is_accepted(client, db):
    resp = client.get(BASE, params={"customer_query": "abc"})
    assert resp.status_code == 200, resp.text




# --------------------------------------------------------------------- SEC-S1
# security review, Phase 3 fix round (captain ruling S14): the route re-checks
# the per-contact `sales_orders.sales_report` reveal key WHEN a contact_id is
# present - unlike the outstanding-report route, which only ever has the LANE
# withhold scope (`so_refused`), this route has no such caller today at all:
# an n8n workflow or a console session calling the MCP tool directly with a
# contact's own contact_id/space_id bypasses the chatbot lane's own gate
# entirely (`lanes/business/__init__.py`'s `_SALES_REPORT_GRANT` check), so the
# only gate that ever runs is the route's - and today there is none.


def test_contact_without_the_key_is_refused_by_the_route(client, db):
    from app.services.contact_field_reveal_service import set_granted_keys

    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0, line_total=Decimal("10.00"),
        order_date=date(2026, 6, 1),
    )
    contact = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6{unique_code('PH')[:10]}")
    db.add(contact)
    db.flush()
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.commit()

    refused = client.get(
        BASE,
        params={"product_code": prod.product_code, "contact_id": contact.id, "space_id": "zzt-space"},
    )
    assert refused.status_code == 403, refused.text
    assert "sales_report_not_enabled" in refused.text, refused.text

    set_granted_keys(db, contact.id, ["sales_orders.sales_report"], actor_id=None)
    db.commit()

    granted = client.get(
        BASE,
        params={"product_code": prod.product_code, "contact_id": contact.id, "space_id": "zzt-space"},
    )
    assert granted.status_code == 200, granted.text

    plain = client.get(BASE, params={"product_code": prod.product_code})
    assert plain.status_code == 200, plain.text


# --------------------------------------------------------------------- SEC-S2
# security review, Phase 3 fix round (captain ruling S14): contact_id and
# space_id are both-or-neither on THIS route - one without the other is 422,
# never silently treated as "no contact at all" (which would skip the SEC-S1
# gate above entirely) or as "no space at all" (which the generic company-scope
# resolver already treats as neither given, per its own `if not contact_id or
# not space_id: return` - the SAME silent-skip this route's own reveal gate
# must not inherit).


@pytest.mark.parametrize(
    "params",
    [
        {"contact_id": "11111111-1111-1111-1111-111111111111"},
        {"space_id": "zzt-space"},
    ],
)
def test_contact_identity_is_both_or_neither(client, db, params):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=1, delivered=0, line_total=Decimal("10.00"))
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, **params})
    assert resp.status_code == 422, (params, resp.text)


# --------------------------------------------------------------------- SEC-B1
# security review, Phase 3 fix round (captain ruling): AC-1642 struck - the
# in-app AI assistant must NEVER carry `crm_sales_report` on its enabled tools
# list, the same reason `crm_low_stock_report` is kept off it (N4, app/main.py,
# above): a staff member who is 403 on the ROUTE (no
# order_management.orders.view permission) could otherwise read the money
# figures through the in-app assistant instead, across every company - the
# assistant is a DIFFERENT auth boundary from the route's RBAC permission.


def test_sales_report_is_not_enabled_for_the_in_app_assistant():
    import importlib
    from pathlib import Path

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.sales_report_bootstrap")

    import app.main as main_mod

    source = Path(main_mod.__file__).read_text()
    assert "sales_report_bootstrap" not in source, (
        "app/main.py must not wire the struck bootstrap at all (AC-1642 STRUCK)"
    )


# --------------------------------------------------------------------- AC-1632


def test_auth_401_403_apikey_and_company_scope(db, monkeypatch):
    """No credential is 401; a user without `order_management.orders.view` is
    403; an X-API-Key principal with an act-as user who HOLDS the grant reaches
    the route (200). Separately, with the REAL `apply_company_scope` resolver
    running (not the `client` fixture's override), a contact-scoped API key
    sees only its OWN company's sales orders - mirrors
    test_outstanding_report.py's AC-1118 (`test_route_permission_and_api_key_act_as`)
    and B1 (`test_report_is_company_scoped_for_api_key_contact`)."""
    from app.config import settings

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=1, delivered=0, line_total=Decimal("10.00"))

    no_grant_user = User(
        id=str(uuid.uuid4()), email=f"{unique_code('NG')}@test.com", name="ZZT No Grant", status="ACTIVE"
    )
    db.add(no_grant_user)
    db.flush()

    permission = UserPermission(id=str(uuid.uuid4()), slug=PERMISSION_SLUG, name=PERMISSION_SLUG)
    role = UserRole(id=str(uuid.uuid4()), slug="zzt_sales_report_view_role", name="ZZT Sales Report View")
    granted_user = User(
        id=str(uuid.uuid4()), email=f"{unique_code('OK')}@test.com", name="ZZT Granted", status="ACTIVE"
    )
    db.add_all([permission, role, granted_user])
    db.flush()
    db.add(UserRoleAssignment(user_id=granted_user.id, role_id=role.id))
    db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()

    integration = Integration(
        id=str(uuid.uuid4()), name="zzt-sales-report-key", type="automation",
        act_as_user_id=granted_user.id, is_active=True,
    )
    db.add(integration)
    db.flush()
    plaintext_key = IntegrationKeyService(db).issue_key(integration)
    db.commit()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        # No credential at all.
        no_cred_client = TestClient(app)
        no_cred = no_cred_client.get(BASE, params={"product_code": prod.product_code})
        assert no_cred.status_code == 401, no_cred.text

        # A real principal without the permission.
        app.dependency_overrides[get_current_user] = lambda: {"id": no_grant_user.id, "email": no_grant_user.email}
        app.dependency_overrides[get_current_user_or_api_key] = lambda: {
            "id": no_grant_user.id, "email": no_grant_user.email
        }
        no_grant_client = TestClient(app)
        denied = no_grant_client.get(BASE, params={"product_code": prod.product_code})
        assert denied.status_code == 403, denied.text

        # X-API-Key + act-as: remove the override so the real key resolves.
        del app.dependency_overrides[get_current_user_or_api_key]
        del app.dependency_overrides[get_current_user]
        apikey_client = TestClient(app)
        allowed = apikey_client.get(
            BASE, params={"product_code": prod.product_code}, headers={"X-API-Key": plaintext_key}
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        app.dependency_overrides.clear()

    # Company scoping: seed a SECOND company with the SAME product code and an
    # SO of its own, a contact scoped to ONLY the first company, and assert the
    # scoped caller never sees the other company's SO. The REAL
    # apply_company_scope resolver runs here (not overridden).
    mocha = seed_mocha(db)
    shared_code = unique_code("SKU2")
    prod_a = product(db, company_id=DEFAULT_COMPANY_ID, code=shared_code)
    prod_b = product(db, company_id=mocha.id, code=shared_code)

    # R-S2 (Phase 3 fix round, captain ruling S16): the created_at fallback is
    # REMOVED, so a line bucketed by neither required_date nor order_date is
    # now excluded from every total - this seed must carry a real order_date
    # or `total_ordered == 10` below would silently see 0 instead. Assertions
    # unchanged; only this seed's own date is added.
    cust_a = seed_customer(db, name=unique_code("Cust"))
    cust_b = seed_customer(db, name=unique_code("MochaCust"), company_id=mocha.id)
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    wh_b = warehouse(db, company_id=mocha.id)
    seed_do(db, customer_id=cust_a.id, order_date=date(2026, 6, 1),
            lines=[line(prod_a.id, wh_a.id, 10, price=Decimal("10"), total=Decimal("100.00"))])
    seed_do(db, customer_id=cust_b.id, order_date=date(2026, 6, 1), company_id=mocha.id,
            lines=[line(prod_b.id, wh_b.id, 500, price=Decimal("10"), total=Decimal("5000.00"))])

    contact = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6{unique_code('PH')[:10]}")
    db.add(contact)
    db.flush()
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID,
        )
    )
    # SEED CHANGED BY THE CODER (SEC-S1/ruling S14, Phase 3 fix round): this test's own
    # contact now carries the per-contact `sales_orders.sales_report` reveal key, or the
    # route's new SEC-S1 gate would refuse this AC-1118/B1-style company-scope check with
    # its own 403 before company scoping is ever exercised - no assertion below changed.
    from app.services.contact_field_reveal_service import set_granted_keys

    set_granted_keys(db, contact.id, ["sales_orders.sales_report"], actor_id=None)

    superadmin = _seed_superadmin(db)
    scope_integration = Integration(
        id=str(uuid.uuid4()), name="zzt-sales-report-scope-key", type="automation",
        act_as_user_id=superadmin["id"], is_active=True,
    )
    db.add(scope_integration)
    db.flush()
    scope_key = IntegrationKeyService(db).issue_key(scope_integration)
    db.commit()
    monkeypatch.setattr(settings, "external_api_key", scope_key)

    app.dependency_overrides[get_db] = _override_db
    try:
        scoped_client = TestClient(app)
        resp = scoped_client.get(
            BASE,
            params={"product_code": shared_code, "contact_id": contact.id, "space_id": "zzt-space"},
            headers={"X-API-Key": scope_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        total_ordered = body["total"]["qty"]
        assert total_ordered == 10, (
            f"a Sorento-only contact must never see Mocha's SO lines: {body}"
        )
    finally:
        app.dependency_overrides.clear()
