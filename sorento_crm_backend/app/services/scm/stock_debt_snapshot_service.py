"""Stock Debt snapshots (STOCK-DEBT-SNAPSHOT): the board frozen at a moment, with a decision
per product.

Nothing here reads the book on its own. A save takes ONE `StockDebtService._board(...)` read
and stores it as it is; every drill line goes through `StockDebtService._demand_row`, the
shape `cell()` serves. A snapshot is never recomputed afterwards: the only live reads are the
`now` block of a line and the reserve fields of its order inquiry rows, taken at request time.

Plan: documentation/plans/scm/PLAN-stock-debt-snapshot-15oct.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, defer

from app.models.inventory import Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import Supplier
from app.models.project_so import (
    OrderInquiry,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
    OrderInquiryRow,
    ProjectSalesOrderLine,
    RESERVE_REQUESTED,
)
from app.models.stock_debt_snapshot import StockDebtSnapshot, StockDebtSnapshotRow
from app.models.user import User
from app.services.error_handler import AppException, handle_not_found
from app.services.order_inquiry_reserve_service import (
    _REQUESTABLE_STATES,
    _REQUESTABLE_VERBS,
    _open_request_row_ids,
    _remaining,
)
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.demand import demand_qty
from app.services.scm.low_stock_report_service import MAX_LOW_STOCK_ROWS
from app.services.scm.stock_debt_service import StockDebtService
from app.services.scm.supply_assignment import month_key

_ID_CHUNK = 2000
_NOTE_MAX = 2000
_DECISION_LABELS = {"reserve": "Reserve", "buy": "Buy", "ignore": "Ignore"}


def _chunks(values: Sequence[str]):
    values = list(values)
    for start in range(0, len(values), _ID_CHUNK):
        yield values[start : start + _ID_CHUNK]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _dmy(value: Any) -> str:
    return value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else str(value)


class StockDebtSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ save

    def create(self, *, name: str, filters: Dict[str, Any], user_id: str) -> StockDebtSnapshot:
        """Freeze the board for `filters` (the `StockDebtFilters` fields as a dict)."""
        svc = StockDebtService(self.db)
        board = svc._board(
            query=filters.get("query"),
            group=filters.get("group"),
            location=filters.get("location"),
            exclude_location=filters.get("exclude_location"),
            only_debt=filters.get("only_debt", True),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            supplier_ids=filters.get("supplier_ids"),
            book=filters.get("book", "all"),
            projected=filters.get("projected", False),
        )
        rows = board["rows"]
        if len(rows) > MAX_LOW_STOCK_ROWS:
            raise AppException(status_code=422, message="Narrow the filters first")

        stored_lines: List[List[Dict[str, Any]]] = []
        core_ids: List[str] = []
        for row in rows:
            result = board["assignments"][row["product_id"]]
            order_ids = {line.line.key: line.line.sales_order_id for line in result.lines}
            lines = [
                {
                    **svc._demand_row(line, order_ids),
                    "bucket": line.bucket,
                    "sales_order_line_id": str(line.line.key),
                }
                for line in result.lines
            ]
            # The drill's own order (`cell()`): due date, then SO number.
            lines.sort(key=lambda l: (l["required_date"] is None, l["required_date"] or 0, l["so_number"]))
            stored_lines.append(lines)
            core_ids.extend(line["sales_order_line_id"] for line in lines)

        linked = self._linked_oi_rows(core_ids)
        for lines in stored_lines:
            for line in lines:
                line["oi_rows"] = [
                    self._frozen_oi(oi_row, inquiry)
                    for oi_row, inquiry in linked.get(line["sales_order_line_id"], [])
                ]

        header = StockDebtSnapshot(
            name=name,
            filters=jsonable_encoder({**filters, **self._labels(filters)}),
            months=list(board["months"]),
            tba_month=month_key(svc._tba_from()),
            totals=jsonable_encoder(board["totals"]),
            row_count=len(rows),
            created_by=user_id,
        )
        self.db.add(header)
        self.db.flush()
        self.db.add_all(
            StockDebtSnapshotRow(
                snapshot_id=header.id,
                position=position,
                product_id=row["product_id"],
                product_code=row["product_code"],
                product_name=row["product_name"],
                row_data=jsonable_encoder(row),
                lines=jsonable_encoder(lines),
            )
            for position, (row, lines) in enumerate(zip(rows, stored_lines))
        )
        self.db.commit()
        self.db.refresh(header)
        return header

    def _labels(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """The location codes and supplier names the filters named, as they read today."""

        def codes(ids: Sequence[str]) -> List[str]:
            if not ids:
                return []
            by_id = {
                str(w.id): w.warehouse_code
                for w in self.db.query(Warehouse).filter(Warehouse.id.in_(list(ids))).all()
            }
            return [by_id.get(str(i), "") for i in ids if by_id.get(str(i))]

        supplier_ids = [s for s in (filters.get("supplier_ids") or []) if s != "none"]
        names = {
            str(s.id): s.supplier_name
            for s in self.db.query(Supplier).filter(Supplier.id.in_(supplier_ids)).all()
        } if supplier_ids else {}
        supplier_names = [
            "No supplier" if s == "none" else names[s]
            for s in (filters.get("supplier_ids") or [])
            if s == "none" or s in names
        ]
        return {
            "location_codes": codes(filters.get("location") or []),
            "exclude_location_codes": codes(filters.get("exclude_location") or []),
            "supplier_names": supplier_names,
        }

    def _linked_oi_rows(self, core_line_ids: Sequence[str]) -> Dict[str, List[tuple]]:
        """`{core line id: [(OrderInquiryRow, OrderInquiry)]}` for every row whose
        `so_line_id` is a project mirror of that core line (AC-5)."""
        out: Dict[str, List[tuple]] = {}
        for chunk in _chunks(sorted(set(core_line_ids))):
            query = (
                self.db.query(
                    ProjectSalesOrderLine.core_sales_order_line_id, OrderInquiryRow, OrderInquiry
                )
                .join(OrderInquiryRow, OrderInquiryRow.so_line_id == ProjectSalesOrderLine.id)
                .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
                .filter(ProjectSalesOrderLine.core_sales_order_line_id.in_(chunk))
                .order_by(OrderInquiry.inquiry_no, OrderInquiryRow.id)
            )
            for core_id, oi_row, inquiry in query.all():
                out.setdefault(str(core_id), []).append((oi_row, inquiry))
        return out

    @staticmethod
    def _frozen_oi(oi_row: OrderInquiryRow, inquiry: OrderInquiry) -> Dict[str, Any]:
        return {
            "oi_id": str(inquiry.id),
            "inquiry_no": inquiry.inquiry_no,
            "row_id": str(oi_row.id),
            "qty": float(oi_row.qty),
            "delivery_date": oi_row.delivery_date.isoformat() if oi_row.delivery_date else None,
            "stock_location": oi_row.stock_location,
            "verb": oi_row.verb,
            "state": oi_row.state,
        }

    # ------------------------------------------------------------------ read

    def _get_snapshot(self, snapshot_id: str) -> StockDebtSnapshot:
        snapshot = (
            self.db.query(StockDebtSnapshot).filter(StockDebtSnapshot.id == snapshot_id).first()
        )
        if snapshot is None:
            raise handle_not_found("Snapshot", snapshot_id)
        return snapshot

    def _get_row(self, snapshot_id: str, row_id: str, *, with_lines: bool = False):
        query = self.db.query(StockDebtSnapshotRow).filter(
            StockDebtSnapshotRow.id == row_id, StockDebtSnapshotRow.snapshot_id == snapshot_id
        )
        if not with_lines:
            query = query.options(defer(StockDebtSnapshotRow.lines))
        row = query.first()
        if row is None:
            raise handle_not_found("Snapshot row", row_id)
        return row

    def _summaries(
        self, snapshots: Sequence[StockDebtSnapshot], user_id: str
    ) -> List[Dict[str, Any]]:
        ids = [s.id for s in snapshots]
        decided = dict(
            self.db.query(StockDebtSnapshotRow.snapshot_id, func.count(StockDebtSnapshotRow.id))
            .filter(
                StockDebtSnapshotRow.snapshot_id.in_(ids),
                StockDebtSnapshotRow.decision.isnot(None),
            )
            .group_by(StockDebtSnapshotRow.snapshot_id)
            .all()
        ) if ids else {}
        creators = [s.created_by for s in snapshots if s.created_by]
        names = dict(
            self.db.query(User.id, User.name).filter(User.id.in_(creators)).all()
        ) if creators else {}
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "filters": s.filters,
                "filters_label": self._filters_label(s.filters),
                "row_count": s.row_count,
                "decided_count": decided.get(s.id, 0),
                "created_by_name": names.get(s.created_by),
                "created_at": s.created_at,
                "can_delete": s.created_by is not None and str(s.created_by) == str(user_id),
            }
            for s in snapshots
        ]

    @staticmethod
    def _filters_label(filters: Dict[str, Any]) -> str:
        parts: List[str] = []
        if filters.get("query"):
            parts.append(f'"{filters["query"]}"')
        parts.append(f"Book {filters.get('book') or 'all'}")
        if filters.get("location_codes"):
            parts.append("Location " + ", ".join(filters["location_codes"]))
        if filters.get("exclude_location_codes"):
            parts.append("Except " + ", ".join(filters["exclude_location_codes"]))
        if filters.get("supplier_names"):
            parts.append("Supplier " + ", ".join(filters["supplier_names"]))
        start, end = filters.get("date_from"), filters.get("date_to")
        if start or end:
            parts.append(
                "Delivery "
                + (f"{_dmy(datetime.fromisoformat(start))} " if start else "")
                + ("to " + _dmy(datetime.fromisoformat(end)) if end else "onwards")
            )
        if filters.get("only_debt", True):
            parts.append("Only in debt")
        if filters.get("projected"):
            parts.append("Projected")
        return " · ".join(parts)

    def get(self, snapshot_id: str, user_id: str) -> Dict[str, Any]:
        snapshot = self._get_snapshot(snapshot_id)
        position = (StockDebtSnapshot.created_at, StockDebtSnapshot.id)
        newer = (
            self.db.query(StockDebtSnapshot.id)
            .filter(
                or_(
                    StockDebtSnapshot.created_at > snapshot.created_at,
                    and_(
                        StockDebtSnapshot.created_at == snapshot.created_at,
                        StockDebtSnapshot.id > snapshot.id,
                    ),
                )
            )
            .order_by(*(c.asc() for c in position))
            .first()
        )
        older = (
            self.db.query(StockDebtSnapshot.id)
            .filter(
                or_(
                    StockDebtSnapshot.created_at < snapshot.created_at,
                    and_(
                        StockDebtSnapshot.created_at == snapshot.created_at,
                        StockDebtSnapshot.id < snapshot.id,
                    ),
                )
            )
            .order_by(*(c.desc() for c in position))
            .first()
        )
        return {
            **self._summaries([snapshot], user_id)[0],
            "months": snapshot.months,
            "tba_month": snapshot.tba_month,
            "totals": snapshot.totals,
            # The list is newest first: "previous" is the newer neighbour, "next" the older.
            "prev_id": str(newer[0]) if newer else None,
            "next_id": str(older[0]) if older else None,
        }

    def list(
        self, *, user_id: str, query: Optional[str], page: int, limit: int
    ) -> Dict[str, Any]:
        q = self.db.query(StockDebtSnapshot)
        if query:
            q = q.filter(StockDebtSnapshot.name.ilike(_like(query.strip()), escape="\\"))
        total = q.count()
        snapshots = (
            q.order_by(StockDebtSnapshot.created_at.desc(), StockDebtSnapshot.id.desc())
            .offset(max(page - 1, 0) * limit)
            .limit(limit)
            .all()
        )
        return {
            "data": self._summaries(snapshots, user_id),
            "pagination": {"total": total, "page": page, "limit": limit},
        }

    def rows(
        self,
        snapshot_id: str,
        *,
        query: Optional[str],
        decision: Optional[str],
        page: int,
        limit: int,
    ) -> Dict[str, Any]:
        self._get_snapshot(snapshot_id)
        q = self.db.query(StockDebtSnapshotRow).filter(
            StockDebtSnapshotRow.snapshot_id == snapshot_id
        )
        if query and query.strip():
            pattern = _like(query.strip())
            q = q.filter(
                or_(
                    StockDebtSnapshotRow.product_code.ilike(pattern, escape="\\"),
                    StockDebtSnapshotRow.product_name.ilike(pattern, escape="\\"),
                )
            )
        if decision == "decided":
            q = q.filter(StockDebtSnapshotRow.decision.isnot(None))
        elif decision == "undecided":
            q = q.filter(StockDebtSnapshotRow.decision.is_(None))
        elif decision:
            q = q.filter(StockDebtSnapshotRow.decision == decision)
        total = q.count()
        found = (
            q.options(defer(StockDebtSnapshotRow.lines))
            .order_by(StockDebtSnapshotRow.position)
            .offset(max(page - 1, 0) * limit)
            .limit(limit)
            .all()
        )
        return {
            "data": self._row_payloads(found),
            "pagination": {"total": total, "page": page, "limit": limit},
        }

    def _row_payloads(self, rows: Sequence[StockDebtSnapshotRow]) -> List[Dict[str, Any]]:
        warehouse_ids = [r.decision_warehouse_id for r in rows if r.decision_warehouse_id]
        codes = dict(
            self.db.query(Warehouse.id, Warehouse.warehouse_code)
            .filter(Warehouse.id.in_(warehouse_ids))
            .all()
        ) if warehouse_ids else {}
        user_ids = [r.decided_by for r in rows if r.decided_by]
        names = dict(
            self.db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
        ) if user_ids else {}
        return [
            {
                **r.row_data,
                "row_id": str(r.id),
                "decision": r.decision,
                "decision_warehouse_id": r.decision_warehouse_id,
                "decision_warehouse_code": codes.get(r.decision_warehouse_id),
                "decision_note": r.decision_note,
                "decided_by_name": names.get(r.decided_by),
                "decided_at": r.decided_at,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ the drill

    def lines(self, snapshot_id: str, row_id: str, month: Optional[str]) -> Dict[str, Any]:
        """The stored lines of one row (one bucket when `month` is given), each with the
        live `now` read at this moment."""
        self._get_snapshot(snapshot_id)
        row = self._get_row(snapshot_id, row_id, with_lines=True)
        lines = [dict(l) for l in row.lines if month is None or l["bucket"] == month]
        lines.sort(key=lambda l: (l["required_date"] is None, l["required_date"] or "", l["so_number"]))
        core_ids = sorted({l["sales_order_line_id"] for l in lines})

        now = self._line_now(core_ids)
        linked = self._linked_oi_rows(core_ids)
        frozen_ids = sorted({oi["row_id"] for l in lines for oi in l["oi_rows"]})
        live_ids = frozen_ids + [
            str(oi_row.id)
            for core_id in core_ids
            for oi_row, _inq in linked.get(core_id, [])
        ]
        oi_now = self._oi_now(sorted(set(live_ids)))

        for line in lines:
            line["now"] = now.get(line["sales_order_line_id"]) or {
                "so_status": "missing", "open_qty": 0.0,
            }
            seen = {oi["row_id"] for oi in line["oi_rows"]}
            oi_rows = [
                {**oi, **oi_now.get(oi["row_id"], self._oi_gone())} for oi in line["oi_rows"]
            ]
            for oi_row, inquiry in linked.get(line["sales_order_line_id"], []):
                if str(oi_row.id) in seen:
                    continue
                oi_rows.append(
                    {
                        **self._frozen_oi(oi_row, inquiry),
                        **oi_now[str(oi_row.id)],
                        "after_snapshot": True,
                    }
                )
            line["oi_rows"] = oi_rows
        return {"data": lines}

    def _line_now(self, core_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for chunk in _chunks(core_ids):
            for line_id, status, open_qty in (
                self.db.query(SalesOrderLine.id, SalesOrder.status, demand_qty())
                .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
                .filter(SalesOrderLine.id.in_(chunk))
                .all()
            ):
                out[str(line_id)] = {
                    "so_status": status,
                    "open_qty": float(open_qty or 0) if status == "open" else 0.0,
                }
        return out

    @staticmethod
    def _oi_gone() -> Dict[str, Any]:
        return {
            "now_state": "missing", "reserve_state": None, "remaining": None,
            "requestable": False,
        }

    def _oi_now(self, row_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """The live reading of order inquiry rows: state, latest reserve request, what is
        left to request and whether the existing request would accept the row. The rules are
        the reserve service's own (`create_request`), imported not copied."""
        if not row_ids:
            return {}
        rows = {
            str(r.id): r
            for chunk in _chunks(row_ids)
            for r in self.db.query(OrderInquiryRow).filter(OrderInquiryRow.id.in_(chunk)).all()
        }
        open_requests = _open_request_row_ids(self.db, list(rows))
        latest: Dict[str, str] = {}
        for rr_row_id, state, parked_at in (
            self.db.query(
                OrderInquiryReserveRequestRow.row_id,
                OrderInquiryReserveRequest.state,
                OrderInquiryReserveRequest.parked_at,
            )
            .join(
                OrderInquiryReserveRequest,
                OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
            )
            .filter(OrderInquiryReserveRequestRow.row_id.in_(list(rows)))
            .order_by(
                OrderInquiryReserveRequest.requested_at.desc(),
                OrderInquiryReserveRequest.ordinal.desc(),
            )
            .all()
        ):
            latest.setdefault(
                str(rr_row_id),
                "parked" if state == RESERVE_REQUESTED and parked_at is not None else state,
            )
        out: Dict[str, Dict[str, Any]] = {}
        for row_id, row in rows.items():
            remaining = _remaining(self.db, row)
            out[row_id] = {
                "now_state": row.state,
                "reserve_state": latest.get(row_id),
                "remaining": float(remaining),
                "requestable": (
                    row.verb in _REQUESTABLE_VERBS
                    and row.state in _REQUESTABLE_STATES
                    and row_id not in open_requests
                    and remaining > 0
                ),
            }
        return out

    # ------------------------------------------------------------------ decide, delete

    def set_decision(
        self,
        snapshot_id: str,
        row_id: str,
        *,
        user_id: str,
        decision: Optional[str],
        warehouse_id: Optional[str],
        note: Optional[str],
    ) -> Dict[str, Any]:
        self._get_snapshot(snapshot_id)
        row = self._get_row(snapshot_id, row_id)
        if decision is None:
            row.decision = row.decision_warehouse_id = row.decision_note = None
            row.decided_by = row.decided_at = None
        else:
            if warehouse_id:
                if decision != "reserve":
                    raise AppException(
                        status_code=422, message="A warehouse is only for a reserve decision."
                    )
                if warehouse_id not in ProjectSupplyService(self.db).site_pool_warehouses():
                    raise AppException(
                        status_code=422, message="Choose a site pool to reserve from."
                    )
            if note is not None and len(note) > _NOTE_MAX:
                raise AppException(status_code=422, message="The note is too long.")
            row.decision = decision
            row.decision_warehouse_id = warehouse_id or None
            row.decision_note = (note or "").strip() or None
            row.decided_by = user_id
            row.decided_at = datetime.utcnow()
        self.db.commit()
        return self._row_payloads([row])[0]

    def get_own(self, snapshot_id: str, user_id: Optional[str]) -> StockDebtSnapshot:
        """The snapshot, only for its creator: anyone else gets the same 404 as a snapshot
        that does not exist (also checked when the delete is parked, so no countdown runs
        for a snapshot that is not theirs)."""
        snapshot = self._get_snapshot(snapshot_id)
        if snapshot.created_by is None or str(snapshot.created_by) != str(user_id):
            raise handle_not_found("Snapshot", snapshot_id)
        return snapshot

    def delete(self, snapshot_id: str, user_id: str) -> None:
        """Hard delete of the header (its rows go with it, ON DELETE CASCADE)."""
        self.db.delete(self.get_own(snapshot_id, user_id))
        self.db.commit()

    # ------------------------------------------------------------------ export

    def export(self, snapshot_id: str, *, split: str, show_aux: bool):
        """Today's workbook, from the STORED rows and axis (AC-16), with Decision, From and
        Note appended. Returns what `StockDebtService._render_workbook` returns."""
        from app.services.scm.proforma_invoice_service import _xlsx_safe_text

        snapshot = self._get_snapshot(snapshot_id)
        stored = (
            self.db.query(StockDebtSnapshotRow)
            .options(defer(StockDebtSnapshotRow.lines))
            .filter(StockDebtSnapshotRow.snapshot_id == snapshot_id)
            .order_by(StockDebtSnapshotRow.position)
            .all()
        )
        payloads = self._row_payloads(stored)
        listing = {"months": list(snapshot.months), "data": payloads}
        extra = (
            ("Decision", 12, lambda r: _DECISION_LABELS.get(r["decision"], "")),
            ("From", 12, lambda r: _xlsx_safe_text(r["decision_warehouse_code"] or "")),
            ("Note", 40, lambda r: _xlsx_safe_text(r["decision_note"] or "")),
        )
        return StockDebtService(self.db)._render_workbook(
            listing, split=split, show_aux=show_aux, extra_columns=extra
        )
