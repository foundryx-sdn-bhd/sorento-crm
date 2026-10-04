r"""The schema/prompt/reader triangle: every key the engine reads off a parser verdict is
declared in the schema, every declared key is named in the prompt, and the schema refuses
an emission the model invents that the prompt never taught it (AC-105, AC-1506).

Three separate guarantees, three separate failure modes for a customer turn:

* A key the engine READS but the schema does not DECLARE never survives a provider round
  trip (`assert_emission` in `head/parser.py` drops anything not in `DECLARED_KEYS`), so a
  reader keyed on it is silently reading `None` forever - a defect that never shows up as
  an exception, only as a customer question the bot never quite answers.
* A key the schema DECLARES but the prompt never MENTIONS is a key the model has no reason
  to ever fill - `additionalProperties: False` still lets it emit `null` for a key it was
  never taught to reason about, so the key exists in name only.
* `additionalProperties: False` on the schema itself (and on every nested object) is what
  turns "the model happened to emit exactly these keys on 488 captured turns" into an
  actual provider-side guarantee rather than an empirically-true-so-far accident.

The read set below is MEASURED, not assumed: every file under `app/services/chatbot/turn/`,
`turn_runtime.py`, `engine.py` and `lanes/` was grepped for a dict-get/dict-index read of
each of the 35 `DECLARED_KEYS` (the raw verdict dict is called `verdict` at its entry point
in `engine.py` and threads downstream under renamed parameters - `parse_output`, `parser`,
`out`, `q`, `semantic_input`, `qf` - as it is copied into `apply.py`'s state and further into
`lanes/business/*`; the file column below names ONE representative reader per key, not
every one). Re-run the grep in the module docstring's own commands below before trusting
this table after an engine refactor - a key moving to a brand-new file would not fail this
test (the check is "is this key read SOMEWHERE", not "is it read at this exact file"), but a
key that stops being read anywhere and a key that starts being read without ever being
declared both would.

    grep -rnoE 'verdict(\.get\(\s*"[a-zA-Z_]+"|\["[a-zA-Z_]+"\])' \
        app/services/chatbot/turn/ app/services/chatbot/turn_runtime.py \
        app/services/chatbot/engine.py app/services/chatbot/lanes/
    grep -rn '"<key>"' app/services/chatbot/turn/ app/services/chatbot/turn_runtime.py \
        app/services/chatbot/engine.py app/services/chatbot/lanes/
"""
from __future__ import annotations

import pytest

from app.services.chatbot.head.parser import DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA
from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

# key -> one file that reads it off a parser-verdict-shaped dict, measured 17 Sep 2026 (see
# the grep commands in the module docstring). Every key here is READ somewhere in the
# engine; this table is evidence for (a) below, not an independent source of truth - a key
# missing from DECLARED_KEYS would still fail (a) even if someone added it here by mistake.
MEASURED_VERDICT_READS: dict[str, str] = {
    "message_type": "app/services/chatbot/turn/apply.py",
    "intent_hint": "app/services/chatbot/turn/memory.py",
    "domain_hint": "app/services/chatbot/turn/memory.py",
    "scope_intent": "app/services/chatbot/turn/apply.py",
    "is_affirmative": "app/services/chatbot/turn/apply.py",
    "user_goal": "app/services/chatbot/lanes/business/answer.py",
    "continuation": "app/services/chatbot/turn/apply.py",
    "access_levels": "app/services/chatbot/turn/tail.py",
    "broaden_axis": "app/services/chatbot/turn/apply.py",
    "date_mode": "app/services/chatbot/turn/apply.py",
    "date_filter_start": "app/services/chatbot/turn/apply.py",
    "date_filter_end": "app/services/chatbot/turn/apply.py",
    "match_mode": "app/services/chatbot/lanes/business/resolve_gate.py",
    "demand_qty": "app/services/chatbot/turn/apply.py",
    "entities": "app/services/chatbot/turn/memory.py",
    "entity_op": "app/services/chatbot/turn/apply.py",
    # `scope_exclusive` row REMOVED 17 Sep 2026 (coder 21's item 2, `3fc38c409`): the
    # key is gone from the schema AND the prompt now (the engine read was already
    # retired by coder 20's earlier slice) - `turn/decide.py::_subject_reading`'s
    # `domain_in_message` table is the sole discriminator. 36 declared keys -> 35.
    "requested_attributes": "app/services/chatbot/turn/apply.py",
    "contains_flyer": "app/services/chatbot/turn/tail.py",
    "reference_positions": "app/services/chatbot/turn/apply.py",
    "reference_target": "app/services/chatbot/turn/apply.py",
    "person_mention": "app/services/chatbot/turn/apply.py",
    "is_active": "app/services/chatbot/lanes/business/fetch.py",
    "order_status": "app/services/chatbot/turn/state.py",
    "group_by": "app/services/chatbot/turn/apply.py",
    "top_n": "app/services/chatbot/turn/apply.py",
    "correction": "app/services/chatbot/turn/apply.py",
    "routing": "app/services/chatbot/turn_runtime.py",
    "escalation": "app/services/chatbot/turn/route.py",
    "document": "app/services/chatbot/turn/state.py",
    "status": "app/services/chatbot/turn/state.py",
    "asks": "app/services/chatbot/turn/apply.py",
    "topic_reset": "app/services/chatbot/turn/apply.py",
    "anaphora": "app/services/chatbot/engine.py",
    # 17 Sep 2026, coder 20's S4-adjacent slice (`8f1ac903c` and descendants): the
    # `domain_in_message`/`broaden_to` pair joins the schema.
    "broaden_to": "app/services/chatbot/turn/apply.py",
    "domain_in_message": "app/services/chatbot/turn/decide.py",
    # 20 Sep 2026 (coder 25's sales report port, `2682a0bd6`): `sales_channel` joins the
    # schema so the sales report's Dealer/Project filter answer is a structured parser
    # field, never a message-text guess - `apply.py` reads it both to seed `Focus.
    # sales_channel` when a filter answer names a channel and to settle a fresh channel
    # word onto the focus directly. 35 declared keys -> 36.
    "sales_channel": "app/services/chatbot/turn/apply.py",
    # 26 Sep 2026 (#1267 sales reports S1): the sales analysis answer's Basis and Company
    # are structured parser fields, read into the fetch's semantic input. 36 -> 38.
    "sales_basis": "app/services/chatbot/lanes/business/__init__.py",
    "sales_company": "app/services/chatbot/lanes/business/__init__.py",
    # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
    # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D13: "go ahead without
    # answering the open question", read by `turn/task.py::StockQtyTask.claims`/
    # `fill`, which `apply.py` runs before decide's four outcomes. 36 declared keys ->
    # 37. The per-entity `entities[].quantity` is NOT a row here: this table is
    # top-level verdict keys, and that one is a nested field of the `entities` row
    # above.
    "proceed_anyway": "app/services/chatbot/turn/task.py",
    # PR #1247 round 8: the parser's declared answer to the "Open question:" object,
    # read by `turn/apply.py::_open_question_answer` before any shape rule. 37 -> 38.
    # Merged with #1267's two rows above: 38 + 2 -> 40.
    "open_question_answer": "app/services/chatbot/turn/apply.py",
    # 26 Sep 2026 (PLAN-chatbot-top-x-hot-selling-24sep.md S4): the top selling ask's
    # three axes, read by `turn/apply.py::_top_selling_rules` onto `Focus.top_selling`.
    # 38 declared keys -> 41.
    "rank_by": "app/services/chatbot/turn/apply.py",
    "basis": "app/services/chatbot/turn/apply.py",
    "rank_group": "app/services/chatbot/turn/apply.py",
    # Fix lane round 4 (owner retest, 27 Sep 2026): "cold selling", "least sold".
    # 41 declared keys -> 42.
    "rank_direction": "app/services/chatbot/turn/apply.py",
    # 29 Sep 2026 (PLAN-chatbot-customer-scope-29sep D2): "my" / "me" as the asker's own
    # account; the engine's `_customer_scope_gate` reads it off the verdict. 44 -> 45.
    "self_reference": "app/services/chatbot/engine.py",
    # 26 Sep 2026 (chatbot memory lane A S3, migration `mem_0002_parser_memory`):
    # `profile_statement` joins the schema - `engine.py`'s tail reads it and applies it
    # as a `stated` fact through `profile_facts.apply_statement`. Counted after main's rows above.
    # Round 3 (AC-MEM033/069, merged 5b110df8) RENAMES it `profile_statements` (a LIST,
    # up to 3) - same reader, same count.
    "profile_statements": "app/services/chatbot/engine.py",
    # 29 Sep 2026 (PLAN-po-spo-warehouse-29sep S4/S5): the PO/SPO sort axis, read by
    # `turn/apply.py::_focus_rules` onto `Focus.sort`. 45 declared keys -> 47.
    "sort_by": "app/services/chatbot/turn/apply.py",
    "sort_dir": "app/services/chatbot/turn/apply.py",
    # 4 Oct 2026 (LOWSTOCK-SEMANTIC): the low stock filters, read by
    # `lanes/business/low_stock_ask.reading`. 48 declared keys -> 49.
    "low_stock": "app/services/chatbot/lanes/business/low_stock_ask.py",
}


def test_measured_read_set_matches_the_49_declared_keys():
    """The table above is complete and has no typo - every declared key is measured read
    exactly once, and the table names nothing DECLARED_KEYS does not also carry. Catches a
    stale table before it can hide a real drift in the two tests below."""
    assert set(MEASURED_VERDICT_READS) == set(DECLARED_KEYS), (
        set(MEASURED_VERDICT_READS) ^ set(DECLARED_KEYS)
    )


@pytest.mark.parametrize("key", sorted(MEASURED_VERDICT_READS))
def test_every_key_the_engine_reads_is_declared_in_the_schema(key):
    """(a) A key the engine reads off a verdict must be one the schema DECLARES (required
    + present in `properties`) - otherwise `assert_emission` strips it out of every real
    provider response before any reader ever sees it, and the read is reading `None`
    forever with no exception to notice it by."""
    assert key in DECLARED_KEYS, (
        f"{key!r} is read at {MEASURED_VERDICT_READS[key]} but is not in "
        f"head/parser.py::DECLARED_KEYS - the provider strips it before any reader sees it"
    )
    assert key in PARSE_OUTPUT_JSON_SCHEMA["properties"], (
        f"{key!r} is in DECLARED_KEYS but head/parser.py's own schema properties do not "
        f"carry it - the two are built from the same dict and should never disagree"
    )


@pytest.mark.parametrize("key", sorted(DECLARED_KEYS))
def test_every_declared_key_is_named_in_the_prompt(key):
    """(b) A key the schema REQUIRES the provider to fill must be a key the PROMPT actually
    teaches the model to reason about - `additionalProperties: False` only says "no keys
    beyond these"; it says nothing about whether the model has ever been told what a
    declared key means, and a key the prompt is silent on gets filled with `null` forever
    whatever the customer actually said."""
    assert key in SEMANTIC_PARSER_PROMPT, (
        f"{key!r} is a required key in PARSE_OUTPUT_JSON_SCHEMA but does not appear "
        f"anywhere in SEMANTIC_PARSER_PROMPT (chatbot_parser_prompt.py) - the model was "
        f"never taught to reason about it"
    )


def test_schema_refuses_additional_properties():
    """(c) The top-level object refuses an undeclared key outright - a provider that
    occasionally invents one (observed in production before R5: `{"nope": true}` routed a
    whole turn off tolerant defaults) fails validation instead of finishing `done` on a
    verdict nobody asked for."""
    assert PARSE_OUTPUT_JSON_SCHEMA.get("additionalProperties") is False


@pytest.mark.parametrize(
    "nested_key", ["routing", "escalation", "anaphora", "entities"]
)
def test_nested_objects_also_refuse_additional_properties(nested_key):
    """The three nested OBJECT keys (`routing`, `escalation`, `anaphora`) and the object
    shape inside the `entities` array each carry their own `additionalProperties: False`
    too - a strict top level with a permissive nested object is the same H44 hole one
    level down."""
    props = PARSE_OUTPUT_JSON_SCHEMA["properties"]
    node = props[nested_key]
    if nested_key == "entities":
        node = node["items"]
    assert node.get("additionalProperties") is False, (
        f"{nested_key!r}'s object shape does not refuse additional properties"
    )
