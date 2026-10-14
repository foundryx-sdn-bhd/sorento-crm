"""PV2 redesign R4a fix round 3: red tests, no LLM.

L1  reply language: the parse decides only when v1's own detector cannot read the typed line (engine harness).
L2  prompt source check: a named document keeps status null.
L3  promo copy: a product-scoped miss says so; a brand plus product ask names no brand in the scope line.
C2  crew 15 Oct (a): a one-option pick is used without asking ONLY on an exact or unique resolution; a partial-name
    or key-less guess with one option is a yes/no confirm that names the real name. DB-level (the real DbResolver
    tells a partial guess from an exact name; a fake cannot). The confirm answer key is "yes".
Tests marked GUARD pass on 9d2633491.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.chatbot.pv2 import _register_envelopes as E

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, mk_state, out
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401 - fixtures

# ------------------------------------------------------------------ C2 (DB level)

PINNED = {"brand": {"SORENTO"}, "product_type": set(), "attachment_type": set(), "spec_attribute": set()}


@pytest.fixture()
def db():
    from tests.chatbot.pv2 import _resolve_seed as S

    with S.resolve_session() as session:
        yield session


def _world(db):
    from tests.chatbot.pv2 import _resolve_seed as S

    brand = S.brand(db, "Sorento Brand", code="SORENTO")
    S.product(db, "SRT391-GM", cat=S.category(db, class_label="Water Closet"), brand_row=brand)
    S.customer(db, "300-H001", "HANLIM TRADING SDN BHD")
    return DbResolver(db, pinned=PINNED)


def _say(resolver, kind, phrase, state=None):
    parsed = out(ask(act="new_topic", domain="order", entities=[entity(kind, phrase=phrase)]))
    return apply_turn(state or new_state(NOW), parsed, resolver=resolver, now=NOW)


def _numbered(reply):
    return [ln for ln in reply.split("\n") if re.match(r"\s*\d+[.)]\s", ln)]


def test_c2a_a_partial_customer_name_with_one_candidate_is_a_yes_no_confirm(db):
    """RED ("lim"): HEAD silently holds the customer."""
    r = _world(db)
    st, acts = _say(r, "customer", "lim")
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert "HANLIM TRADING SDN BHD" in reply and "300-H001" not in reply, reply
    assert _numbered(reply) == [], reply
    assert not st.held.refs.get("customer"), "nothing held, nothing fetched"


def test_c2a_yes_to_the_confirm_holds_that_customer(db):
    """RED: the confirm is answered with option key "yes"."""
    r = _world(db)
    st, acts = _say(r, "customer", "lim")
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    st, acts = apply_turn(st, out(ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None})),
                          resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [x.label for x in st.held.refs["customer"]] == ["HANLIM TRADING SDN BHD"]


def test_c2b_guard_the_full_customer_name_resolves_with_no_question(db):
    """GUARD."""
    st, acts = _say(_world(db), "customer", "HANLIM TRADING SDN BHD")
    assert isinstance(acts[0], Lookup) and [x.label for x in st.held.refs["customer"]] == ["HANLIM TRADING SDN BHD"]


def test_c2c_an_unmapped_brand_word_with_one_visible_brand_is_a_confirm_not_silent(db):
    """RED ("itali brand", no such brand): the key-less index candidate is not a resolution; HEAD silently holds
    the only visible brand."""
    st, acts = _say(_world(db), "brand", "itali")
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert "Sorento Brand" in reply and _numbered(reply) == [], reply
    assert not st.held.refs.get("brand")


# ------------------------------------------------------------------ L2: a named document keeps status null (prompt source)

PROMPT = (Path(__file__).resolve().parents[3] / "app/services/chatbot/pv2/prompt_v2.txt").read_text(encoding="utf-8")


def test_l2_the_status_line_keeps_status_null_when_a_document_number_is_named():
    """RED: the status line has no word for a named document; its own status answers."""
    line = next(ln for ln in PROMPT.split("\n") if ln.lstrip().startswith("- status:")).lower()
    assert "document" in line and "null" in line, line


# ------------------------------------------------------------------ L1: reply language (engine harness)


def _two_turns(session_factory, monkeypatch, fake_turn, *, first, second):
    """Each of `first` / `second` = (typed text, parsed language). Returns the saved reply_language after each."""
    from dataclasses import dataclass as dc

    from tests.chatbot.pv2.carry_fakes import mk_state as _mk
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult
    from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _saved, _verdict
    from tests.chatbot.test_customer_scope_lane import _link_customers
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    @dc
    class LangResult(FakeResult):
        language: str | None = None

    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    _go_v2(session_factory)
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    rendered = E.present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    saved = []
    for text, lang in (first, second):
        if text is None:
            continue
        fake_turn(LangResult(verdict=verdict, state=_mk(), language=lang))
        _run_turn(session_factory, monkeypatch, qf=verdict, text_body=text, msg_id=f"ZZT-l1-{uuid.uuid4().hex[:8]}",
                  attributes=E.ALL_REVEALS, matches={}, mcp_response=rendered)
        saved.append(_saved(session_factory).get("reply_language"))
    return saved


def test_l1_a_language_neutral_line_keeps_the_conversation_language_even_when_parsed_en(session_factory, monkeypatch, fake_turn):
    """RED (zh carried, then "SRTWC286" parsed en): HEAD lets the parse override a line v1 cannot read."""
    saved = _two_turns(session_factory, monkeypatch, fake_turn, first=("orders list pls", "zh"), second=("SRTWC286", "en"))
    assert saved[0] == "zh" and saved[1] == "zh", saved


def test_l1_a_clearly_typed_malay_line_replies_malay_even_when_parsed_en(session_factory, monkeypatch, fake_turn):
    """RED ("stok SRTWC286 ada tak" parsed en): v1's detector reads a clear ms; HEAD takes the parse."""
    saved = _two_turns(session_factory, monkeypatch, fake_turn, first=(None, None), second=("stok SRTWC286 ada tak", "en"))
    assert saved == ["ms"], saved


def test_l1_guard_a_zh_and_ms_mixed_line_parsed_zh_replies_zh(session_factory, monkeypatch, fake_turn):
    """GUARD (the case fix 2 targeted)."""
    saved = _two_turns(session_factory, monkeypatch, fake_turn, first=(None, None),
                       second=("tolong check 这个 SRTWC286 ada stok tak", "zh"))
    assert saved == ["zh"], saved


# ------------------------------------------------------------------ L3: product-scoped promo copy (v1 lane)


def _services_for(code, pid, brand_token=None):
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.test_promo_owner_round2 import LEVELS

    def _resolve(body):
        asked = list(body.get("tokens") or [])
        res, un = [], []
        for r in asked:
            if str(r).lower() != "sorento":
                res.append({"raw": r, "token": r, "matches": [{"uuid": pid, "entity_type": "product",
                                                              "canonical_code": code, "match_tier": "exact"}]})
            else:
                un.append(r)
        return {"tokens": asked, "resolutions": res, "unresolved_tokens": un}

    return ResolveGateServices(access_types=lambda **_: [{"name": n} for n in LEVELS], resolve_entity=_resolve,
                               probe=lambda **_: None)


def _qf(*ents):
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=list(ents))


def _ent(raw, hint):
    return {"raw": raw, "hint": hint, "canonical_code": raw, "current_message": True, "confident": True, "hint_confident": True}


def test_l3_a_product_scoped_promotion_miss_says_for_this_product(session_factory, monkeypatch):
    """RED: HEAD prints the bare "No current promotion." which reads as none at all."""
    from tests.chatbot.pv2.test_r4a_fix2 import _promo_world
    from tests.chatbot.test_promo_owner_round2 import _mcp, _turn
    from tests.chatbot.test_promo_subject_filter import _pick_qf

    made = _promo_world(session_factory)
    code, pid = made["bare"]
    _mcp(monkeypatch, session_factory)
    services = _services_for(code, pid)
    qf = _qf(_ent(code, "product"))
    _turn(session_factory, monkeypatch, services, qf, "promo for it", f"zzt-l3-a-{uuid.uuid4().hex[:6]}")
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", f"zzt-l3-b-{uuid.uuid4().hex[:6]}")
    assert reply == "No current promotion for this product.", reply


def test_l3_a_brand_plus_product_ask_names_no_brand_in_the_scope_line(session_factory, monkeypatch):
    """RED: the product narrows the list, so the scope line must not say "for Sorento" (the brand)."""
    from tests.chatbot.pv2.test_r4a_fix2 import _promo_world
    from tests.chatbot.test_promo_owner_round2 import _mcp, _turn

    made = _promo_world(session_factory)
    code, pid = made["asked"]
    _mcp(monkeypatch, session_factory)
    services = _services_for(code, pid)
    qf = _qf(_ent("Sorento", "brand"), _ent(code, "product"))
    menu = _turn(session_factory, monkeypatch, services, qf, "sorento promo for it", f"zzt-l3-c-{uuid.uuid4().hex[:6]}")
    first = menu.split("\n")[0]
    assert "access level" in first.lower(), menu
    assert "sorento" not in first.lower() and code not in first, first
