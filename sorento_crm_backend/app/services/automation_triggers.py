"""Trigger registry for automations.

A trigger turns an automation row into a list of {context} payloads, one per
match. The first concrete trigger watches promotion expiry: ``X days before
promotion end``; ``days_before_certificate_expiry`` is its certificate twin.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import company_scope
from app.models.certificate import (
    CERTIFICATE_STATUS_ACTIVE,
    Certificate,
    CertificateProduct,
    CertificateRevision,
)
from app.models.marketing import Promotion


@dataclass(frozen=True)
class TriggerSpec:
    type: str
    label: str
    description: str
    config_schema: dict[str, Any]
    # Rule-engine fact sources this trigger exposes. Empty = no rule filtering
    # (conditions_json is ignored). days_before_promotion_end -> ("promotion",).
    fact_sources: tuple[str, ...] = ()
    # True when several matches from one run can be folded into a single email
    # per recipient, i.e. the automation's group_matches flag means anything.
    # Grouping is actually implemented by automation_service._EXPIRY_BATCH_SPECS,
    # so this flag MUST be True for exactly the keys in that dict and nothing
    # else - test_automation_trigger_catalog.py asserts the two cannot drift.
    # The FE reads it to decide whether to render the "Combine into one email"
    # switch, instead of hardcoding a trigger type.
    supports_grouping: bool = False


@dataclass(frozen=True)
class TriggerMatch:
    context: dict[str, Any]
    source_kind: str
    source_id: str
    # ORM objects keyed by fact-source name (e.g. {"promotion": <Promotion>}),
    # fed to rule_engine.resolve_facts for conditions_json filtering. None when
    # the trigger exposes no fact sources.
    fact_sources: Optional[dict] = None


TriggerFn = Callable[[Session, dict[str, Any], str], Iterable[TriggerMatch]]

_REGISTRY: dict[str, tuple[TriggerSpec, TriggerFn]] = {}


def register(spec: TriggerSpec, fn: TriggerFn) -> None:
    _REGISTRY[spec.type] = (spec, fn)


def list_specs() -> list[TriggerSpec]:
    return [s for s, _ in _REGISTRY.values()]


def fire(
    db: Session,
    trigger_type: str,
    config: dict[str, Any],
    timezone: str,
) -> list[TriggerMatch]:
    pair = _REGISTRY.get(trigger_type)
    if not pair:
        raise ValueError(f"Unknown trigger type: {trigger_type}")
    _, fn = pair
    return list(fn(db, config or {}, timezone))


def _today_in_tz(timezone: str) -> date:
    try:
        from datetime import datetime as _dt

        return _dt.now(ZoneInfo(timezone)).date()
    except Exception:
        return date.today()


def _build_promotion_link(promotion_id: str) -> str:
    base = (settings.frontend_base_url or "").rstrip("/")
    if not base:
        return f"/marketing-management/promotions/{promotion_id}"
    return f"{base}/marketing-management/promotions/{promotion_id}"


def _build_certificate_link(certificate_id: str) -> str:
    """Internal, in-system deep link to one certificate.

    Reminder recipients are staff, so they get the CRM detail page and never a
    public ``/view?token=`` link (REM-4). The deep-link-after-login layout
    carries them back here once they sign in.
    """
    base = (settings.frontend_base_url or "").rstrip("/")
    path = f"/master-data-management/certificates/{certificate_id}"
    return f"{base}{path}" if base else path


def _build_complaint_link(complaint_id: str) -> str:
    base = (settings.frontend_base_url or "").rstrip("/")
    if not base:
        return f"/complaint-management/complaints/{complaint_id}"
    return f"{base}/complaint-management/complaints/{complaint_id}"


def _build_purchase_request_link(request_id: str, request_type: str | None = None) -> str:
    base = (settings.frontend_base_url or "").rstrip("/")
    if (request_type or "").strip() == "sponsorship_form":
        path = f"/procurement-management/sponsorship-forms/{request_id}"
    else:
        path = f"/procurement-management/purchase-requests/{request_id}"
    return f"{base}{path}" if base else path


def _trigger_days_before_promotion_end(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    days_before = int(config.get("days_before", 7) or 7)
    today = _today_in_tz(timezone)
    target_end = today + timedelta(days=days_before)

    rows = (
        db.query(Promotion)
        .filter(Promotion.end_date == target_end, Promotion.is_active.is_(True))
        .all()
    )
    for promo in rows:
        end_date = getattr(promo, "end_date")
        start_date = getattr(promo, "start_date")
        days_until_end = (end_date - today).days if end_date else None
        ctx = {
            "promotion": {
                "id": str(getattr(promo, "id")),
                "code": "",
                "name": str(getattr(promo, "description", "") or ""),
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "link": _build_promotion_link(str(getattr(promo, "id"))),
                "days_until_end": days_until_end,
                "created_by": getattr(promo, "created_by", None),
            },
            "today": today.isoformat(),
        }
        yield TriggerMatch(
            context=ctx,
            source_kind="promotion",
            source_id=str(getattr(promo, "id")),
            # The ORM object itself feeds rule_engine.resolve_facts so the
            # automation's conditions_json can filter on access levels / name / dates.
            fact_sources={"promotion": promo},
        )


register(
    TriggerSpec(
        type="days_before_promotion_end",
        label="Days before promotion end",
        description="Fires for every active promotion whose end_date is exactly X days from today (in the automation's timezone).",
        config_schema={
            "type": "object",
            "properties": {
                "days_before": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 7,
                    "title": "Days before promotion ends",
                }
            },
            "required": ["days_before"],
        },
        fact_sources=("promotion",),
        supports_grouping=True,
    ),
    _trigger_days_before_promotion_end,
)


def _trigger_days_before_certificate_expiry(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Certificates whose CURRENT revision expires exactly ``days_before`` days
    from today, in the automation's timezone.

    Exact-date semantics, identical to ``_trigger_days_before_promotion_end``:
    three automation rows (90 / 30 / 7) produce three independent reminders with
    no new code, and there is deliberately NO catch-up window - a scheduler
    outage on the match day loses that window's email (REM-7), which the
    validity-scoped list filter mitigates instead.

    Three exclusions fall out of the query rather than needing branches: an
    archived certificate fails the status filter; a NULL ``valid_until`` never
    equals a date, so an unknown expiry stays inert rather than reading as "no
    expiry"; and the join is on ``current_revision_id`` (re-checking
    ``is_current``), so a superseded revision's window never fires a reminder.
    """
    days_before = int(config.get("days_before", 30) or 30)
    today = _today_in_tz(timezone)
    target_expiry = today + timedelta(days=days_before)

    # A scheduler session never ran the company resolver, so its scope is UNSET -
    # fail-closed, which would return zero certificates. This sweep is a system
    # job that must see every company; the context manager restores whatever
    # scope the caller had.
    with company_scope(db, None):
        rows: list[Any] = (
            db.query(Certificate, CertificateRevision)
            .join(
                CertificateRevision,
                CertificateRevision.id == Certificate.current_revision_id,
            )
            .filter(
                Certificate.status == CERTIFICATE_STATUS_ACTIVE,
                CertificateRevision.is_current.is_(True),
                CertificateRevision.valid_until == target_expiry,
            )
            .order_by(Certificate.scheme, Certificate.certificate_number)
            .all()
        )
        coverage: dict[str, int] = {}
        if rows:
            coverage = {
                str(certificate_id): int(total)
                for certificate_id, total in db.query(
                    CertificateProduct.certificate_id,
                    func.count(CertificateProduct.id),
                )
                .filter(
                    CertificateProduct.certificate_id.in_(
                        [str(cert.id) for cert, _ in rows]
                    )
                )
                .group_by(CertificateProduct.certificate_id)
                .all()
            }

    for cert, revision in rows:
        valid_until = revision.valid_until
        ctx = {
            "certificate": {
                "id": str(cert.id),
                "scheme": cert.scheme,
                "certificate_number": cert.certificate_number,
                "certifying_body": cert.certifying_body or "",
                "issuer": cert.issuer or "",
                "title": cert.title or "",
                "status": cert.status,
                "revision_no": revision.revision_no,
                "valid_from": (
                    revision.valid_from.isoformat() if revision.valid_from else None
                ),
                "valid_until": valid_until.isoformat() if valid_until else None,
                "days_until_expiry": (valid_until - today).days if valid_until else None,
                "covered_product_count": coverage.get(str(cert.id), 0),
                "link": _build_certificate_link(str(cert.id)),
                "created_by": cert.created_by,
            },
            "today": today.isoformat(),
        }
        yield TriggerMatch(
            context=ctx,
            source_kind="certificate",
            source_id=str(cert.id),
            # The ORM row feeds rule_engine.resolve_facts, so conditions_json can
            # scope the automation to a scheme, a certifying body or a company.
            fact_sources={"certificate": cert},
        )


register(
    TriggerSpec(
        type="days_before_certificate_expiry",
        label="Days before certificate expiry",
        description=(
            "Fires for every active certificate whose current revision expires exactly "
            "X days from today (in the automation's timezone). Archived certificates, "
            "certificates with no expiry date and superseded revisions never match."
        ),
        config_schema={
            "type": "object",
            "properties": {
                "days_before": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 30,
                    "title": "Days before the certificate expires",
                }
            },
            "required": ["days_before"],
        },
        fact_sources=("certificate",),
        supports_grouping=True,
    ),
    _trigger_days_before_certificate_expiry,
)


def fact_sources_for(trigger_type: str) -> tuple[str, ...]:
    """Fact sources a trigger exposes for rule filtering (empty if unknown)."""
    pair = _REGISTRY.get(trigger_type)
    return pair[0].fact_sources if pair else ()


def _trigger_complaint_approved(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven trigger; pull-mode evaluation yields nothing.

    Matches are produced via :meth:`AutomationService.dispatch_event` from the
    complaint approval code path, so scheduled ``evaluate_due`` runs deliberately
    return no matches here.
    """
    return []


register(
    TriggerSpec(
        type="complaint_approved",
        label="Complaint approved",
        description="Fires when a complaint transitions to 'approved' (event-driven, dispatched from the approval flow).",
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_complaint_approved,
)


def _trigger_complaint_settled_on_site(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven trigger; pull-mode evaluation yields nothing.

    Matches come from :meth:`AutomationService.dispatch_event`, fired by the skip
    engine's complaint adapter. Deliberately separate from ``complaint_approved``:
    a settled complaint is a different business outcome and its context carries
    ``status='settled_on_site'``, so reusing the approval trigger would hand every
    subscribed automation a status that is not true.
    """
    return []


register(
    TriggerSpec(
        type="complaint_settled_on_site",
        label="Complaint settled on site",
        description=(
            "Fires when a complaint is closed as settled on site - the technician fixed "
            "the issue during the visit, so no replacement is arranged and customer "
            "service is never assigned (event-driven, dispatched from the skip flow)."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_complaint_settled_on_site,
)


def _trigger_complaint_technical_response_updated(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven trigger; pull-mode evaluation yields nothing.

    Matches are produced via :meth:`AutomationService.dispatch_event` from the
    complaint update-and-reply code path (when the technical-team response is
    sent to the customer), so scheduled ``evaluate_due`` runs return nothing.
    """
    return []


register(
    TriggerSpec(
        type="complaint_technical_response_updated",
        label="Complaint technical response sent",
        description="Fires when a complaint's technical-team response is sent to the customer via Update & Reply (event-driven, dispatched from the reply flow).",
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_complaint_technical_response_updated,
)


def _trigger_purchase_request_approved(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via :meth:`AutomationService.dispatch_event` from the
    purchase request approval code path.
    """
    return []


register(
    TriggerSpec(
        type="purchase_request_approved",
        label="Purchase request approved",
        description="Fires when a purchase request is approved (event-driven, dispatched from the approval flow).",
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_purchase_request_approved,
)


def build_order_inquiry_link(inquiry_id: Optional[str]) -> str:
    """The OI detail page, for the header this email is about (S3, AC-LK-01,
    `PLAN-oi-header-list-detail.md`).

    Before this lane there was no per-row detail page
    (`documentation/plans/scm/PLAN-scm-oi-handshake.md`), so every email pointed at the
    worklist's own search narrowed by the SO number instead - `orderInquiryRowHref`'s
    own fallback everywhere else on this screen. Every one of these emails is now about
    ONE header (a raise, a reconfirm, a handover, an undo), so the header id it already
    holds is a better link than a search that can miss when a second amendment raises a
    second header on the same SO. A caller with no id (nothing to point at - a fully
    covered decision that raised no header) gets the unfiltered list.

    PUBLIC (nit, review of PR #471), unlike its `_build_*_link` siblings above: every one
    of those is also called from ITS OWN trigger function inside this module, so the
    underscore reads as "this module's own helper, imported elsewhere too". This one is
    never called from in here - only from `project_order_inquiry_service.py` - so a
    leading underscore promising module-private use was the wrong signal for what it is.
    """
    base = (settings.frontend_base_url or "").rstrip("/")
    path = "/project-sales/order-inquiries"
    if inquiry_id:
        path = f"{path}/{inquiry_id}"
    return f"{base}{path}" if base else path


def _trigger_order_inquiry_changed_with_links(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via :meth:`ProjectOrderInquiryService._dispatch_changed_with_links`
    (`PLAN-scm-reorder-oi-feedback-1sep.md` S1, G6), queued mid-transaction and drained
    post-commit by ``_fire_pending_changed_with_links`` (S5, review of PR #471). Three
    callers reach it, all sharing one rule - a row that already carries a link is amended
    under purchasing: ``_settle_row_in_place`` on a quantity/date settle-in-place AND on
    the need-drops-to-zero cancel that gives the link back (S4), and
    ``_retire_uncovered_rows`` when a dropped line's cascade-linked row is retired on
    supersede (S4). A linkless amendment fires nothing in any of the three: purchasing has
    arranged nothing yet, so there is nothing for this trigger to warn them a change moved.
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_changed_with_links",
        label="Order inquiry changed with links",
        description=(
            "Fires when CS amends an order inquiry row that already has a purchase order "
            "or SPO linked to it (event-driven, dispatched when the row settles). A row "
            "with no links yet fires nothing."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_changed_with_links,
)


def _trigger_order_inquiry_handover(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via `ProjectOrderInquiryService._record_handover`
    (`PLAN-scm-oi-handover-email.md` S0-S3), queued mid-transaction and drained
    post-commit by `_fire_pending_handover` - the same shape
    `_trigger_order_inquiry_changed_with_links` above uses. One dispatch per commit,
    covering every row a CS write raised, settled or cancelled in it.
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_handover",
        label="Order inquiry handover to purchasing",
        description=(
            "Fires once per commit that raises, settles or cancels order inquiry rows "
            "(event-driven), shaped like the manual handover mail CS sends purchasing."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_handover,
)


def _trigger_order_inquiry_undone(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via `ProjectOrderInquiryService._record_undo`
    (`PLAN-board-undo-last-confirm.md` "The email"), queued mid-transaction by
    `undo_last_confirm` and drained post-commit by `_fire_pending_undo` - the same
    shape `_trigger_order_inquiry_handover` above uses. One dispatch per undo commit.
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_undone",
        label="Order inquiry undone",
        description=(
            "Fires once a board Confirm's undo has committed (event-driven), telling "
            "purchasing which order inquiry rows reverted or disappeared."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_undone,
)


def _trigger_order_inquiry_reserve_requested(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via `OrderInquiryReserveService.create_request`
    (`PLAN-oi-request-cs-reserve.md` 3.2, R9: ONE email however many rows), queued
    mid-transaction and drained post-commit by `register_order_inquiry_reserve_post_
    commit_dispatch` - the same simpler shape `_trigger_order_inquiry_changed_with_links`
    above uses (see that module's own docstring for why the transaction-chain
    bookkeeping `order_inquiry_handover` needs does not apply here).
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_reserve_requested",
        label="Order inquiry: request CS to reserve",
        description=(
            "Fires when purchasing asks CS to reserve stock for one or more order "
            "inquiry rows (event-driven), one email per request however many rows it "
            "names."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_reserve_requested,
)


def _trigger_order_inquiry_reserved(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via `OrderInquiryReserveService.reserve`
    (`PLAN-oi-request-cs-reserve.md` 3.3), queued mid-transaction and drained the same
    way the request trigger above is.
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_reserved",
        label="Order inquiry: reserved by CS",
        description=(
            "Fires once CS confirms what was reserved against a request (event-driven), "
            "telling the requester what was reserved and the balance still to buy."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_reserved,
)


def _trigger_order_inquiry_rejected(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via `ProjectOrderInquiryService._uncover_and_notify_rejection`
    (`PLAN-oi-reject-notify-13oct.md`), one per order inquiry a reject touched, queued
    mid-transaction and drained post-commit by `_fire_pending_reject`.
    """
    return []


register(
    TriggerSpec(
        type="order_inquiry_rejected",
        label="Order inquiry rejected",
        description=(
            "Fires when purchasing rejects order inquiry rows (event-driven), one email "
            "per order inquiry listing every line rejected in that action."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_order_inquiry_rejected,
)


def _trigger_sponsorship_form_approved(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Matches are produced via :meth:`AutomationService.dispatch_event` from the
    sponsorship form approval code path (shares the purchase_requests table).
    """
    return []


register(
    TriggerSpec(
        type="sponsorship_form_approved",
        label="Sponsorship form approved",
        description="Fires when a sponsorship form is approved (event-driven, dispatched from the approval flow).",
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_sponsorship_form_approved,
)


def _trigger_low_stock_report_ready(
    db: Session,
    config: dict[str, Any],
    timezone: str,
) -> Iterable[TriggerMatch]:
    """Event-driven; pull-mode evaluation yields nothing.

    Dispatched once by the daily reorder run when it has funded its plan
    (`low_stock_report_service.dispatch_ready`), with context
    ``report.{link, as_of, date_label, low, rows}``: the link opens the in-system low stock
    report page for that run (PLAN-excel-preview-26sep S1; owner ruling 26 Sep, Q1 - the
    daily low stock email is an automation, and its recipients are the automation's own).
    """
    return []


register(
    TriggerSpec(
        type="low_stock_report_ready",
        label="Low stock report ready",
        description=(
            "Fires when the daily reorder run finishes (event-driven). Use {{ report.link }} "
            "for the low stock report page, {{ report.date_label }}, {{ report.low }} and "
            "{{ report.rows }}."
        ),
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    _trigger_low_stock_report_ready,
)
