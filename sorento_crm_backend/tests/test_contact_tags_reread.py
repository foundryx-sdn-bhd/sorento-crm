"""CONTACT-TAGS re-review round (16 Oct): red tests for the merged-policy stock seed, conflict and brand
redaction, and edit-implies-read. Same harness as the review file.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from app.models.access import StockVisibilityPolicy
from app.models.product import Brand
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.test_contact_tags_s1 import (  # noqa: F401
    CONTACTS, EDIT, TAGS, VIEW, _contact, _tag, client, db, perms,
)
from tests.test_contact_tags_s2 import (  # noqa: F401
    AGENTS_EDIT, STOCK_EDIT, _agent, _atype, _brand, _holder, _read, _stag, _wh, all_perms,
)
from tests.test_contact_tags_s3 import _apply, _fresh, _rows  # noqa: F401
from tests.test_contact_tags_review import _items, _other_company  # noqa: F401


def test_stock_seed_from_a_merged_policy_carrying_both_lists_is_not_applied_but_nothing_fails(client, db, all_perms):
    w1, w2 = _wh(db), _wh(db)
    inc, exc = _atype(db, "inc"), _atype(db, "exc")
    db.add(StockVisibilityPolicy(access_type_code=inc.code, mode="compact", warehouse_ids=[w1.id],
                                 excluded_warehouse_ids=None, hide_zero_locations=False))
    db.add(StockVisibilityPolicy(access_type_code=exc.code, mode="compact", warehouse_ids=None,
                                 excluded_warehouse_ids=[w2.id], hide_zero_locations=False))
    db.flush()
    tag = _stag(client, db, {"stock_visibility": {"mode": "availability"},
                             "chatbot": {"chatbot_stock_allowed": False}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True)
    contact.access_types = [inc, exc]
    db.flush()

    row = _rows(_apply(client, [contact.id], dry_run=False))["results"][0]
    assert row["status"] != "failed", row
    assert any("tock" in n["label"] and n["reason"] for n in row["not_applied"]), row["not_applied"]
    assert _fresh(db, contact.id).chatbot_stock_allowed is False  # the other set keys still apply
    assert db.query(StockVisibilityPolicy).filter_by(contact_id=contact.id).count() == 0


def test_stock_conflict_values_are_redacted_for_a_viewer_without_stock_read(client, db, perms):
    w1, w2 = _wh(db), _wh(db)
    inc = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": [w1.id],
                                                  "excluded_warehouse_ids": None, "hide_zero_locations": False}})
    exc = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": None,
                                                  "excluded_warehouse_ids": [w2.id], "hide_zero_locations": False}})
    contact = _holder(client, db, [inc, exc])
    perms["granted"] = {VIEW, EDIT}
    for dry in (True, False):
        resp = client.post(f"{CONTACTS}/bulk-apply-tag-settings", json={"contact_ids": [contact.id], "dry_run": dry})
        assert resp.status_code == 200, resp.text
        for wh in (w1, w2):
            assert wh.warehouse_name not in resp.text, (dry, wh.warehouse_name)
    read = client.get(f"{CONTACTS}/{contact.id}/tag-settings")
    for wh in (w1, w2):
        assert wh.warehouse_name not in read.text


def test_brand_names_follow_the_callers_company_scope(client, db, perms):
    other = _other_company(db)
    foreign = _brand(db)
    db.execute(text("UPDATE brands SET company_id = :c WHERE id = :b"), {"c": other.id, "b": foreign.id})
    tag = _stag(client, db, {"brands": [foreign.id], "chatbot": {"chatbot_stock_allowed": False}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True)
    perms["granted"] = {VIEW, EDIT}
    read = client.get(f"{CONTACTS}/{contact.id}/tag-settings")
    assert read.status_code == 200
    assert foreign.brand_name not in read.text
    for dry in (True, False):
        resp = client.post(f"{CONTACTS}/bulk-apply-tag-settings", json={"contact_ids": [contact.id], "dry_run": dry})
        assert resp.status_code == 200, resp.text
        assert foreign.brand_name not in resp.text, dry
    listed = client.get(CONTACTS, params={"limit": 1000, "tag": tag["id"]})
    assert foreign.brand_name not in listed.text


def test_edit_implies_read_for_agents_and_stock(client, db, perms):
    agent, wh = _agent(db), _wh(db)
    tag = _stag(client, db, {
        "agents": {agent.id: True},
        "stock_visibility": {"mode": "compact", "warehouse_ids": [wh.id],
                             "excluded_warehouse_ids": None, "hide_zero_locations": False},
    })
    contact = _holder(client, db, [tag])
    perms["granted"] = {VIEW, AGENTS_EDIT, STOCK_EDIT}  # edit slugs only, no .view
    cat = client.get(f"{TAGS}/settings-catalog").json()
    assert "agents" in cat and "warehouses" in cat
    body = client.get(f"{CONTACTS}/{contact.id}/tag-settings").json()
    for section in ("agents", "stock_visibility"):
        found = _items(body, section)
        assert found and any(i["expected"] is not None or i["current"] is not None for i in found), section
    assert agent.name in json.dumps(body) and wh.warehouse_name in json.dumps(body)
