"""CHAT-SPARSE: `chs_0001_parser_sparse_output` publishes the GROUPED OUTPUT parser prompt
on the production line and on the code-constant line, unlabelled, and moves no label.

Postgres only (tests._pg_fixture).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import GROUPED_OUTPUT_MARKER, grouped_prompt
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "chs_0001_parser_sparse_output.py"
#: The v52-derived text, standing in for whatever the production label points at.
PROD_TEXT = (BACKEND / "alembic" / "data" / "chatbot_semantic_parser.candidate-20261010.txt").read_text(
    encoding="utf-8"
)
PROMPT_NAME = "chatbot_semantic_parser"


def _module():
    spec = importlib.util.spec_from_file_location("zzt_chs_0001_parser_sparse_output", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version)
        .all()
    )


def _labels(db) -> list[tuple[str, str]]:
    return [(l.label, l.version_id) for l in db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME)]


def _production(db, template: str = PROD_TEXT, version: int = 52) -> AIPromptVersion:
    row = AIPromptVersion(
        name=PROMPT_NAME, version=version, type="text", template=template, variables=["current_date", "x"]
    )
    db.add(row)
    db.flush()
    db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=row.id))
    db.flush()
    return row


def test_revision_id_fits_and_chains() -> None:
    module = _module()
    assert module.revision == "chs_0001_parser_sparse_output"
    assert len(module.revision) <= 32
    assert module.down_revision == "rkind_0003_reference_kind_rows"


def test_publishes_the_production_line_and_the_constant_line_unlabelled() -> None:
    module = _module()
    with blank_session() as db:
        prod = _production(db)
        before = _labels(db)

        assert module.publish(db) == [53, 54]

        rows = {r.version: r for r in _versions(db)}
        assert rows[53].template == grouped_prompt(PROD_TEXT)
        assert rows[53].variables == ["current_date", "x"]
        assert rows[53].config_json == {module.MARKER_KEY: "production", "built_on": "v52"}
        template, _ = module._load_s4()._body(db)
        assert rows[54].template == grouped_prompt(template)
        assert rows[54].config_json[module.MARKER_KEY] == "code constant"
        assert all(GROUPED_OUTPUT_MARKER in rows[v].template for v in (53, 54))
        assert rows[52].template == PROD_TEXT
        assert _labels(db) == before == [("production", prod.id)]


def test_publishing_twice_adds_nothing() -> None:
    module = _module()
    with blank_session() as db:
        _production(db)
        assert len(module.publish(db)) == 2
        assert module.publish(db) == []
        assert len(_versions(db)) == 3


def test_a_production_text_it_cannot_group_is_skipped_not_failed() -> None:
    module = _module()
    with blank_session() as db:
        _production(db, template="a prompt with no output block {{current_date}}")
        published = module.publish(db)
        assert len(published) == 1
        (row,) = [r for r in _versions(db) if r.version in published]
        assert row.config_json[module.MARKER_KEY] == "code constant"


def test_an_already_grouped_production_publishes_only_the_constant_line() -> None:
    module = _module()
    with blank_session() as db:
        _production(db, template=grouped_prompt(PROD_TEXT))
        assert len(module.publish(db)) == 1


def test_downgrade_deletes_only_its_unlabelled_rows(monkeypatch) -> None:
    module = _module()
    with blank_session() as db:
        prod = _production(db)
        module.publish(db)
        promoted = [r for r in _versions(db) if (r.config_json or {}).get(module.MARKER_KEY) == "production"][0]
        db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).delete()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=promoted.id))
        db.add(AIPromptLabel(name=PROMPT_NAME, label="previous", version_id=prod.id))
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        module.downgrade()
        db.commit()
        db.expire_all()
        assert [r.version for r in _versions(db)] == [52, 53]
