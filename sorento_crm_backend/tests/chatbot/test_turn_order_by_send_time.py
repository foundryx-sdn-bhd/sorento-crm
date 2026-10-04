"""A contact's turns are answered in WhatsApp SEND order, not arrival order (issue #1262 round 3).

Mr Loo, 26 Sep 2026: he sent a PHOTO at 14:57:25 (UTC+8) and the word "Stock" at 14:57:29.
n8n forwards a text to `/chat/turn` at once, but runs its own media intake on a photo first
(`sub-media-intake` calls `/external/media/process` and waits ~20 s for the extraction), so
"Stock" reached the CRM first, ran as turn 378 and was answered from yesterday's SRTWC
products. The photo ran as turn 379 twenty seconds later.

The CRM already knows the photo exists when "Stock" arrives: n8n's `/external/media/process`
call wrote the ledger row (`contact_media_usage`) for that message before the text was even
forwarded. And once n8n's media intake is removed (plan S6), both messages reach
`/chat/turn` near together and the photo's row sits in the per-contact queue. Either way the
rule is the same, with no timer: when a turn gets its slot it first answers every
earlier-sent message of this contact the CRM already knows about and has not answered, then
itself. The earlier message's own delivery later comes back `duplicate: true`, so n8n sends
nothing twice.

Owner ruling, 26 Sep 2026 (issue #1262): no stale-focus guard. Carried focus is never dropped
on a calendar-day or clock rule, so a bare "Stock" on yesterday's focus answers from it,
exactly as on main. `TestCarriedFocusAcrossDays` pins that.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope

from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_media_intake_turn import (
    _fresh_contact_id,
    _seed_media_limit,
    _seed_settings,
    media_pipeline,  # noqa: F401
)

# Mr Loo's two messages, with the send times respond.io stamped on them (ms since epoch).
# The photo is the owner's WhatsApp screenshot, 14:57:25 local; "Stock" was sent 4 s later
# (the real envelope's `timestamp`, 14:57:29 local). The ledger path never compares either
# with the CRM's clock (review round 3, B1), so the fixed instants hold whenever the test
# writes the ledger.
PHOTO_SENT_MS = 1790405845000
STOCK_SENT_MS = 1790405849000
PHOTO_MESSAGE_ID = "1790405845000000"
STOCK_MESSAGE_ID = "1790405849000000"

PHOTO_CODES = ["M72-GM-DIY", "M210-GM", "M227-BL"]
YESTERDAY_CODES = ["SRTWC8516-SH-UF-150-NEW", "SRTWC8517-SH-UF-150-NEW"]

@pytest.fixture(autouse=True)
def _stub_mcp(monkeypatch):
    """No stock rows for any code: these tests are about WHICH products a turn asks
    about and in what order, never about the stock figures."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, args: json.dumps({"data": []}))


PHOTO_RESULT = {
    "rendered_text": ", ".join(PHOTO_CODES),
    "confirmation_message": None,
    "entities": [{"raw": code} for code in PHOTO_CODES],
    "attributes": [],
    "notes": "Handwritten list of product codes.",
    "truncated": False,
    "needs_clarification": False,
}


def _product(code: str) -> dict[str, Any]:
    return {"raw": code, "canonical_code": code, "hint": "product", "current_message": False}


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _seed_contact(
    session_factory, contact_id: int, *, focus_codes: list[str], phone: str = "+60160000528"
) -> None:
    session_vars = {
        "focus": {"products": [_product(c) for c in focus_codes], "domains": ["inventory"]},
        "open_question": None,
        "ideation": None,
        "access_levels": [],
        "contains_flyer": False,
    }
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(contact_id), "phone": phone, "sv": json.dumps(session_vars)},
    )
    db.commit()


def _base_envelope(contact_id: int, *, message_id: str, sent_ms: int, inner: dict[str, Any]) -> Envelope:
    return Envelope(
        contact={"id": contact_id, "firstName": "Mr", "lastName": "Loo"},
        message={
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": message_id,
                "timestamp": sent_ms,
                "contactId": contact_id,
                "channelId": 453209,
                "traffic": "incoming",
                "message": inner,
            },
        },
    )


def _stock_envelope(contact_id: int) -> Envelope:
    return _base_envelope(
        contact_id,
        message_id=STOCK_MESSAGE_ID,
        sent_ms=STOCK_SENT_MS,
        inner={"type": "text", "text": "Stock"},
    )


PHOTO_URL = "https://cdn.example/mr-loo-list.jpg"


def _photo_envelope(contact_id: int) -> Envelope:
    return _base_envelope(
        contact_id,
        message_id=PHOTO_MESSAGE_ID,
        sent_ms=PHOTO_SENT_MS,
        inner={
            "type": "attachment",
            "attachment": {"type": "image", "url": PHOTO_URL, "mimeType": "image/jpeg", "size": 98641},
        },
    )


def _n8n_media_intake(
    session_factory, contact_id: int, monkeypatch, *, message_id: str = PHOTO_MESSAGE_ID, worker_runs: bool = True
) -> None:
    """What n8n's `sub-media-intake` does for the photo BEFORE it calls `/chat/turn`: one
    `/external/media/process` call through the real fast path (decide, meter, record,
    enqueue). The extraction is still RUNNING when "Stock" arrives, as it was for Mr Loo
    (~20 s); the worker finishes it while the CRM's existing bounded media poll waits on
    that same job, which is the first `time.sleep` of that poll."""
    import app.api.v1.external.media as media_route
    from app.api.v1.external.media import _decide_meter_record_and_enqueue
    from app.schemas.external.media import MediaProcessRequest
    from app.services.chatbot import media_intake as media_intake_mod

    queued: list[tuple[Any, tuple]] = []
    monkeypatch.setattr(
        media_route,
        "enqueue_job",
        lambda func, *args, **kwargs: queued.append((func, args)) or type("FakeJob", (), {"id": "rq"})(),
    )

    def _worker_finishes(_seconds: float) -> None:
        while queued:
            func, args = queued.pop(0)
            func(*args)

    if worker_runs:
        monkeypatch.setattr(media_intake_mod.time, "sleep", _worker_finishes)

    db = session_factory()
    _decide_meter_record_and_enqueue(
        db,
        MediaProcessRequest(
            respond_io_id=str(contact_id),
            message_id=message_id,
            modality="image",
            media_url=PHOTO_URL,
            mime_type="image/jpeg",
            turn_id="17448466",
        ),
    )


def _verdicts_by_message(calls: list[str]):
    """A parser stub that answers by what the message says, and records every user block.

    The photo's turn reads the codes the extraction rendered; "Stock" names nothing and
    reuses the subject, which is exactly what the live parser returned for turn 378
    (inventory / check_stock, 0 entities, `entity_op: reuse`)."""

    def _on_call(user_block: str) -> None:
        calls.append(user_block)

    def _parse(config, user_block):
        _on_call(user_block)
        if "M210-GM" in user_block.split("Current user message:", 1)[-1].split("\n", 1)[0]:
            return _parser_output(
                domain_hint="inventory",
                intent_hint="check_stock",
                entities=[
                    {
                        "raw": code,
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                    for code in PHOTO_CODES
                ],
                entity_op="replace_combine",
            )
        return _parser_output(
            domain_hint="inventory",
            intent_hint="check_stock",
            entities=[],
            entity_op="reuse",
        )

    return _parse


def _install_parser(monkeypatch, calls: list[str]) -> None:
    from app.services.chatbot.head import parser as parser_mod

    monkeypatch.setattr(
        parser_mod,
        "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test"
        ),
    )
    monkeypatch.setattr(parser_mod, "parse", _verdicts_by_message(calls))


def _message_line(user_block: str) -> str:
    return user_block.split("Current user message:", 1)[-1].split("\n", 1)[0].strip()


def _subject_line(user_block: str) -> str:
    return next((line for line in user_block.splitlines() if line.startswith("Current subject")), "")


def _turn_for(session_factory, contact_id: int, message_id: str) -> ChatbotTurn | None:
    return (
        session_factory()
        .query(ChatbotTurn)
        .filter(
            ChatbotTurn.contact_respond_id == str(contact_id),
            ChatbotTurn.message_id == message_id,
        )
        .order_by(ChatbotTurn.created_at.desc())
        .first()
    )


def _texts(actions: list[dict[str, Any]]) -> list[str]:
    return [str(a.get("text") or "") for a in actions if a.get("kind") == "send_message"]


class TestPhotoStillInN8nMediaIntake:
    """Today's path: n8n is still extracting the photo when "Stock" reaches `/chat/turn`."""

    def test_mr_loo_photo_is_answered_first_and_stock_applies_to_its_products(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        # 14:57:25 photo sent -> n8n starts its own media intake (the ledger row exists).
        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        # 14:57:29 "Stock" sent -> forwarded at once, reaches /chat/turn FIRST.
        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls][:1] == [", ".join(PHOTO_CODES)], (
            "the photo, sent 4 s before 'Stock', must be answered first; the parser saw "
            f"{[_message_line(c) for c in calls]}"
        )
        assert len(calls) == 2, [_message_line(c) for c in calls]
        stock_subject = _subject_line(calls[1])
        assert "M210-GM" in stock_subject, (
            f"'Stock' must apply to the photo's products, not yesterday's: {stock_subject!r}"
        )
        assert "SRTWC8517" not in stock_subject, stock_subject

        photo_row = _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID)
        assert photo_row is not None, "the photo was answered, so it has its own turn row"
        assert photo_row.status == "done", (photo_row.status, photo_row.error)

        texts = _texts(result.actions)
        assert len(texts) >= 2, texts
        assert "M210-GM" in texts[0], f"the photo's reply is sent first: {texts}"

    def test_the_photos_own_late_delivery_is_a_duplicate_and_sends_nothing(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)
        parsed = len(calls)

        # ~20 s later n8n's own intake finishes and it calls /chat/turn for the photo.
        late = engine_mod.run_turn(_photo_envelope(contact_id), session_factory=session_factory)

        assert late.duplicate is True
        assert len(calls) == parsed, "the photo must not be parsed or answered a second time"
        # Review round 2, N2: nothing extracted or charged twice, pinned on the ledger
        # itself rather than through the turn row.
        from app.models.media import ContactMediaUsage, MediaExtractionJob

        db = session_factory()
        usage = (
            db.query(ContactMediaUsage)
            .filter(
                ContactMediaUsage.respond_io_id == str(contact_id),
                ContactMediaUsage.message_id == PHOTO_MESSAGE_ID,
            )
            .all()
        )
        assert len(usage) == 1, [(u.outcome, u.created_at) for u in usage]
        jobs = db.query(MediaExtractionJob).filter(MediaExtractionJob.usage_id == usage[0].id).all()
        assert len(jobs) == 1, [j.status for j in jobs]

    def test_a_photo_whose_extraction_already_finished_is_not_replayed(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """A photo n8n answered on its own reply arm last week never became a turn, and
        its job is finished. It is not on its way, so no later turn answers it again."""
        from app.services.chatbot import media_intake as media_intake_mod

        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        media_intake_mod.time.sleep(0)  # the worker finished it back then

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"], [_message_line(c) for c in calls]
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None

    def test_a_stranded_job_from_before_an_answered_turn_is_not_replayed(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """Bound, with no clock: only a media message the CRM heard about AFTER this
        contact's previous turn can be earlier and unanswered. A job stranded unfinished
        before a turn that has since been answered is history."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        db = session_factory()
        db.execute(
            text("UPDATE contact_media_usage SET created_at = :t WHERE respond_io_id = :c"),
            {"t": datetime.now() - timedelta(days=7), "c": str(contact_id)},
        )
        db.commit()
        _seed_previous_turn(session_factory, contact_id, days_ago=6)

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"], [_message_line(c) for c in calls]
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None


class TestBothInThePerContactQueue:
    """After plan S6 both messages reach `/chat/turn` near together. The queue is still
    taken in arrival order; the turn that gets the slot first answers the earlier-SENT
    message that is waiting behind it, then itself."""

    def test_a_queued_earlier_sent_message_is_answered_before_the_one_holding_the_slot(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        # The photo arrived second and is still waiting for its slot: its row exists, at
        # stage `queued`, exactly as `run_turn` leaves it before `wait_for_turn` returns.
        photo = _base_envelope(
            contact_id,
            message_id=PHOTO_MESSAGE_ID,
            sent_ms=PHOTO_SENT_MS,
            inner={"type": "text", "text": ", ".join(PHOTO_CODES)},
        )
        db = session_factory()
        waiting = engine_mod._insert_turn(db, envelope=photo, contact_respond_id=str(contact_id), queued=True)
        waiting_id = str(waiting.id)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == [", ".join(PHOTO_CODES), "Stock"]
        assert "M210-GM" in _subject_line(calls[1])
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == waiting_id).first()
        assert row.status == "done", (row.status, row.stage, row.error)
        assert _texts(result.actions)[0] == _texts((row.response or {}).get("actions") or [])[0], (
            "the earlier-sent message's reply goes out first"
        )

        # Its own request, once it gets the slot, finds the row answered.
        assert engine_mod._claim_own_row(session_factory, waiting_id) is False

    def test_a_queued_later_sent_message_is_left_for_its_own_request(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        later = _base_envelope(
            contact_id,
            message_id="ZZT-later",
            sent_ms=_now_ms() + 3_600_000,
            inner={"type": "text", "text": "thanks"},
        )
        db = session_factory()
        waiting = engine_mod._insert_turn(db, envelope=later, contact_respond_id=str(contact_id), queued=True)

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert engine_mod._claim_own_row(session_factory, str(waiting.id)) is True


def _seed_previous_turn(session_factory, contact_id: int, *, days_ago: int) -> None:
    db = session_factory()
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db.add(
        ChatbotTurn(
            contact_respond_id=str(contact_id),
            message_id=f"ZZT-prev-{days_ago}",
            envelope={},
            status="done",
            stage="sent",
            created_at=when,
            started_at=when,
            finished_at=when,
        )
    )
    db.commit()


class TestCarriedFocusAcrossDays:
    """Owner ruling, 26 Sep 2026: nothing drops carried focus on a calendar-day rule."""

    def test_bare_stock_on_a_focus_from_an_earlier_kuala_lumpur_day_still_answers_it(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        # The last turn finished two days ago, so it is on an earlier Kuala Lumpur
        # calendar day whatever the local time of this run.
        _seed_previous_turn(session_factory, contact_id, days_ago=2)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert "SRTWC8517" in _subject_line(calls[0]), (
            f"'Stock' reads the carried products: {_subject_line(calls[0])!r}"
        )
        texts = " ".join(_texts(result.actions)).lower()
        assert "give me a product code" not in texts, f"no re-ask for a product: {texts!r}"
        row = _turn_for(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert row.status == "done", (row.status, row.error)


class TestOrderingOnTheWaitingRequest:
    """Ordering ON: the photo's own request is waiting for its ticket while "Stock" (which
    arrived first but was sent later) holds the slot and answers the photo first. When the
    photo's request gets its slot it must find its row answered and send nothing."""

    def _ordering_on(self, monkeypatch, *, contact_id: int, while_waiting) -> None:
        monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: True)
        monkeypatch.setattr(engine_mod, "_ordering_redis", lambda: None)
        monkeypatch.setattr(engine_mod.dispatch, "contact_ticket", lambda redis, contact: 2)
        monkeypatch.setattr(
            engine_mod.dispatch,
            "start_heartbeat",
            lambda *args: type("NoBeat", (), {"stop": lambda self: None})(),
        )
        monkeypatch.setattr(engine_mod.dispatch, "mark_done", lambda *args: None)
        state = {"first": True}

        def _wait(redis, contact, ticket, timeout_s):
            if state["first"]:
                state["first"] = False
                while_waiting()

        monkeypatch.setattr(engine_mod.dispatch, "wait_for_turn", _wait)

    def _photo_as_text(self, contact_id: int) -> Envelope:
        return _base_envelope(
            contact_id,
            message_id=PHOTO_MESSAGE_ID,
            sent_ms=PHOTO_SENT_MS,
            inner={"type": "text", "text": ", ".join(PHOTO_CODES)},
        )

    def test_the_waiting_photo_request_replays_the_answer_given_ahead_of_it(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        slot_holder: dict[str, Any] = {}

        def _stock_holds_the_slot() -> None:
            slot_holder["result"] = engine_mod.run_turn(
                _stock_envelope(contact_id), session_factory=session_factory
            )

        self._ordering_on(monkeypatch, contact_id=contact_id, while_waiting=_stock_holds_the_slot)

        waiting = engine_mod.run_turn(self._photo_as_text(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == [", ".join(PHOTO_CODES), "Stock"]
        assert waiting.duplicate is True, "its answer already went out on the Stock turn's response"
        photo_row = _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID)
        assert photo_row.status == "done"
        assert _texts(slot_holder["result"].actions)[0] == _texts(photo_row.response["actions"])[0]

    def test_a_queue_timeout_after_being_answered_ahead_sends_no_error_reply(
        self, session_factory, stub_access, monkeypatch
    ):
        from app.services.chatbot import dispatch

        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        def _answered_then_timed_out() -> None:
            engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)
            raise dispatch.QueueWait("the Stock turn took longer than the queue budget")

        self._ordering_on(monkeypatch, contact_id=contact_id, while_waiting=_answered_then_timed_out)

        waiting = engine_mod.run_turn(self._photo_as_text(contact_id), session_factory=session_factory)

        assert waiting.duplicate is True
        assert engine_mod.GENERIC_ERROR_REPLY not in _texts(waiting.actions)
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID).status == "done"


def _queued_row(session_factory, contact_id: int, *, message_id: str, sent_ms: int, text_: str) -> str:
    envelope = _base_envelope(
        contact_id, message_id=message_id, sent_ms=sent_ms, inner={"type": "text", "text": text_}
    )
    db = session_factory()
    return str(engine_mod._insert_turn(db, envelope=envelope, contact_respond_id=str(contact_id), queued=True).id)


class TestReviewRound1:
    """Reviewer round 1: stuck rows, best effort, and dry-run isolation (D14)."""

    def test_a_queued_row_stuck_before_an_answered_turn_is_never_replayed(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        stuck = _queued_row(
            session_factory, contact_id, message_id="ZZT-stuck", sent_ms=PHOTO_SENT_MS - 3 * 86_400_000,
            text_="killed by a deploy",
        )
        db = session_factory()
        db.execute(
            text("UPDATE turns SET started_at = now() - interval '3 days' WHERE id = :id"), {"id": stuck}
        )
        db.commit()
        _seed_previous_turn(session_factory, contact_id, days_ago=2)

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert engine_mod._claim_own_row(session_factory, stuck) is True, "left untouched"

    def test_a_failure_taking_a_second_earlier_message_keeps_the_first_answer_and_this_turn(
        self, session_factory, stub_access, monkeypatch
    ):
        from app.services.chatbot import send_order

        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        first = _queued_row(
            session_factory, contact_id, message_id="ZZT-a", sent_ms=PHOTO_SENT_MS, text_="M210-GM"
        )
        second = _queued_row(
            session_factory, contact_id, message_id="ZZT-b", sent_ms=PHOTO_SENT_MS + 1000, text_="hello"
        )
        real_claim = send_order.claim

        def _claim(db, turn_id):
            if turn_id == second:
                raise RuntimeError("database went away")
            return real_claim(db, turn_id)

        monkeypatch.setattr(send_order, "claim", _claim)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["M210-GM", "Stock"]
        first_row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == first).first()
        assert _texts(result.actions)[0] == _texts(first_row.response["actions"])[0]
        assert engine_mod.GENERIC_ERROR_REPLY not in _texts(result.actions)
        assert _turn_for(session_factory, contact_id, STOCK_MESSAGE_ID).status == "done"

    def test_a_failing_lookup_answers_this_turn_alone(self, session_factory, stub_access, monkeypatch):
        from app.services.chatbot import send_order

        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)

        def _boom(*args, **kwargs):
            raise RuntimeError("ledger query broke")

        monkeypatch.setattr(send_order, "earlier_unanswered", _boom)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert engine_mod.GENERIC_ERROR_REPLY not in _texts(result.actions)

    def test_a_dry_run_never_claims_or_answers_live_messages(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """D14: a test "Stock" from the Prompts screen against a real contact must not
        answer the customer's waiting photo or queued message."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        queued = _queued_row(
            session_factory, contact_id, message_id="ZZT-live", sent_ms=PHOTO_SENT_MS, text_="M210-GM"
        )

        test_turn = _stock_envelope(contact_id)
        test_turn.is_test = True
        engine_mod.run_turn(test_turn, session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None
        assert engine_mod._claim_own_row(session_factory, queued) is True


def _received_facts(session_factory, contact_id: int, message_id: str) -> dict[str, Any]:
    row = _turn_for(session_factory, contact_id, message_id)
    assert row is not None, f"no turn row for {message_id}"
    first = (row.trace or [{}])[0]
    assert first.get("stage") == "received", row.trace
    return first.get("facts") or {}


class TestContactIsolation:
    """Review round 2, B1: a pre-step answers THIS contact's earlier messages and nobody
    else's. Either contact filter dropped would put another customer's products and reply
    on this customer's response."""

    def test_another_contacts_queued_earlier_row_is_never_claimed_or_answered(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_a = _fresh_contact_id()
        contact_b = _fresh_contact_id()
        _seed_contact(session_factory, contact_a, focus_codes=[])
        _seed_contact(session_factory, contact_b, focus_codes=[], phone="+60120000002")
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        other = _queued_row(
            session_factory, contact_b, message_id="ZZT-b-earlier", sent_ms=PHOTO_SENT_MS, text_="M210-GM"
        )

        result = engine_mod.run_turn(_stock_envelope(contact_a), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert not any("M210-GM" in t for t in _texts(result.actions)), _texts(result.actions)
        assert engine_mod._claim_own_row(session_factory, other) is True, "B's row is left for B"

    def test_another_contacts_running_ledger_photo_is_never_answered_under_this_contact(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        contact_a = _fresh_contact_id()
        contact_b = _fresh_contact_id()
        _seed_contact(session_factory, contact_a, focus_codes=[])
        _seed_contact(session_factory, contact_b, focus_codes=[], phone="+60120000003")
        _seed_media_limit(session_factory, contact_id=contact_b, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_b, monkeypatch)

        result = engine_mod.run_turn(_stock_envelope(contact_a), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert not any("M210-GM" in t for t in _texts(result.actions)), _texts(result.actions)
        assert _turn_for(session_factory, contact_a, PHOTO_MESSAGE_ID) is None
        assert _turn_for(session_factory, contact_b, PHOTO_MESSAGE_ID) is None


class TestPreStepBounds:
    """Review round 2, S1: a fixed count per turn, and the media waits it takes on never
    add up to n8n's 90 s `chat-turn` timeout. Bounds are counts and the existing per-item
    wait, never a clock. What is left over goes to its own delivery, and the trace says so."""

    def test_at_most_two_earlier_messages_are_answered_ahead_and_the_rest_are_left(
        self, session_factory, stub_access, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        first = _queued_row(session_factory, contact_id, message_id="ZZT-1", sent_ms=PHOTO_SENT_MS, text_="one")
        second = _queued_row(
            session_factory, contact_id, message_id="ZZT-2", sent_ms=PHOTO_SENT_MS + 1000, text_="two"
        )
        third = _queued_row(
            session_factory, contact_id, message_id="ZZT-3", sent_ms=PHOTO_SENT_MS + 2000, text_="three"
        )

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["one", "two", "Stock"]
        assert engine_mod._claim_own_row(session_factory, first) is False
        assert engine_mod._claim_own_row(session_factory, second) is False
        assert engine_mod._claim_own_row(session_factory, third) is True, "left for its own request"
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_answered_ahead") == 2, facts
        assert facts.get("earlier_left_for_own_turns") == 1, facts
        assert "count_bound" in str(facts.get("earlier_left_because")), facts

    def test_a_photo_whose_wait_would_reach_the_n8n_timeout_is_left_for_its_own_turn(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        # One 90 s wait alone is n8n's whole `chat-turn` timeout.
        _seed_settings(session_factory, media_sync_wait_seconds=90)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_id, monkeypatch)

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_answered_ahead") == 0, facts
        assert facts.get("earlier_left_for_own_turns") == 1, facts
        assert "media_wait_budget" in str(facts.get("earlier_left_because")), facts


class TestLedgerPhotoNotReadWhileAnsweredAhead:
    """Review round 2, S2 (today's live path, pre-S6): n8n's `media-route` replies itself
    when its extraction fails or outlives the wait. The CRM then sends NOTHING for that
    photo and leaves it to its own delivery, so the customer never gets two messages."""

    def _setup(self, session_factory, stub_access, media_pipeline, monkeypatch, calls):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        stub_access()
        _install_parser(monkeypatch, calls)
        return contact_id

    def _assert_nothing_for_the_photo(self, session_factory, contact_id, result, calls):
        assert [_message_line(c) for c in calls] == ["Stock"]
        texts = _texts(result.actions)
        assert len(texts) == 1, f"only the Stock reply goes out: {texts}"
        assert engine_mod.GENERIC_ERROR_REPLY not in texts
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None, (
            "no turn row, so the photo's own delivery is not a duplicate"
        )
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_left_for_own_turns") == 1, facts
        assert "photo_not_read" in str(facts.get("earlier_left_because")), facts

    def test_a_failed_extraction_sends_nothing_for_the_photo(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        calls: list[str] = []
        contact_id = self._setup(session_factory, stub_access, media_pipeline, monkeypatch, calls)
        media_pipeline.set_result(error=RuntimeError("provider refused the image"))
        _n8n_media_intake(session_factory, contact_id, monkeypatch)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        self._assert_nothing_for_the_photo(session_factory, contact_id, result, calls)

    def test_an_extraction_that_outlives_the_wait_sends_nothing_for_the_photo(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        import time as time_mod

        calls: list[str] = []
        contact_id = self._setup(session_factory, stub_access, media_pipeline, monkeypatch, calls)
        media_pipeline.set_result(PHOTO_RESULT)
        _n8n_media_intake(session_factory, contact_id, monkeypatch, worker_runs=False)
        real_sleep = time_mod.sleep
        monkeypatch.setattr(time_mod, "sleep", lambda _s: real_sleep(0.05))

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        self._assert_nothing_for_the_photo(session_factory, contact_id, result, calls)


class TestReviewRound2:
    def test_a_raising_close_after_a_raising_earlier_turn_still_answers_this_turn(
        self, session_factory, stub_access, monkeypatch
    ):
        """S3: the earlier message's stages raise, and so does closing its row (a DB blip
        is the likely cause of both). The pre-step swallows it; this turn still answers."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        earlier = _queued_row(
            session_factory, contact_id, message_id="ZZT-blip", sent_ms=PHOTO_SENT_MS, text_="M210-GM"
        )
        real_stages = engine_mod._run_stages
        real_close = engine_mod._close_turn

        def _stages(envelope, **kwargs):
            if kwargs.get("turn_id") == earlier:
                raise RuntimeError("database went away mid-turn")
            return real_stages(envelope, **kwargs)

        def _close(db, turn_id, **kwargs):
            if turn_id == earlier:
                raise RuntimeError("database still away")
            return real_close(db, turn_id, **kwargs)

        monkeypatch.setattr(engine_mod, "_run_stages", _stages)
        monkeypatch.setattr(engine_mod, "_close_turn", _close)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["Stock"]
        assert engine_mod.GENERIC_ERROR_REPLY not in _texts(result.actions)
        assert _turn_for(session_factory, contact_id, STOCK_MESSAGE_ID).status == "done"

    def test_a_ledger_photo_first_seen_after_this_message_was_sent_is_still_answered_ahead(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """Review round 3, B1 (probe P1): n8n's media call reaches the CRM after respond.io,
        n8n's queue and its media intake, so the ledger row routinely postdates the next
        text's send time. The two are on different clocks and must never be compared; the
        photo, first seen before "Stock" arrived, is still answered first."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        # "Stock" was sent 1 s BEFORE n8n first told the CRM about the photo.
        stock = _base_envelope(
            contact_id,
            message_id=STOCK_MESSAGE_ID,
            sent_ms=_now_ms() - 1000,
            inner={"type": "text", "text": "Stock"},
        )
        _n8n_media_intake(session_factory, contact_id, monkeypatch)

        engine_mod.run_turn(stock, session_factory=session_factory)

        assert [_message_line(c) for c in calls] == [", ".join(PHOTO_CODES), "Stock"]
        assert "M210-GM" in _subject_line(calls[1])
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID).status == "done"

    def test_the_row_answered_ahead_names_the_turn_that_answered_it(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """N3: an operator on the trace screen can tell why the photo's reply went out on
        "Stock"'s response."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=YESTERDAY_CODES)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_id, monkeypatch)

        engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        stock_row = _turn_for(session_factory, contact_id, STOCK_MESSAGE_ID)
        photo_row = _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID)
        first = photo_row.trace[0]
        assert first["stage"] == "received"
        assert (first.get("raw") or {}).get("answered_ahead_by") == str(stock_row.id), first.get("raw")
        assert first["facts"].get("answered_ahead_by_message") == STOCK_MESSAGE_ID, first["facts"]
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_answered_ahead") == 1, facts
        assert facts.get("earlier_left_for_own_turns") == 0, facts


def _queue_behind_this_turn(monkeypatch, session_factory, contact_id: int, **row) -> dict[str, str]:
    """Review round 3, N1: the queued text reaches `/chat/turn` AFTER this turn's request,
    as the per-contact FIFO ticket requires of any row still waiting behind it. So it is
    inserted once this turn's own row exists (as the pre-step starts), never
    backdated before it. Returns a holder the row's id lands in."""
    real = engine_mod._answer_earlier_messages
    holder: dict[str, str] = {}

    def _then_answer(*args, **kwargs):
        if "id" not in holder:
            holder["id"] = _queued_row(session_factory, contact_id, **row)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_mod, "_answer_earlier_messages", _then_answer)
    return holder


def _raise_on_wait(monkeypatch) -> None:
    from app.services.chatbot import media_intake as media_intake_mod

    def _raises(*args, **kwargs):
        raise RuntimeError("database went away mid-poll")

    monkeypatch.setattr(media_intake_mod, "await_existing_job", _raises)


class TestReviewRound3:
    def test_a_raising_wait_on_the_ledger_job_leaves_the_photo_and_keeps_the_rest(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """S1 (probe P2): the wait on the photo's job reads the DB on every poll, so a DB
        blip raises there. That is the photo not read: it is left, while the earlier text
        already answered keeps its reply and this turn still answers."""
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(PHOTO_RESULT)
        stub_access()
        calls: list[str] = []
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_id, monkeypatch)
        first = _queue_behind_this_turn(
            monkeypatch,
            session_factory,
            contact_id,
            message_id="ZZT-before",
            sent_ms=PHOTO_SENT_MS - 1000,
            text_="hello",
        )
        _raise_on_wait(monkeypatch)

        result = engine_mod.run_turn(_stock_envelope(contact_id), session_factory=session_factory)

        assert [_message_line(c) for c in calls] == ["hello", "Stock"]
        texts = _texts(result.actions)
        assert engine_mod.GENERIC_ERROR_REPLY not in texts, texts
        assert len(texts) == 2, f"the earlier text's reply and Stock's: {texts}"
        assert engine_mod._claim_own_row(session_factory, first["id"]) is False, "answered ahead"
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None
        assert _turn_for(session_factory, contact_id, STOCK_MESSAGE_ID).status == "done"
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_answered_ahead") == 1, facts
        assert facts.get("earlier_left_for_own_turns") == 1, facts
        assert "photo_not_read" in str(facts.get("earlier_left_because")), facts

    def _photo_then_text(
        self, session_factory, stub_access, monkeypatch, calls, *, worker_runs: bool, media_wait: int = 5
    ):
        contact_id = _fresh_contact_id()
        _seed_contact(session_factory, contact_id, focus_codes=[])
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=media_wait)
        stub_access()
        _install_parser(monkeypatch, calls)
        _n8n_media_intake(session_factory, contact_id, monkeypatch, worker_runs=worker_runs)
        # A text sent after the photo and before "Stock", queued behind "Stock".
        later = _queue_behind_this_turn(
            monkeypatch,
            session_factory,
            contact_id,
            message_id="ZZT-after-photo",
            sent_ms=_now_ms() + 5000,
            text_="hello",
        )
        stock = _base_envelope(
            contact_id,
            message_id=STOCK_MESSAGE_ID,
            sent_ms=_now_ms() + 10_000,
            inner={"type": "text", "text": "Stock"},
        )
        return contact_id, later, stock

    def _assert_photo_left_and_text_answered_ahead(self, session_factory, contact_id, later, calls, reason):
        assert [_message_line(c) for c in calls] == ["hello", "Stock"], (
            "a known, earlier-sent text is never pushed behind this turn"
        )
        assert engine_mod._claim_own_row(session_factory, later["id"]) is False, "answered ahead"
        assert _turn_for(session_factory, contact_id, PHOTO_MESSAGE_ID) is None, (
            "the photo is left to its own delivery, with no row"
        )
        facts = _received_facts(session_factory, contact_id, STOCK_MESSAGE_ID)
        assert facts.get("earlier_answered_ahead") == 1, facts
        assert facts.get("earlier_left_for_own_turns") == 1, facts
        assert reason in str(facts.get("earlier_left_because")), facts

    def test_a_photo_still_being_read_is_left_and_a_later_known_text_is_still_answered_ahead(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """Round 3 S1 (probe P4): the photo outlives the wait, so it is still being read.
        A ledger-only photo reaches `/chat/turn` after every queued row, or never, so
        stopping at it gains the photo nothing and would put "hello" behind "Stock". It is
        left alone and the take goes on."""
        import time as time_mod

        calls: list[str] = []
        media_pipeline.set_result(PHOTO_RESULT)
        contact_id, later, stock = self._photo_then_text(
            session_factory, stub_access, monkeypatch, calls, worker_runs=False
        )
        real_sleep = time_mod.sleep
        monkeypatch.setattr(time_mod, "sleep", lambda _s: real_sleep(0.05))

        engine_mod.run_turn(stock, session_factory=session_factory)

        self._assert_photo_left_and_text_answered_ahead(session_factory, contact_id, later, calls, "photo_not_read")

    def test_a_raising_wait_on_a_photo_does_not_hold_back_a_later_known_text(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """Round 3 S1: the same when the wait on the photo's job raises."""
        calls: list[str] = []
        media_pipeline.set_result(PHOTO_RESULT)
        contact_id, later, stock = self._photo_then_text(
            session_factory, stub_access, monkeypatch, calls, worker_runs=False
        )
        _raise_on_wait(monkeypatch)

        engine_mod.run_turn(stock, session_factory=session_factory)

        self._assert_photo_left_and_text_answered_ahead(session_factory, contact_id, later, calls, "photo_not_read")

    def test_a_ledger_photo_over_the_media_budget_does_not_hold_back_a_later_known_text(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """Round 3 S1, same shape in `send_order.within_bounds`: a ledger photo whose wait
        would reach n8n's timeout is left, and the take goes on past it."""
        calls: list[str] = []
        media_pipeline.set_result(PHOTO_RESULT)
        contact_id, later, stock = self._photo_then_text(
            session_factory, stub_access, monkeypatch, calls, worker_runs=True, media_wait=90
        )

        engine_mod.run_turn(stock, session_factory=session_factory)

        self._assert_photo_left_and_text_answered_ahead(
            session_factory, contact_id, later, calls, "media_wait_budget"
        )

    def test_a_failed_photo_does_not_hold_back_a_later_text(
        self, session_factory, stub_access, media_pipeline, monkeypatch
    ):
        """S2: a failed extraction never reaches `/chat/turn` (n8n answers it on its reply
        arm), so nothing waits on it and the later text is still answered ahead."""
        calls: list[str] = []
        media_pipeline.set_result(error=RuntimeError("provider refused the image"))
        contact_id, later, stock = self._photo_then_text(
            session_factory, stub_access, monkeypatch, calls, worker_runs=True
        )

        engine_mod.run_turn(stock, session_factory=session_factory)

        self._assert_photo_left_and_text_answered_ahead(session_factory, contact_id, later, calls, "photo_not_read")


class TestWithinBounds:
    """Round 3 S1: only a ledger-only photo is skipped over; a queued row that does not fit
    still ends the take, because its own request comes before this turn's successors."""

    def _earlier(self, order_key: float, *, queued: bool):
        from app.services.chatbot.send_order import Earlier

        if queued:
            return Earlier(order_key=order_key, row_id=f"row-{order_key}", carries_media=True)
        return Earlier(order_key=order_key, message_id=f"m-{order_key}", job_id=f"j-{order_key}", carries_media=True)

    def test_a_ledger_photo_over_budget_is_skipped_and_the_take_goes_on(self):
        from app.services.chatbot.send_order import Earlier, within_bounds

        photo = self._earlier(1, queued=False)
        text_ = Earlier(order_key=2, row_id="row-text")
        taken, left, reasons = within_bounds(
            [photo, text_], media_wait_seconds=90, own_carries_media=False, budget_seconds=90
        )
        assert taken == [text_]
        assert left == [photo]
        assert reasons == ["media_wait_budget"]

    def test_a_queued_media_row_over_budget_still_ends_the_take(self):
        from app.services.chatbot.send_order import Earlier, within_bounds

        queued_photo = self._earlier(1, queued=True)
        text_ = Earlier(order_key=2, row_id="row-text")
        taken, left, reasons = within_bounds(
            [queued_photo, text_], media_wait_seconds=90, own_carries_media=False, budget_seconds=90
        )
        assert taken == []
        assert left == [queued_photo, text_]
        assert reasons == ["media_wait_budget"]
