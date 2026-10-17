"""Delete every row the `projects` module owns, when it is uninstalled with purge (ADR-0009).

Discovered, not registered: ``app.modules.runtime.discovery.discover_module_purge_handlers``
picks this file up because it is ``app/modules/projects/purge.py`` and exposes ``purge(db)``,
and ``app.services.module_purge_service`` merges it onto the key ``projects`` additively. There
is no line to add to the hardcoded ``MODULE_PURGE_HANDLERS`` dict, which is the point: the
module carries its own uninstall story.

Three rules govern what is in here:

* **Module-owned tables only, and the model file is what says so.** All 49 live in the
  ``projects`` Postgres schema (ADR-0011), which is a second, mechanically checkable
  expression of the same fact - but ownership is still "declared in
  ``app/models/projects.py`` or ``app/models/project_so.py``", which is exactly what
  ``tests/test_projects_module_purge_invariants.py`` asserts. Nothing outside the module
  reads any of them.
* **Core rows are never touched.** The ``public.sales_orders`` the demand feed created stay,
  and so do complaints, purchase requests, customers, attachments and statuses. Seven of the
  module's bare names also exist as CORE tables (``brands``, ``purchase_orders``,
  ``purchase_order_lines``, ``sales_orders``, ``sales_order_lines``, ``quotations``,
  ``quotation_lines``); the deletes below name MODEL CLASSES, which carry their schema, so
  there is no way for one of them to land on core's copy. The two core-to-module foreign keys
  (``complaints.project_id``, ``purchase_requests.project_id``) go NULL through
  ``ON DELETE SET NULL`` when the project row dies. That is the mechanism; purge does not
  reach across the boundary to do it by hand.
* **Purge never issues ``DROP SCHEMA``.** It empties tables an operator explicitly consented
  to empty and leaves the (now empty) ``projects`` schema, and every table in it, in place.
  A schema is a namespace here, not a lifetime: signed quotations, customer POs and the sales
  orders behind them are system of record, and an uninstall is not a licence to drop the
  structure that held them.

Deletes go through the ORM model classes rather than raw table names, so a table rename moves
both sides at once. Every one of the 34 prefixed names moved in migration 354 and this file
needed no edit, which is the argument for the pattern.

``PURGE_ORDER`` is children-first over the REAL foreign key graph, not alphabetical. Most of
those FKs are ``CASCADE`` and would take the children anyway, but every table gets its own
statement so the returned counts tell an operator what was actually removed rather than
under-reporting whatever the database quietly swept up. Four ``RESTRICT`` edges make the order
load-bearing and are marked inline below; get one of them wrong and the uninstall fails
half-way through, with the rows it already deleted gone.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Type

from sqlalchemy.orm import Session

from app.models.project_so import (
    AllocationClaim,
    CustomerItemCodeMap,
    DeliverySchedule,
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    OrderChangeNotice,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRaise,
    OrderInquiryReserveEvent,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
    OrderInquiryReleasedLink,
    OrderInquiryRow,
    OrderInquirySuggestedLink,
    ProjectDeliveryPhase,
    ProjectPOAnnotation,
    ProjectPOLine,
    ProjectPOVersion,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    ProjectSODivergence,
    ProjectSODivergenceLine,
    SOAmendment,
    SODraftFinding,
    SOLineAllocation,
    SOLineSplit,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)
from app.models.projects import (
    PriceFloorRule,
    Project,
    ProjectBrand,
    ProjectCollaborator,
    ProjectLead,
    ProjectParty,
    ProjectPurchaseOrder,
    ProjectPurchaseOrderLine,
    ProjectQuotation,
    ProjectQuotationDocument,
    ProjectQuotationIssue,
    ProjectQuotationIssueScope,
    ProjectQuotationLine,
    ProjectQuotationVersion,
    ProjectSalesProfile,
    ProjectSample,
    ProjectSeries,
    ProjectSeriesBrand,
    ProjectSeriesCategory,
    ProjectSeriesProduct,
    ProjectSeriesSet,
    ProjectStakeholder,
    ProjectTakeoverRequest,
    ProjectTask,
    ProjectTemplate,
    ProjectTemplateRole,
    ProjectTemplateTask,
    ProjectType,
    QuotationSignature,
    QuotationTemplate,
)

logger = logging.getLogger(__name__)


#: Every module-owned table, children first. Read off the foreign keys declared on the models.
PURGE_ORDER: List[Type] = [
    # --- sales order side: divergences, inquiries and amendments hang off the SO -------
    ProjectSODivergenceLine,
    ProjectSODivergence,
    # order_inquiry_links.row_id CASCADEs off the row and .reserve_request_row_id
    # CASCADEs off the reserve request row (B2, security review round 2 - flipped from
    # SET NULL: a reserve link's `reserve_request_row_id` is one leg of the widened
    # `ck_order_inquiry_links_one_target` CHECK, and a SET NULL there is NOT
    # self-resolving the way an ordinary detach is - deleting `order_inquiry_reserve_
    # request_rows` first used to leave the link with none of its three targets set and
    # the CHECK failed, aborting the whole purge). So the link purges BEFORE every
    # reserve table below it, not just before the row - its own statement so the
    # operator sees the placements counted.
    OrderInquiryLink,
    # order_inquiry_suggested_links.row_id CASCADEs off the row (its other two targets,
    # po_line_id and spo_allocation_id, CASCADE onto CORE purchase_order_lines and
    # spo_allocations, outside the module and irrelevant to this order). A guess, not a
    # placement (PLAN-oi-links-autocount-truth-24sep.md, issue #1215) - purges beside its
    # sibling OrderInquiryLink and before the row it hangs off, its own statement so the
    # operator sees the suggestions counted.
    OrderInquirySuggestedLink,
    # order_inquiry_released_links.row_id CASCADEs off the row; its reserve_request_row_id
    # is SET NULL. Own statement so the operator sees the released links counted.
    OrderInquiryReleasedLink,
    # order_inquiry_reserve_events.reserve_request_row_id CASCADEs off the request row
    # (section 6c, `PLAN-oi-request-cs-reserve.md`), so it purges first, its own
    # statement so the operator sees the history counted.
    OrderInquiryReserveEvent,
    # order_inquiry_reserve_request_rows.request_id and .row_id both CASCADE, so either
    # parent would take the rows anyway; own statement so the operator sees the asks
    # counted. Rows before requests, requests before the header (order_inquiry_reserve_
    # requests.order_inquiry_id CASCADEs off it, S1, `PLAN-oi-request-cs-reserve.md`).
    OrderInquiryReserveRequestRow,
    OrderInquiryReserveRequest,
    OrderInquiryRow,
    # order_inquiry_raises.order_inquiry_id CASCADEs off the header too (S1,
    # `PLAN-oi-header-list-detail.md`) - same reason, its own statement for its own count.
    OrderInquiryRaise,
    OrderInquiry,
    SOAmendment,
    # so_amendments.ocn_id points here, so the notice outlives the amendment by one step.
    OrderChangeNotice,
    SODraftFinding,
    # so_line_allocations.claim_id points at a claim: allocations first, claims after.
    SOLineAllocation,
    AllocationClaim,
    # so_supply_decision_drafts carries no FK to SOSupplyDecision - it names the CORE sales
    # order directly (a save claims no stock and a line whose order nobody has adopted can
    # still be saved) - but a draft is what a decision was BEFORE it was confirmed, so it
    # purges first, the same order the lifecycle itself runs in.
    SOSupplyDecisionDraft,
    # order_inquiry_rows.supply_decision_id and so_line_allocations.decision_id both point
    # here, so the decision outlives the components it grouped by one step.
    SOSupplyDecision,
    # so_line_splits.so_line_id CASCADEs off the line (OI-LINE-SPLIT); its own statement
    # so the operator sees the splits counted.
    SOLineSplit,
    ProjectSalesOrderLine,
    # RESTRICT on project_sales_orders.project_id: this MUST precede Project.
    ProjectSalesOrder,
    # --- delivery schedules and the phases they populate -------------------------------
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    DeliverySchedule,
    ProjectDeliveryPhase,
    # --- the customer PO and its scanned documents -------------------------------------
    ProjectPOAnnotation,
    ProjectPOLine,
    ProjectPOVersion,
    ProjectPurchaseOrderLine,
    ProjectPurchaseOrder,
    ProjectSample,
    # --- quotations: issues reference scopes and versions, documents sit under them -----
    ProjectQuotationIssueScope,
    ProjectQuotationIssue,
    ProjectQuotationLine,
    ProjectQuotationVersion,
    ProjectQuotation,
    ProjectQuotationDocument,
    # Signatures are referenced by documents and issues, so they go once those are gone.
    QuotationSignature,
    QuotationTemplate,
    PriceFloorRule,
    # --- catalogue the quotations priced from -------------------------------------------
    ProjectSeriesProduct,
    ProjectSeriesSet,
    ProjectSeriesBrand,
    ProjectSeriesCategory,
    ProjectSeries,
    # --- everything hanging directly off a project --------------------------------------
    ProjectTask,
    ProjectTemplateTask,
    ProjectTakeoverRequest,
    ProjectCollaborator,
    # RESTRICT on project_stakeholders.role_id: this MUST precede ProjectTemplateRole.
    ProjectStakeholder,
    ProjectBrand,
    ProjectSalesProfile,
    CustomerItemCodeMap,
    # RESTRICT on projects.developer_party_id / type_id / template_id: Project MUST precede
    # ProjectParty, ProjectType and ProjectTemplate.
    Project,
    # RESTRICT on project_leads.developer_party_id: leads MUST precede ProjectParty. After
    # Project rather than before it, so a lead is never detached from a registration that is
    # about to be deleted anyway.
    ProjectLead,
    ProjectTemplateRole,
    # RESTRICT on project_templates.type_id: templates MUST precede ProjectType.
    ProjectTemplate,
    ProjectType,
    ProjectParty,
]


def _count_deleted(db: Session, model: Type) -> int:
    label = model.__table__.fullname
    n = db.query(model).delete(synchronize_session=False)
    logger.info("Purge %s: deleted %s rows", label, n)
    return n


def purge(db: Session) -> Dict[str, int]:
    """Empty every module-owned table. Returns ``{schema.table: rows_deleted}``.

    Keyed on ``__table__.fullname``, not ``__tablename__``, so the operator reads
    ``projects.sales_orders`` and cannot mistake it for core's ``sales_orders`` - which the
    purge does not touch and which is where the company's real order book lives.

    Tables that were already empty report zero rather than being omitted: an operator reading
    the uninstall result needs to see what was CONSIDERED, not only what happened to be there.
    The caller commits.
    """
    return {model.__table__.fullname: _count_deleted(db, model) for model in PURGE_ORDER}
