"""DEV-FAILURES RC-A: the publish migration for v50 plus the "which data answers the question"
addendum (documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Postgres only (tests._pg_fixture). The live parser check of the same rules is
`test_dev_failures_live_parser.py` (opt-in, never in CI).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
DATA = BACKEND / "alembic" / "data"
V50_FILE = DATA / "chatbot_semantic_parser.v50.txt"
ADDENDUM_FILE = DATA / "chatbot_semantic_parser.dev-failures.addendum.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "devf_0001_parser_data_rules.py"
PROMPT_NAME = "chatbot_semantic_parser"

RULE_HEADERS = [
    '== "WHEN WILL THERE BE STOCK" IS AN ETA ASK ==',
    "== STOCK PLUS LAST IN IS TWO ASKS ==",
    "== DID A CUSTOMER ORDER A PRODUCT ==",
    "== WHEN WILL A CUSTOMER'S PRODUCT BE DELIVERED ==",
    "== A BRAND'S PROMOTION FILE IS A PROMOTION ASK ==",
    "== A PRICE TAG REQUEST IS A PORTAL SUBMISSION ==",
    "== A PORTAL SUBMISSION STARTS A NEW SUBJECT ==",
    '== TWO BRANDS JOINED BY "AND" ARE TWO BRAND ENTITIES ==',
]


def _module():
    spec = importlib.util.spec_from_file_location("zzt_devf_0001_parser_data_rules", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def test_revision_ids() -> None:
    module = _module()
    assert module.revision == "devf_0001_parser_data_rules"
    assert module.down_revision == "family_code_0001"
    assert len(module.revision) <= 32


def test_body_is_v50_then_addendum() -> None:
    body = _module().body()
    v50 = V50_FILE.read_text(encoding="utf-8")
    assert body.startswith(v50.rstrip("\n"))
    assert body.endswith(ADDENDUM_FILE.read_text(encoding="utf-8"))
    assert BLOCKS_BEGIN not in body
    assert body.count("{{current_date}}") == v50.count("{{current_date}}")


@pytest.mark.parametrize("header", RULE_HEADERS)
def test_addendum_carries_each_rule(header: str) -> None:
    assert header in ADDENDUM_FILE.read_text(encoding="utf-8")


def test_addendum_teaches_no_language() -> None:
    """Owner rule 11 Oct (CARD-prompt-ablation-11oct.md): business facts, not language
    teaching, so the addendum carries no Chinese or Malay phrase lists and no dashes the
    repo forbids."""
    text = ADDENDUM_FILE.read_text(encoding="utf-8")
    assert not re.search(r"[㐀-鿿]", text)
    for word in ("bila", "ada stok", "hantar", "belum", "sudah"):
        assert word not in text.lower()
    assert "—" not in text and "–" not in text


def test_publish_on_empty_table() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) == 1
        (row,) = _versions(db)
        assert row.template == module.body()
        assert row.variables == ["current_date"]
        assert row.commit_message == module.MESSAGE


def test_next_version_and_no_label_moves() -> None:
    module = _module()
    with blank_session() as db:
        prev = AIPromptVersion(name=PROMPT_NAME, version=50, type="text", template="live", variables=[])
        db.add(prev)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=prev.id))
        db.commit()
        assert module.publish(db) == 51
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert [(l.label, l.version_id) for l in labels] == [("production", prev.id)]


def test_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_downgrade_deletes_only_unlabelled_rows_of_this_message(monkeypatch) -> None:
    module = _module()
    with blank_session() as db:
        mine = AIPromptVersion(name=PROMPT_NAME, version=1, type="text", template="a", variables=[],
                               commit_message=module.MESSAGE)
        labelled = AIPromptVersion(name=PROMPT_NAME, version=2, type="text", template="b", variables=[],
                                   commit_message=module.MESSAGE)
        other = AIPromptVersion(name=PROMPT_NAME, version=3, type="text", template="c", variables=[],
                                commit_message="other")
        db.add_all([mine, labelled, other])
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=labelled.id))
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        module.downgrade()
        db.commit()
        db.expire_all()
        assert sorted(r.version for r in _versions(db)) == [2, 3]
