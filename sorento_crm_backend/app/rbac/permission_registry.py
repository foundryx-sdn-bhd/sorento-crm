"""
Central permission registry: module.resource.action slugs and descriptions.
Used to seed user_permissions and to enforce RBAC. When adding a new menu or module,
add corresponding CRUD (view, add, edit, delete) and optionally import, export, upload, bulk_*.
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.models.user import UserPermission


def _crud(module: str, resource: str, name_prefix: str) -> list[dict]:
    """Generate view, add, edit, delete for a resource."""
    return [
        {"slug": f"{module}.{resource}.view", "name": f"View {name_prefix}", "description": f"Permission to view {name_prefix}."},
        {"slug": f"{module}.{resource}.add", "name": f"Add {name_prefix}", "description": f"Permission to add {name_prefix}."},
        {"slug": f"{module}.{resource}.edit", "name": f"Edit {name_prefix}", "description": f"Permission to edit {name_prefix}."},
        {"slug": f"{module}.{resource}.delete", "name": f"Delete {name_prefix}", "description": f"Permission to delete {name_prefix}."},
    ]


def _with_import_export(module: str, resource: str, name_prefix: str) -> list[dict]:
    """CRUD + import + export."""
    out = _crud(module, resource, name_prefix)
    out.append({"slug": f"{module}.{resource}.import", "name": f"Import {name_prefix}", "description": f"Permission to import {name_prefix}."})
    out.append({"slug": f"{module}.{resource}.export", "name": f"Export {name_prefix}", "description": f"Permission to export {name_prefix}."})
    return out


# Flat list of {slug, name, description}. Order preserved for seeding.
PERMISSION_REGISTRY: list[dict] = []

# User Management
PERMISSION_REGISTRY.extend(_crud("user_management", "users", "Users"))
PERMISSION_REGISTRY.extend(_crud("user_management", "roles", "Roles"))
PERMISSION_REGISTRY.extend(_crud("user_management", "permissions", "Permissions"))
PERMISSION_REGISTRY.extend(_crud("user_management", "access_agents", "Access Agents"))
PERMISSION_REGISTRY.extend(_crud("user_management", "teams", "Teams"))
PERMISSION_REGISTRY.extend([
    {"slug": "user_management.settings.view", "name": "View Settings", "description": "Permission to view system settings."},
    {"slug": "user_management.settings.edit", "name": "Edit Settings", "description": "Permission to edit system settings."},
    {"slug": "user_management.logs.view", "name": "View Logs", "description": "Permission to view system logs."},
    {"slug": "user_management.account.view", "name": "View Account", "description": "Permission to view own account."},
])
PERMISSION_REGISTRY.append({
    "slug": "user_management.contacts.view",
    "name": "View Contacts",
    "description": "Permission to view respond contacts and their routing, segments and access grants.",
})
PERMISSION_REGISTRY.append({
    "slug": "user_management.contacts.portal_link",
    "name": "Get contact portal link",
    "description": "Generate or send a user-submission portal link for a respond contact.",
})
# Contact editing had no slug at all - every contact write is `get_current_user`
# only, so today any authenticated user may change one. This names that authority
# so the media gate can be enforced against something, and migration 357 grants it
# to every existing role, which reproduces today's reach exactly rather than
# silently narrowing it. Revoking it per role is now possible; it was not before.
PERMISSION_REGISTRY.append({
    "slug": "user_management.contacts.edit",
    "name": "Edit Contacts",
    "description": "Edit a respond contact, including its chatbot media access and monthly limits.",
})
# Onboarding intake / review / provisioning. `.approve` is deliberately its own
# slug rather than riding `.edit`: approving is what creates real users with real
# access, so who reviews and who signs off can be different people (the
# Edition-approval convention).
PERMISSION_REGISTRY.extend(_crud("user_management", "onboarding", "Onboarding Requests"))
PERMISSION_REGISTRY.append({
    "slug": "user_management.onboarding.approve",
    "name": "Approve Onboarding Requests",
    "description": (
        "Approve a reviewed onboarding request, which queues provisioning for every "
        "approved person."
    ),
})
PERMISSION_REGISTRY.append({
    "slug": "user_management.reference_data.view",
    "name": "View Reference Data",
    "description": "Opens the Market Segments admin screen. The catalogs themselves (contact access types, market segments) are readable by every signed-in user since 1 Oct 2026.",
})
# `.view` reads the shared catalogs; `.manage` is the narrower authority to WRITE
# them (market-segment create/update/delete). A view grant permitting a hard
# delete was wrong in principle even though the route it replaced had no slug
# at all - see `market_segment.delete` in record_actions.py.
PERMISSION_REGISTRY.append({
    "slug": "user_management.reference_data.manage",
    "name": "Manage Reference Data",
    "description": "Permission to create, update and delete shared reference catalogs (contact access types, market segments).",
})

# Delivery Order Management
PERMISSION_REGISTRY.extend(_crud("order_management", "orders", "Delivery Orders"))
# AutoCount branch table (#1354 S2): the debtors' delivery branches, fed by the ingest only.
PERMISSION_REGISTRY.extend(_crud("order_management", "branches", "Customer Branches"))
PERMISSION_REGISTRY.append({"slug": "order_management.orders.import", "name": "Import Delivery Orders", "description": "Permission to bulk import delivery orders."})
# PLAN-autocount-do-pull-crm-30sep.md: pull a frozen delivery-orders snapshot from AutoCount
# (via FoundryX) and review/confirm it through the DO ingest, instead of uploading the DO
# lines sheet by hand.
PERMISSION_REGISTRY.append({"slug": "order_management.orders.autocount_pull", "name": "Pull Delivery Orders from AutoCount", "description": "Permission to pull a delivery orders snapshot from AutoCount and review/confirm it."})
PERMISSION_REGISTRY.append({"slug": "order_management.orders.export", "name": "Export Delivery Orders", "description": "Permission to export delivery orders with dynamic fields."})
PERMISSION_REGISTRY.append({"slug": "order_management.orders.bulk_delete", "name": "Bulk Delete Delivery Orders", "description": "Permission to bulk delete delivery orders."})
PERMISSION_REGISTRY.extend(_crud("order_management", "order_statuses", "Delivery Order Statuses"))
PERMISSION_REGISTRY.extend(_crud("order_management", "customers", "Customers"))
PERMISSION_REGISTRY.append({
    "slug": "order_management.customers.import",
    "name": "Import Customers",
    "description": (
        "Upload a debtor listing to create and update customers for the active company."
    ),
})

# Complaint Management
PERMISSION_REGISTRY.extend(_crud("complaint_management", "complaints", "Complaints"))
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.approve",
    "name": "Approve Complaints",
    "description": "Permission to approve a complaint after technical team response and notify the contact via Respond.io.",
})
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.reject",
    "name": "Reject Complaints",
    "description": "Permission to reject a complaint after technical team response and notify the contact via Respond.io.",
})
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.settle_on_site",
    "name": "Settle Complaints On Site",
    "description": "Close a complaint as settled on site (status='settled_on_site') when the technician fixed the issue during the visit: resolves the technical stage WITHOUT spawning customer service, so no replacement is arranged. Separate from Approve so it can be granted/hidden independently.",
})
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.resolve",
    "name": "Process Complaints (CS)",
    "description": "Permission for the customer-service team to mark an approved complaint as processed by CS (closes the customer-service SLA stage).",
})
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.close",
    "name": "Close Complaints",
    "description": "Permission to close an approved complaint that can't be resolved (status='closed'; closes the customer-service SLA stage). Separate from CS-processed so it can be granted/hidden independently.",
})
# Form void (per-form slug; mirrors the .process/.close precedent - a dedicated,
# irreversible terminal "void with reason" action, granted/hidden independently).
PERMISSION_REGISTRY.append({
    "slug": "complaint_management.complaints.void",
    "name": "Void Complaints",
    "description": "Void a complaint (irreversible; sets status='voided' with a required reason, stops the SLA by config, notifies assignee/handler/salesperson).",
})

# SLA Management
PERMISSION_REGISTRY.extend(_crud("sla_management", "sla_policies", "SLA Policies"))
PERMISSION_REGISTRY.extend(_crud("sla_management", "conversation_sla_tracking", "Conversation SLA Tracking"))
PERMISSION_REGISTRY.append(
    {
        "slug": "sla_management.conversation_sla_tracking.test_override",
        "name": "Conversation SLA test overrides",
        "description": "Override assignee and SLA timestamps on a tracking record for testing (non-production use).",
    }
)
# Per-action gates for the My Pending / My Team task widget buttons. Each one is
# granted independently so a role can, e.g., resolve but not escalate. Enforced on
# both the FE (show/hide) and the matching routes (require_permission).
PERMISSION_REGISTRY.extend([
    {"slug": "sla_management.conversation_sla_tracking.extend", "name": "Extend SLA deadline", "description": "Extend the resolution deadline on a conversation/form SLA task (assignee action)."},
    {"slug": "sla_management.conversation_sla_tracking.reassign", "name": "Reassign SLA task", "description": "Reassign a conversation SLA task to another user within the actor's visible scope."},
    {"slug": "sla_management.conversation_sla_tracking.resolve", "name": "Resolve SLA task", "description": "Mark a conversation SLA task as resolved (stops the clock, closes the conversation in Respond)."},
    {"slug": "sla_management.conversation_sla_tracking.escalate", "name": "Escalate SLA task", "description": "Manually escalate a conversation SLA task to the next tier with a reason."},
    {"slug": "sla_management.conversation_sla_tracking.takeover", "name": "Takeover SLA task", "description": "Take over a teammate's conversation SLA task (and cancel/reject pending takeovers)."},
])
PERMISSION_REGISTRY.extend(_crud("sla_management", "escalation_logs", "SLA Event Logs"))
# Conversations inbox (UAC AC-N2). READ access to a contact thread is a
# PERMISSION, deliberately not ticket assignment: a reassigned-away previous
# assignee, a mentioned colleague and a manager all have to be able to read.
# `.reply` is the separate act gate for sending from the inbox - the ticket
# drawer's own send keeps its assignee-or-manager rule and needs neither slug.
PERMISSION_REGISTRY.extend([
    {"slug": "sla_management.conversations.view", "name": "View Conversations", "description": "Read any contact's conversation thread, its notes and its media from the Conversations inbox (read access is a permission, not ticket assignment)."},
    {"slug": "sla_management.conversations.reply", "name": "Reply in Conversations", "description": "Send a WhatsApp reply to a contact from the Conversations inbox. Stamped onto the sender's own open ticket for that contact when they hold exactly one."},
])
# Composer snippets (UAC AC-L4). `.view` is what the ticket composer's "/" picker
# reads, so it is granted to everyone who works tickets (migration 329 copies the
# grants from `sla_management.conversation_sla_tracking.view`); add/edit/delete
# are the admin CRUD page.
PERMISSION_REGISTRY.extend(_crud("sla_management", "message_snippets", "Message Snippets"))
PERMISSION_REGISTRY.extend([
    {"slug": "sla_management.form_sla_config.view", "name": "View Form SLA Configurations", "description": "View per-form SLA stage configurations (start / respond / resolve trigger transitions, agent + chain)."},
    {"slug": "sla_management.form_sla_config.manage", "name": "Manage Form SLA Configurations", "description": "Create, update, delete per-form SLA stage configurations."},
    {"slug": "sla_management.form_sla.undo_action", "name": "Undo Form SLA Action", "description": "Reverse a form action after its grace window has closed (voids the stage it opened and reopens the previous one). The in-grace undo needs no permission - it belongs to whoever started the action."},
])

# Coverage (SLA task coverage / delegation). Self-service coverage ("I cover for X")
# is ungated (scope-B membership is the grant). This slug gates a HoD assigning
# coverage ON BEHALF of team members (assign A to cover B). superadmin/admin bypass.
PERMISSION_REGISTRY.append({
    "slug": "notifications.coverage.manage_team",
    "name": "Manage team coverage",
    "description": "Assign or revoke SLA task coverage on behalf of team members (HoD): pick the coverer and the covered user within the manager's visible team scope.",
})

# Master Data (Products)
PERMISSION_REGISTRY.extend(_crud("master_data", "products", "Products"))
PERMISSION_REGISTRY.append({"slug": "master_data.products.import", "name": "Import Products", "description": "Permission to bulk import products."})
PERMISSION_REGISTRY.append({"slug": "master_data.products.export", "name": "Export Products", "description": "Permission to export products with dynamic fields."})
PERMISSION_REGISTRY.append({"slug": "master_data.products.bulk_delete", "name": "Bulk Delete Products", "description": "Permission to bulk delete products."})
# PLAN-autocount-pull-review.md: pull a frozen products snapshot from AutoCount (via
# FoundryX) and review/confirm it, instead of exporting + uploading the workbook by hand.
PERMISSION_REGISTRY.append({"slug": "master_data.products.autocount_pull", "name": "Pull Products from AutoCount", "description": "Permission to pull a products snapshot from AutoCount and review/confirm it."})
# The spec-search vocabulary. Editing it silently reshapes every future product search
# for every customer, so it gets its own permission rather than riding on products.edit.
PERMISSION_REGISTRY.extend(_crud("master_data", "spec_registry", "Spec Registry"))
PERMISSION_REGISTRY.extend(_crud("master_data", "product_attachments", "Product Attachments"))
PERMISSION_REGISTRY.extend(_crud("master_data", "product_sets", "Product Sets"))
PERMISSION_REGISTRY.extend(_crud("master_data", "product_categories", "Product Categories"))
# Chatbot stock ask v2 S1 (PLAN-chatbot-stock-ask-v2-24sep.md, R2): gates editing the
# X / Y chatbot answer limits on a category or product. Only .view (see the two values)
# and .edit (change them) are read by code; .add / .delete are the _crud pattern's
# siblings and gate nothing (there is no row of its own to add or delete).
PERMISSION_REGISTRY.extend(_crud("master_data", "chatbot_stock_limits", "Chatbot Stock Limits"))
PERMISSION_REGISTRY.extend(_crud("master_data", "brands", "Brands"))
PERMISSION_REGISTRY.extend(_crud("master_data", "lookup_sets", "Lookup Sets"))
PERMISSION_REGISTRY.extend(_crud("master_data", "units_of_measure", "Units of Measure"))
PERMISSION_REGISTRY.extend(_crud("master_data", "countries", "Countries"))
PERMISSION_REGISTRY.extend(_with_import_export("master_data", "certificates", "Certificates"))
# The salesperson master. `.edit` gates the annotation (who a code belongs to, and what
# its orders count as); there is no add/delete surface, but the four slugs ship together
# so the slug set matches the AutoCount branch's mirror pages exactly.
PERMISSION_REGISTRY.extend(_crud("master_data", "sales_agents", "Sales Agents"))
PERMISSION_REGISTRY.extend(_crud("master_data", "complaint_root_causes", "Complaint Root Causes"))
PERMISSION_REGISTRY.extend(_crud("master_data", "complaint_resolutions", "Complaint Resolutions"))

# Procurement
PERMISSION_REGISTRY.extend(_crud("procurement", "suppliers", "Suppliers"))
PERMISSION_REGISTRY.append({"slug": "procurement.suppliers.export", "name": "Export Suppliers", "description": "Permission to export suppliers with dynamic fields."})
PERMISSION_REGISTRY.extend(_crud("procurement", "product_suppliers", "Product-Suppliers"))
# Cost price from the supplier (#1288, Lane A, plan section 10). Verify is Sorento-staff
# only - never an integration role, never reachable from a public route - and a migration
# sweep grants all four to every "purchasing role" (whoever holds `scm.proforma_invoice.
# upload` today), plus admin/superadmin by name.
PERMISSION_REGISTRY.append({"slug": "procurement.cost_price_changes.upload", "name": "Upload Cost Price Changes", "description": "Upload a supplier price list, map/skip its codes, discard a draft, and apply it while verification is off."})
PERMISSION_REGISTRY.append({"slug": "procurement.cost_price_changes.view", "name": "View Cost Price Changes", "description": "View cost-price change sets, their lines, history and source file."})
PERMISSION_REGISTRY.append({"slug": "procurement.cost_price_changes.verify", "name": "Verify Cost Price Changes", "description": "Decide lines, return or apply a cost-price change set pending verification."})
PERMISSION_REGISTRY.append({"slug": "procurement.suppliers.price_link", "name": "Share Supplier Price Page", "description": "Issue, view and revoke a supplier's own price-page link."})
PERMISSION_REGISTRY.extend(_crud("procurement", "packing_lists", "Packing Lists"))
PERMISSION_REGISTRY.append({"slug": "procurement.packing_lists.import_container_status", "name": "Import Container Status", "description": "Permission to import the Container Status workbook onto packing lists."})
PERMISSION_REGISTRY.append({"slug": "procurement.packing_lists.view_clearance", "name": "View Container Clearance Dates", "description": "See ETA delay, CIDB inspection/approval and gatepass dates. Without it these keys are absent from API responses, not null."})
PERMISSION_REGISTRY.extend(_crud("procurement", "spo_allocations", "SPO Allocations"))
PERMISSION_REGISTRY.append({"slug": "procurement.spo_allocations.import", "name": "Import SPO Allocations", "description": "Permission to import SPO allocations."})
PERMISSION_REGISTRY.extend(_crud("procurement", "grn", "GRN"))
PERMISSION_REGISTRY.append({"slug": "procurement.grn.import", "name": "Import GRN", "description": "Permission to import GRN."})
PERMISSION_REGISTRY.append({"slug": "procurement.grn.autocount_pull", "name": "Pull Goods Receive Notes from AutoCount", "description": "Permission to pull a goods receive notes snapshot from AutoCount and review/confirm it."})
PERMISSION_REGISTRY.extend(_crud("procurement", "picking_lines", "Picking Lines"))
PERMISSION_REGISTRY.extend(_crud("procurement", "stock_inquiries", "Stock Inquiries"))
PERMISSION_REGISTRY.extend([
    {"slug": "procurement.stock_inquiries.submit_for_project_sales", "name": "Submit stock inquiry for project sales", "description": "Move stock inquiry to pending project sales review."},
    {"slug": "procurement.stock_inquiries.project_sales_approve", "name": "Project sales approve stock inquiry", "description": "Approve stock inquiry and send to purchasing."},
    {"slug": "procurement.stock_inquiries.project_sales_reject", "name": "Project sales reject stock inquiry", "description": "Reject stock inquiry (project sales)."},
    {"slug": "procurement.stock_inquiries.purchasing_approve", "name": "Purchasing respond to stock inquiry", "description": "Update & Reply is used to respond; this permission gates access to that action."},
    {"slug": "procurement.stock_inquiries.purchasing_reject", "name": "Purchasing reject stock inquiry", "description": "Reject stock inquiry (purchasing)."},
    {"slug": "procurement.stock_inquiries.reopen", "name": "Reopen rejected stock inquiry", "description": "Reopen a rejected stock inquiry back to its previous state (pending project sales or pending purchasing)."},
    {"slug": "procurement.stock_inquiries.void", "name": "Void Stock Inquiries", "description": "Void a stock inquiry (irreversible; sets status='voided' with a required reason, stops the SLA by config, notifies assignee/handler/salesperson)."},
])
PERMISSION_REGISTRY.extend(_crud("procurement", "purchase_requests", "Purchase Requests"))
PERMISSION_REGISTRY.extend(_crud("procurement", "sponsorship_forms", "Sponsorship Forms"))
PERMISSION_REGISTRY.extend([
    # TRIAGE (before a decision exists): move a submitted request to pending approval,
    # or reject it outright. Deliberately separate from `.approve` so a sales admin can
    # triage without gaining the approver's decision on requests already pending.
    {"slug": "procurement.purchase_requests.send_for_approval", "name": "Send purchase request / sponsorship form for approval", "description": "Triage a SUBMITTED request: change it to pending approval, or reject it before approval (mandatory reason). Does NOT grant the in-system Approve/Reject decision once a request is pending - that is procurement.purchase_requests.approve."},
    # DECISION (once pending approval): the approver's in-system Approve / Reject, which
    # is identical in effect to the emailed approval link.
    {"slug": "procurement.purchase_requests.approve", "name": "Approve / reject purchase request / sponsorship form", "description": "Approver decision on a request that is PENDING APPROVAL: the in-system Approve / Reject buttons, identical in effect to the emailed approval link (same status transition, notifications, form-SLA event and approval automation)."},
    {"slug": "procurement.purchase_requests.process", "name": "Process purchase request / sponsorship form (CS)", "description": "Customer-service action: mark an approved purchase request or sponsorship form as processed by CS (status='processed_by_cs'; closes the customer-service SLA stage)."},
    {"slug": "procurement.purchase_requests.close", "name": "Close purchase request / sponsorship form (CS)", "description": "Customer-service action: close an approved purchase request or sponsorship form that can't be fulfilled (status='closed'; closes the customer-service SLA stage). Separate from Process so it can be granted independently."},
    # PR + SF share the router AND the detail component, so they share one void slug
    # (repo convention: procurement.purchase_requests.*). Grant/hidden together.
    {"slug": "procurement.purchase_requests.void", "name": "Void Purchase Requests / Sponsorship Forms", "description": "Void a purchase request or sponsorship form (irreversible; sets status='voided' with a required reason, stops the SLA by config, notifies assignee/handler/salesperson)."},
])

# Inventory
PERMISSION_REGISTRY.extend(_with_import_export("inventory", "warehouses", "Warehouses"))
PERMISSION_REGISTRY.extend(_crud("inventory", "storage_zones", "Storage Zones"))
PERMISSION_REGISTRY.extend(_with_import_export("inventory", "stock", "Stock"))
# PLAN-autocount-pull-review.md: pull a frozen stock-balance snapshot from AutoCount (via
# FoundryX) and review/confirm it, instead of exporting + uploading the Stock List by hand.
PERMISSION_REGISTRY.append({"slug": "inventory.stock.autocount_pull", "name": "Pull Stock from AutoCount", "description": "Permission to pull a stock balance snapshot from AutoCount and review/confirm it."})
PERMISSION_REGISTRY.extend(_crud("inventory", "stock_batches", "Stock Batches"))
PERMISSION_REGISTRY.extend(_crud("inventory", "stock_ledger", "Stock Ledger"))
PERMISSION_REGISTRY.extend(_crud("inventory", "stock_transfers", "Stock Transfers"))

# Marketing
PERMISSION_REGISTRY.extend(_crud("marketing", "promotions", "Promotions"))
PERMISSION_REGISTRY.extend(_crud("marketing", "promotion_attachments", "Promotion Attachments"))
PERMISSION_REGISTRY.extend(_crud("marketing", "promotion_products", "Promotion Products"))
PERMISSION_REGISTRY.extend(_crud("marketing", "campaigns", "Campaigns"))
# Seeded in production by migration 362 but never added here, so a FRESH install (and
# CI, which builds its permissions from this list rather than from migration seeds) had
# four slugs its routes enforce and no role could ever hold. Found by
# `tests/test_record_actions_s6b.py`, which asks of every deferred record action that the
# grant it names is one a role can actually be given.
PERMISSION_REGISTRY.extend(_crud("marketing", "promotion_types", "Promotion Types"))

# Forms
PERMISSION_REGISTRY.extend(_crud("forms", "forms", "Forms"))

# Workflow forms (standalone module: builder + submissions / approvals)
PERMISSION_REGISTRY.extend(_crud("workflow_forms", "definitions", "Workflow form definitions"))
PERMISSION_REGISTRY.append(
    {
        "slug": "workflow_forms.definitions.export",
        "name": "Export workflow form definitions",
        "description": "Permission to export workflow form definitions (dynamic list export).",
    }
)
PERMISSION_REGISTRY.extend(_crud("workflow_forms", "submissions", "Workflow form submissions"))
PERMISSION_REGISTRY.append(
    {
        "slug": "workflow_forms.submissions.export",
        "name": "Export workflow form submissions",
        "description": "Permission to export workflow form submissions (dynamic list export).",
    }
)
PERMISSION_REGISTRY.append(
    {
        "slug": "workflow_forms.submissions.transition",
        "name": "Transition workflow submissions",
        "description": "Move a workflow submission to another state (approve, reject, submit, etc.).",
    }
)

# Resource Management (Attachments)
PERMISSION_REGISTRY.extend(_crud("resource", "attachments", "Attachments"))
PERMISSION_REGISTRY.append({"slug": "resource.attachments.upload", "name": "Upload Attachments", "description": "Permission to upload attachments."})
PERMISSION_REGISTRY.append({"slug": "resource.attachments.bulk_import", "name": "Bulk Import Attachments", "description": "Permission to bulk import attachments."})
PERMISSION_REGISTRY.append({"slug": "resource.attachments.bulk_delete", "name": "Bulk Delete Attachments", "description": "Permission to bulk delete attachments."})
PERMISSION_REGISTRY.extend(_crud("resource", "attachment_directories", "Attachment Directories"))
PERMISSION_REGISTRY.extend(_crud("resource", "attachment_types", "Attachment Types"))

# Integration
PERMISSION_REGISTRY.extend(_crud("integration", "integration_logs", "Integration Logs"))
PERMISSION_REGISTRY.extend([
    {"slug": "integration.respond_templates.view", "name": "View WhatsApp Templates", "description": "View synced Respond.io WhatsApp message templates and auto-send defaults."},
    {"slug": "integration.respond_templates.edit", "name": "Edit WhatsApp Template Defaults", "description": "Set or clear the default template + param mapping per auto-send use case."},
    {"slug": "integration.respond_templates.sync", "name": "Sync WhatsApp Templates", "description": "Trigger a Respond.io channel + template sync."},
])
# Managing integration records and their API keys. Separate from
# integration_logs.*: reading what an integration did and being able to mint a
# credential for it are very different levels of trust.
PERMISSION_REGISTRY.extend(_crud("integration", "integrations", "Integrations"))
PERMISSION_REGISTRY.extend([
    {"slug": "integration.integrations.manage_keys", "name": "Manage Integration API Keys", "description": "Issue, rotate and revoke API keys for an integration. Grants the ability to mint a working credential."},
])
# Capabilities exposed only over /api/v1/external, for integration callers
# (n8n, the MCP server, the AutoCount ESB). These have no human-facing screen,
# so no slug existed until AC-AC-05 required every external endpoint to enforce
# one. Granted to existing roles by migration 298 -- a permission with no grant
# path silently 403s the feature it was meant to protect.
PERMISSION_REGISTRY.extend([
    {"slug": "integration.storage.presign", "name": "Presign storage URLs", "description": "Generate presigned upload/download URLs and view links for stored files."},
    {"slug": "integration.assignment.resolve", "name": "Resolve next assignee", "description": "Ask Sorento which team member should handle a conversation next."},
    {"slug": "integration.conversation_context.edit", "name": "Read and write conversation context", "description": "Read or update conversation variables and assistant memory frames."},
    {"slug": "integration.contacts.sync", "name": "Sync contacts", "description": "Create or update Respond.io contact records from an external system."},
    {"slug": "integration.semantic_search.use", "name": "Use semantic search", "description": "Run embedding and tool retrieval searches."},
    {"slug": "integration.ideation.submit", "name": "Submit ideation turns", "description": "Post ideation capture turns from an external channel."},
    # Chatbot media (PLAN-chatbot-media-endpoint). Its own slug because this one
    # spends extraction budget: an integration allowed to sync contacts is not
    # automatically allowed to charge a photo read to a dealer's allowance.
    # Granted to already-provisioned roles by migration 357.
    {"slug": "integration.chatbot_media.process", "name": "Process chatbot media", "description": "Submit an inbound WhatsApp photo or voice note for gating, metering and extraction."},
    # AutoCount ingest contract version (D8, PLAN-autocount-document-ingest-v2). Granted
    # to every role holding `scm.sales_orders.edit` by migration 472.
    {"slug": "integration.contract.read", "name": "Read ingest contract", "description": "Read the AutoCount ESB ingest contract version and supported entity list."},
    {"slug": "integration.chat_turn.submit", "name": "Submit chatbot turns", "description": "Post one inbound WhatsApp turn to the chatbot engine and receive the reply and the actions to execute."},
])

# System
PERMISSION_REGISTRY.extend(_crud("system", "import_jobs", "Import Jobs"))
PERMISSION_REGISTRY.extend(_crud("system", "import_logs", "Import Logs"))
PERMISSION_REGISTRY.extend(_crud("system", "work_calendar", "Work Calendar"))
PERMISSION_REGISTRY.append({
    "slug": "system.outgoing_mails.view",
    "name": "View Outgoing Mails",
    "description": "Permission to view outgoing email log and delivery status.",
})
PERMISSION_REGISTRY.extend([
    {"slug": "system.email_outbox.view", "name": "View Email Outbox", "description": "View pending and historical outbox rows for the email guardrail."},
    {"slug": "system.email_outbox.manage", "name": "Manage Email Outbox", "description": "Retry, cancel, and otherwise manage outbox rows."},
    {"slug": "system.respond_outbox.view", "name": "View Respond Outbox", "description": "View outgoing Respond.io / WhatsApp messages and templates (read-only over integration logs)."},
    # Chat history holds raw customer message content - PII. Gated separately from the
    # outbox view, and export is its own slug because a CSV leaves the system entirely.
    {"slug": "system.chat_history.view", "name": "View Chat History", "description": "View stored WhatsApp/chat messages and round-trip latency. Message content is customer PII."},
    {"slug": "system.chat_history.export", "name": "Export Chat History", "description": "Export chat messages to CSV via My Downloads."},
    {"slug": "system.chat_history.manage", "name": "Manage chatbot turns", "description": "Retry a failed chatbot turn from the turn trace, which re-posts the customer's original message at the chatbot ingress."},
    # Chatbot turn re-architecture (AC-1561). Separate from `chat_history.view`, which
    # READS one customer's conversation: this one edits the policy every conversation
    # is then answered by, so an operator who may read a turn is not thereby allowed to
    # change what the next thousand turns do.
    {"slug": "system.chatbot_config.manage", "name": "Manage Chatbot Configuration", "description": "Create, edit and delete chatbot domains and entity kinds - the policy the turn engine routes and narrows on."},
    {"slug": "system.email_event_configs.view", "name": "View Email Event Configs", "description": "View per-event email kill switches and rate overrides."},
    {"slug": "system.email_event_configs.manage", "name": "Manage Email Event Configs", "description": "Toggle per-event email kill switches and adjust rate overrides."},
])
PERMISSION_REGISTRY.extend([
    {"slug": "system.numbering_rules.view", "name": "View Running Numbers", "description": "Permission to view document numbering rules."},
    {"slug": "system.numbering_rules.edit", "name": "Edit Running Numbers", "description": "Permission to edit document numbering rules."},
])
PERMISSION_REGISTRY.extend([
    {"slug": "system.import_field_aliases.view", "name": "View Import Column Mappings", "description": "Permission to view which header spellings resolve to which import field, per document type."},
    {"slug": "system.import_field_aliases.edit", "name": "Edit Import Column Mappings", "description": "Permission to add or remove a header-to-field mapping for a document type's importer."},
])
PERMISSION_REGISTRY.append({
    "slug": "system.modules.manage",
    "name": "Manage App Store Modules",
    "description": "Install, enable, and disable application modules for the tenant.",
})
PERMISSION_REGISTRY.extend(_crud("system", "respond_workspaces", "Respond.io Workspaces"))
PERMISSION_REGISTRY.append({
    "slug": "system.respond_workspaces.set_default",
    "name": "Set Default Respond.io Workspace",
    "description": "Mark a Respond.io workspace as the tenant default for new contact syncs.",
})
PERMISSION_REGISTRY.extend(
    [
        {
            "slug": "system.ai_assistant_chat.use",
            "name": "Use AI Assistant Chat",
            "description": "Permission to use AI assistant chat in the application.",
        },
        {
            "slug": "system.ai_assistant_settings.view",
            "name": "View AI Assistant Settings",
            "description": "Permission to view AI assistant settings.",
        },
        {
            "slug": "system.ai_assistant_settings.edit",
            "name": "Edit AI Assistant Settings",
            "description": "Permission to edit AI assistant settings.",
        },
    ]
)

# Menu / Quick Access
PERMISSION_REGISTRY.append({
    "slug": "menu.quick_access.pin",
    "name": "Pin Quick Access",
    "description": "Permission to pin menu items or folders to Quick Access.",
})
PERMISSION_REGISTRY.append({
    "slug": "menu.quick_access.unpin",
    "name": "Unpin Quick Access",
    "description": "Permission to remove items from Quick Access.",
})

# Dashboard (optional)
PERMISSION_REGISTRY.append({
    "slug": "dashboard.view",
    "name": "View Dashboard",
    "description": "Permission to access and view the dashboard.",
})



# Tickets - Jira-style internal ticketing.
# Activities/notes for a ticket reuse `tickets.tickets.view` (anyone who can see
# the ticket can read/post activities). `view_all` unlocks the full pool;
# `assign` gates the assignee picker; `export` gates list-query CSV export.
PERMISSION_REGISTRY.extend(_crud("tickets", "tickets", "Tickets"))
PERMISSION_REGISTRY.append({
    "slug": "tickets.tickets.view_all",
    "name": "View all tickets",
    "description": "Without this, tickets are scoped to raised_by / assigned_to / watcher.",
})
PERMISSION_REGISTRY.append({
    "slug": "tickets.tickets.assign",
    "name": "Assign tickets",
    "description": "Set or change the assignee on a ticket.",
})
PERMISSION_REGISTRY.append({
    "slug": "tickets.tickets.export",
    "name": "Export tickets",
    "description": "Permission to export tickets with dynamic fields.",
})
PERMISSION_REGISTRY.append({
    "slug": "tickets.tickets.respond",
    "name": "Respond to tickets",
    "description": "Save a response on a ticket and reply (flips status to responded, notifies submitter).",
})
PERMISSION_REGISTRY.append({
    "slug": "tickets.tickets.resolve",
    "name": "Resolve tickets",
    "description": "Save a resolution on a ticket and reply (flips status to resolved, notifies submitter).",
})
PERMISSION_REGISTRY.append({
    "slug": "system.it_support_intake.use",
    "name": "Use IT Support intake",
    "description": "Allow the MCP intake endpoint to create IT support tickets on behalf of a user or respond contact.",
})


# Email Templates - designable HTML emails with Jinja2 placeholders.
PERMISSION_REGISTRY.extend(_crud("email_templates", "templates", "Email Templates"))
PERMISSION_REGISTRY.append({
    "slug": "email_templates.templates.preview",
    "name": "Preview Email Templates",
    "description": "Render a template against sample context to verify the layout.",
})


# Automation - rule-driven scheduled email sends.
PERMISSION_REGISTRY.extend(_crud("automation", "automations", "Automations"))
PERMISSION_REGISTRY.append({
    "slug": "automation.automations.run",
    "name": "Run Automation",
    "description": "Trigger an automation manually outside its schedule.",
})


# Status engine (ADR-0001) - CORE plumbing that other modules ride. Configuring a
# state machine changes what every record of that entity can legally do, so edit is
# a deliberately separate grant from view.
PERMISSION_REGISTRY.extend([
    {
        "slug": "system.statuses.view",
        "name": "View Status Graphs",
        "description": "View configurable status graphs and their transitions.",
    },
    {
        "slug": "system.statuses.edit",
        "name": "Edit Status Graphs",
        "description": (
            "Create, edit, and delete statuses and transitions, and migrate records "
            "between statuses."
        ),
    },
])

# Translation memory (R15/R16, purchasing consolidation batch, lane C) - view/edit
# only, same shape as the status engine above: rows are written by the upload preview
# and the AI fill, never created by hand here, so this admin page only ever reads,
# corrects or removes one.
PERMISSION_REGISTRY.extend([
    {
        "slug": "system.translations.view",
        "name": "View Translations",
        "description": "View the Chinese-English translation memory and the wording the chatbot uses in its replies.",
    },
    {
        "slug": "system.translations.edit",
        "name": "Edit Translations",
        "description": "Correct a translation or remove one from the memory, and edit the wording the chatbot uses in its replies.",
    },
])

# Project Sales (UAC Group J). Every salesperson sees every project read-only, by
# design: the module exists to stop two people unknowingly working one tender, and
# hiding other people's projects would recreate exactly that blindness. Editing is
# restricted to the owner and approved collaborators, enforced in the service.
PERMISSION_REGISTRY.extend([
    {
        "slug": "projects.projects.view",
        "name": "View Projects",
        "description": "View the project pipeline, project detail, and forecasts.",
    },
    {
        "slug": "projects.projects.edit",
        "name": "Edit Projects",
        "description": (
            "Register projects, edit projects you own or collaborate on, and move "
            "them through the funnel."
        ),
    },
    {
        "slug": "projects.projects.delete",
        "name": "Delete Projects",
        "description": "Hard-delete a project that has no Project PO recorded.",
    },
    {
        "slug": "projects.projects.manage",
        "name": "Manage Any Project",
        "description": (
            "Sales-manager grant: reassign owners, decide join requests and disputes, "
            "and edit any project regardless of ownership."
        ),
    },
    {
        "slug": "projects.quotations.approve",
        "name": "Approve Below-Floor Quotations",
        "description": (
            "Sales-manager grant: approve or reject a quotation carrying a line priced "
            "below its price floor, which is what lets it be issued to the customer. The "
            "whole access control on that decision - there is no team-tier resolution "
            "behind it - so it is deliberately narrow."
        ),
    },
    {
        "slug": "projects.parties.view",
        "name": "View Project Parties",
        "description": (
            "View the organisation master of developers, architects, main contractors, "
            "trading houses and consultants."
        ),
    },
    {
        "slug": "projects.parties.edit",
        "name": "Edit Project Parties",
        "description": "Create, edit and delete project party organisations.",
    },
    {
        "slug": "projects.order_inquiry.action",
        "name": "Action Order Inquiry Rows",
        "description": (
            "Purchasing grant: mark an order inquiry row actioned or cancelled. Held by "
            "purchasing rather than by the project's salesperson, because the row is "
            "purchasing's work and they do not own the project it came from."
        ),
    },
    {
        "slug": "projects.order_inquiries.acknowledge",
        "name": "Acknowledge Order Inquiry Rows",
        "description": (
            "Purchasing grant: acknowledge an order inquiry row - which is the moment "
            "documents are linked to it - reject one with a reason, run Link now, and "
            "upload the purchase order and SPO books from the Order Inquiries page. "
            "Separate from `projects.order_inquiry.action`, which is marking a row's "
            "supply state: acknowledging is purchasing taking CS's instruction on, and "
            "CS may not do it for them."
        ),
    },
    {
        "slug": "projects.order_inquiries.reserve",
        "name": "Reserve Stock for Order Inquiries",
        "description": (
            "CS-head grant (R1, `PLAN-oi-request-cs-reserve.md`): confirm how much of a "
            "purchasing 'request CS to reserve' ask is actually reserved from own or "
            "pool stock. Narrow on purpose - only the person who may commit stock "
            "against a request holds it, separate from `projects.order_inquiries."
            "acknowledge`, which is purchasing's own grant to raise the request."
        ),
    },
    {
        "slug": "projects.stock_debt.view",
        "name": "View Stock Debt",
        "description": (
            "View the Stock Debt month-by-product balance and drill into a month's demand "
            "and supply."
        ),
    },
    {
        "slug": "projects.types.view",
        "name": "View Project Types and Templates",
        "description": "View configurable project types, templates and stakeholder roles.",
    },
    {
        "slug": "projects.types.edit",
        "name": "Edit Project Types and Templates",
        "description": (
            "Create and edit project types, templates and the stakeholder roles a "
            "template offers."
        ),
    },
])


# Ideation (Ideas board embed, shared-service SSO)
PERMISSION_REGISTRY.extend([
    {
        "slug": "ideation.board.view",
        "name": "View the Ideas board",
        "description": "View the Ideas board and open individual ideas.",
    },
    {
        "slug": "ideation.ideas.manage",
        "name": "Manage ideas",
        "description": "Triage ideas: move status, edit, merge, reorder, archive, delete and promote to a business requirement.",
    },
])


# Dealer Kit - these six were previously created ONLY by migration 309's data seed.
# Any database built via create_all + sync_permissions (CI, `scripts/bootstrap_env`)
# never executes that seed, so the slugs did not exist and every Dealer Kit route
# answered 403. Declaring them here is what makes them real on a fresh database;
# migration 309 stays as the path for databases that were already migrated.
# `sync_permissions` skips slugs that exist, so the two paths cannot conflict.
PERMISSION_REGISTRY.extend([
    {
        "slug": "dealer_kit.page.view",
        "name": "View catalogue pages",
        "description": "View Dealer Kit catalogue pages and their versions.",
    },
    {
        "slug": "dealer_kit.page.edit",
        "name": "Edit catalogue pages",
        "description": "Create and edit catalogue pages, and move the staging label.",
    },
    {
        "slug": "dealer_kit.page.publish",
        "name": "Publish catalogue pages",
        "description": "Move the published label, including rolling back to an earlier version.",
    },
    {
        "slug": "dealer_kit.library.manage",
        "name": "Manage Dealer Kit library",
        "description": "Manage assets, tile templates and reusable collections.",
    },
    {
        "slug": "dealer_kit.brochure.create",
        "name": "Create brochures",
        "description": "Produce a brochure from an existing published page.",
    },
    {
        "slug": "dealer_kit.edition.approve",
        "name": "Approve catalogue editions",
        "description": "Approve or reject a catalogue edition before it goes live.",
    },
])


# SCM (supply chain) - these five were previously created ONLY by migration 274's data
# seed. Any database built the way CI and `scripts/bootstrap_env` build one (create_all
# from the ORM, seed reference data, stamp alembic at head) never executes that seed, so
# the slugs did not exist and every SCM route answered 403 "Permission required:
# scm.dashboard.view". Declaring them here is what makes them real on a fresh database;
# migration 274 stays as the path for databases that were already migrated. `sync_permissions`
# skips slugs that exist, so the two paths cannot conflict.
PERMISSION_REGISTRY.extend([
    {
        "slug": "scm.dashboard.view",
        "name": "View SCM dashboard",
        "description": "View the supply-chain / reorder dashboard and position views.",
    },
    {
        "slug": "scm.reorder.run",
        "name": "Run reorder engine",
        "description": "Trigger a reorder run that produces recommendations.",
    },
    {
        "slug": "scm.recommendation.manage",
        "name": "Manage reorder recommendations",
        "description": "Review, override, approve, or reject reorder recommendations.",
    },
    {
        "slug": "scm.policy.manage",
        "name": "Manage reorder policies",
        "description": "Create, update, and delete reorder / scoring / demand policies.",
    },
    {
        "slug": "scm.config.manage",
        "name": "Manage SCM configuration",
        "description": "Manage SCM module configuration, budgets, and reason vocabularies.",
    },
    # Migration 375 creates this one and sweeps it onto whoever holds `scm.reorder.run`;
    # declared here so a create_all database (CI, `scripts/bootstrap_env`) has it at all.
    {
        "slug": "scm.proforma_invoice.upload",
        "name": "Upload proforma invoices",
        "description": "Upload, apply and delete supplier proforma invoices.",
    },
])
# The two documents the AutoCount ESB pushes (`/external/ingest|read/{entity}`), for the
# same reason as the block above and one measurement more: these eight slugs exist on the
# dev copy of production, put there by a migration that has since been retired, and are
# declared NOWHERE in the app. No SCM route gates on them - the screens gate on
# `scm.dashboard.view` / `scm.reorder.run` - so on a fresh database or on CI they simply do
# not exist, and the document push 403s for ever while migration 445's sweep finds no
# source row to copy. Declared here, `sync_permissions` materialises them at startup.
PERMISSION_REGISTRY.extend(_crud("scm", "sales_orders", "SCM Sales Orders"))
PERMISSION_REGISTRY.extend(_crud("scm", "purchase_orders", "SCM Purchase Orders"))
# The shipping-order entity the ESB pushes from slice S3 (PLAN-autocount-document-ingest-v2).
# Same shape and same reason as the two above; swept by migration 472 onto every role
# holding the matching `scm.purchase_orders.*` slug.
PERMISSION_REGISTRY.extend(_crud("scm", "shipping_orders", "SCM Shipping Orders"))


# Dealer Kit - price tag requests + tag templates.
# Migration 309 seeded the page/library/edition/brochure slugs; these five are
# the price-tag-request extension (migration ptag_0001 seeds and grants them).
# Declared here so a create_all database (CI, bootstrap_env) has them at all.
PERMISSION_REGISTRY.extend([
    {
        "slug": "dealer_kit.price_tag_requests.view",
        "name": "View Price Tag Requests",
        "description": "View price tag requests submitted via the portal.",
    },
    {
        "slug": "dealer_kit.price_tag_requests.create",
        "name": "Create Price Tag Requests",
        "description": "Create price tag requests (portal contacts, implicit for linked contacts).",
    },
    {
        "slug": "dealer_kit.price_tag_requests.process",
        "name": "Process Price Tag Requests",
        "description": "Claim, design, and manage price tag request lifecycle (CRM marketing).",
    },
    {
        "slug": "dealer_kit.tag_templates.view",
        "name": "View Tag Templates",
        "description": "View tag templates used in the price tag designer.",
    },
    {
        "slug": "dealer_kit.tag_templates.manage",
        "name": "Manage Tag Templates",
        "description": "Create, edit, and delete tag templates for price tag design.",
    },
])


# Reporting foundation - migration 422 creates these two and sweeps them onto whoever
# already holds the sponsorship view / edit grants. Declared here for the same reason the
# Dealer Kit and SCM blocks above are: a database built with create_all + sync_permissions
# (CI, `scripts/bootstrap_env`) never runs a migration body, so a slug that lives only in
# the migration does not exist there and every report route answers 403.
PERMISSION_REGISTRY.extend([
    {
        "slug": "procurement.sponsorship_forms.report",
        "name": "View Sponsorship Report",
        "description": "Permission to open the Sponsorship report.",
    },
    {
        "slug": "reports.views.publish",
        "name": "Publish Report Views",
        "description": "Permission to share a saved report view and set the default for everyone.",
    },
])


# Saved views (segments) - migration 455 creates this one and sweeps it onto whoever
# already holds `scm.recommendation.manage` (S4, PLAN-scm-reorder-oi-feedback-1sep.md).
# Declared here for the same reason the reporting-foundation block above is: a database
# built with create_all + sync_permissions (CI, `scripts/bootstrap_env`) never runs a
# migration body, so a slug that lives only in the migration does not exist there and
# every publish/set-default route answers 403.
PERMISSION_REGISTRY.extend([
    {
        "slug": "list_query.saved_views.publish",
        "name": "Publish Saved Views",
        "description": "Permission to share a saved listing view (segment) and set the default for everyone.",
    },
])


# Sales module (plan 3.7, slice S6). Migration `sales_0001_teams` creates these and grants
# them to admin and superadmin; declared here as well so a database built with create_all +
# sync_permissions (CI, `scripts/bootstrap_env`) has them.
PERMISSION_REGISTRY.extend(_crud("sales", "teams", "Sales Teams"))
# Slice S2 (opportunities). Migration `sales_0003_opportunities` grants these the same way.
PERMISSION_REGISTRY.extend(_crud("sales", "opportunities", "Sales Opportunities"))
# Slice S1: `sales_0003_targets` creates and grants these the same way. `.edit` also gates a
# period's figure, Duplicate and Add figure (plan 3.7).
PERMISSION_REGISTRY.extend(_crud("sales", "targets", "Sales Targets"))
# Sales-asks-todo S2: `sat_0001_stock_ask_done` creates and grants these. `view_all` lets a user
# read and clear another agent's asks (the manager page's Agent select).
PERMISSION_REGISTRY.extend(_crud("sales", "customer_asks", "Customer Asks"))
PERMISSION_REGISTRY.append({
    "slug": "sales.customer_asks.view_all",
    "name": "View All Customer Asks",
    "description": "Permission to view and clear every sales agent's customer asks, not only your own.",
})


# Finance module (plan 3.5, slice S0, #1309; ruling Q9). Migration `fin_0001_billing_documents`
# creates these and grants them to admin and superadmin; declared here as well so a database
# built with create_all + sync_permissions (CI, `scripts/bootstrap_env`) has them. `edit` and
# `delete` gate only the AutoCount ingest and deletions doors (no UI creates or deletes a
# billing document); `export` is the list's Export (S2).
PERMISSION_REGISTRY.extend([
    {
        "slug": "finance.billing_documents.view",
        "name": "View Billing Documents",
        "description": "Permission to view billing documents (invoices, cash sales, credit and debit notes).",
    },
    {
        "slug": "finance.billing_documents.export",
        "name": "Export Billing Documents",
        "description": "Permission to export the billing documents list.",
    },
    {
        "slug": "finance.billing_documents.edit",
        "name": "Ingest Billing Documents",
        "description": "Permission to push billing documents through the AutoCount ingest.",
    },
    {
        "slug": "finance.billing_documents.delete",
        "name": "Delete Billing Documents",
        "description": "Permission to delete billing documents through the AutoCount ingest.",
    },
])


def sync_permissions(db: Session, created_by_user_id: Optional[str] = None) -> int:
    """
    Idempotent sync: ensure every slug in PERMISSION_REGISTRY exists in user_permissions.
    Creates missing ones; does not change existing. Returns count of newly created.
    """
    existing = {row.slug for row in db.query(UserPermission.slug).all()}
    created = 0
    for entry in PERMISSION_REGISTRY:
        slug = entry["slug"]
        if slug in existing:
            continue
        perm = UserPermission(
            slug=slug,
            name=entry["name"],
            description=entry.get("description"),
            created_by_user_id=created_by_user_id,
        )
        db.add(perm)
        created += 1
        existing.add(slug)
    if created:
        db.commit()
    return created


# Sales module (PLAN-retail-sales-reports-26sep, S1). One slug covers the Yearly
# comparison and the Sales report screens, their Excel export and the chatbot route
# (G10 (a)). Granted to admin and superadmin by `sales_s1_reports_module`; everyone else
# through the role editor.
PERMISSION_REGISTRY.extend([
    {
        "slug": "sales.reports.view",
        "name": "View sales reports",
        "description": "Open the sales reports (Yearly comparison, Sales report), export them and ask for them on WhatsApp.",
    },
])
