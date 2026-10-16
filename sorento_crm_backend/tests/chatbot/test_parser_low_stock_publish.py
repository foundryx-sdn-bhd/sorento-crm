"""AC-74 - the low stock vocabulary reaches a LIVE prompt version (#893).

Console check on the lane stack, 14 Sep 2026: "low stock report" parsed as `check_stock`
and "reorder report" as a form lookup, with every pytest in
`test_parser_low_stock_words.py` green. The reason is the one migration 514 already
records - `ai_prompt_registry.render()` reads the PUBLISHED `chatbot_semantic_parser` row
and falls back to `chatbot_parser_prompt`'s constant only when no DB row exists at all -
so editing the constant reaches a live customer NOWHERE until a migration publishes it.

`TestTheOutstandingVocabularyIsPublished` in `test_parser_growth_r1_reachability.py` is
this file's model; the two halves it pins are the two that can silently regress:

* **the migration exists and carries the words**, read off the file on disk rather than
  off the constant it publishes, and
* **publishing behaves**: two new versions, idempotent on a second call, and NO LABEL
  MOVED - promoting is the owner's own post-deploy step, and a migration that moved
  `production` itself would put an unreviewed parser in front of a customer on deploy.

Postgres only (`tests/_pg_fixture.py::blank_session`) - the publish half writes rows.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session
from tests.chatbot.test_parser_growth_r1_reachability import _alembic_heads_excluding

MIGRATION = "517_chatbot_low_stock_vocab.py"
PROMPT_NAME = "chatbot_semantic_parser"


def _module():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / MIGRATION
    assert path.exists(), (
        "no migration publishes the low stock vocabulary, so LOW_STOCK_ADDENDUM reaches "
        "no live prompt version (console check, 14 Sep 2026)"
    )
    spec = importlib.util.spec_from_file_location("zzt_low_stock_vocab_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheLowStockVocabularyIsPublished:
    def test_the_migration_publishes_both_bodies(self) -> None:
        """BOTH texts, for the reason 487 / 490 / 514 give: prod's `production` label sits
        on the FULL body and dev's on the SLIM one, so publishing one reaches one
        deployment only."""
        module = _module()
        assert callable(module.publish)
        for text in (module._full_text(), module._slim_text()):
            assert "low_stock_report" in text
            assert '"low stock report"' in text
            assert '"reorder report"' in text
            assert '"stock below level"' in text

    def test_the_revision_chains_onto_the_current_head(self) -> None:
        """The head is READ, never spelled out, so the pre-PR re-parent
        (`scripts/alembic-reparent.sh`) cannot fail this test for doing its job."""
        module = _module()
        assert len(module.revision) <= 32, module.revision
        heads = _alembic_heads_excluding(module.revision)
        assert module.down_revision in heads, (
            f"the migration must chain onto a current head; down_revision="
            f"{module.down_revision!r}, heads without this migration = {sorted(heads)}"
        )


class TestPublishBehaviour:
    def test_publish_adds_both_versions_and_is_idempotent(self) -> None:
        """Two versions on the first call, nothing on the second. A migration that
        re-published on every deploy would fill the version list with identical rows and
        make "which version is live" unanswerable from the admin UI."""
        module = _module()
        with blank_session() as db:
            first = module.publish(db)
            assert first["full"] is not None and first["slim"] is not None, first
            assert first["full"] != first["slim"], "each body is its own version"

            rows = (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME)
                .all()
            )
            assert len(rows) == 2, f"expected exactly two published versions: {rows}"
            templates = {r.template for r in rows}
            assert templates == {module._full_text(), module._slim_text()}

            second = module.publish(db)
            assert second == {"full": None, "slim": None}, second
            assert (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME)
                .count()
                == 2
            ), "a second call published a duplicate"

    def test_publish_moves_no_label(self) -> None:
        """The whole point of the immutable-versions-plus-movable-labels split: the new
        bodies land UNLABELLED and `production` keeps pointing exactly where it did. The
        owner promotes after deploy, having read the diff - nothing a customer sees
        changes on the deploy itself.
        """
        module = _module()
        with blank_session() as db:
            previous = AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text",
                template="the body that was live before this lane", variables=[],
            )
            db.add(previous)
            db.flush()
            db.add(AIPromptLabel(name=PROMPT_NAME, label="production",
                                 version_id=previous.id))
            db.commit()

            module.publish(db)

            label = (
                db.query(AIPromptLabel)
                .filter(
                    AIPromptLabel.name == PROMPT_NAME,
                    AIPromptLabel.label == "production",
                )
                .one()
            )
            assert label.version_id == previous.id, (
                "publishing moved the production label - promoting is the owner's own "
                "post-deploy step, never the migration's"
            )
            assert (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME)
                .count()
                == 3
            ), "the two new bodies must be ADDED beside the labelled one, not replace it"

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
