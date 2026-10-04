"""Project Sales API routes."""
from fastapi import APIRouter

from app.api.v1.projects import (
    allocations,
    divergences,
    forecast,
    fulfilment_planning,
    leads,
    order_inquiries,
    parties,
    planning_changes,
    po_intake,
    projects,
    quotation_documents,
    quotation_templates,
    quotations,
    sales_orders,
    samples_pos,
    schedules,
    so_line_attachments,
    status_graphs,
    stock_debt,
    tasks,
    types,
)

router = APIRouter()

# Config first: /config/types and /config/templates would otherwise be captured by
# the /{project_id} path in the projects router.
# Reports before the projects router for the same reason config is: /reports/forecast is a
# literal segment that /projects/{project_id} must not capture.
router.include_router(forecast.router, tags=["project-reports"])
router.include_router(types.router, prefix="/config", tags=["project-config"])
# Cover letter and terms templates are setup too, so they join the /config surface. Same
# reason as above for being ahead of the projects router: /config is a literal segment that
# /projects/{project_id} must not capture.
router.include_router(
    quotation_templates.router, prefix="/config", tags=["project-config"]
)
# Tasks mount at the module root because they span three shapes: nested under a
# project, the cross-project /my-tasks worklist, and template checklist admin. Before
# the projects router, so /projects/{id}/tasks is not captured by /projects/{id}.
router.include_router(tasks.router, tags=["project-tasks"])
# Quotations mount at the root for the same reason as tasks: they span three shapes
# (nested under a project, then /quotations/{id} and /quotation-versions/{id} for the
# revision history) plus their own /config surface.
router.include_router(quotations.router, tags=["project-quotations"])
router.include_router(quotation_documents.router, tags=["project-quotations"])
# Read-only project / task / lead status graphs for salespeople (never-stuck L10).
router.include_router(status_graphs.router, tags=["project-status-graphs"])
# Samples and customer POs mount at the root for the same reason: both are nested
# under a project for listing but addressed directly for editing.
router.include_router(samples_pos.router, tags=["project-samples-pos"])
# Phase 2 document intake. Root-mounted for the same reason as the three above: a PO
# version, a schedule version and an SO draft are each listed under a project but
# addressed directly by id. All three MUST precede the projects router.
router.include_router(po_intake.router, tags=["project-po-intake"])
router.include_router(schedules.router, tags=["project-delivery-schedules"])
# Divergence (P8a) BEFORE sales orders, and the order is not cosmetic:
# `/sales-orders/ingest` and `/sales-orders/{pso_id}` are the same shape to the router, so
# declaring the sales-order router first would capture `ingest` as a sales order id.
router.include_router(divergences.router, tags=["project-so-divergence"])
# Fulfilment planning (Stage 1B) hangs off `/sales-orders/{pso_id}` too, so it joins the
# routers declared ahead of the plain sales-order one for the same reason.
router.include_router(fulfilment_planning.router, tags=["project-fulfilment-planning"])
router.include_router(sales_orders.router, tags=["project-sales-orders"])
# Allocation (P9) hangs off sales order LINES and off claims between projects, so it is
# root-mounted alongside sales orders. Its paths add a segment to the sales-order ones
# (`/sales-orders/{id}/allocations`), so the order relative to that router is free.
router.include_router(allocations.router, tags=["project-allocations"])
# Order inquiry (P10) is read per project and per sales order, and acted on by row id,
# so it is root-mounted for the same reason. Before the projects router, or
# /projects/{id}/order-inquiry-rows is captured by /projects/{project_id}.
router.include_router(order_inquiries.router, tags=["project-order-inquiry"])
# Planning changes (PLAN-so-book-diff-replanning.md) are addressed by their own batch id,
# never nested under a sales order, so root-mounted for the same reason order inquiry is.
router.include_router(planning_changes.router, tags=["project-planning-changes"])
# Stock Debt (S2) is a cross-order READ addressed by product, never nested under a project,
# so it is root-mounted for the same reason planning changes is - and ahead of the projects
# router, or `/project-sales/stock-debt` is captured by `/projects/{project_id}`.
router.include_router(stock_debt.router, tags=["project-stock-debt"])
# Sales-order-line attachments (#1312) address the CORE line directly, never nested
# under a project, so it is root-mounted for the same reason stock debt is.
router.include_router(so_line_attachments.router, tags=["project-so-line-attachments"])
router.include_router(parties.router, prefix="/parties", tags=["project-parties"])
# Leads before projects for the same reason config is: /leads/{id}/qualify returns a
# PROJECT, but the route itself lives under the leads prefix.
router.include_router(leads.router, prefix="/leads", tags=["project-leads"])
router.include_router(projects.router, prefix="/projects", tags=["projects"])
