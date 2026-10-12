"""PLANNING-WHY R3: the admin-only History of one or more sales orders.

Read-only. One merged, newest-first list of what happened to an order's plan: revisions
confirmed, AutoCount book updates, refused Confirms, orders cancelled by a person, and the
emails purchasing got. Everything is read from rows that already exist; nothing is written.
UAC: `documentation/plans/scm/planning-why-12oct-acceptance-criteria.md` AC-W10, AC-W11.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification
from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.project_so import (
    INQUIRY_CANCELLED,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
)
from app.services.planning_change_service import change_text
from app.services.project_service import resolve_user_names

#: The window around a revision in which a row it raised was created (AC-W10).
_WINDOW = timedelta(seconds=5)
#: Both spellings: the audit type the writer uses, and the table name older tooling used.
_ROW_AUDIT_TYPES = ("project_order_inquiry_rows", "order_inquiry_rows")
_SUPERSEDED_NOTE = re.compile(r"Superseded by revision (\d+)")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _qty(value: Any) -> Any:
    if value is None:
        return None
    number = float(value)
    return int(number) if number == int(number) else number


def _line_dict(
    so_line_no, item_code, action, qty, *, previous_qty=None, cause=None, change=None
) -> dict:
    return {
        "so_line_no": so_line_no,
        "item_code": item_code,
        "action": action,
        "qty": _qty(qty),
        "previous_qty": _qty(previous_qty),
        "cause": cause,
        "change": change,
    }


def history(db: Session, pso_ids: Sequence[str]) -> List[dict]:
    orders = {
        str(o.id): o
        for o in db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id.in_(list(pso_ids))).all()
    }
    if not orders:
        return []
    ids = list(orders)
    so_no = {
        pid: (o.autocount_doc_no or o.provisional_ref or "") for pid, o in orders.items()
    }
    events: List[dict] = []
    events += _refusals_and_cancels(db, ids)
    events += _revisions(db, ids)
    events += _updates(db, ids)
    events += _emails(db, ids)
    for event in events:
        event["so_no"] = so_no.get(event["pso_id"], "")
    names = resolve_user_names(db, {e["_actor_id"] for e in events if e.get("_actor_id")})
    for event in events:
        event["actor"] = names.get(event.pop("_actor_id", None))
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return events


# --------------------------------------------------------------------------- audit rows


def _refusals_and_cancels(db: Session, ids: List[str]) -> List[dict]:
    """`confirm_refused` and `oi_cancelled` events. A CANCEL row naming a revision belongs
    to that revision's event instead (`_revisions`)."""
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.root_entity_type == "project_sales_orders",
            AuditLog.root_entity_id.in_(ids),
            or_(
                AuditLog.event == "CONFIRM_REFUSED",
                (AuditLog.event == "OI_ROW_CANCELLED") & AuditLog.entity_type.in_(_ROW_AUDIT_TYPES),
            ),
        )
        .all()
    )
    out: List[dict] = []
    for row in rows:
        values = row.new_values or {}
        if row.event == "CONFIRM_REFUSED":
            out.append(
                {
                    "id": str(row.id),
                    "kind": "confirm_refused",
                    "at": _iso(row.changed_at),
                    "_actor_id": row.user_id,
                    "pso_id": str(row.root_entity_id),
                    "lines": values.get("lines") or [],
                    "preview": bool(values.get("preview")),
                    "reason": row.reason,
                }
            )
        elif values.get("revision_no") is None:
            out.append(
                {
                    "id": str(row.id),
                    "kind": "oi_cancelled",
                    "at": _iso(row.changed_at),
                    "_actor_id": row.user_id,
                    "pso_id": str(row.root_entity_id),
                    "lines": [_cancel_line(values)],
                }
            )
    return out


def _cancel_line(values: dict) -> dict:
    return _line_dict(
        values.get("so_line_no"),
        values.get("item_code"),
        "cancelled",
        values.get("qty"),
        cause=values.get("cause"),
    )


# --------------------------------------------------------------------------- revisions


def _revisions(db: Session, ids: List[str]) -> List[dict]:
    decisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id.in_(ids))
        .order_by(SOSupplyDecision.revision_no.asc())
        .all()
    )
    if not decisions:
        return []
    rows = (
        db.query(OrderInquiryRow, OrderInquiry.project_sales_order_id)
        .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
        .filter(OrderInquiry.project_sales_order_id.in_(ids))
        .all()
    )
    rows_by_order: Dict[str, List[OrderInquiryRow]] = defaultdict(list)
    for row, pso_id in rows:
        rows_by_order[str(pso_id)].append(row)
    line_no = {
        str(line_id): no
        for line_id, no in db.query(ProjectSalesOrderLine.id, ProjectSalesOrderLine.line_no)
        .filter(ProjectSalesOrderLine.project_sales_order_id.in_(ids))
        .all()
    }
    # The CANCEL audit rows that name a revision: who/why, exact.
    recorded: Dict[tuple, dict] = {}
    audit_rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.root_entity_type == "project_sales_orders",
            AuditLog.root_entity_id.in_(ids),
            AuditLog.event == "OI_ROW_CANCELLED",
            AuditLog.entity_type.in_(_ROW_AUDIT_TYPES),
        )
        .all()
    )
    for audit in audit_rows:
        values = audit.new_values or {}
        if values.get("revision_no") is not None:
            recorded[(str(audit.root_entity_id), int(values["revision_no"]), str(audit.entity_id))] = values
    changes: Dict[str, list] = defaultdict(list)
    for change, applied_at in (
        db.query(PlanningChangeRow, PlanningChangeBatch.applied_at)
        .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
        .filter(PlanningChangeRow.project_sales_order_id.in_(ids))
        .all()
    ):
        if change.project_line_id:
            changes[str(change.project_line_id)].append((change, applied_at))
    out: List[dict] = []
    for decision in decisions:
        pso_id = str(decision.project_sales_order_id)
        revision = int(decision.revision_no)
        confirmed_at = decision.confirmed_at or decision.created_at
        start = (decision.created_at or confirmed_at) - _WINDOW
        end = confirmed_at + _WINDOW
        order_rows = rows_by_order.get(pso_id, [])

        ordered = [
            r
            for r in order_rows
            if r.created_at and start <= r.created_at <= end and r.verb in (IV_ORDER, IV_ORDER_BACK)
        ]
        cancelled: List[tuple] = []  # (row, cause, change)
        for row in order_rows:
            values = recorded.get((pso_id, revision, str(row.id)))
            if values is not None:
                cancelled.append((row, values.get("cause"), None))
                continue
            if row.state != INQUIRY_CANCELLED:
                continue
            if revision not in {int(n) for n in _SUPERSEDED_NOTE.findall(row.note or "")}:
                continue
            if any(key[0] == pso_id and key[2] == str(row.id) for key in recorded):
                continue  # a recorded cancel of another revision
            cause, change = _derived_cause(row, confirmed_at, changes)
            cancelled.append((row, cause, change))

        # A cancel matched by an order of the same line, item and qty is a re-issue.
        reissued = 0
        for row, _cause, _change in list(cancelled):
            match = next(
                (
                    o
                    for o in ordered
                    if o.so_line_id == row.so_line_id
                    and o.item_code == row.item_code
                    and _qty(o.qty) == _qty(row.qty)
                ),
                None,
            )
            if match is not None:
                ordered.remove(match)
                cancelled = [c for c in cancelled if c[0] is not row]
                reissued += 1

        lines: List[dict] = []
        for row in ordered:
            reduced = row.previous_qty is not None and _qty(row.previous_qty) > _qty(row.qty)
            lines.append(
                _line_dict(
                    line_no.get(str(row.so_line_id)),
                    row.item_code,
                    "reduced" if reduced else ("order_back" if row.verb == IV_ORDER_BACK else "ordered"),
                    row.qty,
                    previous_qty=row.previous_qty if reduced else None,
                )
            )
        for row, cause, change in cancelled:
            lines.append(
                _line_dict(
                    line_no.get(str(row.so_line_id)), row.item_code, "cancelled", row.qty,
                    cause=cause, change=change,
                )
            )
        lines.sort(key=lambda l: (l["so_line_no"] is None, l["so_line_no"] or 0))
        out.append(
            {
                "id": str(decision.id),
                "kind": "revision_confirmed",
                "at": _iso(confirmed_at),
                "_actor_id": decision.confirmed_by,
                "pso_id": pso_id,
                "revision_no": revision,
                "lines": lines,
                "reissued_unchanged": reissued,
            }
        )
    return out


def _derived_cause(row: OrderInquiryRow, confirmed_at: datetime, changes: Dict[str, list]) -> tuple:
    """AC-W11: a cancel before the release has no recorded cause. `undecided_change` only
    when an AutoCount change for the line was waiting, undecided, when the revision
    confirmed; otherwise the honest `not_recorded`."""
    for change, applied_at in changes.get(str(row.so_line_id), []):
        if (
            change.kind != "cancelled"
            and change.decision is None
            and change.created_at
            and change.created_at < confirmed_at
            and (
                change.applied_state == "pending"
                or (change.applied_state == "applied" and applied_at and applied_at > confirmed_at)
            )
        ):
            return "undecided_change", change_text(change)
    return "not_recorded", None


# --------------------------------------------------------------------------- book updates


def _state_now(row: PlanningChangeRow) -> str:
    if row.applied_state == "applied":
        return "confirmed"
    if row.applied_state == "superseded":
        return "replaced"
    if row.applied_state == "pending":
        return "not_decided" if row.decision is None else "decided"
    return row.applied_state


def _updates(db: Session, ids: List[str]) -> List[dict]:
    rows = (
        db.query(PlanningChangeRow, PlanningChangeBatch)
        .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
        .filter(PlanningChangeRow.project_sales_order_id.in_(ids))
        .order_by(PlanningChangeRow.line_no.asc(), PlanningChangeRow.created_at.asc())
        .all()
    )
    grouped: Dict[tuple, dict] = {}
    for row, batch in rows:
        key = (str(batch.id), str(row.project_sales_order_id))
        event = grouped.setdefault(
            key,
            {
                "id": f"{key[0]}:{key[1]}",
                "kind": "autocount_update",
                "at": _iso(batch.created_at),
                "_actor_id": batch.created_by,
                "pso_id": key[1],
                "counts": defaultdict(int),
                "lines": [],
            },
        )
        state = _state_now(row)
        event["counts"][row.kind] += 1
        event["counts"][state] += 1
        event["lines"].append(
            {
                "so_line_no": row.line_no,
                "item_code": row.item_code,
                "kind": row.kind,
                "change": change_text(row),
                "state": state,
            }
        )
    for event in grouped.values():
        event["counts"] = dict(event["counts"])
    return list(grouped.values())


# --------------------------------------------------------------------------- emails


def _recipients(mail: EmailOutbox) -> List[str]:
    found: List[str] = []
    raw = mail.recipients_json
    if isinstance(raw, dict):
        for key in ("to", "cc", "bcc"):
            found += [str(a) for a in (raw.get(key) or [])]
    elif isinstance(raw, list):
        found += [str(a) for a in raw]
    if not found and mail.recipient_email:
        found = [mail.recipient_email]
    return list(dict.fromkeys(found))


def _parse_lines(body: Optional[str]) -> tuple:
    """The item code and remark of each line of a handover email's table. The header row
    (`... ITEM CODE ... REMARK`) says which columns those are, whatever layout the email had
    (with or without LOCATION, with or without SO DATE / S/O NO); it and anything before it
    are not lines. A table with no header row reads item code as the second cell and remark
    as the last."""
    rows = [
        [c.strip() for c in text.split("|")] for text in (body or "").splitlines() if "|" in text
    ]
    item_at: Optional[int] = 1
    remark_at: Optional[int] = None  # None: the last cell
    for position, cells in enumerate(rows):
        upper_cells = [c.upper() for c in cells]
        if "ITEM CODE" in upper_cells and "REMARK" in upper_cells:
            item_at = upper_cells.index("ITEM CODE")
            remark_at = upper_cells.index("REMARK")
            rows = rows[position + 1 :]
            break
    lines: List[dict] = []
    counts = {"cancel": 0, "order": 0, "order_back": 0}
    for cells in rows:
        last = len(cells) - 1 if remark_at is None else remark_at
        if len(cells) < 3 or last >= len(cells) or item_at >= len(cells):
            continue
        remark = cells[last]
        lines.append({"item_code": cells[item_at], "remark": remark})
        upper = remark.upper()
        if upper.startswith("CANCEL"):
            counts["cancel"] += 1
        elif "ORDER BACK" in upper:
            counts["order_back"] += 1
        elif upper.startswith("ORDER"):
            counts["order"] += 1
    return lines, counts


def _emails(db: Session, ids: List[str]) -> List[dict]:
    inquiries = {
        str(i): str(p)
        for i, p in db.query(OrderInquiry.id, OrderInquiry.project_sales_order_id)
        .filter(OrderInquiry.project_sales_order_id.in_(ids))
        .all()
    }
    if not inquiries:
        return []
    notifications = {
        str(n.id): inquiries[str(n.data["source_id"])]
        for n in db.query(Notification)
        .filter(
            Notification.data["source_kind"].astext == "order_inquiry_handover",
            Notification.data["source_id"].astext.in_(list(inquiries)),
        )
        .all()
    }
    if not notifications:
        return []
    out: List[dict] = []
    for mail in (
        db.query(EmailOutbox)
        .filter(EmailOutbox.metadata_json["notification_id"].astext.in_(list(notifications)))
        .all()
    ):
        lines, counts = _parse_lines(mail.body_text)
        out.append(
            {
                "id": str(mail.id),
                "kind": "email_sent",
                "at": _iso(mail.sent_at or mail.created_at),
                "pso_id": notifications[str(mail.metadata_json["notification_id"])],
                "subject": mail.subject,
                "status": mail.status,
                "sent_at": _iso(mail.sent_at),
                "recipients": _recipients(mail),
                "body_text": mail.body_text,
                "lines": lines,
                "counts": counts,
            }
        )
    return out
