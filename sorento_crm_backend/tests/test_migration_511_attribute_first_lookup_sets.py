"""Migration `511_attribute_first_lookup_sets` (D3, PLAN-attribute-first-asks.md).

Creates two EMPTY lookup sets - `certificate_scheme` and `attachment_type_alias` - with
no options; the owner enters options and keywords on System > Lookup Sets afterwards
(owner decision, 10 Sep 2026: "don't need to seed for scheme, I will enter"). Loaded by
file path since the filename starts with a digit (the `_run_migration` idiom, see
`tests/test_migration_491_chatbot_ladder_incoming_po.py`), against a Postgres blank
schema per PRINCIPLES - never sqlite.

Contract: `documentation/plans/chatbot/attribute-first-asks-acceptance-criteria.md`
AC-1314.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests._pg_fixture import blank_session

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "511_attribute_first_lookup_sets.py"
)

SET_KEYS = ("certificate_scheme", "attachment_type_alias")


def _load_migration():
    assert MIGRATION.exists(), f"{MIGRATION} does not exist - work item D3 is not written yet"
    spec = importlib.util.spec_from_file_location("m511_attribute_first_lookup_sets", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_the_revision_id_is_under_32_characters_and_chains_onto_head():
    """AC-1350/R26: `scripts/alembic-reparent.sh` changes `down_revision` every
    time origin/main moves past this lane's own base - asserting a SPELLED
    parent id here (the old `"510_strip_rtf_so_notes"`) breaks the test on
    every reparent, which is not a defect in this migration. The chain
    property is what must actually hold: the parent exists as a real
    revision somewhere in `alembic/versions`, and the whole tree still has
    exactly ONE head - this lane's own revision id, since D3 is the newest
    migration once merged onto main's current head.

    RED after the reparent (down_revision now "510_pi_description_en"): the
    OLD assertion `module.down_revision == "510_strip_rtf_so_notes"` fails on
    a plain string mismatch - the exact defect this rewrite removes.
    """
    module = _load_migration()
    assert len(module.revision) <= 32, (module.revision, len(module.revision))

    script_dir = _script_directory()
    known_revisions = {rev.revision for rev in script_dir.walk_revisions()}
    # A merge re-parent makes `down_revision` a tuple of main's heads; every parent must exist.
    parents = module.down_revision if isinstance(module.down_revision, tuple) else (module.down_revision,)
    for parent in parents:
        assert parent in known_revisions, (parent, sorted(known_revisions))

    heads = script_dir.get_heads()
    assert len(heads) == 1, heads
    # bcd_0001_brand_chatbot_default (owner brief W5, same lane) now sits on top of 511;
    # 511 must still be on the one chain that head walks.
    chain = {rev.revision for rev in script_dir.iterate_revisions(heads[0], "base")}
    assert module.revision in chain, (heads, module.revision)


def _set_rows(db):
    return db.execute(
        text("SELECT set_key, is_active FROM lookup_sets WHERE set_key = ANY(:keys)"),
        {"keys": list(SET_KEYS)},
    ).fetchall()


def _option_count(db, set_key: str) -> int:
    return db.execute(
        text(
            "SELECT count(*) FROM lookup_options o JOIN lookup_sets s ON s.id = o.set_id "
            "WHERE s.set_key = :key"
        ),
        {"key": set_key},
    ).scalar()


def test_migration_creates_two_empty_lookup_sets_and_downgrade_removes_them():
    module = _load_migration()
    with blank_session() as db:
        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            module.upgrade()

        rows = {row[0]: row[1] for row in _set_rows(db)}
        assert set(rows) == set(SET_KEYS), rows
        for key in SET_KEYS:
            assert rows[key] is True, (key, rows)
            assert _option_count(db, key) == 0, key

        with Operations.context(ctx):
            module.upgrade()  # a second run changes nothing (idempotent)
        rows_again = {row[0]: row[1] for row in _set_rows(db)}
        assert set(rows_again) == set(SET_KEYS)

        with Operations.context(ctx):
            module.downgrade()
        assert _set_rows(db) == []


# --------------------------------------------------------------------------- #
# Revive 26 Sep 2026 (owner: "which water tap got stock ... which water basin  #
# got stock"): the two everyday words reach their class. Appended to the       #
# categories that ALREADY carry the class, never overwriting a word staff or   #
# the backfill put there, and the code list says the same so a later          #
# `backfill_category_signals` (set-where-mismatch) keeps them.                  #
# --------------------------------------------------------------------------- #

WATER_WORDS = {"Tap": "water tap", "Wash Basin": "water basin"}


def _category(db, code: str, class_label: str | None, synonyms: list[str]) -> str:
    import json
    import uuid

    cid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO product_categories (id, category_code, category_name, class_label, "
            "search_synonyms, is_searchable) VALUES (:id, :code, :name, :label, CAST(:syn AS jsonb), TRUE)"
        ),
        {"id": cid, "code": code, "name": code, "label": class_label, "syn": json.dumps(synonyms)},
    )
    return cid


def _synonyms(db, cid: str) -> list[str]:
    return db.execute(
        text("SELECT search_synonyms FROM product_categories WHERE id = :id"), {"id": cid}
    ).scalar()


def test_class_synonyms_in_code_carry_the_water_words():
    from app.services.product_spec_registry import shipped_class_synonyms

    CLASS_SYNONYMS = shipped_class_synonyms()

    for label, word in WATER_WORDS.items():
        assert word in CLASS_SYNONYMS[label], (label, CLASS_SYNONYMS[label])


def test_migration_appends_the_water_words_to_their_class_and_downgrade_removes_only_them():
    module = _load_migration()
    with blank_session() as db:
        tap = _category(db, "ZZT-M511-FT", "Tap", ["tap", "faucet", "staff word"])
        basin = _category(db, "ZZT-M511-WB", "Wash Basin", ["basin"])
        sink = _category(db, "ZZT-M511-KS", "Kitchen Sink", ["sink"])
        unclassed = _category(db, "ZZT-M511-X", None, [])

        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            module.upgrade()
            module.upgrade()  # idempotent: the word is never added twice

        assert _synonyms(db, tap) == ["tap", "faucet", "staff word", "water tap"]
        assert _synonyms(db, basin) == ["basin", "water basin"]
        assert _synonyms(db, sink) == ["sink"]
        assert _synonyms(db, unclassed) == []

        with Operations.context(ctx):
            module.downgrade()
        assert _synonyms(db, tap) == ["tap", "faucet", "staff word"]
        assert _synonyms(db, basin) == ["basin"]
