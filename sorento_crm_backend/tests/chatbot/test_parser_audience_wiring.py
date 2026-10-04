"""PARSER-PER-AUDIENCE, AC-PA-9 and AC-PA-10: the system prompt `parser.parse` actually
receives on a real `engine.run_turn` is the audience render of the one `production`
version, for that contact's `ctx.access.attributes`.

`parser.resolve_config` is NOT stubbed here (every other engine test stubs it); only
`parser.parse` is, and it records the `config.system_prompt` it was handed. The provider
key is faked at `llm_provider.resolve_api_key` so no key is needed in CI. Postgres only:
the blank-schema `session_factory`, with the `production` label and its version seeded
per test.

Two layers: a small synthetic template (red until `resolve_config` takes `grants` and the
engine passes them, whatever the data file looks like) and the real tagged prod text (red
until the coder also lands `prompt_gates.py` and the tagged file).

No em or en dashes.
"""
from __future__ import annotations

import pathlib
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import CONTACT_ID, _envelope  # noqa: F401
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _enable_business_lane,
    _qf,
    _resolve_services,
    _seed_contact,
    _wire_business_services,
)
from tests.chatbot.test_parser_audience_render import ALL_GRANTS, FORBIDDEN, TAG, _has_term, _subset_id

KEY = "chatbot_semantic_parser"
BACKEND = pathlib.Path(__file__).resolve().parents[2]
TAGGED = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.tagged.txt"
PROD = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"

C, P, S, L = ALL_GRANTS

SYNTHETIC = (
    "HEAD {{current_date}}\n"
    "{{#only sales}}\nSALES_BLOCK_ZZT\n{{/only}}\n"
    "{{#only purchase_cost}}\nCOST_BLOCK_ZZT\n{{/only}}\n"
    "{{#only purchase_order}}\nPO_BLOCK_ZZT\n{{/only}}\n"
    "{{#only spo_allocation}}\nSPO_BLOCK_ZZT\n{{/only}}\n"
    "{{#only low_stock_report}}\nLOW_BLOCK_ZZT\n{{/only}}\n"
    "TAIL_ZZT\n"
)
#: Placed carries two rows, so two marks.
BLOCK_MARK = {
    S: ["SALES_BLOCK_ZZT"],
    C: ["COST_BLOCK_ZZT"],
    P: ["PO_BLOCK_ZZT", "SPO_BLOCK_ZZT"],
    L: ["LOW_BLOCK_ZZT"],
}
ALL_MARKS = [m for marks in BLOCK_MARK.values() for m in marks]


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _publish(session_factory, template: str) -> str:
    """Point `production` at a NEW version holding `template`. Returns the version id."""
    db = session_factory()
    vid = db.execute(
        text(
            "INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, created_at) "
            "VALUES (gen_random_uuid(), :n, (SELECT COALESCE(max(version), 0) + 1 FROM ai_prompt_versions WHERE name = :n), "
            "'text', :t, '[\"current_date\"]', now()) RETURNING id"
        ),
        {"n": KEY, "t": template},
    ).scalar()
    moved = db.execute(
        text("UPDATE ai_prompt_labels SET version_id = :v WHERE name = :n AND label = 'production'"),
        {"v": vid, "n": KEY},
    ).rowcount
    if not moved:
        db.execute(
            text("INSERT INTO ai_prompt_labels (id, name, label, version_id) VALUES (gen_random_uuid(), :n, 'production', :v)"),
            {"n": KEY, "v": vid},
        )
    db.commit()
    ai_prompt_registry.bust_cache()
    return str(vid)


def _version_count(session_factory) -> int:
    return session_factory().execute(
        text("SELECT count(*) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}
    ).scalar()


def _real_turn(session_factory, monkeypatch, *, attributes: list[str]) -> str:
    """One real `engine.run_turn` with `resolve_config` UNSTUBBED. Returns the system
    prompt `parser.parse` was handed."""
    if not session_factory().execute(
        text("SELECT 1 FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": str(CONTACT_ID)}
    ).first():
        _seed_contact(session_factory, variables={})
    _enable_business_lane(session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": list(attributes), "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(
        engine_mod, "contact_reveal_grants", lambda db, *, contact_id, space_id: list(attributes)
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    monkeypatch.setattr("app.services.llm_provider.resolve_api_key", lambda cfg, provider: "sk-test")

    seen: list[str] = []

    def _fake_parse(config, user_block):
        seen.append(config.system_prompt)
        return _qf(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        )

    monkeypatch.setattr(parser_mod, "parse", _fake_parse)
    call, _captured = _capturing_mcp(None)
    _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)

    envelope = _envelope()
    envelope.message["message"]["messageId"] = f"ZZT-pa-wiring-{uuid.uuid4().hex[:10]}"
    envelope.message["message"]["message"]["text"] = "hello"
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    assert result.status == "done", result.error
    assert len(seen) == 1, "parser.parse must be called exactly once"
    return seen[0]


# --------------------------------------------------------------------------- #
# The seam itself: resolve_config(grants=...)
# --------------------------------------------------------------------------- #


class TestResolveConfigTakesTheAudience:
    @pytest.mark.parametrize("grants", [[], [S], [C, P, S, L], None], ids=["empty", "S", "all", "None"])
    def test_system_prompt_is_the_render_for_those_grants(
        self, session_factory, monkeypatch, grants
    ) -> None:
        monkeypatch.setattr("app.services.llm_provider.resolve_api_key", lambda cfg, provider: "sk-test")
        _publish(session_factory, SYNTHETIC)
        db = session_factory()
        config = parser_mod.resolve_config(db, current_date="Friday, 02 October 2026", grants=grants)
        held = set(grants or [])
        for grant, marks in BLOCK_MARK.items():
            for mark in marks:
                assert (mark in config.system_prompt) == (grant in held), (grant, config.system_prompt)
        assert "{{#only" not in config.system_prompt and "{{/only}}" not in config.system_prompt
        assert "Friday, 02 October 2026" in config.system_prompt
        assert "TAIL_ZZT" in config.system_prompt

    def test_grants_is_optional_and_means_no_grants(self, session_factory, monkeypatch) -> None:
        monkeypatch.setattr("app.services.llm_provider.resolve_api_key", lambda cfg, provider: "sk-test")
        _publish(session_factory, SYNTHETIC)
        config = parser_mod.resolve_config(session_factory(), current_date="Friday, 02 October 2026")
        assert not any(mark in config.system_prompt for mark in ALL_MARKS)


# --------------------------------------------------------------------------- #
# AC-PA-9 on a real turn
# --------------------------------------------------------------------------- #


class TestTheTurnHandsTheParserItsAudiencesPrompt:
    @pytest.mark.parametrize("grants", [[], [S], [C, P], [C, P, S, L]], ids=lambda g: _subset_id(tuple(g)))
    def test_synthetic_template_per_contact(self, session_factory, monkeypatch, grants) -> None:
        _publish(session_factory, SYNTHETIC)
        prompt = _real_turn(session_factory, monkeypatch, attributes=grants)
        for grant, marks in BLOCK_MARK.items():
            for mark in marks:
                assert (mark in prompt) == (grant in grants), (grant, prompt)
        assert "{{#only" not in prompt and "{{/only}}" not in prompt

    def test_no_version_or_label_is_created_per_audience(self, session_factory, monkeypatch) -> None:
        _publish(session_factory, SYNTHETIC)
        before = _version_count(session_factory)
        _real_turn(session_factory, monkeypatch, attributes=[])
        ai_prompt_registry.bust_cache()
        _real_turn(session_factory, monkeypatch, attributes=[S])
        assert _version_count(session_factory) == before == 1
        labels = session_factory().execute(
            text("SELECT count(*) FROM ai_prompt_labels WHERE name = :n"), {"n": KEY}
        ).scalar()
        assert labels == 1

    def test_unidentified_contact_gets_the_minimal_render(self, session_factory, monkeypatch) -> None:
        _publish(session_factory, SYNTHETIC)
        prompt = _real_turn(session_factory, monkeypatch, attributes=[])
        assert not any(mark in prompt for mark in ALL_MARKS)
        assert "TAIL_ZZT" in prompt

    def test_prod_text_minimal_contact_has_no_forbidden_term(self, session_factory, monkeypatch) -> None:
        _publish(session_factory, TAGGED.read_text(encoding="utf-8"))
        prompt = _real_turn(session_factory, monkeypatch, attributes=[])
        leaked = {
            _subset_id((grant,)): [t for t in FORBIDDEN[grant] if _has_term(prompt, t)]
            for grant in ALL_GRANTS
        }
        assert not any(leaked.values()), {k: v for k, v in leaked.items() if v}
        assert "{{#only" not in prompt

    def test_prod_text_full_audience_equals_the_untagged_prompt(self, session_factory, monkeypatch) -> None:
        _publish(session_factory, TAGGED.read_text(encoding="utf-8"))
        full = _real_turn(session_factory, monkeypatch, attributes=list(ALL_GRANTS))
        for gate_tag in TAG:
            assert "{{#only " + gate_tag not in full
        _publish(session_factory, PROD.read_text(encoding="utf-8"))
        reference = _real_turn(session_factory, monkeypatch, attributes=list(ALL_GRANTS))
        assert full == reference
        assert re.search(r"^== LOW STOCK REPORT", full, flags=re.MULTILINE)


# --------------------------------------------------------------------------- #
# AC-PA-10: one version stream
# --------------------------------------------------------------------------- #


class TestMovingTheLabelMovesEveryAudience:
    @pytest.mark.parametrize("grants", [[], [S], [C, P, S, L]], ids=lambda g: _subset_id(tuple(g)))
    def test_a_new_production_version_reaches_the_audience_after_the_cache(
        self, session_factory, monkeypatch, grants
    ) -> None:
        _publish(session_factory, SYNTHETIC)
        first = _real_turn(session_factory, monkeypatch, attributes=grants)
        assert "NEW_LINE_ZZT" not in first

        _publish(session_factory, SYNTHETIC + "NEW_LINE_ZZT\n")  # bumps the cache, as the TTL would
        second = _real_turn(session_factory, monkeypatch, attributes=grants)
        assert "NEW_LINE_ZZT" in second
        for grant, marks in BLOCK_MARK.items():
            for mark in marks:
                assert (mark in second) == (grant in grants), (grant, second)
        assert _version_count(session_factory) == 2
