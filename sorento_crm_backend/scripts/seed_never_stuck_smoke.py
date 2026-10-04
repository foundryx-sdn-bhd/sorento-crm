"""Seed the three sign-ins the never-stuck route smoke (guard G4) drives.

`e2e/never-stuck.smoke.spec.ts` opens every app route as an admin, as a restricted user
and with an expired session. `scripts.bootstrap_env` builds the schema and reference data
but seeds no users, so this adds them, idempotently:

- admin: the seeded `admin` role, which passes every backend gate.
- restricted: one role holding ONLY the procurement module's `.view` slugs plus
  `user_management.account.view`. That is the procurement-only role from the audit's
  top rows (packing-list tabs reading SCM endpoints the role cannot see, PR #1413). Its
  detail routes open a missing record (only user detail is seeded), so a tab that loads
  only once its record exists is not exercised yet; see `records` below.
- expired: a plain user whose session the spec revokes after sign-in, so its NextAuth
  cookie stays valid while every FastAPI call answers 401 (the owner's report).

Every module in the catalog is installed and enabled for the default tenant, as on
production. A fresh bootstrap leaves them all uninstalled, and the frontend's
`ModuleRouteGuard` then redirects EVERY module route to `/` for every persona: the smoke
would be checking the dashboard 400 times and passing. Every user is granted the default
company. Prints the manifest the spec reads as JSON
(`--out` writes it to a file). Refuses any database that is not local AND named
`*_smoke` / `*_ci`: the admin it adds has a published password, so this is for a
throwaway CI or sandbox database only, never a dev prod-copy.

    DATABASE_URL=postgresql://localhost/sorento_smoke python -m scripts.seed_never_stuck_smoke \
        --out ../sorento_crm_frontend/e2e/.never-stuck-seed.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from urllib.parse import urlparse

import bcrypt

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_PASSWORD = "NeverStuck-Smoke-1"
RESTRICTED_ROLE_SLUG = "never-stuck-procurement-viewer"
RESTRICTED_EXTRA_SLUGS = ("user_management.account.view",)

PERSONAS = {
    "admin": {"email": "never-stuck-admin@example.com", "name": "Never Stuck Admin"},
    "restricted": {"email": "never-stuck-restricted@example.com", "name": "Never Stuck Restricted"},
    "expired": {"email": "never-stuck-expired@example.com", "name": "Never Stuck Expired"},
}


def _require_throwaway_db() -> str:
    url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(url)
    name = parsed.path.lstrip("/")
    if parsed.hostname not in ("localhost", "127.0.0.1") or not name.endswith(("_smoke", "_ci")):
        sys.exit(
            "refusing to seed smoke users: DATABASE_URL must be a local database named "
            f"*_smoke or *_ci (host={parsed.hostname!r}, name={name!r})"
        )
    return url


def _restricted_slugs(db) -> list[str]:
    from app.models.user import UserPermission

    slugs = [
        p.slug
        for p in db.query(UserPermission).filter(UserPermission.slug.like("procurement.%.view")).all()
    ]
    return sorted(set(slugs) | set(RESTRICTED_EXTRA_SLUGS))


def _upsert_role(db, slug: str, name: str, perm_slugs: list[str]):
    from app.models.user import UserPermission, UserRole, UserRolePermission

    role = db.query(UserRole).filter_by(slug=slug).one_or_none()
    if role is None:
        role = UserRole(id=str(uuid.uuid4()), slug=slug, name=name, description="never-stuck smoke")
        db.add(role)
        db.flush()
    have = {rp.permission_id for rp in db.query(UserRolePermission).filter_by(role_id=role.id)}
    for perm in db.query(UserPermission).filter(UserPermission.slug.in_(perm_slugs)).all():
        if perm.id not in have:
            db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role.id, permission_id=perm.id))
    return role


def _upsert_user(db, *, email: str, name: str, password: str, role_id: str | None):
    from app.models.company import UserCompany
    from app.models.user import User, UserRoleAssignment

    user = db.query(User).filter_by(email=email).one_or_none()
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    if user is None:
        user = User(id=str(uuid.uuid4()), email=email, name=name, status="ACTIVE")
        db.add(user)
    user.password = hashed
    user.status = "ACTIVE"
    user.is_trashed = False
    user.last_active_company_id = DEFAULT_COMPANY_ID
    db.flush()
    if role_id and not db.query(UserRoleAssignment).filter_by(user_id=user.id, role_id=role_id).first():
        db.add(UserRoleAssignment(id=str(uuid.uuid4()), user_id=user.id, role_id=role_id))
    if not db.query(UserCompany).filter_by(user_id=user.id, company_id=DEFAULT_COMPANY_ID).first():
        db.add(UserCompany(user_id=user.id, company_id=DEFAULT_COMPANY_ID))
    return user


def _enable_all_modules(db) -> list[str]:
    from app.modules.runtime.installer import (
        DEFAULT_TENANT_ID,
        get_enabled_module_keys,
        install_modules,
        load_dependency_graph,
    )

    install_modules(db, DEFAULT_TENANT_ID, sorted(load_dependency_graph(db)), None, action="install")
    return sorted(get_enabled_module_keys(db, DEFAULT_TENANT_ID))


def seed(password: str) -> dict:
    from app.database import SessionLocal
    from app.models.user import UserRole

    db = SessionLocal()
    try:
        modules = _enable_all_modules(db)
        admin_role = db.query(UserRole).filter_by(slug="admin").one()
        slugs = _restricted_slugs(db)
        restricted_role = _upsert_role(db, RESTRICTED_ROLE_SLUG, "Never Stuck Procurement Viewer", slugs)
        roles = {"admin": admin_role.id, "restricted": restricted_role.id, "expired": None}
        users = {
            key: _upsert_user(db, password=password, role_id=roles[key], **PERSONAS[key])
            for key in PERSONAS
        }
        db.commit()
        return {
            "password": password,
            "personas": {key: {"email": PERSONAS[key]["email"], "id": u.id} for key, u in users.items()},
            "restrictedSlugs": slugs,
            "enabledModules": modules,
            # Route template -> a real id, for the detail routes this seed can fill. Every
            # other dynamic route is opened with a missing-record id (see the spec). Add a
            # row here when a fix lane wants its detail page smoked against real data.
            "records": {
                "/user-management/users/[id]": users["restricted"].id,
            },
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="write the manifest JSON here as well as to stdout")
    args = parser.parse_args()
    _require_throwaway_db()
    manifest = seed(os.environ.get("NEVER_STUCK_PASSWORD", DEFAULT_PASSWORD))
    text = json.dumps(manifest, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
