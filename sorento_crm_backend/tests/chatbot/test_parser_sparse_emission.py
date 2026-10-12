"""CHAT-SPARSE: a sparse parser emission reads exactly as the strict one it stands for.

A prompt version carrying `SPARSE_OUTPUT_MARKER` writes only the keys that carry a value
(`documentation/plans/chatbot/PLAN-chat-sparse-emission-12oct.md`). `head/parser.parse`
calls it non-strict and hydrates the answer back to the full shape, so every reader
downstream sees the dict a strict emission of the same reading would have been. Each key
family the engine reads is pinned here: the sparse wire form of a strict emission must
come out of `parse` equal to that strict emission.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services import llm_provider
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot_parser_prompt import (
    SEMANTIC_PARSER_PROMPT,
    SPARSE_OUTPUT_ADDENDUM,
    SPARSE_OUTPUT_BLOCK,
    SPARSE_OUTPUT_MARKER,
    SPARSE_PHRASE_EDITS,
    STRICT_OUTPUT_BLOCK_END,
    STRICT_OUTPUT_BLOCK_START,
    sparse_prompt,
)

DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"

#: What a strict emission writes for a message that says nothing: the shape of the 20
#: strict live emissions of 12 Oct 2026 (an "is it a yes" null, every list empty, every
#: object written out with its empties). Each family test lays its own values on top.
EMPTY_ENTITY = {
    "raw": None,
    "hint": None,
    "canonical_code": None,
    "current_message": None,
    "confident": None,
    "hint_confident": None,
    "quantity": None,
    "spec_key": None,
    "spec_value": None,
    "account": None,
}


def strict_base() -> dict:
    out = {key: None for key in parser_mod.DECLARED_KEYS}
    out.update(
        message_type="business_query",
        entity_op="reuse",
        domain_in_message=False,
        access_levels=[],
        entities=[],
        requested_attributes=[],
        reference_positions=[],
        self_reference=False,
        routing={"suggested_team": None, "suggested_agent": None},
        escalation={"is_escalation_confirmation": None, "company_pick": None},
        open_question_answer={"mode": None, "picked": [], "items": [], "qty_for_all": None},
        low_stock={"group_by": None, "categories": [], "all_categories": None, "brands": [], "suppliers": []},
        anaphora={"backward_reference": None},
    )
    return out


#: Flags whose `false` is not their null (read `is False`): the prompt has them written
#: whenever true or false (`SPARSE_OUTPUT_ADDENDUM`).
FALSE_IS_A_VALUE = {
    "is_affirmative", "is_active", "domain_in_message", "current_message", "confident", "hint_confident",
    "spec_value",
}

#: Every other flag: no reader tells its false from its null (pinned by
#: `test_no_reader_tells_a_plain_flags_false_from_its_null`), so a sparse emission leaves
#: a false one out and it reads back as null.
FALSE_IS_NULL = {
    "continuation", "contains_flyer", "correction", "topic_reset", "proceed_anyway", "ranking_refine",
    "is_escalation_confirmation", "backward_reference", "all_categories",
}


def as_read(value):
    """A strict emission as a sparse one reads back: a plain flag's false is its null."""
    if isinstance(value, dict):
        return {k: None if k in FALSE_IS_NULL and v is False else as_read(v) for k, v in value.items()}
    if isinstance(value, list):
        return [as_read(v) for v in value]
    return value


def sparse_wire(value, schema=parser_mod.PARSE_OUTPUT_SPARSE_JSON_SCHEMA):
    """`value` as the sparse prompt writes it: an empty value (null, [], false, or an
    object of empties) is left out, unless its object requires the key or it is the
    `false` of a flag whose false is a value."""
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        props = schema.get("properties", {})
        out = {}
        for key, item in value.items():
            child = sparse_wire(item, props.get(key, {}))
            if key in required or (key in FALSE_IS_A_VALUE and child is False) or not _empty(child):
                out[key] = child
        return out
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [sparse_wire(item, schema["items"]) for item in value]
    return value


def _empty(value) -> bool:
    return value is None or value is False or value == [] or value == {}


@pytest.fixture
def wire(monkeypatch):
    """Answers the parser's provider call with the JSON put in `wire.content`, and keeps
    what the call was asked for."""

    class Wire:
        content = "{}"
        calls: list[dict] = []

    def fake_chat(provider, key, model, messages, **kwargs):
        Wire.calls.append(kwargs)
        return llm_provider.ChatResult(content=Wire.content, prompt_tokens=10, completion_tokens=50, total_tokens=60)

    Wire.calls = []
    monkeypatch.setattr(llm_call, "chat", fake_chat)
    return Wire


def config(sparse: bool) -> parser_mod.ParserConfig:
    return parser_mod.ParserConfig(
        system_prompt="S", prompt_version=7, provider="openai", model="m", api_key="k", sparse=sparse
    )


def through_parse(wire, strict: dict) -> tuple[dict, dict]:
    sent = sparse_wire(copy.deepcopy(strict))
    wire.content = json.dumps(sent)
    return dict(parser_mod.parse(config(True), "block")), sent


# --------------------------------------------------------------------------- #
# Each key family: sparse in, the strict emission out
# --------------------------------------------------------------------------- #

FAMILIES = {
    "nothing said": {},
    "escalation": {
        "message_type": "request_for_help",
        "is_affirmative": True,
        "routing": {"suggested_team": "marketing", "suggested_agent": None},
        "escalation": {"is_escalation_confirmation": True, "company_pick": "Sorento Trading"},
    },
    "escalation declined": {
        "message_type": "casual",
        "is_affirmative": False,
        "escalation": {"is_escalation_confirmation": False, "company_pick": None},
    },
    "open_question_answer pick": {
        "open_question_answer": {"mode": "pick", "picked": [2], "items": [], "qty_for_all": None},
        "reference_positions": [2],
    },
    "open_question_answer fill": {
        "open_question_answer": {
            "mode": "fill",
            "picked": [],
            "items": [{"position": 1, "code": None, "qty": 10}, {"position": 2, "code": "SRTWT7408", "qty": None}],
            "qty_for_all": None,
        },
    },
    "open_question_answer all": {
        "open_question_answer": {"mode": "all", "picked": [], "items": [], "qty_for_all": 10},
    },
    "low_stock": {
        "intent_hint": "low_stock_report",
        "domain_hint": "inventory",
        "domain_in_message": True,
        "low_stock": {
            "group_by": "supplier",
            "categories": ["water closet"],
            "all_categories": None,
            "brands": [],
            "suppliers": ["ACME"],
        },
    },
    "low_stock all categories": {
        "low_stock": {"group_by": None, "categories": [], "all_categories": True, "brands": [], "suppliers": []},
    },
    "sales keys": {
        "domain_hint": "order",
        "intent_hint": "check_order",
        "order_status": "sales_report",
        "status": "sales_report",
        "sales_channel": "dealer",
        "sales_basis": "delivered",
        "sales_company": "SRT",
        "rank_by": "quantity",
        "basis": "ordered",
        "rank_group": "item",
        "rank_direction": "bottom",
        "ranking_refine": True,
        "ranking_drill": "scoped",
        "measure": "amount",
        "group_by": "month",
        "top_n": 10,
        "date_filter_start": "2026-03-01",
        "date_filter_end": "2026-03-31",
    },
    "entities spec_key and quantity": {
        "entity_op": "replace_combine",
        "domain_in_message": True,
        "entities": [
            {**EMPTY_ENTITY, "raw": "M210-GM", "hint": "product", "current_message": True, "confident": True,
             "hint_confident": True, "quantity": 5},
            {**EMPTY_ENTITY, "raw": "washdown", "hint": "specification", "current_message": True,
             "confident": False, "hint_confident": False, "spec_key": "flush_type", "spec_value": "washdown"},
            {**EMPTY_ENTITY, "raw": "Soon Heng", "hint": "customer", "current_message": False, "confident": True,
             "account": 1, "canonical_code": "C-001"},
        ],
    },
    "inactive records and a no spec": {
        "is_active": False,
        "domain_hint": "promotion",
        "entity_op": "replace_combine",
        "entities": [
            {**EMPTY_ENTITY, "raw": "non rimless", "hint": "specification", "current_message": True,
             "confident": True, "hint_confident": True, "spec_key": "is_rimless", "spec_value": False},
        ],
    },
    "lists and the rest": {
        "document": ["DO"],
        "asks": [{"domain": "inventory", "intent": "check_stock"}, {"domain": "incoming_stock", "intent": None}],
        "po_refs": ["4500123"],
        "profile_statements": [{"key": "language", "value": "Malay"}, {"key": "usual_sites", "value": ["Penang"]}],
        "chat_report": {"group_by": "topic", "measure": "asks", "subject": None},
        "anaphora": {"backward_reference": True},
        "topic_reset": True,
        "proceed_anyway": True,
        "self_reference": True,
        "continuation": True,
        "correction": True,
        "access_levels": ["dealer"],
        "requested_attributes": ["quantity"],
        "demand_qty": 3,
        "match_mode": "or",
    },
}


@pytest.mark.parametrize("family", list(FAMILIES))
def test_a_sparse_emission_parses_to_the_strict_one(wire, family):
    strict = {**strict_base(), **FAMILIES[family]}

    parsed, sent = through_parse(wire, strict)

    assert len(json.dumps(sent)) < len(json.dumps(strict))
    assert parsed == as_read(strict)


def test_nothing_said_is_three_keys_on_the_wire(wire):
    _, sent = through_parse(wire, strict_base())
    assert sent == {"message_type": "business_query", "entity_op": "reuse", "domain_in_message": False}


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ("is_affirmative", ()),
        ("is_active", ()),
        ("spec_value", ("entities", 0)),
        ("current_message", ("entities", 0)),
        ("confident", ("entities", 0)),
        ("hint_confident", ("entities", 0)),
    ],
)
def test_a_written_false_stays_false_and_an_absent_flag_is_null(wire, key, path):
    """The values read as `is False` (`turn/decide.py:614`, `head/grounding.py:430,612,638`,
    `engine.py:496`, `lanes/business/answer.py:3321`): an absent one must never come back
    as false."""
    entity = {"raw": "SRTWC286", "hint": "product"}
    written = {"message_type": "casual", "entity_op": "reuse", "domain_in_message": False, "entities": [entity]}
    target = written
    for step in path:
        target = target[step]
    target[key] = False
    wire.content = json.dumps(written)
    parsed = dict(parser_mod.parse(config(True), "block"))
    read = parsed
    for step in path:
        read = read[step]
    assert read[key] is False

    del target[key]
    wire.content = json.dumps(written)
    parsed = dict(parser_mod.parse(config(True), "block"))
    read = parsed
    for step in path:
        read = read[step]
    assert read[key] is None


def test_an_absent_object_reads_as_its_empty_object(wire):
    wire.content = json.dumps({"message_type": "casual", "entity_op": "reuse", "domain_in_message": False})
    parsed = parser_mod.parse(config(True), "block")
    assert parsed["escalation"] == {"is_escalation_confirmation": None, "company_pick": None}
    assert parsed["open_question_answer"] == {"mode": None, "picked": [], "items": [], "qty_for_all": None}
    assert parsed["low_stock"]["categories"] == []
    assert parsed["routing"] == {"suggested_team": None, "suggested_agent": None}
    assert parsed["chat_report"] is None
    assert parsed["self_reference"] is False
    assert parsed["entities"] == []


# --------------------------------------------------------------------------- #
# The wire: strict stays strict, sparse is checked here
# --------------------------------------------------------------------------- #


def test_a_strict_version_is_called_exactly_as_before(wire):
    wire.content = json.dumps(strict_base())
    parser_mod.parse(config(False), "block")
    (call,) = wire.calls
    assert call["json_schema"] is parser_mod.PARSE_OUTPUT_JSON_SCHEMA
    assert "json_schema_strict" not in call


def test_a_strict_version_still_refuses_a_missing_key(wire):
    emission = strict_base()
    del emission["escalation"]
    wire.content = json.dumps(emission)
    with pytest.raises(parser_mod.ParserError, match="escalation"):
        parser_mod.parse(config(False), "block")


def test_a_sparse_version_is_called_non_strict_with_the_sparse_schema(wire):
    wire.content = json.dumps({"message_type": "casual", "entity_op": "reuse", "domain_in_message": False})
    parser_mod.parse(config(True), "block")
    (call,) = wire.calls
    assert call["json_schema"] is parser_mod.PARSE_OUTPUT_SPARSE_JSON_SCHEMA
    assert call["json_schema_strict"] is False


def test_the_sparse_schema_declares_the_same_keys_and_requires_only_the_kept_ones():
    strict, sparse = parser_mod.PARSE_OUTPUT_JSON_SCHEMA, parser_mod.PARSE_OUTPUT_SPARSE_JSON_SCHEMA

    def without_required(node):
        if isinstance(node, dict):
            return {k: without_required(v) for k, v in node.items() if k != "required"}
        return node

    assert without_required(strict) == without_required(sparse)
    assert sparse["required"] == ["message_type", "entity_op", "domain_in_message"]
    assert sparse["properties"]["entities"]["items"]["required"] == ["raw", "hint"]
    assert sparse["properties"]["escalation"]["required"] == []
    # The strict schema is untouched by building the sparse one.
    assert set(strict["required"]) == parser_mod.DECLARED_KEYS
    assert "account" in strict["properties"]["entities"]["items"]["required"]


@pytest.mark.parametrize(
    ("emission", "fragment"),
    [
        ({"group_by": "region"}, "group_by"),
        ({"entities": [{"raw": "x", "hint": "product", "quantity": "five"}]}, "entities[0].quantity"),
        ({"low_stock": {"group_by": "planet"}}, "low_stock.group_by"),
        ({"open_question_answer": {"mode": "maybe"}}, "open_question_answer.mode"),
        ({"document": ["INVOICE"]}, "document[0]"),
        ({"top_n": "ten"}, "top_n"),
        ({"self_reference": None}, "self_reference"),
        ({"profile_statements": [{"key": "role", "value": "a"}] * 4}, "profile_statements"),
        ({"entities": [{"raw": "x", "hint": "customer", "account": 2.0}]}, "entities[0].account"),
    ],
)
def test_a_sparse_answer_off_the_schema_fails_the_parse(wire, emission, fragment):
    """What strict mode refused at the provider is refused here (R5): a failed
    `understood`, never a soft default."""
    wire.content = json.dumps({"message_type": "casual", "entity_op": "reuse", "domain_in_message": False, **emission})
    with pytest.raises(parser_mod.ParserError, match="off the schema") as exc:
        parser_mod.parse(config(True), "block")
    assert fragment in str(exc.value)
    assert exc.value.usage["completion_tokens"] == 50


@pytest.mark.parametrize(
    ("emission", "missing"),
    [
        ({"message_type": "casual", "domain_in_message": False}, "entity_op is missing"),
        ({"message_type": "casual", "entity_op": "reuse"}, "domain_in_message is missing"),
        (
            {"message_type": "casual", "entity_op": "reuse", "domain_in_message": False,
             "entities": [{"hint": "product"}]},
            "entities[0].raw is missing",
        ),
        (
            {"message_type": "casual", "entity_op": "reuse", "domain_in_message": False,
             "entities": [{"raw": "SRTWC286", "spec_key": "is_rimless"}]},
            "entities[0].hint is missing",
        ),
    ],
)
def test_a_sparse_answer_missing_a_kept_key_fails_the_parse(wire, emission, missing):
    """A non-strict provider does not enforce `required`; hydrating a dropped entity_op to
    null would route a "reuse" turn as something else (`turn/decide.py:393`)."""
    wire.content = json.dumps(emission)
    with pytest.raises(parser_mod.ParserError, match="off the schema") as exc:
        parser_mod.parse(config(True), "block")
    assert missing in str(exc.value)


def test_an_undeclared_key_inside_an_object_is_dropped(wire):
    """Strict mode refused it (`additionalProperties: false`); `routing.team_source` must
    never reach a reader."""
    wire.content = json.dumps({
        "message_type": "request_for_help", "entity_op": "reuse", "domain_in_message": False,
        "routing": {"suggested_team": "warehouse", "team_source": "guess"},
        "entities": [{"raw": "a", "hint": "product", "colour": "red"}],
    })
    parsed = parser_mod.parse(config(True), "block")
    assert parsed["routing"] == {"suggested_team": "warehouse", "suggested_agent": None}
    assert "colour" not in parsed["entities"][0]


def test_a_sparse_answer_with_no_message_type_fails_the_parse(wire):
    wire.content = json.dumps({"entity_op": "reuse", "intent_hint": "check_stock"})
    with pytest.raises(parser_mod.ParserError, match="message_type"):
        parser_mod.parse(config(True), "block")


def test_an_unknown_key_is_still_ignored(wire):
    wire.content = json.dumps({"message_type": "casual", "entity_op": "reuse", "domain_in_message": False, "mood": 3})
    assert parser_mod.parse(config(True), "block")["mood"] == 3


def test_hydrating_a_full_emission_changes_nothing():
    strict = {**strict_base(), **FAMILIES["entities spec_key and quantity"]}
    assert parser_mod.hydrate_emission(copy.deepcopy(strict)) == strict


def test_the_config_reads_sparse_off_the_prompt_text(monkeypatch):
    from app.services import ai_assistant_service, llm_provider as provider_mod

    class Cfg:
        provider = "openai"
        model = "m"

    monkeypatch.setattr(parser_mod, "agent_model", lambda db, key: (None, None))
    monkeypatch.setattr(ai_assistant_service.AIAssistantConfigService, "get", lambda self: Cfg())
    monkeypatch.setattr(provider_mod, "resolve_api_key", lambda cfg, provider: "k")
    for text, expected in (("plain prompt", False), ("x" + SPARSE_OUTPUT_ADDENDUM, True)):
        monkeypatch.setattr(parser_mod, "render", lambda db, key, **kw: (text, 9))
        assert parser_mod.resolve_config(None, current_date="today").sparse is expected


# --------------------------------------------------------------------------- #
# The prompt: built from any parser line, refused when it cannot be
# --------------------------------------------------------------------------- #

LINES = {
    "code constant": SEMANTIC_PARSER_PROMPT,
    "v52 candidate": (DATA / "chatbot_semantic_parser.candidate-20261010.txt").read_text(encoding="utf-8"),
    "v56": (DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt").read_text(encoding="utf-8"),
    "v42 prod snapshot": (DATA / "chatbot_semantic_parser.prod-20261001.txt").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("line", list(LINES))
def test_sparse_prompt_changes_only_the_output_wording(line):
    original = LINES[line]
    text = sparse_prompt(original)

    assert text.count(SPARSE_OUTPUT_MARKER) == 1
    assert text.endswith(SPARSE_OUTPUT_ADDENDUM)
    assert STRICT_OUTPUT_BLOCK_START not in text
    # Undo the three edits and the text is the original, byte for byte.
    undone = text.removesuffix(SPARSE_OUTPUT_ADDENDUM)
    for old, new in SPARSE_PHRASE_EDITS:
        undone = undone.replace(new, old)
    start = original.index(STRICT_OUTPUT_BLOCK_START)
    end = original.index(STRICT_OUTPUT_BLOCK_END, start) + len(STRICT_OUTPUT_BLOCK_END)
    assert undone.replace(SPARSE_OUTPUT_BLOCK, original[start:end]) == original
    assert "{{current_date}}" in text


def test_sparse_prompt_refuses_a_sparse_text_and_a_text_without_the_block():
    with pytest.raises(ValueError, match="already"):
        sparse_prompt(sparse_prompt(SEMANTIC_PARSER_PROMPT))
    with pytest.raises(ValueError, match="OUTPUT block"):
        sparse_prompt("a prompt with no output block")


def test_no_reader_tells_a_plain_flags_false_from_its_null():
    """A sparse emission leaves a false plain flag out and it hydrates to null. That is
    only the same reading while no reader compares one of them to false or to None."""
    import re

    root = Path(parser_mod.__file__).resolve().parents[1]
    flags = "|".join(sorted(FALSE_IS_NULL))
    pattern = re.compile(
        r"[\"'](" + flags + r")[\"'][\)\]]?\s*(is False|is not False|== False|!= False"
        r"|is None|is not None|== None|!= None)"
        # `_js_loose_eq_bool(jsc.get(v, "flag"), False)`, the idiom `lanes/business/answer.py` uses.
        r"|_js_loose_eq_bool\([^\n]*[\"'](" + flags + r")[\"'][^\n]*,\s*(False|None)\s*\)"
    )
    hits = [
        f"{path.relative_to(root)}:{n}: {line.strip()}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


def test_the_prompt_names_every_key_the_code_keeps():
    """The hydration side is pinned above; this pins the instruction that makes the model
    write those keys at all (dropping the `is_active` line would bring back "expired
    promotions" answered with active ones, every other test still green)."""
    kept = FALSE_IS_A_VALUE | {key for keys in parser_mod.SPARSE_REQUIRED.values() for key in keys}
    missing = sorted(key for key in kept if key not in SPARSE_OUTPUT_ADDENDUM)
    assert missing == []
