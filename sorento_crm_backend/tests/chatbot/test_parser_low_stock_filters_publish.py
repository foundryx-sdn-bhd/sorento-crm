"""LOWSTOCK-SEMANTIC: the low stock filters reach a LIVE prompt version only through a publish.

`ai_prompt_registry.render()` reads the PUBLISHED `chatbot_semantic_parser` row, never the
Python constant (migration 514's lesson, `test_parser_low_stock_publish.py`), so
`LOW_STOCK_FILTERS_ADDENDUM` reaches no customer until `lss_0001_parser_vocab` publishes it
and the owner moves the `production` label. Pinned here: the migration chains onto the
current head, publishes ONE version by the s4 formula (constant plus policy blocks: a bare
constant would lose the blocks the moment it was promoted), is idempotent, and moves no
label.

Postgres only (`tests/_pg_fixture.py::blank_session`): the publish writes rows.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, LOW_STOCK_FILTERS_ADDENDUM
from tests._pg_fixture import blank_session
from tests.chatbot.test_parser_growth_r1_reachability import _alembic_heads_excluding

MIGRATION = "lss_0001_parser_vocab.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _module():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / MIGRATION
    spec = importlib.util.spec_from_file_location("zzt_lss_0001_parser_vocab", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def test_the_revision_chains_onto_the_current_head() -> None:
    module = _module()
    assert len(module.revision) <= 32, module.revision
    heads = _alembic_heads_excluding(module.revision)
    assert module.down_revision in heads, (module.down_revision, sorted(heads))


def test_publish_adds_one_version_with_the_filters_and_the_blocks_and_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        first = module.publish(db)
        assert first is not None
        (row,) = _versions(db)
        assert LOW_STOCK_FILTERS_ADDENDUM.strip() in row.template
        assert '"low_stock"' in row.template and "per vendor" in row.template
        assert BLOCKS_BEGIN in row.template, "the s4 formula: the policy blocks ride with the constant"
        assert (row.config_json or {}).get(module.MARKER_KEY) is True
        assert module.publish(db) is None
        assert len(_versions(db)) == 1, "a second call published a duplicate"


def test_publish_moves_no_label() -> None:
    module = _module()
    with blank_session() as db:
        previous = AIPromptVersion(name=PROMPT_NAME, version=1, type="text", template="live before", variables=[])
        db.add(previous)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=previous.id))
        db.commit()
        assert module.publish(db) == 2
        label = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME,
                                               AIPromptLabel.label == "production").one()
        assert label.version_id == previous.id, "promoting is the owner's step, never the migration's"
