"""AC-DT-10 (`PLAN-oi-decision-trail-ui.md`, round 2): the decision trail behind ONE
core sales-order line - what an OI ROW and a fulfilment-board LINE share
(`OrderInquiryRow.so_line_id` -> the project mirror -> `ProjectSalesOrderLine.
core_sales_order_line_id`, and `BoardContribution.line_id` names the SAME id directly).
ONE read, so the History icon on either surface opens the same trail for the same line.

Four sources, each already written by an existing feature - nothing here writes anything:

- `so_supply_decisions`, every revision (active AND superseded) whose `line_snapshots`
  names this core line - `confirmed`, read the snapshot the way
  `project_fulfilment_board_service.py`'s own `_frozen_decisions` does.
- `so_supply_decision_drafts`, the one row a company may hold for this core line (its own
  unique index, `(company_id, core_line_id)`) - `saved`. Only the LATEST save survives; an
  earlier one a planner overwrote leaves no trace here, same as the board's own "Saved by"
  popover.
- `order_inquiry_raises`, matched to each of this line's own OI rows the same way the
  worklist's "Raised via" column matches one (`app.services.scm.raise_event_matching.
  nearest_raise_event`) - `raised` / `reconfirmed`, or `sheet` when the row's own
  migration note says so, checked FIRST (the same priority `orderInquiryWorklist.ts`'s
  `raisedKindLabel` uses, reviewer B1 round 1) - PLUS (round 3, B3) any LATER raise event
  of the same inquiry, past the row's own 10-minute window, that no row's own origin
  match already reported.
- A row's own planning-change note (`planning_change_service.py`'s exact three stamps) -
  `planning_change`, independent of whether that same row also matched a raise event
  above: the trail is a full history, not a single label picking one fact to show.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session, aliased

from app.models.order import SalesOrderLine
from app.models.project_so import (
    OrderInquiry,
    OrderInquiryRaise,
    OrderInquiryRow,
    ProjectSalesOrderLine,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)
from app.models.user import User
from app.services.error_handler import AppException
from app.services.project_service import resolve_user_names
from app.services.scm.raise_event_matching import RAISE_EVENT_AFTER, nearest_raise_event

_ZERO = Decimal("0")

#: `project_order_inquiry_import_service.py`'s own `_MIGRATION_STAMP` literal, kept as a
#: plain copy rather than an import: that module pulls in the whole import pipeline, and a
#: read-only trail has no business dragging it in. Keep the two in step by hand if either
#: ever changes.
_SHEET_MIGRATION_NOTE_PREFIX = "Migrated from order inquiry sheet"

#: The exact three notes `planning_change_service.py` writes on a row it moves or trims -
#: the same pattern `orderInquiryWorklist.ts`'s `PLANNING_CHANGE_NOTE` tests (reviewer S2,
#: round 1). Kept in step with it by hand: there is no shared source between the two
#: languages.
_PLANNING_CHANGE_NOTE = re.compile(
    r"^Was \d{4}-\d{2}-\d{2}$|^Was [\d.,]+, now |^No previous delivery date$"
)

#: The board's own four composition kinds, in the order a composition reads them.
_KIND_LABEL = {
    "timely_spo": "Incoming",
    "reserve": "Reserve",
    "borrow": "Borrow",
    "buy": "Buy",
}
_KIND_ORDER = ("reserve", "timely_spo", "borrow", "buy")

#: `(inquiry_id, raised_at, kind)` - identifies one `order_inquiry_raises` row, for the
#: two dedupe sets `_raise_entries` keeps (an event already reported as some row's own
#: origin match, and an event already reported once by the "later events" pass).
_EventKey = Tuple[str, datetime, str]


def _qty_str(value: Any) -> str:
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed stored number is data, not a crash
        dec = _ZERO
    return format(dec.normalize(), "f")


def _components_text(components: List[Dict[str, Any]]) -> str:
    """A short composition sentence off a list of `{"kind", "qty"}` components - the
    snapshot's own shape (`so_supply_decisions.line_snapshots[].components`)."""
    totals: Dict[str, Decimal] = {}
    for component in components or []:
        kind = (component or {}).get("kind")
        if kind not in _KIND_LABEL:
            continue
        try:
            qty = Decimal(str((component or {}).get("qty") or 0))
        except Exception:  # noqa: BLE001
            qty = _ZERO
        totals[kind] = totals.get(kind, _ZERO) + qty
    parts = [
        f"{_KIND_LABEL[kind]} {_qty_str(totals[kind])}"
        for kind in _KIND_ORDER
        if totals.get(kind, _ZERO) != _ZERO
    ]
    return " · ".join(parts) if parts else "Nothing recorded"


def _flat_decision_text(decision: Dict[str, Any]) -> str:
    """The same sentence, off a SAVED draft's `decision` JSONB - the frontend's own
    `BoardDecision` shape (`reserve_qty` / `timely_spo_qty` / `borrow[]` / `buy_qty`,
    flat rather than a `components` list)."""
    components: List[Dict[str, Any]] = []
    if decision.get("timely_spo_qty"):
        components.append({"kind": "timely_spo", "qty": decision.get("timely_spo_qty")})
    if decision.get("reserve_qty"):
        components.append({"kind": "reserve", "qty": decision.get("reserve_qty")})
    for entry in decision.get("borrow") or []:
        components.append({"kind": "borrow", "qty": (entry or {}).get("qty")})
    if decision.get("buy_qty"):
        components.append({"kind": "buy", "qty": decision.get("buy_qty")})
    return _components_text(components)


class DecisionTrailService:
    def __init__(self, db: Session):
        self.db = db

    def for_core_line(self, core_line_id: str) -> List[Dict[str, Any]]:
        exists = (
            self.db.query(SalesOrderLine.id)
            .filter(SalesOrderLine.id == core_line_id)
            .first()
        )
        if exists is None:
            raise AppException(
                404,
                "That sales order line no longer exists.",
                code="sales_order_line_not_found",
            )

        entries: List[Dict[str, Any]] = []
        entries.extend(self._confirmed_entries(core_line_id))
        entries.extend(self._saved_entry(core_line_id))
        entries.extend(self._raise_entries(core_line_id))
        entries.extend(self._rejected_entries(core_line_id))

        # Newest first (AC-DT-10). A `sheet` entry now carries the row's own `created_at`
        # (round 3) and sorts among the rest like any other entry; `or datetime.min`
        # stays as a fallback for any future entry kind that genuinely has no timestamp,
        # sorting it to the end rather than claiming to be the oldest OR the newest.
        entries.sort(key=lambda entry: entry["at"] or datetime.min, reverse=True)
        return entries

    # ------------------------------------------------------------------ confirmed

    def _confirmed_entries(self, core_line_id: str) -> List[Dict[str, Any]]:
        pso_ids = [
            row[0]
            for row in self.db.query(ProjectSalesOrderLine.project_sales_order_id)
            .filter(ProjectSalesOrderLine.core_sales_order_line_id == core_line_id)
            .all()
        ]
        if not pso_ids:
            return []
        decisions = (
            self.db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id.in_(pso_ids))
            .order_by(SOSupplyDecision.revision_no.asc())
            .all()
        )
        names = resolve_user_names(
            self.db, {d.confirmed_by for d in decisions if d.confirmed_by}
        )
        out: List[Dict[str, Any]] = []
        for decision in decisions:
            for snapshot in decision.line_snapshots or []:
                if str((snapshot or {}).get("core_line_id") or "") != str(core_line_id):
                    continue
                components = list((snapshot or {}).get("components") or [])
                out.append(
                    {
                        "kind": "confirmed",
                        "actor_name": (
                            names.get(decision.confirmed_by)
                            if decision.confirmed_by
                            else None
                        ),
                        "at": decision.confirmed_at,
                        "detail": (
                            f"Revision {decision.revision_no} · "
                            f"{_components_text(components)}"
                        ),
                    }
                )
        return out

    # ---------------------------------------------------------------------- saved

    def _saved_entry(self, core_line_id: str) -> List[Dict[str, Any]]:
        # `uq_so_supply_decision_drafts_line` holds ONE row per `(company_id,
        # core_line_id)` - a planner's earlier save leaves no trace once a later one
        # overwrites it, same as the board's own "Saved by" popover.
        draft = (
            self.db.query(SOSupplyDecisionDraft)
            .filter(SOSupplyDecisionDraft.core_line_id == core_line_id)
            .first()
        )
        if draft is None:
            return []
        names = resolve_user_names(
            self.db, {draft.saved_by} if draft.saved_by else set()
        )
        decision = draft.decision or {}
        verdict = str(decision.get("verdict") or "saved").replace("_", " ").title()
        if decision.get("verdict") == "rejected":
            reason = decision.get("reason") or ""
            detail = f"{verdict} · {reason}" if reason else verdict
        else:
            detail = f"{verdict} · {_flat_decision_text(decision)}"
        return [
            {
                "kind": "saved",
                "actor_name": names.get(draft.saved_by) if draft.saved_by else None,
                "at": draft.saved_at,
                "detail": detail,
            }
        ]

    # ---------------------------------------------------------------------- raised

    def _rejected_entries(self, core_line_id: str) -> List[Dict[str, Any]]:
        """Every refusal purchasing made on this line (PR 1622, owner: "make sure the
        rejection is stored in the history ... so I can trace back"). Read off the refused
        row's own stamps, which outlive the row being superseded or the badge clearing."""
        rejecter = aliased(User)
        rows = (
            self.db.query(
                OrderInquiry.inquiry_no,
                OrderInquiryRow.rejected_at,
                OrderInquiryRow.rejected_reason,
                rejecter.name,
            )
            .join(ProjectSalesOrderLine, ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .outerjoin(rejecter, rejecter.id == OrderInquiryRow.rejected_by)
            .filter(
                ProjectSalesOrderLine.core_sales_order_line_id == core_line_id,
                OrderInquiryRow.rejected_at.isnot(None),
            )
            .all()
        )
        return [
            {
                "kind": "rejected",
                "actor_name": name,
                "at": rejected_at,
                "detail": f"{inquiry_no or 'Order inquiry'}: {reason or 'no reason given'}",
            }
            for inquiry_no, rejected_at, reason, name in rows
        ]

    def _raise_entries(self, core_line_id: str) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(
                OrderInquiryRow.id,
                OrderInquiryRow.order_inquiry_id,
                OrderInquiryRow.note,
                OrderInquiryRow.qty,
                OrderInquiryRow.created_at,
                OrderInquiry.inquiry_no,
            )
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .filter(ProjectSalesOrderLine.core_sales_order_line_id == core_line_id)
            # A stable order: with none, the "later events" pass below (B3) could
            # attribute one raise event's `· qty N` detail to a different row - and
            # therefore a different quantity - on every request.
            .order_by(OrderInquiryRow.created_at, OrderInquiryRow.id)
            .all()
        )
        if not rows:
            return []
        inquiry_ids = {row.order_inquiry_id for row in rows}
        events = (
            self.db.query(
                OrderInquiryRaise.order_inquiry_id,
                OrderInquiryRaise.kind,
                OrderInquiryRaise.raised_at,
                User.name,
            )
            .outerjoin(User, User.id == OrderInquiryRaise.raised_by)
            .filter(OrderInquiryRaise.order_inquiry_id.in_(inquiry_ids))
            .order_by(OrderInquiryRaise.raised_at.asc())
            .all()
        )
        events_by_inquiry: Dict[str, List[Tuple[datetime, str, Optional[str]]]] = {}
        for inquiry_id, kind, raised_at, name in events:
            events_by_inquiry.setdefault(str(inquiry_id), []).append(
                (raised_at, kind, name)
            )

        out: List[Dict[str, Any]] = []
        # B3 (review round 3): every raise event some row's own ORIGIN match consumed,
        # keyed by `(inquiry_id, raised_at, kind)` - the "later events" pass below must
        # never re-report one of these under a DIFFERENT row's own (later) threshold.
        origin_event_keys: Set[_EventKey] = set()
        for row in rows:
            note = row.note or ""
            detail = f"{row.inquiry_no or 'Unnumbered inquiry'} · qty {_qty_str(row.qty)}"
            # The sheet stamp FIRST, before any event (reviewer B1, round 1): the note is
            # what the row itself says about where it came from, the event is a guess.
            # `at` is the row's own `created_at` (round 3, reviewer): the row is a real
            # fact with a real time, only the uploader is unknown - `actor_name` stays
            # `None`, the sheet import records no uploader.
            if note.startswith(_SHEET_MIGRATION_NOTE_PREFIX):
                out.append(
                    {
                        "kind": "sheet",
                        "actor_name": None,
                        "at": row.created_at,
                        "detail": detail,
                    }
                )
            else:
                candidates = events_by_inquiry.get(str(row.order_inquiry_id or ""), [])
                match = nearest_raise_event(row.created_at, candidates)
                if match is not None:
                    matched_at, matched_kind, matched_name = match
                    out.append(
                        {
                            "kind": matched_kind,
                            "actor_name": matched_name,
                            "at": matched_at,
                            "detail": detail,
                        }
                    )
                    origin_event_keys.add(
                        (str(row.order_inquiry_id or ""), matched_at, matched_kind)
                    )
            # Independent of the above (S2, review round 1's exact-note match): the trail
            # is a full history, not a single label picking one fact to show.
            if _PLANNING_CHANGE_NOTE.match(note):
                out.append(
                    {
                        "kind": "planning_change",
                        "actor_name": None,
                        "at": row.created_at,
                        "detail": note,
                    }
                )

        # B3 (review round 3, SO390524 / OI-2609-0731): a `sheet` row's own origin match
        # never touches an event at all, so a LATER raise on the very same inquiry - the
        # 20 Sep 11:22 UTC reconfirm on a fully sheet-migrated inquiry, for one - never
        # surfaced anywhere. Any event more than `RAISE_EVENT_AFTER` (10 min) past a
        # row's own birth is a fact about the same core line's inquiry that the row's own
        # origin match cannot state, so it earns its own entry - once per `(inquiry,
        # event)` even when several of this line's own rows share the inquiry, and never
        # for an event some row's own origin match already reported.
        emitted_later_keys: Set[_EventKey] = set()
        for row in rows:
            if row.created_at is None:
                continue
            inquiry_id = str(row.order_inquiry_id or "")
            threshold = row.created_at + RAISE_EVENT_AFTER
            detail = f"{row.inquiry_no or 'Unnumbered inquiry'} · qty {_qty_str(row.qty)}"
            for raised_at, kind, name in events_by_inquiry.get(inquiry_id, []):
                if raised_at <= threshold:
                    continue
                key = (inquiry_id, raised_at, kind)
                if key in origin_event_keys or key in emitted_later_keys:
                    continue
                emitted_later_keys.add(key)
                out.append(
                    {"kind": kind, "actor_name": name, "at": raised_at, "detail": detail}
                )
        return out
