"""MULTI-ASK-PLAN S2: `maskp_0001_parser_sub_asks.publish`, its three branches (review S6).

* production carries PO_REFS_ADDENDUM and no literal registry list -> a new UNLABELLED
  version: the production text with SUB_ASKS_ADDENDUM right after PO_REFS_ADDENDUM;
* run again -> nothing (idempotent);
* no PO_REFS_ADDENDUM -> the addendum goes right before the MEMORY block;
* production older than the wording layer's own version (a fresh database) -> nothing;
  neither anchor -> RuntimeError. The label never moves.

Postgres only, blank schema.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import PO_REFS_ADDENDUM, SUB_ASKS_ADDENDUM

from tests._pg_fixture import blank_session

NAME = "chatbot_semantic_parser"


def _migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "maskp_0001_parser_sub_asks.py"
    spec = importlib.util.spec_from_file_location("_maskp_0001_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production(db, template: str) -> AIPromptVersion:
    row = AIPromptVersion(id=str(uuid.uuid4()), name=NAME, version=7, type="text", template=template, variables=[])
    db.add(row)
    db.flush()
    db.add(AIPromptLabel(name=NAME, label="production", version_id=row.id))
    db.flush()
    return row


def _versions(db):
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == NAME).order_by(AIPromptVersion.version).all()


def test_it_publishes_an_unlabelled_edit_of_production_once():
    m = _migration()
    with blank_session() as db:
        prod = _production(db, "HEAD {{domains}}" + PO_REFS_ADDENDUM + "TAIL")
        assert m.publish(db) == 8
        new = _versions(db)[-1]
        assert new.version == 8
        assert new.template == "HEAD {{domains}}" + PO_REFS_ADDENDUM + SUB_ASKS_ADDENDUM + "TAIL"
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == NAME).all()
        assert [(lbl.label, lbl.version_id) for lbl in labels] == [("production", prod.id)]
        assert m.publish(db) is None
        assert len(_versions(db)) == 2


def test_a_production_version_older_than_the_wording_layer_publishes_nothing():
    m = _migration()
    with blank_session() as db:
        _production(db, "domain_hint = ONE of: order | inventory | incoming" + PO_REFS_ADDENDUM)
        db.add(
            AIPromptVersion(
                id=str(uuid.uuid4()), name=NAME, version=9, type="text", template="LAYER", variables=[],
                config_json={"wording_layer_report": []},
            )
        )
        db.flush()
        assert m.publish(db) is None
        assert len(_versions(db)) == 2


def test_a_production_newer_than_the_wording_layer_publishes_even_with_a_kept_literal_list():
    """`pdyn_0003_prod_identical` keeps a list literal where the registry differs, so a
    literal list alone never stops the publish (crew live check on dev v60)."""
    m = _migration()
    with blank_session() as db:
        db.add(
            AIPromptVersion(
                id=str(uuid.uuid4()), name=NAME, version=4, type="text", template="LAYER", variables=[],
                config_json={"wording_layer_report": []},
            )
        )
        db.flush()
        _production(db, "domain_hint = ONE of: order | inventory | incoming" + m.MEMORY_ANCHOR + "m")
        assert m.publish(db) == 8


def test_a_production_text_without_po_refs_gets_the_addendum_before_the_memory_block():
    """Crew live check, 9 Oct 2026: dev's production text (v60) has no PO_REFS_ADDENDUM;
    the addendum then goes right before the MEMORY block, which stays the tail."""
    m = _migration()
    with blank_session() as db:
        _production(db, "HEAD {{domains}}" + m.MEMORY_ANCHOR + "memory body")
        assert m.publish(db) == 8
        new = _versions(db)[-1]
        assert new.template == "HEAD {{domains}}" + SUB_ASKS_ADDENDUM + m.MEMORY_ANCHOR + "memory body"


def test_a_production_text_with_no_anchor_fails_loudly():
    import pytest

    m = _migration()
    with blank_session() as db:
        _production(db, "HEAD {{domains}} TAIL")
        with pytest.raises(RuntimeError, match="no place to go"):
            m.publish(db)
        assert len(_versions(db)) == 1
