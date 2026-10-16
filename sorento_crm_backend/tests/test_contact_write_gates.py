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
* UAC2 - per route, a caller with `.edit` succeeds. The four DB-only routes run
  for real against a scratch schema; the two sync routes stub the
  `ContactService` method that would call Respond.io (tests never call it), and
  attachment-types records its `set_grants` call.
* `PUT /{id}/companies` is gated in the handler body (superadmin), so it is
  pinned behaviourally: `.edit` alone gets 403, a superadmin succeeds.
* UAC3 - structural: every non-GET route on the three routers mounted at
  /contacts is gated by plain `require_permission` on `contacts.edit` (or the
  named portal-link slug, or the body-gated companies PUT), so a new write that
  is ungated, gated on `.view`, or opened to X-API-Key fails here.

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
from app.services import contact_attachment_access
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
    plus a record of every Respond.io sync (and attachment-grant write) the
    routes attempted."""
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

    # An empty grant set is the baseline, so a real attachment-types PUT leaves
    # no visible trace either way; record the call instead.
    def _fake_set_grants(_db, contact_id, attachment_type_ids, actor=None):
        sync_calls.append(("grants", contact_id))
        return {"contact_id": contact_id, "attachment_type_ids": list(attachment_type_ids)}

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
    monkeypatch.setattr(contact_attachment_access, "set_grants", _fake_set_grants)
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
        lambda r, db, c, s: s == [("grants", c["cid"])],
        lambda db, c, s: s == []),
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


def test_companies_put_403_for_non_superadmin_holding_contacts_edit(api, db, monkeypatch):
    """`PUT /{id}/companies` is exempt from the structural sweep because it is
    gated in the handler body. Pin that body gate: `.edit` alone is not enough,
    and the company grants are never rewritten."""
    calls = []
    monkeypatch.setattr(
        ContactService, "set_contact_companies", lambda self, cid, ids: calls.append(cid) or {}
    )
    client, allow, _ = api
    allow.add(CONTACTS_EDIT)
    cid = _seed_contact(db)

    resp = client.put(f"{BASE}/{cid}/companies", json={"company_ids": []})

    assert resp.status_code == 403, resp.text
    assert calls == []


def test_companies_put_succeeds_for_superadmin(api, db, monkeypatch):
    from app.services.user_service import UserPermissionService

    calls = []
    monkeypatch.setattr(
        ContactService, "set_contact_companies", lambda self, cid, ids: calls.append(cid) or {}
    )
    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, uid: {UserPermissionService.SUPERADMIN_ROLE_SLUG}
    )
    client, _, _ = api
    cid = _seed_contact(db)

    resp = client.put(f"{BASE}/{cid}/companies", json={"company_ids": []})

    assert resp.status_code == 200, resp.text
    assert calls == [cid]


# ------------------------------------------------------------- structural


# Every write on the routers mounted at /contacts must be gated on exactly this
# slug, except the entries below, each with its reason.
_EXPECTED_SLUG = CONTACTS_EDIT
_OTHER_SLUG_WRITES = {
    # Portal links have their own slug (migration 160): generating or sending a
    # link is a separate authority from editing the contact.
    ("POST", "/{contact_id}/portal-link"): "user_management.contacts.portal_link",
    ("POST", "/{contact_id}/portal-link/send"): "user_management.contacts.portal_link",
}
# Gated in the handler body instead of by dependency, pinned by the
# test_companies_put_* tests above.
_BODY_GATED_WRITES = {
    # Superadmin-only via `_require_superadmin(db, current_user)` in the handler.
    ("PUT", "/{contact_id}/companies"),
}


def _permission_slugs(route) -> set[str]:
    """The slug of every plain `require_permission(...)` dependency in the
    route's Dependant tree. The `_with_api_key` variant is deliberately NOT
    matched: it opens the route to X-API-Key callers, which these operator
    writes must not be."""
    slugs: set[str] = set()
    seen: set[int] = set()

    def _walk(dependant) -> None:
        if id(dependant) in seen:
            return
        seen.add(id(dependant))
        call = getattr(dependant, "call", None)
        if (getattr(call, "__qualname__", "") or "") == "require_permission.<locals>._require":
            for cell in call.__closure__ or ():
                if isinstance(cell.cell_contents, str):
                    slugs.add(cell.cell_contents)
        for sub in getattr(dependant, "dependencies", []) or []:
            _walk(sub)

    _walk(route.dependant)
    return slugs


def _contacts_write_routes():
    from app.api.v1.user_management import contact_media_access, contact_portal_forms

    for module in (contacts_module, contact_media_access, contact_portal_forms):
        for route in module.router.routes:
            for method in set(getattr(route, "methods", set())) - {"GET", "HEAD"}:
                yield method, route


def test_every_contacts_write_route_is_gated_on_contacts_edit():
    writes = list(_contacts_write_routes())
    # Not vacuous: the seven routes this lane gated plus the ones already gated.
    assert len(writes) >= 20

    wrong = []
    for method, route in writes:
        key = (method, route.path)
        if key in _BODY_GATED_WRITES:
            continue
        expected = _OTHER_SLUG_WRITES.get(key, _EXPECTED_SLUG)
        if expected not in _permission_slugs(route):
            wrong.append(f"{method} {route.path} -> {sorted(_permission_slugs(route)) or 'no permission'}")
    assert wrong == [], f"contact write routes not gated on the expected slug: {sorted(wrong)}"
