"""PR #1247 (chatbot stock ask v2 S3), round 8: the owner's round 7 session (26 Sep
2026 10:11Z to 10:14Z, :3087 console, head d11119af) replayed. Tester-first RED tests,
written from the UAC + Phase 1 contract doc + the captain's brief, before the coder's
Phase 2 pass. No implementation exists yet for any of A through H below.

Sections A to H below map straight onto the captain's own lettering; the file closes
with the full owner replay (item 5) plus the two standalone "row 5" / "row 6" tests the
brief calls out separately, and two engine-level tests over `engine.run_turn`.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus, focus_to_wire

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access, stub_parser
from tests.chatbot.test_stock_ask_ht26_r4_owner_replay import OWNER_FAMILY, TOO_BIG, Console
from tests.chatbot.test_stock_ask_ht26_r6_all_point_form import ALL_READINGS, LIST, POINT_FORM

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
GREETING = "Hi! How can I help you today?"


def _task(values: list[Any], *, status: str = task_mod.OPEN):
    return ht.stock_task(list(zip(OWNER_FAMILY, values)), status=status)


def _state_with_task(task) -> Any:
    focus = Focus(products=ht.product_rows(*OWNER_FAMILY), domains=["inventory"], tasks=(task,))
    return ht.state(focus, turn_no=6)


# =============================================================================== #
# A. Parser schema: `open_question_answer`
# =============================================================================== #


def test_open_question_answer_declared_strict_safe_and_tolerated_absent():
    schema = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
    assert "open_question_answer" in schema["required"]
    prop = schema["properties"]["open_question_answer"]
    assert prop["type"] == "object"
    assert prop["additionalProperties"] is False
    # Round 9 (issue #1293) widened the same object to every question kind: "pick",
    # "yes", "no" and `picked` joined it (`test_stock_ask_ht26_r9_open_question_kinds`).
    assert set(prop["required"]) == {"mode", "picked", "items", "qty_for_all"}
    assert prop["properties"]["mode"]["enum"] == [
        "pick", "yes", "no", "fill", "all", "done", "cancel", None
    ]
    items_schema = prop["properties"]["items"]["items"]
    assert items_schema["additionalProperties"] is False
    assert set(items_schema["required"]) == {"position", "code", "qty"}
    assert items_schema["properties"]["position"]["type"] == ["integer", "null"]
    assert items_schema["properties"]["code"]["type"] == ["string", "null"]
    assert items_schema["properties"]["qty"]["type"] == ["number", "null"]
    assert prop["properties"]["qty_for_all"]["type"] == ["number", "null"]

    # A recorded emission that predates this key (a full emission without it) is
    # still a valid emission.
    assert "open_question_answer" in parser_mod.TOLERATED_ABSENT
    recorded = {key: None for key in parser_mod.DECLARED_KEYS if key != "open_question_answer"}
    parser_mod.assert_emission(recorded)


# =============================================================================== #
# B. `task_mod.open_question`
# =============================================================================== #


def test_open_question_is_none_with_no_stock_task():
    assert task_mod.open_question(()) is None
    other = task_mod.Task(kind="something_else", domain="x")
    assert task_mod.open_question((other,)) is None


def test_open_question_for_an_open_task_lists_owed_positions():
    task = ht.stock_task(
        [("SRTWC286-SH", 10), ("SRTWC286-SH-150", None), ("SRTWC286-SH-200", None)],
        status=task_mod.OPEN,
    )
    result = task_mod.open_question((task,))
    assert result == {
        "kind": "quantities",
        "items": [
            {"position": 1, "code": "SRTWC286-SH", "qty": 10},
            {"position": 2, "code": "SRTWC286-SH-150", "qty": None},
            {"position": 3, "code": "SRTWC286-SH-200", "qty": None},
        ],
        "owed": [2, 3],
    }


def test_open_question_for_an_answered_task_has_no_owed():
    task = ht.stock_task(
        [("SRTWC286-SH", 10), ("SRTWC286-SH-150", 5)], status=task_mod.ANSWERED
    )
    result = task_mod.open_question((task,))
    assert result["kind"] == "last_answer"
    assert result["owed"] == []
    assert result["items"] == [
        {"position": 1, "code": "SRTWC286-SH", "qty": 10},
        {"position": 2, "code": "SRTWC286-SH-150", "qty": 5},
    ]


def test_open_question_accepts_a_wire_dict_the_same_as_a_task():
    task = ht.stock_task([("SRTWC286-SH", 10)], status=task_mod.OPEN)
    wire = task_mod.task_to_wire(task)
    assert task_mod.open_question((wire,)) == task_mod.open_question((task,))


# =============================================================================== #
# C. `parser.build_user_block`: `open_question=`, `recent_exchanges=`
# =============================================================================== #


def test_build_user_block_open_question_and_recent_exchanges_default_to_unchanged():
    baseline = parser_mod.build_user_block(
        previous_response="(none)", latest_user_message="hello", pending_kind=None
    )
    same = parser_mod.build_user_block(
        previous_response="(none)",
        latest_user_message="hello",
        pending_kind=None,
        open_question=None,
        recent_exchanges=None,
    )
    assert same == baseline


def test_build_user_block_states_the_open_question_as_one_json_line():
    obj = {
        "kind": "quantities",
        "items": [{"position": 1, "code": "SRTWC286-SH", "qty": None}],
        "owed": [1],
    }
    block = parser_mod.build_user_block(
        previous_response="(none)", latest_user_message="10", pending_kind=None, open_question=obj
    )
    expected = "Open question: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    assert expected in block.splitlines()


def test_build_user_block_recent_exchanges_oldest_first_last_assistant_not_repeated():
    pairs = [("first msg", "first reply"), ("second msg", "second reply")]
    block = parser_mod.build_user_block(
        previous_response="second reply",
        latest_user_message="third msg",
        pending_kind=None,
        recent_exchanges=pairs,
    )
    lines = block.splitlines()
    assert "Recent exchanges, oldest first:" in lines
    idx = lines.index("Recent exchanges, oldest first:")
    assert lines[idx + 1] == "User: first msg"
    assert lines[idx + 2] == "Assistant: first reply"
    assert lines[idx + 3] == "User: second msg"
    # The LAST pair's assistant text is not repeated - the Previous response line
    # already carries it.
    assert lines[idx + 4] == "Assistant: (the Previous response)"


def test_build_user_block_recent_exchanges_caps_length_and_flattens_newlines():
    long_text = "line one\nline two\n" + ("x" * 600)
    block = parser_mod.build_user_block(
        previous_response="(none)",
        latest_user_message="hi",
        pending_kind=None,
        recent_exchanges=[(long_text, "ok")],
    )
    lines = block.splitlines()
    user_line = next(line for line in lines if line.startswith("User: "))
    text = user_line[len("User: ") :]
    assert "\n" not in text
    assert " / " in text
    assert text.endswith("...")
    assert len(text) == 503


def test_the_open_question_hint_prints_its_lines_for_the_parser_updated_in_r7():
    """Companion of the r7 update: `StockQtyTask.hint` for a multi-slot OPEN task no
    longer prints "1. SRTWC286-SH" style lines at all - they ride on the parser's own
    `Open question:` object exclusively."""
    task = _task([None] * 10)
    hint = task_mod.StockQtyTask().hint(task)
    assert "1. SRTWC286-SH" not in hint


# =============================================================================== #
# D. `turn_runtime.recent_exchanges`
# =============================================================================== #


class TestRecentExchanges:
    def _row(
        self,
        i: int,
        *,
        contact: str,
        status: str = "done",
        ingress: str = "webhook",
        reply_text: str | None = None,
        created_at: datetime,
    ) -> ChatbotTurn:
        return ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=contact,
            message_id=f"ZZT-r8-recent-{i}",
            ingress=ingress,
            is_test=False,
            envelope={
                "message": {"message": {"message": {"text": f"msg-{i}"}}},
                "contact": {"id": contact},
            },
            status=status,
            response={"reply": {"text": reply_text}} if reply_text is not None else None,
            created_at=created_at,
        )

    def test_last_three_done_rows_oldest_first_skips_failed_and_other_contact(
        self, session_factory
    ):
        from app.services.chatbot import turn_runtime

        db = session_factory()
        contact = str(CONTACT_ID)
        base = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)
        rows = [
            self._row(i, contact=contact, reply_text=f"reply-{i}", created_at=base + timedelta(minutes=i))
            for i in range(1, 6)
        ]
        rows.append(
            self._row(
                6, contact=contact, status="failed", reply_text="reply-6",
                created_at=base + timedelta(minutes=6),
            )
        )
        rows.append(
            self._row(
                7, contact="other-contact-9999", reply_text="reply-7",
                created_at=base + timedelta(minutes=7),
            )
        )
        for row in rows:
            db.add(row)
        db.commit()

        result = turn_runtime.recent_exchanges(
            db, contact_respond_id=contact, ingress="webhook", is_test=False, limit=3
        )
        assert result == [
            ("msg-3", "reply-3"),
            ("msg-4", "reply-4"),
            ("msg-5", "reply-5"),
        ]

    def test_a_done_row_with_no_reply_text_is_skipped(self, session_factory):
        from app.services.chatbot import turn_runtime

        db = session_factory()
        contact = str(CONTACT_ID) + "-noreply"
        base = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)
        db.add(self._row(1, contact=contact, reply_text=None, created_at=base))
        db.add(self._row(2, contact=contact, reply_text="reply-2", created_at=base + timedelta(minutes=1)))
        db.commit()

        result = turn_runtime.recent_exchanges(
            db, contact_respond_id=contact, ingress="webhook", is_test=False, limit=3
        )
        assert result == [("msg-2", "reply-2")]


# =============================================================================== #
# E. `turn/apply.py`: the `open_question_answer` object drives the task
# =============================================================================== #


def test_open_question_answer_fill_fills_lines_and_answers_them_keeping_the_rest_open():
    """Row 3: "10 / 20 / 30 / 40 / 5" fills lines 1 to 5 - even though the verdict also
    carries demand_qty 10 and message_type "casual" (the object outranks every other
    field). STOCK-QTY-PARTIAL-FILL (owner ruling R1, 6 Oct 2026): the five filled lines
    are fetched now and lines 6 to 10 stay open on the task; the point-form question is
    not asked again (before this ruling it was re-asked with 6 to 10 blank)."""
    task = _task([None] * 10)
    state = _state_with_task(task)
    v = verdict(
        message_type="casual",
        demand_qty=10,
        entities=[],
        open_question_answer={
            "mode": "fill",
            "items": [
                {"position": i, "code": None, "qty": qty}
                for i, qty in zip(range(1, 6), [10, 20, 30, 40, 5])
            ],
            "qty_for_all": None,
        },
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_fill" in plan.trace.rules_fired
    assert "task_partial_answered_stock_qty" in plan.trace.rules_fired
    assert plan.trace.task_question is None
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == OWNER_FAMILY[:5]
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(code): qty for code, qty in zip(OWNER_FAMILY[:5], [10, 20, 30, 40, 5])
    }
    assert not spec.filters.get("not_checked")
    (stock_task,) = [t for t in new_state.focus.tasks if t.kind == "stock_qty"]
    assert stock_task.status == task_mod.OPEN
    assert [slot.value for slot in stock_task.slots] == [10, 20, 30, 40, 5, None, None, None, None, None]


def test_open_question_answer_done_over_a_fresh_open_task_fetches_filled_and_marks_owed():
    """Rows 4 and 5: "done" fetches immediately with what is given even though the
    verdict's own entities carry all ten codes (quantities 10, 5, then null) - the
    owed lines are skipped, riding only on the fetch spec's `not_checked` filter."""
    task = _task([None] * 10)
    state = _state_with_task(task)
    v = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=(
            [ht.asked(OWNER_FAMILY[0], 10), ht.asked(OWNER_FAMILY[1], 5)]
            + [ht.asked(code, None) for code in OWNER_FAMILY[2:]]
        ),
        open_question_answer={
            "mode": "done",
            "items": [
                {"position": 1, "code": None, "qty": 10},
                {"position": 2, "code": None, "qty": 5},
            ],
            "qty_for_all": None,
        },
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_done" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert {row.get("canonical_code") for row in spec.entities} == {OWNER_FAMILY[0], OWNER_FAMILY[1]}
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(OWNER_FAMILY[0]): 10,
        ht.uuid_of(OWNER_FAMILY[1]): 5,
    }
    assert set(spec.filters.get("not_checked") or []) == set(OWNER_FAMILY[2:])
    assert plan.trace.task_question is None


def test_row5_pasted_list_under_the_open_list_skips_blanks():
    """The owner's row 4 state (an open 10-line task, lines 1 and 2 already filled):
    the whole list pasted back a second time, still only two lines filled - the same
    two answer again, the rest stay owed and unasked."""
    task = _task([10, 5] + [None] * 8)
    state = _state_with_task(task)
    v = verdict(
        entities=[],
        open_question_answer={
            "mode": "done",
            "items": [
                {"position": 1, "code": None, "qty": 10},
                {"position": 2, "code": None, "qty": 5},
            ],
            "qty_for_all": None,
        },
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_done" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(OWNER_FAMILY[0]): 10,
        ht.uuid_of(OWNER_FAMILY[1]): 5,
    }
    assert set(spec.filters.get("not_checked") or []) == set(OWNER_FAMILY[2:])


def test_row6_thats_it_under_the_open_list_answers_what_is_filled():
    """The owner's actual row 5 state (an open 10-line task, lines 1 and 2 filled):
    "that's it" (done, no items) answers what is filled - nothing is re-asked."""
    task = _task([10, 5] + [None] * 8)
    state = _state_with_task(task)
    v = verdict(
        message_type="casual",
        entities=[],
        open_question_answer={"mode": "done", "items": [], "qty_for_all": None},
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_done" in plan.trace.rules_fired
    assert plan.trace.task_question is None
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(OWNER_FAMILY[0]): 10,
        ht.uuid_of(OWNER_FAMILY[1]): 5,
    }
    assert set(spec.filters.get("not_checked") or []) == set(OWNER_FAMILY[2:])


ROW9_CODES = ["SRTWC286-SH-150", "SRTWC287-S-150", "SRTWC286-SH"]
ROW9_QTYS = [5, 10, 1]


def test_open_question_answer_all_applies_to_every_line_of_an_answered_task():
    """Row 10: "how about 3 for all of them" over the three-product ANSWERED check
    (SRTWC286-SH-150 x 5, SRTWC287-S-150 x 10, SRTWC286-SH x 1) answers all three at 3,
    the same "all" reaching an ANSWERED task, not only an OPEN one."""
    task = ht.stock_task(list(zip(ROW9_CODES, ROW9_QTYS)), status=task_mod.ANSWERED)
    focus = Focus(products=ht.product_rows(*ROW9_CODES), domains=["inventory"], tasks=(task,))
    state = ht.state(focus, turn_no=11)
    v = verdict(entities=[], open_question_answer={"mode": "all", "items": [], "qty_for_all": 3})
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_all" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of(c): 3 for c in ROW9_CODES}
    (stock_task,) = [t for t in new_state.focus.tasks if t.kind == "stock_qty"]
    assert [slot.value for slot in stock_task.slots] == [3, 3, 3]


def test_open_question_answer_cancel_closes_the_task_and_fetches_nothing():
    task = _task([10, None] + [None] * 8)
    state = _state_with_task(task)
    v = verdict(
        entities=[], open_question_answer={"mode": "cancel", "items": [], "qty_for_all": None}
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_cancel" in plan.trace.rules_fired
    assert plan.fetch == []
    assert not any(t.kind == "stock_qty" for t in new_state.focus.tasks)


def test_open_question_answer_fill_revises_an_answered_task_and_reanswers_every_line():
    """Row 11: the same three lines with 3 each, "fill" mode over the three-product
    ANSWERED task from row 10 - all three revised and re-answered, none dropped."""
    task = ht.stock_task(list(zip(ROW9_CODES, [3, 3, 3])), status=task_mod.ANSWERED)
    focus = Focus(products=ht.product_rows(*ROW9_CODES), domains=["inventory"], tasks=(task,))
    state = ht.state(focus, turn_no=12)
    v = verdict(
        entities=[],
        open_question_answer={
            "mode": "fill",
            "items": [
                {"position": 1, "code": None, "qty": 3},
                {"position": 2, "code": None, "qty": 3},
                {"position": 3, "code": None, "qty": 3},
            ],
            "qty_for_all": None,
        },
    )
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_fill" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of(c): 3 for c in ROW9_CODES}


# =============================================================================== #
# F. Row 12 fallback: a bare quantity after a multi-product answered check clarifies
# =============================================================================== #


def _row12_task():
    return ht.stock_task(list(zip(ROW9_CODES, ROW9_QTYS)), status=task_mod.ANSWERED)


ROW12_QUESTION = (
    "Is 10 for all 3 products, or for one of them?\n"
    "1. SRTWC286-SH-150\n2. SRTWC287-S-150\n3. SRTWC286-SH"
)


def test_a_bare_quantity_after_a_multi_product_answered_check_clarifies_instead_of_fetching():
    task = _row12_task()
    focus = Focus(products=ht.product_rows(*ROW9_CODES), domains=["inventory"], tasks=(task,))
    state = ht.state(focus, turn_no=13)
    v = verdict(demand_qty=10, entities=[])
    new_state, plan = apply(state, v, build_policy())
    assert plan.fetch == []
    assert plan.trace.task_question == ROW12_QUESTION
    (stock_task,) = [t for t in new_state.focus.tasks if t.kind == "stock_qty"]
    assert [slot.value for slot in stock_task.slots] == ROW9_QTYS
    assert len(stock_task.slots) == 3


def test_a_lone_reference_position_after_a_multi_product_answered_check_clarifies_too():
    task = _row12_task()
    focus = Focus(products=ht.product_rows(*ROW9_CODES), domains=["inventory"], tasks=(task,))
    state = ht.state(focus, turn_no=13)
    v = verdict(reference_positions=[10], entities=[])
    new_state, plan = apply(state, v, build_policy())
    assert plan.fetch == []
    assert plan.trace.task_question == ROW12_QUESTION


# =============================================================================== #
# G. D29 fix: an entity's own quantity keeps its exact code, sibling prefix or not
# =============================================================================== #


def test_exact_code_kept_when_its_own_quantity_is_named_as_a_sibling_prefix():
    """SRTWC286-SH-150 is a prefix sibling of SRTWC286-SH; both are named with their
    own quantities in the same message, and D29's guard must keep both rather than
    dropping the "shorter" one as though it were an unrelated family sibling."""
    from app.services.chatbot.turn.apply import _exact_code_when_a_quantity_is_named
    from app.services.chatbot.turn.plan import FetchSpec, Trace

    spec = FetchSpec(
        domain="inventory", entities=ht.product_rows(*ROW9_CODES), filters={}, date_window=None
    )

    class _Plan:
        fetch = [spec]

    v = verdict(
        entities=[
            ht.asked(ROW9_CODES[0], ROW9_QTYS[0]),
            ht.asked(ROW9_CODES[1], ROW9_QTYS[1]),
            ht.asked(ROW9_CODES[2], ROW9_QTYS[2]),
        ]
    )
    trace = Trace()
    _exact_code_when_a_quantity_is_named(_Plan(), v, trace)
    assert {row["canonical_code"] for row in spec.entities} == set(ROW9_CODES)


def test_after_reply_keeps_a_named_code_that_shares_a_prefix_with_another_named_code():
    """The stock reply's own sibling drop (`task.after_reply`): "SRTWC286-SH" and
    "SRTWC286-SH-150" both named, the resolver's family placed SRTWC286-SH-200 as well,
    and every row still needs a quantity. SRTWC286-SH-200 is the only sibling nobody
    named; SRTWC286-SH-150 is asked for, never dropped."""
    reply = task_mod.after_reply(
        (),
        ht.envelopes(
            ht.row("SRTWC286-SH"), ht.row("SRTWC286-SH-150"), ht.row("SRTWC286-SH-200")
        ),
        turn_no=1,
        asked=[ht.asked("SRTWC286-SH"), ht.asked("SRTWC286-SH-150")],
    )
    (stock,) = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert [slot.label for slot in stock.slots] == ["SRTWC286-SH", "SRTWC286-SH-150"]
    assert reply.text == "How many units for each?\n1. SRTWC286-SH - \n2. SRTWC286-SH-150 - "


def test_row9_three_named_products_with_their_own_quantities_all_answered():
    """Console end-to-end: row 9's three lines, each an entity with its own code and
    quantity, answer all three products - SRTWC286-SH-150 is not dropped as a prefix
    sibling of SRTWC286-SH."""
    console = Console()
    console.state = replace(console.state, focus=Focus(domains=["inventory"]))
    v = verdict(
        domain_hint="inventory",
        entities=[
            ht.asked(ROW9_CODES[0], ROW9_QTYS[0]),
            ht.asked(ROW9_CODES[1], ROW9_QTYS[1]),
            ht.asked(ROW9_CODES[2], ROW9_QTYS[2]),
        ],
    )
    text = console.say(
        "1. SRTWC286-SH-150 - 5\n2. SRTWC287-S-150 - 10\n3. SRTWC286-SH - 1", v
    )
    for code, qty in zip(ROW9_CODES, ROW9_QTYS):
        assert f"{code} x {qty}: {TOO_BIG}" in text


# =============================================================================== #
# H. The prompt: STOCK_TASK_ADDENDUM vocabulary + the n8n JS literal cut
# =============================================================================== #


def test_stock_task_addendum_teaches_the_new_object_and_phrases():
    from app.services.chatbot_parser_prompt import STOCK_TASK_ADDENDUM

    for phrase in (
        "open_question_answer",
        "Open question:",
        "Recent exchanges",
        "that's it",
        "itu saja",
        "for all of them",
    ):
        assert phrase in STOCK_TASK_ADDENDUM, phrase


def test_the_n8n_js_literal_after_companies_offered_is_cut():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    assert "{{ (() =>" not in SEMANTIC_PARSER_PROMPT
    assert "When Executed by Another Workflow" not in SEMANTIC_PARSER_PROMPT
    assert "READING THE CURRENT MESSAGE IN CONTEXT" in SEMANTIC_PARSER_PROMPT


# =============================================================================== #
# Item 5: the owner's round 7 session (26 Sep 10:11Z to 10:14Z), rows 1 to 12, one
# Console.
#
# The Console has no resolver, so rows 7 and 8 are approximated (documented per-row
# below), and rows 3 to 6 have their task state set directly rather than left to
# cascade naturally through the harness - `Console` has no resolver to reopen a fresh
# ten-product family or collapse it, so the state each of those rows starts from is
# set explicitly to the shape the captain's brief describes for it, rather than
# trusting an intermediate reading this harness cannot produce on its own.
# =============================================================================== #


def test_the_owner_round7_session_replayed():
    console = Console()

    # Row 1: "check stock srtwc286" -> the numbered which-one list.
    text1 = console.first_ask("check stock srtwc286")
    assert text1 == LIST

    # Row 2: "semua of them" -> one point-form question, ten lines.
    text2 = console.say("semua of them", ALL_READINGS["every_position"])
    assert text2 == POINT_FORM

    # Row 3: "10 / 20 / 30 / 40 / 5", one number per line -> lines 1 to 5 filled and
    # answered now, 6 to 10 kept open and NOT re-asked (STOCK-QTY-PARTIAL-FILL, owner
    # ruling R1, 6 Oct 2026; before: re-asked blank). The verdict also carries
    # demand_qty 10 and message_type "casual" - noise the object must outrank.
    v3 = verdict(
        message_type="casual",
        demand_qty=10,
        entities=[],
        open_question_answer={
            "mode": "fill",
            "items": [
                {"position": i, "code": None, "qty": qty}
                for i, qty in zip(range(1, 6), [10, 20, 30, 40, 5])
            ],
            "qty_for_all": None,
        },
    )
    text3 = console.say("10 / 20 / 30 / 40 / 5", v3)
    expected3 = "\n".join(
        f"{code} x {qty}: {TOO_BIG}" for code, qty in zip(OWNER_FAMILY[:5], [10, 20, 30, 40, 5])
    )
    assert text3 == expected3
    (task3,) = [t for t in console.state.focus.tasks if t.kind == "stock_qty"]
    assert task3.status == task_mod.OPEN
    assert [s.value for s in task3.slots] == [10, 20, 30, 40, 5] + [None] * 5

    # Row 4: "check stock" + the list pasted back, line 1 = 10, line 2 = 5, rest
    # blank, chained straight on row 3 (review B1: no reset). The paste is the whole
    # answer, so row 3's 30, 40 and 5 on lines 3 to 5 are skipped with the blanks.
    v4 = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=(
            [ht.asked(OWNER_FAMILY[0], 10), ht.asked(OWNER_FAMILY[1], 5)]
            + [ht.asked(code, None) for code in OWNER_FAMILY[2:]]
        ),
        open_question_answer={
            "mode": "done",
            "items": [
                {"position": 1, "code": None, "qty": 10},
                {"position": 2, "code": None, "qty": 5},
            ],
            "qty_for_all": None,
        },
    )
    text4 = console.say(
        "check stock " + " ".join(f"{i}. {c}" for i, c in enumerate(OWNER_FAMILY, 1)), v4
    )
    expected45 = (
        f"{OWNER_FAMILY[0]} x 10: {TOO_BIG}\n{OWNER_FAMILY[1]} x 5: {TOO_BIG}"
    )
    assert text4 == expected45

    assert console.plans[-1].fetch[0].filters.get("not_checked") == OWNER_FAMILY[2:]

    # Row 5: the same list pasted back again, no prefix, chained: the check row 4
    # answered is the "last_answer" now, and its two lines are answered again.
    v5 = verdict(
        entities=[],
        open_question_answer={
            "mode": "done",
            "items": [
                {"position": 1, "code": None, "qty": 10},
                {"position": 2, "code": None, "qty": 5},
            ],
            "qty_for_all": None,
        },
    )
    text5 = console.say(
        " ".join(f"{i}. {c}" for i, c in enumerate(OWNER_FAMILY, 1)), v5
    )
    assert text5 == expected45

    # Row 6: "that's it", chained: nothing is open any more (row 4 answered it), so it
    # is a sign-off. Nothing is fetched and nothing asked; the real engine hands it to
    # the casual lane. The owner's own row 6 (under the still-open list) is
    # `test_row6_thats_it_under_the_open_list_answers_what_is_filled`.
    v6 = verdict(
        message_type="casual",
        entities=[],
        open_question_answer={"mode": "done", "items": [], "qty_for_all": None},
    )
    console.say("that's it", v6)
    assert console.plans[-1].fetch == [] and console.plans[-1].trace.task_question is None
    assert not any("open_question_answer" in r for r in console.plans[-1].trace.rules_fired)

    # Row 7 (approximated): a single-product ask for SRTWC286-SH-150, no resolver.
    console.state = replace(
        console.state,
        focus=Focus(
            products=ht.product_rows("SRTWC286-SH-150"),
            domains=["inventory"],
            tasks=(ht.stock_task([("SRTWC286-SH-150", None)], status=task_mod.OPEN),),
        ),
    )
    text7a = console.say(
        "check stock SRTWC286-SH-150",
        verdict(domain_hint="inventory", entities=[ht.asked("SRTWC286-SH-150")]),
    )
    assert text7a == "How many units of SRTWC286-SH-150?"
    text7b = console.say(
        "5",
        verdict(
            open_question_answer={
                "mode": "fill",
                "items": [{"position": 1, "code": "SRTWC286-SH-150", "qty": 5}],
                "qty_for_all": None,
            }
        ),
    )
    assert text7b == f"SRTWC286-SH-150 x 5: {TOO_BIG}"

    # Row 8 (approximated): the did-you-mean miss/pick is out of the Console's reach
    # with no resolver, so this is read as a direct SRTWC286-SH ask, then a revised
    # quantity, then a corrected one.
    console.state = replace(
        console.state,
        focus=Focus(
            products=ht.product_rows("SRTWC286-SH"),
            domains=["inventory"],
            tasks=(ht.stock_task([("SRTWC286-SH", None)], status=task_mod.OPEN),),
        ),
    )
    text8a = console.say(
        "SRTWC286-150", verdict(domain_hint="inventory", entities=[ht.asked("SRTWC286-SH")])
    )
    assert text8a == "How many units of SRTWC286-SH?"
    text8b = console.say("50", verdict(demand_qty=50, entities=[]))
    assert text8b == f"SRTWC286-SH x 50: {TOO_BIG}"
    text8c = console.say("howa bout 30", verdict(demand_qty=30, correction=True, entities=[]))
    assert text8c == f"SRTWC286-SH x 30: {TOO_BIG}"

    # Row 9: three named products, each with its own quantity - all three answered,
    # not just two (the D29 fix).
    v9 = verdict(
        domain_hint="inventory",
        entities=[
            ht.asked(ROW9_CODES[0], ROW9_QTYS[0]),
            ht.asked(ROW9_CODES[1], ROW9_QTYS[1]),
            ht.asked(ROW9_CODES[2], ROW9_QTYS[2]),
        ],
    )
    text9 = console.say(
        "1. SRTWC286-SH-150 - 5\n2. SRTWC287-S-150 - 10\n3. SRTWC286-SH - 1", v9
    )
    for code, qty in zip(ROW9_CODES, ROW9_QTYS):
        assert f"{code} x {qty}: {TOO_BIG}" in text9

    # Row 10: "how about 3 for all of them" - the three products just listed, 3 each.
    text10 = console.say(
        "how about 3 for all of them",
        verdict(entities=[], open_question_answer={"mode": "all", "items": [], "qty_for_all": 3}),
    )
    for code in ROW9_CODES:
        assert f"{code} x 3: {TOO_BIG}" in text10

    # Row 11: the same three lines with 3 each - all three x 3 again.
    text11 = console.say(
        "1. SRTWC286-SH-150 - 3\n2. SRTWC287-S-150 - 3\n3. SRTWC286-SH - 3",
        verdict(
            entities=[],
            open_question_answer={
                "mode": "fill",
                "items": [
                    {"position": 1, "code": None, "qty": 3},
                    {"position": 2, "code": None, "qty": 3},
                    {"position": 3, "code": None, "qty": 3},
                ],
                "qty_for_all": None,
            },
        ),
    )
    for code in ROW9_CODES:
        assert f"{code} x 3: {TOO_BIG}" in text11

    # Row 12: a bare "10" over the finished three-product check - CLARIFY, never a
    # bigger list and never a blind fetch.
    text12 = console.say("10", verdict(demand_qty=10, entities=[]))
    assert text12 == ROW12_QUESTION
    # And the answer to the clarify: "all" (the parser's mode all, no number of its own).
    text13 = console.say(
        "all",
        verdict(entities=[], open_question_answer={"mode": "all", "items": [], "qty_for_all": None}),
    )
    for code in ROW9_CODES:
        assert f"{code} x 10: {TOO_BIG}" in text13

    transcript = "\n".join(console.transcript)
    assert EM_DASH not in transcript and EN_DASH not in transcript
    assert GREETING not in transcript


# =============================================================================== #
# Engine-level: the user block carries the new lines on a live turn
# =============================================================================== #


def _multi_slot_question_state() -> dict[str, Any]:
    focus = Focus(
        products=ht.product_rows("SRTWC286-SH", "SRTWC286-SH-150"),
        domains=["inventory"],
        tasks=(
            task_mod.Task(
                kind="stock_qty",
                domain="inventory",
                slots=(
                    task_mod.Slot(ht.uuid_of("SRTWC286-SH"), "SRTWC286-SH"),
                    task_mod.Slot(ht.uuid_of("SRTWC286-SH-150"), "SRTWC286-SH-150"),
                ),
                opened_at_turn=2,
                touched_at_turn=2,
            ),
        ),
    )
    return {"focus": focus_to_wire(focus), "open_question": None}


def _message(text: str, message_id: str) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": message_id,
            "contactId": CONTACT_ID,
            "channelId": "whatsapp",
            "traffic": "incoming",
            "message": {"type": "text", "text": text},
        },
    }


def test_engine_user_block_carries_the_open_question_line_for_a_multi_slot_task(
    session_factory, seeded, stub_parser, stub_access, monkeypatch
):
    blocks: list[str] = []
    stub_parser(_parser_output(message_type="casual", entities=[]), on_call=blocks.append)
    stub_access()

    from app.services.ai_assistant_service import MCPRuntimeClient

    def _tool(self, tool_name, args):
        return json.dumps({"stock_availability": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _tool)
    engine_mod.run_turn(
        _envelope(
            is_test=True,
            previous_conversation_state=_multi_slot_question_state(),
            message=_message("10", "ZZT-r8-openq-1"),
        ),
        session_factory=session_factory,
    )
    assert len(blocks) == 1
    assert "Open question: {" in blocks[0]
    assert '"kind":"quantities"' in blocks[0]


def test_engine_user_block_carries_recent_exchanges_on_a_second_turn(
    session_factory, seeded, stub_parser, stub_access, monkeypatch
):
    blocks: list[str] = []
    stub_parser(_parser_output(), on_call=blocks.append)
    stub_access()

    from app.services.ai_assistant_service import MCPRuntimeClient

    def _tool(self, tool_name, args):
        return json.dumps({"master_products": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _tool)

    engine_mod.run_turn(_envelope(is_test=True), session_factory=session_factory)
    engine_mod.run_turn(
        _envelope(is_test=True, message=_message("wc287", "ZZT-r8-recent-2")),
        session_factory=session_factory,
    )
    assert len(blocks) == 2
    second_block = blocks[1]
    assert "Recent exchanges, oldest first:" in second_block
    assert "User: price for SRTWC8517" in second_block
    assert "Assistant: (the Previous response)" in second_block


# =============================================================================== #
# Review round (captain, after the reviewer pass): B1, S1 to S5, T1 and the nits
# =============================================================================== #


def _row12_state():
    focus = Focus(products=ht.product_rows(*ROW9_CODES), domains=["inventory"], tasks=(_row12_task(),))
    return ht.state(focus, turn_no=13)


def test_b1_a_pasted_list_skips_lines_noted_earlier_but_left_blank():
    """Row 3 then row 4 with no reset: lines 3 to 5 carry 30, 40, 5 from row 3, the
    paste leaves them blank, and only lines 1 and 2 are answered."""
    state = _state_with_task(_task([10, 20, 30, 40, 5] + [None] * 5))
    v = verdict(
        entities=[],
        open_question_answer={
            "mode": "done",
            "items": [{"position": 1, "code": None, "qty": 10}, {"position": 2, "code": None, "qty": 5}],
            "qty_for_all": None,
        },
    )
    _new, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(OWNER_FAMILY[0]): 10,
        ht.uuid_of(OWNER_FAMILY[1]): 5,
    }
    assert spec.filters.get("not_checked") == OWNER_FAMILY[2:]


def test_s1_a_line_number_after_the_clarify_places_the_asked_quantity():
    """"2" after "Is 10 for all 3 products, or for one of them?", with no declared
    answer (mode null, the parser read a position): line 2 at 10, never "Is 2 for all
    3 products". The other lines keep their quantities and all three are answered."""
    asked, _plan = apply(_row12_state(), verdict(demand_qty=10, entities=[]), build_policy())
    (task,) = [t for t in asked.focus.tasks if t.kind == "stock_qty"]
    assert task.asked_qty == 10
    assert task_mod.open_question(asked.focus.tasks)["asked_qty"] == 10
    after = replace(asked, turn_no=asked.turn_no + 1)
    new_state, plan = apply(after, verdict(reference_positions=[2], entities=[]), build_policy())
    assert "asked_quantity_placed" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of(ROW9_CODES[0]): 5,
        ht.uuid_of(ROW9_CODES[1]): 10,
        ht.uuid_of(ROW9_CODES[2]): 1,
    }
    assert all(t.asked_qty is None for t in new_state.focus.tasks)


def test_s1_the_asked_quantity_lasts_one_turn():
    asked, _plan = apply(_row12_state(), verdict(demand_qty=10, entities=[]), build_policy())
    later, _ = apply(
        replace(asked, turn_no=asked.turn_no + 1),
        verdict(message_type="casual", entities=[]),
        build_policy(),
    )
    assert all(t.asked_qty is None for t in later.focus.tasks)


def test_s2_a_number_about_another_domain_is_not_the_stock_clarify():
    v = verdict(domain_hint="promotion", intent_hint="check_promotion", demand_qty=10, entities=[])
    _new, plan = apply(_row12_state(), v, build_policy())
    assert plan.trace.task_question != ROW12_QUESTION
    assert "bare_number_over_a_finished_answer_asks_which" not in plan.trace.rules_fired


def test_s3_a_parked_check_is_not_offered_and_not_answered():
    parked = _task([10, 5] + [None] * 8, status=task_mod.PARKED)
    assert task_mod.open_question((parked,)) is None
    state = _state_with_task(parked)
    v = verdict(
        message_type="casual",
        entities=[],
        open_question_answer={"mode": "done", "items": [], "qty_for_all": None},
    )
    _new, plan = apply(state, v, build_policy())
    assert not any("open_question_answer" in r for r in plan.trace.rules_fired)
    assert ht.inventory_specs(plan) == []


def test_s4_the_newest_reply_is_printed_when_it_is_not_the_previous_response():
    block = parser_mod.build_user_block(
        previous_response="something else",
        latest_user_message="10 / 20",
        pending_kind=None,
        recent_exchanges=[("(media)", "How many units for each?")],
    )
    assert "User: (media)" in block.splitlines()
    assert "Assistant: How many units for each?" in block.splitlines()


def test_s4_a_media_turn_is_an_exchange_not_skipped(session_factory):
    from app.services.chatbot import turn_runtime

    db = session_factory()
    contact = str(CONTACT_ID) + "-media"
    db.add(
        ChatbotTurn(
            id=str(uuid.uuid4()),
            contact_respond_id=contact,
            message_id="ZZT-r8-media-1",
            ingress="webhook",
            is_test=False,
            envelope={"message": {"message": {"message": {"type": "audio"}}}, "contact": {"id": contact}},
            status="done",
            response={"reply": {"text": "How many units for each?"}},
        )
    )
    db.commit()
    assert turn_runtime.recent_exchanges(
        db, contact_respond_id=contact, ingress="webhook", is_test=False
    ) == [("(media)", "How many units for each?")]


def test_s5_the_hint_prints_the_lines_when_no_open_question_is_shown():
    task = _task([10] + [None] * 9)
    block = parser_mod.build_user_block(
        previous_response="(none)",
        latest_user_message="ok",
        pending_kind="escalate_offer",
        focus=Focus(domains=["inventory"], tasks=(task,)),
    )
    assert "1. SRTWC286-SH - 10; 2. SRTWC286-SH-150" in block
    assert "see Open question" not in block


def test_t1_a_product_not_on_the_list_leaves_the_object_unapplied():
    """"10 for all, and check SRTKT1631SS": the new product is a new ask, never eaten."""
    state = _state_with_task(_task([None] * 10))
    v = verdict(
        domain_hint="inventory",
        entities=[ht.asked("SRTKT1631SS")],
        open_question_answer={"mode": "all", "items": [], "qty_for_all": 10},
    )
    _new, plan = apply(state, v, build_policy())
    assert not any("open_question_answer" in r for r in plan.trace.rules_fired)


def test_t1_an_open_question_of_another_kind_leaves_the_object_unapplied():
    from app.services.chatbot.turn import pending as turn_pending

    state = replace(
        _state_with_task(_task([None] * 10)),
        pending=turn_pending.ask("escalate_offer", [], asked_at_turn=5),
    )
    v = verdict(entities=[], open_question_answer={"mode": "all", "items": [], "qty_for_all": 10})
    _new, plan = apply(state, v, build_policy())
    assert not any("open_question_answer" in r for r in plan.trace.rules_fired)


def test_a_quantity_of_zero_or_less_leaves_the_object_unapplied():
    state = _state_with_task(_task([None] * 10))
    for bad in (0, -5):
        v = verdict(
            entities=[],
            open_question_answer={
                "mode": "fill",
                "items": [{"position": 1, "code": None, "qty": bad}],
                "qty_for_all": None,
            },
        )
        _new, plan = apply(state, v, build_policy())
        assert not any("open_question_answer" in r for r in plan.trace.rules_fired)
