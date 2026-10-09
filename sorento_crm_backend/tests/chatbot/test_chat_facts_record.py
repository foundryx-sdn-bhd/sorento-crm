"""CA-1 facts: `facts.record_turn`, `facts.sweep` and the `engine._close_turn` hook
(tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md ("Tables", "Code",
"Edge rules"). Postgres only (`tests/chatbot/conftest.py::session_factory`, a blank scratch
schema); every contact, customer, product, brand and turn is seeded here, fake codes only.

`app.services.chatbot.analytics.facts` and `app.models.chat_facts` do not exist yet, so each
test fails with `ModuleNotFoundError`.

Two deliberate choices:
* The session handed to `record_turn` / `sweep` has NO company scope set (a script, the
  nightly sweep). `respond_contact_customers` and `products` are company-owned, so on such a
  session an ORM read returns nothing (fail-closed). The writer must read them unscoped; a
  fact row with a null customer for a linked contact is the bug these tests catch.
* The hook tests monkeypatch `facts.record_turn` on the MODULE, so the engine must call it
  as `facts.record_turn(...)` (module attribute), not a name bound at import.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.chatbot import engine as engine_mod
from tests.chatbot._chat_facts_helpers import (
    CUSTOMER_WORDS,
    REPLY_WORDS,
    SORENTO,
    answered_trace,
    apply_event,
    entity_dict,
    event,
    fact_rows,
    facts_mod,
    item_rows,
    looked_up,
    models_mod,
    register_company_scope,
    seed_catalogue,
    seed_contact,
    seed_turn,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope()


def _cid(tag: str) -> str:
    import uuid

    return f"ZZT-cf-{tag}-{uuid.uuid4().hex[:8]}"


def _record(session_factory, turn_id: str):
    return facts_mod().record_turn(session_factory(), turn_id)


# --------------------------------------------------------------------------- the row


def test_a_closed_live_turn_writes_one_fact_row_with_every_field(session_factory):
    cid = _cid("row")
    who = seed_contact(session_factory, cid, customer=True)
    asked = datetime(2026, 9, 10, 3, 15, tzinfo=UTC)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=answered_trace(named=(("product", "ZZT-NOMATCH"),), rows=3, tools=("crm_stock",)),
        created_at=asked,
        reply_ms=1500,
    )

    assert _record(session_factory, turn_id) is True

    rows = fact_rows(session_factory, turn_id=turn_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "trace"
    assert row.ask_seq == 1
    assert row.contact_respond_id == cid
    assert row.contact_id == who["contact_id"]
    assert str(row.customer_id) == who["customer_id"]
    assert row.company_id == SORENTO
    assert row.audience == "dealer"
    assert row.asked_at == asked
    assert row.channel == "whatsapp"
    assert row.domain == "inventory"
    assert row.intent == "stock"
    assert row.outcome == "answered"
    assert row.failure_reason is None
    assert row.branch_kind is None
    assert list(row.tools) == ["crm_stock"]
    assert row.rows_offered == 3
    assert row.reply_ms == 1500
    assert row.is_media is False
    assert row.phantom_dropped is False
    assert row.episode_id is None
    # CA-2 owns the label columns: a trace row leaves them empty.
    assert row.topic_code is None and row.taxonomy_version is None
    assert row.label_model is None and row.labelled_at is None


def test_a_failed_turn_is_recorded_as_technical(session_factory):
    cid = _cid("failed")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="failed", records=answered_trace(), stage_name="replied")

    assert _record(session_factory, turn_id) is True

    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert (row.outcome, row.failure_reason) == ("technical", "technical")


def test_the_outcome_and_branch_come_from_the_trace_rules(session_factory):
    cid = _cid("miss")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=[apply_event(domain_hint="inventory", domains=["inventory"]), looked_up(missed=["inventory"])],
        branch_kind="answer_lookup",
    )
    _record(session_factory, turn_id)

    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert (row.outcome, row.failure_reason, row.branch_kind) == ("missed", "missed", "answer_lookup")


def test_a_phantom_answer_sets_the_flag_not_the_outcome(session_factory):
    cid = _cid("phantom")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, records=answered_trace() + [event("phantom_answer")])
    _record(session_factory, turn_id)

    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert row.phantom_dropped is True
    assert row.outcome == "answered"


# --------------------------------------------------------------------------- audience


def test_audience_is_staff_when_a_user_is_linked_even_if_a_customer_is_too(session_factory):
    cid = _cid("staff")
    seed_contact(session_factory, cid, customer=True, staff=True)
    turn_id = seed_turn(session_factory, cid)
    _record(session_factory, turn_id)
    assert fact_rows(session_factory, turn_id=turn_id)[0].audience == "staff"


def test_audience_is_dealer_with_a_customer_link_and_unknown_without(session_factory):
    dealer, stranger = _cid("dealer"), _cid("stranger")
    seed_contact(session_factory, dealer, customer=True)
    seed_contact(session_factory, stranger)
    t_dealer = seed_turn(session_factory, dealer)
    t_stranger = seed_turn(session_factory, stranger)
    _record(session_factory, t_dealer)
    _record(session_factory, t_stranger)

    assert fact_rows(session_factory, turn_id=t_dealer)[0].audience == "dealer"
    unknown = fact_rows(session_factory, turn_id=t_stranger)[0]
    assert unknown.audience == "unknown"
    assert unknown.customer_id is None and unknown.company_id is None


def test_a_turn_whose_contact_has_no_respond_contacts_row_is_unknown(session_factory):
    cid = _cid("nocontact")
    turn_id = seed_turn(session_factory, cid)
    assert _record(session_factory, turn_id) is True
    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert row.audience == "unknown"
    assert row.contact_id is None
    assert row.contact_respond_id == cid


def test_customer_and_company_come_from_the_primary_link(session_factory):
    cid = _cid("primary")
    who = seed_contact(session_factory, cid, customer=True, extra_nonprimary_customer=True)
    turn_id = seed_turn(session_factory, cid)
    _record(session_factory, turn_id)
    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert str(row.customer_id) == who["customer_id"]
    assert row.company_id == SORENTO


# --------------------------------------------------------------------------- what never lands


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_test": True},
        {"ingress": "console"},
        {"status": "processing"},
        {"status": "delegated"},
        {"status": "queued"},
    ],
    ids=["is_test", "console", "processing", "delegated", "queued"],
)
def test_turns_that_are_not_closed_live_turns_write_nothing(session_factory, kwargs):
    cid = _cid("skip")
    seed_contact(session_factory, cid, customer=True)
    turn_id = seed_turn(session_factory, cid, **kwargs)

    assert _record(session_factory, turn_id) is False
    assert fact_rows(session_factory, contact_respond_id=cid) == []


def test_an_unknown_turn_id_returns_false(session_factory):
    import uuid

    assert _record(session_factory, str(uuid.uuid4())) is False


def test_recording_the_same_turn_twice_writes_one_row_and_raises_nothing(session_factory):
    cid = _cid("idem")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, records=answered_trace(named=(("product", "ZZT-X"),)))

    assert _record(session_factory, turn_id) is True
    _record(session_factory, turn_id)

    assert len(fact_rows(session_factory, turn_id=turn_id)) == 1


def test_no_customer_or_reply_text_lands_in_any_column(session_factory):
    cid = _cid("privacy")
    codes = seed_catalogue(session_factory, product_codes=("ZZTPRIV1",))
    seed_contact(session_factory, cid, customer=True)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=answered_trace(named=(("product", "ZZTPRIV1"),), shown=(("product", "ZZTPRIV1"),)),
    )
    _record(session_factory, turn_id)

    row = fact_rows(session_factory, turn_id=turn_id)[0]
    dumped = json.dumps(
        {c.name: str(getattr(row, c.name)) for c in row.__table__.columns}
        | {"items": [{c.name: str(getattr(i, c.name)) for c in i.__table__.columns} for i in item_rows(session_factory, row.id)]}
    )
    assert CUSTOMER_WORDS not in dumped
    assert REPLY_WORDS not in dumped
    assert "ZZT label" not in dumped
    assert codes["products"]["ZZTPRIV1"] in dumped  # the id travels, the words do not


# --------------------------------------------------------------------------- retries


def test_a_later_attempt_replaces_the_earlier_attempts_row(session_factory):
    cid = _cid("retry")
    seed_catalogue(session_factory, product_codes=("ZZTRETRY1",))
    seed_contact(session_factory, cid)
    base = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    first = seed_turn(
        session_factory, cid, message_id="ZZT-retry-msg", attempt=1, created_at=base,
        records=answered_trace(named=(("product", "ZZTRETRY1"),)),
    )
    second = seed_turn(
        session_factory, cid, message_id="ZZT-retry-msg", attempt=2, created_at=base + timedelta(minutes=2),
        records=[apply_event(domain_hint="inventory", domains=["inventory"], ask="which size?")],
    )

    _record(session_factory, first)
    first_row_id = fact_rows(session_factory, turn_id=first)[0].id
    assert item_rows(session_factory, first_row_id)  # attempt 1 had an item
    _record(session_factory, second)

    rows = fact_rows(session_factory, contact_respond_id=cid)
    assert [r.turn_id for r in rows] == [second]
    assert rows[0].outcome == "clarified"
    assert item_rows(session_factory, first_row_id) == [], "the replaced row's items go with it"


def test_an_older_attempt_never_replaces_a_later_one(session_factory):
    cid = _cid("retry2")
    seed_contact(session_factory, cid)
    base = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    first = seed_turn(session_factory, cid, message_id="ZZT-retry2-msg", attempt=1, created_at=base)
    second = seed_turn(
        session_factory, cid, message_id="ZZT-retry2-msg", attempt=2, created_at=base + timedelta(minutes=2)
    )

    _record(session_factory, second)
    _record(session_factory, first)

    assert [r.turn_id for r in fact_rows(session_factory, contact_respond_id=cid)] == [second]


def test_two_different_messages_are_two_rows(session_factory):
    cid = _cid("twomsg")
    seed_contact(session_factory, cid)
    a = seed_turn(session_factory, cid, message_id="ZZT-m-a")
    b = seed_turn(session_factory, cid, message_id="ZZT-m-b", created_at=datetime(2026, 9, 10, 4, 0, tzinfo=UTC))
    _record(session_factory, a)
    _record(session_factory, b)
    assert len(fact_rows(session_factory, contact_respond_id=cid)) == 2


# --------------------------------------------------------------------------- items


def test_a_named_product_matches_its_code_exactly_and_case_insensitively(session_factory):
    cid = _cid("item")
    cat = seed_catalogue(session_factory, product_codes=("ZZTPROD1", "ZZTPROD1X"))
    seed_contact(session_factory, cid)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=[
            apply_event(
                domain_hint="inventory",
                domains=["inventory"],
                entities=[
                    entity_dict("product", "zztprod1"),  # same code, other case: matches ZZTPROD1
                    entity_dict("product", "ZZTPROD"),  # a prefix of two products: matches neither
                    entity_dict("product", None, raw="that blue one"),  # unresolved words: no row
                    entity_dict("product", "ZZTNOSUCHCODE"),  # no product by this code
                ],
            ),
            looked_up(sections=1),
        ],
    )
    _record(session_factory, turn_id)

    ask = fact_rows(session_factory, turn_id=turn_id)[0]
    items = item_rows(session_factory, ask.id)
    assert [(i.kind, i.ref_id, i.role, i.resolved_by) for i in items] == [
        ("product", cat["products"]["ZZTPROD1"], "named", "parser")
    ]


def test_a_named_brand_matches_its_brand_code(session_factory):
    cid = _cid("brand")
    cat = seed_catalogue(session_factory, brand_codes=("ZZTBR1",))
    seed_contact(session_factory, cid)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=[
            apply_event(domain_hint="product", domains=["product"], entities=[entity_dict("brand", "zztbr1")]),
            looked_up(sections=1),
        ],
    )
    _record(session_factory, turn_id)

    items = item_rows(session_factory, fact_rows(session_factory, turn_id=turn_id)[0].id)
    assert [(i.kind, i.ref_id, i.role, i.resolved_by) for i in items] == [
        ("brand", cat["brands"]["ZZTBR1"], "named", "parser")
    ]


def test_shown_result_set_codes_become_shown_items_resolved_by_reply(session_factory):
    cid = _cid("shown")
    cat = seed_catalogue(session_factory, product_codes=("ZZTSHOW1", "ZZTSHOW2"))
    seed_contact(session_factory, cid)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=answered_trace(
            shown=(("product", "ZZTSHOW1"), ("product", "ZZTSHOW2"), ("product", "ZZTNOTINCRM")), rows=3
        ),
    )
    _record(session_factory, turn_id)

    items = item_rows(session_factory, fact_rows(session_factory, turn_id=turn_id)[0].id)
    assert sorted((i.kind, i.ref_id, i.role, i.resolved_by) for i in items) == sorted(
        [
            ("product", cat["products"]["ZZTSHOW1"], "shown", "reply"),
            ("product", cat["products"]["ZZTSHOW2"], "shown", "reply"),
        ]
    )


def test_a_product_both_named_and_shown_is_two_items_and_a_repeat_is_one(session_factory):
    cid = _cid("both")
    cat = seed_catalogue(session_factory, product_codes=("ZZTBOTH1",))
    seed_contact(session_factory, cid)
    turn_id = seed_turn(
        session_factory,
        cid,
        records=answered_trace(
            named=(("product", "ZZTBOTH1"), ("product", "zZtBoTh1")),
            shown=(("product", "ZZTBOTH1"), ("product", "ZZTBOTH1")),
        ),
    )
    _record(session_factory, turn_id)

    items = item_rows(session_factory, fact_rows(session_factory, turn_id=turn_id)[0].id)
    assert sorted((i.kind, i.ref_id, i.role) for i in items) == [
        ("product", cat["products"]["ZZTBOTH1"], "named"),
        ("product", cat["products"]["ZZTBOTH1"], "shown"),
    ]


def test_a_turn_with_no_resolvable_code_has_a_fact_and_no_items(session_factory):
    cid = _cid("noitem")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, records=answered_trace(named=(("product", "ZZTNOPE"),)))
    _record(session_factory, turn_id)

    ask = fact_rows(session_factory, turn_id=turn_id)[0]
    assert item_rows(session_factory, ask.id) == []


# --------------------------------------------------------------------------- sweep


def test_sweep_fills_missing_rows_and_skips_existing_and_ineligible_turns(session_factory):
    cid = _cid("sweep")
    seed_contact(session_factory, cid, customer=True)
    day = lambda d, h=3: datetime(2026, 9, d, h, 0, tzinfo=UTC)  # noqa: E731
    existing = seed_turn(session_factory, cid, created_at=day(10))
    missing_a = seed_turn(session_factory, cid, created_at=day(11))
    missing_b = seed_turn(session_factory, cid, created_at=day(12), status="failed", stage_name="replied")
    is_test = seed_turn(session_factory, cid, created_at=day(11, 4), is_test=True)
    console = seed_turn(session_factory, cid, created_at=day(11, 5), ingress="console")
    open_turn = seed_turn(session_factory, cid, created_at=day(11, 6), status="processing")
    too_old = seed_turn(session_factory, cid, created_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    too_new = seed_turn(session_factory, cid, created_at=datetime(2026, 10, 1, 3, 0, tzinfo=UTC))
    _record(session_factory, existing)
    before = fact_rows(session_factory, turn_id=existing)[0]

    written = facts_mod().sweep(
        session_factory(),
        since=datetime(2026, 9, 5, tzinfo=UTC),
        until=datetime(2026, 9, 30, tzinfo=UTC),
    )

    assert written == 2
    have = {r.turn_id for r in fact_rows(session_factory, contact_respond_id=cid)}
    assert have == {existing, missing_a, missing_b}
    for skipped in (is_test, console, open_turn, too_old, too_new):
        assert skipped not in have
    assert fact_rows(session_factory, turn_id=existing)[0].id == before.id, "an existing row is left alone"
    swept = fact_rows(session_factory, turn_id=missing_a)[0]
    assert swept.audience == "dealer" and swept.customer_id is not None, "sweep fills the same fields as the hook"


def test_a_second_sweep_writes_nothing(session_factory):
    cid = _cid("sweep2")
    seed_contact(session_factory, cid)
    seed_turn(session_factory, cid)
    since = datetime(2026, 9, 5, tzinfo=UTC)
    assert facts_mod().sweep(session_factory(), since=since) == 1
    assert facts_mod().sweep(session_factory(), since=since) == 0


# --------------------------------------------------------------------------- the engine close hook


def _close(session_factory, turn_id, *, status="done", records=None, stage=None):
    db = session_factory()
    engine_mod._close_turn(
        db,
        turn_id,
        status=status,
        stage=stage,
        branch_kind=None,
        error="boom" if status == "failed" else None,
        records=records if records is not None else answered_trace(),
        response={"reply": {"text": REPLY_WORDS}},
    )


def test_closing_a_live_turn_through_the_engine_writes_the_fact_row(session_factory):
    cid = _cid("hook")
    seed_contact(session_factory, cid, customer=True)
    started = datetime.now(UTC) - timedelta(seconds=2)
    turn_id = seed_turn(session_factory, cid, status="processing", created_at=started)

    _close(session_factory, turn_id)

    rows = fact_rows(session_factory, turn_id=turn_id)
    assert len(rows) == 1
    assert rows[0].outcome == "answered" and rows[0].audience == "dealer"
    assert 2000 <= rows[0].reply_ms < 120_000


def test_closing_a_turn_as_failed_writes_a_technical_row(session_factory):
    cid = _cid("hookfail")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing")

    _close(session_factory, turn_id, status="failed", stage="replied")

    row = fact_rows(session_factory, turn_id=turn_id)[0]
    assert (row.outcome, row.failure_reason) == ("technical", "technical")


def test_the_delegated_handover_is_not_a_close_and_writes_nothing(session_factory):
    cid = _cid("hookdel")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing")

    _close(session_factory, turn_id, status="delegated")

    assert fact_rows(session_factory, turn_id=turn_id) == []


def test_closing_a_test_turn_writes_nothing(session_factory):
    cid = _cid("hooktest")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing", is_test=True)

    _close(session_factory, turn_id)

    assert fact_rows(session_factory, contact_respond_id=cid) == []


def test_a_refused_second_close_does_not_write_a_second_row(session_factory):
    cid = _cid("hooktwice")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing")

    _close(session_factory, turn_id)
    _close(session_factory, turn_id, status="failed", stage="replied")

    rows = fact_rows(session_factory, turn_id=turn_id)
    assert len(rows) == 1 and rows[0].outcome == "answered"


def test_a_failing_recorder_never_fails_the_turn(session_factory, monkeypatch):
    cid = _cid("hookboom")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing")

    def boom(db, turn_id):
        raise RuntimeError("ZZT analytics down")

    with monkeypatch.context() as patched:
        patched.setattr(facts_mod(), "record_turn", boom)
        _close(session_factory, turn_id)  # must not raise

    from app.models.chatbot_turn import ChatbotTurn

    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).one()
    assert row.status == "done" and row.finished_at is not None
    assert fact_rows(session_factory, contact_respond_id=cid) == []
    # ...and the sweep refills it once the recorder is back.
    assert facts_mod().sweep(session_factory(), since=datetime(2026, 9, 5, tzinfo=UTC)) == 1


def test_a_recorder_that_breaks_the_transaction_still_leaves_the_turn_closed(session_factory, monkeypatch):
    """A DB error inside the recorder aborts the Postgres transaction; the turn was
    committed BEFORE the hook, so it stays closed and the session is usable afterwards."""
    from sqlalchemy import text

    cid = _cid("hookdb")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, status="processing")

    def db_boom(db, turn_id):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(facts_mod(), "record_turn", db_boom)
    db = session_factory()
    engine_mod._close_turn(
        db, turn_id, status="done", stage=None, branch_kind=None, error=None,
        records=answered_trace(), response={"reply": {"text": REPLY_WORDS}},
    )

    from app.models.chatbot_turn import ChatbotTurn

    assert db.query(ChatbotTurn.status).filter(ChatbotTurn.id == turn_id).scalar() == "done"


def test_model_classes_are_exported(session_factory):
    models = models_mod()
    for name in ("ChatAskFact", "ChatAskItem", "ChatEpisodeFact"):
        assert hasattr(models, name), name
        assert models.__dict__[name].__table__.schema == "chatbot", name


# --------------------------------------------------------------------------- fix round 1


def _set_envelope(session_factory, turn_id: str, envelope: dict) -> None:
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).update({ChatbotTurn.envelope: envelope})
    db.commit()


def test_an_image_attachment_marks_the_ask_as_media(session_factory):
    """The stored envelope is `Envelope.model_dump()`: the attachment sits on the inner
    respond message, `message.message.message.attachment`."""
    cid = _cid("media")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid)
    _set_envelope(
        session_factory,
        turn_id,
        {"contact": {"id": "ZZT"}, "message": {"message": {"message": {"type": "attachment", "attachment": {"type": "image", "url": "https://zzt.test/a.jpg"}}}}},
    )
    _record(session_factory, turn_id)
    assert fact_rows(session_factory, turn_id=turn_id)[0].is_media is True


def test_a_text_message_is_not_media(session_factory):
    cid = _cid("nomedia")
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid)
    _record(session_factory, turn_id)
    assert fact_rows(session_factory, turn_id=turn_id)[0].is_media is False


def test_shown_rows_are_products_only_a_brand_code_is_not_shown(session_factory):
    cid = _cid("shownbrand")
    seed_catalogue(session_factory, brand_codes=("ZZTSHOWBR",))
    seed_contact(session_factory, cid)
    turn_id = seed_turn(session_factory, cid, records=answered_trace(shown=(("brand", "ZZTSHOWBR"),)))
    _record(session_factory, turn_id)
    assert item_rows(session_factory, fact_rows(session_factory, turn_id=turn_id)[0].id) == []


def test_a_code_in_two_companies_resolves_to_the_facts_own_company(session_factory):
    import uuid

    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    cid = _cid("twoco")
    sorento = seed_catalogue(session_factory, product_codes=("ZZTTWOCO",))["products"]["ZZTTWOCO"]
    other_company = str(uuid.uuid4())
    db = session_factory()
    set_company_scope(db, None)
    db.add(Company(id=other_company, name="ZZT Other Co", code=f"ZZ{uuid.uuid4().hex[:6]}"))
    db.flush()
    category, uom = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(ProductCategory(id=category, category_code=f"ZZTC{uuid.uuid4().hex[:6]}", category_name="ZZT", company_id=other_company))
    db.add(UnitOfMeasure(id=uom, uom_code=f"ZZTU{uuid.uuid4().hex[:6]}", uom_name="ZZT", company_id=other_company))
    db.flush()
    foreign = str(uuid.uuid4())
    db.add(
        Product(id=foreign, product_code="ZZTTWOCO", product_name="ZZT twin", category_id=category,
                base_uom_id=uom, list_price=1, is_active=True, company_id=other_company)
    )
    db.commit()

    seed_contact(session_factory, cid, customer=True)  # a Sorento dealer
    named = seed_turn(session_factory, cid, records=answered_trace(named=(("product", "ZZTTWOCO"),)))
    _record(session_factory, named)
    items = item_rows(session_factory, fact_rows(session_factory, turn_id=named)[0].id)
    assert [i.ref_id for i in items] == [sorento]

    stranger = _cid("twoco2")
    seed_contact(session_factory, stranger)  # no company: two matches, so no item
    blind = seed_turn(session_factory, stranger, records=answered_trace(named=(("product", "ZZTTWOCO"),)))
    _record(session_factory, blind)
    assert item_rows(session_factory, fact_rows(session_factory, turn_id=blind)[0].id) == []


def test_the_sweep_isolates_one_bad_turn(session_factory, monkeypatch):
    cid = _cid("badturn")
    seed_contact(session_factory, cid)
    day = lambda h: datetime(2026, 9, 12, h, 0, tzinfo=UTC)  # noqa: E731
    good_a = seed_turn(session_factory, cid, created_at=day(1))
    bad = seed_turn(
        session_factory, cid, created_at=day(2),
        records=[apply_event(domain_hint="zztboom", domains=["zztboom"]), looked_up(sections=1)],
    )
    good_b = seed_turn(session_factory, cid, created_at=day(3))

    module = facts_mod()
    real_items = module._items

    def flaky(db, flat, company_id):
        if flat["domain"] == "zztboom":
            raise RuntimeError("ZZT bad turn")
        return real_items(db, flat, company_id)

    monkeypatch.setattr(module, "_items", flaky)

    written = module.sweep(session_factory(), since=datetime(2026, 9, 5, tzinfo=UTC))

    assert written == 2
    have = {r.turn_id for r in fact_rows(session_factory, contact_respond_id=cid)}
    assert have == {good_a, good_b}
    assert bad not in have
