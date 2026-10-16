"""Lane CONTACT-TAGS, slice S2: tag settings, expected vs current, Matches / Differs.

UAC contact-tags-16oct-acceptance-criteria.md (T1.4, T3.4 merge, T3.5 gate, T4.1-T4.3, T4.5);
contract contact-tags-s2-contract.md. Red tests written before the implementation. Tag settings
are seeded with raw SQL (bypassing the validation under test); everything is asserted through the
HTTP surface. Postgres only, every row seeded here.

Assumptions beyond the contract text (reported to the captain):
  * GET /contacts/{id}/tag-settings lists an item for EVERY key a tag of the contact sets
    (``differs`` says which disagree); ``key`` of a nested setting is its inner key
    (``purchase_request``, ``chatbot_eta_offset_applied``, ``tier``, ``mode``, ``warehouse_ids``,
    ``image.monthly_limit``); a top-level setting uses its section name as ``section`` and any key.
  * Set / id valued items carry human names (access type name, brand name, warehouse name).
    Assertions accept the code / id too, where the contract is silent.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.models.access import (
    AccessAgent,
    ContactAccessType,
    ContactAgentAccess,
    ContactFieldReveal,
    StockVisibilityPolicy,
)
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.product import Brand
from app.models.price_tag import ContactPortalFormOverride
from app.services.company_scope import DEFAULT_COMPANY_ID

# fixtures + helpers from the S1 file (same private-schema, mocked-permission setup)
from tests.test_contact_tags_s1 import (  # noqa: F401
    CONTACTS,
    EDIT,
    TAGS,
    VIEW,
    _contact,
    _set_tags,
    _tag,
    client,
    db,
    perms,
)
from tests._pg_fixture import unique_code

AGENTS_EDIT = "user_management.access_agents.edit"
STOCK_EDIT = "inventory.stock.edit"
OUTBOUND_EDIT = "system.respond_workspaces.edit"
COST = "purchase_orders.cost"


@pytest.fixture
def all_perms(perms):
    perms["granted"] = {VIEW, EDIT, AGENTS_EDIT, STOCK_EDIT, OUTBOUND_EDIT}
    return perms


# ---------------------------------------------------------------- seeding


def _settings(db, tag: dict, settings: dict) -> dict:
    db.execute(
        text("UPDATE contact_tags SET settings = CAST(:s AS jsonb) WHERE id = :i"),
        {"s": json.dumps(settings), "i": tag["id"]},
    )
    return tag


def _stag(client, db, settings: dict, name: str | None = None) -> dict:
    return _settings(db, _tag(client, name), settings)


def _holder(client, db, tags, **cols):
    c = _contact(db)
    for k, v in cols.items():
        setattr(c, k, v)
    db.flush()
    _set_tags(client, c, tags)
    return c


def _agent(db) -> AccessAgent:
    code = unique_code("zag")
    row = AccessAgent(code=code, name=f"ZZT agent {code}")
    db.add(row)
    db.flush()
    return row


def _atype(db, label="t") -> ContactAccessType:
    code = unique_code(f"zat{label}").lower()
    row = ContactAccessType(code=code, name=f"ZZT {code}", is_active=True, sort_order=1)
    db.add(row)
    db.flush()
    return row


def _wh(db) -> Warehouse:
    code = unique_code("zwh")
    row = Warehouse(warehouse_code=code, warehouse_name=f"ZZT {code}", company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _brand(db) -> Brand:
    code = unique_code("zbr")
    row = Brand(brand_code=code, brand_name=f"ZZT {code}", company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _read(client, contact) -> dict:
    resp = client.get(f"{CONTACTS}/{contact.id}/tag-settings")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _item(body, section, *key_parts):
    found = [
        i
        for i in body["items"]
        if i["section"] == section and all(p in str(i.get("key") or "") for p in key_parts)
    ]
    assert len(found) == 1, (section, key_parts, body["items"])
    return found[0]


def _as_set(value):
    if isinstance(value, (list, tuple, set)):
        return {str(v).lower() for v in value}
    return {str(value).lower()}


def _is(value, *alts):
    """value equals one of the alternatives (a scalar or a collection), case-insensitive."""
    return any(_as_set(value) == _as_set(a) for a in alts)


# ---------------------------------------------------------------- T1.4 validation


@pytest.mark.parametrize(
    "settings,named",
    [
        ({"bogus_section": {}}, "bogus_section"),
        ({"chatbot": {"bogus_key": True}}, "bogus_key"),
        ({"portal_forms": {"purchase_request": "yes"}}, "purchase_request"),
        ({"access_types": ["zzt_no_such_code"]}, "zzt_no_such_code"),
        ({"agents": {"11111111-1111-4111-8111-111111111111": True}}, "11111111-1111-4111-8111-111111111111"),
        ({"regions": []}, "regions"),
        ({"chatbot": {"memory_level": "bogus"}}, "memory_level"),
        ({"chatbot": {"tier": "bogus"}}, "tier"),
    ],
)
def test_t1_4_invalid_settings_are_422_naming_the_key(client, all_perms, settings, named):
    tag = _tag(client)
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": settings})
    assert resp.status_code == 422, resp.text
    assert named in resp.text
    assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == {}


def test_t1_4_valid_settings_save_and_read_back_unchanged(client, all_perms):
    tag = _tag(client)
    settings = {
        "chatbot": {"chatbot_stock_allowed": False, "tier": "dealer"},
        "regions": ["west"],
        "portal_forms": {"purchase_request": False},
    }
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": settings})
    assert resp.status_code == 200, resp.text
    assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == settings


# ---------------------------------------------------------------- gates


def test_t3_5_changing_a_gated_section_needs_its_slug_and_saves_nothing(client, db, perms):
    agent = _agent(db)
    company = Company(name="ZZT co", code=unique_code("zco")[:50])
    db.add(company)
    db.flush()
    tag = _tag(client)
    perms["granted"] = {VIEW, EDIT}
    cases = [
        ("agents", {"agents": {agent.id: True}}),
        ("stock_visibility", {"stock_visibility": {"mode": "compact", "warehouse_ids": None,
                                                    "excluded_warehouse_ids": None, "hide_zero_locations": False}}),
        ("companies", {"companies": [company.id]}),
        ("outbound_enabled", {"outbound_enabled": False}),
    ]
    for section, settings in cases:
        resp = client.patch(f"{TAGS}/{tag['id']}", json={"settings": settings})
        assert resp.status_code == 403, (section, resp.text)
        assert section.split("_")[0] in resp.text.lower(), (section, resp.text)
        assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == {}, section


def test_t3_5_renaming_a_tag_whose_stored_settings_hold_agents_needs_no_agent_slug(client, db, perms):
    agent = _agent(db)
    tag = _stag(client, db, {"agents": {agent.id: True}}, name="ZZT keeps agents")
    perms["granted"] = {VIEW, EDIT}
    resp = client.patch(f"{TAGS}/{tag['id']}", json={"name": "ZZT renamed"})
    assert resp.status_code == 200, resp.text
    resp = client.patch(
        f"{TAGS}/{tag['id']}", json={"name": "ZZT renamed twice", "settings": {"agents": {agent.id: True}}}
    )
    assert resp.status_code == 200, resp.text
    assert client.get(f"{TAGS}/{tag['id']}").json()["settings"] == {"agents": {agent.id: True}}


# ---------------------------------------------------------------- merge (expected value)


def test_t3_4_bool_restrictive_off_wins_and_names_the_deciding_tag(client, db):
    on = _stag(client, db, {"portal_forms": {"purchase_request": True}}, "ZZT-on")
    off = _stag(client, db, {"portal_forms": {"purchase_request": False}}, "ZZT-off")
    other = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": True}}, "ZZT-other")
    contact = _holder(client, db, [on, off, other])
    item = _item(_read(client, contact), "portal_forms", "purchase_request")
    assert item["expected"] is False
    assert item["decided_by"] == ["ZZT-off"]
    assert item["conflict"] is False


def test_t3_4_eta_buffer_on_wins(client, db):
    a = _stag(client, db, {"chatbot": {"chatbot_eta_offset_applied": True}})
    b = _stag(client, db, {"chatbot": {"chatbot_eta_offset_applied": False}})
    item = _item(_read(client, _holder(client, db, [a, b])), "chatbot", "chatbot_eta_offset_applied")
    assert item["expected"] is True


def test_t3_4_no_order_values_conflict_naming_both_tags(client, db):
    a = _stag(client, db, {"chatbot": {"chatbot_skip_period_questions": True}}, "ZZT-a")
    b = _stag(client, db, {"chatbot": {"chatbot_skip_period_questions": False}}, "ZZT-b")
    body = _read(client, _holder(client, db, [a, b]))
    item = _item(body, "chatbot", "chatbot_skip_period_questions")
    assert item["conflict"] is True and item["differs"] is True
    assert sorted(t["name"] for t in item["conflict_tags"]) == ["ZZT-a", "ZZT-b"]
    assert body["status"] == "differs"


def test_t3_4_tier_office_loses_dealer_vs_end_user_conflicts(client, db):
    office = _stag(client, db, {"chatbot": {"tier": "office"}})
    dealer = _stag(client, db, {"chatbot": {"tier": "dealer"}})
    end_user = _stag(client, db, {"chatbot": {"tier": "end_user"}})
    item = _item(_read(client, _holder(client, db, [office, dealer])), "chatbot", "tier")
    assert _is(item["expected"], "dealer", "Dealer")
    item = _item(_read(client, _holder(client, db, [dealer, end_user])), "chatbot", "tier")
    assert item["conflict"] is True


def test_t3_4_memory_level_lowest_wins(client, db):
    a = _stag(client, db, {"chatbot": {"memory_level": "full"}})
    b = _stag(client, db, {"chatbot": {"memory_level": "conversation"}})
    item = _item(_read(client, _holder(client, db, [a, b])), "chatbot", "memory_level")
    assert "conversation" in str(item["expected"]).lower()


def test_t3_4_sets_intersect_and_all_brands_is_neutral(client, db):
    dealer, mocha = _atype(db, "d"), _atype(db, "m")
    brand = _brand(db)
    a = _stag(client, db, {"access_types": [dealer.code, mocha.code], "brands": "all"})
    b = _stag(client, db, {"access_types": [dealer.code], "brands": [brand.id]})
    body = _read(client, _holder(client, db, [a, b]))
    assert _is(_item(body, "access_types")["expected"], [dealer.code], [dealer.name])
    assert _is(_item(body, "brands")["expected"], [brand.id], [brand.brand_name])


def test_t3_4_disjoint_regions_conflict(client, db):
    a = _stag(client, db, {"regions": ["west"]})
    b = _stag(client, db, {"regions": ["east"]})
    assert _item(_read(client, _holder(client, db, [a, b])), "regions")["conflict"] is True


def test_t3_4_stock_visibility_merge(client, db, perms):
    perms["granted"] = {VIEW, EDIT, "inventory.stock.view"}  # F1: stock values need stock view
    w1, w2, w3, w4, w5 = (_wh(db) for _ in range(5))
    a = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": [w1.id, w2.id],
                                                "excluded_warehouse_ids": None, "hide_zero_locations": False}})
    b = _stag(client, db, {"stock_visibility": {"mode": "availability", "warehouse_ids": [w2.id, w3.id],
                                                "excluded_warehouse_ids": None, "hide_zero_locations": True}})
    body = _read(client, _holder(client, db, [a, b]))
    assert _is(_item(body, "stock_visibility", "mode")["expected"], "availability")
    assert _is(_item(body, "stock_visibility", "warehouse_ids")["expected"], [w2.id], [w2.warehouse_name])
    assert _item(body, "stock_visibility", "hide_zero_locations")["expected"] is True

    c = _stag(client, db, {"stock_visibility": {"mode": "availability", "warehouse_ids": None,
                                                "excluded_warehouse_ids": [w4.id], "hide_zero_locations": False}})
    d = _stag(client, db, {"stock_visibility": {"mode": "compact", "warehouse_ids": None,
                                                "excluded_warehouse_ids": [w5.id], "hide_zero_locations": False}})
    body = _read(client, _holder(client, db, [c, d]))
    assert _is(
        _item(body, "stock_visibility", "excluded_warehouse_ids")["expected"],
        [w4.id, w5.id],
        [w4.warehouse_name, w5.warehouse_name],
    )


def test_t3_4_media_monthly_limit_lowest_wins(client, db):
    a = _stag(client, db, {"media": {"image": {"is_allowed": True, "monthly_limit": 10}}})
    b = _stag(client, db, {"media": {"image": {"is_allowed": True, "monthly_limit": 30}}})
    item = _item(_read(client, _holder(client, db, [a, b])), "media", "image", "monthly_limit")
    assert item["expected"] == 10


# ---------------------------------------------------------------- current value readers


def test_current_portal_forms_follow_the_resolved_visibility(client, db):
    tag = _stag(client, db, {"portal_forms": {"purchase_request": False}})
    contact = _holder(client, db, [tag])
    body = _read(client, contact)
    item = _item(body, "portal_forms", "purchase_request")
    assert item["differs"] is True and item["current"] is True and body["status"] == "differs"

    db.add(ContactPortalFormOverride(contact_id=contact.id, form_type="purchase_request", is_enabled=False))
    db.flush()
    body = _read(client, contact)
    assert _item(body, "portal_forms", "purchase_request")["differs"] is False
    assert body["status"] == "matches"


def test_current_agents_allow_and_deny(client, db):
    agent = _agent(db)
    allow = _stag(client, db, {"agents": {agent.id: True}})
    deny = _stag(client, db, {"agents": {agent.id: False}})

    with_row = _holder(client, db, [allow])
    db.add(ContactAgentAccess(respond_contact_id=with_row.id, respond_contact_phone=with_row.phone_number,
                              agent_id=agent.id, is_allowed=True))
    db.flush()
    assert _read(client, with_row)["status"] == "matches"

    assert _read(client, _holder(client, db, [allow]))["status"] == "differs"
    assert _read(client, _holder(client, db, [deny]))["status"] == "matches"


def test_current_field_reveal_granted_row_matches(client, db):
    tag = _stag(client, db, {"field_reveals": {COST: True}})
    contact = _holder(client, db, [tag])
    assert _read(client, contact)["status"] == "differs"
    db.add(ContactFieldReveal(respond_contact_id=contact.id, field_key=COST, granted=True))
    db.flush()
    assert _read(client, contact)["status"] == "matches"


def test_current_stock_visibility_is_the_effective_policy_not_the_contact_row(client, db):
    atype = _atype(db, "stk")
    db.add(StockVisibilityPolicy(access_type_code=atype.code, mode="availability", hide_zero_locations=False))
    db.flush()
    tag = _stag(client, db, {"stock_visibility": {"mode": "availability", "warehouse_ids": None,
                                                  "excluded_warehouse_ids": None, "hide_zero_locations": False}})
    contact = _holder(client, db, [tag])
    contact.access_types = [atype]
    db.flush()
    assert _read(client, contact)["status"] == "matches"


def test_current_chatbot_switch_differs_with_a_human_label_and_no_ids(client, db):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=True)
    body = _read(client, contact)
    item = _item(body, "chatbot", "chatbot_stock_allowed")
    assert item["differs"] is True and item["label"] == "Stock checks"
    assert item["current"] is True and item["expected"] is False
    assert contact.id not in json.dumps(body) and tag["id"] not in json.dumps(body["items"])


# ---------------------------------------------------------------- status none


def test_status_none_for_empty_settings_and_for_no_tags(client, db):
    empty = _tag(client)  # settings {}
    assert _read(client, _holder(client, db, [empty]))["status"] == "none"
    bare = _contact(db)
    body = _read(client, bare)
    assert body["status"] == "none" and body["items"] == []


# ---------------------------------------------------------------- T4 list


def _list(client, **params):
    resp = client.get(CONTACTS, params={"limit": 1000, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _stock_world(client, db):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}}, "ZZT-stock-off")
    empty = _tag(client, "ZZT-empty")
    m = _holder(client, db, [tag], chatbot_stock_allowed=False)
    d = _holder(client, db, [tag], chatbot_stock_allowed=True)
    n = _holder(client, db, [empty])
    z = _contact(db)
    return tag, m, d, n, z


def test_t4_1_rows_carry_tag_settings(client, db):
    _tag_, m, d, n, z = _stock_world(client, db)
    rows = {r["id"]: r for r in _list(client, tag=_tag_["id"])}
    assert rows[m.id]["tag_settings"] == {"status": "matches", "differ_count": 0, "differ_labels": []}
    assert rows[d.id]["tag_settings"] == {"status": "differs", "differ_count": 1, "differ_labels": ["Stock checks"]}
    allrows = {r["id"]: r for r in _list(client)}
    assert allrows[n.id]["tag_settings"]["status"] == "none"
    assert allrows[z.id]["tag_settings"]["status"] == "none"


def test_t4_2_tag_settings_filter(client, db):
    tag, m, d, n, z = _stock_world(client, db)
    mine = {m.id, d.id, n.id, z.id}

    def ids(**p):
        return {r["id"] for r in _list(client, **p)} & mine

    assert ids(tag_settings="differs") == {d.id}
    assert ids(tag_settings="matches") == {m.id}
    assert ids(tag_settings="differs", tag=tag["id"]) == {d.id}
    assert ids(tag_settings="matches", tag=tag["id"]) == {m.id}


def test_t4_3_a_hand_edit_makes_the_contact_differ_naming_the_setting(client, db):
    tag = _stag(client, db, {"chatbot": {"chatbot_stock_allowed": False}})
    contact = _holder(client, db, [tag], chatbot_stock_allowed=False)
    assert _read(client, contact)["status"] == "matches"
    resp = client.put(f"{CONTACTS}/{contact.id}/chatbot", json={"chatbot_stock_allowed": True})
    assert resp.status_code == 200, resp.text
    body = _read(client, contact)
    assert body["status"] == "differs"
    assert "Stock checks" in [i["label"] for i in body["items"] if i["differs"]]


def test_t4_5_tag_differs_count_equals_the_list_filter_total(client, db):
    tag, m, d, n, z = _stock_world(client, db)
    _holder(client, db, [tag], chatbot_stock_allowed=True)
    filtered = _list(client, tag=tag["id"], tag_settings="differs")
    assert len(filtered) == 2
    assert client.get(f"{TAGS}/{tag['id']}").json()["differs_count"] == 2
    listed = {r["id"]: r for r in client.get(TAGS, params={"limit": 1000}).json()["data"]}
    assert listed[tag["id"]]["differs_count"] == 2
