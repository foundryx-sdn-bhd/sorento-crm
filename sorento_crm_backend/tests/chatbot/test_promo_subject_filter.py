"""PROMO-SUBJECT-FILTER red tests, engine level (AC-4 to AC-12, AC-16).

UAC `documentation/plans/chatbot/promo-subject-filter-acceptance-criteria.md`; card
`promo-subject-filter-behaviour-card.md`. Backend filter half (AC-1..3, and AC-5's data
half) lives in `tests/test_promotions_subject_filter.py`; the MCP half of AC-4 in
`sorento_crm_mcp/tests/test_promotion_scope_terms_param.py`. Live eval AC-14/15 is the
lead's, not here.

Harness: a real `engine.run_turn` through `test_outstanding_lane._run_turn` (parser,
access and MCP faked, grounding real over the seeded class vocabulary, LLM never called).
The new route param is `scope_terms` (a list of class phrases, as the resolver's own
`scope_terms`); the chatbot fetch is asserted on the CAPTURED tool args.

Fake names only (public repo).

AC-13 must-not-regress set, not copied here, must stay green:
`test_attribute_asks_round10.py::test_water_tap_basin_is_one_unknown_product_type`, round4
`test_r5_*`, `test_replay_the_owners_eight_exchanges`,
`test_bound_spec_words_never_reach_the_described_sets_scope_term`,
`test_exact_classes_runs_once_per_distinct_free_term`, `test_spec_grounding_prompt*`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.models.product import ProductCategory
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of

PROMO_TOOL = fetch_mod.TIER_PROBE_TOOL
HAS = {"has_result": True, "items": [{"description": "Fake promo"}]}
NONE = {"has_result": False, "items": []}


def _services(access_names: list[str]) -> ResolveGateServices:
    """Entities never resolve to an id: a class / brand word carries no uuid."""

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        tokens = list(body.get("tokens") or [])
        return {"tokens": tokens, "resolutions": [], "unresolved_tokens": tokens}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in access_names],
        resolve_entity=_resolve,
        probe=lambda **_: None,
    )


TWO_TIERS = ["Sorento Dealer", "Sorento Office"]


def _class(session_factory, label: str, synonyms: tuple[str, ...] = ()) -> None:
    db = session_factory()
    db.add(
        ProductCategory(
            id=str(uuid.uuid4()),
            category_code=f"ZZT{uuid.uuid4().hex[:6].upper()}-X",
            category_name=label,
            class_label=label,
            is_searchable=True,
            search_synonyms=list(synonyms),
        )
    )
    db.commit()


def _seed(session_factory) -> None:
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Wash Basin", ("basin",))
    _class(session_factory, "Cabinet", ("cabinet",))
    _class(session_factory, "Bathroom Furniture")
    _class(session_factory, "Kitchen Sink")


def _ask_qf(raw: str | None, hint: str = "category", **entity_extra: Any) -> dict[str, Any]:
    entities = (
        [{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True, **entity_extra}]
        if raw
        else []
    )
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=entities)


def _pick_qf(position: int) -> dict[str, Any]:
    return _parser_output(
        domain_hint=None, intent_hint=None, entities=[], access_levels=[], reference_positions=[position]
    )


def _use_mcp(monkeypatch, behaviour) -> list[tuple[str, dict[str, Any]]]:
    """Swap the harness's MCP double for `behaviour(name, args) -> payload | raises`."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        out = behaviour(name, args)
        return out if isinstance(out, str) else json.dumps(out)

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


def _say(session_factory, monkeypatch, qf, text, msg_id, behaviour, names=TWO_TIERS):
    captured = _use_mcp(monkeypatch, behaviour)
    result, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=text,
        msg_id=msg_id,
        resolve_services=_services(names),
    )
    return (result.reply or {}).get("text") or "", captured, result


def _promo_calls(captured):
    return [(n, a) for n, a in captured if n == PROMO_TOOL]


def _terms(args: dict[str, Any]) -> list[str]:
    return [str(t).lower() for t in (args.get("scope_terms") or [])]


# --------------------------------------------------------------------------- #
# AC-4 / AC-9: a grounded subject reaches the tier probe, and the menu is stamped
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("hint", ["category", "product_type"])
def test_tier_probe_sends_the_grounded_subject(session_factory, monkeypatch, hint):
    """AC-4: the tier probe carries `scope_terms` for a grounded category / product_type."""
    _seed(session_factory)
    reply, captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture", hint),
        "any bathroom furniture promo", "zzt-psf-probe-" + hint, lambda n, a: HAS,
    )
    calls = _promo_calls(captured)
    assert len(calls) == 2, f"one probe per entitled tier expected, got {captured}"
    assert all(_terms(a) == ["bathroom furniture"] for _n, a in calls), captured


def test_menu_on_the_bridge_path_is_stamped_per_level(session_factory, monkeypatch):
    """AC-9: the not_found / access_ask bridge path ran the probe; every level line is stamped."""
    _seed(session_factory)
    reply, captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-psf-stamp", lambda n, a: HAS,
    )
    assert "Which access level" in reply, reply
    levels = [l for l in reply.splitlines() if l[:2] in ("1.", "2.")]
    assert len(levels) == 2, reply
    assert all(l.endswith(" - has promotion") or l.endswith(" - no promotion") for l in levels), reply


def test_a_level_with_no_matching_promotion_stamps_no_promotion(session_factory, monkeypatch):
    """AC-10: per level; only the dealer level has a match."""
    _seed(session_factory)

    def by_level(_name, args):
        return HAS if "Sorento Dealer" in (args.get("access_levels") or []) else NONE

    reply, _captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-psf-per-level", by_level,
    )
    assert "1. Office - no promotion" in reply, reply
    assert "2. Dealer - has promotion" in reply, reply


def test_every_level_empty_says_no_current_promotion_and_shows_no_menu(session_factory, monkeypatch):
    """AC-10: all held levels zero: no menu, the subject as typed."""
    _seed(session_factory)
    reply, _captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-psf-all-empty", lambda n, a: NONE,
    )
    assert "No current promotion for bathroom furniture." in reply.splitlines(), reply
    assert "Which access level" not in reply, reply
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


def test_probe_failure_says_it_could_not_check_and_shows_no_menu(session_factory, monkeypatch):
    """AC-11: tool error on the probe: no menu, no files, a retry hint line."""
    _seed(session_factory)

    def boom(_name, _args):
        raise RuntimeError("promotions tool timed out")

    reply, _captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-psf-probe-fails", boom,
    )
    lines = reply.splitlines()
    assert lines[0] == "I could not check promotions right now.", reply
    assert len(lines) >= 2 and lines[1].strip(), f"a retry hint line must follow: {reply!r}"
    assert "Which access level" not in reply, reply
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


# --------------------------------------------------------------------------- #
# AC-6 / AC-5: the subject survives the tier pick
# --------------------------------------------------------------------------- #


def test_pick_turn_fetches_with_the_same_subject(session_factory, monkeypatch):
    """AC-6 (and AC-5's chatbot half: only promo A can come back once the filter is sent)."""
    _seed(session_factory)
    _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-psf-pick-ask", lambda n, a: HAS,
    )
    _reply, captured, _ = _say(
        session_factory, monkeypatch, _pick_qf(1), "1", "zzt-psf-pick-answer", lambda n, a: HAS,
    )
    calls = _promo_calls(captured)
    assert calls, f"the pick must run the promotions fetch: {captured}"
    assert all(_terms(a) == ["bathroom furniture"] for _n, a in calls), captured
    assert all(a.get("access_levels") == ["Sorento Office"] for _n, a in calls), captured


# --------------------------------------------------------------------------- #
# AC-7 / AC-8: an unresolved subject asks back
# --------------------------------------------------------------------------- #


def test_unknown_product_type_asks_back_without_menu_or_tool(session_factory, monkeypatch):
    """AC-7 + AC-8: "basin cabinet" is two classes: no menu, no files, no promotions call."""
    _seed(session_factory)
    reply, captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("basin cabinet"),
        "any basin cabinet promo", "zzt-psf-unknown", lambda n, a: HAS,
    )
    lines = reply.splitlines()
    assert 'I could not match "basin cabinet" to a product type.' in lines, reply
    assert "Which product or brand do you mean?" in lines, reply
    assert "Which access level" not in reply, reply
    assert not any(" . " in l or " | " in l for l in lines), f"no dot or bar separators: {reply!r}"
    assert _promo_calls(captured) == [], f"AC-8: the promotions tool must not run: {captured}"
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


# --------------------------------------------------------------------------- #
# AC-16: the menu names the real brand
# --------------------------------------------------------------------------- #


def test_menu_names_the_canonical_brand_not_the_typo(session_factory, monkeypatch):
    """AC-16: "sroento" resolved to the Sorento brand: the question says Sorento."""
    _seed(session_factory)
    from app.models.product import Brand
    from app.services.company_scope import DEFAULT_COMPANY_ID

    _db = session_factory()
    _db.add(Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID))
    _db.commit()
    reply, _captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("sroento", "brand", canonical_code="Sorento"),
        "sroento promo", "zzt-psf-brand", lambda n, a: HAS,
    )
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    assert "sroento" not in reply.lower().replace("sorento", ""), reply


def test_menu_still_echoes_a_typed_product_code_never_a_guess(session_factory, monkeypatch):
    """AC-16 / D16 control: a product word that is no brand keeps the typed echo."""
    _seed(session_factory)
    reply, _captured, _ = _say(
        session_factory, monkeypatch, _ask_qf("SRTWT0000", "product"),
        "promo for SRTWT0000", "zzt-psf-d16", lambda n, a: HAS,
    )
    assert reply.startswith("Which access level do you need for SRTWT0000?"), reply


# --------------------------------------------------------------------------- #
# AC-12: no-subject asks behave as today
# --------------------------------------------------------------------------- #


def test_ask_with_no_subject_probes_every_level_without_a_subject_filter(session_factory, monkeypatch):
    """AC-12 (with AC-9): "any promo" stamps the menu and sends no `scope_terms`."""
    _seed(session_factory)
    reply, captured, _ = _say(
        session_factory, monkeypatch, _ask_qf(None), "any promo", "zzt-psf-nosubject", lambda n, a: HAS,
    )
    calls = _promo_calls(captured)
    assert len(calls) == 2, captured
    assert all(not a.get("scope_terms") for _n, a in calls), captured
    levels = [l for l in reply.splitlines() if l[:2] in ("1.", "2.")]
    assert len(levels) == 2 and all(" - has promotion" in l or " - no promotion" in l for l in levels), reply


# --------------------------------------------------------------------------- #
# AC-17 / AC-18: owner acceptance checks
# --------------------------------------------------------------------------- #


def test_ac18_kitchen_sink_pick_fetches_with_the_sink_subject(session_factory, monkeypatch):
    """AC-18 (chatbot half): "kitchen sink promo" then pick Office: probe and fetch both
    carry scope_terms ["kitchen sink"], so only sink-linked promotions can come back."""
    _seed(session_factory)
    _reply, probes, _ = _say(
        session_factory, monkeypatch, _ask_qf("kitchen sink"),
        "kitchen sink promo", "zzt-psf-sink-ask", lambda n, a: HAS,
    )
    assert len(_promo_calls(probes)) == 2 and all(_terms(a) == ["kitchen sink"] for _n, a in _promo_calls(probes)), probes
    _r, captured, _ = _say(session_factory, monkeypatch, _pick_qf(1), "1", "zzt-psf-sink-pick", lambda n, a: HAS)
    calls = _promo_calls(captured)
    assert calls and all(_terms(a) == ["kitchen sink"] for _n, a in calls), captured


def test_ac17_product_code_ask_keeps_the_product_ids_path(session_factory, monkeypatch):
    """AC-17 regression guard: a product code (resolved to an id) sends product_ids and no
    scope_terms."""
    _seed_contact(session_factory, variables={})
    pid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    def _resolve(body):
        asked = list(body.get("tokens") or [])
        match = {"uuid": pid, "entity_type": "product", "canonical_code": "SRTKS0000", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    svc = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in TWO_TIERS], resolve_entity=_resolve, probe=lambda **_: None
    )
    captured = _use_mcp(monkeypatch, lambda n, a: HAS)
    _run_turn(
        session_factory, monkeypatch, qf=_ask_qf("SRTKS0000", "product"),
        text_body="promo for SRTKS0000", msg_id="zzt-psf-ac17", resolve_services=svc,
    )
    calls = _promo_calls(captured)
    assert calls, captured
    assert all(a.get("product_ids") == [pid] and not a.get("scope_terms") for _n, a in calls), captured
