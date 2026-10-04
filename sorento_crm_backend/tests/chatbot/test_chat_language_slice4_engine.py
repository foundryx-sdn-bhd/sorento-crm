"""CHAT-LANGUAGE slice 4, AC-CL41..45 through `engine.run_turn` (the r9 console harness).

The English strings the stubbed turns produce today are the AC-CL45 baseline: an `en` turn
must keep printing them byte for byte. The ms / zh turns end in the catalog's wording.
"""
from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import text

from app.services.chatbot import copy as copy_mod
from app.services.chatbot import turn_runtime
from tests.chatbot._r9_engine_console import EngineConsole, product, stock
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_escalation_agent_carry import _capture_next_assignee, _capture_sla

MISS_LEAD = 'Couldn\'t find: "ZZNOPE999" (product). '
# The miss lead reads in the reply language too (crew-tester round 1, 3 Oct); zh drops the space.
MS_LEAD = 'Tidak dapat menemui: "ZZNOPE999" (product). '
ZH_LEAD = '找不到："ZZNOPE999" (product)。'
EN_OFFER = MISS_LEAD + "Would you like me to escalate to warehouse team?"
MS_OFFER = MS_LEAD + "Adakah anda mahu saya rujuk kepada pasukan warehouse?"
ZH_OFFER = ZH_LEAD + "需要我转交给 warehouse 团队吗？"
DYM_TAIL = "\n1. SRTWC286-SH\n2. SRTWC286-SH-P"

MS_ASK = "ada stok ZZNOPE999?"
ZH_ASK = "有 ZZNOPE999 的库存吗"
EN_ASK = "check stock ZZNOPE999"


def _staff_console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    """A contact who may see quantities, so a miss offers the warehouse team."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    return c


def _dealer_console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    return EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)


def _bar_escalation(session_factory, phone: str) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET escalation_allowed = false WHERE phone_number = :p"), {"p": phone}
    )
    db.commit()


def _confirmation() -> dict:
    return verdict(
        message_type="casual",
        is_affirmative=True,
        entities=[],
        open_question_answer={"mode": "yes", "picked": [], "items": [], "qty_for_all": None},
        escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": None},
        routing={"suggested_team": None, "suggested_agent": None},
    )


def _declined() -> dict:
    return verdict(
        message_type="casual",
        is_affirmative=False,
        entities=[],
        open_question_answer={"mode": "no", "picked": [], "items": [], "qty_for_all": None},
        escalation={"is_escalation_confirmation": False, "escalation_declined": True, "company_pick": None},
        routing={"suggested_team": None, "suggested_agent": None},
    )


# --------------------------------------------------------------------------- #
# AC-CL41: the offer is localized, still arms, and a "ya" still escalates
# --------------------------------------------------------------------------- #


def test_cl41_an_ms_miss_ends_with_the_localized_offer_and_the_offer_arms(
    session_factory, monkeypatch, stub_access
):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009601")
    reply = c.say(MS_ASK, stock(product("ZZNOPE999")))
    assert reply == MS_OFFER
    assert c.state["reply_language"] == "ms"
    question = c.stored_question
    assert question is not None and question["kind"] == "team_pick", question
    assert question["expects"] == "yes_no" and question["team"] == "warehouse"


def test_cl41_a_zh_miss_ends_with_the_localized_offer(session_factory, monkeypatch, stub_access):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009602")
    assert c.say(ZH_ASK, stock(product("ZZNOPE999"))) == ZH_OFFER
    assert c.stored_question is not None and c.stored_question["kind"] == "team_pick"


def test_cl41_ya_with_the_parser_verdict_still_escalates_to_the_offered_team(
    session_factory, monkeypatch, stub_access
):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009603")
    bodies = _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    assert c.say(MS_ASK, stock(product("ZZNOPE999"))) == MS_OFFER

    c.say("ya", _confirmation())

    assert len(bodies) == 1, bodies
    assert bodies[0]["team_code"] == "warehouse", bodies[0]
    assert c.last_trace.lane == "escalation"
    assert c.stored_question is None
    assert c.state["reply_language"] == "ms"


def test_cl41_the_offer_is_translated_once_not_twice(session_factory, monkeypatch, stub_access):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009604")
    reply = c.say(MS_ASK, stock(product("ZZNOPE999")))
    assert reply.count("Adakah anda mahu saya rujuk") == 1
    assert "escalate" not in reply


# --------------------------------------------------------------------------- #
# AC-CL42: a barred contact: the strip ran on English, the refer line prints once, translated
# --------------------------------------------------------------------------- #


def test_cl42_a_barred_ms_miss_prints_the_refer_line_once_translated_and_no_offer(
    session_factory, monkeypatch, stub_access
):
    phone = "+60000009611"
    c = _staff_console(session_factory, monkeypatch, stub_access, phone)
    _bar_escalation(session_factory, phone)

    reply = c.say(MS_ASK, stock(product("ZZNOPE999")))

    assert reply == MS_LEAD + "Sila rujuk jurujual anda."
    assert reply.count("Sila rujuk jurujual anda.") == 1
    assert "Please refer" not in reply and "escalate" not in reply and "rujuk kepada pasukan" not in reply
    assert c.stored_question is None


def test_cl42_the_same_in_zh(session_factory, monkeypatch, stub_access):
    phone = "+60000009612"
    c = _staff_console(session_factory, monkeypatch, stub_access, phone)
    _bar_escalation(session_factory, phone)
    reply = c.say(ZH_ASK, stock(product("ZZNOPE999")))
    assert reply == ZH_LEAD + "请联系您的销售员。"
    assert c.stored_question is None


def test_cl42_a_dealer_ms_miss_is_referred_once_translated(session_factory, monkeypatch, stub_access):
    c = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009613")
    reply = c.say(MS_ASK, stock(product("ZZNOPE999")))
    # The dealer's miss prints the lead and the refer line as two paragraphs.
    assert reply == MS_LEAD.rstrip() + "\n\nSila rujuk jurujual anda."
    assert c.stored_question is None


# --------------------------------------------------------------------------- #
# AC-CL43: dealer quantity question and did-you-mean
# --------------------------------------------------------------------------- #


def test_cl43_the_dealer_quantity_question_in_ms(session_factory, monkeypatch, stub_access):
    ms = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009621")
    assert ms.say("ada stok SRTWC286-SH?", stock(product("SRTWC286-SH"))) == "Berapa unit SRTWC286-SH?"


def test_cl43_the_dealer_quantity_question_in_zh(session_factory, monkeypatch, stub_access):
    zh = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009622")
    assert zh.say("有 SRTWC286-SH 的库存吗", stock(product("SRTWC286-SH"))) == "SRTWC286-SH 需要多少件？"


def test_cl43_the_dealer_did_you_mean_in_ms(session_factory, monkeypatch, stub_access):
    ms = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009623")
    out = ms.say("ada stok STWC2867", stock(product("STWC2867")))
    assert out == "Tidak dapat menemui STWC2867. Adakah anda maksudkan:" + DYM_TAIL
    assert ms.stored_question is not None and ms.stored_question["kind"] == "product_pick"


def test_cl43_the_dealer_did_you_mean_in_zh(session_factory, monkeypatch, stub_access):
    zh = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009624")
    out = zh.say("有 STWC2867 的库存吗", stock(product("STWC2867")))
    assert out == "找不到 STWC2867。您是指：" + DYM_TAIL


def test_cl43_the_picker_still_works_after_a_localized_did_you_mean(session_factory, monkeypatch, stub_access):
    """The stored pick keeps its English-keyed options: "1" still answers the first code."""
    from tests.chatbot._r9_engine_console import answer, reply

    c = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009625")
    c.say("ada stok STWC2867", stock(product("STWC2867")))
    out = c.say("1", reply(open_question_answer=answer("pick", picked=[1])))
    assert out == "Berapa unit SRTWC286-SH?"


# --------------------------------------------------------------------------- #
# AC-CL44: staff-edited English registry copy stays English; the shipped default translates
# --------------------------------------------------------------------------- #


def _edit_declined_copy(monkeypatch, wording: str) -> None:
    real = copy_mod.resolve

    def edited(db):
        canned = real(db)
        return dataclasses.replace(canned, templates={**canned.templates, "escalation_declined": wording})

    monkeypatch.setattr(copy_mod, "resolve", edited)


def test_cl44_the_shipped_registry_default_is_translated_in_an_ms_turn(session_factory, monkeypatch, stub_access):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009631")
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    c.say(MS_ASK, stock(product("ZZNOPE999")))
    assert c.say("tidak", _declined()) == "Rujukan dibatalkan."


def test_cl44_a_staff_edited_registry_copy_stays_english_in_an_ms_turn(session_factory, monkeypatch, stub_access):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009632")
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    c.say(MS_ASK, stock(product("ZZNOPE999")))
    _edit_declined_copy(monkeypatch, "No problem, I will leave it.")
    assert c.say("tidak", _declined()) == "No problem, I will leave it."
    assert c.state["reply_language"] == "ms"


# --------------------------------------------------------------------------- #
# AC-CL45: an en turn is unchanged
# --------------------------------------------------------------------------- #


def test_cl45_an_en_miss_keeps_the_english_offer_and_it_still_escalates(
    session_factory, monkeypatch, stub_access
):
    c = _staff_console(session_factory, monkeypatch, stub_access, "+60000009641")
    bodies = _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    assert c.say(EN_ASK, stock(product("ZZNOPE999"))) == EN_OFFER
    assert c.state["reply_language"] == "en"
    assert c.stored_question is not None and c.stored_question["kind"] == "team_pick"
    c.say("yes", _confirmation())
    assert len(bodies) == 1 and bodies[0]["team_code"] == "warehouse"


def test_cl45_an_en_barred_miss_and_declined_offer_are_unchanged(session_factory, monkeypatch, stub_access):
    phone = "+60000009642"
    c = _staff_console(session_factory, monkeypatch, stub_access, phone)
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    assert c.say(EN_ASK, stock(product("ZZNOPE999"))) == EN_OFFER
    assert c.say("no", _declined()) == "Escalation declined."
    _bar_escalation(session_factory, phone)
    assert c.say(EN_ASK, stock(product("ZZNOPE999"))) == MISS_LEAD + "Please refer to your salesman."


def test_cl45_an_en_dealer_quantity_question_is_unchanged(session_factory, monkeypatch, stub_access):
    qty = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009643")
    assert qty.say("check stock SRTWC286-SH", stock(product("SRTWC286-SH"))) == "How many units of SRTWC286-SH?"


def test_cl45_an_en_dealer_did_you_mean_is_unchanged(session_factory, monkeypatch, stub_access):
    dym = _dealer_console(session_factory, monkeypatch, stub_access, "+60000009644")
    out = dym.say("check stock STWC2867", stock(product("STWC2867")))
    assert out == "Couldn't find STWC2867. Did you mean:" + DYM_TAIL


@pytest.mark.parametrize("phone", ["+60000009645"])
def test_cl45_an_en_registry_edit_is_printed_as_edited(session_factory, monkeypatch, stub_access, phone):
    c = _staff_console(session_factory, monkeypatch, stub_access, phone)
    _capture_next_assignee(monkeypatch)
    _capture_sla(monkeypatch)
    c.say(EN_ASK, stock(product("ZZNOPE999")))
    _edit_declined_copy(monkeypatch, "No problem, I will leave it.")
    assert c.say("no", _declined()) == "No problem, I will leave it."
