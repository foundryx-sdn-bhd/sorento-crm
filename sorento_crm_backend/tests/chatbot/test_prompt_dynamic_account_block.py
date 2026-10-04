"""PROMPT-DYNAMIC merged over #1432 ACCOUNT-LEDGER (2 Oct 2026): every variable version this
lane builds carries ACCOUNT_LEDGER_ADDENDUM (the `account` entity key), so promoting one
never drops it. PLAN-account-ledger-2oct "Coordination": whichever lands second re-applies
the block.

The owner's production text (1 Oct 2026) predates the block, so the block goes in once,
just before the policy blocks; a text that already carries it (an owner-edited block
included) is left as it is. The identity proofs compare against the owner's text plus
that block.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry, chatbot_prompt_vars as pv
from app.services.chatbot_parser_prompt import ACCOUNT_LEDGER_ADDENDUM, BLOCKS_BEGIN
from tests._pg_fixture import pg_session

KEY = "chatbot_semantic_parser"
BACKEND = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"
BLOCK = ACCOUNT_LEDGER_ADDENDUM.strip("\n")
HEADER = "\nCUSTOMER ACCOUNT NUMBER\n"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration(filename: str):
    return _load(BACKEND / "alembic" / "versions" / filename, f"_ab_{filename}")


def _script():
    return _load(BACKEND / "scripts" / "prompt_dynamic_identical_version.py", "_ab_identical_script")


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    ai_prompt_registry.bust_cache()
    yield
    pv.clear_cache()
    ai_prompt_registry.bust_cache()


def _source() -> str:
    return SNAPSHOT.read_text(encoding="utf-8")


def _source_with_block() -> str:
    """Built here by hand, not by the code under test: the block, a blank line, then the
    policy-block marker (the owner's text already has a blank line before the marker)."""
    src = _source()
    assert src.count(BLOCKS_BEGIN) == 1 and f"\n\n{BLOCKS_BEGIN}" in src
    return src.replace(BLOCKS_BEGIN, f"{BLOCK}\n\n{BLOCKS_BEGIN}", 1)


def _put_version(db, template: str) -> AIPromptVersion:
    top = db.execute(text("SELECT COALESCE(max(version), 0) FROM ai_prompt_versions WHERE name = :n"), {"n": KEY}).scalar()
    row = AIPromptVersion(id=str(uuid.uuid4()), name=KEY, version=int(top) + 1, template=template,
                          commit_message="t", config_json={})
    db.add(row)
    db.flush()
    return row


def _point_production_at(db, row: AIPromptVersion) -> None:
    updated = db.execute(
        text("UPDATE ai_prompt_labels SET version_id = :v WHERE name = :n AND label = 'production'"),
        {"v": row.id, "n": KEY},
    ).rowcount
    if not updated:
        db.add(AIPromptLabel(name=KEY, label="production", version_id=row.id))
    db.flush()


def _render(db, row_id: str) -> str:
    out, _ = ai_prompt_registry.render(db, KEY, current_date="D", override_version_id=row_id)
    return out


def _drop_wording_layers(db) -> None:
    for row in db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).all():
        if pv.is_wording_layer(row.template):
            db.query(AIPromptLabel).filter(AIPromptLabel.version_id == row.id).delete()
            db.delete(row)
    db.flush()


# --------------------------------------------------------------------------- #
# The helper
# --------------------------------------------------------------------------- #


def test_the_block_goes_in_once_just_before_the_policy_blocks():
    out = pv.with_account_block(_source())
    assert out == _source_with_block()
    assert out.count(HEADER) == 1
    assert pv.with_account_block(out) == out


@pytest.mark.parametrize(
    "gap",
    ["", "\n", "\n\n"],
    ids=["no_blank_line", "one_blank_line", "two_blank_lines"],
)
def test_the_block_goes_immediately_before_the_marker_without_touching_the_owners_blank_lines(gap):
    src = f"A\n{gap}{BLOCKS_BEGIN}\nX\n<<<END CHATBOT POLICY BLOCKS>>>\n"
    assert pv.with_account_block(src) == src.replace(BLOCKS_BEGIN, f"{BLOCK}\n\n{BLOCKS_BEGIN}", 1)


def test_a_text_that_already_carries_the_block_keeps_its_own_wording():
    edited = _source_with_block().replace("Never guess a number.", "Never guess a number (owner edit).")
    assert pv.with_account_block(edited) == edited


def test_a_text_without_policy_blocks_gets_the_block_at_the_end():
    assert pv.with_account_block("Intro.\n\nRules.\n") == f"Intro.\n\nRules.\n\n{BLOCK}\n"


# --------------------------------------------------------------------------- #
# Every version the lane builds
# --------------------------------------------------------------------------- #


def test_the_prod_identical_version_carries_the_block_and_renders_the_file_plus_the_block():
    mod = _migration("pdyn_0003_prod_identical.py")
    with pg_session() as db:
        mod.remove(db.connection())
        _point_production_at(db, _put_version(db, _source()))
        version = mod.apply(db.connection())
        assert version is not None
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert row.template.count(HEADER) == 1
        assert _render(db, row.id) == _source_with_block().replace("{{current_date}}", "D")


def test_the_wording_layer_version_carries_the_block():
    mod = _migration("pdyn_0002_wording_layer.py")
    with pg_session() as db:
        _drop_wording_layers(db)
        _point_production_at(db, _put_version(db, _source()))
        version = mod.apply(db.connection())
        assert version is not None
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert row.template.count(HEADER) == 1
        assert f"{BLOCK}\n\n{BLOCKS_BEGIN}" in row.template


def test_a_rebuild_from_an_owner_version_carries_the_block_and_verifies_against_it():
    script = _script()
    with pg_session() as db:
        owner = _put_version(db, _source().replace("Sorento Semantic Parser", "Sorento Semantic Parser (owner edit)", 1))
        result = script.build(db, from_version=owner.version, save=True)
        assert result["identical"] is True
        saved = db.query(AIPromptVersion).filter(
            AIPromptVersion.name == KEY, AIPromptVersion.version == result["saved_version"]
        ).one()
        assert saved.template.count(HEADER) == 1
        assert script.verify(db, saved.version, against_version=owner.version)["equal"] is True
        # Without the block the rebuild would not be what the owner promotes.
        assert BLOCK in _render(db, saved.id)


def test_a_rebuild_keeps_an_owner_edited_block_and_adds_no_second_one():
    script = _script()
    with pg_session() as db:
        edited = _source_with_block().replace("Never guess a number.", "Never guess a number (owner edit).")
        owner = _put_version(db, edited)
        result = script.build(db, from_version=owner.version, save=True)
        assert result["identical"] is True
        saved = db.query(AIPromptVersion).filter(
            AIPromptVersion.name == KEY, AIPromptVersion.version == result["saved_version"]
        ).one()
        assert saved.template.count(HEADER) == 1
        assert "Never guess a number (owner edit)." in saved.template
