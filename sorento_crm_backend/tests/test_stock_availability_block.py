"""Chatbot stock ask v2, S3 - the server availability block
(`chatbot-stock-ask-v2-24sep-acceptance-criteria.md` AC-SA301 to AC-SA318).

UAC `documentation/plans/chatbot/chatbot-stock-ask-v2-24sep-acceptance-criteria.md`. PLAN
`documentation/plans/chatbot/PLAN-chatbot-stock-ask-v2-24sep.md` "S3 - Four-branch verdict
and the R5 ETA read". Rulings R2 to R6, R10, R14.

This file used to pin PR #1118's own dealer-stock-verdict shape (`verdict` / `running_low`
/ `disclaimer` / `available`, an "incoming" read over `spo_allocations`, a purchase read
over `purchase_order_lines`, a low-stock threshold comparison). All of that machinery is
gone (AC-SA316, `app/services/stock_verdict.py` deleted) - `_apply_stock_visibility`'s
`availability` branch now answers with ONE of four fixed branches
(`app.services.stock_ask_branch.branch`, `too_big` / `in_stock` / `incoming` /
`no_incoming`) per `StockAvailabilityEntry` (`branch`, `cap_unset`, `category_name`, `eta`,
`packing_list`). What survives from the old suite: the location-scope guarantee (AC-SA302),
the open-SO subtraction (AC-SA303), the per-code multi-company merge structure, the asked-
order guarantee, the `needs_product` flag, and the `detailed`/`compact` byte-identity check
(AC-SA315) - all re-asserted against the new entry shape. AC-SA301 (the `branch()` truth
table) lives in `tests/test_stock_ask_branch.py` and AC-SA304 to AC-SA309 (the R5 read
itself) live in `tests/test_earliest_packing_list_shipment.py` - neither is duplicated here.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact, StockVisibilityPolicy
from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import InboundShipment, InboundShipmentLine
from app.models.product import ProductCategory
from app.models.resources import Attachment
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.inventory_service import StockService
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock, warehouse
from tests._pg_fixture import blank_session, unique_code

READ_PERM = "inventory.stock.view"
WRITE_PERM = "inventory.stock.edit"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    """A staff caller holding both stock permissions, on the SAME session the
    assertions read - copied from `tests/test_stock_visibility_policy.py`."""

    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-dsv-block@test.com"}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in {READ_PERM, WRITE_PERM},
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------- seeding


def _wh(db, code: str):
    return warehouse(db, company_id=DEFAULT_COMPANY_ID, code=code)


def _contact(db, *, packing_list_allowed: bool = False) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT DSV Contact",
        packing_list_allowed=packing_list_allowed,
    )
    db.add(row)
    db.flush()
    return row


def _policy_row(db, *, mode: str, warehouse_ids=None, contact=None) -> StockVisibilityPolicy:
    row = StockVisibilityPolicy(
        id=str(uuid.uuid4()),
        contact_id=contact.id if contact else None,
        mode=mode,
        warehouse_ids=warehouse_ids,
    )
    db.add(row)
    db.flush()
    return row


def _so_line(
    db,
    *,
    product_id,
    warehouse_id,
    ordered,
    delivered=0,
    status="open",
    company_id=DEFAULT_COMPANY_ID,
):
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=unique_code("SO")[:100],
        company_id=company_id,
    )
    db.add(so)
    db.flush()
    line = SalesOrderLine(
        id=str(uuid.uuid4()),
        sales_order_id=so.id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        qty_ordered=ordered,
        qty_delivered=delivered,
        line_status=status,
        company_id=company_id,
    )
    db.add(line)
    db.flush()
    return line


def _category_of(db, p) -> ProductCategory:
    return db.query(ProductCategory).filter(ProductCategory.id == p.category_id).one()


def _attachment(db) -> Attachment:
    """R5's packing list, seeded the same way `tests/test_earliest_packing_list_shipment.py`
    does it - `attachment_type_id` is nullable, unused here."""
    aid = str(uuid.uuid4())
    row = Attachment(
        id=aid,
        original_filename="packing-list.pdf",
        stored_filename=f"{aid}.pdf",
        file_path=f"/attachments/{aid}.pdf",
        mime_type="application/pdf",
    )
    db.add(row)
    db.flush()
    return row


def _incoming_shipment(
    db,
    *,
    eta,
    attachment_id=None,
    status="in_transit",
    company_id=DEFAULT_COMPANY_ID,
) -> InboundShipment:
    """A qualifying-or-not R5 candidate. `eta_delay_date` is seeded as a poison value
    (R5: never read) the same way `test_earliest_packing_list_shipment.py` does it."""
    row = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=unique_code("SHP")[:50],
        shipment_date=date(2026, 1, 1),
        estimated_arrival_date=eta,
        eta_delay_date=date(2099, 1, 1),
        attachment_id=attachment_id,
        shipment_status=status,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def _incoming_line(
    db,
    *,
    shipment_id,
    product_id,
    shipped=10,
    received=0,
    status="in_transit",
    company_id=DEFAULT_COMPANY_ID,
) -> InboundShipmentLine:
    row = InboundShipmentLine(
        id=str(uuid.uuid4()),
        shipment_id=shipment_id,
        product_id=product_id,
        quantity_shipped=shipped,
        quantity_received=received,
        line_status=status,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def _entry(result, product_id):
    for row in result["stock_availability"]:
        if row["product_id"] == product_id:
            return row
    raise AssertionError(f"no stock_availability entry for {product_id}")


#: Keys allowed to carry a number on an `availability` answer (AC-SA312: no digit of
#: ours anywhere else - `eta` is a formatted string, not a number).
_ALLOWED_NUMERIC_KEYS = {"requested_qty", "needs_quantity"}
_QUANTITY_WORDS = ("quantity", "on_hand", "qty")
#: AVAIL-MODE-REPLIES rule 2: `available_qty` is a declared key (None unless an `in_stock`
#: answer is short of Q within X); its VALUE is still checked against the forbidden numbers.
_ALLOWED_QTY_KEYS = {"available_qty"}


def _walk(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _assert_no_quantity_anywhere(body, forbidden_numbers):
    offending_keys = [
        path
        for path, key, _ in _walk(body)
        if key not in _ALLOWED_NUMERIC_KEYS | _ALLOWED_QTY_KEYS
        and any(word in key for word in _QUANTITY_WORDS)
    ]
    assert not offending_keys, f"quantity-shaped keys leaked: {offending_keys}"

    leaked_values = [
        path
        for path, key, value in _walk(body)
        if key not in _ALLOWED_NUMERIC_KEYS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value in forbidden_numbers
    ]
    assert not leaked_values, f"stock quantities leaked as values: {leaked_values}"


# ==================================================================== AC-SA302, AC-SA303


def test_available_counts_only_the_contacts_policy_locations(db):
    """AC-SA302. 0 on hand in the policy set (BRW), 500 outside it (DC1); X = 100 covers
    Q = 10 easily IF the excluded stock counted - it must not, so the branch is never
    `in_stock` (no shipment seeded, so it lands on `no_incoming`)."""
    brw = _wh(db, "ZZTBRW")
    dc1 = _wh(db, "ZZTDC1")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=500)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 10}
    )

    entry = _entry(result, p.id)
    assert entry["branch"] == "no_incoming"
    _assert_no_quantity_anywhere(result, {500, 100, 10})


def test_open_so_in_policy_scope_is_subtracted(db):
    """AC-SA303. On hand 100, open SO 60 (net 40): Q = 50 exceeds the net, so it is
    answered short with the NET count (AVAIL-MODE-REPLIES rule 2, was "not in_stock");
    Q = 40 exactly covers it and carries no count."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 200
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=100)
    _so_line(db, product_id=p.id, warehouse_id=brw.id, ordered=60)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    not_covered = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 50}
    )
    assert _entry(not_covered, p.id)["branch"] == "in_stock"
    assert _entry(not_covered, p.id)["available_qty"] == 40

    covered = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 40}
    )
    assert _entry(covered, p.id)["branch"] == "in_stock"
    _assert_no_quantity_anywhere(covered, {100, 60, 40})


# =================================================================== AC-SA310, AC-SA317


def test_entry_shape_is_the_v2_shape_not_1118s(db):
    """AC-SA310. `branch`, `cap_unset`, `category_name`, `eta`, `packing_list` are the
    only new keys; `needs_quantity`/`requested_qty` are unchanged; `verdict` /
    `running_low` / `disclaimer` / `available` (#1118's shape) are simply not present."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    category = _category_of(db, p)
    category.chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=100)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 10}
    )

    entry = _entry(result, p.id)
    assert set(entry.keys()) == {
        "product_id",
        "product_code",
        "product_name",
        "needs_quantity",
        "requested_qty",
        "branch",
        "cap_unset",
        "category_name",
        "eta",
        "packing_list",
        "available_qty",
    }
    assert entry["available_qty"] is None
    assert entry["needs_quantity"] is False
    assert entry["requested_qty"] == 10
    assert entry["branch"] == "in_stock"
    assert entry["cap_unset"] is False
    assert entry["category_name"] == category.category_name
    assert entry["eta"] is None
    assert entry["packing_list"] is None
    for gone in ("verdict", "running_low", "disclaimer", "available"):
        assert gone not in entry


def test_entry_cap_unset_true_when_neither_product_nor_category_sets_x(db):
    """AC-SA310 / R2. Neither the product nor its own category sets `chatbot_max_qty`
    (the seed helper's default): `cap_unset` is true and the branch is `too_big` for any
    quantity at all (X resolves to 0)."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=100)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 1}
    )

    entry = _entry(result, p.id)
    assert entry["branch"] == "too_big"
    assert entry["cap_unset"] is True


def test_per_product_map_wins_scalar_fills(db):
    """D20 (kept, unchanged code): `requested_quantities` names A only; B stays
    unanswered (`needs_quantity` true, `branch` null). Adding `requested_qty = 7` as
    the scalar fallback then judges B at 7, while A still reads from the map (5), not
    the scalar."""
    brw = _wh(db, "ZZTBRW")
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("DSVA2"))
    b = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("DSVB2"))
    _category_of(db, a).chatbot_max_qty = 100
    _category_of(db, b).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=a.id, warehouse_id=brw.id, on_hand=100)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=b.id, warehouse_id=brw.id, on_hand=100)
    db.flush()

    map_only = StockService(db).list_stock(
        product_ids=[a.id, b.id], contact_id=contact.id, requested_quantities={a.id: 5}
    )
    a_entry = _entry(map_only, a.id)
    b_entry = _entry(map_only, b.id)
    assert a_entry["requested_qty"] == 5
    assert a_entry["branch"] == "in_stock"
    assert b_entry == {
        "product_id": b.id,
        "product_code": b.product_code,
        "product_name": b.product_name,
        "needs_quantity": True,
        "requested_qty": None,
        "branch": None,
        "cap_unset": None,
        "category_name": None,
        "eta": None,
        "packing_list": None,
        "available_qty": None,
    }

    with_scalar = StockService(db).list_stock(
        product_ids=[a.id, b.id],
        contact_id=contact.id,
        requested_quantities={a.id: 5},
        requested_qty=7,
    )
    a_entry2 = _entry(with_scalar, a.id)
    b_entry2 = _entry(with_scalar, b.id)
    assert a_entry2["requested_qty"] == 5, "the map wins over the scalar for A"
    assert b_entry2["requested_qty"] == 7, "the scalar fills B, which the map did not name"
    assert b_entry2["needs_quantity"] is False
    assert b_entry2["branch"] == "in_stock"


# ================================================================================ AC-SA311


def test_packing_list_present_only_when_the_contacts_toggle_is_on(db):
    """AC-SA311. Same qualifying R5 shipment (no stock on hand, so the branch is
    `incoming`), answered to two contacts that differ only in `packing_list_allowed`:
    present for the contact with the toggle on, absent for the one without it - `eta`
    itself is unaffected either way."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 200
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    att = _attachment(db)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 19), attachment_id=att.id)
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id)

    allowed_contact = _contact(db, packing_list_allowed=True)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=allowed_contact)
    denied_contact = _contact(db, packing_list_allowed=False)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=denied_contact)
    db.flush()

    allowed_result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=allowed_contact.id, requested_quantities={p.id: 5}
    )
    allowed_entry = _entry(allowed_result, p.id)
    assert allowed_entry["branch"] == "incoming"
    assert allowed_entry["eta"] == "19/10/2026"
    assert allowed_entry["packing_list"] == {
        "filename": "packing-list.pdf",
        "file_path": att.file_path,
        "mime_type": "application/pdf",
    }

    denied_result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=denied_contact.id, requested_quantities={p.id: 5}
    )
    denied_entry = _entry(denied_result, p.id)
    assert denied_entry["branch"] == "incoming"
    assert denied_entry["eta"] == "19/10/2026"
    assert denied_entry["packing_list"] is None


def test_packing_list_absent_on_every_branch_other_than_incoming(db):
    """AC-SA311, the mirror case: the contact's toggle is on, but the branch is
    `in_stock` (nothing to attach a packing list to) - `packing_list` stays null."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 200
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=100)
    contact = _contact(db, packing_list_allowed=True)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 10}
    )

    entry = _entry(result, p.id)
    assert entry["branch"] == "in_stock"
    assert entry["packing_list"] is None


def test_eta_offset_adds_y_days_across_a_month_end(db):
    """Blocking 3, review round 1 (R5 truth table row 5): the `+ Y` offset on `eta` is
    only exercised at Y = 0 anywhere in this file before this test - a kill test that
    replaced `timedelta(days=y)` with `timedelta(days=0)` at
    `inventory_service.py:1666` left every block test green. `estimated_arrival_date`
    2026-10-28 + Y = 5 crosses into November, so a date-only slip (no month/year carry)
    would still read October and go undetected without a month-end case."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    category = _category_of(db, p)
    category.chatbot_max_qty = 200
    category.chatbot_eta_offset_days = 5
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    att = _attachment(db)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 28), attachment_id=att.id)
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 150}
    )

    entry = _entry(result, p.id)
    assert entry["branch"] == "incoming"
    assert entry["eta"] == "02/11/2026"


def test_product_eta_offset_overrides_category_offset_through_the_block(db):
    """Blocking 3, review round 1: R2's product-overrides-category rule for Y is only
    proven by S1's pure `stock_ask_limits.effective()` test - never through the full
    availability block, where `inventory_service.py` resolves `x, y = effective_limits(
    product, category)` itself. The category sets Y = 20 (would read 01/11/2026 off a
    2026-10-12 shipment); the product overrides it to Y = 3 (told date 15/10/2026).

    Round 2 review, Nit 1: a product Y of 0 stayed green even when the `+ Y` offset itself
    was zeroed (Blocking 3's own kill test), so it guarded only the override, never the
    offset. A non-zero product Y distinct from both 0 and the category's 20 guards both."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    category = _category_of(db, p)
    category.chatbot_max_qty = 200
    category.chatbot_eta_offset_days = 20
    p.chatbot_eta_offset_days = 3
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=0)
    att = _attachment(db)
    shipment = _incoming_shipment(db, eta=date(2026, 10, 12), attachment_id=att.id)
    _incoming_line(db, shipment_id=shipment.id, product_id=p.id)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 150}
    )

    entry = _entry(result, p.id)
    assert entry["branch"] == "incoming"
    assert entry["eta"] == "15/10/2026"


# =================================================================================== AC-SA316


def test_stock_verdict_module_is_gone():
    """AC-SA316. `app/services/stock_verdict.py` is deleted; importing it 404s."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.stock_verdict")


# ============================================================================= AC-SA315


def test_detailed_and_compact_ignore_requested_quantities(db):
    """AC-SA315. `requested_quantities` is only read under `availability`; a `detailed`
    or `compact` policy, or no `contact_id` at all (the staff grid), returns byte-
    identical responses with and without it - unchanged by S3's rewrite (R10)."""
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=42)
    contact = _contact(db)
    db.flush()

    for mode in ("detailed", "compact"):
        db.query(StockVisibilityPolicy).filter(
            StockVisibilityPolicy.contact_id == contact.id
        ).delete()
        _policy_row(db, mode=mode, contact=contact)
        db.flush()

        without = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
        with_qty = StockService(db).list_stock(
            product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 5}
        )
        assert with_qty == without, mode
        assert "stock_availability" not in with_qty, mode

    staff_without = StockService(db).list_stock(product_ids=[p.id])
    staff_with_qty = StockService(db).list_stock(
        product_ids=[p.id], requested_quantities={p.id: 5}
    )
    assert staff_with_qty == staff_without


def test_compact_stock_summary_stays_in_product_code_order(db):
    """Blocking 2, review round 1 (R10/AC-SA315): the asked-order sort ported for
    `availability` must not reach `compact` - `stock_summary` stays in `product_code`
    order regardless of the order `product_ids` named the products in."""
    brw = _wh(db, "ZZTBRW")
    aaa = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("AAA"))
    bbb = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("BBB"))
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=aaa.id, warehouse_id=brw.id, on_hand=10)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=bbb.id, warehouse_id=brw.id, on_hand=20)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[bbb.id, aaa.id], contact_id=contact.id)

    assert [row["product_code"] for row in result["stock_summary"]] == [
        aaa.product_code,
        bbb.product_code,
    ]


# ============================================================================= route level


def test_route_rejects_bad_requested_quantities_and_declares_the_new_keys(client, db):
    """A non-object, a non-UUID key, or a non-int value in `requested_quantities` is a
    400. The `response_model`'s per-item shape (`StockAvailabilityEntry`) must declare
    `branch`/`cap_unset`/`category_name`/`eta`/`packing_list` (AC-SA310) or
    `response_model` drops them off the wire even once the service computes them - and
    must NOT declare #1118's retired `verdict`/`running_low`/`disclaimer`/`available`."""
    from app.schemas.inventory import StockAvailabilityEntry

    declared = set(StockAvailabilityEntry.model_fields)
    assert {"branch", "cap_unset", "category_name", "eta", "packing_list"} <= declared
    assert not declared & {"verdict", "running_low", "disclaimer", "available"}

    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=10)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    not_an_object = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": p.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": json.dumps([1, 2, 3]),
        },
    )
    assert not_an_object.status_code == 400, not_an_object.text

    non_uuid_key = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": p.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": json.dumps({"not-a-uuid": 5}),
        },
    )
    assert non_uuid_key.status_code == 400, non_uuid_key.text

    non_int_value = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": p.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": json.dumps({str(uuid.uuid4()): "abc"}),
        },
    )
    assert non_int_value.status_code == 400, non_int_value.text


def test_route_answers_a_bad_product_or_warehouse_uuid_with_400_not_500(client, db):
    """Review round 1 nit (unchanged by S3). `parse_uuid_list` raises
    `HTTPException(400)`, but both of its calls sat INSIDE the handler's `except
    Exception as e: raise handle_internal_error(str(e))` block, which turns every
    exception alike into a 500 - `HTTPException` included. Hoisted out of the try the
    same way `requested_quantities` already was."""
    bad_product = client.get(
        "/api/v1/inventory/stock/balance",
        params={"product_ids": "not-a-uuid"},
    )
    assert bad_product.status_code == 400, bad_product.text

    bad_warehouse = client.get(
        "/api/v1/inventory/stock/balance",
        params={"warehouse_ids": "not-a-uuid"},
    )
    assert bad_warehouse.status_code == 400, bad_warehouse.text


# ============================== review round 5, one entry per product code (unchanged)
#
# Live evidence (`tests/chatbot/journeys/dealer-stock-verdict.EVIDENCE.md`, Run 2, case D
# turn 1): the dealer contact spans two companies that both carry product code MHS1028, so
# the block returned two entries for the one code. Ruling (owner, review round 5): a dealer
# sees ONE product per code. The merge happens in the backend block, where the figures
# live - unchanged by S3, only the merged entry's OWN fields (branch et al) are new.


def test_same_product_code_in_two_companies_is_one_entry_judged_on_the_sum(db):
    """The ruling's own scenario, re-judged through `branch()`. Sorento holds 10 with 4
    on open SO (net 6), Mocha holds 9 with 3 on open SO (net 6). Ask 10.

    Merged, the dealer is judged on 12 and the branch is `in_stock`. Unmerged, each
    company's own 6 is short of 10 and BOTH entries would answer `no_incoming` - which
    is the defect, twice over: two lines for one code, and both of them wrong."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    sorento_wh = _wh(db, unique_code("ZZTSRW")[:50])
    mocha_wh = warehouse(db, company_id=MOCHA_ID, code=unique_code("ZZTMCW")[:50])
    code = unique_code("ZZTDUP")[:50]
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    mocha_p = product(db, company_id=MOCHA_ID, code=code)
    # The merged entry keys off the CALLER's first-named id (`sorento_p` here) - the
    # cap is resolved against that product's own category (R2: no parent walk, and no
    # cap resolution across the merged group beyond the one the entry is keyed on).
    _category_of(db, sorento_p).chatbot_max_qty = 50
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=10,
    )
    stock(
        db, company_id=MOCHA_ID, product_id=mocha_p.id, warehouse_id=mocha_wh.id, on_hand=9
    )
    _so_line(db, product_id=sorento_p.id, warehouse_id=sorento_wh.id, ordered=4)
    _so_line(
        db,
        product_id=mocha_p.id,
        warehouse_id=mocha_wh.id,
        ordered=3,
        company_id=MOCHA_ID,
    )
    contact = _contact(db)
    _policy_row(
        db,
        mode="availability",
        warehouse_ids=[sorento_wh.id, mocha_wh.id],
        contact=contact,
    )
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[sorento_p.id, mocha_p.id],
        contact_id=contact.id,
        requested_quantities={sorento_p.id: 10},
    )

    assert len(result["stock_availability"]) == 1, result["stock_availability"]
    entry = result["stock_availability"][0]
    assert entry["product_id"] == sorento_p.id
    assert entry["product_code"] == code
    assert entry["requested_qty"] == 10
    assert entry["branch"] == "in_stock"
    # `pagination.total` still counts PRODUCTS (2 here) - the merge is about what the
    # dealer is told, not about how the page was walked - so 2 is not a forbidden number.
    _assert_no_quantity_anywhere(result, {10, 9, 4, 3, 12, 6})


def test_a_named_id_answers_for_every_company_row_of_that_code(db):
    """Review round 8, D27, re-judged through `branch()`. The dealer asks about one
    code; the reply merges the two companies into one entry keyed on the first id; the
    task sends THAT id back. The answer has to be the same both times - so on-hand
    sitting on the company row that was NOT named still reaches the branch decision
    (here: covers the ask on its own, `in_stock`, even though the named id alone holds
    nothing)."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    sorento_wh = _wh(db, unique_code("ZZTR8S")[:50])
    mocha_wh = warehouse(db, company_id=MOCHA_ID, code=unique_code("ZZTR8M")[:50])
    shared = unique_code("ZZTSHR")[:50]
    mocha_p = product(db, company_id=MOCHA_ID, code=shared)
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID, code=shared)
    _category_of(db, mocha_p).chatbot_max_qty = 50
    # Nothing on the company the dealer NAMED (mocha_p); all the supply sits on the
    # sister row (sorento_p) - the live shape exactly.
    stock(
        db, company_id=MOCHA_ID, product_id=mocha_p.id, warehouse_id=mocha_wh.id, on_hand=0
    )
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=20,
    )
    contact = _contact(db)
    _policy_row(
        db,
        mode="availability",
        warehouse_ids=[sorento_wh.id, mocha_wh.id],
        contact=contact,
    )
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[mocha_p.id],
        contact_id=contact.id,
        requested_quantities={mocha_p.id: 5},
    )

    assert len(result["stock_availability"]) == 1, result["stock_availability"]
    entry = result["stock_availability"][0]
    assert entry["product_code"] == shared
    assert entry["branch"] == "in_stock", (
        "the on-hand on the company row the dealer did not name still answers for the code"
    )


def test_merged_entry_takes_the_earliest_qualifying_shipment_across_the_group(db):
    """The merge structure extended to R5: both merged ids carry their own qualifying
    shipment (Sorento March, Mocha February); the merged entry's `eta` is built off the
    EARLIER of the two, the same "earliest wins" rule `earliest_packing_list_shipment`
    already applies per product id."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    sorento_wh = _wh(db, unique_code("ZZTR9S")[:50])
    mocha_wh = warehouse(db, company_id=MOCHA_ID, code=unique_code("ZZTR9M")[:50])
    stem = unique_code("ZZTCASE")[:50]
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID, code=stem.upper())
    mocha_p = product(db, company_id=MOCHA_ID, code=stem.lower())
    _category_of(db, sorento_p).chatbot_max_qty = 50
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=0,
    )
    stock(
        db, company_id=MOCHA_ID, product_id=mocha_p.id, warehouse_id=mocha_wh.id, on_hand=0
    )
    sorento_att = _attachment(db)
    sorento_shipment = _incoming_shipment(
        db, eta=date(2026, 3, 1), attachment_id=sorento_att.id, company_id=DEFAULT_COMPANY_ID
    )
    _incoming_line(
        db,
        shipment_id=sorento_shipment.id,
        product_id=sorento_p.id,
        company_id=DEFAULT_COMPANY_ID,
    )
    mocha_att = _attachment(db)
    mocha_shipment = _incoming_shipment(
        db, eta=date(2026, 2, 1), attachment_id=mocha_att.id, company_id=MOCHA_ID
    )
    _incoming_line(
        db, shipment_id=mocha_shipment.id, product_id=mocha_p.id, company_id=MOCHA_ID
    )
    contact = _contact(db)
    _policy_row(
        db,
        mode="availability",
        warehouse_ids=[sorento_wh.id, mocha_wh.id],
        contact=contact,
    )
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[sorento_p.id, mocha_p.id],
        contact_id=contact.id,
        requested_quantities={mocha_p.id: 10},
    )

    assert len(result["stock_availability"]) == 1, result["stock_availability"]
    entry = result["stock_availability"][0]
    assert entry["product_code"] in {stem.upper(), stem.lower()}
    assert entry["branch"] == "incoming"
    assert entry["eta"] == "01/02/2026"


# ================================ review round 6, the asked order (unchanged)
#
# Live evidence Run 3, finding B: the reply reordered the dealer's own products.
# `ordered_ids` pages the candidates by `product_code` asc, the right order for the
# catalogue case and the wrong one whenever the caller named the products.


def test_availability_entries_follow_the_callers_product_ids_order(db):
    """The caller's order wins over product_code order."""
    brw = _wh(db, unique_code("ZZTORD")[:50])
    first = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTZED")[:50])
    second = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTALP")[:50])
    for p in (first, second):
        stock(
            db,
            company_id=DEFAULT_COMPANY_ID,
            product_id=p.id,
            warehouse_id=brw.id,
            on_hand=100,
        )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[first.id, second.id],
        contact_id=contact.id,
        requested_quantities={first.id: 5, second.id: 60},
    )

    assert [e["product_code"] for e in result["stock_availability"]] == [
        first.product_code,
        second.product_code,
    ]


def test_availability_with_no_products_named_stays_in_page_order(db):
    """The catalogue case is untouched: nothing was named, so there is no asked order
    to keep and the page's own `product_code` order is the answer's."""
    brw = _wh(db, unique_code("ZZTORD")[:50])
    zed = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTZED")[:50])
    alpha = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTALP")[:50])
    for p in (zed, alpha):
        stock(
            db,
            company_id=DEFAULT_COMPANY_ID,
            product_id=p.id,
            warehouse_id=brw.id,
            on_hand=100,
        )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(contact_id=contact.id, requested_qty=5)

    codes = [e["product_code"] for e in result["stock_availability"]]
    assert codes == sorted(codes)


def test_the_expansion_keeps_the_asked_order_and_the_named_id(db):
    """The expansion must not disturb what round 6 and round 5 pinned: the caller's own
    order, and the merged entry keyed on the id the caller named (the id the task's slot
    carries back)."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    wh = _wh(db, unique_code("ZZTR8O")[:50])
    shared = unique_code("ZZTZED")[:50]
    mocha_p = product(db, company_id=MOCHA_ID, code=shared)
    product(db, company_id=DEFAULT_COMPANY_ID, code=shared)
    alpha = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTALP")[:50])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=alpha.id, warehouse_id=wh.id, on_hand=100)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[mocha_p.id, alpha.id],
        contact_id=contact.id,
        requested_quantities={mocha_p.id: 5, alpha.id: 5},
    )

    assert [e["product_code"] for e in result["stock_availability"]] == [
        shared,
        alpha.product_code,
    ]
    assert result["stock_availability"][0]["product_id"] == mocha_p.id


def test_compact_mode_is_not_expanded_by_code(db):
    """The expansion is the dealer mode's own rule (D27 merges by code there and only
    there). `compact` names locations per product row and is untouched."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    sorento_wh = _wh(db, unique_code("ZZTR8C")[:50])
    mocha_wh = warehouse(db, company_id=MOCHA_ID, code=unique_code("ZZTR8D")[:50])
    shared = unique_code("ZZTCMP")[:50]
    mocha_p = product(db, company_id=MOCHA_ID, code=shared)
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID, code=shared)
    stock(db, company_id=MOCHA_ID, product_id=mocha_p.id, warehouse_id=mocha_wh.id, on_hand=4)
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=7,
    )
    contact = _contact(db)
    _policy_row(
        db, mode="compact", warehouse_ids=[sorento_wh.id, mocha_wh.id], contact=contact
    )
    db.flush()

    result = StockService(db).list_stock(product_ids=[mocha_p.id], contact_id=contact.id)

    assert [row["product_id"] for row in result["stock_summary"]] == [mocha_p.id]


# ====== review round 11, D35: no product named, no catalogue question (unchanged)


def test_availability_with_no_product_named_asks_for_the_code(db):
    """No `product_ids`, no `product_id`: an empty block and the flag the presenter
    renders one sentence from. Never a page of the catalogue as a question."""
    brw = _wh(db, unique_code("ZZTR11")[:50])
    for _ in range(3):
        p = product(db, company_id=DEFAULT_COMPANY_ID)
        stock(
            db,
            company_id=DEFAULT_COMPANY_ID,
            product_id=p.id,
            warehouse_id=brw.id,
            on_hand=25,
        )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(contact_id=contact.id)

    assert result["stock_availability"] == []
    assert result["stock_visibility"]["needs_product"] is True
    assert result["data"] == [], "the dealer mode still names no location and no row"


def test_availability_with_a_product_named_is_unchanged(db):
    """The flag is absent - not false-y-by-accident, absent - whenever the caller did
    name a product, so nothing about today's answer moves."""
    brw = _wh(db, unique_code("ZZTR11")[:50])
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=25)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: 5}
    )

    assert "needs_product" not in result["stock_visibility"]
    assert [e["product_code"] for e in result["stock_availability"]] == [p.product_code]
    assert result["stock_availability"][0]["branch"] == "in_stock"


def test_compact_with_no_product_named_still_pages_the_catalogue(db):
    """`compact` and `detailed` are untouched: they answer with rows and locations, and
    a staff or n8n caller asking for the page is asking for the page."""
    brw = _wh(db, unique_code("ZZTR11")[:50])
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=25)
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(contact_id=contact.id)

    assert [row["product_id"] for row in result["stock_summary"]] == [p.id]
    assert "needs_product" not in result["stock_visibility"]
