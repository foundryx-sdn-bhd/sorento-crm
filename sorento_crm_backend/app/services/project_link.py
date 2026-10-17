"""The staff-side check for a form's project link (FORMS-PROJECT, #1689).

A staff form may only link a project the editor can see: the project must be visible under
the session's company scope, which is the same `projects` list the staff picker reads. The
portal has its own, narrower rule (the contact's companies,
`sample_request_service.assert_portal_project_allowed`).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.services.error_handler import AppException


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def check_project_link(db: Session, project_id: Optional[str]) -> Optional[str]:
    """Return the id to store: None for a blank value, the id for a visible project.

    Anything else (free text, a missing project, one outside the editor's companies) is a
    422 on the project field, so a bad pick is never silently dropped nor a 500 at the FK.
    """
    value = (project_id or "").strip() if isinstance(project_id, str) else project_id
    if not value:
        return None
    from app.models.projects import Project

    value = str(value)
    if not _is_uuid(value) or db.query(Project.id).filter(Project.id == value).first() is None:
        raise AppException(
            status_code=422,
            message="That project was not found.",
            code="PROJECT_LINK_NOT_FOUND",
            details={"field": "project_id"},
        )
    return value


def apply_project_link(db: Session, payload: dict) -> None:
    """Check a payload's `project_id` in place, when the payload carries one."""
    if "project_id" in payload:
        payload["project_id"] = check_project_link(db, payload["project_id"])


def check_portal_project_link(
    db: Session,
    *,
    contact,
    project_id: Optional[str],
    code: str = "PROJECT_LINK_NOT_ALLOWED",
) -> Optional[str]:
    """The portal rule: a picked project must belong to one of the contact's companies (the
    same set the portal lookup lists). Blank means no link; anything else is a 422."""
    value = (project_id or "").strip() if isinstance(project_id, str) else project_id
    if not value:
        return None
    from app.models.base import company_scope
    from app.models.projects import Project
    from app.services.sponsorship_link_service import contact_company_ids

    value = str(value)
    company_ids = contact_company_ids(db, contact=contact) if contact is not None else []
    allowed = False
    if company_ids and _is_uuid(value):
        with company_scope(db, frozenset(company_ids)):
            allowed = (
                db.query(Project.id)
                .filter(Project.id == value, Project.company_id.in_(company_ids))
                .first()
                is not None
            )
    if not allowed:
        raise AppException(
            status_code=422,
            message="Pick one of your own projects.",
            code=code,
            details={"field": "project_id"},
        )
    return value


def project_labels(db: Session, project_ids) -> dict:
    """`{project_id: (project_code, title)}` for the given ids, in one query."""
    ids = {str(pid) for pid in project_ids if pid}
    if not ids:
        return {}
    from app.models.projects import Project

    return {
        str(row[0]): (row[1], row[2])
        for row in db.query(Project.id, Project.project_code, Project.title)
        .filter(Project.id.in_(list(ids)))
        .all()
    }


def project_label(code: Optional[str], title: Optional[str]) -> Optional[str]:
    """`CODE - Title`, the one way a linked project is written on screen."""
    parts = [part for part in (code, title) if part]
    return " - ".join(parts) if parts else None


def attach_project_display(db: Session, items) -> None:
    """Set `project_code` / `project_label` on each row (ORM object or dict) from its
    `project_id`, one query for the lot, so the UI shows a code and title, never a UUID.

    `project_label`, not `project_name`: stock inquiries already use `project_name` for the
    free text the person typed, which stays."""
    items = [item for item in items if item is not None]

    def _get(item):
        return item.get("project_id") if isinstance(item, dict) else getattr(item, "project_id", None)

    labels = project_labels(db, [_get(item) for item in items])
    for item in items:
        code, title = labels.get(str(_get(item)), (None, None)) if _get(item) else (None, None)
        values = {"project_code": code, "project_label": project_label(code, title)}
        for key, value in values.items():
            if isinstance(item, dict):
                item[key] = value
            else:
                setattr(item, key, value)
