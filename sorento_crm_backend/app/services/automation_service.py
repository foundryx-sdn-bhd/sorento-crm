"""Automation CRUD + run executor."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.automation import Automation, AutomationRun
from app.models.email_template import EmailTemplate
from app.models.notification import Notification, NotificationDelivery
from app.models.user import User
from app.services import automation_recipients, automation_triggers
from app.services.email_template_service import EmailTemplateService
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)


def _resolve_owner_user_id(db: Session, automation: Automation) -> Optional[str]:
    """Pick a user_id for the underlying Notification rows.

    Prefer the automation's creator; fall back to any active protected user; then
    any active user. Returns None only if the system has no users at all.
    """
    if automation.created_by_user_id:
        u = db.query(User).filter(User.id == automation.created_by_user_id).first()
        if u is not None and not bool(getattr(u, "is_trashed", False)):
            return str(getattr(u, "id"))
    u = (
        db.query(User)
        .filter(User.is_trashed.is_(False), User.is_protected.is_(True))
        .order_by(User.created_at.asc())
        .first()
    )
    if u is None:
        u = (
            db.query(User)
            .filter(User.is_trashed.is_(False))
            .order_by(User.created_at.asc())
            .first()
        )
    return str(getattr(u, "id")) if u is not None else None


def _next_run_for_daily(run_time: Optional[time], timezone_name: str, *, after: Optional[datetime] = None) -> Optional[datetime]:
    """Compute the next UTC datetime when ``run_time`` will occur in ``timezone_name``.

    ``after`` defaults to now (UTC). Result is naive UTC for storage in
    ``automations.next_run_at`` (which uses ``DateTime(timezone=False)``).
    """
    if not run_time:
        return None
    try:
        tz = ZoneInfo(timezone_name or "Asia/Kuala_Lumpur")
    except Exception:
        tz = ZoneInfo("Asia/Kuala_Lumpur")
    after_utc = after or datetime.utcnow()
    after_aware = after_utc.replace(tzinfo=ZoneInfo("UTC"))
    after_local = after_aware.astimezone(tz)
    candidate_local = datetime.combine(after_local.date(), run_time, tzinfo=tz)
    if candidate_local <= after_local:
        candidate_local = candidate_local + timedelta(days=1)
    return candidate_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _compute_next_run(automation: Automation) -> Optional[datetime]:
    if not automation.enabled:
        return None
    if str(automation.schedule_type) != "daily":
        return None
    return _next_run_for_daily(
        automation.run_time,  # type: ignore[arg-type]
        str(automation.timezone or "Asia/Kuala_Lumpur"),
    )


@dataclass(frozen=True)
class _ExpiryBatchSpec:
    """How a scheduled expiry trigger stamps its batch and groups its email.

    Both members of the family (promotion end, certificate expiry) behave the
    same way: one batch id per run stamped on every kept row BEFORE the send, a
    deep link back to exactly that batch, and one grouped email per recipient
    listing every row they matched. Only the entity nouns differ, so they live in
    a table instead of a second copy of the code.
    """

    context_key: str  # "promotion" -> match.context["promotion"]
    plural_key: str  # "promotions" -> ctx key the email template loops over
    list_path: str  # frontend list route the batch link points at


# Membership here IS what grouping depends on, so every key must also carry
# supports_grouping=True on its TriggerSpec (automation_triggers.py) - that flag
# is how the FE decides whether to offer the "Combine into one email" switch.
# Adding a trigger here without the flag ships a grouping engine the user can
# never turn on; test_automation_trigger_catalog.py fails if the two disagree.
_EXPIRY_BATCH_SPECS: dict[str, _ExpiryBatchSpec] = {
    "days_before_promotion_end": _ExpiryBatchSpec(
        context_key="promotion",
        plural_key="promotions",
        list_path="/marketing-management/promotions",
    ),
    "days_before_certificate_expiry": _ExpiryBatchSpec(
        context_key="certificate",
        plural_key="certificates",
        list_path="/master-data-management/certificates",
    ),
}


def _expiry_batch_model(context_key: str):
    """The ORM model carrying ``expiry_notified_at`` / ``expiry_notify_batch_id``
    for a batch-stamped trigger. Imported lazily to keep this module light."""
    if context_key == "promotion":
        from app.models.marketing import Promotion

        return Promotion
    if context_key == "certificate":
        from app.models.certificate import Certificate

        return Certificate
    return None


class AutomationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- CRUD ----------
    def list(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> tuple[list[Automation], int]:
        q = self.db.query(Automation)
        if query and query.strip():
            term = f"%{query.strip()}%"
            q = q.filter(Automation.name.ilike(term))
        if enabled is not None:
            q = q.filter(Automation.enabled.is_(enabled))
        total = q.count()
        rows = (
            q.order_by(Automation.updated_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return rows, total

    def get(self, automation_id: str) -> Optional[Automation]:
        return self.db.query(Automation).filter(Automation.id == automation_id).first()

    def create(self, payload: dict[str, Any], user_id: Optional[str]) -> Automation:
        self._validate(payload)
        row = Automation(
            name=payload["name"],
            description=payload.get("description"),
            enabled=payload.get("enabled", True),
            trigger_type=payload["trigger_type"],
            trigger_config=payload.get("trigger_config") or {},
            action_type=payload.get("action_type") or "send_email",
            email_template_id=payload["email_template_id"],
            recipient_config=self._normalize_recipient_config(payload.get("recipient_config")),
            group_matches=payload.get("group_matches", True),
            conditions_json=self._validated_conditions(
                payload.get("conditions_json"), payload["trigger_type"]
            ),
            schedule_type=payload.get("schedule_type") or "manual",
            run_time=payload.get("run_time"),
            timezone=payload.get("timezone") or "Asia/Kuala_Lumpur",
            created_by_user_id=user_id,
        )
        row.next_run_at = _compute_next_run(row)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, automation_id: str, payload: dict[str, Any]) -> Automation:
        row = self.get(automation_id)
        if not row:
            raise AppException(status_code=404, message="Automation not found")
        if "recipient_config" in payload and payload["recipient_config"] is not None:
            payload["recipient_config"] = self._normalize_recipient_config(payload["recipient_config"])
        # conditions_json is set explicitly (not via the None-skipping loop below) so
        # an explicit null clears the filter (match all). Validate against the
        # incoming trigger_type when supplied, else the row's existing one.
        if "conditions_json" in payload:
            trigger_type = payload.get("trigger_type") or str(row.trigger_type)
            row.conditions_json = self._validated_conditions(
                payload["conditions_json"], trigger_type
            )
        for field in (
            "name",
            "description",
            "enabled",
            "trigger_type",
            "trigger_config",
            "action_type",
            "email_template_id",
            "recipient_config",
            "group_matches",
            "schedule_type",
            "run_time",
            "timezone",
        ):
            if field in payload and payload[field] is not None:
                setattr(row, field, payload[field])
        merged = {
            "trigger_type": row.trigger_type,
            "trigger_config": row.trigger_config,
            "email_template_id": row.email_template_id,
            "schedule_type": row.schedule_type,
            "run_time": row.run_time,
        }
        self._validate(merged)
        row.next_run_at = _compute_next_run(row)
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def toggle(self, automation_id: str, enabled: bool) -> Automation:
        row = self.get(automation_id)
        if not row:
            raise AppException(status_code=404, message="Automation not found")
        row.enabled = enabled
        row.next_run_at = _compute_next_run(row)
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, automation_id: str) -> None:
        row = self.get(automation_id)
        if not row:
            raise AppException(status_code=404, message="Automation not found")
        self.db.delete(row)
        self.db.commit()

    # ---------- Runs ----------
    def list_runs(
        self,
        automation_id: str,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[AutomationRun], int]:
        q = self.db.query(AutomationRun).filter(AutomationRun.automation_id == automation_id)
        total = q.count()
        rows = (
            q.order_by(AutomationRun.started_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return rows, total

    def run_now(self, automation_id: str, *, run_mode: str = "manual") -> dict[str, Any]:
        automation = self.get(automation_id)
        if not automation:
            raise AppException(status_code=404, message="Automation not found")
        return self._execute(automation, run_mode=run_mode)

    def dispatch_event(
        self,
        trigger_type: str,
        *,
        context: dict[str, Any],
        source_kind: str,
        source_id: str,
    ) -> dict[str, Any]:
        """Run every enabled automation for ``trigger_type`` against a single match.

        Used by domain code (e.g. complaint approval) to fire on-event automations
        immediately, bypassing the daily ``evaluate_due`` scheduler. Failures are
        per-automation; one bad rule does not block the rest.
        """
        automations = (
            self.db.query(Automation)
            .filter(
                Automation.enabled.is_(True),
                Automation.trigger_type == trigger_type,
            )
            .all()
        )
        match = automation_triggers.TriggerMatch(
            context=dict(context or {}),
            source_kind=source_kind,
            source_id=source_id,
        )
        results: list[dict[str, Any]] = []
        for automation in automations:
            try:
                results.append(
                    self._execute(
                        automation,
                        run_mode="event",
                        prebuilt_matches=[match],
                    )
                )
            except Exception:
                logger.exception(
                    "dispatch_event(%s) failed for automation %s",
                    trigger_type,
                    automation.id,
                )
        return {"trigger_type": trigger_type, "fired": len(results), "results": results}

    def send_match(
        self, automation: Automation, match: "automation_triggers.TriggerMatch"
    ) -> dict[str, Any]:
        """Run ONE automation against one prebuilt match whether or not it is enabled: an
        explicit send ("Send to purchasing", `oi_handover_resend`) that a switched-off
        automation must not swallow. Records its run and notification like any other."""
        return self._execute(automation, run_mode="manual", prebuilt_matches=[match])

    def evaluate_due(self) -> dict[str, Any]:
        """Master scheduler entrypoint: run every enabled automation whose next_run_at is due."""
        now = datetime.utcnow()
        due = (
            self.db.query(Automation)
            .filter(
                Automation.enabled.is_(True),
                Automation.next_run_at.isnot(None),
                Automation.next_run_at <= now,
            )
            .all()
        )
        ran = 0
        errors = 0
        for automation in due:
            try:
                self._execute(automation, run_mode="scheduled")
                ran += 1
            except Exception:
                logger.exception("Scheduled automation %s failed", automation.id)
                errors += 1
        return {"due": len(due), "ran": ran, "errors": errors}

    # ---------- Internal ----------
    def _validate(self, payload: dict[str, Any]) -> None:
        if payload.get("trigger_type") not in {s.type for s in automation_triggers.list_specs()}:
            raise AppException(
                status_code=400,
                message=f"Unknown trigger_type: {payload.get('trigger_type')}",
            )
        template = (
            self.db.query(EmailTemplate)
            .filter(EmailTemplate.id == payload["email_template_id"])
            .first()
        )
        if not template:
            raise AppException(status_code=400, message="email_template_id does not exist")
        schedule_type = payload.get("schedule_type") or "manual"
        if schedule_type not in {"manual", "daily"}:
            raise AppException(status_code=400, message="schedule_type must be 'manual' or 'daily'")
        if schedule_type == "daily" and not payload.get("run_time"):
            raise AppException(status_code=400, message="run_time required when schedule_type='daily'")

    @staticmethod
    def _validated_conditions(
        tree: Any, trigger_type: str
    ) -> Optional[dict[str, Any]]:
        """Validate a rule condition tree against the trigger's fact sources.

        Empty / None → None (match all, backward compatible). A non-empty tree
        with problems raises a 422 whose body is the raw problems ARRAY
        (``{"detail": [...]}``) - matching FoundryX so the FE reads the list
        directly (not the AppException string envelope).
        """
        if not tree:
            return None
        if not isinstance(tree, dict):
            raise HTTPException(status_code=422, detail=["Conditions must be a group."])
        from app.rule_engine.schemas import validate_tree

        problems = validate_tree(tree, automation_triggers.fact_sources_for(trigger_type))
        if problems:
            raise HTTPException(status_code=422, detail=problems)
        return tree

    @staticmethod
    def _normalize_recipient_config(config: Any) -> dict[str, Any]:
        if not config:
            return {
                "user_ids": [],
                "role_ids": [],
                "include_promotion_owner": False,
                "include_actor": False,
                # PLAN-oi-request-cs-reserve.md 3.6 (review round): the falsy-config
                # branch used to build its own dict from scratch and never mention
                # either key, so a caller reading `include_raiser` off a freshly-created
                # automation's default `recipient_config` got `None`, not `False`.
                "include_raiser": False,
                "include_requester": False,
                "one_email": False,
                "extra_emails": [],
            }
        if hasattr(config, "model_dump"):
            data = config.model_dump()
        elif isinstance(config, dict):
            data = config
        else:
            data = {}
        return {
            "user_ids": [str(x) for x in (data.get("user_ids") or [])],
            "role_ids": [str(x) for x in (data.get("role_ids") or [])],
            "include_promotion_owner": bool(data.get("include_promotion_owner", False)),
            "include_assigned_cs_pic": bool(data.get("include_assigned_cs_pic", False)),
            # Cc the person who raised the triggering event (AC-H11, PLAN-scm-oi-
            # handover-email.md section 3.5). Reusable: any trigger context that
            # puts `actor` on the context earns this checkbox for free.
            "include_actor": bool(data.get("include_actor", False)),
            # PLAN-oi-request-cs-reserve.md 3.6: Cc the person who raised the order
            # inquiry, or the person who requested the reserve - reusable the same way
            # `include_actor` is, wherever a trigger's context puts `raiser`/`requester`.
            "include_raiser": bool(data.get("include_raiser", False)),
            "include_requester": bool(data.get("include_requester", False)),
            # One email for the whole match, every resolved address on it (AC-H26) -
            # `_send_per_match`'s opt-in; off by default so a promotion/certificate
            # template that personalises `{{ recipient.name }}` per copy is unaffected.
            "one_email": bool(data.get("one_email", False)),
            "extra_emails": [str(x).strip() for x in (data.get("extra_emails") or []) if str(x).strip()],
        }

    def _execute(
        self,
        automation: Automation,
        *,
        run_mode: str,
        prebuilt_matches: Optional[list["automation_triggers.TriggerMatch"]] = None,
    ) -> dict[str, Any]:
        run = AutomationRun(
            automation_id=str(automation.id),
            run_mode=run_mode,
            status="running",
            recipients_attempted=0,
            recipients_delivered=0,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        started_at = run.started_at or datetime.utcnow()
        owner_user_id = _resolve_owner_user_id(self.db, automation)

        try:
            template = (
                self.db.query(EmailTemplate)
                .filter(EmailTemplate.id == automation.email_template_id)
                .first()
            )
            if not template:
                raise AppException(status_code=400, message="Template missing")

            if prebuilt_matches is not None:
                matches = list(prebuilt_matches)
            else:
                matches = automation_triggers.fire(
                    self.db,
                    str(automation.trigger_type),
                    dict(automation.trigger_config or {}),
                    str(automation.timezone or "Asia/Kuala_Lumpur"),
                )

            # Rule filter: keep only matches that pass the automation's
            # conditions_json. Empty tree, or a match with no fact_sources, keeps
            # everything (backward compatible).
            matches = self._filter_matches_by_conditions(automation, matches)

            # Batch stamp (the scheduled expiry triggers): mint one batch id,
            # stamp every kept row (stamp-first, before send), and build the deep
            # link the reminder email points at.
            batch_spec = _EXPIRY_BATCH_SPECS.get(str(automation.trigger_type))
            batch_id: Optional[str] = None
            batch_link: Optional[str] = None
            if batch_spec is not None:
                batch_id, batch_link = self._stamp_expiry_batch(matches, batch_spec)

            template_service = EmailTemplateService(self.db)

            # Group only the scheduled expiry triggers: they are the multi-match
            # ones and their templates render a list (`promotions` /
            # `certificates`). Other (event-driven) triggers always have one match
            # and singular-entity templates, so they always take the per-match path.
            do_group = bool(getattr(automation, "group_matches", True)) and (
                batch_spec is not None
            )

            if do_group:
                attempted, summary = self._send_grouped(
                    automation, run, matches, template, template_service, owner_user_id,
                    batch_id=batch_id, batch_link=batch_link,
                    spec=batch_spec,
                )
            else:
                attempted, summary = self._send_per_match(
                    automation, run, matches, template, template_service, owner_user_id
                )

            self._maybe_enqueue_worker()

            run.recipients_attempted = attempted
            run.recipients_delivered = 0  # updated by delivery worker downstream
            run.status = "success"
            run.finished_at = datetime.utcnow()
            run.duration_ms = int((run.finished_at - started_at).total_seconds() * 1000)
            run.summary = summary

            automation.last_run_at = datetime.utcnow()
            automation.last_status = "success"
            automation.last_error = None
            automation.next_run_at = _compute_next_run(automation)
            self.db.commit()
            self.db.refresh(run)
            return {
                "run_id": str(run.id),
                "status": "success",
                "recipients_attempted": attempted,
                "summary": run.summary,
            }
        except Exception as exc:
            self.db.rollback()
            run = (
                self.db.query(AutomationRun)
                .filter(AutomationRun.id == run.id)
                .first()
            )
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.utcnow()
                run.duration_ms = int((run.finished_at - started_at).total_seconds() * 1000)
                run.error = str(exc)
            automation_row = self.get(str(automation.id))
            if automation_row is not None:
                automation_row.last_run_at = datetime.utcnow()
                automation_row.last_status = "failed"
                automation_row.last_error = str(exc)
                automation_row.next_run_at = _compute_next_run(automation_row)
            self.db.commit()
            logger.exception("Automation %s execution failed", automation.id)
            raise

    def _filter_matches_by_conditions(
        self,
        automation: Automation,
        matches: list["automation_triggers.TriggerMatch"],
    ) -> list["automation_triggers.TriggerMatch"]:
        """Keep matches passing the automation's rule tree (conditions_json).

        Empty tree → keep all. A match carrying no ``fact_sources`` (trigger
        exposes no facts) → keep it. Otherwise resolve only the facts the tree
        reads and evaluate fail-closed.
        """
        tree = automation.conditions_json
        if not tree:
            return matches
        from app.rule_engine.evaluator import collect_fact_keys, evaluate
        from app.rule_engine.registry import resolve_facts

        keys = collect_fact_keys(tree)
        kept: list["automation_triggers.TriggerMatch"] = []
        for m in matches:
            fact_sources = getattr(m, "fact_sources", None)
            if not fact_sources:
                kept.append(m)
                continue
            facts = resolve_facts(self.db, fact_sources, only_keys=keys)
            if evaluate(tree, facts):
                kept.append(m)
        return kept

    def _stamp_expiry_batch(
        self,
        matches: list["automation_triggers.TriggerMatch"],
        spec: _ExpiryBatchSpec,
    ) -> tuple[Optional[str], Optional[str]]:
        """Mint a batch id, stamp every kept row, commit (stamp-first), and
        return ``(batch_id, batch_link)``. No matches → ``(None, None)``."""
        if not matches:
            return None, None
        from uuid import uuid4

        from app.config import settings
        from app.models.base import company_scope

        model = _expiry_batch_model(spec.context_key)
        if model is None:
            return None, None

        batch_id = str(uuid4())
        now = datetime.utcnow()
        for m in matches:
            row = None
            fact_sources = getattr(m, "fact_sources", None)
            if fact_sources and isinstance(fact_sources.get(spec.context_key), model):
                row = fact_sources[spec.context_key]
            if row is None:
                # Both models are company-scoped and this runs on a scheduler
                # session (scope UNSET = fail-closed), so the fallback re-query
                # has to run all-companies or it silently finds nothing.
                with company_scope(self.db, None):
                    row = self.db.query(model).filter(model.id == m.source_id).first()
            if row is not None:
                row.expiry_notified_at = now
                row.expiry_notify_batch_id = batch_id
        self.db.commit()

        base = (settings.frontend_base_url or "").rstrip("/")
        path = f"{spec.list_path}?expiry_notify_batch_id={batch_id}"
        batch_link = f"{base}{path}" if base else path
        return batch_id, batch_link

    def _send_per_match(
        self,
        automation: Automation,
        run: AutomationRun,
        matches: list["automation_triggers.TriggerMatch"],
        template: EmailTemplate,
        template_service: EmailTemplateService,
        owner_user_id: Optional[str],
    ) -> tuple[int, dict[str, Any]]:
        """One email per (match × recipient) - the original behavior, UNLESS
        ``recipient_config.one_email`` (AC-H26): then one email for the whole match,
        every resolved address on it, the actor's own address moved LAST so
        ``notification_tasks.py``'s To/Cc split leaves them on Cc rather than
        displacing the real recipient from To. Opt-in only - the promotion/certificate
        templates this same method sends for (when their own ``group_matches`` is off)
        personalise ``{{ recipient.name }}`` per copy, so the default stays untouched.
        """
        attempted = 0
        per_match: list[dict[str, Any]] = []
        one_email = bool(dict(automation.recipient_config or {}).get("one_email"))
        for match in matches:
            recipients = automation_recipients.resolve_recipients(
                self.db,
                dict(automation.recipient_config or {}),
                promotion_context=match.context,
                source_id=match.source_id,
            )
            if one_email and recipients:
                actor_email = (
                    ((match.context.get("actor") or {}).get("email") or "").strip().lower()
                )
                ordered = recipients
                if actor_email:
                    non_actor = [
                        r for r in recipients if r["email"].strip().lower() != actor_email
                    ]
                    actor_entries = [
                        r for r in recipients if r["email"].strip().lower() == actor_email
                    ]
                    ordered = non_actor + actor_entries
                primary = ordered[0]
                ctx = dict(match.context)
                ctx["recipient"] = {
                    "name": primary.get("name") or primary["email"],
                    "email": primary["email"],
                }
                rendered = template_service.render(template, ctx)
                self._enqueue_email(
                    owner_user_id=owner_user_id,
                    recipient=primary,
                    subject=rendered["subject"],
                    body_html=rendered["body_html"],
                    body_text=rendered["body_text"],
                    metadata={
                        "automation_id": str(automation.id),
                        "automation_run_id": str(run.id),
                        "promotion_id": match.source_id,
                        "source_kind": match.source_kind,
                        "source_id": match.source_id,
                        "trigger_type": str(automation.trigger_type),
                        # #1312: the OI handover's own per-line files, forwarded only
                        # when the dispatch context actually carries them (AC-E1/AC-E5)
                        # - `notification_tasks._enqueue_email_for_delivery` reads this
                        # back off `Notification.data` into the outbox row.
                        **(
                            {"extra_attachments": match.context["email_attachments"]}
                            if match.context.get("email_attachments")
                            else {}
                        ),
                        # Who pressed "Send to purchasing", for the next preview's "Last
                        # sent" line (`oi_handover_resend`).
                        **(
                            {"sent_by": match.context["sent_by"]}
                            if match.context.get("sent_by")
                            else {}
                        ),
                    },
                    recipient_emails=[r["email"] for r in ordered],
                )
                attempted += len(ordered)
                per_match.append(
                    {
                        "source_kind": match.source_kind,
                        "source_id": match.source_id,
                        "recipients": [
                            {"email": r["email"], "subject": rendered["subject"]}
                            for r in ordered
                        ],
                    }
                )
                continue

            rendered_per_recipient: list[dict[str, Any]] = []
            for recipient in recipients:
                ctx = dict(match.context)
                ctx["recipient"] = {
                    "name": recipient.get("name") or recipient["email"],
                    "email": recipient["email"],
                }
                rendered = template_service.render(template, ctx)
                self._enqueue_email(
                    owner_user_id=owner_user_id,
                    recipient=recipient,
                    subject=rendered["subject"],
                    body_html=rendered["body_html"],
                    body_text=rendered["body_text"],
                    # #1312: `email_attachments` is NOT forwarded here - only the
                    # ONE_EMAIL branch above does (AC-E2 is about the OI handover,
                    # which always dispatches one_email=True).
                    metadata={
                        "automation_id": str(automation.id),
                        "automation_run_id": str(run.id),
                        "promotion_id": match.source_id,
                        "source_kind": match.source_kind,
                        "source_id": match.source_id,
                        "trigger_type": str(automation.trigger_type),
                        **(
                            {"sent_by": match.context["sent_by"]}
                            if match.context.get("sent_by")
                            else {}
                        ),
                    },
                )
                attempted += 1
                rendered_per_recipient.append(
                    {"email": recipient["email"], "subject": rendered["subject"]}
                )
            per_match.append(
                {
                    "source_kind": match.source_kind,
                    "source_id": match.source_id,
                    "recipients": rendered_per_recipient,
                }
            )
        summary = {
            "grouped": False,
            "matches": len(per_match),
            "recipients_attempted": attempted,
            "matches_detail": per_match[:50],  # keep summary bounded
        }
        return attempted, summary

    def _send_grouped(
        self,
        automation: Automation,
        run: AutomationRun,
        matches: list["automation_triggers.TriggerMatch"],
        template: EmailTemplate,
        template_service: EmailTemplateService,
        owner_user_id: Optional[str],
        batch_id: Optional[str] = None,
        batch_link: Optional[str] = None,
        spec: Optional[_ExpiryBatchSpec] = None,
    ) -> tuple[int, dict[str, Any]]:
        """One combined email per recipient listing every row they match.

        Recipients are still resolved per-match so per-row entitlement
        (include_promotion_owner / include_assigned_cs_pic) is respected - a
        recipient only receives the rows they are actually entitled to.

        ``spec`` names the entity: a promotion run renders `promotions` /
        `promotion` / `promotions_count`, a certificate run renders
        `certificates` / `certificate` / `certificates_count`. The singular key
        stays for templates that only show the first row.
        """
        spec = spec or _EXPIRY_BATCH_SPECS["days_before_promotion_end"]
        # email(lower) -> {"recipient": {...}, "entities": [...], "source_ids": [...]}
        buckets: dict[str, dict[str, Any]] = {}
        for match in matches:
            recipients = automation_recipients.resolve_recipients(
                self.db,
                dict(automation.recipient_config or {}),
                promotion_context=match.context,
                source_id=match.source_id,
            )
            entity = match.context.get(spec.context_key) or {}
            for recipient in recipients:
                key = recipient["email"].lower()
                bucket = buckets.setdefault(
                    key,
                    {"recipient": recipient, "entities": [], "source_ids": []},
                )
                bucket["entities"].append(entity)
                bucket["source_ids"].append(match.source_id)

        today = matches[0].context.get("today") if matches else None
        attempted = 0
        groups_detail: list[dict[str, Any]] = []
        for bucket in buckets.values():
            recipient = bucket["recipient"]
            entities = bucket["entities"]
            ctx: dict[str, Any] = {
                spec.plural_key: entities,
                spec.context_key: entities[0],  # back-compat: singular = first row
                f"{spec.plural_key}_count": len(entities),
                "today": today,
                "recipient": {
                    "name": recipient.get("name") or recipient["email"],
                    "email": recipient["email"],
                },
                "batch_link": batch_link,
                "expiry_notify_batch_id": batch_id,
            }
            rendered = template_service.render(template, ctx)
            self._enqueue_email(
                owner_user_id=owner_user_id,
                recipient=recipient,
                subject=rendered["subject"],
                body_html=rendered["body_html"],
                body_text=rendered["body_text"],
                metadata={
                    "automation_id": str(automation.id),
                    "automation_run_id": str(run.id),
                    f"{spec.context_key}_ids": bucket["source_ids"],
                    "source_kind": f"{spec.context_key}_group",
                    "source_id": str(run.id),
                    "trigger_type": str(automation.trigger_type),
                },
            )
            attempted += 1
            groups_detail.append(
                {
                    "email": recipient["email"],
                    spec.plural_key: len(entities),
                    "subject": rendered["subject"],
                }
            )
        summary = {
            "grouped": True,
            "matches": len(matches),
            "emails_sent": attempted,
            "recipients_attempted": attempted,
            "groups_detail": groups_detail[:50],  # keep summary bounded
        }
        return attempted, summary

    def _enqueue_email(
        self,
        *,
        owner_user_id: Optional[str],
        recipient: dict[str, Any],
        subject: str,
        body_html: str,
        body_text: str,
        metadata: dict[str, Any],
        recipient_emails: Optional[list[str]] = None,
    ) -> str:
        """Insert one Notification + email NotificationDelivery row.

        Reuses the existing ``notification_deliveries`` infra so the queued send
        appears in System Management → Outgoing Mails. The actual SMTP send is
        handled later by ``send_notification_deliveries`` against pending rows.

        ``recipient_emails`` defaults to the single ``recipient`` (the original, one
        email per recipient shape) - `_send_per_match`'s ``one_email`` branch (AC-H26)
        is the one caller that passes the WHOLE resolved list, so
        ``notification_tasks.py``'s own To/Cc split (first address To, the rest Cc)
        puts everyone on one thread instead of a separate copy each.
        """
        if owner_user_id is None:
            raise AppException(
                status_code=400,
                message="No system user available to author outgoing emails",
            )
        notif = Notification(
            user_id=owner_user_id,
            type="automation_email",
            title=subject,
            body=body_text,
            data={
                "single_email_to_all": True,
                "recipient_emails": recipient_emails or [recipient["email"]],
                "recipient_name": recipient.get("name"),
                "body_html": body_html,
                "body_text": body_text,
                **metadata,
            },
            source_entity_type="automation_run",
            source_entity_id=metadata.get("automation_run_id"),
            event_type=(
                f"recipient:{recipient['email'].lower()}"
                f":source:{metadata.get('source_kind', 'match')}"
                f":id:{metadata.get('source_id', '')}"
            ),
        )
        self.db.add(notif)
        self.db.flush()
        delivery = NotificationDelivery(
            notification_id=str(notif.id),
            channel="email",
            status="pending",
        )
        self.db.add(delivery)
        self.db.commit()
        try:
            from app.services.queue_service import enqueue_job
            from app.tasks import notification_tasks

            enqueue_job(
                notification_tasks.send_notification_deliveries,
                str(notif.id),
                queue_name="notifications",
            )
        except Exception:
            logger.debug("Notifications queue unavailable; delivery row %s stays pending", delivery.id)
        return str(delivery.id)

    @staticmethod
    def _maybe_enqueue_worker() -> None:
        """Reserved hook (per-recipient enqueue happens in _enqueue_email)."""
        return None
