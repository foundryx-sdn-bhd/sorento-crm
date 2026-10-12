"""Postgres-backed TestClient fixtures for the SCM M1 route tests.

The dashboard endpoints read the ``scm`` schema views, which only exist on the
live Postgres DB - so these tests run against the local prod-copy (same source as
the M0 view/golden tests). Every test runs inside a nested SAVEPOINT joined to an
outer transaction: the route's own ``db.commit()`` releases the savepoint (a
restart listener re-opens one), and the whole thing is rolled back on teardown so
nothing (seeded users, created SOs/DOs) ever escapes to the shared DB.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


def _pg_url() -> str | None:
    # A live DATABASE_URL env var wins (CI / container). Fall back to the local
    # .env file, which is NOT shipped in the CI Docker image (.dockerignore) - so
    # its absence must never crash collection, only skip the Postgres route tests.
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        if not os.path.exists(env):
            return None
        with open(env) as fh:
            for line in fh:
                if line.startswith("DATABASE_URL"):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not raw:
        return None
    return re.sub(r"^postgresql\+\w+", "postgresql", raw)


URL = _pg_url()
requires_pg = pytest.mark.skipif(
    not URL or not URL.startswith("postgresql"),
    reason="SCM M1 route tests require a live Postgres DATABASE_URL",
)


# Reference rows this suite assumes exist. Codes are marker-prefixed so they cannot
# collide with anything real on the local prod-copy database.
_REF_CATEGORY_CODE = "ZZSCMREF-CAT"
_REF_UOM_CODE = "ZZSCMREF-UOM"
_REF_PRODUCT_CODE = "ZZSCMREF-PRODUCT"
_REF_WAREHOUSE_CODE = "ZZSCMREF-WH"


def ensure_reference_data(db) -> None:
    """Guarantee the shared reference rows the SCM helpers borrow with ``LIMIT 1``.

    Many helpers in this suite resolve their FK targets by borrowing an existing row,
    for example ``SELECT category_id, base_uom_id FROM products WHERE category_id IS
    NOT NULL LIMIT 1``. On the local prod-copy database that silently finds something
    real, which is why the pattern went unnoticed; on a freshly bootstrapped database
    (how CI builds one) it returns ``None`` and the helper dies unpacking it. That one
    borrowed lookup accounted for 97 failures.

    Seeding here rather than rewriting every helper is deliberate: it makes the suite
    environment-independent in one place, and it runs inside the caller's savepoint so
    the rows are rolled back with everything else. It does NOT make the borrows correct
    in principle, and new tests must still seed their own chain.
    """
    from app.models.inventory import Warehouse
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    cat = (
        db.query(ProductCategory)
        .filter(ProductCategory.category_code == _REF_CATEGORY_CODE)
        .one_or_none()
    )
    if cat is None:
        cat = ProductCategory(
            id=str(uuid.uuid4()),
            category_code=_REF_CATEGORY_CODE,
            category_name="SCM test reference category",
        )
        db.add(cat)
        db.flush()

    uom = (
        db.query(UnitOfMeasure)
        .filter(UnitOfMeasure.uom_code == _REF_UOM_CODE)
        .one_or_none()
    )
    if uom is None:
        uom = UnitOfMeasure(
            id=str(uuid.uuid4()), uom_code=_REF_UOM_CODE, uom_name="SCM test reference unit"
        )
        db.add(uom)
        db.flush()

    # The borrow requires a product carrying BOTH a category and a base uom, so this row
    # exists to satisfy that predicate specifically.
    product = (
        db.query(Product).filter(Product.product_code == _REF_PRODUCT_CODE).one_or_none()
    )
    if product is None:
        db.add(
            Product(
                id=str(uuid.uuid4()),
                product_code=_REF_PRODUCT_CODE,
                product_name="SCM test reference product",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=0,
            )
        )
        db.flush()

    wh = (
        db.query(Warehouse)
        .filter(Warehouse.warehouse_code == _REF_WAREHOUSE_CODE)
        .one_or_none()
    )
    if wh is None:
        db.add(
            Warehouse(
                id=str(uuid.uuid4()),
                warehouse_code=_REF_WAREHOUSE_CODE,
                warehouse_name="SCM test reference warehouse",
                is_active=True,
            )
        )
        db.flush()


@pytest.fixture()
def scm_app():
    """Yields (app, db, get_current_user, get_current_user_or_api_key).

    Everything the route writes lives in a rolled-back savepoint.
    """
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key

    engine = create_engine(URL)
    connection = engine.connect()
    trans = connection.begin()
    Session = sessionmaker(bind=connection)
    db = Session()
    db.begin_nested()

    @event.listens_for(db, "after_transaction_end")
    def _restart_savepoint(session, transaction):  # noqa: ANN001
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    ensure_reference_data(db)

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    try:
        yield app, db, get_current_user, get_current_user_or_api_key
    finally:
        app.dependency_overrides.clear()
        event.remove(db, "after_transaction_end", _restart_savepoint)
        db.close()
        trans.rollback()
        connection.close()
        engine.dispose()


def seed_user(db, role_slug: str | None) -> str:
    """Create a user (rolled back) optionally assigned to an existing role."""
    from app.models.user import User, UserRoleAssignment

    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"scm-{uid}@test.com", name="SCM Test", status="ACTIVE"))
    db.flush()
    if role_slug:
        rid = db.execute(
            text("SELECT id FROM user_roles WHERE slug = :s"), {"s": role_slug}
        ).scalar()
        assert rid, f"role {role_slug} not seeded"
        db.add(UserRoleAssignment(user_id=uid, role_id=rid))
        db.flush()
    return uid


def _ensure_permission_row_committed(permission_slug: str) -> None:
    """Create the `user_permissions` row for `permission_slug`, committed on its OWN
    connection - NEVER inside the caller's savepoint.

    `TestClient(app)`'s startup event runs `sync_permissions`
    (`app/rbac/permission_registry.py`) on a SEPARATE `SessionLocal` connection, and it
    builds its `existing` set from COMMITTED state only. If this row were inserted inside
    the test's own uncommitted savepoint instead, `sync_permissions` cannot see it, tries
    to INSERT the same slug itself, and blocks on the `slug` unique index against our own
    uncommitted row - while our test session is itself blocked waiting for
    `TestClient(app)`'s startup to return. That is a DEADLOCK, not a failure, and it only
    bites on a fresh database where the slug is not already committed - exactly the case
    this helper exists for. Do NOT move this insert back inside the savepoint.

    `ON CONFLICT (slug) DO NOTHING` makes this idempotent with `sync_permissions` itself:
    whichever of the two writes first wins, and committing a row `sync_permissions` would
    have written anyway is safe. On the local prod copy the slug already exists, so this
    writes nothing at all.
    """
    engine = create_engine(URL)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO user_permissions (id, slug, name, description, created_at) "
                    "VALUES (:i, :s, :s, :d, now()) ON CONFLICT (slug) DO NOTHING"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "s": permission_slug,
                    "d": f"Seeded by tests for {permission_slug}.",
                },
            )
    finally:
        engine.dispose()


def grant_permission(db, role_slug: str, permission_slug: str) -> None:
    """Give `role_slug` a permission slug for the life of one test (rolled back).

    Needed because a route's `require_permission` slug is NOT seeded onto any role by
    the migrations. `sync_permissions` (app startup) creates the `user_permissions`
    ROW from `app/rbac/permission_registry.py`, and the grant sweeps that hand it to
    roles are SELECT-driven off grants that already exist - so on CI's freshly
    migrated, dataless database nobody holds it and every call is a 403, while the
    local prod-copy passes because a human granted it there years ago. A test that
    depends on the prod copy's grant table is testing the database, not the route.

    The permission row itself is created OUT OF BAND, on its own committed connection
    (`_ensure_permission_row_committed`) - see that function's docstring for why creating
    it inside this call's savepoint deadlocks against `sync_permissions`. Only the
    `user_role_permissions` grant is written inside the caller's savepoint here: startup
    never touches that table, so it is safe there. Idempotent: a role that already holds
    it is left alone.
    """
    rid = db.execute(
        text("SELECT id FROM user_roles WHERE slug = :s"), {"s": role_slug}
    ).scalar()
    assert rid, f"role {role_slug} not seeded"

    _ensure_permission_row_committed(permission_slug)

    pid = db.execute(
        text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": permission_slug}
    ).scalar()
    assert pid, f"permission {permission_slug} was not created"

    db.execute(
        text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "SELECT :i, :r, :p, now() WHERE NOT EXISTS ("
            "  SELECT 1 FROM user_role_permissions WHERE role_id = :r AND permission_id = :p)"
        ),
        {"i": str(uuid.uuid4()), "r": rid, "p": pid},
    )
    db.flush()


# The fixed Sorento company row (migration 302 / `_seed_default_company`), which is the
# company every reference row in this suite is stamped with.
SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"


def as_user(app, get_current_user, get_current_user_or_api_key, uid: str) -> None:
    """Authenticate as a staff principal AND give the session an active company.

    The company half is not optional. SCM planning artefacts (`scm.reorder_run` and the six
    others) are `CompanyScopedMixin`, so the auto-stamp REFUSES an insert with no single active
    company - a plan that belongs to nobody is exactly what company isolation exists to
    prevent. In a real request `apply_company_scope` resolves it from the caller's token; a
    dependency override replaces that, so the override has to supply it too or every
    run-creating route returns 400 and the failure looks like a bug in the route.

    Scoped to the INCUMBENT company rather than a freshly created one on purpose: the reference
    data this suite borrows (`ensure_reference_data`, and everything on a prod-copy database) is
    Sorento's, so a new company would authenticate successfully into an empty catalogue.
    """
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    principal = {"id": uid, "email": f"scm-{uid}@test.com"}
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    scope = frozenset({SORENTO_COMPANY_ID})
    for db in _sessions_for(app):
        set_company_scope(db, scope)

    async def _scope():
        for db in _sessions_for(app):
            set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _scope


def _sessions_for(app):
    """The session the `scm_app` fixture injected, if any.

    Read off the `get_db` override rather than passed in, so `as_user`'s signature does not
    change for its fourteen existing callers.
    """
    from app.database import get_db

    override = app.dependency_overrides.get(get_db)
    if override is None:
        return []
    try:
        gen = override()
        db = next(gen)
    except Exception:  # noqa: BLE001 - a non-generator override is not ours to interpret
        return []
    return [db] if db is not None else []


def set_plan_grain(db, grain: str) -> None:
    """Put the admin plan-grain policy on `grain` before a run is created.

    A run stamps the configured grain at creation (front planning 5.1 / 5.4) and only that
    grain may be decided on it, so a suite whose decisions are LOCATION-grain (accept /
    adjust / reject a recommendation) has to create its run under the `location` policy -
    under the rollout default, `product`, the run owns the `order_summary_row` decision
    instead and refuses them with `decision_grain_mismatch`, which is the contract rather
    than a defect.

    The settings row is created when absent so this does not depend on a seeded singleton
    (CI's database has none), and everything is rolled back with the caller's savepoint.
    """
    import uuid as _uuid

    from app.models.user import SystemSetting

    settings = db.query(SystemSetting).first()
    if settings is None:
        settings = SystemSetting(id=str(_uuid.uuid4()), name="ZZTSCM settings")
        db.add(settings)
    settings.plan_grain = grain
    db.flush()


def single_location_plan_basis(inputs: dict, warehouse, *, rounded: float) -> dict:
    """The canonical `ReorderRecommendation.inputs["plan_basis"]` of a ONE-LOCATION buy.

    Every buy the engine emits carries the basis of the SIZING GROUP it belongs to -
    the locations the buy was netted over, their channel needs, their shared supply
    facts, and the group's own netted replenishment (`reorder_run_service._plan_basis`).
    For a per-warehouse buy that group is the single location, which is the shape a
    hand-built fixture wants; a pool or network buy carries several locations and a
    group figure that is NOT their sum, which is what
    `tests/scm/test_channel_read_model.py` proves with the real engine.

    Derived from the same `inputs` dict the fixture already writes, so a test states its
    numbers once and cannot drift from the shape the freeze reads.
    """
    wid = str(warehouse.id)
    loc = {
        "warehouse_id": wid,
        "warehouse_code": warehouse.warehouse_code,
        "warehouse_name": warehouse.warehouse_name,
        "project_need": float(inputs.get("project_need") or 0.0),
        "retail_need": float(inputs.get("retail_need") or 0.0),
        "on_hand": inputs.get("on_hand"),
        "incoming_spo": inputs.get("on_order"),
        "on_order_po": inputs.get("po_ordered"),
        "reorder_level": inputs.get("reorder_level"),
        "avg_daily_demand": inputs.get("demand_rate"),
        "location_suggested_qty": float(rounded or 0.0),
    }
    return {
        "group": wid,
        "scope": "location",
        "project_need": loc["project_need"],
        "retail_need": loc["retail_need"],
        "recommended": loc["project_need"] + loc["retail_need"],
        "rounded": float(rounded or 0.0),
        "locations": [loc],
    }

