"""Reusable chat window-state + manual template-send endpoints.

Each entity that exposes a Respond.io chat panel (complaint, stock inquiry,
purchase request) mounts these two routes via ``build_chat_template_router``,
supplying a resolver that turns the entity id into a Respond.io send
identifier + internal respond_contact_id. Keeps the manual template flow
identical across panels (plan: PLAN-whatsapp-template-fallback.md).

The sub-router uses a fixed ``entity_id`` path segment and is mounted under
each entity's existing prefix (e.g. ``/complaints``), so the resulting paths
are ``/complaints/{entity_id}/conversation/window-state`` etc.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.services.error_handler import handle_validation_error

logger = logging.getLogger(__name__)

# (identifier, respond_contact_id) - identifier None means no linked contact.
ChatContactResolver = Callable[[Session, str], Tuple[Optional[str], Optional[str]]]


class TemplateMessageSendRequest(BaseModel):
    template_id: str = Field(..., min_length=1)
    params: dict[str, str] = Field(default_factory=dict)
    # Optional intervention-ticket id: a template sent from the ticket drawer is
    # a real reply and must stop THAT ticket's response clock (finding 4). Every
    # other chat surface omits it and behaves exactly as before. Its presence
    # ALSO selects the synchronous send path (see _send_ticket_template_now):
    # the drawer needs a truthful outcome, not a queued receipt. The stamp is
    # only honoured when the ticket's contact is the one that received the
    # template.
    tracking_id: Optional[str] = None


class ChatMessageSendRequest(BaseModel):
    text: str = Field(..., min_length=1)


def _send_ticket_template_now(
    db: Session,
    *,
    identifier: str,
    body: "TemplateMessageSendRequest",
    business_table: str,
    entity_id: str,
    sender_user_id: Optional[str],
    pre: dict,
) -> dict:
    """Deliver a ticket-drawer template send IN-REQUEST and answer the truth.

    The queued path answers ``{ok: true, queued: true}`` the moment the job is
    in Redis: with no worker draining ``respond_io`` the operator sees a
    success toast, the ticket's response clock never stamps, and NO
    ``integration_log`` row exists until something drains the queue (observed:
    a template sat queued for four hours). That breaks the invariant that every
    Respond send writes an outbox row on success AND failure, and the drawer is
    the one caller that needs the outcome to render its own state - so a send
    naming a ``tracking_id`` runs here instead.

    This route is a plain ``def``, which FastAPI already runs in its external
    threadpool, so the blocking Respond call never touches the event loop.
    The delivery itself (outbox on both outcomes, 502 on failure) is
    ``deliver_manual_template_now``, shared with the Conversations inbox's
    contact-keyed template send. The response-clock stamp is a post-send side
    effect: best-effort, never fatal (the contact already has the message).
    """
    from app.services.respond_chat_template_service import deliver_manual_template_now

    sent = deliver_manual_template_now(
        db,
        identifier=identifier,
        template_id=body.template_id,
        params=body.params,
        business_table=business_table,
        business_id=entity_id,
        sender_user_id=sender_user_id,
    )

    try:
        from app.services.sla_service import ConversationSLATrackingService

        ConversationSLATrackingService(db).mark_ticket_responded_by_id(
            str(body.tracking_id),
            responded_by_user_id=sender_user_id,
            reason="CRM reply (sent_as=template)",
            expect_respond_io_id=identifier,
        )
    except Exception:  # noqa: BLE001 - the message is already delivered
        logger.warning(
            "template send: response-clock stamp failed for %s",
            body.tracking_id,
            exc_info=True,
        )

    # AC-J1: a template sent from the drawer is a human reply too, so it pauses
    # the bot exactly like an in-window text send. Best-effort by construction.
    from app.services.crm_chat_outbound_webhook import notify_human_ticket_send

    notify_human_ticket_send(
        db,
        tracking_id=str(body.tracking_id),
        contact_respond_io_id=identifier,
        message_text=pre.get("rendered_body") or "",
        respond_api_response=sent.get("response") if isinstance(sent, dict) else None,
        sender_user_id=sender_user_id,
    )

    return {
        "ok": True,
        "queued": False,
        "template_name": pre["template_name"],
        "rendered_body": pre["rendered_body"],
    }


def build_chat_template_router(
    *,
    business_table: str,
    resolver: ChatContactResolver,
    chat_use_case: Optional[str] = None,
    chat_use_case_resolver: Optional[Callable[[Session, str], str]] = None,
    context_builder: Optional[Callable[[Session, str], dict]] = None,
    read_dependencies: Optional[list] = None,
) -> APIRouter:
    """Reusable window-state + manual-template + smart-send routes for a chat panel.

    ``chat_use_case`` is the ``*_chat`` / ``conversation_chat`` template use case for
    the pure smart-send route. ``chat_use_case_resolver`` overrides it per entity
    (e.g. purchase_request vs sponsorship_form on a single mount). ``context_builder``
    supplies extra template context (portal_url / view_url) per entity.
    ``read_dependencies`` (LIST-API-PERMS) is the mounting module's permission gate for
    the two GETs, e.g. ``[Depends(require_permission("<module>.<resource>.view"))]``;
    without it they are open to any session, as before.
    """
    router = APIRouter()
    read_gate = list(read_dependencies or [])

    @router.get("/{entity_id}/conversation/window-state", dependencies=read_gate)
    def window_state(
        entity_id: str,
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user),
    ):
        identifier, respond_contact_id = resolver(db, entity_id)
        if not identifier:
            raise handle_validation_error(
                "No Respond.io contact linked; cannot check window state."
            )
        from app.services.respond_chat_template_service import get_window_state_for

        return get_window_state_for(
            db, identifier=identifier, respond_contact_id=respond_contact_id
        )

    @router.post("/{entity_id}/conversation/template-message")
    def template_message(
        entity_id: str,
        body: TemplateMessageSendRequest,
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user),
    ):
        identifier, _ = resolver(db, entity_id)
        if not identifier:
            raise handle_validation_error(
                "No Respond.io contact linked; cannot send a template."
            )
        from app.services.respond_chat_template_service import precheck_manual_template

        # DB-only validation in-request - template not found / not approved / missing
        # params still surface inline. Only the Respond.io send is deferred.
        pre = precheck_manual_template(
            db, template_id=body.template_id, params=body.params
        )

        if body.tracking_id:
            return _send_ticket_template_now(
                db,
                identifier=identifier,
                body=body,
                business_table=business_table,
                entity_id=str(entity_id),
                sender_user_id=str(current_user.get("id") or "") or None,
                pre=pre,
            )

        from app.services.queue_service import enqueue_job
        from app.tasks.respond_io_tasks import deliver_manual_template

        job = enqueue_job(
            deliver_manual_template,
            identifier,
            body.template_id,
            body.params,
            business_table,
            str(entity_id),
            str(current_user.get("id") or "") or None,
            (str(body.tracking_id) if body.tracking_id else None),
            queue_name="respond_io",
            job_timeout=180,
        )
        logger.info(
            "Enqueued manual-template send job %s for %s %s",
            job.id,
            business_table,
            entity_id,
        )
        return {
            "ok": True,
            "queued": True,
            "template_name": pre["template_name"],
            "rendered_body": pre["rendered_body"],
        }

    @router.get("/{entity_id}/conversation/chat-template", dependencies=read_gate)
    def chat_template(
        entity_id: str,
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user),
    ):
        """Describe the form's chat template so the composer can render it inline
        with a fill-in field for the message. DB-only (no Respond.io call)."""
        use_case = (
            chat_use_case_resolver(db, entity_id)
            if chat_use_case_resolver is not None
            else chat_use_case
        )
        if not use_case:
            return {"configured": False, "reason": "not_supported"}
        identifier, respond_contact_id = resolver(db, entity_id)
        if not identifier:
            return {"configured": False, "reason": "no_contact"}
        sender_name = (current_user.get("name") or "").strip() or "Customer Service"
        from app.services.respond_chat_template_service import get_chat_template_preview

        return get_chat_template_preview(
            db,
            identifier=identifier,
            respond_contact_id=respond_contact_id,
            chat_use_case=use_case,
            sender_name=sender_name,
            entity_id=str(entity_id),
            context_builder=context_builder,
        )

    @router.post("/{entity_id}/conversation/send-message")
    def send_message(
        entity_id: str,
        body: ChatMessageSendRequest,
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user),
    ):
        """Pure smart chat send: in-window plain text, out-of-window ``*_chat``
        template with the sender's name. Never mutates the entity."""
        use_case = (
            chat_use_case_resolver(db, entity_id)
            if chat_use_case_resolver is not None
            else chat_use_case
        )
        if not use_case:
            raise handle_validation_error("Chat send not configured for this entity.")
        identifier, respond_contact_id = resolver(db, entity_id)
        if not identifier:
            raise handle_validation_error(
                "No Respond.io contact linked; cannot send a message."
            )
        sender_name = (current_user.get("name") or "").strip() or "Customer Service"
        from app.services.respond_chat_template_service import precheck_chat_send

        # DB-only precheck in-request: resolves sent_as (text vs template) for the
        # optimistic response and still surfaces `no_chat_template` inline. The
        # Respond.io send itself is deferred to the worker, so a 4xx (e.g. 401) lands
        # in the Respond outbox, not the operator's screen.
        pre = precheck_chat_send(
            db,
            identifier=identifier,
            respond_contact_id=respond_contact_id,
            chat_use_case=use_case,
        )

        # Only the out-of-window template path needs the entity portal/view link.
        # Resolve it here (sync) because the context_builder callable can't cross the
        # RQ boundary; in-window sends skip the cost entirely.
        precomputed_context: dict = {}
        if pre["sent_as"] == "template" and context_builder is not None:
            try:
                precomputed_context = context_builder(db, str(entity_id)) or {}
            except Exception:
                logger.exception(
                    "chat send context_builder failed for %s %s",
                    business_table,
                    entity_id,
                )
                precomputed_context = {}

        from app.services.queue_service import enqueue_job
        from app.tasks.respond_io_tasks import deliver_chat_message

        job = enqueue_job(
            deliver_chat_message,
            identifier,
            respond_contact_id,
            body.text,
            use_case,
            business_table,
            str(entity_id),
            sender_name,
            str(current_user.get("id") or "") or None,
            precomputed_context,
            queue_name="respond_io",
            job_timeout=180,
        )
        logger.info(
            "Enqueued chat send job %s (%s) for %s %s",
            job.id,
            pre["sent_as"],
            business_table,
            entity_id,
        )
        return {
            "sent_as": pre["sent_as"],
            "queued": True,
            "window_state": pre["window_state"],
        }

    return router
