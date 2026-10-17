"""CHAT-CODE-LOOKUP slice A: the CODE LOOKUP prompt section, its gate, and its publisher (U0.2).

The code-lookup path runs only for a parser version whose RAW template carries BOTH the
CLOSED LISTS and the CODE LOOKUP markers. The production label keeps today's input.
"""
from __future__ import annotations

import types
import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry
from app.services.chatbot.head import closed_lists_prompt as clp
from app.services.chatbot.head import parser as parser_mod
from tests._pg_fixture import blank_session

KEY = parser_mod.PROMPT_KEY
CLP_MARKER = "== CLOSED LISTS =="
CL_MARKER = "== CODE LOOKUP =="


def _cl():
    from app.services.chatbot.head import code_lookup_prompt

    return code_lookup_prompt


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


def test_the_section_carries_the_marker_after_a_blank_line():
    mod = _cl()
    assert mod.MARKER == CL_MARKER
    assert mod.SECTION.startswith("\n") and CL_MARKER in mod.SECTION


@pytest.mark.parametrize(
    "template, expected",
    [
        (f"parser {CLP_MARKER} {CL_MARKER}", True),
        (f"parser {CL_MARKER} {CLP_MARKER}", True),
        (f"parser {CLP_MARKER}", False),
        (f"parser {CL_MARKER}", False),
        ("parser", False),
        ("", False),
        (None, False),
    ],
)
def test_gate_on_needs_both_markers_in_the_raw_template(template, expected):
    assert _cl().gate_on(template) is expected


def test_parser_config_defaults_to_code_lookup_off():
    config = parser_mod.ParserConfig(system_prompt="x", prompt_version=1, provider="openai", model="m", api_key="k")
    assert config.code_lookup is False


def _stub_resolve(monkeypatch):
    from app.services import ai_assistant_service, chatbot_prompt_vars, llm_provider

    monkeypatch.setattr(parser_mod, "render", lambda db, key, current_date, override_version_id=None: ("sys", 1))
    monkeypatch.setattr(parser_mod, "agent_model", lambda db, key: (None, None))
    monkeypatch.setattr(
        ai_assistant_service.AIAssistantConfigService, "get",
        lambda self: types.SimpleNamespace(provider="openai", model="m"),
    )
    monkeypatch.setattr(llm_provider, "resolve_api_key", lambda config, provider: "k")
    monkeypatch.setattr(chatbot_prompt_vars, "closed_list_enum", lambda db: [])


def test_resolve_config_turns_code_lookup_on_only_for_a_template_with_both_markers(monkeypatch):
    _stub_resolve(monkeypatch)
    with blank_session() as db:
        both = _version(db, f"parser {CLP_MARKER} {CL_MARKER}")
        closed_only = _version(db, f"parser {CLP_MARKER}")
        lookup_only = _version(db, f"parser {CL_MARKER}")
        plain = _version(db, "parser", label="production")

        def cfg(row):
            return parser_mod.resolve_config(db, current_date="2026-10-17", override_version_id=row.id)

        assert cfg(both).code_lookup is True and cfg(both).closed_lists is True
        assert cfg(closed_only).code_lookup is False and cfg(closed_only).closed_lists is True
        assert cfg(lookup_only).code_lookup is False
        assert cfg(plain).code_lookup is False
        assert parser_mod.uses_code_lookup(db, override_version_id=both.id) is True
        assert parser_mod.uses_code_lookup(db, override_version_id=closed_only.id) is False
        assert parser_mod.uses_code_lookup(db) is False


# --- the publish script --------------------------------------------------------------------------


def _publisher():
    from scripts import publish_code_lookup_prompt

    return publish_code_lookup_prompt


def _base_with_anchor(lead: str = "parser") -> str:
    return f"{lead} {CLP_MARKER}\nORDER_STATUS FILTER\n  {_publisher().OUTSTANDING_ANCHOR} tail of the bullet\n  - next bullet\n"


def _expected(base: str) -> str:
    pub = _publisher()
    at = base.index(pub.OUTSTANDING_ANCHOR) + len(pub.OUTSTANDING_ANCHOR)
    return base[:at] + pub.OUTSTANDING_ADDITION + base[at:] + _cl().SECTION


def test_publish_appends_the_section_to_the_closed_lists_production_once_unlabelled():
    with blank_session() as db:
        prod = _version(db, _base_with_anchor(), label="production")

        first = _publisher().publish(db)
        again = _publisher().publish(db)

        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == first).one()
        assert row.template == _expected(prod.template)
        assert again is None
        labels = {(lab.label, lab.version_id) for lab in db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY)}
        assert labels == {("production", prod.id)}


def test_publish_from_a_named_version_builds_on_that_version():
    with blank_session() as db:
        _version(db, _base_with_anchor(), label="production")
        base = _version(db, _base_with_anchor("lane own"))

        new = _publisher().publish(db, from_version=base.version)

        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == new).one()
        assert row.template == _expected(base.template)


def test_publish_inserts_the_when_deliver_class_once_and_leaves_the_rest_byte_identical():
    pub = _publisher()
    with blank_session() as db:
        base = _version(db, _base_with_anchor(), label="production")
        new = pub.publish(db)
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == new).one()
        assert row.template.count(pub.OUTSTANDING_ADDITION) == 1
        assert row.template.endswith(_cl().SECTION)
        body = row.template[: -len(_cl().SECTION)]
        assert body.replace(pub.OUTSTANDING_ADDITION, "", 1) == base.template
        add = pub.OUTSTANDING_ADDITION.lower()
        assert "when" in add and "deliver" in add and "bila hantar" in add and "几时送" in add


def test_publish_refuses_a_base_without_the_outstanding_anchor_and_writes_nothing():
    with blank_session() as db:
        _version(db, f"parser {CLP_MARKER} no outstanding bullet", label="production")
        with pytest.raises(SystemExit):
            _publisher().publish(db)
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == 1


def test_publish_refuses_a_base_with_the_outstanding_anchor_twice():
    anchor = _publisher().OUTSTANDING_ANCHOR
    with blank_session() as db:
        _version(db, f"parser {CLP_MARKER}\n{anchor} a\n{anchor} b\n", label="production")
        with pytest.raises(SystemExit):
            _publisher().publish(db)
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == 1


def test_publish_refuses_a_base_without_the_closed_lists_section():
    with blank_session() as db:
        _version(db, "parser without the closed lists", label="production")
        with pytest.raises(SystemExit):
            _publisher().publish(db)


def test_publish_refuses_a_base_that_already_carries_code_lookup():
    with blank_session() as db:
        _version(db, f"parser {CLP_MARKER} {CL_MARKER}", label="production")
        with pytest.raises(SystemExit):
            _publisher().publish(db)
