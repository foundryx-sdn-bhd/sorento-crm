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
