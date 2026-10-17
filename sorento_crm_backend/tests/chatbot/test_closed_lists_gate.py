"""CHAT-CLOSED-LISTS S3: the prompt section, its gate, and the candidate block in the user turn.

The closed-lists path runs only for a prompt version whose raw template carries the section
marker (the CHAT-STATE pattern), so the production label keeps today's parser input byte for
byte until the owner moves it. The section is code (`head/closed_lists_prompt.py`), published
as an UNLABELLED version by `scripts/publish_closed_lists_prompt.py`.
"""
from __future__ import annotations

import json
import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import ai_prompt_registry
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import candidates
from app.services.chatbot.head import closed_lists_prompt as clp
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from scripts import publish_closed_lists_prompt as publisher
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _envelope, _parser_output, seeded, stub_access  # noqa: F401

KEY = parser_mod.PROMPT_KEY


@pytest.fixture(autouse=True)
def _fresh_cache():
    ai_prompt_registry.bust_cache()
    yield
    ai_prompt_registry.bust_cache()


def _version(db, template: str, *, label: str | None = None) -> AIPromptVersion:
    versions = [v for (v,) in db.query(AIPromptVersion.version).filter(AIPromptVersion.name == KEY)]
    row = AIPromptVersion(
        id=str(uuid.uuid4()), name=KEY, version=max(versions, default=0) + 1, type="text", template=template,
        variables=["current_date"],
    )
    db.add(row)
    db.flush()
    if label:
        db.add(AIPromptLabel(id=str(uuid.uuid4()), name=KEY, label=label, version_id=row.id))
        db.flush()
    return row


# --- the section and the gate --------------------------------------------------------------------


def test_the_section_carries_the_marker_and_both_list_tokens():
    assert clp.MARKER in clp.SECTION
    assert "{{attachment_types}}" in clp.SECTION and "{{certification_types}}" in clp.SECTION
    assert candidates.HEADER.split(" (")[0] in clp.SECTION


def test_the_gate_reads_the_raw_template_only():
    assert clp.gate_on("parser text" + clp.SECTION) is True
    assert clp.gate_on("parser text") is False
    assert clp.gate_on(None) is False


def test_the_production_label_without_the_section_keeps_the_gate_off():
    with blank_session() as db:
        _version(db, "production parser {{current_date}}", label="production")
        assert parser_mod.uses_closed_lists(db) is False


def test_a_pinned_version_with_the_section_turns_the_gate_on():
    with blank_session() as db:
        _version(db, "production parser {{current_date}}", label="production")
        lane = _version(db, "production parser {{current_date}}" + clp.SECTION)
        assert parser_mod.uses_closed_lists(db, override_version_id=lane.id) is True
        assert parser_mod.uses_closed_lists(db) is False


def test_parser_config_defaults_to_the_gate_off():
    config = parser_mod.ParserConfig(system_prompt="x", prompt_version=1, provider="openai", model="m", api_key="k")
    assert config.closed_lists is False


# --- the publish script --------------------------------------------------------------------------


def test_publish_appends_the_section_to_production_as_an_unlabelled_version_once():
    with blank_session() as db:
        prod = _version(db, "production parser {{current_date}}", label="production")

        first = publisher.publish(db)
        again = publisher.publish(db)

        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == first).one()
        assert row.template == prod.template + clp.SECTION
        assert again is None
        labels = {(lab.label, lab.version_id) for lab in db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY)}
        assert labels == {("production", prod.id)}


def test_publish_from_a_named_version_builds_on_that_version():
    with blank_session() as db:
        _version(db, "production parser {{current_date}}", label="production")
        base = _version(db, "a lane's own version {{current_date}}")

        new = publisher.publish(db, from_version=base.version)

        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == new).one()
        assert row.template == base.template + clp.SECTION


def test_publish_refuses_a_base_that_already_carries_the_section():
    with blank_session() as db:
        _version(db, "production parser" + clp.SECTION, label="production")
        with pytest.raises(SystemExit):
            publisher.publish(db)


# --- the candidate block in the user turn --------------------------------------------------------


def _product(session_factory, code: str) -> None:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    db.add(Product(
        id=str(uuid.uuid4()), product_code=code, product_name="Synthetic WC", category_id=cat.id,
        base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
    ))
    db.commit()


def _user_block(session_factory, stub_access, monkeypatch, *, gate: bool) -> str:
    seen: list[str] = []

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="k", closed_lists=gate,
        )

    def fake_parse(config, user_block):
        seen.append(user_block)
        return _parser_output()

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    # The contact reads its own company's products (its scope is resolved elsewhere and
    # tested there; an unlinked test contact fails closed and would read none).
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda factory, contact: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({"ok": True, "items": []}))
    stub_access()
    engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert len(seen) == 1
    return seen[0]


def test_gate_off_the_user_turn_carries_no_candidate_block(session_factory, seeded, stub_access, monkeypatch):
    _product(session_factory, "SRTWC8517")

    block = _user_block(session_factory, stub_access, monkeypatch, gate=False)

    assert candidates.HEADER not in block


def test_gate_on_the_user_turn_lists_the_candidates_after_the_message(session_factory, seeded, stub_access, monkeypatch):
    _product(session_factory, "SRTWC8517")

    block = _user_block(session_factory, stub_access, monkeypatch, gate=True)

    lines = block.split("\n")
    at = lines.index(candidates.HEADER)
    said = lines.index("Current user message: price for SRTWC8517")
    assert said < at and all(not line.strip() for line in lines[said + 1 : at])  # the message's own blank lines
    assert lines[at + 1] == '- "srtwc8517": code SRTWC8517 (Synthetic WC)'


@pytest.mark.parametrize("gate", [False, True])
def test_grounding_runs_under_the_gate_and_the_verdict_says_so(gate, session_factory, seeded, stub_access, monkeypatch):
    from app.services.chatbot.head import grounding

    seen: list[tuple[dict, dict]] = []
    real = grounding.ground

    def spy(db, verdict, **kwargs):
        out = real(db, verdict, **kwargs)
        seen.append((kwargs, out[0]))
        return out

    monkeypatch.setattr(grounding, "ground", spy)
    _user_block(session_factory, stub_access, monkeypatch, gate=gate)

    [(kwargs, _out)] = seen
    assert kwargs.get("closed_lists", False) is gate
    assert engine_mod.closed_lists_verdict({"entities": []}, gate=gate) == (
        {"entities": [], "closed_lists": True} if gate else {"entities": []}
    )


# --- S4b-v2: the closed list on the wire --------------------------------------------------------


def test_the_gated_schema_holds_listed_item_to_the_rendered_items():
    schema = parser_mod.closed_lists_schema(["technical_specifications", "certification", "PPS"])
    entity = schema["properties"]["entities"]["items"]
    assert entity["properties"]["listed_item"] == {
        "type": ["string", "null"],
        "enum": ["technical_specifications", "certification", "PPS", clp.NOT_LISTED, None],
    }
    assert "listed_item" in entity["required"]
    # The production schema is untouched.
    assert "listed_item" not in parser_mod.PARSE_OUTPUT_JSON_SCHEMA["properties"]["entities"]["items"]["properties"]


def test_parse_sends_the_configs_own_schema(monkeypatch):
    from app.services.chatbot import llm_call

    sent: list[dict] = []

    class _Result:
        content = json.dumps({**_parser_output(), "asks": None, "topic_reset": None})
        prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0

    def chat(*args, **kwargs):
        sent.append(kwargs["json_schema"])
        return _Result()

    monkeypatch.setattr(llm_call, "chat", chat)
    schema = parser_mod.closed_lists_schema(["certification"])
    gated = parser_mod.ParserConfig(
        system_prompt="x", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, json_schema=schema,
    )
    plain = parser_mod.ParserConfig(system_prompt="x", prompt_version=1, provider="openai", model="m", api_key="k")

    parser_mod.parse(gated, "block")
    parser_mod.parse(plain, "block")

    assert sent == [schema, parser_mod.PARSE_OUTPUT_JSON_SCHEMA]


def test_the_items_are_what_the_two_lists_render():
    from app.services import chatbot_prompt_vars as pv

    with blank_session() as db:
        assert pv.closed_list_enum(db) == []


def test_no_item_at_all_makes_listed_item_null_only():
    entity = parser_mod.closed_lists_schema([])["properties"]["entities"]["items"]
    assert entity["properties"]["listed_item"] == {"type": "null"}


def test_the_section_names_the_none_of_these_value():
    """S6 (17 Oct): with null as the only way out the parser forced a brochure ask onto the
    nearest listed type; a named "none of these" value is what it picks instead."""
    assert f'"{clp.NOT_LISTED}"' in clp.SECTION


# --- {{specs}} (SPEC-REGISTRY-UI #1765: owner edits to the registry reach the bot) ---------------

BAKED = (
    "Entity kind product: resolver products. Did-you-mean on. Default narrowing narrow_to_code.\n"
    "\n"
    'Specification finish ("Finish"): choices Chrome = chrome (cp); Gunmetal = gunmetal.\n'
    'Specification thickness ("Thickness"): a number in mm.\n'
    "Product types (the category entity keeps these, never a specification): Tap, Wash Basin.\n"
    "<<<END>>>\nrest of the prompt {{current_date}}"
)


def test_publish_turns_the_baked_specification_lines_into_the_specs_token():
    with blank_session() as db:
        _version(db, BAKED, label="production")

        new = publisher.publish(db)

        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == new).one()
        assert "Specification finish" not in row.template and "Product types (the category" not in row.template
        assert row.template == (
            "Entity kind product: resolver products. Did-you-mean on. Default narrowing narrow_to_code.\n"
            "\n{{specs}}\n<<<END>>>\nrest of the prompt {{current_date}}" + clp.SECTION
        )


def test_the_specs_token_renders_the_registry_at_read_time():
    from app.services.chatbot_prompt_vars import render_values

    with blank_session() as db:
        _version(db, BAKED, label="production")
        new = publisher.publish(db)
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == new).one()

        text, _v = ai_prompt_registry.render(db, KEY, override_version_id=row.id, current_date="today")

        live = render_values(db, ["specs"])["specs"]
        assert "{{specs}}" not in text
        assert ("\n\n" + live + "\n<<<END>>>") in text


def test_a_base_with_the_token_or_no_specification_lines_keeps_its_text():
    assert publisher.with_specs_token("a {{specs}} b") == "a {{specs}} b"
    assert publisher.with_specs_token("no spec lines here") == "no spec lines here"


def test_two_separate_specification_runs_are_refused():
    twice = 'Specification a ("A"): yes or no.\nother\nSpecification b ("B"): yes or no.\n'
    with pytest.raises(SystemExit):
        publisher.with_specs_token(twice)


# --- STATE + CLOSED LISTS on one version (#1747 merged first) -----------------------------------


def test_the_gated_schema_over_the_state_schema_keeps_both():
    schema = parser_mod.closed_lists_schema(["certification"], state=True)

    assert "state_changes" in schema["properties"] and "state_changes" in schema["required"]
    entity = schema["properties"]["entities"]["items"]
    assert entity["properties"]["listed_item"]["enum"] == ["certification", clp.NOT_LISTED, None]
    assert "listed_item" in entity["required"]
    # The module's own schemas are never mutated by the build.
    assert "listed_item" not in parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE["properties"]["entities"]["items"]["properties"]


def test_a_version_with_both_sections_resolves_to_the_combined_schema(monkeypatch):
    from app.services import ai_assistant_service, llm_provider
    from app.services.chatbot.head.chat_state_prompt import HEADING
    from types import SimpleNamespace

    cfg = SimpleNamespace(provider="openai", model="gpt-test")
    monkeypatch.setattr(ai_assistant_service, "AIAssistantConfigService", lambda d: SimpleNamespace(get=lambda: cfg))
    monkeypatch.setattr(llm_provider, "resolve_api_key", lambda config, provider: "sk-test")
    with blank_session() as db:
        _version(db, f"Rules {{{{current_date}}}}\n{HEADING}\nFill state_changes." + clp.SECTION, label="production")

        config = parser_mod.resolve_config(db, current_date="today")

    assert config.state_block is True and config.closed_lists is True
    assert "state_changes" in config.json_schema["properties"]
    assert "listed_item" in config.json_schema["properties"]["entities"]["items"]["properties"]


def test_a_state_only_version_keeps_mains_state_schema(monkeypatch):
    from app.services.chatbot import llm_call

    sent: list[dict] = []

    class _Result:
        content = json.dumps({**_parser_output(), "asks": None, "topic_reset": None, "state_changes": None})
        prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0

    monkeypatch.setattr(llm_call, "chat", lambda *a, **kw: sent.append(kw["json_schema"]) or _Result())
    state_only = parser_mod.ParserConfig(
        system_prompt="x", prompt_version=1, provider="openai", model="m", api_key="k", state_block=True,
    )
    try:
        parser_mod.parse(state_only, "block")
    except parser_mod.ParserError:
        pass  # the emission shape is not what this test pins; the schema sent is

    assert sent == [parser_mod.PARSE_OUTPUT_JSON_SCHEMA_WITH_STATE]
