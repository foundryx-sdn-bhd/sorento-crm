"""FORMS-PROJECT / #1689 - a staff-side form save may only link a project the editor can see.

The staff PR/SF and complaint paths wrote `project_id` with no check at all: any UUID was
stored, including a project of a company the editor is not allowed to use, and free text
reached the FK as a 500. The portal has refused that since S4 (`sponsorship_link_service`).

The staff rule is the picker's own: the project must be visible under the session's company
scope (the same `projects` list the staff picker reads). Anything else is a 422 on the
project field, never a silent drop, never a 500. An explicit null clears the link.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import company_scope
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-formproj"


def _uid() -> str:
    return str(uuid.uuid4())


def _code(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("code", "") if isinstance(detail, dict) else ""


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _other_company(db) -> str:
    company_id = _uid()
    db.execute(
        text("insert into companies (id, code, name, is_active) values (:i, :c, :n, true)"),
        {"i": company_id, "c": f"Z{_uid()[:3]}", "n": f"{MARKER} other co"},
    )
    db.flush()
    return company_id


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tower {_uid()[:6]}",
    )
    db.flush()
    return str(project.id)


@pytest.fixture()
def world():
    """Two companies with one project each; the editor's scope is Sorento only."""
    with blank_session() as db:
        srt = _sorento(db)
        other = _other_company(db)
        project_seed_service.run(db, company_id=srt)
        project_seed_service.run(db, company_id=other)
        owner = _uid()
        db.add(User(id=owner, email=f"{owner}@zzt.test", name=f"{MARKER} owner"))
        db.flush()
        own = _project(db, srt, owner)
        foreign = _project(db, other, owner)
        with company_scope(db, frozenset({srt})):
            yield db, own, foreign


# ------------------------------------------------------------------ the helper


def test_helper_accepts_a_visible_project(world):
    from app.services.project_link import check_project_link

    db, own, _foreign = world
    assert check_project_link(db, own) == own


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_helper_turns_blank_into_no_link(world, blank):
    from app.services.project_link import check_project_link

    db, _own, _foreign = world
    assert check_project_link(db, blank) is None


@pytest.mark.parametrize("bad", ["not-a-uuid", "foreign", "missing"])
def test_helper_refuses_free_text_foreign_and_missing_projects(world, bad):
    from app.services.project_link import check_project_link

    db, _own, foreign = world
    value = {"foreign": foreign, "missing": _uid()}.get(bad, bad)
    with pytest.raises(AppException) as exc:
        check_project_link(db, value)
    assert exc.value.status_code == 422
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


# ------------------------------------------------- purchase request / sponsorship


def _pr_service(db):
    from app.services.procurement_service import PurchaseRequestService

    return PurchaseRequestService(db)


def _create_pr(db, **kwargs):
    from app.schemas.procurement import PurchaseRequestHeaderCreate

    return _pr_service(db).create_request(
        PurchaseRequestHeaderCreate(
            request_type=kwargs.pop("request_type", "sponsorship_form"),
            request_number=f"{MARKER}-{uuid.uuid4().hex[:6]}",
            **kwargs,
        )
    )


@pytest.mark.parametrize("request_type", ["purchase_request", "sponsorship_form"])
def test_staff_create_stores_a_visible_project(world, request_type):
    db, own, _foreign = world
    header = _create_pr(db, request_type=request_type, project_id=own)
    assert str(header.project_id) == own


def test_staff_create_refuses_a_foreign_project(world):
    db, _own, foreign = world
    with pytest.raises(AppException) as exc:
        _create_pr(db, project_id=foreign)
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


def test_staff_create_refuses_free_text_instead_of_a_500(world):
    db, _own, _foreign = world
    with pytest.raises(AppException) as exc:
        _create_pr(db, project_id="Residensi Foo")
    assert exc.value.status_code == 422


def test_staff_edit_links_relinks_and_clears(world):
    from app.schemas.procurement import PurchaseRequestHeaderUpdate

    db, own, _foreign = world
    header = _create_pr(db)
    assert header.project_id is None
    svc = _pr_service(db)
    header = svc.update_request(str(header.id), PurchaseRequestHeaderUpdate(project_id=own))
    assert str(header.project_id) == own
    # An edit that does not send project_id leaves the link alone.
    header = svc.update_request(str(header.id), PurchaseRequestHeaderUpdate(pic="site office"))
    assert str(header.project_id) == own
    header = svc.update_request(str(header.id), PurchaseRequestHeaderUpdate(project_id=None))
    assert header.project_id is None


def test_staff_edit_refuses_a_foreign_project_and_keeps_the_old_link(world):
    from app.schemas.procurement import PurchaseRequestHeaderUpdate

    db, own, foreign = world
    header = _create_pr(db, project_id=own)
    with pytest.raises(AppException) as exc:
        _pr_service(db).update_request(
            str(header.id), PurchaseRequestHeaderUpdate(project_id=foreign)
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"
    db.refresh(header)
    assert str(header.project_id) == own


def test_staff_update_and_reply_refuses_a_foreign_project_before_any_send(world):
    from app.schemas.procurement import PurchaseRequestUpdateAndReply

    db, _own, foreign = world
    header = _create_pr(db)
    with pytest.raises(AppException) as exc:
        _pr_service(db).update_request_and_reply(
            str(header.id),
            PurchaseRequestUpdateAndReply(project_id=foreign, reply_message="hi"),
            respond_user_id="1",
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


# ------------------------------------------------------------------- complaint


def _complaint_service(db):
    from app.services.complaints_service import ComplaintService

    return ComplaintService(db)


def test_staff_complaint_create_refuses_a_foreign_project(world):
    from app.schemas.complaints import ComplaintCreate

    db, _own, foreign = world
    with pytest.raises(AppException) as exc:
        _complaint_service(db).create_complaint(
            ComplaintCreate(
                complaint_number=f"{MARKER}-{uuid.uuid4().hex[:6]}", project_id=foreign
            )
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


def test_staff_complaint_create_and_edit_with_a_visible_project(world):
    from app.schemas.complaints import ComplaintCreate, ComplaintUpdate

    db, own, foreign = world
    svc = _complaint_service(db)
    complaint = svc.create_complaint(
        ComplaintCreate(complaint_number=f"{MARKER}-{uuid.uuid4().hex[:6]}", project_id=own)
    )
    assert str(complaint.project_id) == own
    with pytest.raises(AppException):
        svc.update_complaint(str(complaint.id), ComplaintUpdate(project_id=foreign))
    complaint = svc.update_complaint(str(complaint.id), ComplaintUpdate(project_id=None))
    assert complaint.project_id is None
