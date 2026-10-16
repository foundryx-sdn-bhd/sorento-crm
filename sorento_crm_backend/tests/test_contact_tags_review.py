"""CONTACT-TAGS review fix round (16 Oct): red tests for the (T) items in
documentation/plans/contacts/contact-tags-review-fixes.md. F1-F6, R1-R3, R5, N1, N2.
Same harness as the S2/S3 files. Never reaches respond.io.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.main import app  # noqa: E402  (first app import)
from app.dependencies import get_current_user
from app.models.access import (
    AccessAgent,
    ContactAgentAccess,
    RespondContactCustomer,
    SpecVisibilityPolicy,
    StockVisibilityPolicy,
)
from app.models.audit import AuditLog
from app.models.company import Company
from app.models.order import Customer
from app.models.price_tag import ContactPortalFormOverride
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.test_contact_tags_s1 import (  # noqa: F401
    CONTACTS, EDIT, TAGS, VIEW, _contact, _set_tags, _tag, client, db, perms,
)
from tests.test_contact_tags_s2 import (  # noqa: F401
    AGENTS_EDIT, STOCK_EDIT, _agent, _atype, _brand, _holder, _item, _read, _stag, _wh, all_perms,
)
from tests.test_contact_tags_s3 import APPLY, _apply, _change, _fresh, _rows  # noqa: F401
from tests._pg_fixture import unique_code

AGENTS_VIEW = "user_management.access_agents.view"
STOCK_VIEW = "inventory.stock.view"
BULK = f"{CONTACTS}/bulk-tags"


def _actor():
    return app.dependency_overrides[get_current_user]()["id"]


def _other_company(db) -> Company:
    row = Company(name=f"ZZT Other Co {uuid.uuid4().hex[:6]}", code=unique_code("zco")[:50])
    db.add(row)
    db.flush()
    return row


def _items(body, section):
    return [i for i in body["items"] if i["section"] == section]


# ---------------------------------------------------------------- F1


def test_f1_catalog_is_built_per_caller(client, db, perms, monkeypatch):
    agent, wh = _agent(db), _wh(db)
    url = f"{TAGS}/settings-catalog"
    perms["granted"] = {VIEW}
    cat = client.get(url).json()
    for absent in ("companies", "agents", "warehouses"):
        assert absent not in cat, absent
    assert agent.name not in json.dumps(cat) and wh.warehouse_name not in json.dumps(cat)

    perms["granted"] = {VIEW, AGENTS_VIEW}
    cat = client.get(url).json()
    assert agent.name in json.dumps(cat["agents"]) and "warehouses" not in cat

    perms["granted"] = {VIEW, STOCK_VIEW}
    cat = client.get(url).json()
    assert wh.warehouse_name in json.dumps(cat["warehouses"]) and "agents" not in cat

    assert "companies" not in cat
    monkeypatch.setattr("app.api.v1.system.companies._is_superadmin", lambda db_, uid: True)
    assert "companies" in client.get(url).json()


def test_f1_brands_follow_the_callers_company_scope(client, db, all_perms):
    other = _other_company(db)
    foreign = _brand(db)
    db.execute(text("UPDATE brands SET company_id = :c WHERE id = :b"), {"c": other.id, "b": foreign.id})
    own = _brand(db)
    cat = client.get(f"{TAGS}/settings-catalog").json()
    names = json.dumps(cat["brands"])
    assert own.brand_name in names and foreign.brand_name not in names
    tag = _tag(client)
    assert client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"brands": [foreign.id]}}).status_code == 422
    assert client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"brands": [own.id]}}).status_code == 200


def test_f1_unreadable_sections_keep_label_and_differs_but_leak_no_names(client, db, perms):
    agent, wh, company = _agent(db), _wh(db), _other_company(db)
    tag = _stag(client, db, {
        "agents": {agent.id: True},
        "stock_visibility": {"mode": "compact", "warehouse_ids": [wh.id],
                             "excluded_warehouse_ids": None, "hide_zero_locations": False},
        "companies": [company.id],
    })
    contact = _holder(client, db, [tag])
    perms["granted"] = {VIEW}
    resp = client.get(f"{CONTACTS}/{contact.id}/tag-settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for name in (agent.name, agent.code, wh.warehouse_name, company.name):
        assert name not in resp.text, name
    generic = {"agents": "Chatbot agents", "stock_visibility": "Stock visibility", "companies": "Companies"}
    for section, label in generic.items():
        found = _items(body, section)
        assert found, section
        for item in found:
            assert item["label"] == label
            assert "differs" in item
            assert item["current"] is None and item["expected"] is None and item["decided_by"] is None

    listed = client.get(CONTACTS, params={"limit": 1000, "tag": tag["id"]})
    for name in (agent.name, wh.warehouse_name, company.name):
        assert name not in listed.text, name

    perms["granted"] = {VIEW, AGENTS_VIEW}
    full = client.get(f"{CONTACTS}/{contact.id}/tag-settings").json()
    assert any(i["current"] is not None or i["expected"] is not None for i in _items(full, "agents"))


# ---------------------------------------------------------------- F2


@pytest.mark.parametrize(
    "media,named",
    [
        ({"image": {"is_allowed": True, "monthly_limit": 100001}}, "monthly_limit"),
        ({"image": {"is_allowed": True, "monthly_limit": -1}}, "monthly_limit"),
        ({"voice": {"is_allowed": True, "max_clip_seconds": 0}}, "max_clip_seconds"),
        ({"voice": {"is_allowed": True, "max_clip_seconds": 3601}}, "max_clip_seconds"),
    ],
)
def test_f2_media_bounds_match_the_contact_route(client, all_perms, media, named):
    tag = _tag(client)
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"media": media}})
    assert resp.status_code == 422, resp.text
    assert named in resp.text


def test_f2_media_bounds_edges_are_accepted(client, all_perms):
    tag = _tag(client)
    ok = {"image": {"is_allowed": True, "monthly_limit": 100000}, "voice": {"is_allowed": True, "max_clip_seconds": 3600}}
    assert client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"media": ok}}).status_code == 200
    zero = {"image": {"is_allowed": True, "monthly_limit": 0}, "voice": {"is_allowed": True, "max_clip_seconds": 1}}
    assert client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"media": zero}}).status_code == 200


# ---------------------------------------------------------------- F3


def test_f3_counts_false_returns_no_differs_count(client, db, all_perms):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}})
    _holder(client, db, [tag], chatbot_stock_allowed=True)
    plain = client.get(TAGS, params={"limit": 1000, "counts": "false"}).json()["data"]
    assert plain and all(r["differs_count"] is None for r in plain)
    counted = {r["id"]: r for r in client.get(TAGS, params={"limit": 1000}).json()["data"]}
    assert counted[tag["id"]]["differs_count"] == 1


# ---------------------------------------------------------------- F4


def _tag_audit(db, contact, tag_name):
    db.expire_all()
    return [r for r in db.query(AuditLog).filter(AuditLog.entity_id == contact.id).all()
            if tag_name in (r.description or "")]


def test_f4_bulk_tags_audits_each_add_and_remove(client, db, all_perms):
    a, b = _tag(client, "ZZT-aud-a"), _tag(client, "ZZT-aud-b")
    contact = _contact(db)
    _set_tags(client, contact, [b])
    base_a = len(_tag_audit(db, contact, "ZZT-aud-a"))
    resp = client.post(BULK, json={"contact_ids": [contact.id], "add_tag_ids": [a["id"]],
                                   "remove_tag_ids": [b["id"]], "dry_run": False})
    assert resp.status_code == 200, resp.text
    added = _tag_audit(db, contact, "ZZT-aud-a")
    assert len(added) - base_a == 1 and added[-1].user_id == _actor()
    removed = [r for r in _tag_audit(db, contact, "ZZT-aud-b") if r.changed_at]
    assert len(removed) >= 2  # the earlier add through PUT and this remove, each its own row

    dry = client.post(BULK, json={"contact_ids": [contact.id], "add_tag_ids": [b["id"]],
                                  "remove_tag_ids": [], "dry_run": True})
    assert dry.status_code == 200
    assert len(_tag_audit(db, contact, "ZZT-aud-b")) == len(removed)


def test_f4_put_tags_audits_adds_and_removals(client, db, all_perms):
    a, b = _tag(client, "ZZT-put-a"), _tag(client, "ZZT-put-b")
    contact = _contact(db)
    assert client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [a["id"]]}).status_code == 200
    assert len(_tag_audit(db, contact, "ZZT-put-a")) == 1
    assert client.put(f"{CONTACTS}/{contact.id}/tags", json={"tag_ids": [b["id"]]}).status_code == 200
    assert len(_tag_audit(db, contact, "ZZT-put-a")) == 2  # add, then remove
    assert len(_tag_audit(db, contact, "ZZT-put-b")) == 1
    assert all(r.user_id == _actor() for r in _tag_audit(db, contact, "ZZT-put-b"))


# ---------------------------------------------------------------- F5


def test_f5_deleting_a_tag_needs_the_gates_of_the_sections_it_sets(client, db, perms):
    agent = _agent(db)
    tag = _stag(client, db, {"agents": {agent.id: True}})
    perms["granted"] = {VIEW, EDIT}
    resp = client.delete(f"{TAGS}/{tag['id']}")
    assert resp.status_code == 403, resp.text
    assert "Chatbot agents" in resp.text
    assert client.get(f"{TAGS}/{tag['id']}").status_code == 200
    perms["granted"] = {VIEW, EDIT, AGENTS_EDIT}
    assert client.delete(f"{TAGS}/{tag['id']}").status_code == 204


def test_f5_the_deferred_delete_refuses_an_actor_without_the_gate(client, db, perms):
    from app.services import record_actions  # noqa: F401
    from app.services.error_handler import AppException
    from app.services.form_action_registry import get_action

    agent = _agent(db)
    tag = _stag(client, db, {"agents": {agent.id: True}})
    perms["granted"] = {VIEW, EDIT}
    with pytest.raises(AppException) as exc:
        get_action("contact_tag.delete").execute(db, {"entity_id": tag["id"], "requested_by_id": _actor()})
    assert exc.value.status_code == 403 and "Chatbot agents" in str(exc.value.message)
    perms["granted"] = {VIEW, EDIT, AGENTS_EDIT}
    get_action("contact_tag.delete").execute(db, {"entity_id": tag["id"], "requested_by_id": _actor()})
    assert client.get(f"{TAGS}/{tag['id']}").status_code == 404


# ---------------------------------------------------------------- F6


def test_f6_agent_fields_are_validated_against_the_gated_field_owner(client, db, all_perms):
    owner = AccessAgent(code="incoming_stock_enquiries", name="ZZT owner agent")
    other = _agent(db)
    db.add(owner)
    db.flush()
    tag = _tag(client)

    def entry(agent_code, field):
        return {"agent_fields": [{"agent_code": agent_code, "resource": "incoming_stock",
                                  "field_key": field, "is_allowed": True}]}

    ok = client.patch(f"{TAGS}/{tag['id']}", json={"settings": entry(owner.code, "estimated_arrival_date")})
    assert ok.status_code == 200, ok.text
    bad_field = client.patch(f"{TAGS}/{tag['id']}", json={"settings": entry(owner.code, "no_such_field")})
    assert bad_field.status_code == 422 and "no_such_field" in bad_field.text
    wrong_owner = client.patch(f"{TAGS}/{tag['id']}", json={"settings": entry(other.code, "estimated_arrival_date")})
    assert wrong_owner.status_code == 422


# ---------------------------------------------------------------- R1


def test_r1_empty_brand_intersection_is_a_conflict_never_written(client, db, all_perms):
    b1, b2 = _brand(db), _brand(db)
    t1 = _stag(client, db, {"brands": [b1.id]}, "ZZT-b1")
    t2 = _stag(client, db, {"brands": [b2.id]}, "ZZT-b2")
    contact = _holder(client, db, [t1, t2])
    contact.brand_ids = [b1.id, b2.id]
    db.flush()
    item = _item(_read(client, contact), "brands")
    assert item["conflict"] is True and item["differs"] is True
    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert any("rand" in c["label"] for c in row["conflicts"]), row["conflicts"]
    assert sorted(_fresh(db, contact.id).brand_ids) == sorted([b1.id, b2.id])


# ---------------------------------------------------------------- R2


def test_r2_stock_apply_keeps_the_inherited_keys_the_tag_does_not_set(client, db, all_perms):
    w1 = _wh(db)
    atype = _atype(db, "stk")
    db.add(StockVisibilityPolicy(access_type_code=atype.code, mode="compact", warehouse_ids=None,
                                 excluded_warehouse_ids=[w1.id], hide_zero_locations=True))
    db.flush()
    tag = _stag(client, db, {"stock_visibility": {"mode": "availability"}})
    contact = _holder(client, db, [tag])
    contact.access_types = [atype]
    db.flush()

    preview = _rows(_apply(client, [contact.id], dry_run=True))["results"][0]
    keys = {c["key"] for c in preview["changes"] if c["section"] == "stock_visibility"}
    assert keys == {"mode"}

    _rows(_apply(client, [contact.id], dry_run=False))
    db.expire_all()
    row = db.query(StockVisibilityPolicy).filter_by(contact_id=contact.id).one()
    assert row.mode == "availability"
    assert list(row.excluded_warehouse_ids or []) == [w1.id]
    assert row.hide_zero_locations is True


def test_r2_spec_inherited_policy_that_already_matches_writes_no_contact_row(client, db, all_perms):
    from app.models.access import MarketSegment

    seg = MarketSegment(code=unique_code("zseg")[:50], name="ZZT seg")
    db.add(seg)
    db.flush()
    db.add(SpecVisibilityPolicy(segment_code=seg.code, excluded_spec_keys=["board_thickness"]))
    tag = _stag(client, db, {"spec_visibility": {"excluded_spec_keys": ["board_thickness"]}})
    contact = _holder(client, db, [tag])
    db.execute(text("INSERT INTO respond_contact_market_segments (contact_id, segment_code) VALUES (:c, :s)"),
               {"c": contact.id, "s": seg.code})
    assert _read(client, contact)["status"] == "matches"
    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert row["status"] == "unchanged"
    assert db.query(SpecVisibilityPolicy).filter_by(contact_id=contact.id).count() == 0


# ---------------------------------------------------------------- R3


def _phone_only(db, agent, contact, allowed=True):
    db.add(ContactAgentAccess(respond_contact_id=None, respond_contact_phone=contact.phone_number,
                              agent_id=agent.id, is_allowed=allowed))
    db.flush()


def _a_rows(db, agent, contact):
    db.expire_all()
    return [r for r in db.query(ContactAgentAccess).filter_by(agent_id=agent.id).all()
            if r.respond_contact_id == contact.id or r.respond_contact_phone == contact.phone_number]


def test_r3_a_phone_only_row_does_not_count_as_current(client, db, all_perms):
    agent = _agent(db)
    deny = _stag(client, db, {"agents": {agent.id: False}})
    contact = _holder(client, db, [deny])
    _phone_only(db, agent, contact)
    assert _read(client, contact)["status"] == "matches"  # runtime ignores phone-only rows: already denied


def test_r3_allow_adopts_the_phone_only_row(client, db, all_perms):
    agent = _agent(db)
    allow = _stag(client, db, {"agents": {agent.id: True}})
    contact = _holder(client, db, [allow])
    _phone_only(db, agent, contact)
    assert _read(client, contact)["status"] == "differs"
    _rows(_apply(client, [contact.id], dry_run=False))
    rows = _a_rows(db, agent, contact)
    assert len(rows) == 1 and rows[0].respond_contact_id == contact.id and rows[0].is_allowed is True
    assert _read(client, contact)["status"] == "matches"


def test_r3_duplicates_collapse_to_one_row_and_reapply_is_unchanged(client, db, all_perms):
    agent = _agent(db)
    deny = _stag(client, db, {"agents": {agent.id: False}})
    contact = _holder(client, db, [deny])
    db.add(ContactAgentAccess(respond_contact_id=contact.id, respond_contact_phone=contact.phone_number,
                              agent_id=agent.id, is_allowed=True))
    _phone_only(db, agent, contact)
    _rows(_apply(client, [contact.id], dry_run=False))
    rows = _a_rows(db, agent, contact)
    assert len(rows) == 1 and rows[0].is_allowed is False
    again = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert again["status"] == "unchanged"


# ---------------------------------------------------------------- R5


def test_r5_agent_only_form_without_a_sales_agent_is_blocked_not_a_difference(client, db, all_perms):
    tag = _stag(client, db, {"portal_forms": {"customer_asks": True}})
    contact = _holder(client, db, [tag])
    body = _read(client, contact)
    item = _item(body, "portal_forms", "customer_asks")
    assert item["blocked_reason"]
    assert item["differs"] is False
    assert body["status"] == "matches"

    first = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert [n["reason"] for n in first["not_applied"]] == [item["blocked_reason"]]
    assert first["status"] != "changed"
    assert db.query(ContactPortalFormOverride).filter_by(contact_id=contact.id).count() == 0

    audit = db.query(AuditLog).filter(AuditLog.entity_id == contact.id).count()
    second = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert second["status"] == "unchanged"
    assert db.query(AuditLog).filter(AuditLog.entity_id == contact.id).count() == audit


# ---------------------------------------------------------------- N1


def test_n1_tag_settings_filter_combines_with_every_other_filter(client, db, all_perms, monkeypatch):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}})
    cust = Customer(customer_code=unique_code("ZZT-C"), customer_name="ZZT cust", company_id=DEFAULT_COMPANY_ID)
    db.add(cust)
    db.flush()

    def linked(contact):
        db.add(RespondContactCustomer(contact_id=contact.id, customer_id=cust.id,
                                      company_id=DEFAULT_COMPANY_ID, source="manual"))
        db.flush()

    both = _holder(client, db, [tag], chatbot_stock_allowed=True)
    differs_only = _holder(client, db, [tag], chatbot_stock_allowed=True)
    customer_only = _holder(client, db, [tag], chatbot_stock_allowed=False)
    linked(both)
    linked(customer_only)
    resp = client.get(CONTACTS, params={"limit": 1000, "customer_id": str(cust.id), "tag_settings": "differs"})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()["data"]}
    assert ids == {both.id}
    assert resp.json()["pagination"]["total"] == 1
    assert differs_only.id not in ids and customer_only.id not in ids


# ---------------------------------------------------------------- N2


def test_n2_403_names_the_section_in_words(client, db, perms):
    agent, wh, company = _agent(db), _wh(db), _other_company(db)
    tag = _tag(client)
    perms["granted"] = {VIEW, EDIT}
    cases = {
        "Chatbot agents": {"agents": {agent.id: True}},
        "Stock visibility": {"stock_visibility": {"mode": "compact", "warehouse_ids": None,
                                                   "excluded_warehouse_ids": None, "hide_zero_locations": False}},
        "Companies": {"companies": [company.id]},
    }
    for words, settings in cases.items():
        resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": settings})
        assert resp.status_code == 403, resp.text
        assert words in resp.text, (words, resp.text)


def test_n1_the_cap_counts_the_set_left_after_the_other_filters(client, db, all_perms, monkeypatch):
    monkeypatch.setattr("app.services.contact_access_copy_service.MAX_DIFF_CANDIDATES", 2)
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}})
    cust = Customer(customer_code=unique_code("ZZT-C"), customer_name="ZZT cust", company_id=DEFAULT_COMPANY_ID)
    db.add(cust)
    db.flush()
    keep = _holder(client, db, [tag], chatbot_stock_allowed=True)
    for _ in range(2):
        _holder(client, db, [tag], chatbot_stock_allowed=True)
    db.add(RespondContactCustomer(contact_id=keep.id, customer_id=cust.id,
                                  company_id=DEFAULT_COMPANY_ID, source="manual"))
    db.flush()
    resp = client.get(CONTACTS, params={"limit": 1000, "customer_id": str(cust.id), "tag_settings": "differs"})
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()["data"]] == [keep.id]
    assert client.get(CONTACTS, params={"limit": 1000, "tag_settings": "differs"}).status_code == 422
