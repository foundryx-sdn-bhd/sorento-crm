"""SPEC-PROMO-PARAMS hand test step 1 (owner, 12 Oct 2026, crew trace e2a6498a): a size the
parser puts ON the category entity reaches the tool.

"600mm basin got stock?" parsed (parser v86) to ONE entity: hint category, raw "600mm basin",
canonical "basin", spec_key dim_width, spec_value 600, and no specification entity. Grounding
swapped the raw to "basin" and the spec pair on the category entity was read by nothing, so the
stock tool got the basin ids with no constraint and listed every basin.

Rule (owner): the parser decides, code only honours what the parser emitted; no word rule on the
customer's text. A spec pair the parser emitted on a category entity is a specification of that
ask. A millimetre number there carries no axis word of the customer's own, so it is the virtual
`size` key and the route reads the axis from the data (`product_spec_filter.resolve_size`): one
axis holds it -> that axis; several -> any of them (owner ruling 12 Oct 2026, after this hand
test: no "Length or Width?" question); none -> the values on file.

Made-up product codes only (ZZT-*).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.chatbot.test_lane_require import _seed_registry


@pytest.fixture()
def vdb(session_factory):
    db = session_factory()
    _seed_registry(db)
    db.commit()
    yield db
    db.close()


def _category(raw: str, canonical: str | None = None, **spec: Any) -> dict[str, Any]:
    return {
        "raw": raw, "hint": "category", "canonical_code": canonical,
        "current_message": True, "confident": True, "hint_confident": True,
        "quantity": None, **spec,
    }


def _ground(db, *entities: dict[str, Any], message: str | None = None, domain: str = "inventory"):
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, {"entities": list(entities), "domain_hint": domain}, message=message)
    return verdict


def _specs(entities):
    return [e for e in entities if isinstance(e, dict) and e.get("hint") == "specification"]


# --------------------------------------------------------------------------- grounding


def test_g1_mm_number_on_a_category_entity_becomes_a_size_specification(vdb):
    """RED: the pair stays on the category entity and no specification entity exists."""
    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    specs = _specs(verdict["entities"])
    assert [(s["spec_key"], s["spec_value"]) for s in specs] == [("size", 600)], verdict["entities"]
    assert specs[0]["current_message"] is True


def test_g1b_the_category_entity_keeps_its_subject_and_drops_the_pair(vdb):
    """The subject still grounds; the pair is not left on it for a later reader to guess at."""
    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    subjects = [e for e in verdict["entities"] if e.get("hint") == "category"]
    assert subjects, verdict["entities"]
    assert all(not e.get("spec_key") for e in subjects), subjects


def test_g2_an_enum_pair_on_a_category_entity_keeps_its_key(vdb):
    """RED: a finish the parser put on the category entity is a finish specification."""
    verdict = _ground(
        vdb, _category("gunmetal basin", "basin", spec_key="finish", spec_value="gunmetal"),
        message="gunmetal basin got stock?",
    )
    specs = _specs(verdict["entities"])
    assert ("finish", "gunmetal") in [(s["spec_key"], s["spec_value"]) for s in specs], verdict["entities"]


def test_g3_a_category_entity_without_a_pair_adds_no_specification(vdb):
    """Guard: nothing new appears when the parser emitted no pair."""
    verdict = _ground(vdb, _category("basin"), message="basin got stock?")
    assert _specs(verdict["entities"]) == [], verdict["entities"]


def test_g4_an_unknown_key_on_a_category_entity_is_not_invented(vdb):
    """Guard: a key the registry does not hold, with a word value, grounds to nothing new."""
    verdict = _ground(
        vdb, _category("zzt basin", "basin", spec_key="zzt_not_a_key", spec_value="zzt"),
        message="zzt basin got stock?",
    )
    assert [s for s in _specs(verdict["entities"]) if s.get("spec_value") is not None] == [], verdict["entities"]


# --------------------------------------------------------------------------- lane mapping


def test_m1_the_lane_sends_the_size_constraint(vdb, session_factory):
    """RED: `spec_constraints_for` reads only specification entities, so the size never left."""
    from app.services.chatbot.lanes.business import fetch
    from tests.chatbot.test_promo_subject_filter import _class

    _class(session_factory, "Wash Basin", ("basin",))

    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    constraints = fetch.spec_constraints_for({**verdict, "intent_hint": "check_stock"}, vdb) or []
    assert {"key": "size", "op": "eq", "value": 600} in constraints, constraints
    assert fetch.carries_size(constraints)


# --------------------------------------------------------------------------- the turn


STOCK_TOOL = "crm_inventory_stock_balance_list"
STOCK_PATH = "/api/v1/inventory/stock/balance"
PRODUCTS_TOOL = "crm_master_products_list"
ROUTES = {STOCK_TOOL: STOCK_PATH, PRODUCTS_TOOL: "/api/v1/master-data/products/"}


def _seed_basins(session_factory, rows=None) -> list[str]:
    """Wash basins with one stock row each. `rows`: `(tag, {mm key: value})` pairs; default the
    step-1 world (600 on Length for two, on Width for one, one with no size). Returns the codes."""
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product, stock, warehouse
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_promo_subject_filter import _class

    _seed_contact(session_factory, variables={})
    _class(session_factory, "Wash Basin", ("basin",))
    db = session_factory()
    _seed_registry(db)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    codes: list[str] = []
    for tag, mm in rows or (("L1", {"dim_length": 600}), ("L2", {"dim_length": 600}), ("W1", {"dim_width": 600}), ("N1", {})):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-SOC-{tag}-{uuid.uuid4().hex[:4]}")
        values = {"class": {"value": "Wash Basin"}, **{k: {"value": v, "unit": "mm"} for k, v in mm.items()}}
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values=values))
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
        codes.append(p.product_code)
    db.commit()
    return codes


def _route_backed_stock(monkeypatch, session_factory):
    """The stock and products tools answered by the real routes and the real MCP presenter."""
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService
    from tests.chatbot.test_outstanding_lane import _present_response

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    user = {"id": "130c548f-048f-53b2-97a6-3a54676bea77", "email": "u@test.com", "auth_method": "api_key"}

    def _db():
        yield session_factory()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_or_api_key] = lambda: user
    app.dependency_overrides[apply_company_scope] = lambda: None
    client = TestClient(app)
    captured: list[tuple[str, dict[str, Any]]] = []

    def call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        if name not in ROUTES:
            return json.dumps({"has_result": False, "items": []})
        params = {
            k: (json.dumps(v) if k == "spec_constraints" else v)
            for k, v in args.items()
            if k != "view" and v not in (None, "", [])
        }
        res = client.get(ROUTES[name], params=params)
        assert res.status_code == 200, res.text
        return _present_response()(name, res.text)

    return call, captured


@pytest.fixture()
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_t1_size_on_the_category_entity_lists_basins_holding_it_on_any_axis(session_factory, monkeypatch, _clear_overrides):
    """Crew trace e2a6498a: the size was ignored and every basin listed. Owner ruling (12 Oct
    2026): 600 matches Length OR Width, so the basins holding 600 on either axis are listed and
    the one with no size is not. The real resolver reads the seeded basins, as on the console."""
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_outstanding_lane import _run_turn
    from tests.chatbot import test_outstanding_lane as harness

    _seed_basins(session_factory)
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_category("600mm basin", "basin", spec_key="dim_width", spec_value=600)],
    )
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="600mm basin got stock?",
        msg_id=f"zzt-soc-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    reply = (result.reply or {}).get("text") or ""
    stock_calls = [a for n, a in captured if n == STOCK_TOOL]
    assert stock_calls, f"the stock tool must be asked: {captured}"
    sent = [c for a in stock_calls for c in (a.get("spec_constraints") or [])]
    assert {"key": "size", "op": "eq", "value": 600} in sent, stock_calls
    for tag in ("L1", "L2", "W1"):
        assert f"ZZT-SOC-{tag}-" in reply, reply
    assert "ZZT-SOC-N1-" not in reply, reply
    assert "Which one do you mean" not in reply, reply


# --------------------------------------------------------------------------- an axis the customer never said


def _spec_entity(raw: str, key: str, value: Any) -> dict[str, Any]:
    return {
        "raw": raw, "hint": "specification", "canonical_code": None,
        "current_message": True, "confident": True, "hint_confident": True,
        "quantity": None, "spec_key": key, "spec_value": value,
    }


def test_g5_an_mm_spec_whose_words_name_no_axis_is_a_size(vdb):
    """RED (live parse, 12 Oct): "600mm basin got stock?" parsed to a specification entity
    raw "600mm" with spec_key dim_width. The customer named no axis (the registry's own word
    reader finds none in "600mm"), so the axis is the data's to read (owner Q2)."""
    verdict = _ground(
        vdb, _category("basin"), _spec_entity("600mm", "dim_width", 600), message="600mm basin got stock?",
    )
    assert [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])] == [("size", 600)], verdict["entities"]


@pytest.mark.parametrize(
    "raw,key", [("600mm width", "dim_width"), ("600mm length", "dim_length"), ("dia 600mm", "diameter")]
)
def test_g6_an_axis_word_in_the_spec_keeps_that_axis(vdb, raw, key):
    """Guard: when the spec's own words name the axis, the parser's key stands."""
    verdict = _ground(vdb, _category("basin"), _spec_entity(raw, key, 600), message=f"{raw} basin got stock?")
    assert [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])] == [(key, 600)], verdict["entities"]


@pytest.mark.parametrize("domain,intent", [("inventory", "check_stock"), ("master_products", "check_product")])
def test_t2_live_parse_shape_matches_either_axis(session_factory, monkeypatch, _clear_overrides, domain, intent):
    """The live parse's own shape (a category plus a dim_width spec on "600mm"; the live parse
    named `master_products`) listed only the width basin, while 600 sits on Length for two basins.
    Owner ruling (12 Oct 2026): every basin holding 600 on any axis."""
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_outstanding_lane import _run_turn
    from tests.chatbot import test_outstanding_lane as harness

    _seed_basins(session_factory)
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    qf = _parser_output(
        domain_hint=domain, intent_hint=intent,
        entities=[_category("basin", "basin"), _spec_entity("600mm", "dim_width", 600)],
    )
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="600mm basin got stock?",
        msg_id=f"zzt-soc-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    reply = (result.reply or {}).get("text") or ""
    for tag in ("L1", "L2", "W1"):
        assert f"ZZT-SOC-{tag}-" in reply, reply
    assert "ZZT-SOC-N1-" not in reply, reply


# --------------------------------------------------------------------------- reviewer round (step-1 diff)


def test_r_s1_a_corrected_word_on_the_category_pair_binds_nothing(vdb):
    """Reviewer S1 (owner "exact only", #833 round 10): the parser's corrected word ("gunmetal" for
    a typed "gunmetl") never filters; it is an unknown finish in the customer's own spelling."""
    verdict = _ground(
        vdb, _category("gunmetl basin", "basin", spec_key="finish", spec_value="gunmetal"),
        message="gunmetl basin got stock?",
    )
    finishes = [s for s in _specs(verdict["entities"]) if s["spec_key"] == "finish"]
    assert finishes and all(s["spec_value"] is None for s in finishes), verdict["entities"]
    assert any("gunmetl" in str(s["raw"]) for s in finishes), finishes


def test_r_s2_an_axis_word_in_the_category_raw_keeps_that_axis(vdb):
    """Reviewer S2: the customer said "wide", so the pair stays on Width (no `size`)."""
    verdict = _ground(
        vdb, _category("600mm wide basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm wide basin got stock?",
    )
    specs = [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])]
    assert ("dim_width", 600) in specs and ("size", 600) not in specs, verdict["entities"]


def test_r_b1_a_size_ask_outside_the_spec_lanes_keeps_its_require_leg():
    """Reviewer B1: "600mm basin cert?" (product_attachment) never sends spec_constraints, so the
    resolver's `require` (the cert condition) must stay; only a size that reaches the tool drops it."""
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    size = {"raw": "600", "hint": "specification", "canonical_code": "600", "current_message": True,
            "confident": True, "spec_key": "size", "spec_value": 600, "spec_unit": "mm"}
    cert = {"raw": "cert", "hint": "attachment_type", "canonical_code": "certificate", "current_message": True, "confident": True}

    def body(domain, intent, extra):
        return resolve_entity_body({
            "text": {"message": {"message": {"text": "600mm basin"}}},
            "contact": {"id": "1"},
            "parse": {"output": {
                "message_type": "business_query", "domain_hint": domain, "intent_hint": intent,
                "match_mode": "and", "access_levels": [],
                "entities": [_category("basin"), dict(size), *extra],
            }},
        })

    assert body("product_attachment", "check_product_attachment", [cert]).get("require"), "the cert leg stays"
    assert not body("inventory", "check_stock", []).get("require"), "a size the stock tool filters drops the leg"


def test_g7_the_axis_said_in_the_message_keeps_the_parsers_key(vdb):
    """Live parse on the v52 line (12 Oct): "600mm length basin got stock?" -> spec raw "600mm",
    spec_key dim_length. The axis word sits in the message, not the raw; the registry's word
    reader finds Length there, so the parser's key stands (owner: an axis word narrows)."""
    verdict = _ground(
        vdb, _category("basin"), _spec_entity("600mm", "dim_length", 600), message="600mm length basin got stock?",
    )
    assert [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])] == [("dim_length", 600)], verdict["entities"]


def _product(raw: str, **spec: Any) -> dict[str, Any]:
    return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True, "quantity": None, **spec}


def test_g8_a_size_the_parser_hinted_as_a_product_is_the_size(vdb):
    """Live parse on the v52 line (12 Oct): its entity hint list has no "specification", so
    "600mm" came as {hint product, raw "600mm", spec_key size, spec_value 600}. The spec pair is
    the parser's own reading; the "product" is nothing but that size, so it is no product token."""
    verdict = _ground(vdb, _product("600mm", spec_key="size", spec_value=600), _category("basin"), message="600mm basin got stock?")
    assert [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])] == [("size", 600)], verdict["entities"]
    assert not [e for e in verdict["entities"] if e.get("hint") == "product"], verdict["entities"]


def test_g9_a_real_product_keeps_its_entity_beside_the_split_spec(vdb):
    """Guard: a code with a stray spec pair keeps the code (its words are more than the spec)."""
    verdict = _ground(vdb, _product("ZZTWC286", spec_key="dim_length", spec_value=600), message="ZZTWC286 600mm length stock?")
    products = [e for e in verdict["entities"] if e.get("hint") == "product"]
    assert products and products[0]["raw"] == "ZZTWC286" and not products[0].get("spec_key"), verdict["entities"]
    assert ("dim_length", 600) in [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])], verdict["entities"]


def test_r_s5a_an_axis_in_a_carried_category_raw_keeps_that_axis(vdb):
    """Reviewer S5: the category came from an earlier turn ("600mm wide basin", current_message
    False) and this message says no axis. Only the category's own words carry "wide"."""
    carried = {**_category("600mm wide basin", "basin", spec_key="dim_width", spec_value=600), "current_message": False}
    verdict = _ground(vdb, carried, message="got stock?")
    specs = [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])]
    assert ("dim_width", 600) in specs and ("size", 600) not in specs, verdict["entities"]


def test_r_s5b_an_axis_named_outright_drops_the_require_leg_like_a_size():
    """Reviewer S5: "600mm length basin got stock?" grounds on dim_length; the route filters it,
    so the resolver's counted set (which would hold every basin) is not built for it."""
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    length = {"raw": "600mm length", "hint": "specification", "canonical_code": "600", "current_message": True,
              "confident": True, "spec_key": "dim_length", "spec_value": 600, "spec_unit": "mm"}
    out = resolve_entity_body({
        "text": {"message": {"message": {"text": "600mm length basin got stock?"}}},
        "contact": {"id": "1"},
        "parse": {"output": {
            "message_type": "business_query", "domain_hint": "inventory", "intent_hint": "check_stock",
            "match_mode": "and", "access_levels": [], "entities": [_category("basin"), length],
        }},
    })
    assert not out.get("require"), out.get("require")


@pytest.mark.parametrize("raw,left", [("250mm floor grating", "floor grating"), ("600 mm basin", "basin")])
def test_r_fg_the_split_measure_leaves_the_category_words(vdb, raw, left):
    """Crew live trace a92941c7 (v87): the parser put the size INSIDE the category's words.
    Once the size is its own specification, the category keeps only what the thing is, so it
    names the type to the resolver ("floor grating" does; "250mm floor grating" did not)."""
    verdict = _ground(vdb, _category(raw, None, spec_key="size", spec_value=int(raw.split("m")[0].strip())), message=f"{raw} got stock?")
    cats = [e for e in verdict["entities"] if e.get("hint") == "category"]
    assert [c["raw"] for c in cats] == [left], verdict["entities"]
    assert ("size", int(raw.split("m")[0].strip())) in [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])]


def test_r_lat_a_size_the_route_filters_skips_the_resolvers_model_phrase_read():
    """Crew latency trace (a92941c7: routed 3037 ms): a size ask that reaches a tool sends no
    `require`, so an unplaced subject fell into the resolver's spec fallback and its model
    phrase read (2-3 s, `references.py` `understand_phrase`). The route filters that ask; the
    read is wasted. Any other ask keeps it."""
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    size = {"raw": "250", "hint": "specification", "canonical_code": "250", "current_message": True,
            "confident": True, "spec_key": "size", "spec_value": 250, "spec_unit": "mm"}

    def body(domain, intent, entities):
        return resolve_entity_body({
            "text": {"message": {"message": {"text": "250mm floor grating got stock?"}}},
            "contact": {"id": "1"},
            "parse": {"output": {"message_type": "business_query", "domain_hint": domain, "intent_hint": intent,
                                 "match_mode": "and", "access_levels": [], "entities": entities}},
        })

    assert body("inventory", "check_stock", [_category("floor grating"), dict(size)])["understand_phrase"] is False
    assert body("inventory", "check_stock", [_category("floor grating")])["understand_phrase"] is True


# --------------------------------------------------------------------------- a measure in the words, no pair
# SPEC-PROMO-HELP (crew, "250mm floor grating got stock?") read a "<n> mm" left in the subject's
# words as a size pair. Reviewer blocker on 045a0fea: that is code deciding a spec the parser did
# not emit (owner rule: the parser decides). The published "A SIZE AND A CLASS WORD" rule tells
# the parser to emit the size as its own entity; code never adds one.


@pytest.mark.parametrize("ents", [
    lambda: [_category("250mm floor grating")],
    lambda: [_category("600mm basin", "basin")],
    lambda: [_product("250mm"), _category("floor grating")],
])
def test_r_hm_code_never_adds_a_size_the_parser_did_not_emit(vdb, ents):
    """Reviewer blocker (045a0fea): a measure left in an entity's words with no spec pair is not
    read into a size by code; the parser's rule puts the size on its own entity."""
    verdict = _ground(vdb, *ents(), message="250mm floor grating got stock?")
    assert not [s for s in _specs(verdict["entities"]) if s.get("spec_key") == "size"], verdict["entities"]


@pytest.mark.parametrize("ents", [
    lambda: [_product("SR250MM")],
    lambda: [_category("basin", "basin")],
    lambda: [_category("5 l bucket")],
])
def test_r_hm_guard_no_mm_measure_no_size(vdb, ents):
    """Guard: a code with digits, a subject with no number, a number in another unit."""
    verdict = _ground(vdb, *ents(), message="got stock?")
    assert not [s for s in _specs(verdict["entities"]) if s.get("spec_key") == "size"], verdict["entities"]


@pytest.mark.parametrize("ents", [
    lambda: [_category("1.2mm kitchen sink"), _spec_entity("1.2mm thick", "thickness", 1.2)],
    lambda: [_product("1.2mm"), _category("kitchen sink"), _spec_entity("1.2mm thick", "thickness", 1.2)],
])
def test_r_hm_b1_a_number_the_parser_already_keyed_is_not_also_a_size(vdb, ents):
    """Reviewer B1: the parser repeated the measure in the subject's words AND emitted it as a
    thickness. A second, size reading of the same number turned a thickness ask into a size ask
    (no counted set, no 1.2 mm sinks). The parser's key stands."""
    verdict = _ground(vdb, *ents(), message="1.2mm thick kitchen sink got stock?")
    keys = [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])]
    assert ("size", 1.2) not in keys, verdict["entities"]
    assert ("thickness", 1.2) in keys, verdict["entities"]


def test_r_hm_b1_a_carried_keyed_number_is_not_also_a_size(vdb):
    """Reviewer B1 kill test: the axis is named only by a thickness carried from an earlier
    turn, not by this message, so the message's axis read (S1) cannot hide a size reading."""
    carried = {**_spec_entity("thickness 1.2mm", "thickness", 1.2), "current_message": False}
    verdict = _ground(vdb, _category("1.2mm kitchen sink"), carried, message="1.2mm kitchen sink got stock?")
    keys = [(s["spec_key"], s["spec_value"]) for s in _specs(verdict["entities"])]
    assert ("size", 1.2) not in keys and ("thickness", 1.2) in keys, verdict["entities"]
