"""CA-3 RED tests: the parser learns the chat report (plan contract, "Parser output").

`chat_report` is ONE new nullable object on the parse output (the `low_stock` object pattern, so
no shared enum changes): group_by / measure / subject. The prompt names the domain
`chat_analytics` and the intent `chat_report`; a prompt migration publishes it (the
`report_drill_0001_prompt` pattern) and a migration seeds the `chat_analytics` routing domain with
reveal key `chatbot.analytics`.

Pinned where the plan is silent (flagged to the captain):
* the prompt migration file is found by name: a file in alembic/versions whose stem contains
  `ca3` or `chat_analytics` or `chat_report` AND defines `publish`;
* the domain seed is found by content: a migration file that mentions `chatbot_domains`,
  `chat_analytics` and `chatbot.analytics`.

pin loosened by lead 10 Oct 2026: golden wording is graded live, not by prompt text (the
addendum must not carry the golden messages, so it is pinned structurally).
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
GROUP_BY = {"topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language"}
MEASURE = {"asks", "failed", "failure_rate", "contacts"}


def _object_def(prop: dict) -> dict:
    """The object schema of a nullable object property, whichever way the schema spells null."""
    if prop.get("type") == "object" or (isinstance(prop.get("type"), list) and "object" in prop["type"]):
        return prop
    for branch in prop.get("anyOf", []):
        if branch.get("type") == "object":
            return branch
    raise AssertionError(f"no object definition in {prop}")


def _nullable(prop: dict) -> bool:
    kind = prop.get("type")
    return (isinstance(kind, list) and "null" in kind) or any(b.get("type") == "null" for b in prop.get("anyOf", []))


# ------------------------------------------------------------------ schema


def test_chat_report_is_a_declared_required_nullable_object() -> None:
    prop = PARSE_OUTPUT_JSON_SCHEMA["properties"]["chat_report"]
    assert _nullable(prop), "null on every other message"
    assert "chat_report" in PARSE_OUTPUT_JSON_SCHEMA["required"], "strict mode: declared means required"
    assert _object_def(prop).get("additionalProperties") is False


def test_chat_report_group_by_enum_is_the_ten_axes_and_null() -> None:
    props = _object_def(PARSE_OUTPUT_JSON_SCHEMA["properties"]["chat_report"])["properties"]
    enum = props["group_by"]["enum"]
    assert set(e for e in enum if e is not None) == GROUP_BY, enum
    assert None in enum, "null = a total with no ranking axis"


def test_chat_report_measure_enum_is_the_four_measures_and_null() -> None:
    props = _object_def(PARSE_OUTPUT_JSON_SCHEMA["properties"]["chat_report"])["properties"]
    enum = props["measure"]["enum"]
    assert set(e for e in enum if e is not None) == MEASURE, enum
    assert None in enum, "null reads as asks"


def test_chat_report_subject_is_a_nullable_string_not_an_enum() -> None:
    """A chatbot domain name ("inventory", "promotion", ...): the domain list is data, not a code enum."""
    obj = _object_def(PARSE_OUTPUT_JSON_SCHEMA["properties"]["chat_report"])
    subject = obj["properties"]["subject"]
    assert "string" in subject["type"] and "null" in subject["type"], subject
    assert "enum" not in subject, subject
    assert set(obj["required"]) == {"group_by", "measure", "subject"}, obj["required"]


def test_the_shared_sales_group_by_enum_is_untouched() -> None:
    """The chat axes live inside `chat_report`: the shared enum gains nothing (UAC-23)."""
    enum = PARSE_OUTPUT_JSON_SCHEMA["properties"]["group_by"]["enum"]
    for word in ("topic", "failure_reason", "language"):
        assert word not in enum, (word, enum)


# ------------------------------------------------------------------ prompt


def test_the_prompt_names_the_domain_the_intent_and_the_object() -> None:
    for word in ("chat_analytics", "chat_report", "failure_rate"):
        assert word in SEMANTIC_PARSER_PROMPT, word


def test_the_prompt_teaches_the_four_languages_with_synthetic_examples() -> None:
    chunk = SEMANTIC_PARSER_PROMPT[SEMANTIC_PARSER_PROMPT.index("chat_report"):]
    from app.services.chatbot_parser_prompt import CHAT_REPORT_ADDENDUM

    assert chunk and "chat_analytics" in CHAT_REPORT_ADDENDUM and "chat_report" in CHAT_REPORT_ADDENDUM
    for word in GROUP_BY | MEASURE:
        assert word in CHAT_REPORT_ADDENDUM, word


def test_the_prompt_says_no_count_is_top_n_null() -> None:
    chunk = SEMANTIC_PARSER_PROMPT[SEMANTIC_PARSER_PROMPT.index("chat_report"):]
    assert "top_n null" in chunk


# ------------------------------------------------------------------ migrations


def _load(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prompt_migration() -> Path:
    found = [
        p for p in VERSIONS.glob("*.py")
        if any(tag in p.stem for tag in ("ca3", "chat_analytics", "chat_report")) and "def publish" in p.read_text()
    ]
    assert len(found) == 1, f"expected ONE prompt migration for the chat report, found {[p.name for p in found]}"
    return found[0]


def _production_label(session):
    return (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
        .first()
    )


def test_the_prompt_migration_revision_id_fits_and_publish_is_callable() -> None:
    module = _load(_prompt_migration(), "mig_ca3_prompt")
    assert len(module.revision) <= 32 and callable(module.publish)


def test_the_prompt_migration_publishes_a_new_version_label_unmoved_and_idempotent() -> None:
    module = _load(_prompt_migration(), "mig_ca3_prompt_publish")
    with blank_session() as session:
        from app.services.ai_prompt_registry import PROMPT_KEYS
        from app.services.ai_prompt_seed import seed_prompt_registry

        session.add(
            AIPromptVersion(
                name=PROMPT_NAME, version=1, type="text", template="STALE PROMPT TEXT (before the chat report)",
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
        assert "chat_report" in published.template and "chat_analytics" in published.template
        session.expire_all()
        after = _production_label(session)
        assert after is not None and after.version_id == version_before


def test_a_migration_seeds_the_chat_analytics_domain_with_its_reveal_key() -> None:
    seeds = [
        p for p in VERSIONS.glob("*.py")
        if all(w in p.read_text() for w in ("chatbot_domains", "chat_analytics", "chatbot.analytics"))
    ]
    assert seeds, "no migration seeds the chat_analytics domain (reveal_key chatbot.analytics)"
    text = seeds[0].read_text()
    assert "crm_report_ask" in text, "the domain's tool"
    assert "ON CONFLICT" in text.upper() or "NOT EXISTS" in text.upper(), "idempotent: insert if absent"
