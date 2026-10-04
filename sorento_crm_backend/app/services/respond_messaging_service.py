"""24h-window-aware Respond.io sending: plain text OR default WhatsApp template.

WhatsApp only delivers free-form messages inside the 24-hour customer service
window (contact's last *incoming* message < 24h ago). Respond.io still returns
success for sends outside the window - they are silently dropped. So every
auto-send branches up front:

- window open  → plain text (existing behaviour)
- window closed → the use case's default approved template
  (``respond_template_defaults``), params resolved from context vars.

Window source of truth is the Respond.io message list (chat_history misses
messages when ``is_human_intervened``); on API error we degrade to
chat_history; with no data at all the window is treated as closed (template is
the safe branch). A 23h margin avoids boundary drops.

Plan: docs/plans/PLAN-whatsapp-template-fallback.md
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.document_number import display_document_number

logger = logging.getLogger(__name__)


def _button_url_suffix(base: str, full_url: str) -> str:
    """Suffix appended after a dynamic URL button's static ``base``.

    Meta dynamic URL buttons are host+prefix-locked to ``base`` (e.g.
    ``https://fe-sorento.foundryx.my/``); the button parameter supplies only the
    rest of the path. The resolved CRM link (portal_url / form_url / …) can carry
    a DIFFERENT host than the button base - locally ``FRONTEND_BASE_URL`` is
    ``http://localhost:3000`` while the approved template's button is the prod
    domain. A literal prefix strip then fails and the WHOLE link (scheme+host
    included) is appended, producing
    ``https://fe-sorento.foundryx.my/http://localhost:3000/portal/...``.

    So strip by URL STRUCTURE: when the link has its own scheme+host, drop them
    and keep path+query+fragment (minus the leading slash the base ends with).
    Same-host links keep the literal-strip fast path so a base path segment
    beyond the host is preserved.
    """
    if not full_url:
        return ""
    if base and full_url.startswith(base):
        return full_url[len(base):]
    from urllib.parse import urlsplit

    parts = urlsplit(full_url)
    if not parts.scheme and not parts.netloc:
        # Already a bare path/suffix (no host to drop).
        return full_url.lstrip("/")
    tail = parts.path
    if parts.query:
        tail += f"?{parts.query}"
    if parts.fragment:
        tail += f"#{parts.fragment}"
    return tail.lstrip("/")

WINDOW_HOURS = 23
PARAM_MAX_LEN = 900

# How long a resolved "last incoming message" is reused before Respond.io is
# asked again. Every window lookup is a live 15s-timeout HTTP call, and a
# single drawer open resolves it more than once (header, then the send). The
# answer moves on a DAY scale, so a sub-minute reuse cannot hide a transition:
# the cache holds the FACT (when the contact last wrote), never the verdict -
# openness is recomputed against the current clock on every call.
WINDOW_CACHE_TTL_SECONDS = 45

# identifier -> (resolved_at_monotonic, last_incoming, source). Process-local
# and deliberately unsynchronised: a race only costs a duplicate upstream call,
# which is the thing we were already doing.
_window_cache: Dict[str, tuple] = {}


def _monotonic() -> float:
    """Indirection point so tests can advance the cache clock."""
    import time

    return time.monotonic()


def reset_window_cache() -> None:
    """Drop every cached window fact (tests; also safe at runtime)."""
    _window_cache.clear()

_URL_RE = re.compile(r"https?://\S+")
_WS_RUN_RE = re.compile(r"[ \t]{2,}")


class TemplateSendSkipped(Exception):
    """Window closed but no valid default template for the use case.

    Raised instead of silently dropping a plain text send into a closed
    window; callers log it as a failed integration log.
    """

    def __init__(self, use_case: str, reason: str):
        self.use_case = use_case
        self.reason = reason
        super().__init__(
            f"24h window closed and template send skipped for use case "
            f"'{use_case}': {reason}"
        )


def _attach_send_context(exc: Exception, request_payload: dict, window: dict, sent_as: str) -> None:
    """Stamp the attempted payload / window / branch onto a send exception so the
    caller's outbox log reflects what was actually attempted (text vs template),
    instead of a hardcoded default. Best-effort - never raises."""
    try:
        exc.request_payload = request_payload  # type: ignore[attr-defined]
        exc.window_state = window  # type: ignore[attr-defined]
        exc.sent_as = sent_as  # type: ignore[attr-defined]
    except Exception:
        pass


def sanitize_param(
    value: Optional[str], *, max_len: int = PARAM_MAX_LEN, flatten: bool = True
) -> str:
    """Coerce a value into a template parameter, length-bounded.

    ``flatten=True`` (WhatsApp template params): newlines/tabs/4+ spaces are
    rejected by WhatsApp, so newlines collapse to a single space and runs collapse.
    (Was ``" | "`` - dropped 2026-06-22 once the portal link moved to a dynamic URL
    button, so a flattened body reads as prose, not a piped run-on. See
    PLAN-whatsapp-url-button-templates.md.)
    ``flatten=False`` (in-window free text): newlines are legal and nicer, so
    keep them - only normalise tabs and bound length. See PLAN-whatsapp-inwindow-
    template-uniformity.md (richer-multiline decision).
    """
    text = str(value or "").strip()
    if not text:
        return "-"
    if flatten:
        text = re.sub(r"\s*\n+\s*", " ", text)
        text = text.replace("\t", " ")
        text = _WS_RUN_RE.sub(" ", text)
    else:
        text = text.replace("\t", " ")
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def extract_first_url(text: Optional[str]) -> Optional[str]:
    m = _URL_RE.search(text or "")
    if not m:
        return None
    # `\S+` greedily includes trailing sentence punctuation (e.g. a URL written
    # `…/{uuid}: status`); strip it so the extracted link is the bare URL. Hit by
    # portal deep links whose path ends in a UUID followed by a colon.
    return m.group(0).rstrip(").,:;!?'\"")


def _ms_to_utc(ts: Any) -> Optional[datetime]:
    try:
        ms = int(ts)
    except (TypeError, ValueError):
        return None
    if ms > 10**12:  # milliseconds
        return datetime.utcfromtimestamp(ms / 1000.0)
    return datetime.utcfromtimestamp(ms)


def _message_timestamp(item: dict) -> Optional[datetime]:
    """Best creation timestamp (UTC naive) for a Respond.io message item.

    Outgoing messages carry delivery ``status`` entries (epoch ms); incoming
    messages have ``status: []`` and instead encode their time in ``messageId``,
    which Respond emits as epoch MICROSECONDS. The earlier code only read
    ``status[0].timestamp`` and so silently dropped every inbound message - 
    making the 24h window look permanently closed even right after a reply.
    """
    statuses = item.get("status") or []
    if isinstance(statuses, list) and statuses:
        dts = [
            _ms_to_utc(s.get("timestamp"))
            for s in statuses
            if isinstance(s, dict) and s.get("timestamp") is not None
        ]
        dts = [d for d in dts if d is not None]
        if dts:
            return min(dts)  # 'pending'/creation time, not 'delivered'
    mid = item.get("messageId")
    if mid is not None:
        try:
            # microseconds -> milliseconds for _ms_to_utc's >1e12 branch.
            return _ms_to_utc(int(mid) // 1000)
        except (TypeError, ValueError):
            return None
    return None


def _last_incoming_from_respond(identifier: str) -> Optional[datetime]:
    """Latest incoming-message timestamp from the Respond.io API (UTC naive).

    Scans one page (newest 50). No incoming message in the page means the
    last reply is old → closed either way.
    """
    from app.services.integration_service import RespondClient

    raw = RespondClient().list_messages(identifier, limit=50)
    items = (raw or {}).get("items") or []
    latest: Optional[datetime] = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if (item.get("traffic") or "").lower() not in ("incoming", "inbound"):
            continue
        dt = _message_timestamp(item)
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    return latest


def _last_incoming_from_chat_history(
    db: Session,
    identifier: Optional[str],
    respond_contact_id: Optional[str] = None,
) -> Optional[datetime]:
    """Latest incoming-message timestamp from local chat_histories (UTC naive).

    chat_histories.contact_id stores the Respond.io id (e.g. "900000008"), NOT
    the internal respond_contacts.id (UUID). The send path passes the Respond
    ``identifier`` (the respond_io_id, optionally ``id:`` / ``phone:`` prefixed)
    but usually NOT respond_contact_id - so derive the respond_io_id straight
    from the identifier and match on it (+ phone when the contact resolves).
    Comparing the internal UUID directly never matched, so the fallback always
    returned None and the window falsely read CLOSED (template) on every
    API-error send.
    """
    from sqlalchemy import func as sa_func, or_

    from app.models.access import RespondContact
    from app.models.chat_history import ChatHistory

    respond_io_id = identifier.split(":", 1)[-1].strip() if identifier else ""

    contact = None
    if respond_contact_id:
        contact = (
            db.query(RespondContact)
            .filter(RespondContact.id == str(respond_contact_id))
            .first()
        )
    if contact is None and respond_io_id:
        contact = (
            db.query(RespondContact)
            .filter(RespondContact.respond_io_id == respond_io_id)
            .first()
        )

    rid = (contact.respond_io_id if contact else None) or respond_io_id
    conds = []
    if rid:
        conds.append(ChatHistory.contact_id == str(rid))
    if contact is not None and contact.phone_number:
        conds.append(ChatHistory.phone_number == contact.phone_number)
    if not conds:
        return None
    return (
        db.query(sa_func.max(ChatHistory.sent_at))
        .filter(ChatHistory.type == "incoming", or_(*conds))
        .scalar()
    )


def _resolve_last_incoming(
    db: Session,
    identifier: str,
    respond_contact_id: Optional[str],
) -> tuple:
    """(last incoming datetime | None, source) straight from upstream.

    Respond.io's message list is the source of truth; on API error we degrade
    to local chat_history, and with no data at all the window is treated as
    closed (the template branch is the safe one). Uncached - the caller decides.
    """
    try:
        return _last_incoming_from_respond(identifier), "respond_api"
    except Exception as e:
        logger.warning(
            "Window check: Respond.io list_messages failed for %s (%s); "
            "falling back to chat_history",
            identifier,
            e,
        )
    try:
        last_incoming = _last_incoming_from_chat_history(
            db, identifier, respond_contact_id
        )
    except Exception:
        logger.exception("Window check: chat_history fallback failed")
        return None, "none"
    return (last_incoming, "chat_history") if last_incoming is not None else (None, "none")


def get_window_state(
    db: Session,
    identifier: str,
    *,
    respond_contact_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Whether the contact's 24h WhatsApp window is open (23h margin).

    ``identifier`` is the Respond.io send identifier (same value the existing
    send paths use). ``respond_contact_id`` (internal ``respond_contacts.id``)
    enables the chat_history fallback when the Respond API errors.

    The upstream lookup is cached per identifier for ``WINDOW_CACHE_TTL_SECONDS``
    (see that constant). No cache busting on send: an OUTGOING message never
    opens the window - only the contact writing to us does - so nothing we do
    can invalidate the cached fact early.
    """
    now = datetime.utcnow()
    cache_key = str(identifier or "")
    cached = _window_cache.get(cache_key)
    if cached is not None and (_monotonic() - cached[0]) < WINDOW_CACHE_TTL_SECONDS:
        last_incoming, source = cached[1], cached[2]
    else:
        last_incoming, source = _resolve_last_incoming(db, identifier, respond_contact_id)
        _window_cache[cache_key] = (_monotonic(), last_incoming, source)

    is_open = bool(last_incoming and last_incoming >= now - timedelta(hours=WINDOW_HOURS))
    return {
        "open": is_open,
        "last_incoming_at": last_incoming.isoformat() if last_incoming else None,
        "checked_at": now.isoformat(),
        "source": source,
    }


def resolve_template_params(
    *,
    param_mapping: Dict[str, str],
    param_count: int,
    context_vars: Dict[str, Any],
    flatten: bool = True,
) -> List[str]:
    """Positional params ["1".."n"] resolved via mapping + sanitized.

    ``flatten=False`` keeps newlines in values (in-window free-text render).
    """
    params: List[str] = []
    for i in range(1, param_count + 1):
        var = param_mapping.get(str(i)) or ""
        params.append(sanitize_param(context_vars.get(var), flatten=flatten))
    return params


def build_template_request(
    db: Session,
    *,
    use_case: str,
    context_vars: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve the use case's default template and render it, without sending.

    Returns ``{template, params, button, request_payload}``; ``request_payload`` is the
    ``{"message": {"type": "whatsapp_template", ...}}`` block the Respond outbox renders.
    Raises TemplateSendSkipped when no valid default is configured."""
    from app.services.respond_template_service import (
        BUTTON_URL_KEY,
        button_url_base,
        get_default_row,
        is_copy_code_button,
        serialize_default,
        url_button_of,
    )

    row = get_default_row(db, use_case)
    serialized = serialize_default(use_case, row)
    if not serialized["is_valid"] or row is None or row.template is None:
        if serialized["template_removed"]:
            reason = "configured template was removed on sync"
        elif serialized["template_id"] is None:
            reason = "no default template configured"
        elif serialized["template_status"] != "approved":
            reason = f"template is not approved (status: {serialized['template_status']})"
        else:
            reason = "param mapping does not cover every template parameter"
        raise TemplateSendSkipped(use_case, reason)

    template = row.template
    mapping = dict(row.param_mapping or {})

    # Dynamic URL button: resolve the link var to the suffix appended after the
    # button's static base (Meta dynamic URL buttons carry only a suffix). When a
    # button carries the link, strip that URL from the body so it is not duplicated
    # as dead (unclickable) text inside a body param.
    button_payload: Optional[dict] = None
    url_button = url_button_of(getattr(template, "components", None))
    if url_button and is_copy_code_button(url_button):
        # COPY_CODE auth button: Meta copies the button PARAMETER (the OTP code),
        # not a CRM link. Reuse the code already mapped to the body so the copy
        # button works without the admin mapping any link variable.
        button_payload = {
            "text": url_button.get("text") or "Copy code",
            "url": str(url_button.get("url") or ""),
            "suffix": str(context_vars.get("otp_code") or "").strip(),
        }
    elif url_button:
        link_var = mapping.get(BUTTON_URL_KEY)
        full_url = str(context_vars.get(link_var) or "").strip() if link_var else ""
        base = button_url_base(url_button) or ""
        suffix = _button_url_suffix(base, full_url)
        button_payload = {
            "text": url_button.get("text") or "View",
            "url": str(url_button.get("url") or ""),
            "suffix": suffix,
        }
        logger.info(
            "template button URL resolved: use_case=%s link_var=%s base=%s "
            "full_url=%s suffix=%s final=%s",
            use_case,
            link_var,
            base,
            full_url,
            suffix,
            f"{base}{suffix}",
        )
        if full_url:
            stripped_vars = dict(context_vars)
            for k, v in list(stripped_vars.items()):
                if isinstance(v, str) and full_url in v:
                    stripped_vars[k] = v.replace(full_url, "").rstrip()
            context_vars = stripped_vars

    params = resolve_template_params(
        param_mapping=mapping,
        param_count=template.param_count,
        context_vars=context_vars,
    )
    # Full resolved template payload - identical shape whether the send below
    # succeeds OR fails, so the Respond outbox renders the template name + filled
    # params (not "-") in every case. body_text lets the UI render the message
    # with variables substituted.
    request_payload = {
        "message": {
            "type": "whatsapp_template",
            "template_name": template.name,
            "template_id": str(template.id),
            "use_case": use_case,
            "language_code": template.language_code,
            "body_text": template.body_text,
            "parameters": params,
            "button": button_payload,
        }
    }
    return {
        "template": template,
        "params": params,
        "button": button_payload,
        "request_payload": request_payload,
    }


def send_template_for_use_case(
    db: Session,
    *,
    identifier: str,
    use_case: str,
    context_vars: Dict[str, Any],
) -> Dict[str, Any]:
    """Send the use case's default template. Raises TemplateSendSkipped when
    no valid default is configured."""
    from app.services.integration_service import RespondClient

    built = build_template_request(db, use_case=use_case, context_vars=context_vars)
    template = built["template"]
    params = built["params"]
    button_payload = built["button"]
    request_payload = built["request_payload"]
    # Single-workspace today: RespondClient() resolves the default workspace key.
    # Switch to RespondClient.for_identifier(db, identifier) when multi-workspace
    # routing per contact is needed.
    try:
        response = RespondClient().send_template_message(
            identifier,
            channel_id=template.channel.respond_channel_id,
            template_name=template.name,
            language_code=template.language_code,
            body_text=template.body_text,
            parameters=params,
            button=button_payload,
        )
    except Exception as e:
        _attach_send_context(e, request_payload, {"open": False}, "template")
        raise
    return {
        "response": response,
        "template_name": template.name,
        "template_id": str(template.id),
        "params": params,
        "button": button_payload,
        "request_payload": request_payload,
    }


def render_in_window_text(
    db: Session,
    *,
    use_case: str,
    context_vars: Dict[str, Any],
    fallback_text: str,
) -> str:
    """Render the use-case default template body for an in-window (free-text)
    send, so the message reads the same as the out-of-window template.

    Returns ``fallback_text`` unchanged when the use case has no valid configured
    default - uniformity rolls out per use-case, never breaks an unconfigured one.
    Newlines in values are preserved (``flatten=False``). Best-effort: any
    resolution error degrades to the raw text.
    """
    try:
        from app.services.respond_template_service import (
            get_default_row,
            serialize_default,
        )
        from app.services.respond_chat_template_service import render_filled_body
        from app.services.respond_template_service import url_button_of

        row = get_default_row(db, use_case)
        serialized = serialize_default(use_case, row)
        if not serialized["is_valid"] or row is None or row.template is None:
            return fallback_text
        template = row.template
        has_button = url_button_of(getattr(template, "components", None)) is not None
        # Decouple in-window from button templates (decision 2026-06-22): a button
        # template has a deliberately short body that can't carry the rich update,
        # and the button doesn't render in free text. So in-window send the caller's
        # full text as-is (real newlines + clickable URL inline).
        #
        # EXCEPTION (complaint, decision 2026-06-23): the structured complaint
        # template's body IS the rich update (Project / Customer / Enquiry / DO /
        # Update lines), so render it in-window too for cross-window uniformity.
        # Free text has no button, so append the link inline from portal_url.
        if has_button and use_case != "complaint":
            return fallback_text
        params = resolve_template_params(
            param_mapping=dict(row.param_mapping or {}),
            param_count=template.param_count,
            context_vars=context_vars,
            flatten=False,
        )
        rendered = render_filled_body(template.body_text, params)
        if has_button and use_case == "complaint":
            link = str(context_vars.get("portal_url") or "").strip()
            if link and link not in rendered:
                rendered = f"{rendered}\n\n{link}"
        return rendered
    except Exception:
        logger.exception(
            "render_in_window_text: falling back to raw text for use case '%s'",
            use_case,
        )
        return fallback_text


def send_text_or_template(
    db: Session,
    *,
    identifier: str,
    text: str,
    use_case: str,
    context_vars: Optional[Dict[str, Any]] = None,
    respond_contact_id: Optional[str] = None,
    render_template_in_window: bool = True,
) -> Dict[str, Any]:
    """The choke point for every CRM auto-send to a Respond.io contact.

    ``render_template_in_window=False`` sends the caller's ``text`` verbatim inside the
    window (ideation status update: the owner asked for a natural session message).

    Returns {sent_as, response, window_state, template_name?, request_payload}
  - callers attach ``request_payload`` to their integration log so template
    sends are distinguishable. Raises on Respond errors and on
    TemplateSendSkipped (so RQ/json callers record a failed log).

    Variable resolution (``message`` / ``portal_url`` defaulting) happens ONCE,
    before the window branch, so in-window text and out-of-window template render
    from identical context - the uniformity guarantee.
    """
    from app.services.integration_service import RespondClient

    window = get_window_state(db, identifier, respond_contact_id=respond_contact_id)

    vars_resolved = dict(context_vars or {})
    vars_resolved.setdefault("message", text)
    if not vars_resolved.get("portal_url"):
        url = extract_first_url(text)
        if url:
            vars_resolved["portal_url"] = url

    # Conversation SLA has no entity number - when a template maps a slot to
    # `entity_number`, fill it with the SLA's contact name / phone instead of "-".
    # (Form SLA rows already carry a real entity_number, so this never overrides them.)
    if use_case in ("sla_assignment", "sla_escalation") and not vars_resolved.get("entity_number"):
        contact_label = vars_resolved.get("contact_name")
        if not contact_label:
            try:
                from app.models.access import RespondContact

                ident = (
                    identifier.split(":", 1)[-1] if identifier else (respond_contact_id or "")
                )
                contact = None
                if respond_contact_id:
                    contact = (
                        db.query(RespondContact)
                        .filter(RespondContact.id == respond_contact_id)
                        .first()
                    )
                if contact is None and ident:
                    contact = (
                        db.query(RespondContact)
                        .filter(RespondContact.respond_io_id == ident)
                        .first()
                    )
                if contact is not None:
                    contact_label = (
                        contact.name
                        or " ".join(filter(None, [contact.first_name, contact.last_name]))
                        or contact.phone_number
                    )
            except Exception:
                logger.exception("send_text_or_template: SLA contact fallback failed")
        if contact_label:
            vars_resolved["entity_number"] = contact_label

    if window["open"]:
        # Render the same template body the closed-window branch would send, so
        # the message is uniform across the 24h boundary. Falls back to raw text
        # when the use case has no configured default.
        out_text = (
            render_in_window_text(
                db, use_case=use_case, context_vars=vars_resolved, fallback_text=text
            )
            if render_template_in_window
            else text
        )
        text_payload = {"message": {"type": "text", "text": out_text}}
        try:
            response = RespondClient().send_message(identifier, out_text)
        except Exception as e:
            # Attach the actual attempted payload + window so callers log the
            # truth in the Respond outbox (was a hardcoded text default before).
            _attach_send_context(e, text_payload, window, "text")
            raise
        return {
            "sent_as": "text",
            "response": response,
            "window_state": window,
            "request_payload": text_payload,
            # What the contact actually received (in-window free text).
            "rendered_text": out_text,
        }

    try:
        result = send_template_for_use_case(
            db,
            identifier=identifier,
            use_case=use_case,
            context_vars=vars_resolved,
        )
    except Exception as e:
        # Window closed -> a TEMPLATE was attempted. send_template_for_use_case
        # already stamps the FULL resolved payload (name + filled params) when the
        # template resolved and only the HTTP send failed. Only fall back to a
        # minimal payload when nothing was attached (e.g. TemplateSendSkipped:
        # no template configured), so the outbox still shows it as a template row.
        if not hasattr(e, "request_payload"):
            _attach_send_context(
                e,
                {
                    "message": {
                        "type": "whatsapp_template",
                        "use_case": use_case,
                        "parameters_context": vars_resolved,
                    }
                },
                window,
                "template",
            )
        raise
    # Render the template body with the resolved params, so callers can echo the
    # exact message the contact received (params filled) - same render as the
    # manual-template path (render_filled_body over {{n}} slots).
    from app.services.respond_chat_template_service import render_filled_body

    _body_text = (result.get("request_payload") or {}).get("message", {}).get("body_text") or ""
    rendered_text = render_filled_body(_body_text, result["params"])
    return {
        "sent_as": "template",
        "response": result["response"],
        "window_state": window,
        "template_name": result["template_name"],
        "rendered_text": rendered_text,
        "request_payload": {
            "message": {
                "type": "whatsapp_template",
                "template_name": result["template_name"],
                "template_id": result["template_id"],
                "use_case": use_case,
                # body_text lets the Respond outbox show the filled message, the
                # same as the payload build_template_request stamps on a failure.
                "body_text": _body_text,
                "parameters": result["params"],
                "button": result.get("button"),
            },
            "window_state": window,
        },
    }


def build_context_vars(
    db: Session,
    *,
    use_case: str,
    business_id: str,
    identifier: str,
) -> Dict[str, Any]:
    """Best-effort variable resolution for auto-sends (RQ tasks / sync sites).

    Never raises - a missing var resolves to "-" at param fill time.
    """
    vars_out: Dict[str, Any] = {}
    try:
        from app.models.access import RespondContact

        ident = identifier.split(":", 1)[-1] if identifier else ""
        contact = (
            db.query(RespondContact)
            .filter(RespondContact.respond_io_id == ident)
            .first()
        )
        if contact:
            vars_out["contact_name"] = (
                contact.name
                or " ".join(filter(None, [contact.first_name, contact.last_name]))
                or contact.phone_number
            )
    except Exception:
        logger.exception("build_context_vars: contact lookup failed")

    try:
        # The chat panels use ``<base>_chat`` use cases (stock_inquiry_chat, …) but
        # the entity lookup keys off the base name - normalize so a chat template's
        # {{entity_number}} etc. resolves the same as the status-update send.
        entity_use_case = use_case[:-5] if use_case.endswith("_chat") else use_case
        if entity_use_case == "complaint":
            from app.models.complaints import Complaint

            row = db.query(Complaint).filter(Complaint.id == business_id).first()
            if row:
                vars_out["entity_number"] = row.complaint_number
                vars_out["status"] = row.status
                vars_out["reason"] = row.rejection_reason
                # Discrete fields for the structured complaint template (Project /
                # Customer / Delivery Order lines). Unambiguous row columns, so
                # auto-resolved here; the action-specific `update` core + portal_url
                # are threaded via extra_context_vars (can't be reconstructed from
                # the row - reply vs status-change touch the same row).
                vars_out["project"] = row.project_title
                vars_out["customer"] = row.customer_name
                vars_out["delivery_order"] = row.delivery_order_number
        elif entity_use_case == "stock_inquiry":
            from app.models.procurement import StockInquiry

            row = db.query(StockInquiry).filter(StockInquiry.id == business_id).first()
            if row:
                # {{entity_number}} is what the CONTACT reads, so it carries the
                # revision (UAC N1/N5). The stored column stays bare.
                vars_out["entity_number"] = display_document_number(row) or row.inquiry_number
                vars_out["status"] = row.status
                vars_out["reason"] = row.rejection_reason
                # Discrete fields for the structured stock-inquiry template (Customer /
                # Project lines). StockInquiry stores these as project_customer /
                # project_name (no customer_name/project_title columns).
                vars_out["customer"] = row.project_customer
                vars_out["project"] = row.project_name
                vars_out["product_code"] = row.product_code
        elif entity_use_case in ("purchase_request", "sponsorship_form"):
            from app.models.procurement import PurchaseRequestHeader

            row = (
                db.query(PurchaseRequestHeader)
                .filter(PurchaseRequestHeader.id == business_id)
                .first()
            )
            if row:
                vars_out["entity_number"] = display_document_number(row) or row.request_number
                vars_out["status"] = row.approval_status or row.status
                # Rejection reason lives in approval_comments (public/in-system
                # approval flows write the reviewer's note there).
                vars_out["reason"] = row.approval_comments
                # Discrete fields for the structured PR/SF template (Customer /
                # Project lines) - same unambiguous row columns as complaint.
                vars_out["customer"] = row.customer_name
                vars_out["project"] = row.project_title
        elif entity_use_case == "price_tag_update":
            from app.models.price_tag import PriceTagRequest
            from app.services.portal_service import PortalService

            row = (
                db.query(PriceTagRequest)
                .filter(PriceTagRequest.id == business_id)
                .first()
            )
            if row:
                vars_out["entity_number"] = row.doc_number
                vars_out["status"] = row.status
                vars_out["portal_url"] = (
                    PortalService(db).submission_link(
                        row.contact_id, "price_tag_request", str(row.id)
                    )
                    or ""
                )
    except Exception:
        logger.exception("build_context_vars: entity lookup failed (%s)", use_case)

    return {k: v for k, v in vars_out.items() if v}


def use_case_for_purchase_request(header) -> str:
    """purchase_requests rows carry both PRs and sponsorship forms."""
    return (
        "sponsorship_form"
        if getattr(header, "request_type", None) == "sponsorship_form"
        else "purchase_request"
    )
