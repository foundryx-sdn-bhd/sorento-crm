"""The order inquiry HEADER list, detail and related documents (S2/S3,
`PLAN-oi-header-list-detail.md`, `oi-header-list-detail-acceptance-criteria.md`,
AC-LS-01..07, AC-DT-01..03).

One OI = one sales order's instructions. `order_inquiry_worklist_service.py` is already
1.7k+ lines answering "every ROW, across every subject" - this file answers the
different, HEADER-shaped question a purchasing manager actually opens first: which
document, who raised it, how much of it still waits.

Everything here reuses the exact join shape `OrderInquiryWorklistService._base` already
proved correct (an adopted AutoCount order's customer comes off the core sales order,
an authored one's off its issuing party; its project title falls back to the SO's own
free-text label) - copied, not reinvented, because it is the same domain question this
file is not the first to answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, List, Literal, Optional, get_args

from sqlalchemy import Date, String, and_, case, cast, func, or_
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, aliased

from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation, Supplier
from app.models.project_so import (
    ACK_AWAITING,
    ACK_CHANGED,
    INQUIRY_CANCELLED,
    IV_ORDER,
    IV_ORDER_BACK,
    IV_RESERVE_AND_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRaise,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.projects import Project, ProjectParty, ProjectPurchaseOrder
from app.models.sales_agent import SalesAgent
from app.models.user import User
from app.schemas.project_order_inquiry import (
    OrderInquiryHeaderDetailOut,
    OrderInquiryHeaderOut,
    OrderInquiryRaiseHistoryEntryOut,
    OrderInquiryRelatedDocumentsOut,
    OrderInquiryRelatedPOOut,
    OrderInquiryRelatedSPOOut,
)
from app.services.error_handler import AppException
from app.services.order_inquiry_reserve_service import reserve_status_predicates
from app.services.scm import spo_supply

_ZERO = Decimal("0")
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """A typed word as a LITERAL - copied from `order_inquiry_worklist_service.py`'s own
    `_escape_like` (established precedent: every service that builds an `ilike` declares
    its own copy rather than importing another module's private helper -
    `product_code_resolution.py`, `outstanding_report_service.py`,
    `sales_report_service.py`)."""
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def _dec(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed stored number is data, not a crash
        return _ZERO


def _qty_str(value: Any) -> str:
    return format(_dec(value).normalize(), "f")


# The Project column's text - a registered project wins, an adopted AutoCount order
# (`project_id` NULL by design) falls back to the SO's own free-text label. Same rule
# `order_inquiry_worklist_service.py`'s `_PROJECT_TITLE` states, copied rather than
# imported: it is a private module constant there, not part of that module's contract.
_PROJECT_TITLE = func.coalesce(Project.title, SalesOrder.project_label)
_SO_NUMBER = func.coalesce(ProjectSalesOrder.autocount_doc_no, ProjectSalesOrder.provisional_ref)
_SO_DATE = func.coalesce(
    SalesOrder.order_date,
    cast(ProjectSalesOrder.published_at, Date),
    cast(ProjectSalesOrder.created_at, Date),
)
# An adopted order's customer is the core SO's own; an authored one's is the party the
# purchase order was issued to (`order_inquiry_worklist_service.py`'s own `_CUSTOMER_ID`).
_CUSTOMER_ID = func.coalesce(ProjectParty.customer_id, SalesOrder.customer_id)
# The Agent column's text (reviewer B1): `person_label` is NULL for every one of the 80
# agents on the prod copy - the name purchasing actually knows an agent by is
# `sales_agents.sales_agent`. `person_label` is kept as the PREFERRED value (it groups
# several codes under one person for reporting, `app/models/sales_agent.py`), so a
# company that DOES populate it still gets that grouping; the fallback only covers the
# common case where it never was. The worklist's own Agent facet
# (`order_inquiry_worklist_service.py::_agents`) already computes `label or code` in
# Python for the same reason - this is the SQL-side equivalent, used everywhere this
# file reads or filters on the agent's name.
_AGENT_NAME = func.coalesce(SalesAgent.person_label, SalesAgent.sales_agent)

#: The Lines tab's buy verbs (`isInquiryBuyRow`, `_shared/lib/orderInquiryWorklist.ts`):
#: the only rows whose `qty` is in the Requested footer, and so in `qty_total` (G10).
_BUY_VERBS = (IV_ORDER, IV_ORDER_BACK, IV_RESERVE_AND_ORDER)

HeaderSort = Literal[
    "raised_at",
    "inquiry_no",
    "so_number",
    "raised_by",
    "lines_total",
    "qty_total",
    "customer",
    "project",
    "agent",
    "so_date",
    "status",
]
HeaderState = Literal["outstanding", "completed", "all"]

ReserveStatus = Literal["parked", "needs_reserve", "notice_parked", "reserved"]

DEFAULT_SORT_FIELD: HeaderSort = "raised_at"


@dataclass
class HeaderListResult:
    """`OrderInquiryHeaderService.list`'s own return - never the `ListResponse`
    envelope itself (the route builds that), so a service-level caller (a test, a
    future script) reads plain attributes rather than a dict shaped for the wire."""

    items: List[OrderInquiryHeaderOut] = field(default_factory=list)
    total: int = 0
    page: int = 1
    limit: int = 25


class OrderInquiryHeaderService:
    """Reads header rows - one per order inquiry, never one per instruction."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ shared query

    def _joined(self):
        """The one join chain every reader below starts from. Every join past
        `OrderInquiry`/`ProjectSalesOrder` is OUTER and keyed on a primary key, so it
        can only narrow a 1:1 lookup, never fan a header out into more than one row."""
        return (
            self.db.query(OrderInquiry)
            .select_from(OrderInquiry)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(
                ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id
            )
            .outerjoin(Customer, Customer.id == _CUSTOMER_ID)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
            .outerjoin(User, User.id == OrderInquiry.raised_by)
        )

    def _rows_agg(self):
        """One row per header, counted the way the OI detail's Lines tab renders it
        (`PLAN-oi-no-double-count-25sep.md`, owner ruling 26 Sep 2026, G10: "The header
        list lines and qty should be truthful to what is shown in the Lines tab").

        * A LINE is the rows' sales order line, keyed exactly as the tab folds them
          (`orderInquiryLineFold.ts::foldKeyOf`): the row's MIRROR line (`so_line_id`),
          else the row alone. The mirror is one-to-one with the core line and is there
          before AutoCount reconciles it (PR #1266 review S1: a core-line key split an
          unreconciled line in two). Only NON-CANCELLED rows make a line render,
          so a line whose every row was superseded is not one; a used row and a
          cancelled sales order line's row still do (the tab shows them folded, or grey).
        * `lines_total` = distinct lines. `lines_to_confirm` = distinct lines with a
          non-cancelled row in awaiting / changed, used rows included (G6).
        * `qty_total` = the tab's Requested footer: `qty` of the live buy rows (a buy
          verb, not cancelled, not `redirected_to_pool`) on lines not cancelled on the SO.
          The two line joins below are for that last test (`line_status`) only.

        A subquery rather than a join on the outer query, so a header's row count never
        fans the header itself out."""
        line_key = func.coalesce(
            cast(OrderInquiryRow.so_line_id, String), cast(OrderInquiryRow.id, String)
        )
        live_buy_qty = case(
            (
                and_(
                    OrderInquiryRow.verb.in_(_BUY_VERBS),
                    OrderInquiryRow.redirected_to_pool.isnot(True),
                    func.coalesce(SalesOrderLine.line_status, "") != "cancelled",
                ),
                OrderInquiryRow.qty,
            ),
            else_=0,
        )
        return (
            self.db.query(
                OrderInquiryRow.order_inquiry_id.label("order_inquiry_id"),
                func.count(func.distinct(line_key)).label("lines_total"),
                func.count(func.distinct(line_key))
                .filter(OrderInquiryRow.ack_state.in_((ACK_AWAITING, ACK_CHANGED)))
                .label("lines_to_confirm"),
                func.sum(live_buy_qty).label("qty_total"),
            )
            .select_from(OrderInquiryRow)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .outerjoin(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
            .group_by(OrderInquiryRow.order_inquiry_id)
            .subquery()
        )

    def _reserve_agg(self):
        """OI-STAGED-NOTIFY (AC-SN-30): one row per header that has any reserve request row,
        saying which of the four statuses its rows are in and who parked what. One aggregate
        subquery joined to the page, never a query per header."""
        pred = reserve_status_predicates()
        return (
            self.db.query(
                OrderInquiryReserveRequest.order_inquiry_id.label("order_inquiry_id"),
                func.bool_or(pred["parked"]).label("has_parked"),
                func.bool_or(pred["needs_reserve"]).label("has_needs"),
                func.bool_or(pred["notice_parked"]).label("has_notice"),
                func.bool_or(pred["reserved"]).label("has_reserved"),
                func.max(OrderInquiryReserveRequest.requested_by)
                .filter(pred["parked"])
                .label("parked_requester"),
                func.max(OrderInquiryReserveRequestRow.notice_parked_by)
                .filter(pred["notice_parked"])
                .label("notice_parker"),
            )
            .select_from(OrderInquiryReserveRequestRow)
            .join(
                OrderInquiryReserveRequest,
                OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
            )
            .group_by(OrderInquiryReserveRequest.order_inquiry_id)
            .subquery()
        )

    def _base(
        self,
        *,
        agg,
        reserve: Optional[str] = None,
        query: Optional[str] = None,
        raised_by: Optional[str] = None,
        agent: Optional[str] = None,
        project: Optional[str] = None,
        state: HeaderState = "outstanding",
    ):
        """`agg` is ALWAYS the caller's own `_rows_agg()` instance, never one built in
        here: `list()` needs to sort by `lines_total` / `qty_total` / `status`, which
        means its `ORDER BY` has to name the EXACT SAME subquery object this method
        joins - a second `_rows_agg()` call builds a second, differently-aliased
        subquery that was never added to the `FROM` clause, and Postgres refuses the
        query outright."""
        lines_total = func.coalesce(agg.c.lines_total, 0)
        lines_to_confirm = func.coalesce(agg.c.lines_to_confirm, 0)
        qty_total = func.coalesce(agg.c.qty_total, 0)
        status_expr = case((lines_to_confirm > 0, "outstanding"), else_="completed")
        ragg = self._reserve_agg()
        requester_user = aliased(User)
        parker_user = aliased(User)
        reserve_status = case(
            (ragg.c.has_parked.is_(True), "parked"),
            (ragg.c.has_needs.is_(True), "needs_reserve"),
            (ragg.c.has_notice.is_(True), "notice_parked"),
            (ragg.c.has_reserved.is_(True), "reserved"),
            else_=None,
        )
        parked_by_name = case(
            (ragg.c.has_parked.is_(True), requester_user.name),
            (
                and_(ragg.c.has_needs.isnot(True), ragg.c.has_notice.is_(True)),
                parker_user.name,
            ),
            else_=None,
        )

        base = (
            self._joined()
            .outerjoin(agg, agg.c.order_inquiry_id == OrderInquiry.id)
            .outerjoin(ragg, ragg.c.order_inquiry_id == OrderInquiry.id)
            .outerjoin(requester_user, requester_user.id == ragg.c.parked_requester)
            .outerjoin(parker_user, parker_user.id == ragg.c.notice_parker)
            .add_columns(
                User.name.label("raised_by_name"),
                SalesOrder.id.label("sales_order_id"),
                SalesOrder.doc_type.label("so_doc_type"),
                _SO_NUMBER.label("so_number"),
                _SO_DATE.label("so_date"),
                Customer.customer_name.label("customer_name"),
                Customer.customer_code.label("customer_code"),
                ProjectSalesOrder.project_id.label("project_id"),
                _PROJECT_TITLE.label("project_title"),
                _AGENT_NAME.label("agent_name"),
                lines_total.label("lines_total"),
                lines_to_confirm.label("lines_to_confirm"),
                qty_total.label("qty_total"),
                status_expr.label("status"),
                reserve_status.label("reserve_status"),
                parked_by_name.label("parked_by_name"),
            )
        )

        if reserve is not None:
            flag = {
                "parked": ragg.c.has_parked,
                "needs_reserve": ragg.c.has_needs,
                "notice_parked": ragg.c.has_notice,
                "reserved": ragg.c.has_reserved,
            }.get(reserve)
            if flag is None:
                raise AppException(
                    422,
                    f"'{reserve}' is not a reserve status.",
                    code="invalid_reserve_filter",
                )
            base = base.filter(flag.is_(True))

        if state == "outstanding":
            base = base.filter(lines_to_confirm > 0)
        elif state == "completed":
            base = base.filter(lines_to_confirm == 0)
        elif state != "all":
            raise AppException(
                422, f"'{state}' is not a state. Use outstanding, completed or all.",
                code="invalid_state_filter",
            )

        if raised_by:
            base = base.filter(OrderInquiry.raised_by == raised_by)
        if agent:
            base = base.filter(_AGENT_NAME == agent)
        if project:
            # B2 (reviewer): filter on the SAME text the Project column prints
            # (`_PROJECT_TITLE`), not `ProjectSalesOrder.project_id` - 0 of 738 headers
            # on the prod copy carry a `project_id` (an adopted AutoCount order has no
            # registered `Project` row), so that filter never matched anything. Same
            # rule `order_inquiry_worklist_service.py`'s own `project` filter already
            # applies.
            base = base.filter(_PROJECT_TITLE == project)
        if query:
            like = f"%{_escape_like(str(query))}%"
            row_match = (
                self.db.query(OrderInquiryRow.id)
                .filter(
                    OrderInquiryRow.order_inquiry_id == OrderInquiry.id,
                    OrderInquiryRow.state != INQUIRY_CANCELLED,
                    or_(
                        OrderInquiryRow.item_code.ilike(like, escape=_LIKE_ESCAPE),
                        OrderInquiryRow.stock_location.ilike(like, escape=_LIKE_ESCAPE),
                    ),
                )
                .correlate(OrderInquiry)
                .exists()
            )
            base = base.filter(
                or_(
                    OrderInquiry.inquiry_no.ilike(like, escape=_LIKE_ESCAPE),
                    OrderInquiry.legacy_inquiry_no.ilike(like, escape=_LIKE_ESCAPE),
                    _SO_NUMBER.ilike(like, escape=_LIKE_ESCAPE),
                    Customer.customer_name.ilike(like, escape=_LIKE_ESCAPE),
                    _PROJECT_TITLE.ilike(like, escape=_LIKE_ESCAPE),
                    _AGENT_NAME.ilike(like, escape=_LIKE_ESCAPE),
                    row_match,
                )
            )
        return base

    # ------------------------------------------------------------------ serialization

    def _serialize(self, row) -> OrderInquiryHeaderOut:
        inquiry: OrderInquiry = row[0]
        return OrderInquiryHeaderOut(
            id=inquiry.id,
            inquiry_no=inquiry.inquiry_no,
            legacy_inquiry_no=inquiry.legacy_inquiry_no,
            raised_at=inquiry.raised_at,
            raised_by_name=row.raised_by_name,
            sales_order_id=row.sales_order_id,
            so_doc_type=row.so_doc_type,
            project_sales_order_id=inquiry.project_sales_order_id,
            so_number=row.so_number,
            so_date=row.so_date,
            customer_name=row.customer_name,
            customer_code=row.customer_code,
            project_id=row.project_id,
            project_title=row.project_title,
            agent_name=row.agent_name,
            lines_total=int(row.lines_total or 0),
            lines_to_confirm=int(row.lines_to_confirm or 0),
            qty_total=_qty_str(row.qty_total),
            status=row.status,
            reserve_status=row.reserve_status,
            parked_by_name=row.parked_by_name,
        )

    # ------------------------------------------------------------------ reads

    def list(
        self,
        *,
        state: HeaderState = "outstanding",
        query: Optional[str] = None,
        raised_by: Optional[str] = None,
        agent: Optional[str] = None,
        project: Optional[str] = None,
        reserve: Optional[str] = None,
        sort: Optional[str] = None,
        direction: Optional[str] = "asc",
        page: int = 1,
        limit: int = 25,
    ) -> HeaderListResult:
        field_name = sort or DEFAULT_SORT_FIELD
        if field_name not in get_args(HeaderSort):
            raise AppException(
                422, f"'{field_name}' is not a column this list can be sorted by.",
                code="unsortable_field",
            )
        if direction not in (None, "asc", "desc"):
            raise AppException(
                422, f"'{direction}' is not a sort direction. Use asc or desc.",
                code="invalid_sort_direction",
            )
        descending = direction == "desc"
        page = max(page, 1)

        # ONE `_rows_agg()` instance for this whole call: `_base` joins it into the
        # FROM clause, and the sort columns below have to name that SAME object, or
        # Postgres refuses an ORDER BY naming a subquery it never joined.
        agg = self._rows_agg()
        sort_columns = {
            "raised_at": OrderInquiry.raised_at,
            "inquiry_no": OrderInquiry.inquiry_no,
            "so_number": _SO_NUMBER,
            "raised_by": User.name,
            "lines_total": func.coalesce(agg.c.lines_total, 0),
            "qty_total": func.coalesce(agg.c.qty_total, 0),
            "customer": Customer.customer_name,
            "project": _PROJECT_TITLE,
            "agent": _AGENT_NAME,
            "so_date": _SO_DATE,
            "status": case(
                (func.coalesce(agg.c.lines_to_confirm, 0) > 0, "outstanding"),
                else_="completed",
            ),
        }

        base = self._base(
            agg=agg, state=state, query=query, raised_by=raised_by, agent=agent,
            project=project, reserve=reserve,
        )
        total = int(
            base.with_entities(func.count(OrderInquiry.id)).order_by(None).scalar() or 0
        )
        column = sort_columns[field_name]
        ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
        rows = (
            base.order_by(ordering, OrderInquiry.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return HeaderListResult(
            items=[self._serialize(row) for row in rows], total=total, page=page, limit=limit
        )

    def get(self, inquiry_id: str) -> OrderInquiryHeaderDetailOut:
        row = (
            self._base(agg=self._rows_agg(), state="all")
            .filter(OrderInquiry.id == inquiry_id)
            .first()
        )
        if row is None:
            raise AppException(404, "This order inquiry no longer exists.", code="oi_header_not_found")
        header = self._serialize(row)
        order_type = (
            self.db.query(SalesOrder.order_type)
            .select_from(ProjectSalesOrder)
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .filter(ProjectSalesOrder.id == row[0].project_sales_order_id)
            .scalar()
        )
        raises = (
            self.db.query(OrderInquiryRaise, User.name)
            .outerjoin(User, User.id == OrderInquiryRaise.raised_by)
            .filter(OrderInquiryRaise.order_inquiry_id == inquiry_id)
            .order_by(OrderInquiryRaise.raised_at.desc())
            .all()
        )
        raise_history = [
            OrderInquiryRaiseHistoryEntryOut(kind=raise_row.kind, by_name=name, at=raise_row.raised_at)
            for raise_row, name in raises
        ]
        return OrderInquiryHeaderDetailOut(
            **header.model_dump(), order_type=order_type, raise_history=raise_history
        )

    def related_documents(self, inquiry_id: str) -> OrderInquiryRelatedDocumentsOut:
        """Every purchase order / SPO this header's own non-cancelled rows are linked
        to (AC-DT-03), grouped by document - `lines_linked` counts DISTINCT ROWS (never
        links: one row split across two lines of the same document is still one line of
        this OI on that document), `qty_linked` sums every link's own quantity.

        404 for an unknown id or another company's header (AC-N2): `OrderInquiry` is
        company-scoped at the session's own `do_orm_execute` listener (the same scoped
        lookup `get()` uses), so a plain filter-by-id already returns nothing for
        another company's row - one query, no bespoke company check here."""
        exists = (
            self.db.query(OrderInquiry.id).filter(OrderInquiry.id == inquiry_id).first()
        )
        if exists is None:
            raise AppException(404, "This order inquiry no longer exists.", code="oi_header_not_found")
        po_rows = (
            self.db.query(
                PurchaseOrder.id.label("po_id"),
                PurchaseOrder.po_number.label("po_number"),
                Supplier.supplier_name.label("supplier_name"),
                PurchaseOrder.issue_date.label("po_date"),
                func.count(func.distinct(OrderInquiryLink.row_id)).label("lines_linked"),
                func.sum(OrderInquiryLink.qty).label("qty_linked"),
            )
            .select_from(OrderInquiryLink)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .filter(
                OrderInquiryRow.order_inquiry_id == inquiry_id,
                OrderInquiryRow.state != INQUIRY_CANCELLED,
                OrderInquiryLink.po_line_id.isnot(None),
            )
            .group_by(
                PurchaseOrder.id, PurchaseOrder.po_number, Supplier.supplier_name,
                PurchaseOrder.issue_date,
            )
            .all()
        )
        spo_rows = (
            self.db.query(
                SPOAllocation.spo_number.label("spo_number"),
                # One entry per SPO even when its lines' source POs name different
                # suppliers (each line resolves its own): every distinct name, sorted.
                func.array_to_string(
                    func.array_agg(
                        aggregate_order_by(
                            Supplier.supplier_name.distinct(), Supplier.supplier_name
                        )
                    ),
                    ", ",
                ).label("supplier_name"),
                func.count(func.distinct(OrderInquiryLink.row_id)).label("lines_linked"),
                func.sum(OrderInquiryLink.qty).label("qty_linked"),
            )
            .select_from(OrderInquiryLink)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
            # OI-SUPPLIER-FROM-PO: the source PO's supplier, the SPO's own only
            # when the CRM holds no such purchase order.
            .outerjoin(Supplier, Supplier.id == spo_supply.supplier_id_for())
            .filter(
                OrderInquiryRow.order_inquiry_id == inquiry_id,
                OrderInquiryRow.state != INQUIRY_CANCELLED,
                OrderInquiryLink.spo_allocation_id.isnot(None),
            )
            .group_by(SPOAllocation.spo_number)
            .all()
        )
        return OrderInquiryRelatedDocumentsOut(
            purchase_orders=[
                OrderInquiryRelatedPOOut(
                    po_id=r.po_id, po_number=r.po_number, supplier_name=r.supplier_name,
                    po_date=r.po_date, lines_linked=int(r.lines_linked or 0),
                    qty_linked=_qty_str(r.qty_linked),
                )
                for r in po_rows
            ],
            spos=[
                OrderInquiryRelatedSPOOut(
                    spo_number=r.spo_number, supplier_name=r.supplier_name,
                    lines_linked=int(r.lines_linked or 0), qty_linked=_qty_str(r.qty_linked),
                )
                for r in spo_rows
            ],
        )
