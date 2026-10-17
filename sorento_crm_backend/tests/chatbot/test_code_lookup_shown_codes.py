"""CHAT-CODE-LOOKUP R2-7: the `replied` trace record's facts gain `shown_codes`, every product code
the reply LISTS in a "Which product do you mean" picker, a did-you-mean or a "Related products"
list, in display order; None when the reply lists none. Trace only: the reply text is unchanged and
the key is ungated (code_lookup on or off). Harnesses reused: tests/chatbot/test_code_lookup_held_family.py
(family ZZTWC286, `_turns`) and tests/chatbot/test_code_lookup_refusal.py (`_seed`). Reads the trace the
way tests/chatbot/test_ai_allowed_gate.py does: the persisted ChatbotTurn row."""
from __future__ import annotations

import json

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.chatbot_turn import ChatbotTurn
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_code_lookup_held_family import _ask, _product, _seed as _seed_family, _turns  # noqa: F401
from tests.chatbot.test_code_lookup_refusal import _seed as _seed_codes
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401

PICKER_CODES = ["ZZTWC286-BL", "ZZTWC286-GM", "ZZTWC286-NW", "ZZTWC286-P", "ZZTWC286-SH", "ZZTWC286-SH-NEW"]
DYM_CODES = ["ZZTWC286-P", "ZZTWC286-BL", "ZZTWC286-GM"]
RELATED_CODES = ["ZZRL4410", "ZZRL4410-BL", "ZZRL4410-GM"]

PICKER_TEXT = (
    "Which product do you mean? Please choose:\n1. ZZTWC286-BL - no incoming\n2. ZZTWC286-GM - no incoming\n"
    "3. ZZTWC286-NW - no incoming\n4. ZZTWC286-P - no incoming\n5. ZZTWC286-SH - no incoming\n"
    "6. ZZTWC286-SH-NEW - no incoming\n\nNone of these have incoming stock right now."
)
DYM_TEXT = (
    'Couldn\'t find "zztwc287" (product). Did you mean:\n1. ZZTWC286-P - no stock details\n'
    "2. ZZTWC286-BL - no stock details\n3. ZZTWC286-GM - no stock details\n"
    "Reply with a code to continue, or would you like me to escalate to warehouse team?"
)
RELATED_TEXT = (
    "No incoming stock (ETA) found for ZZRL4410.\nNo incoming and no stock for ZZRL4410.\n\nRelated products:\n"
    "1. ZZRL4410 - no incoming\n2. ZZRL4410-BL - no incoming\n3. ZZRL4410-GM - no incoming\n"
    "Reply with a number to check its incoming, or reply 'yes' to escalate to purchasing team."
)
PLAIN_TEXT = (
    "Here's what you want:\n• product: ZZTWC286-P\n\nBut no inventory matched these.\n"
    "No stock and no incoming for ZZTWC286-P.\n\nWould you like me to escalate to warehouse team?"
)


def _replied_facts(session_factory, result) -> dict:
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    facts = [r["facts"] for r in (row.trace or []) if isinstance(r, dict) and r.get("stage") == "replied"]
    assert len(facts) == 1, facts
    return facts[0]


def _picker(session_factory, stub_access, monkeypatch, code_lookup=True):
    _seed_family(session_factory)
    v = _ask("check_incoming", "incoming", _product("zztwc286"))
    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("zztwc286 eta", v)], code_lookup=code_lookup)
    return r


def _dym(session_factory, stub_access, monkeypatch, code_lookup=True):
    _seed_family(session_factory)
    unknown = {"raw": "zztwc287", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
    v = _ask("check_stock", "inventory", unknown)
    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("stock zztwc287", v)], code_lookup=code_lookup)
    return r


def _related(session_factory, stub_access, monkeypatch, code_lookup=True):
    _seed_codes(session_factory, "ZZRL4410", "ZZRL4410-GM", "ZZRL4410-BL")
    v = _ask("check_incoming", "incoming", _product("ZZRL4410"))
    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("ZZRL4410 eta", v)], code_lookup=code_lookup)
    return r


def _text(r) -> str:
    return (r.reply or {}).get("text") or ""


def test_which_product_picker_records_shown_codes(session_factory, seeded, stub_access, monkeypatch):
    r = _picker(session_factory, stub_access, monkeypatch)

    assert _replied_facts(session_factory, r).get("shown_codes") == PICKER_CODES


def test_did_you_mean_records_shown_codes(session_factory, seeded, stub_access, monkeypatch):
    r = _dym(session_factory, stub_access, monkeypatch)

    assert _replied_facts(session_factory, r).get("shown_codes") == DYM_CODES


def test_related_products_records_shown_codes(session_factory, seeded, stub_access, monkeypatch):
    r = _related(session_factory, stub_access, monkeypatch)

    assert _replied_facts(session_factory, r).get("shown_codes") == RELATED_CODES


def test_a_plain_answer_records_no_shown_codes(session_factory, seeded, stub_access, monkeypatch):
    _seed_family(session_factory)
    v = _ask("check_stock", "inventory", _product("ZZTWC286-P"))
    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("stock ZZTWC286-P", v)])

    facts = _replied_facts(session_factory, r)
    assert "shown_codes" in facts and facts["shown_codes"] is None, facts


@pytest.mark.parametrize("code_lookup", [True, False])
@pytest.mark.parametrize(
    "build, expected", [(_picker, PICKER_TEXT), (_dym, DYM_TEXT), (_related, RELATED_TEXT)], ids=["picker", "dym", "related"]
)
def test_shown_codes_do_not_change_the_reply(build, expected, code_lookup, session_factory, seeded, stub_access, monkeypatch):
    """Ungated, and the text of each list reply is today's, byte for byte (green today, stays green)."""
    assert _text(build(session_factory, stub_access, monkeypatch, code_lookup)) == expected


# ---- reviewer blocker on d77bf95b45: two listed shapes escaped the trace

ONE_CODE = "ZZQXW55510"
ONE_TEXT_CONTACT = (
    'Couldn\'t find "zzqxw55519" (product). Did you mean:\n1. ZZQXW55510 - no stock details\n'
    "Reply with a code to continue, or would you like me to escalate to warehouse team?"
)
ONE_TEXT_STAFF = (
    'Couldn\'t find "zzqxw55519" (product). Did you mean:\n1. ZZQXW55510 - no stock details\nReply with a code to continue.'
)


def _one_code_dym(session_factory, stub_access, monkeypatch, *, staff: bool):
    from sqlalchemy import text as sql

    _seed_codes(session_factory, ONE_CODE)
    if staff:
        db = session_factory()
        db.execute(sql("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb)"), {"p": json.dumps({"tier": "office"})})
        db.commit()
    unknown = {"raw": "zzqxw55519", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
    v = _ask("check_stock", "inventory", unknown)
    [(r, _c)] = _turns(session_factory, stub_access, monkeypatch, [("stock zzqxw55519", v)])
    return r


def test_one_code_did_you_mean_records_its_code(session_factory, seeded, stub_access, monkeypatch):
    """One candidate falls under the roster guard and becomes a team_pick offer (answer_bridge.py:1337,
    :1384-1392); the reply still lists the code."""
    r = _one_code_dym(session_factory, stub_access, monkeypatch, staff=False)

    assert _text(r) == ONE_TEXT_CONTACT
    assert _replied_facts(session_factory, r).get("shown_codes") == [ONE_CODE]


def test_one_code_did_you_mean_for_staff_records_its_code(session_factory, seeded, stub_access, monkeypatch):
    """Staff: the offer is stripped, the question is None, the reply still lists the code."""
    r = _one_code_dym(session_factory, stub_access, monkeypatch, staff=True)

    assert _text(r) == ONE_TEXT_STAFF
    assert _replied_facts(session_factory, r).get("shown_codes") == [ONE_CODE]


def test_a_listed_set_code_is_recorded():
    """A roster whose first row is a set becomes `product_set_pick`, and set rows carry entity_type
    "product_set" (entity_resolver.py:985): _shown_codes must keep them, in display order with the
    products (a roster builds its options through answer_bridge._roster_option)."""
    from types import SimpleNamespace

    from app.services.chatbot.answer_bridge import _roster_option
    from app.services.chatbot.engine import _shown_codes

    rows = [
        {"entity_type": "product_set", "code": "ZZSETQ7701-RL", "uuid": "u1"},
        {"entity_type": "product", "code": "ZZPRQ7701-GM", "uuid": "u2"},
        {"entity_type": "product_set", "code": "ZZSETQ7701-P-RL", "uuid": "u3"},
    ]
    for kind in ("product_set_pick", "product_pick"):
        options = [_roster_option(row, kind=kind, position=i + 1) for i, row in enumerate(rows)]
        question = SimpleNamespace(kind=kind, options=options)

        assert _shown_codes(question) == ["ZZSETQ7701-RL", "ZZPRQ7701-GM", "ZZSETQ7701-P-RL"], kind


# ---- reviewer on 0bb2beb109: the compose did-you-mean (turn/compose.py:491-505) and the escalation
# lane's own product did-you-mean (lanes/escalation.py:836-849 -> run_tail) list codes too

COMPOSE_HEAD = (
    "Stock availability:\n\n*Product Code:* SRTWC286\n*Warehouse:* BUKIT RAJA\n*Quantity On Hand:* 12\n\n"
    'Couldn\'t find "srt5764" (product). Did you mean:\n'
)
COMPOSE_MULTI_TEXT = COMPOSE_HEAD + (
    "1. SRT57-CR\n2. SRT5713\n3. SRT5732\nReply with a code to continue, or would you like me to escalate to warehouse team?"
)
COMPOSE_ONE_TEXT = COMPOSE_HEAD + "1. SRT57-CR\nReply with a code to continue, or would you like me to escalate to warehouse team?"
COMPOSE_ONE_STAFF_TEXT = COMPOSE_HEAD + "1. SRT57-CR\nReply with a code to continue."


def _compose_shown(answer):
    """What the engine's replied record reads off a composed answer (engine.py:7979)."""
    from app.services.chatbot.engine import _shown_codes

    return _shown_codes(answer.question) or getattr(answer, "shown_codes", None)


def _compose_answer(suggestions, profile=None):
    from tests.chatbot.test_multi_code_dym import _answer, _envelope

    return _answer(_envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": suggestions}), profile)


def test_answered_code_plus_suggested_token_records_the_suggestions():
    from tests.chatbot.test_multi_code_dym import SUGGEST

    answer = _compose_answer(SUGGEST)

    assert answer.text == COMPOSE_MULTI_TEXT
    assert _compose_shown(answer) == ["SRT57-CR", "SRT5713", "SRT5732"]


def test_answered_code_plus_one_suggestion_records_it():
    """One offered code is a team_pick escalate offer, not a product_pick."""
    from tests.chatbot.test_multi_code_dym import SUGGEST

    answer = _compose_answer(SUGGEST[:1])

    assert answer.text == COMPOSE_ONE_TEXT
    assert _compose_shown(answer) == ["SRT57-CR"]


def test_answered_code_plus_one_suggestion_for_staff_records_it():
    """Staff: no offer, the question is None, the reply still lists the code."""
    from app.services.chatbot.turn.state import Profile
    from tests.chatbot.test_multi_code_dym import SUGGEST

    answer = _compose_answer(SUGGEST[:1], Profile(tier="office"))

    assert answer.text == COMPOSE_ONE_STAFF_TEXT
    assert _compose_shown(answer) == ["SRT57-CR"]


@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_escalation_did_you_mean_records_its_codes(session_factory, stub_access, monkeypatch):
    """A real escalation turn: "escalate <typo'd code> to the marketing team" asks the lane's own product
    did-you-mean (a team_pick over product options) through engine.run_tail, which hardcodes shown_codes None."""
    from app.services.chatbot import engine as engine_mod
    from tests.chatbot._turn_helpers import entity, verdict
    from tests.chatbot.test_engine import _envelope, _parser_output  # noqa: F401
    from tests.chatbot.test_escalation_agent_carry import _seed_contact, _seed_product

    _seed_contact(session_factory, phone="+60900000777")
    for code in ("ZZEWC8609-PP", "ZZEWC8606-PP"):
        _seed_product(session_factory, code=code)
    parsed = verdict(
        message_type="business_query", domain_hint="master_products", intent_hint="check_product",
        user_goal="please escalate ZZEWC8607-PP water closet to the marketing team",
        entities=[entity("ZZEWC8607-PP", hint="product", confident=False)],
        routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )
    _install_parser(monkeypatch, parsed)
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert _text(result) == (
        'Couldn\'t find "ZZEWC8607-PP". Did you mean:\n1. ZZEWC8606-PP\n2. ZZEWC8609-PP\n'
        "Reply with the number and I will pass it to the marketing product team."
    )
    assert _replied_facts(session_factory, result).get("shown_codes") == ["ZZEWC8606-PP", "ZZEWC8609-PP"]


def _install_parser(monkeypatch, parsed) -> None:
    from app.services.chatbot.head import parser as parser_mod
    from tests.chatbot.test_engine import stub_access as _unused  # noqa: F401

    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        ),
    )
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: parsed)


# ---- reviewer on df16528084: shown_codes must hold only the codes THIS reply shows


def test_a_pick_that_misses_over_an_open_roster_records_only_what_the_reply_shows(session_factory, monkeypatch):
    """Hand pass 9 D1 flow (tests/chatbot/test_rearch_r9_handpass9_replay.py): turn 1 rosters A, B, C; turn 2
    picks A, a genuine miss, and the miss re-arms the carried roster as answer.question (contract 36,
    turn/compose.py:951). The reply shows the picked product and the miss line, not B or C."""
    from tests._pg_fixture import unique_code
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_outstanding_lane import _session_of
    from tests.chatbot.test_rearch_r5_production_decides import (
        _mcp_double,
        _run_turn_real,
        _run_turn_with_mcp_call,
        _seed_contact_and_get,
    )
    from tests.chatbot.test_rearch_r9_handpass9_replay import _photo_roster_ask_qf, _position_of, _seed_photo_family

    _seed_contact_and_get(session_factory)
    base = unique_code("ZZTSC7PHA").replace("-", "")
    family = _seed_photo_family(session_factory, base)
    a_code, _a_id = family["a"]
    _run_turn_real(
        session_factory, monkeypatch, qf=_photo_roster_ask_qf(base),
        text_body=f"photo for {base}", msg_id="zzt-sc7-roster", mcp_response={"data": []},
    )
    options = (_session_of(session_factory).get("open_question") or {}).get("options") or []
    position = _position_of(options, a_code)
    assert len(options) == 3 and position is not None, options
    qf2 = _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[], reference_positions=[position],
    )

    result2 = _run_turn_with_mcp_call(
        session_factory, monkeypatch, qf=qf2, text_body=str(position), msg_id="zzt-sc7-pick",
        mcp_call=_mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))[0],
    )

    reply = _text(result2)
    assert f"• product: {a_code}" in reply and "But no Product Photos matched these." in reply, reply
    shown = _replied_facts(session_factory, result2).get("shown_codes") or []
    assert all(code in reply for code in shown), (shown, reply)


def test_a_lane_question_does_not_hide_this_turns_did_you_mean_codes():
    """The replied record reads `_shown_codes(question) or answer.shown_codes`, so a question that carries
    codes the reply does not show hides the did-you-mean codes it does. Unit level (no real turn mints both):
    an Answer whose own list is X, Y while its question carries the carried roster A, B, C. The seam the test
    names, `engine._replied_shown_codes(answer)`, is the one place the replied record's value is computed
    (it replaces the inline expression at engine.py:7979); the coder adds it."""
    from types import SimpleNamespace

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.turn.compose import Answer

    carried = SimpleNamespace(
        kind="product_pick",
        options=[{"entity_type": "product", "code": c} for c in ("ZZCARRY-A", "ZZCARRY-B", "ZZCARRY-C")],
    )
    answer = Answer(text="Did you mean:\n1. ZZSHOWN-X\n2. ZZSHOWN-Y", question=carried, shown_codes=["ZZSHOWN-X", "ZZSHOWN-Y"])

    assert engine_mod._replied_shown_codes(answer) == ["ZZSHOWN-X", "ZZSHOWN-Y"]
