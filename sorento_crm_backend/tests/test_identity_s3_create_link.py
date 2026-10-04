"""S3 identity red tests (#1280): AC-40 to AC-47, AC-52 (BE part), AC-54, AC-55, select filters.

Written against `documentation/plans/identity/s3-contract.md` sections 1 and 3, BEFORE any
implementation exists. Every symbol the contract adds (`app.services.user_contact_link`,
the PHONE_BELONGS_TO_USER/USER_ALREADY_LINKED/EMAIL_OR_PHONE_REQUIRED codes, the
`user.unlink_contact` deferred action, `respond_contact_id`/optional email on create,
the five new `GET /users/{id}` fields, the three `GET /users/select` filters) is imported
or exercised INSIDE each test body, so a missing piece fails only its own test.

Postgres only (`tests/_pg_fixture.py`). `blank_session()` for a fresh schema per test;
`pg_session()` for the two AC-45 tests that must see the REAL bootstrapped
`salesperson`/`portal_user` roles (built by `scripts.bootstrap_env`'s migration grant
sweep, not by anything this test file seeds). Every test seeds its own chain.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models.access import RespondContact
from app.models.user import User, UserRole, UserRoleAssignment
from tests._pg_fixture import blank_session, pg_session, unique_code


def _phone() -> str:
    return f"+6011{uuid.uuid4().int % 10_000_000:07d}"


def _msisdn(raw: str) -> str:
    from app.services.phone_utils import normalize_msisdn

    return normalize_msisdn(raw)


def _seed_contact(db: Session, *, phone: str | None = None, name: str = "ZZT Contact") -> RespondContact:
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=phone or _msisdn(_phone()),
        name=name,
    )
    db.add(contact)
    db.flush()
    return contact


def _seed_role(db: Session, *, slug: str, is_default: bool = False, is_protected: bool = False) -> UserRole:
    role = UserRole(
        id=str(uuid.uuid4()),
        slug=slug,
        name=slug.title(),
        is_protected=is_protected,
        is_default=is_default,
    )
    db.add(role)
    db.flush()
    return role


def _seed_admin_user(db: Session) -> User:
    role = _seed_role(db, slug="admin")
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


def _seed_user(db: Session, *, name: str = "ZZT User", email: str | None = None, phone: str | None = None) -> User:
    if email is None and phone is None:
        email = f"{unique_code('u')}@x.com".lower()
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        name=name,
        status="ACTIVE",
        contact_number=phone,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def api_client():
    """A TestClient over the real app, `get_current_user`/`get_db` overridden to an
    admin acting principal on a `blank_session`. Mirrors tests/test_identity_s0_model.py.
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


@pytest.fixture()
def real_session_client():
    """A TestClient over the real app with a REAL minted admin session (Authorization
    Bearer header), `get_current_user` NOT overridden, so the auth dependency actually
    runs `_stamp_session_actor` and the audit actor context carries `real_user_id`
    (AC-47). Only `get_db` is overridden, mirroring tests/test_audit_actor_contract.py.
    """
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        admin = _seed_admin_user(db)
        session_row = mint_session(db, admin.id, user_agent="ZZT-Agent/1.0")

        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        with TestClient(app) as client:
            client.headers.update(
                {"Authorization": f"Bearer {session_row.token}", "User-Agent": "ZZT-Agent/1.0"}
            )
            yield client, db, admin
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# AC-40: one function decides who is a salesperson contact                     #
# --------------------------------------------------------------------------- #
def test_ac40_segment_requestor_contact_is_salesperson():
    from app.models.access import MarketSegment, respond_contact_market_segments
    from app.services.user_contact_link import is_salesperson_contact, suggested_role_slug

    with blank_session() as db:
        contact = _seed_contact(db)
        segment = MarketSegment(
            code=unique_code("seg")[:20],
            name="ZZT Segment",
            is_requestor_selectable=True,
        )
        db.add(segment)
        db.flush()
        db.execute(
            respond_contact_market_segments.insert().values(
                contact_id=contact.id, segment_code=segment.code
            )
        )
        db.commit()

        assert is_salesperson_contact(db, contact.id) is True
        assert suggested_role_slug(db, contact.id) == "salesperson"


def test_ac40_sales_agent_contact_is_salesperson():
    from app.models.sales_agent import SalesAgent
    from app.services.user_contact_link import is_salesperson_contact, suggested_role_slug

    with blank_session() as db:
        contact = _seed_contact(db)
        db.add(SalesAgent(id=str(uuid.uuid4()), sales_agent=unique_code("AGENT"), contact_id=contact.id))
        db.commit()

        assert is_salesperson_contact(db, contact.id) is True
        assert suggested_role_slug(db, contact.id) == "salesperson"


def test_ac40_other_contact_is_not_salesperson_suggests_portal_user():
    from app.services.user_contact_link import is_salesperson_contact, suggested_role_slug

    with blank_session() as db:
        contact = _seed_contact(db)
        db.commit()

        assert is_salesperson_contact(db, contact.id) is False
        assert suggested_role_slug(db, contact.id) == "portal_user"


def test_ac40_function_leaves_users_and_role_assignment_counts_unchanged():
    from app.services.user_contact_link import is_salesperson_contact, suggested_role_slug

    with blank_session() as db:
        contact = _seed_contact(db)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        is_salesperson_contact(db, contact.id)
        suggested_role_slug(db, contact.id)

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


# --------------------------------------------------------------------------- #
# AC-41: create from a contact                                                 #
# --------------------------------------------------------------------------- #
def test_ac41_create_from_contact_links_and_uses_contact_phone_not_body_phone(api_client):
    client, db, _admin = api_client
    contact_phone = _msisdn(_phone())
    contact = _seed_contact(db, phone=contact_phone, name="Ali Contact")
    db.commit()

    body_phone = _msisdn(_phone())
    resp = client.post(
        "/api/v1/user-management/users/",
        json={
            "email": f"{unique_code('c41')}@x.com".lower(),
            "name": "Ali",
            "respond_contact_id": contact.id,
            "contact_number": body_phone,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["respond_contact_id"] == contact.id
    assert data["contact_number"] == contact_phone, "the contact's phone must win, not the body's"


def test_ac41_create_from_contact_roles_companies_exact_first_company_is_last_active(api_client):
    client, db, _admin = api_client
    from app.models.company import Company, UserCompany

    contact = _seed_contact(db)
    role = _seed_role(db, slug=unique_code("role").lower())
    company_a = Company(id=str(uuid.uuid4()), name="ZZT Co A", code=unique_code("COA")[:20])
    company_b = Company(id=str(uuid.uuid4()), name="ZZT Co B", code=unique_code("COB")[:20])
    db.add_all([company_a, company_b])
    db.commit()

    resp = client.post(
        "/api/v1/user-management/users/",
        json={
            "email": f"{unique_code('c41b')}@x.com".lower(),
            "name": "Roles Co",
            "respond_contact_id": contact.id,
            "role_ids": [role.id],
            "company_ids": [company_a.id, company_b.id],
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    role_ids = {r.role_id for r in db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user_id)}
    assert role_ids == {role.id}
    company_ids = {
        c.company_id for c in db.query(UserCompany).filter(UserCompany.user_id == user_id)
    }
    assert company_ids == {company_a.id, company_b.id}

    saved = db.query(User).filter(User.id == user_id).one()
    assert saved.last_active_company_id == company_a.id, "the FIRST company_id becomes last_active_company_id"


def test_ac41_create_from_contact_is_active_with_no_password(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db)
    db.commit()

    resp = client.post(
        "/api/v1/user-management/users/",
        json={"name": "Phone Person", "respond_contact_id": contact.id},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "ACTIVE"
    saved = db.query(User).filter(User.id == data["id"]).one()
    assert saved.password is None


def test_ac41_email_only_create_is_inactive_with_no_password(api_client):
    client, db, _admin = api_client
    resp = client.post(
        "/api/v1/user-management/users/",
        json={"email": f"{unique_code('c41e')}@x.com".lower(), "name": "Email Only"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "INACTIVE"
    saved = db.query(User).filter(User.id == data["id"]).one()
    assert saved.password is None


def test_ac41_phone_only_create_has_null_email(api_client):
    client, db, _admin = api_client
    phone = _msisdn(_phone())
    resp = client.post(
        "/api/v1/user-management/users/",
        json={"name": "Phone Only Person", "contact_number": phone},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email"] is None
    assert data["contact_number"] == phone


def test_ac41_neither_email_nor_phone_is_422_email_or_phone_required(api_client):
    client, db, _admin = api_client
    before = db.query(User).count()
    resp = client.post("/api/v1/user-management/users/", json={"name": "Nobody"})
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "EMAIL_OR_PHONE_REQUIRED", resp.json()
    assert db.query(User).count() == before


def test_ac41_unknown_contact_is_404(api_client):
    client, db, _admin = api_client
    resp = client.post(
        "/api/v1/user-management/users/",
        json={
            "email": f"{unique_code('c41u')}@x.com".lower(),
            "name": "Ghost Contact",
            "respond_contact_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404, resp.text


def test_ac41_put_linking_unknown_contact_is_404(api_client):
    client, db, _admin = api_client
    target = _seed_user(db, name="Link Target", email=f"{unique_code('c41p')}@x.com".lower())
    db.commit()

    resp = client.put(
        f"/api/v1/user-management/users/{target.id}",
        json={"respond_contact_id": str(uuid.uuid4())},
    )

    assert resp.status_code == 404, resp.text
    db.refresh(target)
    assert target.respond_contact_id is None


# --------------------------------------------------------------------------- #
# AC-42: phone collision names the holder, no id, no phone in the body         #
# --------------------------------------------------------------------------- #
def test_ac42_create_with_held_phone_is_409_phone_belongs_to_user_no_id_no_phone_leaked(api_client):
    client, db, _admin = api_client
    phone = _msisdn(_phone())
    holder = _seed_user(db, name="Phone Holder", email=None, phone=phone)
    db.commit()

    before = db.query(User).count()
    resp = client.post(
        "/api/v1/user-management/users/",
        json={"name": "New Person", "contact_number": phone},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body.get("code") == "PHONE_BELONGS_TO_USER", body
    assert "Phone Holder" in str(body.get("message") or "")
    assert holder.id not in resp.text
    assert phone not in resp.text
    assert db.query(User).count() == before


def test_ac42_put_linking_contact_to_that_existing_user_succeeds_no_role_added(api_client):
    client, db, _admin = api_client
    phone = _msisdn(_phone())
    holder = _seed_user(db, name="Phone Holder Two", email=f"{unique_code('h2')}@x.com".lower(), phone=phone)
    contact = _seed_contact(db, phone=phone)
    db.commit()

    before_roles = {
        r.role_id for r in db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == holder.id)
    }

    resp = client.put(
        f"/api/v1/user-management/users/{holder.id}",
        json={"respond_contact_id": contact.id},
    )
    assert resp.status_code == 200, resp.text
    saved = db.query(User).filter(User.id == holder.id).one()
    assert saved.respond_contact_id == contact.id

    after_roles = {
        r.role_id for r in db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == holder.id)
    }
    assert after_roles == before_roles


# --------------------------------------------------------------------------- #
# AC-43: contact collision / user already linked elsewhere                     #
# --------------------------------------------------------------------------- #
def test_ac43_create_with_held_contact_is_409_contact_already_linked(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db)
    holder = _seed_user(db, name="Contact Holder")
    holder.respond_contact_id = contact.id
    db.commit()

    resp = client.post(
        "/api/v1/user-management/users/",
        json={"email": f"{unique_code('c43')}@x.com".lower(), "name": "Another", "respond_contact_id": contact.id},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "CONTACT_ALREADY_LINKED", resp.json()


def test_ac43_put_linking_user_already_linked_elsewhere_is_409_user_already_linked(api_client):
    client, db, _admin = api_client
    contact_a = _seed_contact(db, name="Contact A")
    contact_b = _seed_contact(db, name="Contact B")
    target = _seed_user(db, name="Already Linked Target")
    target.respond_contact_id = contact_a.id
    db.commit()

    resp = client.put(
        f"/api/v1/user-management/users/{target.id}",
        json={"respond_contact_id": contact_b.id},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body.get("code") == "USER_ALREADY_LINKED", body
    assert "Contact A" in str(body.get("message") or "")

    db.refresh(target)
    assert target.respond_contact_id == contact_a.id, "the original link must be untouched"


# --------------------------------------------------------------------------- #
# AC-44: nothing but the owner's create/edit/link changes users or their roles #
# --------------------------------------------------------------------------- #
def test_ac44_adding_contact_to_requestor_segment_leaves_user_counts_unchanged():
    from app.models.access import MarketSegment, respond_contact_market_segments

    with blank_session() as db:
        contact = _seed_contact(db)
        segment = MarketSegment(code=unique_code("seg2")[:20], name="ZZT Seg2", is_requestor_selectable=True)
        db.add(segment)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        db.execute(
            respond_contact_market_segments.insert().values(contact_id=contact.id, segment_code=segment.code)
        )
        db.commit()

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


def test_ac44_linking_contact_to_sales_agent_leaves_user_counts_unchanged():
    from app.models.sales_agent import SalesAgent

    with blank_session() as db:
        contact = _seed_contact(db)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        db.add(SalesAgent(id=str(uuid.uuid4()), sales_agent=unique_code("AGENT2"), contact_id=contact.id))
        db.commit()

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


def test_ac44_removing_contact_from_every_salesperson_source_leaves_user_counts_unchanged():
    from app.models.access import MarketSegment, respond_contact_market_segments
    from app.models.sales_agent import SalesAgent

    with blank_session() as db:
        contact = _seed_contact(db)
        segment = MarketSegment(code=unique_code("seg3")[:20], name="ZZT Seg3", is_requestor_selectable=True)
        db.add(segment)
        db.flush()
        db.execute(
            respond_contact_market_segments.insert().values(contact_id=contact.id, segment_code=segment.code)
        )
        agent = SalesAgent(id=str(uuid.uuid4()), sales_agent=unique_code("AGENT3"), contact_id=contact.id)
        db.add(agent)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        db.execute(
            respond_contact_market_segments.delete().where(
                respond_contact_market_segments.c.contact_id == contact.id
            )
        )
        agent.contact_id = None
        db.commit()

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


def test_ac44_contact_phone_update_via_contact_service_leaves_user_counts_unchanged():
    from app.schemas.user import RespondContactUpdate
    from app.services.contact_service import ContactService

    with blank_session() as db:
        contact = _seed_contact(db)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        ContactService(db).update_contact(contact.id, RespondContactUpdate(phone_number=_msisdn(_phone())))

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


def test_ac44_portal_otp_verify_leaves_user_counts_unchanged():
    import datetime as _dt

    from app.models.portal import PortalOtpCode
    from app.models.respond_workspace import RespondWorkspace
    from app.services.portal_service import PortalService, _hash_otp, _utcnow

    with blank_session() as db:
        ws = RespondWorkspace(
            id=str(uuid.uuid4()),
            space_id=f"ZZT-sp-{uuid.uuid4().hex[:8]}",
            name="ZZT WS",
            api_key_ciphertext="test-cipher",
        )
        db.add(ws)
        db.flush()
        contact = RespondContact(
            id=str(uuid.uuid4()),
            phone_number=_msisdn(_phone()),
            name="Portal Verifier",
            workspace_id=ws.id,
        )
        db.add(contact)
        db.flush()
        code = "112233"
        otp = PortalOtpCode(
            contact_id=contact.id,
            space_id=ws.space_id,
            code_hash=_hash_otp(code),
            expires_at=_utcnow() + _dt.timedelta(minutes=10),
        )
        db.add(otp)
        db.commit()

        before_users = db.query(User).count()
        before_assignments = db.query(UserRoleAssignment).count()

        PortalService(db).verify_otp(contact.id, ws.space_id, code)

        assert db.query(User).count() == before_users
        assert db.query(UserRoleAssignment).count() == before_assignments


def test_ac44_static_no_disallowed_user_construction_outside_allowlist():
    """Walk `app/**/*.py` and fail on a `User(` or `UserRoleAssignment(` construction
    site outside today's allowlist (by file, since `invite_user` is being deleted and
    is not allowlisted by name): `app/services/user_service.py` (create paths),
    `app/api/v1/auth.py` (self-signup), `app/services/integration_seed.py`
    (integration machine principal).

    A tripwire, not a proof (fix round 2, N5): it allowlists whole files and matches
    only `User(`/`UserRoleAssignment(` constructor calls, so a raw `INSERT`, an aliased
    import, or a new creating function inside an allowlisted file all pass it. The
    behavioural AC-44 tests above (and the Respond.io sync one in
    test_identity_s3_unlink_holds.py) are what pin each source."""
    import re
    from pathlib import Path

    app_root = Path(__file__).resolve().parent.parent / "app"
    allowlist = {
        app_root / "services" / "user_service.py",
        app_root / "api" / "v1" / "auth.py",
        app_root / "services" / "integration_seed.py",
    }
    pattern = re.compile(r"(?<![A-Za-z0-9_.])(User|UserRoleAssignment)\(")
    class_def_pattern = re.compile(r"^\s*class\s+(User|UserRoleAssignment)\(")

    offenders = []
    for path in app_root.rglob("*.py"):
        if path in allowlist:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if class_def_pattern.match(line):
                continue
            if pattern.search(line):
                offenders.append(f"{path}: {line.strip()}")

    assert not offenders, "User(...)/UserRoleAssignment(...) constructed outside the allowlist:\n" + "\n".join(
        offenders
    )


# --------------------------------------------------------------------------- #
# AC-45: salesperson / portal_user are protected, empty, never default         #
# --------------------------------------------------------------------------- #
def test_ac45_salesperson_and_portal_user_protected_not_default_zero_permissions():
    from app.models.user import UserRolePermission

    with pg_session() as db:
        for slug in ("salesperson", "portal_user"):
            role = db.query(UserRole).filter(UserRole.slug == slug).first()
            assert role is not None, f"the real bootstrapped database must already seed {slug!r}"
            assert role.is_protected is True
            assert role.is_default is False
            perms = db.query(UserRolePermission).filter(UserRolePermission.role_id == role.id).count()
            assert perms == 0, f"{slug!r} must hold zero CRM permissions (Q8)"


def test_ac45_user_holding_only_salesperson_gets_403_on_get_users():
    from app.dependencies import get_current_user

    with pg_session() as db:
        role = db.query(UserRole).filter(UserRole.slug == "salesperson").first()
        assert role is not None, "the real bootstrapped database must already seed 'salesperson'"
        contact = _seed_contact(db)
        salesperson_user = _seed_user(db, name="ZZT Salesperson Only", email=None, phone=_msisdn(_phone()))
        salesperson_user.respond_contact_id = contact.id
        db.add(UserRoleAssignment(user_id=salesperson_user.id, role_id=role.id))
        db.commit()

        def _override_user():
            return {"id": salesperson_user.id, "email": None, "name": salesperson_user.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as client:
                resp = client.get("/api/v1/user-management/users/")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# AC-46: a contact's phone change never touches the linked user's phone        #
# --------------------------------------------------------------------------- #
def test_ac46_contact_phone_change_leaves_user_phone_unchanged_flags_differs_true(api_client):
    from app.schemas.user import RespondContactUpdate
    from app.services.contact_service import ContactService

    client, db, _admin = api_client
    original_phone = _msisdn(_phone())
    contact = _seed_contact(db, phone=original_phone)
    user = _seed_user(db, name="Drifted User", email=None, phone=original_phone)
    user.respond_contact_id = contact.id
    db.commit()

    ContactService(db).update_contact(contact.id, RespondContactUpdate(phone_number=_msisdn(_phone())))
    db.commit()

    db.refresh(user)
    assert user.contact_number == original_phone, "the linked user's phone must not move"

    resp = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("phone_differs_from_contact") is True


def test_ac46_use_new_number_clears_phone_verified_at_revokes_sessions_flag_goes_false(api_client):
    import datetime as _dt

    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    original_phone = _msisdn(_phone())
    contact = _seed_contact(db, phone=original_phone)
    user = _seed_user(db, name="Use New Number User", email=None, phone=original_phone)
    user.respond_contact_id = contact.id
    user.phone_verified_at = _dt.datetime.utcnow()
    db.commit()
    session_row = mint_session(db, user.id)

    new_phone = _msisdn(_phone())
    contact.phone_number = new_phone
    db.commit()

    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"contact_number": new_phone})
    assert resp.status_code == 200, resp.text

    db.refresh(user)
    assert user.contact_number == new_phone
    assert user.phone_verified_at is None

    db.refresh(session_row)
    assert session_row.revoked_at is not None, "every session must be revoked on a phone change"

    resp2 = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp2.json().get("phone_differs_from_contact") is False


# --------------------------------------------------------------------------- #
# AC-47: create-from-contact and link write an audited row naming the actor    #
# --------------------------------------------------------------------------- #
def test_ac47_create_from_contact_writes_audit_row_with_real_user_id_and_contact_name(real_session_client):
    from app.models.audit import AuditLog

    client, db, admin = real_session_client
    contact = _seed_contact(db, name="Audited Contact")
    db.commit()

    resp = client.post(
        "/api/v1/user-management/users/",
        json={"name": "Audited Person", "respond_contact_id": contact.id},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    row = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "user", AuditLog.entity_id == user_id, AuditLog.action == "UPDATE", AuditLog.description.ilike("%WhatsApp contact%"))
        .order_by(AuditLog.changed_at.desc())
        .first()
    )
    assert row is not None, "create from a contact must write a link_contact audit row"
    assert row.real_user_id == admin.id
    assert "Audited Contact" in (row.description or "")


def test_ac47_link_writes_audit_row_with_real_user_id_and_contact_name(real_session_client):
    from app.models.audit import AuditLog

    client, db, admin = real_session_client
    contact = _seed_contact(db, name="Link Audited Contact")
    target = _seed_user(db, name="Link Target")
    db.commit()

    resp = client.put(f"/api/v1/user-management/users/{target.id}", json={"respond_contact_id": contact.id})
    assert resp.status_code == 200, resp.text

    row = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "user", AuditLog.entity_id == target.id, AuditLog.action == "UPDATE", AuditLog.description.ilike("%WhatsApp contact%"))
        .order_by(AuditLog.changed_at.desc())
        .first()
    )
    assert row is not None
    assert row.real_user_id == admin.id
    assert "Link Audited Contact" in (row.description or "")


def test_ac47_unlink_writes_audit_row_with_real_user_id(real_session_client):
    from app.models.audit import AuditLog

    client, db, admin = real_session_client
    contact = _seed_contact(db, name="Unlink Audited Contact")
    target = _seed_user(db, name="Unlink Target")
    target.respond_contact_id = contact.id
    db.commit()

    resp = client.put(f"/api/v1/user-management/users/{target.id}", json={"respond_contact_id": None})
    assert resp.status_code == 200, resp.text

    row = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "user", AuditLog.entity_id == target.id, AuditLog.action == "UPDATE", AuditLog.description.ilike("Unlinked WhatsApp contact%"))
        .order_by(AuditLog.changed_at.desc())
        .first()
    )
    assert row is not None
    assert row.real_user_id == admin.id


# --------------------------------------------------------------------------- #
# AC-52 (BE part): the five read fields                                        #
# --------------------------------------------------------------------------- #
def test_ac52_get_user_and_get_me_carry_five_fields(api_client):
    import datetime as _dt

    from app.services.user_session_service import mint_session

    client, db, admin = api_client
    contact = _seed_contact(db, name="Sign In Contact")
    phone = _msisdn(_phone())
    user = _seed_user(db, name="Sign In User", email=None, phone=phone)
    user.respond_contact_id = contact.id
    user.phone_verified_at = _dt.datetime.utcnow()
    db.commit()
    mint_session(db, user.id, auth_method="phone_otp")

    resp = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for field in ("phone_verified_at", "linked_contact", "phone_differs_from_contact", "last_sign_in_method", "needs_invitation"):
        assert field in data, f"GET /users/{{id}} must carry {field!r}"
    assert data["linked_contact"]["id"] == contact.id
    assert data["last_sign_in_method"] == "phone_otp"

    from app.dependencies import get_current_user

    def _override_self():
        return {"id": user.id, "email": None, "name": user.name, "status": "ACTIVE"}

    app.dependency_overrides[get_current_user] = _override_self
    try:
        me = client.get("/api/v1/user-management/users/me")
    finally:
        from app.dependencies import get_current_user as _gcu

        app.dependency_overrides[_gcu] = lambda: {
            "id": admin.id,
            "email": admin.email,
            "name": admin.name,
            "status": "ACTIVE",
        }
    assert me.status_code == 200, me.text
    me_data = me.json()
    for field in ("phone_verified_at", "linked_contact", "phone_differs_from_contact", "last_sign_in_method", "needs_invitation"):
        assert field in me_data, f"GET /users/me must carry {field!r}"


def test_ac52_get_me_carries_real_sign_in_field_values_not_just_schema_defaults(api_client):
    """GET /users/me must actually merge `sign_in_summary` for the CALLER, not merely
    answer with UserResponse's own field defaults (None/False), which would make the
    `field in me_data` check in test_ac52_get_user_and_get_me_carry_five_fields pass
    even when get_current_user_profile never calls sign_in_summary at all."""
    import datetime as _dt

    from app.dependencies import get_current_user
    from app.services.user_session_service import mint_session

    client, db, admin = api_client
    contact = _seed_contact(db, name="Me Sign In Contact")
    phone = _msisdn(_phone())
    self_user = _seed_user(db, name="Self Sign In User", email=None, phone=phone)
    self_user.respond_contact_id = contact.id
    self_user.phone_verified_at = _dt.datetime.utcnow()
    db.commit()
    mint_session(db, self_user.id, auth_method="phone_otp")

    def _override_self():
        return {"id": self_user.id, "email": None, "name": self_user.name, "status": "ACTIVE"}

    app.dependency_overrides[get_current_user] = _override_self
    try:
        resp = client.get("/api/v1/user-management/users/me")
    finally:
        app.dependency_overrides[get_current_user] = lambda: {
            "id": admin.id,
            "email": admin.email,
            "name": admin.name,
            "status": "ACTIVE",
        }

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("linked_contact") is not None and data["linked_contact"]["id"] == contact.id, (
        "GET /users/me must carry the caller's real linked_contact, not the schema default"
    )
    assert data.get("last_sign_in_method") == "phone_otp", (
        "GET /users/me must carry the caller's real last_sign_in_method, not the schema default"
    )


def test_ac52_needs_invitation_true_then_false_after_resend_invite(api_client):
    client, db, _admin = api_client
    user = _seed_user(db, name="Never Invited", email=f"{unique_code('inv')}@x.com".lower())
    db.commit()

    resp = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp.json().get("needs_invitation") is True

    resend = client.post(f"/api/v1/user-management/users/{user.id}/resend-invite")
    assert resend.status_code == 200, resend.text

    resp2 = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp2.json().get("needs_invitation") is False


def test_ac52_last_sign_in_method_is_newest_session_method(api_client):
    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    user = _seed_user(db, name="Multi Session User")
    db.commit()
    mint_session(db, user.id, auth_method="password")
    mint_session(db, user.id, auth_method="portal_link")

    resp = client.get(f"/api/v1/user-management/users/{user.id}")
    assert resp.json().get("last_sign_in_method") == "portal_link"


# --------------------------------------------------------------------------- #
# AC-54: unlink / phone change revoke sessions; first link does not            #
# --------------------------------------------------------------------------- #
def test_ac54_put_unlink_revokes_every_session_and_writes_audit(api_client):
    from app.models.audit import AuditLog
    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    contact = _seed_contact(db, name="Unlink Session Contact")
    user = _seed_user(db, name="Unlink Session User")
    user.respond_contact_id = contact.id
    db.commit()
    session_row = mint_session(db, user.id)

    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"respond_contact_id": None})
    assert resp.status_code == 200, resp.text

    db.refresh(session_row)
    assert session_row.revoked_at is not None

    row = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "user", AuditLog.entity_id == user.id, AuditLog.action == "UPDATE", AuditLog.description.ilike("Unlinked WhatsApp contact%"))
        .first()
    )
    assert row is not None


def test_ac54_deferred_unlink_contact_executor_revokes_sessions_and_writes_audit():
    from app.models.audit import AuditLog
    from app.services.form_action_registry import get_action
    from app.services.user_session_service import mint_session

    with blank_session() as db:
        contact = _seed_contact(db, name="Deferred Unlink Contact")
        user = _seed_user(db, name="Deferred Unlink User")
        user.respond_contact_id = contact.id
        db.commit()
        session_row = mint_session(db, user.id)

        action = get_action("user.unlink_contact")
        assert action is not None, "user.unlink_contact must be registered"
        action.execute(db, {"entity_id": user.id})
        db.commit()

        db.refresh(user)
        assert user.respond_contact_id is None
        db.refresh(session_row)
        assert session_row.revoked_at is not None

        row = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "user", AuditLog.entity_id == user.id, AuditLog.action == "UPDATE", AuditLog.description.ilike("Unlinked WhatsApp contact%"))
            .first()
        )
        assert row is not None


def test_ac54_deferred_unlink_names_the_requester_not_the_ambient_actor():
    """The executor runs from whichever request or sweep commits the deferred
    window, not from the click that started it - the audit row must still name
    the OWNER who asked for the unlink (`requested_by_id` on the payload), even
    when a different actor (or none) is stamped at commit time."""
    from app.audit_context import AuditActor, clear_actor, stamp_actor
    from app.models.audit import AuditLog
    from app.services.form_action_registry import get_action

    with blank_session() as db:
        contact = _seed_contact(db, name="Requester Attribution Contact")
        user = _seed_user(db, name="Requester Attribution User")
        user.respond_contact_id = contact.id
        requester = _seed_admin_user(db)
        db.commit()

        # A DIFFERENT actor is ambiently stamped - the sweep/commit's own
        # principal, never the person who parked the action.
        stamp_actor(AuditActor(actor_type="worker", user_id=None, real_user_id=None), db=db)
        try:
            action = get_action("user.unlink_contact")
            assert action is not None
            action.execute(db, {"entity_id": user.id, "requested_by_id": requester.id})
            db.commit()
        finally:
            clear_actor(db)

        row = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == user.id,
                AuditLog.action == "UPDATE",
                AuditLog.description.ilike("Unlinked WhatsApp contact%"),
            )
            .first()
        )
        assert row is not None
        assert row.user_id == requester.id, "the row must name the requester, not the ambient actor"
        assert row.real_user_id == requester.id


def test_ac54_phone_change_revokes_sessions(api_client):
    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    original_phone = _msisdn(_phone())
    user = _seed_user(db, name="Phone Change Sessions User", email=None, phone=original_phone)
    db.commit()
    session_row = mint_session(db, user.id)

    new_phone = _msisdn(_phone())
    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"contact_number": new_phone})
    assert resp.status_code == 200, resp.text

    db.refresh(session_row)
    assert session_row.revoked_at is not None


def test_ac54_first_link_does_not_revoke_sessions(api_client):
    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    contact = _seed_contact(db, name="First Link Contact")
    user = _seed_user(db, name="First Link User")
    db.commit()
    session_row = mint_session(db, user.id)

    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"respond_contact_id": contact.id})
    assert resp.status_code == 200, resp.text

    db.refresh(session_row)
    assert session_row.revoked_at is None, "a FIRST link (owner ruling) must not end the session"


def test_ac54_clearing_phone_to_null_revokes_sessions_and_clears_verified_at(api_client):
    import datetime as _dt

    from app.services.user_session_service import mint_session

    client, db, _admin = api_client
    phone = _msisdn(_phone())
    user = _seed_user(db, name="Clear Phone User", email=f"{unique_code('clr')}@x.com".lower(), phone=phone)
    user.phone_verified_at = _dt.datetime.utcnow()
    db.commit()
    session_row = mint_session(db, user.id)

    resp = client.put(f"/api/v1/user-management/users/{user.id}", json={"contact_number": None})
    assert resp.status_code == 200, resp.text

    db.refresh(user)
    assert user.contact_number is None
    assert user.phone_verified_at is None

    db.refresh(session_row)
    assert session_row.revoked_at is not None, "clearing the phone must revoke every session"


# --------------------------------------------------------------------------- #
# AC-55: permission gating, no new permission added                           #
# --------------------------------------------------------------------------- #
def _seed_no_perm_user(db: Session, *, slug: str) -> User:
    role = _seed_role(db, slug=slug)
    user = _seed_user(db, name=f"No Perm {slug}")
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.commit()
    return user


def test_ac55_post_users_without_users_add_is_403():
    from app.dependencies import get_current_user

    with blank_session() as db:
        weak_user = _seed_no_perm_user(db, slug=unique_code("norole").lower())

        def _override_user():
            return {"id": weak_user.id, "email": weak_user.email, "name": weak_user.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/user-management/users/",
                    json={"email": f"{unique_code('nope')}@x.com".lower(), "name": "Nope"},
                )
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 403, resp.text


def test_ac55_put_link_without_users_edit_is_403():
    from app.dependencies import get_current_user

    with blank_session() as db:
        weak_user = _seed_no_perm_user(db, slug=unique_code("norole2").lower())
        contact = _seed_contact(db)
        target = _seed_user(db, name="Edit Denied Target")
        db.commit()

        def _override_user():
            return {"id": weak_user.id, "email": weak_user.email, "name": weak_user.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as client:
                resp = client.put(
                    f"/api/v1/user-management/users/{target.id}",
                    json={"respond_contact_id": contact.id},
                )
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 403, resp.text


def test_ac55_resend_invite_without_users_edit_is_403():
    from app.dependencies import get_current_user

    with blank_session() as db:
        weak_user = _seed_no_perm_user(db, slug=unique_code("norole3").lower())
        target = _seed_user(db, name="Resend Denied Target", email=f"{unique_code('rd')}@x.com".lower())
        db.commit()

        def _override_user():
            return {"id": weak_user.id, "email": weak_user.email, "name": weak_user.name, "status": "ACTIVE"}

        def _override_db():
            yield db

        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as client:
                resp = client.post(f"/api/v1/user-management/users/{target.id}/resend-invite")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 403, resp.text


def test_ac55_user_unlink_contact_registered_with_users_edit_permission():
    from app.services.form_action_registry import get_action

    action = get_action("user.unlink_contact")
    assert action is not None, "user.unlink_contact must be registered"
    assert action.permission == "user_management.users.edit"
    assert action.entity_types == ("user",)


def test_ac55_contact_list_and_detail_carry_linked_user_only_with_users_view(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db, name="Gated Contact")
    linked_user = _seed_user(db, name="Gated Linked User")
    linked_user.respond_contact_id = contact.id
    db.commit()

    # With the admin (who holds users.view), the linked user must be visible.
    resp_list = client.get("/api/v1/user-management/contacts/")
    assert resp_list.status_code == 200, resp_list.text
    rows = resp_list.json().get("data", [])
    row = next((r for r in rows if r.get("id") == contact.id), None)
    assert row is not None, "seeded contact must appear in the list"
    assert row.get("linked_user_id") == linked_user.id

    resp_detail = client.get(f"/api/v1/user-management/contacts/{contact.id}")
    assert resp_detail.status_code == 200, resp_detail.text
    linked = resp_detail.json().get("linked_user")
    assert linked is not None and linked.get("id") == linked_user.id

    # Without users.view (only contacts.view), both must be null/absent.
    from app.dependencies import get_current_user

    no_users_view_user = _seed_no_perm_user(db, slug=unique_code("contactsonly").lower())
    from app.models.user import UserPermission, UserRolePermission

    contacts_view_perm = UserPermission(
        id=str(uuid.uuid4()), slug="user_management.contacts.view", name="View contacts"
    )
    db.add(contacts_view_perm)
    db.flush()
    role_id = db.query(UserRoleAssignment.role_id).filter(UserRoleAssignment.user_id == no_users_view_user.id).scalar()
    db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=contacts_view_perm.id))
    db.commit()

    def _override_user():
        return {"id": no_users_view_user.id, "email": no_users_view_user.email, "name": no_users_view_user.name, "status": "ACTIVE"}

    app.dependency_overrides[get_current_user] = _override_user
    try:
        resp_list2 = client.get("/api/v1/user-management/contacts/")
        assert resp_list2.status_code == 200, resp_list2.text
        rows2 = resp_list2.json().get("data", [])
        row2 = next((r for r in rows2 if r.get("id") == contact.id), None)
        assert row2 is not None
        assert not row2.get("linked_user_id")

        resp_detail2 = client.get(f"/api/v1/user-management/contacts/{contact.id}")
        assert resp_detail2.status_code == 200, resp_detail2.text
        assert not resp_detail2.json().get("linked_user")
    finally:
        app.dependency_overrides[get_current_user] = lambda: {"id": _admin.id, "email": _admin.email, "name": _admin.name, "status": "ACTIVE"}


# --------------------------------------------------------------------------- #
# Select filters: phone, respond_contact_id, unlinked                         #
# --------------------------------------------------------------------------- #
def test_select_filter_phone_returns_the_right_user(api_client):
    client, db, _admin = api_client
    phone = _msisdn(_phone())
    target = _seed_user(db, name="Findable By Phone", email=None, phone=phone)
    _other = _seed_user(db, name="Not Findable By Phone")
    db.commit()

    resp = client.get("/api/v1/user-management/users/select", params={"phone": phone})
    assert resp.status_code == 200, resp.text
    ids = {u["id"] for u in resp.json()}
    assert ids == {target.id}


def test_select_filter_phone_junk_returns_empty_not_every_phoneless_user(api_client):
    client, db, _admin = api_client
    # A phone-less user: `normalize_msisdn("junk")` is None, and
    # `User.contact_number == None` would otherwise compile to `IS NULL` and
    # match this row, answering "found" for garbage input.
    _phoneless = _seed_user(db, name="No Phone At All")
    db.commit()

    resp = client.get("/api/v1/user-management/users/select", params={"phone": "junk"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_select_filter_respond_contact_id_returns_the_right_user(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db)
    target = _seed_user(db, name="Findable By Contact")
    target.respond_contact_id = contact.id
    _other = _seed_user(db, name="Not Findable By Contact")
    db.commit()

    resp = client.get("/api/v1/user-management/users/select", params={"respond_contact_id": contact.id})
    assert resp.status_code == 200, resp.text
    ids = {u["id"] for u in resp.json()}
    assert ids == {target.id}


def test_select_filter_unlinked_true_returns_only_users_with_no_contact(api_client):
    client, db, _admin = api_client
    contact = _seed_contact(db)
    linked = _seed_user(db, name="Linked For Unlinked Filter")
    linked.respond_contact_id = contact.id
    unlinked = _seed_user(db, name="Unlinked For Unlinked Filter")
    db.commit()

    resp = client.get("/api/v1/user-management/users/select", params={"unlinked": "true"})
    assert resp.status_code == 200, resp.text
    ids = {u["id"] for u in resp.json()}
    assert unlinked.id in ids
    assert linked.id not in ids


# --------------------------------------------------------------------------- #
# Fix round 2, S3: the contact's linked_user says whether the WhatsApp code    #
# can reach that user (its phone equals the contact's)                         #
# --------------------------------------------------------------------------- #
def test_s3_contact_detail_linked_user_flags_phone_differs_from_contact(api_client):
    client, db, _admin = api_client
    phone = _msisdn(_phone())

    same = _seed_contact(db, phone=phone, name="Same Phone Contact")
    same_user = _seed_user(db, name="Same Phone User", phone=phone)
    same_user.respond_contact_id = same.id

    other = _seed_contact(db, name="Other Phone Contact")
    other_user = _seed_user(db, name="Other Phone User", phone=_msisdn(_phone()))
    other_user.respond_contact_id = other.id

    none = _seed_contact(db, name="No Phone Contact")
    none_user = _seed_user(db, name="No Phone User", email=f"{unique_code('np')}@x.com".lower())
    none_user.respond_contact_id = none.id
    db.commit()

    def _flag(contact_id: str):
        resp = client.get(f"/api/v1/user-management/contacts/{contact_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()["linked_user"].get("phone_differs_from_contact")

    assert _flag(same.id) is False
    assert _flag(other.id) is True
    assert _flag(none.id) is True
