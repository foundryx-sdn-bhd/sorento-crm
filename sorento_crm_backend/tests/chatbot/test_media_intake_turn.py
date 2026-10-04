"""S2 (PLAN-chatbot-media-into-turn.md): image/voice intake INSIDE `run_turn`.

AC-1800 to AC-1816 (UAC section A). Written FIRST - every test here is expected to fail
today because `engine.py::_run_stages` has no media-intake step at all: an image
attachment falls through to being parsed on its caption text (`build_latest_user_message`'s
existing `attachment.description` fallback) and a voice attachment dies at the existing
`AUDIO_NOT_PATCHED_ERROR` block. The RIGHT red reason for every test below is "no
`media_intake` trace stage was ever recorded" / "no `MediaProcessRequest` was ever built" -
never an import error or a fixture bug.

Seams stubbed, same technique `tests/chatbot/test_console_media_turn.py` and
`tests/test_media_process_endpoint.py` already use for the real media pipeline:

* `app.api.v1.external.media.decide_and_record` - a spy WRAPPING the real function (never
  replaced), so the gate/quota/burst logic that already has its own test suite
  (`tests/test_media_process_endpoint.py`) keeps running for real and this file only reads
  what it decided.
* `app.api.v1.external.media.enqueue_job` - runs the "RQ" job INLINE, synchronously,
  against this test's own blank-schema session (never a real Redis/worker).
* `app.tasks.media_tasks.SessionLocal` - repointed at the same blank-schema
  `session_factory`, so the inline job commits into the same transaction this test reads
  back.
* `app.tasks.media_tasks.run_media_extraction` - a canned result body, never a real
  vision/transcription provider call.

None of this asserts anything about the EXTRACTION itself (S3.5's own shapes, already
covered by `tests/test_media_process_endpoint.py` and `tests/test_media_job_lifecycle.py`);
it only proves the ENGINE calls the real pipeline, on the real `chatbot.turns.id`, with no
DB session held across the wait, and reacts correctly to every decision/status the
pipeline can return.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.chatbot_turn import ChatbotTurn
from app.models.media import ContactMediaLimit, ContactMediaUsage, MediaExtractionJob
from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod

from tests.chatbot.test_engine import (
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,  # noqa: F401
    stub_access,  # noqa: F401
    stub_parser,  # noqa: F401
)


# --------------------------------------------------------------------------- #
# Per-test contact ids.
#
# Coordinator fix round, 23 Sep 2026: every test in this module used to share
# `test_engine.CONTACT_ID` (900000008). The media gate's burst check
# (`media_access_service.decide_and_record` step 5) hits REAL Redis keyed by
# `respond_io_id` with a real TTL bucket - NOT rolled back with the blank-schema
# Postgres transaction each test rolls back - so running every test that reaches the
# gate in ONE pytest invocation (CI's own shape) shares one bucket. `media_burst_limit`
# defaults to 5/60s (`app/models/user.py:524`), so the 6th "accepted" decision anywhere
# in the run is `denied_burst` regardless of what that particular test is checking -
# measured, not guessed (`test_media_storage_link.py`'s storage-upload-failure test
# came back `decision: denied_burst` before this fix, with a completely unrelated
# assertion). Every test that seeds a `ContactMediaLimit` row (i.e. reaches the gate)
# now gets its OWN contact id via `_fresh_contact_id()`; `_seed_settings` also defaults
# `media_burst_limit` high as a second, redundant safety net. Mirrors
# `test_console_media_turn.py::_seeded_contact_and_media_limit`'s own docstring, which
# independently hit and named the same Redis-bucket hazard for the SAME reason.
# --------------------------------------------------------------------------- #


def _fresh_contact_id() -> int:
    """A `contact.id` no other test in this run has used - an int, the shape Respond.io
    actually sends (`test_engine.py`'s own `CONTACT_ID` doc-comment)."""
    return 800_000_000 + (uuid.uuid4().int % 99_999_999)


def _seed_media_contact(session_factory, contact_id: int) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb)) "
            "ON CONFLICT DO NOTHING"
        ),
        {"cid": str(contact_id), "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Envelope builders - the Respond.io shapes named in the plan/UAC verbatim.
# --------------------------------------------------------------------------- #


def _attachment_envelope(
    *,
    kind: str,
    url: str = "https://cdn.example/x.jpg",
    mime_type: str = "image/jpeg",
    description: str | None = None,
    size: int = 98641,
    duration_ms: int | None = None,
    contact_id: int = CONTACT_ID,
) -> Any:
    envelope = _envelope()
    if contact_id != CONTACT_ID:
        envelope.contact["id"] = contact_id
        envelope.message["contact"]["id"] = contact_id
        envelope.message["message"]["contactId"] = contact_id
    attachment: dict[str, Any] = {"type": kind, "url": url, "mimeType": mime_type, "size": size}
    if description is not None:
        attachment["description"] = description
    if duration_ms is not None:
        attachment["duration"] = duration_ms
    envelope.message["message"]["message"] = {"type": "attachment", "attachment": attachment}
    return envelope


def _image_envelope(caption: str | None = None, *, contact_id: int = CONTACT_ID) -> Any:
    return _attachment_envelope(kind="image", description=caption, contact_id=contact_id)


def _voice_envelope(*, contact_id: int = CONTACT_ID) -> Any:
    return _attachment_envelope(
        kind="audio",
        mime_type="audio/ogg",
        url="https://cdn.example/v.ogg",
        duration_ms=4500,
        contact_id=contact_id,
    )


def _document_envelope(*, contact_id: int = CONTACT_ID) -> Any:
    return _attachment_envelope(
        kind="document", url="https://cdn.example/doc.pdf", mime_type="application/pdf", contact_id=contact_id
    )


def _already_patched_envelope(rendered_text: str = "please check stock for A", *, contact_id: int = CONTACT_ID) -> Any:
    """n8n's transition-window shape (review round S3): `type:"text"` on the message
    PLUS a top-level `envelope.media` carrying what n8n's own pipeline already
    decided - `{envelope: {..., message, media: <patched item>}}` - never an inner
    `_media` marker nested inside the message (that reading is retired)."""
    envelope = _envelope()
    if contact_id != CONTACT_ID:
        envelope.contact["id"] = contact_id
        envelope.message["contact"]["id"] = contact_id
        envelope.message["message"]["contactId"] = contact_id
    envelope.message["message"]["message"] = {"type": "text", "text": rendered_text}
    envelope.media = {"_media": {"modality": "image", "already_patched": True}}
    return envelope


# --------------------------------------------------------------------------- #
# Pipeline seams
# --------------------------------------------------------------------------- #


def _contact_uuid(session_factory, contact_id: int = CONTACT_ID) -> str:
    db = session_factory()
    return db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(contact_id)}
    ).scalar()


def _seed_media_limit(
    session_factory, *, contact_id: int = CONTACT_ID, modality: str = "image", allowed: bool = True
) -> None:
    db = session_factory()
    db.add(
        ContactMediaLimit(
            contact_id=_contact_uuid(session_factory, contact_id), modality=modality, is_allowed=allowed
        )
    )
    db.commit()


def _seed_settings(session_factory, **overrides: Any) -> None:
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    # Redundant safety net beside the per-test fresh contact id (see module docstring
    # above): a test that forgets to ask for a fresh id still does not spuriously trip
    # the burst gate, because the window this run's tests could plausibly fit in never
    # reaches four figures of accepted media messages for one contact.
    overrides.setdefault("media_burst_limit", 1000)
    for key, value in overrides.items():
        setattr(row, key, value)
    db.commit()


@pytest.fixture()
def media_pipeline(monkeypatch, session_factory):
    """Wires the real gate/ledger through a spy, runs the "worker" inline, and lets the
    test hand back a canned extraction result. Returns a small controller object.
    """
    import app.api.v1.external.media as media_route
    import app.tasks.media_tasks as media_tasks_mod
    from app.services.media_access_service import decide_and_record as real_decide_and_record

    calls: list[Any] = []

    def _spy_decide_and_record(db, request, **kwargs):
        calls.append(request)
        return real_decide_and_record(db, request, **kwargs)

    monkeypatch.setattr(media_route, "decide_and_record", _spy_decide_and_record)

    def _inline_enqueue(func, *args, **kwargs):
        func(*args)
        return type("FakeJob", (), {"id": "fake-rq-job"})()

    monkeypatch.setattr(media_route, "enqueue_job", _inline_enqueue)
    monkeypatch.setattr(media_tasks_mod, "SessionLocal", session_factory)

    class _Controller:
        def set_result(
            self,
            result: dict[str, Any] | None = None,
            *,
            error: Exception | None = None,
            media_bytes: bytes | None = None,
            media_content_type: str | None = None,
        ) -> None:
            """`media_bytes` (security review item 1): `_store_media_bytes` reads the
            fetched bytes back off the result's own transient keys rather than
            re-fetching - a test that wants a real attachment row (rather than the
            silent "no bytes to store" no-op) passes them here, the same shape
            `media_extract.service._result_with_bytes` produces for real.
            """
            def _run(job):
                if error is not None:
                    raise error
                body = dict(result) if result is not None else {}
                if media_bytes is not None:
                    body["_media_bytes"] = media_bytes
                    body["_media_content_type"] = media_content_type
                return body

            monkeypatch.setattr(media_tasks_mod, "run_media_extraction", _run)

        def never_runs(self) -> None:
            """The job stays QUEUED forever - simulates outliving the sync wait."""
            monkeypatch.setattr(media_route, "enqueue_job", lambda *a, **k: type("FakeJob", (), {"id": "never"})())

        @property
        def calls(self) -> list[Any]:
            return calls

    return _Controller()


def _trace_of(session_factory, turn_id: str) -> list[dict[str, Any]]:
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    assert row is not None, f"no chatbot.turns row for {turn_id}"
    return row.trace or []


def _media_intake_stage(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in trace if r.get("stage") == "media_intake"), None)


def _usage_rows(session_factory, contact_id: int = CONTACT_ID) -> list[ContactMediaUsage]:
    return (
        session_factory()
        .query(ContactMediaUsage)
        .filter(ContactMediaUsage.respond_io_id == str(contact_id))
        .all()
    )


IMAGE_RESULT = {
    "rendered_text": "Check stock: A, B",
    "confirmation_message": "I read A and B from that photo. Is that right?",
    "entities": [{"raw": "A"}, {"raw": "B"}],
    "attributes": [],
    "notes": None,
    "truncated": False,
    "needs_clarification": False,
}


class TestImageIntakeStage:
    """AC-1800."""

    def test_image_envelope_records_media_intake_stage(
        self, session_factory, stub_parser, stub_access, media_pipeline
    ):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(IMAGE_RESULT)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(
            _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
        )

        trace = _trace_of(session_factory, result.turn_id)
        stage = _media_intake_stage(trace)
        assert stage is not None, (
            "no media_intake trace stage was recorded - the engine does not yet run "
            "image intake inside run_turn (S2 not implemented)"
        )
        facts = stage.get("facts") or {}
        assert facts.get("modality") == "image"
        assert facts.get("decision") == "accepted"
        assert facts.get("status") == "completed"
        assert "elapsed_ms" in facts
        # Review round S5: `job_id` moved to the record's `raw` (never printed on
        # the trace screen - `facts` alone is display-safe, and a UUID is not).
        assert (stage.get("raw") or {}).get("job_id")


class TestVoiceIntakeStage:
    """AC-1801: modality voice, duration_ms forwarded."""

    def test_voice_envelope_records_media_intake_stage(
        self, session_factory, stub_parser, stub_access, media_pipeline
    ):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": "stock for SRTWB1455", "confirmation_message": "ok"})
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_voice_envelope(contact_id=contact_id), session_factory=session_factory)

        trace = _trace_of(session_factory, result.turn_id)
        stage = _media_intake_stage(trace)
        assert stage is not None, "no media_intake stage - voice intake not implemented"
        facts = stage.get("facts") or {}
        assert facts.get("modality") == "voice"
        assert facts.get("status") == "completed"

        assert media_pipeline.calls, "decide_and_record was never called for the voice envelope"
        assert media_pipeline.calls[0].duration_ms == 4500


class TestTurnIdAndContext:
    """AC-1802: `MediaProcessRequest.turn_id` == `chatbot.turns.id`; `context.source ==
    "chat-turn"`; `contact_media_usage.turn_id` carries the same id."""

    def test_turn_id_and_context_source(
        self, session_factory, stub_parser, stub_access, media_pipeline
    ):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(IMAGE_RESULT)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(
            _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
        )

        assert media_pipeline.calls, "media_intake never built a MediaProcessRequest"
        request = media_pipeline.calls[0]
        assert str(request.turn_id) == str(result.turn_id)
        assert (request.context or {}).get("source") == "chat-turn"

        usage_rows = _usage_rows(session_factory, contact_id)
        assert len(usage_rows) == 1
        assert usage_rows[0].turn_id == str(result.turn_id)


class TestNoSessionHeldAcrossTheWait:
    """AC-1803: no DB session is open while the intake call is in flight; the wait is
    bounded by `media_sync_wait_seconds`.

    Observes the REAL poll loop (`app.services.chatbot.media_intake._poll`), rather
    than running the "worker" inline during `enqueue_job` (the `media_pipeline`
    fixture's own technique, used by every other test in this file): running the job
    inline finishes it before the loop's own wait ever starts, so the count taken at
    that point is dominated by whichever session happens to still be open around the
    enqueue call - a test-harness artifact, not the property AC-1803 is actually about.
    Here the job is left QUEUED and `time.sleep` (the loop's own between-reads pause,
    `media_intake.py::_poll`) is the seam counted at, then used to flip the job
    terminal so the loop's next read picks it up - the same "count during the real
    wait, then let it resolve" shape `test_engine.py::TestSessionDiscipline` uses for
    the parser call.
    """

    def test_no_session_open_while_the_extraction_runs(
        self, counting_session_factory, session_factory, stub_parser, stub_access, monkeypatch
    ):
        import time as real_time

        from app.models.media import MediaExtractionJob
        from app.services.chatbot import media_intake as media_intake_mod

        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        stub_parser()
        stub_access()

        # The job is created for real (through the real gate) but never actually
        # runs - `enqueue_job` becomes a no-op, exactly `media_pipeline.never_runs()`'s
        # own technique.
        import app.api.v1.external.media as media_route

        monkeypatch.setattr(
            media_route, "enqueue_job", lambda *a, **k: type("FakeJob", (), {"id": "never"})()
        )

        observed: list[int] = []

        class _FakeTime:
            """Forwards everything but `sleep`, which is `_poll`'s own between-reads
            pause - the actual "wait" AC-1803 is about."""

            def monotonic(self) -> float:
                return real_time.monotonic()

            def perf_counter(self) -> float:
                return real_time.perf_counter()

            def sleep(self, seconds: float) -> None:
                observed.append(counting_session_factory.state["open"])
                if len(observed) == 1:
                    # Resolve the job now, using a session OF ITS OWN (never the
                    # counted factory - a test helper writing the canned result is
                    # not part of what this test measures), so `_poll`'s next read
                    # returns terminal instead of looping until the real timeout.
                    db = session_factory()
                    job = db.query(MediaExtractionJob).order_by(MediaExtractionJob.created_at.desc()).first()
                    assert job is not None, "no MediaExtractionJob row was created by the gate"
                    job.status = "completed"
                    job.result = IMAGE_RESULT
                    db.commit()

        monkeypatch.setattr(media_intake_mod, "time", _FakeTime())

        engine_mod.run_turn(
            _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=counting_session_factory
        )

        assert observed, (
            "the poll loop never slept once - media intake is not wired into "
            "run_turn yet (S2 not implemented), so there was no wait to observe"
        )
        assert observed == [0], (
            f"a DB session was held open across the media extraction wait: {observed}"
        )


class TestNonImageAttachmentsNotIntaked:
    """AC-1804: a document is not intake and the turn runs on its caption text.

    NOTE FOR THE CAPTAIN (flagged, not silently resolved): a bare "no media_intake
    stage" assertion is TRUE today for every envelope, image included, because no intake
    exists at all yet - so this test alone does not fail for the media feature's absence.
    It is paired here with a positive assertion (the caption text reaches the parser,
    today's existing `attachment.description` fallback) so the test is still meaningful,
    but it will not go red before the coder's change the way the other tests in this file
    do. Recorded rather than deleted because it is a real regression guard once S2 lands.
    """

    def test_document_attachment_is_not_intaked(self, session_factory, seeded, stub_parser, stub_access):
        blocks: list[str] = []
        stub_parser(_parser_output(), on_call=blocks.append)
        stub_access()

        envelope = _document_envelope()
        envelope.message["message"]["message"]["attachment"]["description"] = "here is the price list"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        trace = _trace_of(session_factory, result.turn_id)
        assert _media_intake_stage(trace) is None
        assert "here is the price list" in blocks[0]


class TestAlreadyPatchedEnvelopeNotIntakedTwice:
    """AC-1805, restated by review round S3: n8n's own pipeline already decided,
    metered and recorded this one upstream (`envelope.media`), so THIS module must
    run no intake at all for it - zero ledger rows, not one. The old reading (an
    inner `_media` marker, metered again through the SAME `run()` call as a live
    attachment) double-counted a message n8n had already metered itself.
    """

    def test_zero_usage_rows_for_a_patched_upstream_envelope(
        self, session_factory, stub_parser, stub_access, media_pipeline
    ):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(IMAGE_RESULT)
        stub_parser()
        stub_access()

        envelope = _already_patched_envelope(contact_id=contact_id)
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        rows = _usage_rows(session_factory, contact_id)
        assert len(rows) == 0, (
            f"expected ZERO contact_media_usage rows for a patched-upstream "
            f"envelope (n8n already metered it) - found {len(rows)}"
        )
        assert not media_pipeline.calls, "decide_and_record ran for a patched-upstream envelope"

        trace = _trace_of(session_factory, result.turn_id)
        stage = _media_intake_stage(trace)
        assert stage is not None
        assert (stage.get("facts") or {}).get("skipped") == "patched_upstream"


class TestAudioDeadEndRemoved:
    """AC-1806: `AUDIO_NOT_PATCHED_ERROR` is gone - a raw voice envelope never fails
    with the old "media intake did not transcribe this voice note" wording."""

    def test_voice_envelope_never_hits_the_retired_error(
        self, session_factory, stub_parser, stub_access, media_pipeline
    ):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": "stock for SRTWB1455"})
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(_voice_envelope(contact_id=contact_id), session_factory=session_factory)

        assert result.reply is None or "did not transcribe" not in (result.reply or {}).get("text", "")
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row.error is None or "did not transcribe" not in row.error


class TestParserInputShapes:
    """AC-1807/AC-1808/AC-1809: what the parser is actually handed."""

    def test_caption_and_raws_joined(self, session_factory, stub_access, media_pipeline):
        blocks: list[str] = []

        def _fake_parse(config, user_block):
            blocks.append(user_block)
            return _parser_output()

        import app.services.chatbot.head.parser as parser_mod

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        from unittest.mock import patch as _patch

        contact_id = _fresh_contact_id()
        with _patch.object(parser_mod, "resolve_config", fake_resolve_config), _patch.object(
            parser_mod, "parse", _fake_parse
        ):
            _seed_media_contact(session_factory, contact_id)
            _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
            _seed_settings(session_factory, media_sync_wait_seconds=5)
            media_pipeline.set_result(IMAGE_RESULT)
            stub_access()

            engine_mod.run_turn(
                _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
            )

        assert blocks, "the parser was never called"
        assert "Check stock: A, B" in blocks[0], blocks[0]

    def test_no_caption_still_renders_the_raws(self, session_factory, stub_parser, stub_access, media_pipeline):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {
                "rendered_text": "A, B",
                "entities": [{"raw": "A"}, {"raw": "B"}],
                "needs_clarification": True,
                "confirmation_message": "I read A and B. What would you like me to do with it?",
            }
        )
        blocks: list[str] = []
        stub_parser(on_call=blocks.append)
        stub_access()

        engine_mod.run_turn(_image_envelope(caption=None, contact_id=contact_id), session_factory=session_factory)

        assert blocks, "no-caption image never reached the parser"
        assert "A, B" in blocks[0], blocks[0]

    def test_voice_transcript_reaches_the_parser_verbatim(
        self, session_factory, stub_access, media_pipeline
    ):
        blocks: list[str] = []
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        contact_id = _fresh_contact_id()
        with _patch.object(parser_mod, "resolve_config", fake_resolve_config), _patch.object(
            parser_mod, "parse", lambda config, user_block: blocks.append(user_block) or _parser_output()
        ):
            _seed_media_contact(session_factory, contact_id)
            _seed_media_limit(session_factory, contact_id=contact_id, modality="voice")
            _seed_settings(session_factory, media_sync_wait_seconds=5)
            media_pipeline.set_result({"transcript": "stock for SRTWB1455"})
            stub_access()

            engine_mod.run_turn(_voice_envelope(contact_id=contact_id), session_factory=session_factory)

        assert blocks
        assert "stock for SRTWB1455" in blocks[0], blocks[0]


class TestDenialArms:
    """AC-1810/AC-1811/AC-1812: gate/quota/duration/burst denials are ordinary turn
    replies with the existing notice wording, branch_kind `media_denied`, zero parser
    calls (except the silent burst-repeat arm, which answers empty)."""

    def test_denied_gate_replies_with_not_enabled_wording_and_no_parser_call(
        self, session_factory, seeded, stub_access, monkeypatch
    ):
        # No ContactMediaLimit row at all == denied_gate (absence is denial); this
        # returns at step 3 of `decide_and_record`, before the burst check, so the
        # shared `CONTACT_ID`/`seeded` fixture is safe here (never touches Redis).
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        parser_calls: list[str] = []
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        from app.services.media_extract import wording

        stub_access()
        with _patch.object(parser_mod, "parse", lambda *a, **k: parser_calls.append(1) or {}):
            result = engine_mod.run_turn(_image_envelope(caption="Check stock"), session_factory=session_factory)

        assert result.branch_kind == "media_denied", (
            f"expected branch_kind 'media_denied', got {result.branch_kind!r} - denial "
            "arm not implemented"
        )
        assert result.reply is not None
        assert wording.not_enabled("image") in result.reply.get("text", "")
        assert not parser_calls, "the parser must not be called on a media denial"

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row.status == "done"

    def test_denied_burst_already_shown_answers_empty_with_no_actions(
        self, session_factory, stub_access, media_pipeline
    ):
        """AC-1812. `media_access_service.decide_and_record` step 5 shows the burst
        notice on the FIRST denial in a window (`first_in_window`) and stays silent on
        every one after - so the sequence needed to reach the "already shown" arm is
        accept, deny-with-notice, THEN deny-silently: three calls, not two (a bug in
        this test's own original sequencing, caught in the fix round once the shared-
        contact-id burst pollution stopped masking it - measured against the real
        `decide_and_record`, never assumed). Own dedicated contact id, separate from
        every other test in this module, and a LOW explicit `media_burst_limit`
        (overriding `_seed_settings`'s high default) - this is the one test in the file
        meant to actually trip the burst gate."""
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5, media_burst_limit=1)
        media_pipeline.set_result(IMAGE_RESULT)
        stub_access()

        with _patch.object(parser_mod, "parse", lambda *a, **k: {}):
            # First image: consumes the burst allowance (accepted).
            first_envelope = _image_envelope(caption="Check stock", contact_id=contact_id)
            first_envelope.message["message"]["messageId"] = "ZZT-media-burst-1"
            engine_mod.run_turn(first_envelope, session_factory=session_factory)

            # Second image, same window: denied_burst, notice SHOWN (first denial).
            second_envelope = _image_envelope(caption="Check stock", contact_id=contact_id)
            second_envelope.message["message"]["messageId"] = "ZZT-media-burst-2"
            second_result = engine_mod.run_turn(second_envelope, session_factory=session_factory)

            # Third image, same window: denied_burst, notice ALREADY shown - silent.
            third_envelope = _image_envelope(caption="Check stock", contact_id=contact_id)
            third_envelope.message["message"]["messageId"] = "ZZT-media-burst-3"
            result = engine_mod.run_turn(third_envelope, session_factory=session_factory)

        assert second_result.branch_kind == "media_denied"
        assert (second_result.reply or {}).get("text"), "the FIRST denial must carry the burst notice"

        assert result.branch_kind == "media_denied"
        assert (result.reply or {}).get("text") == ""
        assert result.actions == []


class TestFailedAndPendingArms:
    """AC-1813/AC-1814."""

    def test_failed_extraction_replies_nothing_read(
        self, session_factory, stub_access, media_pipeline
    ):
        from app.services.media_extract import wording
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(error=RuntimeError("provider timed out"))
        stub_access()

        with _patch.object(parser_mod, "parse", lambda *a, **k: {}):
            result = engine_mod.run_turn(
                _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
            )

        assert (result.reply or {}).get("text") == wording.nothing_read()
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row.status == "failed"

    def test_pending_after_the_wait_replies_the_timeout_sentence(
        self, session_factory, stub_access, media_pipeline
    ):
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)  # floored to 5s
        media_pipeline.never_runs()
        stub_access()

        with _patch.object(parser_mod, "parse", lambda *a, **k: {}):
            result = engine_mod.run_turn(
                _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
            )

        assert (result.reply or {}).get("text") == (
            "I could not read that photo in time. Please send it again or type the codes."
        )
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row.status == "failed"


class TestQuotaWarnNoticeAppended:
    """AC-1815."""

    def test_warn_notice_appended_once(self, session_factory, stub_parser, stub_access, media_pipeline):
        contact_id = _fresh_contact_id()
        _seed_media_contact(session_factory, contact_id)
        _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
        # limit=1, warn threshold low enough that the FIRST accepted item already warns.
        _seed_settings(
            session_factory,
            media_sync_wait_seconds=5,
            media_image_monthly_limit=1,
            media_warn_threshold_percent=1,
        )
        media_pipeline.set_result(IMAGE_RESULT)
        stub_parser()
        stub_access()

        result = engine_mod.run_turn(
            _image_envelope(caption="Check stock", contact_id=contact_id), session_factory=session_factory
        )

        text_reply = (result.reply or {}).get("text") or ""
        assert text_reply.count("resets") <= 1  # sanity: not appended twice
        usage = _usage_rows(session_factory, contact_id)
        assert usage and usage[0].notices, "expected a warn_80 notice recorded on the ledger row"
        assert any(n.get("kind") == "warn_80" for n in usage[0].notices)
        assert any(n.get("text") in text_reply for n in usage[0].notices if n.get("kind") == "warn_80")


class TestConsoleBuildsTheSameEnvelope:
    """AC-1816: the console path builds the SAME attachment envelope and calls
    `run_turn` - `_run_console_media_turn`'s own poll+turn code is deleted."""

    def test_poll_media_job_helper_is_gone(self) -> None:
        from app.services.chatbot import console_service

        assert not hasattr(console_service, "_poll_media_job"), (
            "console_service._poll_media_job still exists - S2's collapse to "
            "'build an envelope, call run_turn' has not happened yet"
        )

    def test_console_calls_run_turn_with_an_attachment_envelope(self, monkeypatch, session_factory):
        """Whatever the console's new implementation looks like, it must call the ONE
        `run_turn` seam with an envelope carrying `message.message.attachment`, not the
        retired `run_console_turn(text=...)` shape."""
        from app.services.chatbot import console_service

        captured: list[Any] = []

        def _fake_run_turn(envelope, *, session_factory):
            captured.append(envelope)
            raise NotImplementedError("stop here - only the call shape is under test")

        monkeypatch.setattr(console_service, "run_turn", _fake_run_turn, raising=False)
        # `_run_console_media_turn` uploads the console's bytes BEFORE building the
        # envelope (`_upload_console_media`, real S3/R2 storage) - mocked here so the
        # test reaches the call under test rather than dying on a real network attempt,
        # never asserted on itself (S4's own storage tests own that).
        monkeypatch.setattr(
            console_service, "_upload_console_media", lambda **kwargs: "https://fake.example/media.jpg"
        )

        with pytest.raises(Exception):
            console_service._run_console_media_turn(
                session_factory(),
                contact_respond_id=str(CONTACT_ID),
                caption="",
                session_vars=None,
                prompt_version_id=None,
                run_id=f"ZZT-console-{uuid.uuid4().hex[:8]}",
                media={"kind": "image", "filename": "p.jpg", "mime": "image/jpeg", "content_base64": "Zm9v"},
            )

        assert captured, (
            "console_service.run_turn was never called - _run_console_media_turn has "
            "not been collapsed to build-envelope-and-call-run_turn yet"
        )
        envelope = captured[0]
        inner = envelope.message["message"]["message"] if hasattr(envelope, "message") else envelope["message"]["message"]["message"]
        assert inner.get("attachment", {}).get("type") == "image"
