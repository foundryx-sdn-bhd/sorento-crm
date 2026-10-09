"""CA-2 topic labeller: `app.services.chatbot.analytics.labeller` (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Behaviour card"
(rules R1-R8), "Card answers" and "Q5 follow-up". Postgres only (the
`tests/chatbot/conftest.py::session_factory` scratch schema); fake codes only (prefix ZZT).

The LLM and the product resolver are injected as plain callables, so nothing here reaches a
provider:

* `chat(messages, schema) -> str`: the provider's raw JSON content;
* `resolve(word, contact_respond_id) -> list[dict]`: the resolver's product matches
  (`entity_type`, `uuid`, `match_tier`), the shape `resolve_reference_post` returns per
  token. The contact rides along because the bot resolves inside the asking contact's
  company and brand scope, and the pass must resolve the same way.
"""
from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.chatbot._chat_facts_helpers import (
    CUSTOMER_WORDS,
    REPLY_WORDS,
    answered_trace,
    facts_mod,
    item_rows,
    models_mod,
    register_company_scope,
    seed_contact,
    seed_turn,
    stage,
)

LEAVES = (
    ("inventory.availability", "inventory", "Availability"),
    ("inventory.other", "inventory", "Other stock"),
    ("incoming.product_eta", "incoming", "Product ETA"),
    ("incoming.other", "incoming", "Other incoming"),
    ("master_products.price", "master_products", "Price"),
    ("master_products.other", "master_products", "Other product"),
    ("general.noise", "general", "Noise"),
    ("general.other", "general", "Other"),
)
SUBJECTS = ("inventory", "incoming", "master_products", "general")


def labeller():
    return importlib.import_module("app.services.chatbot.analytics.labeller")


# --------------------------------------------------------------------------- seeding


def seed_taxonomy(session_factory, *, leaves=LEAVES, leaf_version: int = 2) -> None:
    models = models_mod()
    db = session_factory()
    for order, code in enumerate(SUBJECTS):
        db.add(models.ChatTopic(code=code, label=code.title(), definition=code, version=1, sort_order=order))
    db.flush()
    for order, (code, parent, label) in enumerate(leaves):
        db.add(
            models.ChatTopic(
                code=code, parent_code=parent, label=label, definition=label, version=leaf_version, sort_order=order
            )
        )
    db.commit()


def seed_fact(session_factory, respond_id: str = "ZZT-lab-1", *, records=None, minutes: int = 0) -> str:
    """A closed live turn and its CA-1 fact row. Returns the fact id."""
    register_company_scope()
    seed_contact(session_factory, respond_id)
    turn_id = seed_turn(
        session_factory,
        respond_id,
        records=records if records is not None else answered_trace(),
        created_at=datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
    )
    assert facts_mod().record_turn(session_factory(), turn_id)
    models = models_mod()
    return session_factory().query(models.ChatAskFact.id).filter_by(turn_id=turn_id).scalar()


def casual_trace() -> list[dict]:
    """A casual message with no domain (`ok thanks`)."""
    return [stage("understood", facts={"message_type": "casual", "domain": None, "intent": None})]


def answer_for(keys_to_asks: dict, *, language: str = "en") -> str:
    return json.dumps(
        {
            "labels": [
                {"key": key, "language": language, "asks": asks}
                for key, asks in keys_to_asks.items()
            ]
        }
    )


def ask(subject: str, leaf: str, *, confidence: float = 0.9, products=()) -> dict:
    return {
        "subject": subject,
        "leaf": leaf,
        "confidence": confidence,
        "product_words": [{"word": w, "confidence": c} for w, c in products],
    }


class FakeChat:
    """Answers every batch with `answer(keys)`; records the calls."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[list[dict], dict]] = []

    def __call__(self, messages, schema):
        self.calls.append((messages, schema))
        keys = [k for k in labeller().batch_keys(messages)]
        return self.answer(keys)


def no_products(word, contact_respond_id):
    return []


def run(session_factory, chat, resolve=no_products, **kwargs):
    return labeller().run(session_factory(), chat=chat, resolve=resolve, model="zzt-model", **kwargs)


def fact(session_factory, fact_id):
    models = models_mod()
    return session_factory().query(models.ChatAskFact).filter_by(id=fact_id).one()


# --------------------------------------------------------------------------- pure: subject


def test_the_subject_is_the_fact_domain():
    assert labeller().subject_for("inventory", "business_query") == ("inventory", None)


def test_no_domain_and_casual_is_general_noise():
    assert labeller().subject_for(None, "casual") == ("general", "general.noise")


def test_no_domain_and_not_casual_is_general_with_the_leaf_left_to_the_labeller():
    assert labeller().subject_for(None, "business_query") == ("general", None)
    assert labeller().subject_for(None, None) == ("general", None)


def test_casual_with_a_carried_domain_keeps_the_domain():
    # A pick ("2") or a "yes" the engine carried a domain for is not noise (taxonomy_v1.md rule 1).
    assert labeller().subject_for("incoming", "casual") == ("incoming", None)


# --------------------------------------------------------------------------- pure: parse


def _taxonomy():
    lab = labeller()
    return lab.Taxonomy(
        version=2,
        subjects={s: s.title() for s in SUBJECTS},
        leaves={
            s: [(code, label, label) for code, parent, label in LEAVES if parent == s] for s in SUBJECTS
        },
    )


def _row(key="k1", subject="inventory", forced_leaf=None):
    return labeller().AskInput(
        key=key, subject=subject, forced_leaf=forced_leaf, text="zzt words", domain=subject,
        intent=None, outcome="answered", reply="zzt reply",
    )


def test_a_valid_answer_parses():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("inventory", "inventory.availability", confidence=0.93)]}, language="ms"),
        [_row()],
        _taxonomy(),
    )
    label = out["k1"]
    assert label.language == "ms"
    assert [(a.subject, a.leaf) for a in label.asks] == [("inventory", "inventory.availability")]
    assert label.asks[0].confidence == pytest.approx(0.93)


def test_a_leaf_from_another_subject_becomes_the_subjects_other():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("inventory", "incoming.product_eta")]}), [_row()], _taxonomy()
    )
    assert out["k1"].asks[0].leaf == "inventory.other"


def test_an_unknown_leaf_becomes_the_subjects_other():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("inventory", "inventory.zzt_invented")]}), [_row()], _taxonomy()
    )
    assert out["k1"].asks[0].leaf == "inventory.other"


def test_a_subject_with_no_other_leaf_falls_back_to_the_subject_itself():
    lab = labeller()
    taxonomy = lab.Taxonomy(
        version=1,
        subjects={"inventory": "Stock", "purchase_cost": "Cost", "sales": "Sales"},
        leaves={
            "inventory": [("inventory.availability", "A", "A"), ("inventory.other", "O", "O")],
            "purchase_cost": [("purchase_cost.last_cost", "L", "L")],
            "sales": [],
        },
    )
    out = lab.parse_labels(
        answer_for({"k1": [ask("purchase_cost", "purchase_cost.zzt")], "k2": [ask("sales", "sales.figures")]}),
        [_row(key="k1", subject="purchase_cost"), _row(key="k2", subject="sales")],
        taxonomy,
    )
    assert out["k1"].asks[0].leaf == "purchase_cost"
    assert out["k2"].asks[0].leaf == "sales"


def test_ask_one_keeps_the_rows_subject_whatever_the_model_says():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("incoming", "incoming.product_eta")]}), [_row(subject="inventory")], _taxonomy()
    )
    first = out["k1"].asks[0]
    assert first.subject == "inventory"
    assert first.leaf == "inventory.other"


def test_a_split_ask_takes_the_labellers_subject_when_it_is_a_known_subject():
    out = labeller().parse_labels(
        answer_for(
            {
                "k1": [
                    ask("inventory", "inventory.availability"),
                    ask("master_products", "master_products.price"),
                    ask("zzt_not_a_subject", "zzt_not_a_subject.x"),
                ]
            }
        ),
        [_row()],
        _taxonomy(),
    )
    assert [(a.subject, a.leaf) for a in out["k1"].asks] == [
        ("inventory", "inventory.availability"),
        ("master_products", "master_products.price"),
    ]


def test_a_forced_leaf_wins_over_the_model():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("general", "general.other")]}),
        [_row(subject="general", forced_leaf="general.noise")],
        _taxonomy(),
    )
    assert out["k1"].asks[0].leaf == "general.noise"


def test_a_language_outside_the_list_is_other_and_mixed_is_kept():
    lab = labeller()
    out = lab.parse_labels(answer_for({"k1": [ask("inventory", "inventory.availability")]}, language="fr"),
                           [_row()], _taxonomy())
    assert out["k1"].language == "other"
    out = lab.parse_labels(answer_for({"k1": [ask("inventory", "inventory.availability")]}, language="mixed"),
                           [_row()], _taxonomy())
    assert out["k1"].language == "mixed"


def test_a_key_not_in_the_batch_or_missing_is_ignored():
    out = labeller().parse_labels(
        answer_for({"zzt-stranger": [ask("inventory", "inventory.availability")]}), [_row()], _taxonomy()
    )
    assert out == {}


def test_garbage_is_no_labels_not_an_exception():
    assert labeller().parse_labels("not json {", [_row()], _taxonomy()) == {}
    assert labeller().parse_labels(json.dumps({"labels": "nope"}), [_row()], _taxonomy()) == {}


def test_confidence_is_clamped_to_0_1():
    out = labeller().parse_labels(
        answer_for({"k1": [ask("inventory", "inventory.availability", confidence=7)]}), [_row()], _taxonomy()
    )
    assert out["k1"].asks[0].confidence == 1.0


# --------------------------------------------------------------------------- pure: prompt


def test_the_prompt_lists_each_rows_own_subject_leaves_and_cuts_the_reply():
    lab = labeller()
    row = lab.AskInput(
        key="k1", subject="inventory", forced_leaf=None, text="zzt words", domain="inventory", intent=None,
        outcome="answered", reply="R" * 1000,
    )
    messages = lab.build_messages([row], _taxonomy())
    blob = json.dumps(messages)
    assert "inventory.availability" in blob
    assert "R" * 301 not in blob and "R" * 300 in blob
    assert lab.batch_keys(messages) == ["k1"]


def test_the_schema_is_strict_and_names_the_languages():
    schema = labeller().json_schema()
    assert schema["additionalProperties"] is False
    label = schema["properties"]["labels"]["items"]
    assert label["additionalProperties"] is False
    assert set(label["properties"]["language"]["enum"]) == {"en", "ms", "zh", "mixed", "other"}


# --------------------------------------------------------------------------- DB: run


def test_no_leaves_means_nothing_is_labelled_and_no_call_is_made(session_factory):
    # Taxonomy v1 not approved yet: only the subjects exist.
    seed_taxonomy(session_factory, leaves=())
    fact_id = seed_fact(session_factory)
    chat = FakeChat(lambda keys: answer_for({k: [ask("inventory", "inventory.availability")] for k in keys}))

    result = run(session_factory, chat)

    assert result["skipped"] == "no_leaves"
    assert chat.calls == []
    assert fact(session_factory, fact_id).topic_code is None


def test_an_unlabelled_fact_is_labelled_with_its_audit_columns(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory)
    chat = FakeChat(
        lambda keys: answer_for({k: [ask("inventory", "inventory.availability", confidence=0.88)] for k in keys},
                                language="zh")
    )

    result = run(session_factory, chat)

    row = fact(session_factory, fact_id)
    assert result["labelled"] == 1
    assert row.topic_code == "inventory.availability"
    assert row.taxonomy_version == 2
    assert row.label_model == "zzt-model"
    assert row.label_prompt_version == labeller().PROMPT_VERSION
    assert row.labelled_at is not None
    assert row.language == "zh"
    assert row.label_confidence == pytest.approx(0.88)


def test_the_customer_text_reaches_the_model_but_never_a_row(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory)
    chat = FakeChat(lambda keys: answer_for({k: [ask("inventory", "inventory.availability")] for k in keys}))

    run(session_factory, chat)

    assert CUSTOMER_WORDS in json.dumps(chat.calls[0][0])
    assert REPLY_WORDS[:20] in json.dumps(chat.calls[0][0])
    row = fact(session_factory, fact_id)
    for column in row.__table__.columns:
        assert CUSTOMER_WORDS not in str(getattr(row, column.name) or "")


def test_a_current_row_is_not_sent_and_a_stale_row_is_relabelled(session_factory):
    seed_taxonomy(session_factory)
    current = seed_fact(session_factory, "ZZT-lab-cur")
    stale = seed_fact(session_factory, "ZZT-lab-old", minutes=5)
    db = session_factory()
    models = models_mod()
    db.query(models.ChatAskFact).filter_by(id=current).update(
        {"topic_code": "inventory.other", "taxonomy_version": 2}
    )
    db.query(models.ChatAskFact).filter_by(id=stale).update(
        {"topic_code": "inventory.other", "taxonomy_version": 1}
    )
    db.commit()
    chat = FakeChat(lambda keys: answer_for({k: [ask("inventory", "inventory.availability")] for k in keys}))

    run(session_factory, chat)

    sent = [k for messages, _ in chat.calls for k in labeller().batch_keys(messages)]
    assert sent == [stale]
    assert fact(session_factory, stale).topic_code == "inventory.availability"
    assert fact(session_factory, current).topic_code == "inventory.other"


def test_casual_with_no_domain_is_noise_whatever_the_model_says(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory, records=casual_trace())
    chat = FakeChat(lambda keys: answer_for({k: [ask("general", "general.other")] for k in keys}))

    run(session_factory, chat)

    assert fact(session_factory, fact_id).topic_code == "general.noise"


def test_a_split_writes_one_more_row_and_a_rerun_does_not_duplicate_it(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory)
    first = fact(session_factory, fact_id)
    chat = FakeChat(
        lambda keys: answer_for(
            {
                k: [ask("inventory", "inventory.availability"), ask("master_products", "master_products.price")]
                for k in keys
            }
        )
    )

    run(session_factory, chat)
    # Force a relabel of the same turn: a stale version.
    db = session_factory()
    models = models_mod()
    db.query(models.ChatAskFact).filter_by(id=fact_id).update({"taxonomy_version": 1})
    db.commit()
    run(session_factory, chat)

    rows = session_factory().query(models.ChatAskFact).filter_by(turn_id=first.turn_id).order_by(
        models.ChatAskFact.ask_seq
    ).all()
    assert [r.ask_seq for r in rows] == [1, 2]
    second = rows[1]
    assert second.domain == "master_products"
    assert second.topic_code == "master_products.price"
    assert second.taxonomy_version == 2
    assert second.contact_respond_id == first.contact_respond_id
    assert second.asked_at == first.asked_at
    assert second.outcome == first.outcome
    assert second.source == first.source


def test_45_asks_are_two_calls_of_at_most_40(session_factory):
    seed_taxonomy(session_factory)
    for i in range(45):
        seed_fact(session_factory, f"ZZT-lab-b{i}", minutes=i)
    chat = FakeChat(lambda keys: answer_for({k: [ask("inventory", "inventory.availability")] for k in keys}))

    result = run(session_factory, chat)

    sizes = [len(labeller().batch_keys(messages)) for messages, _ in chat.calls]
    assert sorted(sizes) == [5, 40]
    assert result["labelled"] == 45


def test_a_row_the_model_left_out_stays_unlabelled_for_the_next_night(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory)
    chat = FakeChat(lambda keys: answer_for({}))

    run(session_factory, chat)

    assert fact(session_factory, fact_id).topic_code is None


def test_limit_caps_the_rows_picked(session_factory):
    seed_taxonomy(session_factory)
    for i in range(3):
        seed_fact(session_factory, f"ZZT-lab-l{i}", minutes=i)
    chat = FakeChat(lambda keys: answer_for({k: [ask("inventory", "inventory.availability")] for k in keys}))

    result = run(session_factory, chat, limit=2)

    assert result["labelled"] == 2


# --------------------------------------------------------------------------- DB: resolver pass


def _resolver(table: dict):
    def resolve(word, contact_respond_id):
        assert contact_respond_id == "ZZT-lab-1"
        return table.get(word, [])

    return resolve


def _match(uid: str, tier: str = "exact") -> dict:
    return {"entity_type": "product", "uuid": uid, "match_tier": tier}


@pytest.mark.parametrize(
    "matches, confidence, expect_item",
    [
        ([_match("P1")], 0.9, True),
        ([_match("P1")], 0.8, True),
        ([_match("P1")], 0.79, False),  # under the floor
        ([_match("P1", "prefix")], 0.95, False),  # not an exact tier
        ([_match("P1", "trgm")], 0.95, False),
        ([_match("P1", "embedding")], 0.95, False),
        ([_match("P1"), _match("P2")], 0.95, False),  # several products
        ([_match("P1"), _match("P1")], 0.95, True),  # one product twice is one product
        ([], 0.95, False),
    ],
)
def test_the_resolver_pass_writes_only_a_unique_exact_confident_product(
    session_factory, matches, confidence, expect_item
):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory, records=answered_trace(named=()))
    product_id = str(uuid.uuid4())
    table = {"zzt-word": [{**m, "uuid": product_id if m["uuid"] == "P1" else str(uuid.uuid4())} for m in matches]}
    chat = FakeChat(
        lambda keys: answer_for(
            {k: [ask("inventory", "inventory.availability", products=(("zzt-word", confidence),))] for k in keys}
        )
    )

    run(session_factory, chat, resolve=_resolver(table))

    items = [i for i in item_rows(session_factory, fact_id) if i.resolved_by == "resolver_pass"]
    if expect_item:
        assert [(i.kind, i.ref_id, i.role) for i in items] == [("product", product_id, "named")]
        assert items[0].confidence == pytest.approx(confidence)
    else:
        assert items == []


def test_a_resolver_item_is_not_duplicated_by_a_relabel(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory, records=answered_trace(named=()))
    product_id = str(uuid.uuid4())
    chat = FakeChat(
        lambda keys: answer_for(
            {k: [ask("inventory", "inventory.availability", products=(("zzt-word", 0.9),))] for k in keys}
        )
    )
    resolve = _resolver({"zzt-word": [_match(product_id)]})
    run(session_factory, chat, resolve=resolve)
    db = session_factory()
    models = models_mod()
    db.query(models.ChatAskFact).filter_by(id=fact_id).update({"taxonomy_version": 1})
    db.commit()
    run(session_factory, chat, resolve=resolve)

    items = [i for i in item_rows(session_factory, fact_id) if i.resolved_by == "resolver_pass"]
    assert len(items) == 1


def test_a_resolver_failure_leaves_the_label_and_skips_the_item(session_factory):
    seed_taxonomy(session_factory)
    fact_id = seed_fact(session_factory, records=answered_trace(named=()))

    def boom(word, contact_respond_id):
        raise RuntimeError("zzt resolver down")

    chat = FakeChat(
        lambda keys: answer_for(
            {k: [ask("inventory", "inventory.availability", products=(("zzt-word", 0.9),))] for k in keys}
        )
    )
    run(session_factory, chat, resolve=boom)

    assert fact(session_factory, fact_id).topic_code == "inventory.availability"
    assert [i for i in item_rows(session_factory, fact_id) if i.resolved_by == "resolver_pass"] == []


# --------------------------------------------------------------------------- wiring


def test_the_analytics_queue_is_a_batch_queue_the_worker_drains():
    import worker

    assert "analytics" in worker.queues_for_role("batch")
    assert "analytics" in worker.DEFAULT_QUEUES


def test_the_scheduler_queues_the_labeller_at_0215_malaysia(monkeypatch):
    from app.scheduler import task_scheduler

    added = {}

    class FakeScheduler:
        def add_job(self, func, trigger=None, id=None, **kwargs):
            added[id] = (func, trigger)

        def start(self):
            pass

    monkeypatch.setattr(task_scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(task_scheduler, "register_task_handlers", lambda: None)
    task_scheduler.start_scheduler()

    func, trigger = added["chat_topic_labeller"]
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "2" and fields["minute"] == "15"
    assert str(trigger.timezone) == "Asia/Kuala_Lumpur"


@pytest.mark.parametrize("enabled, expect_enqueued", [(False, 0), (True, 1)])
def test_the_tick_queues_the_job_only_when_switched_on(monkeypatch, enabled, expect_enqueued):
    # Owner, 9 Oct 2026: labelling runs on dev only until he says otherwise, so the nightly
    # tick is off unless CHAT_TOPIC_LABELLER_ENABLED is set.
    from app.config import settings
    from app.scheduler import task_scheduler
    from app.tasks import chat_turns

    calls = []
    monkeypatch.setattr(settings, "chat_topic_labeller_enabled", enabled, raising=False)
    monkeypatch.setattr(chat_turns, "enqueue_chat_topic_labeller", lambda: calls.append(1))

    task_scheduler._chat_topic_labeller_tick()

    assert len(calls) == expect_enqueued


# --------------------------------------------------------------------------- review round 1: parse hardening


def test_an_entry_whose_key_is_not_in_the_batch_is_ignored():
    content = json.dumps(
        {
            "labels": [
                {"key": "zzt-not-in-batch", "language": "en", "asks": [ask("inventory", "inventory.availability")]},
                {"key": "k1", "language": "ms", "asks": [ask("inventory", "inventory.availability")]},
            ]
        }
    )

    out = labeller().parse_labels(content, [_row()], _taxonomy())

    assert set(out) == {"k1"}
    assert out["k1"].language == "ms"


def test_for_a_duplicate_key_the_first_entry_wins():
    content = json.dumps(
        {
            "labels": [
                {"key": "k1", "language": "ms", "asks": [ask("inventory", "inventory.availability")]},
                {"key": "k1", "language": "zh", "asks": [ask("inventory", "inventory.other")]},
            ]
        }
    )

    out = labeller().parse_labels(content, [_row()], _taxonomy())

    assert out["k1"].language == "ms"
    assert [a.leaf for a in out["k1"].asks] == ["inventory.availability"]


def test_extra_asks_per_message_are_capped_at_three():
    asks = [ask("inventory", "inventory.availability")] + [
        ask("master_products", "master_products.price") for _ in range(5)
    ]

    out = labeller().parse_labels(answer_for({"k1": asks}), [_row()], _taxonomy())

    assert len(out["k1"].asks) == 4, "ask 1 plus at most three extra asks (ask_seq max 4)"
    assert out["k1"].asks[0].leaf == "inventory.availability"
