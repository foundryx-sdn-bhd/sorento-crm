"""System settings API routes."""
import logging
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.access import RespondContact
from app.models.product import UnitOfMeasure
from app.models.user import SystemSetting, User, UserRole
from app.services.error_handler import handle_internal_error
from app.services.signin_background import resolve_signin_background_url

logger = logging.getLogger(__name__)

router = APIRouter()


class SystemSettingUpdate(BaseModel):
    name: Optional[str] = None
    logo: Optional[str] = None
    active: Optional[bool] = None
    address: Optional[str] = None
    website_url: Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = None
    currency_format: Optional[str] = None
    social_facebook: Optional[str] = None
    social_twitter: Optional[str] = None
    social_instagram: Optional[str] = None
    social_linkedin: Optional[str] = None
    social_pinterest: Optional[str] = None
    social_youtube: Optional[str] = None
    notify_stock_email: Optional[bool] = None
    notify_stock_web: Optional[bool] = None
    notify_stock_threshold: Optional[str] = None
    notify_stock_role_ids: Optional[list[str]] = None
    notify_new_order_email: Optional[bool] = None
    notify_new_order_web: Optional[bool] = None
    notify_new_order_role_ids: Optional[list[str]] = None
    notify_order_status_update_email: Optional[bool] = None
    notify_order_status_update_web: Optional[bool] = None
    notify_order_status_update_role_ids: Optional[list[str]] = None
    notify_payment_failure_email: Optional[bool] = None
    notify_payment_failure_web: Optional[bool] = None
    notify_payment_failure_role_ids: Optional[list[str]] = None
    notify_system_error_failure_email: Optional[bool] = None
    notify_system_error_web: Optional[bool] = None
    notify_system_error_role_ids: Optional[list[str]] = None
    complaint_do_delivered_notify_tiers: Optional[str] = None
    import_job_rows_retention_days: Optional[int] = Field(None, ge=1, le=3650)
    smtp_host: Optional[str] = None
    smtp_port: Optional[str] = None
    smtp_secure: Optional[bool] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None  # update-only; never returned in GET
    smtp_from: Optional[str] = None
    default_product_supplier_id: Optional[str] = None
    default_product_standard_lead_time_days: Optional[int] = Field(None, ge=0, le=10950)
    #: The unit a product gets when the source states none. `null` clears it back to the
    #: built-in `EA` fallback; an unknown id is a 400 rather than a silent fallback the
    #: operator would only discover through an import that quietly did something else.
    default_uom_id: Optional[str] = None
    # Takeover cooldown window in seconds (0 = instant). Cap at 1 hour.
    takeover_cooldown_seconds: Optional[int] = Field(None, ge=0, le=3600)
    # Project registration clash bars (AC-C5). Bounded to (0, 1] because a trigram
    # score is a ratio, and 0 would make every project clash with every other.
    project_clash_surface_threshold: Optional[float] = Field(None, gt=0, le=1)
    project_clash_block_threshold: Optional[float] = Field(None, gt=0, le=1)
    # Global default grace window for form-SLA actions. 0 = nothing defers, which is
    # what ships; a stage may override it (form_sla_configs.grace_seconds).
    form_sla_grace_seconds: Optional[int] = Field(None, ge=0, le=600)
    # PHOTO-SEND-CAP (owner Q1): days a chatbot file short link stays open. null = never,
    # which is the owner's choice; an explicit null clears it back to never.
    chatbot_file_link_expiry_days: Optional[int] = Field(None, ge=1, le=3650)
    # The two deferred-action windows (D16). `ge=1`, not `ge=0`: zero would apply a
    # delete with no way back, which is the confirmation dialog's failure mode wearing
    # the new model's clothes. Both must appear here AND in the GET dict below.
    deferred_delete_seconds: Optional[int] = Field(None, ge=1, le=600)
    deferred_action_seconds: Optional[int] = Field(None, ge=1, le=600)
    # r9 D10. Zero is meaningful here, unlike the countdowns above: it turns the
    # auto-collect sweep off and leaves the hand-over waiting for a person.
    price_tag_auto_collect_days: Optional[int] = Field(None, ge=0, le=90)
    n8n_attachment_webhook_url: Optional[str] = None
    n8n_crm_chat_outbound_webhook_url: Optional[str] = None
    n8n_stock_inquiry_revise_webhook_url: Optional[str] = None
    n8n_close_convo_webhook_url: Optional[str] = None
    purchase_request_default_approver_user_id: Optional[str] = None
    sponsorship_form_default_approver_user_id: Optional[str] = None
    # AI assistant trace (M2) retention + payload caps.
    ai_trace_ttl_days: Optional[int] = Field(None, ge=1, le=3650)
    ai_trace_error_ttl_days: Optional[int] = Field(None, ge=1, le=3650)
    ai_trace_max_payload_bytes: Optional[int] = Field(None, ge=512, le=1048576)
    ai_assistant_role_split_enabled: Optional[bool] = None
    # Form handling-lock (PLAN-form-handling-lock): the source_entity_types the lock is
    # enabled for. Stored as CSV; accepted/returned as a list. Empty = off everywhere.
    handling_lock_enabled_types: Optional[list[str]] = None
    # System-health observability (digest + watchdog alerts).
    health_digest_enabled: Optional[bool] = None
    health_alerts_enabled: Optional[bool] = None
    health_notify_role_ids: Optional[list[str]] = None
    health_notify_user_ids: Optional[list[str]] = None
    health_integration_fail_threshold: Optional[int] = Field(None, ge=1, le=100000)
    health_audit_volume_floor: Optional[int] = Field(None, ge=0, le=1000000)
    # WhatsApp round-trip latency SLA (S4). Must appear in BOTH this schema and
    # the GET dict below - inheriting the column is not enough, the GET builds a
    # manual dict and silently drops anything not listed there.
    chat_latency_p99_target_seconds: Optional[int] = Field(None, ge=1, le=3600)
    chat_latency_percentile: Optional[int] = Field(None, ge=1, le=99)
    chat_latency_ceiling_multiplier: Optional[int] = Field(None, ge=1, le=100)
    chat_latency_no_reply_minutes: Optional[int] = Field(None, ge=1, le=1440)
    chat_latency_min_sample: Optional[int] = Field(None, ge=1, le=100000)
    # Portal submission revisions: global kill switch + fallback cap. Per-type
    # overrides live in portal_revision_configs. Same rule as the latency block above -
    # must appear here AND in the GET dict.
    portal_revisions_enabled: Optional[bool] = None
    portal_max_revisions: Optional[int] = Field(None, ge=0, le=50)
    # SCM front planning: the admin plan-grain policy (plan 5.1, AC-F01). A Literal, so
    # anything but the two grains is a 422 before it can reach the column. Same rule as
    # the blocks above - it must appear HERE and in the GET dict, because both are manual.
    plan_grain: Optional[Literal["product", "location"]] = None
    # Cost price from the supplier (#1288, AC-S2-20). Off by default (first rollout);
    # on, a staff upload goes through Submit/Decide/Return before Apply. Same rule again -
    # must appear here AND in the GET dict below.
    cost_price_verification_enabled: Optional[bool] = None
    # Sample requests (SR-CORE, card rule 2): soft-transition switch, OFF on deploy. Must appear here, in the GET dict, and in AppConfigResponse below.
    sample_request_require_project: Optional[bool] = None
    # Chatbot media endpoint (PLAN-chatbot-media-endpoint section 2.4). Same rule
    # again - every one of these must ALSO appear in the GET dict below.
    #
    # The two wait bounds are enforced HERE, not only in the settings form: a
    # number that only the UI refuses is not a constraint. The 110 ceiling exists
    # so even a maximally misconfigured pair cannot exceed the dispatcher's 120
    # second lock TTL, and the >= relationship between them is checked below so a
    # job that outlives the sync wait still finishes rather than being killed
    # mid-flight.
    media_image_monthly_limit: Optional[int] = Field(None, ge=0, le=100000)
    media_voice_monthly_limit: Optional[int] = Field(None, ge=0, le=100000)
    media_voice_max_seconds: Optional[int] = Field(None, ge=1, le=3600)
    media_burst_limit: Optional[int] = Field(None, ge=1, le=1000)
    media_burst_window_seconds: Optional[int] = Field(None, ge=1, le=3600)
    media_warn_threshold_percent: Optional[int] = Field(None, ge=1, le=100)
    media_image_provider: Optional[str] = None
    media_image_model: Optional[str] = None
    media_image_degraded_model: Optional[str] = None
    media_transcribe_model: Optional[str] = None
    # Voice's own degraded tier. NULL means the voice quota is a hard refusal
    # rather than a degrade - image was measured and is seeded, voice was not.
    media_voice_degraded_model: Optional[str] = None
    # Three modes and no others: `language_strategy()` builds a different request
    # shape per mode and silently treats anything unrecognised as `pinned`, so a
    # typo would look like the setting had been ignored rather than refused.
    media_language_mode: Optional[str] = Field(None, pattern="^(pinned|hints|auto)$")
    media_language_pinned: Optional[str] = None
    media_language_hints: Optional[str] = None
    media_sync_wait_seconds: Optional[int] = Field(None, ge=5, le=90)
    media_extraction_timeout_seconds: Optional[int] = Field(None, ge=5, le=110)
    media_max_entities: Optional[int] = Field(None, ge=1, le=100)
    # How long the low stock report route holds a chat turn open before it answers
    # `pending` and leaves delivery to the worker push (PLAN-low-stock-report S5, AC-43).
    # The same 5..90 band the media wait carries, and for the same reason: a turn held
    # longer than the chatbot's own queue-wait budget is a turn nobody is waiting on.
    low_stock_sync_wait_seconds: Optional[int] = Field(None, ge=5, le=90)
    # The chatbot report engine's cap (lane REPORT-RATE-LIMIT-CONFIG). 0 = no limit.
    report_ask_rate_limit: Optional[int] = Field(None, ge=0, le=1000)
    report_ask_rate_window_minutes: Optional[int] = Field(None, ge=1, le=1440)
    chatbot_stock_denial_enabled: Optional[bool] = None
    # PLAN-local-buy-routing-toggle.md: local-supplier Buy routing is a switch, off
    # by default. Off means the rule does not run at all (no pill, every Buy raises).
    local_buy_routing_enabled: Optional[bool] = None
    # PLAN-borrow-location-only-8oct.md D1: "Borrow from another order", off by default.
    borrow_from_order_enabled: Optional[bool] = None
    # AC-304 (D5): the unsupported-domain list. `List[str]`, so an owner cannot save a
    # bare string that would then be iterated one CHARACTER at a time by the route's
    # membership test.
    chatbot_unsupported_domains: Optional[List[str]] = None
    # A7 (chatbot-growth-r1): the cross-domain probe ladder, per origin domain -
    # {"inventory": ["incoming", "purchase_order"], "incoming": ["inventory"]} by default.
    chatbot_crossdomain_ladder: Optional[Dict[str, List[str]]] = None
    # Chatbot turn re-architecture (AC-1502): the tier order, one copy.
    chatbot_tier_order: Optional[List[str]] = None
    # Chatbot turn re-architecture (AC-1513): the Memory card, one JSONB.
    chatbot_memory: Optional[Dict[str, Any]] = None
    # Which chatbot lanes the CRM may FINISH, by `branch_kind`. `[]` (the default) means
    # none, and every turn delegates to n8n exactly as today. Validated as a list of
    # strings only: an unknown branch kind is the ENGINE's problem to ignore-and-warn, not
    # this endpoint's to reject, because the vocabulary grows slice by slice and a settings
    # form that rejects tomorrow's lane name is a support ticket.
    chatbot_completed_lanes: Optional[list[str]] = None
    # AC-810: the two switches that used to be environment flags. Same rule as every block
    # above - they must appear HERE and in the GET dict, because both are manual.
    chatbot_business_lane_enabled: Optional[bool] = None
    chatbot_ordering_enabled: Optional[bool] = None
    # CHAT-SPEED: the direct-ingress switch. A Literal, so a mode outside the three answers
    # 422 instead of being stored and read as off. Same rule as every block above - it must
    # appear HERE and in the GET dict, because both are manual.
    chatbot_direct_ingress_mode: Optional[Literal["off", "contacts", "all"]] = None
    chatbot_direct_ingress_contacts: Optional[str] = Field(None, max_length=20000)
    # Price tag packages (D2): the product classes a request line is WARNED about
    # when it reaches marketing without its catalogue package. Same rule as every
    # block above - it must appear HERE and in the GET dict, because both are manual.
    price_tag_guarded_classes: Optional[list[str]] = None
    # PLAN-oi-request-cs-reserve.md section 6c (F1): the reserve dialog's own default
    # Location, owner-configurable. `null` clears it back to "no configured default"
    # (the dialog then falls back to the row's own site pool, R3). Same rule as every
    # block above - it must appear HERE and in the GET dict, because both are manual.
    oi_reserve_default_pool_warehouse_id: Optional[str] = None


class ChatbotLane(BaseModel):
    """One branch kind on the Chatbot settings screen (AC-809).

    `built` is what the SCREEN needs and the vocabulary alone cannot say: the three
    business arms only run when `chatbot_business_lane_enabled` is on, so checking one
    while that switch is off does nothing at all. The checkbox is disabled instead of
    letting the owner discover it by pressing Save.
    """

    kind: str
    built: bool


class SmtpTestResult(BaseModel):
    success: bool
    message: str


class AppConfigResponse(BaseModel):
    """The non-sensitive slice of the system settings singleton.

    A short list, and the model is what makes "and nothing else" enforceable:
    anything not declared here is dropped on serialization rather than leaking
    because a dict builder grew a line. Do NOT extend it - see the route below.
    """

    currency: Optional[str] = None
    currency_format: Optional[str] = None
    purchase_request_default_approver_user_id: Optional[str] = None
    purchase_request_default_approver_email: Optional[str] = None
    sponsorship_form_default_approver_user_id: Optional[str] = None
    sponsorship_form_default_approver_email: Optional[str] = None
    #: r9 D10. A number of days, read by the price tag detail card.
    price_tag_auto_collect_days: Optional[int] = None
    #: PLAN-oi-request-cs-reserve.md section 6c (F1): the reserve dialog's own default
    #: Location must reach every `projects.order_inquiries.reserve` holder (Eling, CS),
    #: who does not hold `user_management.settings.view` - the same reason
    #: `price_tag_auto_collect_days` is here. A warehouse id, never rendered as text -
    #: only matched against the dialog's own resolved pool options.
    oi_reserve_default_pool_warehouse_id: Optional[str] = None
    #: SR-CORE: a salesperson's sample request form must know whether to require a
    #: registered project, and holds no `user_management.settings.view`.
    sample_request_require_project: bool = False


@router.get("/")
async def get_settings(
    current_user: dict = Depends(require_permission("user_management.settings.view")),
    db: Session = Depends(get_db)
):
    """Get system settings and roles."""
    try:
        settings = db.query(SystemSetting).first()
        roles = db.query(UserRole).order_by(UserRole.name.asc()).all()
        
        smtp_response = None
        if settings:
            smtp_response = {
                "smtp_host": settings.smtp_host,
                "smtp_port": settings.smtp_port,
                "smtp_secure": settings.smtp_secure,
                "smtp_username": settings.smtp_username,
                "smtp_from": settings.smtp_from,
                "smtp_password": None,  # never return raw password
            }
        pr_uid = getattr(settings, "purchase_request_default_approver_user_id", None) if settings else None
        sf_uid = getattr(settings, "sponsorship_form_default_approver_user_id", None) if settings else None
        user_pr = db.query(User).filter(User.id == pr_uid).first() if pr_uid else None
        user_sf = db.query(User).filter(User.id == sf_uid).first() if sf_uid else None
        # The default unit's CODE as well as its id: the settings screen may not render a
        # bare UUID, and the id alone would leave it with nothing to show until the units
        # master happens to be loaded beside it.
        default_uom_id = getattr(settings, "default_uom_id", None) if settings else None
        default_uom = (
            db.query(UnitOfMeasure).filter(UnitOfMeasure.id == default_uom_id).first()
            if default_uom_id
            else None
        )
        return {
            "settings": {
                "id": settings.id if settings else None,
                "name": settings.name if settings else None,
                "logo": settings.logo if settings else None,
                # A new settings column reaches the FE only if it is on this manual dict.
                # Signed here, not raw: the stored value is a non-signed CDN URL and the
                # settings screen renders it straight into an <img>.
                "signin_background": resolve_signin_background_url(settings),
                "active": settings.active if settings else None,
                "address": settings.address if settings else None,
                "website_url": settings.website_url if settings else None,
                "support_email": settings.support_email if settings else None,
                "support_phone": settings.support_phone if settings else None,
                "language": settings.language if settings else None,
                "timezone": settings.timezone if settings else None,
                "currency": settings.currency if settings else None,
                "currency_format": settings.currency_format if settings else None,
                "default_product_supplier_id": settings.default_product_supplier_id if settings else None,
                "default_product_standard_lead_time_days": (
                    settings.default_product_standard_lead_time_days if settings else None
                ),
                # A new settings column reaches the FE only if it is on this manual dict
                # AND on `SystemSettingUpdate` above.
                "default_uom_id": default_uom_id,
                "default_uom_code": default_uom.uom_code if default_uom else None,
                "form_sla_grace_seconds": (
                    getattr(settings, "form_sla_grace_seconds", 0) if settings else 0
                ),
                # PHOTO-SEND-CAP: null = chatbot file links never expire.
                "chatbot_file_link_expiry_days": (
                    getattr(settings, "chatbot_file_link_expiry_days", None) if settings else None
                ),
                # The two deferred-action windows (D16). Defaults stated here as well as
                # on the column, so a settings row that predates the migration still
                # renders 10 and 5 rather than an empty field.
                "deferred_delete_seconds": (
                    getattr(settings, "deferred_delete_seconds", 10) or 10
                    if settings
                    else 10
                ),
                "deferred_action_seconds": (
                    getattr(settings, "deferred_action_seconds", 5) or 5
                    if settings
                    else 5
                ),
                # A new settings column reaches the FE only if it is in this
                # manual dict too (LESSONS).
                "price_tag_auto_collect_days": (
                    getattr(settings, "price_tag_auto_collect_days", 7)
                    if settings
                    else 7
                ),
                "takeover_cooldown_seconds": (
                    getattr(settings, "takeover_cooldown_seconds", 60) if settings else None
                ),
                # float() because the column is NUMERIC, which psycopg2 hands back as
                # Decimal -- and Decimal is not JSON-serialisable.
                "project_clash_surface_threshold": (
                    float(getattr(settings, "project_clash_surface_threshold", 0.55) or 0.55)
                    if settings
                    else None
                ),
                "project_clash_block_threshold": (
                    float(getattr(settings, "project_clash_block_threshold", 0.70) or 0.70)
                    if settings
                    else None
                ),
                "purchase_request_default_approver_user_id": pr_uid,
                "purchase_request_default_approver_name": user_pr.name if user_pr else None,
                "purchase_request_default_approver_email": user_pr.email if user_pr else None,
                "sponsorship_form_default_approver_user_id": sf_uid,
                "sponsorship_form_default_approver_name": user_sf.name if user_sf else None,
                "sponsorship_form_default_approver_email": user_sf.email if user_sf else None,
                "n8n_attachment_webhook_url": getattr(settings, "n8n_attachment_webhook_url", None)
                if settings
                else None,
                "n8n_crm_chat_outbound_webhook_url": getattr(
                    settings, "n8n_crm_chat_outbound_webhook_url", None
                )
                if settings
                else None,
                "n8n_stock_inquiry_revise_webhook_url": getattr(
                    settings, "n8n_stock_inquiry_revise_webhook_url", None
                )
                if settings
                else None,
                "n8n_close_convo_webhook_url": getattr(
                    settings, "n8n_close_convo_webhook_url", None
                )
                if settings
                else None,
                "complaint_do_delivered_notify_tiers": getattr(
                    settings, "complaint_do_delivered_notify_tiers", "1,2"
                )
                if settings
                else None,
                "import_job_rows_retention_days": getattr(
                    settings, "import_job_rows_retention_days", 90
                )
                if settings
                else None,
                "ai_trace_ttl_days": getattr(settings, "ai_trace_ttl_days", 30) if settings else None,
                "ai_trace_error_ttl_days": getattr(settings, "ai_trace_error_ttl_days", 90) if settings else None,
                "ai_trace_max_payload_bytes": getattr(settings, "ai_trace_max_payload_bytes", 16384) if settings else None,
                "ai_assistant_role_split_enabled": getattr(settings, "ai_assistant_role_split_enabled", False) if settings else None,
                "handling_lock_enabled_types": (
                    [
                        t.strip()
                        for t in str(getattr(settings, "handling_lock_enabled_types", "") or "").split(",")
                        if t.strip()
                    ]
                    if settings
                    else []
                ),
                "health_digest_enabled": getattr(settings, "health_digest_enabled", True) if settings else None,
                "health_alerts_enabled": getattr(settings, "health_alerts_enabled", True) if settings else None,
                "health_notify_role_ids": getattr(settings, "health_notify_role_ids", None) if settings else None,
                "health_notify_user_ids": getattr(settings, "health_notify_user_ids", None) if settings else None,
                "health_integration_fail_threshold": getattr(settings, "health_integration_fail_threshold", 10) if settings else None,
                "health_audit_volume_floor": getattr(settings, "health_audit_volume_floor", 0) if settings else None,
                "chat_latency_p99_target_seconds": getattr(settings, "chat_latency_p99_target_seconds", 10) if settings else None,
                "chat_latency_percentile": getattr(settings, "chat_latency_percentile", 99) if settings else None,
                "chat_latency_ceiling_multiplier": getattr(settings, "chat_latency_ceiling_multiplier", 3) if settings else None,
                "chat_latency_no_reply_minutes": getattr(settings, "chat_latency_no_reply_minutes", 5) if settings else None,
                "chat_latency_min_sample": getattr(settings, "chat_latency_min_sample", 30) if settings else None,
                "portal_revisions_enabled": getattr(settings, "portal_revisions_enabled", True) if settings else None,
                "portal_max_revisions": getattr(settings, "portal_max_revisions", 2) if settings else None,
                # Rollout default (plan 5.1): a row saved before the column existed reads
                # as Product rather than as "no policy", which has no meaning here.
                "plan_grain": (getattr(settings, "plan_grain", None) or "product") if settings else None,
                "cost_price_verification_enabled": (
                    bool(getattr(settings, "cost_price_verification_enabled", False)) if settings else False
                ),
                "sample_request_require_project": (
                    bool(getattr(settings, "sample_request_require_project", False)) if settings else False
                ),
                # Chatbot media. NULL is meaningful for the three model columns:
                # provider/model inherit the AIAssistantConfig row, and a NULL
                # degraded model means the monthly quota is a hard stop rather
                # than an accepted-but-degraded read.
                "media_image_monthly_limit": getattr(settings, "media_image_monthly_limit", 50) if settings else None,
                "media_voice_monthly_limit": getattr(settings, "media_voice_monthly_limit", 100) if settings else None,
                "media_voice_max_seconds": getattr(settings, "media_voice_max_seconds", 120) if settings else None,
                "media_burst_limit": getattr(settings, "media_burst_limit", 5) if settings else None,
                "media_burst_window_seconds": getattr(settings, "media_burst_window_seconds", 60) if settings else None,
                "media_warn_threshold_percent": getattr(settings, "media_warn_threshold_percent", 80) if settings else None,
                "media_image_provider": getattr(settings, "media_image_provider", None) if settings else None,
                "media_image_model": getattr(settings, "media_image_model", None) if settings else None,
                "media_image_degraded_model": getattr(settings, "media_image_degraded_model", None) if settings else None,
                "media_transcribe_model": getattr(settings, "media_transcribe_model", "whisper-1") if settings else None,
                "media_voice_degraded_model": getattr(settings, "media_voice_degraded_model", None) if settings else None,
                "media_language_mode": getattr(settings, "media_language_mode", "pinned") if settings else None,
                "media_language_pinned": getattr(settings, "media_language_pinned", "en") if settings else None,
                "media_language_hints": getattr(settings, "media_language_hints", "en,ms,zh") if settings else None,
                "media_sync_wait_seconds": getattr(settings, "media_sync_wait_seconds", 30) if settings else None,
                "media_extraction_timeout_seconds": getattr(settings, "media_extraction_timeout_seconds", 45) if settings else None,
                "media_max_entities": getattr(settings, "media_max_entities", 10) if settings else None,
                # PLAN-low-stock-report S5 (AC-43/AC-48): in BOTH manual builders, or the
                # System Settings screen never sees the column at all.
                "low_stock_sync_wait_seconds": getattr(settings, "low_stock_sync_wait_seconds", 40) if settings else None,
                "report_ask_rate_limit": getattr(settings, "report_ask_rate_limit", 10) if settings else None,
                "report_ask_rate_window_minutes": getattr(settings, "report_ask_rate_window_minutes", 10) if settings else None,
                "chatbot_stock_denial_enabled": getattr(settings, "chatbot_stock_denial_enabled", False) if settings else None,
                # PLAN-local-buy-routing-toggle.md: BOTH manual builders, or the setting
                # never reaches the General settings screen at all.
                "local_buy_routing_enabled": getattr(settings, "local_buy_routing_enabled", False) if settings else None,
                "borrow_from_order_enabled": getattr(settings, "borrow_from_order_enabled", False) if settings else None,
                "chatbot_unsupported_domains": getattr(settings, "chatbot_unsupported_domains", None) if settings else None,
                "chatbot_crossdomain_ladder": getattr(settings, "chatbot_crossdomain_ladder", None) if settings else None,
                "chatbot_tier_order": getattr(settings, "chatbot_tier_order", None) if settings else None,
                # Chatbot memory lane A (contract section 5): `own_level_count` is a
                # LIVE count of a DIFFERENT table, never stored on this row - merged
                # in here so the GET response carries exactly {enabled, default_level,
                # own_level_count}.
                "chatbot_memory": (
                    {
                        **(getattr(settings, "chatbot_memory", None) or {}),
                        "own_level_count": _chatbot_memory_own_level_count(db),
                    }
                    if settings
                    else None
                ),
                "chatbot_completed_lanes": getattr(settings, "chatbot_completed_lanes", None) or [] if settings else None,
                "chatbot_business_lane_enabled": getattr(settings, "chatbot_business_lane_enabled", False) if settings else None,
                "chatbot_ordering_enabled": getattr(settings, "chatbot_ordering_enabled", False) if settings else None,
                "chatbot_direct_ingress_mode": getattr(settings, "chatbot_direct_ingress_mode", None) or "off" if settings else None,
                "chatbot_direct_ingress_contacts": getattr(settings, "chatbot_direct_ingress_contacts", None) if settings else None,
                "price_tag_guarded_classes": getattr(settings, "price_tag_guarded_classes", None) or [] if settings else None,
                # PLAN-oi-request-cs-reserve.md section 6c (F1): BOTH manual builders,
                # or the setting never reaches the General settings screen at all.
                "oi_reserve_default_pool_warehouse_id": (
                    getattr(settings, "oi_reserve_default_pool_warehouse_id", None)
                    if settings
                    else None
                ),
                "smtp": smtp_response,
            } if settings else None,
            "roles": [{"id": r.id, "name": r.name} for r in roles]
        }
    except Exception as e:
        raise handle_internal_error(str(e))


# Declared immediately after `GET /` and ahead of every other GET in this file so a
# path-parameter route added later (e.g. `/{section}`) cannot shadow this static path.
@router.get("/app-config", response_model=AppConfigResponse)
async def get_app_config(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The narrow, non-sensitive projection of system settings that any authenticated
    user may read.

    It exists because `GET /settings/` is gated on `user_management.settings.view` -
    that is where the full blob lives, and it stays there - while three consumers on
    screens outside user-management legitimately need a handful of harmless fields:
    `useCurrencyFormat` (currency format string), `use-excel-accept`, and
    `PurchaseRequestDetail` (the configured default approver it falls back to when
    sending an approval link, which is why the two approver EMAILS are here).

    NOTHING SENSITIVE MAY EVER BE ADDED TO THIS ROUTE. Not SMTP config, not the n8n
    webhook URLs (they are bearer-capability URLs), not the tenant roles list, not
    `health_notify_role_ids` / `health_notify_user_ids`, not the AI trace config. If a
    caller needs any of those, it needs `user_management.settings.view` and the full
    blob - do not widen this projection. The approver NAMES are deliberately absent
    too: nothing reads them.
    """
    try:
        settings = db.query(SystemSetting).first()
        pr_uid = getattr(settings, "purchase_request_default_approver_user_id", None) if settings else None
        sf_uid = getattr(settings, "sponsorship_form_default_approver_user_id", None) if settings else None
        user_pr = db.query(User).filter(User.id == pr_uid).first() if pr_uid else None
        user_sf = db.query(User).filter(User.id == sf_uid).first() if sf_uid else None
        return AppConfigResponse(
            currency=settings.currency if settings else None,
            currency_format=settings.currency_format if settings else None,
            purchase_request_default_approver_user_id=pr_uid,
            purchase_request_default_approver_email=user_pr.email if user_pr else None,
            sponsorship_form_default_approver_user_id=sf_uid,
            sponsorship_form_default_approver_email=user_sf.email if user_sf else None,
            # r9 D10: the CRM price tag card says when a hand-over auto-collects,
            # and marketing does not hold `user_management.settings.view`. A
            # number of days is not sensitive.
            price_tag_auto_collect_days=(
                getattr(settings, "price_tag_auto_collect_days", 7) if settings else 7
            ),
            # PLAN-oi-request-cs-reserve.md section 6c (F1): the reserve dialog's own
            # default pool must reach every `projects.order_inquiries.reserve` holder,
            # who does not hold `user_management.settings.view`.
            oi_reserve_default_pool_warehouse_id=(
                getattr(settings, "oi_reserve_default_pool_warehouse_id", None)
                if settings
                else None
            ),
            sample_request_require_project=bool(
                getattr(settings, "sample_request_require_project", False)
            ),
        )
    except Exception as e:
        raise handle_internal_error(str(e))


# SETTINGS-IN-DB-SORENTO: the settings that used to be prod env vars. Their own slug, not
# `.edit`: that one is also held by warehouse_manager and the integration API-key roles, and
# the webhook secret decides who may write chat history. See app/services/runtime_settings.py.
RUNTIME_SETTINGS_PERMISSION = "user_management.settings.secrets"


_RUNTIME_MAX_LENGTH = {
    "connector_ss_to_product_secret": 1000,
    "connector_product_to_ss_secret": 1000,
    "connector_connection_id": 255,
    "connector_ss_callback_url": 2048,
    "analytics_ro_database_url": 2000,
    "respond_webhook_secret": 1000,
}
_RO_DSN_SCHEMES = ("postgresql://", "postgresql+psycopg2://", "postgres://")


def _refused_date(name: str, value: str) -> Optional[str]:
    """Why a date setting is refused, or None. ISO text only (the box sends YYYY-MM-DD), and
    never after today in Malaysia time: the DO cutover marks every DO dated before it delivered,
    so a future date would land today's live DOs delivered with no tracking."""
    import re
    from datetime import date

    from app.services.incoming_stock_service import today_myt

    text = value.strip()
    if not text:
        return None
    try:
        day = date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    except ValueError:
        day = None
    if day is None:
        return f"{name} must be a date (YYYY-MM-DD) or null."
    if day > today_myt():
        return f"{name} cannot be a future date."
    return None


def _row_without_secrets(settings: SystemSetting) -> dict:
    """The settings row for a PUT's echo, minus every stored secret: the runtime secrets'
    ciphertext (SETTINGS-IN-DB-SORENTO review) and the SMTP password. Returning the ORM row
    itself serialised every column to any caller of these routes."""
    from sqlalchemy import inspect as sa_inspect

    return {
        attr.key: getattr(settings, attr.key)
        for attr in sa_inspect(SystemSetting).column_attrs
        if not attr.key.endswith("_ciphertext") and attr.key != "smtp_password"
    }


def _validate_runtime_payload(payload: Any) -> dict:
    """Omitted = keep; null or "" = clear (env applies again); a value = save it.

    Checked here rather than by a Pydantic model on purpose: the app's 422 handler logs each
    Pydantic error with its `input`, which for these fields is a secret or a DSN carrying a
    password (security review M1). Every refusal below names the field, never the value."""
    def _refuse(message: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)

    if not isinstance(payload, dict):
        raise _refuse("Expected a JSON object.")
    from app.services import runtime_settings

    unknown = sorted(k for k in payload if k not in runtime_settings.FIELDS)
    if unknown:
        raise _refuse(f"Unknown settings: {', '.join(str(k)[:64] for k in unknown)}")
    for name, value in payload.items():
        if value is None:
            continue
        if name in runtime_settings.BOOL_FIELDS:
            if not isinstance(value, bool):
                raise _refuse(f"{name} must be true, false or null.")
            continue
        if name in runtime_settings.DATE_FIELDS:
            refused = (_refused_date(name, value) if isinstance(value, str)
                       else f"{name} must be a date (YYYY-MM-DD) or null.")
            if refused:
                raise _refuse(refused)
            continue
        if not isinstance(value, str):
            raise _refuse(f"{name} must be text or null.")
        if len(value) > _RUNTIME_MAX_LENGTH[name]:
            raise _refuse(f"{name} is longer than {_RUNTIME_MAX_LENGTH[name]} characters.")
        if name == "respond_webhook_secret":
            from app.api.v1.public.respond_webhook import MAX_KEYS, MIN_KEY_LENGTH, split_keys

            keys = split_keys(value)
            # Name the field, never the value: the message is echoed to the browser.
            if any(len(key) < MIN_KEY_LENGTH for key in keys):
                raise _refuse(
                    f"{name}: every signing key must be at least {MIN_KEY_LENGTH} characters."
                )
            if len(keys) > MAX_KEYS:
                raise _refuse(f"{name}: at most {MAX_KEYS} signing keys.")
        text = value.strip().lower()
        if text and name == "connector_ss_callback_url" and not text.startswith("https://"):
            raise _refuse("The callback URL must start with https://")
        if text and name == "analytics_ro_database_url" and not text.startswith(_RO_DSN_SCHEMES):
            raise _refuse("The read-only database URL must be a postgresql:// URL.")
    return payload


def _runtime_settings_body(db: Session) -> dict:
    from app.api.v1.public.respond_webhook import SIGNATURE_HEADERS
    from app.models.order import OrderStatus
    from app.services import runtime_settings

    return {
        "fields": runtime_settings.describe(db),
        # The DO ingest's own lookup (`AutocountDocIngestService._delivered_status_id`): without
        # this status "Assume DOs delivered before" only logs a warning and DOs land new.
        "delivered_order_status_exists": db.query(OrderStatus.id)
        .filter(OrderStatus.status_code.ilike("delivered"))
        .first() is not None,
        "respond_webhook_path": "/api/v1/public/respond/webhook",
        "respond_signature_headers": list(SIGNATURE_HEADERS),
        # `respond_webhook.SHARED_SECRET_HEADER`, in the casing Respond.io's form is given.
        "respond_shared_secret_header": "X-Respond-Webhook-Secret",
    }


@router.get("/runtime")
async def get_runtime_settings(
    current_user: dict = Depends(require_permission(RUNTIME_SETTINGS_PERMISSION)),
    db: Session = Depends(get_db),
):
    """The env-fallback runtime settings with their source; secrets masked (last 4 only)."""
    return _runtime_settings_body(db)


@router.put("/runtime")
async def update_runtime_settings(
    payload: Any = Body(...),
    current_user: dict = Depends(require_permission(RUNTIME_SETTINGS_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Save the provided runtime settings; one audit row per save, never a secret value."""
    from app.audit_context import get_actor
    from app.services import runtime_settings

    # The person at the keyboard: the admin, not the target, under impersonation.
    actor = get_actor(db)
    actor_id = (actor.real_user_id or actor.user_id) if actor else None
    runtime_settings.update(db, _validate_runtime_payload(payload), actor_id or current_user["id"])
    return _runtime_settings_body(db)


def _chatbot_memory_own_level_count(db: Session) -> int:
    """Read-only: how many contacts carry their own `chatbot_memory_level`, for the
    Memory card's "Contacts with their own level" line (chatbot memory lane A,
    contract section 2/5). Never stored on the settings row - it is a live count of a
    DIFFERENT table, not a setting."""
    return (
        db.query(RespondContact)
        .filter(RespondContact.chatbot_memory_level.isnot(None))
        .count()
    )


def _completed_lane_vocabulary() -> frozenset[str]:
    """The branch kinds the chatbot BUILD can finish, read at call time.

    Through the chatbot MODULE's own surface (`app/modules/chatbot/lane_vocabulary.py`),
    never `app/services/chatbot/` directly: this file is core, and core importing the
    package is what AC-002 forbids and `tests/chatbot/test_import_boundary.py` fails on.
    Lazily imported, the same shape `handling_lock_enabled_types` reads `FORM_SLA_TYPES`
    with, and the module reads the attribute off `contracts` at call time so a slice that
    adds a lane needs no edit here.
    """
    from app.modules.chatbot.lane_vocabulary import completed_lane_kinds

    return completed_lane_kinds()


@router.get("/chatbot-lanes", response_model=List[ChatbotLane])
async def get_chatbot_lanes(
    current_user: dict = Depends(require_permission("user_management.settings.view")),
    db: Session = Depends(get_db),
):
    """The lane vocabulary the Chatbot settings screen renders (AC-809).

    Declared ahead of every path-parameter route in this file, for the same reason
    `/app-config` is: a `/{section}` added later must not shadow a static path.

    The vocabulary is `contracts.CRM_COMPLETED_BRANCH_KINDS` and nothing else - the same
    set `PUT /general` validates against, so the screen can never offer a lane the save
    would refuse. `built` carries the one extra condition the vocabulary does not express:
    the three business arms need `chatbot_business_lane_enabled` on before checking them
    changes anything.
    """
    try:
        from app.modules.chatbot.lane_vocabulary import lane_options

        settings = db.query(SystemSetting).first()
        return [
            ChatbotLane(kind=kind, built=built)
            for kind, built in lane_options(
                business_lane_enabled=bool(
                    getattr(settings, "chatbot_business_lane_enabled", False)
                )
            )
        ]
    except Exception as e:
        raise handle_internal_error(str(e))


# The SMTP login on `SystemSettingUpdate`. Written ONLY through `PUT /smtp`, which carries
# the secrets slug; `/general` refuses them (SETTINGS-PERM-GATE security review: its
# setattr loop below took them on `.edit` alone, which went around that gate).
_SMTP_FIELDS = ("smtp_host", "smtp_port", "smtp_secure", "smtp_username", "smtp_password", "smtp_from")


def _update_general_settings_impl(settings_data: SystemSettingUpdate, db: Session):
    """Shared implementation for PUT/POST general settings."""
    from app.models.procurement import Supplier

    settings = db.query(SystemSetting).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    update_data = settings_data.model_dump(exclude_unset=True)

    smtp_keys = sorted(k for k in update_data if k in _SMTP_FIELDS)
    if smtp_keys:
        # Names the fields, never a value: one of them may be the password.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"SMTP settings are saved through PUT /settings/smtp, not here: {', '.join(smtp_keys)}",
        )

    if "default_product_supplier_id" in update_data:
        sid = update_data["default_product_supplier_id"]
        if sid is not None and str(sid).strip():
            sid_clean = str(sid).strip()
            found = db.query(Supplier.id).filter(Supplier.id == sid_clean).first()
            if not found:
                raise HTTPException(status_code=400, detail="Default product supplier not found")
            update_data["default_product_supplier_id"] = sid_clean
        else:
            update_data["default_product_supplier_id"] = None

    # The default unit a UOM-less product takes. Validated where it is WRITTEN, the same as
    # the default supplier above: an id that resolves to nothing would otherwise be
    # discovered by an import quietly falling back to `EA` on nine thousand rows.
    if "default_uom_id" in update_data:
        uid = update_data["default_uom_id"]
        if uid is not None and str(uid).strip():
            uid_clean = str(uid).strip()
            if db.query(UnitOfMeasure.id).filter(UnitOfMeasure.id == uid_clean).first() is None:
                raise HTTPException(status_code=400, detail="Default unit of measure not found")
            update_data["default_uom_id"] = uid_clean
        else:
            update_data["default_uom_id"] = None

    # PLAN-oi-request-cs-reserve.md section 6c (F1): the reserve dialog's own default
    # Location must be an ACTIVE POOL - a bare warehouse code, no `-SUFFIX` group and no
    # `pool_warehouse_id` of its own naming another warehouse as ITS pool. 422, not 400
    # (the sibling id fields above are 400): this is a shape refusal about WHAT KIND of
    # warehouse was named, the same family as the other 422s in this function.
    if "oi_reserve_default_pool_warehouse_id" in update_data:
        wid = update_data["oi_reserve_default_pool_warehouse_id"]
        if wid is not None and str(wid).strip():
            from app.models.inventory import Warehouse
            from app.services.scm.group_netting import group_of_warehouse_code

            wid_clean = str(wid).strip()
            warehouse = (
                db.query(Warehouse)
                .filter(Warehouse.id == wid_clean, Warehouse.is_active.is_(True))
                .first()
            )
            is_group_member = (
                warehouse is not None
                and group_of_warehouse_code(warehouse.warehouse_code) is not None
            )
            if warehouse is None or is_group_member:
                raise HTTPException(
                    status_code=422,
                    detail="Choose an active site pool, not a group warehouse.",
                )
            update_data["oi_reserve_default_pool_warehouse_id"] = wid_clean
        else:
            update_data["oi_reserve_default_pool_warehouse_id"] = None

    for col in (
        "purchase_request_default_approver_user_id",
        "sponsorship_form_default_approver_user_id",
    ):
        if col in update_data:
            uid = update_data[col]
            if uid is not None and str(uid).strip():
                uid_clean = str(uid).strip()
                if db.query(User.id).filter(User.id == uid_clean).first() is None:
                    raise HTTPException(status_code=400, detail="Default approver user not found")
                update_data[col] = uid_clean
            else:
                update_data[col] = None

    # Handling-lock enabled types: accept a list, persist as a de-duped CSV of valid
    # form types (mirrors the singleton gotcha - the generic setattr loop below would
    # otherwise write a Python list into a Text column).
    if "handling_lock_enabled_types" in update_data:
        from app.services.form_sla_service import FORM_SLA_TYPES

        raw = update_data["handling_lock_enabled_types"] or []
        seen: list[str] = []
        for t in raw:
            t = str(t).strip()
            if t in FORM_SLA_TYPES and t not in seen:
                seen.append(t)
        update_data["handling_lock_enabled_types"] = ",".join(seen)

    # A block bar below the surface bar means every surfaced candidate also blocks,
    # which silently turns the two-bar design back into one aggressive bar. Reject it
    # here rather than let an admin discover it through false blocks in the field.
    if "project_clash_surface_threshold" in update_data or (
        "project_clash_block_threshold" in update_data
    ):
        effective_surface = float(
            update_data.get(
                "project_clash_surface_threshold",
                getattr(settings, "project_clash_surface_threshold", 0.55) or 0.55,
            )
        )
        effective_block = float(
            update_data.get(
                "project_clash_block_threshold",
                getattr(settings, "project_clash_block_threshold", 0.70) or 0.70,
            )
        )
        if effective_block < effective_surface:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The project clash blocking threshold must be at or above the "
                    "surfacing threshold."
                ),
            )

    # Chatbot media: a model id belongs to the provider it was picked from, so a
    # provider change that does not also name the models clears them rather than
    # sending the previous provider's ids to the new provider. The FE does the
    # same clear-on-change; this is the backstop for a direct PUT, where a kept
    # stale degraded model would fail only for over-quota contacts - the hardest
    # failure to attribute.
    if "media_image_provider" in update_data:
        new_provider = (update_data["media_image_provider"] or "").strip() or None
        update_data["media_image_provider"] = new_provider
        old_provider = (
            getattr(settings, "media_image_provider", None) or ""
        ).strip() or None
        if new_provider != old_provider:
            for model_col in ("media_image_model", "media_image_degraded_model"):
                if model_col not in update_data:
                    update_data[model_col] = None

    # Chatbot media: the pair has to hold together, and the per-field ge/le on
    # SystemSettingUpdate cannot express a relationship between two fields. An
    # extraction ceiling below the synchronous wait would kill a job mid-flight at
    # exactly the moment the endpoint degrades to `pending` - the result would be
    # neither returned inline nor retrievable afterwards (PLAN 2.4 / UAC S3-01d).
    if "media_sync_wait_seconds" in update_data or "media_extraction_timeout_seconds" in update_data:
        wait = update_data.get(
            "media_sync_wait_seconds", getattr(settings, "media_sync_wait_seconds", 30)
        )
        ceiling = update_data.get(
            "media_extraction_timeout_seconds",
            getattr(settings, "media_extraction_timeout_seconds", 45),
        )
        if wait is not None and ceiling is not None and int(ceiling) < int(wait):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Extraction timeout must be at least the synchronous wait, "
                    "or a job that outlives the wait is killed instead of degrading."
                ),
            )

    # AC-809: a lane this build cannot complete is refused HERE, naming it, rather than
    # saved and then ignored with a warning by the engine. The screen offers exactly this
    # vocabulary, so a well-behaved form can never trip it; a direct PUT with a typo used
    # to look saved while the lane kept delegating forever, which is the one failure an
    # owner cannot see from the screen they set it on.
    if update_data.get("chatbot_completed_lanes"):
        vocabulary = _completed_lane_vocabulary()
        unknown = [
            str(kind)
            for kind in update_data["chatbot_completed_lanes"]
            if str(kind) not in vocabulary
        ]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This build cannot complete "
                    + ", ".join(sorted(unknown))
                    + ". Only these lanes can be turned on: "
                    + ", ".join(sorted(vocabulary))
                    + "."
                ),
            )

    # `chatbot_memory` is one JSONB carrying two named keys (chatbot memory lane A,
    # contract section 2/5). An unknown key would be stored, returned and read by
    # nobody, and the card that was meant to set it would read as broken with no error
    # anywhere - the same silent failure the `chatbot_completed_lanes` check above
    # exists for.
    if update_data.get("chatbot_memory") is not None:
        from app.modules.chatbot.lane_vocabulary import (
            CHATBOT_MEMORY_DEFAULT_LEVELS,
            CHATBOT_MEMORY_KEYS,
        )

        memory = update_data["chatbot_memory"]
        if not isinstance(memory, dict):
            raise HTTPException(
                status_code=422, detail="chatbot_memory must be an object."
            )
        unknown_keys = sorted(set(memory) - set(CHATBOT_MEMORY_KEYS))
        if unknown_keys:
            raise HTTPException(
                status_code=422,
                detail=(
                    "chatbot_memory does not carry "
                    + ", ".join(unknown_keys)
                    + ". Its keys are: "
                    + ", ".join(CHATBOT_MEMORY_KEYS)
                    + "."
                ),
            )
        if "enabled" in memory and not isinstance(memory["enabled"], bool):
            raise HTTPException(
                status_code=422, detail="chatbot_memory.enabled must be a boolean."
            )
        # Never "off" and never null (contract section 2): the select has no clear,
        # and "off" is what a contact's OWN level is for, not the system default.
        if "default_level" in memory and memory["default_level"] not in CHATBOT_MEMORY_DEFAULT_LEVELS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "chatbot_memory.default_level must be one of: "
                    + ", ".join(CHATBOT_MEMORY_DEFAULT_LEVELS)
                    + "."
                ),
            )
        # A partial body changes only the keys it names (reviewer pass at d89110c0,
        # S9): `{"enabled": true}` alone must not drop the stored `default_level`,
        # which would resolve every contact to off with the switch on.
        update_data["chatbot_memory"] = {**(settings.chatbot_memory or {}), **memory}

    # The chatbot columns are NOT NULL with a default, so an explicit `null` in the body
    # means "reset to the default" - not a null write. Without this the loop below sends
    # NULL into a NOT NULL column and the PUT 500s at commit, which reads to the caller as
    # an outage rather than as the clear it asked for. The defaults repeat
    # `SystemSetting`'s own (`app/models/user.py`), which is the source of truth.
    from app.modules.chatbot.lane_vocabulary import default_chatbot_memory, default_tier_order, default_unsupported_domains

    _CHATBOT_COLUMN_DEFAULTS: dict[str, object] = {
        # Not a chatbot column, but it has the identical shape and the identical
        # failure: NOT NULL with a default, so an explicit `null` here would send
        # NULL into it and 500 at commit. An empty LIST is a legitimate answer
        # (warn about nothing) and is written as sent; only `null` resets.
        "price_tag_guarded_classes": ["Bathroom Furniture", "Kitchen Sink"],
        # NOT a literal (AC-931): read from the chatbot module's own doorway, which
        # projects it off `contracts.DOMAIN_SPEC`. This copy is why the doorway exists -
        # A6 unblocked `spo_allocation` in route.py and in the migration and this third
        # copy kept refusing it, which no test would have caught. Core may not import
        # `app/services/chatbot/` (AC-002), so the doorway is the seam.
        "chatbot_unsupported_domains": default_unsupported_domains(),
        "chatbot_completed_lanes": [],
        "chatbot_stock_denial_enabled": False,
        "local_buy_routing_enabled": False,
        "borrow_from_order_enabled": False,
        "chatbot_business_lane_enabled": False,
        "chatbot_ordering_enabled": False,
        "chatbot_direct_ingress_mode": "off",
        # A7 (chatbot-growth-r1): repeats SystemSetting.chatbot_crossdomain_ladder's own
        # default (app/models/user.py).
        "chatbot_crossdomain_ladder": {
            "inventory": ["incoming", "purchase_order"],
            "incoming": ["inventory"],
        },
        # Chatbot turn re-architecture (AC-1502): repeats `SystemSetting.
        # chatbot_tier_order`'s own default (app/models/user.py), through the same
        # doorway `chatbot_unsupported_domains` above uses.
        "chatbot_tier_order": default_tier_order(),
        # AC-1513: repeats `SystemSetting.chatbot_memory`'s own default through the
        # same doorway, so an explicit null on the form resets the whole card.
        "chatbot_memory": default_chatbot_memory(),
    }
    for column, default in _CHATBOT_COLUMN_DEFAULTS.items():
        if column in update_data and update_data[column] is None:
            if isinstance(default, list):
                update_data[column] = list(default)
            elif isinstance(default, dict):
                update_data[column] = dict(default)
            else:
                update_data[column] = default

    # #1288 security review (Nits): the column is NOT NULL - an explicit JSON `null`
    # here (as opposed to the key being absent) would otherwise reach `setattr` below
    # and 500 at commit instead of being refused as the bad request it is.
    if (
        "cost_price_verification_enabled" in update_data
        and update_data["cost_price_verification_enabled"] is None
    ):
        raise HTTPException(
            status_code=422, detail="cost_price_verification_enabled cannot be null.",
        )

    # #1288 AC-S2-20: the verification switch is the control that lets staff skip a
    # second person, so a change is a named, audited event of its own - not just another
    # row in the generic settings dict.
    cost_price_verification_changed = (
        "cost_price_verification_enabled" in update_data
        and bool(update_data["cost_price_verification_enabled"])
        != bool(getattr(settings, "cost_price_verification_enabled", False))
    )

    # NOT NULL booleans: an explicit null would 500 at commit, so it reads as "off".
    for column in ("sample_request_require_project",):
        if column in update_data and update_data[column] is None:
            update_data[column] = False

    for key, value in update_data.items():
        setattr(settings, key, value)

    if cost_price_verification_changed:
        from app.services.audit_service import log_audit
        from app.audit_context import get_actor

        # The person at the keyboard: the admin, not the target, under impersonation.
        actor = get_actor(db)
        real_user_id = (actor.real_user_id or actor.user_id) if actor else None
        log_audit(
            db, "system_settings", str(settings.id), "UPDATE",
            new_values={
                "event": "COST_VERIFICATION_SETTING",
                "cost_price_verification_enabled": update_data["cost_price_verification_enabled"],
            },
            user_id=real_user_id,
        )

    db.commit()
    db.refresh(settings)
    return {"message": "General settings updated successfully", "data": _row_without_secrets(settings)}


@router.put("/general", status_code=status.HTTP_200_OK)
async def update_general_settings(
    settings_data: SystemSettingUpdate,
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db)
):
    """Update general system settings.

    Gated on the EDIT slug, the same one `/upload-logo` and `/signin-background`
    already use. Reading this blob has required `user_management.settings.view`
    since the read-gate slice, and writing it required only authentication - so
    any authenticated session could rewrite the tenant's settings. That gap
    became load-bearing when the chatbot lane switches moved in here: clearing
    `chatbot_unsupported_domains` makes the bot answer questions it is
    deliberately not allowed to answer, and `chatbot_completed_lanes` decides
    what a production turn does for a real customer.

    No new slug and no grant sweep: every role holding `.view` today also holds
    `.edit` (admin, warehouse_manager and the three integration roles).
    """
    try:
        return _update_general_settings_impl(settings_data, db)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/general", status_code=status.HTTP_200_OK)
async def update_general_settings_post(
    settings_data: SystemSettingUpdate,
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db)
):
    """Update general system settings (POST allowed for frontend form submit).

    Same gate as the PUT above, and it has to be: the two write the same blob
    through the same implementation, so gating only one would leave the other as
    the way around it.
    """
    try:
        return _update_general_settings_impl(settings_data, db)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/social", status_code=status.HTTP_200_OK)
async def update_social_settings(
    settings_data: SystemSettingUpdate,
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db)
):
    """Update social media settings. Same gate as `PUT /general` (SETTINGS-PERM-GATE:
    it checked only that the caller was signed in)."""
    try:
        settings = db.query(SystemSetting).first()
        if not settings:
            raise HTTPException(status_code=404, detail="Settings not found")
        
        social_fields = [
            "social_facebook", "social_twitter", "social_instagram",
            "social_linkedin", "social_pinterest", "social_youtube"
        ]
        update_data = {k: v for k, v in settings_data.model_dump(exclude_unset=True).items() if k in social_fields}
        
        for key, value in update_data.items():
            setattr(settings, key, value)
        
        db.commit()
        db.refresh(settings)
        return {"message": "Social settings updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/notifications", status_code=status.HTTP_200_OK)
async def update_notification_settings(
    settings_data: SystemSettingUpdate,
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db)
):
    """Update notification settings. Same gate as `PUT /general` (SETTINGS-PERM-GATE:
    it checked only that the caller was signed in)."""
    try:
        settings = db.query(SystemSetting).first()
        if not settings:
            raise HTTPException(status_code=404, detail="Settings not found")
        
        notification_fields = [
            "notify_stock_email", "notify_stock_web", "notify_stock_threshold", "notify_stock_role_ids",
            "notify_new_order_email", "notify_new_order_web", "notify_new_order_role_ids",
            "notify_order_status_update_email", "notify_order_status_update_web", "notify_order_status_update_role_ids",
            "notify_payment_failure_email", "notify_payment_failure_web", "notify_payment_failure_role_ids",
            "notify_system_error_failure_email", "notify_system_error_web", "notify_system_error_role_ids"
        ]
        update_data = {k: v for k, v in settings_data.model_dump(exclude_unset=True).items() if k in notification_fields}
        
        for key, value in update_data.items():
            setattr(settings, key, value)
        
        db.commit()
        db.refresh(settings)
        return {"message": "Notification settings updated successfully", "data": _row_without_secrets(settings)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/smtp", status_code=status.HTTP_200_OK)
async def update_smtp_settings(
    settings_data: SystemSettingUpdate,
    current_user: dict = Depends(require_permission(RUNTIME_SETTINGS_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Update SMTP settings. Password is update-only (omit to keep existing).

    On the secrets slug, not `.edit` (SETTINGS-PERM-GATE): the host, login and password
    decide where every outbound email goes, which is a credential for the same reason the
    runtime secrets are, and `.edit` is also held by warehouse_manager and the integration
    API-key roles."""
    try:
        settings = db.query(SystemSetting).first()
        if not settings:
            raise HTTPException(status_code=404, detail="Settings not found")
        update_data = {k: v for k, v in settings_data.model_dump(exclude_unset=True).items() if k in _SMTP_FIELDS}
        for key, value in update_data.items():
            if key == "smtp_secure":
                setattr(settings, key, bool(value) if value is not None else True)
            elif key in ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from"):
                setattr(settings, key, value if value and str(value).strip() else None)
            else:
                setattr(settings, key, value)
        db.commit()
        db.refresh(settings)
        return {"message": "SMTP settings updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save SMTP settings: %s", e)
        raise handle_internal_error(str(e))


@router.post("/smtp/test", status_code=status.HTTP_200_OK, response_model=SmtpTestResult)
async def test_smtp_connection(
    current_user: dict = Depends(require_permission(RUNTIME_SETTINGS_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Test SMTP connection using current system settings (or env fallback). Same gate
    as `PUT /smtp`: it logs in with the stored password and reports what the server said."""
    try:
        from app.services.notification_email import test_smtp_connection as do_test
        settings = db.query(SystemSetting).first()
        success, message = do_test(settings)
        return SmtpTestResult(success=success, message=message)
    except Exception as e:
        raise handle_internal_error(str(e))


# ---------------------------------------------------------------------------
# Sign-in background
# ---------------------------------------------------------------------------
#
# Its own multipart endpoint rather than a field on `PUT /general`, for two reasons:
# the general update is a JSON body and threading a file through it would turn every
# save of an unrelated field into a multipart request, and the write path here has to
# be the ONLY way this column is set. `signin_background` is deliberately absent from
# `SystemSettingUpdate` above: it holds a URL that the sign-in page loads for anonymous
# visitors, so letting a client PUT an arbitrary string into it would let an admin point
# the login screen at somebody else's server. The bytes come to us or they do not go in.
#
# Read side: the column is on the GET dict (resolved to a signed URL, see above) and on
# `GET /api/v1/public/branding` for the unauthenticated sign-in page.

SIGNIN_BACKGROUND_MAX_BYTES = 5 * 1024 * 1024
SIGNIN_BACKGROUND_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
SIGNIN_BACKGROUND_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class SigninBackgroundResponse(BaseModel):
    """The URL the settings screen previews, or null once it has been removed."""

    signin_background_url: Optional[str] = None


@router.post(
    "/signin-background",
    status_code=status.HTTP_200_OK,
    response_model=SigninBackgroundResponse,
)
async def update_signin_background(
    request: Request,
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db),
):
    """Upload or clear the photograph behind the sign-in card.

    Multipart form:
      backgroundAction = "save" | "remove"
      backgroundFile   = the image, required when saving

    Removing clears the columns and the sign-in page falls back to its designed default
    wash, which is why there is no confirmation of a "nothing set" state to worry about:
    the screen it returns to is a finished one.
    """
    import re
    import uuid as _uuid
    from pathlib import Path

    settings = db.query(SystemSetting).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")

    form = await request.form()
    raw_action = form.get("backgroundAction")
    action = (raw_action if isinstance(raw_action, str) else str(raw_action or "")).strip()

    if action == "remove":
        # Shared with the deferred `signin_background.remove` record action, so the two
        # paths cannot drift about which columns "removed" means.
        from app.services.signin_background import clear_signin_background

        clear_signin_background(db)
        return SigninBackgroundResponse(signin_background_url=None)

    if action != "save":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="backgroundAction must be 'save' or 'remove'",
        )

    upload = form.get("backgroundFile")
    # A multipart field can arrive as a plain string, so narrow before reading it rather
    # than trusting that a field named `backgroundFile` carried a file.
    # request.form() hands back starlette's UploadFile, of which fastapi's is a
    # subclass - so the check must be against the starlette class or it never passes.
    filename = upload.filename if isinstance(upload, StarletteUploadFile) else None
    if not isinstance(upload, StarletteUploadFile) or not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An image is required when saving a sign-in background",
        )

    content = await upload.read()
    if len(content) > SIGNIN_BACKGROUND_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The sign-in background must be 5MB or smaller",
        )

    content_type = getattr(upload, "content_type", None) or "application/octet-stream"
    if content_type not in SIGNIN_BACKGROUND_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPG, PNG or WebP images are allowed",
        )

    original = Path(filename).name or "signin-background.jpg"
    stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(original).stem)[:80] or "signin-background"
    ext = (Path(original).suffix[:10] or ".jpg").lower()
    if ext not in SIGNIN_BACKGROUND_EXTENSIONS:
        ext = ".jpg"
    storage_path = f"branding/signin-background/{_uuid.uuid4().hex}_{stem}{ext}"

    try:
        from app.services.storage_router import (
            cdn_base_url,
            default_provider,
            get_backend,
        )

        provider = default_provider()
        key, _ = get_backend(provider).upload_file(content, storage_path, content_type=content_type)
        setattr(settings, "signin_background", cdn_base_url(provider, key))
        setattr(settings, "signin_background_storage_provider", provider)
    except ValueError as cfg_err:
        logger.error("Sign-in background upload configuration error: %s", cfg_err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured. Contact an administrator.",
        )
    except Exception as upload_err:  # noqa: BLE001
        logger.exception("Sign-in background upload failed: %s", upload_err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload the sign-in background. Please try again.",
        )

    db.commit()
    return SigninBackgroundResponse(
        signin_background_url=resolve_signin_background_url(settings)
    )
