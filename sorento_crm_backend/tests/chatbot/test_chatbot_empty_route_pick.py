"""CHATBOT-EMPTY-ROUTE-PICK (owner repro, 30 Sep 2026, Chatbot Console, contact Fanny Ng,
parser v37): "Zhin heng delivered on 23/9" inside an open DO list answered with

    Customer: Zhin heng / Product: all products / Dates: 23/09/2026

    Please choose who to route to (reply with the number):

    If you have no preference, just reply 'yes' and we'll assign automatically.
    No orders matched these.

and the parser emitted `open_question_answer {"mode": "pick", "items": [], "picked": [1]}`
although no question was open (the previous bot turn was the list, a data answer).

Two defects, one file:

* `order_list.list_reply` (R6, "inside an order list no escalate offer and no routing
  picker") took the picker's numbered rows out of the text but left the picker's header
  and its "reply 'yes'" close standing over nothing, and `_one_line_miss` dropped the
  "But no order matched these. Would you like me to escalate ..." line (the offer
  sentence rides on it), which is the blank line under the header.
* Nothing dropped the parser's declared answer to a question that was never asked: the
  `Open question:` line is built by `turn/question.open_question(pending, tasks)` off
  `State.pending` and the stock task, and when that is None every reader must see the
  null answer and no positions - a hallucinated pick must never influence the turn.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services.chatbot import order_list
from app.services.chatbot.order_list import EMPTY_LIST_LINE
from app.services.chatbot.turn import pending as pending_mod
from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn.compose import Answer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _order_ask
from tests.chatbot.test_samantha_27sep_r7_owner_replay import (  # noqa: F401 - fixture
    OWNER_TURNS,
    _header,
    owner_chat,
)
from tests.chatbot.test_top_selling_round5 import cat, route  # noqa: F401 - fixtures

PICKER_HEADER = "Please choose who to route to (reply with the number):"
PICKER_CLOSE = "If you have no preference, just reply 'yes' and we'll assign automatically."

#: The parser's post-processed output for the owner's turn, key for key (v37).
PHANTOM_PICK = {"mode": "pick", "items": [], "picked": [1]}


def _zhin_heng_ask(**extra: Any) -> dict[str, Any]:
    return _order_ask(
        entities=[{"raw": "Zhin heng", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
        document=["DO"],
        status="delivered",
        date_mode="range",
        date_filter_start="2026-09-23",
        date_filter_end="2026-09-23",
        routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries", "team_source": None},
        open_question_answer=dict(PHANTOM_PICK),
        **extra,
    )


# --------------------------------------------------------------------------- #
# 1. A declared answer with no open question is dropped (pure).
# --------------------------------------------------------------------------- #


def _drop(verdict: dict[str, Any], **flags: Any) -> tuple[dict[str, Any], bool]:
    """`without_phantom_answer` with nothing open and every flag off unless named."""
    flags = {"positions_read_elsewhere": False, "focus_has_product": False, **flags}
    return question_mod.without_phantom_answer(verdict, open_question=None, **flags)


class TestPhantomAnswerIsDropped:
    def test_no_open_question_drops_the_pick_and_the_positions(self) -> None:
        verdict = _parser_output(open_question_answer=dict(PHANTOM_PICK), reference_positions=[1])
        out, dropped = _drop(verdict)
        assert dropped is True
        assert out["open_question_answer"] == question_mod.NO_ANSWER
        assert out["reference_positions"] == []
        # The parser's other readings stand: the message is still the order ask it was.
        assert out["entities"] == verdict["entities"]
        assert out["message_type"] == verdict["message_type"]

    def test_a_declared_pick_drops_its_positions_even_with_a_product_in_focus(self) -> None:
        """The v37 phantom itself: a pick declared against no question indexes nothing,
        whatever the focus carries, so the positions that ride with it go too."""
        verdict = _parser_output(open_question_answer=dict(PHANTOM_PICK), reference_positions=[1])
        out, dropped = _drop(verdict, focus_has_product=True)
        assert dropped is True
        assert out["open_question_answer"] == question_mod.NO_ANSWER
        assert out["reference_positions"] == []

    def test_a_bare_number_with_a_product_in_focus_keeps_its_positions(self) -> None:
        """RELEASE-HOTFIX-0930B (owner ruling, round 4 hand test): "1" after the
        escalation of MWC-SC8609-PP went out, nothing open, is "that product again".
        The parser declared no pick (`open_question_answer` null), only a position;
        `reference_positions` is what keeps a casual-typed "1" out of idle chat
        (`turn/apply.py::_IDLE_CHAT_DISQUALIFIERS`), so with a product in focus the
        verdict is left exactly as it was."""
        verdict = _parser_output(
            message_type="casual", open_question_answer=dict(question_mod.NO_ANSWER), reference_positions=[1]
        )
        out, dropped = _drop(verdict, focus_has_product=True)
        assert dropped is False
        assert out is verdict
        assert out["reference_positions"] == [1]

    def test_a_bare_number_with_no_product_in_focus_loses_its_positions(self) -> None:
        """The same bare number with nothing in focus answers nothing: the positions go."""
        verdict = _parser_output(
            message_type="casual", open_question_answer=dict(question_mod.NO_ANSWER), reference_positions=[1]
        )
        out, dropped = _drop(verdict, focus_has_product=False)
        assert dropped is True
        assert out["open_question_answer"] == question_mod.NO_ANSWER
        assert out["reference_positions"] == []

    def test_an_open_question_keeps_the_answer(self) -> None:
        verdict = _parser_output(open_question_answer=dict(PHANTOM_PICK), reference_positions=[1])
        question = {"kind": "pick_one", "options": [{"position": 1, "code": "A"}], "owed": ["pick"]}
        out, dropped = question_mod.without_phantom_answer(
            verdict, open_question=question, positions_read_elsewhere=False, focus_has_product=False
        )
        assert dropped is False
        assert out is verdict

    def test_a_null_answer_with_no_question_is_left_alone(self) -> None:
        verdict = _parser_output(open_question_answer=dict(question_mod.NO_ANSWER), reference_positions=[])
        out, dropped = _drop(verdict)
        assert dropped is False
        assert out is verdict

    def test_a_question_answered_by_a_position_elsewhere_keeps_the_positions(self) -> None:
        """`lanes/ideate.py::build_arguments` reads `reference_positions` as the media
        selection while `ideation.pending_media` is outstanding, and `turn/apply.py`'s
        top selling rules read them while `focus.top_selling.asked` is set; neither is an
        `Open question:` object - the positions stay, only the declared answer goes."""
        verdict = _parser_output(open_question_answer=dict(PHANTOM_PICK), reference_positions=[1, 3])
        out, dropped = _drop(verdict, positions_read_elsewhere=True)
        assert dropped is True
        assert out["open_question_answer"] == question_mod.NO_ANSWER
        assert out["reference_positions"] == [1, 3]

    def test_positions_read_elsewhere_names_the_two_questions(self) -> None:
        from app.services.chatbot.engine import _positions_read_elsewhere
        from app.services.chatbot.turn.state import Focus, State

        assert _positions_read_elsewhere(State(focus=Focus())) is False
        assert _positions_read_elsewhere(State(focus=Focus(), ideation={"pending_media": [{"id": "m1"}]})) is True
        assert _positions_read_elsewhere(State(focus=Focus(top_selling={"asked": "who"}))) is True
        assert _positions_read_elsewhere(State(focus=Focus(top_selling={"rank_by": "amount"}))) is False


def test_the_first_one_answers_the_top_selling_who_question(session_factory, monkeypatch, cat) -> None:
    """Reviewer B1 on this lane: "the first one" under "Do you mean customer SAMPLE -
    FANNY NG or sales agent ...? Reply 1 for the customer, 2 for the sales agent." arrives
    as `reference_positions: [1]` (and, on a newer prompt, as a declared pick). That
    question is `focus.top_selling.asked`, not an `Open question:` object, so the drop
    must leave the positions to `turn/apply.py`'s `top_selling_who_is_the_customer`."""
    from tests.chatbot.test_top_selling_round5 import _asked_who, _calls, _position, _turn

    _asked_who(session_factory, monkeypatch)
    text, captured = _turn(
        session_factory, monkeypatch, _position(1, open_question_answer=dict(PHANTOM_PICK)), "the first one"
    )
    (args,) = _calls(captured)
    assert args["customer_ids"] == [cat.customers["SAMPLE - FANNY NG"]], (text, args)
    assert "sales_agent_ids" not in args, args


# --------------------------------------------------------------------------- #
# 2. The R6 list reply never leaves a picker frame with no rows (pure).
# --------------------------------------------------------------------------- #


class _Plan:
    def __init__(self) -> None:
        self.fetch = [type("F", (), {"domain": "order"})()]


def _member_offer(options: list[dict[str, Any]]) -> Any:
    return pending_mod.ask("member_offer", options, team="customer_service", asked_at_turn=1, payload={})


#: What the miss composer + member picker produced for the owner's turn, before R6 ran.
OWNER_MISS_TEXT = (
    "Customer: Zhin heng / Product: all products / Dates: 23/09/2026\n\n"
    "Here's what you want: delivery orders for Zhin heng on 23/09/2026\n"
    "• Zhin heng: not found\n\n"
    "But no order matched these. Would you like me to escalate to *Sorento* customer service team?\n\n"
    f"{PICKER_HEADER}\n1. Maryam Ariffin\n2. Ah Chong\n\n{PICKER_CLOSE}"
)


class TestListReplyDropsTheWholePicker:
    def _reply(self, text: str, options: list[dict[str, Any]], *, missed: bool = True) -> Answer:
        answer = Answer(text=text, question=_member_offer(options))
        envelope = {"has_result": not missed, "answers": [] if missed else [{"x": 1}]}
        return order_list.list_reply(
            answer, was_open=True, fetch_plan=_Plan(), envelopes=[envelope], order_status="delivered"
        )

    def test_the_owner_reply_is_the_header_and_one_line(self) -> None:
        options = [
            {"position": 1, "label": "Maryam Ariffin", "entity_type": "member"},
            {"position": 2, "label": "Ah Chong", "entity_type": "member"},
        ]
        out = self._reply(OWNER_MISS_TEXT, options)
        assert out.question is None
        assert out.text == (
            "Customer: Zhin heng / Product: all products / Dates: 23/09/2026\n"
            f"{EMPTY_LIST_LINE}"
        ), out.text

    def test_the_frame_goes_even_when_no_row_label_matched(self) -> None:
        """The defect's exact shape: the rows were taken out (or were never there) and
        the header and the close stood over nothing."""
        text = (
            "Customer: Zhin heng / Product: all products / Dates: 23/09/2026\n\n"
            f"{PICKER_HEADER}\n\n{PICKER_CLOSE}\n"
            f"{EMPTY_LIST_LINE}"
        )
        out = self._reply(text, [{"position": 1, "label": "Someone Else", "entity_type": "member"}])
        assert PICKER_HEADER not in out.text and "reply 'yes'" not in out.text, out.text
        lines = out.text.splitlines()
        assert [line for line in lines if line.strip()] == [
            "Customer: Zhin heng / Product: all products / Dates: 23/09/2026",
            EMPTY_LIST_LINE,
        ], out.text
        assert "\n\n\n" not in out.text, out.text

    def test_a_hit_list_loses_the_offer_sentence_with_its_picker(self) -> None:
        """A list that found rows and carried the silent-company offer (`answer_bridge.
        _silent_company_offer`): the offer sentence, the header, the rows and the close
        all go; the list stays."""
        text = (
            "Customer: Cheng Huat / Product: all products\n"
            "1. 202609-2571 (Sorento)\n\n"
            "Would you like me to escalate to *Mocha* customer service team?\n\n"
            f"{PICKER_HEADER}\n2. Maryam Ariffin\n\n{PICKER_CLOSE}"
        )
        out = self._reply(text, [{"position": 2, "label": "Maryam Ariffin", "entity_type": "member"}], missed=False)
        assert out.text == "Customer: Cheng Huat / Product: all products\n1. 202609-2571 (Sorento)", out.text

    def test_the_combined_roster_wording_goes_too(self) -> None:
        """`answer_bridge._cs_roster_text_block` / `reply_ladder`'s variant of the same
        frame ("To escalate, choose who to route to. Reply the number or name:" ...
        "Or just reply 'yes' and we'll assign automatically.")."""
        text = (
            "Customer: Zhin heng / Product: all products\n\n"
            "Here's what you want: delivery orders for Zhin heng\n"
            "• Zhin heng: not found\n\n"
            "But no order matched these. Would you like me to escalate to customer service team?\n\n"
            "To escalate, choose who to route to. Reply the number or name:\n"
            "1. Maryam Ariffin (Sorento / Mocha)\n"
            "[ Mocha: no customer-service members are configured - omitted. ]\n\n"
            "Or just reply 'yes' and we'll assign automatically."
        )
        out = self._reply(text, [{"position": 1, "label": "Maryam Ariffin", "entity_type": "member"}])
        assert out.text == f"Customer: Zhin heng / Product: all products\n{EMPTY_LIST_LINE}", out.text

    def test_a_group_header_that_is_a_data_field_stays(self) -> None:
        """`lanes/business/fetch.py` renders a field as `*Label:* value`, and an empty
        value leaves exactly `*Label:*` - only the dropped question's own company group
        headers go (reviewer N3)."""
        text = (
            "Customer: Zhin heng / Product: all products\n"
            "*Remarks:*\n\n"
            "Here's what you want: delivery orders for Zhin heng\n\n"
            "But no order matched these. Would you like me to escalate to customer service team?\n\n"
            f"{PICKER_HEADER}\n*Sorento:*\n1. Maryam Ariffin\n*Mocha:*\n2. Ah Chong\n\n{PICKER_CLOSE}"
        )
        question = pending_mod.ask(
            "member_offer",
            [{"position": 1, "label": "Maryam Ariffin"}, {"position": 2, "label": "Ah Chong"}],
            team="customer_service", asked_at_turn=1,
            payload={"roster_plan": [{"company_name": "Sorento"}, {"company_name": "Mocha"}]},
        )
        answer = Answer(text=text, question=question)
        out = order_list.list_reply(
            answer, was_open=True, fetch_plan=_Plan(), envelopes=[{"has_result": False}], order_status="delivered"
        )
        assert out.text == f"Customer: Zhin heng / Product: all products\n*Remarks:*\n{EMPTY_LIST_LINE}", out.text

    def test_a_first_ask_is_untouched(self) -> None:
        answer = Answer(text=OWNER_MISS_TEXT, question=_member_offer([{"position": 1, "label": "Maryam Ariffin"}]))
        out = order_list.list_reply(
            answer, was_open=False, fetch_plan=_Plan(), envelopes=[{"has_result": False}], order_status="delivered"
        )
        assert out is answer


class TestNoPickerWithZeroRows:
    """AC-5: `answer_bridge._miss_company_picker` never prints the frame over nothing."""

    def _picker(self, monkeypatch, *, rows_become_options: bool):
        from app.services.chatbot import answer_bridge
        from app.services.chatbot.tail import member_offer as member_mod

        monkeypatch.setattr(
            member_mod, "fetch_rosters",
            lambda db, plan, ctx: [{"body": [{"user_id": "u1", "respond_user_id": "ru1", "name": "Maryam Ariffin"}]}],
        )
        if not rows_become_options:
            monkeypatch.setattr(answer_bridge, "member_option", lambda row, position: None)
        return answer_bridge._miss_company_picker(
            "Customer: Zhin heng", "Would you like me to escalate to *Sorento* customer service team?",
            company={"id": "c1", "name": "Sorento"},
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
            db=object(), ctx={}, asked_at_turn=1, brand=None,
        )

    def test_a_roster_with_rows_prints_them(self, monkeypatch) -> None:
        text, question = self._picker(monkeypatch, rows_become_options=True)
        assert f"{PICKER_HEADER}\n1. Maryam Ariffin\n\n{PICKER_CLOSE}" in text, text
        assert question.kind == "member_offer" and len(question.options) == 1

    def test_no_option_means_no_picker(self, monkeypatch) -> None:
        assert self._picker(monkeypatch, rows_become_options=False) is None


# --------------------------------------------------------------------------- #
# 3. The owner's turn, replayed through the real engine.
# --------------------------------------------------------------------------- #


def _trace_of(chat) -> list[dict[str, Any]]:
    from app.models.chatbot_turn import ChatbotTurn

    db = chat.session_factory()
    row = db.query(ChatbotTurn).filter(ChatbotTurn.message_id == f"ZZT-r3-brand-carry-{chat._turn}").first()
    assert row is not None
    return list(row.trace or [])


def _understood(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return next(r for r in trace if r.get("stage") == "understood")


def _open_question(chat) -> dict[str, Any] | None:
    from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _open_question_of

    q = _open_question_of(chat.session_factory)
    return q or None


def _focus_products_of(chat) -> list[dict[str, Any]]:
    """The persisted focus's product entries (`session_vars.focus.products`), or `[]`."""
    import json

    from sqlalchemy import text

    from tests.chatbot.test_engine import CONTACT_ID

    db = chat.session_factory()
    try:
        row = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": str(CONTACT_ID)}
        ).first()
    finally:
        db.close()
    raw = row.session_vars if row is not None else {}
    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return list((parsed.get("focus") or {}).get("products") or [])


@pytest.fixture
def one_cs_member(monkeypatch):
    """The customer service roster the owner's contact sees: one member, so the miss
    composer builds the member picker exactly as it did on 30 Sep."""
    from app.services.chatbot.tail import member_offer as member_mod

    def stub_fetch_rosters(db, plan, ctx):
        return [{"body": [{"user_id": "u1", "respond_user_id": "ru1", "name": "Maryam Ariffin"}]}]

    monkeypatch.setattr(member_mod, "fetch_rosters", stub_fetch_rosters)


ZHIN_HENG_FAMILIES = ("ZHIN HENG HARDWARE & TRADING SDN BHD", "ZHIN HENG HOMEMART SDN BHD")


@pytest.fixture
def zhin_heng_is_two_customers(owner_chat, monkeypatch):
    """What the owner's dev DB resolves "Zhin heng" to (v40 run, 30 Sep 2026): two
    families, "ZHIN HENG HARDWARE & TRADING SDN BHD (SRT)" and "ZHIN HENG HOMEMART SDN
    BHD (SRT)". The harness resolver knows only cheng huat sentul; this one adds them."""
    import uuid as uuid_mod

    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.conftest import validating_resolve_entity

    chat = owner_chat
    ids = {name: str(uuid_mod.uuid4()) for name in ZHIN_HENG_FAMILIES}

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        tokens = list(body.get("tokens") or [])
        resolutions = []
        unresolved = []
        for t in tokens:
            if t.casefold() == "cheng huat sentul":
                resolutions.append({"token": t, "resolved": True, "matches": [
                    {"uuid": lid, "entity_type": "customer", "canonical_code": t, "match_tier": "exact"}
                    for lid in (chat.sorento_ledger, chat.mocha_ledger)
                ]})
            elif t.casefold() == "zhin heng":
                resolutions.append({"token": t, "resolved": True, "matches": [
                    {
                        "uuid": ids[name], "entity_type": "customer", "canonical_code": f"3000/Z{i}",
                        "match_tier": "fuzzy", "company_code": "SRT",
                        "display": {"customer_name": name, "debtor_name": name},
                    }
                    for i, name in enumerate(ZHIN_HENG_FAMILIES, 1)
                ]})
            else:
                unresolved.append(t)
        return {"tokens": tokens, "resolutions": resolutions, "unresolved_tokens": unresolved}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(resolve_entity),
        probe=lambda **_: None,
    )
    monkeypatch.setattr(engine_mod.business_services, "production_services", lambda db, *, space_id=None: services)
    return ids


def test_owner_turn_inside_the_open_list_asks_which_customer(owner_chat, one_cs_member, zhin_heng_is_two_customers) -> None:
    """The owner's turn. The phantom pick rides as `reference_positions: [1]` as well
    (the console's post-processed output showed `picked [1]`): `lanes/business/gate.py`
    reads a non-empty `reference_positions` as "a pick was already applied" and skips
    "Which customer do you mean?" - which is how an ambiguous "Zhin heng" fell through
    to a fetch it could not scope and "No orders matched these." (item 3 of the lane).
    With the phantom answer dropped, v37's emission asks the same question v40's did.
    The positions go with the DECLARED pick (RELEASE-HOTFIX-0930B: a bare number with a
    product in focus keeps them; a declared pick against nothing never does)."""
    chat = owner_chat
    # The list is open: the previous bot turn was a data answer, no question.
    chat.turn(OWNER_TURNS[5][0], OWNER_TURNS[5][1])
    assert _open_question(chat) in (None, {}), "test setup: nothing is open before the owner's turn"
    assert _focus_products_of(chat) == [], "test setup: the open list carries a customer, no product"

    reply, calls, _ = chat.turn("Zhin heng delivered on 23/9", _zhin_heng_ask(reference_positions=[1]))

    assert chat.kind() == "business_query", reply
    assert "Which customer do you mean? Please choose:" in reply, reply
    assert all(name in reply for name in ZHIN_HENG_FAMILIES), reply
    assert PICKER_HEADER not in reply and "reply 'yes'" not in reply, reply
    assert (_open_question(chat) or {}).get("kind") == "customer_pick", _open_question(chat)

    trace = _trace_of(chat)
    derived = (_understood(trace).get("raw") or {}).get("derived") or {}
    assert derived.get("open_question_answer") == question_mod.NO_ANSWER, derived.get("open_question_answer")
    assert derived.get("reference_positions") == [], derived
    dropped = [e for e in trace if e.get("kind") == "phantom_answer"]
    assert dropped and dropped[0].get("open_question_answer") == PHANTOM_PICK, trace
    assert dropped[0].get("reference_positions") == [1], dropped


def test_an_empty_list_inside_the_open_list_has_no_picker_frame(owner_chat, one_cs_member) -> None:
    """The empty-picker half of the owner's reply, on the harness's own words: a miss
    inside the open list, with a customer service roster to offer. R6 takes the
    routing picker out; nothing of its frame may stay."""
    chat = owner_chat
    chat.turn(OWNER_TURNS[5][0], OWNER_TURNS[5][1])
    reply, calls, _ = chat.turn(
        "what about 2019",
        _parser_output(
            domain_hint="order", intent_hint="check_order", entities=[], domain_in_message=False,
            # January only: a whole year is over the 31-day cap and would be refused, not listed
            # (DO-ASK-SIMPLIFY, owner 4 Oct 2026); this test needs an empty list.
            date_mode="range", date_filter_start="2019-01-01", date_filter_end="2019-01-31",
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries", "team_source": None},
        ),
    )
    assert [a for n, a in calls if n == ORDERS_LIST], calls
    assert chat.kind() == "business_query", reply
    lines = reply.strip().splitlines()
    assert lines[-1] == EMPTY_LIST_LINE, reply
    assert PICKER_HEADER not in reply and "reply 'yes'" not in reply, reply
    assert "escalate" not in reply.casefold(), reply
    assert all(line.strip() for line in lines), f"no blank line stands where a picker was: {reply!r}"
    assert _open_question(chat) in (None, {}), "no routing picker is left open"


def test_owner_turn_as_a_first_ask_answers_the_ask_not_the_pick(owner_chat, one_cs_member) -> None:
    """The same emission with no list open and a customer nobody resolves: the pick is
    dropped all the same and the turn is answered as the order ask it is (a first ask
    keeps its escalate offer). Nothing is in focus on a first ask, so the positions go
    on both counts (RELEASE-HOTFIX-0930B: declared pick, and no product in focus)."""
    chat = owner_chat
    reply, calls, _ = chat.turn("Zhin heng delivered on 23/9", _zhin_heng_ask(reference_positions=[1]))
    assert chat.kind() == "business_query", reply
    assert _focus_products_of(chat) == [], "a first ask carries no product into focus"
    derived = (_understood(_trace_of(chat)).get("raw") or {}).get("derived") or {}
    assert derived.get("open_question_answer") == question_mod.NO_ANSWER, derived
    assert derived.get("reference_positions") == [], derived
    # AC-7: a first ask keeps its escalate offer and its picker, rows and all.
    assert reply == (
        'Couldn\'t find: "Zhin heng" (customer). Would you like me to escalate to customer service team?\n\n'
        f"{PICKER_HEADER}\n1. Maryam Ariffin\n\n{PICKER_CLOSE}"
    ), reply
    assert (_open_question(chat) or {}).get("kind") == "member_offer", _open_question(chat)
