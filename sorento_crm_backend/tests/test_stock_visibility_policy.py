"""Stock visibility policy - resolution, enforcement and the admin CRUD.

UAC `documentation/plans/inventory/stock-visibility-policy-acceptance-criteria.md`
sections A, B and C. PLAN `documentation/plans/inventory/PLAN-stock-visibility-policy.md`.

What the feature has to guarantee, and what each group here pins:

* **A - resolution.** Three tiers (contact override > access type > global default)
  with the MOST RESTRICTIVE access-type row winning when a contact holds several.
  A contact tagged both `dealer` and `end_user` must not be widened by the looser
  tag, or the dealer roll-out leaks quantities the day someone adds a second tag.
* **B - enforcement.** The policy is applied in `StockService.list_stock`, and ONLY
  when the request carries `contact_id` - the staff web grid calls the same route
  with no contact and must stay byte-identical. `compact` and `availability` return
  `data: []`: a row with the quantity stripped still names the locations and their
  count, and the raw (non-render) shape is readable by any direct MCP caller, so
  empty is the only shape that cannot leak.
* **C - CRUD.** One body shape for every tier (`{effective, override}`), reads on
  `inventory.stock.view`, writes on `inventory.stock.edit`, DELETE answering with
  the tier the caller falls back to.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import (
    get_current_user,
    get_current_user_or_api_key,
    get_db,
)
from app.models.access import ContactAccessType, RespondContact, StockVisibilityPolicy
from app.models.base import set_company_scope
from app.models.inventory import StockLedger
from app.models.product import ProductCategory
from app.models.respond_workspace import RespondWorkspace
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.inventory_service import StockService
from app.services.stock_visibility import default_policy, resolve_policy
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock, warehouse
from tests._pg_fixture import blank_session, unique_code

READ_PERM = "inventory.stock.view"
WRITE_PERM = "inventory.stock.edit"
BASE = "/api/v1/inventory/stock-visibility"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


# --------------------------------------------------------------------- seeding


def _workspace(db, space_id: str) -> RespondWorkspace:
    row = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=space_id,
        name="ZZT workspace",
        api_key_ciphertext="zzt",
    )
    db.add(row)
    db.flush()
    return row


def _contact(db, *, respond_io_id=None, workspace=None) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Contact",
        respond_io_id=respond_io_id,
        workspace_id=workspace.id if workspace else None,
    )
    db.add(row)
    db.flush()
    return row


def _access_type(db, code: str, name: str | None = None) -> ContactAccessType:
    row = ContactAccessType(code=code, name=name or code.title(), is_active=True)
    db.add(row)
    db.flush()
    return row


def _tag(db, contact: RespondContact, access_type: ContactAccessType) -> None:
    from app.models.access import respond_contact_access_types

    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact.id, access_type_code=access_type.code
        )
    )
    db.flush()


def _policy_row(
    db,
    *,
    mode: str,
    warehouse_ids=None,
    excluded_warehouse_ids=None,
    contact=None,
    access_type=None,
    hide_zero_locations: bool = False,
) -> StockVisibilityPolicy:
    row = StockVisibilityPolicy(
        id=str(uuid.uuid4()),
        contact_id=contact.id if contact else None,
        access_type_code=access_type.code if access_type else None,
        mode=mode,
        warehouse_ids=warehouse_ids,
        excluded_warehouse_ids=excluded_warehouse_ids,
        hide_zero_locations=hide_zero_locations,
    )
    db.add(row)
    db.flush()
    return row


def _wh(db, code: str, *, company_id: str = DEFAULT_COMPANY_ID, segment=None):
    row = warehouse(db, company_id=company_id, code=code)
    row.segment = segment
    db.flush()
    return row


def _bulk_import_ledger(db, *, product_id: str, warehouse_id: str) -> StockLedger:
    """The row `last_updated_at` is derived from (system-wide latest BULK_IMPORT)."""
    row = StockLedger(
        id=str(uuid.uuid4()),
        product_id=product_id,
        warehouse_id=warehouse_id,
        transaction_type="BULK_IMPORT",
        quantity_change=1,
        previous_quantity=0,
        new_quantity=1,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


# ============================================================ A. resolution


_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "416_stock_visibility_policy_stock_visibility_policies.py"
)

#: AC-1. Adds `excluded_warehouse_ids` and the CHECK
#: `ck_stock_visibility_policies_one_location_rule` on top of 416.
_MIGRATION_508 = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "508_stock_visibility_excl_wh.py"
)


def _load_migration(path: pathlib.Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_from_migration(db) -> None:
    """Run ONLY the migration's seed half against the blank schema.

    `create_all` builds the table but never runs a migration's INSERT (see
    LESSONS `create_all skips migration seeds`), so the seed has to be invoked
    explicitly to be tested at all.
    """
    module = _load_migration(_MIGRATION, "migration_416")
    module.seed_default_row(db.connection())


def test_migration_builds_the_table_the_model_expects(db):
    """`create_all` and the migration are two independent descriptions of the same
    table, and only one of them runs in production. Drop the created one, run the
    real `upgrade()`s in their place (416 then 508), and use the ORM against the
    result - a CHECK or a partial unique that only exists on the model would fail
    here.

    AC-1: 508 adds `excluded_warehouse_ids` and the CHECK
    `ck_stock_visibility_policies_one_location_rule`
    (`warehouse_ids IS NULL OR excluded_warehouse_ids IS NULL`); the seeded
    default row reads `("detailed", None, None)`.

    The contact is created BEFORE the DROP TABLE, and each `pytest.raises`
    assertion below rolls back only its OWN savepoint (`db.begin_nested()`),
    never the whole session. A plain `db.rollback()` between the two
    assertions - the first draft of this test - discards the DROP TABLE and
    both `upgrade()`s along with the failed insert, because this session joins
    the outer (per-test) transaction as one savepoint (`blank_session`,
    `join_transaction_mode="create_savepoint"`): rolling back reverts the
    table back to the one `create_all` declared from the ORM model, and the
    second assertion then passes against THAT table's CHECK rather than the
    migration's - green even with `op.create_check_constraint` deleted from
    508.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text as sa_text
    from sqlalchemy.exc import IntegrityError

    contact = _contact(db)
    db.flush()

    db.execute(sa_text("DROP TABLE stock_visibility_policies"))
    module_416 = _load_migration(_MIGRATION, "migration_416_upgrade")
    module_508 = _load_migration(_MIGRATION_508, "migration_508_upgrade")
    context = MigrationContext.configure(db.connection())
    with Operations.context(context):
        module_416.upgrade()
        module_508.upgrade()

    seeded = db.query(StockVisibilityPolicy).all()
    assert [
        (row.mode, row.warehouse_ids, row.excluded_warehouse_ids) for row in seeded
    ] == [("detailed", None, None)]

    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        _policy_row(db, mode="availability")
        db.flush()
    sp.rollback()

    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        _policy_row(
            db,
            mode="detailed",
            warehouse_ids=[str(uuid.uuid4())],
            excluded_warehouse_ids=[str(uuid.uuid4())],
            contact=contact,
        )
        db.flush()
    sp.rollback()

    db.rollback()


def test_default_row_seeded_inert(db):
    """A1. The deploy leaves one default row that changes nothing for anyone, and
    running the seed twice does not mint a second."""
    _seed_from_migration(db)
    _seed_from_migration(db)

    rows = (
        db.query(StockVisibilityPolicy)
        .filter(
            StockVisibilityPolicy.contact_id.is_(None),
            StockVisibilityPolicy.access_type_code.is_(None),
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].mode == "detailed"
    assert rows[0].warehouse_ids is None

    policy = default_policy(db)
    assert (policy.mode, policy.warehouse_ids, policy.source) == (
        "detailed",
        None,
        "default",
    )


def test_default_policy_without_a_row_is_still_detailed(db):
    """The floor of the chain is code, not data: a database built by `create_all`
    (CI) carries no seeded row, and the resolver must not fail closed there - it
    would black out the staff-facing default for everyone."""
    policy = default_policy(db)
    assert (policy.mode, policy.warehouse_ids, policy.source) == (
        "detailed",
        None,
        "default",
    )


def test_contact_override_beats_access_type(db):
    """A2."""
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    _tag(db, contact, dealer)
    _policy_row(db, mode="availability", access_type=dealer)
    _policy_row(db, mode="compact", contact=contact)

    policy = resolve_policy(db, contact.id)

    assert policy.mode == "compact"
    assert policy.source == "contact"


def test_multiple_access_types_most_restrictive(db):
    """A3. Most restrictive mode, intersection of warehouses, NULL = all."""
    brw = _wh(db, unique_code("BRW")[:50])
    mwh = _wh(db, unique_code("MWH")[:50])
    dc1 = _wh(db, unique_code("DC1")[:50])
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    end_user = _access_type(db, unique_code("end_user")[:50], "End user")
    _tag(db, contact, dealer)
    _tag(db, contact, end_user)
    _policy_row(
        db,
        mode="availability",
        warehouse_ids=[brw.id, mwh.id, dc1.id],
        access_type=dealer,
    )
    _policy_row(db, mode="detailed", warehouse_ids=None, access_type=end_user)

    policy = resolve_policy(db, contact.id)

    assert policy.mode == "availability"
    assert policy.warehouse_ids == frozenset({brw.id, mwh.id, dc1.id})
    assert policy.source == "access_type"


def test_multiple_access_types_intersect_their_warehouses(db):
    """A3, the half a single-row fixture cannot show: two rows that each name
    warehouses narrow to the OVERLAP, never to the union."""
    brw = _wh(db, unique_code("BRW")[:50])
    mwh = _wh(db, unique_code("MWH")[:50])
    dc1 = _wh(db, unique_code("DC1")[:50])
    contact = _contact(db)
    one = _access_type(db, unique_code("t1")[:50], "Type one")
    two = _access_type(db, unique_code("t2")[:50], "Type two")
    _tag(db, contact, one)
    _tag(db, contact, two)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id, mwh.id], access_type=one)
    _policy_row(db, mode="compact", warehouse_ids=[mwh.id, dc1.id], access_type=two)

    policy = resolve_policy(db, contact.id)

    assert policy.warehouse_ids == frozenset({mwh.id})


def test_falls_back_to_default(db):
    """A4."""
    _seed_from_migration(db)
    contact = _contact(db)

    policy = resolve_policy(db, contact.id)

    assert (policy.mode, policy.warehouse_ids, policy.source) == (
        "detailed",
        None,
        "default",
    )


def test_unresolvable_contact_fails_closed(db):
    """A5. Same shape as company scope today: zero rows, and NO visibility block -
    a block would tell the caller a policy was applied when none resolved."""
    assert resolve_policy(db, "ZZT-NO-SUCH-CONTACT") is None

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    wh = _wh(db, unique_code("WH")[:50])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id="ZZT-NO-SUCH-CONTACT", space_id="364817"
    )

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert "stock_visibility" not in result


def test_contact_id_both_forms(db):
    """A6. n8n thinks in Respond.io ids; other callers have already resolved the
    internal one. Guessing wrong denies an entitled contact."""
    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="99887766", workspace=workspace)
    _policy_row(db, mode="compact", contact=contact)

    by_internal = resolve_policy(db, contact.id)
    by_respond = resolve_policy(db, "99887766", "364817")

    assert by_internal.mode == by_respond.mode == "compact"
    assert by_internal.source == by_respond.source == "contact"


def test_staff_path_ignores_policy(db):
    """A7. The staff web grid calls the same route with no contact params. Even
    with the DEFAULT row flipped to compact, staff still get full rows - or
    flipping the default for the chatbot silently breaks /inventory/stock."""
    _policy_row(db, mode="compact")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    wh = _wh(db, unique_code("WH")[:50])
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=7)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id])

    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1
    assert "stock_visibility" not in result
    assert "stock_summary" not in result


# ============================================================ B. enforcement


def _three_warehouses(db):
    return (
        _wh(db, "ZZTBRW", segment="dealer"),
        _wh(db, "ZZTBRW-BB", segment="project"),
        _wh(db, "ZZTDC1", segment="dealer"),
    )


def test_detailed_filters_warehouses(db):
    """B1."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="detailed", warehouse_ids=[brw.id, brw_bb.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id}
    assert result["pagination"]["total"] == 2
    assert result["stock_visibility"] == {
        "mode": "detailed",
        "warehouse_codes": ["ZZTBRW", "ZZTBRW-BB"],
        "source": "contact",
        "hide_zero_locations": False,
    }


def test_detailed_null_is_all(db):
    """B2. NULL warehouses = today's behaviour, byte for byte."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh in (brw, brw_bb, dc1):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    contact = _contact(db)
    _policy_row(db, mode="detailed", warehouse_ids=None, contact=contact)
    db.flush()

    scoped = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
    staff = StockService(db).list_stock(product_ids=[p.id])

    assert len(scoped["data"]) == len(staff["data"]) == 3
    assert scoped["stock_visibility"]["warehouse_codes"] is None


def test_compact_groups_and_sums_on_hand(db):
    """B3. One block per product; the disallowed location is absent from the
    total as well as from the list."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    brw_ib = _wh(db, "ZZTBRW-IB", segment="project")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (brw_ib, 300), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(
        db,
        mode="compact",
        warehouse_ids=[brw.id, brw_bb.id, brw_ib.id],
        contact=contact,
    )
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert result["data"] == []
    # `total` counts the PRODUCTS answered for, not the rows withheld - the
    # summary modes page over products (B15).
    assert result["pagination"]["total"] == 1
    assert result["stock_visibility"]["mode"] == "compact"
    assert len(result["stock_summary"]) == 1
    block = result["stock_summary"][0]
    assert block["product_id"] == p.id
    assert block["product_code"] == p.product_code
    assert block["total_on_hand"] == 1000
    assert block["locations"] == [
        {"warehouse_code": "ZZTBRW", "quantity_on_hand": 500},
        {"warehouse_code": "ZZTBRW-BB", "quantity_on_hand": 200},
        {"warehouse_code": "ZZTBRW-IB", "quantity_on_hand": 300},
    ]
    assert block["flags"] == {"discontinued": False}


def test_compact_multi_product_blocks(db):
    """B4. One block per product, each with its own total."""
    brw, brw_bb, _ = _three_warehouses(db)
    first = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-A")
    second = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-B")
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=first.id, warehouse_id=brw.id, on_hand=10)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=first.id, warehouse_id=brw_bb.id, on_hand=5)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=second.id, warehouse_id=brw.id, on_hand=2)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[first.id, second.id], contact_id=contact.id
    )

    blocks = {b["product_code"]: b for b in result["stock_summary"]}
    assert list(blocks) == ["ZZT-SKU-A", "ZZT-SKU-B"]
    assert blocks["ZZT-SKU-A"]["total_on_hand"] == 15
    assert blocks["ZZT-SKU-B"]["total_on_hand"] == 2


def test_compact_names_a_product_with_no_stock(db):
    """B13. A product the contact NAMED still gets a block when it has no stock
    in any allowed location.

    Grouping only over the rows that came back drops that product silently, and
    silence is the one answer the reader cannot interpret: "we have none left"
    and "I never found what you asked for" arrive as the same empty reply. An
    id that names no product at all is still dropped - inventing a block for it
    would answer a question about a product that does not exist.
    """
    brw, brw_bb, dc1 = _three_warehouses(db)
    stocked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-HAS")
    bare = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-NONE")
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=stocked.id, warehouse_id=brw.id, on_hand=7
    )
    # `bare` HAS stock - just nowhere this contact is allowed to look.
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=bare.id, warehouse_id=dc1.id, on_hand=999
    )
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id, brw_bb.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[stocked.id, bare.id, str(uuid.uuid4())], contact_id=contact.id
    )

    blocks = {b["product_code"]: b for b in result["stock_summary"]}
    assert list(blocks) == ["ZZT-SKU-HAS", "ZZT-SKU-NONE"]
    assert blocks["ZZT-SKU-HAS"]["total_on_hand"] == 7
    assert blocks["ZZT-SKU-NONE"] == {
        "product_id": bare.id,
        "product_code": "ZZT-SKU-NONE",
        "product_name": bare.product_name,
        "total_on_hand": 0,
        "locations": [],
        "flags": {"discontinued": False},
    }


def test_compact_uses_on_hand_not_available(db):
    """B5. The basis is `quantity_on_hand` (today's answer); reserved is ignored,
    or the compact block would silently disagree with the detailed one."""
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    row = stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=100
    )
    row.quantity_reserved = 40
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert result["stock_summary"][0]["total_on_hand"] == 100


def _category_of(db, p) -> ProductCategory:
    """Chatbot stock ask v2 (S1): the `branch()` cap. `product()` always creates
    a fresh category with no `chatbot_max_qty`, so a B-series test that wants its
    yes/no judged on stock (not on an unset cap, R2) sets this first."""
    return db.query(ProductCategory).filter(ProductCategory.id == p.category_id).one()


# ---- the availability leak walk ---------------------------------------------

#: Keys allowed to carry a number on an `availability` answer. `requested_qty` is
#: the DEALER'S OWN number echoed back, not stock.
_ALLOWED_NUMERIC_KEYS = {"requested_qty", "needs_quantity"}
_QUANTITY_WORDS = ("quantity", "on_hand", "qty")


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
        # AVAIL-MODE-REPLIES rule 2: `available_qty` is a declared key; its VALUE is still
        # checked against the forbidden numbers below.
        if key not in _ALLOWED_NUMERIC_KEYS | {"available_qty"}
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


def test_availability_needs_quantity_no_leak(db):
    """B6. No number given yet -> ask for one, and say nothing else."""
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=613)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert result["data"] == []
    assert result["stock_availability"] == [
        {
            "product_id": p.id,
            "product_code": p.product_code,
            "product_name": p.product_name,
            "needs_quantity": True,
            "requested_qty": None,
            "branch": None,
            "cap_unset": None,
            "category_name": None,
            "eta": None,
            "packing_list": None,
            "available_qty": None,
        }
    ]
    assert "stock_summary" not in result
    _assert_no_quantity_anywhere(result, {613})


def test_availability_yes(db):
    """B7. 50 asked for, 60 on hand across the allowed locations."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=40)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw_bb.id, on_hand=20)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id, brw_bb.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=50
    )

    entry = result["stock_availability"][0]
    assert entry["branch"] == "in_stock"
    assert entry["needs_quantity"] is False
    assert entry["requested_qty"] == 50
    _assert_no_quantity_anywhere(result, {40, 20, 60})


def test_availability_no_ignores_disallowed_warehouses(db):
    """B8. 500 sitting in a location the dealer may not see is not their stock."""
    brw, _, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=40)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=500)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=50
    )

    entry = result["stock_availability"][0]
    # AVAIL-MODE-REPLIES rule 2 (owner, 2 Oct 2026): 40 allowed, Q 50 within X is "got
    # stock, 40 available" - never the 500 in the location the dealer may not see.
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == 40
    _assert_no_quantity_anywhere(result, {500})


def test_availability_says_no_for_a_product_with_no_stock(db):
    """B13, dealer side. Nothing in the allowed pool is a NO, not a missing
    entry: the dealer asked about this product and has to be told something."""
    brw, _, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    category = _category_of(db, p)
    category.chatbot_max_qty = 100
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=500
    )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=50
    )

    assert result["stock_availability"] == [
        {
            "product_id": p.id,
            "product_code": p.product_code,
            "product_name": p.product_name,
            "needs_quantity": False,
            "requested_qty": 50,
            "branch": "no_incoming",
            "cap_unset": False,
            "category_name": category.category_name,
            "eta": None,
            "packing_list": None,
            "available_qty": None,
        }
    ]
    _assert_no_quantity_anywhere(result, {500})


def test_availability_names_every_asked_product_even_the_one_with_no_stock(db):
    """CHATBOT-QUEUE-FIX review C3 (prod turn f0a2, "Srtswt3001 / Srtswt3001-gm stock"):
    two products asked, only one with stock. The availability block still carries an
    entry, and so a sentence, for EACH - the one with nothing is a NO, never dropped."""
    brw, _, _ = _three_warehouses(db)
    stocked = product(db, company_id=DEFAULT_COMPANY_ID)
    empty = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, stocked).chatbot_max_qty = 100
    _category_of(db, empty).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=stocked.id, warehouse_id=brw.id, on_hand=60)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[stocked.id, empty.id], contact_id=contact.id, requested_qty=10
    )

    branches = {e["product_id"]: e["branch"] for e in result["stock_availability"]}
    assert branches == {stocked.id: "in_stock", empty.id: "no_incoming"}
    _assert_no_quantity_anywhere(result, {60})


def test_availability_still_asks_for_a_product_with_no_stock(db):
    """B13. With no number given yet it is still the ask, even for a product
    with nothing in the pool: "how many do you need" is the only reply that
    does not disclose the (empty) quantity."""
    brw, _, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=500
    )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert result["stock_availability"] == [
        {
            "product_id": p.id,
            "product_code": p.product_code,
            "product_name": p.product_name,
            "needs_quantity": True,
            "requested_qty": None,
            "branch": None,
            "cap_unset": None,
            "category_name": None,
            "eta": None,
            "packing_list": None,
            "available_qty": None,
        }
    ]
    _assert_no_quantity_anywhere(result, {500})


def test_last_updated_carried_on_every_mode(db):
    """B10. n8n's `_Data last updated_` footer reads the envelope's
    `last_updated_at`, which the MCP walks out of the body. `compact` and
    `availability` carry no rows, so the payload has to carry it itself or the
    footer silently disappears for exactly the contacts on the new formats."""
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=9)
    ledger = _bulk_import_ledger(db, product_id=p.id, warehouse_id=brw.id)
    contact = _contact(db)
    db.flush()

    for mode in ("detailed", "compact", "availability"):
        db.query(StockVisibilityPolicy).filter(
            StockVisibilityPolicy.contact_id == contact.id
        ).delete()
        _policy_row(db, mode=mode, contact=contact)
        db.flush()

        result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

        assert result["last_updated_at"] == ledger.created_at, mode


def test_policy_composes_with_company_scope(db):
    """B12. Company scope runs first and the policy narrows what is left; a
    Mocha warehouse named on a Sorento contact's policy still yields nothing."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    sorento_wh = _wh(db, "ZZTBRW")
    mocha_wh = _wh(db, "ZZTMCH", company_id=MOCHA_ID)
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID)
    mocha_p = product(db, company_id=MOCHA_ID)
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=10,
    )
    stock(
        db,
        company_id=MOCHA_ID,
        product_id=mocha_p.id,
        warehouse_id=mocha_wh.id,
        on_hand=777,
    )
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[sorento_p.id, mocha_p.id], contact_id=contact.id
    )

    codes = {b["product_code"] for b in result["stock_summary"]}
    assert codes == {sorento_p.product_code}
    totals = [b["total_on_hand"] for b in result["stock_summary"]]
    assert totals == [10]


# ============================================================ route level


@pytest.fixture
def client(db, monkeypatch):
    """A staff caller holding both stock permissions, on the SAME session the
    assertions read."""

    def _override_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-stock-visibility@test.com"}
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


def test_requested_qty_below_one_is_read_as_no_quantity(client, db):
    """B9. `requested_qty` arrives from an LLM reading a sentence, so 0 is a
    parse artefact, not a demand. Refusing the whole call with a 422 loses the
    question as well as the number - the contact gets an error instead of "how
    many units do you need?" - so a value below 1 is treated as absent, in every
    mode, and the turn carries on."""
    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="90807060", workspace=workspace)
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=500)
    _policy_row(db, mode="availability", contact=contact)
    db.flush()

    for bad in (0, -5):
        response = client.get(
            "/api/v1/inventory/stock/balance",
            params={
                "product_ids": p.id,
                "contact_id": contact.id,
                "space_id": "364817",
                "requested_qty": bad,
            },
        )
        assert response.status_code == 200, bad
        entry = response.json()["stock_availability"][0]
        assert entry["needs_quantity"] is True, bad
        assert entry["requested_qty"] is None, bad
        assert entry["branch"] is None, bad


def test_response_model_declares_blocks(client, db):
    """B11. `response_model` silently drops undeclared fields, so the blocks are
    asserted on the SCHEMA and on a real response body."""
    from app.schemas.inventory import StockBalanceListResponse

    declared = set(StockBalanceListResponse.model_fields)
    assert {
        "stock_visibility",
        "stock_summary",
        "stock_availability",
        "last_updated_at",
    } <= declared

    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=12)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    body = client.get(
        "/api/v1/inventory/stock/balance",
        params={"product_ids": p.id, "contact_id": contact.id, "space_id": "364817"},
    ).json()

    assert body["data"] == []
    assert body["stock_visibility"]["mode"] == "compact"
    assert body["stock_summary"][0]["total_on_hand"] == 12


# ============================================================ C. CRUD API


def test_contact_policy_upsert(client, db):
    """C1. Second PUT REPLACES the warehouse list; merging would make removing a
    location impossible."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    contact = _contact(db)
    db.flush()

    first = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": [brw.id, brw_bb.id], "excluded_warehouse_ids": None},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["override"]["mode"] == "compact"
    assert [w["code"] for w in body["override"]["warehouses"]] == ["ZZTBRW", "ZZTBRW-BB"]
    assert body["effective"]["source"] == "contact"

    second = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "availability", "warehouse_ids": [dc1.id], "excluded_warehouse_ids": None},
    )
    assert [w["code"] for w in second.json()["override"]["warehouses"]] == ["ZZTDC1"]
    assert second.json()["effective"]["mode"] == "availability"
    assert (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .count()
        == 1
    )


def test_contact_policy_get_shows_the_inherited_tier(client, db):
    """The card has to name where the policy comes from, so an inheriting tier
    answers with the tier above it and a null override."""
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    _tag(db, contact, dealer)
    _policy_row(db, mode="availability", access_type=dealer)
    db.flush()

    body = client.get(f"{BASE}/contacts/{contact.id}").json()

    assert body["override"] is None
    assert body["effective"]["mode"] == "availability"
    assert body["effective"]["source"] == "access_type"
    assert body["effective"]["source_label"] == "Dealer"


def test_contact_policy_delete_falls_back(client, db):
    """C2. DELETE answers with the tier the contact falls back to, so the card
    re-renders the inherited policy without a second round trip."""
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    _tag(db, contact, dealer)
    _policy_row(db, mode="availability", access_type=dealer)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    response = client.delete(f"{BASE}/contacts/{contact.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["override"] is None
    assert body["effective"]["mode"] == "availability"
    assert body["effective"]["source"] == "access_type"
    assert (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .count()
        == 0
    )


def test_access_type_policy_roundtrip(client, db):
    """One dealer row is what makes the roll-out scale: every contact tagged
    `dealer` inherits it, so the access-type tier needs the same three verbs."""
    brw, _, dc1 = _three_warehouses(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    db.flush()

    saved = client.put(
        f"{BASE}/access-types/{dealer.code}",
        json={"mode": "availability", "warehouse_ids": [brw.id, dc1.id], "excluded_warehouse_ids": None},
    ).json()
    assert saved["effective"]["mode"] == "availability"
    assert saved["effective"]["source"] == "access_type"
    assert saved["effective"]["source_label"] == "Dealer"

    dropped = client.delete(f"{BASE}/access-types/{dealer.code}").json()
    assert dropped["override"] is None
    assert dropped["effective"]["source"] == "default"


def test_default_policy_routes(client, db):
    """The default tier is the floor: its override is always present and equals
    the effective policy, and there is no DELETE to leave the chain footless."""
    body = client.get(f"{BASE}/default").json()
    assert body["override"] == body["effective"]
    assert body["effective"]["source"] == "default"

    saved = client.put(f"{BASE}/default", json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None}).json()
    assert saved["effective"]["mode"] == "compact"
    assert saved["override"]["warehouses"] is None

    assert client.delete(f"{BASE}/default").status_code == 405


def test_policy_validation(client, db):
    """C3. Unknown access type -> 404 (the row would dangle); a mode outside the
    three or a warehouse that does not exist -> 422."""
    contact = _contact(db)
    db.flush()

    unknown_type = client.put(
        f"{BASE}/access-types/ZZT-NO-SUCH-CODE",
        json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None},
    )
    assert unknown_type.status_code == 404

    bad_mode = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "summary", "warehouse_ids": None, "excluded_warehouse_ids": None},
    )
    assert bad_mode.status_code == 422

    bad_warehouse = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": [str(uuid.uuid4())], "excluded_warehouse_ids": None},
    )
    assert bad_warehouse.status_code == 422

    unknown_contact = client.put(
        f"{BASE}/contacts/ZZT-NO-SUCH-CONTACT",
        json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None},
    )
    assert unknown_contact.status_code == 404


def test_single_default_row(client, db):
    """C6. Postgres NULLs are distinct, so a plain UNIQUE would let a second
    default row in and the resolution chain would pick one at random."""
    from sqlalchemy.exc import IntegrityError

    _policy_row(db, mode="detailed")
    db.flush()
    with pytest.raises(IntegrityError):
        _policy_row(db, mode="compact")
        db.flush()
    db.rollback()


def test_a_row_is_exactly_one_tier(db):
    """The CHECK behind the three-tier chain: a row naming both a contact and an
    access type has no defined precedence."""
    from sqlalchemy.exc import IntegrityError

    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    db.flush()
    with pytest.raises(IntegrityError):
        _policy_row(db, mode="compact", contact=contact, access_type=dealer)
        db.flush()
    db.rollback()


def test_effective_external(client, db):
    """C4. n8n's preflight convenience, reachable with the integration key's
    act-as principal rather than a staff JWT."""
    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="55443322", workspace=workspace)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    body = client.get(
        f"{BASE}/effective",
        params={"contact_id": "55443322", "space_id": "364817"},
    ).json()

    assert body["mode"] == "compact"
    assert body["source"] == "contact"


def test_effective_is_reachable_without_a_staff_jwt(db, monkeypatch):
    """C4, the half the shared fixture hides: `/effective` must accept the
    API-key principal. Only `get_current_user_or_api_key` is overridden here, so
    a route guarded by the JWT-only `require_permission` would 401."""

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "zzt-integration@test.com",
    }
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == READ_PERM,
    )
    try:
        response = TestClient(app).get(f"{BASE}/effective")
        assert response.status_code == 200
        assert response.json()["source"] == "default"
    finally:
        app.dependency_overrides.clear()


def test_policy_rbac(db, monkeypatch):
    """C5. Reads need view, writes need edit, and no credential at all is 401 -
    not 403, which would send an operator to the roles screen for nothing."""
    contact = _contact(db)
    db.flush()

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        anonymous = TestClient(app)
        assert anonymous.get(f"{BASE}/contacts/{contact.id}").status_code == 401
        assert (
            anonymous.put(
                f"{BASE}/contacts/{contact.id}",
                json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None},
            ).status_code
            == 401
        )

        principal = {"id": str(uuid.uuid4()), "email": "zzt-reader@test.com"}
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        monkeypatch.setattr(
            UserPermissionService,
            "check_user_has_permission",
            lambda self, uid, slug: slug == READ_PERM,
        )
        reader = TestClient(app)
        assert reader.get(f"{BASE}/contacts/{contact.id}").status_code == 200
        assert (
            reader.put(
                f"{BASE}/contacts/{contact.id}",
                json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None},
            ).status_code
            == 403
        )
        assert reader.delete(f"{BASE}/contacts/{contact.id}").status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---- the warehouse `segment` filter the "Dealer pool" preset needs ----------


def test_warehouses_segment_filter(client, db):
    """The preset's only caller. `warehouses.segment` and `WarehouseResponse.segment`
    already existed; without the filter the FE would have to page the whole master
    and filter client-side, which is what the no-capped-dropdown rule forbids."""
    _wh(db, "ZZTBRW", segment="dealer")
    _wh(db, "ZZTMWH", segment="dealer")
    _wh(db, "ZZTBRW-BB", segment="project")
    db.flush()

    body = client.get(
        "/api/v1/inventory/warehouses/",
        params={"segment": "dealer", "is_active": "true", "limit": 200},
    ).json()

    codes = {row["warehouse_code"] for row in body["data"]}
    assert codes == {"ZZTBRW", "ZZTMWH"}


# ============================================ B14. the neighbour probe under a policy


def _neighbour_probe(monkeypatch, *, returns=None, capture=None):
    """Stand in for the trigram/variant probe.

    Stubbed rather than seeded: `blank_session` pins `search_path` off `public`,
    so pg_trgm's `similarity()` cannot resolve there and the real probe would
    swallow its own exception and answer `[]` - which is what a test asserting
    "no alternatives" would then be reading. A stub that ALWAYS suggests is the
    only way to prove the suppression is the policy's doing.
    """
    from app.services import entity_resolver

    def _fake(db, code, *, has_data, limit=3, suggest_floor=0.4):
        if capture is not None:
            capture["code"] = code
            capture["has_data"] = has_data
        return list(returns or [])

    monkeypatch.setattr(entity_resolver, "find_entity_neighbours_with_data", _fake)


_NEIGHBOUR = [
    {"value": "ZZT-SKU-NEIGHBOUR", "display": "ZZT-SKU-NEIGHBOUR", "id": "n1", "sim": 0.9, "is_variant": True}
]


def test_availability_never_offers_alternatives(db, monkeypatch):
    """B14. An `availability` contact (the dealer mode) must never be handed a list
    of OTHER products that do have stock.

    The data-miss probe fires on `total == 0`, which is exactly the state a
    dealer asking about an out-of-stock product produces, and its answer rides
    the envelope as `alternatives` / `relaxed_axis`. The neighbour it names
    discloses the product list and the fact that stock exists somewhere, which
    is what the dealer mode exists to withhold.

    WA-MSG-TRIM (owner, 3 Oct 2026): `compact` is no longer in this rule. Every
    contact moved to compact, and the owner kept the "try these instead"
    suggestions for everyone - see `test_compact_offers_alternatives`.
    """
    capture: dict = {}
    _neighbour_probe(monkeypatch, returns=_NEIGHBOUR, capture=capture)
    brw, _, dc1 = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=asked.id, warehouse_id=dc1.id, on_hand=999
    )
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert "alternatives" not in result
    assert "relaxed_axis" not in result
    # Not even asked: the probe's own queries name products this contact is
    # not being told about.
    assert capture == {}


def test_compact_offers_alternatives(db, monkeypatch):
    """B14 as amended by WA-MSG-TRIM (owner, 3 Oct 2026): a `compact` contact
    gets the data-miss suggestions, exactly like `detailed`."""
    _neighbour_probe(monkeypatch, returns=_NEIGHBOUR)
    brw, _, _ = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert result["alternatives"] == _NEIGHBOUR
    assert result["relaxed_axis"] == "entity"


def test_compact_alternatives_only_count_stock_the_policy_allows(db, monkeypatch):
    """B14. Same has-data gate as `detailed`: a compact contact restricted to BRW
    is never offered a product whose only stock sits in a hidden location."""
    capture: dict = {}
    _neighbour_probe(monkeypatch, returns=[], capture=capture)
    brw, _, dc1 = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    allowed = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ALLOWED")
    hidden = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-HIDDEN")
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=allowed.id, warehouse_id=brw.id, on_hand=5
    )
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=hidden.id, warehouse_id=dc1.id, on_hand=500
    )
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert capture["code"] == asked.product_code
    assert capture["has_data"]([allowed.id, hidden.id]) == {allowed.id}


def test_detailed_still_offers_alternatives(db, monkeypatch):
    """B14, the other half. Suppression is scoped to the availability mode - a
    `detailed` contact still gets the data-miss suggestions every caller has had
    since §3.3, or the fix would quietly delete a working feature."""
    _neighbour_probe(monkeypatch, returns=_NEIGHBOUR)
    brw, _, _ = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    contact = _contact(db)
    _policy_row(db, mode="detailed", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert result["alternatives"] == _NEIGHBOUR
    assert result["relaxed_axis"] == "entity"


def test_detailed_alternatives_only_count_stock_the_policy_allows(db, monkeypatch):
    """B14. The has-data gate is what decides which neighbour is worth naming,
    and it counted stock in EVERY active warehouse. For a contact restricted to
    BRW that means suggesting a product whose only stock sits in a location they
    may not be told about - the suggestion is a promise the next question cannot
    keep, and it discloses the hidden location's contents by implication."""
    capture: dict = {}
    _neighbour_probe(monkeypatch, returns=[], capture=capture)
    brw, _, dc1 = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    allowed = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ALLOWED")
    hidden = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-HIDDEN")
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=allowed.id, warehouse_id=brw.id, on_hand=5
    )
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=hidden.id, warehouse_id=dc1.id, on_hand=500
    )
    contact = _contact(db)
    _policy_row(db, mode="detailed", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert capture["code"] == asked.product_code
    assert capture["has_data"]([allowed.id, hidden.id]) == {allowed.id}


# ============================================ the empty flag on a summary answer


def test_summary_modes_are_not_empty_when_they_carry_an_answer(db):
    """`empty` is what the MCP's escalation hint reads to decide a tool found
    nothing. `compact` and `availability` clear `data` by design, so a real
    answer - 500 units in BRW, or a yes - was being labelled empty and shipped
    with "We don't have that information available. Would you like me to route
    you to our warehouse team?" stapled to it."""
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=500)
    contact = _contact(db)
    db.flush()

    for mode in ("compact", "availability"):
        db.query(StockVisibilityPolicy).filter(
            StockVisibilityPolicy.contact_id == contact.id
        ).delete()
        _policy_row(db, mode=mode, contact=contact)
        db.flush()

        result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

        assert result["empty"] is False, mode
        assert result["data"] == [], mode


def test_a_summary_with_no_block_at_all_is_still_empty(db):
    """The other side of the flag: nothing asked for, nothing answered. Here
    "we don't have that information" is the right thing to offer."""
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[str(uuid.uuid4())], contact_id=contact.id
    )

    assert result["stock_summary"] == []
    assert result["empty"] is True


# ============================================ a location never reaches a dealer


def test_availability_block_never_names_a_location(db):
    """`stock_visibility.warehouse_codes` is the policy echoed back, and on the
    dealer mode it is a list of the exact locations the dealer may not be told
    about. The mode exists so no location and no number leaves the building, so
    the echo sits it out."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=5)
    contact = _contact(db)
    _policy_row(
        db, mode="availability", warehouse_ids=[brw.id, brw_bb.id], contact=contact
    )
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=1
    )

    assert result["stock_visibility"] == {
        "mode": "availability",
        "source": "contact",
        "hide_zero_locations": False,
    }
    assert "ZZTBRW" not in json.dumps(result, default=str)


def test_warehouse_codes_name_only_active_locations(db):
    """A policy row keeps the id of a warehouse that is later deactivated. The
    listing already ignores inactive locations, so echoing the dead code back
    would name a location no row can ever come from."""
    brw, brw_bb, _ = _three_warehouses(db)
    brw_bb.is_active = False
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id, brw_bb.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(contact_id=contact.id)

    assert result["stock_visibility"]["warehouse_codes"] == ["ZZTBRW"]


# ============================================ contact identity cannot diverge


def test_contact_without_a_space_id_fails_closed(client, db):
    """A5. `contact_id` alone applies the POLICY while company scope stays off
    entirely (`_resolve_api_key_scope` needs both params), so a contact would be
    answered a policy-shaped slice of EVERY company's stock. The two resolutions
    have to agree or nobody is answered at all."""
    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="11223344", workspace=workspace)
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=42)
    _policy_row(db, mode="detailed", contact=contact)
    db.flush()

    body = client.get(
        "/api/v1/inventory/stock/balance",
        params={"product_ids": p.id, "contact_id": contact.id},
    ).json()

    assert body["data"] == []
    assert body["pagination"]["total"] == 0
    assert body["stock_visibility"] is None

    with_space = client.get(
        "/api/v1/inventory/stock/balance",
        params={"product_ids": p.id, "contact_id": contact.id, "space_id": "364817"},
    ).json()

    assert len(with_space["data"]) == 1
    assert with_space["stock_visibility"]["mode"] == "detailed"


def test_company_scope_resolves_the_contact_the_policy_resolved(db):
    """A6. Both id forms name one contact, so both resolvers have to land on the
    same one. Company scope read `respond_io_id` ONLY, so an internal
    `respond_contacts.id` - the form the CRM's own callers hold - resolved a
    policy over ZERO rows: the answer looked like "we have none" and was in fact
    "I could not tell which company you are"."""
    from app.models.company import RespondContactCompany
    from app.services.contact_access_type_service import ContactAccessTypeService
    from app.services.field_access import resolve_contact_id

    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="66778899", workspace=workspace)
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()),
            respond_contact_id=contact.id,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()

    service = ContactAccessTypeService(db)
    by_respond = service.resolve_contact_company_ids("66778899", "364817")
    by_internal = service.resolve_contact_company_ids(contact.id, "364817")

    assert by_respond == by_internal == [DEFAULT_COMPANY_ID]
    assert (
        resolve_contact_id(db, contact.id, "364817")
        == resolve_contact_id(db, "66778899", "364817")
        == contact.id
    )


def test_company_scope_still_fails_closed_on_a_stranger(db):
    """The widening above must not become a hole: an id that names nobody, and a
    contact with no company rows, both still resolve to no companies at all."""
    from app.services.contact_access_type_service import ContactAccessTypeService

    workspace = _workspace(db, "364817")
    contact = _contact(db, respond_io_id="44556677", workspace=workspace)
    db.flush()

    service = ContactAccessTypeService(db)
    assert service.resolve_contact_company_ids("ZZT-NO-SUCH-CONTACT", "364817") == []
    assert service.resolve_contact_company_ids(contact.id, "364817") == []
    assert service.resolve_contact_company_ids(contact.id, "") == []


# ============================================ the admin routes, sharp edges


def test_contact_tier_routes_disambiguate_with_a_space_id(client, db):
    """The card opens on a contact whose Respond.io id also exists in another
    workspace. `/effective` takes a `space_id` and answers; the three tier routes
    did not, so the same contact 404'd on the screen that edits it - the admin
    reads "no such contact" about a contact they are looking at."""
    first = _workspace(db, "364817")
    second = _workspace(db, "999999")
    mine = _contact(db, respond_io_id="12341234", workspace=first)
    _contact(db, respond_io_id="12341234", workspace=second)
    _policy_row(db, mode="compact", contact=mine)
    db.flush()

    assert client.get(f"{BASE}/contacts/12341234").status_code == 404

    body = client.get(f"{BASE}/contacts/12341234", params={"space_id": "364817"}).json()
    assert body["override"]["mode"] == "compact"

    saved = client.put(
        f"{BASE}/contacts/12341234",
        params={"space_id": "364817"},
        json={"mode": "availability", "warehouse_ids": None, "excluded_warehouse_ids": None},
    )
    assert saved.status_code == 200
    assert saved.json()["override"]["mode"] == "availability"

    dropped = client.delete(f"{BASE}/contacts/12341234", params={"space_id": "364817"})
    assert dropped.status_code == 200
    assert dropped.json()["override"] is None
    assert (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == mine.id)
        .count()
        == 0
    )


def test_a_malformed_warehouse_id_is_rejected_not_a_500(client, db):
    """C3. `warehouse_ids` is a list of strings on the wire, so a value that is
    not a UUID reaches Postgres as one and comes back as a 500 the admin can do
    nothing with. It is the same mistake as naming a warehouse that does not
    exist, and it gets the same 422 and the same sentence."""
    contact = _contact(db)
    db.flush()

    response = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": ["not-a-uuid"], "excluded_warehouse_ids": None},
    )

    assert response.status_code == 422
    assert "not-a-uuid" in json.dumps(response.json())


def test_the_upsert_body_must_state_its_warehouses(client, db):
    """A PUT is a whole-row replace. With `warehouse_ids` defaulted, a caller
    that sent only `{mode}` silently widened the policy to every location - the
    one edit an admin can make without meaning to."""
    contact = _contact(db)
    db.flush()

    response = client.put(f"{BASE}/contacts/{contact.id}", json={"mode": "compact"})

    assert response.status_code == 422


def test_policy_writes_and_deletes_are_audited(client, db):
    """Who told the chatbot to stop naming locations for this contact, and when.

    A policy row is master data with a blast radius - it decides what every
    future answer to that contact contains - so it needs the same history every
    other audited table has. The delete half is the one that breaks silently: a
    bulk `query.delete()` never loads the row, so no ORM event fires and the
    removal leaves no trace at all.
    """
    from app.models.audit import AuditLog
    from app.services.audit_service import _audit_entity_type, register_audit_listeners

    register_audit_listeners()
    contact = _contact(db)
    db.flush()

    client.put(
        f"{BASE}/contacts/{contact.id}", json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None}
    )
    row_id = str(
        db.query(StockVisibilityPolicy.id)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .scalar()
    )
    client.delete(f"{BASE}/contacts/{contact.id}")

    entries = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == row_id)
        .order_by(AuditLog.changed_at)
        .all()
    )
    assert _audit_entity_type(StockVisibilityPolicy) == "stock_visibility_policies"
    actions = [entry.action for entry in entries]
    assert "CREATE" in actions
    assert "DELETE" in actions


# ============================================ C7. the policy is not company data


def test_a_policy_spanning_two_companies_reads_back_whole(client, db):
    """C7. `warehouses` is company-scoped; `stock_visibility_policies` is not.

    So a policy naming BRW (Sorento) and MWH (Mocha) resolved to BRW alone for a
    Sorento admin - and because the card Saves what it was shown, the next Save
    silently dropped Mocha from the list. An override naming ONLY Mocha rendered
    as "no locations" and saved as NULL, which is every location: the widest
    possible policy, written by an admin who touched nothing.
    """
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    sorento_wh = _wh(db, "ZZTBRW")
    mocha_wh = _wh(db, "ZZTMCH", company_id=MOCHA_ID)
    contact = _contact(db)
    _policy_row(
        db, mode="compact", warehouse_ids=[sorento_wh.id, mocha_wh.id], contact=contact
    )
    db.flush()

    body = client.get(f"{BASE}/contacts/{contact.id}").json()

    assert [w["code"] for w in body["override"]["warehouses"]] == ["ZZTBRW", "ZZTMCH"]


def test_a_warehouse_from_another_company_can_be_saved(client, db):
    """C7, the write half. The validation runs the same scoped lookup, so a
    perfectly real Mocha warehouse came back as "Unknown warehouse" - a 422
    naming a UUID, on a screen that never shows one."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    mocha_wh = _wh(db, "ZZTMCH", company_id=MOCHA_ID)
    contact = _contact(db)
    db.flush()

    response = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": [mocha_wh.id], "excluded_warehouse_ids": None},
    )

    assert response.status_code == 200, response.json()
    assert [w["code"] for w in response.json()["override"]["warehouses"]] == ["ZZTMCH"]


def test_stock_enforcement_stays_company_scoped(db):
    """The bypass above is for the POLICY row's own warehouse list, and nowhere
    else. The listing still answers from the caller's companies only."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    sorento_wh = _wh(db, "ZZTBRW")
    mocha_wh = _wh(db, "ZZTMCH", company_id=MOCHA_ID)
    sorento_p = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-SRT")
    mocha_p = product(db, company_id=MOCHA_ID, code="ZZT-SKU-MCH")
    stock(
        db,
        company_id=DEFAULT_COMPANY_ID,
        product_id=sorento_p.id,
        warehouse_id=sorento_wh.id,
        on_hand=10,
    )
    stock(
        db,
        company_id=MOCHA_ID,
        product_id=mocha_p.id,
        warehouse_id=mocha_wh.id,
        on_hand=777,
    )
    contact = _contact(db)
    _policy_row(
        db, mode="compact", warehouse_ids=[sorento_wh.id, mocha_wh.id], contact=contact
    )
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[sorento_p.id, mocha_p.id], contact_id=contact.id
    )

    assert [b["product_code"] for b in result["stock_summary"]] == ["ZZT-SKU-SRT"]
    assert [b["total_on_hand"] for b in result["stock_summary"]] == [10]


# ============================================ B15. a summary answer is paged


def test_compact_pages_over_products(db):
    """B15. "What stock do you have?" with no product named aggregated over every
    row the contact may see and returned one block per product - about 5,500 of
    them - while `pagination.total` read 0. The page/limit the caller sent has to
    mean the same thing in this mode as in the detailed one: products, in code
    order, with the real count beside them."""
    brw, _, _ = _three_warehouses(db)
    codes = ["ZZT-SKU-P1", "ZZT-SKU-P2", "ZZT-SKU-P3"]
    for code in codes:
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        stock(
            db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=5
        )
    contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    first = StockService(db).list_stock(contact_id=contact.id, limit=2)
    second = StockService(db).list_stock(contact_id=contact.id, limit=2, page=2)

    assert [b["product_code"] for b in first["stock_summary"]] == codes[:2]
    assert first["pagination"] == {"total": 3, "page": 1, "limit": 2}
    assert [b["product_code"] for b in second["stock_summary"]] == codes[2:]
    assert second["pagination"] == {"total": 3, "page": 2, "limit": 2}


def test_availability_pages_over_products(db):
    """B15, the dealer side: the same cap, or one question returns thousands of
    yes/no lines for products nobody asked about.

    D35 (review round 11, ported from PR #1118) answers the "nobody asked about" half
    at the source - a dealer ask that names NO product now comes back as an empty
    block and a request for the code, never as a page. So the products are named
    here, and what this still pins is the cap itself: a named set larger than the
    page returns one page of it with the true total beside it."""
    brw, _, _ = _three_warehouses(db)
    products = []
    for code in ("ZZT-SKU-P1", "ZZT-SKU-P2", "ZZT-SKU-P3"):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        stock(
            db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=5
        )
        products.append(p)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        contact_id=contact.id,
        product_ids=[p.id for p in products],
        limit=2,
        requested_qty=1,
    )

    assert [e["product_code"] for e in result["stock_availability"]] == [
        "ZZT-SKU-P1",
        "ZZT-SKU-P2",
    ]
    assert result["pagination"]["total"] == 3


# ============================================ the access-type lookup is a service


def test_unknown_access_type_is_rejected_by_the_service(db):
    """The router used to run this query itself, which PRINCIPLES.md hard-fails.
    It lives with the rest of the policy logic now, and answers the same 404 -
    a dangling `access_type_code` would be refused by the FK anyway, and a 500
    tells the admin nothing about which code was wrong."""
    from app.services.error_handler import AppException
    from app.services.stock_visibility import require_access_type

    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    db.flush()

    assert require_access_type(db, dealer.code) == dealer.code
    with pytest.raises(AppException) as raised:
        require_access_type(db, "ZZT-NO-SUCH-CODE")
    assert raised.value.status_code == 404


# ============================================ B17. the zero-quantity locations


def test_detailed_hides_zero_rows_but_keeps_the_negative_ones(db):
    """B17. `hide_zero_locations` drops the rows a reader has no use for - a
    location holding none of the product - without touching the anomalies.

    A negative on-hand is not "none left", it is a count that cannot be true, and
    the person who can fix it is the one reading this listing. Filtering on
    `quantity_on_hand != 0` keeps it; a `> 0` filter would have hidden exactly
    the rows somebody has to act on.
    """
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 0), (dc1, -4)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, dc1.id}
    assert result["pagination"]["total"] == 2
    assert result["stock_visibility"]["hide_zero_locations"] is True


def test_detailed_without_the_flag_still_shows_the_zero_rows(db):
    """B17. The flag defaults to false, so an existing policy answers exactly what
    it answered before this slice - the zero row included."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 0)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id}
    assert result["stock_visibility"]["hide_zero_locations"] is False


def test_detailed_hide_zero_takes_the_existing_empty_path(db):
    """B17 (re-added, review fix round - D5 does not reverse this). The product
    HAS stock (5 at BRW), but the REQUEST itself is narrowed to a DIFFERENT
    warehouse (BRW-BB, via the `warehouse_id` argument) where it has no row at
    all - not a zero row, no row. That is the plain "nothing matched this
    filter" shape, unrelated to D5's zero-everywhere rule, and it still takes
    the existing empty path: `data: []`, `total: 0`, `empty: true`."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=5)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, warehouse_id=brw_bb.id
    )

    assert result["data"] == []
    assert result["pagination"]["total"] == 0
    assert result["empty"] is True


def test_compact_drops_the_zero_locations_and_keeps_the_total(db):
    """B17. In `compact` the flag works on the location LINES, not on the rows:
    a line reading `BRW-BB: 0` is noise in a WhatsApp message, while the total is
    the answer to the question that was asked and must not move. A negative line
    stays for the same reason it does in `detailed`."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    brw_ib = _wh(db, "ZZTBRW-IB", segment="project")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 0), (brw_ib, -20)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(
        db,
        mode="compact",
        warehouse_ids=[brw.id, brw_bb.id, brw_ib.id],
        contact=contact,
        hide_zero_locations=True,
    )
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    block = result["stock_summary"][0]
    assert block["locations"] == [
        {"warehouse_code": "ZZTBRW", "quantity_on_hand": 500},
        {"warehouse_code": "ZZTBRW-IB", "quantity_on_hand": -20},
    ]
    # The withheld line held nothing, so the total it contributed to is the same
    # number it always was.
    assert block["total_on_hand"] == 480
    assert result["stock_visibility"]["hide_zero_locations"] is True


def test_compact_keeps_a_product_whose_every_location_is_zero(db):
    """B17 + B13. Hiding the lines must not hide the PRODUCT: a block reading
    `Total: 0` with no locations says "we have none", and dropping the block
    entirely says "I never found what you asked for". They are different answers
    and the contact cannot tell them apart from silence."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ALLZERO")
    for wh in (brw, brw_bb):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=0)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert len(result["stock_summary"]) == 1
    block = result["stock_summary"][0]
    assert block["product_code"] == "ZZT-SKU-ALLZERO"
    assert block["total_on_hand"] == 0
    assert block["locations"] == []
    # It carries an answer, so it is not the "nothing found" shape (B15).
    assert result["empty"] is False


# ================================ AC-9..AC-12 (12 Sep 2026): detailed hide-zero
# keeps a product that is zero EVERYWHERE, the same "none left, not never
# found" rule `compact` already applies (finding 5 / D5).


def test_detailed_hide_zero_keeps_a_product_zero_at_every_location(db):
    """AC-9. Two warehouses, both reading 0 - the product is zero EVERYWHERE for
    this contact, so both rows must still return (today's unconditional
    `!= 0` filter drops both; this is the regression the plan calls finding 5)."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh in (brw, brw_bb):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=0)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id}
    assert result["pagination"]["total"] == 2


def test_detailed_hide_zero_still_drops_zero_beside_real_stock(db):
    """AC-10 (regression pin - passes today already). 5 at BRW, 0 at BRW-BB: the
    product HAS stock somewhere, so the zero row is still dropped."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=5)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw_bb.id, on_hand=0)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id}
    assert result["pagination"]["total"] == 1


def test_detailed_hide_zero_negative_row_stays_and_the_zero_sibling_still_drops(db):
    """AC-11 (regression pin - passes today already). -2 at BRW, 0 at BRW-BB: the
    negative row is never dropped (existing rule), and the product is not zero
    everywhere (a negative is not zero), so the BRW-BB zero row still drops."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=-2)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw_bb.id, on_hand=0)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id}
    assert result["pagination"]["total"] == 1


def test_detailed_hide_zero_all_zero_ignores_stock_in_an_inactive_warehouse(db):
    """AC-12 (inactive-warehouse half, review fix round). The AC-9 product also
    holds 5 in a THIRD test warehouse that is `is_active=False` - the outer
    query already excludes inactive warehouses
    (`Stock.warehouse.has(Warehouse.is_active.is_(True))`), and the
    zero-everywhere predicate must carry that SAME criterion, or the inactive
    row still counts as "has stock somewhere" and wrongly drops the two active
    0 rows."""
    brw, brw_bb, _ = _three_warehouses(db)
    inactive_wh = _wh(db, unique_code("INACTWH")[:50])
    inactive_wh.is_active = False
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh in (brw, brw_bb):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=0)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=inactive_wh.id, on_hand=5)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id}
    assert result["pagination"]["total"] == 2


def test_detailed_hide_zero_all_zero_ignores_stock_in_an_excluded_warehouse(db):
    """AC-12 (excluded-warehouse half). The AC-9 product also holds 7 in a
    warehouse the policy EXCLUDES - that stock must not count as "has stock
    somewhere" for a contact who can never see it, so the two 0 rows still
    return. Pins D5's own warning: the zero-everywhere predicate must carry the
    SAME warehouse criterion as the outer query, explicitly."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh in (brw, brw_bb):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=0)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=7)
    contact = _contact(db)
    _policy_row(
        db, mode="detailed", contact=contact, hide_zero_locations=True,
        excluded_warehouse_ids=[dc1.id],
    )
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id}
    assert result["pagination"]["total"] == 2


def test_detailed_hide_zero_all_zero_ignores_stock_in_another_company(db):
    """AC-12 (another-company half). A row in ANOTHER company, sharing the SAME
    `product_id` (a data shape that should never make a product "has stock
    somewhere" for a DIFFERENT company's contact), must not count either.

    Company scope is pinned to BOTH companies (review fix round): under a
    single-company scope, `with_loader_criteria(..., include_aliases=True)`
    already hides the Mocha row inside the EXISTS on its own, so the test would
    stay green even with the predicate's `s2.company_id == Stock.company_id`
    clause deleted - it would not be pinning anything. With both companies in
    scope the loader criteria injects nothing, so this is the ONLY thing left
    to keep the Mocha row from being read as "stock somewhere": mentally
    delete the predicate and the Mocha row makes the product look non-zero
    everywhere, and the two 0 rows would vanish along with the Mocha row
    itself (the query already scopes `product_ids=[p.id]` company-blind, but
    the visibility policy's OWN row filter is what must exclude it). All three
    rows return - the two 0 rows genuinely visible to this Sorento contact,
    and the Mocha row because the query itself is scoped to both companies
    here (this is a service-level unit test, not a company-scoped API path)."""
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    brw, brw_bb, _ = _three_warehouses(db)
    mocha_wh = _wh(db, unique_code("MCHWH")[:50], company_id=MOCHA_ID)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh in (brw, brw_bb):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=0)
    stock(db, company_id=MOCHA_ID, product_id=p.id, warehouse_id=mocha_wh.id, on_hand=7)
    contact = _contact(db)
    _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=True)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id, mocha_wh.id}
    assert result["pagination"]["total"] == 3


def test_availability_ignores_hide_zero_locations(db):
    """B17. The dealer mode has no lines and no numbers to hide, so the flag
    changes nothing about the verdict: the yes/no is still judged on the sum over
    every allowed location, zero rows included."""
    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    for wh, qty in ((brw, 40), (brw_bb, 0)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="availability", contact=contact, hide_zero_locations=True)
    db.flush()

    yes = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=40
    )
    no = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=41
    )

    assert yes["stock_availability"][0]["branch"] == "in_stock"
    assert yes["stock_availability"][0]["available_qty"] is None
    # AVAIL-MODE-REPLIES rule 2: 41 against 40 is short, so it names the 40.
    assert no["stock_availability"][0]["branch"] == "in_stock"
    assert no["stock_availability"][0]["available_qty"] == 40
    # Still no location named, flag or no flag.
    assert "warehouse_codes" not in yes["stock_visibility"]


def test_access_types_merge_hide_zero_with_or(db):
    """B17. Most restrictive wins, and hiding is the restrictive reading: a
    contact tagged both `dealer` (hide) and `end_user` (show) must not have the
    dealer rule undone by the looser tag - the same rule the mode rank and the
    warehouse intersection already follow."""
    contact = _contact(db)
    hiding = _access_type(db, unique_code("dealer")[:50], "Dealer")
    showing = _access_type(db, unique_code("end_user")[:50], "End user")
    _tag(db, contact, hiding)
    _tag(db, contact, showing)
    _policy_row(db, mode="detailed", access_type=hiding, hide_zero_locations=True)
    _policy_row(db, mode="detailed", access_type=showing, hide_zero_locations=False)
    db.flush()

    policy = resolve_policy(db, contact.id)

    assert policy.hide_zero_locations is True
    assert policy.source == "access_type"


def test_a_contact_override_carries_its_own_hide_zero(db):
    """The override wins wholesale, in both directions: it can switch the flag ON
    over an access type that leaves it off, and OFF over one that sets it."""
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    _tag(db, contact, dealer)
    _policy_row(db, mode="detailed", access_type=dealer, hide_zero_locations=True)
    override = _policy_row(db, mode="detailed", contact=contact, hide_zero_locations=False)
    db.flush()

    assert resolve_policy(db, contact.id).hide_zero_locations is False

    setattr(override, "hide_zero_locations", True)
    db.flush()
    assert resolve_policy(db, contact.id).hide_zero_locations is True


def test_hide_zero_locations_round_trips_through_the_put(client, db):
    """C1, for the third field. The card sends it on every Save, so it has to
    survive the upsert, the read back and the effective/override payloads - a
    field the API drops looks to the admin like a toggle that will not stay on."""
    brw, _, _ = _three_warehouses(db)
    contact = _contact(db)
    db.flush()

    saved = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={
            "mode": "compact",
            "warehouse_ids": [brw.id],
            "excluded_warehouse_ids": None,
            "hide_zero_locations": True,
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["override"]["hide_zero_locations"] is True
    assert body["effective"]["hide_zero_locations"] is True

    assert client.get(f"{BASE}/contacts/{contact.id}").json()["override"][
        "hide_zero_locations"
    ] is True
    row = (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .one()
    )
    assert row.hide_zero_locations is True


def test_a_put_that_omits_hide_zero_locations_reads_as_false(client, db):
    """The default is off, and a PUT replaces the whole row: a body that says
    nothing about the flag turns it off rather than leaving the old value in
    place. `warehouse_ids` is required for the opposite reason - omitting it
    widened the policy - while omitting this one only ever narrows what is
    hidden, so the safe reading is the literal one."""
    contact = _contact(db)
    db.flush()

    client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None, "hide_zero_locations": True},
    )
    body = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None},
    ).json()

    assert body["override"]["hide_zero_locations"] is False
    row = (
        db.query(StockVisibilityPolicy)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .one()
    )
    assert row.hide_zero_locations is False


def test_the_other_two_tiers_carry_the_flag_too(client, db):
    """One card serves three tiers, so the field cannot exist on only one of the
    three routes - the dealer rule is set once on the access type, and the global
    switch-over is one edit on the default row."""
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    db.flush()

    access_type = client.put(
        f"{BASE}/access-types/{dealer.code}",
        json={"mode": "compact", "warehouse_ids": None, "excluded_warehouse_ids": None, "hide_zero_locations": True},
    ).json()
    assert access_type["effective"]["hide_zero_locations"] is True

    default = client.put(
        f"{BASE}/default",
        json={"mode": "detailed", "warehouse_ids": None, "excluded_warehouse_ids": None, "hide_zero_locations": True},
    ).json()
    assert default["effective"]["hide_zero_locations"] is True
    assert client.get(f"{BASE}/default").json()["effective"]["hide_zero_locations"] is True


def test_the_response_model_declares_hide_zero_locations(client, db):
    """B11, for the new field. `response_model` drops what it does not declare,
    so the echo n8n branches on would arrive as if the toggle had never been set."""
    from app.schemas.inventory import StockVisibilityBlock
    from app.schemas.stock_visibility import (
        StockVisibilityInput,
        StockVisibilityPolicyOut,
    )

    assert "hide_zero_locations" in StockVisibilityBlock.model_fields
    assert "hide_zero_locations" in StockVisibilityPolicyOut.model_fields
    assert "hide_zero_locations" in StockVisibilityInput.model_fields

    brw, brw_bb, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 12), (brw_bb, 0)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact, hide_zero_locations=True)
    db.flush()

    body = client.get(
        "/api/v1/inventory/stock/balance",
        params={"product_ids": p.id, "contact_id": contact.id, "space_id": "364817"},
    ).json()

    assert body["stock_visibility"]["hide_zero_locations"] is True
    assert body["stock_summary"][0]["locations"] == [
        {"warehouse_code": "ZZTBRW", "quantity_on_hand": 12}
    ]


# ============================================ excluded locations ("all except these")
#
# PLAN-stock-visibility-exclude-locations, UAC AC-2 .. AC-12. `warehouse_ids` is the
# include list unchanged; `excluded_warehouse_ids` is the new sibling: every ACTIVE
# warehouse except the ones named, including one created after the policy was saved.
# The CHECK enforcing "one rule or the other, never both" is asserted inside
# `test_migration_builds_the_table_the_model_expects` (AC-1) above.


def test_excluded_warehouses_hide_only_those(db):
    """AC-2. Detailed balance for a contact excluding A, with stock at A and B:
    only B's rows return. The echoed `warehouse_codes` names the non-excluded
    ACTIVE codes - an exclusion is a named set too (every active warehouse
    except these), so it echoes the same way an include list does; it is only
    null when the policy carries neither list at all."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {brw_bb.id, dc1.id}
    assert result["pagination"]["total"] == 2
    assert result["stock_visibility"]["warehouse_codes"] == ["ZZTBRW-BB", "ZZTDC1"]


def test_an_excluded_policy_sees_a_warehouse_created_later(db):
    """AC-3. A warehouse created (with stock) AFTER the exclusion was saved is
    visible to the contact without any change to the policy row - the whole
    point of "all except these" over an include list that would otherwise need
    editing every time a location is added."""
    brw, _, _ = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=500)
    contact = _contact(db)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw.id], contact=contact)
    db.flush()

    new_wh = _wh(db, "ZZTNEW")
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=new_wh.id, on_hand=42)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert {row.warehouse_id for row in result["data"]} == {new_wh.id}


def test_access_types_union_their_exclusions(db):
    """AC-4. Two access types excluding different warehouses merge to the UNION
    of what is withheld - the same direction hiding already wins in every other
    merge on this policy (mode rank, `hide_zero_locations`)."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    contact = _contact(db)
    one = _access_type(db, unique_code("t1")[:50], "Type one")
    two = _access_type(db, unique_code("t2")[:50], "Type two")
    _tag(db, contact, one)
    _tag(db, contact, two)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw.id], access_type=one)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw_bb.id], access_type=two)

    policy = resolve_policy(db, contact.id)
    assert policy.excluded_warehouse_ids == frozenset({brw.id, brw_bb.id})

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
    assert {row.warehouse_id for row in result["data"]} == {dc1.id}


def test_include_and_exclude_across_access_types_compose(db):
    """AC-5. One access type includes {A, B}, another excludes {B}: the merge
    carries BOTH rules and enforcement applies the include first, then the
    exclude on top of it - only A survives."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    contact = _contact(db)
    includer = _access_type(db, unique_code("t1")[:50], "Includer")
    excluder = _access_type(db, unique_code("t2")[:50], "Excluder")
    _tag(db, contact, includer)
    _tag(db, contact, excluder)
    _policy_row(db, mode="detailed", warehouse_ids=[brw.id, brw_bb.id], access_type=includer)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw_bb.id], access_type=excluder)

    policy = resolve_policy(db, contact.id)
    assert policy.warehouse_ids == frozenset({brw.id, brw_bb.id})
    assert policy.excluded_warehouse_ids == frozenset({brw_bb.id})

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
    assert {row.warehouse_id for row in result["data"]} == {brw.id}
    # The merged policy carries an include AND an exclude; the echoed codes must
    # not name the one the exclusion withheld even though it was on the include
    # list too.
    assert "ZZTBRW-BB" not in result["stock_visibility"]["warehouse_codes"]


def test_contact_exclusion_beats_access_type_include(db):
    """AC-6. The contact override wins WHOLE, same as every other field on this
    row: an access type naming {A, B} as the include list is beaten outright by
    a contact-level exclusion of {A} - B's rows return, and so does a third
    warehouse the access type never mentioned."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    _tag(db, contact, dealer)
    _policy_row(db, mode="detailed", warehouse_ids=[brw.id, brw_bb.id], access_type=dealer)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[brw.id], contact=contact)

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
    assert {row.warehouse_id for row in result["data"]} == {brw_bb.id, dc1.id}


def test_compact_honours_exclusion(db):
    """AC-7, compact half. The excluded location's line and its quantity are
    absent from the summary block; the total covers only the rest."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    contact = _contact(db)
    _policy_row(db, mode="compact", excluded_warehouse_ids=[dc1.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    block = result["stock_summary"][0]
    assert block["locations"] == [
        {"warehouse_code": "ZZTBRW", "quantity_on_hand": 500},
        {"warehouse_code": "ZZTBRW-BB", "quantity_on_hand": 200},
    ]
    assert block["total_on_hand"] == 700


def test_availability_no_ignores_excluded_warehouses(db):
    """AC-7, availability half. 999 sitting at the one EXCLUDED location is not
    this contact's stock - with nowhere else to look, 50 asked for is a no."""
    _, _, dc1 = _three_warehouses(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=dc1.id, on_hand=999)
    contact = _contact(db)
    _policy_row(db, mode="availability", excluded_warehouse_ids=[dc1.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_qty=50
    )

    assert result["stock_availability"][0]["branch"] != "in_stock"


def test_detailed_alternatives_skip_excluded_stock(db, monkeypatch):
    """AC-8. The data-miss probe's has-data gate must honour an EXCLUDE policy
    the same way it already honours an include list (see
    `test_detailed_alternatives_only_count_stock_the_policy_allows`): a
    neighbour whose only stock sits at an excluded warehouse is not worth
    suggesting - the next question about it cannot be answered either."""
    capture: dict = {}
    _neighbour_probe(monkeypatch, returns=[], capture=capture)
    brw, _, dc1 = _three_warehouses(db)
    asked = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ASKED")
    allowed = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-ALLOWED")
    hidden = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SKU-HIDDEN")
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=allowed.id, warehouse_id=brw.id, on_hand=5
    )
    stock(
        db, company_id=DEFAULT_COMPANY_ID, product_id=hidden.id, warehouse_id=dc1.id, on_hand=500
    )
    contact = _contact(db)
    _policy_row(db, mode="detailed", excluded_warehouse_ids=[dc1.id], contact=contact)
    db.flush()

    StockService(db).list_stock(product_ids=[asked.id], contact_id=contact.id)

    assert capture["code"] == asked.product_code
    assert capture["has_data"]([allowed.id, hidden.id]) == {allowed.id}


def test_put_rejects_include_and_exclude_together(client, db):
    """AC-9. Both non-null is not a valid rule, and the message is exact - not
    a generic 422 the admin has to decode."""
    contact = _contact(db)
    db.flush()

    response = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={
            "mode": "compact",
            "warehouse_ids": [str(uuid.uuid4())],
            "excluded_warehouse_ids": [str(uuid.uuid4())],
        },
    )

    assert response.status_code == 422
    assert response.json()["message"] == "Pick locations to include or to exclude, not both."


def test_the_upsert_body_must_state_its_exclusions(client, db):
    """AC-9. `excluded_warehouse_ids` is REQUIRED, same reasoning as
    `warehouse_ids`: a PUT replaces the whole row, so an omitted key must not
    silently change a stored exclusion by accident."""
    contact = _contact(db)
    db.flush()

    response = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "compact", "warehouse_ids": None},
    )

    assert response.status_code == 422


def test_a_malformed_excluded_warehouse_id_is_rejected_not_a_500(client, db):
    """AC-9. Same treatment as a malformed `warehouse_ids` entry - a 422 naming
    the bad value, never a 500."""
    contact = _contact(db)
    db.flush()

    response = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={
            "mode": "compact",
            "warehouse_ids": None,
            "excluded_warehouse_ids": ["not-a-uuid"],
        },
    )

    assert response.status_code == 422
    assert "not-a-uuid" in json.dumps(response.json())


def test_excluded_warehouse_ids_round_trip_on_every_tier(client, db):
    """AC-10. Round-trips on the contact, access-type and default tiers alike -
    one card, one shape, three rows - and the write is audited like every
    other field on this table."""
    from app.models.audit import AuditLog
    from app.services.audit_service import register_audit_listeners

    register_audit_listeners()
    brw, _, _ = _three_warehouses(db)
    contact = _contact(db)
    dealer = _access_type(db, unique_code("dealer")[:50], "Dealer")
    db.flush()

    for path in (
        f"{BASE}/contacts/{contact.id}",
        f"{BASE}/access-types/{dealer.code}",
        f"{BASE}/default",
    ):
        saved = client.put(
            path,
            json={
                "mode": "detailed",
                "warehouse_ids": None,
                "excluded_warehouse_ids": [brw.id],
            },
        )
        assert saved.status_code == 200, saved.json()
        override = saved.json()["override"]
        assert override["excluded_warehouses"] == [
            {"id": brw.id, "code": "ZZTBRW", "name": "Warehouse", "company_name": "Sorento"}
        ]
        assert override["warehouses"] is None

        read_back = client.get(path).json()["override"]
        assert read_back["excluded_warehouses"] == [
            {"id": brw.id, "code": "ZZTBRW", "name": "Warehouse", "company_name": "Sorento"}
        ]
        assert read_back["warehouses"] is None

    row_id = str(
        db.query(StockVisibilityPolicy.id)
        .filter(StockVisibilityPolicy.contact_id == contact.id)
        .scalar()
    )
    entries = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == row_id)
        .order_by(AuditLog.changed_at)
        .all()
    )
    # Not just THAT a row was written (a CREATE fires for any new policy) - the
    # audited change record must actually carry the field this test is about, or
    # a column excluded from `_model_to_audit_dict` would pass a weaker check
    # silently.
    create_entries = [entry for entry in entries if entry.action == "CREATE"]
    assert create_entries, "expected a CREATE audit row for the new contact override"
    assert create_entries[0].new_values.get("excluded_warehouse_ids") == [brw.id]


def test_the_response_model_declares_excluded_warehouses(client, db):
    """AC-11. `response_model` silently drops undeclared fields, so this is
    asserted on the SCHEMA and on a real response body."""
    from app.schemas.stock_visibility import StockVisibilityPolicyOut

    assert "excluded_warehouses" in StockVisibilityPolicyOut.model_fields

    brw, _, _ = _three_warehouses(db)
    contact = _contact(db)
    db.flush()

    body = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "detailed", "warehouse_ids": None, "excluded_warehouse_ids": [brw.id]},
    ).json()

    assert body["override"]["excluded_warehouses"] == [
        {"id": brw.id, "code": "ZZTBRW", "name": "Warehouse", "company_name": "Sorento"}
    ]


def test_an_empty_exclusion_is_stored_and_means_all(client, db):
    """AC-12. `[]` under Exclude is a real, storable value distinct from
    `null` on the wire, and it enforces the same as "every active warehouse" -
    the opposite of what `[]` means under Include, which is "none at all"."""
    brw, brw_bb, dc1 = _three_warehouses(db)
    contact = _contact(db)
    db.flush()

    saved = client.put(
        f"{BASE}/contacts/{contact.id}",
        json={"mode": "detailed", "warehouse_ids": None, "excluded_warehouse_ids": []},
    )
    assert saved.status_code == 200
    assert saved.json()["override"]["excluded_warehouses"] == []

    p = product(db, company_id=DEFAULT_COMPANY_ID)
    for wh, qty in ((brw, 500), (brw_bb, 200), (dc1, 999)):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)
    assert {row.warehouse_id for row in result["data"]} == {brw.id, brw_bb.id, dc1.id}
