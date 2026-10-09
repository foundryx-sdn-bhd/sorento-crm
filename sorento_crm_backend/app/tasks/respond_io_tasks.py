"""RQ tasks for Respond.io message sends.

Decouples the external Respond.io API call from the request lifecycle so a 4xx/5xx
from Respond.io does not fail the surrounding business write (status update,
SLA tracking, etc.). Failed sends land in the RQ FailedJobRegistry and an
``integration_logs`` row with ``status='failed'``.

Sends are 24h-window-aware (``respond_messaging_service.send_text_or_template``):
window open → plain text; closed → the use case's default WhatsApp template.
Outside-window plain sends are pointless - Respond.io reports success but
WhatsApp silently drops them, which is also why the old per-send delivery
polling was removed (plan: docs/plans/PLAN-whatsapp-template-fallback.md).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _send_and_log(
    *,
    use_case: str,
    business_table: str,
    business_id: str,
    identifier: str,
    message_text: str,
    respond_user_id: str,
    crm_sender_user_id: Optional[str],
    space_id: Optional[str],
    sla_entity_type: str,
    extra_context_vars: Optional[dict] = None,
    emit_outbound_webhook: bool = True,
) -> dict:
    """Shared worker body: window-aware send, outbound webhook, integration log.

    Re-raises on failure so RQ records the job as FAILED.

    ``extra_context_vars`` merges into the resolved template context (e.g. the
    portal OTP code, which has no business-entity row to derive). Set
    ``emit_outbound_webhook=False`` for system messages (OTP) that should be
    logged in the Respond outbox but NOT mirrored into the CRM chat thread.

    OTP sends (sign-in and portal) keep the real code in the outbox row:
    owner ruling 29 Sep 2026 overruled security round B1 (#1280), because
    the operator reads the code from the outbox on a test copy.
    """
    from app.database import SessionLocal
    from app.schemas.integration import IntegrationLogCreate
    from app.services.crm_chat_outbound_webhook import (
        enqueue_crm_chat_outbound_webhook,
        resolve_sla_assignee_respond_user_id,
    )
    from app.services.integration_service import IntegrationLogService
    from app.services.respond_messaging_service import (
        build_context_vars,
        send_text_or_template,
    )

    db = SessionLocal()
    try:
        log_service = IntegrationLogService(db)
        request_payload = {"message": {"type": "text", "text": message_text}}
        try:
            context_vars = build_context_vars(
                db,
                use_case=use_case,
                business_id=business_id,
                identifier=identifier,
            )
            if extra_context_vars:
                context_vars.update(extra_context_vars)
            result = send_text_or_template(
                db,
                identifier=identifier,
                text=message_text,
                use_case=use_case,
                context_vars=context_vars,
            )
            request_payload = result["request_payload"]
            response = result["response"]

            if emit_outbound_webhook:
                # The thread reads from chat_histories: write the row now
                # (optimistic send) instead of waiting for the n8n echo. The
                # same gate as the webhook - a system message that must stay
                # out of the thread (OTP) stays out of the local mirror too.
                try:
                    from app.services.conversation_thread_service import mirror_outgoing_send

                    mirror_outgoing_send(
                        db,
                        identifier=identifier,
                        respond_contact_id=None,
                        response=response,
                        message={
                            "type": "text",
                            "text": result.get("rendered_text") or message_text,
                        },
                    )
                except Exception:  # noqa: BLE001 - the send already succeeded
                    logger.warning(
                        "local mirror failed for %s %s", business_table, business_id, exc_info=True
                    )
                enqueue_crm_chat_outbound_webhook(
                    db,
                    business_table=business_table,
                    business_id=business_id,
                    contact_respond_io_id=identifier,
                    message_text=message_text,
                    respond_api_response=response if isinstance(response, dict) else None,
                    space_id=space_id,
                    crm_sender_user_id=crm_sender_user_id,
                    respond_user_id_fallback=respond_user_id,
                    assignee_respond_user_id=resolve_sla_assignee_respond_user_id(
                        db, sla_entity_type, business_id
                    ),
                )
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table=business_table,
                    business_id=business_id,
                    external_reference=identifier,
                    direction="outbound",
                    endpoint=f"https://api.respond.io/v2/contact/id:{identifier}/message",
                    http_method="POST",
                    status="success",
                    response_payload=str(response)[:50000] if response else None,
                ),
                request_payload_dict=request_payload,
            )
            return {
                "business_id": business_id,
                "status": "success",
                "sent_as": result["sent_as"],
                "response": response,
            }
        except Exception as e:
            logger.exception(
                "Respond.io send failed (queued task) for %s %s",
                business_table,
                business_id,
            )
            # Log what was ACTUALLY attempted (text vs template) - the window-aware
            # send stamps the real payload on the exception. Closed window => the
            # outbox row shows a template attempt, not the default text payload.
            request_payload = getattr(e, "request_payload", request_payload)
            # Capture Respond.io's actual HTTP response (status + body) on 4xx/5xx
            # so the failure is diagnosable (e.g. WHY a 403 - WAF block vs window
            # vs channel error) instead of just "403 Forbidden for url ...".
            resp = getattr(e, "response", None)
            resp_code = None
            resp_body = None
            if resp is not None:
                try:
                    resp_code = resp.status_code
                except Exception:
                    resp_code = None
                try:
                    resp_body = (resp.text or "")[:50000]
                except Exception:
                    resp_body = None
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table=business_table,
                    business_id=business_id,
                    external_reference=identifier or "",
                    direction="outbound",
                    endpoint=f"https://api.respond.io/v2/contact/id:{identifier or ''}/message",
                    http_method="POST",
                    status="failed",
                    status_code=resp_code,
                    response_payload=resp_body,
                    error_message=str(e),
                ),
                request_payload_dict=request_payload,
            )
            raise
    finally:
        db.close()


def send_portal_otp_respond_message(
    otp_id: str,
    identifier: str,
    message_text: str,
    otp_code: str,
    space_id: Optional[str],
) -> dict:
    """Worker-side: window-aware Respond.io send for a portal login OTP.

    Logged in the Respond outbox (``integration_logs``, business_table
    ``portal_otp_codes``) like every other send - including a ``status='failed'``
    row when the send can't go out (e.g. local dev with no Respond.io
    connectivity), whose ``request_payload`` carries the code so it can be read
    back for testing (owner ruling 29 Sep 2026, overruling security round B1,
    #1280). Not mirrored into the CRM chat thread (system message).
    """
    return _send_and_log(
        use_case="portal_otp",
        business_table="portal_otp_codes",
        business_id=otp_id,
        identifier=identifier,
        message_text=message_text,
        respond_user_id="",
        crm_sender_user_id=None,
        space_id=space_id,
        sla_entity_type="",
        extra_context_vars={"otp_code": otp_code},
        emit_outbound_webhook=False,
    )


def send_login_otp_respond_message(
    otp_id: str,
    identifier: str,
    message_text: str,
    otp_code: str,
    space_id: Optional[str],
) -> dict:
    """Worker-side: window-aware Respond.io send for a phone sign-in OTP (identity S1, #1280).

    Sends under the approved ``portal_otp`` template (owner ruling, 27 Sep
    2026: CRM sign-in reuses it, no new template goes to Meta). Only when
    ``settings.phone_signin_otp_use_case`` is set AND has a mapped default
    does the send use that override instead. Logged in the Respond outbox
    exactly like the portal's own OTP send (``business_table=
    'portal_otp_codes'``), and likewise not mirrored into the CRM chat thread.
    The row carries the real code (owner ruling 29 Sep 2026, overruling
    security round B1, #1280): the operator reads it from the outbox on a
    test copy.
    """
    from app.config import settings
    from app.database import SessionLocal
    from app.services.respond_template_service import get_default_row

    use_case = "portal_otp"
    override = settings.phone_signin_otp_use_case
    if override:
        db = SessionLocal()
        try:
            if get_default_row(db, override) is not None:
                use_case = override
        finally:
            db.close()

    return _send_and_log(
        use_case=use_case,
        business_table="portal_otp_codes",
        business_id=otp_id,
        identifier=identifier,
        message_text=message_text,
        respond_user_id="",
        crm_sender_user_id=None,
        space_id=space_id,
        sla_entity_type="",
        extra_context_vars={"otp_code": otp_code},
        emit_outbound_webhook=False,
    )


def dispatch_phone_signin_code(num: str) -> None:
    """Worker-side: decide eligibility and send, for EVERY request-code call
    (security round S1, #1280).

    `POST /auth/phone/request-code` enqueues this for every normalised
    number, known or not, with no eligibility check of its own - so the
    route's timing carries no tell. This job is what used to run inline in
    that route: resolve the number, and only when it belongs to exactly one
    eligible user, create the code and send it - INLINE (`dispatch_inline=
    True`), since this job already IS the `respond_io` queue's own worker, so
    a second `enqueue_job` would just add a hop. Every failure (ineligible
    number, a contact-level cooldown/cap hit, a send failure) is logged and
    swallowed - nothing here is visible to the HTTP caller, which already
    answered 200.
    """
    from app.database import SessionLocal
    from app.services import phone_signin_service as svc

    db = SessionLocal()
    try:
        eligible = svc.find_eligible(db, num)
        if eligible is None:
            return
        _user, contact = eligible
        svc.send_signin_code(db, contact, dispatch_inline=True)
    finally:
        db.close()


def send_complaint_respond_message(
    complaint_id: str,
    identifier: str,
    display_message: str,
    respond_user_id: str,
    crm_sender_user_id: Optional[str],
    space_id: Optional[str],
    extra_context_vars: Optional[dict] = None,
) -> dict:
    """Worker-side: window-aware Respond.io send for a complaint update.

    ``extra_context_vars`` carries the action-specific structured-template vars
    (bare ``update`` core + explicit ``portal_url``) that can't be reconstructed
    from the complaint row alone. See PLAN-complaint-structured-update-template.md.
    """
    return _send_and_log(
        use_case="complaint",
        business_table="complaints",
        business_id=complaint_id,
        identifier=identifier,
        message_text=display_message,
        respond_user_id=respond_user_id,
        crm_sender_user_id=crm_sender_user_id,
        space_id=space_id,
        sla_entity_type="complaint",
        extra_context_vars=extra_context_vars,
    )


def send_stock_inquiry_respond_message(
    inquiry_id: str,
    identifier: str,
    message_text: str,
    respond_user_id: str,
    crm_sender_user_id: Optional[str],
    space_id: Optional[str],
    verify_delivery: bool = True,
    extra_context_vars: Optional[dict] = None,
) -> dict:
    """Worker-side: window-aware Respond.io send for a stock inquiry update.

    ``verify_delivery`` is accepted for enqueue-signature compatibility but
    ignored - post-send delivery polling was removed in favour of the up-front
    window check.

    ``extra_context_vars`` carries the action-specific structured-template vars
    (bare ``update`` core + explicit ``portal_url``) that can't be reconstructed
    from the inquiry row alone.
    """
    return _send_and_log(
        use_case="stock_inquiry",
        business_table="stock_inquiries",
        business_id=inquiry_id,
        identifier=identifier,
        message_text=message_text,
        respond_user_id=respond_user_id,
        crm_sender_user_id=crm_sender_user_id,
        space_id=space_id,
        sla_entity_type="stock_inquiry",
        extra_context_vars=extra_context_vars,
    )


_FORM_COMMENT_TABLES = {
    "complaint": "complaints",
    "stock_inquiry": "stock_inquiries",
    "purchase_request": "purchase_requests",
    "sponsorship_form": "purchase_requests",
}


def send_form_comment_message(
    kind: str,
    entity_id: str,
    identifier: str,
    message_text: str,
    respond_user_id: str,
    crm_sender_user_id: Optional[str],
    space_id: Optional[str],
    extra_context_vars: Optional[dict] = None,
) -> dict:
    """Worker-side: window-aware Respond.io send of a staff comment on a form."""
    return _send_and_log(
        use_case=kind,
        business_table=_FORM_COMMENT_TABLES[kind],
        business_id=entity_id,
        identifier=identifier,
        message_text=message_text,
        respond_user_id=respond_user_id,
        crm_sender_user_id=crm_sender_user_id,
        space_id=space_id,
        sla_entity_type=kind,
        extra_context_vars=extra_context_vars,
    )


# ---------------------------------------------------------------------------
# Manual chat-panel sends (complaint / stock inquiry / purchase request /
# conversation-SLA chat window). Decouple the Respond.io HTTP call from the
# request so a 4xx/5xx (e.g. a 401 on a bad workspace key) lands in the Respond
# outbox instead of the operator's face. Both delegate to the shared
# respond_chat_template_service, which writes the integration_log outbox on
# success AND failure and re-raises so RQ records the job FAILED. The in-request
# route runs the DB-only prechecks (precheck_*), so fixable input errors still
# surface inline; only the delivery is async here.
# ---------------------------------------------------------------------------


def deliver_chat_message(
    identifier: str,
    respond_contact_id: Optional[str],
    text: str,
    chat_use_case: str,
    business_table: str,
    business_id: str,
    sender_name: str,
    crm_sender_user_id: Optional[str],
    precomputed_context: Optional[dict] = None,
) -> dict:
    """Worker-side smart chat send (in-window raw text, out-of-window ``*_chat``
    template). ``precomputed_context`` carries the entity's portal/view-link vars
    resolved in-request (a callable can't cross the RQ boundary)."""
    from app.database import SessionLocal
    from app.services.respond_chat_template_service import send_chat_message_for

    db = SessionLocal()
    try:
        return send_chat_message_for(
            db,
            identifier=identifier,
            respond_contact_id=respond_contact_id,
            text=text,
            chat_use_case=chat_use_case,
            business_table=business_table,
            business_id=business_id,
            sender_name=sender_name,
            created_by=crm_sender_user_id,
            context_builder=None,
            precomputed_context=precomputed_context or {},
        )
    finally:
        db.close()


def deliver_manual_template(
    identifier: str,
    template_id: str,
    params: dict,
    business_table: str,
    business_id: str,
    crm_sender_user_id: Optional[str],
    sla_tracking_id: Optional[str] = None,
) -> dict:
    """Worker-side manual approved-template send by id + positional params.

    ``sla_tracking_id`` (optional, defaulted so already-queued jobs keep
    deserializing) names an intervention ticket to stamp once the send
    SUCCEEDS: a "Send template" from the ticket drawer is a real reply, so it
    must stop that ticket's response clock exactly like an in-window text send
    (finding 4). Best-effort and idempotent - the contact already has the
    message, so a stamping problem must never fail the job.
    """
    from app.database import SessionLocal
    from app.services.respond_chat_template_service import send_manual_template_for

    db = SessionLocal()
    try:
        result = send_manual_template_for(
            db,
            identifier=identifier,
            template_id=template_id,
            params=params,
            business_table=business_table,
            business_id=business_id,
            created_by=crm_sender_user_id,
        )
        if sla_tracking_id:
            try:
                from app.services.sla_service import ConversationSLATrackingService

                ConversationSLATrackingService(db).mark_ticket_responded_by_id(
                    str(sla_tracking_id),
                    responded_by_user_id=crm_sender_user_id,
                    reason="CRM reply (sent_as=template)",
                    expect_respond_io_id=identifier,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "deliver_manual_template: response-clock stamp failed for %s",
                    sla_tracking_id,
                    exc_info=True,
                )
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Conversation lifecycle ops (close / reassign) for conversation-SLA actions.
# Unlike the message sends above these are NOT 24h-window-aware - they act on the
# conversation object itself. Each writes a Respond outbox row (integration_logs)
# on success AND failure and re-raises so RQ records the job FAILED. business_table
# = "conversation_sla_tracking", business_id = the tracking id.
# ---------------------------------------------------------------------------


TICKET_RESOLVED_EXCERPT_CHARS = 120


def _enquiry_excerpt(text: Optional[str]) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= TICKET_RESOLVED_EXCERPT_CHARS:
        return body
    return body[: TICKET_RESOLVED_EXCERPT_CHARS - 3].rstrip() + "..."


def send_ticket_resolved_message(tracking_id: str) -> dict:
    """Worker-side: tell the contact THIS enquiry is resolved
    (PLAN-ticket-resolved-closing-message, one message per ticket).

    The CRM is the only sender, on every resolve lane; n8n's respond-close-convo
    keeps the contact-level housekeeping for the last open ticket and no longer
    messages. Wording is the admin's ``ticket_resolved`` template use case
    (in-window: the configured default body over these variables, or the
    built-in fallback below; out-of-window: the mapped approved template).
    Variables: ``contact_name`` (the contact), ``message`` (the enquiry excerpt),
    ``entity_number`` (the contact name again - a conversation ticket has no
    number, and a template mapped with the SLA convention still has to fill).
    """
    from app.database import SessionLocal
    from app.models.sla import ConversationSLATracking

    db = SessionLocal()
    try:
        tracking = (
            db.query(ConversationSLATracking)
            .filter(ConversationSLATracking.id == str(tracking_id))
            .first()
        )
        if tracking is None:
            return {"business_id": str(tracking_id), "status": "skipped", "reason": "no_tracking"}
        contact = getattr(tracking, "contact", None)
        identifier = str(getattr(contact, "respond_io_id", "") or "").strip()
        if not identifier:
            return {
                "business_id": str(tracking_id),
                "status": "skipped",
                "reason": "no_respond_contact",
            }
        contact_name = (
            getattr(contact, "name", None)
            or " ".join(
                filter(None, [getattr(contact, "first_name", None), getattr(contact, "last_name", None)])
            )
            or getattr(contact, "phone_number", None)
            or "there"
        )
        excerpt = _enquiry_excerpt(getattr(tracking, "source_message_text", None))
        enquiry_words = f'your enquiry "{excerpt}"' if excerpt else "your enquiry"
        fallback_text = (
            f"Hi {contact_name}, {enquiry_words} has been resolved. "
            "If there is anything else we can help with, just reply here."
        )
        resolved_by = str(getattr(tracking, "resolved_by", None) or "").strip() or None
    finally:
        db.close()

    return _send_and_log(
        use_case="ticket_resolved",
        business_table="conversation_sla_tracking",
        business_id=str(tracking_id),
        identifier=identifier,
        message_text=fallback_text,
        respond_user_id="",
        crm_sender_user_id=resolved_by,
        space_id=None,
        sla_entity_type="conversation_sla_tracking",
        extra_context_vars={
            "contact_name": contact_name,
            "message": excerpt or "your enquiry",
            "entity_number": contact_name,
        },
    )


def _resolve_respond_io_id(db, tracking) -> Optional[str]:
    """The contact's Respond.io id (respond_io_id), never the internal
    respond_contact_id. None when the tracking has no resolvable contact."""
    from app.models.access import RespondContact

    contact_id = getattr(tracking, "respond_contact_id", None)
    if not contact_id:
        return None
    row = (
        db.query(RespondContact.respond_io_id)
        .filter(RespondContact.id == str(contact_id))
        .first()
    )
    return str(row[0]) if row and row[0] else None


def _log_respond_conversation_op(
    db,
    *,
    tracking_id: str,
    identifier: str,
    endpoint: str,
    request_payload: dict,
    status: str,
    response=None,
    error: Optional[Exception] = None,
) -> None:
    """Write one Respond outbox row for a conversation op. Mirrors _send_and_log:
    captures the Respond HTTP status_code + body from the exception on failure so a
    4xx/5xx (WAF block, 'category required', bad workspace key) is diagnosable."""
    import json as _json

    from app.schemas.integration import IntegrationLogCreate
    from app.services.integration_service import IntegrationLogService

    resp_code = None
    resp_body = None
    if error is not None:
        resp = getattr(error, "response", None)
        if resp is not None:
            try:
                resp_code = resp.status_code
            except Exception:  # noqa: BLE001
                resp_code = None
            try:
                resp_body = (resp.text or "")[:50000]
            except Exception:  # noqa: BLE001
                resp_body = None
    elif response is not None:
        resp_code = 200
        resp_body = str(response)[:50000]
    try:
        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="respond_io",
                business_table="conversation_sla_tracking",
                business_id=str(tracking_id),
                external_reference=identifier or "",
                direction="outbound",
                endpoint=endpoint,
                http_method="POST",
                status=status,
                status_code=resp_code,
                response_payload=resp_body,
                error_message=str(error) if error is not None else None,
            ),
            request_payload_dict=request_payload,
        )
    except Exception as le:  # noqa: BLE001 - never let logging mask the real outcome
        db.rollback()
        logger.warning("Respond conversation-op outbox write failed: %s", le)


def close_respond_conversation(tracking_id: str) -> dict:
    """Worker-side: close (resolve) the Respond.io conversation for a resolved
    conversation-SLA tracking. Logged in the Respond outbox; re-raises on failure."""
    from app.database import SessionLocal
    from app.models.sla import ConversationSLATracking
    from app.services.integration_service import RespondClient

    db = SessionLocal()
    try:
        tracking = (
            db.query(ConversationSLATracking)
            .filter(ConversationSLATracking.id == str(tracking_id))
            .first()
        )
        if tracking is None:
            logger.warning("close_respond_conversation: tracking %s not found", tracking_id)
            return {"tracking_id": tracking_id, "status": "skipped_no_tracking"}
        identifier = _resolve_respond_io_id(db, tracking)
        if not identifier:
            logger.warning(
                "close_respond_conversation: no respond_io_id for %s; skipping", tracking_id
            )
            return {"tracking_id": tracking_id, "status": "skipped_no_respond_id"}
        endpoint = f"https://api.respond.io/v2/contact/id:{identifier}/conversation/status"
        payload = {"status": "close", "category": "Resolved", "summary": "Resolved from Sorento CRM SLA tracking."}
        try:
            response = RespondClient.for_contact_id(
                db, str(tracking.respond_contact_id)
            ).close_conversation(identifier, category="Resolved", summary=payload["summary"])
            _log_respond_conversation_op(
                db, tracking_id=tracking_id, identifier=identifier, endpoint=endpoint,
                request_payload=payload, status="success", response=response,
            )
            return {"tracking_id": tracking_id, "status": "success"}
        except Exception as e:  # noqa: BLE001
            logger.exception("close_respond_conversation failed for %s", tracking_id)
            _log_respond_conversation_op(
                db, tracking_id=tracking_id, identifier=identifier, endpoint=endpoint,
                request_payload=payload, status="failed", error=e,
            )
            raise
    finally:
        db.close()


def set_respond_conversation_assignee(tracking_id: str, respond_user_id: str) -> dict:
    """Worker-side: set the Respond.io conversation assignee (owner) for a
    conversation-SLA tracking - used by reassign, takeover, and escalate. Logged in
    the Respond outbox; re-raises on failure."""
    from app.database import SessionLocal
    from app.models.sla import ConversationSLATracking
    from app.services.integration_service import RespondClient

    db = SessionLocal()
    try:
        assignee_id = str(respond_user_id or "").strip()
        tracking = (
            db.query(ConversationSLATracking)
            .filter(ConversationSLATracking.id == str(tracking_id))
            .first()
        )
        if tracking is None:
            logger.warning("set_respond_conversation_assignee: tracking %s not found", tracking_id)
            return {"tracking_id": tracking_id, "status": "skipped_no_tracking"}
        if not assignee_id:
            logger.warning(
                "set_respond_conversation_assignee: no respond_user_id for %s; skipping", tracking_id
            )
            return {"tracking_id": tracking_id, "status": "skipped_no_assignee"}
        identifier = _resolve_respond_io_id(db, tracking)
        if not identifier:
            logger.warning(
                "set_respond_conversation_assignee: no respond_io_id for %s; skipping", tracking_id
            )
            return {"tracking_id": tracking_id, "status": "skipped_no_respond_id"}
        endpoint = f"https://api.respond.io/v2/contact/id:{identifier}/conversation/assignee"
        payload = {"assigneeId": assignee_id}
        try:
            response = RespondClient.for_identifier(db, identifier).set_conversation_assignee(
                identifier, assignee_id
            )
            _log_respond_conversation_op(
                db, tracking_id=tracking_id, identifier=identifier, endpoint=endpoint,
                request_payload=payload, status="success", response=response,
            )
            return {"tracking_id": tracking_id, "status": "success"}
        except Exception as e:  # noqa: BLE001
            logger.exception("set_respond_conversation_assignee failed for %s", tracking_id)
            _log_respond_conversation_op(
                db, tracking_id=tracking_id, identifier=identifier, endpoint=endpoint,
                request_payload=payload, status="failed", error=e,
            )
            raise
    finally:
        db.close()
