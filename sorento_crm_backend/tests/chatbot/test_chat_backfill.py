"""CA-4 history backfill: `app.services.chatbot.analytics.backfill` (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca4-backfill-09oct.md, rules B1 to B10;
UAC AC-1 to AC-18. Postgres only (the `tests/chatbot/conftest.py::session_factory` scratch
schema); synthetic rows only (prefix ZZT), no real message, name or number.

The model and the resolver are injected (`chat`, `resolve`), exactly as in the CA-2 labeller
tests, so nothing here reaches a provider.
"""
from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timedelta

import pytest

from tests.chatbot._chat_facts_helpers import (
    CUSTOMER_WORDS,
    REPLY_WORDS,
    models_mod,
    register_company_scope,
    seed_catalogue,
    seed_contact,
    seed_turn,
)
from tests.chatbot.test_chat_topic_labeller import ask, seed_taxonomy


def backfill():
    return importlib.import_module("app.services.chatbot.analytics.backfill")


def labeller():
    return importlib.import_module("app.services.chatbot.analytics.labeller")


#: Inside era A (UTC, naive, as `chat_histories.sent_at` is stored).
A_TIME = datetime(2026, 5, 20, 3, 0)


# --------------------------------------------------------------------------- seeding


def seed_history(
    session_factory,
    contact: str,
    *,
    kind: str = "incoming",
    words: str = CUSTOMER_WORDS,
    at: datetime = A_TIME,
    message_id: str | None = None,
    exec_id: str | None = None,
    parser_raw=None,
    result=None,
    sender_source: str | None = None,
    media_url: str | None = None,
) -> int:
    from app.models.chat_history import ChatHistory

    db = session_factory()
    row = ChatHistory(
        channel="whatsapp",
        contact_id=contact,
        phone_number="+60100000000",
        message=words,
        sent_at=at,
        created_at=at,  # real pre-cutover rows were written then (the dedupe index keys on it)
        type=kind,
        message_id=message_id,
        turn_id=exec_id,
        state_trace={"v": 1, "parser_raw": parser_raw} if parser_raw is not None or kind == "incoming" else None,
        result=result,
        sender_source=sender_source,
        media_url=media_url,
    )
    db.add(row)
    db.commit()
    return int(row.id)


def contact_id() -> str:
    return f"ZZT-bf-{uuid.uuid4().hex[:8]}"


def answer_for(entries: dict, *, language: str = "en") -> str:
    """`entries`: key -> (outcome, [asks])."""
    return json.dumps(
        {
            "labels": [
                {"key": key, "language": language, "outcome": outcome, "asks": asks}
                for key, (outcome, asks) in entries.items()
            ]
        }
    )


class FakeChat:
    """Answers every batch with `answer(keys, rows)`; records the calls."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[list[dict], dict]] = []

    def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        return self.answer(labeller().batch_keys(messages), rows_of(messages))


def rows_of(messages) -> list[dict]:
    for message in messages:
        if message["role"] == "user":
            return json.loads(message["content"])["rows"]
    return []


def answered(subject="inventory", leaf="inventory.availability", outcome="answered", **kw):
    """Every key answered with one ask."""
    return FakeChat(lambda keys, rows: answer_for({k: (outcome, [ask(subject, leaf, **kw)]) for k in keys}))


def no_products(word, contact_respond_id):
    return []


def run(session_factory, chat, era="A", resolve=no_products, **kwargs):
    return backfill().run(session_factory(), era=era, chat=chat, resolve=resolve, model="zzt-model", **kwargs)


def facts(session_factory, history_id=None):
    models = models_mod()
    q = session_factory().query(models.ChatAskFact).filter(models.ChatAskFact.source == "history_backfill")
    if history_id is not None:
        q = q.filter(models.ChatAskFact.history_message_id == history_id)
    return q.order_by(models.ChatAskFact.history_message_id, models.ChatAskFact.ask_seq).all()


def runs(session_factory):
    models = models_mod()
    return session_factory().query(models.ChatBackfillRun).order_by(models.ChatBackfillRun.started_at).all()


@pytest.fixture
def taxonomy(session_factory):
    register_company_scope()
    seed_taxonomy(session_factory)


# --------------------------------------------------------------------------- eras


def test_era_windows_are_malaysia_dates_in_utc():
    bf = backfill()
    assert bf.era_window("A") == (datetime(2026, 4, 16, 16, 0), datetime(2026, 7, 21, 16, 0))
    assert bf.era_window("B") == (datetime(2026, 7, 21, 16, 0), datetime(2026, 8, 18, 16, 0))
    assert bf.era_window("C") == (datetime(2026, 8, 18, 16, 0), datetime(2026, 9, 4, 16, 0))
    assert bf.era_window("D") == (datetime(2026, 9, 4, 16, 0), datetime(2026, 9, 22, 16, 0))


def test_an_unknown_era_is_refused():
    with pytest.raises(ValueError):
        backfill().era_window("Z")


# --------------------------------------------------------------------------- selection


def test_only_incoming_rows_inside_the_era_are_pending(session_factory, taxonomy):
    c = contact_id()
    inside = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", at=A_TIME + timedelta(seconds=5))
    # 17 Apr 00:00 MYT is the first moment of era A; 16 Apr 15:59 UTC is before it.
    first = seed_history(session_factory, c, at=datetime(2026, 4, 16, 16, 0))
    seed_history(session_factory, c, at=datetime(2026, 4, 16, 15, 59))
    seed_history(session_factory, c, at=datetime(2026, 7, 21, 16, 0))  # era B
    assert sorted(backfill().pending(session_factory(), "A")) == sorted([inside, first])


def test_a_message_the_engine_traced_is_never_backfilled(session_factory, taxonomy):
    c = contact_id()
    seed_turn(session_factory, c, message_id="ZZT-m-traced")
    seed_turn(session_factory, c, message_id="ZZT-m-test", is_test=True)
    when = datetime(2026, 9, 10, 3, 0)
    seed_history(session_factory, c, at=when, message_id="ZZT-m-traced")
    test_only = seed_history(session_factory, c, at=when + timedelta(minutes=1), message_id="ZZT-m-test")
    untraced = seed_history(session_factory, c, at=when + timedelta(minutes=2), message_id="ZZT-m-none")
    assert sorted(backfill().pending(session_factory(), "D")) == sorted([test_only, untraced])


def test_exact_duplicates_collapse_to_the_lowest_id(session_factory, taxonomy):
    c = contact_id()
    first = seed_history(session_factory, c, message_id="ZZT-dup")
    seed_history(session_factory, c, message_id="ZZT-dup", at=A_TIME + timedelta(seconds=1))
    no_id = seed_history(session_factory, c, words="ZZT same words", at=A_TIME + timedelta(minutes=5))
    seed_history(session_factory, c, words="ZZT same words", at=A_TIME + timedelta(minutes=5, microseconds=300))
    later = seed_history(session_factory, c, words="ZZT same words", at=A_TIME + timedelta(minutes=5, seconds=1))
    assert sorted(backfill().pending(session_factory(), "A")) == sorted([first, no_id, later])


def test_a_backfilled_message_is_not_pending_and_a_rerun_writes_nothing(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c)
    chat = answered()
    assert run(session_factory, chat)["messages_read"] == 1
    assert backfill().pending(session_factory(), "A") == []
    second = answered()
    out = run(session_factory, second)
    assert out["messages_read"] == 0
    assert second.calls == []
    assert len(facts(session_factory)) == 1


# --------------------------------------------------------------------------- reply link


def test_the_reply_is_the_outgoing_rows_of_the_same_execution(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c, exec_id="ZZT-exec-1")
    seed_history(session_factory, c, kind="outgoing", words="ZZT-OTHER-REPLY", at=A_TIME + timedelta(seconds=2), exec_id="ZZT-exec-0")
    seed_history(session_factory, c, kind="outgoing", words="ZZT-LINKED-REPLY", at=A_TIME + timedelta(seconds=4), exec_id="ZZT-exec-1")
    chat = answered()
    run(session_factory, chat)
    row = rows_of(chat.calls[0][0])[0]
    assert "ZZT-LINKED-REPLY" in row["bot_reply_start"]
    assert "ZZT-OTHER-REPLY" not in row["bot_reply_start"]


def test_with_no_execution_link_the_reply_is_the_outgoing_rows_before_the_next_incoming(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c, exec_id="ZZT-exec-lonely")  # no outgoing carries it
    seed_history(session_factory, c, kind="outgoing", words="ZZT-PART-ONE", at=A_TIME + timedelta(seconds=3))
    seed_history(session_factory, c, kind="outgoing", words="ZZT-PART-TWO", at=A_TIME + timedelta(seconds=4))
    seed_history(session_factory, c, at=A_TIME + timedelta(minutes=1), words="ZZT next ask")
    seed_history(session_factory, c, kind="outgoing", words="ZZT-LATER-REPLY", at=A_TIME + timedelta(minutes=1, seconds=3))
    other = contact_id()
    seed_history(session_factory, other, kind="outgoing", words="ZZT-OTHER-CONTACT", at=A_TIME + timedelta(seconds=2))
    chat = answered()
    run(session_factory, chat)
    rows = sorted(rows_of(chat.calls[0][0]), key=lambda r: int(r["key"]))
    assert "ZZT-PART-ONE" in rows[0]["bot_reply_start"] and "ZZT-PART-TWO" in rows[0]["bot_reply_start"]
    assert "ZZT-LATER-REPLY" not in rows[0]["bot_reply_start"]
    assert "ZZT-OTHER-CONTACT" not in rows[0]["bot_reply_start"]
    assert "ZZT-LATER-REPLY" in rows[1]["bot_reply_start"]


def test_the_reply_the_model_sees_is_cut_to_300_characters(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words="x" * 900, at=A_TIME + timedelta(seconds=2))
    chat = answered()
    run(session_factory, chat)
    assert len(rows_of(chat.calls[0][0])[0]["bot_reply_start"]) == 300


# --------------------------------------------------------------------------- outcome


def test_no_reply_at_all_is_no_reply_and_not_a_failure(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, at=A_TIME + timedelta(minutes=1), words="ZZT again")
    run(session_factory, answered(outcome="cannot_do"))
    fact = facts(session_factory, h)[0]
    assert fact.outcome == "no_reply"
    assert fact.failure_reason is None


def test_staff_rows_only_is_a_handover(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words="ZZT staff", at=A_TIME + timedelta(seconds=9), sender_source="user")
    chat = answered(outcome="answered")
    run(session_factory, chat)
    assert facts(session_factory, h)[0].outcome == "handover"
    assert rows_of(chat.calls[0][0])[0]["bot_reply_start"] == ""


def test_the_models_outcome_is_kept_and_cannot_do_is_a_failure(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    run(session_factory, answered(outcome="cannot_do"))
    fact = facts(session_factory, h)[0]
    assert fact.outcome == "cannot_do"
    assert fact.failure_reason == "cannot_do"


@pytest.mark.parametrize("bad", ["missed", "technical", "phantom", "whatever", None])
def test_an_outcome_outside_the_list_leaves_the_message_for_the_next_run(session_factory, taxonomy, bad):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    out = run(session_factory, answered(outcome=bad))
    assert facts(session_factory, h) == []
    assert out["messages_skipped"] == 1
    assert backfill().pending(session_factory(), "A") == [h]


def test_the_schema_holds_the_model_to_the_outcome_list(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c)
    chat = answered()
    run(session_factory, chat)
    schema = chat.calls[0][1]
    label = schema["properties"]["labels"]["items"]
    assert set(label["properties"]["outcome"]["enum"]) == set(backfill().MODEL_OUTCOMES)
    assert "outcome" in label["required"]
    assert set(backfill().MODEL_OUTCOMES) == {
        "answered", "answered_empty", "clarified", "refused_policy", "cannot_do", "handover", "noise",
    }


# --------------------------------------------------------------------------- subject


def _parser(domain, intent="stock"):
    return {backfill().PARSER_DOMAIN_KEY: domain, backfill().PARSER_INTENT_KEY: intent}


def test_saved_parser_output_fixes_the_subject(session_factory, taxonomy):
    c = contact_id()
    when = datetime(2026, 8, 1, 3, 0)  # era B
    h = seed_history(session_factory, c, at=when, parser_raw=_parser("inventory", "stock"))
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=when + timedelta(seconds=2))
    chat = answered(subject="incoming", leaf="incoming.product_eta")
    run(session_factory, chat, era="B")
    assert rows_of(chat.calls[0][0])[0]["subject"] == "inventory"
    fact = facts(session_factory, h)[0]
    assert fact.domain == "inventory"
    assert fact.intent == "stock"
    assert fact.topic_code == "inventory.other"  # the model's leaf was under another subject


@pytest.mark.parametrize("parser_raw", [None, "not an object", {"other": 1}])
def test_with_no_usable_parser_output_the_model_picks_a_known_subject(session_factory, taxonomy, parser_raw):
    c = contact_id()
    when = datetime(2026, 8, 1, 3, 0)
    h = seed_history(session_factory, c, at=when, parser_raw=parser_raw)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=when + timedelta(seconds=2))
    chat = answered(subject="incoming", leaf="incoming.product_eta")
    run(session_factory, chat, era="B")
    assert rows_of(chat.calls[0][0])[0]["subject"] is None
    fact = facts(session_factory, h)[0]
    assert (fact.domain, fact.topic_code, fact.intent) == ("incoming", "incoming.product_eta", None)


def test_a_parser_domain_that_is_not_a_subject_is_ignored(session_factory, taxonomy):
    c = contact_id()
    when = datetime(2026, 8, 1, 3, 0)
    h = seed_history(session_factory, c, at=when, parser_raw=_parser("zzt_nowhere"))
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=when + timedelta(seconds=2))
    run(session_factory, answered(subject="incoming", leaf="incoming.other"), era="B")
    assert facts(session_factory, h)[0].domain == "incoming"


def test_an_unknown_subject_from_the_model_leaves_the_message(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    out = run(session_factory, answered(subject="zzt_nowhere", leaf="zzt_nowhere.x"))
    assert facts(session_factory, h) == []
    assert out["messages_skipped"] == 1


def test_the_general_subject_has_no_domain(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    run(session_factory, answered(subject="general", leaf="general.other"))
    fact = facts(session_factory, h)[0]
    assert (fact.domain, fact.topic_code) == (None, "general.other")


# --------------------------------------------------------------------------- the fact row


def test_a_fact_row_records_the_message_and_the_label_audit(session_factory, taxonomy):
    c = contact_id()
    ids = seed_contact(session_factory, c, customer=True)
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    run(session_factory, answered(confidence=0.77), )
    fact = facts(session_factory, h)[0]
    assert fact.source == "history_backfill"
    assert fact.turn_id is None
    assert fact.history_message_id == h
    assert fact.ask_seq == 1
    assert fact.contact_respond_id == c
    assert fact.audience == "dealer" and fact.contact_type == "dealer"
    assert fact.customer_id == ids["customer_id"]
    assert fact.asked_at.replace(tzinfo=None) == A_TIME
    assert fact.channel == "whatsapp"
    assert fact.topic_code == "inventory.availability"
    assert fact.label_confidence == pytest.approx(0.77)
    assert fact.label_model == "zzt-model"
    assert fact.label_prompt_version == backfill().PROMPT_VERSION
    assert fact.taxonomy_version == 2
    assert fact.language == "en"
    assert fact.tools == []
    assert fact.is_media is False


def test_nothing_the_customer_or_the_bot_wrote_is_stored(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    run(session_factory, answered())
    fact = facts(session_factory, h)[0]
    for column in fact.__table__.columns:
        value = str(getattr(fact, column.name))
        assert "ZZT-SECRET" not in value, column.name
    for r in runs(session_factory):
        for column in r.__table__.columns:
            assert "ZZT-SECRET" not in str(getattr(r, column.name)), column.name


def test_a_second_ask_in_the_message_is_its_own_row(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    chat = FakeChat(
        lambda keys, rows: answer_for(
            {k: ("answered", [ask("inventory", "inventory.availability"), ask("master_products", "master_products.price")]) for k in keys}
        )
    )
    run(session_factory, chat)
    rows = facts(session_factory, h)
    assert [(r.ask_seq, r.domain, r.topic_code) for r in rows] == [
        (1, "inventory", "inventory.availability"),
        (2, "master_products", "master_products.price"),
    ]
    assert rows[1].history_message_id == h and rows[1].turn_id is None
    assert rows[1].outcome == rows[0].outcome


def test_a_message_with_no_text_is_noise_without_a_model_call(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c, words="", media_url="https://zzt.invalid/a.jpg")
    chat = answered()
    run(session_factory, chat)
    assert chat.calls == []
    fact = facts(session_factory, h)[0]
    assert (fact.outcome, fact.topic_code, fact.is_media, fact.domain) == ("noise", "general.noise", True, None)


# --------------------------------------------------------------------------- items


def test_named_products_go_through_the_exact_resolver_rule(session_factory, taxonomy):
    c = contact_id()
    catalogue = seed_catalogue(session_factory, product_codes=("ZZTBF-P1", "ZZTBF-P2"))
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    hits = {
        "zzt p1": [{"entity_type": "product", "uuid": catalogue["products"]["ZZTBF-P1"], "match_tier": "exact"}],
        "zzt p2": [{"entity_type": "product", "uuid": catalogue["products"]["ZZTBF-P2"], "match_tier": "trgm"}],
    }
    run(
        session_factory,
        answered(products=(("zzt p1", 0.9), ("zzt p2", 0.95))),
        resolve=lambda word, contact: hits.get(word, []),
    )
    from tests.chatbot._chat_facts_helpers import item_rows

    items = item_rows(session_factory, facts(session_factory, h)[0].id)
    assert [(i.ref_id, i.role, i.resolved_by) for i in items] == [
        (catalogue["products"]["ZZTBF-P1"], "named", "resolver_pass")
    ]


def test_shown_products_come_from_the_reply_result_ids(session_factory, taxonomy):
    c = contact_id()
    catalogue = seed_catalogue(session_factory, product_codes=("ZZTBF-S1",))
    h = seed_history(session_factory, c)
    seed_history(
        session_factory,
        c,
        kind="outgoing",
        words=REPLY_WORDS,
        at=A_TIME + timedelta(seconds=2),
        result=[
            {"idx": 1, "entity_type": "product", "uuid": catalogue["products"]["ZZTBF-S1"], "label": "ZZT"},
            {"idx": 2, "entity_type": "product", "uuid": str(uuid.uuid4()), "label": "ZZT gone"},
            {"idx": 3, "entity_type": "order", "uuid": str(uuid.uuid4())},
            {"idx": 4, "entity_type": "product", "uuid": "not-a-uuid"},
        ],
    )
    run(session_factory, answered())
    from tests.chatbot._chat_facts_helpers import item_rows

    items = item_rows(session_factory, facts(session_factory, h)[0].id)
    assert [(i.kind, i.ref_id, i.role, i.resolved_by) for i in items] == [
        ("product", catalogue["products"]["ZZTBF-S1"], "shown", "history_result")
    ]


# --------------------------------------------------------------------------- the run


def test_estimate_counts_and_projects_without_a_call_or_a_write(session_factory, taxonomy):
    c = contact_id()
    for minute in range(3):
        seed_history(session_factory, c, at=A_TIME + timedelta(minutes=minute), words=f"ZZT ask {minute}")
    out = backfill().estimate(session_factory(), "A", batch_size=2)
    assert out["era"] == "A"
    assert out["eligible"] == 3
    assert out["pending"] == 3
    assert out["calls"] == 2
    assert out["tokens_in"] > 0 and out["tokens_out"] > 0
    assert facts(session_factory) == []
    assert runs(session_factory) == []


def test_limit_and_batches_with_one_commit_per_batch_recorded_on_the_run(session_factory, taxonomy):
    c = contact_id()
    for minute in range(5):
        seed_history(session_factory, c, at=A_TIME + timedelta(minutes=minute), words=f"ZZT ask {minute}")
    chat = answered()
    out = run(session_factory, chat, limit=3, batch_size=2)
    assert len(chat.calls) == 2
    assert out["messages_read"] == 3
    assert len(facts(session_factory)) == 3
    (r,) = runs(session_factory)
    assert (r.era, r.status, r.row_limit) == ("A", "done", 3)
    assert (r.messages_eligible, r.messages_read, r.messages_skipped, r.asks_written) == (5, 3, 0, 3)
    assert (r.calls, r.failed_calls) == (2, 0)
    assert r.model == "zzt-model" and r.prompt_version == backfill().PROMPT_VERSION and r.taxonomy_version == 2
    assert r.last_history_id is not None and r.finished_at is not None


def test_a_failed_call_skips_its_batch_and_the_run_goes_on(session_factory, taxonomy):
    c = contact_id()
    for minute in range(4):
        seed_history(session_factory, c, at=A_TIME + timedelta(minutes=minute), words=f"ZZT ask {minute}")
    calls = {"n": 0}

    def flaky(messages, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("zzt provider down")
        keys = labeller().batch_keys(messages)
        return answer_for({k: ("answered", [ask("inventory", "inventory.availability")]) for k in keys})

    out = run(session_factory, flaky, batch_size=2)
    assert out["messages_read"] == 2
    (r,) = runs(session_factory)
    assert (r.calls, r.failed_calls, r.status) == (2, 1, "done")
    assert len(backfill().pending(session_factory(), "A")) == 2


def test_tokens_are_recorded_from_the_providers_usage(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c)

    class Result:
        def __init__(self, content):
            self.content = content
            self.prompt_tokens = 1200
            self.completion_tokens = 80
            self.cached_tokens = 900

    def chat(messages, schema):
        keys = labeller().batch_keys(messages)
        return Result(answer_for({k: ("answered", [ask("inventory", "inventory.availability")]) for k in keys}))

    run(session_factory, chat)
    (r,) = runs(session_factory)
    assert (r.tokens_in, r.tokens_out, r.tokens_cached) == (1200, 80, 900)


def test_no_taxonomy_leaves_means_no_run(session_factory):
    register_company_scope()
    seed_history(session_factory, contact_id())
    chat = answered()
    out = run(session_factory, chat)
    assert out["skipped"] == "no_leaves"
    assert chat.calls == []
    assert runs(session_factory) == []


def test_relabel_rereads_messages_labelled_under_an_older_taxonomy(session_factory, taxonomy):
    c = contact_id()
    h = seed_history(session_factory, c)
    seed_history(session_factory, c, kind="outgoing", words=REPLY_WORDS, at=A_TIME + timedelta(seconds=2))
    run(session_factory, answered())
    db = session_factory()
    models = models_mod()
    db.query(models.ChatAskFact).filter_by(history_message_id=h).update({"taxonomy_version": 1})
    db.commit()
    assert backfill().pending(session_factory(), "A") == []
    assert backfill().pending(session_factory(), "A", relabel=True) == [h]
    run(session_factory, answered(subject="master_products", leaf="master_products.price"), relabel=True)
    rows = facts(session_factory, h)
    assert [(r.topic_code, r.taxonomy_version) for r in rows] == [("master_products.price", 2)]


def test_the_prompt_carries_the_taxonomy_and_the_row_fields(session_factory, taxonomy):
    c = contact_id()
    seed_history(session_factory, c)
    chat = answered()
    run(session_factory, chat)
    system = chat.calls[0][0][0]["content"]
    assert "inventory.availability" in system
    row = rows_of(chat.calls[0][0])[0]
    assert set(row) == {"key", "subject", "intent", "message", "bot_reply_start"}
    assert row["message"] == CUSTOMER_WORDS
