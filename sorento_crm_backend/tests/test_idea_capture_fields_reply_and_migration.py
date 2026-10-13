"""IDEA-CAPTURE (13 Oct): the canny-style fields, reply copy and migration.

UAC: documentation/plans/ideation/idea-capture-fields-13oct-acceptance-criteria.md

- AC-20..AC-22: ``ideation_capture_complete`` (en, ms, zh) lists each missing field on its own
  ``- <name>`` line; the no-link variant is unchanged.
- AC-30/AC-31: one migration, revision ``idea_capture_fields_0001``, publishes the new
  ``ideate_extractor`` fallback and the three ``ideation_capture_complete*`` texts only where
  production still equals a stock text. The test finds the migration by revision id.

Contract the migration module must expose (the coder follows the ideation_capture_0001 shape):

- ``STOCK_IDEATE_EXTRACTOR_TEXTS``: tuple of str, includes the pre-lane fallback (sha256 pinned
  below).
- ``publish(bind)``: does the four publishes; ``upgrade()`` calls ``seed_prompt_registry`` then
  ``publish``.
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
from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY
from app.services.ideation_capture_replies import render_reply
from tests._pg_fixture import blank_session

VERSIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
REVISION = "idea_capture_fields_0001"
PRIOR_REVISION = "ideation_capture_0001"
EXTRACTOR = "ideate_extractor"
# sha256 of .strip().encode() of the ideate_extractor fallback as it stood before this lane
# (commit 4b3daeae), i.e. what an unedited install holds on `production`.
PRE_LANE_EXTRACTOR_SHA256 = "d91d516ed532410d2fc01f63333fd20c59bff146e52025bf6ba82d7b47fb0ea0"

#: registry key -> (copy table short name, the stock text before this lane)
COMPLETE_KEYS = {
    "chatbot_reply_ideation_capture_complete": (
        "ideation_capture_complete",
        "Idea {{idea_number}} is in: {{title}}\n"
        "Open it to add what I didn't catch: {{missing}}.\n"
        "{{link}}",
    ),
    "chatbot_reply_ideation_capture_complete.ms": (
        "ideation_capture_complete.ms",
        "Idea {{idea_number}} sudah diterima: {{title}}\n"
        "Buka untuk menambah apa yang saya tidak tangkap: {{missing}}.\n"
        "{{link}}",
    ),
    "chatbot_reply_ideation_capture_complete.zh": (
        "ideation_capture_complete.zh",
        "创意 {{idea_number}} 已收到：{{title}}\n"
        "打开它补充我没有记录到的内容：{{missing}}。\n"
        "{{link}}",
    ),
}

LINK = "https://crm.example/ideas/0b1f6c1e"
MISSING_3 = ["Desired outcome", "Use cases", "Photos or files"]


def _facts(missing: list[str], link: str | None = LINK) -> dict:
    return {"idea_number": "IDEA-0123", "title": "Retyping WhatsApp orders into CRM",
            "link": link, "missing": missing}


# --------------------------------------------------------------------------- #
# AC-20 / AC-21 / AC-22 - the reply                                           #
# --------------------------------------------------------------------------- #
def test_ac20_en_complete_reply_lists_each_missing_field_on_its_own_line():
    reply = render_reply("complete", _facts(MISSING_3), user_message="x", language="en")
    assert reply == (
        "Idea IDEA-0123 is in: Retyping WhatsApp orders into CRM\n"
        "Open it to add what I didn't catch:\n"
        "- Desired outcome\n"
        "- Use cases\n"
        "- Photos or files\n"
        f"{LINK}"
    )


def test_ac20_single_missing_field_is_one_bullet():
    reply = render_reply("complete", _facts(["Photos or files"]), user_message="x", language="en")
    assert reply.splitlines() == [
        "Idea IDEA-0123 is in: Retyping WhatsApp orders into CRM",
        "Open it to add what I didn't catch:",
        "- Photos or files",
        LINK,
    ]


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_ac20_ac21_every_language_has_the_same_line_structure_with_english_names(lang):
    missing = ["Desired outcome", "Use cases", "Current workaround", "Photos or files"]
    reply = render_reply("complete", _facts(missing), user_message="x", language=lang)
    lines = reply.splitlines()
    assert len(lines) == 2 + len(missing) + 1, lines
    assert "IDEA-0123" in lines[0] and "Retyping WhatsApp orders into CRM" in lines[0]
    assert lines[1].strip() and not lines[1].startswith("- ")
    assert lines[2:-1] == [f"- {name}" for name in missing]
    assert lines[-1] == LINK
    assert " · " not in reply
    assert ", " not in "\n".join(lines[1:-1])
    assert "、" not in reply


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_ac20_the_prompt_line_has_no_trailing_period_glued_to_the_list(lang):
    reply = render_reply("complete", _facts(MISSING_3), user_message="x", language=lang)
    # the old copy ended "{{missing}}." - a stray full stop after the last name
    assert "files." not in reply and "files。" not in reply


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_ac20_shipped_copy_keeps_the_missing_token_on_its_own_lines(lang):
    key = "ideation_capture_complete" + ("" if lang == "en" else f".{lang}")
    _reg, text, tokens = CHATBOT_REPLY_COPY[key]
    assert "missing" in tokens
    own = [ln for ln in text.splitlines() if "{{missing}}" in ln]
    assert len(own) == 1
    assert own[0].removeprefix("- ") == "{{missing}}", own[0]


EXPECTED_NO_LINK = {
    "en": "Idea IDEA-0123 is in: Retyping WhatsApp orders into CRM",
    "ms": "Idea IDEA-0123 sudah diterima: Retyping WhatsApp orders into CRM",
    "zh": "创意 IDEA-0123 已收到：Retyping WhatsApp orders into CRM",
}


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_ac22_no_link_variant_is_unchanged(lang):
    reply = render_reply("complete", _facts(MISSING_3, link=None), user_message="x", language=lang)
    assert reply == EXPECTED_NO_LINK[lang]


# --------------------------------------------------------------------------- #
# AC-30 / AC-31 - the migration                                               #
# --------------------------------------------------------------------------- #
def _assign(src: str, name: str):
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def _migration_path() -> pathlib.Path:
    found = [
        p for p in VERSIONS_DIR.glob("*.py")
        if _assign(p.read_text(encoding="utf-8"), "revision") == REVISION
    ]
    assert len(found) == 1, f"expected exactly one migration with revision {REVISION}"
    return found[0]


def _load_migration():
    spec = importlib.util.spec_from_file_location("_idea_capture_fields_0001_under_test", _migration_path())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fallback() -> str:
    return ai_prompt_registry.PROMPT_KEYS[EXTRACTOR].fallback()


def _shipped(key: str) -> str:
    return CHATBOT_REPLY_COPY[COMPLETE_KEYS[key][0]][1]


def test_ac31_migration_identity_chain_and_single_head():
    path = _migration_path()
    src = path.read_text(encoding="utf-8")
    revision = _assign(src, "revision")
    assert isinstance(revision, str) and 0 < len(revision) <= 32
    assert path.name == f"{REVISION}.py"

    revisions, parents = set(), set()
    for p in VERSIONS_DIR.glob("*.py"):
        p_src = p.read_text(encoding="utf-8")
        rev = _assign(p_src, "revision")
        if not isinstance(rev, str):
            continue
        revisions.add(rev)
        down = _assign(p_src, "down_revision")
        parents.update(down if isinstance(down, (tuple, list)) else [down])
    down_revision = _assign(src, "down_revision")
    assert isinstance(down_revision, str) and down_revision in revisions
    assert down_revision != revision
    assert PRIOR_REVISION in revisions
    assert len(revisions - parents) == 1, f"alembic heads: {sorted(revisions - parents)}"

    upgrade = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    called = {
        n.func.id for n in ast.walk(upgrade)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "seed_prompt_registry" in called
    assert "publish" in called


def test_ac31_stock_extractor_texts_include_the_pre_lane_fallback_verbatim():
    texts = _load_migration().STOCK_IDEATE_EXTRACTOR_TEXTS
    assert isinstance(texts, tuple)
    hashes = {hashlib.sha256(t.strip().encode()).hexdigest() for t in texts}
    assert PRE_LANE_EXTRACTOR_SHA256 in hashes
    for t in texts:
        assert t.strip() != _fallback().strip()


@pytest.fixture
def db():
    with blank_session() as session:
        try:
            ai_prompt_registry.bust_cache()
            yield session
        finally:
            ai_prompt_registry.bust_cache()


def _pre_lane_extractor(mig) -> str:
    return next(
        t for t in mig.STOCK_IDEATE_EXTRACTOR_TEXTS
        if hashlib.sha256(t.strip().encode()).hexdigest() == PRE_LANE_EXTRACTOR_SHA256
    )


def _seed(db, name: str, template: str | None):
    v1 = AIPromptVersion(name=name, version=1, type="text", template=template, variables=[])
    db.add(v1)
    db.flush()
    if template is not None:
        db.add(AIPromptLabel(name=name, label="production", version_id=v1.id))
        db.flush()
    return v1


def _state(db, name: str):
    db.expire_all()
    versions = (
        db.query(AIPromptVersion).filter(AIPromptVersion.name == name).order_by(AIPromptVersion.version).all()
    )
    label = (
        db.query(AIPromptLabel).filter(AIPromptLabel.name == name, AIPromptLabel.label == "production").first()
    )
    current = None
    if label is not None:
        current = db.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).one()
    return versions, label, current


def _seed_all_stock(db, mig) -> None:
    _seed(db, EXTRACTOR, _pre_lane_extractor(mig))
    for key, (_short, stock) in COMPLETE_KEYS.items():
        _seed(db, key, stock)


def test_ac30_stock_production_is_upgraded_once_for_all_four_keys(db):
    mig = _load_migration()
    _seed_all_stock(db, mig)
    mig.publish(db.connection())

    versions, _label, current = _state(db, EXTRACTOR)
    assert [v.version for v in versions] == [1, 2]
    assert current.version == 2 and current.template == _fallback()
    for key in COMPLETE_KEYS:
        versions, _label, current = _state(db, key)
        assert [v.version for v in versions] == [1, 2], key
        assert current.version == 2, key
        assert current.template == _shipped(key), key
        assert "{{missing}}" in current.template


def test_ac30_stock_with_whitespace_difference_still_counts_as_stock(db):
    mig = _load_migration()
    _seed(db, EXTRACTOR, _pre_lane_extractor(mig) + "\n\n")
    for key, (_short, stock) in COMPLETE_KEYS.items():
        _seed(db, key, stock + "\n")
    mig.publish(db.connection())
    assert _state(db, EXTRACTOR)[2].template == _fallback()
    for key in COMPLETE_KEYS:
        assert _state(db, key)[2].template == _shipped(key)


def test_ac30_rerun_is_a_noop(db, caplog):
    mig = _load_migration()
    _seed_all_stock(db, mig)
    mig.publish(db.connection())
    with caplog.at_level(logging.WARNING):
        mig.publish(db.connection())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    for name in (EXTRACTOR, *COMPLETE_KEYS):
        versions, _label, current = _state(db, name)
        assert [v.version for v in versions] == [1, 2], name
        assert current.version == 2, name


def test_ac30_ac31_owner_edited_production_is_left_alone_with_a_warning(db, caplog):
    mig = _load_migration()
    edited = {name: f"OWNER EDITED {name}, placeholder text." for name in (EXTRACTOR, *COMPLETE_KEYS)}
    v1s = {name: _seed(db, name, text) for name, text in edited.items()}
    with caplog.at_level(logging.WARNING):
        mig.publish(db.connection())
    for name, text in edited.items():
        versions, label, current = _state(db, name)
        assert len(versions) == 1, name
        assert label.version_id == v1s[name].id, name
        assert current.template == text, name
    assert any(r.levelno >= logging.WARNING for r in caplog.records), "expected a warning log"


def test_ac30_each_key_is_judged_on_its_own(db):
    """Edited extractor and edited .ms stay; the stock en and .zh are still upgraded."""
    mig = _load_migration()
    keys = list(COMPLETE_KEYS)
    en, ms, zh = keys
    _seed(db, EXTRACTOR, "OWNER EDITED extractor")
    _seed(db, en, COMPLETE_KEYS[en][1])
    _seed(db, ms, "Owner edited Malay copy {{missing}}")
    _seed(db, zh, COMPLETE_KEYS[zh][1])
    mig.publish(db.connection())

    assert _state(db, EXTRACTOR)[2].template == "OWNER EDITED extractor"
    assert _state(db, ms)[2].template == "Owner edited Malay copy {{missing}}"
    assert len(_state(db, ms)[0]) == 1
    assert _state(db, en)[2].template == _shipped(en)
    assert _state(db, zh)[2].template == _shipped(zh)


def test_ac31_fresh_database_seeded_with_the_new_texts_gets_no_extra_version(db, caplog):
    """A fresh DB: the seed writes v1 from the new fallbacks, so publish has nothing to do."""
    from app.services.ai_prompt_seed import seed_prompt_registry

    mig = _load_migration()
    seed_prompt_registry(db.connection())
    with caplog.at_level(logging.WARNING):
        mig.publish(db.connection())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "owner" in r.getMessage().lower()] == []
    versions, _label, current = _state(db, EXTRACTOR)
    assert [v.version for v in versions] == [1]
    assert current.template == _fallback()
    for key in COMPLETE_KEYS:
        versions, _label, current = _state(db, key)
        assert [v.version for v in versions] == [1], key
        assert current.template == _shipped(key), key


def test_ac31_empty_registry_and_missing_label_change_nothing(db):
    mig = _load_migration()
    mig.publish(db.connection())
    for name in (EXTRACTOR, *COMPLETE_KEYS):
        versions, label, _current = _state(db, name)
        assert versions == [] and label is None
    _seed(db, EXTRACTOR, None)
    mig.publish(db.connection())
    versions, label, _current = _state(db, EXTRACTOR)
    assert len(versions) == 1 and label is None
