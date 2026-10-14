"""Fulfilment Planning and reconciliation wire shapes (Stage 1B).

Contract: `documentation/plans/scm/STAGE1B-scm-front-planning-reconciliation.md` section 3,
which the frontend was built against in Phase 1. Field names and optionality here match
`app/(protected)/project-sales/_shared/types/fulfilmentPlanning.types.ts` exactly.

Two things are load bearing:

* **Quantities are ``Decimal``**, which pydantic renders as a JSON string. A float round
  trip loses the tail of a quantity the customer signed for.
* **An exception's ``message`` carries the reason alone.** The screen prints the subject
  from ``line_no`` and ``item_code`` ("Line 2, SRT501-CP"), so a message that repeats the
  subject renders the same fact twice.

Nothing here is a per-line workflow state: the whole SO has exactly one pre-confirmation
state (AC-A03), so no line ever reads confirmed, partially confirmed or purchasing-ready.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class FulfilmentPlanningRow(BaseModel):
    """One subject on the cross-project worklist, from either arm of the union.

    The list is a UNION (PLAN-fulfilment-planning-from-autocount-so section 6): an
    outstanding project-class CORE sales order, planned or not, and a planning record
    authored here that no core-order row already carries. One row per subject.

    **Everything a not-started row cannot have is optional, and that is the point.** No
    planning record exists for it, so it has no `id`, no `provisional_ref`, no `status` and
    no counts off a mirror nobody has written; stating that absence is what lets the screen
    offer Start planning instead of Open. `project_id` is nullable for the same class of
    reason: an adopted order has no project registration and must not invent one.
    """

    model_config = ConfigDict(from_attributes=True)

    #: Which arm this row came from. ADDRESSING ONLY, never rendered.
    row_kind: Optional[str] = None
    #: The planning record's id. Absent on a not-started row: there is no record.
    id: Optional[str] = None
    #: The core `sales_orders` id, for addressing the SCM sales order. Never rendered.
    sales_order_id: Optional[str] = None
    #: AC-CONSIGN-CRM: that core order's doc_type; null on an authored record (no core order).
    so_doc_type: Optional[str] = None
    #: The AutoCount / core sales-order number. The human key of a core-arm row.
    so_number: Optional[str] = None
    #: `authored` or `adopted`. Absent when nobody has planned this order yet.
    origin: Optional[str] = None
    provisional_ref: Optional[str] = None
    autocount_doc_no: Optional[str] = None
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    #: The registered project's name when there is one, else the string the Order Inquiry
    #: sheet wrote on the core order. Never an id.
    project_label: Optional[str] = None
    customer_name: Optional[str] = None
    #: Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`). Null on an authored
    #: record, which carries no agent of its own, and on a core order the upload could not
    #: resolve one for.
    agent_code: Optional[str] = None
    agent_label: Optional[str] = None
    po_number: Optional[str] = None
    area_group: Optional[str] = None
    #: The existing sales-order status (published, amended, adopted), never a review state.
    status: Optional[str] = None
    line_count: int = 0
    lines_linked: int = 0
    exception_count: int = 0
    #: Summed over the still-owed lines. ``Decimal``, which pydantic renders as a string.
    outstanding_qty: Optional[Decimal] = None
    #: The earliest still-owed required date across the lines: the order the work is due in.
    earliest_required_date: Optional[date] = None
    review_state: str
    updated_at: Optional[datetime] = None
    #: The newest PENDING planning-change batch on this order's core sales order, or null
    #: (`PLAN-scm-board-picks-up-pending-change.md`, AC-B7). Same rule, same id the SCM
    #: Sales Orders list and the fulfilment board name off
    #: `planning_change_service.pending_batch_id_by_sales_order` - the `Changed` pill here
    #: links to the board with the same batch already loaded.
    planning_change_batch_id: Optional[str] = None


class AdoptSalesOrderBody(BaseModel):
    """What Start planning asks for, and the whole of it (AC-FP07)."""

    sales_order_id: str


class AdoptSalesOrderResult(BaseModel):
    """What Start planning answers with.

    ``already_adopted`` is what makes the button safe to press twice: adoption is
    idempotent, so a retry or a second CS gets the record that exists rather than a second
    one, and the screen can say so instead of pretending it just created it.
    """

    project_sales_order_id: str
    so_number: str
    review_state: Optional[str] = None
    already_adopted: bool = False


class ReconciliationHeader(BaseModel):
    outcome: str
    #: The AutoCount number the core order carries. Never its id.
    core_so_number: Optional[str] = None
    reason: str


class ReconciliationLine(BaseModel):
    id: str
    line_no: int
    product_code: Optional[str] = None
    description: Optional[str] = None
    qty: Decimal
    uom: Optional[str] = None
    delivery_date: Optional[date] = None
    stock_location: Optional[str] = None
    #: `linked`, `missing`, `ambiguous` or `duplicate` (the core line this line would take
    #: is already held by another Project SO).
    link: str
    #: How many core lines could still be this one: 1 on a linked line, the number of core
    #: candidates at that product and date on an ambiguous one, 0 otherwise.
    candidate_count: int = 0
    reason: str


class ReconciliationException(BaseModel):
    #: Absent on a surplus core line, which no Project line claims.
    line_no: Optional[int] = None
    item_code: Optional[str] = None
    #: `header`, `missing`, `ambiguous`, `duplicate` or `surplus`.
    kind: str
    message: str


class ReconciliationSummary(BaseModel):
    project_sales_order_id: str
    provisional_ref: str
    autocount_doc_no: Optional[str] = None
    #: None on an ADOPTED record: it came out of the AutoCount book and has no project
    #: registration (plan section 4). Always set on an authored one.
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    customer_name: Optional[str] = None
    po_number: Optional[str] = None
    area_group: Optional[str] = None
    status: str
    #: None on an order that is not published or amended: it is reconciled against
    #: nothing, so it carries no state rather than one it has not earned (AC-A03).
    review_state: Optional[str] = None
    header: ReconciliationHeader
    lines: List[ReconciliationLine] = []
    exceptions: List[ReconciliationException] = []
    lines_total: int = 0
    lines_linked: int = 0
