"""Lane CONTACT-REGION: respond_contacts.regions (West / East Malaysia). Red tests.

UAC ``documentation/plans/chatbot/contact-region-acceptance-criteria.md`` AC-CR-1..13, 18..21.
Postgres only (``blank_session``); every row is seeded here. Reads of the stored value go
through raw SQL so a missing column fails as a missing column, and API assertions go through
the HTTP response (``response_model`` drops undeclared fields).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact
from app.models.audit import AuditLog
from app.models.base import set_company_scope
from app.services import eta_policy
from app.services.audit_service import register_audit_listeners
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
VIEW = "user_management.contacts.view"
EDIT = "user_management.contacts.edit"
BASE = "/api/v1/user-management/contacts"


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()
    register_audit_listeners()
    yield


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({SORENTO}))
        yield session


@pytest.fixture
def perms():
    return {"granted": {VIEW, EDIT}}


@pytest.fixture
def client(db, perms, monkeypatch):
    from app.models.user import User

    actor = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(actor)
    db.flush()
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({SORENTO})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in perms["granted"],
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- seeding


def _contact(db, tag: str = "c", regions: list[str] | None = None) -> RespondContact:
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT {tag} {unique_code('c')}",
        chatbot_profile={"language": "en"},
    )
    db.add(row)
    db.flush()
    if regions is not None:
        _set_raw(db, row.id, regions)
    return row


def _set_raw(db, contact_id: str, regions: list[str]) -> None:
    db.execute(
        text("update respond_contacts set regions = cast(:r as text[]) where id = :i"),
        {"r": "{" + ",".join(regions) + "}", "i": contact_id},
    )
    db.flush()


def _stored(db, contact_id: str) -> list[str]:
    return list(
        db.execute(text("select regions from respond_contacts where id = :i"), {"i": contact_id}).scalar_one()
    )


def _put(client, contact_id: str, body: dict):
    return client.put(f"{BASE}/{contact_id}/chatbot", json=body)


# ---------------------------------------------------------------- schema


def test_ac_cr_1_default_is_west(db):
    c = _contact(db)
    assert _stored(db, c.id) == ["west"]


def test_ac_cr_1_model_declares_regions(db):
    assert hasattr(RespondContact, "regions"), "RespondContact.regions is missing"
    c = _contact(db)
    db.refresh(c)
    assert list(c.regions) == ["west"]


def test_ac_cr_2_check_rejects_empty(db):
    c = _contact(db)
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        db.execute(text("update respond_contacts set regions = '{}' where id = :i"), {"i": c.id})
    assert "ck_respond_contacts_regions" in str(exc.value)


def test_ac_cr_2_check_rejects_unknown_code(db):
    c = _contact(db)
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        db.execute(text("update respond_contacts set regions = '{north}' where id = :i"), {"i": c.id})
    assert "ck_respond_contacts_regions" in str(exc.value)


# ---------------------------------------------------------------- API


def test_ac_cr_3_get_carries_regions_default_west(client, db):
    c = _contact(db)
    resp = client.get(f"{BASE}/{c.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["regions"] == ["west"]


def test_ac_cr_4_put_stores_both_and_get_shows_it(client, db):
    c = _contact(db)
    resp = _put(client, c.id, {"regions": ["west", "east"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["regions"] == ["west", "east"]
    assert _stored(db, c.id) == ["west", "east"]
    assert client.get(f"{BASE}/{c.id}").json()["regions"] == ["west", "east"]


def test_ac_cr_5_absent_leaves_alone(client, db):
    c = _contact(db)
    _set_raw(db, c.id, ["east"])
    resp = _put(client, c.id, {"notify_salesman": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["regions"] == ["east"]
    assert _stored(db, c.id) == ["east"]


@pytest.mark.parametrize("bad", [[], ["north"], None, ["west", "north"]])
def test_ac_cr_6_invalid_is_422_and_row_unchanged(client, db, bad):
    c = _contact(db)
    _set_raw(db, c.id, ["east"])
    resp = _put(client, c.id, {"regions": bad})
    assert resp.status_code == 422, resp.text
    assert _stored(db, c.id) == ["east"]


def test_ac_cr_7_duplicates_collapse_to_canonical_order(client, db):
    c = _contact(db)
    resp = _put(client, c.id, {"regions": ["east", "west", "east"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["regions"] == ["west", "east"]
    assert _stored(db, c.id) == ["west", "east"]


def test_ac_cr_8_without_edit_is_403_and_unchanged(client, db, perms):
    c = _contact(db)
    perms["granted"] = {VIEW}
    resp = _put(client, c.id, {"regions": ["east"]})
    assert resp.status_code == 403, resp.text
    assert _stored(db, c.id) == ["west"]


def test_ac_cr_9_change_writes_audit_row_naming_old_and_new(client, db):
    c = _contact(db)
    resp = _put(client, c.id, {"regions": ["west", "east"]})
    assert resp.status_code == 200, resp.text
    db.flush()
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == c.id, AuditLog.action == "UPDATE")
        .all()
    )
    hit = [
        r
        for r in rows
        if "regions" in (r.new_values or {}) and "regions" in (r.old_values or {})
    ]
    assert hit, f"no UPDATE audit row names regions; rows: {[(r.old_values, r.new_values) for r in rows]}"
    assert list(hit[-1].old_values["regions"]) == ["west"]
    assert list(hit[-1].new_values["regions"]) == ["west", "east"]


# ---------------------------------------------------------------- chatbot effect


def test_ac_cr_10_contact_regions_returns_stored_set(db):
    a = _contact(db, "a")
    b = _contact(db, "b")
    _set_raw(db, a.id, ["east"])
    _set_raw(db, b.id, ["west", "east"])
    assert eta_policy.contact_regions(db, a.id) == frozenset({"east"})
    assert eta_policy.contact_regions(db, b.id) == frozenset({"west", "east"})


def test_ac_cr_10_missing_or_none_id_is_west(db):
    assert eta_policy.contact_regions(db, str(uuid.uuid4())) == frozenset({"west"})
    assert eta_policy.contact_regions(db, None) == frozenset({"west"})


def test_ac_cr_11_stored_west_sees_west_only(db):
    c = _contact(db)
    assert eta_policy.rules_for_contact(db, c.id).regions == frozenset({"west"})


def test_ac_cr_12_stored_east_is_literal(db):
    c = _contact(db)
    _set_raw(db, c.id, ["east"])
    assert eta_policy.rules_for_contact(db, c.id).regions == frozenset({"east"})


def test_ac_cr_12_stored_both_sees_both(db):
    c = _contact(db)
    _set_raw(db, c.id, ["west", "east"])
    assert eta_policy.rules_for_contact(db, c.id).regions == frozenset({"east", "west"})


@pytest.mark.parametrize(
    "held, expected",
    [
        (["east"], {"east"}),
        (["west"], {"west"}),
        (["west", "east"], {"west", "east"}),
        ([], {"west"}),
        (None, {"west"}),
        (["north"], {"west"}),
        (["east", "north"], {"east"}),
    ],
)
def test_ac_cr_21_visible_regions_is_literal(held, expected):
    assert eta_policy.visible_regions(held) == frozenset(expected)


def test_ac_cr_21_rules_follow_visible_regions_only(db, monkeypatch):
    c = _contact(db)
    _set_raw(db, c.id, ["east"])
    monkeypatch.setattr(eta_policy, "visible_regions", lambda held: frozenset(held))
    assert eta_policy.rules_for_contact(db, c.id).regions == frozenset({"east"})


# ---------------------------------------------------------------- copy access

BULK = f"{BASE}/bulk-copy-access"


def _copy(client, source, target, dry_run):
    return client.post(
        BULK,
        json={"source_contact_id": source.id, "target_contact_ids": [target.id], "dry_run": dry_run},
    )


def test_ac_cr_18_copy_applies_regions(client, db):
    source = _contact(db, "src")
    target = _contact(db, "tgt")
    _set_raw(db, source.id, ["west", "east"])
    resp = _copy(client, source, target, dry_run=False)
    assert resp.status_code == 200, resp.text
    assert _stored(db, target.id) == ["west", "east"]


def test_ac_cr_18_diff_lists_regions_change_with_labels(client, db):
    source = _contact(db, "src")
    target = _contact(db, "tgt")
    _set_raw(db, source.id, ["west", "east"])
    resp = _copy(client, source, target, dry_run=True)
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["results"]
    regions = [c for c in row["changes"] if c["label"] == "Regions"]
    assert regions, f"no Regions change in {row['changes']}"
    blob = str(regions[0])
    assert "East Malaysia" in blob and "West Malaysia" in blob
    assert _stored(db, target.id) == ["west"]


def test_ac_cr_18_equal_regions_no_change(client, db):
    source = _contact(db, "src")
    target = _contact(db, "tgt")
    _set_raw(db, source.id, ["east"])
    _set_raw(db, target.id, ["east"])
    resp = _copy(client, source, target, dry_run=True)
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["results"]
    assert [c for c in row["changes"] if c["label"] == "Regions"] == []


# ---------------------------------------------------------------- list


def _ids(resp):
    return {r["id"] for r in resp.json()["data"]}


def test_ac_cr_20_region_filter_east(client, db):
    w = _contact(db, "w")
    e = _contact(db, "e")
    both = _contact(db, "b")
    _set_raw(db, e.id, ["east"])
    _set_raw(db, both.id, ["west", "east"])
    resp = client.get(f"{BASE}/", params={"region": "east", "limit": 1000})
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert e.id in ids and both.id in ids and w.id not in ids


def test_ac_cr_20_region_filter_west(client, db):
    w = _contact(db, "w")
    e = _contact(db, "e")
    _set_raw(db, e.id, ["east"])
    resp = client.get(f"{BASE}/", params={"region": "west", "limit": 1000})
    assert resp.status_code == 200, resp.text
    ids = _ids(resp)
    assert w.id in ids and e.id not in ids


def test_ac_cr_20_absent_filter_returns_all(client, db):
    w = _contact(db, "w")
    e = _contact(db, "e")
    _set_raw(db, e.id, ["east"])
    resp = client.get(f"{BASE}/", params={"limit": 1000})
    assert resp.status_code == 200, resp.text
    assert {w.id, e.id} <= _ids(resp)


def test_ac_cr_20_unknown_region_is_422(client, db):
    resp = client.get(f"{BASE}/", params={"region": "north"})
    assert resp.status_code == 422, resp.text


def test_ac_cr_19_list_rows_carry_regions(client, db):
    c = _contact(db, "b")
    _set_raw(db, c.id, ["west", "east"])
    resp = client.get(f"{BASE}/", params={"limit": 1000})
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["data"] if r["id"] == c.id)
    assert row["regions"] == ["west", "east"]
