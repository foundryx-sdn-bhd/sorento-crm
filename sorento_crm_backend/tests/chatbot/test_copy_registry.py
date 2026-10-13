"""AC-302, journey B: the chatbot's canned replies through the REAL prompt registry.

`test_tail_units.py::TestCannedCopy` proves `escalate_catalog`'s own switch renders each
arm's text correctly, given a `CannedCopy`. It never touches `app.services.chatbot.copy`'s
`resolve(db)` (the function that actually builds that object from the database) or the
migration's seed - so nothing today proves the three pieces AC-302 depends on actually
agree: the fallback text `resolve()` falls back to, a published override winning after
`bust_cache`, and the copy-seed migration being safe to run twice. (Docstring only: the
migration was renumbered 473 -> 476 when the S1b chain merged, and a number in prose goes
stale the next time it moves.)

Pattern follows `tests/test_ai_prompt_registry.py` (the generic registry's own suite) -
`blank_session()`, not a hand-rolled sqlite shim (`LESSONS-LEARNT.md`: Postgres only).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import ai_prompt_registry
from app.services.ai_prompt_registry import PROMPT_KEYS, get_prompt
from app.services.ai_prompt_seed import seed_prompt_registry
from app.services.chatbot import copy as copy_mod
from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY
from tests._pg_fixture import blank_session


@pytest.fixture
def db() -> Session:
    with blank_session() as session:
        try:
            yield session
        finally:
            ai_prompt_registry.bust_cache()


class TestEveryChatbotReplyKeyFallsBackToTodaysText:
    """No row in the database at all - `resolve(db)` must still answer with the exact
    strings `chatbot_reply_copy.py` ships, because a DB-unreachable turn falls back to
    them (copy.py's own docstring promise)."""

    def test_resolve_on_an_empty_database_returns_every_fallback_verbatim(self, db: Session) -> None:
        canned = copy_mod.resolve(db)
        for short_name, (key, fallback_text, _tokens) in CHATBOT_REPLY_COPY.items():
            assert canned.templates[short_name] == fallback_text, (
                f"{key}: resolve() on an empty DB must equal the hardcoded fallback"
            )

    def test_fallback_copy_needs_no_database_at_all(self) -> None:
        """The node-replay seam: no `db` argument exists to pass one."""
        canned = copy_mod.fallback_copy()
        for short_name, (_key, fallback_text, _tokens) in CHATBOT_REPLY_COPY.items():
            assert canned.templates[short_name] == fallback_text


class TestAPublishedOverrideWins:
    def test_a_new_version_labelled_production_reaches_resolve_after_bust_cache(
        self, db: Session
    ) -> None:
        seed_prompt_registry(db.get_bind())
        key = "chatbot_reply_not_supported"
        short_name = "not_supported"

        # journey B: the owner edits the reply and publishes - a new version, the
        # `production` label MOVED onto it.
        new_version = AIPromptVersion(name=key, version=2, type="text", template="New words.", variables=[])
        db.add(new_version)
        db.flush()
        label = db.query(AIPromptLabel).filter_by(name=key, label="production").one()
        label.version_id = new_version.id
        db.commit()

        # Without busting the cache the OLD text would still be served for the TTL
        # window - which is exactly why the label-move route calls `bust_cache` (D5).
        ai_prompt_registry.bust_cache(key)
        rendered = get_prompt(db, key)
        assert rendered.text == "New words."
        assert rendered.version == 2

        canned = copy_mod.resolve(db)
        assert canned.templates[short_name] == "New words."
        assert canned.render(short_name) == "New words."


class TestSeedIsIdempotent:
    """The migration's `upgrade()` is exactly `seed_prompt_registry(op.get_bind())` - run
    twice on the SAME blank schema (what re-running `alembic upgrade head` on an
    already-migrated database does) and it must not duplicate a version or a label."""

    def test_running_the_seed_twice_creates_no_duplicates(self, db: Session) -> None:
        seed_prompt_registry(db.get_bind())
        seed_prompt_registry(db.get_bind())

        chatbot_keys = {key for key, _t, _v in CHATBOT_REPLY_COPY.values()}
        assert chatbot_keys <= set(PROMPT_KEYS.keys())
        for key in chatbot_keys:
            versions = db.query(AIPromptVersion).filter_by(name=key).all()
            assert [v.version for v in versions] == [1], f"{key}: seeding twice must not add a v2"
            labels = db.query(AIPromptLabel).filter_by(name=key, label="production").all()
            assert len(labels) == 1, f"{key}: seeding twice must not add a second production label"

    def test_the_seed_matches_todays_fallback_text_character_for_character(self, db: Session) -> None:
        """D8, parity before improvement: seeding must change WHO can edit the copy, not
        WHAT it says."""
        seed_prompt_registry(db.get_bind())
        for short_name, (key, fallback_text, _tokens) in CHATBOT_REPLY_COPY.items():
            version = db.query(AIPromptVersion).filter_by(name=key, version=1).one()
            assert version.template == fallback_text, f"{key}: seeded v1 text drifted from the fallback"

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
