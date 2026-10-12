"""MULTI-ASK-PLAN2: `maskp_0001_parser_sub_asks.publish` on main's trimmed parser base.

The new version is the PROMPT-ABLATION text (`pab_0001_parser_ablation`, dev v56) with
SUB_ASKS_ADDENDUM right before its MEMORY block, published as a NEW UNLABELLED version:

* any existing versions -> the next version number, the production label unmoved;
* run again -> nothing (idempotent);
* the downgrade drops only its own unlabelled row.

Postgres only, blank schema.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, SUB_ASKS_ADDENDUM

from tests._pg_fixture import blank_session

NAME = "chatbot_semantic_parser"
BACKEND = Path(__file__).resolve().parents[2]
ABLATION_FILE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.refkinds-v56-20261011.txt"


def _migration():
    path = BACKEND / "alembic" / "versions" / "maskp_0001_parser_sub_asks.py"
    spec = importlib.util.spec_from_file_location("_maskp_0001_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db):
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == NAME).order_by(AIPromptVersion.version).all()


def _base() -> str:
    return ABLATION_FILE.read_text(encoding="utf-8")


def test_revision_ids_chain_onto_mains_head():
    m = _migration()
    assert m.revision == "maskp_0001_parser_sub_asks"
    assert m.down_revision == "scope_seeds_debtor_company"
    assert len(m.revision) <= 32


def test_the_frozen_addendum_is_the_code_constant():
    """Editing SUB_ASKS_ADDENDUM needs a new publish revision: this revision's text is frozen."""
    assert _migration().ADDENDUM.read_text(encoding="utf-8") == SUB_ASKS_ADDENDUM


def test_it_publishes_the_ablation_text_plus_the_addendum_unlabelled_once():
    m = _migration()
    base = _base()
    assert base.count(m.MEMORY_ANCHOR) == 1
    with blank_session() as db:
        prod = AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=60, type="text", template="v60 text", variables=[])
        db.add(prod)
        db.flush()
        db.add(AIPromptLabel(name=NAME, label="production", version_id=prod.id))
        db.flush()
        assert m.publish(db) == 61
        new = _versions(db)[-1]
        devf = "".join(a.read_text(encoding="utf-8") for a in m.DEV_FAILURES_ADDENDA)
        assert new.template == base.replace(m.MEMORY_ANCHOR, SUB_ASKS_ADDENDUM + m.MEMORY_ANCHOR).rstrip("\n") + devf
        # One candidate holds every v56 addendum on main: REF-PROBES, DEV-FAILURES and this PR's.
        assert "REFERENCE KINDS" in new.template and "portal_form" in new.template
        assert new.variables == ["current_date"]
        assert new.commit_message == m.MESSAGE
        assert BLOCKS_BEGIN not in new.template
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == NAME).all()
        assert [(lbl.label, lbl.version_id) for lbl in labels] == [("production", prod.id)]
        assert m.publish(db) is None
        assert len(_versions(db)) == 2


def test_it_never_edits_the_old_production_text():
    """Rebuilt on the v56 base, never on v60 (owner, 12 Oct 2026): whatever production holds,
    the published text is the ablation base plus the addendum."""
    m = _migration()
    with blank_session() as db:
        prod = AIPromptVersion(
            id=str(uuid.uuid4()), name=NAME, version=3, type="text",
            template="OLD" + m.MEMORY_ANCHOR + "old memory", variables=[],
        )
        db.add(prod)
        db.flush()
        db.add(AIPromptLabel(name=NAME, label="production", version_id=prod.id))
        db.flush()
        m.publish(db)
        assert not _versions(db)[-1].template.startswith("OLD")


def test_the_downgrade_drops_only_its_own_unlabelled_row(monkeypatch):
    m = _migration()
    with blank_session() as db:
        assert m.publish(db) == 1
        labelled = AIPromptVersion(
            id=str(uuid.uuid4()), name=NAME, version=2, type="text", template="b", variables=[], commit_message=m.MESSAGE
        )
        other = AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=3, type="text", template="c", variables=[])
        db.add_all([labelled, other])
        db.flush()
        db.add(AIPromptLabel(name=NAME, label="production", version_id=labelled.id))
        db.commit()
        monkeypatch.setattr(m, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        m.downgrade()
        db.commit()
        db.expire_all()
        assert [v.version for v in _versions(db)] == [2, 3]


# ---- crew live check 13 Oct 2026: the version crew pins is on PRODUCTION's line (prod v52),
# so the migration also publishes the production text plus the addendum, unlabelled.


def _production(db, template: str, version: int = 52) -> AIPromptVersion:
    row = AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=version, type="text", template=template, variables=["current_date"])
    db.add(row)
    db.flush()
    db.add(AIPromptLabel(name=NAME, label="production", version_id=row.id))
    db.flush()
    return row


def test_the_production_line_version_puts_the_addendum_before_memory_once():
    m = _migration()
    with blank_session() as db:
        prod = _production(db, "PROD {{domains}}" + m.MEMORY_ANCHOR + "memory")
        version = m.publish_on_production(db)
        new = next(v for v in _versions(db) if v.version == version)
        assert new.template == "PROD {{domains}}" + SUB_ASKS_ADDENDUM + m.MEMORY_ANCHOR + "memory"
        assert new.commit_message == m.PROD_MESSAGE
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == NAME).all()
        assert [(lbl.label, lbl.version_id) for lbl in labels] == [("production", prod.id)]
        assert m.publish_on_production(db) is None


def test_the_production_line_falls_back_to_the_policy_blocks_anchor():
    m = _migration()
    with blank_session() as db:
        tail = m.BLOCKS_ANCHOR + "\n{{domains_detail}}"
        _production(db, "PROD" + tail)
        version = m.publish_on_production(db)
        assert next(v for v in _versions(db) if v.version == version).template == "PROD" + SUB_ASKS_ADDENDUM + tail


def test_a_production_text_with_no_anchor_fails_loudly():
    import pytest

    m = _migration()
    with blank_session() as db:
        _production(db, "PROD with no anchor")
        with pytest.raises(RuntimeError, match="no place to go"):
            m.publish_on_production(db)


def test_no_production_label_publishes_nothing_on_the_production_line():
    m = _migration()
    with blank_session() as db:
        assert m.publish_on_production(db) is None


def test_a_production_text_with_an_older_sub_asks_block_gets_no_second_one():
    """Review N1: a promoted r7 text already carries a SUB ASKS AND FAMILY block."""
    m = _migration()
    with blank_session() as db:
        _production(db, "PROD\n\nSUB ASKS AND FAMILY\nold r7 rules" + m.MEMORY_ANCHOR + "memory")
        assert m.publish_on_production(db) is None
