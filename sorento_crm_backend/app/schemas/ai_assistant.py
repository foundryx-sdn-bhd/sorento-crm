"""Schemas for AI assistant settings and chat APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AIAssistantConfigUpdate(BaseModel):
    provider: str = Field(..., max_length=64)
    model: str = Field(..., max_length=128)
    temperature: int = Field(0, ge=0, le=2)
    system_prompt: str = ""
    api_key: Optional[str] = Field(None, description="Optional replacement API key")
    anthropic_api_key: Optional[str] = Field(
        None, description="Optional replacement Anthropic key (SCM market web search)"
    )
    gemini_api_key: Optional[str] = Field(
        None, description="Optional replacement Google Gemini key (chatbot media image lane)"
    )
    enabled_tools: list[str] = Field(default_factory=list)
    rag_enabled: bool = True
    is_enabled: bool = True


class AIAssistantConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    model: str
    temperature: int
    system_prompt: str
    api_key_masked: Optional[str] = None
    anthropic_api_key_masked: Optional[str] = None
    gemini_api_key_masked: Optional[str] = None
    enabled_tools: list[str]
    rag_enabled: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class PageEntityRef(BaseModel):
    """The specific record the user is viewing, registered by the FE per-screen
    context provider. Covers detail pages AND modals (URL-parse alone can't see
    modals). Additive/optional on the snapshot - old clients omit it."""

    entity_type: str
    id: str


class PageSnapshotPayload(BaseModel):
    """Comet-style page context shipped with each chat turn (optional)."""

    path: str = ""
    search: str = ""
    title: str = ""
    visible_text: str = ""
    entity: PageEntityRef | None = None


class AIAssistantMessageCreate(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(..., min_length=1)
    page_snapshot: Optional[PageSnapshotPayload] = None
    # M3a write-confirmation: set by the FE Confirm/Cancel buttons rendered on a
    # pending-write assistant message. "confirm" executes the stored write;
    # "cancel" drops it. None = a normal turn.
    confirm_action: Optional[Literal["confirm", "cancel"]] = None


class AIAssistantMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    suggestions: list[str] = Field(default_factory=list)


class AIAssistantConversationResponse(BaseModel):
    id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: list[AIAssistantMessageResponse] = Field(default_factory=list)


class AIAssistantAuthContext(BaseModel):
    user_id: str
    role_ids: list[str]
    permission_slugs: list[str]
    enabled_modules: list[str]


# --- Settings: test-connection ---------------------------------------------


class TestConnectionRequest(BaseModel):
    provider: str = Field(..., max_length=64)
    api_key: str = Field(..., min_length=1)
    model: Optional[str] = Field(None, max_length=128)


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int


class ProviderModelItem(BaseModel):
    value: str
    label: str


class ProviderModelsResponse(BaseModel):
    """The pickable models for one provider.

    `source` says whether the provider answered ("live") or the built-in list is
    standing in ("fallback"); `message` carries the reason when it is standing
    in. Both reach the screen: an operator picking a model off a list that could
    not be refreshed should be told so.
    """

    provider: str
    source: str
    message: Optional[str] = None
    models: list[ProviderModelItem] = Field(default_factory=list)


class TestModelRequest(BaseModel):
    """A named model on a named provider, run with the stored key.

    No `api_key` field, unlike `TestConnectionRequest`: this probe is about the
    MODEL, so it uses the key already configured rather than asking a settings
    page that never had the plaintext to send one. A blank provider inherits the
    assistant's, the same as a blank provider on the media settings page.
    """

    provider: Optional[str] = Field(None, max_length=64)
    model: str = Field(..., min_length=1, max_length=128)
    # Set by the image lane's fields: a text-only model answers the plain probe
    # and then fails on every photo, so "does it work" there means "can it see".
    with_image: bool = False


class TestModelResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int


class GreetingResponse(BaseModel):
    greeting: str
    suggestions: list[str] = Field(default_factory=list)


# --- Usage analytics --------------------------------------------------------


class UsageSummaryResponse(BaseModel):
    total_messages: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0


class UsageByDayItem(BaseModel):
    date: str
    messages: int
    tokens: int
    cost_usd: float = 0.0
    unpriced_calls: int = 0


class TopUserItem(BaseModel):
    user_id: Optional[str] = None
    name: Optional[str] = None
    messages: int
    tokens: int
    cost_usd: float = 0.0
    unpriced_calls: int = 0


class TopContactItem(BaseModel):
    contact_id: Optional[str] = None
    name: Optional[str] = None
    phone_number: Optional[str] = None
    messages: int
    tokens: int
    cost_usd: float = 0.0
    unpriced_calls: int = 0


class RecentQueryItem(BaseModel):
    message_id: Optional[str] = None
    user_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    feature: Optional[str] = None
    form_key: Optional[str] = None
    query_preview: str = ""
    response_time_ms: int = 0
    tokens: int = 0
    created_at: Optional[datetime] = None
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    priced: bool = False
    cached_price_missing: bool = False


class QueryDetailResponse(BaseModel):
    message_id: Optional[str] = None
    user_name: Optional[str] = None
    query_preview: str = ""
    reply: str = ""
    response_time_ms: int = 0
    tokens: int = 0
    created_at: Optional[datetime] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    usage: Optional[dict[str, Any]] = None
    tools_used: list[dict[str, Any]] = Field(default_factory=list)


class TraceSpanItem(BaseModel):
    """One pipeline span (M2). Superset of all span-kind fields; unused ones
    are null for a given kind (LLM vs TOOL vs RETRIEVER etc)."""

    id: str
    parent_id: Optional[str] = None
    dotted_order: str = ""
    span_kind: str
    name: str = ""
    input_json: Optional[Any] = None
    output_json: Optional[Any] = None
    status: str = "ok"
    error: Optional[str] = None
    latency_ms: int = 0
    # LLM
    request_model: Optional[str] = None
    finish_reason: Optional[str] = None
    invocation_params: Optional[dict[str, Any]] = None
    tokens_in: int = 0
    tokens_out: int = 0
    prompt_name: Optional[str] = None
    prompt_version: Optional[int] = None
    # TOOL
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_args: Optional[Any] = None
    tool_result: Optional[Any] = None
    # RETRIEVER
    query: Optional[str] = None
    documents: Optional[Any] = None
    top_k: Optional[int] = None


class TraceResponse(BaseModel):
    """Root trace + ordered span list (M2)."""

    id: str
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    status: str = "ok"
    flagged: bool = False
    env: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    latency_ms: int = 0
    span_count: int = 0
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    spans: list[TraceSpanItem] = Field(default_factory=list)


class WishlistClusterItem(BaseModel):
    id: str
    representative_question: str
    category: Optional[str] = None
    count: int
    last_seen_at: Optional[datetime] = None
    created_at: datetime


class ModelPriceBody(BaseModel):
    """Update body. A price row's model never changes, so a `model` sent here is ignored."""

    model: Optional[str] = None
    input_usd_per_1m: float = Field(ge=0)
    cached_input_usd_per_1m: Optional[float] = Field(default=None, ge=0)
    output_usd_per_1m: float = Field(ge=0)
    note: Optional[str] = None


class ModelPriceCreate(ModelPriceBody):
    model: str = Field(min_length=1, max_length=128)


class ModelPriceResponse(BaseModel):
    id: str
    model: str
    input_usd_per_1m: float
    cached_input_usd_per_1m: Optional[float] = None
    output_usd_per_1m: float
    note: Optional[str] = None
    updated_at: datetime


class UnpricedModelItem(BaseModel):
    model: str
    calls: int
    last_seen_at: datetime
