"""C5 (PLAN-ideation-capture-02oct section 4a-bis): an intent-only message such as
"want to submit idea" has NO problem. The rule ships as the fallback text of
``ideate_extractor`` and one migration publishes it to the production registry row,
without overwriting a template the owner edited.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import pathlib

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry
from tests._pg_fixture import blank_session

VERSIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
REVISION = "ideation_capture_0001"
NAME = "ideate_extractor"
NO_IDEA_PHRASE = "Never invent a problem for a message that names no idea"
# sha256 of .strip().encode() of the stock ideate_extractor text, computed by exec-ing
# _ideate_extractor_fallback() out of `git show <rev>:sorento_crm_backend/app/services/ai_prompt_registry.py`.
# MAIN = origin/main (101fda82b), what production holds; no "- language:" line.
MAIN_SHA256 = "7c414ddc9ace3b7fc1da49b9f458a36ae41b27da4fbe68cd2ff4292e993c609a"
# FB88 = fb88209b, the lane's C1 text (has "- language:"); the old OLD_IDEATE_EXTRACTOR.
FB88_SHA256 = "7127890ce450520c0ff95cbd01fdb724242826182bff67880e2b2a8fb221e018"


def _fallback() -> str:
    return ai_prompt_registry.PROMPT_KEYS[NAME].fallback()


def _assign(src: str, name: str):
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def _migration_path() -> pathlib.Path:
    found = [
        p
        for p in VERSIONS_DIR.glob("*.py")
        if _assign(p.read_text(encoding="utf-8"), "revision") == REVISION
    ]
    assert len(found) == 1, f"expected exactly one migration with revision {REVISION}"
    return found[0]


def _load_migration():
    path = _migration_path()
    spec = importlib.util.spec_from_file_location("_ideation_capture_0001_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- B1: fallback text -------------------------------------------------------


def test_fallback_has_intent_only_rule():
    text = _fallback()
    assert "want to submit idea" in text
    assert NO_IDEA_PHRASE in text
    assert "Never write a problem the user did not state" not in text


def test_fallback_drops_always_emit_problem_contradiction():
    text = _fallback()
    assert "ALWAYS emit problem" not in text
    assert "every draft has one from its very first message" not in text


def test_fallback_stated_idea_still_gets_need_behind_it_as_problem():
    text = _fallback()
    assert "We need our own production line." in text
    assert "need behind it" in text


def test_no_problem_rule_sits_inside_problem_field_description():
    text = _fallback()
    start = text.index("- problem:")
    end = text.index("- proposed_solution:")
    assert start < end
    for needle in ("want to submit idea", NO_IDEA_PHRASE):
        assert start < text.index(needle) < end, f"{needle!r} must be inside the problem field"
    assert "NO-PROBLEM RULE:" not in text, "one instruction for problem, not a second paragraph"


# --- S2: migration identity --------------------------------------------------


def test_migration_is_single_head_and_upgrade_wires_seed_and_publish():
    path = _migration_path()
    src = path.read_text(encoding="utf-8")
    revision = _assign(src, "revision")
    assert isinstance(revision, str) and 0 < len(revision) <= 32

    for p in VERSIONS_DIR.glob("*.py"):
        if p == path:
            continue
        down = _assign(p.read_text(encoding="utf-8"), "down_revision")
        downs = list(down) if isinstance(down, (tuple, list)) else [down]
        assert REVISION not in downs, f"{p.name} sits on top of {REVISION}: not the head"

    upgrade = next(
        n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    called = {
        n.func.id
        for n in ast.walk(upgrade)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "seed_prompt_registry" in called
    assert "publish_ideate_extractor" in called


def test_stock_texts_are_main_and_fb88209b_verbatim():
    texts = _load_migration().STOCK_IDEATE_EXTRACTOR_TEXTS
    assert isinstance(texts, tuple) and len(texts) == 2
    hashes = [hashlib.sha256(t.strip().encode()).hexdigest() for t in texts]
    assert hashes == [MAIN_SHA256, FB88_SHA256]
    assert "- language:" not in texts[0] and "- language:" in texts[1]
    for t in texts:
        assert t.strip() != _fallback().strip()


# --- S1/S3: owner-edit guard -------------------------------------------------


@pytest.fixture
def db():
    with blank_session() as session:
        try:
            ai_prompt_registry.bust_cache()
            yield session
        finally:
            ai_prompt_registry.bust_cache()


def _seed_production(db, template: str | None):
    """v1 holding ``template`` and a production label on it; None means no label."""
    v1 = AIPromptVersion(
        name=NAME, version=1, type="text", template=template, variables=[]
    )
    db.add(v1)
    db.flush()
    if template is not None:
        db.add(AIPromptLabel(name=NAME, label="production", version_id=v1.id))
        db.flush()
    return v1


def _state(db):
    db.expire_all()
    versions = (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.name == NAME)
        .order_by(AIPromptVersion.version)
        .all()
    )
    label = (
        db.query(AIPromptLabel)
        .filter(AIPromptLabel.name == NAME, AIPromptLabel.label == "production")
        .first()
    )
    current = None
    if label is not None:
        current = db.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).one()
    return versions, label, current


@pytest.mark.parametrize("idx", [0, 1], ids=["main_text", "fb88209b_text"])
def test_stock_old_production_is_republished_with_new_fallback(db, idx):
    mig = _load_migration()
    _seed_production(db, mig.STOCK_IDEATE_EXTRACTOR_TEXTS[idx])
    mig.publish_ideate_extractor(db.connection())
    versions, label, current = _state(db)
    assert [v.version for v in versions] == [1, 2]
    assert current.version == 2
    assert current.template == _fallback()


@pytest.mark.parametrize("idx", [0, 1], ids=["main_text", "fb88209b_text"])
def test_old_with_whitespace_difference_still_counts_as_stock(db, idx):
    mig = _load_migration()
    _seed_production(db, mig.STOCK_IDEATE_EXTRACTOR_TEXTS[idx] + "\n\n")
    mig.publish_ideate_extractor(db.connection())
    versions, _label, current = _state(db)
    assert len(versions) == 2
    assert current.template == _fallback()


def test_owner_edited_production_is_left_alone_and_warned(db, caplog):
    mig = _load_migration()
    edited = "OWNER EDITED ideate prompt, placeholder text."
    v1 = _seed_production(db, edited)
    with caplog.at_level(logging.WARNING):
        mig.publish_ideate_extractor(db.connection())
    versions, label, current = _state(db)
    assert len(versions) == 1
    assert label.version_id == v1.id
    assert current.template == edited
    assert any(r.levelno >= logging.WARNING for r in caplog.records), "expected a warning log"


def test_production_already_equal_to_new_fallback_is_unchanged(db, caplog):
    mig = _load_migration()
    v1 = _seed_production(db, _fallback())
    with caplog.at_level(logging.WARNING):
        mig.publish_ideate_extractor(db.connection())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    versions, label, _current = _state(db)
    assert len(versions) == 1
    assert label.version_id == v1.id


def test_no_production_label_changes_nothing(db):
    mig = _load_migration()
    _seed_production(db, None)
    mig.publish_ideate_extractor(db.connection())
    versions, label, _current = _state(db)
    assert len(versions) == 1
    assert label is None


def test_empty_registry_changes_nothing(db):
    mig = _load_migration()
    mig.publish_ideate_extractor(db.connection())
    versions, label, _current = _state(db)
    assert versions == []
    assert label is None


@pytest.mark.parametrize("idx", [0, 1], ids=["main_text", "fb88209b_text"])
def test_second_call_adds_no_extra_version(db, idx):
    mig = _load_migration()
    _seed_production(db, mig.STOCK_IDEATE_EXTRACTOR_TEXTS[idx])
    mig.publish_ideate_extractor(db.connection())
    mig.publish_ideate_extractor(db.connection())
    versions, _label, current = _state(db)
    assert [v.version for v in versions] == [1, 2]
    assert current.version == 2
