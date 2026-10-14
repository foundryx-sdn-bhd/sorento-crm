"""PV2 redesign R-GAPS-b, RG-6 (#1517) and RG-8: the empty product ask and chat-visible product lists. No model call.

Fake level: the empty ask through `apply_turn`; the chat_visible param at the fetch builder and the MCP catalog.
DB level (blank Postgres schema): `ProductService.list_products(chat_visible=True)` and the PV2 family lists.
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out

# ------------------------------------------------------------------ the empty ask: a which-product question, no tool call


@pytest.mark.parametrize("domain", ["inventory", "master_products"])
def test_rg6_an_entity_less_new_topic_in_inventory_or_master_products_asks_which_product_with_no_lookup(domain):
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain=domain)), resolver=FakeResolver({}), now=NOW)
    assert len(acts) == 1 and isinstance(acts[0], ClarifyAct), f"{type(acts[0]).__name__}: an empty ask fetches nothing"
    assert acts[0].about == "entity" and st.pending is not None


@pytest.mark.parametrize("domain", ["order", "promotion", "product_attachment"])
def test_rg6_guard_other_domains_keep_their_unchanged_empty_ask(domain):
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain=domain)), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), f"{domain} is unchanged by #1517: {type(acts[0]).__name__}"


def test_rg6_guard_an_inventory_ask_that_names_a_brand_is_not_empty():
    r = FakeResolver({"entities": {"brand|sorento": {"status": "exact", "refs": [
        {"kind": "brand", "key": "sorento", "label": "Sorento"}]}}})
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("brand", phrase="sorento")]))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)


# ------------------------------------------------------------------ a chat product list carries the chat-visible filter


def test_rg6_the_chat_fetch_for_the_product_list_asks_for_chat_visible_rows_only():
    from app.services.chatbot.lanes.business import fetch

    for semantic in ({"domain_hint": "master_products"}, {"intent_hint": "check_product"}, {}):
        trigger = {"entities": [], "tool": "crm_master_products_list", "semantic_input": {**semantic, "contact_id": "1", "space_id": "s"}}
        assert fetch.entity_ids_transformer(trigger).get("chat_visible") is True, semantic


def test_rg6_guard_no_other_tool_gets_the_chat_visible_param():
    from app.services.chatbot.lanes.business import fetch

    trigger = {"entities": [], "tool": "crm_inventory_stock_balance_list", "semantic_input": {"contact_id": "1", "space_id": "s"}}
    assert "chat_visible" not in fetch.entity_ids_transformer(trigger)


def test_rg6_the_mcp_catalog_declares_chat_visible_on_the_product_list_tool():
    from sorento_crm_mcp.catalog import CATALOG

    spec = next(s for s in CATALOG if s.name == "crm_master_products_list")
    assert "chat_visible" in spec.query_params


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _four_products(db):
    ok_cat = S.category(db, class_label="Water Closet")
    hidden_cat = S.category(db, class_label="Internal", searchable=False)
    S.product(db, "RG6-SHOWN", cat=ok_cat)
    S.product(db, "RG6-HIDDENP", cat=ok_cat).is_searchable = False
    S.product(db, "RG6-HIDDENC", cat=hidden_cat)
    S.product(db, "RG6-INACTIVE", cat=ok_cat).is_active = False
    db.flush()


def _listed(db, **kw):
    from app.services.product_service import ProductService

    rows = ProductService(db).list_products(limit=200, **kw)["data"]
    got = {(r["product_code"] if isinstance(r, dict) else r.product_code) for r in rows}
    return got & {"RG6-SHOWN", "RG6-HIDDENP", "RG6-HIDDENC", "RG6-INACTIVE"}


def test_rg6_guard_the_admin_product_list_still_shows_every_row(db):
    _four_products(db)
    assert _listed(db) == {"RG6-SHOWN", "RG6-HIDDENP", "RG6-HIDDENC", "RG6-INACTIVE"}


def test_rg6_chat_visible_leaves_out_an_unsearchable_product_category_or_an_inactive_product(db):
    _four_products(db)
    assert _listed(db, chat_visible=True) == {"RG6-SHOWN"}


def test_rg6_the_pv2_family_lists_skip_inactive_products(db):
    cat = S.category(db, class_label="Water Closet")
    S.product(db, "RG6FAM-A", cat=cat)
    off = S.product(db, "RG6FAM-B", cat=cat)
    off.is_active = False
    db.flush()
    r = DbResolver(db, pinned={"product_class": {"water_closet"}})
    assert [m.key for m in r.family_members("RG6FAM")] == ["RG6FAM-A"]
    res = r.resolve_entities([entity("product", phrase="rg6fam")], held=__import__(
        "app.services.chatbot.pv2.state", fromlist=["Held"]).Held())[0]
    assert res.status == "exact" and "1 variant" in res.refs[0].label, res.refs[0].label
