"""OI-LINE-SPLIT: one SO line split into deliveries (qty, date or cancel, the line it sits on).

Plan: `documentation/plans/scm/PLAN-oi-line-split-16oct.md`; UAC
`oi-line-split-16oct-acceptance-criteria.md` (A to D). Storage is `projects.so_line_splits`
(`SOLineSplit`); a line with no rows is not split.

Base rules (UAC B2), what a line's splits must add up to exactly (B1):

1. a line already split keeps the base its splits were made against (stored on each row);
2. else, when a pending planning-change row exists for the line, the qty and date BEFORE
   that change (the row's `from`);
3. else the line's own open qty and date.

A partly delivered line splits only its open qty. Sits-on is only ever what staff picked
(C1); a split whose sits-on line is cancelled reads as a new line (C2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.order import SalesOrderLine
from app.models.product import Product
from app.models.project_so import (
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOLineSplit,
)
from app.services.error_handler import AppException

_ZERO = Decimal("0")

#: The change words a split reads with, against its own line's base date (UAC A9). The
#: same spellings the OI email and Excel already print.
WORD_REMAIN = "REMAIN"
WORD_DELAY = "DELAY"
WORD_ADVANCE = "ADVANCE"
WORD_CANCEL = "CANCEL BALANCE"


def _dec(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed stored number reads as nothing
        return _ZERO


def _as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def qty_text(value: Any) -> str:
    """`600`, not `600.0000`."""
    return format(_dec(value).normalize(), "f")


def change_word(*, is_cancel: bool, delivery_date: Optional[date], base_date: Optional[date]) -> str:
    """REMAIN / DELAY / ADVANCE / CANCEL BALANCE for one split against its line's base date."""
    if is_cancel:
        return WORD_CANCEL
    if delivery_date is None or base_date is None or delivery_date == base_date:
        return WORD_REMAIN
    return WORD_DELAY if delivery_date > base_date else WORD_ADVANCE


def _cancelled(core: Optional[SalesOrderLine]) -> bool:
    return core is not None and (core.line_status or "open") == "cancelled"


@dataclass
class LineFacts:
    """One mirror line as the split view reads it."""

    line: ProjectSalesOrderLine
    core: Optional[SalesOrderLine]
    item_code: str
    location: str
    #: The line's OPEN qty now (AutoCount qty less delivered), the same footing as the base.
    now_qty: Decimal
    now_date: Optional[date]
    delivered_qty: Decimal
    pending_from: Optional[Dict[str, Any]] = None
    splits: List[SOLineSplit] = field(default_factory=list)

    @property
    def line_id(self) -> str:
        return str(self.line.id)

    @property
    def cancelled(self) -> bool:
        return _cancelled(self.core)

    @property
    def pending(self) -> bool:
        return self.pending_from is not None

    def base(self) -> Tuple[Decimal, Optional[date]]:
        """`(base_qty, base_date)` by the three rules in the module docstring."""
        if self.splits:
            first = self.splits[0]
            return _dec(first.base_qty), first.base_date
        return self.live_base()

    def live_base(self) -> Tuple[Decimal, Optional[date]]:
        """Rules 2 and 3 alone: what a split made NOW would be made against."""
        if self.pending_from is not None:
            from_qty = self.pending_from.get("qty")
            if from_qty is None:
                qty = self.now_qty
            else:
                qty = max(_dec(from_qty) - self.delivered_qty, _ZERO)
            when = _as_date(self.pending_from.get("required_date")) or self.now_date
            return qty, when
        return self.now_qty, self.now_date

    @property
    def splits_act(self) -> bool:
        """Do this line's stored splits still describe the change in front of the planner?

        A split was made against a base (`base_qty`, `base_date`). It acts on Confirm (moves
        links, tells purchasing its lines, nets a cancel) only while the line still reads from
        that base: its pending change starts from it, or, with no change pending, its open qty
        and date are it. Once AutoCount moved on (a change applied, a newer one pending), the
        splits are the saved plan to read, never applied again.
        """
        if not self.splits:
            return False
        first = self.splits[0]
        qty, when = self.live_base()
        return _dec(first.base_qty) == qty and first.base_date == when


class LineSplitService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ reads

    def splits_of_lines(self, line_ids: Iterable[str]) -> Dict[str, List[SOLineSplit]]:
        """Every stored split of these ORIGIN lines, in their own order, one query."""
        wanted = sorted({str(i) for i in line_ids if i})
        if not wanted:
            return {}
        out: Dict[str, List[SOLineSplit]] = {}
        for split in (
            self.db.query(SOLineSplit)
            .filter(SOLineSplit.so_line_id.in_(wanted))
            .order_by(SOLineSplit.so_line_id, SOLineSplit.seq.asc(), SOLineSplit.created_at.asc())
            .all()
        ):
            out.setdefault(str(split.so_line_id), []).append(split)
        return out

    def cancelled_line_ids(self, line_ids: Iterable[str]) -> set:
        """Which of these mirror lines sit on a cancelled core line."""
        wanted = sorted({str(i) for i in line_ids if i})
        if not wanted:
            return set()
        return {
            str(line_id)
            for (line_id,) in self.db.query(ProjectSalesOrderLine.id)
            .join(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .filter(
                ProjectSalesOrderLine.id.in_(wanted),
                SalesOrderLine.line_status == "cancelled",
            )
            .all()
        }

    def effective_sits_on(self, splits: Sequence[SOLineSplit]) -> Dict[str, Optional[str]]:
        """`split id -> the line it sits on`, NULL once that line is cancelled (UAC C2)."""
        targets = {str(s.sits_on_line_id) for s in splits if s.sits_on_line_id}
        gone = self.cancelled_line_ids(targets)
        return {
            str(s.id): (
                str(s.sits_on_line_id)
                if s.sits_on_line_id and str(s.sits_on_line_id) not in gone
                else None
            )
            for s in splits
        }

    def _pending_from_by_line(self, line_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """The newest PENDING planning-change row's `from` per mirror line (the same
        "pending" `planning_change_service.pending_batch_id_by_sales_order` reads: the
        batch unapplied and the row still pending). A newer edit of the same line carries
        the older row's `from` (`build_batch`), so the newest row is the base."""
        if not line_ids:
            return {}
        from app.models.planning_change import (
            PLANNING_CHANGE_STATE_PENDING,
            PlanningChangeBatch,
            PlanningChangeRow,
        )

        found = (
            self.db.query(PlanningChangeRow.project_line_id, PlanningChangeRow.from_json)
            .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
            .filter(
                PlanningChangeRow.project_line_id.in_(list(line_ids)),
                PlanningChangeBatch.applied_at.is_(None),
                PlanningChangeRow.applied_state == PLANNING_CHANGE_STATE_PENDING,
            )
            .order_by(PlanningChangeRow.created_at.desc(), PlanningChangeRow.id.desc())
            .all()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for line_id, from_json in found:
            out.setdefault(str(line_id), dict(from_json or {}))
        return out

    def line_facts(self, order: ProjectSalesOrder) -> Dict[str, LineFacts]:
        """Every mirror line of the order, keyed by id, with its core line, codes and splits."""
        return self._facts_for(
            ProjectSalesOrderLine.project_sales_order_id == order.id
        )

    def acting_origin_ids(self, line_ids: Iterable[str]) -> set:
        """Which of these ORIGIN lines have stored splits that still act (`splits_act`): the
        one gate the Confirm engine, its email lines and the cancel netting all read."""
        wanted = sorted({str(i) for i in line_ids if i})
        if not wanted:
            return set()
        facts = self._facts_for(ProjectSalesOrderLine.id.in_(wanted))
        return {line_id for line_id, f in facts.items() if f.splits_act}

    def _facts_for(self, criterion: Any) -> Dict[str, LineFacts]:
        rows = (
            self.db.query(ProjectSalesOrderLine, SalesOrderLine)
            .outerjoin(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .filter(criterion)
            .all()
        )
        product_ids = {
            (line.product_id or (core.product_id if core is not None else None))
            for line, core in rows
        } - {None}
        codes: Dict[str, str] = (
            {
                str(product_id): str(code or "")
                for product_id, code in self.db.query(Product.id, Product.product_code)
                .filter(Product.id.in_(list(product_ids)))
                .all()
            }
            if product_ids
            else {}
        )
        warehouse_ids = {
            core.warehouse_id for _line, core in rows if core is not None and core.warehouse_id
        }
        warehouses: Dict[str, str] = (
            {
                str(warehouse_id): str(code or "")
                for warehouse_id, code in self.db.query(Warehouse.id, Warehouse.warehouse_code)
                .filter(Warehouse.id.in_(list(warehouse_ids)))
                .all()
            }
            if warehouse_ids
            else {}
        )
        line_ids = [str(line.id) for line, _core in rows]
        pending = self._pending_from_by_line(line_ids)
        splits = self.splits_of_lines(line_ids)
        out: Dict[str, LineFacts] = {}
        for line, core in rows:
            product_id = line.product_id or (core.product_id if core is not None else None)
            location = line.stock_location or (
                warehouses.get(str(core.warehouse_id)) if core is not None else None
            )
            delivered = _dec(getattr(core, "qty_delivered", None))
            ordered = _dec(line.qty if line.qty is not None else getattr(core, "qty_ordered", None))
            out[str(line.id)] = LineFacts(
                line=line,
                core=core,
                item_code=codes.get(str(product_id), ""),
                location=str(location or ""),
                now_qty=max(ordered - delivered, _ZERO),
                now_date=line.delivery_date or (core.required_date if core is not None else None),
                delivered_qty=delivered,
                pending_from=pending.get(str(line.id)),
                splits=splits.get(str(line.id), []),
            )
        return out

    def read(self, order: ProjectSalesOrder) -> Dict[str, Any]:
        """The GET payload: the order's open lines, item code then location then line no."""
        from app.services.project_order_inquiry_service import ProjectOrderInquiryService

        facts = ProjectOrderInquiryService(self.db)._handover_order_facts(str(order.id))
        lines = self.line_facts(order)
        sitting_on: set = set()
        all_splits = [s for f in lines.values() for s in f.splits]
        sits = self.effective_sits_on(all_splits)
        for target in sits.values():
            if target:
                sitting_on.add(target)
        listed = [
            f
            for f in lines.values()
            if not f.cancelled
            and (
                f.base()[0] > _ZERO
                or f.pending
                or f.splits
                or f.line_id in sitting_on
            )
        ]
        listed.sort(key=lambda f: (f.item_code, f.location, f.line.line_no or 0))
        out_lines = []
        for f in listed:
            base_qty, base_date = f.base()
            out_lines.append(
                {
                    "line_id": f.line_id,
                    "line_no": f.line.line_no,
                    "item_code": f.item_code,
                    "location": f.location,
                    "base_qty": float(base_qty),
                    "base_date": base_date,
                    "now_qty": float(f.now_qty),
                    "now_date": f.now_date,
                    "pending_change": f.pending,
                    "delivered_qty": float(f.delivered_qty),
                    "splits": [
                        {
                            "id": str(s.id),
                            "qty": float(_dec(s.qty)),
                            "delivery_date": s.delivery_date,
                            "is_cancel": bool(s.is_cancel),
                            "sits_on_line_id": sits.get(str(s.id)),
                            "remark": s.remark or "",
                        }
                        for s in f.splits
                    ],
                }
            )
        return {
            "pso_id": str(order.id),
            "so_number": facts.get("so_number")
            or order.autocount_doc_no
            or order.provisional_ref,
            "customer": facts.get("customer"),
            "project": facts.get("project"),
            "lines": out_lines,
        }

    # ------------------------------------------------------------------ write

    def save(
        self, order: ProjectSalesOrder, lines: Sequence[Any], *, actor_user_id: Optional[str]
    ) -> None:
        """Replace the splits of every named line, all or nothing (UAC B3, B4).

        Every line is checked before anything is written, so one refusal saves nothing.
        A line given no splits, or one split equal to its base on its own line and date with
        no remark, is not split: its rows are deleted.
        """
        facts = self.line_facts(order)
        plans: List[Tuple[LineFacts, Decimal, Optional[date], List[Dict[str, Any]]]] = []
        for entry in lines:
            line_id = str(entry.line_id)
            f = facts.get(line_id)
            if f is None:
                raise AppException(
                    status_code=422,
                    message="A line in this save is not on this sales order. Reload and try again.",
                    code="line_split_line_not_on_order",
                )
            label = f"Line {f.line.line_no}"
            base_qty, base_date = f.base()
            rows: List[Dict[str, Any]] = []
            for split in entry.splits:
                qty = _dec(split.qty)
                if qty <= _ZERO:
                    raise AppException(
                        status_code=422,
                        message=f"{label}: every split needs a quantity above 0.",
                        code="line_split_qty_not_positive",
                    )
                is_cancel = bool(split.is_cancel)
                when = None if is_cancel else split.delivery_date
                if not is_cancel and when is None:
                    raise AppException(
                        status_code=422,
                        message=f"{label}: every split needs a delivery date or Cancel.",
                        code="line_split_date_missing",
                    )
                sits_on = line_id if is_cancel else (
                    str(split.sits_on_line_id) if split.sits_on_line_id else None
                )
                if sits_on and sits_on != line_id:
                    target = facts.get(sits_on)
                    if (
                        target is None
                        or target.item_code != f.item_code
                        or target.location != f.location
                    ):
                        raise AppException(
                            status_code=422,
                            message=(
                                f"{label}: a split can only sit on a line of the same item "
                                f"and location on this sales order."
                            ),
                            code="line_split_sits_on_invalid",
                        )
                rows.append(
                    {
                        "qty": qty,
                        "delivery_date": when,
                        "is_cancel": is_cancel,
                        "sits_on_line_id": sits_on,
                        "remark": (split.remark or "").strip() or None,
                    }
                )
            total = sum((r["qty"] for r in rows), _ZERO)
            if rows and total != base_qty:
                raise AppException(
                    status_code=422,
                    message=(
                        f"{label}: the splits add up to {qty_text(total)}, "
                        f"not {qty_text(base_qty)}."
                    ),
                    code="line_split_sum_mismatch",
                )
            plans.append((f, base_qty, base_date, rows))

        for f, base_qty, base_date, rows in plans:
            for existing in f.splits:
                self.db.delete(existing)
            if self._is_unsplit(f, base_qty, base_date, rows):
                continue
            for seq, r in enumerate(rows):
                self.db.add(
                    SOLineSplit(
                        project_sales_order_id=str(order.id),
                        so_line_id=f.line_id,
                        seq=seq,
                        qty=r["qty"],
                        delivery_date=r["delivery_date"],
                        is_cancel=r["is_cancel"],
                        sits_on_line_id=r["sits_on_line_id"],
                        remark=r["remark"],
                        base_qty=base_qty,
                        base_date=base_date,
                        created_by=actor_user_id,
                        updated_by=actor_user_id,
                    )
                )
        self.db.flush()

    @staticmethod
    def _is_unsplit(
        f: LineFacts, base_qty: Decimal, base_date: Optional[date], rows: List[Dict[str, Any]]
    ) -> bool:
        if not rows:
            return True
        if len(rows) != 1:
            return False
        only = rows[0]
        return (
            not only["is_cancel"]
            and only["qty"] == base_qty
            and only["sits_on_line_id"] == f.line_id
            and only["delivery_date"] == base_date
            and not only["remark"]
        )


# ---------------------------------------------------------------------- purchasing outputs


def _fmt_date(value: Optional[date]) -> str:
    """`21/07/2026`, the way the OI email and their sheet spell a date."""
    return value.strftime("%d/%m/%Y") if value else ""


@dataclass
class SplitView:
    """One stored split as the purchasing outputs read it."""

    split: SOLineSplit
    origin_line_no: Optional[int]
    #: The line it sits on now (NULL once that line is cancelled, UAC C2).
    sits_on: Optional[str]

    @property
    def origin(self) -> str:
        return str(self.split.so_line_id)

    @property
    def word(self) -> str:
        return change_word(
            is_cancel=bool(self.split.is_cancel),
            delivery_date=self.split.delivery_date,
            base_date=self.split.base_date,
        )


class SplitOutputs:
    """The splits an output (OI Excel, OI email, OI Lines) needs for a set of lines, in two
    queries: every split OF those lines (origin view) and every split sitting ON them."""

    def __init__(self, db: Session, line_ids: Iterable[str]):
        wanted = sorted({str(i) for i in line_ids if i})
        self.by_origin: Dict[str, List[SplitView]] = {}
        self.by_sits_on: Dict[str, List[SplitView]] = {}
        self.line_nos: Dict[str, Optional[int]] = {}
        if not wanted:
            return
        from sqlalchemy import or_

        splits = (
            db.query(SOLineSplit)
            .filter(
                or_(
                    SOLineSplit.so_line_id.in_(wanted),
                    SOLineSplit.sits_on_line_id.in_(wanted),
                )
            )
            .order_by(SOLineSplit.so_line_id, SOLineSplit.seq.asc(), SOLineSplit.created_at.asc())
            .all()
        )
        if not splits:
            return
        service = LineSplitService(db)
        sits = service.effective_sits_on(splits)
        named = sorted(
            {str(s.so_line_id) for s in splits}
            | {str(s.sits_on_line_id) for s in splits if s.sits_on_line_id}
        )
        line_nos = {
            str(line_id): line_no
            for line_id, line_no in db.query(
                ProjectSalesOrderLine.id, ProjectSalesOrderLine.line_no
            )
            .filter(ProjectSalesOrderLine.id.in_(named))
            .all()
        }
        self.line_nos = line_nos
        wanted_set = set(wanted)
        for s in splits:
            view = SplitView(
                split=s,
                origin_line_no=line_nos.get(str(s.so_line_id)),
                sits_on=sits.get(str(s.id)),
            )
            if str(s.so_line_id) in wanted_set:
                self.by_origin.setdefault(str(s.so_line_id), []).append(view)
            if view.sits_on and view.sits_on in wanted_set:
                self.by_sits_on.setdefault(view.sits_on, []).append(view)
        for views in self.by_sits_on.values():
            # Own splits first, then other lines' in line order.
            views.sort(key=lambda v: (v.origin != v.sits_on, v.origin_line_no or 0, v.split.seq))

    def sitting_remark(self, view: SplitView, line_id: str) -> str:
        """REMARK of one OI Excel row printed for a split sitting on `line_id` (UAC D2):
        the change word, "from line N" when it is another line's, and the remark."""
        parts = [view.word]
        if view.origin != str(line_id):
            parts.append(f"from line {view.origin_line_no}")
        if view.split.remark:
            parts.append(view.split.remark)
        return ", ".join(parts)

    def plan_text(self, line_id: str) -> str:
        """The split plan of one line in words, for an OI Excel row that cannot be printed
        split by split: its own splits (and where they sit) then the other lines' splits
        sitting on it. Empty for a line with none."""
        line_id = str(line_id)
        views = list(self.by_origin.get(line_id, []))
        views += [v for v in self.by_sits_on.get(line_id, []) if v.origin != line_id]
        if not views:
            return ""
        pieces = []
        for v in views:
            text = f"{qty_text(v.split.qty)} {v.word}"
            if v.split.delivery_date:
                text += f" {_fmt_date(v.split.delivery_date)}"
            if v.origin != line_id:
                text += f" from line {v.origin_line_no}"
            elif not v.split.is_cancel and v.sits_on is None:
                text += " new line"
            elif not v.split.is_cancel and v.sits_on != line_id:
                text += f" to line {self.line_nos.get(str(v.sits_on))}"
            if v.split.remark:
                text += f" ({v.split.remark})"
            pieces.append(text)
        return "SPLIT " + "; ".join(pieces)
