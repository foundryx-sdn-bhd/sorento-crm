"""Lane CONTACT-TAGS, slice S1: tag catalog, tagging routes, list tags + filters, schema.

UAC ``documentation/plans/contacts/contact-tags-16oct-acceptance-criteria.md`` (T1, T2 without
apply, T4.1, T4.2, T4.4, T6). Contract ``contact-tags-s1-contract.md``. Red tests written before
the implementation. Postgres only (``blank_session``); every row is seeded here. Tags are created
and assigned through the HTTP routes (the contract's own surface) and read back through raw SQL,
so no test imports a model that does not exist yet.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import ContactAccessType, RespondContact
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
UM = "/api/v1/user-management"
TAGS = f"{UM}/contact-tags"
CONTACTS = f"{UM}/contacts"
BULK = f"{CONTACTS}/bulk-tags"
VIEW = "user_management.contacts.view"
EDIT = "user_management.contacts.edit"


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


def _tag(client, name=None, **extra) -> dict:
    name = name or f"ZZT {unique_code('tag')}"
    resp = client.post(TAGS, json={"name": name, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _contact(db, tag: str = "c") -> RespondContact:
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT {tag} {unique_code('c')}",
        chatbot_profile={"language": "en"},
    )
    db.add(row)
    db.flush()
    return row


def _set_tags(client, contact, tags) -> None:
    resp = client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [t["id"] for t in tags]})
    assert resp.status_code == 200, resp.text


def _held_names(db, contact_id: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT t.name FROM respond_contact_tags rct JOIN contact_tags t ON t.id = rct.tag_id "
            "WHERE rct.contact_id = :c ORDER BY t.name"
        ),
        {"c": contact_id},
    ).all()
    return [r[0] for r in rows]


def _bulk(client, contact_ids, add=(), remove=(), dry_run=True):
    return client.post(
        BULK,
        json={
            "contact_ids": list(contact_ids),
            "add_tag_ids": [t["id"] if isinstance(t, dict) else t for t in add],
            "remove_tag_ids": [t["id"] if isinstance(t, dict) else t for t in remove],
            "dry_run": dry_run,
        },
    )


# ---------------------------------------------------------------- T1 catalog


def test_t1_1_create_trims_name_and_returns_defaults(client):
    resp = client.post(TAGS, json={"name": "  Dealer  ", "description": "Our dealers"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Dealer"
    assert body["description"] == "Our dealers"
    assert body["is_active"] is True
    assert body["sort_order"] == 0
    assert body["settings"] == {}
    assert body["contact_count"] == 0
    assert body["id"]


def test_t1_3_blank_name_is_422(client):
    assert client.post(TAGS, json={"name": "   "}).status_code == 422


def test_t1_3_duplicate_case_insensitive_is_409_naming_the_clash(client):
    _tag(client, "Dealer")
    resp = client.post(TAGS, json={"name": "dealer"})
    assert resp.status_code == 409
    assert "Dealer" in resp.text


def test_t1_3_patch_to_another_tags_name_is_409(client):
    _tag(client, "Dealer")
    other = _tag(client, "Office")
    resp = client.patch(f"{TAGS}/{other['id']}", json={"name": " DEALER "})
    assert resp.status_code == 409


def test_t1_1_list_order_count_search_active_and_limit(client, db):
    b = _tag(client, "ZZT-B", sort_order=2)
    a = _tag(client, "ZZT-A", sort_order=2)
    c = _tag(client, "ZZT-C", sort_order=1, is_active=False)
    holder = _contact(db)
    _set_tags(client, holder, [a])

    resp = client.get(TAGS, params={"limit": 1000, "search": "ZZT-"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert [r["name"] for r in rows] == ["ZZT-C", "ZZT-A", "ZZT-B"]  # sort_order, then name
    counts = {r["name"]: r["contact_count"] for r in rows}
    assert counts == {"ZZT-C": 0, "ZZT-A": 1, "ZZT-B": 0}

    only_b = client.get(TAGS, params={"search": "zzt-b"}).json()["data"]
    assert [r["id"] for r in only_b] == [b["id"]]

    inactive = client.get(TAGS, params={"is_active": "false", "search": "ZZT-"}).json()["data"]
    assert [r["id"] for r in inactive] == [c["id"]]


def test_t1_1_get_one_and_404(client):
    tag = _tag(client)
    assert client.get(f"{TAGS}/{tag['id']}").json()["id"] == tag["id"]
    assert client.get(f"{TAGS}/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- gates


def test_t1_2_view_only_reads_but_cannot_write(client, db, perms):
    tag = _tag(client)
    contact = _contact(db)
    perms["granted"] = {VIEW}
    assert client.get(TAGS).status_code == 200
    assert client.get(f"{TAGS}/{tag['id']}").status_code == 200
    assert client.post(TAGS, json={"name": "Nope"}).status_code == 403
    assert client.patch(f"{TAGS}/{tag['id']}", json={"name": "Nope"}).status_code == 403
    assert client.delete(f"{TAGS}/{tag['id']}").status_code == 403
    assert client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [tag["id"]]}).status_code == 403
    assert _bulk(client, [contact.id], add=[tag], dry_run=False).status_code == 403
    perms["granted"] = {VIEW, EDIT}
    assert _held_names(db, contact.id) == []


def test_t1_2_without_view_reads_are_403(client, perms):
    perms["granted"] = set()
    assert client.get(TAGS).status_code == 403
    assert client.get(f"{TAGS}/{uuid.uuid4()}").status_code == 403


# ---------------------------------------------------------------- T1.5 delete


def test_t1_5_delete_removes_tag_and_links_only(client, db):
    keep = _tag(client, "ZZT-keep")
    doomed = _tag(client, "ZZT-doomed")
    atype = ContactAccessType(code=unique_code("zat"), name="ZZT type", is_active=True, sort_order=1)
    db.add(atype)
    db.flush()
    contact = _contact(db)
    contact.chatbot_stock_allowed = False
    contact.access_types = [atype]
    db.flush()
    _set_tags(client, contact, [keep, doomed])

    resp = client.delete(f"{TAGS}/{doomed['id']}")
    assert resp.status_code == 204, resp.text
    assert client.get(f"{TAGS}/{doomed['id']}").status_code == 404
    assert (
        db.execute(
            text("SELECT count(*) FROM respond_contact_tags WHERE tag_id = :t"), {"t": doomed["id"]}
        ).scalar()
        == 0
    )
    assert _held_names(db, contact.id) == ["ZZT-keep"]
    db.expire_all()
    fresh = db.get(RespondContact, contact.id)
    assert fresh.chatbot_stock_allowed is False
    assert [t.code for t in fresh.access_types] == [atype.code]


def test_d7_contact_tag_delete_is_a_registered_deferred_action(client, db):
    from app.services import record_actions  # noqa: F401  (registers the actions)
    from app.services.form_action_grace import WINDOW_DESTRUCTIVE
    from app.services.form_action_registry import get_action

    action = get_action("contact_tag.delete")
    assert action is not None
    assert "contact_tag" in action.entity_types
    assert action.permission == EDIT
    assert action.window == WINDOW_DESTRUCTIVE == get_action("market_segment.delete").window

    tag = _tag(client)
    action.execute(db, {"entity_id": tag["id"]})
    assert client.get(f"{TAGS}/{tag['id']}").status_code == 404


# ---------------------------------------------------------------- T2 tagging


def test_t2_6_put_replaces_the_set_and_rejects_unknown_ids(client, db):
    a, b = _tag(client, "ZZT-a"), _tag(client, "ZZT-b")
    contact = _contact(db)
    resp = client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [a["id"], b["id"]]})
    assert resp.status_code == 200, resp.text
    assert sorted(t["name"] for t in resp.json()["tags"]) == ["ZZT-a", "ZZT-b"]

    resp = client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [b["id"]]})
    assert [t["id"] for t in resp.json()["tags"]] == [b["id"]]
    assert _held_names(db, contact.id) == ["ZZT-b"]

    ghost = str(uuid.uuid4())
    resp = client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [ghost]})
    assert resp.status_code == 422
    assert ghost in resp.text
    assert _held_names(db, contact.id) == ["ZZT-b"]


def test_t2_2_t2_3_dry_run_writes_nothing_and_preview_equals_apply(client, db):
    a, b = _tag(client, "ZZT-a"), _tag(client, "ZZT-b")
    c1, c2, c3 = _contact(db, "one"), _contact(db, "two"), _contact(db, "three")
    _set_tags(client, c1, [b])
    _set_tags(client, c2, [a])
    ids = [c3.id, c1.id, c2.id]

    preview = _bulk(client, ids, add=[a], remove=[b], dry_run=True)
    assert preview.status_code == 200, preview.text
    pbody = preview.json()
    assert pbody["dry_run"] is True
    assert [r["contact_id"] for r in pbody["results"]] == ids
    for r in pbody["results"]:
        assert {"label", "status", "tags_before", "tags_after", "error"} <= set(r)
    assert _held_names(db, c3.id) == [] and _held_names(db, c1.id) == ["ZZT-b"]
    assert _held_names(db, c2.id) == ["ZZT-a"]

    applied = _bulk(client, ids, add=[a], remove=[b], dry_run=False).json()
    assert applied["dry_run"] is False
    assert applied["counts"] == pbody["counts"] == {"changed": 2, "unchanged": 1, "failed": 0}
    for r, c in zip(applied["results"], [c3, c1, c2]):
        assert sorted(_held_names(db, c.id)) == sorted(r["tags_after"])
    pv = [(r["contact_id"], r["status"], r["tags_before"], r["tags_after"]) for r in pbody["results"]]
    ap = [(r["contact_id"], r["status"], r["tags_before"], r["tags_after"]) for r in applied["results"]]
    assert pv == ap


def test_t2_5_noop_rows_are_unchanged_without_error(client, db):
    a, b = _tag(client, "ZZT-a"), _tag(client, "ZZT-b")
    contact = _contact(db)
    _set_tags(client, contact, [a])
    body = _bulk(client, [contact.id], add=[a], remove=[b], dry_run=False).json()
    (row,) = body["results"]
    assert row["status"] == "unchanged"
    assert row["error"] is None
    assert body["counts"] == {"changed": 0, "unchanged": 1, "failed": 0}
    assert _held_names(db, contact.id) == ["ZZT-a"]


def test_t2_5_same_tag_in_add_and_remove_is_422(client, db):
    a = _tag(client)
    contact = _contact(db)
    assert _bulk(client, [contact.id], add=[a], remove=[a]).status_code == 422


def test_t2_1_unknown_contact_fails_its_row_others_apply(client, db):
    a = _tag(client, "ZZT-a")
    contact = _contact(db)
    ghost = str(uuid.uuid4())
    body = _bulk(client, [ghost, contact.id], add=[a], dry_run=False).json()
    assert [r["contact_id"] for r in body["results"]] == [ghost, contact.id]
    assert body["results"][0]["status"] == "failed"
    assert "contact not found" in body["results"][0]["error"].lower()
    assert body["results"][1]["status"] == "changed"
    assert body["counts"] == {"changed": 1, "unchanged": 0, "failed": 1}
    assert _held_names(db, contact.id) == ["ZZT-a"]


def test_t2_1_request_validation_422(client, db):
    a = _tag(client)
    contact = _contact(db)
    assert _bulk(client, [], add=[a]).status_code == 422
    assert _bulk(client, [str(uuid.uuid4()) for _ in range(501)], add=[a]).status_code == 422
    assert _bulk(client, [contact.id], add=[str(uuid.uuid4())]).status_code == 422
    assert _bulk(client, [contact.id], remove=[str(uuid.uuid4())]).status_code == 422


def test_t2_4_removing_a_tag_leaves_stored_settings_fields_alone(client, db):
    a = _tag(client, "ZZT-a")
    atype = ContactAccessType(code=unique_code("zat"), name="ZZT type", is_active=True, sort_order=1)
    db.add(atype)
    db.flush()
    contact = _contact(db)
    contact.chatbot_allowed = False
    contact.chatbot_stock_allowed = False
    contact.access_types = [atype]
    db.flush()
    _set_tags(client, contact, [a])

    assert _bulk(client, [contact.id], remove=[a], dry_run=False).status_code == 200
    assert _held_names(db, contact.id) == []
    db.expire_all()
    fresh = db.get(RespondContact, contact.id)
    assert fresh.chatbot_allowed is False
    assert fresh.chatbot_stock_allowed is False
    assert [t.code for t in fresh.access_types] == [atype.code]


# ---------------------------------------------------------------- T4 contacts list


def test_t4_1_list_and_detail_rows_carry_ordered_tags(client, db):
    late = _tag(client, "ZZT-a-late", sort_order=5)
    first = _tag(client, "ZZT-z-first", sort_order=1)
    contact = _contact(db)
    _set_tags(client, contact, [late, first])
    want = [{"id": first["id"], "name": "ZZT-z-first"}, {"id": late["id"], "name": "ZZT-a-late"}]

    rows = client.get(CONTACTS, params={"limit": 1000, "tag": late["id"]}).json()["data"]
    (row,) = [r for r in rows if r["id"] == contact.id]
    assert row["tags"] == want

    detail = client.get(f"{CONTACTS}/{contact.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["tags"] == want


def test_t4_2_tag_filters(client, db):
    a, b = _tag(client, "ZZT-a"), _tag(client, "ZZT-b")
    atype = ContactAccessType(code=unique_code("zat"), name="ZZT type", is_active=True, sort_order=1)
    db.add(atype)
    db.flush()
    ha, hb, hab, none = (_contact(db, n) for n in ("ha", "hb", "hab", "none"))
    _set_tags(client, ha, [a])
    _set_tags(client, hb, [b])
    _set_tags(client, hab, [a, b])
    ha.access_types = [atype]
    db.flush()

    def ids(**params):
        resp = client.get(CONTACTS, params={"limit": 1000, **params})
        assert resp.status_code == 200, resp.text
        return {r["id"] for r in resp.json()["data"]}

    mine = {ha.id, hb.id, hab.id, none.id}
    assert ids(tag=a["id"]) & mine == {ha.id, hab.id}
    assert ids(tag=[a["id"], b["id"]]) & mine == {ha.id, hb.id, hab.id}
    assert ids(tags="none") & mine == {none.id}
    assert ids(tag=a["id"], access_type=atype.code) & mine == {ha.id}


# ---------------------------------------------------------------- T1.6 no auto-assignment


def test_t1_6_created_contact_has_no_tags(db):
    from app.schemas.user import RespondContactCreate
    from app.services.contact_service import ContactService

    contact = ContactService(db).create_contact(
        RespondContactCreate(phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}", name="ZZT created")
    )
    count = db.execute(
        text("SELECT count(*) FROM respond_contact_tags WHERE contact_id = :c"), {"c": contact.id}
    ).scalar()
    assert count == 0


# ---------------------------------------------------------------- T6 schema


def test_t6_1_tables_columns_and_cascade(db):
    insp = inspect(db.connection())
    tag_cols = {c["name"] for c in insp.get_columns("contact_tags")}
    assert {
        "id", "name", "description", "is_active", "sort_order", "settings",
        "created_at", "updated_at", "created_by", "updated_by",
    } <= tag_cols
    link_cols = {c["name"] for c in insp.get_columns("respond_contact_tags")}
    assert {"contact_id", "tag_id", "created_at", "created_by"} <= link_cols

    contact = _contact(db)
    tag_id = str(uuid.uuid4())
    db.execute(text("INSERT INTO contact_tags (id, name) VALUES (:i, :n)"), {"i": tag_id, "n": unique_code("t")})
    db.execute(
        text("INSERT INTO respond_contact_tags (contact_id, tag_id) VALUES (:c, :t)"),
        {"c": contact.id, "t": tag_id},
    )
    db.execute(text("DELETE FROM respond_contacts WHERE id = :c"), {"c": contact.id})
    assert db.execute(
        text("SELECT count(*) FROM respond_contact_tags WHERE contact_id = :c"), {"c": contact.id}
    ).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM contact_tags WHERE id = :t"), {"t": tag_id}).scalar() == 1
