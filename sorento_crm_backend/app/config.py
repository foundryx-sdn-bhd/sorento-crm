"""Configuration settings for the FastAPI application."""
import os
from pathlib import Path as _Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Union


def _resolve_settings_env_file() -> str:
    """Which dotenv Settings() reads.

    SORENTO_ENV_FILE overrides the default ".env" so a test suite can point
    Settings at a private dotenv instead of the backend's real .env, which a
    running dev server also reads (mirrors app.main._load_env_file, which
    does the equivalent for os.environ). This runs at CLASS-DEFINITION time
    (module import), before `settings = Settings()` below, which is why the
    override reaches pydantic-settings even though conftest imports
    app.database -> app.config long before app.main is ever imported.

    Resolved the same way app.main resolves its own ".env": relative to the
    backend directory (this file's grandparent), not the process cwd.

    Unlike app.main's loader, a SET-but-missing override raises here rather
    than silently falling back to the real .env - a typo in the override
    must never point a test run at the live stack's database.
    """
    override = os.environ.get("SORENTO_ENV_FILE")
    if not override:
        return ".env"
    candidate = _Path(override)
    if not candidate.is_absolute():
        candidate = _Path(__file__).resolve().parent.parent / candidate
    if not candidate.exists():
        raise RuntimeError(
            f"SORENTO_ENV_FILE={override!r} does not resolve to an existing "
            f"file (looked for {candidate}); refusing to fall back to .env."
        )
    return str(candidate)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str
    direct_url: str | None = None
    
    # JWT Authentication
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    
    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # CORS - accept as string and parse.
    # Default covers Next.js auto-bumped ports (3000 taken -> 3001 -> 3002 ...) so multiple
    # local instances do not need to override CORS_ORIGINS just to load.
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,"
        "http://localhost:3003,http://localhost:3004,http://localhost:3005"
    )
    
    @field_validator('cors_origins', mode='before')
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> str:
        """Parse CORS origins from comma-separated string or list."""
        if isinstance(v, list):
            return ','.join(v)
        return v
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Get CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(',') if origin.strip()]
    
    # Environment
    environment: str = "development"
    debug: bool = False

    # DEV-LOGIN-BYPASS (app/services/dev_login.py): passwordless sign-in for LOCAL test
    # copies. Off by default; the app refuses to start with it on outside a dev ENVIRONMENT.
    # DEV_AUTO_LOGIN_USERS is a comma list of existing user emails, first = default.
    # DEV_AUTO_LOGIN_SECRET (16+ chars) is shared with the frontend SERVER only.
    dev_auto_login: bool = False  # DEV_AUTO_LOGIN
    dev_auto_login_users: str = ""  # DEV_AUTO_LOGIN_USERS
    dev_auto_login_secret: str = ""  # DEV_AUTO_LOGIN_SECRET

    # Complaint <-> DO auto-fulfilment: which Complaint-team tiers (agent `complaint`,
    # set `complaint`) receive the replacement-DO-delivered email/in-app. Comma list,
    # e.g. "1,2" (Tier 1 + Tier 2) or "1" (Tier 1 only). COMPLAINT_DO_DELIVERED_NOTIFY_TIERS
    complaint_do_delivered_notify_tiers: str = "1,2"

    # Which spreadsheet extensions the SCM upload channels accept. Comma list, no dots.
    # SCM_UPLOAD_EXTENSIONS. Configurable because the format is the CUSTOMER's, not ours:
    # AutoCount's own "Purchase Order Listing With Detail" export is legacy BIFF `.xls`,
    # and refusing it means asking somebody to re-save 13 MB of history by hand before
    # they can load it. One list, read by the route that rejects and by the reader that
    # dispatches, so the two can never disagree about what is accepted.
    scm_upload_extensions: str = "xlsx,xlsm,xls"

    # Respond.io
    respond_api_key: str | None = None
    respond_base_url: str = "https://api.respond.io"
    respond_app_base_url: str = "https://app.respond.io"  # Base URL for inbox links (e.g. /space/{id}/inbox/{contact_id})
    respond_space_id: str | None = None
    
    # Shared secret the CRM puts on its DIRECT n8n webhook calls (respond-send-user,
    # respond-close-convo) as X-CRM-Webhook-Secret. N8N_CRM_WEBHOOK_SECRET. Unset =
    # the header is omitted, the send still goes out, and the n8n gate stays closed:
    # a misconfigured deploy degrades to "bot-pause inert", never to a blocked send
    # (UAC AC-J6).
    n8n_crm_webhook_secret: str | None = None

    # Direct webhook the CRM calls when a resolve closes the contact's LAST open
    # intervention ticket (UAC AC-M3). N8N_CLOSE_CONVO_WEBHOOK_URL. Unset = the
    # call is skipped with a warning and the resolve is unaffected.
    n8n_close_convo_webhook_url: str | None = None

    # S2b Retry (AC-705). The trace screen's Retry re-posts the ORIGINAL respond.io
    # webhook body to n8n's inject webhook, so the turn re-enters at the SAME ingress a
    # live message uses and gets the same ordering and the same lanes. The CRM never
    # sends to the customer itself (D9).
    #
    # The ingress URL and its key are NOT here. They live on the respond workspace ROW
    # and are edited on System > Respond Workspaces: that config is per tenant and an
    # environment variable does not scale past one (owner ruling, 5 Sep 2026; AC-804).
    # See `app/models/respond_workspace.py` for the two columns. What stays here is the
    # timing knob below, which is behaviour of the endpoint rather than a tenant address.
    #
    # How long a `retry_requested_at` marker blocks a second Retry. The marker is
    # cleared by the re-injected turn arriving; if it never arrives (n8n dropped it,
    # the contact was deleted) the row would otherwise be un-retryable forever, so an
    # older marker is treated as stale and the operator may try again.
    chatbot_retry_stale_minutes: int = 5  # CHATBOT_RETRY_STALE_MINUTES
    # How long a turn may sit `delegated` before the sweep calls it failed. A delegated
    # turn is one an n8n lane took over and is expected to finish by calling `/complete`;
    # when that lane dies mid-turn the call never comes and the row would stay `delegated`
    # forever - a ghost in the trace list that Retry (failed turns only, R4) cannot touch.
    chatbot_delegated_ttl_minutes: int = 10  # CHATBOT_DELEGATED_TTL_MINUTES

    # S7 MODE and the business lane's own switch are NOT here (AC-810). Both are columns
    # on the `system_settings` row now, toggled on System > Settings > Chatbot and read
    # per turn, because the owner turns them on and off while watching live turns and an
    # environment variable makes each flip a deploy. The two settings below stay here:
    # they are deployment properties of THIS box, not owner decisions.
    #
    # The longest a turn waits behind a predecessor that is still ALIVE (heart-beating; a
    # dead one is walked past within `dispatch.ALIVE_TTL_SECONDS`). Past it the turn runs
    # anyway, possibly out of order (CHATBOT-QUEUE-FIX, 1 Oct). The waiter's own reply
    # rides n8n's same 90 s turn budget, so the value is capped at
    # `dispatch.max_queue_wait_seconds()` (90 - 35 parser - 15 lane - 10 margin = 30 s);
    # a higher env value is clamped there, never honoured. CHATBOT_QUEUE_WAIT_SECONDS.
    chatbot_queue_wait_seconds: float = 30.0
    # Optional worker offload (AC-703). OFF by default: in-process is simpler and the
    # measured trigger for moving the turn off the API threads (beyond ~250 concurrent) has
    # not arrived. When true the request enqueues on the `chat` queue and waits for the
    # result, the pattern `/external/media` already uses. CHATBOT_TURN_ON_WORKER.
    chatbot_turn_on_worker: bool = False
    # How long the request waits for the offloaded turn before giving up on it.
    # CHATBOT_TURN_WAIT_SECONDS.
    chatbot_turn_wait_seconds: int = 60
    # Per-MCP-call bound for the chatbot's fetch step. The plan's capacity section says 10
    # seconds; the AI assistant's own 20 is a different budget for a different surface (a
    # user watching a screen, not a customer waiting inside a whole WhatsApp turn's latency
    # target). CHATBOT_MCP_TIMEOUT_SECONDS.
    chatbot_mcp_timeout_seconds: int = 10

    # External API Access
    external_api_key: str | None = None  # API key for external parties to access endpoints
    # When set, X-API-Key auth resolves RBAC as this users row (required for MCP/n8n read tools).
    external_api_key_act_as_user_id: str | None = None  # EXTERNAL_API_KEY_ACT_AS_USER_ID

    # AutoCount pull + review: the FoundryX gateway base URL and key are NOT env vars
    # (PLAN-foundryx-pull-connection-ui.md retires the two settings this comment used to
    # name) - they live on the seeded `foundryx-esb` `integrations` row, configured from
    # Integration Management. See `app/services/foundryx_autocount_client.py`.

    # Redis Queue (must match everywhere: API, workers, seed scripts; use same host:port/db)
    redis_url: str = "redis://localhost:6379/0"

    # Redis pub/sub channel for the conversation ticket server-push (UAC K, S4.2).
    # Namespaced because one Redis instance is shared with RQ and with every local
    # worktree; override per environment (CONVERSATION_EVENTS_CHANNEL) when two
    # stacks on one broker must not hear each other.
    conversation_events_channel: str = "sorento:conversation-events:v1"

    # Request idempotency (duplicate-submit / network-slowness backstop) - see
    # documentation/plans/PLAN-uniform-idempotency.md. Scoped to an allowlist of action endpoints
    # in app/middleware/idempotency_middleware.py.
    idempotency_enabled: bool = True            # IDEMPOTENCY_ENABLED
    # Ops kill switch for external-request telemetry. On by default; exists so a
    # write-path problem can be shut off without a deploy.
    api_call_log_enabled: bool = True           # API_CALL_LOG_ENABLED
    # Emergency off switch for audit capture (PLAN-audit-standard-26sep.md "Best-effort
    # capture"). Read on every write, never cached by the listeners; the env value itself is
    # loaded at process start, so set it and restart the API and worker, no deploy needed.
    # False means NO trail and no gap row for any write while it is off.
    audit_capture_enabled: bool = True          # AUDIT_CAPTURE_ENABLED
    idempotency_mode: str = "enforce"           # IDEMPOTENCY_MODE: "enforce" | "observe"
    idempotency_result_ttl: int = 10            # dedupe window seconds (a repeat within this is collapsed)
    idempotency_lock_ttl: int = 60              # in-flight lock seconds (must exceed max handler duration)
    idempotency_wait_ms: int = 2000             # how long a concurrent replay waits for the first, then 409
    idempotency_max_body: int = 262144          # skip dedupe for bodies larger than this (bytes)

    # Per-IP rate limits on unauthenticated/abuse-prone endpoints (fixed window,
    # fail-open if Redis down) - see PLAN-fix-security-cluster Sub-plan A. All
    # env-overridable; set max<=0 to disable a given limiter.
    rate_limit_signup_max: int = 3              # signups per window per IP
    rate_limit_signup_window_seconds: int = 3600
    rate_limit_reset_max: int = 5              # password-reset requests per window per IP
    rate_limit_reset_window_seconds: int = 900
    rate_limit_portal_otp_max: int = 30        # portal OTP requests per window per IP
    rate_limit_portal_otp_window_seconds: int = 60

    # identity S1 (#1280): an optional override for the WhatsApp template use
    # case a phone sign-in code sends under. Ships empty: owner ruling of
    # 27 Sep 2026 reuses the approved portal_otp template for CRM sign-in, so
    # no new template goes to Meta. Set it (e.g. "login_otp") only once an
    # approved template is mapped for that use case; an unmapped override
    # still falls back to "portal_otp" (send_login_otp_respond_message).
    phone_signin_otp_use_case: str = ""

    # Presigned-URL hardening (external API) - see PLAN-fix-security-cluster Sub-plan B.
    # When True, /external/presigned-url only signs a file_path that resolves to a
    # real attachments row (blocks signing arbitrary/guessed keys). Escape hatch:
    # set PRESIGNED_REQUIRE_ATTACHMENT_ROW=false if a legit n8n flow presigns a key
    # with no row yet. Max TTL clamps expires_in (URLs shouldn't outlive the action).
    presigned_require_attachment_row: bool = True
    presigned_max_ttl_seconds: int = 3600

    embedding_queue_name: str = "embeddings"
    # When True, scheduled task also drains pending rows from embedding_queue if Redis is empty of jobs.
    # Set False to use Redis (RQ) only; then REDIS_URL must be the instance where jobs were enqueued.
    embedding_queue_db_fallback_enabled: bool = True  # EMBEDDING_QUEUE_DB_FALLBACK_ENABLED
    embedding_max_retries: int = 5
    embedding_retry_backoff_seconds: int = 60
    embedding_chunk_size: int = 900
    embedding_chunk_overlap: int = 120
    embedding_provider: str = "openai"
    embedding_model_name: str = "text-embedding-3-small"
    embedding_model_version: str = "v1"
    embedding_dimensions: int = 1536
    openai_api_key: str | None = None
    # SCM M5 market research - Anthropic web-search backend (key-gated; the run
    # endpoint degrades to a 'failed' run row when unset, never crashes).
    anthropic_api_key: str | None = None
    # Google Gemini - env fallback for the DB-configured key (System > AI
    # Assistant). Read by the chatbot media image lane when Gemini is selected.
    gemini_api_key: str | None = None
    openai_embeddings_url: str = "https://api.openai.com/v1/embeddings"
    openai_chat_completions_url: str = "https://api.openai.com/v1/chat/completions"

    # Document extraction (customer PO scans, delivery-schedule matrices).
    # Separate from the AI assistant on purpose: a different provider wins on
    # this job (measured, PLAN-project-lead-to-so.md 5b) and it must be
    # switchable without touching assistant behaviour. Blank key = the feature
    # reports "extraction unavailable" instead of crashing an upload.
    gemini_api_key: str | None = None                       # GEMINI_API_KEY (secret)
    document_ai_provider: str = "gemini"                    # DOCUMENT_AI_PROVIDER
    document_ai_model: str = "gemini-2.5-flash"             # DOCUMENT_AI_MODEL
    document_ai_render_dpi: int = 170                       # DOCUMENT_AI_RENDER_DPI
    document_ai_page_limit: int = 40                        # DOCUMENT_AI_PAGE_LIMIT
    # How many pages read concurrently (19 Aug follow-up: "make it absolutely the
    # fastest"). The model call is a synchronous HTTP round trip per page and the
    # provider holds no shared mutable state, so a bounded thread pool is the whole
    # change; 8 is generous headroom over the 40-page ceiling above without opening
    # that many sockets to the provider at once.
    document_ai_page_concurrency: int = 8                    # DOCUMENT_AI_PAGE_CONCURRENCY

    # Project sales allocation (P9). The master location a sales order line is
    # sourced from first. Held as a warehouse CODE, not an id, because it is the
    # AutoCount location code people say out loud, and because all four sites run
    # a "-BB" bin (BRW-BB, DC1-BB, MWH-BB, WH3-BB) so the site prefix decides
    # which one is the master. No matching warehouse row means no `brw` candidate,
    # never a blank screen.
    project_allocation_brw_warehouse_code: str = "BRW-BB"

    # AI assistant
    ai_assistant_enabled: bool = True
    ai_assistant_mcp_url: str = "http://localhost:8765/mcp"
    ai_assistant_mcp_timeout_seconds: int = 20
    ai_assistant_tool_call_limit: int = 3
    # RAG: how many MCP tools to BIND to the agent per turn. Default 1 - 
    # deterministic single-tool resolution (the parser already fixes intent+domain,
    # so the top-1 candidate is the tool; the runners-up are kept in the trace as
    # is_current=false for visibility but are not bound). Raise to expose more.
    ai_assistant_rag_top_k: int = 1
    # Agent loop: max tool-calling iterations before forcing a final answer.
    ai_assistant_agent_max_iterations: int = 6
    # Reformulator: include the last N history messages (user+assistant) as context.
    ai_assistant_reformulator_history_turns: int = 6

    # Outline (user-guide knowledge base) base URL. INTERNAL Foundryx asset - 
    # its URL must never be surfaced to end users by the AI assistant. Used
    # here only to derive the host to STRIP from guide tool-results and from
    # the assistant's final answer (see ai_assistant_service redaction).
    outline_base_url: str = "https://doc.foundryx.my"

    # Ideation pipeline (ideate intent + Ideas iframe host). All .env-driven and
    # DORMANT when blank: absent values make the feature inert without touching
    # existing routes. Secrets are masked in any echo. create_idea is called over
    # HTTP (server-to-server httpx to ideation_shared_service_url) - NOT MCP; there
    # is deliberately no ideation_mcp_url (sorento_crm_mcp is read-only).
    ideation_shared_service_url: str | None = None   # IDEATION_SHARED_SERVICE_URL
    ideation_intake_api_key: str | None = None       # IDEATION_INTAKE_API_KEY (secret)
    ideation_embed_signing_secret: str | None = None  # IDEATION_EMBED_SIGNING_SECRET (secret)
    ideation_embed_connection_id: str | None = None  # IDEATION_EMBED_CONNECTION_ID
    ideation_embed_fe_base_url: str | None = None    # IDEATION_EMBED_FE_BASE_URL (iframe FE root)

    # Frontend app URL (for password reset and other links in emails)
    frontend_base_url: str | None = None
    # Fallback business WhatsApp number (E.164 digits, no '+') for the portal
    # verify page's wa.me escape hatch when the workspace has none configured.
    portal_whatsapp_number: str | None = None

    # Which RQ queue a report export is enqueued on. The default IS the production queue
    # the deployed worker drains; this exists so a developer verifying an export in a
    # worktree can point a private worker at a queue of their own, because RQ workers are
    # SHARED across worktrees on this machine and a sibling would otherwise steal the job
    # and render it against its own code.
    report_export_queue: str = "imports"  # REPORT_EXPORT_QUEUE

    # Feature flags (progressive rollout)
    notifications_v1_enabled: bool = True  # NOTIFICATIONS_V1_ENABLED
    # When False (default), module guards allow all API routes if tenant has no module rows yet (legacy).
    # When True, disabled modules return 403 from guarded routers.
    module_guard_strict: bool = False  # MODULE_GUARD_STRICT
    # DO pull (PLAN-autocount-do-pull-crm-30sep.md, owner Q4): when True, Confirm on a
    # delivery-orders pull is refused until BOTH Excel files have been compared with zero
    # differences; when False (default) the compare is advisory, as on Products / Stock.
    autocount_do_pull_confirm_requires_match: bool = False  # AUTOCOUNT_DO_PULL_CONFIRM_REQUIRES_MATCH

    model_config = SettingsConfigDict(
        env_file=_resolve_settings_env_file(),
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables (like AWS_* variables)
    )


# Values come from environment / .env at runtime; static analysis cannot infer required env vars.
settings = Settings()  # type: ignore[call-arg]
