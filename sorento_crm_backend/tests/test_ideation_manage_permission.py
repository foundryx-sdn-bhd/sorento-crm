"""IDEATION-IN-CRM B-01: the ``ideation.ideas.manage`` permission.

UAC AC-B-01: the slug exists in the permission registry (module ideation) and the reference
seed grants it to admin and superadmin. It is NOT granted to other roles by the seed.
"""
from __future__ import annotations

from sqlalchemy import text

from app.rbac.permission_registry import PERMISSION_REGISTRY, sync_permissions
from app.services import reference_seed
from tests._pg_fixture import blank_session

_SLUG = "ideation.ideas.manage"


def test_b01_manage_slug_registered_in_ideation_module():
    entries = [e for e in PERMISSION_REGISTRY if e["slug"] == _SLUG]
    assert len(entries) == 1, "ideation.ideas.manage must be registered exactly once"
    assert _SLUG.split(".")[0] == "ideation"
    assert entries[0]["name"].strip()
    assert (entries[0].get("description") or "").strip()


def test_b01_view_slug_still_registered():
    assert "ideation.board.view" in {e["slug"] for e in PERMISSION_REGISTRY}


def test_b01_reference_seed_grants_manage_to_admin_and_superadmin():
    with blank_session() as db:
        sync_permissions(db)
        reference_seed.seed_roles(db)
        reference_seed.grant_all_permissions_to_admin_roles(db)
        db.flush()
        rows = db.execute(
            text(
                "SELECT r.slug FROM user_role_permissions rp "
                "JOIN user_roles r ON r.id = rp.role_id "
                "JOIN user_permissions p ON p.id = rp.permission_id "
                "WHERE p.slug = :slug"
            ),
            {"slug": _SLUG},
        ).all()
        granted = {r[0] for r in rows}
    assert {"admin", "superadmin"} <= granted
    # The seed grants the slug to administrators only.
    assert granted <= {"admin", "superadmin"}
