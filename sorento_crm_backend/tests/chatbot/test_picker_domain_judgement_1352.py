"""Issue #1352: a pick never overrides the message's own domain; the parser reports, the
engine judges.

Owner, 29 Sep 2026 00:5x MYT: "no I don't like this, I want the resolving to be still
semantic, otherwise it is fragile, we need to find a methodology to maintain the sticky
picker while solving our problem". 01:0x MYT: "does it handle for like multi turn of
different picker ... also i thought the resolving of typed code to the reference position
is the parser job?"

So the sticky roster stays exactly as retained today, the parser still resolves every pick
semantically (a number, a number word, an ordinal, a typed option label), and it fills the
domain fields as the message itself says. The engine reads the two together:

  J1 pick, no domain word          -> the pick, in the roster's domain (unchanged)
  J2 domain word, no pick          -> an ordinary ask over the carried subject, roster kept
  J3 pick AND domain word          -> the pick, in the message's own domain, roster kept
  J4 typed option label            -> the parser's position, judged by J1 / J3
  J5 domain_in_message, no domain  -> nothing to judge against: J1

Every verdict below is the parser's reading, stubbed as the new prompt asks for it; no test
feeds the engine a customer's words to decide on. Plan:
`documentation/plans/chatbot/PLAN-picker-domain-judgement-29sep.md`; UAC ids in each test.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.turn.decide import decide
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.state import Focus

from tests.chatbot._r9_engine_console import EngineConsole, answer, product
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

INCOMING = "crm_incoming_stock_list"
STOCK = "crm_inventory_stock_balance_list"
FAMILY_HEAD = "Which product do you mean? Please choose:"


# =============================================================================== #
# The parser's readings (the new prompt: every field filled as the message says)
# =============================================================================== #


def _incoming_ask() -> dict[str, Any]:
    """"incoming srtwc286"."""
    return verdict(
        domain_hint="incoming",
        intent_hint="check_incoming",
        domain_in_message=True,
        entities=[product("srtwc286")],
    )


def _bare_pick(n: int, carried_domain: str | None = "incoming") -> dict[str, Any]:
    """"4" / "the fourth" / "four": a position, no domain word. The parser may carry the
    previous turn's domain_hint (the prompt's continuation rule); `domain_in_message`
    false is what says the message named none."""
    return verdict(
        message_type="business_query",
        domain_hint=carried_domain,
        domain_in_message=False,
        reference_positions=[n],
        open_question_answer=answer("pick", picked=[n]),
    )


def _check_stock(*, pick: int | None = None) -> dict[str, Any]:
    """"check stock": the domain word, over the carried subject. With `pick` the parser
    ALSO reports the roster position it sees (the production verdict replayed [4])."""
    extra: dict[str, Any] = {}
    if pick is not None:
        extra = {"reference_positions": [pick], "open_question_answer": answer("pick", picked=[pick])}
    return verdict(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True, **extra)


def _stock_for_position(n: int) -> dict[str, Any]:
    """"4 stock" / "stock for the 4th"."""
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        domain_in_message=True,
        reference_positions=[n],
        open_question_answer=answer("pick", picked=[n]),
    )


def _typed_label(code: str, position: int | None) -> dict[str, Any]:
    """"stoick SRTWC286-SH-NEW": the typo read for meaning (inventory), the typed code its
    entity and, when the parser resolved it onto the roster (owner ruling 3), its position."""
    extra: dict[str, Any] = {}
    if position is not None:
        extra = {"reference_positions": [position], "open_question_answer": answer("pick", picked=[position])}
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        domain_in_message=True,
        entities=[product(code)],
        **extra,
    )


# =============================================================================== #
# A. The judgement, pure (`decide.Decision.own_domain`, AC-PK032)
# =============================================================================== #


def _roster() -> Any:
    options = [
        {"position": i, "label": code, "code": code, "entity_type": "product", "uuid": f"u{i}", "uuids": [f"u{i}"], "payload": {}}
        for i, code in enumerate(["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-200", "SRTWC286-SH-NEW"], 1)
    ]
    return pending_ask("product_pick", options, asked_at_turn=1, payload={"domain": "incoming"})


@pytest.mark.parametrize(
    ("reading", "own"),
    [
        (_bare_pick(4), False),  # J1
        (_bare_pick(4, carried_domain=None), False),  # J1, nothing carried
        (_stock_for_position(4), True),  # J3
        (_check_stock(pick=4), True),  # J3, the replayed pick
        (verdict(domain_in_message=True, reference_positions=[4]), False),  # J5
        (verdict(reference_positions=[4], asks=[{"domain": "inventory"}]), True),  # asks alone
    ],
)
def test_a_decision_carries_whether_the_message_named_its_own_domain(reading, own):
    """AC-PK032: the position AND the domain fields, read together, once."""
    decision = decide(reading, Focus(domains=["incoming"]), _roster())
    assert decision.answers and decision.positions == (4,)
    assert decision.own_domain is own
    assert bool(decision.as_trace().get("own_domain")) is own


def test_a_a_domain_word_with_no_pick_answers_nothing_on_the_roster():
    """AC-PK002 (J2), the reading half: no position, so nothing on the roster is answered."""
    decision = decide(_check_stock(), Focus(domains=["incoming"]), _roster())
    assert not decision.answers and decision.positions == ()


# =============================================================================== #
# B. The production cases, through `engine.run_turn` (console harness)
# =============================================================================== #


def _console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    """A contact who may see quantities: the stock tool ANSWERS ("Total: 12"), so a stock
    turn is an answer, never the dealer's "How many units?" (that flow is AC-PK014)."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 12
    return c


def _fetched(c: EngineConsole, since: int) -> list[tuple[str, list[str]]]:
    """Every tool call since `since`, as `(tool, [codes])`."""
    return [(name, [c.codes[p] for p in args.get("product_ids") or []]) for name, args in c.tool_calls[since:]]


def _say(c: EngineConsole, message: str, reading: dict[str, Any]) -> tuple[str, list[tuple[str, list[str]]]]:
    since = len(c.tool_calls)
    text = c.say(message, reading)
    return text, _fetched(c, since)


def _answered_positions(c: EngineConsole) -> list[int] | None:
    question = c.stored_question
    if question is None:
        return None
    return list((question.get("payload") or {}).get("answered_positions") or [])


def _picked_roster(c: EngineConsole) -> None:
    """"incoming srtwc286" -> the ten-variant roster -> "4" (J1, AC-PK001)."""
    text, calls = _say(c, "incoming srtwc286", _incoming_ask())
    assert text.startswith(FAMILY_HEAD), text
    assert c.stored_question["kind"] == "product_pick"
    text, calls = _say(c, "4", _bare_pick(4))
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW"]), calls
    assert "pick_in_roster_domain" in c.last_trace.rules_fired, c.last_trace.rules_fired
    assert "domain_locked_by_pick" not in c.last_trace.rules_fired
    assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == [4]


def test_b_case_1_check_stock_is_a_stock_ask_and_7_still_picks(session_factory, monkeypatch, stub_access):
    """AC-PK001, AC-PK002, AC-PK005, AC-PK013. "incoming srtwc286" -> "4" -> "check stock"
    answers stock for SRTWC286-SH-NEW with the roster still stored; then "7" picks from it,
    in the roster's own domain."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013521")
    _picked_roster(c)

    text, calls = _say(c, "check stock", _check_stock())
    assert calls == [(STOCK, ["SRTWC286-SH-NEW"])], calls
    assert "*Total:* 12" in text, text
    assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == [4]

    text, calls = _say(c, "7", _bare_pick(7, carried_domain="inventory"))
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW-P"]), calls
    assert "pick_in_roster_domain" in c.last_trace.rules_fired
    assert _answered_positions(c) == [4, 7]


def test_b_case_1_with_the_pick_replayed_answers_stock(session_factory, monkeypatch, stub_access):
    """AC-PK003, AC-PK030, AC-PK031: the parser still reports the roster position it sees
    for "check stock" AND the domain word. The pick answers in the MESSAGE's domain."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013522")
    _picked_roster(c)

    text, calls = _say(c, "check stock", _check_stock(pick=4))
    assert calls == [(STOCK, ["SRTWC286-SH-NEW"])], calls
    assert "*Total:* 12" in text, text
    rules = c.last_trace.rules_fired
    assert "pick_in_message_domain" in rules and "domain_locked_by_pick" not in rules, rules
    assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == [4]

    text, calls = _say(c, "7", _bare_pick(7, carried_domain="inventory"))
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW-P"]), calls


def test_b_4_stock_answers_stock_for_the_4th(session_factory, monkeypatch, stub_access):
    """AC-PK003: "4 stock" straight after the roster is the 4th option's stock."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013523")
    text, _ = _say(c, "incoming srtwc286", _incoming_ask())
    assert text.startswith(FAMILY_HEAD), text
    text, calls = _say(c, "4 stock", _stock_for_position(4))
    assert calls == [(STOCK, ["SRTWC286-SH-NEW"])], calls
    assert "*Total:* 12" in text, text
    assert "pick_in_message_domain" in c.last_trace.rules_fired
    assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == [4]


def test_b_case_2_stoick_the_picked_code_answers_stock(session_factory, monkeypatch, stub_access):
    """AC-PK004: "stoick SRTWC286-SH-NEW" after an incoming pick, the parser reading
    inventory and resolving the typed code onto its position (owner ruling 3)."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013524")
    _picked_roster(c)
    text, calls = _say(c, "stoick SRTWC286-SH-NEW", _typed_label("SRTWC286-SH-NEW", 4))
    assert calls == [(STOCK, ["SRTWC286-SH-NEW"])], calls
    assert "*Total:* 12" in text, text
    assert "pick_in_message_domain" in c.last_trace.rules_fired
    assert c.stored_question["kind"] == "product_pick"


def test_b_case_2_the_typed_code_alone_is_a_stock_ask(session_factory, monkeypatch, stub_access):
    """AC-PK004, the entity-only reading: no position, the typed code an entity with a
    domain word of its own. An ordinary stock ask; the roster it is on stays stored."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013525")
    _picked_roster(c)
    text, calls = _say(c, "stoick SRTWC286-SH-NEW", _typed_label("SRTWC286-SH-NEW", None))
    assert calls and {name for name, _ in calls} == {STOCK}, calls
    assert "SRTWC286-SH-NEW" in calls[0][1], calls
    assert "domain_locked_by_pick" not in c.last_trace.rules_fired
    assert c.stored_question["kind"] == "product_pick"


def test_b_a_domain_flag_with_no_domain_named_keeps_the_roster_domain(session_factory, monkeypatch, stub_access):
    """AC-PK006 (J5): the old prompt's production verdict for "check stock"
    (`domain_in_message: true`, `domain_hint: null`, pick [4]) names no domain to judge
    against, so the pick answers as J1 does. The new prompt's consistency rule is what
    stops the parser emitting it."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013526")
    _picked_roster(c)
    reading = verdict(
        message_type="casual",
        domain_in_message=True,
        reference_positions=[4],
        open_question_answer=answer("pick", picked=[4]),
    )
    _text, calls = _say(c, "check stock", reading)
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW"]), calls
    assert "pick_in_roster_domain" in c.last_trace.rules_fired


def test_b_a_pick_naming_the_roster_domain_is_a_plain_pick(session_factory, monkeypatch, stub_access):
    """AC-PK007: "incoming for the 4th" over an incoming roster names the roster's own
    domain, so it answers exactly as the bare pick does (the roster's carried status
    included, AC-1704)."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000013528")
    _say(c, "incoming srtwc286", _incoming_ask())
    reading = verdict(
        domain_hint="incoming",
        intent_hint="check_incoming",
        domain_in_message=True,
        reference_positions=[4],
        open_question_answer=answer("pick", picked=[4]),
    )
    _text, calls = _say(c, "incoming for the 4th", reading)
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW"]), calls
    assert "pick_in_roster_domain" in c.last_trace.rules_fired
    assert "pick_in_message_domain" not in c.last_trace.rules_fired


# =============================================================================== #
# C. Chained questions (AC-PK010, AC-PK011, AC-PK014)
# =============================================================================== #


def test_c_the_stock_quantity_asked_after_a_roster_takes_the_bare_number(
    session_factory, monkeypatch, stub_access
):
    """AC-PK014: the dealer's flow. "check stock" asks "How many units of SRTWC286-SH-NEW?"
    AFTER the roster was asked, so that is the current question: "5" is the quantity, never
    variant 5 off the roster underneath (the scout's T4). The roster stays stored."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60000013527")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
    _say(c, "incoming srtwc286", _incoming_ask())
    _say(c, "4", _bare_pick(4))
    text, calls = _say(c, "check stock", _check_stock())
    assert text == "How many units of SRTWC286-SH-NEW?", text
    assert calls and {name for name, _ in calls} == {STOCK}, calls

    since = len(c.tool_calls)
    text = c.say("5", _bare_pick(5, carried_domain="inventory"))
    calls = c.tool_calls[since:]
    assert [name for name, _ in calls] == [STOCK], calls
    (args,) = [a for _, a in calls]
    assert [c.codes[p] for p in args["product_ids"]] == ["SRTWC286-SH-NEW"], args
    wanted = args.get("requested_quantities")
    wanted = json.loads(wanted) if isinstance(wanted, str) else wanted
    assert wanted == {c.uuid_of["SRTWC286-SH-NEW"]: 5}, args
    assert text.startswith("SRTWC286-SH-NEW x 5:"), text
    assert "bare_number_is_the_quantity" in c.last_trace.rules_fired
    assert c.stored_question and c.stored_question["kind"] == "product_pick"
    assert _answered_positions(c) == [4]


from tests.chatbot import test_outstanding_lane as outstanding  # noqa: E402


def test_c_outstanding_customer_pick_then_the_document_question(session_factory, monkeypatch):
    """AC-PK010: the outstanding chain, customer pick then SO / DO. "1" picks the customer
    (no domain word: the roster's own outstanding ask), the scope question is asked, and
    "delivery order" (a document word, the parser also reading position 2) answers it."""
    outstanding._seed_open_outstanding_customer_pick(session_factory)
    result, captured = outstanding._run_turn(
        session_factory,
        monkeypatch,
        qf=outstanding._parser_output(
            message_type="business_query", intent_hint=None, domain_hint="order", entities=[],
            reference_positions=[1], domain_in_message=False, order_status=None,
        ),
        text_body="1",
        msg_id="ZZT-1352-outstanding-1",
        attributes=["sales_orders.outstanding"],
    )
    reply = (result.reply or {}).get("text") or ""
    assert captured == [] and "Outstanding for which document?" in reply, (captured, reply)
    result, captured = outstanding._run_turn(
        session_factory,
        monkeypatch,
        qf=outstanding._parser_output(
            message_type="business_query", intent_hint="check_order", domain_hint="order",
            entities=[], document=["DO"], reference_positions=[2], domain_in_message=True,
            order_status="outstanding",
        ),
        text_body="delivery order",
        msg_id="ZZT-1352-outstanding-2",
        attributes=["sales_orders.outstanding"],
        mcp_response=outstanding.REPORT_HIT,
    )
    assert captured, "the document answer runs the report"
    name, args = captured[0]
    assert name == "crm_outstanding_report", name
    assert args.get("customer_ids") == [outstanding.HANLIM_UUID_1], args
    assert args.get("scope") == "do", args


from tests.chatbot.test_top_selling_round5 import ASK_METRIC, _answer, _ask, cat, route  # noqa: E402,F401
from tests.chatbot.test_top_selling_round6 import _assert_ranking, console  # noqa: E402,F401


def test_c_top_selling_metric_then_top_x(console):
    """AC-PK011: "top selling item" -> "By quantity or by amount?" -> "amount" ranks by
    amount and asks how many to show -> "20" is the top X, never a pick or a new ask."""
    turn1, captured = console(_ask(), "top selling item", {})
    assert turn1.reply_text == ASK_METRIC and not captured, turn1.reply_text
    turn2, captured = console(_answer(rank_by="amount"), "amount", turn1.session_vars)
    call = _assert_ranking(turn2.reply_text, captured, rank_by="amount", top_n=None)
    assert call.get("n") is None, call
    assert "How many items do you want to see?" in turn2.reply_text, turn2.reply_text
    turn3, captured = console(_answer(top_n=20), "20", turn2.session_vars)
    _assert_ranking(turn3.reply_text, captured, rank_by="amount", top_n=20)


# =============================================================================== #
# D. The prompt (AC-PK020 to AC-PK024)
# =============================================================================== #


def _prompt() -> str:
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


def _open_numbered_block() -> str:
    text = _prompt()
    start = text.index("== AN OPEN NUMBERED QUESTION IS ANSWERED WITH reference_positions ==")
    end = text.index("\n== ", start + 5)
    return text[start:end]


def test_d_a_pick_no_longer_blanks_the_domain_fields():
    """AC-PK020."""
    block = _open_numbered_block()
    assert "NOTHING else" not in block
    assert "domain_hint null, intent_hint null" not in block
    assert "fill every other field as the message itself says" in block
    assert '"4 stock"' in block and '"check stock"' in block


def test_d_domain_in_message_true_names_a_domain():
    """AC-PK021."""
    assert "domain_in_message true means domain_hint is never null" in _prompt()


def test_d_the_sticky_roster_wins_the_contradiction():
    """AC-PK022."""
    text = _prompt()
    assert "that list was closed the moment one product was" not in text
    assert "the roster stays on screen until its own topic changes" in text


def test_d_the_28_sep_production_sentence_is_absent():
    """AC-PK023: superseded by the judgement, never shipped from the repo."""
    assert "never an answer" not in _prompt().lower()


def test_d_a_typed_option_code_is_its_position_and_its_entity():
    """AC-PK024: owner ruling 3, the parser resolves "B" to position 2."""
    block = _open_numbered_block()
    assert "Never emit BOTH an entity and a reference_position" not in block
    assert "A CODE OR NAME FROM THE LIST IS ITS POSITION" in block


# =============================================================================== #
# E. The prompt version migration (AC-PK025)
# =============================================================================== #

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "chatbot_picker_domain_1352.py"


def _migration():
    spec = importlib.util.spec_from_file_location("chatbot_picker_domain_1352", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_e_the_migration_chains_on_the_single_head_with_a_short_id():
    module = _migration()
    assert module.revision == "chatbot_picker_domain_1352"
    assert len(module.revision) <= 32
    assert module.down_revision == "cpc4_cost_packaging_method"


def test_e_publish_adds_the_text_once_and_leaves_the_label(session_factory):
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.ai_prompt_seed import seed_prompt_registry

    module = _migration()
    db = session_factory()
    try:
        seed_prompt_registry(db.get_bind())
        production = (
            db.query(AIPromptLabel)
            .join(AIPromptVersion, AIPromptVersion.id == AIPromptLabel.version_id)
            .filter(AIPromptVersion.name == "chatbot_semantic_parser", AIPromptLabel.label == "production")
            .first()
        )
        before = production.version_id if production is not None else None
        first = module.publish(db)
        again = module.publish(db)
        assert again is None
        row = (
            db.query(AIPromptVersion)
            .filter(AIPromptVersion.name == "chatbot_semantic_parser", AIPromptVersion.template == _prompt())
            .one()
        )
        assert first is None or row.version == first
        if production is not None:
            db.refresh(production)
            assert production.version_id == before
    finally:
        db.close()



# =============================================================================== #
# F. Successive picks keep the carried subject (PR #1353 fix round 1, AC-PK015)
#
# Owner hand test on the copy with parser v48 (29 Sep 00:2x MYT): "promotion for
# srtwc286" -> tier roster -> "1" Office promotions for srtwc286 (correct) -> "2" three
# Cabana office-use promotions, not srtwc286's Dealer ones. The v48 prompt keeps
# `domain_hint` on a pick ("A pick never blanks domain_hint"), and three readers took the
# carried "promotion" for a word of the message: the tier roster recorded no domain of its
# own, `_roster_is_about` never read the code-only tier axis, and the resolver was handed
# the carried domain with no token (the product was already settled), so its gate refused
# "every promotion" and its tier gate re-asked the tier. Each reading below is a shape the
# parser may emit for a bare "2"; every one must answer the SAME roster, in its domain,
# about the SAME subject.
# =============================================================================== #


_SECOND_PICK_READINGS = {
    "bare": {"domain_hint": None, "intent_hint": None},
    # A stale hint from some earlier turn, `domain_in_message` false: still no word of
    # this message.
    "stale_other_domain": {"domain_hint": "master_products", "intent_hint": "check_product"},
    "carried_domain": {"domain_hint": "promotion", "intent_hint": "check_promotion"},
    "carried_domain_flagged": {
        "domain_hint": "promotion", "intent_hint": "check_promotion", "domain_in_message": True,
    },
    # PR #1353 fix round 2, the LIVE v48 reading (chatbot.turns, 29 Sep 2026 10:22 MYT,
    # contact 487555417): with `previous_conversation_state` carrying access_levels
    # ["Sorento Office"] after "1", the parser echoed that level on the bare "2"
    # (parser_raw: domain_hint promotion, domain_in_message false, access_levels
    # ["Sorento Office"]), and the fetch answered Office again. The carried level names
    # none of the picked options, so the picked option decides.
    "carried_office_level": {
        "domain_hint": "promotion", "intent_hint": "check_promotion", "domain_in_message": False,
        "access_levels": ["Sorento Office"],
    },
    # The same echo one turn earlier: a stale Dealer level on the "1" that picked Office.
    "carried_dealer_level": {
        "domain_hint": "promotion", "intent_hint": "check_promotion", "domain_in_message": False,
        "access_levels": ["Sorento Dealer"],
    },
}


def _promotion_double(captured: list[tuple[str, dict[str, Any]]]):
    """The promotion tool through the real presenter: one file, named for the scope it was
    asked for, so the reply itself says which product and which tier were fetched."""
    from app.services.chatbot.lanes.business import fetch as fetch_mod
    from tests.chatbot.test_outstanding_lane import _present_response

    def call(name: str, args: dict[str, Any]) -> str:
        captured.append((name, dict(args)))
        if name != fetch_mod.TIER_PROBE_TOOL:
            return json.dumps({"has_result": False, "items": []})
        scope = "product" if args.get("product_ids") else "UNSCOPED"
        levels = "+".join(args.get("access_levels") or ["ALL"]).replace(" ", "_")
        fname = f"{scope}-{levels}.pdf"
        body = {
            "data": [{
                "id": "p1", "company_name": "Sorento", "start_date": "2026-09-01", "end_date": "2026-12-31",
                "attachments": [{
                    "file_path": f"https://files.test/{fname}", "original_filename": fname,
                    "stored_filename": fname, "mime_type": "application/pdf",
                }],
            }],
            "pagination": {"total": 1, "page": 1, "limit": 50},
        }
        return _present_response()(name, json.dumps(body))

    return call


@pytest.mark.parametrize("shape", list(_SECOND_PICK_READINGS))
def test_f_two_successive_tier_picks_keep_the_product(session_factory, monkeypatch, shape):
    """AC-PK015, tier_pick: "promotion for <code>" -> "1" Office -> "2" Dealer, both for
    the product the roster was asked about, the roster still stored after both."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business import fetch as fetch_mod
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._pg_fixture import unique_code
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_engine_company_scope import _seed_product
    from tests.chatbot.test_outstanding_lane import _session_of
    from tests.chatbot.test_rearch_r5_production_decides import _seed_contact_and_get
    from tests.chatbot.test_rearch_r6_review_round import (
        _mark_workspace_default,
        _run_turn_engine,
        _seed_three_tiers_two_entitled,
    )

    traces: list[Any] = []
    real_apply = engine_mod.turn_apply

    def traced_apply(*args: Any, **kwargs: Any):
        state_out, plan = real_apply(*args, **kwargs)
        traces.append(plan.trace)
        return state_out, plan

    monkeypatch.setattr(engine_mod, "turn_apply", traced_apply)
    _seed_contact_and_get(session_factory)
    _mark_workspace_default(session_factory)
    _seed_three_tiers_two_entitled(session_factory)
    code = unique_code("ZZT1353TIER")
    product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

    def turn(text: str, qf: dict[str, Any]) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        captured: list[tuple[str, dict[str, Any]]] = []
        result = _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=text, msg_id=f"zzt-1353-{shape}-{text}",
            mcp_call=_promotion_double(captured), real_entitlement=True,
        )
        return (result.reply or {}).get("text") or "", [
            a for n, a in captured if n == fetch_mod.TIER_PROBE_TOOL
        ]

    ask = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
    )
    text, _ = turn(f"promotion for {code}", ask)
    assert "1. Office - has promotion" in text and "2. Dealer - has promotion" in text, text

    for position, level in ((1, "Sorento Office"), (2, "Sorento Dealer")):
        reading = _parser_output(
            **{"entities": [], "access_levels": [], "reference_positions": [position], **_SECOND_PICK_READINGS[shape]}
        )
        text, calls = turn(str(position), reading)
        assert calls, f"'{position}' must fetch promotions: {text!r}"
        assert calls[-1].get("product_ids") == [product_id], calls
        assert calls[-1].get("access_levels") == [level], calls
        assert f"product-{level.replace(' ', '_')}.pdf" in text, text
        rules = traces[-1].rules_fired
        assert "pick_in_roster_domain" in rules and "new_ask_closes_stale_roster" not in rules, rules
        question = _session_of(session_factory).get("open_question") or {}
        assert question.get("kind") == "tier_pick", question
        assert (question.get("payload") or {}).get("answered_positions") == list(range(1, position + 1)), question


_PRODUCT_SECOND_PICKS = {
    "bare": lambda n: _bare_pick(n, carried_domain=None),
    "carried_domain": lambda n: _bare_pick(n),
    "carried_domain_flagged": lambda n: verdict(
        message_type="business_query", domain_hint="incoming", domain_in_message=True,
        reference_positions=[n], open_question_answer=answer("pick", picked=[n]),
    ),
}


@pytest.mark.parametrize("shape", list(_PRODUCT_SECOND_PICKS))
def test_f_two_successive_product_picks_keep_the_roster_domain(session_factory, monkeypatch, stub_access, shape):
    """AC-PK015, product_pick: "incoming srtwc286" -> "4" -> "7", each answered as incoming
    for its own option, the roster stored with both positions."""
    c = _console(session_factory, monkeypatch, stub_access, f"+6000001353{len(shape)}")
    text, _ = _say(c, "incoming srtwc286", _incoming_ask())
    assert text.startswith(FAMILY_HEAD), text
    for position, code, answered in ((4, "SRTWC286-SH-NEW", [4]), (7, "SRTWC286-SH-NEW-P", [4, 7])):
        _text, calls = _say(c, str(position), _PRODUCT_SECOND_PICKS[shape](position))
        assert calls and calls[0] == (INCOMING, [code]), calls
        rules = c.last_trace.rules_fired
        assert "pick_in_roster_domain" in rules and "new_ask_closes_stale_roster" not in rules, rules
        assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == answered


_CUSTOMER_OPTIONS = [
    {"position": 1, "label": "HANLIM TRADING SDN BHD", "code": "C-HAN-1", "uuid": "c-1", "uuids": ["c-1"], "payload": {}, "entity_type": "customer"},
    {"position": 2, "label": "HANLIM TRADING (JB) SDN BHD", "code": "C-HAN-2", "uuid": "c-2", "uuids": ["c-2"], "payload": {}, "entity_type": "customer"},
]
_CARRIED_PRODUCT = {"raw": "SRTWC286", "hint": "product", "canonical_code": "SRTWC286", "uuid": "p-286", "current_message": False}


@pytest.mark.parametrize(
    "extra",
    [{}, {"domain_hint": "order"}, {"domain_hint": "order", "domain_in_message": True}],
    ids=["bare", "carried_domain", "carried_domain_flagged"],
)
def test_f_two_successive_customer_picks_keep_the_product(extra):
    """AC-PK015, customer_pick: "orders for hanlim srtwc286" -> customer roster -> "1" ->
    "2". Each pick settles the customer and keeps the carried product and the order domain;
    the roster stays stored with both positions answered."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Profile, State
    from tests.chatbot._turn_helpers import build_policy

    state = State(
        focus=Focus(products=[dict(_CARRIED_PRODUCT)], domains=["order"]),
        pending=pending_ask("customer_pick", [dict(o) for o in _CUSTOMER_OPTIONS], asked_at_turn=1, payload={"domain": "order"}),
        profile=Profile(),
    )
    for position, answered in ((1, [1]), (2, [1, 2])):
        state, plan = apply(state, verdict(reference_positions=[position], **extra), build_policy())
        rules = plan.trace.rules_fired
        assert "pick_in_roster_domain" in rules and "new_ask_closes_stale_roster" not in rules, rules
        assert state.focus.domains == ["order"], state.focus.domains
        assert [c.get("uuid") for c in state.focus.customers] == [f"c-{position}"], state.focus.customers
        assert [p.get("uuid") for p in state.focus.products] == ["p-286"], state.focus.products
        assert state.pending is not None and state.pending.kind == "customer_pick"
        assert state.pending.payload.get("answered_positions") == answered, state.pending.payload


def test_f_a_tier_roster_records_the_promotion_domain():
    """AC-PK015: the tier roster says which domain it was asked FOR, as every roster does
    (contract 121), so a pick that repeats "promotion" is judged a plain pick (J1b)."""
    from app.services.chatbot.answer_bridge import _tier_options

    question = _tier_options(
        [{"tier": "office", "label": "Office"}, {"tier": "dealer", "label": "Dealer"}], asked_at_turn=1
    )
    assert question is not None and question.payload.get("domain") == "promotion", question


# =============================================================================== #
# G. The picked option decides the roster's own axis (PR #1353 fix round 2)
#
# Owner retest of round 1 on the :3105 copy, parser v48 (chatbot.turns, 29 Sep 2026 10:22
# MYT, contact 487555417): "promo srtwc286" -> roster (1 Office, 2 Dealer, 3 End user) ->
# "1" Office files (correct) -> "2" the SAME Office files. Turn "2"'s parser_raw:
# domain_hint promotion, domain_in_message false, access_levels ["Sorento Office"] - the
# level `previous_conversation_state` carried after "1", echoed on a bare position. The
# fetch trace read access_levels ["Sorento Office", "Mocha Office", "Cabana Office"]: the
# resolver's own tier gate (`tier_gate.tier_gate`) states the tier off the parser's
# `access_levels`, so it answered Office over the Dealer the customer had just picked.
# Owner ruling: the parser reports, the engine judges. On a bare pick answered in the
# roster's domain the picked option(s) are the axis, and a carried value of that axis
# naming none of them is ignored (tier, product, customer alike).
# =============================================================================== #


def _seed_live_entitlement(session_factory) -> None:
    """The live contact's entitlement: Office and Dealer in all three brands, plus End
    User, so a tier recomposes to three compound levels exactly as the live fetch did."""
    from tests.chatbot.test_rearch_r6_review_round import _grant_access_type, _seed_access_type

    for brand in ("Sorento", "Mocha", "Cabana"):
        for tier in ("Office", "Dealer"):
            code = f"{brand.lower()}_{tier.lower()}"
            _seed_access_type(session_factory, code=code, name=f"{brand} {tier}")
            _grant_access_type(session_factory, code=code)
    _seed_access_type(session_factory, code="end_user", name="End User")
    _grant_access_type(session_factory, code="end_user")


_OFFICE_ALL = ["Cabana Office", "Mocha Office", "Sorento Office"]
_DEALER_ALL = ["Cabana Dealer", "Mocha Dealer", "Sorento Dealer"]


@pytest.mark.parametrize(
    "carried",
    [["Sorento Office"], ["Sorento Office", "Mocha Office", "Cabana Office"]],
    ids=["live_parser_raw", "all_office_levels"],
)
def test_g_a_carried_office_level_on_a_bare_2_answers_dealer(session_factory, monkeypatch, carried):
    """The live v48 reading, replayed: "promo <family>" -> "1" (parser access_levels
    ["Sorento Office"]) -> "2" (the SAME ["Sorento Office"], carried). Turn "2" fetches
    Dealer, and the resolver's own tier gate states Dealer too, never the carried Office."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business import fetch as fetch_mod
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._pg_fixture import unique_code
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_engine_company_scope import _seed_product
    from tests.chatbot.test_rearch_r5_production_decides import _seed_contact_and_get
    from tests.chatbot.test_rearch_r6_review_round import _mark_workspace_default, _run_turn_engine

    gates: list[dict[str, Any]] = []
    real_resolve = engine_mod.turn_runtime.resolve_kinds

    def traced_resolve(*args: Any, **kwargs: Any):
        outcome = real_resolve(*args, **kwargs)
        gates.append(dict(((outcome.payload or {}).get("tier_gate")) or {}))
        return outcome

    monkeypatch.setattr(engine_mod.turn_runtime, "resolve_kinds", traced_resolve)
    _seed_contact_and_get(session_factory)
    _mark_workspace_default(session_factory)
    _seed_live_entitlement(session_factory)
    code = unique_code("ZZT1353LIVE")
    ids = {
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{code}-SH"),
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{code}-SH-NEW"),
    }

    def turn(text: str, qf: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        captured: list[tuple[str, dict[str, Any]]] = []
        result = _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=text, msg_id=f"zzt-1353-g-{len(carried)}-{text}",
            mcp_call=_promotion_double(captured), real_entitlement=True,
        )
        return (result.reply or {}).get("text") or "", [a for n, a in captured if n == fetch_mod.TIER_PROBE_TOOL]

    text, _ = turn(f"promo {code}", _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", domain_in_message=True,
        entities=[{"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
    ))
    assert "1. Office" in text and "2. Dealer" in text and "3. End user" in text, text

    for position, expected in ((1, _OFFICE_ALL), (2, _DEALER_ALL)):
        gates.clear()
        text, calls = turn(str(position), _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", domain_in_message=False,
            entities=[], access_levels=list(carried), reference_positions=[position],
        ))
        assert calls, f"'{position}' must fetch promotions: {text!r}"
        assert set(calls[-1].get("product_ids") or []) == ids, calls
        assert sorted(calls[-1].get("access_levels") or []) == expected, calls[-1].get("access_levels")
        assert gates and sorted(gates[-1].get("access_levels_recomposed") or []) == expected, gates
        tier = expected[0].split()[-1].lower()
        assert gates[-1].get("tier_stated") == [tier], gates[-1]


@pytest.mark.parametrize("carried_current", [False, True], ids=["carried", "echoed_as_typed"])
def test_g_a_carried_product_on_a_bare_7_answers_the_7th(session_factory, monkeypatch, stub_access, carried_current):
    """product_pick: "incoming srtwc286" -> "4" -> "7", where the parser echoes the 4th's
    code (the carried subject) on the bare "7". The 7th option is what "7" answers."""
    from tests.chatbot._turn_helpers import entity

    c = _console(session_factory, monkeypatch, stub_access, f"+6000013532{int(carried_current)}")
    _picked_roster(c)
    reading = _bare_pick(7)
    reading["entities"] = [entity("SRTWC286-SH-NEW", hint="product", canonical_code="SRTWC286-SH-NEW", current_message=carried_current)]
    _text, calls = _say(c, "7", reading)
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW-P"]), calls
    assert c.stored_question["kind"] == "product_pick" and _answered_positions(c) == [4, 7]


def test_g_the_picked_axis_replaces_a_carried_value_that_names_no_picked_option():
    """The engine's rule, pure, for each roster axis the parser may carry: tier
    (`access_levels`), product and customer (`entities` of the roster's kind)."""
    from app.services.chatbot.engine import _with_the_picked_axis

    tiers = pending_ask(
        "tier_pick",
        [
            {"position": 1, "label": "Office", "entity_type": "tier", "payload": {"value": "office"}},
            {"position": 2, "label": "Dealer", "entity_type": "tier", "payload": {"value": "dealer"}},
            {"position": 3, "label": "End user", "entity_type": "tier", "payload": {"value": "end_user"}},
        ],
        asked_at_turn=1,
        payload={"domain": "promotion"},
    )
    out = _with_the_picked_axis(verdict(access_levels=["Sorento Office"], reference_positions=[2]), tiers, [2])
    assert out["access_levels"] == ["dealer"], out["access_levels"]
    out = _with_the_picked_axis(verdict(access_levels=["Sorento Dealer"], reference_positions=[2]), tiers, [2])
    assert out["access_levels"] == ["Sorento Dealer"], "a carried level naming the pick stays"
    out = _with_the_picked_axis(verdict(access_levels=["Sorento Office"], reference_positions=[1, 2]), tiers, [1, 2])
    assert out["access_levels"] == ["Sorento Office", "dealer"], out["access_levels"]

    customers = pending_ask("customer_pick", [dict(o) for o in _CUSTOMER_OPTIONS], asked_at_turn=1, payload={"domain": "order"})
    carried = {"raw": "HANLIM TRADING SDN BHD", "hint": "customer", "canonical_code": "C-HAN-1", "uuid": "c-1", "current_message": True}
    out = _with_the_picked_axis(verdict(entities=[carried, dict(_CARRIED_PRODUCT)], reference_positions=[2]), customers, [2])
    assert [e.get("hint") for e in out["entities"]] == ["product"], out["entities"]
    picked = {"raw": "HANLIM TRADING (JB) SDN BHD", "hint": "customer", "current_message": True}
    out = _with_the_picked_axis(verdict(entities=[picked], reference_positions=[2]), customers, [2])
    assert out["entities"] == [picked], "an entity naming the picked option stays"


# =============================================================================== #
# H. The engine reads a bare position itself (PR #1353 fix round 3)
#
# Owner retest of round 2 on the :3105 copy, parser v48 (chatbot.turns, 29 Sep 2026 11:44
# MYT, contact 487555417): "promo srtwc286" -> roster (1 Office, 2 Dealer, 3 End user) ->
# "1" Office files (correct) -> "2" the SAME Office files. Turn "2" (id 3f56a40d): the
# parser read the bare "2" as reference_positions [1], open_question_answer {pick, [1]},
# access_levels ["Sorento Office"]. Round 2's picked-axis rule had nothing to correct: the
# position it was handed named the carried Office level. Owner ruling: the parser
# reports, the engine judges. While a numbered roster is open, a bare "2", "1 and 2",
# "1, 2" or "all" (the forms the roster's own reply line offers) is read by the engine and
# its positions win over the parser's; the parser stays authoritative for a typed label
# or code and for any message that is not a bare pick.
#
# The user block the parser was shown on that turn is replayed too: its "Recent
# exchanges, oldest first" opened with "User: 2 / Assistant: I have attached the file(s)
# below." because the 11:32 run of the same conversation had ended with that "2" - the
# block reads the contact's last three COMPLETED turns, oldest first, whatever run they
# came from, and the newest pair's reply is the Previous response line.
# =============================================================================== #


def _live_pick_reading(position_the_parser_read: int, carried: list[str]) -> dict[str, Any]:
    """The v48 reading of a bare number, as chatbot.turns recorded it."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", domain_in_message=False,
        entities=[], access_levels=list(carried), reference_positions=[position_the_parser_read],
        open_question_answer={"mode": "pick", "picked": [position_the_parser_read]},
    )


def _exchanges(block: str) -> list[str]:
    lines = block.splitlines()
    return lines[lines.index("Recent exchanges, oldest first:") + 1:]


def test_h_the_live_2_read_as_1_answers_dealer_with_the_live_user_block(session_factory, monkeypatch):
    """The reproduction, turn for turn: two runs of "promo <family>" -> "1" -> "2" on one
    contact, the parser reading every bare number as position 1 with the Office level
    carried after the first pick. The second run's "2" is shown exactly the block the
    owner quoted, and answers Dealer."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business import fetch as fetch_mod
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._pg_fixture import unique_code
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_engine_company_scope import _seed_product
    from tests.chatbot.test_rearch_r5_production_decides import _seed_contact_and_get
    from tests.chatbot.test_rearch_r6_review_round import _mark_workspace_default, _run_turn_engine

    from app.services.chatbot.turn import context as context_mod

    # Chatbot memory lane A (S3): the engine builds the parser's user block with
    # `turn/context.assemble`, not `parser.build_user_block`, so the block is traced there.
    blocks: list[str] = []
    real_assemble = context_mod.assemble

    def traced_assemble(*args: Any, **kwargs: Any) -> Any:
        block, report = real_assemble(*args, **kwargs)
        blocks.append(block)
        return block, report

    monkeypatch.setattr(context_mod, "assemble", traced_assemble)
    _seed_contact_and_get(session_factory)
    _mark_workspace_default(session_factory)
    _seed_live_entitlement(session_factory)
    code = unique_code("ZZT1353R3")
    ids = {
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{code}-SH"),
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{code}-SH-NEW"),
    }

    def turn(run: str, text: str, qf: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        captured: list[tuple[str, dict[str, Any]]] = []
        result = _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=text, msg_id=f"zzt-1353-h-{run}-{text}",
            mcp_call=_promotion_double(captured), real_entitlement=True,
        )
        return (result.reply or {}).get("text") or "", [a for n, a in captured if n == fetch_mod.TIER_PROBE_TOOL]

    ask = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", domain_in_message=True,
        entities=[{"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
    )
    replies: dict[str, str] = {}
    for run in ("11:32", "11:44"):
        roster, _ = turn(run, f"promo {code}", ask)
        assert "1. Office" in roster and "2. Dealer" in roster and "3. End user" in roster, roster
        office, calls = turn(run, "1", _live_pick_reading(1, []))
        assert sorted(calls[-1].get("access_levels") or []) == _OFFICE_ALL, calls
        dealer, calls = turn(run, "2", _live_pick_reading(1, ["Sorento Office"]))
        assert calls, f"'2' must fetch promotions: {dealer!r}"
        assert set(calls[-1].get("product_ids") or []) == ids, calls
        assert sorted(calls[-1].get("access_levels") or []) == _DEALER_ALL, (run, calls[-1].get("access_levels"))
        replies[run] = dealer

    # The block the second "2" was parsed from, as the owner quoted it.
    block = blocks[-1]
    assert "Current user message: 2" in block, block
    assert '"kind":"pick_one"' in block and '"position":2,"code":"dealer","label":"Dealer"' in block, block
    assert "Pending: the assistant is waiting for a tier_pick reply." in block, block
    assert "Open question options: Office; Dealer; End user" in block, block
    from app.services.chatbot.head.parser import _exchange_text

    assert _exchanges(block) == [
        "User: 2",
        f"Assistant: {_exchange_text(replies['11:32'])}",
        f"User: promo {code}",
        f"Assistant: {_exchange_text(roster)}",
        "User: 1",
        "Assistant: (the Previous response)",
    ], block


@pytest.mark.parametrize(
    ("message", "positions"),
    [
        ("2", [2]), (" 2. ", [2]), ("#3", [3]), ("1 and 2", [1, 2]), ("1, 2", [1, 2]),
        ("1,3", [1, 3]), ("1 & 3", [1, 3]), ("2 1", [1, 2]), ("all", [1, 2, 3]), ("ALL", [1, 2, 3]),
        ("4", None), ("0", None), ("1 and 4", None), ("dealer", None), ("2 dealer", None),
        ("promo 2", None), ("stock for 2", None), ("", None), ("2nd", None), ("1 or 2", None),
    ],
)
def test_h_the_engine_reads_only_a_bare_position_over_the_open_roster(message, positions):
    """What counts as a bare pick: a whole number from 1 to the option count, the
    "1 and 2" / "1, 2" lists and "all". A word beside the number, an ordinal, a typed
    label or a number past the roster is the parser's to read."""
    from app.services.chatbot.engine import _bare_roster_positions

    tiers = pending_ask(
        "tier_pick",
        [{"position": i, "label": t, "entity_type": "tier", "payload": {"value": t.lower()}} for i, t in enumerate(["Office", "Dealer", "End user"], 1)],
        asked_at_turn=1,
        payload={"domain": "promotion"},
    )
    assert _bare_roster_positions(tiers, message) == positions


def test_h_the_engine_positions_win_over_the_parsers_on_every_roster_kind():
    """tier_pick, product_pick and customer_pick alike: the engine's positions replace the
    parser's `reference_positions` and `open_question_answer`, and a message that is not a
    bare pick keeps the parser's reading untouched."""
    from app.services.chatbot.engine import _with_the_engine_pick

    rosters = [
        _roster(),
        pending_ask("customer_pick", [dict(o) for o in _CUSTOMER_OPTIONS], asked_at_turn=1, payload={"domain": "order"}),
    ]
    for roster in rosters:
        reading = _live_pick_reading(1, [])
        out = _with_the_engine_pick(reading, roster, "2")
        assert out["reference_positions"] == [2], out
        assert out["open_question_answer"] == {"mode": "pick", "picked": [2]}, out
        assert out["domain_in_message"] is False, out
        assert _with_the_engine_pick(reading, roster, "check stock") is reading
        assert _with_the_engine_pick(reading, roster, "2 stock") is reading
    # The parser read the bare "2" as a pick of 2 already: nothing to correct.
    same = _live_pick_reading(2, [])
    assert _with_the_engine_pick(same, rosters[0], "2") is same
    # No roster open: the parser's reading stands.
    assert _with_the_engine_pick(_live_pick_reading(1, []), None, "2")["reference_positions"] == [1]


def test_h_a_three_turn_console_conversation_reads_oldest_first_on_a_created_at_tie(session_factory):
    """The "Recent exchanges, oldest first" order, pinned for a three-turn console
    conversation: "promo srtwc286" -> "1" -> "2". `created_at` is Postgres `now()`, the
    transaction's start, so rows written in one transaction tie on it (every test that
    drives `engine.run_turn` over this fixture does); `started_at`, the head's own clock,
    is what orders them then. Rows inserted out of order so a tie cannot pass by luck.
    The newest pair's answer is also the Previous response line."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.head.parser import build_user_block

    contact = "zzt-1353-r3-order"
    tie = datetime(2026, 9, 29, 3, 44, tzinfo=timezone.utc)
    turns = [("promo srtwc286", "Which access level do you need for srtwc286?"), ("1", "Office files"), ("2", "Dealer files")]
    db = session_factory()
    for index in (2, 0, 1):
        said, answered = turns[index]
        db.add(ChatbotTurn(
            id=str(uuid.uuid4()), contact_respond_id=contact, message_id=f"zzt-1353-r3-{index}",
            ingress=turn_runtime._CONSOLE_INGRESS, is_test=True, status="done",
            envelope={"message": {"message": {"message": {"text": said}}}},
            response={"reply": {"text": answered}}, created_at=tie, started_at=tie + timedelta(seconds=index),
        ))
    db.commit()

    scope = {"contact_respond_id": contact, "ingress": turn_runtime._CONSOLE_INGRESS, "is_test": True}
    recent = turn_runtime.recent_exchanges(db, **scope)
    assert recent == turns, recent
    previous = turn_runtime.previous_reply_text(db, **scope)
    assert previous == "Dealer files", previous
    block = build_user_block(previous_response=previous, latest_user_message="3", pending_kind=None, recent_exchanges=recent)
    assert _exchanges(block) == [
        "User: promo srtwc286", "Assistant: Which access level do you need for srtwc286?",
        "User: 1", "Assistant: Office files",
        "User: 2", "Assistant: (the Previous response)",
    ], block
    assert "Current user message: 3" in block and "User: 3" not in block, block
