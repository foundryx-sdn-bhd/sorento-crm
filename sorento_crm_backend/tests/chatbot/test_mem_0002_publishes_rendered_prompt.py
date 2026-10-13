"""`mem_0002_parser_memory` publishes the RENDERED production text (reviewer pass on
PR #1304 at d89110c0, finding B3).

Production parser versions are `SEMANTIC_PARSER_PROMPT` plus the policy blocks rendered
from `chatbot_domains` / `chatbot_entity_kinds` (`chatbot_rearch_s4._body`, promoted by
`chatbot_rearch_s12`). At d89110c0 this migration published the bare constant, so the
version the owner's hand test puts on the label had every domain and entity-kind paragraph
missing. This pins: the published template IS `s4._body(session)`, carries the memory
addendum and the policy blocks, sits under main's 41,163 est. token ceiling plus the 512 bound on the memory addendum (re-pinned 29 Sep 2026, see `test_parser_prompt_budget.CEILING`), is unlabelled,
and a second run publishes nothing.

Runs on the blank scratch schema (`blank_session`), the same way
`test_rearch_s4_prompt_blocks.py` runs S4 alone.
"""
from __future__ import annotations

import math

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, MEMORY_ADDENDUM
from tests._pg_fixture import blank_session
from tests.chatbot.test_parser_prompt_budget import CEILING, MEMORY_ADDENDUM_CEILING
from tests.chatbot.test_rearch_s12_config_ships import _load

PROMPT_NAME = "chatbot_semantic_parser"


def _est_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 3)


def _versions(db) -> dict[str, AIPromptVersion]:
    return {
        row.id: row
        for row in db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)
    }


def _seeded(db):
    """Domains, kinds and the registry's v1 plus `production` label: what a database
    has when `mem_0002_parser_memory.upgrade()` reaches its `publish`."""
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = db.get_bind()
    _load("chatbot_rearch_s0.py").seed_domains_and_kinds(bind)
    seed_prompt_registry(bind)
    return _load("mem_0002_parser_memory.py")


def _production(db):
    return (
        db.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .one()
        .version_id
    )


class TestMem0002PublishesTheRenderedBody:
    def test_the_published_version_is_the_constant_plus_the_policy_blocks(self) -> None:
        with blank_session() as db:
            mem = _seeded(db)
            before = _versions(db)

            mem.publish(db)

            rows = [row for vid, row in _versions(db).items() if vid not in before]
            assert len(rows) == 1, f"expected one new version, found {len(rows)}"
            template = rows[0].template
            expected, blocks_hash = _load("chatbot_rearch_s4.py")._body(db)
            assert BLOCKS_BEGIN in template and BLOCKS_END in template, (
                "the published version has no policy blocks: it is the bare constant"
            )
            assert template == expected
            assert rows[0].config_json.get("blocks_hash") == blocks_hash
            assert MEMORY_ADDENDUM.strip() in template

    def test_it_is_under_the_ceiling_unlabelled_and_published_once(self) -> None:
        with blank_session() as db:
            mem = _seeded(db)
            production_before = _production(db)
            before = _versions(db)

            mem.publish(db)
            mem.publish(db)

            new_rows = [row for vid, row in _versions(db).items() if vid not in before]
            assert len(new_rows) == 1, f"publish once, then nothing: {len(new_rows)} new"
            (row,) = new_rows
            rendered = row.template.replace("{{current_date}}", "Thursday, 25 September 2026")
            assert MEMORY_ADDENDUM in row.template, "the memory addendum is not in the template"
            without_memory = row.template.replace(MEMORY_ADDENDUM, "", 1).replace(
                "{{current_date}}", "Thursday, 25 September 2026"
            )
            assert _est_tokens(without_memory) <= CEILING, (
                f"{_est_tokens(without_memory)} est. tokens without the memory addendum, "
                f"over main bc75eb96's measured {CEILING} ceiling"
            )
            assert _est_tokens(rendered) <= CEILING + MEMORY_ADDENDUM_CEILING, (
                f"{_est_tokens(rendered)} est. tokens, over {CEILING} + "
                f"{MEMORY_ADDENDUM_CEILING} (main plus the memory addendum)"
            )
            assert _production(db) == production_before, "the production label moved"
            labelled = {
                r.version_id
                for r in db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME)
            }
            assert row.id not in labelled

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
