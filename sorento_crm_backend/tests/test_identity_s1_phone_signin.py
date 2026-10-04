"""S1 phone sign-in red tests (#1280): AC-21 to AC-24, AC-26, AC-27, AC-28 (backend half).

Written against `documentation/plans/identity/s1-contract.md` and
`identity-unified-login-acceptance-criteria.md` (AC-20 to AC-29) BEFORE any
implementation exists. No route under `/api/v1/auth/phone/*` and no
`app.tasks.respond_io_tasks.send_login_otp_respond_message` exist yet, so most
of this file is red on a 404 or an ImportError - that is the point.

Postgres only (`tests/_pg_fixture.py`, `blank_session`), one rolled-back
transaction per test, except the two AC-22 task-level tests that exercise the
real ``app.database.SessionLocal`` (mirrors ``tests/test_portal_otp_flow.py``)
because ``_send_and_log`` opens its own session - those clean up every row
they write.

CONTRACT AMBIGUITIES resolved here (see the tester's report to the captain):
  1. The contract names ``GET /api/v1/user-management/account/`` for
     ``has_password`` / ``phone_verified_at``; no such backend route is
     mounted (grepped). The frontend's own account proxy
     (`app/api/user-management/account/route.ts`) forwards to
     ``GET /api/v1/user-management/users/me`` today, so that is what the
     password-flow tests (``test_identity_s1_password.py``) exercise instead.
  2. The per-number 24h daily-send cap's Redis bucket name is not pinned by
     the contract (only the IP bucket names ``phone_signin_otp`` /
     ``phone_signin_verify`` are). ``test_ac23_eleventh_request_in_24h_is_429``
     guesses the bucket ``phone_signin_otp_daily`` - flagged inline and in
     the report.
  3. "A code older than 10 minutes" (AC-23) is tested via the equivalent,
     key-name-agnostic boundary of "a number for which no code was ever
     requested" (no live Redis marker either way) rather than by moving a
     hidden marker whose key format is not specified.

SECURITY ROUND (#1280) test changes, authorised by the captain:
  - S1 (timing): `POST /phone/request-code` no longer decides eligibility
    itself - it enqueues `dispatch_phone_signin_code` for EVERY number,
    known or not, with identical args, and that job (not the route) creates
    the code only for an eligible one. Every AC-21 test that used to assert
    "enqueue called only for the eligible number" now asserts eligibility
    directly via `find_eligible`, and `test_ac21_code_created_and_enqueued_
    only_for_the_eligible_user` is renamed and rewritten to prove BOTH halves
    (identical enqueue, eligible-only dispatch). Every AC-23/24/27 test that
    used to read the OTP code back from `mock_enqueue.call_args.args[4]`
    (the code doesn't reach the route's own enqueue call any more - it's
    generated inside the job) now seeds one directly via `_seed_signin_code`,
    which calls the same `PortalService.create_and_dispatch_otp` the job
    calls, on the test's own db.
  - S4 (per-IP DoS): `test_ac23_verify_per_ip_limit_is_429` pinned a per-IP
    429 on `/phone/verify` that no longer exists (NextAuth calls that route
    server-to-server, so the "IP" is the Next.js server for every real user -
    a shared bucket anyone could exhaust). Renamed and rewritten to prove the
    opposite: a burst of verifies for DIFFERENT numbers from one client is
    NOT globally blocked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.access import RespondContact
from app.models.portal import PortalOtpCode
from app.models.respond_workspace import RespondWorkspace
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from tests._pg_fixture import blank_session, unique_code


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _digits() -> str:
    """A normalised MY mobile number: '60' + '1' + 8 random digits."""
    return "601" + f"{uuid.uuid4().int % 10**8:08d}"


def _national_variant(digits: str) -> str:
    """digits like '601XXXXXXXX' (11 chars) -> a typed national form with
    dashes and a space, e.g. '011-234 56789'. normalize_msisdn strips
    everything but digits/plus, so punctuation placement doesn't matter."""
    national = "0" + digits[2:]
    return f"{national[:4]}-{national[4:7]} {national[7:]}"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _workspace(db) -> RespondWorkspace:
    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=f"sp_{uuid.uuid4().hex[:8]}",
        name="ZZT WS",
        api_key_ciphertext="test-cipher",
    )
    db.add(ws)
    db.flush()
    return ws


def _contact(db, ws, phone: str) -> RespondContact:
    c = RespondContact(
        id=str(uuid.uuid4()), phone_number=phone, name="ZZT Contact", workspace_id=ws.id
    )
    db.add(c)
    db.flush()
    return c


def _user(
    db,
    *,
    phone: str | None = None,
    contact_id: str | None = None,
    status: str = "ACTIVE",
    is_trashed: bool = False,
    is_integration: bool = False,
    email: str | None = None,
    name: str = "ZZT User",
) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=email,
        name=name,
        status=status,
        is_trashed=is_trashed,
        is_integration=is_integration,
        contact_number=phone,
        respond_contact_id=contact_id,
    )
    db.add(u)
    db.commit()
    return u


def _eligible_chain(db):
    """A fully eligible phone-sign-in user: ACTIVE, non-integration, phone ==
    linked contact's phone."""
    ws = _workspace(db)
    digits = _digits()
    contact = _contact(db, ws, digits)
    user = _user(
        db,
        phone=digits,
        contact_id=contact.id,
        email=f"{unique_code('elig')}@x.com".lower(),
    )
    return ws, contact, user, digits


def _seed_signin_code(db, contact) -> str:
    """Security round S1: `/phone/request-code` no longer creates the OTP row
    itself (it enqueues `dispatch_phone_signin_code`, which does). A test
    that needs a REAL, usable code on its own `blank_session` db calls
    `create_and_dispatch_otp` directly with a fake inline task - exactly what
    that job does - instead of reading the code back out of a mocked
    `enqueue_job` call, which no longer carries it."""
    from app.services.phone_signin_service import SIGNIN_OTP_TEXT
    from app.services.portal_service import PortalService

    captured: dict = {}

    def _fake_task(otp_id, identifier, message_text, otp_code, space_id):
        captured["code"] = otp_code
        return {"status": "success"}

    space_id = ""
    workspace = getattr(contact, "workspace", None)
    if workspace is not None:
        space_id = getattr(workspace, "space_id", None) or ""

    PortalService(db).create_and_dispatch_otp(
        contact, space_id, SIGNIN_OTP_TEXT, _fake_task, dispatch_inline=True
    )
    return captured["code"]


class _client_ctx:
    """TestClient bound to a single blank-schema db (get_db overridden)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self._tc = TestClient(app)
        self._client = self._tc.__enter__()
        return self._client

    def __exit__(self, *exc):
        self._tc.__exit__(*exc)
        app.dependency_overrides.pop(get_db, None)


def _phone_client(db):
    return _client_ctx(db)


@pytest.fixture()
def rate_limit_cleanup():
    """Every test appends the phone digits (or 'testclient') it touched;
    torn down here so Redis rate-limit counters never bleed across tests or
    across a shared local Postgres/Redis used by several checkouts."""
    from app.services.queue_service import redis_conn

    idents: list[str] = []
    yield idents
    for ident in idents:
        for key in redis_conn.keys(f"*{ident}*"):
            redis_conn.delete(key)
    for bucket in ("phone_signin_otp", "phone_signin_verify"):
        redis_conn.delete(f"rate_limit:v1:{bucket}:testclient")


# --------------------------------------------------------------------------- #
# AC-21: POST /api/v1/auth/phone/request-code - no enumeration                #
# --------------------------------------------------------------------------- #
def test_ac21_identical_200_shape_for_known_and_unknown_number(rate_limit_cleanup):
    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        unknown = _digits()
        rate_limit_cleanup.extend([digits, unknown])
        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            r1 = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            r2 = client.post("/api/v1/auth/phone/request-code", json={"phone": unknown})

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    b1, b2 = r1.json(), r2.json()
    expected_keys = {"sent_to", "expires_in_seconds", "resend_in_seconds"}
    assert set(b1.keys()) == expected_keys, b1
    assert set(b2.keys()) == expected_keys, b2
    assert b1["expires_in_seconds"] == b2["expires_in_seconds"] == 600
    assert b1["resend_in_seconds"] == b2["resend_in_seconds"] == 60
    # sent_to masks the TYPED number, never a stored one - no "exists" tell.
    assert b1["sent_to"].endswith(digits[-4:]), b1
    assert b2["sent_to"].endswith(unknown[-4:]), b2


def test_ac21_route_enqueues_dispatch_identically_then_dispatch_creates_code_only_for_eligible(
    rate_limit_cleanup,
):
    """Security round S1: the eligibility split moved from the route to the
    `dispatch_phone_signin_code` job it enqueues, so the route itself must be
    identical for a known and an unknown number (part 1), and the job - run
    directly here, against the real DB it opens its own SessionLocal on,
    mirroring the AC-22 task-level tests - must create/send a code only for
    the eligible one (part 2)."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import dispatch_phone_signin_code

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        unknown = _digits()
        rate_limit_cleanup.extend([digits, unknown])
        with _phone_client(db) as client, patch(
            "app.services.queue_service.enqueue_job"
        ) as mock_enqueue:
            r1 = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            r2 = client.post("/api/v1/auth/phone/request-code", json={"phone": unknown})

        assert r1.status_code == 200 and r2.status_code == 200
        assert mock_enqueue.call_count == 2, mock_enqueue.call_args_list
        for call in mock_enqueue.call_args_list:
            assert call.args[0] is dispatch_phone_signin_code
            assert call.kwargs.get("queue_name") == "respond_io"
        assert {call.args[1] for call in mock_enqueue.call_args_list} == {digits, unknown}

    # Part 2: the job itself, real DB (mirrors the AC-22 task-level tests -
    # dispatch_phone_signin_code opens its own SessionLocal, so a
    # blank_session fixture would be invisible to it).
    real_db = SessionLocal()
    ws2 = contact2 = user2 = None
    try:
        ws2, contact2, user2, digits2 = _eligible_chain(real_db)
        unknown2 = _digits()

        with patch("app.tasks.respond_io_tasks.send_login_otp_respond_message") as mock_send:
            dispatch_phone_signin_code(digits2)
        assert mock_send.call_count == 1, mock_send.call_args_list
        assert (
            real_db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact2.id).count() == 1
        )

        with patch("app.tasks.respond_io_tasks.send_login_otp_respond_message") as mock_send_unknown:
            dispatch_phone_signin_code(unknown2)
        assert mock_send_unknown.call_count == 0
    finally:
        if contact2 is not None:
            real_db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact2.id).delete(
                synchronize_session=False
            )
        if user2 is not None:
            real_db.query(User).filter(User.id == user2.id).delete(synchronize_session=False)
        if contact2 is not None:
            real_db.query(RespondContact).filter(RespondContact.id == contact2.id).delete(
                synchronize_session=False
            )
        if ws2 is not None:
            real_db.query(RespondWorkspace).filter(RespondWorkspace.id == ws2.id).delete(
                synchronize_session=False
            )
        real_db.commit()
        real_db.close()


def test_ac21_contact_with_no_user_gets_no_code_and_creates_no_user(rate_limit_cleanup):
    """Security round S1: the route no longer checks eligibility (that moved
    to `dispatch_phone_signin_code`, proved end-to-end above), so this now
    asserts the request-code route's own invariants (200, no user created)
    plus the eligibility decision itself (`find_eligible`), which is what
    actually determines "gets no code"."""
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)  # no linked user at all
        rate_limit_cleanup.append(digits)
        users_before = db.query(User).count()

        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})

        assert resp.status_code == 200, resp.text
        assert db.query(User).count() == users_before
        assert find_eligible(db, digits) is None
        assert (
            db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact.id).count() == 0
        )


def test_ac21_user_whose_phone_differs_from_linked_contact_gets_no_code(rate_limit_cleanup):
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        ws = _workspace(db)
        contact_phone = _digits()
        user_phone = _digits()
        contact = _contact(db, ws, contact_phone)
        user = _user(
            db,
            phone=user_phone,
            contact_id=contact.id,
            email=f"{unique_code('mismatch')}@x.com".lower(),
        )
        rate_limit_cleanup.extend([contact_phone, user_phone])

        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": user_phone})

        assert resp.status_code == 200, resp.text
        assert find_eligible(db, user_phone) is None


@pytest.mark.parametrize(
    "bad_state",
    [
        {"status": "INACTIVE"},
        {"status": "BLOCKED"},
        {"is_trashed": True},
        {"is_integration": True},
    ],
    ids=["inactive", "blocked", "trashed", "integration"],
)
def test_ac21_ineligible_user_states_get_no_code(rate_limit_cleanup, bad_state):
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)
        _user(
            db,
            phone=digits,
            contact_id=contact.id,
            email=f"{unique_code('inelig')}@x.com".lower(),
            **bad_state,
        )
        rate_limit_cleanup.append(digits)

        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})

        assert resp.status_code == 200, resp.text
        assert find_eligible(db, digits) is None


def test_ac21_user_with_no_linked_contact_gets_no_code(rate_limit_cleanup):
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        digits = _digits()
        _user(
            db,
            phone=digits,
            contact_id=None,
            email=f"{unique_code('nocontact')}@x.com".lower(),
        )
        rate_limit_cleanup.append(digits)

        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})

        assert resp.status_code == 200, resp.text
        assert find_eligible(db, digits) is None


def test_ac21_national_format_with_spaces_and_dashes_still_resolves(rate_limit_cleanup):
    """Security round S1: the route's own enqueue no longer distinguishes
    eligibility, so this proves normalisation the way it now matters - the
    NORMALISED number (the exact arg the route hands to `enqueue_job`, and
    what `dispatch_phone_signin_code` receives) is the stored digits, which
    `find_eligible` then resolves."""
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        typed = _national_variant(digits)
        rate_limit_cleanup.append(digits)

        with _phone_client(db) as client, patch(
            "app.services.queue_service.enqueue_job"
        ) as mock_enqueue:
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": typed})

        assert resp.status_code == 200, resp.text
        assert mock_enqueue.call_count == 1
        normalised = mock_enqueue.call_args.args[1]
        assert normalised == digits, "typed national format must normalise to the stored digits"
        assert find_eligible(db, normalised) is not None


@pytest.mark.parametrize("bad", ["not-a-phone-at-all", "12"])
def test_ac21_unparseable_number_is_422_regardless_of_existence(rate_limit_cleanup, bad):
    with blank_session() as db:
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": bad})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# AC-22: WhatsApp-only send, login_otp use case, integration_logs on both     #
# outcomes                                                                     #
# --------------------------------------------------------------------------- #
def test_ac22_login_otp_is_a_declared_use_case_requiring_otp_code():
    from app.models.respond_template import TEMPLATE_DEFAULT_USE_CASES
    from app.services.respond_template_service import REQUIRED_PARAM_VARIABLE

    assert "login_otp" in TEMPLATE_DEFAULT_USE_CASES
    assert REQUIRED_PARAM_VARIABLE.get("login_otp") == "otp_code"


def test_ac22_set_default_login_otp_accepts_when_otp_code_mapped():
    from app.models.respond_template import RespondChannel, RespondMessageTemplate
    from app.services import respond_template_service as svc

    with blank_session() as db:
        ws = _workspace(db)
        db.commit()
        ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=ws.id, respond_channel_id=1)
        db.add(ch)
        db.flush()
        tpl = RespondMessageTemplate(
            id=str(uuid.uuid4()),
            channel_id=ch.id,
            respond_template_id=1,
            name="ZZT signin",
            language_code="en",
            status="approved",
            components=[{"type": "body", "text": "Your Sorento sign-in code is {{1}}"}],
            body_text="Your Sorento sign-in code is {{1}}",
            param_count=1,
        )
        db.add(tpl)
        db.commit()

        out = svc.set_default(db, "login_otp", template_id=str(tpl.id), param_mapping={"1": "otp_code"})
        assert out["is_valid"] is True
        assert "otp_code" in out["param_mapping"].values()


def test_ac22_set_default_login_otp_without_otp_code_is_rejected():
    """Mirrors tests/test_respond_templates.py::test_set_default_portal_otp_requires_otp_code.

    NOTE: today this ALREADY raises (login_otp isn't in TEMPLATE_DEFAULT_USE_CASES
    yet, so set_default raises 'Unknown use_case' for any mapping) - i.e. it
    passes today for the WRONG reason. It is kept as the regression guard for
    once login_otp is registered: a mapping missing otp_code must still be
    refused, now for the RIGHT reason (REQUIRED_PARAM_VARIABLE)."""
    from app.models.respond_template import RespondChannel, RespondMessageTemplate
    from app.services import respond_template_service as svc
    from app.services.error_handler import AppException

    with blank_session() as db:
        ws = _workspace(db)
        db.commit()
        ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=ws.id, respond_channel_id=1)
        db.add(ch)
        db.flush()
        tpl = RespondMessageTemplate(
            id=str(uuid.uuid4()),
            channel_id=ch.id,
            respond_template_id=1,
            name="ZZT signin no code",
            language_code="en",
            status="approved",
            components=[{"type": "body", "text": "Hi {{1}}"}],
            body_text="Hi {{1}}",
            param_count=1,
        )
        db.add(tpl)
        db.commit()

        with pytest.raises(AppException):
            svc.set_default(db, "login_otp", template_id=str(tpl.id), param_mapping={"1": "message"})


def test_ac22_setting_ships_empty_so_sign_in_reuses_portal_otp():
    """Owner ruling (27 Sep 2026): CRM sign-in reuses the approved portal_otp
    template; the override ships empty, so no template lookup even runs."""
    from app.config import Settings
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    assert Settings.model_fields["phone_signin_otp_use_case"].default == ""
    with patch("app.config.settings.phone_signin_otp_use_case", ""), patch(
        "app.services.respond_template_service.get_default_row", return_value=MagicMock()
    ) as mock_lookup, patch("app.tasks.respond_io_tasks._send_and_log") as mock_send:
        send_login_otp_respond_message("ZZT-otp-0", "ZZT-respond-id-0", "text", "123456", "space-0")

    assert mock_lookup.call_count == 0
    _, kwargs = mock_send.call_args
    assert kwargs.get("use_case") == "portal_otp", kwargs


def test_ac22_task_uses_login_otp_override_when_set_and_a_default_row_exists():
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    with patch("app.config.settings.phone_signin_otp_use_case", "login_otp"), patch(
        "app.services.respond_template_service.get_default_row", return_value=MagicMock()
    ), patch("app.tasks.respond_io_tasks._send_and_log") as mock_send:
        send_login_otp_respond_message("ZZT-otp-1", "ZZT-respond-id-1", "text", "123456", "space-1")

    assert mock_send.call_count == 1
    _, kwargs = mock_send.call_args
    assert kwargs.get("use_case") == "login_otp", kwargs


def test_ac22_task_falls_back_to_portal_otp_when_no_default_row():
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    with patch("app.config.settings.phone_signin_otp_use_case", "login_otp"), patch(
        "app.services.respond_template_service.get_default_row", return_value=None
    ), patch("app.tasks.respond_io_tasks._send_and_log") as mock_send:
        send_login_otp_respond_message("ZZT-otp-2", "ZZT-respond-id-2", "text", "654321", "space-2")

    _, kwargs = mock_send.call_args
    assert kwargs.get("use_case") == "portal_otp", kwargs


def test_ac22_failed_send_writes_integration_log_row_failed():
    """Real DB, no network: _send_and_log opens its own SessionLocal(), so this
    runs against the shared local Postgres directly (mirrors
    tests/test_portal_otp_flow.py) and cleans up its own row."""
    from app.database import SessionLocal
    from app.models.integration import IntegrationLog
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    otp_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        with patch(
            "app.services.respond_messaging_service.send_text_or_template",
            side_effect=RuntimeError("no network - test double"),
        ):
            with pytest.raises(RuntimeError):
                send_login_otp_respond_message(otp_id, "ZZT-ac22-respond-id", "text", "112233", None)

        row = (
            db.query(IntegrationLog)
            .filter(
                IntegrationLog.business_table == "portal_otp_codes",
                IntegrationLog.business_id == otp_id,
            )
            .order_by(IntegrationLog.created_at.desc())
            .first()
        )
        assert row is not None, "expected an integration_log row for the failed send"
        assert row.status == "failed"
    finally:
        db.query(IntegrationLog).filter(IntegrationLog.business_id == otp_id).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()


# --------------------------------------------------------------------------- #
# AC-23: limits                                                               #
# --------------------------------------------------------------------------- #
def test_ac23_per_ip_limit_on_request_code_is_429(rate_limit_cleanup):
    from app.services.queue_service import redis_conn

    key = "rate_limit:v1:phone_signin_otp:testclient"
    redis_conn.set(key, settings.rate_limit_portal_otp_max)
    redis_conn.expire(key, settings.rate_limit_portal_otp_window_seconds)

    with blank_session() as db:
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": _digits()})

    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body.get("code") == "RATE_LIMITED", body
    assert "retry_after_seconds" in body
    assert "Retry-After" in resp.headers


def test_ac23_second_request_within_60s_is_429_for_known_and_unknown(rate_limit_cleanup):
    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        unknown = _digits()
        rate_limit_cleanup.extend([digits, unknown])
        with _phone_client(db) as client, patch("app.services.queue_service.enqueue_job"):
            for number in (digits, unknown):
                first = client.post("/api/v1/auth/phone/request-code", json={"phone": number})
                second = client.post("/api/v1/auth/phone/request-code", json={"phone": number})
                assert first.status_code == 200, first.text
                assert second.status_code == 429, second.text
                body = second.json()
                assert body.get("code") == "RATE_LIMITED", body
                assert "retry_after_seconds" in body
                assert "Retry-After" in second.headers


def test_ac23_eleventh_request_in_24h_is_429(rate_limit_cleanup):
    """ASSUMPTION (contract ambiguity, flagged to the captain): the per-number
    24h cap's Redis bucket name is not pinned anywhere in the contract (only
    the two IP buckets are named). Guessed as 'phone_signin_otp_daily', keyed
    by the normalised digits, mirroring the IP bucket's
    rate_limit:v1:<bucket>:<ident> shape. If the real bucket name differs this
    test needs its key renamed - that is not a fixture bug, it is this exact
    naming guess."""
    from app.services.queue_service import redis_conn

    digits = _digits()
    rate_limit_cleanup.append(digits)
    key = f"rate_limit:v1:phone_signin_otp_daily:{digits}"
    redis_conn.set(key, 10)
    redis_conn.expire(key, 86400)

    with blank_session() as db:
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})

    assert resp.status_code == 429, resp.text
    assert resp.json().get("code") == "RATE_LIMITED"


def test_ac23_verify_burst_for_different_numbers_from_one_client_is_not_globally_blocked(
    rate_limit_cleanup,
):
    """Security round S4 (renamed and rewritten; the old
    `test_ac23_verify_per_ip_limit_is_429` pinned a per-IP 429 on
    `/phone/verify` that has been REMOVED, not just re-tuned): NextAuth calls
    this route server-to-server, so `request.client.host` is the Next.js
    server's own address for EVERY signed-in user - a per-IP bucket here
    would let one attacker's burst 429 every real sign-in at once. The
    per-number atomic reservation (B2) is the real bound, so a burst of
    verifies for DIFFERENT numbers from the SAME client must NOT trip a
    shared limit; each just answers its own 401 for its own wrong code."""
    with blank_session() as db:
        with _phone_client(db) as client:
            statuses = []
            for _ in range(35):
                number = _digits()
                rate_limit_cleanup.append(number)
                resp = client.post(
                    "/api/v1/auth/phone/verify", json={"phone": number, "code": "000000"}
                )
                statuses.append(resp.status_code)

    assert all(s == 401 for s in statuses), statuses


def test_ac23_five_wrong_verify_attempts_count_down_then_lock_identically(rate_limit_cleanup):
    """AC-23: five wrong attempts per code. Fix lane round 2 (reviewer B1 at
    56daafae) rewrote this test: it used to pin the off-by-one (a 429 on the
    5th call, before its code was ever compared). Now 4 wrong answers read 4,
    3, 2 and 1 tries left, the 5th wrong answer is the 429, and a 6th call is
    refused without a compare, for a known and an unknown number alike."""
    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        unknown = _digits()
        rate_limit_cleanup.extend([digits, unknown])
        with _phone_client(db) as client:
            for number in (digits, unknown):
                with patch("app.services.queue_service.enqueue_job"):
                    req = client.post(
                        "/api/v1/auth/phone/request-code", json={"phone": number}
                    )
                assert req.status_code == 200, req.text

                results = []
                for _ in range(5):
                    resp = client.post(
                        "/api/v1/auth/phone/verify", json={"phone": number, "code": "000000"}
                    )
                    results.append((resp.status_code, resp.json()))

                for i in range(4):
                    status_code, body = results[i]
                    assert status_code == 401, (number, i, body)
                    assert body.get("code") == "CODE_WRONG", (number, i, body)
                    assert body.get("attempts_left") == 4 - i, (number, i, body)

                status_code, body = results[4]
                assert status_code == 429, (number, body)
                assert body.get("code") == "RATE_LIMITED", (number, body)
                assert body.get("retry_after_seconds", 0) > 0
                assert "15 minutes" in body.get("message", ""), body

                with patch("hmac.compare_digest") as mock_compare:
                    sixth = client.post(
                        "/api/v1/auth/phone/verify", json={"phone": number, "code": "123456"}
                    )
                assert sixth.status_code == 429, (number, sixth.text)
                mock_compare.assert_not_called()


def test_ac23_never_requested_code_is_401_code_expired(rate_limit_cleanup):
    """Equivalent, key-name-agnostic boundary of 'a code older than 10
    minutes': a number nobody ever requested a code for has no live marker
    either, which is exactly the state an expired one decays to."""
    digits = _digits()
    rate_limit_cleanup.append(digits)
    with blank_session() as db:
        with _phone_client(db) as client:
            resp = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": "123456"}
            )

    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body.get("code") == "CODE_EXPIRED", body
    assert "expired" in str(body.get("message", "")).lower()


def test_ac23_new_request_code_does_not_lift_the_verify_lock(rate_limit_cleanup):
    """Fix lane round 2 (reviewer Should fix 3 at 56daafae): the second
    request-code used to land inside the 60 s per-number cooldown, answer an
    unasserted 429 and never reach `mark_code_requested`, so this test could
    not tell a lock-lifting request-code apart. The cooldown bucket is now
    cleared first and the second request-code must answer 200."""
    from app.services.queue_service import redis_conn

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            code = _seed_signin_code(db, contact)
            wrong = "000000" if code != "000000" else "111111"

            for _ in range(5):
                client.post("/api/v1/auth/phone/verify", json={"phone": digits, "code": wrong})

            locked = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
            )
            assert locked.status_code == 429, locked.text

            for key in redis_conn.keys(f"*phone_signin_otp_cooldown*{digits}*"):
                redis_conn.delete(key)
            with patch("app.services.queue_service.enqueue_job"):
                again = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            assert again.status_code == 200, again.text

            still_locked = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
            )
            assert still_locked.status_code == 429, still_locked.text


# --------------------------------------------------------------------------- #
# AC-24: verify creates the unified session                                   #
# --------------------------------------------------------------------------- #
def test_ac24_verify_success_returns_login_shape_and_mints_session(rate_limit_cleanup):
    from app.models.user_session import UserSession

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                req = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            assert req.status_code == 200, req.text
            code = _seed_signin_code(db, contact)

            before = datetime.now(timezone.utc).replace(tzinfo=None)
            resp = client.post("/api/v1/auth/phone/verify", json={"phone": digits, "code": code})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            for key in ("token", "id", "status", "role_id", "role_ids", "home_path"):
                assert key in body, body
            assert body["id"] == user.id

            session_row = (
                db.query(UserSession)
                .filter(UserSession.user_id == user.id)
                .order_by(UserSession.created_at.desc())
                .first()
            )
            assert session_row is not None
            assert session_row.auth_method == "phone_otp"
            assert session_row.rolling is True
            assert session_row.expires_at > before + timedelta(days=29)

            db.refresh(user)
            assert user.phone_verified_at is not None
            assert user.last_sign_in_at is not None

            # Kill-test finding (captain, S1 security round): a successful
            # verify must actually CONSUME the row, not just answer 200 -
            # asserted directly here rather than only inferred from the
            # "reused" check below, which (see the next test) can pass for
            # the wrong reason once the Redis marker is gone.
            otp = (
                db.query(PortalOtpCode)
                .filter(PortalOtpCode.contact_id == contact.id)
                .order_by(PortalOtpCode.created_at.desc())
                .first()
            )
            assert otp is not None
            assert otp.consumed_at is not None

            reused = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
            )
            assert reused.status_code == 401, reused.text

            sessions_resp = client.get(
                "/api/v1/auth/sessions",
                headers={"Authorization": f"Bearer {body['token']}"},
            )
            assert sessions_resp.status_code == 200, sessions_resp.text


def test_regression_phone_otp_session_is_30d_and_slides_after_a_day(rate_limit_cleanup):
    """(3) SIGNIN-ALWAYS-SLIDE regression: phone OTP sign-in still mints the 30-day
    sliding session, and a request more than a day later moves expires_at forward."""
    from app.models.user_session import UserSession
    from app.services.user_session_service import _utcnow

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                req = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            assert req.status_code == 200, req.text
            code = _seed_signin_code(db, contact)

            resp = client.post("/api/v1/auth/phone/verify", json={"phone": digits, "code": code})
            assert resp.status_code == 200, resp.text
            token = resp.json()["token"]

            row = db.query(UserSession).filter(UserSession.token == token).one()
            assert row.auth_method == "phone_otp"
            assert row.rolling is True
            remaining = row.expires_at - _utcnow()
            assert timedelta(days=29, hours=23) < remaining <= timedelta(days=30, minutes=1)

            row.expires_at = row.expires_at - timedelta(days=1, hours=1)
            db.commit()
            pulled_back = row.expires_at

            auth = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/v1/auth/sessions", headers=auth).status_code == 200

            db.refresh(row)
            assert row.expires_at > pulled_back, "expires_at did not move forward on activity"
            assert row.expires_at - _utcnow() > timedelta(days=29, hours=23)


def test_ac24_consumed_code_is_refused_even_with_a_fresh_request_marker(rate_limit_cleanup):
    """Kill-test finding (captain, S1 security round): mutating out the
    consumption logic in `attempt_verify`/`PortalService.reserve_attempt`
    left `test_ac24_verify_success_returns_login_shape_and_mints_session`'s
    own "reused" check green, because that check's second verify call hits
    CODE_EXPIRED (the Redis request marker was cleared by the FIRST success)
    before the code is ever compared - it never actually exercises the
    `consumed_at` guard. This test restores the "a code was requested"
    marker directly (no new code, exactly what a real fresh request-code
    would leave behind) so the SAME already-consumed code has to be refused
    by the DB check, not by an absent marker.
    """
    from app.services import phone_signin_service as svc

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            code = _seed_signin_code(db, contact)

            first = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
            )
            assert first.status_code == 200, first.text

            otp = (
                db.query(PortalOtpCode)
                .filter(PortalOtpCode.contact_id == contact.id)
                .order_by(PortalOtpCode.created_at.desc())
                .first()
            )
            assert otp is not None and otp.consumed_at is not None

            svc.mark_code_requested(digits)

            reused = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
            )
            assert reused.status_code != 200, reused.text
            assert reused.json().get("code") != "CODE_EXPIRED", reused.json()


# --------------------------------------------------------------------------- #
# AC-26: email login no longer enumerates                                     #
# --------------------------------------------------------------------------- #
# (Additional AC-26 assertions live in tests/test_auth_login.py, which also
# gets its existing 404-for-unknown-email assertion corrected - see that
# file's diff and the tester's report.)


# --------------------------------------------------------------------------- #
# AC-27: phone sign-in works for staff and admins too                         #
# --------------------------------------------------------------------------- #
def test_ac27_staff_user_signs_in_by_phone_code_with_normal_permissions(rate_limit_cleanup):
    from app.services.user_service import UserPermissionService

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)

        perm = UserPermission(id=str(uuid.uuid4()), slug="test.ac27.view", name="AC27 View")
        role = UserRole(
            id=str(uuid.uuid4()),
            slug=f"staff-{uuid.uuid4().hex[:6]}",
            name="ZZT Staff Role",
            is_protected=False,
            is_default=False,
        )
        db.add_all([perm, role])
        db.flush()
        db.add(UserRolePermission(role_id=role.id, permission_id=perm.id))
        db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
        db.commit()

        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                req = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            assert req.status_code == 200, req.text
            code = _seed_signin_code(db, contact)
            resp = client.post("/api/v1/auth/phone/verify", json={"phone": digits, "code": code})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert role.id in body.get("role_ids", []), body
        assert UserPermissionService(db).check_user_has_permission(user.id, "test.ac27.view") is True


def test_ac27_superadmin_signs_in_by_phone_code(rate_limit_cleanup):
    from app.services.user_service import UserPermissionService

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)

        role = UserRole(
            id=str(uuid.uuid4()),
            slug="superadmin",
            name="ZZT Superadmin",
            is_protected=False,
            is_default=False,
        )
        db.add(role)
        db.flush()
        db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
        db.commit()

        with _phone_client(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                req = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            assert req.status_code == 200, req.text
            code = _seed_signin_code(db, contact)
            resp = client.post("/api/v1/auth/phone/verify", json={"phone": digits, "code": code})

        assert resp.status_code == 200, resp.text
        assert role.id in resp.json().get("role_ids", [])
        assert UserPermissionService(db).check_user_has_permission(user.id, "any.perm.at.all") is True


# --------------------------------------------------------------------------- #
# AC-28 (backend half): home_path on LoginResponse                            #
# --------------------------------------------------------------------------- #
def _login_client_with_password_user(db, *, roles_and_perms, contact_id=None, phone=None):
    pw = "correct-horse-battery-staple"
    user = _user(
        db,
        phone=phone,
        contact_id=contact_id,
        email=f"{unique_code('home')}@x.com".lower(),
    )
    user.password = _hash(pw)
    for role_slug, perm_slug in roles_and_perms:
        role = UserRole(
            id=str(uuid.uuid4()), slug=role_slug, name=role_slug, is_protected=False, is_default=False
        )
        db.add(role)
        db.flush()
        if perm_slug:
            perm = UserPermission(id=str(uuid.uuid4()), slug=perm_slug, name=perm_slug)
            db.add(perm)
            db.flush()
            db.add(UserRolePermission(role_id=role.id, permission_id=perm.id))
        db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.commit()
    return user, pw


def test_ac28_salesperson_role_wins_home_path_even_with_crm_permission(rate_limit_cleanup):
    from app.services.portal_service import PortalService

    with blank_session() as db:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)
        slug = PortalService(db).get_or_create_slug(contact)
        db.commit()
        rate_limit_cleanup.append(digits)

        user, pw = _login_client_with_password_user(
            db,
            roles_and_perms=[
                ("salesperson", None),
                (f"crm-{uuid.uuid4().hex[:6]}", "test.ac28.crm"),
            ],
            contact_id=contact.id,
            phone=digits,
        )

        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": pw})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("home_path") == f"/portal/c/{slug}", resp.json()


def test_ac28_staff_with_crm_permission_lands_on_crm_home():
    with blank_session() as db:
        user, pw = _login_client_with_password_user(
            db, roles_and_perms=[(f"crm-{uuid.uuid4().hex[:6]}", "test.ac28.crm2")]
        )
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": pw})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("home_path") == "/", resp.json()


def test_ac28_user_with_no_permission_and_linked_contact_lands_on_portal_home():
    from app.services.portal_service import PortalService

    with blank_session() as db:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)
        slug = PortalService(db).get_or_create_slug(contact)
        db.commit()

        user, pw = _login_client_with_password_user(
            db, roles_and_perms=[], contact_id=contact.id, phone=digits
        )
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": pw})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("home_path") == f"/portal/c/{slug}", resp.json()


def test_ac28_user_with_no_permission_and_no_contact_lands_on_crm_root():
    with blank_session() as db:
        user, pw = _login_client_with_password_user(db, roles_and_perms=[])
        with _phone_client(db) as client:
            resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": pw})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("home_path") == "/", resp.json()
