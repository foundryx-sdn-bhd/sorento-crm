"""CHAT-STATE S5 (red): the state block in the parser input, the schema gate, the lane prompt.

Traces to UAC B1-B3 and plan test list S5 items 1-5 plus the lane prompt build/publish
(PROMPT-DELTA-chat-state.md). Contract used: `ContextLayers.state_block: str | None`,
`ParserConfig.state_block: bool`, `parser.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE`,
`head/chat_state_prompt.build_lane_prompt(base) -> str`,
`scripts/publish_chat_state_prompt.publish(session, *, from_version) -> int`.
Each test imports the not-yet-existing name inside its body so it fails on its own.
"""
from __future__ import annotations

import json
import types
import uuid
from typing import Any

import pytest

from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn.context import ContextLayers, assemble
from app.services.chatbot.turn.state import Focus, State

LEVELS = ("off", "conversation", "full")
LEGACY_PREFIXES = ("Current subject", "Open task", "Open question", "Pending", "Open question options")
PREVIOUS = "Which one?\n1. SRT1\n2. SRT2"


def _layers(level: str, **extra: Any) -> ContextLayers:
    return ContextLayers(
        level=level,
        profile_facts=[{"key": "role", "value": "dealer"}],
        summaries=["Asked about SRT1 stock"],
        earlier_messages=[{"created_at": "2026-10-15", "text": "hi"}],
        previous_response=PREVIOUS,
        current_subject="products SRT1; customer Customer X",
        pending_kind="product_pick",
        pending_options=["1. SRT1", "2. SRT2"],
        settings_profile_line="Profile: dealer",
        current_message="2",
        reply_to=None,
        media_line=None,
        task_lines=["Open task: stock quantity for SRT1"],
        open_question_line="Open question: product_pick",
        recent_exchanges=[("stock srt", PREVIOUS)],
        brands=[{"brand_name": "Sorento", "brand_code": "SRT"}],
        **extra,
    )


#: `assemble()` on main for `_layers(level)`, captured at c03ecce68.
GOLDEN = {
    "off": "Previous response: Which one?\n1. SRT1\n2. SRT2\nCurrent user message: 2\nCurrent subject: products SRT1; customer Customer X\nOpen task: stock quantity for SRT1\nOpen question: product_pick\nPending: the assistant is waiting for a product_pick reply.\nOpen question options: 1. SRT1; 2. SRT2\nProfile: dealer\nKnown brands: Sorento (SRT)\nRecent exchanges, oldest first:\nUser: stock srt\nAssistant: (the Previous response)",
    "conversation": "Earlier in this conversation (oldest first):\n- 2026-10-15 you: hi\nRecent exchanges, oldest first:\nUser: stock srt\nAssistant: (the Previous response)\nPrevious response: Which one?\n1. SRT1\n2. SRT2\nKnown brands: Sorento (SRT)\nCurrent subject: products SRT1; customer Customer X\nOpen task: stock quantity for SRT1\nOpen question: product_pick\nPending: the assistant is waiting for a product_pick reply.\nOpen question options: 1. SRT1; 2. SRT2\nCurrent user message: 2",
    "full": "About this contact:\nrole dealer\nRecent conversations:\n- Asked about SRT1 stock\nEarlier in this conversation (oldest first):\n- 2026-10-15 you: hi\nRecent exchanges, oldest first:\nUser: stock srt\nAssistant: (the Previous response)\nPrevious response: Which one?\n1. SRT1\n2. SRT2\nKnown brands: Sorento (SRT)\nCurrent subject: products SRT1; customer Customer X\nOpen task: stock quantity for SRT1\nOpen question: product_pick\nPending: the assistant is waiting for a product_pick reply.\nOpen question options: 1. SRT1; 2. SRT2\nCurrent user message: 2",
}


def _block(products: int = 3, rows: int = 0) -> str:
    from app.services.chatbot.turn import chat_state

    focus = Focus(
        products=[
            {"raw": f"SRT{i}", "canonical_code": f"SRT{i}", "hint": "product", "current_message": False}
            for i in range(1, products + 1)
        ],
        customers=[{"raw": "Customer X", "canonical_code": "C0001", "hint": "customer", "current_message": False}],
        domains=["inventory"],
        last_list=[{"position": i, "label": f"SRTWC286-LOC{i}", "code": f"SRTWC286-LOC{i}"} for i in range(1, rows + 1)]
        or None,
    )
    text = chat_state.render(chat_state.view(State(focus=focus, turn_no=3), None))
    assert text
    return text


# --------------------------------------------------------------------------- #
# 1-3: context.assemble
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", LEVELS)
def test_flag_off_assemble_is_byte_identical_to_main(level):
    text, report = assemble(_layers(level, state_block=None))
    assert text == GOLDEN[level]
    assert not any(layer["dropped"] for layer in report["layers"])


@pytest.mark.parametrize("level", LEVELS)
def test_flag_on_prints_the_block_in_full_and_none_of_the_five_legacy_lines(level):
    block = _block()
    text, _ = assemble(_layers(level, state_block=block))
    assert block in text
    for line in text.splitlines():
        assert not line.startswith(LEGACY_PREFIXES), line
    assert "Known brands: Sorento (SRT)" in text
    assert "Current user message: 2" in text


@pytest.mark.parametrize("level", LEVELS)
def test_a_block_over_350_estimated_tokens_is_never_trimmed(level):
    from app.services.chatbot.turn.context import CAPS, est_tokens

    block = _block(products=50, rows=33)
    assert est_tokens(block) > CAPS["L2"]
    text, report = assemble(_layers(level, state_block=block))
    assert block in text
    assert "more)" not in text
    l2 = next(layer for layer in report["layers"] if layer["layer"] == "L2")
    assert l2["dropped"] is False


# --------------------------------------------------------------------------- #
# 4: resolve_config sets the flag from the prompt text
# --------------------------------------------------------------------------- #


HEADING_LINE = "## Conversation state and state_changes"


@pytest.fixture(autouse=True)
def _fresh_prompt_cache():
    """The registry caches a prompt per (name, label) for a minute; a text left by an earlier
    test would answer these."""
    from app.services import ai_prompt_registry

    ai_prompt_registry.bust_cache()
    yield
    ai_prompt_registry.bust_cache()


def _resolve(monkeypatch, session_factory, raw_template: str, *, current_date: str = "2026-10-16"):
    """The real registry: a seeded production version whose RAW template is `raw_template`."""
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services import ai_assistant_service, llm_provider

    db = session_factory()
    version = AIPromptVersion(
        id=str(uuid.uuid4()), name=parser_mod.PROMPT_KEY, version=1, type="text",
        template=raw_template, variables=["current_date"],
    )
    db.add(version)
    db.flush()
    db.add(AIPromptLabel(id=str(uuid.uuid4()), name=parser_mod.PROMPT_KEY, label="production", version_id=version.id))
    db.flush()
    cfg = types.SimpleNamespace(provider="openai", model="gpt-test")
    monkeypatch.setattr(
        ai_assistant_service, "AIAssistantConfigService", lambda d: types.SimpleNamespace(get=lambda: cfg)
    )
    monkeypatch.setattr(llm_provider, "resolve_api_key", lambda config, provider: "sk-test")
    return parser_mod.resolve_config(db, current_date=current_date)


def test_resolve_config_opts_in_when_the_raw_template_carries_the_section_heading(monkeypatch, session_factory):
    config = _resolve(monkeypatch, session_factory, f"Rules {{{{current_date}}}}\n{HEADING_LINE}\nFill state_changes.")
    assert config.state_block is True


def test_resolve_config_stays_off_for_a_template_without_the_heading(monkeypatch, session_factory):
    config = _resolve(monkeypatch, session_factory, "Rules {{current_date}}. Current subject and Open question lines.")
    assert config.state_block is False


def test_a_rendered_value_that_says_state_changes_does_not_turn_the_path_on(monkeypatch, session_factory):
    """The gate reads the RAW template, never the text after {{variable}} rendering."""
    config = _resolve(
        monkeypatch, session_factory, "Rules {{current_date}}.", current_date="2026-10-16 state_changes " + HEADING_LINE
    )
    assert "state_changes" in config.system_prompt
    assert config.state_block is False


def test_the_bare_word_in_a_raw_template_without_the_heading_stays_off(monkeypatch, session_factory):
    config = _resolve(monkeypatch, session_factory, "Rules {{current_date}}. Never mention state_changes.")
    assert config.state_block is False


# --------------------------------------------------------------------------- #
# 5: parse() sends the schema the flag selects
# --------------------------------------------------------------------------- #


def _parse_with(monkeypatch, *, state_block: bool) -> dict[str, Any]:
    from app.services.chatbot import llm_call

    sent: dict[str, Any] = {}

    def fake_chat(provider, api_key, model, messages, **kwargs):
        sent.update(kwargs)
        content = {key: None for key in parser_mod.DECLARED_KEYS}
        return types.SimpleNamespace(
            content=json.dumps(content), prompt_tokens=1, completion_tokens=1, total_tokens=2, cached_tokens=0
        )

    monkeypatch.setattr(llm_call, "chat", fake_chat)
    config = parser_mod.ParserConfig(
        system_prompt="p", prompt_version=1, provider="openai", model="gpt-test", api_key="k",
        state_block=state_block,
    )
    parser_mod.parse(config, "Current user message: hi")
    return sent


def test_parse_sends_the_base_schema_object_when_the_flag_is_off(monkeypatch):
    sent = _parse_with(monkeypatch, state_block=False)
    assert sent["json_schema"] is parser_mod.PARSE_OUTPUT_JSON_SCHEMA


def test_parse_sends_the_state_schema_object_when_the_flag_is_on(monkeypatch):
    sent = _parse_with(monkeypatch, state_block=True)
    assert sent["json_schema"] is parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE


def test_the_state_schema_adds_state_changes_last_required_and_strict():
    schema = parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE
    base = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
    assert list(schema["properties"])[-1] == "state_changes"
    assert schema["required"][-1] == "state_changes"
    assert schema["additionalProperties"] is False
    assert {k: v for k, v in schema["properties"].items() if k != "state_changes"} == base["properties"]
    assert schema["required"][:-1] == base["required"]
    change = schema["properties"]["state_changes"]
    assert sorted(change["type"]) == ["array", "null"]
    item = change["items"]
    assert item["type"] == "object" and item["additionalProperties"] is False
    assert set(item["required"]) == {"op", "axis", "position"}
    assert set(item["properties"]["op"]["enum"]) == {
        "keep", "new_subject", "set", "clear", "pick", "answer", "switch_domain", "close_question",
    }
    assert sorted(item["properties"]["axis"]["type"]) == ["null", "string"]
    assert sorted(item["properties"]["position"]["type"]) == ["integer", "null"]


def test_the_base_schema_and_declared_keys_do_not_know_state_changes():
    assert "state_changes" not in parser_mod.PARSE_OUTPUT_JSON_SCHEMA["properties"]
    assert "state_changes" not in parser_mod.PARSE_OUTPUT_JSON_SCHEMA["required"]
    assert "state_changes" not in parser_mod.DECLARED_KEYS


# --------------------------------------------------------------------------- #
# 6: the lane prompt builder
# --------------------------------------------------------------------------- #

BASE = "You read a customer message.\nRules:\n- Current subject and Open question lines tell you the context.\n"
HEADING = "## Conversation state and state_changes"


def _build(text: str) -> str:
    from app.services.chatbot.head.chat_state_prompt import build_lane_prompt

    return build_lane_prompt(text)


def test_the_lane_prompt_is_the_base_unchanged_plus_the_state_section():
    out = _build(BASE)
    assert out.startswith(BASE)
    assert out.count(HEADING) == 1
    assert "state_changes" in out[len(BASE):]


def test_the_lane_prompt_builder_is_idempotent():
    once = _build(BASE)
    assert _build(once) == once
    assert once.count(HEADING) == 1


# --------------------------------------------------------------------------- #
# 7: the publish script
# --------------------------------------------------------------------------- #

NAME = "chatbot_semantic_parser"


def _seed(session, *, production_template: str = BASE):
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    old = AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=1, type="text", template=production_template, variables=[])
    session.add(old)
    session.flush()
    session.add(AIPromptLabel(id=str(uuid.uuid4()), name=NAME, label="production", version_id=old.id))
    session.flush()
    return old


def _versions(session) -> list[Any]:
    from app.models.ai_prompt import AIPromptVersion

    return session.query(AIPromptVersion).filter(AIPromptVersion.name == NAME).order_by(AIPromptVersion.version).all()


def _publish(session, **kwargs):
    from scripts.publish_chat_state_prompt import publish

    return publish(session, **kwargs)


def test_publish_adds_one_unlabelled_version_built_from_the_production_template(session_factory):
    from app.models.ai_prompt import AIPromptLabel

    session = session_factory()
    old = _seed(session)
    version = _publish(session, from_version=None)
    rows = _versions(session)
    assert [r.version for r in rows] == [1, version] and version == 2
    assert rows[-1].template == _build(BASE)
    labels = session.query(AIPromptLabel).filter(AIPromptLabel.name == NAME).all()
    assert [(l.label, l.version_id) for l in labels] == [("production", old.id)]


def test_publish_from_a_named_version_uses_that_template(session_factory):
    from app.models.ai_prompt import AIPromptVersion

    session = session_factory()
    _seed(session)
    other = "Another base prompt.\n"
    session.add(AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=2, type="text", template=other, variables=[]))
    session.flush()
    version = _publish(session, from_version=2)
    assert _versions(session)[-1].version == version == 3
    assert _versions(session)[-1].template == _build(other)


def test_publishing_twice_does_not_create_a_second_identical_version(session_factory):
    session = session_factory()
    _seed(session)
    first = _publish(session, from_version=None)
    count = len(_versions(session))
    second = _publish(session, from_version=None)
    assert len(_versions(session)) == count == 2
    assert second in (first, None)


def test_r6_sf3_the_prompt_says_the_held_period_carries_on_a_new_subject():
    import re

    from app.services.chatbot.head.chat_state_prompt import STATE_SECTION

    text = " ".join(STATE_SECTION.split())
    assert "channel or period carries" not in text, "a line still says the period does not carry"
    assert re.search(r"new_subject[^|]*\bperiod\b[^|]*(carr|kept|keeps|stays)", text), text
    assert re.search(r"(skip|clear) period", text)


def test_r6_nit_resolve_config_reads_the_prompt_once_so_gate_and_text_come_from_one_version(monkeypatch, session_factory):
    from app.services import ai_prompt_registry

    reads = []
    real = ai_prompt_registry.get_prompt

    def counting(*a, **k):
        reads.append(1)
        return real(*a, **k)

    # `render` reads through the registry's own name; `parser` imported the function by name.
    monkeypatch.setattr(ai_prompt_registry, "get_prompt", counting)
    monkeypatch.setattr(parser_mod, "get_prompt", counting)
    _resolve(monkeypatch, session_factory, f"Rules {{{{current_date}}}}\n{HEADING_LINE}\n")
    assert len(reads) == 1, f"the prompt was read {len(reads)} times"


def test_r8_the_prompt_says_a_reference_to_the_held_subject_is_keep_and_never_an_entity():
    import re

    from app.services.chatbot.head.chat_state_prompt import STATE_SECTION

    text = " ".join(STATE_SECTION.split())
    assert re.search(r"(refer|point)[^.]*held[^.]*`keep`", text) or re.search(r"`keep`[^.]*(refer|point)[^.]*held", text), text
    assert re.search(r"never[^.]*(as|an) entit", text), text


def test_r11_the_prompt_puts_the_options_of_the_open_question_under_pick_and_values_under_answer():
    from app.services.chatbot.head.chat_state_prompt import STATE_SECTION

    rows = {l.split("|")[1].strip(): l for l in STATE_SECTION.splitlines() if l.startswith("| `")}
    pick, answer = rows["`pick`"].lower(), rows["`answer`"].lower()
    assert "open question" in pick and "option" in pick and "last list" in pick, pick
    assert "yes" in answer and "no" in answer and "value" in answer, answer
    assert "one of its options" not in answer, answer


# --------------------------------------------------------------------------- #
# Round 2 integration: one schema when both gated sections are on
# --------------------------------------------------------------------------- #

CLOSED_MARKER = "== CLOSED LISTS =="


def _sent_schema(monkeypatch, session_factory, raw_template: str) -> tuple[Any, dict[str, Any]]:
    """resolve_config on a seeded version, then the schema parse() hands the provider."""
    from app.services.chatbot import llm_call

    config = _resolve(monkeypatch, session_factory, raw_template)
    sent: dict[str, Any] = {}

    def fake_chat(provider, api_key, model, messages, **kwargs):
        sent.update(kwargs)
        return types.SimpleNamespace(
            content=json.dumps({key: None for key in parser_mod.DECLARED_KEYS}),
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cached_tokens=0,
        )

    monkeypatch.setattr(llm_call, "chat", fake_chat)
    parser_mod.parse(config, "Current user message: hi")
    return config, sent["json_schema"]


def _entity_props(schema: dict[str, Any]) -> dict[str, Any]:
    return schema["properties"]["entities"]["items"]["properties"]


def test_r2_both_sections_turn_both_gates_on_and_one_schema_carries_both_additions(monkeypatch, session_factory):
    raw = f"Rules {{{{current_date}}}}\n{CLOSED_MARKER}\n{HEADING_LINE}\nFill state_changes.\n"
    config, schema = _sent_schema(monkeypatch, session_factory, raw)
    assert config.state_block is True and config.closed_lists is True
    assert "listed_item" in _entity_props(schema)
    assert "listed_item" in schema["properties"]["entities"]["items"]["required"]
    assert list(schema["properties"])[-1] == "state_changes"
    assert schema["required"][-1] == "state_changes"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["state_changes"] == parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE["properties"]["state_changes"]


def test_r2_closed_lists_alone_sends_its_own_schema_without_state_changes(monkeypatch, session_factory):
    config, schema = _sent_schema(monkeypatch, session_factory, f"Rules {{{{current_date}}}}\n{CLOSED_MARKER}\n")
    assert config.closed_lists is True and config.state_block is False
    assert "listed_item" in _entity_props(schema)
    assert "state_changes" not in schema["properties"]


def test_r2_state_alone_sends_the_state_schema_object(monkeypatch, session_factory):
    config, schema = _sent_schema(monkeypatch, session_factory, f"Rules {{{{current_date}}}}\n{HEADING_LINE}\n")
    assert config.state_block is True and config.closed_lists is False
    assert schema is parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE


def test_r2_neither_section_sends_the_base_schema_object(monkeypatch, session_factory):
    config, schema = _sent_schema(monkeypatch, session_factory, "Rules {{current_date}}.\n")
    assert config.state_block is False and config.closed_lists is False
    assert schema is parser_mod.PARSE_OUTPUT_JSON_SCHEMA


# --------------------------------------------------------------------------- #
# r2-B / r2-D: prompt text
# --------------------------------------------------------------------------- #


def _section_text() -> str:
    from app.services.chatbot.head.chat_state_prompt import STATE_SECTION

    return " ".join(STATE_SECTION.split())


def test_r2b_an_entity_is_what_this_message_names_even_when_the_state_holds_it():
    import re

    text = _section_text()
    assert "Never restate an item of the State" not in text
    assert re.search(r"even when the State (already )?holds", text), text
    assert re.search(r"(State item|item of the State)[^.]*(does not name|not named)[^.]*not (copied|an entity)", text) or re.search(
        r"not (copied|an entity)", text
    ), text


def test_r2b_the_reference_rule_stays():
    text = _section_text()
    assert "such a reference is never output as an entity" in text


def test_r2d_a_message_naming_only_another_item_asks_the_same_fact_as_the_last_answer():
    import re

    text = _section_text()
    assert "(answered)" in text, "the subject's domain marker must be explained"
    assert re.search(r"(naming|names) only (another|a different) (item|code|product)[^.]*(same|keep)[^.]*(domain|fact|last answer)", text), text
    assert re.search(r"`set product`", text)


# --------------------------------------------------------------------------- #
# r2-C: one combined publish
# --------------------------------------------------------------------------- #


def _combined(session, **kwargs):
    from scripts.publish_chat_state_prompt import publish

    return publish(session, with_closed_lists=True, **kwargs)


def test_r2c_the_combined_publish_carries_both_sections_once_over_an_unchanged_base(session_factory):
    from app.services.chatbot.head.closed_lists_prompt import MARKER

    session = session_factory()
    _seed_publish_base(session)
    version = _combined(session, from_version=None)
    rows = _publish_versions(session)
    assert version == 2 and [r.version for r in rows] == [1, 2]
    template = rows[-1].template
    assert template.startswith(PUBLISH_BASE)
    assert template.count(MARKER) == 1 and template.count(HEADING_LINE) == 1


def test_r2c_the_combined_publish_is_idempotent_and_writes_no_label(session_factory):
    from app.models.ai_prompt import AIPromptLabel

    session = session_factory()
    old = _seed_publish_base(session)
    first = _combined(session, from_version=None)
    second = _combined(session, from_version=None)
    assert second in (first, None)
    assert len(_publish_versions(session)) == 2
    labels = session.query(AIPromptLabel).filter(AIPromptLabel.name == parser_mod.PROMPT_KEY).all()
    assert [(l.label, l.version_id) for l in labels] == [("production", old.id)]


def test_r2c_resolve_config_on_the_combined_version_turns_both_gates_on(monkeypatch, session_factory):
    from app.services import ai_assistant_service, llm_provider

    session = session_factory()
    _seed_publish_base(session)
    version = _combined(session, from_version=None)
    row = [r for r in _publish_versions(session) if r.version == version][0]
    cfg = types.SimpleNamespace(provider="openai", model="gpt-test")
    monkeypatch.setattr(
        ai_assistant_service, "AIAssistantConfigService", lambda d: types.SimpleNamespace(get=lambda: cfg)
    )
    monkeypatch.setattr(llm_provider, "resolve_api_key", lambda config, provider: "sk-test")
    config = parser_mod.resolve_config(session, current_date="2026-10-17", override_version_id=row.id)
    assert config.state_block is True and config.closed_lists is True


PUBLISH_BASE = "You read a customer message.\nToday is {{current_date}}.\n"


def _seed_publish_base(session):
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    old = AIPromptVersion(id=str(uuid.uuid4()), name=parser_mod.PROMPT_KEY, version=1, type="text",
                          template=PUBLISH_BASE, variables=["current_date"])
    session.add(old)
    session.flush()
    session.add(AIPromptLabel(id=str(uuid.uuid4()), name=parser_mod.PROMPT_KEY, label="production", version_id=old.id))
    session.flush()
    from app.services import ai_prompt_registry

    ai_prompt_registry.bust_cache()
    return old


def _publish_versions(session):
    from app.models.ai_prompt import AIPromptVersion

    return (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == parser_mod.PROMPT_KEY)
        .order_by(AIPromptVersion.version)
        .all()
    )
