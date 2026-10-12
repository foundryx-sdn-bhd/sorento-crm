"""Owner hand test round 2: digits answering the promotion menu, the which-question as an open
question, and one line per promotion. Fake names only; route + real presenter backed stub."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of
from tests.chatbot.test_promo_kind_subject import PRODUCTS_TOOL, PROMO_TOOL, _promo, subject_of
from tests.chatbot.test_promo_owner_round2 import _mcp, _turn, _unresolved
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf

THREE = ["Sorento Office", "Sorento Dealer", "End User"]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _world(session_factory, *, basin_promo: bool = True):
    """basin cabinet promo only at Dealer; side cabinet only at Office; tall cabinet only at End user."""
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Bathroom Furniture")
    _class(session_factory, "Wash Basin")
    db = session_factory()
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add_all(
        [
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key="furniture_type", label="Furniture type", data_type="enum",
            allowed_values=["basin_cabinet", "side_cabinet", "tall_cabinet"],
            synonyms={"basin_cabinet": ["basin cabinet"], "side_cabinet": ["side cabinet"], "tall_cabinet": ["tall cabinet"]},
        )
    )
    db.flush()
    for value, level in (("basin_cabinet", "sorento_dealer"), ("side_cabinet", "sorento_office"), ("tall_cabinet", "end_user")):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT{value[:4].upper()}9")
        p.brand_id = brand.id
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values={"class": {"value": "Bathroom Furniture"}, "furniture_type": {"value": value}}))
        db.flush()
        if value == "basin_cabinet" and not basin_promo:
            continue
        _promo(db, f"ZZT PROMO {value.upper()}", std, p, levels=[level])
    db.commit()
    return str(brand.id)


def _recording_services(queries: list, *, products: list[tuple[str, str]] | None = None) -> ResolveGateServices:
    """Records every resolver call's `query`; optionally resolves any word to a product set."""

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        queries.append(body.get("query"))
        asked = list(body.get("tokens") or [])
        if not products:
            return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}
        matches = [{"uuid": u, "entity_type": "product", "canonical_code": c, "match_tier": "exact"} for u, c in products]
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": matches} for r in asked], "unresolved_tokens": []}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in THREE], resolve_entity=_resolve, probe=lambda **_: None
    )


def _ask_qf(raw: str, brand: str | None = None):
    ents = [{"raw": raw, "hint": "category", "canonical_code": None, "current_message": True, "confident": True}]
    if brand:
        ents.insert(0, {"raw": "sorento", "hint": "brand", "canonical_code": "Sorento", "current_message": True, "confident": True})
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)


def _brand_services(brand_id: str) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        hits = [r for r in asked if r.strip().lower() == "sorento"]
        match = {"uuid": brand_id, "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        return {
            "tokens": asked,
            "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in hits],
            "unresolved_tokens": [r for r in asked if r not in hits],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in THREE], resolve_entity=_resolve, probe=lambda **_: None
    )


# (A) a stamped "no promotion" level answers in one line ---------------------------------------


def _assert_one_line_none(reply: str):
    assert reply.strip() == "No End user promotion for basin cabinet.", reply


def test_picking_a_stamped_no_promotion_level_after_a_result_answers_in_one_line(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """(A)+(B2): menu (Office no, Dealer has, End user no) -> "2" (results) -> "3": one line, no
    did-you-mean, no product codes."""
    _world(session_factory)
    _mcp(monkeypatch, session_factory)
    services = _unresolved(THREE)
    menu = _turn(session_factory, monkeypatch, services, _ask_qf("basin cabinet"), "basin cabinet promo", "zzt-md-a-ask")
    lines = menu.splitlines()
    assert "1. Office - no promotion" in lines and "2. Dealer - has promotion" in lines and "3. End user - no promotion" in lines, menu
    results = _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", "zzt-md-a-2")
    assert "ZZT PROMO BASIN_CABINET" in results, results
    _assert_one_line_none(_turn(session_factory, monkeypatch, services, _pick_qf(3), "3", "zzt-md-a-3"))


def test_picking_a_stamped_no_promotion_level_straight_from_the_menu_answers_in_one_line(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    _world(session_factory)
    _mcp(monkeypatch, session_factory)
    queries: list = []
    services = _recording_services(queries)
    _turn(session_factory, monkeypatch, services, _ask_qf("basin cabinet"), "basin cabinet promo", "zzt-md-a2-ask")
    n = len(queries)
    _assert_one_line_none(_turn(session_factory, monkeypatch, services, _pick_qf(3), "3", "zzt-md-a2-3"))
    assert "3" not in queries[n:], queries  # a numbered pick is never sent to the resolver as a query


def test_a_level_read_on_the_first_turn_still_gets_the_stamped_menu_and_the_subject_filter(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """(A2) "basin cabinet promo" with the parser also reading a level (access_levels): the menu is
    the stamped one, and the pick fetch carries scope_terms ["basin cabinet"], never the carried
    product set (`product_ids`)."""
    _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    services = _recording_services([], products=[(str(uuid.uuid4()), "ZZTCARRIED1"), (str(uuid.uuid4()), "ZZTCARRIED2")])
    qf = _ask_qf("basin cabinet")
    qf["access_levels"] = ["Mocha Dealer"]
    menu = _turn(session_factory, monkeypatch, services, qf, "basin cabinet promo", "zzt-md-a3-ask")
    lines = [l for l in menu.splitlines() if l[:2] in ("1.", "2.", "3.")]
    assert lines and all(l.endswith("- has promotion") or l.endswith("- no promotion") for l in lines), menu
    n = len(captured)
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", "zzt-md-a3-pick")
    # owner Q4 option (a), 12 Oct: the pick fetch is the promotion-products tool with spec_constraints
    fetches = [a for name, a in captured[n:] if name == PRODUCTS_TOOL]
    assert fetches, captured
    for a in fetches:
        assert subject_of(a) == {"basin cabinet"}, a
        assert not a.get("scope_terms"), a
        assert not a.get("product_ids"), a
    assert "no promotion matched" not in reply.lower() and "ZZTCARRIED" not in reply, reply


def test_after_a_pick_another_digit_picks_again_from_the_same_menu(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """(B2): "2" then "1": the second digit re-answers the same menu (Office), not a new search."""
    _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    services = _unresolved(THREE)
    _turn(session_factory, monkeypatch, services, _ask_qf("basin cabinet"), "basin cabinet promo", "zzt-md-b2-ask")
    _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", "zzt-md-b2-2")
    n = len(captured)
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", "zzt-md-b2-1")
    # owner Q4 option (a), 12 Oct: the re-pick runs the promotion-products tool
    fetches = [a for name, a in captured[n:] if name == PRODUCTS_TOOL]
    assert fetches and all(a.get("access_levels") == ["Sorento Office"] for a in fetches), captured[n:]
    assert "Couldn't find" not in reply and "Did you mean" not in reply, reply
    assert reply.strip() == "No Office promotion for basin cabinet.", reply


# (B) "which cabinet?" is an open question ------------------------------------------------------


@pytest.mark.parametrize("answer", ["typed", "digit"])
def test_the_which_question_is_an_open_question_and_its_answer_narrows_by_brand(session_factory, monkeypatch, answer):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    brand_id = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    services = _brand_services(brand_id)
    which = _turn(session_factory, monkeypatch, services, _ask_qf("cabinet", brand="Sorento"), "Pls send sorento cabinet promotion", f"zzt-md-w-ask-{answer}")
    assert which.splitlines()[0] == "Which cabinet do you mean?", which
    question = _session_of(session_factory).get("open_question") or {}
    options = question.get("options") or []
    assert len(options) == 3, question
    if answer == "typed":
        reply_qf = _ask_qf("basin cabinet")
        text = "basin cabinet"
    else:
        position = next(o["position"] for o in options if "basin" in str(o.get("label")).lower())
        reply_qf = _pick_qf(position)
        text = str(position)
    menu = _turn(session_factory, monkeypatch, services, reply_qf, text, f"zzt-md-w-menu-{answer}")
    assert menu.startswith("Which access level"), menu
    assert "2. Dealer - has promotion" in menu.splitlines(), menu
    probes = [a for name, a in captured if name == PROMO_TOOL]
    assert probes and all(a.get("brand_ids") == [brand_id] and [t.lower() for t in a.get("scope_terms") or []] == ["basin cabinet"] for a in probes), probes
    n = len(captured)
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", f"zzt-md-w-pick-{answer}")
    # owner Q4 option (a), 12 Oct: the pick is the promotion-products tool; stamp and pick still
    # agree on brand, kind and subject (the subject read from spec_constraints or scope_terms)
    fetch = [a for name, a in captured[n:] if name == PRODUCTS_TOOL]
    assert fetch, captured
    stamp, pick = probes[0], fetch[0]
    for key in ("brand_ids", "promotion_types"):
        assert stamp.get(key) == pick.get(key), (key, stamp, pick)
    assert subject_of(stamp) == subject_of(pick) == {"basin cabinet"}, (stamp, pick)
    assert "ZZT PROMO BASIN_CABINET" in reply, reply


# (D) one line per promotion ---------------------------------------------------------------------


def test_a_promotion_returned_under_two_companies_is_listed_once(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})

    def _call(name: str, args: dict[str, Any]) -> Any:
        row = {
            "description": "ZZT MOCHA A3 FLYER", "start_date": "2026-10-01", "end_date": "2026-12-31",
            "is_expired": False, "attachments": [],
        }
        data = [
            {**row, "id": "00000000-0000-0000-0000-000000000011", "company_name": "Sorento"},
            {**row, "id": "00000000-0000-0000-0000-000000000012", "company_name": "Mocha"},
        ]
        body = {"data": data, "pagination": {"total": 2, "page": 1, "limit": 10}, "empty": False, "fallback_used": False}
        return lane_harness._present_response()(name, json.dumps(body))

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, []))
    pid = str(uuid.uuid4())

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": pid, "entity_type": "product", "canonical_code": "ZZTFLY1", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}], resolve_entity=_resolve, probe=lambda **_: None
    )
    qf = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": "ZZTFLY1", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
    )
    reply = _turn(session_factory, monkeypatch, services, qf, "promo for ZZTFLY1", "zzt-md-d")
    assert reply.count("ZZT MOCHA A3 FLYER") == 1, reply


@pytest.mark.parametrize("typed", ["basin cabinet", "3"])
def test_the_which_answer_read_as_a_position_still_gets_the_stamped_menu(session_factory, monkeypatch, typed):
    """Live flow 2 turn 2: the parser answers the which-question with reference_positions [3] (no
    entities), so the resolver does not run. The reply is the stamped menu for basin cabinet by
    brand Sorento; "2" then picks Dealer."""
    brand_id = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    services = _brand_services(brand_id)
    _turn(session_factory, monkeypatch, services, _ask_qf("cabinet", brand="Sorento"), "Pls send sorento cabinet promotion", f"zzt-md-pos-ask-{typed[:1]}")
    menu = _turn(session_factory, monkeypatch, services, _pick_qf(3), typed, f"zzt-md-pos-menu-{typed[:1]}")
    lines = menu.splitlines()
    assert lines[0].startswith("Which access level do you need for basin cabinet"), menu
    assert "1. Office - no promotion" in lines and "2. Dealer - has promotion" in lines and "3. End user - no promotion" in lines, menu
    probes = [a for name, a in captured if name == PROMO_TOOL]
    assert probes and all(a.get("brand_ids") == [brand_id] and [t.lower() for t in a.get("scope_terms") or []] == ["basin cabinet"] for a in probes), probes
    n = len(captured)
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", f"zzt-md-pos-pick-{typed[:1]}")
    fetch = [a for name, a in captured[n:] if name == PROMO_TOOL]
    assert fetch and all(a.get("access_levels") == ["Sorento Dealer"] for a in fetch), captured[n:]
    assert "ZZT PROMO BASIN_CABINET" in reply, reply


def test_the_live_which_answer_shape_gets_the_stamped_menu(session_factory, monkeypatch):
    """Live flow 2 turn 2 exactly: category_pick pending (Side/Tall/Basin cabinet); the parser
    answers {entities [category "basin cabinet" with canonical_code "basin cabinet"],
    reference_positions [3], reference_target "result", open_question_answer pick [3]}; the
    contact is multi-level (Sorento, Mocha and Cabana dealer, office, end user). The resolver is the REAL route."""
    brand_id = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    names = ["Sorento Office", "Sorento Dealer", "End User", "Mocha Dealer", "Cabana Dealer"]

    from app.services.chatbot.lanes.business.services import _resolve_entity

    # The REAL resolver route (`resolve_reference_post`) over the seeded DB, as the engine calls it.
    services = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in names],
        resolve_entity=_resolve_entity(session_factory()), probe=lambda **_: None,
    )
    which = _turn(session_factory, monkeypatch, services, _ask_qf("cabinet", brand="Sorento"), "Pls send sorento cabinet promotion", "zzt-md-live-ask")
    assert which.splitlines()[0] == "Which cabinet do you mean?", which
    answer = _parser_output(
        message_type="business_query", domain_hint="promotion", intent_hint="check_promotion", access_levels=[],
        promotion_kind=None, entity_op="replace_combine", reference_positions=[3], reference_target="result",
        entities=[{"raw": "basin cabinet", "hint": "category", "canonical_code": "basin cabinet", "confident": True, "current_message": True}],
        open_question_answer={"mode": "pick", "items": [], "picked": [3], "qty_for_all": None},
    )
    menu = _turn(session_factory, monkeypatch, services, answer, "basin cabinet", "zzt-md-live-answer")
    assert menu.startswith("Which access level do you need for basin cabinet"), menu
    lines = [l for l in menu.splitlines() if l[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert lines and all(l.endswith("- has promotion") or l.endswith("- no promotion") for l in lines), menu
    probes = [a for name, a in captured if name == PROMO_TOOL]
    assert probes and all(a.get("brand_ids") == [brand_id] and [t.lower() for t in a.get("scope_terms") or []] == ["basin cabinet"] for a in probes), probes


def test_the_resolver_route_with_an_empty_query_and_grounded_specs_does_not_raise(session_factory):
    """On the pick turn the engine sends the resolver query "" with the grounded spec
    (furniture_type basin_cabinet) in `extracted_specs` (the body captured live, same fields):
    the in-process route must answer, never raise (a raise leaves the turn without a resolver
    payload and the old unstamped menu shows)."""
    from app.services.chatbot.lanes.business.services import _resolve_entity

    _world(session_factory)
    body = {
        "query": "", "match_mode": "and", "tokens": [], "raw_tokens": [], "allowed_entity_types": [],
        "access_levels": ["Sorento Office", "Sorento Dealer", "End User"], "domain": "promotion",
        "fallback_to_all_types": True, "limit": 50, "spec_fallback": True, "exact_match": True,
        "understand_phrase": True, "hidden_spec_keys": [], "contact_id": "900000008", "space_id": None,
        "extracted_specs": [{"key": "furniture_type", "value": "basin_cabinet", "evidence": "basin cabinet", "grounded": True}],
        "require": {"promotion": True}, "predicate_words": ["promotion"], "set_list_max": 50,
    }
    out = _resolve_entity(session_factory())(body)
    assert isinstance(out, dict), out


def test_answering_the_which_question_closes_it_and_a_later_digit_never_merges_subjects(session_factory, monkeypatch):
    """Sorento has no basin cabinet promotion: which-question -> "basin cabinet" -> "No current
    promotion for basin cabinet, ..." (no menu) -> "2". The which-question is closed by its answer,
    so "2" is not read against it: no "tall cabinet", never a merged "tall cabinet, basin cabinet"."""
    from app.services.chatbot.lanes.business.services import _resolve_entity

    _world(session_factory, basin_promo=False)
    _mcp(monkeypatch, session_factory)
    names = ["Sorento Office", "Sorento Dealer", "End User"]
    services = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in names],
        resolve_entity=_resolve_entity(session_factory()), probe=lambda **_: None,
    )
    which = _turn(session_factory, monkeypatch, services, _ask_qf("cabinet", brand="Sorento"), "Pls send sorento cabinet promotion", "zzt-md-close-ask")
    assert which.splitlines()[0] == "Which cabinet do you mean?", which

    def pick(position, raw=None):
        ents = [{"raw": raw, "hint": "category", "canonical_code": raw, "confident": True, "current_message": True}] if raw else []
        return _parser_output(
            message_type="business_query", domain_hint="promotion", intent_hint="check_promotion", access_levels=[],
            entity_op="replace_combine", reference_positions=[position], reference_target="result", entities=ents,
            open_question_answer={"mode": "pick", "items": [], "picked": [position], "qty_for_all": None},
        )

    empty = _turn(session_factory, monkeypatch, services, pick(3, "basin cabinet"), "basin cabinet", "zzt-md-close-answer")
    assert empty.startswith("No current promotion for basin cabinet"), empty
    assert "Which access level" not in empty, empty
    question = _session_of(session_factory).get("open_question")
    assert not question or question.get("kind") != "category_pick", question
    after = _turn(session_factory, monkeypatch, services, pick(2), "2", "zzt-md-close-2")
    assert "tall cabinet" not in after.lower(), after
