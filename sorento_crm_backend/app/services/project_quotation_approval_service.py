"""The price-floor approval gate on a quotation document (S14-S16).

Price floors already existed and already flagged a line: ``project_quotation_service._apply_guardrails``
stores ``is_below_floor`` / ``floor_value_applied`` on every line from
``project_pricing_service.resolve_floor``. Nothing enforced them, so a quotation with every line
below floor issued exactly as freely as one that was never discounted. This module is the
enforcement, and it is the whole of it.

Four rules are worth stating here rather than leaving to a reader of the code:

1. **The ordinary quotation never touches the graph.** ``approval_status_id`` stays NULL until a
   below-floor line makes a manager necessary, so no status row is written, no rung is entered,
   and the Issue flow for the common case is byte-for-byte what it was. A document defaulted to
   `draft` on create would enrol every quotation in an approval lifecycle for the sake of the
   minority that discount past the floor. NULL therefore reads as "sitting at the graph's initial
   rung, and has never had to say so" - which is why ``move`` resolves the initial rung as the
   from-position when the column is empty, instead of demanding a first move to get onto a graph
   the user never asked to be on.

2. **The gate is at ISSUE, not at Sign.** The internal signature is readiness, not dispatch: a
   person may sign a quotation at any price. What they may not do is send it.

3. **Issuing spends the approval.** A manager approved THOSE prices, so ``mark_issued`` moves the
   document to `issued` and the next revision that dips below the floor has to be approved on its
   own merits. Leaving it on `approved` for good would be the gate quietly disabling itself after
   one use.

4. **Approve and reject are not generic status moves.** Both edges exist on the graph, and both
   are refused on the generic move route, because approving carries a permission and rejecting
   carries a required reason. A route that asked for neither would make both rules decorative.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.projects import (
    Project,
    ProjectQuotation,
    ProjectQuotationDocument,
    ProjectQuotationLine,
    ProjectQuotationVersion,
)
from app.models.status import Status
from app.services import status_service
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

ENTITY_TYPE = "quotation"

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_ISSUED = "issued"

APPROVE_PERMISSION = "projects.quotations.approve"

# The rungs a salesperson may move to themselves. Everything else on the graph belongs to an act
# with its own rules (a permission, a reason, an actual issue), so the generic move route refuses
# them even where the edge exists.
# `draft` is not here: a rejected quotation resubmits straight to pending (C1, C2), and revise has
# its own act.
SELF_SERVE_KEYS = frozenset({STATUS_PENDING})

# While the document stands on one of these rungs, nobody edits it (D1, D2). Revise is the way out.
LOCKED_KEYS = frozenset({STATUS_PENDING, STATUS_APPROVED})


# ------------------------------------------------------------------ the floor


def current_version_ids(db: Session, document: ProjectQuotationDocument) -> list[str]:
    """The version each scope would contribute if this document were issued right now.

    Same rule ``issue()`` itself uses (MAX(version_no) per scope), because the gate has to be
    asking about the rows that would actually go out. Asking about every version a scope ever
    had would block on a price somebody already corrected.
    """
    scope_ids = [
        row[0]
        for row in db.query(ProjectQuotation.id)
        .filter(ProjectQuotation.document_id == document.id)
        .all()
    ]
    if not scope_ids:
        return []
    latest = (
        db.query(
            ProjectQuotationVersion.quotation_id.label("quotation_id"),
            func.max(ProjectQuotationVersion.version_no).label("version_no"),
        )
        .filter(ProjectQuotationVersion.quotation_id.in_(scope_ids))
        .group_by(ProjectQuotationVersion.quotation_id)
        .subquery()
    )
    rows = (
        db.query(ProjectQuotationVersion.id)
        .join(
            latest,
            (ProjectQuotationVersion.quotation_id == latest.c.quotation_id)
            & (ProjectQuotationVersion.version_no == latest.c.version_no),
        )
        .all()
    )
    return [row[0] for row in rows]


def below_floor_line_count(db: Session, document: ProjectQuotationDocument) -> int:
    """How many lines this document would send out priced below their floor.

    Read from the STORED per-line flag, never re-resolved: the floor that applies is the one
    that applied when the line was priced (AC-E7). Re-resolving here would let a policy edited
    after the fact retroactively block a quotation nobody has changed.
    """
    version_ids = current_version_ids(db, document)
    if not version_ids:
        return 0
    return int(
        db.query(func.count(ProjectQuotationLine.id))
        .filter(
            ProjectQuotationLine.version_id.in_(version_ids),
            ProjectQuotationLine.is_below_floor.is_(True),
        )
        .scalar()
        or 0
    )


def requires_approval(db: Session, document: ProjectQuotationDocument) -> bool:
    return below_floor_line_count(db, document) > 0


# ------------------------------------------------------------------ the graph


def graph(db: Session) -> status_service.StatusGraph:
    """The one ``quotation`` graph. No scoped forks: a price floor is a company policy."""
    return status_service.resolve_graph(db, ENTITY_TYPE, None)


def status_of(db: Session, document: ProjectQuotationDocument) -> Optional[Status]:
    if not document.approval_status_id:
        return None
    return (
        db.query(Status).filter(Status.id == document.approval_status_id).first()
    )


def current_key(db: Session, document: ProjectQuotationDocument) -> Optional[str]:
    """The rung's machine key, or None while the document has never needed one."""
    status = status_of(db, document)
    return status.key if status is not None else None


def _from_status_id(db: Session, document: ProjectQuotationDocument) -> str:
    """Where a move starts from.

    A document with no position is sitting at the graph's initial rung without having had to
    say so, so that is what a move is checked against. The alternative - refusing every move
    until somebody "enters" the graph - would make asking for approval two presses, the first
    of which means nothing to anybody.
    """
    if document.approval_status_id:
        return document.approval_status_id
    return status_service.initial_status(db, ENTITY_TYPE, None).id


def _status_by_key(db: Session, key: str) -> Status:
    row = graph(db).by_key(key)
    if row is None:
        raise AppException(
            status_code=422,
            message=(
                f"The quotation approval graph has no '{key}' status. "
                "Restore it in Setup > Status Graphs."
            ),
            code="status_graph_missing",
        )
    return row


# ------------------------------------------------------------------- the gate


def assert_issuable(db: Session, document: ProjectQuotationDocument) -> None:
    """Refuse to issue a below-floor quotation that no manager has approved.

    Says how many lines rather than only that something is wrong, because the block on the
    screen has to be actionable: "two lines" tells the salesperson there is a second one to
    look at after they have fixed the obvious one.
    """
    below = below_floor_line_count(db, document)
    if not below:
        return
    if current_key(db, document) == STATUS_APPROVED:
        return
    raise AppException(
        status_code=422,
        message=(
            f"{'One line is' if below == 1 else f'{below} lines are'} priced below the "
            "floor, so this quotation needs a manager's approval before it can be sent "
            "to the customer."
        ),
        code="quotation_below_floor_pending_approval",
    )


def mark_issued(db: Session, document: ProjectQuotationDocument) -> None:
    """Spend the approval, if there was one. Called from ``issue()`` after the revision lands.

    Only moves a document that is actually standing on `approved`. A quotation that never needed
    a manager stays off the graph entirely (DoD item 1), and one sitting at `draft` or `rejected`
    with nothing below the floor is issuing on its own merits, so there is no approval to spend.
    """
    if not document.approval_status_id:
        return
    if current_key(db, document) != STATUS_APPROVED:
        return
    _set_status(db, document, _status_by_key(db, STATUS_ISSUED))


def assert_unlocked(db: Session, document: Optional[ProjectQuotationDocument]) -> None:
    """Refuse an edit to a quotation a manager is looking at, or has already approved."""
    if document is None:
        return
    if current_key(db, document) in LOCKED_KEYS:
        raise AppException(
            status_code=422,
            message=(
                "This quotation is with a manager or already approved, so it cannot be "
                "edited. Revise it first."
            ),
            code="quotation_locked_for_approval",
        )


# ------------------------------------------------------------------ the moves


def _set_status(db: Session, document: ProjectQuotationDocument, status: Status) -> None:
    document.approval_status_id = status.id
    if status.key != STATUS_REJECTED:
        # The reason explains a state the document is no longer in. Leaving it would have the
        # screen quoting a rejection that has already been answered.
        document.approval_rejected_reason = None
    db.flush()


def _project_of(db: Session, document: ProjectQuotationDocument) -> Optional[Project]:
    return db.query(Project).filter(Project.id == document.project_id).first()


def _actor_name(db: Session, actor_user_id: Optional[str]) -> str:
    from app.models.user import User

    if not actor_user_id:
        return "Somebody"
    user = db.query(User).filter(User.id == actor_user_id).first()
    return (user.name if user and user.name else None) or "Somebody"


def _record(
    db: Session,
    document: ProjectQuotationDocument,
    *,
    template: str,
    body_text: str,
    actor_user_id: Optional[str],
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """One activity row per decision, on the PROJECT's feed.

    A discount below the floor is exactly the decision somebody asks about six months later, so
    who asked, who decided and why it was sent back all have to be recoverable from the project's
    own history rather than from a manager's memory.

    Deliberately NOT added to ``project_activity_service.MEANINGFUL_TEMPLATES``: that whitelist
    drives the staleness clock, and its own rule is that a new event type stays silent until
    somebody decides it counts as work on the pursuit.
    """
    project = _project_of(db, document)
    if project is None:
        return
    from app.services import project_activity_service as activity

    activity.record_project_event(
        db,
        project=project,
        template=template,
        payload={
            "document_id": str(document.id),
            "document_no": document.document_no,
            **(payload or {}),
        },
        actor_id=actor_user_id,
        body_text=body_text,
    )


def _requested_event(db: Session, document: ProjectQuotationDocument):
    """The newest `quotation_approval_requested` row naming THIS document, or None."""
    from app.models.activities import ActivityEvent

    project = _project_of(db, document)
    if project is None:
        return None
    return (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == str(project.id),
            ActivityEvent.system_template == "quotation_approval_requested",
            ActivityEvent.system_payload["document_id"].astext == str(document.id),
        )
        .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
        .first()
    )


def _press_count(db: Session, document: ProjectQuotationDocument) -> int:
    """How many times this document has been sent for approval: the per-press dedup key."""
    from app.models.activities import ActivityEvent

    project = _project_of(db, document)
    if project is None:
        return 0
    return (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == str(project.id),
            ActivityEvent.system_template == "quotation_approval_requested",
            ActivityEvent.system_payload["document_id"].astext == str(document.id),
        )
        .count()
    )


def requester_id(db: Session, document: ProjectQuotationDocument) -> Optional[str]:
    """Who pressed Send for approval last; the project owner when no such row exists.

    The owner fallback skips an integration user: a bot has nobody to read the message.
    """
    event = _requested_event(db, document)
    if event is not None and event.actor_id:
        return str(event.actor_id)
    project = _project_of(db, document)
    if project is None or not project.owner_user_id:
        return None
    from app.models.user import User

    owner = db.query(User).filter(User.id == project.owner_user_id).first()
    if owner is None or owner.is_integration:
        return None
    return str(project.owner_user_id)


def move(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    to_status_id: str,
    actor_user_id: str,
) -> ProjectQuotationDocument:
    """The salesperson's one self-serve move: ask for approval.

    From Rejected the same press resubmits: the graph has no rejected -> pending edge, so both
    edges it does have (rejected -> draft -> pending) are checked and the document lands on
    pending in one step, with one `quotation_approval_requested` row. There is no back-to-draft
    move. The edit rights the route checks are the only grant needed.
    """
    target = graph(db).by_id(to_status_id)
    if target is None:
        raise AppException(
            status_code=422,
            message="That status does not belong to the quotation approval graph.",
            code="status_not_in_graph",
        )
    if target.key not in SELF_SERVE_KEYS:
        raise AppException(
            status_code=422,
            message=(
                f"'{target.label}' is not a move you can make here. Approving and rejecting "
                "are the manager's own actions, and 'Issued' is stamped by issuing the "
                "quotation."
            ),
            code="quotation_status_not_self_serve",
        )

    from_id = _from_status_id(db, document)
    if target.key == STATUS_PENDING and current_key(db, document) == STATUS_REJECTED:
        # Resubmit (C1): the graph has no rejected -> pending edge, so walk the two it has.
        draft = _status_by_key(db, STATUS_DRAFT)
        status_service.assert_transition_allowed(
            db, ENTITY_TYPE, from_id, draft.id, scope_id=None
        )
        from_id = draft.id
    status_service.assert_transition_allowed(
        db, ENTITY_TYPE, from_id, to_status_id, scope_id=None
    )
    _set_status(db, document, target)

    if target.key == STATUS_PENDING:
        below = below_floor_line_count(db, document)
        _record(
            db,
            document,
            template="quotation_approval_requested",
            body_text=(
                f"{_actor_name(db, actor_user_id)} asked for approval on quotation "
                f"{document.document_no} ({below} line{'' if below == 1 else 's'} below floor)."
            ),
            actor_user_id=actor_user_id,
            payload={"below_floor_line_count": below},
        )
    return document


def _assert_can_decide(permissions: Iterable[str]) -> None:
    if APPROVE_PERMISSION not in set(permissions or ()):
        raise AppException(
            status_code=403,
            message=(
                "Only a sales manager can approve or reject below-floor pricing on a "
                "quotation."
            ),
            code="quotation_approval_forbidden",
        )


def approve(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    permissions: Iterable[str],
) -> ProjectQuotationDocument:
    """The manager accepts the below-floor pricing. The next Issue press then proceeds."""
    _assert_can_decide(permissions)
    target = _status_by_key(db, STATUS_APPROVED)
    status_service.assert_transition_allowed(
        db, ENTITY_TYPE, _from_status_id(db, document), target.id, scope_id=None
    )
    _set_status(db, document, target)
    _record(
        db,
        document,
        template="quotation_approval_granted",
        body_text=(
            f"{_actor_name(db, actor_user_id)} approved the below-floor pricing on "
            f"quotation {document.document_no}."
        ),
        actor_user_id=actor_user_id,
    )
    return document


def reject(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    reason: Optional[str],
    permissions: Iterable[str],
) -> ProjectQuotationDocument:
    """The manager sends it back, and the reason is required.

    Checked BEFORE the permission would let anything move, and stored on the document rather
    than only in the feed: "rejected" with no reason leaves the salesperson guessing which line
    to move, which is the conversation this gate exists to make explicit.
    """
    _assert_can_decide(permissions)
    cleaned = " ".join((reason or "").split())
    if not cleaned:
        raise AppException(
            status_code=422,
            message="Say why you are sending this back, so the salesperson knows what to change.",
            code="quotation_reject_reason_required",
        )

    target = _status_by_key(db, STATUS_REJECTED)
    status_service.assert_transition_allowed(
        db, ENTITY_TYPE, _from_status_id(db, document), target.id, scope_id=None
    )
    _set_status(db, document, target)
    document.approval_rejected_reason = cleaned
    db.flush()
    _record(
        db,
        document,
        template="quotation_approval_rejected",
        body_text=(
            f"{_actor_name(db, actor_user_id)} sent quotation {document.document_no} "
            f"back: {cleaned}"
        ),
        actor_user_id=actor_user_id,
        payload={"reason": cleaned},
    )
    return document


def revise_approval(
    db: Session,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    reason: Optional[str],
) -> ProjectQuotationDocument:
    """The creator takes a Pending or Approved quotation back to Draft to change it (D3-D5).

    A system move like ``mark_issued``: the graph has no edge for it, the two rungs it leaves
    are simply the two the lock guards. The reason is required and lands in the feed.
    """
    cleaned = " ".join((reason or "").split())
    if not cleaned:
        raise AppException(
            status_code=422,
            message="Say what changed and why, so the manager can see it.",
            code="quotation_revise_reason_required",
        )
    was = current_key(db, document)
    if was not in LOCKED_KEYS:
        raise AppException(
            status_code=422,
            message="Only a quotation that is pending or approved needs revising.",
            code="quotation_not_revisable",
        )
    _set_status(db, document, _status_by_key(db, STATUS_DRAFT))
    _record(
        db,
        document,
        template="quotation_approval_revised",
        body_text=(
            f"{_actor_name(db, actor_user_id)} reopened quotation {document.document_no} "
            f"for changes: {cleaned}"
        ),
        actor_user_id=actor_user_id,
        payload={"reason": cleaned},
    )
    return document


# ---------------------------------------------------------------- notifications
#
# Called by the ROUTES after they commit the status. Best effort, each with its own try/except:
# a notification fault never fails the action and never undoes the committed rung. A fault that
# poisons the session is rolled back here, which is harmless because the status is already
# committed.


def _best_effort(kind: str, db: Session, send) -> None:
    try:
        send()
    except Exception as exc:  # noqa: BLE001
        logger.warning("quotation approval notification failed: kind=%s (%s)", kind, exc)
    finally:
        # A statement that failed inside the sender (swallowed per recipient) leaves the
        # transaction aborted; the status is committed already, so rolling back loses nothing
        # and lets the route go on to read the document.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def notify_requested(
    db: Session, document: ProjectQuotationDocument, actor_user_id: Optional[str]
) -> None:
    def send() -> None:
        project = _project_of(db, document)
        if project is None:
            return
        from app.services import project_notify_service as notify

        notify.notify_quotation_approval_requested(
            db,
            project=project,
            document=document,
            press=str(_press_count(db, document)),
            below=below_floor_line_count(db, document),
            requester_id=actor_user_id,
            requester_name=_actor_name(db, actor_user_id),
        )

    _best_effort("requested", db, send)


def notify_decided(
    db: Session,
    document: ProjectQuotationDocument,
    actor_user_id: Optional[str],
    *,
    approved: bool,
    reason: Optional[str] = None,
) -> None:
    def send() -> None:
        project = _project_of(db, document)
        if project is None:
            return
        from app.services import project_notify_service as notify

        notify.notify_quotation_approval_decided(
            db,
            project=project,
            document=document,
            approved=approved,
            recipient_id=requester_id(db, document),
            decider_id=actor_user_id,
            decider_name=_actor_name(db, actor_user_id),
            reason=reason,
        )

    _best_effort("approved" if approved else "rejected", db, send)


def notify_withdrawn(
    db: Session,
    document: ProjectQuotationDocument,
    actor_user_id: Optional[str],
    reason: Optional[str],
) -> None:
    def send() -> None:
        project = _project_of(db, document)
        if project is None:
            return
        from app.services import project_notify_service as notify

        notify.notify_quotation_approval_withdrawn(
            db,
            project=project,
            document=document,
            actor_id=actor_user_id,
            actor_name=_actor_name(db, actor_user_id),
            reason=reason,
        )

    _best_effort("withdrawn", db, send)


# ------------------------------------------------------------------ serialize


def serialize_approval(db: Session, document: ProjectQuotationDocument) -> Dict[str, Any]:
    """The approval block of the document payload.

    Its own function so ``serialize_document`` cannot drift from it, and so every one of these
    keys is present on EVERY document - absent would be as bad as wrong, because the screen
    reads them to decide whether to render the gate at all.
    """
    status = status_of(db, document)
    below = below_floor_line_count(db, document)
    return {
        "approval_status_id": str(status.id) if status is not None else None,
        "approval_status_key": status.key if status is not None else None,
        "approval_status_label": status.label if status is not None else None,
        "approval_rejected_reason": document.approval_rejected_reason,
        "requires_approval": below > 0,
        "below_floor_line_count": below,
    }
