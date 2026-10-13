"""Every ACTIVE prompt key has a row after the migrations run (owner report, 5 Sep 2026).

The Prompts screen for `chatbot_clarifier` was empty: `ai_prompt_versions` held two rows
for `chatbot_semantic_parser` and none for the clarifier, so the runtime silently used
`ai_prompt_registry._chatbot_clarifier_fallback` and the owner had nothing to edit.

The cause is an ORDERING one, and it is the reason this test runs the migration rather
than the seeder. `seed_prompt_registry` loops over `PROMPT_KEYS`, so it always covers
whatever is registered WHEN IT RUNS - asserting that directly would be a tautology. What
can actually go wrong is a slice registering a new key AFTER the last migration that
calls the seeder: every database already at that revision then has a key with no row, and
nothing fails. That is exactly what the local dev database showed, because its seed was
run by hand at 475, before S4 registered `chatbot_clarifier` and before S3 registered the
eleven `chatbot_reply_*` keys.

So the assertion is: run the LAST migration in this lane that seeds, on a blank schema,
and every active key must come out with a version and a `production` label. A future
slice that adds a key without a seeding migration fails here.

Migration is imported and executed for real (the `_run_migration_310` idiom in
`tests/test_complaint_settled_on_site.py`), not re-stated, so the test cannot drift from
the migration it grades. Postgres blank schema, per PRINCIPLES.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS
from tests._pg_fixture import blank_session

# The newest migration in the chatbot lane that calls `seed_prompt_registry`. When a
# later slice adds one, point this at it: the property under test is that the LAST
# seeding migration covers the whole registry, not that any particular file does.
LAST_SEEDING_MIGRATION = "478_chatbot_s3_copy.py"


def _run_migration(conn, filename: str) -> None:
    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location("migration_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        module.upgrade()


def test_every_active_prompt_key_is_seeded_by_the_migrations() -> None:
    with blank_session() as session:
        _run_migration(session.get_bind(), LAST_SEEDING_MIGRATION)

        seeded = {row[0] for row in session.query(AIPromptVersion.name).all()}
        labelled = {
            row[0]
            for row in session.query(AIPromptLabel.name)
            .filter(AIPromptLabel.label == "production")
            .all()
        }
        active = {name for name, spec in PROMPT_KEYS.items() if spec.active}

        assert active - seeded == set(), (
            "these active prompt keys have no ai_prompt_versions row after the "
            f"migrations, so their admin screen is empty: {sorted(active - seeded)}"
        )
        assert active - labelled == set(), (
            "these active prompt keys have no production label, so the runtime "
            f"resolves the code fallback: {sorted(active - labelled)}"
        )


def test_the_chatbot_keys_the_owner_reported_are_among_them() -> None:
    """Named individually, because "all of them" passes on the day the registry is
    empty and this is the report that started it."""
    with blank_session() as session:
        _run_migration(session.get_bind(), LAST_SEEDING_MIGRATION)

        seeded = {row[0] for row in session.query(AIPromptVersion.name).all()}

        assert "chatbot_clarifier" in seeded
        assert "chatbot_semantic_parser" in seeded
        reply_keys = {name for name in PROMPT_KEYS if name.startswith("chatbot_reply_")}
        assert reply_keys, "the canned-reply keys are registered from the copy table"
        assert reply_keys <= seeded

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
