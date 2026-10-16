"""Lane CONTACT-TAGS, slice S3: apply tag settings (preview, apply, add-tag apply choice).

UAC T2.1-T2.3 (apply part), T3.1-T3.9; contract contact-tags-s3-contract.md. Red tests written
before the implementation. Tag settings are seeded with raw SQL; everything is asserted through the
HTTP surface and direct DB reads. Postgres only (blank schema), every row seeded here.
Never reaches respond.io: the outbound test makes any HTTP send raise.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import requests
from sqlalchemy import text

from app.main import app  # noqa: E402  (first app import)
from app.dependencies import get_current_user
from app.models.access import (
    ContactAgentAccess,
    ContactAccessType,
    RespondContact,
    RespondContactCustomer,
)
from app.models.audit import AuditLog
from app.models.order import Customer
from app.models.price_tag import ContactPortalFormOverride
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.test_contact_tags_s1 import (  # noqa: F401
    CONTACTS, EDIT, TAGS, VIEW, _contact, _set_tags, _tag, client, db, perms,
)
from tests.test_contact_tags_s2 import (  # noqa: F401
    AGENTS_EDIT, _agent, _atype, _holder, _read, _stag, all_perms,
)
from tests._pg_fixture import unique_code

APPLY = f"{CONTACTS}/bulk-apply-tag-settings"
BULK = f"{CONTACTS}/bulk-tags"


def _apply(client, ids, dry_run=True):
    return client.post(APPLY, json={"contact_ids": list(ids), "dry_run": dry_run})


def _rows(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def _change(row, section, key=None):
    found = [c for c in row["changes"] if c["section"] == section and (key is None or c["key"] == key)]
    assert len(found) == 1, (section, key, row["changes"])
    return found[0]


def _fresh(db, contact_id) -> RespondContact:
    db.expire_all()
    return db.get(RespondContact, contact_id)


def _stock_tag(client, db, name=None, value=False):
    return _stag(client, db, {"chatbot": {"chatbot_stock_allowed": value}}, name)


# ---------------------------------------------------------------- preview / apply


def test_t3_1_t3_2_dry_run_rows_in_request_order_with_changes_and_no_writes(client, db, all_perms):
    tag = _stock_tag(client, db, "ZZT-stock")
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True)
    ghost = str(uuid.uuid4())
    body = _rows(_apply(client, [ghost, contact.id], dry_run=True))
    assert body["dry_run"] is True
    assert [r["contact_id"] for r in body["results"]] == [ghost, contact.id]
    assert body["results"][0]["status"] == "failed"
    assert "contact not found" in body["results"][0]["error"].lower()
    row = body["results"][1]
    assert row["status"] == "changed" and row["label"] == contact.name
    change = _change(row, "chatbot", "chatbot_stock_allowed")
    assert change["label"] == "Stock checks"
    assert change["before"] is True and change["after"] is False
    assert change["decided_by"] == ["ZZT-stock"]
    assert _fresh(db, contact.id).chatbot_stock_allowed is True


def test_t3_3_apply_makes_every_set_key_match_and_leaves_the_rest(client, db, all_perms):
    tag = _stock_tag(client, db)
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True, notify_salesman=True)
    contact.chatbot_profile = {"language": "ms", "facts": {"k": "v"}}
    customer = Customer(customer_code=unique_code("ZZT-C"), customer_name="ZZT cust", company_id=DEFAULT_COMPANY_ID)
    db.add(customer)
    db.flush()
    db.add(RespondContactCustomer(contact_id=contact.id, customer_id=customer.id,
                                  company_id=DEFAULT_COMPANY_ID, source="manual"))
    db.flush()

    body = _rows(_apply(client, [contact.id], dry_run=False))
    assert body["results"][0]["status"] == "changed"
    assert body["counts"]["changed"] == 1
    assert _read(client, contact)["status"] == "matches"
    fresh = _fresh(db, contact.id)
    assert fresh.chatbot_stock_allowed is False
    assert fresh.notify_salesman is True  # no tag sets it
    assert fresh.chatbot_profile["facts"] == {"k": "v"} and fresh.chatbot_profile["language"] == "ms"
    assert db.query(RespondContactCustomer).filter_by(contact_id=contact.id).count() == 1


def test_preview_equals_apply_and_second_apply_is_unchanged_with_no_writes(client, db, all_perms):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False, "chatbot_eta_offset_applied": False}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True, chatbot_eta_offset_applied=True)
    preview = _rows(_apply(client, [contact.id], dry_run=True))
    applied = _rows(_apply(client, [contact.id], dry_run=False))
    strip = lambda b: [(r["contact_id"], r["status"], r["changes"], r["conflicts"], r["not_applied"]) for r in b["results"]]  # noqa: E731
    assert strip(preview) == strip(applied)
    assert preview["counts"] == applied["counts"]

    audit_before = db.query(AuditLog).filter(AuditLog.entity_id == contact.id).count()
    again = _rows(_apply(client, [contact.id], dry_run=False))
    assert [r["status"] for r in again["results"]] == ["unchanged"]
    assert again["results"][0]["changes"] == []
    assert db.query(AuditLog).filter(AuditLog.entity_id == contact.id).count() == audit_before


def test_t3_4_conflict_is_listed_not_written(client, db, all_perms):
    a = _stag(client, db, {"chatbot": {"chatbot_skip_period_questions": True}}, "ZZT-a")
    b = _stag(client, db, {"chatbot": {"chatbot_skip_period_questions": False}}, "ZZT-b")
    contact = _holder(client, db, [a, b], chatbot_skip_period_questions=False)
    body = _rows(_apply(client, [contact.id], dry_run=False))
    row = body["results"][0]
    assert len(row["conflicts"]) == 1
    assert sorted(t["name"] for t in row["conflicts"][0]["tags"]) == ["ZZT-a", "ZZT-b"]
    assert body["counts"]["conflicts"] == 1
    assert _fresh(db, contact.id).chatbot_skip_period_questions is False


def test_t3_5_a_section_the_actor_may_not_write_is_not_applied_the_rest_is(client, db, perms):
    agent = _agent(db)
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}, "agents": {agent.id: True}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True)
    perms["granted"] = {VIEW, EDIT}  # no access_agents.edit
    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert _fresh(db, contact.id).chatbot_stock_allowed is False
    assert row["status"] == "changed"
    assert len(row["not_applied"]) == 1
    assert "agent" in row["not_applied"][0]["label"].lower() and row["not_applied"][0]["reason"]
    assert db.query(ContactAgentAccess).filter_by(respond_contact_id=contact.id).count() == 0


def test_t3_6_a_failing_write_fails_that_row_only(client, db, all_perms):
    tag = _stock_tag(client, db)
    bad = _holder(client, db, [tag], chatbot_stock_allowed=True)
    good = _holder(client, db, [tag], chatbot_stock_allowed=True)
    db.execute(text("CREATE FUNCTION zzt_boom() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'zzt boom'; END $$"))
    db.execute(text(
        f"CREATE TRIGGER zzt_boom BEFORE UPDATE ON respond_contacts FOR EACH ROW "
        f"WHEN (OLD.id = '{bad.id}') EXECUTE FUNCTION zzt_boom()"
    ))
    body = _rows(_apply(client, [bad.id, good.id], dry_run=False))
    rows = {r["contact_id"]: r for r in body["results"]}
    assert rows[bad.id]["status"] == "failed" and rows[bad.id]["error"]
    assert rows[good.id]["status"] == "changed"
    assert body["counts"]["failed"] == 1 and body["counts"]["changed"] == 1
    db.execute(text("DROP TRIGGER zzt_boom ON respond_contacts"))
    assert _fresh(db, bad.id).chatbot_stock_allowed is True
    assert _fresh(db, good.id).chatbot_stock_allowed is False


# ---------------------------------------------------------------- writers


def test_t3_7_portal_override_only_where_resolved_visibility_differs(client, db, all_perms):
    off = _stag(client, db, {"portal_forms": {"purchase_request": False}})
    on = _stag(client, db, {"portal_forms": {"purchase_request": True}})
    needs = _holder(client, db, [off])
    already = _holder(client, db, [on])  # default visibility already shows it
    _rows(_apply(client, [needs.id, already.id], dry_run=False))
    rows = db.query(ContactPortalFormOverride).filter_by(contact_id=needs.id, form_type="purchase_request").all()
    assert [r.is_enabled for r in rows] == [False]
    assert db.query(ContactPortalFormOverride).filter_by(contact_id=already.id).count() == 0


def test_t3_8_agents_update_only_the_agents_set_and_reuse_the_legacy_row(client, db, all_perms):
    a, b = _agent(db), _agent(db)
    tag = _stag(client, db, {"agents": {a.id: False}})
    contact = _holder(client, db, [tag])
    db.add(ContactAgentAccess(respond_contact_id=contact.id, respond_contact_phone=contact.phone_number,
                              agent_id=a.id, is_allowed=True))
    db.add(ContactAgentAccess(respond_contact_id=contact.id, respond_contact_phone=contact.phone_number,
                              agent_id=b.id, is_allowed=True))
    legacy = _holder(client, db, [tag])
    db.add(ContactAgentAccess(respond_contact_id=None, respond_contact_phone=legacy.phone_number,
                              agent_id=a.id, is_allowed=True))
    db.flush()
    _rows(_apply(client, [contact.id, legacy.id], dry_run=False))
    db.expire_all()
    a_row = db.query(ContactAgentAccess).filter_by(agent_id=a.id, respond_contact_id=contact.id).one()
    assert a_row.is_allowed is False
    assert db.query(ContactAgentAccess).filter_by(agent_id=b.id, respond_contact_id=contact.id).one().is_allowed is True
    legacy_rows = db.query(ContactAgentAccess).filter_by(agent_id=a.id, respond_contact_phone=legacy.phone_number).all()
    assert len(legacy_rows) == 1 and legacy_rows[0].is_allowed is False


def test_t3_8_deny_on_a_contact_with_no_grant_row_is_already_denied_so_no_row_is_created(client, db, all_perms):
    a = _agent(db)
    tag = _stag(client, db, {"agents": {a.id: False}})
    bare = _holder(client, db, [tag])
    body = _rows(_apply(client, [bare.id], dry_run=False))
    assert body["results"][0]["status"] == "unchanged"
    assert db.query(ContactAgentAccess).filter_by(agent_id=a.id, respond_contact_phone=bare.phone_number).count() == 0


def test_tier_apply_changes_only_the_tier_key(client, db, all_perms):
    tag = _stag(client, db, {"chatbot": {"tier": "dealer"}})
    contact = _holder(client, db, [tag])
    contact.chatbot_profile = {"language": "ms", "tier": "office", "facts": {"k": "v"}, "default_ledgers": ["L1"]}
    db.flush()
    _rows(_apply(client, [contact.id], dry_run=False))
    assert _fresh(db, contact.id).chatbot_profile == {
        "language": "ms", "tier": "dealer", "facts": {"k": "v"}, "default_ledgers": ["L1"],
    }


def test_outbound_apply_sets_the_column_and_never_calls_respond_io(client, db, all_perms, monkeypatch):
    real_send = httpx.Client.send

    def guarded(self, request, *a, **kw):
        if request.url.host != "testserver":  # the TestClient itself is an httpx client
            raise AssertionError("respond.io must not be called")
        return real_send(self, request, *a, **kw)

    def boom(*a, **kw):
        raise AssertionError("respond.io must not be called")

    monkeypatch.setattr(httpx.Client, "send", guarded)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    monkeypatch.setattr(requests.Session, "send", boom)
    tag = _stag(client, db, {"outbound_enabled": False})
    contact = _holder(client, db, [tag], outbound_enabled=True)
    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert row["status"] == "changed"
    assert _fresh(db, contact.id).outbound_enabled is False


def test_t3_9_one_audit_row_per_changed_contact_naming_actor_and_tag(client, db, all_perms):
    tag = _stock_tag(client, db, "ZZT-audited")
    changed = _holder(client, db, [tag], chatbot_stock_allowed=True)
    same = _holder(client, db, [tag], chatbot_stock_allowed=False)
    actor = app.dependency_overrides[get_current_user]()["id"]
    _rows(_apply(client, [changed.id, same.id], dry_run=False))
    db.expire_all()

    def named(contact):
        return [
            r for r in db.query(AuditLog).filter(AuditLog.entity_id == contact.id).all()
            if "ZZT-audited" in (r.description or "")
        ]

    (row,) = named(changed)
    assert row.user_id == actor
    assert named(same) == []


def test_set_sections_replace_exactly_and_removals_are_listed_by_name(client, db, all_perms):
    dealer, mocha = _atype(db, "d"), _atype(db, "m")
    tag = _stag(client, db, {"access_types": [dealer.code]})
    contact = _holder(client, db, [tag])
    contact.access_types = [dealer, mocha]
    db.flush()
    row = _rows(_apply(client, [contact.id], dry_run=True))["results"][0]
    change = _change(row, "access_types")
    assert mocha.name in change["removed"] and dealer.name not in change["removed"]
    _apply(client, [contact.id], dry_run=False)
    assert sorted(t.code for t in _fresh(db, contact.id).access_types) == [dealer.code]


# ---------------------------------------------------------------- entry points


def test_tag_apply_covers_every_holder_ordered_by_name_and_only_this_tags_keys(client, db, all_perms):
    stock = _stock_tag(client, db, "ZZT-stock")
    eta = _stag(client, db, {"chatbot": {"chatbot_eta_offset_applied": False}}, "ZZT-eta")
    c_b = _holder(client, db, [stock, eta], chatbot_stock_allowed=True, chatbot_eta_offset_applied=True)
    c_a = _holder(client, db, [stock], chatbot_stock_allowed=True)
    c_b.name, c_a.name = "ZZT Bravo", "ZZT Alpha"
    db.flush()
    resp = client.post(f"{TAGS}/{stock['id']}/apply", json={"dry_run": False})
    body = _rows(resp)
    assert [r["contact_id"] for r in body["results"]] == [c_a.id, c_b.id]
    assert _fresh(db, c_b.id).chatbot_stock_allowed is False
    assert _fresh(db, c_b.id).chatbot_eta_offset_applied is True  # set only by the other tag


def test_bulk_tags_apply_settings_true_adds_and_applies_false_only_links(client, db, all_perms):
    tag = _stock_tag(client, db)
    with_apply = _contact(db)
    without = _contact(db)
    with_apply.chatbot_stock_allowed = without.chatbot_stock_allowed = True
    db.flush()

    def post(contact, apply_settings):
        return client.post(BULK, json={"contact_ids": [contact.id], "add_tag_ids": [tag["id"]],
                                       "remove_tag_ids": [], "apply_settings": apply_settings, "dry_run": False})

    row = _rows(post(with_apply, True))["results"][0]
    assert row["settings"]["status"] in ("changed", "unchanged")
    assert _fresh(db, with_apply.id).chatbot_stock_allowed is False
    assert _rows(post(without, False))["results"][0]["status"] == "changed"
    assert _fresh(db, without.id).chatbot_stock_allowed is True
    held = db.execute(text("SELECT count(*) FROM respond_contact_tags WHERE contact_id = :c"), {"c": without.id}).scalar()
    assert held == 1


def test_gates_limits_and_skipped(client, db, perms, all_perms):
    tag = _stock_tag(client, db)
    empty = _tag(client)
    held = _holder(client, db, [tag])
    nothing = _holder(client, db, [empty])
    body = _rows(_apply(client, [nothing.id, held.id], dry_run=True))
    assert [r["status"] for r in body["results"]] == ["skipped", "changed"]
    assert body["counts"]["skipped"] == 1

    assert _apply(client, []).status_code == 422
    assert _apply(client, [str(uuid.uuid4()) for _ in range(501)]).status_code == 422

    perms["granted"] = {VIEW}
    assert _apply(client, [held.id]).status_code == 403
    assert client.post(f"{TAGS}/{tag['id']}/apply", json={"dry_run": True}).status_code == 403
    assert client.post(BULK, json={"contact_ids": [held.id], "add_tag_ids": [tag["id"]],
                                   "remove_tag_ids": [], "apply_settings": True, "dry_run": False}).status_code == 403


# ---------------------------------------------------------------- stock / spec include vs exclude


def _wh(db):
    from app.models.inventory import Warehouse

    code = unique_code("zwh")
    row = Warehouse(warehouse_code=code, warehouse_name=f"ZZT {code}", company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def test_stock_visibility_with_both_include_and_exclude_is_422_naming_the_section(client, db, all_perms):
    w1, w2 = _wh(db), _wh(db)
    tag = _tag(client)
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"stock_visibility": {
        "mode": "compact", "warehouse_ids": [w1.id], "excluded_warehouse_ids": [w2.id], "hide_zero_locations": False}}})
    assert resp.status_code == 422, resp.text
    assert "stock_visibility" in resp.text
    assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == {}


def test_spec_visibility_with_both_include_and_exclude_is_422_naming_the_section(client, db, all_perms):
    from app.services.spec_visibility import DEFAULT_HIDDEN_KEYS

    key = sorted(DEFAULT_HIDDEN_KEYS)[0]  # always an accepted key, so only the both-lists rule can reject
    tag = _tag(client)
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": {"spec_visibility": {
        "spec_keys": [key], "excluded_spec_keys": [key]}}})
    assert resp.status_code == 422, resp.text
    assert "spec_visibility" in resp.text
    assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == {}


def test_include_on_one_tag_and_exclude_on_another_is_a_stock_conflict_not_written_not_failed(client, db, all_perms):
    w1, w2 = _wh(db), _wh(db)
    inc = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": [w1.id],
                                                  "excluded_warehouse_ids": None, "hide_zero_locations": False}}, "ZZT-inc")
    exc = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": None,
                                                  "excluded_warehouse_ids": [w2.id], "hide_zero_locations": False}}, "ZZT-exc")
    contact = _holder(client, db, [inc, exc])
    read = _read(client, contact)
    conflicts = [i for i in read["items"] if i["section"] == "stock_visibility" and i["conflict"]]
    assert conflicts, read["items"]
    assert sorted(t["name"] for t in conflicts[0]["conflict_tags"]) == ["ZZT-exc", "ZZT-inc"]

    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert row["status"] != "failed", row
    assert any("tock" in c["label"] for c in row["conflicts"]), row["conflicts"]
    assert db.execute(text("SELECT count(*) FROM stock_visibility_policies WHERE contact_id = :c"),
                      {"c": contact.id}).scalar() == 0
