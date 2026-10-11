"""AC-7 (DB half) and AC-20: the migration creates `chatbot_examples`, seeds ~20 synthetic
rows without embeddings, publishes the two prompt keys, and the lineage has one head.

`alembic upgrade head` cannot build this schema from zero (CI uses `bootstrap_env`), so the
migration is imported and run for real over the blank Postgres schema, the idiom of
`tests/test_prompt_registry_is_seeded_by_migrations.py`. The blank schema is built by
`create_all`, so `chatbot_examples` is dropped first: the migration then creates it itself.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from tests._pg_fixture import blank_session
from tests.chatbot.prompt_diet._common import BACKEND, CARDS_TXT, CORE_TXT, split_cards

VERSIONS = BACKEND / "alembic" / "versions"


def _migration_file() -> Path:
    hits = [p for p in sorted(VERSIONS.glob("*.py")) if "chatbot_examples" in p.read_text(encoding="utf-8")]
    assert hits, "no alembic migration mentions chatbot_examples"
    assert len(hits) == 1, f"expected one migration to own chatbot_examples, found {[p.name for p in hits]}"
    return hits[0]


def _run_upgrade(conn) -> None:
    path = _migration_file()
    spec = importlib.util.spec_from_file_location("migration_prompt_diet", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        module.upgrade()


@contextmanager
def _migrated():
    with blank_session() as session:
        session.execute(text("DROP TABLE IF EXISTS chatbot_examples"))
        _run_upgrade(session.get_bind())
        yield session


def test_ac20_single_alembic_head_and_it_fits_the_column() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads
    assert len(heads[0]) <= 32, heads[0]


def test_ac20_the_migration_is_the_head_or_below_it_and_chains() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    text_ = _migration_file().read_text(encoding="utf-8")
    revisions = {rev.revision: rev for rev in script.walk_revisions()}
    owner = next(r for r in revisions.values() if r.path and Path(r.path).name == _migration_file().name)
    assert owner.down_revision, "the migration must chain onto a committed head"
    assert "chatbot_examples" in text_


def test_ac20_table_has_the_pinned_columns() -> None:
    with _migrated() as migrated:
        rows = migrated.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'chatbot_examples'"
            )
        ).all()
        cols = {name: nullable for name, nullable in rows}
        assert set(cols) >= {
            "id", "area", "message", "context", "expected_output", "embedding", "source", "enabled", "created_at",
        }, cols
        assert cols["context"] == "YES" and cols["embedding"] == "YES"
        assert cols["area"] == "NO" and cols["message"] == "NO" and cols["expected_output"] == "NO"


def test_ac20_about_twenty_synthetic_seeds_without_embeddings() -> None:
    with _migrated() as migrated:
        rows = migrated.execute(
            text("SELECT area, message, source, enabled, embedding IS NULL AS no_vec FROM chatbot_examples")
        ).all()
        assert 15 <= len(rows) <= 30, len(rows)
        assert {r.source for r in rows} == {"synthetic"}
        assert all(r.enabled for r in rows)
        assert all(r.no_vec for r in rows), "seeds carry no embedding; the seed script fills them"
        from app.services.chatbot.head.prompt_diet import AREA_BUNDLE

        assert {r.area for r in rows} <= set(AREA_BUNDLE), "every seed names an area that selects a bundle"
        assert len({r.area for r in rows}) >= 5, "seeds cover several areas"
        assert len({r.message for r in rows}) == len(rows), "no duplicate seeds"


def test_ac20_seeds_use_fake_names_only() -> None:
    with _migrated() as migrated:
        """Synthetic means invented: no row may carry an e-mail address or a phone-shaped number."""
        import re

        rows = migrated.execute(text("SELECT message, context, expected_output::text FROM chatbot_examples")).all()
        blob = "\n".join(" ".join(str(c) for c in r if c) for r in rows)
        assert not re.search(r"[\w.]+@[\w.]+\.\w+", blob)
        assert not re.search(r"\+?\d{9,}", blob)


def test_ac20_the_seeded_expected_outputs_are_json_objects() -> None:
    with _migrated() as migrated:
        kinds = {k for (k,) in migrated.execute(text("SELECT DISTINCT jsonb_typeof(expected_output) FROM chatbot_examples"))}
        assert kinds == {"object"}


def test_ac7_the_two_prompt_keys_hold_exactly_the_data_files() -> None:
    with _migrated() as migrated:
        from app.models.ai_prompt import AIPromptVersion

        def template(name: str) -> str:
            rows = migrated.query(AIPromptVersion).filter(AIPromptVersion.name == name).all()
            assert len(rows) == 1, f"{name}: expected one seeded version, got {len(rows)}"
            return rows[0].template

        assert template("chatbot_parser_core") == CORE_TXT.read_text(encoding="utf-8")
        assert template("chatbot_parser_cards") == CARDS_TXT.read_text(encoding="utf-8")


def test_ac7_registry_fallbacks_are_the_data_files() -> None:
    """A missing registry row falls back to the same text the migration seeds."""
    from app.services.ai_prompt_registry import PROMPT_KEYS

    assert PROMPT_KEYS["chatbot_parser_core"].fallback() == CORE_TXT.read_text(encoding="utf-8")
    assert PROMPT_KEYS["chatbot_parser_cards"].fallback() == CARDS_TXT.read_text(encoding="utf-8")
