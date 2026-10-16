"""PROMPT-DIET stage 1 AC15: the publish migration for the diet-s1 parser text.

Red until `alembic/versions/pdiet_s1_0001_parser_diet_s1.py` and the output data file exist.
Postgres only (tests._pg_fixture).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
OUT_FILE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.diet-s1-20261016.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "pdiet_s1_0001_parser_diet_s1.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _text() -> str:
    if not OUT_FILE.exists():
        pytest.fail(f"built file missing: {OUT_FILE}")
    return OUT_FILE.read_text(encoding="utf-8")


def _module():
    if not MIGRATION.exists():
        pytest.fail(f"migration missing: {MIGRATION}")
    spec = importlib.util.spec_from_file_location("zzt_pdiet_s1_0001", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def test_ac15_revision_id() -> None:
    module = _module()
    assert module.revision == "pdiet_s1_0001"
    assert len(module.revision) <= 32


def test_ac15_publish_on_empty_table() -> None:
    module = _module()
    text = _text()
    with blank_session() as db:
        assert module.publish(db) == 1
        (row,) = _versions(db)
        assert row.template == text
        assert row.variables == ["current_date", "find_filters"]
        assert "PROMPT-DIET stage 1" in row.commit_message
        assert row.commit_message == module.MESSAGE


def test_ac15_next_version_and_labels_untouched() -> None:
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


def test_ac15_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_ac15_downgrade_removes_only_its_unlabelled_row(monkeypatch) -> None:
    module = _module()
    with blank_session() as db:
        mine = AIPromptVersion(
            name=PROMPT_NAME, version=1, type="text", template="a", variables=[], commit_message=module.MESSAGE
        )
        labelled = AIPromptVersion(
            name=PROMPT_NAME, version=2, type="text", template="b", variables=[], commit_message=module.MESSAGE
        )
        other = AIPromptVersion(
            name=PROMPT_NAME, version=3, type="text", template="c", variables=[], commit_message="other"
        )
        db.add_all([mine, labelled, other])
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=labelled.id))
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        module.downgrade()
        db.commit()
        db.expire_all()
        assert sorted(r.version for r in _versions(db)) == [2, 3]


# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
