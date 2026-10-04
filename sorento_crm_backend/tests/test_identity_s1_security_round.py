"""Identity S1 security-review fix round (#1280): B1, B2, B3, S2, S5, and the
note fix. (S4's own regression test lives in
tests/test_identity_s1_phone_signin.py, replacing the existing S1 test it
made wrong - see that file's module docstring.) Written against the
captain's fix-round brief; verified red-for-cause against the pre-fix
baseline (commit 21f263349) by reverting the fix files to that commit,
confirming 12 of 14 failed for the stated reasons (the other 2 are documented
regression guards, not red-before-fix cases), then restoring the fixes.

Postgres only (`tests/_pg_fixture.py`), one rolled-back transaction per test,
except the B1 tests which exercise the real `app.database.SessionLocal`
(mirrors `tests/test_identity_s1_phone_signin.py`'s AC-22 tests) because
`_send_and_log` opens its own session - those clean up every row they write.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.access import RespondContact
from app.models.integration import IntegrationLog
from app.models.portal import PortalOtpCode
from app.models.respond_workspace import RespondWorkspace
from app.models.user import User, UserRole, UserRoleAssignment
from app.services.portal_service import PortalService, _hash_otp, _utcnow
from tests._pg_fixture import blank_session, unique_code


def _digits() -> str:
    return "601" + f"{uuid.uuid4().int % 10**8:08d}"


def _workspace(db) -> RespondWorkspace:
    ws = RespondWorkspace(
        id=str(uuid.uuid4()), space_id=f"sp_{uuid.uuid4().hex[:8]}",
        name="ZZT WS", api_key_ciphertext="test-cipher",
    )
    db.add(ws)
    db.flush()
    return ws


def _contact(db, ws, phone: str) -> RespondContact:
    c = RespondContact(id=str(uuid.uuid4()), phone_number=phone, name="ZZT Contact", workspace_id=ws.id)
    db.add(c)
    db.flush()
    return c


def _user(
    db, *, phone=None, contact_id=None, status="ACTIVE", is_trashed=False,
    is_integration=False, email=None,
) -> User:
    u = User(
        id=str(uuid.uuid4()), email=email, name="ZZT User", status=status,
        is_trashed=is_trashed, is_integration=is_integration,
        contact_number=phone, respond_contact_id=contact_id,
    )
    db.add(u)
    db.commit()
    return u


def _eligible_chain(db):
    ws = _workspace(db)
    digits = _digits()
    contact = _contact(db, ws, digits)
    user = _user(db, phone=digits, contact_id=contact.id, email=f"{unique_code('sec')}@x.com".lower())
    return ws, contact, user, digits


def _seed_signin_code(db, contact) -> str:
    """S1 moved OTP creation out of the request-code route and into the
    background job; tests that need a REAL code on the test's own
    (blank_session) db call `create_and_dispatch_otp` directly with a fake
    inline task, exactly mirroring what that job does, without touching the
    real Respond.io send or the real Redis queue."""
    from app.services.phone_signin_service import SIGNIN_OTP_TEXT

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


@pytest.fixture()
def rate_limit_cleanup():
    from app.services.queue_service import redis_conn

    idents: list[str] = []
    yield idents
    for ident in idents:
        for key in redis_conn.keys(f"*{ident}*"):
            redis_conn.delete(key)
    for bucket in ("phone_signin_otp", "phone_signin_verify"):
        redis_conn.delete(f"rate_limit:v1:{bucket}:testclient")


# --------------------------------------------------------------------------- #
# B1, overruled by the owner on 29 Sep 2026 ("show the code in the outbox"): #
# the operator reads the code from the outbox on a test copy, so the          #
# integration_log row carries the real code for sign-in and portal sends.     #
# --------------------------------------------------------------------------- #
def test_b1_signin_otp_success_send_shows_code_in_integration_log():
    """Owner ruling 29 Sep 2026: the success row's request and response
    payloads carry the real sign-in code, unmasked."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    code = "778899"
    otp_id = str(uuid.uuid4())
    db = SessionLocal()
    fake_result = {
        "sent_as": "text",
        "rendered_text": f"Your Sorento sign-in code is {code}.",
        "response": {"ok": True, "id": "resp-b1-success", "echo": f"code {code} sent"},
        "request_payload": {"message": {"type": "text", "text": f"Your Sorento sign-in code is {code}."}},
    }
    try:
        with patch(
            "app.services.respond_messaging_service.send_text_or_template",
            return_value=fake_result,
        ):
            send_login_otp_respond_message(otp_id, "ZZT-b1-success-id", "text", code, None)

        row = (
            db.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "portal_otp_codes", IntegrationLog.business_id == otp_id)
            .order_by(IntegrationLog.created_at.desc())
            .first()
        )
        assert row is not None
        assert row.status == "success"
        assert code in (row.request_payload or ""), row.request_payload
        assert "******" not in (row.request_payload or ""), row.request_payload
        assert code in (row.response_payload or ""), row.response_payload
    finally:
        db.query(IntegrationLog).filter(IntegrationLog.business_id == otp_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_b1_signin_otp_failed_send_shows_code_in_integration_log():
    """Owner ruling 29 Sep 2026: a failed send (local dev, no Respond.io)
    still leaves the code readable in the `failed` row, the operator's one
    way to read it back on a test copy."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    code = "334455"
    otp_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        with patch(
            "app.services.respond_messaging_service.send_text_or_template",
            side_effect=RuntimeError(f"no network - code was {code}"),
        ):
            with pytest.raises(RuntimeError):
                send_login_otp_respond_message(
                    otp_id, "ZZT-b1-failed-id",
                    f"Your Sorento sign-in code is {code}. It expires in 10 minutes.",
                    code, None,
                )

        row = (
            db.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "portal_otp_codes", IntegrationLog.business_id == otp_id)
            .order_by(IntegrationLog.created_at.desc())
            .first()
        )
        assert row is not None
        assert row.status == "failed"
        assert code in (row.request_payload or ""), row.request_payload
        assert "******" not in (row.request_payload or ""), row.request_payload
        assert code in (row.error_message or ""), row.error_message
    finally:
        db.query(IntegrationLog).filter(IntegrationLog.business_id == otp_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_b1_portal_otp_success_send_shows_code_in_integration_log():
    """The portal OTP send shares `_send_and_log` with sign-in, so the same
    ruling applies: the code is readable in the row."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import send_portal_otp_respond_message

    code = "112233"
    otp_id = str(uuid.uuid4())
    db = SessionLocal()
    fake_result = {
        "sent_as": "text",
        "rendered_text": f"Your Sorento portal verification code is {code}.",
        "response": {"ok": True, "id": "resp-b1-portal-success", "echo": f"code {code} sent"},
        "request_payload": {"message": {"type": "text", "text": f"Your Sorento portal verification code is {code}."}},
    }
    try:
        with patch(
            "app.services.respond_messaging_service.send_text_or_template",
            return_value=fake_result,
        ):
            send_portal_otp_respond_message(otp_id, "ZZT-b1-portal-success-id", "text", code, None)

        row = (
            db.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "portal_otp_codes", IntegrationLog.business_id == otp_id)
            .order_by(IntegrationLog.created_at.desc())
            .first()
        )
        assert row is not None
        assert code in (row.request_payload or ""), row.request_payload
        assert "******" not in (row.request_payload or ""), row.request_payload
        assert code in (row.response_payload or ""), row.response_payload
    finally:
        db.query(IntegrationLog).filter(IntegrationLog.business_id == otp_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_b1_portal_otp_failed_send_shows_code_in_integration_log():
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import send_portal_otp_respond_message

    code = "556677"
    otp_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        with patch(
            "app.services.respond_messaging_service.send_text_or_template",
            side_effect=RuntimeError(f"no network - code was {code}"),
        ):
            with pytest.raises(RuntimeError):
                send_portal_otp_respond_message(
                    otp_id, "ZZT-b1-portal-failed-id",
                    f"Your Sorento portal verification code is {code}.",
                    code, None,
                )

        row = (
            db.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "portal_otp_codes", IntegrationLog.business_id == otp_id)
            .order_by(IntegrationLog.created_at.desc())
            .first()
        )
        assert row is not None
        assert row.status == "failed"
        assert code in (row.request_payload or ""), row.request_payload
        assert "******" not in (row.request_payload or ""), row.request_payload
        assert code in (row.error_message or ""), row.error_message
    finally:
        db.query(IntegrationLog).filter(IntegrationLog.business_id == otp_id).delete(synchronize_session=False)
        db.commit()
        db.close()


# --------------------------------------------------------------------------- #
# B2: verify race - reserve before compare                                    #
# --------------------------------------------------------------------------- #
def test_b2_reserve_attempt_refuses_once_cap_reached(rate_limit_cleanup):
    """RED reason (pre-fix): `reserve_attempt` did not exist; the cap was
    enforced by a Python-side read-then-write (`otp.attempts += 1`) with no
    atomic guard, so two parallel reservations could both read the same
    stale value and both "succeed"."""
    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        otp = PortalOtpCode(
            contact_id=contact.id, space_id=ws.space_id, code_hash=_hash_otp("123456"),
            expires_at=_utcnow() + timedelta(minutes=10), attempts=5,
        )
        db.add(otp)
        db.commit()

        svc = PortalService(db)
        assert svc.reserve_attempt(otp.id) is None


def test_b2_second_consume_of_the_same_otp_row_returns_false(rate_limit_cleanup):
    """RED reason (pre-fix): `consume_reserved` did not exist; `verify_otp`
    set `otp.consumed_at = _utcnow()` in Python with no WHERE guard, so a
    second parallel verify that already passed its own (stale) compare would
    silently overwrite the same value and mint a SECOND session/token for one
    one-time code."""
    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        otp = PortalOtpCode(
            contact_id=contact.id, space_id=ws.space_id, code_hash=_hash_otp("123456"),
            expires_at=_utcnow() + timedelta(minutes=10),
        )
        db.add(otp)
        db.commit()

        svc = PortalService(db)
        assert svc.consume_reserved(otp.id, _utcnow()) is True
        assert svc.consume_reserved(otp.id, _utcnow()) is False


def test_b2_locked_number_is_429_without_ever_comparing_the_code(rate_limit_cleanup):
    """Regression guard, not a red-before-fix case on its own: a number ALREADY
    at the lock threshold (no race needed to get there) was refused without
    comparing even pre-fix, because the old code's read-only `check_locked`
    pre-check already caught this simple, non-concurrent case. Kept here
    because B2's real fix (`reserve_verify_attempt` INCR-then-check) must
    keep this exact behaviour - the two DB-level tests above are what were
    actually red pre-fix for the RACE itself."""
    from app.services.queue_service import redis_conn

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        redis_conn.set(f"phone_signin:tries:{digits}", 5, ex=900)

        with _client_ctx(db) as client, patch("hmac.compare_digest") as mock_compare:
            resp = client.post(
                "/api/v1/auth/phone/verify", json={"phone": digits, "code": "123456"}
            )

        assert resp.status_code == 429, resp.text
        assert resp.json().get("code") == "RATE_LIMITED"
        mock_compare.assert_not_called()


def test_b2_redis_counter_is_incremented_before_the_code_is_compared(rate_limit_cleanup):
    """RED reason (pre-fix): the increment (`record_wrong_attempt`) ran AFTER
    a failed compare, not before it - so by the time this test's `compare_digest`
    stand-in runs, the real code had not incremented the counter yet."""
    from app.services.queue_service import redis_conn

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)

        seen = {}

        def _fake_compare(a, b):
            seen["tries_at_compare_time"] = redis_conn.get(f"phone_signin:tries:{digits}")
            return False

        with _client_ctx(db) as client:
            with patch("app.services.queue_service.enqueue_job"):
                client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
            # S1 moved OTP creation into the (here, mocked-out) background
            # job - seed a real row the same way that job would, via the
            # same create_and_dispatch_otp the route no longer calls inline.
            _seed_signin_code(db, contact)
            with patch("hmac.compare_digest", side_effect=_fake_compare):
                resp = client.post(
                    "/api/v1/auth/phone/verify", json={"phone": digits, "code": "000000"}
                )

        assert resp.status_code == 401, resp.text
        assert seen.get("tries_at_compare_time") is not None, (
            "the wrong-attempt counter must already be incremented by the "
            "time the code is compared, not after"
        )
        assert int(seen["tries_at_compare_time"]) == 1


# --------------------------------------------------------------------------- #
# B3: POST /auth/password must refuse under impersonation                     #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def impersonation_setup():
    """A real admin session impersonating a real target, via the actual
    X-Impersonate-User-Id header path (not a dependency override) - so
    `request.state.impersonation_session_id` is set exactly the way
    `dependencies._maybe_apply_impersonation` sets it in production."""
    from app.models.impersonation import ImpersonationSession
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        admin_role = UserRole(
            id=str(uuid.uuid4()), slug="admin", name="ZZT Admin Role",
            is_protected=False, is_default=False,
        )
        db.add(admin_role)
        db.flush()
        admin = _user(db, email=f"{unique_code('admin')}@x.com".lower())
        target = _user(db, email=f"{unique_code('target')}@x.com".lower())
        target.password = "existing-hash-not-checked-here"
        db.add(UserRoleAssignment(user_id=admin.id, role_id=admin_role.id))
        db.commit()

        imp = ImpersonationSession(
            id=str(uuid.uuid4()), admin_user_id=admin.id, target_user_id=target.id,
        )
        db.add(imp)
        db.commit()

        admin_session = mint_session(db, admin.id, auth_method="password")
        target_session = mint_session(db, target.id, auth_method="password")

        yield db, admin, target, admin_session, target_session


def test_b3_password_change_is_refused_while_impersonating(impersonation_setup):
    """RED reason (pre-fix): the route had no impersonation check at all - an
    admin impersonating `target` could set `target`'s password with no
    current-password check (current_user IS the target under impersonation),
    an account takeover, not a support action."""
    db, admin, target, admin_session, _target_session = impersonation_setup

    with _client_ctx(db) as client:
        resp = client.post(
            "/api/v1/auth/password",
            json={"new_password": "brandnewpassword1"},
            headers={
                "Authorization": f"Bearer {admin_session.token}",
                "X-Impersonate-User-Id": target.id,
            },
        )

    assert resp.status_code == 403, resp.text
    db.refresh(target)
    assert target.password == "existing-hash-not-checked-here"


def test_b3_normal_password_change_revokes_the_changed_users_own_sessions(impersonation_setup):
    """Regression guard, not red pre-fix: outside impersonation
    `get_actor_user_id` already returned the same id as `current_user["id"]`,
    so the ordinary path behaved identically before and after tightening the
    revoke call to read `current_user["id"]` directly. Kept so that
    simplification can't silently regress this path while fixing B3's actual
    (impersonation-only) bug in the test above."""
    from app.models.user_session import UserSession
    from app.services.user_session_service import mint_session

    db, admin, target, _admin_session, target_session = impersonation_setup
    target.password = _hash_password("original-password-123")
    db.commit()
    other_target_session = mint_session(db, target.id, auth_method="password")

    with _client_ctx(db) as client:
        resp = client.post(
            "/api/v1/auth/password",
            json={"current_password": "original-password-123", "new_password": "new-password-123"},
            headers={"Authorization": f"Bearer {target_session.token}"},
        )

    assert resp.status_code == 200, resp.text
    current_row = db.query(UserSession).filter(UserSession.id == target_session.id).one()
    other_row = db.query(UserSession).filter(UserSession.id == other_target_session.id).one()
    assert current_row.revoked_at is None
    assert other_row.revoked_at is not None


def _hash_password(pw: str) -> str:
    import bcrypt

    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# --------------------------------------------------------------------------- #
# S2: email login's "no password hash at all" branch must not skip the       #
# timing-equalising dummy bcrypt check                                        #
# --------------------------------------------------------------------------- #
def test_s2_no_password_user_login_still_runs_the_dummy_bcrypt_check():
    """RED reason (pre-fix): the `pw_raw is None or pw_raw == ""` branch
    raised 401 immediately with no `bcrypt.checkpw` call at all, so this
    branch answered measurably faster than a wrong-password branch does -
    itself an enumeration tell (OAuth-only vs password-holding accounts)."""
    import bcrypt

    with blank_session() as db:
        user = _user(db, email=f"{unique_code('nopw')}@x.com".lower())
        user.password = ""
        db.commit()

        with _client_ctx(db) as client, patch(
            "bcrypt.checkpw", wraps=bcrypt.checkpw
        ) as mock_checkpw:
            resp = client.post(
                "/api/v1/auth/login", json={"email": user.email, "password": "anything-at-all"}
            )

        assert resp.status_code == 401, resp.text
        mock_checkpw.assert_called_once()


# --------------------------------------------------------------------------- #
# S4: /auth/phone/verify must not share one global per-IP bucket              #
#                                                                              #
# Test lives in tests/test_identity_s1_phone_signin.py, in place of the old  #
# test_ac23_verify_per_ip_limit_is_429 it replaces (renamed                  #
# test_ac23_verify_burst_for_different_numbers_from_one_client_is_not_       #
# globally_blocked) - that is an EXISTING S1 file pinning the exact per-IP    #
# behaviour this removes, so the fix belongs there, not in a new file.       #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# S5: find_eligible must treat >1 match as ineligible, not pick one           #
# --------------------------------------------------------------------------- #
def test_s5_two_users_with_plain_and_plus_prefixed_same_number_are_ineligible(rate_limit_cleanup):
    """RED reason (pre-fix): `find_eligible` used `.first()` over
    `contact_number IN (num, '+'+num)` - with two DIFFERENT user rows
    matching (one stored as digits, one as `+digits`), `.first()` picks
    whichever the query planner returns first instead of refusing an
    ambiguous match. Two distinct contacts (`respond_contact_id` is itself
    unique per user) so the only thing under test is the `contact_number`
    match count."""
    from app.services.phone_signin_service import find_eligible

    with blank_session() as db:
        ws = _workspace(db)
        digits = _digits()
        contact_a = _contact(db, ws, digits)
        contact_b = _contact(db, ws, _digits())
        rate_limit_cleanup.append(digits)

        _user(db, phone=digits, contact_id=contact_a.id, email=f"{unique_code('dup1')}@x.com".lower())
        _user(db, phone=f"+{digits}", contact_id=contact_b.id, email=f"{unique_code('dup2')}@x.com".lower())

        assert find_eligible(db, digits) is None


# --------------------------------------------------------------------------- #
# Note fix: SetPasswordRequest.new_password must cap at 72 bytes              #
# --------------------------------------------------------------------------- #
def test_note_password_over_72_bytes_is_422_not_500(rate_limit_cleanup):
    """RED reason (pre-fix): bcrypt raises `ValueError` past 72 bytes and
    nothing caught it, so this 500'd instead of answering a clean 422."""
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        user = _user(db, email=f"{unique_code('longpw')}@x.com".lower())
        db.commit()
        session_row = mint_session(db, user.id, auth_method="password")

        with _client_ctx(db) as client:
            resp = client.post(
                "/api/v1/auth/password",
                json={"new_password": "x" * 73},
                headers={"Authorization": f"Bearer {session_row.token}"},
            )

    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Kill-test round 2 (captain, d8bf43f9): survivors 8, 10, 15.                  #
#                                                                              #
# Each of these passes on CURRENT code with no implementation change; kill    #
# evidence (the mutation described in the docstring, applied to a /tmp        #
# cp-backup of the file and restored from that backup - never `git           #
# checkout`) is reported alongside the test run, not encoded here.            #
# --------------------------------------------------------------------------- #
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
def test_survivor10_dispatch_creates_no_code_for_an_ineligible_users_number(bad_state):
    """Kill target: `dispatch_phone_signin_code` resolving the number with a
    plain `RespondContact` lookup by phone instead of `find_eligible` would
    send a code regardless of the linked user's own eligibility. Real DB
    (mirrors the AC-22/S1 task-level tests): `dispatch_phone_signin_code`
    opens its own `SessionLocal`, invisible to a `blank_session` fixture."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import dispatch_phone_signin_code

    db = SessionLocal()
    ws = contact = user = None
    try:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)
        user = _user(
            db, phone=digits, contact_id=contact.id,
            email=f"{unique_code('surv10')}@x.com".lower(), **bad_state,
        )

        with patch("app.tasks.respond_io_tasks.send_login_otp_respond_message") as mock_send:
            dispatch_phone_signin_code(digits)

        assert mock_send.call_count == 0
        assert db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact.id).count() == 0
    finally:
        if contact is not None:
            db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact.id).delete(
                synchronize_session=False
            )
        if user is not None:
            db.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        if contact is not None:
            db.query(RespondContact).filter(RespondContact.id == contact.id).delete(
                synchronize_session=False
            )
        if ws is not None:
            db.query(RespondWorkspace).filter(RespondWorkspace.id == ws.id).delete(
                synchronize_session=False
            )
        db.commit()
        db.close()


def test_survivor10_dispatch_creates_no_code_for_a_contact_with_no_linked_user():
    """Same kill target as above, for the "no `find_eligible` at all" shape
    of the mutation: a contact that no user links to at all must get no code
    from a plain phone lookup either."""
    from app.database import SessionLocal
    from app.tasks.respond_io_tasks import dispatch_phone_signin_code

    db = SessionLocal()
    ws = contact = None
    try:
        ws = _workspace(db)
        digits = _digits()
        contact = _contact(db, ws, digits)  # no user links to this contact at all
        # dispatch_phone_signin_code opens its OWN SessionLocal (a different
        # connection) - a flush alone is invisible there; must commit.
        db.commit()

        with patch("app.tasks.respond_io_tasks.send_login_otp_respond_message") as mock_send:
            dispatch_phone_signin_code(digits)

        assert mock_send.call_count == 0
        assert db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact.id).count() == 0
    finally:
        if contact is not None:
            db.query(PortalOtpCode).filter(PortalOtpCode.contact_id == contact.id).delete(
                synchronize_session=False
            )
            db.query(RespondContact).filter(RespondContact.id == contact.id).delete(
                synchronize_session=False
            )
        if ws is not None:
            db.query(RespondWorkspace).filter(RespondWorkspace.id == ws.id).delete(
                synchronize_session=False
            )
        db.commit()
        db.close()


def test_survivor15_attempt_verify_refuses_when_consume_reserved_loses_the_race(rate_limit_cleanup):
    """Kill target: `attempt_verify` ignoring `consume_reserved`'s return
    value would mint a session even when a parallel verify already consumed
    the row first. Forces that race deterministically by monkeypatching
    `PortalService.consume_reserved` to report a lost race (`False`) right
    after a genuinely correct code has been reserved and compared."""
    from app.models.user_session import UserSession
    from app.services.phone_signin_service import attempt_verify

    with blank_session() as db:
        ws, contact, user, digits = _eligible_chain(db)
        rate_limit_cleanup.append(digits)
        code = _seed_signin_code(db, contact)

        sessions_before = db.query(UserSession).filter(UserSession.user_id == user.id).count()

        with patch.object(PortalService, "consume_reserved", return_value=False):
            result = attempt_verify(db, digits, code, user_agent=None, ip_address=None)

        assert result is None
        sessions_after = db.query(UserSession).filter(UserSession.user_id == user.id).count()
        assert sessions_after == sessions_before


def test_survivor8_password_change_revokes_current_users_sessions_not_get_actor_user_ids(
    rate_limit_cleanup,
):
    """Kill target: revoking `get_actor_user_id(request, current_user)`'s
    sessions instead of `current_user["id"]`'s would revoke the WRONG
    person's other devices whenever the two differ. Patches
    `get_actor_user_id` AS IMPORTED IN `app.api.v1.auth` (not its definition
    module) to a different user id, so a route that still consulted it would
    revoke that OTHER user's sessions instead of the acting user's own."""
    from app.models.user_session import UserSession
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        acting_user = _user(db, email=f"{unique_code('actor')}@x.com".lower())
        acting_user.password = _hash_password("original-password-123")
        other_user = _user(db, email=f"{unique_code('other')}@x.com".lower())
        db.commit()

        current_session = mint_session(db, acting_user.id, auth_method="password")
        acting_other_session = mint_session(db, acting_user.id, auth_method="password")
        other_user_session = mint_session(db, other_user.id, auth_method="password")

        with _client_ctx(db) as client, patch(
            "app.api.v1.auth.get_actor_user_id", return_value=other_user.id
        ):
            resp = client.post(
                "/api/v1/auth/password",
                json={
                    "current_password": "original-password-123",
                    "new_password": "new-password-123",
                },
                headers={"Authorization": f"Bearer {current_session.token}"},
            )

        assert resp.status_code == 200, resp.text
        current_row = db.query(UserSession).filter(UserSession.id == current_session.id).one()
        acting_other_row = db.query(UserSession).filter(UserSession.id == acting_other_session.id).one()
        other_user_row = db.query(UserSession).filter(UserSession.id == other_user_session.id).one()

        assert current_row.revoked_at is None
        assert acting_other_row.revoked_at is not None
        assert other_user_row.revoked_at is None
