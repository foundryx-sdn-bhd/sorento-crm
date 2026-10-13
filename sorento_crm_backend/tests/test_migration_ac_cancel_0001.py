"""Migration `ac_cancel_0001_docs` (AC-CANCEL-CRM): up, down, up, and who gets the new slugs.

Adds `autocount_cancel_docs` / `autocount_cancel_lines`, `qty_cancelled` on SO / PO / SPO lines,
`cancelled_by_cancel_doc` on SO / PO lines, and the six `scm.cancel_*` ingest slugs, granted to
admin / superadmin and to every role holding the matching order slug (the AutoCount feed's
role). The DDL runs on the real tables inside an outer transaction that is rolled back, so
nothing survives the test. Named `test_migration_*.py`: CI runs it in the serial migration pass.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import engine

VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()
REVISION = "ac_cancel_0001_docs"
NEW_COLUMNS = {
    "sales_order_lines": {"qty_cancelled", "cancelled_by_cancel_doc"},
    "purchase_order_lines": {"qty_cancelled", "cancelled_by_cancel_doc"},
    "spo_allocations": {"qty_cancelled"},
}
TABLES = ("autocount_cancel_docs", "autocount_cancel_lines")
_SO_SIDE = ("cancel_sales_orders", "consignments", "consignment_returns")
# The order's `.edit` brings view / edit; only the order's `.delete` brings the sweep (D30).
SO_SLUGS = {f"scm.{e}.{a}" for e in _SO_SIDE for a in ("view", "edit")}
SO_SWEEP = {f"scm.{e}.delete" for e in _SO_SIDE}
PO_SLUGS = {f"scm.cancel_purchase_orders.{a}" for a in ("view", "edit")}


def _load():
    alembic_dir = str(VERSIONS.parent)
    if alembic_dir not in sys.path:
        sys.path.insert(0, alembic_dir)
    spec = importlib.util.spec_from_file_location(f"m_{REVISION}", VERSIONS / f"{REVISION}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _state(conn) -> bool:
    inspector = sa.inspect(conn)
    cols = {t: {c["name"] for c in inspector.get_columns(t)} for t in NEW_COLUMNS}
    present = [NEW_COLUMNS[t] <= cols[t] for t in NEW_COLUMNS] + [
        inspector.has_table(t) for t in TABLES]
    absent = [not (NEW_COLUMNS[t] & cols[t]) for t in NEW_COLUMNS] + [
        not inspector.has_table(t) for t in TABLES]
    if all(present):
        return True
    if all(absent):
        return False
    raise AssertionError("partially migrated")


def _granted(conn, role_id: str) -> set[str]:
    return set(conn.execute(sa.text(
        "SELECT p.slug FROM user_role_permissions rp JOIN user_permissions p "
        "ON p.id = rp.permission_id WHERE rp.role_id = :r AND (p.slug LIKE 'scm.cancel_%' "
        "OR p.slug LIKE 'scm.consignment%')"
    ), {"r": role_id}).scalars())


def _role(conn, slug: str, holds: str | None) -> str:
    role_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO user_roles (id, slug, name, description, is_protected, is_default, is_trashed) "
        "VALUES (:i, :s, :s, '', false, false, false)"), {"i": role_id, "s": slug})
    if holds:
        conn.execute(sa.text(
            "INSERT INTO user_permissions (id, slug, name, created_at) "
            "SELECT gen_random_uuid()::text, :p, :p, now() "
            "WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = :p)"), {"p": holds})
        conn.execute(sa.text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "SELECT gen_random_uuid()::text, :r, id, now() FROM user_permissions "
            "WHERE slug = :p"), {"r": role_id, "p": holds})
    return role_id


def test_revision_fits_and_chains_onto_one_head():
    module = _load()
    assert module.revision == REVISION
    assert len(module.revision) <= 32
    assert module.down_revision == "oirj_0002_theme_sorento"


def test_up_down_up_and_the_grants():
    module = _load()
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            if _state(conn):
                _run(conn, module.downgrade)
            assert _state(conn) is False
            feed_so = _role(conn, f"zzt-feed-so-{uuid.uuid4().hex[:6]}", "scm.sales_orders.edit")
            feed_po = _role(conn, f"zzt-feed-po-{uuid.uuid4().hex[:6]}",
                            "scm.purchase_orders.edit")
            sweeper = _role(conn, f"zzt-del-so-{uuid.uuid4().hex[:6]}", "scm.sales_orders.delete")
            bystander = _role(conn, f"zzt-none-{uuid.uuid4().hex[:6]}", None)
            _run(conn, module.upgrade)
            assert _state(conn) is True
            assert _granted(conn, feed_so) == SO_SLUGS
            assert _granted(conn, feed_po) == PO_SLUGS
            assert _granted(conn, sweeper) == SO_SWEEP
            assert _granted(conn, bystander) == set()
            # idempotent: the seed again adds nothing
            before = conn.execute(sa.text("SELECT count(*) FROM user_role_permissions")).scalar()
            module.seed_permissions(conn)
            assert conn.execute(sa.text(
                "SELECT count(*) FROM user_role_permissions")).scalar() == before
            _run(conn, module.downgrade)
            assert _state(conn) is False
            assert _granted(conn, feed_so) == set()
            _run(conn, module.upgrade)
            assert _state(conn) is True
        finally:
            outer.rollback()
