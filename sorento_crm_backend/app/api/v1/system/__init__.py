"""System-level API routes."""
from fastapi import APIRouter, Depends
from app.api.v1.system import (
    api_call_logs,
    health,
    import_field_aliases,
    import_logs,
    jobs,
    calendar,
    outgoing_mails,
    scheduled_tasks,
    numbering_rules,
    embeddings,
    ai_assistant,
    references,
    tool_capabilities,
    mcp_tools,
    respond_contacts,
    respond_workspaces,
    email_templates,
    automation,
    email_outbox,
    email_event_configs,
    respond_outbox,
    chat_history,
    chatbot,
    chatbot_config,
    chatbot_field_reveals,
    chatbot_roles,
    statuses,
    translations,
)
from app.modules.runtime.guards import require_module_enabled_with_api_key

router = APIRouter(dependencies=[Depends(require_module_enabled_with_api_key("base"))])

router.include_router(health.router, tags=["health"])
router.include_router(api_call_logs.router, tags=["api-call-logs"])
router.include_router(import_logs.router, tags=["import-logs"])
router.include_router(jobs.router, tags=["jobs"])
router.include_router(calendar.router, tags=["calendar"])
router.include_router(outgoing_mails.router, tags=["outgoing-mails"])
router.include_router(scheduled_tasks.router, tags=["scheduled-tasks"])
router.include_router(numbering_rules.router, tags=["numbering-rules"])
router.include_router(import_field_aliases.router, tags=["import-field-aliases"])
router.include_router(embeddings.router, tags=["embeddings"])
router.include_router(ai_assistant.router, tags=["ai-assistant"])
router.include_router(references.router, tags=["references"])
router.include_router(tool_capabilities.router, tags=["tool-capabilities"])
router.include_router(mcp_tools.router, tags=["mcp-tools"])
router.include_router(respond_workspaces.router, tags=["respond-workspaces"])
router.include_router(respond_contacts.router, tags=["respond-contacts"])
router.include_router(email_templates.router, tags=["email-templates"])
router.include_router(automation.router, tags=["automation"])
router.include_router(email_outbox.router, tags=["email-outbox"])
router.include_router(email_event_configs.router, tags=["email-event-configs"])
router.include_router(respond_outbox.router, tags=["respond-outbox"])
router.include_router(chat_history.router, tags=["chat-history"])
# The turn trace behind Chat History. Its own module guard: the trace only exists
# where the chatbot engine is installed, and the screen is that module's read surface.
router.include_router(
    chatbot.router,
    tags=["chatbot-turns"],
    dependencies=[Depends(require_module_enabled_with_api_key("chatbot"))],
)
# The chatbot's POLICY tables (turn re-architecture S5, AC-1561): domains and entity
# kinds, the rows `turn/policy.py::load_policy` reads once per turn. Same module guard
# as the trace above - config for an engine that is not installed answers nothing.
router.include_router(
    chatbot_config.router,
    tags=["chatbot-config"],
    dependencies=[Depends(require_module_enabled_with_api_key("chatbot"))],
)
# Per-contact field reveals (Slice C): admin config over the chatbot's field-gate
# table, not a turn-path surface. Same module guard: the grant is meaningless
# where the chatbot engine is not installed.
router.include_router(
    chatbot_field_reveals.router,
    tags=["chatbot-field-reveals"],
    dependencies=[Depends(require_module_enabled_with_api_key("chatbot"))],
)
# Chatbot roles, the access registry and per-contact access (ACCESS-MODEL S4).
router.include_router(
    chatbot_roles.router,
    tags=["chatbot-roles"],
    dependencies=[Depends(require_module_enabled_with_api_key("chatbot"))],
)
# Status engine (ADR-0001). CORE plumbing, so it rides the always-on `base` guard
# rather than a module key of its own.
router.include_router(statuses.router, tags=["statuses"])
# Translation memory (R15/R16, purchasing consolidation batch). Not module-scoped -
# see `app.models.translation_memory` for why.
router.include_router(translations.router, tags=["translations"])
