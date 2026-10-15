"""Project activity feed and the meaningful-activity clock (UAC Group H: AC-H1, AC-H2).

**No new tables.** A project registers an adapter with the generic `activities_registry`, so
the Activities feed, `@`-mentions and internal notes all work through the same routes tickets
already use. Building a second notes system per module is how a codebase ends up with four
comment tables that behave slightly differently.

The interesting decision is which events advance ``last_meaningful_activity_at``, because
that clock is the only thing standing between the staleness ladder and irrelevance:

- **Any human post counts, unconditionally.** A salesperson writing "developer says the
  decision moved to March" is the single most valuable row in the record.
- **System events count only from a whitelist.** A stage change, a quotation, a sample, a
  sponsorship, a PO -- things somebody DID. An import, a field edit, a status re-derivation
  or opening the page do not.

If ordinary edits advanced the clock, the ladder would be trivially gamed by anyone who
learned that touching a field silences it, and within a month the "Unattended" badge would
mean nothing at all. That is why the whitelist is a closed tuple here rather than a
"skip these" blacklist: a new event type is silent until somebody decides it is real work.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.projects import Project

logger = logging.getLogger(__name__)

ENTITY_TYPE = "project"

# AC-H2's whitelist. AC-N8 adds task created / completed, which `project_task_service`
# stamps directly on the project (it owns those transitions and has no activity row).
MEANINGFUL_TEMPLATES = (
    "stage_changed",
    "quotation_created",
    "quotation_revised",
    "sample_submitted",
    "sponsorship_recorded",
    "po_recorded",
)


def is_meaningful(template: Optional[str]) -> bool:
    return bool(template) and template in MEANINGFUL_TEMPLATES


def _advance_clock(db: Session, project: Project) -> None:
    """Advance the clock and drop the project off the staleness ladder.

    Both together, always. Advancing the clock while leaving ``stale_level`` at 3 would
    keep an "Unattended" badge on a project somebody just worked on until the next
    overnight sweep, and the person who did the work would reasonably conclude the badge
    is broken.
    """
    project.last_meaningful_activity_at = datetime.utcnow()
    project.stale_level = 0
    project.stale_since = None
    project.stale_reason = None


def can_view(db: Session, project_id: str, current_user: dict) -> bool:
    """Visibility gate for the shared activities panel (AC-A3, AC-J2).

    Supplied because the generic gate is opt-IN: an adapter that leaves `can_view` as None is
    treated as "no opinion" and every holder of `projects.projects.view` can read and post on
    ANY project id. `activity_events` is not company-scoped, so without this a user in one
    company could read another company's internal notes by pasting a project id -- and a post
    would reset that project's staleness clock, clearing an Unattended badge they cannot see.

    The check is deliberately just "does this project resolve for you": the query runs on the
    request session, so the fail-closed company filter answers the cross-company half, and
    everything else about project visibility is already "anyone with `.view` in this company"
    (AC-J2). Edit rights are a separate question the write routes answer.
    """
    if not project_id:
        return False
    try:
        uuid.UUID(str(project_id))
    except (AttributeError, TypeError, ValueError):
        # A malformed id never reaches Postgres. Comparing one to a uuid column raises, and the
        # raise ABORTS the transaction -- the generic gate turns that into a correct 404 while
        # leaving the session poisoned for whatever the request does next. An id that is not an
        # id is simply not visible.
        return False
    try:
        return (
            db.query(Project.id).filter(Project.id == str(project_id)).first() is not None
        )
    except Exception:  # noqa: BLE001 -- a failed lookup is a "no", not a 500
        if not db.is_active:
            db.rollback()
        return False


def record_project_event(
    db: Session,
    *,
    project: Project,
    template: str,
    payload: Optional[Dict[str, Any]] = None,
    actor_id: Optional[str] = None,
    body_text: Optional[str] = None,
) -> None:
    """Append a system activity row for a project, advancing the clock when it earns it.

    Best-effort on the FEED, strict on the CLOCK. The activity row is a narrative aid: if
    the activities service raises, the caller's real work (the PO, the stage change) has
    already happened and must not 500. The clock, by contrast, is business state that the
    ladder reads, so it is updated first and inside the caller's transaction.
    """
    if is_meaningful(template):
        _advance_clock(db, project)

    try:
        from app.services import activities_service

        activities_service.record_system_event(
            db,
            ENTITY_TYPE,
            str(project.id),
            template=template,
            payload=payload or {},
            actor_id=actor_id,
            body_text=body_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project activity row not written: project=%s template=%s (%s)",
            project.id,
            template,
            exc,
        )


def note_user_activity(db: Session, *, project_id: str, actor_id: Optional[str] = None) -> None:
    """A human posted on the project. Called from the activities adapter's on_post hook.

    Takes an id rather than a Project because the generic activities route only knows the
    entity id; looking the row up here keeps that route free of project imports.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        return
    _advance_clock(db, project)
    db.flush()


def respond_contacts_for(db: Session, project_id: str):
    """No contact-facing chat on a project (AC-H1).

    A project is an internal pursuit record: the people on the other side are a developer,
    an architect and a main contractor, tracked as parties and stakeholders rather than as
    Respond.io contacts. Returning an empty list keeps the shared panel's message tab
    correctly empty instead of showing somebody else's conversation.
    """
    return []
