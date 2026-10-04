"""API v1 routes."""
from fastapi import APIRouter, Depends
from app.api.v1 import (
    auth,
    dev_login,
    audit,
    master_data,
    order_management,
    inventory,
    procurement,
    incoming_stock,
    marketing,
    projects,
    forms,
    complaints,
    sla,
    resources,
    user_management,
    integrations,
    test_auth,
    system,
    external,
    public,
    notifications,
    list_query,
    workflow_forms,
    lookup,
    ideation,
    activities,
    tickets,
    downloads,
    dealer_kit,
    reports,
    sales,
    scm,
)
from app.api.v1.system import (
    modules_runtime,
    pending_actions,
    rule_facts,
    companies as system_companies,
)
from app.api.v1.assistant import record_context as assistant_record_context
from app.modules.runtime.guards import (
    require_any_module_enabled,
    require_module_enabled,
    require_module_enabled_with_api_key,
)

api_router = APIRouter()

# Include all module routers
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# DEV-LOGIN-BYPASS: 404 unless every guard in app/services/dev_login.py holds.
api_router.include_router(dev_login.router, prefix="/auth", tags=["auth"])
# Auth endpoints: /login, /signup, /reset-password, /change-password, /verify-email
# App Store / module lifecycle (not behind base system router guard; uses its own permissions)
api_router.include_router(modules_runtime.router, prefix="/system/modules", tags=["system-modules"])
api_router.include_router(
    audit.router,
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_module_enabled_with_api_key("audit"))],
)
api_router.include_router(
    master_data.router,
    prefix="/master-data",
    tags=["master-data"],
    dependencies=[Depends(require_module_enabled_with_api_key("product"))],
)
api_router.include_router(
    order_management.router,
    prefix="/order-management",
    tags=["order-management"],
    dependencies=[Depends(require_module_enabled_with_api_key("order"))],
)
api_router.include_router(
    inventory.router,
    prefix="/inventory",
    tags=["inventory"],
    dependencies=[Depends(require_module_enabled_with_api_key("inventory"))],
)
api_router.include_router(
    procurement.router,
    prefix="/procurement",
    tags=["procurement"],
    dependencies=[Depends(require_module_enabled_with_api_key("procurement"))],
)
# User-facing incoming-stock surface (redacts received quantities, hides IDs/SPO details).
# Intended primary route for the AI assistant / MCP layer.
api_router.include_router(
    incoming_stock.router,
    prefix="/incoming-stock",
    tags=["incoming-stock"],
    dependencies=[Depends(require_module_enabled_with_api_key("procurement"))],
)
api_router.include_router(
    marketing.router,
    prefix="/marketing",
    tags=["marketing"],
    dependencies=[Depends(require_module_enabled_with_api_key("marketing"))],
)
api_router.include_router(
    projects.router,
    prefix="/project-sales",
    tags=["project-sales"],
    dependencies=[Depends(require_module_enabled_with_api_key("projects"))],
)
api_router.include_router(
    sales.router,
    prefix="/sales",
    tags=["sales"],
    dependencies=[Depends(require_module_enabled_with_api_key("sales"))],
)
api_router.include_router(
    forms.router,
    prefix="/forms-management",
    tags=["forms"],
    dependencies=[Depends(require_module_enabled_with_api_key("forms"))],
)
api_router.include_router(
    workflow_forms.router,
    prefix="/workflow-forms",
    tags=["workflow-forms"],
    dependencies=[Depends(require_module_enabled_with_api_key("workflow_forms"))],
)
api_router.include_router(
    complaints.router,
    prefix="/complaints-management",
    tags=["complaints"],
    dependencies=[Depends(require_module_enabled_with_api_key("complaints"))],
)
api_router.include_router(
    sla.router,
    prefix="/sla-management",
    tags=["sla"],
    # JWT or X-API-Key (same EXTERNAL_API_KEY as /external/*) for n8n / automation
    dependencies=[Depends(require_module_enabled_with_api_key("sla"))],
)
api_router.include_router(
    resources.router,
    prefix="/resource-management",
    tags=["resources"],
    dependencies=[Depends(require_module_enabled_with_api_key("resources"))],
)
api_router.include_router(
    user_management.router,
    prefix="/user-management",
    tags=["user-management"],
    dependencies=[Depends(require_module_enabled("base"))],
)
# Spec visibility policy (PLAN-spec-visibility-policy.md): contact-side admin,
# so it sits under user-management rather than inventory, but mounted at the
# TOP LEVEL rather than through `user_management.router` - that router's own
# module gate is the JWT-only `require_module_enabled`, and `GET .../effective`
# (AC-11) is an n8n preflight convenience that must accept X-API-Key, same as
# `inventory.stock-visibility`'s own `/effective`. Every route inside still
# reuses `user_management.contacts.view` / `.edit` - no new permission slug.
from app.api.v1.user_management import spec_visibility as spec_visibility_module

api_router.include_router(
    spec_visibility_module.router,
    prefix="/user-management/spec-visibility",
    tags=["spec-visibility"],
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
# Integration management (AC-AC-08). JWT only -- deliberately NOT X-API-Key:
# an integration must not be able to mint credentials for itself or enumerate
# the other integrations, or a compromise of one caller escalates to all of them.
# (The per-agent MCP tool ownership sub-routes that used to sit here were removed
# on main in PR #30; only the integration-management mount stays.)
api_router.include_router(
    integrations.admin.router,
    prefix="/integrations/manage",
    tags=["integrations"],
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
api_router.include_router(
    integrations.logs.router,
    prefix="/integrations/logs",
    tags=["integrations"],
    # JWT or X-API-Key so n8n / external tools can call logs (same key as /external/*)
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
api_router.include_router(
    integrations.respond_templates.router,
    prefix="/integrations/respond",
    tags=["integrations"],
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
# Ideas iframe embed-session mint (SSO, §5.3) - JWT logged-in user only (the
# endpoint's get_current_user dependency enforces auth); never X-API-Key/n8n.
api_router.include_router(
    integrations.ideation_embed.router,
    prefix="/integrations/ideation",
    tags=["integrations"],
)
# Ideas gateway (IDEATION-IN-CRM): CRM routes over the ss embed API, as the calling user.
api_router.include_router(
    ideation.router,
    prefix="/ideation",
    tags=["ideation"],
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
# Alias so /api/v1/integration-management/integration-logs/* works when requests hit backend directly (e.g. nginx)
api_router.include_router(
    integrations.logs.router,
    prefix="/integration-management/integration-logs",
    tags=["integrations"],
    dependencies=[Depends(require_module_enabled_with_api_key("base"))],
)
# Multi-company isolation: Companies admin + grant/membership management +
# my-context / switch (System Management → Companies). Self-gated (JWT + superadmin
# for writes); mounted alongside the base system router. Companies tables are not
# company-scoped, so the do_orm_execute filter never touches them.
api_router.include_router(system_companies.router, prefix="/system", tags=["system-companies"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
# Rule-facts catalog for the RuleBuilder (nested AND/OR condition builder in the
# Automation edit form). JWT + automation.view permission; never X-API-Key.
api_router.include_router(rule_facts.router, prefix="/rule-facts", tags=["rule-facts"])
# Deferred record actions (D7, S6) - the grace window that replaced the confirmation
# dialog. Cross-cutting by design (products, orders, users), so it is mounted at the
# root under `base` rather than inside one domain; each action enforces its OWN
# permission slug when it is parked. JWT only: an X-API-Key caller has no countdown to
# cancel in, so it should call the domain route and have the effect immediately.
api_router.include_router(
    pending_actions.router,
    prefix="/pending-actions",
    tags=["pending-actions"],
    dependencies=[Depends(require_module_enabled("base"))],
)
# Bubble record-context assembler (JWT+RBAC only; never exposed to EXTERNAL_API_KEY).
api_router.include_router(
    assistant_record_context.router,
    tags=["assistant-record-context"],
)
api_router.include_router(test_auth.router, tags=["test"])
api_router.include_router(external.router, prefix="/external", tags=["external"])
api_router.include_router(public.router, prefix="/public", tags=["public"])
api_router.include_router(
    notifications.router,
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_module_enabled("notifications"))],
)
api_router.include_router(
    downloads.router,
    prefix="/downloads",
    tags=["downloads"],
)
# Reporting foundation: one set of routes for every report, each gated on its own
# permission slug AND its own module. The procurement guard that sat here while the
# sponsorship report was the only one is per report now (the second module, `sales`,
# arrived: PLAN-retail-sales-reports-26sep 5.3 extension 1).
api_router.include_router(
    reports.router,
    prefix="/reports",
    tags=["reports"],
)
api_router.include_router(
    list_query.router,
    prefix="/list-query",
    tags=["list-query"],
)
api_router.include_router(lookup.router, prefix="/lookup", tags=["lookup"])

# Activities & Notes - generic per-entity panel (consumed by tickets et al.).
api_router.include_router(
    activities.router,
    prefix="/activities",
    tags=["activities"],
    dependencies=[Depends(require_module_enabled_with_api_key("activities"))],
)

# Tickets - Jira-style internal ticketing.
api_router.include_router(
    tickets.router,
    prefix="/tickets-management",
    tags=["tickets"],
    dependencies=[Depends(require_module_enabled_with_api_key("tickets"))],
)

# Supply Chain & Inventory Optimisation (SCM) - reorder dashboard, SO/PO surfaces.
api_router.include_router(
    scm.router,
    prefix="/scm",
    tags=["scm"],
    dependencies=[Depends(require_module_enabled_with_api_key("scm"))],
)

# AutoCount pull + review (PLAN-autocount-pull-review.md): pull a frozen products/stock
# snapshot from AutoCount instead of exporting + uploading the workbook by hand; delivery
# orders joined in PLAN-autocount-do-pull-crm-30sep.md (`order` module), goods receive notes
# in PLAN-autocount-grn-pull-crm-02oct.md (`procurement` module). No entity's module
# gates another (require_any_module_enabled) - each route enforces the permission of ITS
# OWN entity once the pull is resolved (AC-PM-2).
api_router.include_router(
    integrations.autocount_pull.router,
    prefix="/autocount/pulls",
    tags=["autocount-pull"],
    dependencies=[Depends(require_any_module_enabled("product", "inventory", "order", "procurement"))],
)

# Dealer Sales Kit - catalogue page builder, collections, brochure export.
api_router.include_router(
    dealer_kit.router,
    prefix="/dealer-kit",
    tags=["dealer-kit"],
    dependencies=[Depends(require_module_enabled_with_api_key("dealer_kit"))],
)

# Auto-discovery of self-contained modules under app/modules/<key>/.
# Additive: skips prefixes already registered above (LEGACY_REGISTERED_PREFIXES).
from app.modules.runtime.discovery import discover_module_routers  # noqa: E402
discover_module_routers(api_router)
