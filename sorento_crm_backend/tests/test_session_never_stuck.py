"""SESSION-NEVER-STUCK: the backend half of "every failure ends in a clear, truthful state".

`dependencies._maybe_apply_impersonation` silently ignores an X-Impersonate-User-Id it
cannot honour (view-as stopped elsewhere, the admin lost the admin role, the target was
deactivated) and serves the admin's own data. The client kept saying "viewing as X"
over that data. Now the response says so with `X-Impersonation-Ended: 1`, which the FE
turns into one notice and a local end of view-as.

Also pins the 401 reason-code contract the FE's dead-session path keys on.

Postgres only (`tests/_pg_fixture.py`), one rolled-back transaction per test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.user import User, UserRole, UserRoleAssignment
from tests._pg_fixture import blank_session, unique_code

ENDED = "X-Impersonation-Ended"
ME_PERMS = "/api/v1/user-management/users/me/permissions"


def _now() -> datetime:
    """Naive UTC, the shape every timestamp column here stores."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user(db, label: str, status: str = "ACTIVE") -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code(label)}@x.com".lower(),
        name=f"ZZT {label}",
        status=status,
        is_trashed=False,
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def view_as():
    from app.models.impersonation import ImpersonationSession
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        role = UserRole(
            id=str(uuid.uuid4()), slug="admin", name="ZZT Admin Role",
            is_protected=False, is_default=False,
        )
        db.add(role)
        db.flush()
        admin = _user(db, "admin")
        target = _user(db, "target")
        assignment = UserRoleAssignment(user_id=admin.id, role_id=role.id)
        db.add(assignment)
        imp = ImpersonationSession(
            id=str(uuid.uuid4()), admin_user_id=admin.id, target_user_id=target.id,
        )
        db.add(imp)
        db.commit()
        session = mint_session(db, admin.id, auth_method="password")

        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        try:
            yield db, TestClient(app), admin, target, imp, assignment, session
        finally:
            app.dependency_overrides.pop(get_db, None)


def _get(client, session, target_id: str | None):
    headers = {"Authorization": f"Bearer {session.token}"}
    if target_id:
        headers["X-Impersonate-User-Id"] = target_id
    return client.get(ME_PERMS, headers=headers)


def test_active_view_as_carries_no_ended_signal(view_as):
    """Regression guard: a view-as the backend honours says nothing extra."""
    _db, client, _admin, target, _imp, _a, session = view_as
    resp = _get(client, session, target.id)
    assert resp.status_code == 200, resp.text
    assert ENDED not in resp.headers


def test_no_view_as_header_no_signal(view_as):
    """Regression guard: plain admin traffic is untouched."""
    _db, client, _admin, _target, _imp, _a, session = view_as
    resp = _get(client, session, None)
    assert resp.status_code == 200, resp.text
    assert ENDED not in resp.headers


def test_view_as_stopped_elsewhere_is_signalled(view_as):
    """RED pre-fix: the ended row was silently ignored, no header."""
    db, client, _admin, target, imp, _a, session = view_as
    imp.ended_at = _now()
    db.commit()

    resp = _get(client, session, target.id)

    assert resp.status_code == 200, resp.text
    assert resp.headers.get(ENDED) == "1"


def test_admin_role_removed_is_signalled(view_as):
    """RED pre-fix: a non-admin's header was silently ignored, no header."""
    db, client, _admin, target, _imp, assignment, session = view_as
    db.delete(assignment)
    db.commit()

    resp = _get(client, session, target.id)

    assert resp.headers.get(ENDED) == "1"


def test_target_deactivated_is_signalled(view_as):
    """RED pre-fix: an inactive target was silently ignored, no header."""
    db, client, _admin, target, _imp, _a, session = view_as
    target.status = "BLOCKED"
    db.commit()

    resp = _get(client, session, target.id)

    assert resp.status_code == 200, resp.text
    assert resp.headers.get(ENDED) == "1"


def test_ended_signal_is_readable_cross_origin(view_as):
    """RED pre-fix: CORS did not expose the header, so a browser calling the API
    from another origin (dev with NEXT_PUBLIC_API_URL) could never read it."""
    from app.config import settings

    db, client, _admin, target, imp, _a, session = view_as
    imp.ended_at = _now()
    db.commit()
    origin = settings.cors_origins_list[0]

    resp = client.get(
        ME_PERMS,
        headers={
            "Authorization": f"Bearer {session.token}",
            "X-Impersonate-User-Id": target.id,
            "Origin": origin,
        },
    )

    exposed = {h.strip().lower() for h in resp.headers.get("access-control-expose-headers", "").split(",")}
    assert ENDED.lower() in exposed


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda s: setattr(s, "expires_at", _now() - timedelta(minutes=1)), "session_expired"),
        (lambda s: setattr(s, "revoked_at", _now()), "session_revoked"),
    ],
)
def test_dead_session_401_carries_the_reason_code_the_fe_keys_on(view_as, mutate, code):
    """Regression guard for the FE contract (`lib/api.ts` _SESSION_DEAD_CODES): a dead
    session is 401 with detail.code, even mid-view-as."""
    db, client, _admin, target, _imp, _a, session = view_as
    mutate(session)
    db.commit()

    resp = _get(client, session, target.id)

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == code
