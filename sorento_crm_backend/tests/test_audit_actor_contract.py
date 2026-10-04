"""Audit actor contract red tests (#1280 S0): AC-08, AC-09, AC-10, AC-11, AC-12, AC-14.

`documentation/plans/identity/s0-contract.md` section 4 + plan section 8. Every case
performs a REAL audited write (`User.__audit_track__ = True`, entity_type "users")
through a real request path and reads the `audit_logs` row it produced.

None of `AuditLog.actor_type` / `real_user_id` / `auth_method` / `session_id` /
`integration_id` / `job_id` / `user_agent` exist yet, so most assertions below fail
on the FIRST such attribute access - AttributeError, a missing column, not a fixture
bug. The handful of tests that build request context by hand (AC-10) import the new
`app.audit_context.AuditActor` / `stamp_actor` / `get_actor` inside the test body, so
a missing symbol only fails that test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.main  # noqa: F401  registers every router (dependencies import from here)
from app.database import get_db
from app.services.audit_service import register_audit_listeners

# The audit-log SQLAlchemy `before_flush` listener is registered on `app.main`'s
# STARTUP EVENT, not on import - a standalone `FastAPI()` test app (used below so
# each scenario gets its own single route) never fires that event. The listener is
# global on the `Session` CLASS (not per-app), so registering it once here covers
# every session any test in this file uses, `app.main`'s included.
register_audit_listeners()
from app.dependencies import (
    IMPERSONATE_HEADER,
    get_current_user,
    get_current_user_or_api_key,
    get_external_api_user,
)
from app.middleware.logging_middleware import LoggingMiddleware
from app.models.access import RespondContact
from app.models.audit import AuditLog
from app.models.impersonation import ContactImpersonationSession, ImpersonationSession
from app.models.integration import Integration, IntegrationApiKey
from app.models.portal import PortalToken
from app.models.user import User, UserRole, UserRoleAssignment
from app.models.user_session import UserSession
from app.services.integration_key_service import IntegrationKeyService
from app.services.user_session_service import mint_session
from tests._pg_fixture import blank_session, unique_code


def _phone() -> str:
    return f"+6011{uuid.uuid4().int % 10_000_000:07d}"


def _seed_contact(db: Session, *, phone: str | None = None, name: str = "ZZT Contact") -> RespondContact:
    contact = RespondContact(id=str(uuid.uuid4()), phone_number=phone or _phone(), name=name)
    db.add(contact)
    db.flush()
    return contact


def _seed_user(db: Session, *, name: str, contact: RespondContact | None = None, status: str = "ACTIVE") -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('u')}@example.com".lower(),
        name=name,
        status=status,
        respond_contact_id=contact.id if contact else None,
    )
    db.add(user)
    db.flush()
    return user


def _last_audit_row(db: Session, entity_id: str) -> AuditLog:
    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "users", AuditLog.entity_id == entity_id, AuditLog.action == "UPDATE")
        .order_by(AuditLog.changed_at.desc())
        .first()
    )


def _rename_app(db: Session, dependency, *, is_async: bool) -> FastAPI:
    """A tiny app with ONE route: rename `target_id` (path param), Depends(dependency)
    for auth, committing through `db`. The real audit flush listener runs on commit.
    """
    test_app = FastAPI()
    test_app.add_middleware(LoggingMiddleware)

    if is_async:
        async def _write(target_id: str, actor: dict = Depends(dependency), session: Session = Depends(get_db)):
            user = session.query(User).filter(User.id == target_id).one()
            user.name = f"Renamed {uuid.uuid4().hex[:8]}"
            session.commit()
            return {"actor": actor.get("id") if isinstance(actor, dict) else str(actor)}
    else:
        def _write(target_id: str, actor: dict = Depends(dependency), session: Session = Depends(get_db)):
            user = session.query(User).filter(User.id == target_id).one()
            user.name = f"Renamed {uuid.uuid4().hex[:8]}"
            session.commit()
            return {"actor": actor.get("id") if isinstance(actor, dict) else str(actor)}

    test_app.post("/write/{target_id}")(_write)

    def _override_db():
        yield db

    test_app.dependency_overrides[get_db] = _override_db
    return test_app


# --------------------------------------------------------------------------- #
# AC-08: staff Bearer session                                                  #
# --------------------------------------------------------------------------- #
def test_ac08_staff_bearer_session_write_carries_full_actor():
    with blank_session() as db:
        contact = _seed_contact(db)
        staff = _seed_user(db, name="Staff One", contact=contact)
        db.commit()
        session_row = mint_session(db, staff.id, user_agent="ZZT-Agent/1.0")

        test_app = _rename_app(db, get_current_user, is_async=True)
        with TestClient(test_app) as client:
            resp = client.post(
                f"/write/{staff.id}",
                headers={"Authorization": f"Bearer {session_row.token}", "User-Agent": "ZZT-Agent/1.0"},
            )
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, staff.id)
        assert row is not None
        assert row.actor_type == "user"
        assert row.user_id == staff.id
        assert row.real_user_id == staff.id
        assert row.auth_method == "password"
        assert row.session_id == session_row.id
        assert row.contact_id == contact.id
        assert row.ip_address is not None
        assert row.user_agent == "ZZT-Agent/1.0"
        assert row.trace_id is not None


# --------------------------------------------------------------------------- #
# AC-09: an integration write names the integration                            #
# --------------------------------------------------------------------------- #
def _seed_integration(db: Session, *, name: str = "n8n") -> tuple[Integration, str, User]:
    act_as = User(id=str(uuid.uuid4()), email=f"{unique_code('int')}@integrations.local".lower(), name=f"Bot {name}", status="ACTIVE", is_integration=True)
    db.add(act_as)
    db.flush()
    integration = Integration(id=str(uuid.uuid4()), name=f"{name}-{uuid.uuid4().hex[:6]}", type="automation", act_as_user_id=act_as.id, is_active=True)
    db.add(integration)
    db.flush()
    key = IntegrationKeyService(db).issue_key(integration)
    db.commit()
    return integration, key, act_as


def test_ac09_api_key_write_via_current_user_or_api_key_names_the_integration():
    with blank_session() as db:
        target = _seed_user(db, name="Target For Integration")
        integration, key, act_as = _seed_integration(db)
        db.commit()

        test_app = _rename_app(db, get_current_user_or_api_key, is_async=False)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "integration"
        assert row.integration_id == integration.id
        assert row.user_id == act_as.id
        assert row.auth_method == "api_key"


def test_ac09_api_key_write_via_get_external_api_user_names_the_integration():
    with blank_session() as db:
        target = _seed_user(db, name="Target For External")
        integration, key, act_as = _seed_integration(db, name="esb")
        db.commit()

        test_app = _rename_app(db, get_external_api_user, is_async=True)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "integration"
        assert row.integration_id == integration.id
        assert row.user_id == act_as.id


def test_ac09_tool_name_header_lands_in_description_when_row_has_none():
    with blank_session() as db:
        target = _seed_user(db, name="Target For Tool Name")
        _integration, key, _act_as = _seed_integration(db, name="mcp")
        db.commit()

        test_app = _rename_app(db, get_current_user_or_api_key, is_async=False)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"X-API-Key": key, "X-Tool-Name": "crm_x"})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert "crm_x" in (row.description or "")


# --------------------------------------------------------------------------- #
# AC-10: a background job / scheduler tick keeps its actor                     #
# --------------------------------------------------------------------------- #
def test_ac10_enqueue_job_writes_current_actor_into_job_meta(monkeypatch):
    from app.audit_context import AuditActor, stamp_actor  # new contract symbols
    from app.services import queue_service

    captured: dict = {}

    class _FakeJob:
        id = "fake-job-id"

    class _FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            captured.update(kwargs)
            return _FakeJob()

    monkeypatch.setattr(queue_service, "get_queue", lambda name="imports": _FakeQueue())

    user_id = str(uuid.uuid4())
    stamp_actor(AuditActor(actor_type="user", user_id=user_id, real_user_id=user_id))

    def _noop():
        return None

    queue_service.enqueue_job(_noop, queue_name="imports")

    meta = captured.get("meta") or {}
    actor_meta = meta.get("actor") or {}
    assert actor_meta.get("user_id") == user_id
    assert actor_meta.get("real_user_id") == user_id


def _job_rename_tracked_user(user_id: str, new_name: str) -> None:
    """Runs OUT OF PROCESS (via run_sync_rq_jobs, in-process here) - its own session,
    a real commit against the real CI database. The test cleans up what it wrote.
    """
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is not None:
            user.name = new_name
            session.commit()
    finally:
        session.close()


def test_ac10_worker_drain_stamps_actor_type_worker_with_job_id():
    from app.database import SessionLocal
    from app.services.queue_service import get_queue, run_sync_rq_jobs

    session = SessionLocal()
    user_id = str(uuid.uuid4())
    queue_name = f"zzt-identity-{uuid.uuid4().hex[:8]}"
    try:
        session.add(User(id=user_id, email=f"{unique_code('worker')}@example.com".lower(), name="Worker Target", status="ACTIVE"))
        session.commit()

        enqueuing_user_id = str(uuid.uuid4())
        session.add(User(id=enqueuing_user_id, email=f"{unique_code('enq')}@example.com".lower(), name="Enqueuer", status="ACTIVE"))
        session.commit()

        queue = get_queue(queue_name)
        job = queue.enqueue(
            _job_rename_tracked_user,
            user_id,
            "Renamed By Worker",
            meta={"actor": {"user_id": enqueuing_user_id, "real_user_id": enqueuing_user_id, "trace_id": "zzt-trace"}},
        )

        run_sync_rq_jobs(queue_name, 1)

        row = (
            session.query(AuditLog)
            .filter(AuditLog.entity_type == "users", AuditLog.entity_id == user_id, AuditLog.action == "UPDATE")
            .order_by(AuditLog.changed_at.desc())
            .first()
        )
        assert row is not None, "the job's write must have been audited"
        assert row.actor_type == "worker"
        assert row.user_id == enqueuing_user_id
        assert row.job_id == job.id
    finally:
        # audit_logs is append-only (#1281 S0): the cleanup runs under the maintenance flag.
        session.execute(text("SET LOCAL sorento.audit_maintenance = 'on'"))
        session.query(AuditLog).filter(AuditLog.entity_type == "users", AuditLog.entity_id == user_id).delete()
        session.query(User).filter(User.id.in_([user_id, enqueuing_user_id])).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_ac10_scheduler_tick_write_has_actor_type_scheduler_and_no_user():
    from app.scheduler.task_scheduler import scheduler_session

    with blank_session() as db:
        target = _seed_user(db, name="Scheduler Target")
        db.commit()

        import app.scheduler.task_scheduler as ts_module

        bind = db.get_bind()

        def _factory() -> Session:
            # `scheduler_session()` closes what it opens - a factory (test_auth_login.py's
            # pattern) sharing `db`'s connection via a savepoint, not `db` itself, or its
            # `finally: db.close()` tears down the session this test asserts with.
            return Session(bind=bind, join_transaction_mode="create_savepoint")

        original_session_local = ts_module.SessionLocal
        ts_module.SessionLocal = _factory
        try:
            with scheduler_session() as sched_db:
                row = sched_db.query(User).filter(User.id == target.id).one()
                row.name = "Renamed By Scheduler"
                sched_db.commit()
        finally:
            ts_module.SessionLocal = original_session_local

        audit_row = _last_audit_row(db, target.id)
        assert audit_row is not None
        assert audit_row.actor_type == "scheduler"
        assert audit_row.user_id is None


# --------------------------------------------------------------------------- #
# AC-11: admin "view as contact" is credited to the admin                      #
# --------------------------------------------------------------------------- #
def test_ac11_impersonation_portal_token_write_credits_the_admin():
    from app.api.v1.public.portal import get_portal_token

    with blank_session() as db:
        contact = _seed_contact(db)
        contact_user = _seed_user(db, name="Contact's User", contact=contact)
        admin = _seed_user(db, name="Admin Viewing As")
        db.commit()

        token = PortalToken(
            id=str(uuid.uuid4()),
            token=uuid.uuid4().hex,
            contact_id=contact.id,
            space_id="ZZT-space",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
            is_impersonation=True,
        )
        db.add(token)
        db.flush()
        db.add(
            ContactImpersonationSession(
                id=str(uuid.uuid4()),
                admin_user_id=admin.id,
                target_contact_id=contact.id,
                space_id="ZZT-space",
                portal_token_id=token.id,
            )
        )
        db.commit()

        test_app = _rename_app(db, get_portal_token, is_async=False)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{contact_user.id}", headers={"X-Portal-Token": token.token})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, contact_user.id)
        assert row is not None
        # Pre-fix observed value (today): the row carries no real_user_id / auth_method
        # at all - get_portal_token only stamped the contact, it never looked at
        # contact_impersonation_sessions, so an admin "viewing as" gets no credit.
        assert row.real_user_id == admin.id
        assert row.contact_id == contact.id
        assert row.auth_method == "impersonation"


# --------------------------------------------------------------------------- #
# AC-12: the actor survives a synchronous dependency                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("is_async", [False, True], ids=["def_route", "async_def_route"])
def test_ac12_bearer_session_through_current_user_or_api_key_records_user_id(is_async: bool):
    with blank_session() as db:
        target = _seed_user(db, name="AC12 Target")
        staff = _seed_user(db, name="AC12 Staff")
        db.commit()
        session_row = mint_session(db, staff.id)

        test_app = _rename_app(db, get_current_user_or_api_key, is_async=is_async)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"Authorization": f"Bearer {session_row.token}"})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        # Today's gap (plan 3.5): NULL on at least one of the two route shapes, because
        # get_current_user_or_api_key is a plain `def` and FastAPI runs it in a separate
        # threadpool thread from the flush - a contextvar set there is invisible here.
        assert row.user_id == staff.id, f"user_id lost on {'async def' if is_async else 'def'} route"


# --------------------------------------------------------------------------- #
# AC-14: a portal write by a contact with / without a user                     #
# --------------------------------------------------------------------------- #
def test_ac14_portal_token_of_contact_with_no_user_is_actor_type_contact():
    from app.api.v1.public.portal import get_portal_token

    with blank_session() as db:
        contact = _seed_contact(db, name="No User Contact")
        target = _seed_user(db, name="AC14 Target A")
        db.commit()

        token = PortalToken(
            id=str(uuid.uuid4()),
            token=uuid.uuid4().hex,
            contact_id=contact.id,
            space_id="ZZT-space",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(token)
        db.commit()

        test_app = _rename_app(db, get_portal_token, is_async=False)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"X-Portal-Token": token.token})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "contact"
        assert row.auth_method == "portal_token"
        assert row.contact_id == contact.id
        assert row.user_id is None


def test_ac14_portal_token_of_contact_with_a_user_is_actor_type_user():
    from app.api.v1.public.portal import get_portal_token

    with blank_session() as db:
        contact = _seed_contact(db, name="Has User Contact")
        contact_user = _seed_user(db, name="AC14 Linked User", contact=contact)
        target = _seed_user(db, name="AC14 Target B")
        db.commit()

        token = PortalToken(
            id=str(uuid.uuid4()),
            token=uuid.uuid4().hex,
            contact_id=contact.id,
            space_id="ZZT-space",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(token)
        db.commit()

        test_app = _rename_app(db, get_portal_token, is_async=False)
        with TestClient(test_app) as client:
            resp = client.post(f"/write/{target.id}", headers={"X-Portal-Token": token.token})
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "user"
        assert row.user_id == contact_user.id
        assert row.auth_method == "portal_token"
        assert row.contact_id == contact.id


# --------------------------------------------------------------------------- #
# Impersonation of a USER (CRM impersonation, not "view as contact")           #
# --------------------------------------------------------------------------- #
def test_impersonation_of_a_user_credits_admin_as_real_user_and_keeps_target_created_by():
    with blank_session() as db:
        admin = _seed_user(db, name="CRM Admin")
        role = UserRole(id=str(uuid.uuid4()), slug="admin", name="Admin", is_protected=False, is_default=False)
        db.add(role)
        db.flush()
        db.add(UserRoleAssignment(user_id=admin.id, role_id=role.id))
        target = _seed_user(db, name="Impersonation Target")
        db.commit()
        admin_session = mint_session(db, admin.id)
        db.add(
            ImpersonationSession(
                id=str(uuid.uuid4()),
                admin_user_id=admin.id,
                target_user_id=target.id,
            )
        )
        db.commit()

        test_app = _rename_app(db, get_current_user, is_async=True)
        with TestClient(test_app) as client:
            resp = client.post(
                f"/write/{target.id}",
                headers={
                    "Authorization": f"Bearer {admin_session.token}",
                    IMPERSONATE_HEADER: target.id,
                },
            )
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.user_id == target.id
        assert row.real_user_id == admin.id
        assert row.auth_method == "impersonation"
        db.refresh(target)
        # Plan 8.2: `_swap_actor_fields_during_impersonation` is REMOVED - the effective
        # user (the target) stays on created_by/updated_by; only real_user_id says who
        # was at the keyboard. Today the swap still runs, so this fails the other way.
        assert target.created_by_user_id == target.id if hasattr(target, "created_by_user_id") else True


def test_no_principal_on_public_path_is_actor_type_public_link():
    test_app = FastAPI()
    test_app.add_middleware(LoggingMiddleware)

    with blank_session() as db:
        target = _seed_user(db, name="Public Target")
        db.commit()

        def _write(target_id: str, session: Session = Depends(get_db)):
            user = session.query(User).filter(User.id == target_id).one()
            user.name = f"Renamed {uuid.uuid4().hex[:8]}"
            session.commit()
            return {"ok": True}

        test_app.post("/api/v1/public/write/{target_id}")(_write)

        def _override_db():
            yield db

        test_app.dependency_overrides[get_db] = _override_db
        with TestClient(test_app) as client:
            resp = client.post(f"/api/v1/public/write/{target.id}")
        assert resp.status_code == 200, resp.text

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "public_link"


def test_direct_service_write_with_no_request_is_actor_type_system():
    with blank_session() as db:
        target = _seed_user(db, name="System Target")
        db.commit()
        target.name = "Renamed Directly"
        db.commit()

        row = _last_audit_row(db, target.id)
        assert row is not None
        assert row.actor_type == "system"


def test_api_call_log_actor_is_user_prefixed_id():
    """GET /api/v1/user-management/users/me needs only get_current_user, no extra
    permission seeding. `X-Source` makes ApiCallLogMiddleware log ANY path
    (`_is_sourced_call`), not only `/api/v1/external/*`.
    """
    import app.database as app_database
    from app.main import app as real_app

    from app.models.api_call_log import ApiCallLog

    with blank_session() as db:
        staff = _seed_user(db, name="ApiLog Staff")
        db.commit()
        session_row = mint_session(db, staff.id)
        token = session_row.token

        bind = db.get_bind()
        # `api_call_log` is not on Base.metadata until its model module is imported
        # somewhere - `app.main`'s import chain never does (the middleware imports
        # it lazily, at request time). Whichever test in this SESSION built the
        # shared blank schema first may have missed it; make sure it exists here.
        ApiCallLog.__table__.create(bind=bind, checkfirst=True)

        def _factory() -> Session:
            # A NEW Session per call, sharing `db`'s connection/transaction via a
            # savepoint (test_auth_login.py's pattern) - the middleware's own
            # `db.close()` must not tear down the session THIS test asserts with.
            return Session(bind=bind, join_transaction_mode="create_savepoint")

        def _override_db():
            yield db

        real_app.dependency_overrides[get_db] = _override_db
        original_session_local = app_database.SessionLocal
        app_database.SessionLocal = _factory  # the middleware opens its OWN session
        try:
            with TestClient(real_app) as client:
                resp = client.get(
                    "/api/v1/user-management/users/me",
                    headers={"Authorization": f"Bearer {token}", "X-Source": "zzt-test"},
                )
        finally:
            real_app.dependency_overrides.pop(get_db, None)
            app_database.SessionLocal = original_session_local

        assert resp.status_code == 200, resp.text
        from app.models.api_call_log import ApiCallLog

        row = (
            db.query(ApiCallLog)
            .filter(ApiCallLog.endpoint == "/api/v1/user-management/users/me")
            .order_by(ApiCallLog.id.desc())
            .first()
        )
        assert row is not None, "ApiCallLogMiddleware should have written a row for an X-Source call"
        assert row.actor == f"user:{staff.id}"
