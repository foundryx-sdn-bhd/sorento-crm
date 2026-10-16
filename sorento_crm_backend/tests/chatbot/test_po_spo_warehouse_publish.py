"""RED tests - the PO / SPO warehouse vocabulary reaches a LIVE prompt version.

`documentation/plans/chatbot/PLAN-po-spo-warehouse-29sep.md` sections M1, M2;
`documentation/plans/chatbot/po-spo-warehouse-29sep-acceptance-criteria.md` AC-15, AC-16.

Editing `chatbot_parser_prompt.py` reaches nobody until a migration publishes it
(`ai_prompt_registry.render()` reads the PUBLISHED row). Model:
`test_parser_low_stock_publish.py` and `test_parser_growth_r1_reachability.py`.

Postgres only (`tests/_pg_fixture.py::blank_session`).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
REVISION = "chatbot_po_spo_warehouse_vocab"
MIGRATION = BACKEND / "alembic" / "versions" / f"{REVISION}.py"
SCRIPT = BACKEND / "scripts" / "publish_parser_prompt.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _load(path: Path, alias: str, missing: str):
    assert path.exists(), missing
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load(MIGRATION, "zzt_po_spo_warehouse_migration", f"{MIGRATION} does not exist (AC-15)")


def _script():
    return _load(SCRIPT, "zzt_publish_parser_prompt_script", f"{SCRIPT} does not exist (AC-16)")


def _body() -> str:
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


class TestTheMigrationIsAMergeRevision:
    def test_the_revision_id(self) -> None:
        module = _migration()
        assert module.revision == REVISION
        assert len(module.revision) <= 32

    def test_down_revision_is_a_real_revision_that_is_not_ours(self) -> None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        module = _migration()
        down = module.down_revision
        # The parent is whatever main's head was at the last re-parent
        # (scripts/alembic-reparent.sh): a string when main had one head, a tuple when
        # the lane had to join several. It is checked as "a real revision that is not
        # ours", never by name and never as "the heads without this file": the join
        # migration main adds after this one merges (merge_29sep_batch8 after #1373)
        # references the sibling heads, so a pinned parent set goes red on every main
        # merge.
        parents = set(down) if isinstance(down, (tuple, list)) else {down}
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        sd = ScriptDirectory.from_config(cfg)
        for parent in parents:
            assert isinstance(parent, str) and parent != REVISION, parents
            assert sd.get_revision(parent) is not None, parent

    def test_with_it_the_graph_has_one_head(self) -> None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        _migration()  # the file must exist before the graph can be said to have it
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        sd = ScriptDirectory.from_config(cfg)
        # A later migration chained on top (a join batch on main) must not turn this
        # red, so check the revision is on the single head's ancestry, not that it IS
        # the head.
        heads = list(sd.get_heads())
        assert len(heads) == 1, heads
        assert REVISION in {r.revision for r in sd.walk_revisions(base="base", head=heads[0])}


class TestPublishBehaviour:
    def test_publish_adds_the_next_version_once(self) -> None:
        module = _migration()
        with blank_session() as db:
            first = module.publish(db)
            assert isinstance(first, int), first
            row = (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
                .one()
            )
            assert row.template == _body()
            assert "spo_allocation" in row.template

            assert module.publish(db) is None
            assert (
                db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).count() == 1
            ), "a second call published a duplicate"

    def test_publish_carries_the_addendum_text(self) -> None:
        import app.services.chatbot_parser_prompt as mod

        addendum = getattr(mod, "PO_SPO_WAREHOUSE_ADDENDUM", None)
        assert addendum is not None, "PO_SPO_WAREHOUSE_ADDENDUM does not exist yet"
        module = _migration()
        with blank_session() as db:
            version = module.publish(db)
            row = (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == version)
                .one()
            )
            assert addendum in row.template

    def test_publish_moves_no_label(self) -> None:
        module = _migration()
        with blank_session() as db:
            previous = AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text",
                template="the body that was live before this lane", variables=[],
            )
            db.add(previous)
            db.flush()
            db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=previous.id))
            db.commit()

            assert module.publish(db) == 2

            label = (
                db.query(AIPromptLabel)
                .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
                .one()
            )
            assert label.version_id == previous.id, "publishing moved the production label"
            assert (
                db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).count() == 2
            )


class TestThePublishScript:
    def test_the_script_publishes_the_same_body(self) -> None:
        script = _script()
        migration = _migration()
        with blank_session() as db:
            first = script.publish(db)
            assert isinstance(first, int), first
            row = (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
                .one()
            )
            assert row.template == _body()

            assert script.publish(db) is None
            # The migration sees the script's version as already carrying the body.
            assert migration.publish(db) is None
            assert (
                db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).count() == 1
            )

    def test_the_script_moves_no_label(self) -> None:
        script = _script()
        with blank_session() as db:
            previous = AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text", template="older body", variables=[],
            )
            db.add(previous)
            db.flush()
            db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=previous.id))
            db.commit()

            script.publish(db)

            label = (
                db.query(AIPromptLabel)
                .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
                .one()
            )
            assert label.version_id == previous.id

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
