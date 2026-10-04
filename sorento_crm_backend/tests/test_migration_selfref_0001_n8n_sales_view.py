"""CHATBOT-SELFREF-SCOPE B1: `integration_n8n` may read the sales reports.

Production, 30 Sep 2026: `crm_sales_analysis` was answered 403 because the n8n key's
act-as principal (role `integration_n8n`) never held `sales.reports.view`
(`sales_s1_reports_module.GRANT_ROLES` is admin and superadmin only). Pinned against the
migration's own function, the way `test_migration_375_proforma_grant_sweep.py` pins its
sweep: the grant lands, it is idempotent, it creates the permission row when a create_all
database lacks it, a database without the role grants nothing, and the downgrade removes
exactly the one grant.

A `test_migration_*` file: CI runs it serially (LESSONS 97).
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests._pg_fixture import blank_session

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "selfref_0001_n8n_sales_view.py"
)
SLUG = "sales.reports.view"
ROLE = "integration_n8n"


def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_migration_selfref_0001", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _permission(db, slug: str) -> str:
    from app.models.user import UserPermission

    row = db.query(UserPermission).filter_by(slug=slug).first()
    if row is None:
        row = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug, description="")
        db.add(row)
        db.flush()
    return row.id


def _role(db, slug: str) -> str:
    from app.models.user import UserRole

    row = UserRole(
        id=str(uuid.uuid4()), slug=slug, name=slug, description="", is_protected=False, is_default=False
    )
    db.add(row)
    db.flush()
    return row.id


def _slugs_for(db, role_id: str) -> set[str]:
    rows = db.execute(
        text(
            "SELECT p.slug FROM user_role_permissions rp JOIN user_permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r"
        ),
        {"r": role_id},
    ).fetchall()
    return {slug for (slug,) in rows}


def _grant(db) -> None:
    _migration_module().grant_sales_reports_view(db.connection())


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def test_the_migration_is_a_single_parent_child_of_the_head_it_names():
    module = _migration_module()
    assert module.revision == "selfref_0001_n8n_sales_view"
    assert len(module.revision) <= 32
    assert isinstance(module.down_revision, str)


def test_n8n_gains_the_slug(db):
    role = _role(db, ROLE)
    _permission(db, SLUG)

    _grant(db)

    assert SLUG in _slugs_for(db, role)


def test_the_permission_row_is_created_when_missing(db):
    """A create_all database syncs the registry, but a bare one may not carry the row."""
    role = _role(db, ROLE)
    assert db.execute(text("SELECT count(*) FROM user_permissions WHERE slug = :s"), {"s": SLUG}).scalar() == 0

    _grant(db)

    assert db.execute(text("SELECT count(*) FROM user_permissions WHERE slug = :s"), {"s": SLUG}).scalar() == 1
    assert SLUG in _slugs_for(db, role)


def test_other_roles_gain_nothing(db):
    other = _role(db, "integration_sorento_mcp")
    outsider = _role(db, "zzt_selfref_outsider")
    _permission(db, SLUG)

    _grant(db)

    assert _slugs_for(db, other) == set()
    assert _slugs_for(db, outsider) == set()


def test_running_it_twice_changes_nothing(db):
    role = _role(db, ROLE)
    _permission(db, SLUG)

    _grant(db)
    _grant(db)

    count = db.execute(
        text("SELECT count(*) FROM user_role_permissions WHERE role_id = :r"), {"r": role}
    ).scalar()
    assert count == 1


def test_without_the_role_nothing_happens(db):
    _permission(db, SLUG)

    _grant(db)

    assert db.execute(text("SELECT count(*) FROM user_role_permissions")).scalar() == 0


def test_downgrade_removes_only_this_grant(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    role = _role(db, ROLE)
    admin = _role(db, "admin")
    perm = _permission(db, SLUG)
    db.execute(
        text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "VALUES (:id, :r, :p, now())"
        ),
        {"id": str(uuid.uuid4()), "r": admin, "p": perm},
    )
    _grant(db)
    assert SLUG in _slugs_for(db, role)

    module = _migration_module()
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        module.downgrade()

    assert SLUG not in _slugs_for(db, role)
    assert SLUG in _slugs_for(db, admin)
