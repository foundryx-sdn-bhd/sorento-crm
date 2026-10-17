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
        payload["escalate_offered"] = True
    pending = ask(kind, opts, team=team if offered else None, asked_at_turn=2, payload=payload)
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
