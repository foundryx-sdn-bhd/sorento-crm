"""CHAT-SPARSE: the GROUPED wire shape reads exactly as the flat strict one it stands for.

A prompt version carrying `GROUPED_OUTPUT_MARKER` is held to `PARSE_OUTPUT_GROUPED_JSON_SCHEMA`:
still STRICT, with the rarely used key families nested in nullable objects
(`documentation/plans/chatbot/PLAN-chat-sparse-emission-12oct.md`). `head/parser.parse`
flattens the answer, so every reader downstream sees the flat dict. Each key family the
engine reads is pinned here: the grouped wire form of a flat emission must come out of
`parse` equal to that emission.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from app.services import llm_provider
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot_parser_prompt import (
    ENTITY_LINE_PREFIX,
    ENTITY_OUTPUT_GROUPS,
    GROUPED_OUTPUT_ADDENDUM,
    GROUPED_OUTPUT_MARKER,
    OUTPUT_GROUPS,
    PLAIN_FLAGS,
    SEMANTIC_PARSER_PROMPT,
    STRICT_OUTPUT_BLOCK_END,
    STRICT_OUTPUT_BLOCK_START,
    grouped_prompt,
)

DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"
FLAT = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
GROUPED = parser_mod.PARSE_OUTPUT_GROUPED_JSON_SCHEMA

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
    """A flat strict emission for a message that says nothing (the shape of the 20 strict
    live emissions of 12 Oct 2026)."""
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


def as_read(value):
    """A flat emission as the grouped wire can say it: a plain flag is true or null."""
    if isinstance(value, dict):
        return {k: None if k in PLAIN_FLAGS and v is False else as_read(v) for k, v in value.items()}
    if isinstance(value, list):
        return [as_read(v) for v in value]
    return value


def _empty(value) -> bool:
    if isinstance(value, dict):
        return all(_empty(v) for v in value.values())
    return value is None or value == []


def _fold(flat: dict, groups: dict) -> dict:
    group_of = {key: group for group, keys in groups.items() for key in keys}
    out: dict = {}
    for key, value in flat.items():
        group = group_of.get(key)
        if group is None:
            out[key] = value
        elif group not in out:
            members = {m: flat[m] for m in groups[group]}
            out[group] = None if _empty(members) else members
    return out


def to_wire(flat: dict) -> dict:
    """`flat` as the grouped schema writes it: each family folded into its group (null when
    every member is empty), each nullable object null when empty."""
    flat = as_read(copy.deepcopy(flat))
    wire = _fold(flat, OUTPUT_GROUPS)
    for key in parser_mod.NULLABLE_OBJECTS:
        if _empty(wire[key]):
            wire[key] = None
    wire["entities"] = [_fold(e, ENTITY_OUTPUT_GROUPS) for e in wire["entities"]]
    return wire


@pytest.fixture
def wire(monkeypatch):
    class Wire:
        content = "{}"
        calls: list[dict] = []

    def fake_chat(provider, key, model, messages, **kwargs):
        Wire.calls.append(kwargs)
        return llm_provider.ChatResult(content=Wire.content, prompt_tokens=10, completion_tokens=50, total_tokens=60)

    Wire.calls = []
    monkeypatch.setattr(llm_call, "chat", fake_chat)
    return Wire


def config(grouped: bool) -> parser_mod.ParserConfig:
    return parser_mod.ParserConfig(
        system_prompt="S", prompt_version=7, provider="openai", model="m", api_key="k", grouped=grouped
    )


# --------------------------------------------------------------------------- #
# Each key family: grouped in, the flat emission out
# --------------------------------------------------------------------------- #

FAMILIES = {
    "nothing said": {},
    "escalation": {
        "message_type": "request_for_help",
        "is_affirmative": True,
        "routing": {"suggested_team": "marketing", "suggested_agent": None},
        "escalation": {"is_escalation_confirmation": True, "company_pick": "Sorento Trading"},
    },
    "escalation declined": {"message_type": "casual", "is_affirmative": False},
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
            "group_by": "supplier", "categories": ["water closet"], "all_categories": None,
            "brands": [], "suppliers": ["ACME"],
        },
    },
    "low_stock all categories": {
        "low_stock": {"group_by": None, "categories": [], "all_categories": True, "brands": [], "suppliers": []},
    },
    "sales and ranking keys": {
        "domain_hint": "order",
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
        "sort_by": "date",
        "sort_dir": "desc",
        "group_by": "month",
        "top_n": 10,
    },
    "one ranking key": {"rank_direction": "top"},
    "dates": {"date_mode": "overlap", "date_filter_start": "2026-03-01", "date_filter_end": "2026-03-31"},
    "conversation": {
        "scope_intent": "broaden",
        "broaden_axis": "customer",
        "broaden_to": "all",
        "correction": True,
        "topic_reset": True,
        "proceed_anyway": True,
        "continuation": True,
    },
    "conversation flags only false": {"continuation": False, "correction": False, "topic_reset": False},
    "filters": {
        "access_levels": ["dealer"],
        "demand_qty": 3,
        "contains_flyer": True,
        "person_mention": "Ah Seng",
        "is_active": True,
        "promotion_kind": "CLEARANCE",
    },
    "inactive records": {"is_active": False},
    "entities detail": {
        "entity_op": "replace_combine",
        "domain_in_message": True,
        "entities": [
            {**EMPTY_ENTITY, "raw": "M210-GM", "hint": "product", "current_message": True, "confident": True,
             "hint_confident": True, "quantity": 5},
            {**EMPTY_ENTITY, "raw": "non rimless", "hint": "specification", "current_message": True,
             "confident": False, "hint_confident": False, "spec_key": "is_rimless", "spec_value": False},
            {**EMPTY_ENTITY, "raw": "Soon Heng", "hint": "customer", "current_message": False, "confident": True,
             "account": 1, "canonical_code": "C-001"},
            {**EMPTY_ENTITY, "raw": "SRTWC286", "hint": "product", "current_message": True, "confident": True},
        ],
    },
    "lists and the rest": {
        "document": ["DO"],
        "asks": [{"domain": "inventory", "intent": "check_stock"}],
        "po_refs": ["4500123"],
        "profile_statements": [{"key": "language", "value": "Malay"}],
        "chat_report": {"group_by": "topic", "measure": "asks", "subject": None},
        "anaphora": {"backward_reference": True},
        "self_reference": True,
        "requested_attributes": ["quantity"],
        "match_mode": "or",
    },
}


@pytest.mark.parametrize("family", list(FAMILIES))
def test_a_grouped_emission_parses_to_the_flat_one(wire, family):
    flat = {**strict_base(), **FAMILIES[family]}
    sent = to_wire(flat)
    wire.content = json.dumps(sent)

    parsed = dict(parser_mod.parse(config(True), "block"))

    assert parsed == as_read(flat)
    assert not set(OUTPUT_GROUPS) & set(parsed)


def test_nothing_said_writes_every_group_and_object_as_null(wire):
    sent = to_wire(strict_base())
    for key in [*OUTPUT_GROUPS, *parser_mod.NULLABLE_OBJECTS]:
        assert sent[key] is None, key
    assert len(json.dumps(sent)) < len(json.dumps(strict_base())) * 0.6


def test_a_null_object_reads_as_its_object_of_empties(wire):
    wire.content = json.dumps(to_wire(strict_base()))
    parsed = parser_mod.parse(config(True), "block")
    assert parsed["escalation"] == {"is_escalation_confirmation": None, "company_pick": None}
    assert parsed["open_question_answer"] == {"mode": None, "picked": [], "items": [], "qty_for_all": None}
    assert parsed["low_stock"] == {"group_by": None, "categories": [], "all_categories": None, "brands": [], "suppliers": []}
    assert parsed["anaphora"] == {"backward_reference": None}
    assert parsed["access_levels"] == []
    assert parsed["is_active"] is None


def test_a_missing_group_is_still_a_failed_parse(wire):
    sent = to_wire(strict_base())
    del sent["conversation"]
    wire.content = json.dumps(sent)
    with pytest.raises(parser_mod.ParserError, match="continuation"):
        parser_mod.parse(config(True), "block")


# --------------------------------------------------------------------------- #
# The wire: strict both ways
# --------------------------------------------------------------------------- #


def test_a_flat_version_is_called_exactly_as_before(wire):
    wire.content = json.dumps(strict_base())
    parser_mod.parse(config(False), "block")
    (call,) = wire.calls
    assert call["json_schema"] is FLAT
    assert set(call) == {"temperature", "max_tokens", "json_schema", "json_schema_name", "prompt_cache_key"}


def test_a_grouped_version_is_called_strict_with_the_grouped_schema(wire):
    wire.content = json.dumps(to_wire(strict_base()))
    parser_mod.parse(config(True), "block")
    (call,) = wire.calls
    assert call["json_schema"] is GROUPED
    assert "json_schema_strict" not in call


def _strict_valid(node, path="$"):
    """OpenAI strict mode: every object lists all its properties as required and refuses
    additional ones."""
    if isinstance(node, dict):
        if "properties" in node:
            assert node.get("additionalProperties") is False, path
            assert sorted(node["required"]) == sorted(node["properties"]), path
        for key, value in node.items():
            _strict_valid(value, f"{path}.{key}")
    elif isinstance(node, list):
        for item in node:
            _strict_valid(item, path)


def test_the_grouped_schema_is_strict_valid_and_declares_the_same_leaves():
    _strict_valid(GROUPED)
    flat_props = FLAT["properties"]
    grouped_props = GROUPED["properties"]
    for group, keys in OUTPUT_GROUPS.items():
        assert grouped_props[group]["type"] == ["object", "null"]
        for key in keys:
            assert key not in grouped_props
            member = grouped_props[group]["properties"][key]
            assert member == ({"type": ["boolean", "null"], "enum": [True, None]} if key in PLAIN_FLAGS else flat_props[key])
    for key in parser_mod.NULLABLE_OBJECTS:
        assert grouped_props[key]["type"] == ["object", "null"]
    item = grouped_props["entities"]["items"]["properties"]
    assert item["detail"]["type"] == ["object", "null"]
    assert sorted(item["detail"]["properties"]) == sorted(ENTITY_OUTPUT_GROUPS["detail"])
    # Building the grouped schema left the flat one untouched.
    assert set(FLAT["required"]) == parser_mod.DECLARED_KEYS
    assert "quantity" in FLAT["properties"]["entities"]["items"]["required"]


def test_every_group_member_is_a_declared_key_in_one_group_only():
    members = [key for keys in OUTPUT_GROUPS.values() for key in keys]
    assert len(members) == len(set(members))
    assert set(members) <= parser_mod.DECLARED_KEYS
    assert not set(OUTPUT_GROUPS) & parser_mod.DECLARED_KEYS
    entity_keys = set(FLAT["properties"]["entities"]["items"]["properties"])
    assert {k for keys in ENTITY_OUTPUT_GROUPS.values() for k in keys} <= entity_keys
    assert PLAIN_FLAGS.isdisjoint({"is_affirmative", "is_active", "domain_in_message", "spec_value"})


def test_the_config_reads_grouped_off_the_prompt_text(monkeypatch):
    from app.services import ai_assistant_service, llm_provider as provider_mod

    class Cfg:
        provider = "openai"
        model = "m"

    monkeypatch.setattr(parser_mod, "agent_model", lambda db, key: (None, None))
    monkeypatch.setattr(ai_assistant_service.AIAssistantConfigService, "get", lambda self: Cfg())
    monkeypatch.setattr(provider_mod, "resolve_api_key", lambda cfg, provider: "k")
    for text, expected in (("plain prompt", False), (grouped_prompt(SEMANTIC_PARSER_PROMPT), True)):
        monkeypatch.setattr(parser_mod, "render", lambda db, key, **kw: (text, 9))
        assert parser_mod.resolve_config(None, current_date="today").grouped is expected


def test_no_reader_tells_a_plain_flags_false_from_its_null():
    """Inside a group a plain flag is true or null, never false, and an unused group reads
    as null. That is the same reading only while no reader compares one to false or None."""
    root = Path(parser_mod.__file__).resolve().parents[1]
    flags = "|".join(sorted(PLAIN_FLAGS))
    pattern = re.compile(
        r"[\"'](" + flags + r")[\"'][\)\]]?\s*(is False|is not False|== False|!= False"
        r"|is None|is not None|== None|!= None)"
        r"|_js_loose_eq_bool\([^\n]*[\"'](" + flags + r")[\"'][^\n]*,\s*(False|None)\s*\)"
    )
    hits = [
        f"{path.relative_to(root)}:{n}: {line.strip()}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #

LINES = {
    "code constant": SEMANTIC_PARSER_PROMPT,
    "v52 candidate": (DATA / "chatbot_semantic_parser.candidate-20261010.txt").read_text(encoding="utf-8"),
    "v56": (DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt").read_text(encoding="utf-8"),
    "v42 prod snapshot": (DATA / "chatbot_semantic_parser.prod-20261001.txt").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("line", list(LINES))
def test_grouped_prompt_changes_only_the_output_block_and_the_entity_line(line):
    original = LINES[line]
    text = grouped_prompt(original)

    assert text.count(GROUPED_OUTPUT_MARKER) == 1
    assert text.endswith(GROUPED_OUTPUT_ADDENDUM)
    start = original.index(STRICT_OUTPUT_BLOCK_START)
    end = original.index(STRICT_OUTPUT_BLOCK_END, start) + len(STRICT_OUTPUT_BLOCK_END)
    new_start = text.index(STRICT_OUTPUT_BLOCK_START)
    new_end = text.index(STRICT_OUTPUT_BLOCK_END, new_start) + len(STRICT_OUTPUT_BLOCK_END)
    block = text[new_start:new_end]
    for group, keys in OUTPUT_GROUPS.items():
        assert f'  "{group}": null, or {{' in block
        for key in keys:
            assert f'"{key}":' in block
            # Folded into the group line, never left on a line of its own.
            assert not re.search(rf'^\s*"{key}"\s*:', block, re.MULTILINE), key
    assert '  "escalation": null, or {' in block
    assert '  "anaphora": null, or {' in block
    assert '"topic_reset": true|null, true only when the message drops the current subject' in block
    # Outside the block, only the entity format line changed.
    old_rest = (original[:start] + original[end:]).split("\n")
    new_rest = (text[:new_start] + text[new_end:].removesuffix(GROUPED_OUTPUT_ADDENDUM)).split("\n")
    assert len(old_rest) == len(new_rest)
    changed = [(a, b) for a, b in zip(old_rest, new_rest) if a != b]
    assert len(changed) == 1 and changed[0][0].startswith(ENTITY_LINE_PREFIX)
    assert '"detail": null, or { "quantity", "spec_key", "spec_value", "account" }' in changed[0][1]
    assert "{{current_date}}" in text


def test_grouped_prompt_refuses_a_grouped_text_and_a_text_without_the_block():
    with pytest.raises(ValueError, match="already"):
        grouped_prompt(grouped_prompt(SEMANTIC_PARSER_PROMPT))
    with pytest.raises(ValueError, match="OUTPUT block"):
        grouped_prompt("a prompt with no output block")


def test_the_prompt_names_every_group_and_member():
    for group, keys in {**OUTPUT_GROUPS, **ENTITY_OUTPUT_GROUPS}.items():
        assert f'"{group}"' in GROUPED_OUTPUT_ADDENDUM
        for key in keys:
            assert key in GROUPED_OUTPUT_ADDENDUM
    for key in parser_mod.NULLABLE_OBJECTS:
        assert key in GROUPED_OUTPUT_ADDENDUM


CANDIDATE = LINES["v52 candidate"]


@pytest.mark.parametrize(
    ("variant", "reason"),
    [
        (CANDIDATE.replace("\n}\n" + STRICT_OUTPUT_BLOCK_END, "\n}\n\n" + STRICT_OUTPUT_BLOCK_END, 1), "close"),
        (CANDIDATE.replace('\n  "is_active":', '\n   "is_active":', 1), "is_active"),
        (
            CANDIDATE.replace(
                '  "escalation": { "is_escalation_confirmation": false,',
                '  "escalation": {\n    "is_escalation_confirmation": false,',
                1,
            ),
            "escalation",
        ),
        (CANDIDATE.replace('"confident": true|false }', '"confident": true|false, "hint_confident": true|false }', 1), "entity"),
    ],
)
def test_grouped_prompt_refuses_a_text_it_cannot_fold_cleanly(variant, reason):
    """A prompt line we have not seen (prod v52, the eval's v63) must be refused, never
    published half-folded: the migration and the script skip a refused text."""
    assert variant != CANDIDATE
    with pytest.raises(ValueError, match=reason):
        grouped_prompt(variant)


def test_gemini_drops_the_non_string_enum_of_a_grouped_flag():
    from app.services.llm_provider import _gemini_schema

    flag = GROUPED["properties"]["conversation"]["properties"]["continuation"]
    assert _gemini_schema(flag) == {"type": "boolean", "nullable": True}
    assert _gemini_schema({"type": ["string", "null"], "enum": ["a", None]}) == {
        "type": "string", "nullable": True, "enum": ["a"],
    }
