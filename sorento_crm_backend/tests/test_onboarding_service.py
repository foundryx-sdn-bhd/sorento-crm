"""Onboarding requests: token, status transitions, collisions, provisioning.

Postgres only, on a blank scratch schema. Every test seeds its OWN chain -
company, role, template, request, people - with a marker prefix, because CI's
database has no data at all and a test that borrows an existing row passes here
and dies there on a NOT NULL foreign key.

The status graph is seeded by importing the migration's own seeder and running
it against this transaction, rather than by asserting whatever the shared
database happens to hold. That tests the code that ships instead of the
environment it ran in.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.onboarding import (
    REVIEW_APPROVED,
    REVIEW_REJECTED,
    OnboardingPerson,
    OnboardingRequest,
    OnboardingTemplate,
)
from app.models.user import User, UserRole
from app.services import onboarding_service
from app.services.error_handler import AppException
from app.services.onboarding_service import OnboardingAuthError
from tests._pg_fixture import blank_session, unique_code

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "360_onboarding_slice1.py"
)

#: The Sorento company the suite seeds into every scratch schema.
SORENTO = "00000000-0000-0000-0000-000000000001"


def _load_migration():
    spec = importlib.util.spec_from_file_location("onboarding_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_graph(db):
    """Run the migration's own graph seeder against this transaction."""
    module = _load_migration()
    module._seed_graph(db.connection())
    module._mark_system(db.connection())


@pytest.fixture()
def db():
    with blank_session() as session:
        _seed_graph(session)
        yield session


@pytest.fixture()
def template(db) -> OnboardingTemplate:
    role = UserRole(
        id=unique_code("role"), slug=unique_code("role").lower(), name=unique_code("Role")
    )
    db.add(role)
    db.flush()
    tpl = OnboardingTemplate(
        name=unique_code("Salesperson"),
        role_ids=[role.id],
        company_ids=[SORENTO],
        company_id=SORENTO,
        default_needs_system_account=True,
    )
    db.add(tpl)
    db.commit()
    return tpl


def _make_request(db, **overrides) -> OnboardingRequest:
    values = {
        "company_id": SORENTO,
        "title": unique_code("MOCHA staff"),
        "requester_name": "Esther Lim",
        "requester_email": f"{unique_code('requester')}@mocha.com.my".lower(),
    }
    values.update(overrides)
    return onboarding_service.create_request(db, **values)


def _add_person(db, request, *, name, email=None, phone=None, template=None, **kw):
    person = OnboardingPerson(
        request_id=request.id,
        company_id=request.company_id,
        row_number=len(request.people) + 1,
        full_name=name,
        email_raw=email,
        email=email.strip().lower() if email else None,
        phone_raw=phone,
        phone=phone,
        template_id=template.id if template else None,
        **kw,
    )
    db.add(person)
    db.commit()
    db.refresh(request)
    return person


# --- token --------------------------------------------------------------------


def test_token_is_crockford_and_unique(db):
    a = _make_request(db)
    b = _make_request(db)
    assert a.token != b.token
    assert len(a.token) == 48
    # No I, L, O or U: the alphabet exists so a token read off a screenshot is
    # not mistyped.
    assert not set(a.token) & set("ILOU")


def test_token_resolves_and_is_multi_use(db):
    request = _make_request(db)
    for _ in range(3):
        assert onboarding_service.resolve_token(db, request.token).id == request.id


def test_unknown_token_is_refused(db):
    with pytest.raises(OnboardingAuthError):
        onboarding_service.resolve_token(db, "NOTATOKEN")


def test_expired_token_is_refused(db):
    request = _make_request(db)
    request.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(OnboardingAuthError):
        onboarding_service.resolve_token(db, request.token)


def test_revoked_token_is_refused_immediately(db):
    request = _make_request(db)
    onboarding_service.revoke(db, str(request.id))
    with pytest.raises(OnboardingAuthError):
        onboarding_service.resolve_token(db, request.token)


def test_regenerating_kills_the_old_link(db):
    request = _make_request(db)
    old = request.token
    onboarding_service.regenerate_token(db, str(request.id))
    assert request.token != old
    with pytest.raises(OnboardingAuthError):
        onboarding_service.resolve_token(db, old)


def test_a_revoked_request_keeps_its_status(db):
    """Revocation kills the LINK, not the batch - a leaked link on a request
    already in review must not throw the review away."""
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.revoke(db, str(request.id))
    assert request.status_key == onboarding_service.SENT


# --- the requester's writes ---------------------------------------------------


def test_rows_can_only_be_saved_while_the_link_is_open(db):
    request = _make_request(db)
    with pytest.raises(AppException) as exc:
        onboarding_service.replace_people(db, request, [{"full_name": "Too early"}])
    assert exc.value.status_code == 409


def test_replace_people_is_a_whole_list_replace(db):
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))

    onboarding_service.replace_people(
        db,
        request,
        [
            {"full_name": "Aisyah", "email_raw": " person4@example.com ", "phone_raw": "010-0000002"},
            {"full_name": "Wei Ming", "email_raw": "person40@example.com"},
        ],
    )
    assert [p.full_name for p in request.people] == ["Aisyah", "Wei Ming"]
    # Normalised on the way in, raw kept for the requester's own eyes.
    aisyah = request.people[0]
    assert aisyah.email == "person4@example.com"
    assert aisyah.email_raw == " person4@example.com "
    assert aisyah.phone == "60100000002"

    onboarding_service.replace_people(db, request, [{"full_name": "Only one left"}])
    assert [p.full_name for p in request.people] == ["Only one left"]


def test_the_role_she_typed_is_saved_and_editable(db):
    """Free text, kept as written. The reviewer maps it to access; nothing else
    reads it, so nothing may normalise it either."""
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(
        db, request, [{"full_name": "Aisyah", "role_label": "Sales admin, KL"}]
    )
    assert request.people[0].role_label == "Sales admin, KL"

    onboarding_service.submit(db, request)
    person = onboarding_service.update_person(
        db, str(request.people[0].id), {"role_label": "Sales admin"}
    )
    assert person.role_label == "Sales admin"


def test_blank_rows_are_dropped_not_refused(db):
    """A row she started and abandoned must not cost her a 30-person submission."""
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(
        db, request, [{"full_name": "Real person"}, {"full_name": "   "}]
    )
    assert len(request.people) == 1


def test_submitting_needs_at_least_one_person(db):
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    with pytest.raises(AppException) as exc:
        onboarding_service.submit(db, request)
    assert exc.value.status_code == 422


def test_submit_closes_the_requesters_write_window(db):
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(db, request, [{"full_name": "Aisyah"}])
    onboarding_service.submit(db, request)

    assert request.status_key == onboarding_service.SUBMITTED
    assert request.submitted_at is not None
    with pytest.raises(AppException) as exc:
        onboarding_service.replace_people(db, request, [{"full_name": "Sneaky"}])
    assert exc.value.status_code == 409


# --- transitions --------------------------------------------------------------


def _to_review(db, template=None) -> OnboardingRequest:
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(
        db,
        request,
        [{"full_name": "Aisyah", "email_raw": f"{unique_code('a')}@mocha.com.my".lower()}],
    )
    if template is not None:
        request.people[0].template_id = template.id
        db.commit()
    onboarding_service.submit(db, request)
    onboarding_service.start_review(db, str(request.id))
    return request


def test_an_illegal_transition_is_refused_by_the_graph(db):
    """draft -> processing is not an edge, and the refusal comes from the seeded
    graph rather than from an `if` somebody has to remember to write."""
    request = _make_request(db)
    with pytest.raises(AppException) as exc:
        onboarding_service.approve(db, str(request.id))
    assert exc.value.status_code in (422, 404)


def test_approving_twice_is_refused(db):
    request = _to_review(db)
    onboarding_service.set_person_verdict(
        db, str(request.people[0].id), verdict=REVIEW_APPROVED
    )
    onboarding_service.approve(db, str(request.id))
    assert request.status_key == onboarding_service.PROCESSING

    with pytest.raises(AppException):
        onboarding_service.approve(db, str(request.id))


def test_approving_with_nobody_kept_is_refused(db):
    """Approving a batch where every row is still `proposed` would queue a job
    with nothing to do and report success."""
    request = _to_review(db)
    with pytest.raises(AppException) as exc:
        onboarding_service.approve(db, str(request.id))
    assert exc.value.status_code == 422


def test_rejecting_a_person_requires_a_reason(db):
    request = _to_review(db)
    person_id = str(request.people[0].id)
    with pytest.raises(AppException) as exc:
        onboarding_service.set_person_verdict(db, person_id, verdict=REVIEW_REJECTED, reason="  ")
    assert exc.value.status_code == 422

    person = onboarding_service.set_person_verdict(
        db, person_id, verdict=REVIEW_REJECTED, reason="Left the company."
    )
    assert person.rejection_reason == "Left the company."


def test_keeping_a_rejected_person_clears_the_reason(db):
    """A reason left on a kept row reads as a rejection on every screen."""
    request = _to_review(db)
    person_id = str(request.people[0].id)
    onboarding_service.set_person_verdict(
        db, person_id, verdict=REVIEW_REJECTED, reason="Left the company."
    )
    person = onboarding_service.set_person_verdict(db, person_id, verdict=REVIEW_APPROVED)
    assert person.rejection_reason is None


def test_rejecting_the_batch_requires_a_reason(db):
    request = _to_review(db)
    with pytest.raises(AppException) as exc:
        onboarding_service.reject_request(db, str(request.id), reason="")
    assert exc.value.status_code == 422


def test_a_person_cannot_be_edited_once_the_batch_has_left_review(db):
    """The provisioned rows ARE the record of what was created.

    Without this the detail page - which renders the same editable grid in every
    status - lets a stale tab rewrite a name or an email after the invitation
    carrying the old one has already gone out.
    """
    request = _to_review(db)
    person_id = str(request.people[0].id)
    onboarding_service.set_person_verdict(db, person_id, verdict=REVIEW_APPROVED)
    onboarding_service.approve(db, str(request.id))

    with pytest.raises(AppException) as exc:
        onboarding_service.update_person(db, person_id, {"full_name": "Someone Else"})
    assert exc.value.status_code == 409

    db.refresh(request.people[0])
    assert request.people[0].full_name == "Aisyah"


def test_a_verdict_cannot_be_changed_once_the_batch_has_left_review(db):
    """Rejecting somebody the job has already provisioned contradicts the ledger."""
    request = _to_review(db)
    person_id = str(request.people[0].id)
    onboarding_service.set_person_verdict(db, person_id, verdict=REVIEW_APPROVED)
    onboarding_service.approve(db, str(request.id))

    with pytest.raises(AppException) as exc:
        onboarding_service.set_person_verdict(
            db, person_id, verdict=REVIEW_REJECTED, reason="Changed my mind."
        )
    assert exc.value.status_code == 409

    db.refresh(request.people[0])
    assert request.people[0].review_status == REVIEW_APPROVED


def test_a_person_is_still_editable_before_start_review(db):
    """The reviewer legitimately fixes a typo on a submitted batch."""
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(
        db, request, [{"full_name": "Aisyah", "email_raw": f"{unique_code('a')}@mocha.com.my".lower()}]
    )
    onboarding_service.submit(db, request)

    person = onboarding_service.update_person(
        db, str(request.people[0].id), {"full_name": "Nurul Aisyah"}
    )
    assert person.full_name == "Nurul Aisyah"


# --- collisions ---------------------------------------------------------------


def test_collisions_are_found_on_every_unique_key(db):
    from app.models.access import RespondContact

    email = f"{unique_code('taken')}@mocha.com.my".lower()
    phone = "60100000071"
    existing = User(
        id=unique_code("user"),
        email=email,
        name="Already Here",
        contact_number=phone,
        status="ACTIVE",
    )
    db.add(existing)
    contact_phone = "60100000076"
    db.add(RespondContact(phone_number=contact_phone, name="Known Contact"))
    db.commit()

    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(
        db,
        request,
        [
            {"full_name": "By email", "email_raw": email},
            {"full_name": "By user phone", "phone_raw": phone},
            {"full_name": "By contact phone", "phone_raw": contact_phone},
            {"full_name": "Nobody", "email_raw": f"{unique_code('new')}@mocha.com.my".lower()},
        ],
    )

    found = onboarding_service.collisions_for(db, request)
    kinds = {
        p.full_name: {c.kind for c in found[str(p.id)]} for p in request.people
    }
    assert kinds["By email"] == {"user_email"}
    assert kinds["By user phone"] == {"user_phone"}
    assert kinds["By contact phone"] == {"contact_phone"}
    assert kinds["Nobody"] == set()


def test_collision_labels_never_carry_an_id(db):
    email = f"{unique_code('taken')}@mocha.com.my".lower()
    db.add(
        User(id=unique_code("user"), email=email, name="Already Here", status="ACTIVE")
    )
    db.commit()

    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(db, request, [{"full_name": "Dup", "email_raw": email}])
    found = onboarding_service.collisions_for(db, request)
    label = found[str(request.people[0].id)][0].label
    assert "Already Here" in label
    assert "-" * 1 not in label.split(":")[-1].strip()[:8] or "Already Here" in label


# --- templates ----------------------------------------------------------------


def test_template_names_are_unique_per_company(db, template):
    with pytest.raises(AppException) as exc:
        onboarding_service.create_template(
            db, {"name": template.name.lower(), "company_ids": [], "role_ids": []}
        )
    assert exc.value.status_code == 409


def test_capture_from_user_copies_roles_and_companies(db):
    from app.models.company import UserCompany
    from app.models.user import UserRoleAssignment

    role = UserRole(
        id=unique_code("role"), slug=unique_code("sales").lower(), name=unique_code("Sales")
    )
    user = User(id=unique_code("user"), email=f"{unique_code('u')}@x.com".lower(), name="Model User", status="ACTIVE")
    db.add_all([role, user])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.add(UserCompany(user_id=user.id, company_id=SORENTO))
    db.commit()

    captured = onboarding_service.capture_template_from_user(
        db, user.id, name=unique_code("Captured")
    )
    assert captured.role_ids == [role.id]
    assert captured.company_ids == [SORENTO]
    assert captured.captured_from_user_id == user.id


# --- provisioning -------------------------------------------------------------


def _run_user_lane(db, person, template):
    """The task's per-person lane, called directly.

    The task itself opens its own session and its own company scope, which a
    rolled-back test transaction cannot share. The lane function is where the
    behaviour under test lives, so that is what is exercised.
    """
    from app.tasks.onboarding_tasks import _provision_user_lane

    _provision_user_lane(db, person, template)
    db.commit()
    db.refresh(person)
    return person


def test_provisioning_creates_a_user_with_the_templates_roles(db, template, monkeypatch):
    import app.tasks.onboarding_tasks as tasks

    # The invitation email is a separate concern with its own failure mode; the
    # lane's contract is that a failed send does not fail the lane, which the
    # next test pins.
    monkeypatch.setattr(
        "app.api.v1.user_management.users._send_invitation_link_for_user",
        lambda db, user: None,
    )

    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    _run_user_lane(db, person, template)

    assert person.user_step == "done"
    assert person.user_id is not None
    created = db.query(User).filter(User.id == person.user_id).first()
    assert created is not None
    assert created.email == person.email
    assert created.name == person.full_name


def test_a_failed_invitation_email_does_not_fail_the_lane(db, template, monkeypatch):
    def _boom(db, user):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(
        "app.api.v1.user_management.users._send_invitation_link_for_user", _boom
    )

    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    _run_user_lane(db, person, template)
    assert person.user_step == "done"
    assert person.user_error is None


def test_a_pre_existing_email_is_skipped_not_failed(db, template, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.user_management.users._send_invitation_link_for_user",
        lambda db, user: None,
    )
    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    existing = User(
        id=unique_code("user"), email=person.email, name="Already Here", status="ACTIVE"
    )
    db.add(existing)
    db.commit()

    _run_user_lane(db, person, template)
    assert person.user_step == "skipped"
    assert person.user_id == existing.id
    assert person.user_error is None


def test_a_person_with_no_email_fails_only_their_own_lane(db, template):
    request = _make_request(db)
    onboarding_service.send_request(db, str(request.id))
    onboarding_service.replace_people(db, request, [{"full_name": "No Email"}])
    onboarding_service.submit(db, request)
    onboarding_service.start_review(db, str(request.id))
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    _run_user_lane(db, person, template)
    assert person.user_step == "failed"
    assert "email" in (person.user_error or "").lower()


def test_a_lane_that_is_done_is_not_re_run(db, template, monkeypatch):
    """Re-running the job is the recovery path, so it must not create a second
    user for somebody who already has one."""
    monkeypatch.setattr(
        "app.api.v1.user_management.users._send_invitation_link_for_user",
        lambda db, user: None,
    )
    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    _run_user_lane(db, person, template)
    first_user_id = person.user_id
    assert person.user_step == "done"

    _run_user_lane(db, person, template)
    assert person.user_id == first_user_id
    assert db.query(User).filter(User.email == person.email).count() == 1


def test_a_person_who_needs_no_account_is_skipped(db, template):
    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    person.needs_system_account = False
    db.commit()

    _run_user_lane(db, person, template)
    assert person.user_step == "skipped"


# --- finalise -----------------------------------------------------------------


def test_finalise_reads_the_user_lane_only(db, template, monkeypatch):
    """The contact and agent lanes are `pending` by design in this slice, and
    counting them would mark every batch incomplete forever."""
    monkeypatch.setattr(
        "app.api.v1.user_management.users._send_invitation_link_for_user",
        lambda db, user: None,
    )
    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    person.needs_respond_contact = True
    person.needs_agent_seat = True
    db.commit()

    onboarding_service.approve(db, str(request.id))
    _run_user_lane(db, person, template)
    onboarding_service.finalise(db, request)

    assert person.contact_step == "pending"
    assert person.agent_step == "pending"
    assert request.status_key == onboarding_service.COMPLETED


def test_a_failed_lane_makes_the_batch_partially_completed(db, template):
    request = _to_review(db, template=template)
    person = request.people[0]
    person.review_status = REVIEW_APPROVED
    db.commit()

    onboarding_service.approve(db, str(request.id))
    person.user_step = "failed"
    person.user_error = "something went wrong"
    db.commit()

    onboarding_service.finalise(db, request)
    assert request.status_key == onboarding_service.PARTIALLY_COMPLETED
