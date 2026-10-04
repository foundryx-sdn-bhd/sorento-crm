"""System AI assistant config and chat endpoints."""
from __future__ import annotations


from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_any_permission, require_permission
from app.models.access import RespondContact
from app.models.ai_assistant import (
    AIAssistantConversation,
    AIAssistantMessage,
    AIAssistantSpan,
    AIAssistantTrace,
    AIAssistantUsageLog,
)
from app.models.user import User
from app.schemas.ai_prompt import (
    DryRunRequest,
    DryRunResponse,
    DryRunToolCall,
    PromptKeySummary,
    PromptVersionDetail,
    PromptVersionsResponse,
    RegistryVariableRow,
    SaveVersionRequest,
    SetLabelRequest,
    SetAgentModelRequest,
    SetAgentModelResponse,
    SetLabelResponse,
)
from app.api.v1.external.chat import (
    CHATBOT_PROMPT_KEYS,
    DryRunTurnRefused,
    run_prompt_dry_run_turn,
)
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.ai_prompt_service import AIPromptRegistryError, AIPromptService
from app.schemas.ai_assistant import (
    AIAssistantConfigResponse,
    AIAssistantConfigUpdate,
    AIAssistantConversationResponse,
    AIAssistantMessageCreate,
    AIAssistantMessageResponse,
    GreetingResponse,
    QueryDetailResponse,
    RecentQueryItem,
    TestConnectionRequest,
    ProviderModelsResponse,
    TestConnectionResponse,
    TestModelRequest,
    TestModelResponse,
    TopContactItem,
    TopUserItem,
    TraceResponse,
    TraceSpanItem,
    UsageByDayItem,
    UsageSummaryResponse,
    WishlistClusterItem,
)
from app.services.ai_assistant_service import AIAssistantChatService, AIAssistantConfigService
from app.services.ai_wishlist_service import AiWishlistService
from app.services.llm_provider import get_provider
from app.services.provider_model_catalog import (
    list_models as list_provider_models,
    model_choices,
    probe_model,
)
from app.services.user_service import UserPermissionService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()


def _feature_filters(feature: Optional[str]) -> list:
    """Optional ``AIAssistantUsageLog.feature == feature`` filter clause.

    Returns ``[]`` when no feature is requested, so the existing default
    behavior is unchanged. NULL feature values (legacy rows written before
    the discriminator was added) are treated as legacy AI assistant rows
    and only included when ``feature`` is unset.
    """
    if not feature:
        return []
    return [AIAssistantUsageLog.feature == feature]


def _resolve_window(from_: Optional[str], to: Optional[str]) -> tuple[datetime, datetime]:
    """Parse ISO-8601 window params; default to last 30 days."""
    now = datetime.utcnow()
    default_from = now - timedelta(days=30)
    try:
        f = datetime.fromisoformat(from_) if from_ else default_from
    except ValueError:
        f = default_from
    try:
        t = datetime.fromisoformat(to) if to else now
    except ValueError:
        t = now
    if t < f:
        f, t = t, f
    return f, t


def _message_to_response(row: AIAssistantMessage) -> AIAssistantMessageResponse:
    meta = row.metadata_json or {}
    suggestions = meta.get("suggestions") if isinstance(meta, dict) else []
    if not isinstance(suggestions, list):
        suggestions = []
    return AIAssistantMessageResponse(
        id=str(row.id),
        role=row.role,
        content=row.content,
        metadata_json=meta if isinstance(meta, dict) else {},
        created_at=row.created_at,
        suggestions=[str(s) for s in suggestions][:5],
    )


@router.get("/ai-assistant/config", response_model=AIAssistantConfigResponse)
def get_ai_assistant_config(
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    svc = AIAssistantConfigService(db)
    row = svc.get()
    return AIAssistantConfigResponse(**svc.to_response_dict(row))


@router.put("/ai-assistant/config", response_model=AIAssistantConfigResponse)
def update_ai_assistant_config(
    payload: AIAssistantConfigUpdate,
    user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
    db: Session = Depends(get_db),
):
    svc = AIAssistantConfigService(db)
    row = svc.update(payload, user_id=str(user["id"]))
    return AIAssistantConfigResponse(**svc.to_response_dict(row))


@router.post("/ai-assistant/test-connection", response_model=TestConnectionResponse)
def test_ai_assistant_connection(
    payload: TestConnectionRequest,
    _user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
):
    """One-shot, non-persistent connection probe for the configured provider."""
    try:
        provider = get_provider(payload.provider, payload.api_key, payload.model)
    except ValueError as exc:
        return TestConnectionResponse(ok=False, message=str(exc), latency_ms=0)
    ok, message, latency_ms = provider.test_connection()
    return TestConnectionResponse(ok=ok, message=message, latency_ms=latency_ms)


# Both routes below are reachable from TWO screens with different permissions -
# the assistant settings page and the chatbot media settings page - so they are
# gated on either. Gating on the assistant permission alone would 403 the media
# page for exactly the operator who has to fix a broken degraded model.
_MODEL_VIEW = ["system.ai_assistant_settings.view", "user_management.settings.view"]
_MODEL_EDIT = ["system.ai_assistant_settings.edit", "user_management.settings.edit"]

# The slug the Chat History trace screen already uses to show a contact's turn trace
# (`app/api/v1/system/chat_history.py`, and `VIEW` in `app/api/v1/system/chatbot.py`).
# The Prompts Test button for the two chatbot keys runs a dry-run turn for a
# CALLER-NAMED contact and returns its trace, which reads that contact's access level
# and remembered session state - so it is REQUIRED IN ADDITION to
# `system.ai_assistant_settings.edit`, which is a prompt-editing slug and says nothing
# about who may read a customer's conversation state (AC-807, S8a review S3).
_CHATBOT_TRACE_VIEW = "system.chat_history.view"


@router.get("/ai-assistant/models", response_model=ProviderModelsResponse)
def list_ai_assistant_models(
    provider: Optional[str] = Query(None, max_length=64),
    _user: dict = Depends(require_any_permission(_MODEL_VIEW)),
    db: Session = Depends(get_db),
):
    """What `provider` says this key may call, cached for an hour.

    A blank `provider` is not an error: it is what the media settings page saves
    to mean "inherit the assistant's provider", so it resolves to that provider
    and the response says which one it landed on.

    Never 502s on an unreachable provider: the built-in list is served with
    `source="fallback"` and the reason, because a model picker that fails closed
    stops an operator fixing the very setting they came for.
    """
    result = list_provider_models(db, provider)
    return ProviderModelsResponse(
        provider=result.provider,
        source=result.source,
        message=result.message,
        models=model_choices(result),
    )


@router.post("/ai-assistant/test-model", response_model=TestModelResponse)
def test_ai_assistant_model(
    payload: TestModelRequest,
    _user: dict = Depends(require_any_permission(_MODEL_EDIT)),
    db: Session = Depends(get_db),
):
    """Call the model once, for real, and report the provider's own words.

    Distinct from `/test-connection`, which proves the KEY works: Gemini's key
    probe is a ListModels call, and ListModels happily returns a model whose
    first generateContent is a 404. This is the check that catches that.

    `with_image` sends a 1x1 PNG along with the prompt, which is what a caller
    certifying an image-lane model wants: a text-only model passes the plain
    probe and then fails on every photo.
    """
    ok, message, latency_ms = probe_model(
        db, payload.provider, payload.model, with_image=payload.with_image
    )
    return TestModelResponse(ok=ok, message=message, latency_ms=latency_ms)


@router.get("/ai-assistant/tools", response_model=list[str])
def list_ai_assistant_tools(
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    chat = AIAssistantChatService(db)
    return chat.list_mcp_tools()


@router.get("/ai-assistant/greeting", response_model=GreetingResponse)
def ai_assistant_greeting(
    current_user: dict = Depends(require_permission("system.ai_assistant_chat.use")),
    db: Session = Depends(get_db),
):
    chat = AIAssistantChatService(db)
    data = chat.generate_greeting(current_user["id"])
    return GreetingResponse(**data)


@router.get("/ai-assistant/conversations", response_model=list[AIAssistantConversationResponse])
def list_ai_assistant_conversations(
    q: Optional[str] = Query(None, description="Substring filter on conversation title"),
    user: dict = Depends(require_permission("system.ai_assistant_chat.use")),
    db: Session = Depends(get_db),
):
    chat = AIAssistantChatService(db)
    rows = chat.list_conversations(str(user["id"]), q=q, limit=100)
    return [
        AIAssistantConversationResponse(
            id=str(r.id),
            title=r.title,
            created_at=r.created_at,
            updated_at=r.updated_at,
            messages=[],
        )
        for r in rows
    ]


@router.get("/ai-assistant/conversations/{conversation_id}/messages", response_model=list[AIAssistantMessageResponse])
def list_ai_assistant_messages(
    conversation_id: str,
    user: dict = Depends(require_permission("system.ai_assistant_chat.use")),
    db: Session = Depends(get_db),
):
    chat = AIAssistantChatService(db)
    rows = chat.list_messages(conversation_id, str(user["id"]))
    return [_message_to_response(r) for r in rows]


@router.post("/ai-assistant/chat", response_model=AIAssistantConversationResponse)
def send_ai_assistant_message(
    payload: AIAssistantMessageCreate,
    user: dict = Depends(require_permission("system.ai_assistant_chat.use")),
    db: Session = Depends(get_db),
):
    chat = AIAssistantChatService(db)
    conv, _ = chat.respond(
        user_id=str(user["id"]),
        conversation_id=payload.conversation_id,
        message=payload.message,
        page_snapshot=payload.page_snapshot,
        confirm_action=payload.confirm_action,
    )
    rows = chat.list_messages(str(conv.id), str(user["id"]))
    return AIAssistantConversationResponse(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[_message_to_response(r) for r in rows],
    )


# --- Prompt registry --------------------------------------------------------


def _ensure_known_prompt(name: str) -> None:
    if name not in PROMPT_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown prompt key '{name}'.")


@router.get("/ai-assistant/prompts", response_model=list[PromptKeySummary])
def list_ai_assistant_prompts(
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    return [PromptKeySummary(**row) for row in AIPromptService(db).list_keys()]


@router.get("/ai-assistant/prompts/{name}/versions", response_model=PromptVersionsResponse)
def get_ai_assistant_prompt_versions(
    name: str,
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    _ensure_known_prompt(name)
    return PromptVersionsResponse(**AIPromptService(db).get_versions(name))


@router.get(
    "/ai-assistant/prompts/{name}/versions/{version}",
    response_model=PromptVersionDetail,
)
def get_ai_assistant_prompt_version(
    name: str,
    version: int,
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    _ensure_known_prompt(name)
    return PromptVersionDetail(**AIPromptService(db).get_version(name, version))


@router.post(
    "/ai-assistant/prompts/{name}/versions",
    response_model=PromptVersionDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_ai_assistant_prompt_version(
    name: str,
    payload: SaveVersionRequest,
    response: Response,
    user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
    db: Session = Depends(get_db),
):
    _ensure_known_prompt(name)
    try:
        row = AIPromptService(db).save_version(
            name,
            template=payload.template,
            commit_message=payload.commit_message,
            user_id=str(user["id"]),
        )
    except AIPromptRegistryError as exc:
        # Contract §8b: 422 { error, unknown_tokens, missing_vars } at top level.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": exc.error,
                "unknown_tokens": exc.unknown_tokens,
                "missing_vars": exc.missing_vars,
            },
        )
    response.status_code = status.HTTP_201_CREATED
    return PromptVersionDetail(**row)


@router.get(
    "/ai-assistant/prompts/{name}/registry-variables",
    response_model=list[RegistryVariableRow],
)
def get_ai_assistant_prompt_registry_variables(
    name: str,
    version: Optional[int] = Query(None),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    """The editor's "Wired to this agent" panel (PLAN-prompt-dynamic-30sep R5a): every
    registry variable with its source, row count, last change and CURRENT rendered text.
    `used` is read against `version` when given; the editor tracks its own draft."""
    from app.services import chatbot_prompt_vars

    _ensure_known_prompt(name)
    if not PROMPT_KEYS[name].registry_variables:
        return []
    template = AIPromptService(db).get_version(name, version)["template"] if version else None
    return [RegistryVariableRow(**row) for row in chatbot_prompt_vars.describe(db, template)]


@router.post("/ai-assistant/prompts/{name}/labels", response_model=SetLabelResponse)
def set_ai_assistant_prompt_label(
    name: str,
    payload: SetLabelRequest,
    user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
    db: Session = Depends(get_db),
):
    _ensure_known_prompt(name)
    labels = AIPromptService(db).set_label(
        name,
        label=payload.label,
        version_id=payload.version_id,
        user_id=str(user["id"]),
    )
    return SetLabelResponse(labels=labels)


@router.post("/ai-assistant/prompts/{name}/model", response_model=SetAgentModelResponse)
def set_ai_assistant_agent_model(
    name: str,
    payload: SetAgentModelRequest,
    user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
    db: Session = Depends(get_db),
):
    """Choose which LLM this agent runs on. Blank = the global assistant model."""
    _ensure_known_prompt(name)
    result = AIPromptService(db).set_agent_model(
        name,
        label=payload.label,
        provider=payload.provider,
        model=payload.model,
    )
    return SetAgentModelResponse(**result)


@router.post("/ai-assistant/prompts/{name}/test")
def test_ai_assistant_prompt_version(
    name: str,
    payload: DryRunRequest,
    user: dict = Depends(require_permission("system.ai_assistant_settings.edit")),
    db: Session = Depends(get_db),
):
    """Single-message dry-run: run one real assistant turn with ONLY this key
    overridden to ``version_id`` (rest = production). The throwaway conversation
    is deleted afterwards so it never pollutes history, and write-capable MCP
    tools (``*_submit`` / ``*_create`` / ``*_link``) are stripped for the turn so
    a test can never persist real business data. Dormant key → 400; a key that is not
    an assistant-pipeline node → 400 as well.

    **The two chatbot keys run a chatbot TURN instead** (AC-807). They are not assistant
    pipeline nodes, so the chat dry run above would answer from prompts the operator did
    not edit; the turn is what actually exercises them. Same version pinning, and
    `is_test` so it writes nothing outside `chatbot.turns` (D14). They need
    `system.chat_history.view` ON TOP of the edit slug, because that turn reads a named
    contact's state and hands back the trace.

    **No `response_model`.** The two answers have different shapes (an assistant output
    and token usage; a turn id, status, branch and trace) and `response_model` silently
    DROPS undeclared fields, so declaring either one would empty the other
    (`LESSONS-LEARNT`). Both are serialised as plain dicts and pinned by tests.
    """
    _ensure_known_prompt(name)
    if not PROMPT_KEYS[name].active:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt '{name}' is dormant and has no runtime call site to test.",
        )
    if name in CHATBOT_PROMPT_KEYS:
        return _test_chatbot_prompt_version(name, payload, db, user)
    if not PROMPT_KEYS[name].dry_runnable:
        # Checked BEFORE the version lookup: running a non-assistant key (the spec
        # extractor, the SCM advisory) through the chat pipeline produces an answer
        # from prompts the tester did not edit, which reads as a result rather than
        # as a category error.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Prompt '{name}' is not part of the assistant pipeline, so a chat "
                f"dry-run would not exercise it. Test it from the screen that uses it."
            ),
        )
    # Validate the version belongs to this key before running a real turn.
    _version_for_key_or_404(db, name, payload.version_id)

    chat = AIAssistantChatService(db)
    conv, msg = chat.respond(
        user_id=str(user["id"]),
        conversation_id=None,
        message=payload.message,
        prompt_overrides={name: payload.version_id},
        dry_run=True,
    )
    meta = msg.metadata_json if isinstance(msg.metadata_json, dict) else {}
    tool_calls = [
        DryRunToolCall(name=str(c.get("tool_name") or ""), ok=bool(c.get("ok")))
        for c in (meta.get("tool_calls") or [])
        if isinstance(c, dict)
    ]
    usage_row = (
        db.query(AIAssistantUsageLog)
        .filter(AIAssistantUsageLog.message_id == str(msg.id))
        .first()
    )
    token_usage = {
        "prompt_tokens": int(usage_row.prompt_tokens or 0) if usage_row else 0,
        "completion_tokens": int(usage_row.completion_tokens or 0) if usage_row else 0,
        "total_tokens": int(usage_row.total_tokens or 0) if usage_row else 0,
    }
    output = msg.content or ""

    # Cleanup: dry-run is non-persistent. Usage logs reference the conversation
    # via SET NULL, so delete them explicitly; deleting the conversation cascades
    # to its messages.
    try:
        db.query(AIAssistantUsageLog).filter(
            AIAssistantUsageLog.conversation_id == str(conv.id)
        ).delete(synchronize_session=False)
        conv_row = db.query(AIAssistantConversation).filter(
            AIAssistantConversation.id == str(conv.id)
        ).first()
        if conv_row is not None:
            db.delete(conv_row)
        db.commit()
    except Exception:
        db.rollback()

    return DryRunResponse(
        output=output,
        token_usage=token_usage,
        tool_calls=tool_calls,
        used_overrides={name: payload.version_id},
    ).model_dump()


def _version_for_key_or_404(db: Session, name: str, version_id: str) -> None:
    """404 unless `version_id` is a version OF THIS KEY.

    One lookup for both branches of the Test action. A stale id from the browser must be
    a 404 rather than a turn that quietly ran the published prompt, which would look like
    a passing test of an edit that was never exercised.

    `validate_uuid_path` first, because `AIPromptVersion.id` is a `uuid` column and
    Postgres raises on a malformed literal - which surfaced as a 500 where the route
    promises a 404.
    """
    from app.models.ai_prompt import AIPromptVersion

    validate_uuid_path(version_id, resource="Prompt version")
    version = (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.id == version_id, AIPromptVersion.name == name)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found for this prompt.")


def _test_chatbot_prompt_version(
    name: str, payload: DryRunRequest, db: Session, user: dict
) -> dict:
    """AC-807: run one dry-run chatbot turn on the version being edited.

    **Two slugs, not one.** The route's own `system.ai_assistant_settings.edit` says the
    caller may edit prompts. This branch additionally reads a CALLER-NAMED contact's
    access level and remembered session state and returns the trace, which is what the
    Chat History trace screen shows under `system.chat_history.view` - so that slug is
    required here too, and nobody reads a contact's remembered state through a weaker one
    (S8a review S3). The contact stays caller-supplied: the workspace has no dev-contact
    column, and inventing one to satisfy the AC's wording would be a new config surface
    for a test button.
    """
    if not UserPermissionService(db).check_user_has_permission(
        user["id"], _CHATBOT_TRACE_VIEW
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Permission required: {_CHATBOT_TRACE_VIEW}. Running a chatbot turn "
                "reads that contact's remembered conversation state."
            ),
        )
    _version_for_key_or_404(db, name, payload.version_id)

    try:
        result = run_prompt_dry_run_turn(
            prompt_key=name,
            version_id=payload.version_id,
            message=payload.message,
            contact_respond_id=payload.contact_respond_id,
        )
    except DryRunTurnRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "used_overrides": {name: payload.version_id}}


# --- Usage analytics --------------------------------------------------------


@router.get("/ai-assistant/usage/summary", response_model=UsageSummaryResponse)
def usage_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    feature: Optional[str] = Query(None, description="Filter by usage feature (ai_assistant | ai_extract | ai_document_extract | chatbot_parser)."),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    f, t = _resolve_window(from_, to)
    row = (
        db.query(
            func.count(AIAssistantUsageLog.id).label("messages"),
            func.coalesce(func.sum(AIAssistantUsageLog.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(AIAssistantUsageLog.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(AIAssistantUsageLog.total_tokens), 0).label("total"),
        )
        .filter(
            AIAssistantUsageLog.created_at >= f,
            AIAssistantUsageLog.created_at <= t,
            *_feature_filters(feature),
        )
        .one()
    )
    return UsageSummaryResponse(
        total_messages=int(row.messages or 0),
        prompt_tokens=int(row.prompt or 0),
        completion_tokens=int(row.completion or 0),
        total_tokens=int(row.total or 0),
    )


@router.get("/ai-assistant/usage/by-day", response_model=list[UsageByDayItem])
def usage_by_day(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    feature: Optional[str] = Query(None, description="Filter by usage feature (ai_assistant | ai_extract | ai_document_extract | chatbot_parser)."),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    f, t = _resolve_window(from_, to)
    bucket = func.date_trunc("day", AIAssistantUsageLog.created_at).label("day")
    rows = (
        db.query(
            bucket,
            func.count(AIAssistantUsageLog.id).label("messages"),
            func.coalesce(func.sum(AIAssistantUsageLog.total_tokens), 0).label("tokens"),
        )
        .filter(
            AIAssistantUsageLog.created_at >= f,
            AIAssistantUsageLog.created_at <= t,
            *_feature_filters(feature),
        )
        .group_by(bucket)
        .order_by(bucket.asc())
        .all()
    )
    out: list[UsageByDayItem] = []
    for r in rows:
        date_value = r.day
        if isinstance(date_value, datetime):
            date_str = date_value.date().isoformat()
        else:
            date_str = str(date_value)[:10]
        out.append(UsageByDayItem(date=date_str, messages=int(r.messages or 0), tokens=int(r.tokens or 0)))
    return out


@router.get("/ai-assistant/usage/top-users", response_model=list[TopUserItem])
def usage_top_users(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    feature: Optional[str] = Query(None, description="Filter by usage feature (ai_assistant | ai_extract | ai_document_extract | chatbot_parser)."),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    f, t = _resolve_window(from_, to)
    rows = (
        db.query(
            AIAssistantUsageLog.user_id,
            User.name,
            func.count(AIAssistantUsageLog.id).label("messages"),
            func.coalesce(func.sum(AIAssistantUsageLog.total_tokens), 0).label("tokens"),
        )
        .outerjoin(User, User.id == AIAssistantUsageLog.user_id)
        .filter(
            AIAssistantUsageLog.created_at >= f,
            AIAssistantUsageLog.created_at <= t,
            AIAssistantUsageLog.user_id.isnot(None),
            *_feature_filters(feature),
        )
        .group_by(AIAssistantUsageLog.user_id, User.name)
        .order_by(desc("tokens"))
        .limit(limit)
        .all()
    )
    return [
        TopUserItem(
            user_id=str(r.user_id) if r.user_id else None,
            name=r.name,
            messages=int(r.messages or 0),
            tokens=int(r.tokens or 0),
        )
        for r in rows
    ]


@router.get("/ai-assistant/usage/top-contacts", response_model=list[TopContactItem])
def usage_top_contacts(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    feature: Optional[str] = Query("ai_extract", description="Default scopes to ai_extract - pass empty for all."),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    """Top portal contacts by token spend.

    Joins respond_contacts so admins can see phone number + name. Excludes
    rows without a contact_id (those are user-scoped - see top-users).
    Defaults to feature='ai_extract' since that's the only writer that
    populates contact_id today.
    """
    f, t = _resolve_window(from_, to)
    feature_param = feature or None
    rows = (
        db.query(
            AIAssistantUsageLog.contact_id,
            RespondContact.name,
            RespondContact.phone_number,
            func.count(AIAssistantUsageLog.id).label("messages"),
            func.coalesce(func.sum(AIAssistantUsageLog.total_tokens), 0).label("tokens"),
        )
        .outerjoin(RespondContact, RespondContact.id == AIAssistantUsageLog.contact_id)
        .filter(
            AIAssistantUsageLog.created_at >= f,
            AIAssistantUsageLog.created_at <= t,
            AIAssistantUsageLog.contact_id.isnot(None),
            *_feature_filters(feature_param),
        )
        .group_by(AIAssistantUsageLog.contact_id, RespondContact.name, RespondContact.phone_number)
        .order_by(desc("tokens"))
        .limit(limit)
        .all()
    )
    return [
        TopContactItem(
            contact_id=str(r.contact_id) if r.contact_id else None,
            name=r.name,
            phone_number=r.phone_number,
            messages=int(r.messages or 0),
            tokens=int(r.tokens or 0),
        )
        for r in rows
    ]


@router.get("/ai-assistant/usage/recent-queries", response_model=list[RecentQueryItem])
def usage_recent_queries(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    feature: Optional[str] = Query(None, description="Filter by usage feature (ai_assistant | ai_extract | ai_document_extract | chatbot_parser)."),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    f, t = _resolve_window(from_, to)
    rows = (
        db.query(
            AIAssistantUsageLog,
            User.name,
            RespondContact.name.label("contact_name"),
            RespondContact.phone_number,
            AIAssistantMessage.created_at,
            AIAssistantMessage.conversation_id,
        )
        .outerjoin(User, User.id == AIAssistantUsageLog.user_id)
        .outerjoin(RespondContact, RespondContact.id == AIAssistantUsageLog.contact_id)
        .outerjoin(AIAssistantMessage, AIAssistantMessage.id == AIAssistantUsageLog.message_id)
        .filter(
            AIAssistantUsageLog.created_at >= f,
            AIAssistantUsageLog.created_at <= t,
            *_feature_filters(feature),
        )
        .order_by(AIAssistantUsageLog.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[RecentQueryItem] = []
    for log, user_name, contact_name, contact_phone, asst_created, conv_id in rows:
        # Find the user message that prompted this assistant turn:
        # most recent user message in the same conversation BEFORE the assistant message.
        preview = ""
        if conv_id and asst_created:
            user_msg = (
                db.query(AIAssistantMessage.content)
                .filter(
                    AIAssistantMessage.conversation_id == conv_id,
                    AIAssistantMessage.role == "user",
                    AIAssistantMessage.created_at <= asst_created,
                )
                .order_by(AIAssistantMessage.created_at.desc())
                .first()
            )
            if user_msg:
                preview = (user_msg[0] or "")[:120]
        # AI extract rows have no conversation/message - surface the form_key
        # as the preview so the table is still informative.
        if not preview and log.form_key:
            preview = f"[{log.form_key}]"
        out.append(
            RecentQueryItem(
                message_id=str(log.message_id) if log.message_id else None,
                user_name=user_name,
                contact_name=contact_name,
                contact_phone=contact_phone,
                feature=log.feature,
                form_key=log.form_key,
                query_preview=preview,
                response_time_ms=int(log.response_time_ms or 0),
                tokens=int(log.total_tokens or 0),
                created_at=log.created_at,
            )
        )
    return out


@router.get("/ai-assistant/usage/queries/{message_id}", response_model=QueryDetailResponse)
def usage_query_detail(
    message_id: str,
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    msg = db.query(AIAssistantMessage).filter(AIAssistantMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    log = (
        db.query(AIAssistantUsageLog)
        .filter(AIAssistantUsageLog.message_id == message_id)
        .order_by(AIAssistantUsageLog.created_at.desc())
        .first()
    )
    usage_dict: Optional[dict[str, Any]] = None
    if log:
        usage_dict = {
            "model": log.model,
            "provider": log.provider,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "total_tokens": log.total_tokens,
            "tool_calls_count": log.tool_calls_count,
            "response_time_ms": log.response_time_ms,
            "was_answered": log.was_answered,
        }
    meta = msg.metadata_json if isinstance(msg.metadata_json, dict) else {}
    tools_used = meta.get("tool_calls") or []
    if not isinstance(tools_used, list):
        tools_used = []
    # Find prompting user message for query_preview
    user_query = ""
    if msg.conversation_id:
        prior = (
            db.query(AIAssistantMessage.content)
            .filter(
                AIAssistantMessage.conversation_id == msg.conversation_id,
                AIAssistantMessage.role == "user",
                AIAssistantMessage.created_at <= msg.created_at,
            )
            .order_by(AIAssistantMessage.created_at.desc())
            .first()
        )
        if prior:
            user_query = (prior[0] or "")[:500]
    user_name = None
    if log and log.user_id:
        u = db.query(User).filter(User.id == log.user_id).first()
        user_name = u.name if u else None
    return QueryDetailResponse(
        message_id=str(msg.id),
        user_name=user_name,
        query_preview=user_query,
        reply=msg.content or "",
        response_time_ms=int(log.response_time_ms or 0) if log else 0,
        tokens=int(log.total_tokens or 0) if log else 0,
        created_at=msg.created_at,
        metadata_json=meta,
        usage=usage_dict,
        tools_used=tools_used,
    )


@router.get(
    "/ai-assistant/usage/queries/{message_id}/trace",
    response_model=TraceResponse,
)
def usage_query_trace(
    message_id: str,
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    """M2 - full per-turn trace (root + ordered span tree) for an assistant
    message. Admin-only. 404 when the turn has no trace (e.g. legacy rows or a
    swept/expired trace)."""
    trace = (
        db.query(AIAssistantTrace)
        .filter(AIAssistantTrace.message_id == message_id)
        .order_by(AIAssistantTrace.created_at.desc())
        .first()
    )
    if not trace:
        # Fall back to the message.trace_id link (covers SET NULL-detached rows).
        msg = db.query(AIAssistantMessage).filter(AIAssistantMessage.id == message_id).first()
        if msg and getattr(msg, "trace_id", None):
            trace = db.query(AIAssistantTrace).filter(AIAssistantTrace.id == msg.trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="No trace for this message")

    spans = (
        db.query(AIAssistantSpan)
        .filter(AIAssistantSpan.trace_id == trace.id)
        .order_by(AIAssistantSpan.dotted_order.asc())
        .all()
    )
    return TraceResponse(
        id=str(trace.id),
        message_id=str(trace.message_id) if trace.message_id else None,
        conversation_id=str(trace.conversation_id) if trace.conversation_id else None,
        user_id=str(trace.user_id) if trace.user_id else None,
        session_id=trace.session_id,
        status=trace.status,
        flagged=bool(trace.flagged),
        env=trace.env,
        total_tokens_in=int(trace.total_tokens_in or 0),
        total_tokens_out=int(trace.total_tokens_out or 0),
        latency_ms=int(trace.latency_ms or 0),
        span_count=int(trace.span_count or 0),
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        created_at=trace.created_at,
        spans=[
            TraceSpanItem(
                id=str(s.id),
                parent_id=str(s.parent_id) if s.parent_id else None,
                dotted_order=s.dotted_order or "",
                span_kind=s.span_kind,
                name=s.name or "",
                input_json=s.input_json,
                output_json=s.output_json,
                status=s.status,
                error=s.error,
                latency_ms=int(s.latency_ms or 0),
                request_model=s.request_model,
                finish_reason=s.finish_reason,
                invocation_params=s.invocation_params,
                tokens_in=int(s.tokens_in or 0),
                tokens_out=int(s.tokens_out or 0),
                prompt_name=s.prompt_name,
                prompt_version=s.prompt_version,
                tool_name=s.tool_name,
                tool_call_id=s.tool_call_id,
                tool_args=s.tool_args,
                tool_result=s.tool_result,
                query=s.query,
                documents=s.documents,
                top_k=s.top_k,
            )
            for s in spans
        ],
    )


@router.get("/ai-assistant/wishlist", response_model=list[WishlistClusterItem])
def list_wishlist_clusters(
    limit: int = Query(20, ge=1, le=100),
    _user: dict = Depends(require_permission("system.ai_assistant_settings.view")),
    db: Session = Depends(get_db),
):
    items = AiWishlistService(db).list_clusters(limit=limit)
    return [
        WishlistClusterItem(
            id=str(it["id"]),
            representative_question=it.get("representative_question") or "",
            category=it.get("category"),
            count=int(it.get("count") or 0),
            last_seen_at=it.get("last_seen_at"),
            created_at=it["created_at"],
        )
        for it in items
    ]
