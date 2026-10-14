"""Phase 2 RED tests - REPORT-DRILLDOWN, the parser half.

`documentation/plans/chatbot/PLAN-report-drilldown-08oct.md` design 5 and card rule 1: after a
ranking, a ranking on a new axis is read by the PARSER as `ranking_drill` "scoped" (the rows
above), "global" (its own ask) or "unsure" (ask). Never a keyword rule in code.

The prompt ships as a NEW dev version (migration `report_drill_0001_prompt`, the
`report_engine_0001_prompt` pattern): the `production` label never moves; the owner hand-merges
into the prod prompt after the hand test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot.head.parser import DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA, TOLERATED_ABSENT

from tests._pg_fixture import blank_session

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PROMPT_NAME = "chatbot_semantic_parser"
MIGRATION = "report_drill_0001_prompt"


def test_ranking_drill_is_a_declared_tolerated_key_with_three_readings() -> None:
    prop = PARSE_OUTPUT_JSON_SCHEMA["properties"]["ranking_drill"]
    assert set(prop["enum"]) == {"scoped", "global", "unsure", None}, prop
    assert "ranking_drill" in DECLARED_KEYS
    assert "ranking_drill" in TOLERATED_ABSENT


def test_the_addendum_teaches_the_three_readings_with_the_owner_examples() -> None:
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM, SEMANTIC_PARSER_PROMPT

    assert REPORT_ASK_ADDENDUM in SEMANTIC_PARSER_PROMPT
    for word in ('"ranking_drill"', '"scoped"', '"global"', '"unsure"'):
        assert word in REPORT_ASK_ADDENDUM, word
    assert "customers for these sales agent" in REPORT_ASK_ADDENDUM
    assert "or across all customers?" in REPORT_ASK_ADDENDUM, "the reply to the unsure question"


def _load(alias: str):
    path = VERSIONS / f"{MIGRATION}.py"
    assert path.exists(), f"{MIGRATION}.py does not exist"
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chains_onto_the_report_engine_prompt() -> None:
    module = _load("mig_report_drill_0001")
    assert module.revision == MIGRATION and len(module.revision) <= 32
    assert callable(module.publish)


def _production(session):
    return (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def test_migration_publishes_a_new_version_label_unmoved_and_idempotent() -> None:
    module = _load("mig_report_drill_0001_publish")
    with blank_session() as session:
        from app.services.ai_prompt_registry import PROMPT_KEYS
        from app.services.ai_prompt_seed import seed_prompt_registry

        session.add(
            AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text", template="STALE PROMPT TEXT (before drill down)",
                variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
            )
        )
        session.commit()
        seed_prompt_registry(session.get_bind())
        before = _production(session).version_id

        first = module.publish(session)
        assert isinstance(first, int) and first >= 2, first
        assert module.publish(session) is None

        published = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
            .one()
        )
        assert '"ranking_drill"' in published.template
        session.expire_all()
        assert _production(session).version_id == before


def test_a_products_or_categories_drill_is_a_sales_ranking_not_top_selling() -> None:
    """Review B1: after a ranking, "which products do these agents sell" must reach the drill
    (a top selling ask holds no listed rows and would answer for everyone)."""
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM

    assert "which products do these agents sell" in REPORT_ASK_ADDENDUM
    at = REPORT_ASK_ADDENDUM.index("which products do these agents sell")
    assert "sales_ranking" in REPORT_ASK_ADDENDUM[at:at + 300], REPORT_ASK_ADDENDUM[at:at + 300]


def test_the_reply_to_the_unsure_question_takes_its_count_from_that_question() -> None:
    """Review S4: the one exception to "top_n only from the current message"."""
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM

    at = REPORT_ASK_ADDENDUM.index("or across all customers?")
    assert "exception" in REPORT_ASK_ADDENDUM[at:at + 400], REPORT_ASK_ADDENDUM[at:at + 400]


def test_a_different_ranked_noun_is_never_a_count_refine() -> None:
    """Crew live pass: "top 10 customers for these sales agents" came back as a count refine of
    the agent ranking. The addendum says so before the refine list."""
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM

    rule = REPORT_ASK_ADDENDUM.index("never a refine")
    assert rule < REPORT_ASK_ADDENDUM.index("A message that ONLY changes:"), "the rule precedes the refine list"
    assert '"top 10 customers for these sales agents"' in REPORT_ASK_ADDENDUM


def test_global_only_when_said_and_doubt_is_unsure() -> None:
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM

    assert "In doubt, \"unsure\", never \"global\"" in REPORT_ASK_ADDENDUM

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
