"""CA-3 revision 10 Oct 2026: the focused chat report extractor (RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md "Revision 10 Oct
2026: focused extractor". `app/services/chatbot/analytics/chat_report_extractor.py`
`extract_chat_report(db, message_text, *, today) -> dict | None` returns
`{group_by, measure, subject, top_n, date_from, date_to}` and NEVER raises.

No network: the provider seam is `get_provider` imported at module level of the extractor (the
`ideation_extractor` pattern, patched as `<module>.get_provider`); `resolve_api_key` is patched
with `create=True` because the plan names it but not where it lives. Red today with
ModuleNotFoundError until the module exists.

Pinned where the plan is silent (flagged to the captain):
* the migration is found by a filename containing `chat_report_extractor` or `ca3_0003`;
* a `top_n` that is not null must be an int from 1 to 1000, else the output is invalid (None).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.services import ai_prompt_registry
from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.llm_provider import ChatResult
from tests._pg_fixture import blank_session

MODULE = "app.services.chatbot.analytics.chat_report_extractor"
VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
GROUP_BY = {"topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language"}
MEASURE = {"asks", "failed", "failure_rate", "contacts"}
KEYS = {"group_by", "measure", "subject", "top_n", "date_from", "date_to", "askers", "category_words", "brand_words",
        "customer_words", "product_words"}  # lead sign-off 10 Oct (owner live round 3: customer_words / product_words)
TODAY = date(2026, 10, 10)
GOOD = {"group_by": "topic", "measure": "asks", "subject": None, "top_n": 5,
        "date_from": "2026-10-05", "date_to": "2026-10-11",
        "askers": [], "category_words": [], "brand_words": [], "customer_words": [], "product_words": []}


@pytest.fixture
def db() -> Session:
    with blank_session() as session:
        ai_prompt_registry.bust_cache()
        yield session
        ai_prompt_registry.bust_cache()


@pytest.fixture
def configured(db: Session) -> Session:
    from app.models.chatbot_policy import ChatbotDomain

    cfg = AIAssistantConfigService(db).get()
    cfg.api_key_ciphertext = "fake-key"
    cfg.provider = "openai"
    cfg.model = "gpt-4o-mini"
    cfg.is_enabled = True
    for name in ("inventory", "promotion"):
        db.add(ChatbotDomain(name=name, label=name.title()))
    db.commit()
    return db


class _Provider:
    def __init__(self, content=None, error: Exception | None = None):
        self.content, self.error, self.calls = content, error, []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        return ChatResult(content=self.content, prompt_tokens=1, completion_tokens=1, total_tokens=2)


def _run(db, provider, text="top 5 questions this week"):
    from app.services.chatbot.analytics import chat_report_extractor

    with patch(f"{MODULE}.get_provider", return_value=provider, create=True), \
         patch(f"{MODULE}.resolve_api_key", return_value="fake-key", create=True):
        return chat_report_extractor.extract_chat_report(db, text, today=TODAY)


# ------------------------------------------------------------------ output


def test_a_valid_reply_returns_exactly_the_six_keys(configured):
    """Eleven keys, lead sign-off 10 Oct (owner live round 3: customer_words / product_words): the old six plus askers, category_words, brand_words, customer_words, product_words."""
    out = _run(configured, _Provider(json.dumps(GOOD)))
    assert out == GOOD and set(out) == KEYS


def test_a_total_with_nothing_else_is_allowed(configured):
    nothing = {"group_by": None, "measure": None, "subject": None, "top_n": None, "date_from": None, "date_to": None,
               "askers": [], "category_words": [], "brand_words": [], "customer_words": [], "product_words": []}
    assert _run(configured, _Provider(json.dumps(nothing))) == nothing


def test_the_message_and_today_reach_the_provider(configured):
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider, text="top 5 questions this week")
    (call,) = provider.calls
    blob = json.dumps(call["messages"])
    assert "top 5 questions this week" in blob and "2026-10-10" in blob, "the model needs today to read 'this week'"


def test_the_schema_sent_is_strict_with_the_contract_enums(configured):
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    schema = provider.calls[0]["json_schema"]
    schema = schema.get("schema", schema)
    assert schema["additionalProperties"] is False and set(schema["required"]) == KEYS, schema
    props = schema["properties"]
    assert {v for v in props["group_by"]["enum"] if v} == GROUP_BY | {"asker", "salesperson", "category", "brand"} and None in props["group_by"]["enum"]
    assert {v for v in props["measure"]["enum"] if v} == MEASURE and None in props["measure"]["enum"]
    subject = props["subject"]["enum"]
    assert {"inventory", "promotion"} <= set(subject) and None in subject, "chatbot domain names plus null"
    assert provider.calls[0]["temperature"] == 0.0


# ------------------------------------------------------------------ never raises


@pytest.mark.parametrize(
    "provider",
    [
        _Provider(error=RuntimeError("provider down")),
        _Provider(error=TimeoutError("slow")),
        _Provider(content="not json at all"),
        _Provider(content=""),
        _Provider(content=json.dumps({**GOOD, "group_by": "nonsense"})),
        _Provider(content=json.dumps({**GOOD, "measure": "amount"})),
        _Provider(content=json.dumps({**GOOD, "top_n": 0})),
        _Provider(content=json.dumps({**GOOD, "top_n": "five"})),
        _Provider(content=json.dumps({k: v for k, v in GOOD.items() if k != "measure"})),
    ],
    ids=["error", "timeout", "bad_json", "empty", "group_by_out_of_enum", "measure_out_of_enum",
         "top_n_zero", "top_n_text", "missing_key"],
)
def test_any_failure_returns_none_and_never_raises(configured, provider):
    assert _run(configured, provider) is None


def test_no_api_key_returns_none(db):
    cfg = AIAssistantConfigService(db).get()
    cfg.api_key_ciphertext = None
    db.commit()
    from app.services.chatbot.analytics import chat_report_extractor

    with patch(f"{MODULE}.resolve_api_key", return_value=None, create=True), \
         patch(f"{MODULE}.get_provider", side_effect=AssertionError("no key: no call"), create=True):
        assert chat_report_extractor.extract_chat_report(db, "top 5 questions", today=TODAY) is None


# ------------------------------------------------------------------ prompt registry


def test_the_prompt_key_is_registered_with_a_fallback():
    spec = PROMPT_KEYS["chat_report_extractor"]
    text = spec.fallback()
    assert isinstance(text, str) and len(text) > 200


def test_render_serves_the_fallback_until_a_version_is_published(db):
    text, _version = ai_prompt_registry.render(db, "chat_report_extractor")
    assert text == PROMPT_KEYS["chat_report_extractor"].fallback()


def test_a_published_version_wins_over_the_fallback(db):
    from app.services.ai_prompt_seed import seed_prompt_registry
    from app.services.ai_prompt_service import AIPromptService

    seed_prompt_registry(db.get_bind())
    svc = AIPromptService(db)
    saved = svc.save_version("chat_report_extractor", template="PUBLISHED EXTRACTOR TEXT", commit_message="v2", user_id=None)
    svc.set_label("chat_report_extractor", label="production", version_id=saved["id"], user_id=None)
    text, version = ai_prompt_registry.render(db, "chat_report_extractor")
    assert text == "PUBLISHED EXTRACTOR TEXT" and version == 2


def test_the_extractor_sends_the_registry_prompt(configured):
    from app.services.ai_prompt_seed import seed_prompt_registry
    from app.services.ai_prompt_service import AIPromptService

    seed_prompt_registry(configured.get_bind())
    svc = AIPromptService(configured)
    saved = svc.save_version("chat_report_extractor", template="REGISTRY SYSTEM TEXT", commit_message="v2", user_id=None)
    svc.set_label("chat_report_extractor", label="production", version_id=saved["id"], user_id=None)
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    system = next(m for m in provider.calls[0]["messages"] if m["role"] == "system")
    assert system["content"] == "REGISTRY SYSTEM TEXT"


# ------------------------------------------------------------------ migration


def _migration() -> Path:
    found = [p for p in VERSIONS.glob("*.py") if "chat_report_extractor" in p.stem or "ca3_0003" in p.stem]
    assert len(found) == 1, f"expected ONE extractor prompt migration, found {[p.name for p in found]}"
    return found[0]


def test_a_migration_publishes_the_extractor_prompt_on_the_single_chain():
    path = _migration()
    spec = importlib.util.spec_from_file_location("mig_ca3_extractor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.revision) <= 32 and module.down_revision, module.revision
    text = path.read_text()
    assert "chat_report_extractor" in text and ("seed_prompt_registry" in text or "AIPromptVersion" in text)


def test_seeding_the_registry_gives_the_key_a_production_label(db):
    from app.models.ai_prompt import AIPromptLabel
    from app.services.ai_prompt_seed import seed_prompt_registry

    seed_prompt_registry(db.get_bind())
    label = db.query(AIPromptLabel).filter_by(name="chat_report_extractor", label="production").one()
    assert label.version_id


# ------------------------------------------------------------------ re-review: no hang


def test_the_provider_call_is_bounded_by_the_shared_attempt_deadline(configured):
    from app.services.chatbot.llm_call import ATTEMPT_TIMEOUT_SECONDS

    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    timeout = provider.calls[0].get("timeout")
    assert timeout is not None and 0 < timeout <= ATTEMPT_TIMEOUT_SECONDS, (
        "the provider default (600 s) would hang the turn", timeout,
    )


# ------------------------------------------------------------------ prompt-teaching pins (generalisation)


def _prompt() -> str:
    return PROMPT_KEYS["chat_report_extractor"].fallback().lower()


def test_the_prompt_teaches_quarters_as_periods_in_all_three_languages():
    """Prompt-teaching pins only: they check the fallback text names the idea, not exact
    sentences. The model's behaviour is graded live (third fresh held-out set)."""
    text = _prompt()
    for word in ("quarter", "q3", "suku", "季度"):
        assert word in text, word
    assert "three" in text and "july" in text and "september" in text, "a quarter is three calendar months (Q3 = July to September)"


def test_the_prompt_teaches_that_how_many_is_one_total():
    """Prompt-teaching pins; behaviour graded live. A 'how many' question is ONE total unless
    the message splits it or asks for a list."""
    text = _prompt()
    for word in ("how many", "berapa", "多少", "total"):
        assert word in text, word
    for splitter in ("by", "per", "each", "ikut", "每"):
        assert splitter in text, splitter
    assert "list" in text and "null" in text


def test_the_prompt_describes_each_domain_with_its_label_and_switch_words(configured):
    from app.models.chatbot_policy import ChatbotDomain

    promo = configured.query(ChatbotDomain).filter_by(name="promotion").one()
    promo.label, promo.switch_words = "promotions", ["promo"]
    configured.add(ChatbotDomain(name="order", label="orders", switch_words=["delivery", "penghantaran"]))
    configured.commit()
    provider = _Provider(json.dumps(GOOD))
    _run(configured, provider)
    system = next(m for m in provider.calls[0]["messages"] if m["role"] == "system")["content"].lower()
    for words in (("order", "orders", "delivery", "penghantaran"), ("promotion", "promotions", "promo")):
        for word in words:
            assert word in system, word


def test_the_prompt_teaches_that_the_agent_of_the_asking_customer_is_a_customer_row_not_a_salesperson_ranking():
    """Prompt-teaching pin, loose wording: "which sales agent does the customer belong to that
    asked the most" is group_by customer (each customer row shows its agent), not salesperson."""
    text = _prompt()
    at = text.find("agent")
    assert at != -1, "the prompt never mentions a customer's sales agent"
    near = text[max(0, at - 300): at + 300]
    assert "customer" in near and ("row" in near or "shown" in near or "shows" in near), near
