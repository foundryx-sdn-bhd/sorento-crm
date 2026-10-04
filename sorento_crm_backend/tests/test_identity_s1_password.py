"""S1 recovery / password red tests (#1280): plan section 5.3 (password set,
GET account has_password, lost-phone), plus the BLOCKED/trashed phone
sign-in refusal named in the same section.

Written against `documentation/plans/identity/s1-contract.md`
("POST /api/v1/auth/password", "Lost phone") BEFORE any implementation
exists. Postgres only (`tests/_pg_fixture.py`), one rolled-back transaction
per test.

CONTRACT AMBIGUITY (same one noted in test_identity_s1_phone_signin.py): the
contract names `GET /api/v1/user-management/account/` for `has_password` /
`phone_verified_at`. No such backend route is mounted - grepped
`app/api/v1/__init__.py` and every `user_management/*.py` router. The
frontend's own account page proxy (`app/api/user-management/account/route.ts`)
forwards to `GET /api/v1/user-management/users/me` today, so
`test_s1_account_payload_carries_has_password` targets that route instead.

SECURITY ROUND (#1280): `test_s1_blocked_or_trashed_user_cannot_phone_sign_in_
even_with_valid_code` used to read the OTP code back from a mocked
`enqueue_job`'s call args - S1 moved code creation into the
`dispatch_phone_signin_code` job the route now enqueues, so that mock no
longer carries it; it seeds a real code via `_seed_signin_code` instead
(mirrors `test_identity_s1_phone_signin.py`'s own helper of the same name).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.access import RespondContact
from app.models.respond_workspace import RespondWorkspace
from app.models.user import User, UserRole, UserRoleAssignment
from app.services.user_session_service import mint_session
from tests._pg_fixture import blank_session, unique_code


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _digits() -> str:
    return "601" + f"{uuid.uuid4().int % 10**8:08d}"


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
    c = RespondContact(id=str(uuid.uuid4()), phone_number=phone, name="ZZT Contact", workspace_id=ws.id)
    db.add(c)
    db.flush()
    return c


def _user(db, *, phone=None, contact_id=None, status="ACTIVE", is_trashed=False, email=None) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=email,
        name="ZZT User",
        status=status,
        is_trashed=is_trashed,
        contact_number=phone,
        respond_contact_id=contact_id,
    )
    db.add(u)
    db.commit()
    return u


def _seed_signin_code(db, contact) -> str:
    """S1 moved OTP creation out of the request-code route and into the
    background job; seed a real one directly the same way that job does,
    without touching the real Respond.io send or the real Redis queue."""
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


@pytest.fixture()
def app_client():
    """TestClient over a blank schema; get_current_user is NOT overridden, so a
    Bearer token minted via mint_session() against this same `db` resolves for
    real through the app's own session lookup (request.state.session_id gets
    stamped, which the "keep current session" behaviour needs)."""
    with blank_session() as db:
        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def admin_client():
    """Admin-acting client for the /users/{id} PUT tests - mirrors
    tests/test_identity_s0_model.py's api_client (role slug 'admin' bypasses
    every permission check in UserPermissionService)."""
    from app.dependencies import get_current_user

    with blank_session() as db:
        role = UserRole(
            id=str(uuid.uuid4()), slug="admin", name=f"ZZT Admin Role {uuid.uuid4().hex[:6]}",
            is_protected=False, is_default=False,
        )
        admin = User(
            id=str(uuid.uuid4()), email=f"{unique_code('admin')}@x.com".lower(), name="ZZT Admin",
            status="ACTIVE",
        )
        db.add_all([role, admin])
        db.flush()
        db.add(UserRoleAssignment(user_id=admin.id, role_id=role.id))
        db.commit()

        def _override_user():
            return {"id": admin.id, "email": admin.email, "name": admin.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# POST /api/v1/auth/password                                                  #
# --------------------------------------------------------------------------- #
def test_s1_phone_only_user_sets_password_without_current_password(app_client):
    client, db = app_client
    digits = _digits()
    contact = _contact(db, _workspace(db), digits)
    user = _user(db, phone=digits, contact_id=contact.id, email=f"{unique_code('phoneonly')}@x.com".lower())
    session_row = mint_session(db, user.id, auth_method="phone_otp")

    resp = client.post(
        "/api/v1/auth/password",
        json={"new_password": "brandnewpassword1"},
        headers={"Authorization": f"Bearer {session_row.token}"},
    )
    assert resp.status_code == 200, resp.text

    login_resp = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "brandnewpassword1"}
    )
    assert login_resp.status_code == 200, login_resp.text


def test_s1_wrong_current_password_is_400(app_client):
    client, db = app_client
    pw = "original-password-123"
    user = _user(db, email=f"{unique_code('haspw')}@x.com".lower())
    user.password = _hash(pw)
    db.commit()
    session_row = mint_session(db, user.id, auth_method="password")

    resp = client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-the-password", "new_password": "another-password-1"},
        headers={"Authorization": f"Bearer {session_row.token}"},
    )
    assert resp.status_code == 400, resp.text
    assert "not right" in resp.text.lower()


def test_s1_password_change_revokes_every_other_session_keeps_current(app_client):
    from app.models.user_session import UserSession

    client, db = app_client
    pw = "original-password-123"
    user = _user(db, email=f"{unique_code('multi')}@x.com".lower())
    user.password = _hash(pw)
    db.commit()
    current = mint_session(db, user.id, auth_method="password")
    other = mint_session(db, user.id, auth_method="password")

    resp = client.post(
        "/api/v1/auth/password",
        json={"current_password": pw, "new_password": "new-password-123"},
        headers={"Authorization": f"Bearer {current.token}"},
    )
    assert resp.status_code == 200, resp.text

    current_row = db.query(UserSession).filter(UserSession.id == current.id).one()
    other_row = db.query(UserSession).filter(UserSession.id == other.id).one()
    assert current_row.revoked_at is None
    assert other_row.revoked_at is not None


def test_s1_new_password_under_8_chars_is_422(app_client):
    client, db = app_client
    user = _user(db, email=f"{unique_code('short')}@x.com".lower())
    user.password = _hash("original-password")
    db.commit()
    session_row = mint_session(db, user.id, auth_method="password")

    resp = client.post(
        "/api/v1/auth/password",
        json={"current_password": "original-password", "new_password": "short1"},
        headers={"Authorization": f"Bearer {session_row.token}"},
    )
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Account payload (see the module-level ambiguity note)                       #
# --------------------------------------------------------------------------- #
def test_s1_account_payload_carries_has_password_and_phone_verified_at(app_client):
    client, db = app_client
    user = _user(db, email=f"{unique_code('hp')}@x.com".lower())
    user.password = _hash("some-password-123")
    db.commit()
    session_row = mint_session(db, user.id, auth_method="password")

    resp = client.get(
        "/api/v1/user-management/users/me",
        headers={"Authorization": f"Bearer {session_row.token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "has_password" in body, (
        "response_model=UserResponse silently drops undeclared fields "
        "(LESSONS-LEARNT) - has_password must be declared on the schema"
    )
    assert body["has_password"] is True
    assert "phone_verified_at" in body


# --------------------------------------------------------------------------- #
# Lost phone: admin edits contact_number                                      #
# --------------------------------------------------------------------------- #
def test_s1_admin_changing_contact_number_clears_verification_and_revokes_sessions(admin_client):
    from app.models.user_session import UserSession

    client, db = admin_client
    ws = _workspace(db)
    digits = _digits()
    contact = _contact(db, ws, digits)
    user = _user(db, phone=digits, contact_id=contact.id, email=f"{unique_code('lost')}@x.com".lower())
    user.phone_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    session_row = mint_session(db, user.id, auth_method="phone_otp")

    new_digits = _digits()
    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"contact_number": new_digits})
    assert resp.status_code == 200, resp.text

    db.refresh(user)
    assert user.phone_verified_at is None

    session_after = db.query(UserSession).filter(UserSession.id == session_row.id).one()
    assert session_after.revoked_at is not None


def test_s1_admin_put_without_phone_change_leaves_verification_and_sessions(admin_client):
    """Kept as a regression guard: today's PUT touches neither
    phone_verified_at nor sessions regardless of the field changed, so this
    passes trivially before the lost-phone behaviour ships. It must keep
    passing once the phone-change branch above exists."""
    from app.models.user_session import UserSession

    client, db = admin_client
    ws = _workspace(db)
    digits = _digits()
    contact = _contact(db, ws, digits)
    user = _user(db, phone=digits, contact_id=contact.id, email=f"{unique_code('samephone')}@x.com".lower())
    user.phone_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    session_row = mint_session(db, user.id, auth_method="phone_otp")

    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"name": "Renamed ZZT"})
    assert resp.status_code == 200, resp.text

    db.refresh(user)
    assert user.phone_verified_at is not None

    session_after = db.query(UserSession).filter(UserSession.id == session_row.id).one()
    assert session_after.revoked_at is None


# --------------------------------------------------------------------------- #
# Blocked / trashed cannot sign in by code even with a valid code             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_state", [{"status": "BLOCKED"}, {"is_trashed": True}], ids=["blocked", "trashed"]
)
def test_s1_blocked_or_trashed_user_cannot_phone_sign_in_even_with_valid_code(
    rate_limit_cleanup, bad_state
):
    with blank_session() as db:
        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        try:
            ws = _workspace(db)
            digits = _digits()
            contact = _contact(db, ws, digits)
            user = _user(
                db, phone=digits, contact_id=contact.id,
                email=f"{unique_code('flip')}@x.com".lower(),
            )
            rate_limit_cleanup.append(digits)

            with TestClient(app) as client:
                with patch("app.services.queue_service.enqueue_job"):
                    req = client.post("/api/v1/auth/phone/request-code", json={"phone": digits})
                assert req.status_code == 200, req.text
                code = _seed_signin_code(db, contact)

                for field, value in bad_state.items():
                    setattr(user, field, value)
                db.commit()

                resp = client.post(
                    "/api/v1/auth/phone/verify", json={"phone": digits, "code": code}
                )
            assert resp.status_code != 200, resp.text
        finally:
            app.dependency_overrides.pop(get_db, None)
