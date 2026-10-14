"""PV2 redesign R4a fix round 2: owner product rules 15 Oct. Red tests, no LLM.

R1  a named customer carries; only product then customer asks (the flipped rgaps_b pins are in place in
    test_rgaps_b_carry / fix1 to fix4; the customer-held-then-product direction is pinned here, staff and dealer).
C1  thanks mid-session: NOT PINNED. The decision lives in engine._fallback_context (a DB read) and fallback.compose,
    which has no signal for "mid-session thanks" (a greeting legitimately gets the recap, replay_memory/fallback);
    a pure test would have to invent the interface. Needs the coder's choice first (reported).
C2  a pick with exactly one option asks nothing.
R5  a promotions reply for a named product never echoes its code (engine harness, v1 promotion lane).
R2  reply language follows the parse (engine harness; persisted `reply_language`).
R2p prompt source check.
Open 1 / Open 2 / nit: LAST REPLY after a mirrored v1 picker; HELD prints the label; report rows cut at 20 say so.
Tests marked GUARD pass on 09205ac72.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from tests.chatbot.pv2 import _register_envelopes as E

from app.services.chatbot.pv2 import answer, turn as turn_mod
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.state import ClarifyAct, Held, Lookup, ResolvedRef, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401 - fixtures

P = MemoryProfile(audience="staff")


def _mem(st) -> str:
    return render_memory(st, P, NOW)


def _line(st, prefix):
    return next((ln for ln in _mem(st).split("\n") if ln.startswith(prefix)), f"<no {prefix} line>")


def _exact(kind, key, label=None):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": label or key}]}


# ------------------------------------------------------------------ R1: a named customer carries (owner rule 15 Oct)

R = FakeResolver({"entities": {
    "customer|kedai maju": _exact("customer", "c-kedai", "KEDAI MAJU SDN BHD"),
    "product|BX-220": _exact("product", "BX-220", "BX-220 Basin Mixer"),
}})
WHO = pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "dealer"])


@WHO
@pytest.mark.parametrize("act", ["new_topic", "refines_held"])
def test_r1_a_named_product_over_a_held_customer_is_a_lookup_with_both_and_no_question(can_offer, act):
    """RED ("how about the BX-220?" after a customer's deliveries): HEAD asks "only <customer> or all customers?"."""
    first = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="kedai maju")]))
    st, _ = apply_turn(new_state(NOW), first, resolver=R, now=NOW, can_offer_handover=can_offer)
    second = out(ask(act=act, domain="order", entities=[entity("product", phrase="BX-220")]))
    st, acts = apply_turn(st, second, resolver=R, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [r.key for r in acts[0].held.refs["customer"]] == ["c-kedai"]
    assert [r.key for r in acts[0].held.refs["product"]] == ["BX-220"]
    assert st.pending is None


@WHO
def test_r1_guard_a_named_customer_over_a_held_product_still_asks_only_the_product_or_all(can_offer):
    """GUARD: product then customer keeps the carry question."""
    first = out(ask(act="new_topic", domain="order", entities=[entity("product", phrase="BX-220")]))
    st, _ = apply_turn(new_state(NOW), first, resolver=R, now=NOW, can_offer_handover=can_offer)
    second = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="kedai maju")]))
    st, acts = apply_turn(st, second, resolver=R, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], ClarifyAct) and "carry" in acts[0].pending.kind, type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert "BX-220" in reply and "products" in reply.lower(), reply


# ------------------------------------------------------------------ C2: moved to test_r4a_fix3.py
# crew 15 Oct (a): an EXACT or unique one-option resolution is used; a partial-name guess is a yes/no confirm. The
# FakeResolver cannot tell a partial guess from an exact single option, so the pins are DB-level in test_r4a_fix3.py.


# ------------------------------------------------------------------ Open 1: LAST REPLY beside a mirrored v1 picker


def test_open1_a_mirrored_v1_picker_reads_asked_not_answered():
    """RED: after a Lookup the v1 lane asked a picker; `after_v1` mirrors it as v1_open_question. MEMORY must not
    say `answered inventory` next to `PENDING QUESTION: kind=v1_open_question`."""
    r = FakeResolver({"entities": {"product|BX-220": _exact("product", "BX-220")}})
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="inventory",
                                                  entities=[entity("product", phrase="BX-220")])), resolver=r, now=NOW)
    assert _line(st, "LAST REPLY:") == "LAST REPLY: answered inventory"
    st = turn_mod.after_v1(st, question_kind="sales_report_detail", option_labels=["By customer", "By product"])
    assert "kind=v1_open_question" in _mem(st)
    assert _line(st, "LAST REPLY:") == "LAST REPLY: asked v1_open_question"


# ------------------------------------------------------------------ Open 2: HELD prints the label


def test_open2_held_prints_the_resolver_label_with_code_and_name():
    """RED: the resolver's exact product label is `<code> <name>` (and it marks the key as the code); HELD prints the
    label whole, as before R4a, so the name is not lost."""
    h = Held(domain="inventory", refs={"product": [ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286 Black seat cover")]})
    held_block = _mem(mk_state(h)).split("PENDING QUESTION")[0]
    assert "  product: SRTWC286 Black seat cover" in held_block.split("\n"), held_block


# ------------------------------------------------------------------ nit: PV2's own report rows cut at 20 say so


def test_nit_report_rows_cut_at_20_end_with_the_count():
    """RED: 25 report rows printed, 20 kept; the line ends ` (20 of 25)` like the register rows do."""
    from app.services.chatbot.pv2 import reports
    from tests.chatbot.pv2.test_redesign_r2_stack import TODAY, _report_lookup, _report_runner

    rows = [option(f"C{n}", f"CUSTOMER {n:02d}", {reports.ROW_PAYLOAD: {"dimension": "customer", "keys": [f"c{n}"], "label": f"CUSTOMER {n:02d}"}})
            for n in range(1, 26)]
    result = answer.build_result(mk_state(Held(domain="sales")), [_report_lookup()], out(ask(act="new_topic", domain="sales")),
                                 today=TODAY, report_answer=_report_runner(rows))
    line = _line(result.state, "LAST ANSWER ROWS:")
    assert "20=" in line and "21=" not in line
    assert line.endswith(" (20 of 25)"), line[-40:]


# ------------------------------------------------------------------ R2p: the status guidance in the prompt (source check)

PROMPT = (Path(__file__).resolve().parents[3] / "app/services/chatbot/pv2/prompt_v2.txt").read_text(encoding="utf-8")


def _status_line():
    return next(ln for ln in PROMPT.split("\n") if ln.lstrip().startswith("- status:"))


def test_r2p_the_status_line_makes_a_when_question_outstanding_and_delivered_past_only():
    """RED: the status guidance has no word for a question about WHEN goods reach a customer, nor that delivered
    is past only. Loose checks on the prompt text (not user text)."""
    line = _status_line().lower()
    assert "outstanding" in line and "delivered" in line
    assert "when" in line, "a when-question is named in the status guidance"
    assert "past" in line, "delivered is for past deliveries only"


def test_r2p_a_delivery_to_a_customer_is_the_order_domain_not_incoming():
    """RED: the incoming-domain line says what incoming is not."""
    lines = [ln.lower() for ln in PROMPT.split("\n") if ln.lstrip().startswith("- incoming:") or "incoming" in ln.lower() and "domain" in ln.lower()]
    assert any("customer" in ln and "order" in ln for ln in lines), lines[:3]


def test_r2p_guard_no_word_list_was_added():
    """GUARD: quoted-example lines stay at the HEAD count (43); no new act."""
    import re

    wordy = [ln for ln in PROMPT.split("\n") if len(re.findall(r'["“]([^"”]{1,30})["”]', ln)) >= 4]
    assert len(wordy) <= 43, len(wordy)
    assert PROMPT.count("act ") <= PROMPT.count("act ") and "answers_bot_question" in PROMPT


# ------------------------------------------------------------------ R2: reply language follows the parse (engine)


def test_r2_the_reply_language_of_a_pv2_lookup_follows_the_parsed_language(session_factory, monkeypatch, fake_turn):
    """RED: a mixed message parsed as zh. HEAD saves the language v1's own detector read from the typed line (en)."""
    from tests.chatbot.pv2.carry_fakes import mk_state as _mk
    from tests.chatbot.pv2.test_redesign_r1_engine import _go_v2, _saved, _verdict
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult
    from tests.chatbot.test_customer_scope_lane import _link_customers
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    @dataclass
    class LangResult(FakeResult):
        language: str | None = None

    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT OWN A")
    _go_v2(session_factory)
    verdict = _verdict(domain_hint="order", intent_hint="check_order", order_status=None, entities=[],
                       date_filter_start="2026-09-01", date_filter_end="2026-09-30")
    fake_turn(LangResult(verdict=verdict, state=_mk(), language="zh"))
    rendered = E.present_response(E.ORDERS_TOOL, json.dumps(E.ORDERS_RAW))
    _run_turn(session_factory, monkeypatch, qf=verdict, text_body="orders list pls",
              msg_id=f"ZZT-r2l-{uuid.uuid4().hex[:8]}", attributes=E.ALL_REVEALS, matches={}, mcp_response=rendered)
    assert _saved(session_factory).get("reply_language") == "zh"


# ------------------------------------------------------------------ R5: a promotions reply never echoes the product code (v1 lane)
# The bridge hands the lane the RESOLVED code as the entity's raw (bridge.py:131), so v1's "echo what the customer
# typed" prints a canonical code. Owner rule 15 Oct: drop only the echo; the list stays narrowed to the product.


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from app.main import app

    app.dependency_overrides.clear()


def _promo_world(session_factory):
    from app.models.access import ContactAccessType
    from app.models.marketing import PromotionType
    from app.models.product import Brand
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_promo_kind_subject import _promo

    _seed_contact(session_factory, variables={})
    db = session_factory()
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[],
                        match_priority=90, is_default=True)
    db.add(std)
    db.add_all([ContactAccessType(code="sorento_office", name="Sorento Office"),
                ContactAccessType(code="sorento_dealer", name="Sorento Dealer")])
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    db.flush()
    made = {}
    for key, names in (("asked", ["ZZT PROMO ALPHA", "ZZT PROMO BETA"]), ("other", ["ZZT PROMO OTHER"]), ("bare", [])):
        prod = product(db, company_id=DEFAULT_COMPANY_ID)
        prod.brand_id = brand.id
        db.flush()
        for name in names:
            _promo(db, name, std, prod, levels=["sorento_office", "sorento_dealer"])
        made[key] = (prod.product_code, prod.id)
    db.commit()
    return made


def _promo_turn(session_factory, monkeypatch, code, text_body, msg_id):
    from tests.chatbot.test_engine import _parser_output
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.test_promo_owner_round2 import LEVELS, _mcp, _turn

    _mcp(monkeypatch, session_factory)
    code, pid = code

    def _resolve(body):
        asked = list(body.get("tokens") or [])
        match = {"uuid": pid, "entity_type": "product", "canonical_code": code, "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked],
                "unresolved_tokens": []}

    services = ResolveGateServices(access_types=lambda **_: [{"name": n} for n in LEVELS], resolve_entity=_resolve,
                                   probe=lambda **_: None)
    qf = _parser_output(domain_hint="promotion", intent_hint="check_promotion",
                        entities=[{"raw": code, "hint": "product", "canonical_code": code, "current_message": True,
                                   "confident": True, "hint_confident": True}])
    return qf, services, _turn


def test_r5_the_access_level_question_for_a_named_product_does_not_echo_its_code(session_factory, monkeypatch):
    """RED: HEAD asks "Which access level do you need for <code>?"."""
    made = _promo_world(session_factory)
    qf, services, turn = _promo_turn(session_factory, monkeypatch, made["asked"], "promo for it", "x")
    menu = turn(session_factory, monkeypatch, services, qf, "promo for it", f"zzt-r5-menu-{uuid.uuid4().hex[:6]}")
    assert "access level" in menu.lower(), menu
    assert made["asked"][0] not in menu, menu


def test_r5_guard_the_promotions_list_for_a_named_product_stays_narrowed_and_shows_no_code(session_factory, monkeypatch):
    """GUARD (crew ruling 15 Oct: drop only the echo): the product's own promotions are listed and the other
    product's is not, and the code is not in the list reply. The `I found N promotions for <code>.` intro is only
    built when the promotions carry attached files (answer.py ~2190); this seed has none, so that site is not
    reached here (reported)."""
    from tests.chatbot.test_promo_subject_filter import _pick_qf

    made = _promo_world(session_factory)
    qf, services, turn = _promo_turn(session_factory, monkeypatch, made["asked"], "promo for it", "x")
    turn(session_factory, monkeypatch, services, qf, "promo for it", f"zzt-r5-ask-{uuid.uuid4().hex[:6]}")
    reply = turn(session_factory, monkeypatch, services, _pick_qf(1), "1", f"zzt-r5-pick-{uuid.uuid4().hex[:6]}")
    assert "ZZT PROMO ALPHA" in reply and "ZZT PROMO BETA" in reply, reply
    assert "ZZT PROMO OTHER" not in reply, reply
    assert made["asked"][0] not in reply, reply


def test_r5_a_product_with_no_promotion_does_not_echo_its_code(session_factory, monkeypatch):
    """RED on the echo ("No current promotion for <code>." / the menu): the code never shows in the scope line."""
    made = _promo_world(session_factory)
    qf, services, turn = _promo_turn(session_factory, monkeypatch, made["bare"], "promo for it", "x")
    reply = turn(session_factory, monkeypatch, services, qf, "promo for it", f"zzt-r5-none-{uuid.uuid4().hex[:6]}")
    assert reply, "some reply"
    assert made["bare"][0] not in reply, reply
