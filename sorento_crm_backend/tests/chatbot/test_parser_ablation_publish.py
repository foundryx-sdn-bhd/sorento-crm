"""PROMPT-ABLATION U12-U17: the publish migration for the trimmed parser prompt.

Red until `alembic/versions/pab_0001_parser_ablation.py` and the ablation data file exist.
Postgres only (tests._pg_fixture).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
ABLATION_FILE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.ablation-20261011.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "pab_0001_parser_ablation.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _text() -> str:
    return ABLATION_FILE.read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("zzt_pab_0001_parser_ablation", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def test_u17_revision_ids() -> None:
    module = _module()
    assert module.revision == "pab_0001_parser_ablation"
    assert module.down_revision == "chat_backfill_0001"
    assert len(module.revision) <= 32


def test_u12_publish_on_empty_table_is_verbatim() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) == 1
        (row,) = _versions(db)
        assert row.template == _text()
        assert BLOCKS_BEGIN not in row.template
        assert row.variables == ["current_date"]
        assert row.commit_message == module.MESSAGE
        assert row.name == PROMPT_NAME


def test_u12_u13_next_version_and_no_label_moves() -> None:
    module = _module()
    with blank_session() as db:
        prev = AIPromptVersion(name=PROMPT_NAME, version=7, type="text", template="live before", variables=[])
        db.add(prev)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=prev.id))
        db.commit()
        assert module.publish(db) == 8
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert [(l.label, l.version_id) for l in labels] == [("production", prev.id)]


def test_u14_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_u15_downgrade_deletes_only_unlabelled_message_rows(monkeypatch) -> None:
    module = _module()
    with blank_session() as db:
        unlabelled = AIPromptVersion(
            name=PROMPT_NAME, version=1, type="text", template="a", variables=[], commit_message=module.MESSAGE
        )
        labelled = AIPromptVersion(
            name=PROMPT_NAME, version=2, type="text", template="b", variables=[], commit_message=module.MESSAGE
        )
        other = AIPromptVersion(
            name=PROMPT_NAME, version=3, type="text", template="c", variables=[], commit_message="other"
        )
        db.add_all([unlabelled, labelled, other])
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=labelled.id))
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        module.downgrade()
        db.commit()
        db.expire_all()
        assert sorted(r.version for r in _versions(db)) == [2, 3]
