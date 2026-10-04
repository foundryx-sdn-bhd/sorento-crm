"""S0 identity model red tests (#1280): AC-01, AC-02, AC-03, AC-05, AC-06, AC-07.

Written against `documentation/plans/identity/s0-contract.md` BEFORE any implementation
exists. Every symbol the contract adds (nullable email, the two unique indexes, the
email-or-phone check constraint, `mint_session(..., auth_method=...)`, the seeded
`salesperson` / `portal_user` roles) is imported or exercised inside each test body so a
missing piece fails only the test that needs it, never the whole module.

Postgres only (`tests/_pg_fixture.py`), one rolled-back transaction per test. Every test
seeds its own chain - the CI database (`sorento_ci`, built by `scripts.bootstrap_env` from
the CURRENT models) starts empty and, until S0 lands, has none of the columns this
contract adds.
"""
from __future__ import annotations

import uuid

import bcrypt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models.access import RespondContact
from app.models.user import User, UserRole, UserRoleAssignment
from tests._pg_fixture import blank_session, unique_code


def _phone() -> str:
    return f"+6011{uuid.uuid4().int % 10_000_000:07d}"


def _seed_contact(db: Session, *, phone: str, name: str = "ZZT Contact") -> RespondContact:
    contact = RespondContact(id=str(uuid.uuid4()), phone_number=phone, name=name)
    db.add(contact)
    db.flush()
    return contact


def _seed_role(db: Session, *, slug: str) -> UserRole:
    row = UserRole(id=str(uuid.uuid4()), slug=slug, name=slug.title(), is_protected=False, is_default=False)
    db.add(row)
    db.flush()
    return row


def _seed_admin_user(db: Session) -> User:
    role = _seed_role(db, slug=f"admin-{uuid.uuid4().hex[:6]}")
    # `admin` bypasses require_permission's check entirely (UserPermissionService).
    role.slug = "admin"
    admin = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('admin')}@example.com".lower(),
        name="ZZT Admin",
        status="ACTIVE",
    )
    db.add(admin)
    db.flush()
    db.add(UserRoleAssignment(user_id=admin.id, role_id=role.id))
    db.commit()
    return admin


@pytest.fixture()
def api_client():
    """A TestClient over the real app, `get_current_user`/`get_db` overridden to an
    admin acting principal on a `blank_session`. Mirrors tests/test_impersonation.py.
    """
    from app.dependencies import get_current_user

    with blank_session() as db:
        admin = _seed_admin_user(db)

        def _override_user():
            return {"id": admin.id, "email": admin.email, "name": admin.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        with TestClient(app) as client:
            yield client, db, admin
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# AC-01: one principal per person, second link on the same contact is 409     #
# --------------------------------------------------------------------------- #
def test_ac01_second_user_linking_same_contact_is_integrity_error_db_level():
    with blank_session() as db:
        contact = _seed_contact(db, phone=_phone())
        u1 = User(id=str(uuid.uuid4()), email=f"{unique_code('u1')}@x.com".lower(), name="U1", status="ACTIVE", respond_contact_id=contact.id)
        db.add(u1)
        db.commit()

        u2 = User(id=str(uuid.uuid4()), email=f"{unique_code('u2')}@x.com".lower(), name="U2", status="ACTIVE", respond_contact_id=contact.id)
        db.add(u2)
        with pytest.raises(IntegrityError):
            db.commit()


def test_ac01_many_users_with_null_respond_contact_id_is_fine():
    with blank_session() as db:
        for _ in range(3):
            db.add(User(id=str(uuid.uuid4()), email=f"{unique_code('n')}@x.com".lower(), name="N", status="ACTIVE"))
        db.commit()  # must not raise


def test_ac01_update_route_linking_contact_already_held_is_409_contact_already_linked(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db, phone=_phone())
    holder = User(id=str(uuid.uuid4()), email=f"{unique_code('holder')}@x.com".lower(), name="Holder Person", status="ACTIVE", respond_contact_id=contact.id)
    target = User(id=str(uuid.uuid4()), email=f"{unique_code('target')}@x.com".lower(), name="Target Person", status="ACTIVE")
    db.add_all([holder, target])
    db.commit()

    resp = client.put(f"/api/v1/user-management/users/{target.id}", json={"respond_contact_id": contact.id})

    assert resp.status_code == 409, resp.text
    # app_exception_handler (app/main.py) writes AppException.detail AS THE TOP-LEVEL
    # body, not nested under a "detail" key - {"message", "detail", "code"}.
    body = resp.json()
    assert body.get("code") == "CONTACT_ALREADY_LINKED", body
    message = str(body.get("message") or "")
    assert "Holder Person" in message
    assert holder.id not in resp.text


def test_ac01_resolve_user_respond_contact_never_caches_onto_a_contact_another_user_holds():
    from app.services.respond_link_service import resolve_user_respond_contact

    with blank_session() as db:
        # Stored form: E.164 digits WITHOUT '+' (phone_utils.normalize_msisdn), which is
        # what resolve_user_respond_contact matches against.
        phone = _phone().lstrip("+")
        contact = _seed_contact(db, phone=phone)
        holder = User(id=str(uuid.uuid4()), email=f"{unique_code('h')}@x.com".lower(), name="Holder", status="ACTIVE", respond_contact_id=contact.id)
        seeker = User(id=str(uuid.uuid4()), email=f"{unique_code('s')}@x.com".lower(), name="Seeker", status="ACTIVE", contact_number=phone)
        db.add_all([holder, seeker])
        db.commit()

        result = resolve_user_respond_contact(db, seeker)  # must not raise

        # S3 fix round 2 (B1): no link, no contact - the phone-match fallback that
        # returned the holder's contact here is gone (respond_link_service docstring).
        assert result is None
        db.refresh(seeker)
        assert seeker.respond_contact_id is None, "seeker must not have cached a contact another user already holds"


# --------------------------------------------------------------------------- #
# AC-02: no email is fine when there is a phone; at least one of the two      #
# --------------------------------------------------------------------------- #
def test_ac02_user_with_phone_and_no_email_commits_with_no_placeholder():
    with blank_session() as db:
        phone = _phone()
        user = User(id=str(uuid.uuid4()), email=None, name="Phone Only", status="ACTIVE", contact_number=phone)
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.email is None, "no placeholder email must ever be written"


def test_ac02_user_with_neither_email_nor_phone_is_integrity_error_named_check():
    with blank_session() as db:
        user = User(id=str(uuid.uuid4()), email=None, name="Nobody", status="ACTIVE")
        db.add(user)
        with pytest.raises(IntegrityError, match="ck_users_email_or_phone"):
            db.commit()


# --------------------------------------------------------------------------- #
# AC-03: case-insensitive email uniqueness                                    #
# --------------------------------------------------------------------------- #
def test_ac03_create_route_case_duplicate_email_is_409_email_taken(api_client):
    client, db, _admin = api_client
    stem = unique_code("dup")
    db.add(User(id=str(uuid.uuid4()), email=f"{stem}@Example.com".lower(), name="First", status="ACTIVE"))
    db.commit()

    resp = client.post(
        "/api/v1/user-management/users/",
        json={"email": f"{stem}@EXAMPLE.COM", "name": "Second"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "EMAIL_TAKEN", resp.json()


def test_ac03_update_route_case_duplicate_email_is_409(api_client):
    client, db, _admin = api_client
    stem = unique_code("dup2")
    first = User(id=str(uuid.uuid4()), email=f"{stem}@example.com".lower(), name="First", status="ACTIVE")
    second = User(id=str(uuid.uuid4()), email=f"other-{stem}@example.com".lower(), name="Second", status="ACTIVE")
    db.add_all([first, second])
    db.commit()

    resp = client.put(f"/api/v1/user-management/users/{second.id}", json={"email": f"{stem}@EXAMPLE.COM"})
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "EMAIL_TAKEN", resp.json()


def test_ac03_created_email_is_stored_lowercased(api_client):
    client, _db, _admin = api_client
    stem = unique_code("mixed")
    resp = client.post(
        "/api/v1/user-management/users/",
        json={"email": f"{stem}@ExAmPlE.CoM", "name": "Mixed Case"},
    )
    assert resp.status_code == 201, resp.text
    # unique_code() returns an upper-case "ZZT-" stem, so the stored form lowercases it too.
    assert resp.json()["email"] == f"{stem}@example.com".lower()


def test_ac03_login_finds_user_by_case_insensitive_email():
    from app.dependencies import get_db as _get_db

    with blank_session() as db:
        stem = unique_code("login")
        stored_email = f"{stem}@example.com"
        pw = "correct-horse-battery-staple"
        db.add(
            User(
                id=str(uuid.uuid4()),
                email=stored_email,
                name="Login Case",
                password=bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
                status="ACTIVE",
            )
        )
        db.commit()

        def _override():
            yield db

        # A mixed-case variant of the stored address, per J-B / AC-03 ("Some.One@Example.COM").
        mixed = stored_email.replace(stem, stem.upper()).replace("@example.com", "@EXAMPLE.COM")

        app.dependency_overrides[_get_db] = _override
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/auth/login", json={"email": mixed, "password": pw})
        finally:
            app.dependency_overrides.pop(_get_db, None)

        assert resp.status_code == 200, resp.text


def test_ac03_db_level_case_duplicate_emails_is_integrity_error_named_index():
    with blank_session() as db:
        stem = unique_code("idx")
        db.add(User(id=str(uuid.uuid4()), email=f"{stem}@example.com", name="A", status="ACTIVE"))
        db.commit()
        db.add(User(id=str(uuid.uuid4()), email=f"{stem}@EXAMPLE.COM", name="B", status="ACTIVE"))
        with pytest.raises(IntegrityError, match="uq_users_email_lower"):
            db.commit()


# --------------------------------------------------------------------------- #
# AC-07: every session records how it was minted                              #
# --------------------------------------------------------------------------- #
def test_ac07_mint_session_default_auth_method_is_password():
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        user = User(id=str(uuid.uuid4()), email=f"{unique_code('m1')}@x.com".lower(), name="M", status="ACTIVE")
        db.add(user)
        db.commit()
        row = mint_session(db, user.id)
        assert row.auth_method == "password"


@pytest.mark.parametrize("method", ["phone_otp", "portal_link", "impersonation"])
def test_ac07_mint_session_accepts_each_contract_value(method: str):
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        user = User(id=str(uuid.uuid4()), email=f"{unique_code('m2')}@x.com".lower(), name="M", status="ACTIVE")
        db.add(user)
        db.commit()
        row = mint_session(db, user.id, auth_method=method)
        assert row.auth_method == method


def test_ac07_mint_session_unknown_auth_method_raises_value_error():
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        user = User(id=str(uuid.uuid4()), email=f"{unique_code('m3')}@x.com".lower(), name="M", status="ACTIVE")
        db.add(user)
        db.commit()
        with pytest.raises(ValueError):
            mint_session(db, user.id, auth_method="carrier_pigeon")


def test_ac07_login_route_creates_session_with_auth_method_password():
    from app.dependencies import get_db as _get_db
    from app.models.user_session import UserSession

    with blank_session() as db:
        pw = "correct-horse-battery-staple"
        user = User(
            id=str(uuid.uuid4()),
            email=f"{unique_code('loginam')}@x.com".lower(),
            name="Login AM",
            password=bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
            status="ACTIVE",
        )
        db.add(user)
        db.commit()

        def _override():
            yield db

        app.dependency_overrides[_get_db] = _override
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/auth/login",
                    json={"email": user.email, "password": pw},
                    headers={"X-Forwarded-For": f"10.0.1.{uuid.uuid4().int % 250 + 1}"},
                )
        finally:
            app.dependency_overrides.pop(_get_db, None)

        assert resp.status_code == 200, resp.text
        row = db.query(UserSession).filter(UserSession.user_id == user.id).first()
        assert row is not None
        assert row.auth_method == "password"


# --------------------------------------------------------------------------- #
# AC-06: no re-registration, existing sign-in and sessions keep working        #
# --------------------------------------------------------------------------- #
def test_ac06_old_image_session_row_with_no_auth_method_reads_password():
    """A raw INSERT that never mentions `auth_method` (what the OLD image writes
    during a blue/green swap) must read back the server default `password`."""
    from app.models.user_session import UserSession

    with blank_session() as db:
        user = User(id=str(uuid.uuid4()), email=f"{unique_code('old')}@x.com".lower(), name="Old", status="ACTIVE")
        db.add(user)
        db.commit()

        db.execute(
            UserSession.__table__.insert().values(
                id=str(uuid.uuid4()),
                token=uuid.uuid4().hex,
                user_id=user.id,
                expires_at="2999-01-01 00:00:00",
                rolling=True,
            )
        )
        db.commit()
        row = db.query(UserSession).filter(UserSession.user_id == user.id).one()
        assert row.auth_method == "password"


def test_ac06_existing_portal_token_still_resolves_via_portal_service():
    from app.models.portal import PortalToken
    from app.services.portal_service import PortalService

    with blank_session() as db:
        contact = _seed_contact(db, phone=_phone())
        from datetime import datetime, timedelta, timezone

        token = PortalToken(
            id=str(uuid.uuid4()),
            token=uuid.uuid4().hex,
            contact_id=contact.id,
            space_id="ZZT-space",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(token)
        db.commit()

        resolved = PortalService(db).resolve_token(token.token)
        assert resolved.id == token.id


def test_ac06_existing_user_with_password_logs_in_after_the_schema_change():
    from app.dependencies import get_db as _get_db

    with blank_session() as db:
        pw = "correct-horse-battery-staple"
        user = User(
            id=str(uuid.uuid4()),
            email=f"{unique_code('preexisting')}@x.com".lower(),
            name="Pre Existing",
            password=bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
            status="ACTIVE",
        )
        db.add(user)
        db.commit()

        def _override():
            yield db

        app.dependency_overrides[_get_db] = _override
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/auth/login",
                    json={"email": user.email, "password": pw},
                    headers={"X-Forwarded-For": f"10.0.2.{uuid.uuid4().int % 250 + 1}"},
                )
        finally:
            app.dependency_overrides.pop(_get_db, None)
        assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# AC-05: audit_logs.actor_type legacy default; seeded protected empty roles   #
# --------------------------------------------------------------------------- #
def test_ac05_old_image_audit_row_with_no_actor_type_reads_legacy():
    from app.models.audit import AuditLog

    with blank_session() as db:
        db.execute(
            AuditLog.__table__.insert().values(
                id=str(uuid.uuid4()),
                entity_type="users",
                entity_id=str(uuid.uuid4()),
                action="UPDATE",
            )
        )
        db.commit()
        row = db.query(AuditLog).order_by(AuditLog.changed_at.desc()).first()
        assert row.actor_type == "legacy"


def test_ac05_reference_seed_seeds_salesperson_and_portal_user_protected_no_default_no_perms():
    from app.services import reference_seed

    with blank_session() as db:
        reference_seed.run(db)
        db.commit()

        for slug in ("salesperson", "portal_user"):
            role = db.query(UserRole).filter(UserRole.slug == slug).first()
            assert role is not None, f"reference_seed.run must seed the {slug!r} role"
            assert role.is_protected is True
            assert role.is_default is False
            from app.models.user import UserRolePermission

            perms = db.query(UserRolePermission).filter(UserRolePermission.role_id == role.id).count()
            assert perms == 0


# --------------------------------------------------------------------------- #
# Reviewer pass at 03d3b474, N5: a write that loses the race to the unique     #
# index is the same 409, not a 500. The pre-checks are stubbed out, which is   #
# exactly what a concurrent writer that commits between check and flush does. #
# --------------------------------------------------------------------------- #
def test_create_losing_the_email_race_to_the_unique_index_is_409_email_taken(api_client, monkeypatch):
    from app.services.user_service import UserService

    client, db, _admin = api_client
    stem = unique_code("race")
    db.add(User(id=str(uuid.uuid4()), email=f"{stem}@example.com", name="Race Winner", status="ACTIVE"))
    db.commit()
    monkeypatch.setattr(UserService, "_check_email_free", lambda self, email, exclude_user_id: None)

    resp = client.post("/api/v1/user-management/users/", json={"email": f"{stem}@EXAMPLE.COM", "name": "Race Loser"})

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body.get("code") == "EMAIL_TAKEN", body
    assert "Race Winner" in str(body.get("message") or "")


def test_invite_losing_the_email_race_to_the_unique_index_is_409_email_taken(api_client, monkeypatch):
    from app.services.user_service import UserService

    client, db, _admin = api_client
    stem = unique_code("irace")
    db.add(User(id=str(uuid.uuid4()), email=f"{stem}@example.com", name="Invite Race Winner", status="ACTIVE"))
    db.commit()
    monkeypatch.setattr(UserService, "_check_email_free", lambda self, email, exclude_user_id: None)

    # S3 (AC-58) removed the POST /users/invite route; UserService.invite_user stays for
    # onboarding provisioning, so the race is pinned on the service method it calls.
    from app.schemas.user import UserCreate
    from app.services.error_handler import AppException

    with pytest.raises(AppException) as raised:
        UserService(db).invite_user(
            UserCreate(email=f"{stem}@EXAMPLE.COM", name="Invite Race Loser"),
            invited_by_user_id=_admin.id,
        )

    assert raised.value.status_code == 409
    assert raised.value.code == "EMAIL_TAKEN"
    assert "Invite Race Winner" in raised.value.message


def test_update_losing_the_contact_race_to_the_unique_index_is_409_contact_already_linked(api_client, monkeypatch):
    from app.services.user_service import UserService

    client, db, _admin = api_client
    contact = _seed_contact(db, phone=_phone())
    holder = User(id=str(uuid.uuid4()), email=f"{unique_code('rh')}@x.com", name="Race Holder", status="ACTIVE", respond_contact_id=contact.id)
    target = User(id=str(uuid.uuid4()), email=f"{unique_code('rt')}@x.com", name="Race Target", status="ACTIVE")
    db.add_all([holder, target])
    db.commit()
    monkeypatch.setattr(UserService, "_check_contact_free", lambda self, contact_id, exclude_user_id: None)

    resp = client.put(f"/api/v1/user-management/users/{target.id}", json={"respond_contact_id": contact.id})

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body.get("code") == "CONTACT_ALREADY_LINKED", body
    assert "Race Holder" in str(body.get("message") or "")
    assert holder.id not in resp.text
