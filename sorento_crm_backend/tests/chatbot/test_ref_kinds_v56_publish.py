"""REF-PROBES: the reference kinds section rebuilt on the PROMPT-ABLATION parser text (v56).

`rkind_0001_parser_ref_kinds` publishes the code constant (v50 lineage) plus REF_KINDS. The
graded candidate for promotion is the ablation text (`pab_0001_parser_ablation`), so this lane
also publishes that text with the two sections it lacks, CHAT_REPORT_ADDENDUM and
REF_KINDS_ADDENDUM, placed beneath PO_REFS_ADDENDUM in the code's order. Label unmoved.

Red until `alembic/versions/rkind_0002_parser_ref_kinds_v56.py` and its data file exist.
Postgres only (tests._pg_fixture).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import (
    BLOCKS_BEGIN,
    CHAT_REPORT_ADDENDUM,
    PO_REFS_ADDENDUM,
    REF_KINDS_ADDENDUM,
)
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
DATA = BACKEND / "alembic" / "data"
ABLATION_FILE = DATA / "chatbot_semantic_parser.ablation-20261011.txt"
REF_KINDS_FILE = DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "rkind_0002_parser_ref_kinds_v56.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _text() -> str:
    return REF_KINDS_FILE.read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("zzt_rkind_0002_parser_ref_kinds_v56", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def test_data_file_is_the_ablation_text_plus_the_two_sections_beneath_po_refs() -> None:
    ablation = ABLATION_FILE.read_text(encoding="utf-8")
    assert ablation.count(PO_REFS_ADDENDUM) == 1
    expected = ablation.replace(
        PO_REFS_ADDENDUM, PO_REFS_ADDENDUM + CHAT_REPORT_ADDENDUM + REF_KINDS_ADDENDUM
    )
    assert _text() == expected


def test_data_file_carries_each_section_once_and_no_policy_blocks() -> None:
    text = _text()
    assert text.count(REF_KINDS_ADDENDUM) == 1
    assert text.count(CHAT_REPORT_ADDENDUM) == 1
    assert BLOCKS_BEGIN not in text
    assert text.index(PO_REFS_ADDENDUM) < text.index(CHAT_REPORT_ADDENDUM) < text.index(REF_KINDS_ADDENDUM)
    assert "{{current_date}}" in text


def test_revision_ids_chain_onto_rkind_0001() -> None:
    module = _module()
    assert module.revision == "rkind_0002_parser_ref_kinds_v56"
    assert module.down_revision == "rkind_0001_parser_ref_kinds"
    assert len(module.revision) <= 32


def test_publish_on_empty_table_is_verbatim() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) == 1
        (row,) = _versions(db)
        assert row.template == _text()
        assert row.variables == ["current_date"]
        assert row.commit_message == module.MESSAGE


def test_publish_is_the_next_version_and_moves_no_label() -> None:
    module = _module()
    with blank_session() as db:
        prev = AIPromptVersion(name=PROMPT_NAME, version=56, type="text", template="v56", variables=[])
        db.add(prev)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=prev.id))
        db.commit()
        assert module.publish(db) == 57
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert [(l.label, l.version_id) for l in labels] == [("production", prev.id)]


def test_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_downgrade_deletes_only_its_unlabelled_rows(monkeypatch) -> None:
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
