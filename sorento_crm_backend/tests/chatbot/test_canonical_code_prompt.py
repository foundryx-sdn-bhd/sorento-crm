"""RESOLVE-SEMANTIC-FALLBACK, the static half of the prompt change: `CANONICAL_CODE_ADDENDUM`
is stacked once in `SEMANTIC_PARSER_PROMPT` (before the memory addendum, which stays the
tail), states the owner's rule in the owner's own example, and is what the migration
publishes as the next unlabelled `chatbot_semantic_parser` version.

The LIVE grading (does the model actually fill canonical_code) is
`test_canonical_code_live_parser.py`, opt-in; this file runs in CI and needs no provider.
Follows `test_samantha_26sep_s9_brand_feed.py::TestPromptHasNoHardCodedBrandList`: the
migration module is imported directly and graded on its own `_full_text()`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.chatbot_parser_prompt import (
    CANONICAL_CODE_ADDENDUM,
    MEMORY_ADDENDUM,
    SEMANTIC_PARSER_PROMPT,
)

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "chatbot_canonical_code.py"


def test_the_addendum_is_stacked_once_before_the_memory_tail():
    assert SEMANTIC_PARSER_PROMPT.count(CANONICAL_CODE_ADDENDUM) == 1
    assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM), "the memory addendum stays the tail"
    assert SEMANTIC_PARSER_PROMPT.index(CANONICAL_CODE_ADDENDUM) < SEMANTIC_PARSER_PROMPT.index(MEMORY_ADDENDUM)


def test_the_addendum_states_the_owners_rule_in_the_owners_example():
    """Card Q1 = (a): one entity, raw unchanged, canonical_code the bare code as typed."""
    assert '"TAP SRTWT5608 x 5" -> entity {raw: "TAP SRTWT5608", hint: "product", canonical_code:' in CANONICAL_CODE_ADDENDUM
    assert '"SRTWT5608", quantity: 5}' in CANONICAL_CODE_ADDENDUM
    assert "NOT a category entity of its own" in CANONICAL_CODE_ADDENDUM
    assert "NEVER invent a code, complete a fragment, join two words into one, or fix a" in CANONICAL_CODE_ADDENDUM
    assert 'hint "product" only' in CANONICAL_CODE_ADDENDUM


def _migration():
    assert MIGRATION.exists(), "no migration publishes the canonical-code prompt, so it reaches no live version"
    spec = importlib.util.spec_from_file_location("zzt_canonical_code_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_migration_publishes_the_full_text_with_the_addendum():
    module = _migration()
    assert callable(module.publish)
    assert module.revision == "chatbot_canonical_code"
    assert len(module.revision) <= 32
    full = module._full_text()
    assert full == SEMANTIC_PARSER_PROMPT
    assert CANONICAL_CODE_ADDENDUM in full


def test_the_migration_publishes_once_and_downgrades_only_its_own_row(session_factory):
    from app.models.ai_prompt import AIPromptVersion

    module = _migration()
    db = session_factory()
    db.add(
        AIPromptVersion(name="chatbot_semantic_parser", version=901, type="text", template="an earlier body", variables=[])
    )
    db.flush()

    first = module.publish(db)
    second = module.publish(db)

    assert first["full"] == 902
    assert second["full"] is None, "idempotent on template equality"
    rows = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").all()
    assert sorted(r.version for r in rows) == [901, 902]

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
