"""Phase 2 RED tests - REPORT-ENGINE slice 1b: the parser learns `sales_ranking`.

`documentation/plans/chatbot/PLAN-report-engine.md` section 11, "Parser", "Contracts" and the
migration `report_engine_0001_prompt` (the `acct_ledger_0002_vocab` pattern).

Ambiguities flagged to the captain:
* `order_status` is a permissive `string_or_null` in the strict schema today (its values live in
  the prompt, not an enum), so "the order_status enum gains sales_ranking" cannot be asserted
  literally. Pinned: IF the schema ever constrains it with an enum it must hold `sales_ranking`,
  and the prompt names `sales_ranking` (below).
* section 11 says the addendum is appended AFTER `MEMORY_ADDENDUM`, while the ACCOUNT-LEDGER
  tests pin MEMORY as the prompt's tail. Only "REPORT_ASK_ADDENDUM is in the prompt" is pinned.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA
from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

from tests._pg_fixture import blank_session

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PROMPT_NAME = "chatbot_semantic_parser"


def test_order_status_schema_does_not_exclude_sales_ranking() -> None:
    prop = PARSE_OUTPUT_JSON_SCHEMA["properties"]["order_status"]
    if "enum" in prop:
        assert "sales_ranking" in prop["enum"], prop


def test_group_by_enum_gains_the_new_dimensions() -> None:
    enum = PARSE_OUTPUT_JSON_SCHEMA["properties"]["group_by"]["enum"]
    for word in ("sales_agent", "brand", "category", "channel"):
        assert word in enum, (word, enum)


def test_the_addendum_is_in_the_prompt() -> None:
    from app.services import chatbot_parser_prompt

    assert hasattr(chatbot_parser_prompt, "REPORT_ASK_ADDENDUM"), "REPORT_ASK_ADDENDUM is not defined"
    addendum = chatbot_parser_prompt.REPORT_ASK_ADDENDUM
    assert addendum in SEMANTIC_PARSER_PROMPT
    assert "sales_ranking" in addendum
    assert "top 3 salesman for Sorento brand last month" in addendum
    assert "sales_agent" in addendum


def test_the_addendum_keeps_top_selling_for_products_and_categories() -> None:
    from app.services import chatbot_parser_prompt

    addendum = getattr(chatbot_parser_prompt, "REPORT_ASK_ADDENDUM", "")
    assert "top_selling" in addendum, "ranking products stays top_selling"


def test_sales_figure_statuses_gain_sales_ranking() -> None:
    from app.services.chatbot import contracts

    assert "sales_ranking" in contracts.SALES_FIGURE_STATUSES, contracts.SALES_FIGURE_STATUSES


# ------------------------------------------------------------------ migration


def _load(filename: str, alias: str):
    path = VERSIONS / filename
    assert path.exists(), f"{filename} does not exist"
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_id_and_publish_callable() -> None:
    module = _load("report_engine_0001_prompt.py", "mig_report_engine_0001")
    assert module.revision == "report_engine_0001_prompt"
    assert len(module.revision) <= 32
    assert callable(module.publish)


def _production_label(session):
    return (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def test_migration_publishes_a_new_version_label_unmoved_and_idempotent() -> None:
    module = _load("report_engine_0001_prompt.py", "mig_report_engine_0001_publish")
    with blank_session() as session:
        from app.services.ai_prompt_registry import PROMPT_KEYS
        from app.services.ai_prompt_seed import seed_prompt_registry

        session.add(
            AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text", template="STALE PROMPT TEXT (before sales ranking)",
                variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
            )
        )
        session.commit()
        seed_prompt_registry(session.get_bind())
        before = _production_label(session)
        assert before is not None
        version_before = before.version_id

        first = module.publish(session)
        assert isinstance(first, int) and first >= 2, first
        assert module.publish(session) is None

        published = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == first)
            .one()
        )
        assert "sales_ranking" in published.template
        session.expire_all()
        after = _production_label(session)
        assert after is not None and after.version_id == version_before


# ------------------------------------------------------------------ owner hand-test message

OWNER_MESSAGE = "who's the top 3 salesman for sorento water closet this year"


def test_the_addendum_teaches_the_owner_hand_test_message_as_a_sales_agent_ranking() -> None:
    """The message that fell onto the old top selling path on dev is a worked example of the
    addendum, mapped to group_by "sales_agent" and the brand + category split."""
    from app.services import chatbot_parser_prompt

    addendum = getattr(chatbot_parser_prompt, "REPORT_ASK_ADDENDUM", "")
    at = addendum.find(OWNER_MESSAGE)
    assert at >= 0, "the owner's message is not an example in REPORT_ASK_ADDENDUM"
    # The example's own bullet: from its quote to the next bullet or the end of the block.
    rest = addendum[at + len(OWNER_MESSAGE):]
    bullet = rest.split("\n  - ", 1)[0]
    assert 'order_status "sales_ranking"' in bullet or "sales_ranking" in bullet, bullet
    assert 'group_by "sales_agent"' in bullet, bullet
    assert "top_n 3" in bullet, bullet
    assert "brand" in bullet and "category" in bullet, bullet


def test_the_prompt_stays_inside_its_budget_ceiling() -> None:
    """The existing budget test is the gate (`test_parser_prompt_budget.py`); this pins that the
    addendum's new example is counted by it, not skipped: the rendered prompt carries it."""
    assert OWNER_MESSAGE in SEMANTIC_PARSER_PROMPT


# ------------------------------------------------------------------ semantic only (owner rule, 4 Oct 2026)
# The parser decides meaning; the code reads no message text. What the removed regexes did is now
# taught by the addendum, pinned here.


def _addendum() -> str:
    from app.services import chatbot_parser_prompt

    return getattr(chatbot_parser_prompt, "REPORT_ASK_ADDENDUM", "")


def _bullet(addendum: str, quote: str) -> str:
    at = addendum.find(quote)
    assert at >= 0, f"REPORT_ASK_ADDENDUM has no example {quote!r}"
    return addendum[at + len(quote):].split("\n  - ", 1)[0]


def test_the_schema_declares_ranking_refine_and_measure() -> None:
    props = PARSE_OUTPUT_JSON_SCHEMA["properties"]
    assert "ranking_refine" in props and "measure" in props, sorted(props)
    assert "ranking_refine" in PARSE_OUTPUT_JSON_SCHEMA["required"]
    assert "measure" in PARSE_OUTPUT_JSON_SCHEMA["required"]
    measure = props["measure"]
    if "enum" in measure:
        assert {"qty", "amount"} <= set(measure["enum"]), measure


def test_the_addendum_teaches_ranking_refine_after_a_sales_ranking() -> None:
    a = _addendum()
    assert "ranking_refine" in a and "Previous response" in a
    for example in ('"5"', '"top 10"', '"this year"', '"2025"', '"last month"', '"ordered"', "by quantity"):
        assert example in a, example
    assert "ranking_refine true" in a and "ranking_refine false" in a


def test_the_addendum_says_a_new_axis_or_subject_is_a_new_ask_with_its_own_count_and_dates() -> None:
    a = _addendum()
    bullet = _bullet(a, '"top 3 salesman for sorento"')
    assert "ranking_refine false" in bullet, bullet
    assert "top salesman" in a and "top_n null" in a, "a message with no count leaves top_n null"


def test_the_addendum_teaches_measure() -> None:
    a = _addendum()
    assert 'measure "qty"' in a and "measure null" in a
    owner = _bullet(a, OWNER_MESSAGE)
    assert "measure null" in owner, owner
    assert '"qty"' in _bullet(a, "by quantity") or 'measure "qty"' in a


def test_the_addendum_says_the_ranked_noun_is_never_an_entity() -> None:
    a = _addendum().lower()
    assert "never an entity" in a
    for noun in ("salesman", "customers", "sa"):
        assert noun in a


def test_the_addendum_keeps_product_rankings_top_selling() -> None:
    assert '"top 10 sales items for sorento"' in _addendum()
    assert "top_selling" in _bullet(_addendum(), '"top 10 sales items for sorento"')


def test_the_addendum_keeps_an_sa_coded_agent_a_filter_under_top_selling() -> None:
    bullet = _bullet(_addendum(), '"top SA01 items')
    assert "top_selling" in bullet and "sales_agent" in bullet, bullet


def test_the_addendum_teaches_the_how_many_answer_as_the_parsers_top_n() -> None:
    """The reply to "How many? For example top 5." is read by the parser, not by code."""
    a = _addendum()
    assert "How many? For example top 5." in a
    at = a.find("How many? For example top 5.")
    bullet = a[at:].split("\n  - ", 1)[0]
    for reply in ('"5"', '"top 5"', '"five"'):
        assert reply in bullet, (reply, bullet)
    assert "sales_ranking" in bullet and "top_n 5" in bullet, bullet


def test_the_addendum_says_a_people_ranking_is_a_new_ask_even_over_an_open_top_selling_question() -> None:
    a = _addendum()
    at = a.find("A ranking of PEOPLE or ACCOUNTS")
    assert at >= 0, "the people-ranking rule is missing"
    rule = a[at:].split("\n  - ", 1)[0].split("\nNOT a sales ranking", 1)[0]
    assert 'order_status "sales_ranking"' in rule and "own group_by" in rule, rule
    assert "By quantity or by amount?" in rule and "not a reply to that question" in rule, rule
    assert "unless it ranks people or accounts" in SEMANTIC_PARSER_PROMPT


def test_the_addendum_pins_the_singular_no_count_and_the_noun_never_an_entity_of_any_hint() -> None:
    a = _addendum()
    assert "A singular" in a and "NO count: top_n null" in a and "(not 1)" in a
    assert "also after a ranking" in a
    assert "never an entity of ANY hint" in a and "account(s)" in a
