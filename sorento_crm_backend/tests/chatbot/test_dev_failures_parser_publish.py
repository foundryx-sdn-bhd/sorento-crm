"""DEV-FAILURES: the publish migration for main's v56 parser plus the RC-A and RC-B addenda
(documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

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
BASE_FILE = DATA / "chatbot_semantic_parser.ablation-20261011.txt"
ADDENDUM_FILE = DATA / "chatbot_semantic_parser.dev-failures.addendum.txt"
PORTAL_FORM_FILE = DATA / "chatbot_semantic_parser.portal-form.addendum.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "devf_0001_parser_data_rules.py"
PROMPT_NAME = "chatbot_semantic_parser"

RULES = [
    "WHEN a product will have stock is an arrival question",
    "Stock and last receipt of a product in one message are two asks",
    "Whether a customer ordered a product",
    "WHEN a product will be delivered or sent out",
    "A promotion word decides the domain over a generic paper word",
    "Requesting price tags (not the template file) is portal_link",
    "A portal_link ask starts a new subject",
    'Two brands joined by "and" are two brand entities',
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
    assert module.down_revision == "rkind_0002_parser_ref_kinds_v56"
    assert len(module.revision) <= 32


def test_body_is_v56_then_both_addenda() -> None:
    body = _module().body()
    base = BASE_FILE.read_text(encoding="utf-8")
    addenda = ADDENDUM_FILE.read_text(encoding="utf-8") + PORTAL_FORM_FILE.read_text(encoding="utf-8")
    assert body == base.rstrip("\n") + addenda
    assert BLOCKS_BEGIN not in body
    assert body.count("{{current_date}}") == 1


@pytest.mark.parametrize("rule", RULES)
def test_addendum_carries_each_rule(rule: str) -> None:
    assert rule in ADDENDUM_FILE.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [ADDENDUM_FILE, PORTAL_FORM_FILE], ids=["rc_a", "rc_b"])
def test_addendum_is_minimal_business_rules(path: Path) -> None:
    """Owner 12 Oct (#1542 audit): one short rule per gap, at most one English example per rule,
    no word lists, no Chinese or Malay; and no dashes the repo forbids."""
    text = path.read_text(encoding="utf-8")
    rules = [line for line in text.splitlines() if line.startswith("- ")]
    assert rules and all(len(line) <= 160 for line in rules)
    assert all(line.count("Example:") <= 1 for line in rules)
    assert not re.search(r"[㐀-鿿]", text)
    assert "\u2014" not in text and "\u2013" not in text


def test_addenda_stay_small() -> None:
    total = len(ADDENDUM_FILE.read_text(encoding="utf-8")) + len(PORTAL_FORM_FILE.read_text(encoding="utf-8"))
    assert total <= 1300


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
        prev = AIPromptVersion(name=PROMPT_NAME, version=56, type="text", template="live", variables=[])
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


def test_stock_plus_last_in_keeps_the_message_order() -> None:
    """Crew gate 12 Oct (spo_allocation.last_in -20 on ed5751be): the two-asks rule must not fix
    an order of its own, or a combined ask loses domain_hint spo_allocation. It defers to ASKS."""
    line = next(l for l in ADDENDUM_FILE.read_text(encoding="utf-8").splitlines() if "last receipt" in l)
    assert "ordered as ASKS says" in line
    assert "then spo_allocation" not in line
