"""Phase 2 RED tests - REPORTS-STEP2 S3a: the report failure shapes that live below the lane
(AC-28 same-code products in two companies, AC-31 not-supported copy, AC-32 the typed code resolves to one
product). The lane halves are in `tests/chatbot/test_reports_step2_s3a_lane.py`.

Postgres only, every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import company_scope, set_company_scope
from app.models.order import Order, OrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from tests._mc_lookup_seed import MOCHA_ID, customer, product, seed_mocha, warehouse
from tests._pg_fixture import blank_session, unique_code
from tests.test_top_selling_report import _seed_superadmin

OUTSTANDING = "/api/v1/order-management/outstanding-report"
SALES_REPORT = "/api/v1/order-management/sales-report"
TWIN_CODE = "ZZTMKS9451-GM"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        yield s


@pytest.fixture
def two_company_client(db):
    """A caller whose scope holds BOTH companies (the contact belongs to Sorento and Mocha)."""
    principal = _seed_superadmin(db)

    async def _scope():
        scope = frozenset({DEFAULT_COMPANY_ID, MOCHA_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _twin_dos(db):
    """The same product code in two companies; one pending DO line on EACH twin (7 and 5)."""
    seed_mocha(db)
    twins = {}
    for company_id, qty in ((DEFAULT_COMPANY_ID, 7), (MOCHA_ID, 5)):
        prod = product(db, company_id=company_id, code=TWIN_CODE)
        cust = customer(db, company_id=company_id, name=unique_code("Cust"))
        wh = warehouse(db, company_id=company_id, code=unique_code("WH"))
        order = Order(id=str(uuid.uuid4()), order_number=unique_code("DO"), customer_id=cust.id,
                      order_date=date(2026, 9, 10), company_id=company_id)
        db.add(order)
        db.flush()
        db.add(OrderLine(id=str(uuid.uuid4()), order_id=order.id, product_id=prod.id, warehouse_id=wh.id,
                         quantity=qty, total=Decimal(qty * 10), company_id=company_id))
        twins[company_id] = prod
    db.commit()
    return twins


def test_ac28_outstanding_counts_the_lines_of_every_same_code_product(two_company_client, db):
    _twin_dos(db)
    resp = two_company_client.get(OUTSTANDING, params={"product_code": TWIN_CODE, "scope": "do"})
    assert resp.status_code == 200, resp.text
    block = resp.json()["do"]
    assert block["do_count"] == 2 and block["pending_qty"] == 12, block


def test_ac28_the_delivered_sales_report_counts_both_twins(two_company_client, db):
    _twin_dos(db)
    resp = two_company_client.get(SALES_REPORT, params={"product_code": TWIN_CODE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"]["qty"] == 12, resp.json()["total"]


# ------------------------------------------------------------------ AC-32 resolver


def test_ac32_a_typed_code_with_a_slash_resolves_to_the_one_product_it_names(db):
    from app.services import entity_resolver as er

    for code in ("CB88SS-DIY", "CB88SS-DIY-X", "CB88SS", "ACC-SCREW", "BT015-SS-CR", "CB1536SS-CR", "CB88-SS-CR"):
        product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.flush()
    with company_scope(db, frozenset({DEFAULT_COMPANY_ID})):
        result = er.resolve_references(db, ["Cb88ss/diy"])
    token = result.resolutions[0]
    assert [m.canonical_code for m in token.matches] == ["CB88SS-DIY"], (
        [m.canonical_code for m in token.matches], [a.canonical_code for a in token.alternatives])
    assert not token.ambiguous


def test_ac32_scr_is_the_customer_not_a_family_of_products(db):
    """"scr" matches no product code by exact or prefix, only CONTAINS-after-separators candidates
    (CB1536SS-CR, ACC-SCREW, BT015-SS-CR), and whole-word matches one customer: the data decides it is the
    customer. It is never expanded into those products."""
    from app.services import entity_resolver as er

    for code in ("CB88SS-DIY", "CB1536SS-CR", "ACC-SCREW", "BT015-SS-CR", "CB88-SS-CR"):
        product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    wanted = customer(db, company_id=DEFAULT_COMPANY_ID, name="SCR MARKETING (M) SDN BHD")
    customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT UNRELATED TRADING SDN BHD")
    db.flush()
    with company_scope(db, frozenset({DEFAULT_COMPANY_ID})):
        result = er.resolve_references(db, ["Cb88ss/diy", "scr"])
    by_token = {r.token: r for r in result.resolutions}
    assert [m.canonical_code for m in by_token["Cb88ss/diy"].matches] == ["CB88SS-DIY"]
    scr = by_token["scr"].matches
    assert [(m.entity_type, m.uuid) for m in scr] == [("customer", wanted.id)], [
        (m.entity_type, m.canonical_code) for m in scr]


# ------------------------------------------------------------------ AC-31 copy

L1 = "Overdue and payment balances are not available in this chat. Please check with your salesman or our accounts team."
L1_ZH = "此聊天无法查询逾期款项和付款余额。请向您的业务员或我们的账务部查询。"
L1_MS = "Baki tertunggak dan bayaran tidak dapat disemak dalam chat ini. Sila semak dengan jurujual anda atau pasukan akaun kami."
L2 = "I can't look up nearby dealers in this chat. Please ask your salesman for the dealer nearest you."
L2_ZH = "此聊天无法查询附近的经销商。请向您的业务员询问离您最近的经销商。"
L2_MS = "Saya tidak dapat mencari pengedar berdekatan dalam chat ini. Sila tanya jurujual anda tentang pengedar yang paling dekat."

#: Pinned by the tester: the copy keys are `not_supported_receivables` and `not_supported_dealer_locator`
#: (short names in `chatbot/copy.py` FALLBACKS + REPLY_KEYS); the zh / ms defaults are the `.zh` / `.ms`
#: keys `CannedCopy.render_in` reads.
COPY = {
    "not_supported_receivables": (L1, L1_ZH, L1_MS),
    "not_supported_dealer_locator": (L2, L2_ZH, L2_MS),
}


@pytest.mark.parametrize("key", sorted(COPY))
def test_ac31_the_not_supported_lines_exist_in_en_zh_and_ms(key):
    from app.services.chatbot import copy

    en, zh, ms = COPY[key]
    fallback = copy.fallback_copy()
    assert fallback.render(key) == en
    assert fallback.render_in(key, "zh") == zh
    assert fallback.render_in(key, "ms") == ms
    assert key in copy.REPLY_KEYS, "the line must be an editable registry key"
