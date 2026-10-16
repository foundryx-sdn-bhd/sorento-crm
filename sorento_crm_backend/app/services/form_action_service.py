"""Deferral, cancel and commit for form-SLA actions (PLAN-form-sla-undo.md).

One entry point (``dispatch``) that either runs the action now or parks it for the
length of its grace window. Nothing about the domain changes while an action is parked -
that is the whole promise of the in-grace undo, and it is why the grace window defers
rather than compensates: the approve path sends the contact a WhatsApp that cannot be
unsent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.sla import (
    FORM_ACTION_CANCELLED,
    FORM_ACTION_CHANNEL_UI,
    FORM_ACTION_COMMITTED,
    FORM_ACTION_FAILED,
    FORM_ACTION_INELIGIBLE,
    FORM_ACTION_PENDING,
    FORM_ACTION_UNDONE,
    SlaFormAction,
)

# Refusal copy, keyed on the guardrail's machine-readable reason.
_UNDO_REFUSAL_COPY = {
    "no_action": "There is no completed action on this form to undo.",
    "next_stage_acted": "The next stage has already been acted on, so this cannot be undone.",
    "status_moved": "This form has moved on since that action.",
    "not_invertible": "This action cannot be reversed automatically.",
    "no_permission": "You do not have permission to undo actions on this form.",
    "action_pending": "Another action on this form is still pending. Let it apply or withdraw it first.",
}
from app.models.base import (
    UNSET,
    CompanyScope,
    company_scope,
    get_company_scope,
)
from app.services.error_handler import AppException, handle_conflict, handle_not_found
from app.services.form_action_registry import action_for

logger = logging.getLogger(__name__)

#: Reserved payload key holding the company scope the REQUESTER had when the action
#: was parked.
#:
#: A parked action is executed later, by whoever gets to it first: the scheduler sweep
#: (``set_company_scope(db, None)`` - every company, because a tick has no principal)
#: or a lazy commit inside SOMEBODY ELSE'S request. Either way the session running the
#: handler is not the session that authorised it, so without this the handler ran with
#: the sweeper's scope: a user of company A could park a delete naming a company-B
#: record, and ten seconds later the sweep - which sees every company - would carry it
#: out. The permission check at the click is a slug check; it never was a company check.
#:
#: So the scope travels WITH the action and the commit runs inside it. A row parked
#: before this existed has no key, and a missing scope reads as ``UNSET`` (0 rows),
#: never as ``None`` (all companies) - fail-closed, the same rule
#: ``app/services/company_scope.py`` applies to a session that never resolved one.
#:
#: In the payload rather than in a column of its own: nothing renders the payload (see
#: ``_serialize_pending`` in ``app/api/v1/system/pending_actions.py``, which never
#: returns it), so a reserved key is invisible where a column would be a migration. The
#: engine strips it before the handler sees the payload, so no handler can read it by
#: accident either.
_SCOPE_PAYLOAD_KEY = "__company_scope"


def _utc_naive_now() -> datetime:
    """Naive UTC, matching every other tracking timestamp in this schema."""
    return datetime.utcnow()


def _payload_with_scope(db: Session, payload: Optional[dict]) -> dict:
    """The payload as stored on a PARKED row, carrying the requester's scope.

    ``UNSET`` stores nothing: the key's absence already reads back as ``UNSET``, and
    writing a sentinel for it would only give a second spelling of the same state.
    """
    stored = dict(payload or {})
    scope = get_company_scope(db)
    if scope is None:
        # The deliberate system / all-companies principal. Recorded EXPLICITLY, so a
        # commit can tell "this action was authorised across every company" from
        # "nobody ever asked".
        stored[_SCOPE_PAYLOAD_KEY] = None
    elif isinstance(scope, frozenset):
        stored[_SCOPE_PAYLOAD_KEY] = sorted(str(company_id) for company_id in scope)
    return stored


def _scope_from_payload(payload: Optional[dict]) -> CompanyScope:
    """The scope a parked action must execute under. Missing => ``UNSET`` (0 rows)."""
    stored = payload or {}
    if _SCOPE_PAYLOAD_KEY not in stored:
        return UNSET
    value = stored[_SCOPE_PAYLOAD_KEY]
    if value is None:
        return None
    return frozenset(str(company_id) for company_id in value)


def _handler_payload(payload: Optional[dict]) -> dict:
    """What the handler is given: the payload without the engine's reserved keys."""
    handler = dict(payload or {})
    handler.pop(_SCOPE_PAYLOAD_KEY, None)
    return handler


def _audit(
    db: Session,
    row: "SlaFormAction",
    action: str,
    *,
    user_id: Optional[str] = None,
    description: str,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
) -> None:
    """Audit every path - request, commit, cancel, undo, refusal (AC-A-1).

    Audited against the FORM, not the action row: someone reading a request's history
    wants to see that it was approved and then reversed, without knowing this feature's
    internal table exists.

    `action` is always UPDATE. `audit_logs.action` carries a CHECK constraint allowing
    only CREATE/READ/UPDATE/DELETE/IMPORT, so a custom verb is rejected outright - and
    the AuditTrail UI renders `description` in preference to a value diff anyway, so the
    detail lands where a reader will actually see it.
    """
    try:
        from app.services.audit_service import log_audit

        log_audit(
            db,
            entity_type=row.source_entity_type,
            entity_id=str(row.source_entity_id),
            action=action,
            user_id=user_id,
            description=description,
            old_values=old_values,
            new_values=new_values,
        )
        db.commit()
    except Exception:
        # An audit failure must never take down the operation it is describing - but a
        # failed INSERT leaves the session unusable, so roll back or the NEXT statement
        # dies instead and the audit bug surfaces as an unrelated one.
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Audit write failed for form action %s", getattr(row, "id", None))


#: Postgres foreign-key violation. The one failure a hard delete meets in normal use,
#: and the only one worth a sentence of its own.
_FK_VIOLATION = "23503"


def _reader_facing_error(exc: Exception, row: "SlaFormAction") -> str:
    """What a failed commit is allowed to SAY, as opposed to what it is allowed to log.

    `error_text` is handed to the frontend by `GET /pending-actions/current` and
    rendered straight into a toast, because a countdown that simply disappears reads
    exactly like success. That makes it user-facing copy, and a raw exception is not:
    a psycopg2 IntegrityError stringifies to the failing INSERT/DELETE, the constraint
    name and the bound parameters, so a product that could not be deleted showed the
    reader the SQL and the row's own values. The full exception still reaches the log,
    where whoever is on call can read it.

    A deliberate 4xx is the exception: the domain wrote that sentence for a person
    ("Conversation is already responded."). A 500 is `handle_internal_error(str(e))`
    somewhere below, which is the same raw exception wearing a message field. So a
    handler with something specific to say raises an AppException with a 4xx; anything
    else that escapes is a defect, and a defect's message is not copy.
    """
    subject = (row.source_entity_type or "record").replace("_", " ")
    deleting = str(row.action_key or "").endswith(".delete")

    if isinstance(exc, AppException) and 400 <= int(getattr(exc, "status_code", 500)) < 500:
        detail = getattr(exc, "detail", None)
        message = detail.get("message") if isinstance(detail, dict) else detail
        if message:
            return str(message)[:2000]
    elif isinstance(exc, IntegrityError):
        if getattr(getattr(exc, "orig", None), "pgcode", None) == _FK_VIOLATION:
            return (
                f"Cannot delete this {subject}: other records still reference it."
                if deleting
                else f"Cannot change this {subject}: another record depends on it."
            )
        return f"Cannot save this {subject}: it conflicts with a record that already exists."

    if deleting:
        return f"This {subject} could not be deleted. Nothing was changed."
    return f"This {subject} could not be updated. Nothing was changed."


@dataclass(frozen=True)
class DispatchResult:
    """What the route turns into either a 202 (deferred) or today's 200 body."""

    deferred: bool
    action_id: str
    commit_at: Optional[datetime] = None
    window_seconds: Optional[int] = None
    # Whatever the underlying service method returned, on the immediate path.
    result: Any = None


class FormActionService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- dispatch ----------------

    def dispatch(
        self,
        *,
        action_key: str,
        entity_type: str,
        entity_id: str,
        payload: dict,
        actor_id: Optional[str],
        channel: str,
        grace_seconds: int,
    ) -> DispatchResult:
        """Run the action now, or park it for ``grace_seconds``.

        Only an in-system UI caller can defer: everyone else (portal, API key, n8n, MCP)
        has no Undo button to press, so deferring would just delay their result and
        change a response shape they already depend on (AC-D-2/AC-D-3).
        """
        action = action_for(action_key)
        if entity_type not in action.entity_types:
            raise handle_conflict(
                f"Action {action_key!r} does not apply to {entity_type!r}."
            )

        defers = channel == FORM_ACTION_CHANNEL_UI and grace_seconds > 0

        # Captured BEFORE anything runs, on both paths: the immediate path still needs
        # a prior-state snapshot, or an action that never deferred could not be undone
        # afterwards (AC-PGE-1).
        prior_state = action.capture(self.db, payload) or {}

        parked_at = _utc_naive_now()
        row = SlaFormAction(
            action_key=action_key,
            source_entity_type=entity_type,
            source_entity_id=str(entity_id),
            event_name=action.resolve_event(payload),
            # A parked row carries the requester's company scope; an immediate one has
            # no gap between the click and the work, so it needs nothing stored - it
            # runs on the requester's own session (see `_execute`).
            payload_json=_payload_with_scope(self.db, payload) if defers else (payload or {}),
            prior_state_json=prior_state,
            requested_by_id=actor_id,
            channel=channel,
            status=FORM_ACTION_PENDING,
            # Both stamps from ONE clock. `created_at` used to come from the database
            # default, and Postgres `now()` is the transaction START, which under a
            # loaded CI shard sat seconds before this line ran; the countdown's
            # denominator is derived from commit_at - created_at, so a 10 s window
            # read back as 12 (test_current_carries_every_field_the_countdown_needs).
            created_at=parked_at,
            commit_at=parked_at + timedelta(seconds=grace_seconds) if defers else None,
        )

        if defers:
            # Only a DEFERRED action is ever persisted as `pending` - the row IS the
            # parked work, and the partial unique index enforces one at a time.
            self.db.add(row)
            try:
                self.db.flush()
            except IntegrityError:
                # The partial unique index caught a second pending action on this form.
                # Rolling back keeps the session usable for the caller's error response.
                self.db.rollback()
                raise handle_conflict(
                    "Another action on this form is still pending. Wait for it to apply, "
                    "or undo it first."
                )
            self.db.commit()
            _audit(
                self.db,
                row,
                "UPDATE",
                user_id=actor_id,
                description=(
                    f"Action requested ({action_key}); held {grace_seconds}s before applying"
                ),
                new_values={"action_key": action_key, "commit_at": str(row.commit_at)},
            )
            return DispatchResult(
                deferred=True,
                action_id=str(row.id),
                commit_at=row.commit_at,
                window_seconds=grace_seconds,
            )

        # Immediate path: the row is deliberately NOT in the session while the wrapped
        # method runs. Services commit internally, and a flushed `pending` row with
        # `commit_at=NULL` made durable by that internal commit is invisible to both
        # the sweep (filters commit_at IS NOT NULL) and the lazy commit - a crash
        # before the COMMITTED stamp then bricks the form: the partial unique index
        # 409s every future action forever. Holding the row back also means two
        # concurrent immediate actions no longer collide on that index; the wrapped
        # method's own premise check arbitrates them, exactly as before this feature.
        result = self._execute(row, action)
        return DispatchResult(deferred=False, action_id=str(row.id), result=result)

    # ---------------- commit ----------------

    def commit_due(self, limit: int = 100) -> dict:
        """Scheduler sweep. Mirrors SlaTakeoverService.commit_due."""
        now = _utc_naive_now()
        rows = (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.status == FORM_ACTION_PENDING,
                SlaFormAction.commit_at.isnot(None),
                SlaFormAction.commit_at <= now,
            )
            .order_by(SlaFormAction.commit_at.asc())
            .limit(limit)
            .all()
        )
        committed = 0
        for row in rows:
            try:
                if self.commit_one(row):
                    committed += 1
            except Exception:
                logger.exception("Form action %s failed to commit", row.id)
        return {"scanned": len(rows), "committed": committed}

    def commit_one(self, row: SlaFormAction) -> bool:
        """Execute a due pending action exactly once.

        The sweep and the lazy-commit-on-read can both reach the same row. A
        conditional UPDATE with a rowcount check is what makes the loser a no-op -
        checking `row.status` in Python would let both pass (AC-D-6).

        The handler is resolved BEFORE the row is claimed, deliberately. Two
        processes can share this table without sharing a registry - every dev
        worktree points at one Postgres, and a rolling deploy briefly runs two
        versions - so a process that has never heard of `row.action_key` must
        leave the row exactly as it found it (PENDING) rather than claim it and
        then fail to run it: claiming first stamps `status=COMMITTED` while
        nothing has actually happened, and that status permanently stops any
        other sweep from ever looking at the row again - a silent, unrecoverable
        no-op the frontend reports as success (traced live: a folder's `Set
        company -> Shared` read `committed` with the folder never touched).

        The claim UPDATE is then deliberately left UNCOMMITTED: Postgres holds
        the row lock from the moment the statement runs, not from commit, so a
        concurrent claim attempt already blocks on it and re-reads `pending` as
        `committed` once we finally commit - AC-D-6 needs no commit of its own to
        hold. Committing the claim early used to stamp `status=committed` durable
        BEFORE the handler had written anything: a process that died between that
        commit and the handler's (a `--reload` restart mid-request, a SIGKILL) left
        the row reading `committed`, no `error_text`, with the domain mutation
        never applied - and nothing ever retries a row that already reads
        `committed`. Leaving the claim uncommitted folds it into the SAME
        transaction as the handler's own commit (or `_execute`'s rollback-then-
        refail on an exception): either both land, or neither does, and a crash
        before either leaves the row `pending` for the next sweep to pick up.

        The two rules cover different failure modes and both are load-bearing: the
        first stops a row being claimed by a process that cannot run it at all, the
        second stops a claimed row being stamped done before its work is durable.
        """
        try:
            action = action_for(row.action_key)
        except KeyError:
            logger.warning(
                "Form action %s has action_key %r, unregistered in this process; "
                "leaving it pending for a process that has it.",
                row.id,
                row.action_key,
            )
            return False

        claimed = (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.id == row.id,
                SlaFormAction.status == FORM_ACTION_PENDING,
            )
            .update(
                # Stamped together with the status. Setting the timestamp later leaves a
                # window where the row reads committed with committed_at NULL, and every
                # "last committed action" query then has to cope with a half-written row.
                {"status": FORM_ACTION_COMMITTED, "committed_at": _utc_naive_now()},
                synchronize_session=False,
            )
        )
        if not claimed:
            return False
        # No commit here - see the docstring. `refresh` still reads back what we
        # just wrote, visible to ourselves inside the same open transaction.
        self.db.refresh(row)

        try:
            self._execute(row, action, already_claimed=True)
        except AppException as exc:
            # A 4xx from the wrapped method means the premise changed under the pending
            # action - someone else decided the form, or it moved on. That is
            # `ineligible`, not `failed`: nothing is broken, the action simply must not
            # be applied on top of what happened instead (AC-D-8).
            if 400 <= int(getattr(exc, "status_code", 500)) < 500:
                # The reason is read off the screen, so it is the domain's sentence -
                # not `str(detail)`, which is the dict AppException wraps it in.
                self.void_as_ineligible(row, _reader_facing_error(exc, row))
                return False
            raise
        return True

    def _active_tracker_id(self, entity_type: str, entity_id: str) -> Optional[str]:
        """The unresolved form-SLA stage tracker for this form, if any.

        `conversation_sla_tracking.source_entity_id` is a uuid COLUMN, and since S6b a
        record action's entity id is a key rather than necessarily a uuid: a currency
        rate is addressed by its three-letter code, a stock-visibility policy at the
        access-type tier by that code, the sign-in background by a constant. Asking this
        question about one of those raised `invalid input syntax for type uuid` INSIDE
        the commit, which aborted the request's transaction and turned the poll that was
        watching the countdown into a 500.

        A record action never has a tracker anyway - a product is not a form submission -
        so a non-uuid key is answered without going to the database at all.
        """
        from app.models.sla import ConversationSLATracking

        try:
            uuid.UUID(str(entity_id))
        except (ValueError, AttributeError, TypeError):
            return None

        row = (
            self.db.query(ConversationSLATracking.id)
            .filter(
                ConversationSLATracking.source_entity_type == entity_type,
                ConversationSLATracking.source_entity_id == str(entity_id),
                ConversationSLATracking.is_resolved.is_(False),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .first()
        )
        return str(row[0]) if row else None

    def _execute(self, row: SlaFormAction, action, *, already_claimed: bool = False):
        """Run the real service method and stamp the outcome onto the history row.

        `already_claimed` (the deferred commit path) means the row is durable; the
        immediate path hands in a row that is NOT in the session yet, so the wrapped
        method's internal commits cannot persist it half-written.

        It also decides WHOSE company scope the handler runs under. A deferred commit
        happens on a session that never authorised anything - the scheduler sweep runs
        every company (`set_company_scope(db, None)`), and a lazy commit runs inside
        whichever request happened to poll - so the scope stored at park time is put
        back for the length of the call, and an action parked before that was stored
        runs UNSET, seeing nothing (see `_SCOPE_PAYLOAD_KEY`). The immediate path is
        already ON the requester's session and simply re-asserts what is there.
        """
        # The stage this action is about to close, recorded before it runs - the undo
        # has no other way to find which tracker to reopen once it is resolved.
        prior_tracking_id = self._active_tracker_id(
            row.source_entity_type, row.source_entity_id
        )
        scope = (
            _scope_from_payload(row.payload_json)
            if already_claimed
            else get_company_scope(self.db)
        )
        try:
            with company_scope(self.db, scope):
                # The requester rides along so a handler can re-check the gates of its own action
                # (a deferred delete runs after the request, with no user on the session).
                result = action.execute(
                    self.db,
                    {**_handler_payload(row.payload_json), "requested_by_id": row.requested_by_id},
                )
        except Exception as exc:
            self.db.rollback()
            # Never `str(exc)`: this string is shown to the user (see
            # `_reader_facing_error`). The exception itself goes to the log below.
            error_text = _reader_facing_error(exc, row)
            if already_claimed:
                row_id = str(row.id)
                self.db.query(SlaFormAction).filter(SlaFormAction.id == row_id).update(
                    {
                        "status": FORM_ACTION_FAILED,
                        "error_text": error_text,
                        "resolved_at": _utc_naive_now(),
                    },
                    synchronize_session=False,
                )
            else:
                # The row was never persisted; record the failure as a fresh terminal
                # row so the history still shows the attempt.
                row.status = FORM_ACTION_FAILED
                row.error_text = error_text
                row.resolved_at = _utc_naive_now()
                self.db.add(row)
            self.db.commit()
            logger.exception("Form action %s failed", row.id)
            raise

        # Whatever stage is open now is the one the commit spawned - unless nothing
        # advanced, in which case it is still the stage we started from and there is
        # nothing to void on an undo.
        spawned = self._active_tracker_id(row.source_entity_type, row.source_entity_id)

        row.status = FORM_ACTION_COMMITTED
        row.committed_at = _utc_naive_now()
        row.prior_tracking_id = prior_tracking_id
        row.spawned_tracking_id = spawned if spawned != prior_tracking_id else None
        # No-op for the already-persistent deferred row; first persistence for the
        # immediate one - it enters history already committed, never as `pending`.
        self.db.add(row)
        self.db.commit()
        _audit(
            self.db,
            row,
            "UPDATE",
            user_id=row.requested_by_id,
            description=f"Action applied ({row.action_key})",
        )
        return result

    # ---------------- in-grace cancel ----------------

    def cancel(
        self,
        action_id: str,
        *,
        actor_id: Optional[str],
        actor_is_admin: bool = False,
    ) -> SlaFormAction:
        """Withdraw a pending action. Nothing ran, so nothing is reversed and nobody
        is told (AC-IG-4/5)."""
        row = (
            self.db.query(SlaFormAction).filter(SlaFormAction.id == str(action_id)).first()
        )
        if row is None:
            raise handle_not_found("Form action", str(action_id))

        if row.status != FORM_ACTION_PENDING:
            # Already committed / cancelled / voided. Never a silent success - the
            # caller has to know an undo is now a different, heavier operation.
            raise handle_conflict(
                "That action has already been applied. Use Undo to reverse it."
            )

        is_requester = actor_id is not None and str(row.requested_by_id or "") == str(actor_id)
        if not (is_requester or actor_is_admin):
            raise handle_conflict("Only the person who started this action can undo it.")

        cancelled = (
            self.db.query(SlaFormAction)
            .filter(SlaFormAction.id == row.id, SlaFormAction.status == FORM_ACTION_PENDING)
            .update(
                {
                    "status": FORM_ACTION_CANCELLED,
                    "resolution_reason": "cancelled",
                    "resolved_at": _utc_naive_now(),
                },
                synchronize_session=False,
            )
        )
        if not cancelled:
            raise handle_conflict(
                "That action has already been applied. Use Undo to reverse it."
            )
        self.db.commit()
        self.db.refresh(row)
        _audit(
            self.db,
            row,
            "UPDATE",
            user_id=actor_id,
            description=f"Action withdrawn during its grace window ({row.action_key}); nothing was sent",
        )
        return row

    def void_as_ineligible(self, row: SlaFormAction, reason: str) -> None:
        """The form moved underneath a pending action, so it must not run (AC-D-8).

        No status filter: this is reached both from a still-`pending` row (pre-flight
        check) and from one `_execute` has already stamped `failed` after the wrapped
        method rejected it. Filtering on `pending` would silently no-op the second case
        and leave a premise failure looking like a system failure.
        """
        self.db.query(SlaFormAction).filter(SlaFormAction.id == row.id).update(
            {
                "status": FORM_ACTION_INELIGIBLE,
                "resolution_reason": "ineligible",
                "error_text": reason[:2000],
                "resolved_at": _utc_naive_now(),
            },
            synchronize_session=False,
        )
        self.db.commit()
        _audit(
            self.db,
            row,
            "UPDATE",
            user_id=row.requested_by_id,
            description=f"Action not applied ({row.action_key}) - {reason}",
        )

    # ---------------- post-grace undo ----------------

    def undo(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str,
        actor_id: Optional[str],
        reason: str,
        has_permission: bool,
    ) -> SlaFormAction:
        """Reverse the last committed action on a form.

        Order matters: restore the domain state first, then void the stage the action
        opened, then reopen the stage it closed. Notifications come last and are
        best-effort - the reversal has already committed by then, so a failed email
        must not turn a completed undo into a 500 the caller retries.
        """
        from app.services.form_action_registry import action_for
        from app.services import form_action_undo as undo_ops

        eligibility = undo_ops.evaluate(
            self.db,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            has_permission=has_permission,
        )
        if not eligibility.can_undo or eligibility.action is None:
            if eligibility.action is not None:
                _audit(
                    self.db,
                    eligibility.action,
                    "UPDATE",
                    user_id=actor_id,
                    description=f"Undo refused: {eligibility.blocked_reason}",
                )
            raise handle_conflict(
                _UNDO_REFUSAL_COPY.get(
                    eligibility.blocked_reason or "", "This action cannot be undone."
                )
            )

        row = eligibility.action
        # Claim it, so two undos racing on one form cannot both reverse it (AC-PGE-6).
        claimed = (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.id == row.id,
                SlaFormAction.status == FORM_ACTION_COMMITTED,
            )
            .update(
                {
                    "status": FORM_ACTION_UNDONE,
                    "undone_by_id": actor_id,
                    "undone_at": _utc_naive_now(),
                    "undo_reason": reason.strip(),
                },
                synchronize_session=False,
            )
        )
        if not claimed:
            raise handle_conflict("This action has already been undone.")
        self.db.commit()

        try:
            action = action_for(row.action_key)
            action.invert(self.db, row)

            if row.spawned_tracking_id:
                undo_ops.void_tracker(self.db, str(row.spawned_tracking_id), reason)
            if row.prior_tracking_id:
                undo_ops.reopen_tracker(self.db, str(row.prior_tracking_id))
        except Exception:
            # The claim is durable but the reversal did not complete. Left as-is the
            # row reads `undone` while the domain was never reversed, and every retry
            # is refused as already-undone - a permanent half-undo. Hand the claim
            # back so the retry can run the whole reversal again (invert and the
            # tracker ops are idempotent: they write absolute prior values).
            try:
                self.db.rollback()
                self.db.query(SlaFormAction).filter(SlaFormAction.id == row.id).update(
                    {
                        "status": FORM_ACTION_COMMITTED,
                        "undone_by_id": None,
                        "undone_at": None,
                        "undo_reason": None,
                    },
                    synchronize_session=False,
                )
                self.db.commit()
            except Exception:
                logger.exception(
                    "Could not release the undo claim on form action %s", row.id
                )
            raise

        try:
            from app.services.form_action_notify import notify_undo

            notify_undo(self.db, row, actor_id=actor_id, reason=reason)
        except Exception:
            # Post-commit side effect: warn, never raise. The caller would otherwise
            # get a 500 for an operation that actually succeeded, and the retry would
            # be refused as already-undone.
            logger.exception("Undo notifications failed for form action %s", row.id)

        _audit(
            self.db,
            row,
            "UPDATE",
            user_id=actor_id,
            description=f"Undo: {reason.strip()} (reversed {row.action_key})",
            old_values=dict(row.prior_state_json or {}),
        )

        self.db.refresh(row)
        return row

    # ---------------- reads ----------------

    def pending_for(self, entity_type: str, entity_id: str) -> Optional[SlaFormAction]:
        return (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.source_entity_type == entity_type,
                SlaFormAction.source_entity_id == str(entity_id),
                SlaFormAction.status == FORM_ACTION_PENDING,
            )
            .first()
        )

    def last_terminal_outcome(
        self, entity_type: str, entity_id: str
    ) -> Optional[SlaFormAction]:
        """The most recent action that ended `ineligible` or `failed`.

        Rides on GET /current so the FE can tell the user WHY a parked action
        vanished - without it, a deferred approve that was voided underneath (or blew
        up at commit) just disappears: banner gone, CTAs back, no signal, and the
        user walks away believing it applied (AC-U-4).
        """
        return (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.source_entity_type == entity_type,
                SlaFormAction.source_entity_id == str(entity_id),
                SlaFormAction.status.in_([FORM_ACTION_INELIGIBLE, FORM_ACTION_FAILED]),
            )
            .order_by(SlaFormAction.resolved_at.desc().nullslast())
            .first()
        )

    def last_committed(self, entity_type: str, entity_id: str) -> Optional[SlaFormAction]:
        """The only action a post-grace undo may reverse (AC-PG-1). No time limit."""
        return (
            self.db.query(SlaFormAction)
            .filter(
                SlaFormAction.source_entity_type == entity_type,
                SlaFormAction.source_entity_id == str(entity_id),
                SlaFormAction.status == FORM_ACTION_COMMITTED,
            )
            .order_by(SlaFormAction.committed_at.desc().nullslast())
            .first()
        )
