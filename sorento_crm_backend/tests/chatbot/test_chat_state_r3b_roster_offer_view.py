"""CHAT-STATE round 3, R3-B (red): the State view shows the hand-over offer on a carried roster.

A roster was shown, the contact picked, the lookup missed and the reply offered a hand-over.
Contract 36 keeps the roster as the stored question with the team and
`payload.escalate_offered = True` stamped on it. The State the NEXT parser sees must then
show that question as the hand-over offer (`offer`, `hand_over_to <team>`), not as a pick
still owed (card R5: an answer closes the question; UAC C3, C8: the last list stays
pickable). Asserted on `chat_state.view` / `render`, the object and block the parser sees.

Real members (shapes, codes, teams from their recorded prior turn): cp0-12e64c3f37,
cp0-b2f537d9b6, cp0-2d60b19a93, es-33ba35d807, es-bf24a3e2e3, es-s2bd9aaeea,
es-safcbef216, es-s6fd301ccd.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.turn import chat_state, question as question_mod
from app.services.chatbot.turn.pending import ask
from app.services.chatbot.turn.state import Focus, State

TIERS = [("Office", "office"), ("Dealer", "dealer"), ("End user", "end_user")]


def _tier(_n=3):
    return [(lab, code) for lab, code in TIERS], "tier_pick", "access_level"


# (id, kind, entity_type, [(label, code)], team, answered position, subject domain)
ROSTERS = [
    ("cp0_12e64c3f37_product_en", "product_pick", "product",
     [("MWB7630", "MWB7630"), ("MWC7630-SH-P", "MWC7630-SH-P"), ("MWC7630-SH-S10", "MWC7630-SH-S10"),
      ("MWC7630-SH-S8", "MWC7630-SH-S8"), ("MSS6917-BL", "MSS6917-BL"), ("MSS6917-GM", "MSS6917-GM")],
     "purchasing", 4, "incoming"),
    ("cp0_b2f537d9b6_customer_en", "customer_pick", "customer",
     [("YOO LIVING HOUSE (SRT, MCH)", "C0001"), ("YOO LIVING HOUSE [A/C III] - PRICETAG (SRT)", "C0002"),
      ("SMT - I (MCH)", "C0003"), ("MOHD NOOR HISHAM ( YOO LIVING ) (MCH)", "C0004")],
     "sales", 1, "order"),
    ("cp0_2d60b19a93_product_did_you_mean", "product_pick", "product",
     [("MWCX8609-RL-P", "MWCX8609-RL-P"), ("MWCX8609-RL-S10", "MWCX8609-RL-S10"), ("MWC7609-RL-P", "MWC7609-RL-P")],
     "purchasing_certification", 2, "product_attachment"),
    ("es_33ba35d807_related_products", "product_pick", "product",
     [("SRTWF12C-NEW", "SRTWF12C-NEW"), ("SRTWF12C", "SRTWF12C"), ("SRTWF12C-BL", "SRTWF12C-BL"),
      ("SRTWF12C-BL-NEW", "SRTWF12C-BL-NEW")],
     "purchasing", 1, "incoming"),
    ("es_bf24a3e2e3_tier_pick_3", "tier_pick", "access_level", TIERS, "marketing_promotion", 3, "promotion"),
    ("es_s2bd9aaeea_tier_dealer_word", "tier_pick", "access_level", TIERS, "marketing_promotion", 2, "promotion"),
    ("es_safcbef216_tier_end_user_word", "tier_pick", "access_level", TIERS, "marketing_promotion", 3, "promotion"),
    ("es_s6fd301ccd_tier_bm_dealer", "tier_pick", "access_level", TIERS, "marketing_promotion", 2, "promotion"),
    ("zh_customer_roster", "customer_pick", "customer",
     [("YOO LIVING HOUSE (SRT, MCH)", "C0001"), ("SMT - I (MCH)", "C0003")], "sales", 2, "order"),
]
IDS = [r[0] for r in ROSTERS]


def _options(entity_type, labels):
    return [
        {"position": i, "label": lab, "code": code, "name": None, "entity_type": entity_type, "payload": {}}
        for i, (lab, code) in enumerate(labels, 1)
    ]


def _state(kind, entity_type, labels, team, answered, domain, *, offered=True, restated=False):
    opts = _options(entity_type, labels)
    payload = {"domain": domain, "answered_positions": [answered]}
    if offered:
        # As production stores it: the roster was asked at turn 1, the miss reply (turn 2) stamped the offer.
        payload["escalate_offered"] = True
        payload["offered_at_turn"] = 2
    pending = ask(kind, opts, team=team if offered else None, asked_at_turn=1, payload=payload)
    rows = [{"raw": "x", "canonical_code": labels[0][1], "hint": "product", "current_message": False}] if restated else []
    focus = Focus(products=rows, domains=[domain], last_list=[
        {"position": o["position"], "label": o["label"], "code": o["code"]} for o in opts])
    return State(focus=focus, pending=pending, turn_no=3), pending, opts


def _view(state, pending):
    return chat_state.view(state, question_mod.of_pending(pending))


@pytest.mark.parametrize("kind,et,labels,team,answered,domain", [r[1:] for r in ROSTERS], ids=IDS)
def test_r3b_an_answered_roster_with_the_offer_stamped_shows_the_hand_over_offer(kind, et, labels, team, answered, domain):
    state, pending, _ = _state(kind, et, labels, team, answered, domain)
    oq = _view(state, pending)["open_question"]
    assert oq.get("offer"), oq
    assert oq.get("hand_over_to") == team.replace("_", " "), oq


@pytest.mark.parametrize("kind,et,labels,team,answered,domain", [r[1:] for r in ROSTERS], ids=IDS)
def test_r3b_the_answered_roster_is_not_shown_as_owed_a_pick(kind, et, labels, team, answered, domain):
    state, pending, _ = _state(kind, et, labels, team, answered, domain)
    v = _view(state, pending)
    assert "pick" not in (v["open_question"].get("owed") or []), v["open_question"]
    assert '"owed": ["pick"]' not in chat_state.render(v)


@pytest.mark.parametrize("kind,et,labels,team,answered,domain", [r[1:] for r in ROSTERS], ids=IDS)
def test_r3b_the_last_list_still_holds_the_rosters_options_in_order(kind, et, labels, team, answered, domain):
    state, pending, opts = _state(kind, et, labels, team, answered, domain)
    v = _view(state, pending)
    assert [i["position"] for i in v["last_list"]] == [o["position"] for o in opts]
    assert [i.get("code") for i in v["last_list"]] == [c for _l, c in labels]
    # and the offer is on the rendered open-question line the parser reads
    line = next(l for l in chat_state.render(v).splitlines() if l.startswith("- open question:"))
    assert "offer" in line and team.replace("_", " ") in line, line


@pytest.mark.parametrize("restated", [False, True], ids=["pick_named_nothing", "pick_restated_the_held_product"])
@pytest.mark.parametrize("kind,et,labels,team,answered,domain", [r[1:] for r in ROSTERS[:5]], ids=IDS[:5])
def test_r3b_same_whether_or_not_the_pick_restated_the_held_product(kind, et, labels, team, answered, domain, restated):
    state, pending, _ = _state(kind, et, labels, team, answered, domain, restated=restated)
    oq = _view(state, pending)["open_question"]
    assert oq.get("offer") and oq.get("hand_over_to") == team.replace("_", " "), oq
    assert "pick" not in (oq.get("owed") or []), oq


@pytest.mark.parametrize("kind,et,labels,team,answered,domain", [r[1:] for r in ROSTERS], ids=IDS)
def test_r3b_a_roster_not_answered_and_no_offer_stamped_keeps_showing_as_owed(kind, et, labels, team, answered, domain):
    state, pending, _ = _state(kind, et, labels, team, answered, domain, offered=False)
    oq = _view(state, pending)["open_question"]
    assert oq["owed"] == ["pick"], oq
    assert "offer" not in oq and "hand_over_to" not in oq, oq


def test_r3b_a_hand_over_kind_renders_as_today():
    offer = ask("team_pick", [{"position": 1, "label": "incoming stock", "entity_type": "team", "payload": {"team": "purchasing"}}],
                team="purchasing", expects="yes_no", asked_at_turn=2, payload={})
    oq = _view(State(focus=Focus(), pending=offer, turn_no=3), offer)["open_question"]
    assert oq.get("offer") == "team_pick" and oq.get("hand_over_to") == "purchasing", oq


# --------------------------------------------------------------------------- #
# Review round: the offer shows only when the LAST reply made it
# --------------------------------------------------------------------------- #

FRESH = [
    ("did_you_mean_cp0_2d60b19a93", "product_pick", "product",
     [("MWCX8609-RL-P", "MWCX8609-RL-P"), ("MWCX8609-RL-S10", "MWCX8609-RL-S10"), ("MWC7609-RL-P", "MWC7609-RL-P")],
     "purchasing_certification", "product_attachment"),
    ("did_you_mean_cp0_12e64c3f37", "product_pick", "product",
     [("MWB7630", "MWB7630"), ("MWC7630-SH-P", "MWC7630-SH-P"), ("MWC7630-SH-S10", "MWC7630-SH-S10")],
     "purchasing", "incoming"),
    ("did_you_mean_es_33ba35d807", "product_pick", "product",
     [("SRTWF12C-NEW", "SRTWF12C-NEW"), ("SRTWF12C", "SRTWF12C"), ("SRTWF12C-BL", "SRTWF12C-BL")],
     "purchasing", "incoming"),
    ("did_you_mean_zh_srtwc", "product_pick", "product",
     [("SRTWC286-SH", "SRTWC286-SH"), ("SRTWC286-SH-150", "SRTWC286-SH-150")], "purchasing", "inventory"),
]


@pytest.mark.parametrize("kind,et,labels,team,domain", [r[1:] for r in FRESH], ids=[r[0] for r in FRESH])
def test_r3b_review_a_fresh_did_you_mean_roster_stamped_from_birth_still_shows_the_pick_owed(kind, et, labels, team, domain):
    opts = _options(et, labels)
    pending = ask(kind, opts, team=team, asked_at_turn=2, payload={"domain": domain, "escalate_offered": True})
    focus = Focus(domains=[domain], last_list=[{"position": o["position"], "label": o["label"], "code": o["code"]} for o in opts])
    v = _view(State(focus=focus, pending=pending, turn_no=3), pending)
    assert v["open_question"]["owed"] == ["pick"], v["open_question"]
    assert [i["position"] for i in v["last_list"]] == [o["position"] for o in opts]
    assert "offer" not in v["open_question"] or v["open_question"]["owed"] == ["pick"]


def test_r3b_review_the_rendered_last_list_of_a_stamped_answered_roster_lists_its_items():
    state, pending, opts = _state("product_pick", "product", ROSTERS[0][3], "purchasing", 4, "incoming")
    text = chat_state.render(_view(state, pending))
    line = next(l for l in text.splitlines() if l.startswith("- last list"))
    assert "the open question's options" not in line, line
    assert "MWC7630-SH-P" in line and "MSS6917-GM" in line, line


# ---- stale flag after a later pick that found stock (driven as turns through the seam) ----

from tests.chatbot._r9_engine_console import OWNER_FAMILY, _present_response  # noqa: E402
from tests.chatbot.test_chat_state_seam import Seam, _emit, _mem_state, _op, _roster_state, seam  # noqa: E402,F401
from tests.chatbot.test_engine import stub_access  # noqa: E402,F401


def _pick(n: int, text: str):
    return _emit(domain_hint="inventory", intent_hint="check_stock", reference_positions=[n],
                 open_question_answer={"mode": "pick", "picked": [n], "items": [], "qty_for_all": None},
                 state_changes=[_op("pick", position=n)])


def _stock_found(seam_, monkeypatch):
    from app.services.ai_assistant_service import MCPRuntimeClient

    present = _present_response()
    codes = {pid: c for c, pid in seam_.uuid_of.items()}

    def fake(client, name, args):
        seam_.tool_calls.append((name, args))
        if name != "crm_inventory_stock_balance_list":
            return json.dumps({"answers": []})
        summary = [{"product_id": pid, "product_code": codes[pid], "total_on_hand": 40,
                    "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 40}]}
                   for pid in args.get("product_ids") or [] if pid in codes]
        return present(name, json.dumps({
            "data": [], "pagination": {"total": len(summary), "page": 1, "limit": 50},
            "stock_visibility": {"mode": "compact", "warehouse_codes": None, "source": "contact"},
            "stock_summary": summary, "last_updated_at": "2026-09-29T01:00:00"}))

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake)


import json  # noqa: E402

STALE = [("first_then_second", OWNER_FAMILY[:3], 1, 2), ("second_then_third", OWNER_FAMILY[:3], 2, 3),
         ("third_then_first", OWNER_FAMILY[:4], 3, 1)]


@pytest.mark.parametrize("codes,miss,found", [c[1:] for c in STALE], ids=[c[0] for c in STALE])
def test_r3b_review_a_later_pick_that_found_stock_leaves_no_offer_in_the_next_state(seam, monkeypatch, codes, miss, found):
    from app.services.chatbot import turn_runtime
    from tests.chatbot.test_escalation_agent_carry import _seed_escalation_team

    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)

    _seed_escalation_team(seam.session_factory, agent_code="incoming_stock_enquiries", team_code="purchasing",
                          team_label="Packing List", members=[("Lucas", []), ("Jereen", [])])
    a = seam.say(str(miss), _pick(miss, str(miss)), previous=_roster_state(seam, codes))
    assert "escalate" in ((a.reply or {}).get("text") or "").lower(), (a.reply, "turn A must offer the hand-over")
    _stock_found(seam, monkeypatch)
    b = seam.say(str(found), _pick(found, str(found)))
    assert "escalate" not in ((b.reply or {}).get("text") or "").lower(), (b.reply, "turn B must find stock")
    c = seam.say("ok", _emit(domain_hint="inventory", intent_hint="check_stock", state_changes=[_op("keep")]))
    shown = _mem_state(seam, c)["shown"]
    assert "hand_over" not in shown and '"offer"' not in shown, shown


# --------------------------------------------------------------------------- #
# The offer shows on the turn after it is made, through both stamp paths
# --------------------------------------------------------------------------- #

_YES = dict(domain_hint=None, intent_hint=None, is_affirmative=True,
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            open_question_answer={"mode": "yes", "picked": [], "items": [], "qty_for_all": None})


def _staff_with_team(seam, monkeypatch):
    from app.services.chatbot import turn_runtime
    from tests.chatbot.test_escalation_agent_carry import _seed_escalation_team

    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    _seed_escalation_team(seam.session_factory, agent_code="incoming_stock_enquiries", team_code="purchasing",
                          team_label="Packing List", members=[("Lucas", []), ("Jereen", [])])


@pytest.mark.parametrize("codes,miss", [(OWNER_FAMILY[:3], 1), (OWNER_FAMILY[:4], 3)], ids=["pick_1", "pick_3"])
def test_r3b_positive_answer_bridge_stamp_the_next_state_shows_the_offer_and_a_yes_hands_over(seam, monkeypatch, codes, miss):
    _staff_with_team(seam, monkeypatch)
    a = seam.say(str(miss), _pick(miss, str(miss)), previous=_roster_state(seam, codes))
    reply = ((a.reply or {}).get("text") or "").lower()
    assert "escalate" in reply, reply
    yes = seam.say("yes", _emit(state_changes=[_op("answer")], **_YES))
    shown = _mem_state(seam, yes)["shown"]
    line = next(l for l in shown.splitlines() if l.startswith("- open question:"))
    oq = json.loads(line.split(": ", 1)[1])
    assert oq.get("offer") == "hand_over" and oq.get("owed") == ["yes_no"], line
    assert oq.get("hand_over_to") and oq["hand_over_to"].replace(" ", "_") in reply.replace(" ", "_"), (line, reply)
    assert [o["code"] for o in oq["options"]] == codes, line
    assert "answer_pending_accept" in seam.rules_fired, seam.rules_fired


@pytest.mark.parametrize("codes,answered", [(["SRTWC286-SH", "SRTWC286-SH-150"], 1), (["MWB7630", "MWC7630-SH-P", "MWC7630-SH-S10"], 2)],
                         ids=["two_options", "three_options"])
def test_r3b_positive_compose_stamp_the_next_state_shows_the_offer(codes, answered):
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Profile
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    opts = _options("product", [(c, c) for c in codes])
    pend = ask("product_pick", opts, asked_at_turn=1, payload={"domain": "inventory", "answered_positions": [answered]})
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock", "escalation_team_code": "warehouse"}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    env = {"domain": "inventory", "denied": False, "entities": [codes[0]], "product_codes": [], "figures": [], "files": [],
           "miss": [codes[0]], "has_result": False, "tool_has_result": False, "unresolved": [], "unresolved_suggestions": {},
           "error": None, "lane_text": ""}
    answer = compose([env], State(focus=Focus(), pending=pend, profile=Profile(), turn_no=3), policy, ctx=None)
    assert answer.question is not None and "escalate" in answer.text.lower(), answer.text
    nxt = State(focus=Focus(last_list=[{"position": o["position"], "label": o["label"], "code": o["code"]} for o in opts]),
                pending=answer.question, turn_no=4)
    v = chat_state.view(nxt, question_mod.of_pending(answer.question))
    assert v["open_question"].get("offer") == "hand_over" and v["open_question"].get("owed") == ["yes_no"], v["open_question"]
    assert v["open_question"].get("hand_over_to") == "warehouse", v["open_question"]
    assert [i["position"] for i in v["last_list"]] == [o["position"] for o in opts]


@pytest.mark.parametrize("codes,miss", [(OWNER_FAMILY[:3], 1), (OWNER_FAMILY[:3], 2)], ids=["pick_1", "pick_2"])
def test_r3b_s1_the_offer_turns_own_after_view_shows_the_offer_as_the_next_parser_sees_it(seam, monkeypatch, codes, miss):
    _staff_with_team(seam, monkeypatch)
    a = seam.say(str(miss), _pick(miss, str(miss)), previous=_roster_state(seam, codes))
    assert "escalate" in ((a.reply or {}).get("text") or "").lower()
    after = _mem_state(seam, a)["after"]["open_question"]
    assert after.get("offer") == "hand_over" and after.get("owed") == ["yes_no"], after


@pytest.mark.parametrize("kind,et,labels,team,domain", [
    ("product_pick", "product", [("MWCX8609-RL-P", "MWCX8609-RL-P"), ("MWC7609-RL-P", "MWC7609-RL-P")], "purchasing", "product_attachment"),
    ("tier_pick", "access_level", TIERS, "marketing_promotion", "promotion"),
    ("customer_pick", "customer", [("SMT - I (MCH)", "C0003"), ("YOO LIVING HOUSE (SRT, MCH)", "C0001")], "sales", "order"),
], ids=["product", "tier", "customer"])
def test_r3b_guard_a_stamped_roster_never_picked_from_shows_no_offer(kind, et, labels, team, domain):
    opts = _options(et, labels)
    pending = ask(kind, opts, team=team, asked_at_turn=1,
                  payload={"domain": domain, "escalate_offered": True, "offered_at_turn": 2})
    focus = Focus(domains=[domain], last_list=[{"position": o["position"], "label": o["label"], "code": o["code"]} for o in opts])
    oq = _view(State(focus=focus, pending=pending, turn_no=3), pending)["open_question"]
    assert oq["owed"] == ["pick"], oq
    assert "offer" not in oq and "hand_over_to" not in oq, oq
