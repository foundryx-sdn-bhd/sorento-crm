"""`mem_0003_parser_history` publishes the S4 parser words once, unlabelled, and seeds the
S4 reply templates (PR #1304 round 4).

Two databases matter: one that runs `mem_0002` and `mem_0003` in the same upgrade (a
fresh install or CI: `mem_0002` already renders today's constant, so this publishes
nothing), and one already stamped at `mem_0002` with the round 3 words (the shared
hand-test DB: this publishes the one new version the owner then picks). Both run here
on the blank scratch schema, the way `test_mem_0002_publishes_rendered_prompt.py` does.
"""
from __future__ import annotations

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import MEMORY_ADDENDUM
from app.services.chatbot_reply_copy import FALLBACK_REPLY_COPY
from tests._pg_fixture import blank_session
from tests.chatbot.test_rearch_s12_config_ships import _load

PROMPT_NAME = "chatbot_semantic_parser"


def _seeded(db):
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = db.get_bind()
    _load("chatbot_rearch_s0.py").seed_domains_and_kinds(bind)
    seed_prompt_registry(bind)


def _parser_versions(db) -> dict[str, AIPromptVersion]:
    return {r.id: r for r in db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)}


def _production(db) -> str:
    return (
        db.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .one()
        .version_id
    )


class TestMem0003:
    def test_after_mem_0002_in_one_upgrade_it_publishes_nothing(self) -> None:
        with blank_session() as db:
            _seeded(db)
            assert _load("mem_0002_parser_memory.py").publish(db) is not None
            before = set(_parser_versions(db))
            assert _load("mem_0003_parser_history.py").publish(db) is None
            assert set(_parser_versions(db)) == before

    def test_over_the_round_3_words_it_publishes_one_unlabelled_version(self) -> None:
        with blank_session() as db:
            _seeded(db)
            production = _production(db)
            # The shared hand-test DB: `mem_0002` published the round 3 words.
            old = AIPromptVersion(
                name=PROMPT_NAME,
                version=99,
                type="text",
                template="the round 3 memory words",
                variables=[],
                config_json={"mem_0002_parser_memory": True},
                commit_message="round 3",
            )
            db.add(old)
            db.commit()
            mem = _load("mem_0003_parser_history.py")
            before = set(_parser_versions(db))

            version = mem.publish(db)
            assert version == 100
            assert mem.publish(db) is None, "a second run publishes nothing"

            (row,) = [r for vid, r in _parser_versions(db).items() if vid not in before]
            assert MEMORY_ADDENDUM.strip() in row.template
            assert '"what do I normally ask about"' in row.template
            assert row.config_json.get(mem.MARKER_KEY) is True
            assert _production(db) == production, "the production label moved"

    def test_the_s4_templates_are_seeded_for_every_language(self) -> None:
        with blank_session() as db:
            _seeded(db)
            names = {r.name for r in db.query(AIPromptVersion)}
            for short in FALLBACK_REPLY_COPY:
                for suffix in ("", ".ms", ".zh"):
                    assert f"chatbot_reply_{short}{suffix}" in names

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
