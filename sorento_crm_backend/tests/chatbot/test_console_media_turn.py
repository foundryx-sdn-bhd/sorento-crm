"""The in-app chatbot console's media path (commit 2, chatbot growth r1 final): image and
voice through the REAL `/external/media/process` pipeline
(`documentation/plans/_archive/ideation/PLAN-chatbot-media-endpoint.md`), no second
extractor.

Seams faked, same technique `tests/test_media_job_lifecycle.py` uses for the extraction
itself: `app.tasks.media_tasks.run_media_extraction` returns a canned result instead of
calling a real vision/transcription provider. Two more seams are console-specific:
`console_service._upload_console_media` (no real S3/R2 credentials in a test run) and
`app.services.queue_service.enqueue_job` (runs the "RQ" job INLINE and synchronously,
against this test's own blank-schema session, rather than a real worker process - which is
sound here because the whole call happens inside one test, unlike
`test_media_job_lifecycle.py`'s cross-process job which needs a real committed DB).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.chatbot_turn import ChatbotTurn
from app.models.media import ContactMediaLimit, ContactMediaUsage, MediaExtractionJob
from app.services.chatbot import console_service
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.user_service import UserPermissionService

from tests.chatbot.test_console_turn_endpoint import NOT_SUPPORTED_OUTPUT
from tests.chatbot.test_engine import _envelope

VIEW = "system.chat_history.view"
CONSOLE_TURN_URL = "/api/v1/system/chatbot/console/turn"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Console Media Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(session_factory):
    def _override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def stub_console_and_media_seams(monkeypatch, session_factory):
    """The text-turn seams (`test_console_turn_endpoint.py::stub_console_seams`) PLUS the
    media-specific ones: the "worker" runs inline against this same blank schema, the
    extraction is canned, and the storage upload never reaches real S3/R2.
    """
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    monkeypatch.setattr(
        console_service, "_upload_console_media", lambda **kwargs: "https://fake.example/media.jpg"
    )

    import app.tasks.media_tasks as media_tasks_mod

    monkeypatch.setattr(media_tasks_mod, "SessionLocal", session_factory)


def _seeded_contact_and_media_limit(session_factory, *, sync_wait_seconds: int = 5) -> tuple[str, str]:
    """Returns `(respond_io_id, contact_id)`.

    A FRESH `respond_io_id` per call, never the shared `CONTACT_ID` other chatbot tests
    reuse: the media gate's burst check (`decide_and_record` step 5) buckets by
    `respond_io_id` in REAL Redis with a real TTL - not rolled back with the blank-schema
    Postgres transaction - so a fixed id lets a stray key from an earlier run (this file
    alone, or alongside another chatbot test module in the same pytest invocation) trip
    `media_burst_limit` and turn an expected `accepted`/`pending` into `denied_burst`
    (read back here as `media_status: "failed"`). Mirrors
    `test_media_process_endpoint.py::_contact`'s own reason for doing this.
    """
    from app.models.user import SystemSetting

    db = session_factory()
    contact_id = str(uuid.uuid4())
    respond_io_id = f"ZZT-console-media-{uuid.uuid4().hex[:10]}"
    db.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (:id, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"id": contact_id, "cid": respond_io_id, "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
    )
    envelope = _envelope(**{"contact": {"id": respond_io_id}})
    db.add(
        ChatbotTurn(
            contact_respond_id=respond_io_id,
            message_id="ZZT-seed-msg",
            ingress="webhook",
            envelope=json.loads(envelope.model_dump_json()),
            is_test=False,
            status="done",
            stage="sent",
            branch_kind="business_query",
        )
    )
    db.add(ContactMediaLimit(contact_id=contact_id, modality="image", is_allowed=True))
    db.add(SystemSetting(media_sync_wait_seconds=sync_wait_seconds))
    db.commit()
    return respond_io_id, contact_id


def _run_inline(monkeypatch):
    """`enqueue_job` runs the "worker" INLINE, synchronously, against this test's own
    session (via the `media_tasks_mod.SessionLocal` patch above) - so by the time
    `_run_console_media_turn`'s poll loop runs, the job is already terminal on its first
    read.

    Patched at `app.api.v1.external.media.enqueue_job` - the `from`-import that module
    holds, not `app.services.queue_service.enqueue_job` itself: `_run_console_media_turn`
    (S2's collapse to one media path) calls `_decide_meter_record_and_enqueue`, which
    lives in `app.api.v1.external.media` and calls `enqueue_job` through ITS OWN bound
    name, so patching the `queue_service` module attribute never reaches it. Same seam
    `test_media_process_endpoint.py` patches for the exact same function.
    """
    def _inline_enqueue(func, *args, **kwargs):
        func(*args)
        return type("FakeJob", (), {"id": "fake-rq-job"})()

    monkeypatch.setattr("app.api.v1.external.media.enqueue_job", _inline_enqueue)


def _media_payload(content_base64: str = "Zm9v") -> dict[str, Any]:
    return {"kind": "image", "filename": "photo.jpg", "mime": "image/jpeg", "content_base64": content_base64}


class TestConsoleMediaTurnCompletesInline:
    def test_image_turn_extracts_then_runs_the_chatbot_turn(
        self, client, session_factory, stub_console_and_media_seams, monkeypatch,
    ):
        respond_io_id, _ = _seeded_contact_and_media_limit(session_factory)
        _run_inline(monkeypatch)

        import app.tasks.media_tasks as media_tasks_mod

        monkeypatch.setattr(
            media_tasks_mod,
            "run_media_extraction",
            lambda job: {"rendered_text": "please check stock for SRTWC8517", "entities": []},
        )

        resp = client.post(
            CONSOLE_TURN_URL,
            json={
                "contact_respond_id": respond_io_id,
                "text": "",
                "run_id": f"ZZT-console-{uuid.uuid4().hex[:8]}",
                "media": _media_payload(),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_status"] == "done"
        assert body["media_id"] is not None
        assert body["media_text"] == "please check stock for SRTWC8517"
        # The extracted text became the turn's own message - the stubbed parser output
        # (`NOT_SUPPORTED_OUTPUT`) is a fixed canned response regardless of the text sent,
        # so what proves the wiring is the media_status/media_text pair above plus a real
        # branch_kind/reply coming back at all (the turn actually ran).
        assert body["branch_kind"] == "not_supported"
        assert body["reply_text"]

        # Rows written: the real ledger + job (D14 deviation, documented in
        # console_service.py), plus exactly one NEW is_test chatbot.turns row.
        usage = (
            session_factory()
            .query(ContactMediaUsage)
            .filter(ContactMediaUsage.respond_io_id == respond_io_id)
            .all()
        )
        assert len(usage) == 1
        assert usage[0].outcome == "accepted"
        jobs = session_factory().query(MediaExtractionJob).filter(
            MediaExtractionJob.usage_id == usage[0].id
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status == "completed"
        turn_row = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.id == body["turn_id"])
            .first()
        )
        assert turn_row is not None
        assert turn_row.is_test is True
        assert turn_row.ingress == "console"

    def test_a_failed_extraction_reports_the_reason_with_no_business_logic_run(
        self, client, session_factory, stub_console_and_media_seams, monkeypatch,
    ):
        """A failed extraction never reaches the parser or a lane (S2's `media_denied`
        arm) - proven by `branch_kind`, not by `turn_id`. `turn_id` is now ALWAYS
        populated (S2's collapse to one path: `_run_console_media_turn` always calls
        `run_turn`, which inserts the `chatbot.turns` row first thing, before media
        intake decides anything) - the pre-collapse console implementation ran a
        SEPARATE, turn-less path for a caption-less media send, which no longer exists.
        """
        respond_io_id, _ = _seeded_contact_and_media_limit(session_factory)
        _run_inline(monkeypatch)

        import app.tasks.media_tasks as media_tasks_mod

        def _boom(job):
            raise RuntimeError("provider timed out")

        monkeypatch.setattr(media_tasks_mod, "run_media_extraction", _boom)

        resp = client.post(
            CONSOLE_TURN_URL,
            json={
                "contact_respond_id": respond_io_id,
                "text": "",
                "run_id": f"ZZT-console-{uuid.uuid4().hex[:8]}",
                "media": _media_payload(),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_status"] == "failed"
        assert "provider timed out" in (body["media_error"] or "")
        assert body["turn_id"] is not None
        assert body["branch_kind"] == "media_denied", "no parser call, no lane - denied at intake alone"


class TestConsoleMediaTurnPendingThenDonePoll:
    def test_outliving_the_sync_wait_reports_pending_then_the_poll_reports_done(
        self, client, session_factory, stub_console_and_media_seams, monkeypatch,
    ):
        # sync_wait_seconds is clamped to a 5s floor (media_access_service.
        # SYNC_WAIT_MIN_SECONDS) - the job is left QUEUED (never run) so the wait
        # genuinely outlives it.
        respond_io_id, _ = _seeded_contact_and_media_limit(session_factory, sync_wait_seconds=5)

        def _never_runs(func, *args, **kwargs):
            return type("FakeJob", (), {"id": "fake-rq-job"})()

        monkeypatch.setattr("app.services.queue_service.enqueue_job", _never_runs)

        resp = client.post(
            CONSOLE_TURN_URL,
            json={
                "contact_respond_id": respond_io_id,
                "text": "",
                "run_id": f"ZZT-console-{uuid.uuid4().hex[:8]}",
                "media": _media_payload(),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["media_status"] == "pending"
        media_id = body["media_id"]
        assert media_id

        poll = client.get(f"/api/v1/system/chatbot/console/media/{media_id}")
        assert poll.status_code == 200, poll.text
        assert poll.json()["status"] == "pending"

        # The "worker" finally runs, out of band from the request that timed out.
        import app.tasks.media_tasks as media_tasks_mod

        monkeypatch.setattr(
            media_tasks_mod, "run_media_extraction", lambda job: {"rendered_text": "heard it"}
        )
        media_tasks_mod.process_media_extraction(media_id)

        poll2 = client.get(f"/api/v1/system/chatbot/console/media/{media_id}")
        assert poll2.status_code == 200, poll2.text
        done = poll2.json()
        assert done["status"] == "done"
        assert done["text"] == "heard it"
        assert done["error"] is None

    def test_an_unknown_media_id_is_a_404(self, client, session_factory, stub_console_and_media_seams):
        resp = client.get(f"/api/v1/system/chatbot/console/media/{uuid.uuid4()}")
        assert resp.status_code == 404, resp.text

    def test_a_real_customers_job_is_a_404_too(self, client, session_factory, stub_console_and_media_seams):
        """Review round 2, nit 2: the poll is scoped to console jobs (`message_id LIKE
        'console-media-%'`), so a job from a real WhatsApp message cannot be read here."""
        from app.models.media import ContactMediaUsage, MediaExtractionJob

        db = session_factory()
        usage = ContactMediaUsage(
            id=str(uuid.uuid4()), respond_io_id="900000008", modality="image",
            message_id="wamid.REAL-CUSTOMER-MESSAGE", media_ordinal=0, period_key="2026-09",
            outcome="extract",
        )
        db.add(usage)
        db.flush()
        job = MediaExtractionJob(id=str(uuid.uuid4()), usage_id=usage.id, status="done", modality="image")
        db.add(job)
        db.commit()
        resp = client.get(f"/api/v1/system/chatbot/console/media/{job.id}")
        assert resp.status_code == 404, resp.text


class TestConsoleMediaPermissionGate:
    def test_media_status_needs_the_view_permission(self, client):
        _GRANTS.discard(VIEW)
        resp = client.get(f"/api/v1/system/chatbot/console/media/{uuid.uuid4()}")
        assert resp.status_code == 403, resp.text
