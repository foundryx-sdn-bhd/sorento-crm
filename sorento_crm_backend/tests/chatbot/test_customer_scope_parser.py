"""Phase 2 RED tests - the parser learns `self_reference` (D2).

`documentation/plans/chatbot/PLAN-chatbot-customer-scope-29sep.md` D2 and
`chatbot-customer-scope-29sep-acceptance-criteria.md` AC-CS-20 and AC-CS-21.

* AC-CS-20: the strict schema declares boolean `self_reference` (required, so the provider
  must answer it), `TOLERATED_ABSENT` lets an older prompt version omit it, and the prompt
  teaches it. `TOLERATED_ABSENT` lives in `head/parser.py` (the plan says `contracts`; that
  is where the docs put it, the code does not).
* AC-CS-21: the migration publishes the prompt carrying the key as a NEW version and leaves
  the `production` label where it is; a second `publish` is a no-op. The migration file is
  imported by path, the `487_chatbot_warehouse_cue` idiom (`tests/
  test_chatbot_warehouse_cue_migration.py`), on a blank Postgres schema.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot.head.parser import DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA, TOLERATED_ABSENT
from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT
from tests._pg_fixture import blank_session

MIGRATION_FILE = "chatbot_self_reference_vocab.py"
PROMPT_NAME = "chatbot_semantic_parser"


def test_self_reference_in_strict_schema_and_tolerated_absent() -> None:
    """AC-CS-20: a boolean key in the strict schema, declared and required, and exempt from
    the replay check so an older prompt version's emission is still accepted."""
    props = PARSE_OUTPUT_JSON_SCHEMA["properties"]
    assert "self_reference" in props, sorted(props)
    kind = props["self_reference"]["type"]
    assert "boolean" in (kind if isinstance(kind, list) else [kind]), kind
    assert "self_reference" in PARSE_OUTPUT_JSON_SCHEMA["required"]
    assert "self_reference" in DECLARED_KEYS
    assert "self_reference" in TOLERATED_ABSENT
    assert PARSE_OUTPUT_JSON_SCHEMA["additionalProperties"] is False


def test_prompt_teaches_self_reference() -> None:
    """AC-CS-20: the prompt names the key and teaches the first-person cues in English,
    Malay and Chinese, including "we" as the asker's own business."""
    for needle in ("self_reference", "saya punya", "kami punya", "我的", "我们的", "what did we order"):
        assert needle in SEMANTIC_PARSER_PROMPT, needle


def _load_migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / MIGRATION_FILE
    spec = importlib.util.spec_from_file_location("migration_under_test_chatbot_self_reference_vocab", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_label(session) -> AIPromptLabel | None:
    return (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def test_migration_publishes_a_new_version_label_unmoved() -> None:
    """AC-CS-21: the first `publish` returns the new version number, the second returns None
    (idempotent), the published template carries the key, and the `production` label is
    still on the version it was on before."""
    module = _load_migration()
    assert len(module.revision) <= 32, module.revision
    with blank_session() as session:
        from app.services.ai_prompt_registry import PROMPT_KEYS
        from app.services.ai_prompt_seed import seed_prompt_registry

        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=1,
                type="text",
                template="STALE PROMPT TEXT (before self_reference)",
                variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
            )
        )
        session.commit()
        seed_prompt_registry(session.get_bind())
        label_before = _production_label(session)
        assert label_before is not None
        version_before = label_before.version_id

        first = module.publish(session)
        assert isinstance(first, int) and first >= 2, first
        assert module.publish(session) is None

        published = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
            .one()
        )
        assert "self_reference" in published.template
        count = session.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).count()
        assert count == 2, count

        session.expire_all()
        label_after = _production_label(session)
        assert label_after is not None and label_after.version_id == version_before, (
            "publish() must never move the production label"
        )

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
