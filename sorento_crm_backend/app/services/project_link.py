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
