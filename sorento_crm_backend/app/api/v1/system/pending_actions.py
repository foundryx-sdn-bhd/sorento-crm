"""Deferred record actions - park, cancel, read (D7, S6).

The product has no confirmation dialogs. A destructive or reversible action is parked
HERE for the length of its grace window; the button becomes a countdown with a Cancel,
and the server applies it when the window lapses - even if the tab is closed. Three
routes are the whole contract the frontend consumes:

    POST /api/v1/pending-actions              park it, 202
    POST /api/v1/pending-actions/{id}/cancel  withdraw it, 200 before / 409 after
    GET  /api/v1/pending-actions/current      what is parked, and how the last one ended

Two things this file owes the reader on screen:

* **`last_outcome`.** A countdown that simply disappears reads exactly like success, so
  a commit that FAILED has to be legible afterwards, by `status` and `action_key` - a
  delivery order carries both a status change and a delete, and only the key tells the
  two apart.
* **The lazy commit.** GET applies an action whose window has already closed, before it
  answers. The scheduler sweep (`form_action_commit`) covers the records nobody is
  looking at; this covers whoever is. A stopped scheduler may DELAY an action, never
  lose one.

The engine underneath is the form-SLA one, unchanged: same table, same claim-then-run
commit, same partial unique index that allows one pending action per record.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.base import company_scope
from app.services.company_scope_resolver import grants_scope_value
from app.models.sla import (
    FORM_ACTION_CANCELLED,
    FORM_ACTION_CHANNEL_UI,
    FORM_ACTION_COMMITTED,
    FORM_ACTION_FAILED,
    FORM_ACTION_INELIGIBLE,
    FORM_ACTION_PENDING,
    SlaFormAction,
)
from app.services.error_handler import (
    AppException,
    handle_internal_error,
    handle_not_found,
    handle_validation_error,
)
from app.services.form_action_grace import record_action_window_seconds
from app.services.form_action_registry import get_action
from app.services.form_action_service import FormActionService

# Module scope, not per-request: the lazy commit below calls `action_for(row.action_key)`,
# and on an API worker that has never served a POST the registry would be empty - the
# poll for a due action would 500, the countdown would vanish, and the action would stay
# parked server-side. Which is the exact loss this model exists to prevent.
import app.services.record_actions  # noqa: E402,F401  (registers the record actions)

logger = logging.getLogger(__name__)

router = APIRouter()

# How a stored status reads to the frontend. `ineligible` is the engine's word for "the
# record moved underneath the parked action"; on screen that is a failure with a reason,
# not a fifth state the countdown has to know about.
_OUTCOME_STATUS = {
    FORM_ACTION_COMMITTED: "committed",
    FORM_ACTION_CANCELLED: "cancelled",
    FORM_ACTION_FAILED: "failed",
    FORM_ACTION_INELIGIBLE: "failed",
}


class _CreateRequest(BaseModel):
    #: `<entity>.<verb>` - `product.delete`, `order.set_status`, `user.delete`.
    action_key: str
    entity_type: str
    entity_id: str
    #: Whatever the handler needs at commit time (a status id). Never the entity id -
    #: the route puts that there itself, so a caller cannot park an action against one
    #: record and have it run against another.
    payload: dict = Field(default_factory=dict)


def _record_action(action_key: str):
    """The registered record action, or a 400 that says which part is wrong."""
    action = get_action(action_key)
    if action is None:
        raise handle_validation_error(f"Unknown action: {action_key!r}.")
    if not action.permission:
        # A form-SLA action. Those are dispatched from inside their own domain route,
        # which has already checked its own grant; parking one here would skip it.
        raise handle_validation_error(f"Action {action_key!r} cannot be deferred here.")
    return action


# Some record actions cannot be replayed without a specific payload key captured at
# park time - `project_sales_order.undo_confirm` needs the decision it targets, so a
# Confirm written during the countdown is told apart from a stale one (AC-UC-28,
# review round Contract G). Refused HERE, at park time: the execute path is too late
# to give the click anything to show the refusal on.
_REQUIRED_PAYLOAD_KEYS: dict = {
    "project_sales_order.undo_confirm": ("decision_id",),
    # The ss comment route is addressed by idea AND comment, and the row only holds the comment.
    "idea_comment.delete": ("idea_id",),
}

# A record action whose PARK gate accepts more than one grant (SF-4,
# `PLAN-oi-request-cs-reserve.md`, review round): `FormAction.permission` stays the ONE
# slug the generic registry contract checks (`test_record_actions_s6b.py`'s own
# per-action sweep) - this table is consulted ONLY here, at the click, for an action
# whose actual gate is "one of many". `order_inquiry_reserve_request.cancel` is started
# by the requester (`ACKNOWLEDGE`) or by CS (`RESERVE`, who may cancel a request that
# is not hers - the ownership question itself is `cancel_request`'s own job, not the
# park gate's).
_ANY_OF_PERMISSIONS: dict = {
    "order_inquiry_reserve_request.cancel": (
        "projects.order_inquiries.acknowledge",
        "projects.order_inquiries.reserve",
    ),
}


#: Actions whose ids become an ss URL path: the record id (and `payload.idea_id`) must be a UUID
#: at park time, so a crafted id is refused here and not ten seconds later (AC-A-10).
_UUID_ENTITY_ACTIONS = ("idea.archive", "idea.delete", "idea_comment.delete")


def _assert_required_payload(action_key: str, payload: dict, entity_id: Optional[str] = None) -> None:
    for key in _REQUIRED_PAYLOAD_KEYS.get(action_key, ()):
        if not (payload or {}).get(key):
            raise handle_validation_error(f"{key!r} is required for {action_key!r}.")
    if action_key in _UUID_ENTITY_ACTIONS:
        from app.services.ideation_gateway_service import require_uuid

        require_uuid(entity_id, "entity_id")
        if action_key == "idea_comment.delete":
            require_uuid((payload or {}).get("idea_id"), "idea_id")


def _assert_entity_visible(db: Session, action_key: str, entity_id: str) -> None:
    """Park-time existence check for the contact-customers and customer-group actions.

    The record is read under the requester's company scope, so a link or customer in
    another company (or one that does not exist) refuses 404 before anything is parked -
    the same answer either way, and no countdown for a record the caller cannot see.
    Deliberately just these keys: no other action asked for it."""
    if action_key == "contact_customer_link.unlink":
        from app.services.contact_customer_service import get_link_by_id

        if get_link_by_id(db, entity_id) is None:
            raise handle_not_found("Customer link", entity_id)
    elif action_key == "customer_group.delete":
        from app.services.customer_group_service import CustomerGroupService

        CustomerGroupService(db).get_group(entity_id)
    elif action_key in ("customer.unassign_sales_agent", "customer.remove_from_group"):
        from app.services.contact_customer_service import get_customer_in_scope

        if get_customer_in_scope(db, entity_id) is None:
            raise handle_not_found("Customer", entity_id)


def _assert_undo_not_refused(
    db: Session,
    action_key: str,
    entity_id: str,
    payload: Optional[dict] = None,
    actor_id: Optional[str] = None,
) -> None:
    """`project_sales_order.undo_confirm`'s own refusals, checked at PARK time too
    (review round, follow-up), not only when the countdown lapses several seconds
    later - a raw API call must get the same synchronous 409/403 the disabled gear
    entry already implies. The commit-time check inside `undo_last_confirm` /
    `reconstruct_undo` stays: a link or an actioned row can still land purchasing's
    way DURING the window, and that is caught there.

    The order is loaded under the requester's own company scope
    (`ProjectSupplyService.get_order`), so a foreign company's order 404s here
    exactly as it would at commit time, before anything is parked.

    S5 (AC-R2-34/35, `PLAN-scm-oi-handover-r2-undo.md`): `payload["mode"]` names
    which path this park is for - `"journal"` (the default, for an old caller that
    never sends the key) or `"reconstructed"`. A `mode` that does not match the
    order's active decision's own journal state refuses 409 `mode_mismatch` before
    anything else is checked; `"reconstructed"` additionally refuses 403 unless the
    requester's role is `superadmin`/`admin` (AC-R2-35) - hand-crafting the payload
    past a gear entry the board never showed them gets no further than this.
    """
    if action_key != "project_sales_order.undo_confirm":
        return
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision
    from app.services.project_supply_service import ProjectSupplyService
    from app.services.project_supply_undo_service import (
        _REFUSAL_MESSAGES,
        _decision_is_journalled,
        refusal_for_order_with_detail,
    )

    order = ProjectSupplyService(db).get_order(entity_id)
    mode = (payload or {}).get("mode") or "journal"

    decision = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .first()
    )
    is_journalled = decision is not None and _decision_is_journalled(decision)

    if mode == "reconstructed":
        # Review round 1: the role gate runs BEFORE the `decision is None` early
        # return, not after - a non-admin hand-crafting this payload must always
        # get the same 403 regardless of whether the order even has an active
        # decision to reconstruct, never a 202 that only later 409s "no_journal"
        # (which would leak "a decision exists here" to a caller who has no
        # business asking).
        from app.services.user_service import UserPermissionService

        role_slugs = (
            UserPermissionService(db).get_user_role_slugs(actor_id) if actor_id else set()
        )
        if not (role_slugs & {"superadmin", "admin"}):
            raise AppException(
                status_code=status.HTTP_403_FORBIDDEN,
                message="Only an admin can run a reconstructed undo.",
                code="FORBIDDEN",
            )
        if is_journalled:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                message="This confirm carries a journal; use the journal undo.",
                code="mode_mismatch",
            )
        if decision is None:
            # Nothing active at all - `no_journal` is the execute-time answer,
            # same as today; there is no decision here to gate a refusal against.
            return
        from app.services.project_supply_undo_reconstruct_service import (
            _REFUSAL_MESSAGES as _RECONSTRUCT_REFUSAL_MESSAGES,
            reconstruct_refusal,
        )

        refusal = reconstruct_refusal(db, decision)
        if refusal:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                message=_RECONSTRUCT_REFUSAL_MESSAGES[refusal],
                code=refusal,
            )
        return

    # mode == "journal"
    if decision is not None and not is_journalled:
        raise AppException(
            status_code=status.HTTP_409_CONFLICT,
            message=_REFUSAL_MESSAGES["no_journal"],
            code="no_journal",
        )
    refusal, refusal_detail = refusal_for_order_with_detail(db, str(order.id))
    if refusal:
        # Review round 1 nit: the CLIENT message stays the generic sentence - a
        # table name and a row's UUID are internal detail, not something to hand
        # a browser. The detail is still there for whoever has to diagnose it,
        # just in the log, not the response body.
        if refusal == "changed" and refusal_detail:
            logger.info(
                "undo_confirm changed refusal: %s pk=%s",
                refusal_detail.get("table"),
                refusal_detail.get("pk"),
            )
        raise AppException(
            status_code=status.HTTP_409_CONFLICT,
            message=_REFUSAL_MESSAGES[refusal],
            code=refusal,
        )


def _assert_permission(db: Session, user_id: Optional[str], action_key: str, slug: str) -> None:
    """Enforce the action's own slug at the CLICK.

    Not a route dependency, because the slug is not known until the body is read - and
    checking at commit time instead would leave the refusal with no button to appear on.

    `OWN_RECORD` is the one action whose grant is ownership: its handler is scoped to
    the requester, so being signed in IS the check (see `record_actions.OWN_RECORD`).

    `_ANY_OF_PERMISSIONS` widens the gate to "any of several grants" for the one action
    that needs it - `slug` (the action's own DECLARED permission) is what every other
    action still checks, unchanged.
    """
    from app.services.record_actions import OWN_RECORD
    from app.services.user_service import UserPermissionService

    if slug == OWN_RECORD:
        if not user_id:
            raise AppException(
                status_code=status.HTTP_403_FORBIDDEN,
                message="Sign in to do that.",
                code="FORBIDDEN",
            )
        return

    any_of = _ANY_OF_PERMISSIONS.get(action_key)
    if any_of:
        held = set(UserPermissionService(db).get_user_permission_slugs(user_id)) if user_id else set()
        if not held & set(any_of):
            raise AppException(
                status_code=status.HTTP_403_FORBIDDEN,
                message=f"One of these permissions required: {', '.join(any_of)}",
                code="FORBIDDEN",
            )
        return

    if not user_id or not UserPermissionService(db).check_user_has_permission(
        str(user_id), slug
    ):
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            message=f"Permission required: {slug}",
            code="FORBIDDEN",
        )


def _window_seconds(row: SlaFormAction) -> Optional[int]:
    """The window as STORED, derived from the two timestamps.

    Read back rather than recomputed, so the bar keeps a fixed denominator across a
    remount even if an admin retunes the setting mid-countdown.
    """
    if not (row.commit_at and row.created_at):
        return None
    return max(1, int((row.commit_at - row.created_at).total_seconds()))


def _serialize_pending(db: Session, row: SlaFormAction) -> dict:
    from app.models.user import User

    requester = (
        db.query(User).filter(User.id == row.requested_by_id).first()
        if row.requested_by_id
        else None
    )
    return {
        "id": str(row.id),
        "action_key": row.action_key,
        "entity_type": row.source_entity_type,
        "entity_id": str(row.source_entity_id),
        "commit_at": row.commit_at.isoformat() if row.commit_at else None,
        "window_seconds": _window_seconds(row),
        "requested_by_id": str(row.requested_by_id) if row.requested_by_id else None,
        # A second browser shows the same countdown, and the reader there did not start
        # it. "a teammate" beats a blank, and a UUID is never shown.
        "requested_by_name": (
            ((requester.name or requester.email) if requester else None) or "a teammate"
        ),
    }


def _last_outcome(db: Session, entity_type: str, entity_id: str) -> Optional[dict]:
    """How the most recent action on this record ended.

    Whether it applied is the ONE thing the countdown cannot infer from its own
    disappearance, so it is answered here rather than assumed there.
    """
    row = (
        db.query(SlaFormAction)
        .filter(
            SlaFormAction.source_entity_type == entity_type,
            SlaFormAction.source_entity_id == str(entity_id),
            SlaFormAction.status.in_(list(_OUTCOME_STATUS)),
        )
        .order_by(
            SlaFormAction.committed_at.desc().nullslast(),
            SlaFormAction.resolved_at.desc().nullslast(),
            SlaFormAction.created_at.desc(),
        )
        .first()
    )
    if row is None:
        return None
    ended_at = row.committed_at or row.resolved_at
    return {
        "id": str(row.id),
        "action_key": row.action_key,
        "status": _OUTCOME_STATUS[row.status],
        "error_text": row.error_text,
        "ended_at": ended_at.isoformat() if ended_at else None,
    }


def _commit_if_due(service: FormActionService, row: Optional[SlaFormAction]) -> None:
    """Apply a parked action whose window has already closed.

    A commit can wait on other work (a spec remove waits up to 30 s for a running
    catalogue read), so every route calling this is a plain `def`: FastAPI runs it on
    the threadpool, and the wait never stalls the event loop (review S-R1, #1286).
    """
    if row is None or row.commit_at is None:
        return
    if row.commit_at > datetime.utcnow():
        return
    try:
        service.commit_one(row)
    except Exception:
        # The engine has already stamped the row `failed` with the reason, and
        # `last_outcome` is where the reader learns it. Re-raising here would turn a
        # handler's problem into a 500 for whoever happened to be polling when the
        # window closed - which is exactly the person owed the explanation.
        logger.exception("Pending action %s failed to commit", getattr(row, "id", None))


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_pending_action(
    body: _CreateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Park the action. Nothing is applied until the window lapses (S6-01)."""
    action = _record_action(body.action_key)
    if body.entity_type not in action.entity_types:
        raise handle_validation_error(
            f"Action {body.action_key!r} does not apply to {body.entity_type!r}."
        )
    actor_id = (current_user or {}).get("id")
    _assert_permission(db, actor_id, action.key, action.permission)
    _assert_required_payload(body.action_key, body.payload, body.entity_id)
    # A contact belongs to no one company: park (and later commit, under the stored scope)
    # across every company the requester is granted, never more. Restored on exit.
    scope = (
        company_scope(db, grants_scope_value(db, actor_id))
        if body.action_key == "contact_customer_link.unlink"
        else nullcontext()
    )
    with scope:
        _assert_entity_visible(db, body.action_key, body.entity_id)
        _assert_undo_not_refused(db, body.action_key, body.entity_id, body.payload, actor_id)

        try:
            service = FormActionService(db)
            existing = service.pending_for(body.entity_type, body.entity_id)
            # An overdue row is not really pending; commit it first so a click that lands a
            # moment after the previous window closed starts a fresh action rather than a 409.
            _commit_if_due(service, existing)
            existing = service.pending_for(body.entity_type, body.entity_id)
            if existing is not None:
                if existing.action_key == body.action_key:
                    # A double click parks one action, not two, and answers with the
                    # countdown already running (S6-01).
                    return {
                        "id": str(existing.id),
                        "commit_at": (
                            existing.commit_at.isoformat() if existing.commit_at else None
                        ),
                        "window_seconds": _window_seconds(existing),
                    }
                # One record holds ONE pending action: `current` answers per record, so a
                # second key would leave both countdowns draining the other one's window.
                raise AppException(
                    status_code=status.HTTP_409_CONFLICT,
                    message="Another action on this record is still counting down.",
                    code="CONFLICT",
                )

            window_seconds = record_action_window_seconds(db, action)
            outcome = service.dispatch(
                action_key=body.action_key,
                entity_type=body.entity_type,
                entity_id=str(body.entity_id),
                # The handler is handed the record it runs on and who asked for it, so it
                # never has to read the action row back.
                payload={
                    **(body.payload or {}),
                    "entity_id": str(body.entity_id),
                    "requested_by_id": actor_id,
                },
                actor_id=actor_id,
                channel=FORM_ACTION_CHANNEL_UI,
                grace_seconds=window_seconds,
            )
            return {
                "id": outcome.action_id,
                "commit_at": outcome.commit_at.isoformat() if outcome.commit_at else None,
                "window_seconds": outcome.window_seconds,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise handle_internal_error(str(exc))


@router.post("/{action_id}/cancel", status_code=status.HTTP_200_OK)
def cancel_pending_action(
    action_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Withdraw a parked action. Nothing ran, so nothing is reversed (S6-02).

    No permission slug: the grant was checked when the action was parked, and requiring
    a second one to take back your own two-second-old click would be theatre.
    """
    try:
        service = FormActionService(db)
        row = db.query(SlaFormAction).filter(SlaFormAction.id == str(action_id)).first()
        if row is not None and row.status == FORM_ACTION_PENDING:
            # A Cancel that arrives after the window closed must lose to the commit, not
            # race it - so the overdue action is applied first and `cancel` then 409s.
            _commit_if_due(service, row)
        uid = (current_user or {}).get("id")
        row = service.cancel(action_id, actor_id=uid, actor_is_admin=_is_admin(db, uid))
        return {"id": str(row.id), "status": row.status}
    except HTTPException:
        raise
    except Exception as exc:
        raise handle_internal_error(str(exc))


@router.get("/current")
def get_current_pending_action(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What is parked on this record, and how the last action on it ended (S6-05)."""
    try:
        service = FormActionService(db)
        row = service.pending_for(entity_type, entity_id)
        _commit_if_due(service, row)
        row = service.pending_for(entity_type, entity_id)
        return {
            "pending": _serialize_pending(db, row) if row else None,
            "last_outcome": _last_outcome(db, entity_type, entity_id),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise handle_internal_error(str(exc))


def _is_admin(db: Session, user_id: Optional[str]) -> bool:
    """Admins can cancel someone else's countdown; the engine asks for the flag."""
    from app.services.user_service import UserPermissionService

    if not user_id:
        return False
    return bool(
        UserPermissionService(db).get_user_role_slugs(str(user_id))
        & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
    )
