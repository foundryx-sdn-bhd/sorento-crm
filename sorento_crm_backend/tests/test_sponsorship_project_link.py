"""S4 sponsorship-to-project link (UAC Group F, AC-F3 to AC-F7).

One form, not two (AC-F3): `purchase_requests` gains a nullable `project_id`, and every
existing sponsorship keeps working untouched.

The rollout is per CONTACT (AC-F4), which is the point of the whole design: Sorento wants
to require a registered project from the salespeople they have trained, without breaking
the form for everybody else on the same day. So the rules under test are:

- an UNFLAGGED contact submits exactly as before, with or without a project;
- a FLAGGED contact cannot submit without one (AC-F5, hard block, deliberately chosen
  over an inline registration modal and over a free-text fallback);
- a project belonging to a company the contact is NOT linked to is refused, because the
  picker only offers the ones they are (AC-F4a) and the server cannot trust the client;
- the block lives in the SERVICE, not in the browser: the portal FE is not a trust
  boundary.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.access import RespondContact
from app.models.company import RespondContactCompany
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-sponsor"


def _uid() -> str:
    return str(uuid.uuid4())


def _message(exc: AppException) -> str:
    """AppException stuffs the message into HTTPException.detail as a dict."""
    detail = exc.detail
    return (detail or {}).get("message", "") if isinstance(detail, dict) else str(detail)


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _contact(db, *, requires_project: bool, company_ids=()) -> RespondContact:
    contact = RespondContact(
        id=_uid(),
        phone_number=f"+6011{_uid()[:8]}",
        name=f"{MARKER} contact",
        session_vars={},
        requires_registered_project=requires_project,
    )
    db.add(contact)
    db.flush()
    for company_id in company_ids:
        db.add(
            RespondContactCompany(
                id=_uid(), respond_contact_id=contact.id, company_id=company_id
            )
        )
    db.flush()
    return contact


def _token(contact):
    """`_apply_payload` reads only `contact_id` off the token."""
    from datetime import datetime, timedelta

    from app.models.portal import PortalToken

    return PortalToken(
        id=_uid(),
        token=_uid(),
        contact_id=contact.id,
        space_id="zzt-space",
        expires_at=datetime.utcnow() + timedelta(days=1),
    )


def _project(db, company_id: str, owner: str, title=None):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=title or f"{MARKER} Tower {_uid()[:6]}",
    )


def _second_company(db) -> str:
    """MOCHA if it is present, otherwise one created for the test."""
    existing = db.execute(text("select id from companies where code = 'MOC'")).scalar()
    if existing:
        return str(existing)
    company_id = _uid()
    db.execute(
        text(
            "insert into companies (id, code, name, is_active) "
            "values (:i, :c, :n, true)"
        ),
        {"i": company_id, "c": f"Z{_uid()[:3]}", "n": f"{MARKER} other co"},
    )
    db.flush()
    return company_id


# ------------------------------------------------------------------- the block


def test_an_unflagged_contact_submits_without_a_project(seeded_db=None):
    """AC-F4: today's behaviour, unchanged. This is the test that would catch a rollout
    accidentally applied to everybody."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        contact = _contact(db, requires_project=False, company_ids=[company_id])

        # No exception, and nothing is invented on the row.
        links.assert_project_requirement(db, contact=contact, project_id=None)


def test_a_flagged_contact_cannot_submit_without_a_project(seeded_db=None):
    """AC-F5, the hard block. The message has to tell them what to do, because the answer
    is "go and register it on the web", which is not guessable."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        contact = _contact(db, requires_project=True, company_ids=[company_id])

        with pytest.raises(AppException) as excinfo:
            links.assert_project_requirement(db, contact=contact, project_id=None)

        assert excinfo.value.status_code == 422
        assert "register" in _message(excinfo.value).lower()


def test_a_flagged_contact_submits_with_one_of_their_own_projects(seeded_db=None):
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)
        contact = _contact(db, requires_project=True, company_ids=[company_id])

        links.assert_project_requirement(db, contact=contact, project_id=project.id)


def test_a_project_from_a_company_the_contact_is_not_linked_to_is_refused(seeded_db=None):
    """AC-F4a decides what the picker OFFERS; this decides what the server ACCEPTS. The
    two have to agree, and only the second one is a boundary."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        other_company = _second_company(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)
        # Linked to the OTHER company only.
        contact = _contact(db, requires_project=True, company_ids=[other_company])

        with pytest.raises(AppException) as excinfo:
            links.assert_project_requirement(db, contact=contact, project_id=project.id)

        assert excinfo.value.status_code == 422


def test_an_unflagged_contact_naming_a_foreign_project_is_still_refused(seeded_db=None):
    """The flag decides whether a project is REQUIRED, never whether the link may be
    wrong. A sponsorship attached to somebody else's project corrupts that project's
    spend rollup, flagged or not."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        other_company = _second_company(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)
        contact = _contact(db, requires_project=False, company_ids=[other_company])

        with pytest.raises(AppException):
            links.assert_project_requirement(db, contact=contact, project_id=project.id)


def test_a_contact_linked_to_no_company_at_all_is_told_that_specifically(seeded_db=None):
    """Otherwise the user reads "that project is not yours" and goes looking for a project
    problem, when the real fix is an admin linking their company."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)
        contact = _contact(db, requires_project=True, company_ids=[])

        with pytest.raises(AppException) as excinfo:
            links.assert_project_requirement(db, contact=contact, project_id=project.id)
        assert "compan" in _message(excinfo.value).lower()


# ------------------------------------------------------------------ the picker


def test_the_picker_lists_only_the_contacts_companies_projects_with_the_company_named():
    """AC-F4a. The company is on every row because a contact mapped to two of them cannot
    otherwise tell two similarly-named phases apart."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        other_company = _second_company(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        mine = _project(db, company_id, owner, title=f"{MARKER} Mine")
        from app.models.base import company_scope

        with company_scope(db, frozenset({other_company})):
            theirs = _project(db, other_company, owner, title=f"{MARKER} Theirs")
        contact = _contact(db, requires_project=True, company_ids=[company_id])

        rows = links.projects_for_contact(db, contact=contact)

        ids = {row["id"] for row in rows}
        assert mine.id in ids
        assert theirs.id not in ids
        assert rows[0]["company_name"]
        assert rows[0]["project_code"].startswith("PRJ-")


def test_the_picker_spans_every_company_the_contact_is_linked_to():
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        other_company = _second_company(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        from app.models.base import company_scope

        mine = _project(db, company_id, owner, title=f"{MARKER} Mine")
        with company_scope(db, frozenset({other_company})):
            theirs = _project(db, other_company, owner, title=f"{MARKER} Theirs")
        contact = _contact(db, requires_project=True, company_ids=[company_id, other_company])

        ids = {row["id"] for row in links.projects_for_contact(db, contact=contact)}
        assert {mine.id, theirs.id} <= ids


# ------------------------------------------------------------------ the rollup


def test_sponsorship_spend_rolls_up_per_project_and_per_year():
    """AC-F7. Per YEAR as well as per project, because "how much did we spend sponsoring
    this development" and "how much did we spend on sponsorship in 2026" are two different
    management questions and only the second one justifies a budget."""
    from app.models.procurement import PurchaseRequestHeader
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)

        for request_date, amount in (
            ("2026-03-01", "1500.00"),
            ("2026-09-01", "2500.00"),
            ("2025-06-01", "800.00"),
        ):
            db.add(
                PurchaseRequestHeader(
                    id=_uid(),
                    request_type="sponsorship_form",
                    status="submitted",
                    request_date=request_date,
                    project_id=project.id,
                    total_project_value=Decimal(amount),
                    customer_name=f"{MARKER} dev",
                )
            )
        db.flush()

        rollup = links.sponsorship_rollup(db, project_id=project.id)

        assert rollup["total"] == Decimal("4800.00")
        assert rollup["form_count"] == 3
        by_year = {row["year"]: row["total"] for row in rollup["by_year"]}
        assert by_year[2026] == Decimal("4000.00")
        assert by_year[2025] == Decimal("800.00")


def test_the_rollup_reports_sponsorship_to_po_conversion():
    """AC-F7's second half: spend with no PO behind it is the number that changes
    behaviour, so the rollup has to say whether a PO ever arrived."""
    from app.models.procurement import PurchaseRequestHeader
    from app.services import project_po_service as pos
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        # Deliberately unalike: the clash matcher would refuse two near-identical
        # titles under the same developer, which is a different test's subject.
        sponsored_won = _project(db, company_id, owner, title=f"{MARKER} Bukit Jalil Residensi")
        sponsored_open = _project(db, company_id, owner, title=f"{MARKER} Kota Damansara Suites")

        for project in (sponsored_won, sponsored_open):
            db.add(
                PurchaseRequestHeader(
                    id=_uid(),
                    request_type="sponsorship_form",
                    status="submitted",
                    request_date="2026-05-01",
                    project_id=project.id,
                    total_project_value=Decimal("1000.00"),
                    customer_name=f"{MARKER} dev",
                )
            )
        db.flush()
        pos.create_po(
            db,
            project=sponsored_won,
            actor_user_id=owner,
            payload={"po_source": "contractor_direct", "po_number": "PO-SPON-1"},
        )

        conversion = links.sponsorship_conversion(db, company_id=company_id)

        assert conversion["sponsored_projects"] == 2
        assert conversion["converted_projects"] == 1
        assert conversion["rate"] == Decimal("50.00")


def test_conversion_is_none_rather_than_zero_when_nothing_was_sponsored():
    """0% reads as "we sponsor and never win"; None reads as "we have not sponsored"."""
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        conversion = links.sponsorship_conversion(db, company_id=company_id)
        assert conversion["sponsored_projects"] == 0
        assert conversion["rate"] is None


def test_linked_sponsorships_are_listed_for_the_project_tab():
    from app.models.procurement import PurchaseRequestHeader
    from app.services import sponsorship_link_service as links

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Ali")
        project = _project(db, company_id, owner)
        db.add(
            PurchaseRequestHeader(
                id=_uid(),
                request_type="sponsorship_form",
                status="submitted",
                request_date="2026-05-01",
                project_id=project.id,
                total_project_value=Decimal("1200.00"),
                customer_name=f"{MARKER} dev",
                request_number="SF-0001",
                sponsor_subject="Launch event",
            )
        )
        # A PURCHASE REQUEST against the same project must not appear: the tab is about
        # sponsorship spend, and mixing the two would double-count it.
        db.add(
            PurchaseRequestHeader(
                id=_uid(),
                request_type="purchase_request",
                status="submitted",
                request_date="2026-05-02",
                project_id=project.id,
                customer_name=f"{MARKER} dev",
            )
        )
        db.flush()

        rows = links.list_sponsorships(db, project_id=project.id)

        assert len(rows) == 1
        assert rows[0]["request_number"] == "SF-0001"
        assert rows[0]["sponsor_subject"] == "Launch event"
        assert rows[0]["total_project_value"] == Decimal("1200.00")


def test_the_contact_response_dict_carries_the_flag():
    """`contact_to_response_dict` is a MANUAL dict, so a new column reaches the FE only if
    it is listed there -- inheriting the field on the schema is not enough. Same family as
    the get_user/get_me drop-fields bug, and the symptom is identical: the toggle always
    renders its default and never the saved value.
    """
    from app.services.contact_service import ContactService

    with blank_session() as db:
        contact = _contact(db, requires_project=True)
        body = ContactService.contact_to_response_dict(contact)
        assert body["requires_registered_project"] is True


# ------------------------------------------------- free text is not a project id


def test_free_text_in_the_project_field_is_refused_not_a_500():
    """`purchase_requests.project_id` is a UUID FK. A portal payload carrying typed text
    (the picker used to accept free text, and the browser is not a trust boundary anyway)
    reached Postgres as an id and came back an internal server error, which tells the
    submitter nothing. It is refused at the boundary instead, with the action in the
    message."""
    from app.models.procurement import PurchaseRequestHeader
    from app.services.portal_service import PortalService

    with blank_session() as db:
        row = PurchaseRequestHeader(id=_uid(), request_type="sponsorship_form")

        contact = _contact(db, requires_project=False, company_ids=[_sorento(db)])
        with pytest.raises(AppException) as excinfo:
            PortalService(db)._apply_payload(
                "sponsorship_form", row, {"project_id": "PO received"}, token=_token(contact)
            )

        # 400, the same client-error shape every other payload guard here raises.
        assert excinfo.value.status_code == 400
        assert "list" in _message(excinfo.value).lower()
        assert getattr(row, "project_id", None) is None


def test_a_real_project_id_still_applies():
    """The guard has to pass a genuine id through untouched, or it would break the
    flagged-contact flow it is meant to protect."""
    from app.models.procurement import PurchaseRequestHeader
    from app.services.portal_service import PortalService

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} Siti")
        project = _project(db, company_id, owner)
        row = PurchaseRequestHeader(id=_uid(), request_type="sponsorship_form")
        # FORMS-PROJECT: every portal write now checks the contact owns the project.
        contact = _contact(db, requires_project=False, company_ids=[company_id])

        PortalService(db)._apply_payload(
            "sponsorship_form", row, {"project_id": str(project.id)}, token=_token(contact)
        )

        assert str(row.project_id) == str(project.id)
