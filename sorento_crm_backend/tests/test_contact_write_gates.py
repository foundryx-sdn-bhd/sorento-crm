"""Write-route permission gates on the respond-contacts router.

Context: `documentation/plans/security/PLAN-sec-contact-write-perms-16oct.md` +
`documentation/plans/security/sec-contact-write-perms-acceptance-criteria.md`.

Seven writes in `app/api/v1/user_management/contacts.py` were
`Depends(get_current_user)` only, so any signed-in user could create, delete,
bulk delete, re-sync or re-scope a respond contact. They now require
`user_management.contacts.edit`, the slug every sibling contact write already
uses (migration 357 granted it to every role, so no role that can open the
contacts page loses an action).

Structure:

* UAC1 - per route, a caller without `.edit` gets 403 and nothing changes: the
  contact still exists, no contact is created, segments and attachment grants
  are untouched, and the Respond.io sync is never called.
* UAC2 - per route, a caller with `.edit` succeeds. The five DB-only routes run
  for real against a scratch schema; the two sync routes stub the
  `ContactService` method that would call Respond.io (tests never call it).
* UAC3 - structural: every non-GET route in `contacts.py` carries a
  `require_permission` dependency or sits in a commented allowlist, so a write
  added tomorrow without a gate fails here.

Dependency-override + `allow`-set monkeypatch pattern copied from
tests/test_user_management_read_gates.py.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.v1.user_management import contacts as contacts_module
from app.database import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.access import MarketSegment, RespondContact
from app.models.base import set_company_scope
from app.services.company_scope_resolver import apply_company_scope
from app.services.contact_service import ContactService
from tests._pg_fixture import blank_session, unique_code

CONTACTS_EDIT = "user_management.contacts.edit"
BASE = "/api/v1/user-management/contacts"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(db, monkeypatch):
    """A caller whose permission set is controlled by the returned `allow` set,
    plus a record of every Respond.io sync the routes attempted."""
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()
    sync_calls: list[tuple[str, object]] = []
    caller = {"id": str(uuid.uuid4()), "email": "write-gates-caller@zzt.test"}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        set_company_scope(_db, None)
        return None

    def _fake_sync_one(self, contact_id):
        sync_calls.append(("one", contact_id))
        return self.db.query(RespondContact).filter(RespondContact.id == contact_id).one()

    def _fake_sync_bulk(self, contact_ids):
        sync_calls.append(("bulk", list(contact_ids)))
        return {"synced": len(contact_ids), "failed": 0}

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    monkeypatch.setattr(ContactService, "sync_contact_name", _fake_sync_one)
    monkeypatch.setattr(ContactService, "bulk_sync_contacts_from_respond", _fake_sync_bulk)
    try:
        yield TestClient(app), allow, sync_calls
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(apply_company_scope, None)


# --------------------------------------------------------------------- seeds


def _seed_contact(db) -> str:
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=unique_code("phone"),
        name="ZZT Contact",
    )
    db.add(contact)
    db.flush()
    return str(contact.id)


def _seed_segment(db) -> str:
    code = unique_code("seg").lower().replace("-", "_")[:50]
    db.add(MarketSegment(id=str(uuid.uuid4()), code=code, name="ZZT Segment", is_active=True, sort_order=0))
    db.flush()
    return code


def _contact_exists(db, contact_id: str) -> bool:
    db.expire_all()
    return db.query(RespondContact.id).filter(RespondContact.id == contact_id).first() is not None


def _segment_codes(db, contact_id: str) -> set[str]:
    db.expire_all()
    contact = db.query(RespondContact).filter(RespondContact.id == contact_id).one()
    return {str(s.code) for s in contact.market_segments}


# ---------------------------------------------------------------- route table
#
# Each spec: (label, method, build(db) -> (url, json, ctx), succeeded(resp, db, ctx, sync_calls),
#             unchanged(db, ctx, sync_calls)).


def _bulk_sync(db):
    cid = _seed_contact(db)
    return f"{BASE}/bulk-sync", {"ids": [cid]}, {"cid": cid}


def _bulk_delete(db):
    cid = _seed_contact(db)
    return f"{BASE}/bulk", {"ids": [cid]}, {"cid": cid}


def _create(db):
    phone = unique_code("phone-new")
    return f"{BASE}/", {"phone_number": phone, "name": "ZZT New"}, {"phone": phone}


def _delete(db):
    cid = _seed_contact(db)
    return f"{BASE}/{cid}", None, {"cid": cid}


def _sync_one(db):
    cid = _seed_contact(db)
    return f"{BASE}/{cid}/sync", None, {"cid": cid}


def _segments(db):
    cid = _seed_contact(db)
    code = _seed_segment(db)
    return f"{BASE}/{cid}/market-segments", {"codes": [code]}, {"cid": cid, "code": code}


def _attachment_types(db):
    cid = _seed_contact(db)
    return f"{BASE}/{cid}/attachment-types", {"attachment_type_ids": []}, {"cid": cid}


def _created(db, phone) -> bool:
    db.expire_all()
    return db.query(RespondContact.id).filter(RespondContact.phone_number == phone).first() is not None


ROUTES = [
    ("POST /contacts/bulk-sync", "POST", _bulk_sync,
        lambda r, db, c, s: s == [("bulk", [c["cid"]])],
        lambda db, c, s: s == []),
    ("DELETE /contacts/bulk", "DELETE", _bulk_delete,
        lambda r, db, c, s: not _contact_exists(db, c["cid"]),
        lambda db, c, s: _contact_exists(db, c["cid"])),
    ("POST /contacts/", "POST", _create,
        lambda r, db, c, s: r.json()["phone_number"] == c["phone"] and _created(db, c["phone"]),
        lambda db, c, s: not _created(db, c["phone"])),
    ("DELETE /contacts/{id}", "DELETE", _delete,
        lambda r, db, c, s: not _contact_exists(db, c["cid"]),
        lambda db, c, s: _contact_exists(db, c["cid"])),
    ("POST /contacts/{id}/sync", "POST", _sync_one,
        lambda r, db, c, s: s == [("one", c["cid"])] and r.json()["id"] == c["cid"],
        lambda db, c, s: s == []),
    ("PUT /contacts/{id}/market-segments", "PUT", _segments,
        lambda r, db, c, s: r.json() == {"codes": [c["code"]]} and _segment_codes(db, c["cid"]) == {c["code"]},
        lambda db, c, s: _segment_codes(db, c["cid"]) == set()),
    ("PUT /contacts/{id}/attachment-types", "PUT", _attachment_types,
        lambda r, db, c, s: _contact_exists(db, c["cid"]),
        lambda db, c, s: _contact_exists(db, c["cid"])),
]

ROUTE_IDS = [r[0] for r in ROUTES]


@pytest.mark.parametrize("label,method,build,_succeeded,unchanged", ROUTES, ids=ROUTE_IDS)
def test_write_route_403_without_contacts_edit(api, db, label, method, build, _succeeded, unchanged):
    client, allow, sync_calls = api
    # Holding view alone is exactly the gap: it opens the page, not the writes.
    allow.add("user_management.contacts.view")
    url, body, ctx = build(db)

    resp = client.request(method, url, json=body)

    assert resp.status_code == 403, f"{label}: expected 403, got {resp.status_code} {resp.text}"
    assert unchanged(db, ctx, sync_calls), f"{label}: the handler ran despite the 403"


@pytest.mark.parametrize("label,method,build,succeeded,_unchanged", ROUTES, ids=ROUTE_IDS)
def test_write_route_succeeds_with_contacts_edit(api, db, label, method, build, succeeded, _unchanged):
    client, allow, sync_calls = api
    allow.add(CONTACTS_EDIT)
    url, body, ctx = build(db)

    resp = client.request(method, url, json=body)

    assert resp.status_code < 300, f"{label}: expected success, got {resp.status_code} {resp.text}"
    assert succeeded(resp, db, ctx, sync_calls), f"{label}: succeeded without doing its work"


def test_attachment_types_put_403_leaves_grants_alone(api, db, monkeypatch):
    """The attachment-types table case above cannot see a write on its own (an
    empty set is the baseline), so pin the handler body directly."""
    from app.services import contact_attachment_access

    calls = []
    monkeypatch.setattr(contact_attachment_access, "set_grants", lambda *a, **k: calls.append(a) or {})
    client, allow, _ = api
    cid = _seed_contact(db)

    resp = client.put(f"{BASE}/{cid}/attachment-types", json={"attachment_type_ids": []})

    assert resp.status_code == 403
    assert calls == []


# ------------------------------------------------------------- structural


# Non-GET routes that are gated in the handler body instead of by dependency.
# Each entry needs its reason.
_BODY_GATED_WRITES = {
    # Superadmin-only via `_require_superadmin(db, current_user)` in the handler.
    ("PUT", "/{contact_id}/companies"),
}


def _has_permission_dependency(route) -> bool:
    """True when a `require_permission(...)` dependency sits anywhere in the
    route's Dependant tree (same walk as the read-gates structural sweep)."""
    seen: set[int] = set()

    def _walk(dependant) -> bool:
        if id(dependant) in seen:
            return False
        seen.add(id(dependant))
        qualname = getattr(getattr(dependant, "call", None), "__qualname__", "") or ""
        if "require_permission" in qualname or "require_any_permission" in qualname:
            return True
        return any(_walk(sub) for sub in getattr(dependant, "dependencies", []) or [])

    return _walk(route.dependant)


def test_every_contacts_write_route_is_permission_gated():
    write_routes = [
        r for r in contacts_module.router.routes
        if set(getattr(r, "methods", set())) - {"GET", "HEAD"}
    ]
    # Not vacuous: the router carries at least the seven routes this lane gated
    # plus the eleven that were already gated.
    assert len(write_routes) >= 18

    ungated = []
    for route in write_routes:
        methods = set(getattr(route, "methods", set())) - {"GET", "HEAD"}
        for method in methods:
            if (method, route.path) in _BODY_GATED_WRITES:
                continue
            if not _has_permission_dependency(route):
                ungated.append(f"{method} {route.path}")
    assert ungated == [], f"contact write routes with no permission dependency: {sorted(ungated)}"
