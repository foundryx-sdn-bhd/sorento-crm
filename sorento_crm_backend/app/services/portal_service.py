"""User submission portal service.

Owns:
- Portal token issuance and validation (7 day TTL, contact-scoped, revocable).
- OTP request / verification via Respond.io once a token expires.
- List, create, update, submit and attachment helpers across the four supported
  submission types (complaint, stock_inquiry, purchase_request, sponsorship_form).

Editing rules:
- A submission is editable from the portal when ``portal_draft_at IS NOT NULL``
  or when ``status == 'rejected'``.
- Saving as draft sets ``portal_draft_at`` and does not trigger team notifications.
- Submitting clears ``portal_draft_at`` and triggers the existing per-type
  notification flow (same one used by the legacy external-create endpoints).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.access import RespondContact
from app.models.complaints import Complaint
from app.models.portal import PortalOtpCode, PortalRevisionDraft, PortalToken
from app.models.procurement import (
    PurchaseRequestHeader,
    PurchaseRequestLine,
    StockInquiry,
)
from app.models.resources import AttachmentType
from app.services.crockford import (
    CROCKFORD_ALPHABET,
    CROCKFORD_TOKEN_LEN,
    crockford_token,
)
from app.services.document_number import display_document_number
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_validation_error,
)
from app.services.integration_service import RespondClient
from app.services.validators import validate_project_value

logger = logging.getLogger(__name__)


PORTAL_TOKEN_TTL = timedelta(days=7)
# Device trust: OTP-verified tokens live 30 days, sliding - each authenticated
# request re-extends to now+30d (throttled to ~once per day via the threshold
# below). Active contacts never re-verify; dormant ones re-OTP after 30 days.
PORTAL_VERIFIED_TOKEN_TTL = timedelta(days=30)
PORTAL_SLIDE_THRESHOLD = timedelta(days=29)
OTP_TTL = timedelta(minutes=10)
OTP_REQUEST_COOLDOWN = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
# A code row that can never exist, used to pay for the same DB round trips an
# unknown/ineligible number's verify would for a real one (B2 timing parity,
# identity S1 security round, #1280).
NIL_OTP_ID = "00000000-0000-0000-0000-000000000000"
# Hard daily cap per contact - the slug URL is bookmarkable/shareable, so an
# attacker with a leaked slug could otherwise spam the contact with OTP sends.
OTP_DAILY_CAP = 10
# The kinds the GENERIC portal machinery serves: the submission CRUD below, the
# neighbours, the revision policy rows, the attachment linkage. A price tag
# request is served by NONE of them - it has its own routes, its own service and
# no revision policy - so it is deliberately absent. It was added here once, and
# that made the generic listing answer `200 []` for it instead of refusing, put a
# revision-config row on screen that configures nothing, and left the router
# include ORDER as the only thing keeping the generic handler off the real price
# tag writes.
SUPPORTED_TYPES = ("complaint", "stock_inquiry", "purchase_request", "sponsorship_form")

# The kinds an ADMIN may grant on a contact access type, which is a different
# question: it is about what the portal LANDING offers a contact, and the price
# tag request has a landing card like the rest. `portal_form_types` is validated
# against this one. `customer_asks` (chatbot stock ask v2 fix round 5, owner 29 Sep) is a
# landing kind with no form behind it: the stock asks of the linked sales agent's customers,
# switched per contact like Price Tag Request and off until an admin turns it on.
CUSTOMER_ASKS_FORM_TYPE = "customer_asks"
# `conversation` (lane SALES-CONVO, owner 30 Sep): the WhatsApp threads of the linked sales
# agent's customers, read-only. Same switch and the same agent rule as `customer_asks`.
CONVERSATION_FORM_TYPE = "conversation"
# The kinds only a contact linked to a sales agent can hold, whatever the switch says.
AGENT_ONLY_FORM_TYPES = (CUSTOMER_ASKS_FORM_TYPE, CONVERSATION_FORM_TYPE)
GRANTABLE_PORTAL_FORM_TYPES = SUPPORTED_TYPES + (
    "price_tag_request",
    "sales_opportunity",
    CUSTOMER_ASKS_FORM_TYPE,
    CONVERSATION_FORM_TYPE,
)
PORTAL_ATTACHMENT_TYPE_CODE = "portal_submission"

# Crockford base32 alphabet - excludes I, L, O, U to eliminate look-alike
# transcription errors (I↔l↔1, O↔0, U↔V). Tokens are user-visible in URLs and
# occasionally retyped from screenshots, so we trade ~14% entropy density vs
# base64url for unambiguous chars. 48 chars × 5 bits = 240 bits - equivalent to
# token_urlsafe(30).
#
# The alphabet itself now lives in `app.services.crockford`, shared with the
# onboarding intake token: two token families each carrying their own copy of
# "the Crockford alphabet" is how one of them silently gains an `O`.
_CROCKFORD_ALPHABET = CROCKFORD_ALPHABET
_CROCKFORD_TOKEN_LEN = CROCKFORD_TOKEN_LEN
# Contact slug: 10 chars × 5 bits = 50 bits - unguessable identity hint for the
# stable URL /portal/c/{slug}. NOT a credential: knowing it only lets you
# request an OTP that goes to the contact's own WhatsApp.
_PORTAL_SLUG_LEN = 10


def _crockford_token(length: int = _CROCKFORD_TOKEN_LEN) -> str:
    return crockford_token(length)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class PortalAuthError(Exception):
    """Raised when a portal token is missing, expired, or revoked."""


class PortalService:
    def __init__(self, db: Session):
        self.db = db

    def _resolve_contact(self, contact_id: str) -> RespondContact:
        """Resolve a RespondContact by internal `id` or `respond_io_id`.

        External callers (MCP/n8n) pass the Respond.io contact id (e.g. "900000008"),
        while internal endpoints pass the internal UUID. Accept either; raise NOT_FOUND
        if neither matches.
        """
        contact = (
            self.db.query(RespondContact)
            .filter(
                or_(
                    RespondContact.id == contact_id,
                    RespondContact.respond_io_id == contact_id,
                )
            )
            .first()
        )
        if contact is None:
            raise handle_not_found("Contact", contact_id)
        return contact

    # ---------- Contact slug (stable bookmarkable URL) ----------

    def get_or_create_slug(self, contact: RespondContact) -> str:
        """Return the contact's stable portal slug, lazily minting it.

        The slug is immutable once minted - it is the bookmarkable identity
        behind /portal/c/{slug}. Retries on the (astronomically unlikely)
        unique collision.
        """
        existing = (contact.portal_slug or "").strip()
        if existing:
            return existing
        for _ in range(5):
            candidate = _crockford_token(_PORTAL_SLUG_LEN)
            clash = (
                self.db.query(RespondContact.id)
                .filter(RespondContact.portal_slug == candidate)
                .first()
            )
            if clash is None:
                contact.portal_slug = candidate
                self.db.commit()
                return candidate
        raise handle_validation_error("Could not allocate a portal slug. Try again.")

    def contact_by_slug(self, slug: str) -> Optional[RespondContact]:
        slug = (slug or "").strip()
        if not slug:
            return None
        return (
            self.db.query(RespondContact)
            .filter(RespondContact.portal_slug == slug)
            .first()
        )

    def whatsapp_number_for_contact(self, contact: RespondContact) -> Optional[str]:
        """Business WhatsApp number for the contact's workspace (wa.me hatch).

        Workspace column first, env fallback. Digits only.
        """
        number = None
        ws = getattr(contact, "workspace", None)
        if ws is not None:
            number = (getattr(ws, "whatsapp_number", None) or "").strip()
        if not number:
            number = (getattr(settings, "portal_whatsapp_number", None) or "").strip()
        digits = "".join(ch for ch in number if ch.isdigit())
        return digits or None

    def identity_hint(self, contact: RespondContact) -> dict:
        """Public identity-hint shared by slug-info and token-info: contact name
        + masked phone for owner recognition + business WhatsApp for the wa.me
        hatch. The name lets the verify card say "this form belongs to {name}"
        so a second device / other salesperson knows whose login to use."""
        return {
            "name": self._contact_display_name(contact),
            "masked_phone": self._mask_phone(contact.phone_number),
            "whatsapp_number": self.whatsapp_number_for_contact(contact),
        }

    @staticmethod
    def _contact_display_name(contact: RespondContact) -> Optional[str]:
        name = (
            (contact.name or "").strip()
            or " ".join(
                p for p in [(contact.first_name or "").strip(), (contact.last_name or "").strip()] if p
            ).strip()
        )
        return name or None

    def slug_info(self, slug: str) -> dict:
        """Public identity hint behind GET /slug-info/{slug}.

        Returns just enough to render the verify page: who to OTP
        (contact/space ids), a masked phone for recognition, and the business
        WhatsApp number for the escape hatch. 404s with no detail on unknown
        slugs - never confirm revoked-vs-missing.
        """
        contact = self.contact_by_slug(slug)
        if contact is None:
            raise handle_not_found("Portal link", slug)
        # space_id: prefer what the latest token used; fall back to the
        # workspace's Respond space identifier (NOT the workspace PK - the
        # system-wide convention is space_id == respond_workspaces.space_id).
        latest = (
            self.db.query(PortalToken)
            .filter(PortalToken.contact_id == contact.id)
            .order_by(PortalToken.created_at.desc())
            .first()
        )
        if latest is not None:
            space_id = latest.space_id
        else:
            ws = getattr(contact, "workspace", None)
            space_id = (getattr(ws, "space_id", None) or "") if ws is not None else ""
        return {
            "contact_id": contact.id,
            "space_id": space_id,
            **self.identity_hint(contact),
        }

    # ---------- Token lifecycle ----------

    def mint_token(
        self, contact_id: str, space_id: str, *, is_impersonation: bool = False
    ) -> PortalToken:
        contact_id = (contact_id or "").strip()
        space_id = (space_id or "").strip()
        if not contact_id or not space_id:
            raise handle_validation_error("contact_id and space_id are required.")
        contact = self._resolve_contact(contact_id)
        # Every minted link should carry the stable slug URL - mint it now.
        self.get_or_create_slug(contact)
        token = PortalToken(
            token=_crockford_token(),
            contact_id=contact.id,
            space_id=space_id,
            expires_at=_utcnow() + PORTAL_TOKEN_TTL,
            is_impersonation=is_impersonation,
        )
        self.db.add(token)
        self.db.commit()
        self.db.refresh(token)
        return token

    def revoke_token(self, token_value: str) -> bool:
        """Server-side logout: revoke the token row. Idempotent."""
        row = (
            self.db.query(PortalToken)
            .filter(PortalToken.token == (token_value or "").strip())
            .first()
        )
        if row is None:
            return False
        if row.revoked_at is None:
            row.revoked_at = _utcnow()
            self.db.commit()
        return True

    def get_or_mint_token(self, contact_id: str, space_id: str) -> tuple[PortalToken, bool]:
        """Return latest live token for (contact_id, space_id) or mint a new one.

        Returns (token, reused). A token is "live" if revoked_at is null and expires_at > now.
        """
        contact_id = (contact_id or "").strip()
        space_id = (space_id or "").strip()
        if not contact_id or not space_id:
            raise handle_validation_error("contact_id and space_id are required.")
        contact = self._resolve_contact(contact_id)
        live = (
            self.db.query(PortalToken)
            .filter(
                PortalToken.contact_id == contact.id,
                PortalToken.space_id == space_id,
                PortalToken.revoked_at.is_(None),
                PortalToken.expires_at > _utcnow(),
            )
            .order_by(PortalToken.expires_at.desc())
            .first()
        )
        if live is not None:
            return live, True
        return self.mint_token(contact.id, space_id), False

    def _build_send_message_text(
        self, contact: RespondContact, portal_url: str, expires_at: datetime
    ) -> str:
        name = (contact.name or contact.first_name or "").strip()
        greeting = f"Hi {name}," if name else "Hi,"
        expires_human = expires_at.strftime("%b %d, %Y")
        return (
            f"{greeting} here is your secure portal link:\n"
            f"{portal_url}\n\n"
            f"The link expires on {expires_human}. Reply if you need help."
        )

    def send_link_via_respond_io(
        self,
        contact_id: str,
        space_id: str,
        base_url: Optional[str] = None,
        submission_type: Optional[str] = None,
    ) -> dict:
        """Mint or reuse a portal token and deliver it via Respond.io chat.

        Raises httpx.HTTPStatusError on upstream failure (caller maps to 502).
        """
        contact = self._resolve_contact(contact_id)
        respond_io_id = (contact.respond_io_id or "").strip()
        if not respond_io_id:
            raise handle_validation_error(
                "Contact has no Respond.io identifier; cannot send link."
            )
        token, reused = self.get_or_mint_token(contact.id, space_id)
        portal_url = self.build_portal_url(
            token.token, base_url, submission_type, token_row=token
        )
        text = self._build_send_message_text(contact, portal_url, token.expires_at)
        # Log the attempt to the Respond outbox whether it succeeds or fails.
        # This send previously wrote nothing, so a 401/403 here left no trace in
        # integration_logs at all - the admin saw an error and the outbox stayed
        # empty. Keep the raise: the caller maps it to a 502 so the admin gets
        # immediate feedback rather than a silent queue.
        from app.services.integration_service import log_respond_send

        request_payload = {"message": {"type": "text", "text": text}}
        try:
            response = RespondClient().send_message(respond_io_id, text)
        except Exception as e:
            log_respond_send(
                self.db,
                business_table="portal_tokens",
                business_id=str(token.id),
                identifier=respond_io_id,
                request_payload=request_payload,
                exc=e,
            )
            raise
        log_respond_send(
            self.db,
            business_table="portal_tokens",
            business_id=str(token.id),
            identifier=respond_io_id,
            request_payload=request_payload,
            response=response,
        )
        return {
            "token": token.token,
            "expires_at": token.expires_at.isoformat(),
            "portal_url": portal_url,
            "reused": reused,
            "sent": True,
        }

    def resolve_token(self, token_value: str) -> PortalToken:
        if not token_value or not token_value.strip():
            raise PortalAuthError("Missing portal token.")
        row = (
            self.db.query(PortalToken)
            .filter(PortalToken.token == token_value.strip())
            .first()
        )
        if row is None:
            raise PortalAuthError("Invalid portal token.")
        if row.revoked_at is not None:
            raise PortalAuthError("Portal token revoked.")
        if row.expires_at <= _utcnow():
            raise PortalAuthError("Portal token expired. Verify with OTP to continue.")
        if getattr(row, "verified_at", None) is None:
            # First-visit OTP gate. Admin-issued tokens (Send via Respond.io / QR /
            # copy-link) start unverified; the contact must verify once via OTP
            # before the token grants access. After verify_otp, all unrevoked tokens
            # for this contact are marked verified, so this token works going forward.
            raise PortalAuthError(
                "Portal access requires OTP verification. Verify with OTP to continue."
            )
        # Sliding device trust: verified tokens re-extend to now+30d on use.
        # The 29d threshold throttles the write to roughly once per day.
        # Impersonation tokens are excluded - an admin browsing as a contact
        # must not mint themselves an immortal credential.
        if not getattr(row, "is_impersonation", False):
            now = _utcnow()
            if row.expires_at < now + PORTAL_SLIDE_THRESHOLD:
                row.expires_at = now + PORTAL_VERIFIED_TOKEN_TTL
                self.db.commit()
        return row

    def build_portal_url(
        self,
        token: str,
        base_url_override: Optional[str] = None,
        submission_type: Optional[str] = None,
        *,
        token_row: Optional[PortalToken] = None,
    ) -> str:
        """Build the link sent to contacts.

        Durable form: /portal/c/{slug}?token={token} - the token half expires,
        the slug half keeps working as a bookmarkable re-entry point. Falls
        back to the legacy /portal?token= shape when the token row or slug
        cannot be resolved (e.g. unit tests minting bare tokens).

        Pass ``token_row`` when the caller already holds the freshly minted
        PortalToken to skip the redundant lookup SELECT.
        """
        base = (base_url_override or "").strip().rstrip("/")
        if not base:
            base = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        kind = (submission_type or "").strip().lower()
        if kind and kind not in SUPPORTED_TYPES:
            raise handle_validation_error(
                f"Unsupported submission type: {submission_type!r}. "
                f"Allowed: {', '.join(SUPPORTED_TYPES)}."
            )
        slug = None
        row = token_row
        if row is None:
            row = (
                self.db.query(PortalToken)
                .filter(PortalToken.token == token)
                .first()
            )
        if row is not None:
            contact = (
                self.db.query(RespondContact)
                .filter(RespondContact.id == row.contact_id)
                .first()
            )
            if contact is not None:
                slug = self.get_or_create_slug(contact)
        path = f"/portal/c/{slug}?token={token}" if slug else f"/portal?token={token}"
        if kind:
            path += f"&type={kind}"
        return f"{base}{path}" if base else path

    def submission_link(
        self,
        contact_id: Optional[str],
        submission_type: str,
        entity_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Optional[str]:
        """Deep link to a contact's submission in the portal.

        Path form ``/portal/c/{slug}/{type}/{entity_id}`` (FE route
        ``app/(auth)/portal/c/[slug]/[type]/[id]``) so a customer status message
        lands the contact directly on that complaint / request / inquiry, where
        they can act/resubmit (portal auth handled by the route via OTP). When
        ``entity_id`` is omitted it links to the type's portal index
        ``/portal/c/{slug}/{type}``. Returns None when the contact/slug can't be
        resolved - callers fall back to the read-only view link.
        """
        try:
            cid = (str(contact_id) if contact_id else "").strip()
            if not cid:
                return None
            contact = self._resolve_contact(cid)
            slug = (self.get_or_create_slug(contact) or "").strip()
            if not slug:
                return None
            # Resolve an absolute base the same way the view-link builders do, so
            # the link is never relative even when frontend_base_url is unset.
            if not (base_url or "").strip():
                base_url = (getattr(settings, "frontend_base_url", None) or "").strip()
            if not (base_url or "").strip():
                from app.models.user import SystemSetting

                sys_settings = self.db.query(SystemSetting).first()
                if sys_settings and getattr(sys_settings, "website_url", None):
                    base_url = (sys_settings.website_url or "").strip()
            base = (base_url or "").rstrip("/")
            kind = (submission_type or "").strip().lower()
            path = f"/portal/c/{slug}/{kind}"
            eid = (str(entity_id) if entity_id else "").strip()
            if eid:
                path += f"/{eid}"
            return f"{base}{path}" if base else path
        except Exception:
            logger.warning(
                "submission_link: could not build portal link for contact %s; "
                "caller will fall back to view link.",
                contact_id,
            )
            return None

    # ---------- OTP flow ----------

    def create_and_dispatch_otp(
        self,
        contact: RespondContact,
        space_id: str,
        text_template: str,
        task,
        *,
        dispatch_inline: bool = False,
    ) -> PortalOtpCode:
        """Cooldown + daily-cap check, code creation and Respond.io dispatch.

        Shared by the portal's own OTP request and phone sign-in (identity S1,
        #1280) - the two differ only in the WhatsApp copy (``text_template``,
        interpolating ``{code}``) and the RQ task that carries it. Raises
        ``AppException`` (400) on a cooldown/cap hit or an enqueue failure;
        phone sign-in swallows both (AC-21 never changes its 200 answer),
        the portal's own ``request_otp`` lets them surface as before.

        ``dispatch_inline`` (security round S1, #1280): phone sign-in's
        request-code route now enqueues ONE job for every number, known or
        not (so enqueue timing carries no eligibility tell) - that job is
        already running on the ``respond_io`` queue by the time it reaches
        here, so it calls ``task`` directly instead of enqueuing a SECOND job.
        The portal's own ``request_otp`` runs inside an HTTP request, not a
        job, so it keeps the default (queue it).
        """
        # Rate-limit: at most one outstanding OTP per contact within cooldown.
        recent = (
            self.db.query(PortalOtpCode)
            .filter(PortalOtpCode.contact_id == contact.id)
            .order_by(PortalOtpCode.created_at.desc())
            .first()
        )
        if recent is not None and (_utcnow() - recent.created_at) < OTP_REQUEST_COOLDOWN:
            raise handle_validation_error("Please wait before requesting another verification code.")

        # Daily cap: the slug URL is shareable, so cap sends per contact per
        # 24h to stop OTP-spam harassment via a leaked link.
        sent_today = (
            self.db.query(PortalOtpCode)
            .filter(
                PortalOtpCode.contact_id == contact.id,
                PortalOtpCode.created_at >= _utcnow() - timedelta(hours=24),
            )
            .count()
        )
        if sent_today >= OTP_DAILY_CAP:
            raise handle_validation_error(
                "Daily verification limit reached. Please try again tomorrow."
            )

        code = f"{secrets.randbelow(1_000_000):06d}"
        otp = PortalOtpCode(
            contact_id=contact.id,
            space_id=space_id,
            code_hash=_hash_otp(code),
            expires_at=_utcnow() + OTP_TTL,
        )
        self.db.add(otp)
        self.db.commit()
        self.db.refresh(otp)

        # Dispatch via the RQ ``respond_io`` queue - the SAME path as complaint /
        # stock-inquiry status replies. The worker does the window-aware send
        # (free-form text inside the 24h window, else the approved WhatsApp
        # template) AND writes an ``integration_logs`` outbox row, including a
        # ``status='failed'`` row when the send can't go out (e.g. local dev
        # with no Respond.io connectivity) - but the code itself is REDACTED
        # from that row (security round B1, #1280: a signed-in user with no
        # special permission could otherwise read it back out of
        # `GET /api/v1/integrations/logs`, which has no permission gate, and
        # use it to verify as the target). For local dev, read the code from
        # the worker's own DEBUG log line instead. Decoupling also means a
        # Respond outage no longer 500s the request - the caller just retries.
        identifier = (contact.respond_io_id or "").strip() or contact.id
        otp_text = text_template.format(code=code)

        if dispatch_inline:
            # Already running inside the respond_io queue's own job - call the
            # send directly rather than enqueuing a second one. A send failure
            # here is NOT refunded (the attempt was made, same as the async
            # path where a failed send still keeps its integration_logs row);
            # the caller (send_signin_code) decides whether to swallow it.
            task(otp.id, identifier, otp_text, code, space_id)
            return otp

        try:
            from app.services.queue_service import enqueue_job

            enqueue_job(
                task,
                otp.id,
                identifier,
                otp_text,
                code,
                space_id,
                queue_name="respond_io",
                job_timeout=180,
                # Fix lane round 2 (reviewer Should fix 1): RQ's worker logs
                # the job description at INFO, and the default one renders
                # every argument, the code included.
                description=f"{task.__module__}.{task.__name__}(<redacted>)",
            )
        except Exception as e:  # noqa: BLE001
            # Enqueue itself failed (e.g. Redis unreachable) - refund the code so
            # it doesn't burn the daily cap, then surface a retryable error.
            logger.warning("Failed to enqueue OTP for contact %s: %s", contact.id, e)
            try:
                self.db.delete(otp)
                self.db.commit()
            except Exception:  # noqa: BLE001
                self.db.rollback()
            raise handle_validation_error(
                "Could not send the verification code right now. Please try again shortly."
            ) from e
        return otp

    def request_otp(self, contact_id: str, space_id: str) -> dict:
        """Generate a fresh OTP code and dispatch via Respond.io. Returns delivery hint."""
        contact_id = (contact_id or "").strip()
        space_id = (space_id or "").strip()
        if not contact_id or not space_id:
            raise handle_validation_error("contact_id and space_id are required.")
        contact = self._resolve_contact(contact_id)

        from app.tasks.respond_io_tasks import send_portal_otp_respond_message

        otp = self.create_and_dispatch_otp(
            contact,
            space_id,
            "Your Sorento portal verification code is {code}. It expires in 10 "
            "minutes. Please do not share with anyone.",
            send_portal_otp_respond_message,
        )
        masked_phone = self._mask_phone(contact.phone_number)
        return {"sent_to": masked_phone, "expires_at": otp.expires_at.isoformat()}

    def reserve_attempt(self, otp_id: str) -> Optional[tuple[int, str]]:
        """Atomically bump ``attempts`` by 1 IF the row is still guessable
        (below the cap, not consumed, not expired). Returns
        ``(attempts_after, code_hash)`` or ``None`` when it can't be reserved
        (locked out / consumed / expired / missing, including
        :data:`NIL_OTP_ID`, which never matches a row).

        Security round B2 (#1280): the increment happens IN THE UPDATE
        STATEMENT ITSELF (``attempts = attempts + 1``), guarded by
        ``attempts < :max`` in the SAME statement, so two parallel verify
        calls for the same code cannot each read a stale ``attempts`` value
        and both "reserve" the last guess - Postgres serialises the two
        UPDATEs on the row and the second one's WHERE clause re-evaluates
        against the first's committed result. Compare the returned
        ``code_hash`` AFTER this call, never before it.
        """
        row = self.db.execute(
            text(
                "UPDATE portal_otp_codes SET attempts = attempts + 1 "
                "WHERE id = :id AND attempts < :max_attempts AND consumed_at IS NULL "
                "AND expires_at > :now "
                "RETURNING attempts, code_hash"
            ),
            {"id": otp_id, "max_attempts": OTP_MAX_ATTEMPTS, "now": _utcnow()},
        ).first()
        self.db.commit()
        if row is None:
            return None
        return int(row[0]), str(row[1])

    def consume_reserved(self, otp_id: str, now: datetime) -> bool:
        """Atomically mark a row consumed; ``True`` only if THIS call did it.

        Guards the mint-a-session step: if two parallel verifies both pass
        the code compare (only possible before this call runs), only the
        first ``UPDATE ... WHERE consumed_at IS NULL`` affects a row: the
        second sees 0 rows and must refuse instead of minting a second
        session for the same one-time code.
        """
        result = self.db.execute(
            text("UPDATE portal_otp_codes SET consumed_at = :now WHERE id = :id AND consumed_at IS NULL"),
            {"id": otp_id, "now": now},
        )
        self.db.commit()
        return result.rowcount == 1

    def verify_otp(self, contact_id: str, space_id: str, code: str) -> PortalToken:
        contact_id = (contact_id or "").strip()
        space_id = (space_id or "").strip()
        code = (code or "").strip()
        if not contact_id or not space_id or not code:
            raise handle_validation_error("contact_id, space_id and code are required.")
        contact = self._resolve_contact(contact_id)

        otp = (
            self.db.query(PortalOtpCode)
            .filter(
                PortalOtpCode.contact_id == contact.id,
                PortalOtpCode.consumed_at.is_(None),
            )
            .order_by(PortalOtpCode.created_at.desc())
            .first()
        )
        if otp is None:
            raise handle_validation_error("No verification code outstanding. Request a new one.")
        if otp.expires_at <= _utcnow():
            raise handle_validation_error("Verification code expired. Request a new one.")

        reserved = self.reserve_attempt(otp.id)
        if reserved is None:
            # Reached the cap (or lost a race to consumption/expiry between the
            # SELECT above and the reservation) - either way, no free guess.
            raise handle_validation_error("Too many attempts. Request a new verification code.")
        _attempts_after, code_hash = reserved

        if not hmac.compare_digest(code_hash, _hash_otp(code)):
            raise handle_validation_error("Incorrect verification code.")

        if not self.consume_reserved(otp.id, _utcnow()):
            # Someone else's parallel verify consumed this exact code first.
            raise handle_validation_error("Incorrect verification code.")

        # Mark every unrevoked token for this contact as verified so the original
        # admin-issued QR / link / "Send via Respond.io" token grants access
        # immediately after OTP success - no need to re-issue through the new
        # minted token unless the caller wants a fresh expiry window.
        now = _utcnow()
        self.db.query(PortalToken).filter(
            PortalToken.contact_id == contact.id,
            PortalToken.revoked_at.is_(None),
            PortalToken.verified_at.is_(None),
        ).update({"verified_at": now}, synchronize_session=False)
        self.db.commit()
        new_token = self.mint_token(contact.id, space_id)
        # Newly minted token also bypasses the gate (verifier just succeeded)
        # and gets the full sliding device-trust window straight away.
        new_token.verified_at = now
        new_token.expires_at = now + PORTAL_VERIFIED_TOKEN_TTL
        self.db.commit()
        return new_token

    @staticmethod
    def _mask_phone(phone: Optional[str]) -> Optional[str]:
        """Mask to '+60••••1234' - country prefix + last 4, recognizable to the
        owner but useless to a stranger holding a leaked slug link."""
        if not phone:
            return None
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) <= 4:
            return phone
        prefix = f"+{digits[:2]}" if len(digits) > 9 else "+"
        return f"{prefix}••••{digits[-4:]}"

    # ---------- Identity ----------

    def get_contact(self, token: PortalToken) -> RespondContact:
        contact = (
            self.db.query(RespondContact)
            .filter(RespondContact.id == token.contact_id)
            .first()
        )
        if contact is None:
            raise handle_not_found("Contact", token.contact_id)
        return contact

    def _ids_with_revision_draft(self, source_entity_type: str, ids: list[str]) -> set[str]:
        """Ids among ``ids`` that have an unsent revision draft parked.

        One query per list call, skipped entirely when there is nothing to check
        against - the list row marks "you have unsent changes here", not the
        submission's real status (that stays untouched; see the revising banner
        on the form itself for the "not sent yet" message).
        """
        if not ids:
            return set()
        rows = (
            self.db.query(PortalRevisionDraft.source_entity_id)
            .filter(
                PortalRevisionDraft.source_entity_type == source_entity_type,
                PortalRevisionDraft.source_entity_id.in_(ids),
            )
            .all()
        )
        return {str(r[0]) for r in rows}

    # ---------- List submissions ----------

    def list_submissions(
        self,
        token: PortalToken,
        kind: str,
        q: Optional[str] = None,
    ) -> list[dict]:
        kind = (kind or "").strip().lower()
        if kind not in SUPPORTED_TYPES:
            raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
        like = f"%{q.strip()}%" if q and q.strip() else None
        if kind == "complaint":
            query = self.db.query(Complaint).filter(
                Complaint.contact_id == token.contact_id,
                Complaint.space_id == token.space_id,
            )
            if like is not None:
                query = query.filter(
                    or_(
                        Complaint.complaint_number.ilike(like),
                        Complaint.delivery_order_number.ilike(like),
                        Complaint.customer_name.ilike(like),
                        Complaint.customer_type.ilike(like),
                        Complaint.customer_type_others.ilike(like),
                        Complaint.contact_person.ilike(like),
                        Complaint.contact_number.ilike(like),
                        Complaint.customer_address.ilike(like),
                        Complaint.product_code.ilike(like),
                        Complaint.product_type.ilike(like),
                        Complaint.complaint_type.ilike(like),
                        Complaint.defects_discovered.ilike(like),
                        Complaint.defect_description.ilike(like),
                        Complaint.salesperson.ilike(like),
                        Complaint.project_title.ilike(like),
                        Complaint.within_warranty.ilike(like),
                        Complaint.status.ilike(like),
                    )
                )
            rows = query.order_by(Complaint.created_at.desc()).all()
            return [self._serialize_complaint_summary(r) for r in rows]
        if kind == "stock_inquiry":
            query = self.db.query(StockInquiry).filter(
                StockInquiry.contact_id == token.contact_id,
                StockInquiry.space_id == token.space_id,
            )
            if like is not None:
                query = query.filter(
                    or_(
                        StockInquiry.inquiry_number.ilike(like),
                        StockInquiry.salesperson.ilike(like),
                        StockInquiry.product_code.ilike(like),
                        StockInquiry.item_description.ilike(like),
                        StockInquiry.project_customer.ilike(like),
                        StockInquiry.project_name.ilike(like),
                        StockInquiry.remark.ilike(like),
                        StockInquiry.additional_remark.ilike(like),
                        StockInquiry.status.ilike(like),
                    )
                )
            # A revision is new work for the reader, so it sorts as if created then.
            rows = query.order_by(
                func.coalesce(StockInquiry.last_revised_at, StockInquiry.created_at).desc(),
                StockInquiry.id.asc(),
            ).all()
            draft_ids = self._ids_with_revision_draft(
                "stock_inquiry", [str(r.id) for r in rows]
            )
            return [
                self._serialize_stock_inquiry_summary(
                    r, has_revision_draft=str(r.id) in draft_ids
                )
                for r in rows
            ]
        # purchase_request / sponsorship_form
        query = self.db.query(PurchaseRequestHeader).filter(
            PurchaseRequestHeader.contact_id == token.contact_id,
            PurchaseRequestHeader.space_id == token.space_id,
            PurchaseRequestHeader.request_type == kind,
        )
        if like is not None:
            line_subq = (
                self.db.query(PurchaseRequestLine.purchase_request_id)
                .filter(
                    or_(
                        PurchaseRequestLine.item_code.ilike(like),
                        PurchaseRequestLine.remark.ilike(like),
                    )
                )
            )
            query = query.filter(
                or_(
                    PurchaseRequestHeader.request_number.ilike(like),
                    PurchaseRequestHeader.customer_name.ilike(like),
                    PurchaseRequestHeader.project_title.ilike(like),
                    PurchaseRequestHeader.purpose.ilike(like),
                    PurchaseRequestHeader.delivery_address.ilike(like),
                    PurchaseRequestHeader.sponsor_subject.ilike(like),
                    PurchaseRequestHeader.requested_by.ilike(like),
                    PurchaseRequestHeader.external_reference.ilike(like),
                    PurchaseRequestHeader.total_project_value_text.ilike(like),
                    PurchaseRequestHeader.status.ilike(like),
                    PurchaseRequestHeader.id.in_(line_subq),
                )
            )
        # A revision is new work for the reader, so it sorts as if created then.
        rows = query.order_by(
            func.coalesce(PurchaseRequestHeader.last_revised_at, PurchaseRequestHeader.created_at).desc(),
            PurchaseRequestHeader.id.asc(),
        ).all()
        # source_entity_type on portal_revision_drafts matches `kind` exactly for
        # both purchase_request and sponsorship_form (see the revision adapters).
        draft_ids = self._ids_with_revision_draft(kind, [str(r.id) for r in rows])
        return [
            self._serialize_request_summary(r, has_revision_draft=str(r.id) in draft_ids)
            for r in rows
        ]

    # ---------- Detail ----------

    def _raise_submission_missing(
        self, entity_name: str, model: Any, submission_id: str, *, request_type: Optional[str] = None
    ) -> None:
        """Raise the right error when a contact-scoped submission lookup misses.

        If the row exists for ANOTHER contact (same id, ignoring the token's
        contact) → 403 ``OWNER_MISMATCH`` so the portal can offer "log in as the
        owner" (the deep-link slug identifies that owner) instead of a misleading
        "deleted" 404. Truly absent → 404. Mirrors the no-session path, which the
        owner already sees via the confirm-identity card.
        """
        exists_q = self.db.query(model.id).filter(model.id == submission_id)
        if request_type is not None:
            exists_q = exists_q.filter(model.request_type == request_type)
        if exists_q.first() is not None:
            raise AppException(
                status_code=403,
                message=(
                    f"This {entity_name.lower()} belongs to another contact. "
                    f"Log in with that number to open it."
                ),
                code="OWNER_MISMATCH",
            )
        raise handle_not_found(entity_name, submission_id)

    def get_submission(self, token: PortalToken, kind: str, submission_id: str) -> dict:
        kind = kind.strip().lower()
        if kind == "complaint":
            row = (
                self.db.query(Complaint)
                .filter(
                    Complaint.id == submission_id,
                    Complaint.contact_id == token.contact_id,
                    Complaint.space_id == token.space_id,
                )
                .first()
            )
            if row is None:
                self._raise_submission_missing("Complaint", Complaint, submission_id)
            return self._serialize_complaint_detail(row)
        if kind == "stock_inquiry":
            row = (
                self.db.query(StockInquiry)
                .filter(
                    StockInquiry.id == submission_id,
                    StockInquiry.contact_id == token.contact_id,
                    StockInquiry.space_id == token.space_id,
                )
                .first()
            )
            if row is None:
                self._raise_submission_missing("Stock Inquiry", StockInquiry, submission_id)
            return self._serialize_stock_inquiry_detail(row)
        if kind in ("purchase_request", "sponsorship_form"):
            row = (
                self.db.query(PurchaseRequestHeader)
                .options(joinedload(PurchaseRequestHeader.lines))
                .filter(
                    PurchaseRequestHeader.id == submission_id,
                    PurchaseRequestHeader.contact_id == token.contact_id,
                    PurchaseRequestHeader.space_id == token.space_id,
                    PurchaseRequestHeader.request_type == kind,
                )
                .first()
            )
            if row is None:
                self._raise_submission_missing(
                    "Purchase Request", PurchaseRequestHeader, submission_id, request_type=kind
                )
            return self._serialize_request_detail(row)
        raise handle_validation_error(f"Unsupported submission type: {kind!r}.")

    # ---------- Mutations ----------

    def create_or_update_draft(
        self,
        token: PortalToken,
        kind: str,
        payload: dict,
        submission_id: Optional[str] = None,
    ) -> dict:
        kind = kind.strip().lower()
        if submission_id is None:
            row = self._instantiate(kind, token, payload)
            row.portal_draft_at = _utcnow()
            self.db.add(row)
            self.db.flush()
            self._replace_request_lines_if_needed(kind, row, payload)
            self.db.commit()
            self.db.refresh(row)
        else:
            row = self._fetch_for_edit(kind, token, submission_id)
            approval_rejected = (
                (getattr(row, "approval_status", None) or "").strip().lower() == "rejected"
            )
            row_status = (getattr(row, "status", None) or "").strip().lower()
            row_status_rejected = row_status == "rejected"
            # A `responded` stock inquiry can only be changed through Revise -
            # mirroring `rejected`, which can only progress via Submit - no parking
            # either back in a plain draft save.
            row_status_responded = row_status == "responded"
            if approval_rejected or row_status_rejected or row_status_responded:
                if row_status_responded:
                    raise handle_validation_error(
                        "This submission can only be changed through Revise."
                    )
                raise handle_validation_error(
                    "This submission must be resent via Submit - draft saves are disabled."
                )
            self._apply_payload(kind, row, payload)
            row.portal_draft_at = _utcnow()
            self._replace_request_lines_if_needed(kind, row, payload)
            self.db.commit()
            self.db.refresh(row)
        return self.get_submission(token, kind, str(row.id))

    def submit_draft(
        self,
        token: PortalToken,
        kind: str,
        submission_id: str,
        payload: Optional[dict] = None,
    ) -> dict:
        kind = kind.strip().lower()
        row = self._fetch_for_edit(kind, token, submission_id)
        if payload:
            self._apply_payload(kind, row, payload)
            self._replace_request_lines_if_needed(kind, row, payload)

        # Status transition + notification.
        previous_status = row.status
        # Captured BEFORE the branches below clear them: a resubmit-after-rejection
        # writes a history row carrying the rejection it answers (UAC C4).
        previous_approval_status = (
            (getattr(row, "approval_status", None) or "").strip().lower()
        )
        previous_rejection_reason = (
            getattr(row, "approval_comments", None)
            if previous_approval_status == "rejected"
            else getattr(row, "rejection_reason", None)
        )
        row.portal_draft_at = None
        if kind == "complaint":
            if previous_status not in ("draft", "rejected"):
                raise handle_validation_error(
                    f"Cannot submit complaint with status {previous_status!r}."
                )
            row.status = "submitted"
        elif kind == "stock_inquiry":
            # A `responded` inquiry is read-only on this path - it changes only
            # through the revision endpoint (`PortalRevisionService.revise`).
            if previous_status not in ("draft", "rejected"):
                raise handle_validation_error(
                    f"Cannot submit stock inquiry with status {previous_status!r}."
                )
            # Portal submit mirrors the system "submit for project sales" flow:
            # advance directly to pending_project_sales so the Project Sales
            # team is queued for review (notification fires below).
            row.status = "pending_project_sales"
            row.rejection_reason = None
            row.rejected_at = None
            row.rejected_by = None
            row.rejected_from = None
        else:  # purchase_request / sponsorship_form
            if kind == "sponsorship_form":
                # AC-F4/AC-F5, and it lives HERE rather than in _apply_payload because a
                # draft may legitimately be incomplete. The portal FE also gates the
                # field, but the portal is a public token-reached surface and the browser
                # is not a trust boundary.
                from app.services import sponsorship_link_service

                sponsorship_link_service.assert_project_requirement(
                    self.db,
                    contact=self.get_contact(token),
                    project_id=getattr(row, "project_id", None),
                )
                # #1227: same reasoning as the project-requirement gate above - a
                # sponsorship line without a usable unit price is a draft's business,
                # never a submission's. Checked against the lines actually persisted by
                # `_replace_request_lines_if_needed` above (autoflushed by this query),
                # so a bare resubmit of an already-saved draft is checked too, not only
                # a submit that also sends a fresh `products` payload.
                self._require_sponsorship_unit_prices(row)
            approval_rejected = (
                (getattr(row, "approval_status", None) or "").strip().lower() == "rejected"
            )
            if previous_status not in ("draft", "rejected") and not approval_rejected:
                raise handle_validation_error(
                    f"Cannot submit {kind} with status {previous_status!r}."
                )
            row.status = "submitted"
            # Top "Date" on the document = submitted date. Re-stamp on every submit,
            # including resubmit-after-rejection, so it reflects the latest submission
            # (mirrors approved_at, which also resets per approval cycle).
            row.submitted_at = _utcnow()
            if approval_rejected:
                # Salesperson is re-submitting a rejected approval - clear the rejection
                # state so reviewers see a fresh submission.
                row.approval_status = None
                row.approval_comments = None
                row.approved_at = None
                row.approved_by = None
                row.approval_signature_ref = None

        # Document number generation (skip complaint - no number column).
        self._assign_document_number_if_missing(kind, row)

        # Revision history: every submitted version gets a row, including this one
        # (UAC C4/G1). Runs before the commit so history and submit are atomic.
        self._record_submission_history(
            kind,
            row,
            token,
            is_resubmission=(
                previous_status in ("rejected", "responded")
                or previous_approval_status == "rejected"
            ),
            reason=previous_rejection_reason,
        )

        self.db.commit()
        self.db.refresh(row)

        # Notifications - failures must not block the user-facing submit.
        try:
            self._post_submit_notify(
                kind, row, is_resubmission=previous_status in ("rejected", "responded")
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Post-submit notify failed for %s %s: %s", kind, row.id, e)

        # A sponsorship recorded against a project is real work on that project (AC-H2),
        # so it advances the project's staleness clock and shows in its feed. Best-effort:
        # the form is already submitted and committed by this point.
        if kind == "sponsorship_form" and getattr(row, "project_id", None):
            try:
                from app.models.projects import Project
                from app.services import project_activity_service as activity

                project = (
                    self.db.query(Project)
                    .filter(Project.id == str(row.project_id))
                    .first()
                )
                if project is not None:
                    activity.record_project_event(
                        self.db,
                        project=project,
                        template="sponsorship_recorded",
                        payload={
                            "sponsorship_form_id": str(row.id),
                            "request_number": getattr(row, "request_number", None),
                        },
                    )
                    self.db.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Sponsorship project activity not recorded for %s: %s", row.id, e
                )

        # Form SLA: emit `submit` so the configured form_sla_configs spawn a tracker.
        # Portal submit bypasses the API state-transition methods; this hook covers
        # complaint / stock_inquiry / purchase_request / sponsorship_form uniformly.
        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                kind,
                str(row.id),
                "submit",
                contact_id=getattr(row, "contact_id", None),
                actor_user_id=None,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Form SLA emit 'submit' (portal submit_draft) failed for %s %s: %s",
                kind,
                row.id,
                e,
            )

        return self.get_submission(token, kind, str(row.id))

    def _record_submission_history(
        self,
        kind: str,
        row: Any,
        token: PortalToken,
        *,
        is_resubmission: bool,
        reason: Optional[str],
    ) -> None:
        """Write this version's ``portal_form_revisions`` row (UAC C4/G1).

        First submit writes the ``original``; a resubmit after an office rejection
        writes a ``resubmission`` - ``version_no`` advances, ``revision_no`` does not,
        so an office reject never burns one of the contact's revisions (UAC C1).

        Inside a SAVEPOINT: history is worth having, but it must never be able to roll
        back the submit it describes. The submit's own changes are flushed FIRST so the
        savepoint contains nothing but the history insert - otherwise a rollback to the
        savepoint would take the status transition with it. Types with no revision
        adapter (complaint) are a no-op.
        """
        try:
            from app.services.portal_revision_service import PortalRevisionService

            self.db.flush()
            with self.db.begin_nested():
                PortalRevisionService(self.db).record_submission(
                    kind,
                    row,
                    token.contact_id,
                    is_resubmission=is_resubmission,
                    reason=reason,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Portal revision history write failed for %s %s: %s", kind, row.id, e
            )

    def _assign_document_number_if_missing(self, kind: str, row: Any) -> None:
        """Assign a stable document number on submit using ``DocumentNumberingRule``.

        Falls back to the legacy ``{prefix}-{YYYYMMDD}-{nnnn}`` stem when no
        configured rule exists for the doc_type, so installs without a rule still
        produce a number.

      - ``complaint`` -> ``complaint_number`` (legacy fallback prefix ``CMP``)
      - ``stock_inquiry`` -> ``inquiry_number``
      - ``purchase_request`` -> ``request_number``
      - ``sponsorship_form`` -> ``request_number``
        """
        from app.services.numbering_service import NumberingService

        numbering = NumberingService(self.db)
        date_stem = _utcnow().strftime("%Y%m%d")

        if kind == "complaint":
            if getattr(row, "complaint_number", None):
                return
            generated = numbering.get_next_number("complaint", commit_rule=False)
            if generated:
                row.complaint_number = generated
                return
            prefix = "CMP"
            stem = f"{prefix}-{date_stem}-"
            existing = (
                self.db.query(Complaint)
                .filter(Complaint.complaint_number.like(f"{stem}%"))
                .count()
            )
            row.complaint_number = f"{stem}{existing + 1:04d}"
            return
        if kind == "stock_inquiry":
            if getattr(row, "inquiry_number", None):
                return
            generated = numbering.get_next_number("stock_inquiry", commit_rule=False)
            if generated:
                row.inquiry_number = generated
                return
            prefix = "SI"
            stem = f"{prefix}-{date_stem}-"
            existing = (
                self.db.query(StockInquiry)
                .filter(StockInquiry.inquiry_number.like(f"{stem}%"))
                .count()
            )
            row.inquiry_number = f"{stem}{existing + 1:04d}"
            return
        if kind in ("purchase_request", "sponsorship_form"):
            if getattr(row, "request_number", None):
                return
            generated = numbering.get_next_number(kind, commit_rule=False)
            if generated:
                row.request_number = generated
                return
            prefix = "PR" if kind == "purchase_request" else "SP"
            stem = f"{prefix}-{date_stem}-"
            existing = (
                self.db.query(PurchaseRequestHeader)
                .filter(PurchaseRequestHeader.request_number.like(f"{stem}%"))
                .count()
            )
            row.request_number = f"{stem}{existing + 1:04d}"
            return

    def delete_draft(self, token: PortalToken, kind: str, submission_id: str) -> None:
        """Hard-delete a draft submission. Only drafts (portal_draft_at IS NOT NULL) are deletable."""
        kind = kind.strip().lower()
        row = self._fetch_for_edit(kind, token, submission_id)
        if row.portal_draft_at is None:
            raise handle_validation_error("Only drafts can be deleted.")
        self.db.delete(row)
        self.db.commit()

    def _build_respond_inbox_url(self, contact_id: Optional[str], space_id: Optional[str]) -> Optional[str]:
        """Build respond.io inbox URL: {base}/space/{space_id}/inbox/{respond_io_id}.

        Accepts internal RespondContact.id (UUID stored on PortalToken) and resolves
        it to the Respond.io contact identifier required by the inbox URL.
        """
        if not contact_id or not space_id:
            return None
        base = (settings.respond_app_base_url or "").rstrip("/")
        if not base:
            return None
        contact = (
            self.db.query(RespondContact)
            .filter(
                or_(
                    RespondContact.id == contact_id.strip(),
                    RespondContact.respond_io_id == contact_id.strip(),
                )
            )
            .first()
        )
        respond_io_id = (contact.respond_io_id or "").strip() if contact else ""
        if not respond_io_id:
            return None
        return f"{base}/space/{space_id.strip()}/inbox/{respond_io_id}"

    def _instantiate(self, kind: str, token: PortalToken, payload: dict) -> Any:
        respond_inbox_url = self._build_respond_inbox_url(token.contact_id, token.space_id)
        if kind == "complaint":
            row = Complaint(
                contact_id=token.contact_id,
                space_id=token.space_id,
                respond_inbox_url=respond_inbox_url,
                status="draft",
            )
            self._apply_payload(kind, row, payload)
            return row
        if kind == "stock_inquiry":
            row = StockInquiry(
                contact_id=token.contact_id,
                space_id=token.space_id,
                respond_inbox_url=respond_inbox_url,
                status="draft",
            )
            self._apply_payload(kind, row, payload)
            return row
        if kind in ("purchase_request", "sponsorship_form"):
            row = PurchaseRequestHeader(
                contact_id=token.contact_id,
                space_id=token.space_id,
                respond_inbox_url=respond_inbox_url,
                request_type=kind,
                status="draft",
                source="portal",
            )
            self._apply_payload(kind, row, payload)
            return row
        raise handle_validation_error(f"Unsupported submission type: {kind!r}.")

    def _fetch_for_edit(self, kind: str, token: PortalToken, submission_id: str) -> Any:
        if kind == "complaint":
            row = (
                self.db.query(Complaint)
                .filter(
                    Complaint.id == submission_id,
                    Complaint.contact_id == token.contact_id,
                    Complaint.space_id == token.space_id,
                )
                .first()
            )
            if row is None:
                raise handle_not_found("Complaint", submission_id)
        elif kind == "stock_inquiry":
            row = (
                self.db.query(StockInquiry)
                .filter(
                    StockInquiry.id == submission_id,
                    StockInquiry.contact_id == token.contact_id,
                    StockInquiry.space_id == token.space_id,
                )
                .first()
            )
            if row is None:
                raise handle_not_found("Stock Inquiry", submission_id)
        elif kind in ("purchase_request", "sponsorship_form"):
            row = (
                self.db.query(PurchaseRequestHeader)
                .options(joinedload(PurchaseRequestHeader.lines))
                .filter(
                    PurchaseRequestHeader.id == submission_id,
                    PurchaseRequestHeader.contact_id == token.contact_id,
                    PurchaseRequestHeader.space_id == token.space_id,
                    PurchaseRequestHeader.request_type == kind,
                )
                .first()
            )
            if row is None:
                raise handle_not_found("Purchase Request", submission_id)
        else:
            raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
        approval_rejected = (
            (getattr(row, "approval_status", None) or "").strip().lower() == "rejected"
        )
        # `responded` (stock_inquiry only) is editable so a salesperson can act on
        # purchasing's clarifying reply - edit + resubmit without a formal reject.
        responded_editable = kind == "stock_inquiry" and row.status == "responded"
        if not (
            row.portal_draft_at
            or row.status == "rejected"
            or approval_rejected
            or responded_editable
        ):
            raise handle_validation_error("This submission is not editable.")
        return row

    def _apply_payload(self, kind: str, row: Any, payload: dict) -> None:
        editable = self._editable_fields(kind)
        requestor_field = self._requestor_contact_field(kind)
        for field in editable:
            if field in payload:
                if field == requestor_field:
                    # Handled separately below - needs eligibility validation +
                    # live label derivation, not a bare setattr.
                    continue
                value = payload.get(field)
                # Coerce list values for multi-text fields (e.g. complaint.delivery_order_number).
                if isinstance(value, list):
                    value = ",".join(str(v).strip() for v in value if v not in (None, ""))
                if value == "":
                    value = None
                if field in self._date_fields(kind):
                    value = self._coerce_date(value)
                if field in self._decimal_fields(kind):
                    # Shared guard rail: portal save enforces the same numeric +
                    # range rules as system save (validators.validate_project_value).
                    value = validate_project_value(value)
                if (
                    kind == "stock_inquiry"
                    and field == "quantity"
                    and value not in (None, "")
                ):
                    try:
                        if float(str(value)) < 0:
                            raise handle_validation_error(
                                "Quantity must be a non-negative number."
                            )
                    except (TypeError, ValueError):
                        pass  # legacy free-text values pass through
                if field == "project_id" and value not in (None, ""):
                    # The column is a UUID FK, so anything typed rather than picked
                    # would reach Postgres as an id and come back a 500 on flush. Say
                    # what to do instead, at the boundary, as a 422.
                    try:
                        value = str(UUID(str(value).strip()))
                    except (TypeError, ValueError):
                        raise handle_validation_error(
                            "Pick a registered project from the list - the project field "
                            "does not take free text."
                        )
                setattr(row, field, value)
        if requestor_field and requestor_field in payload:
            self._apply_requestor_contact(kind, row, payload.get(requestor_field))
        # Sponsorship: resolve sponsor_subject through the lookup so an unmatched
        # free-text value lands on 'others' (+ parked detail) instead of tripping
        # the strict lookup write-validator on flush.
        if kind == "sponsorship_form" and "sponsor_subject" in payload:
            from app.services.procurement_service import PurchaseRequestService

            norm_subject, norm_other = PurchaseRequestService(
                self.db
            )._normalize_sponsor_subject(
                "sponsorship_form", getattr(row, "sponsor_subject", None)
            )
            row.sponsor_subject = norm_subject
            explicit_other = (getattr(row, "sponsor_subject_other", None) or "").strip() or None
            row.sponsor_subject_other = (
                explicit_other if norm_subject == "others" and explicit_other else norm_other
            )

    def _replace_request_lines_if_needed(self, kind: str, row: Any, payload: dict) -> None:
        if kind == "complaint":
            self._replace_complaint_lines_if_needed(row, payload)
            return
        if kind not in ("purchase_request", "sponsorship_form"):
            return
        if "products" not in payload:
            return
        products = payload.get("products") or []
        # Drop existing lines for this row (cascade on delete via relationship).
        self.db.query(PurchaseRequestLine).filter(
            PurchaseRequestLine.purchase_request_id == row.id
        ).delete(synchronize_session=False)
        for index, line_data in enumerate(products):
            if not isinstance(line_data, dict):
                continue
            self.db.add(
                PurchaseRequestLine(
                    purchase_request_id=row.id,
                    item_code=line_data.get("item_code"),
                    quantity=self._coerce_decimal(line_data.get("quantity")),
                    unit_price=self._coerce_decimal(line_data.get("unit_price")),
                    total=self._coerce_decimal(line_data.get("total")),
                    remark=line_data.get("remark"),
                    sort_order=index,
                )
            )

    def _require_sponsorship_unit_prices(self, row: Any) -> None:
        """#1227: refuse a sponsorship form submit while any line lacks a usable unit
        price. Same detail shape `price_tag_request_service` uses for a line refusal
        (``detail=f"line:{index}"``) so the portal can name the offending line exactly
        like it already does for a price tag request line. Purchase requests are
        unchanged - this is never called for that kind.
        """
        lines = (
            self.db.query(PurchaseRequestLine)
            .filter(PurchaseRequestLine.purchase_request_id == row.id)
            .order_by(PurchaseRequestLine.sort_order)
            .all()
        )
        for index, line in enumerate(lines):
            price = line.unit_price
            if price is None or price < 0:
                raise AppException(
                    status_code=422,
                    message="Unit price is required.",
                    detail=f"line:{index}",
                    code="SPONSORSHIP_UNIT_PRICE_REQUIRED",
                )

    def _replace_complaint_lines_if_needed(self, row: Any, payload: dict) -> None:
        """Rebuild complaint product lines from payload['product_lines'] (the new
        per-product widget) and re-derive the legacy product_code / product_type /
        quantity CSV columns index-aligned. No-op if the key is absent (older
        portal clients still send flat product_code/quantity via _apply_payload)."""
        if "product_lines" not in payload:
            return
        from app.models.complaints import ComplaintProductLine

        lines = payload.get("product_lines") or []
        self.db.query(ComplaintProductLine).filter(
            ComplaintProductLine.complaint_id == row.id
        ).delete(synchronize_session=False)

        codes: list[str] = []
        types: list[str] = []
        qtys: list[str] = []
        for index, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            code = (str(line.get("product_code") or "")).strip()
            if not code:
                continue
            qty = line.get("quantity")
            qty = (str(qty).strip() or None) if qty is not None else None
            ptype = line.get("product_type")
            ptype = (str(ptype).strip() or None) if ptype is not None else None
            self.db.add(
                ComplaintProductLine(
                    complaint_id=row.id,
                    product_code=code,
                    quantity=qty,
                    product_type=ptype,
                    sort_order=index,
                )
            )
            codes.append(code)
            types.append(ptype or "")
            qtys.append(qty or "")

        row.product_code = ", ".join(codes) if codes else None
        row.product_type = ", ".join(types) if any(types) else None
        row.quantity = ", ".join(qtys) if any(qtys) else None

    def _editable_fields(self, kind: str) -> tuple[str, ...]:
        if kind == "complaint":
            return (
                "delivery_order_number",
                "complaint_date",
                "customer_type",
                "customer_type_others",
                "within_warranty",
                "product_type",
                "defects_discovered",
                "complaint_type",
                "defect_description",
                "product_code",
                "quantity",
                "salesperson",
                "customer_name",
                "contact_person",
                "contact_number",
                "customer_address",
                "project_title",
            )
        if kind == "stock_inquiry":
            return (
                "salesperson",
                "salesperson_contact_id",  # requestor FK - CS pin lookup key (D6/D9)
                "product_code",
                "item_description",
                "project_customer",
                "project_name",
                "quantity",
                "delivery_date",
                "remark",
                "additional_remark",
            )
        if kind == "price_tag_request":
            # price_tag_request has its own dedicated router/service
            # (portal_price_tag.py, not this class's generic CRUD) - this
            # branch exists ONLY so the revision engine's `build_snapshot` /
            # `revise()._apply_payload` (both keyed on this ONE list, UAC J2)
            # know the header fields for this type too.
            # D1 (S6): `promotion_id` is gone - the promotion is a LINE fact,
            # carried through `products[].promotion_id` in `_apply_price_tag_lines`
            # (`portal_revision_service.py`), never this header whitelist. A
            # stray header `promotion_id` here used to `setattr` onto a column
            # the model no longer has - a silent no-op, not a write.
            return (
                "debtor_code",
                "debtor_name",
                "needed_by_date",
                "notes",
                "price_mode",
            )
        return (
            "customer_name",
            "pic",
            "project_title",
            # AC-F3: the link itself. Ownership and the per-contact requirement are
            # enforced in submit_draft, not here, because a DRAFT is allowed to be
            # incomplete -- blocking on save would stop somebody parking a half-filled
            # form, which is what drafts are for.
            "project_id",
            "purpose",
            "delivery_address",
            "total_project_value",
            "total_project_value_text",
            "sponsor_subject",
            "sponsor_subject_other",
            "sales_type",  # PR: project/cash_sales (lookup-bound); required for PR, routes CS
            "expected_delivery_date",
            "expected_po_date",
            "requested_by",
            "requested_by_contact_id",  # requestor FK - CS pin lookup key (D6/D9)
            "external_reference",
        )

    @staticmethod
    def _requestor_contact_field(kind: str) -> Optional[str]:
        """Column name of the requestor FK for this submission kind, or None."""
        if kind in ("purchase_request", "sponsorship_form"):
            return "requested_by_contact_id"
        if kind == "stock_inquiry":
            return "salesperson_contact_id"
        return None

    @staticmethod
    def _date_fields(kind: str) -> tuple[str, ...]:
        if kind == "complaint":
            return ("complaint_date",)
        if kind in ("purchase_request", "sponsorship_form"):
            return ("expected_delivery_date", "expected_po_date")
        if kind == "price_tag_request":
            return ("needed_by_date",)
        return ()

    @staticmethod
    def _decimal_fields(kind: str) -> tuple[str, ...]:
        if kind in ("purchase_request", "sponsorship_form"):
            return ("total_project_value",)
        return ()

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    # ---------- Requestor ("requested by" / "salesperson") options ----------
    #
    # Delegates to app.services.requestor_options_service - the single
    # definition of "who can be a requestor" shared with the internal (JWT) CRM
    # picker (D7), rather than a second, divergent query here. Eligible set
    # (D6): contacts with >=1 active market segment flagged
    # is_requestor_selectable, UNION whichever extra ids the caller passes in
    # (the submitting contact and/or the value already saved on the row).
    # Nobody is ever blocked from submitting, and editing a row whose
    # requestor lost eligibility can't silently blank the field. Names only
    # no phone/email/respond_io_id/segment/company ever leaves this surface.

    def list_requestor_options(
        self, token: PortalToken, q: Optional[str] = None, limit: int = 50
    ) -> dict:
        from app.services.requestor_options_service import (
            list_requestor_options as _list_requestor_options,
        )

        items, has_more = _list_requestor_options(
            self.db, q=q, limit=limit, include_ids=[token.contact_id]
        )
        return {"items": items, "has_more": has_more}

    def _apply_requestor_contact(self, kind: str, row: Any, contact_id_value: Any) -> None:
        """Validate + stamp the requestor FK and its label (D6/D9).

        Thin wrapper over the shared writer so the portal (token) save and the
        internal (JWT) create/update cannot drift: same eligibility rule, same
        live label derivation.
        """
        from app.services.requestor_options_service import (
            apply_requestor_contact as _apply_requestor_contact,
        )

        fk_field = self._requestor_contact_field(kind)
        if not fk_field:
            return
        label_field = (
            "requested_by" if kind in ("purchase_request", "sponsorship_form") else "salesperson"
        )
        _apply_requestor_contact(
            self.db, row, fk_field, label_field, contact_id_value
        )

    # ---------- Portal record navigation ----------

    def get_neighbours(self, token: PortalToken, kind: str, submission_id: str) -> dict:
        """Token-scoped prev/next over the contact's OWN submissions of the SAME
        kind, newest-first - mirrors ``list_submissions`` ordering exactly (D11).

        Ownership is enforced by the token, never by the requested id: the
        ``get_submission`` call below raises 404/403 for a foreign or missing
        id before any neighbour is computed, so a token holder can never page
        into another contact's submissions.
        """
        kind = (kind or "").strip().lower()
        if kind not in SUPPORTED_TYPES:
            raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
        self.get_submission(token, kind, submission_id)  # ownership check; raises on miss

        if kind == "complaint":
            id_query = (
                self.db.query(Complaint.id)
                .filter(
                    Complaint.contact_id == token.contact_id,
                    Complaint.space_id == token.space_id,
                )
                .order_by(Complaint.created_at.desc())
            )
        elif kind == "stock_inquiry":
            id_query = (
                self.db.query(StockInquiry.id)
                .filter(
                    StockInquiry.contact_id == token.contact_id,
                    StockInquiry.space_id == token.space_id,
                )
                .order_by(StockInquiry.created_at.desc())
            )
        else:  # purchase_request / sponsorship_form
            id_query = (
                self.db.query(PurchaseRequestHeader.id)
                .filter(
                    PurchaseRequestHeader.contact_id == token.contact_id,
                    PurchaseRequestHeader.space_id == token.space_id,
                    PurchaseRequestHeader.request_type == kind,
                )
                .order_by(PurchaseRequestHeader.created_at.desc())
            )
        ids = [str(r[0]) for r in id_query.all()]
        try:
            idx = ids.index(str(submission_id))
        except ValueError:
            # Unreachable in practice - get_submission above already confirmed
            # ownership - but fail closed rather than raise an unhandled error.
            raise handle_not_found("Submission", submission_id)

        total = len(ids)
        return {
            "prev_id": ids[idx - 1] if idx > 0 else None,
            "next_id": ids[idx + 1] if idx + 1 < total else None,
            "position": idx + 1,
            "total": total,
        }

    # ---------- Notifications ----------

    def _post_submit_notify(self, kind: str, row: Any, is_resubmission: bool = False) -> None:
        if kind == "complaint":
            from app.services.complaints_service import ComplaintService

            service = ComplaintService(self.db)
            try:
                service.notify_team_complaint_external_created(
                    complaint_id=str(row.id), is_resubmission=is_resubmission
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Complaint submit notify failed: %s", e)
            try:
                service.get_or_create_view_token(str(row.id))
                self.db.commit()
            except Exception:  # noqa: BLE001
                pass
        elif kind == "stock_inquiry":
            from app.services.procurement_service import StockInquiryService

            service = StockInquiryService(self.db)
            try:
                service.get_or_create_view_token(str(row.id))
                self.db.commit()
            except Exception:  # noqa: BLE001
                pass
            try:
                service._notify_team_stock_inquiry(  # noqa: SLF001
                    inquiry_id=str(row.id),
                    agent_code="lead_time_enquiries",
                    team_assignment_code="project_sales",
                    title="New Stock Inquiry created",
                    intro_plain=(
                        "Dear Project Sales Team,\n\nA new stock inquiry has been "
                        "submitted via the user portal and requires your review."
                    ),
                    intro_html=(
                        "Dear Project Sales Team,<br /><br />A new stock inquiry has been "
                        "submitted via the user portal and requires your review."
                    ),
                    event_type="portal_submitted",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Stock inquiry submit notify failed: %s", e)
        else:  # purchase_request / sponsorship_form
            from app.services.procurement_service import PurchaseRequestService

            service = PurchaseRequestService(self.db)
            try:
                service.get_or_create_view_token(str(row.id))
                self.db.commit()
            except Exception:  # noqa: BLE001
                pass
            try:
                service._notify_team_on_external_pr_created(  # noqa: SLF001
                    header_id=str(row.id),
                    request_type=row.request_type,
                    request_number=row.request_number or "N/A",
                    project_title=row.project_title or "N/A",
                    base_url_override=None,
                    integration_action="created",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Purchase request submit notify failed: %s", e)

    # ---------- Attachment policy ----------

    def get_portal_attachment_type(self) -> AttachmentType:
        row = (
            self.db.query(AttachmentType)
            .filter(AttachmentType.code == PORTAL_ATTACHMENT_TYPE_CODE)
            .first()
        )
        if row is None:
            raise handle_validation_error(
                "Portal submission attachment type is not configured."
            )
        return row

    # ---------- Serializers ----------

    def _serialize_complaint_summary(self, row: Complaint) -> dict:
        return {
            "id": str(row.id),
            "kind": "complaint",
            "title": (row.defect_description or "Complaint")[:100],
            "reference": row.complaint_number or row.delivery_order_number,
            "document_number": row.complaint_number,
            "status": row.status,
            "rejection_reason": row.rejection_reason,
            "is_editable": bool(row.portal_draft_at) or row.status == "rejected",
            "is_draft": row.portal_draft_at is not None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "product_code": row.product_code,
            "project_title": row.project_title,
            "customer_name": row.customer_name,
            "delivery_order_number": row.delivery_order_number,
        }

    def _serialize_complaint_detail(self, row: Complaint) -> dict:
        base = self._serialize_complaint_summary(row)
        base.update(
            {
                "complaint_number": row.complaint_number,
                "delivery_order_number": row.delivery_order_number,
                "complaint_date": row.complaint_date.isoformat() if row.complaint_date else None,
                "customer_type": row.customer_type,
                "customer_type_others": row.customer_type_others,
                "within_warranty": row.within_warranty,
                "product_type": row.product_type,
                "defects_discovered": row.defects_discovered,
                "complaint_type": row.complaint_type,
                "defect_description": row.defect_description,
                "product_code": row.product_code,
                "quantity": row.quantity,
                "product_lines": [
                    {
                        "product_code": ln.product_code,
                        "quantity": ln.quantity,
                        "product_type": ln.product_type,
                    }
                    for ln in (row.product_lines or [])
                ],
                "salesperson": row.salesperson,
                "customer_name": row.customer_name,
                "contact_person": row.contact_person,
                "contact_number": row.contact_number,
                "customer_address": row.customer_address,
                "project_title": row.project_title,
                "technical_team_response": row.technical_team_response,
            }
        )
        return base

    def _serialize_stock_inquiry_summary(
        self, row: StockInquiry, *, has_revision_draft: bool = False
    ) -> dict:
        return {
            "id": str(row.id),
            "kind": "stock_inquiry",
            "title": (row.product_code or "Stock Inquiry"),
            # Display only, and the contact must see which version they are looking
            # at, so both carry the revision (UAC N1). The stored column stays bare.
            "reference": display_document_number(row) or row.inquiry_number,
            "document_number": display_document_number(row) or row.inquiry_number,
            "status": row.status,
            "rejection_reason": row.rejection_reason,
            "is_editable": bool(row.portal_draft_at) or row.status == "rejected",
            "is_draft": row.portal_draft_at is not None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_revised_at": row.last_revised_at.isoformat() if row.last_revised_at else None,
            "revision_no": int(row.revision_no or 0),
            # Marks a list row that has an unsent revision draft parked - the row's
            # own `status` stays the real one; this is what tells the two apart
            # from the entity that simply hasn't been touched (UAC: revision drafts).
            "has_revision_draft": has_revision_draft,
            "product_code": row.product_code,
            "project_name": row.project_name,
            "project_customer": row.project_customer,
            "item_description": row.item_description,
        }

    def _resolve_contact_name_by_id(self, contact_id: Optional[str]) -> Optional[str]:
        """Live display name for a requestor FK - a rename fixes every screen
        with no backfill (D9); the stored text label stays as the point-in-time
        fallback when the FK is NULL."""
        if not contact_id:
            return None
        contact = self.db.query(RespondContact).filter(RespondContact.id == contact_id).first()
        return self._contact_display_name(contact) if contact else None

    def _serialize_stock_inquiry_detail(self, row: StockInquiry) -> dict:
        base = self._serialize_stock_inquiry_summary(row)
        salesperson_contact_id = getattr(row, "salesperson_contact_id", None)
        base.update(
            {
                "salesperson": row.salesperson,
                "product_code": row.product_code,
                "item_description": row.item_description,
                "project_customer": row.project_customer,
                "project_name": row.project_name,
                "quantity": row.quantity,
                "delivery_date": row.delivery_date,
                "remark": row.remark,
                "additional_remark": row.additional_remark,
                "purchasing_response": row.purchasing_response,
                "salesperson_contact_id": salesperson_contact_id,
                "salesperson_contact_name": self._resolve_contact_name_by_id(salesperson_contact_id),
            }
        )
        return base

    def _serialize_request_summary(
        self, row: PurchaseRequestHeader, *, has_revision_draft: bool = False
    ) -> dict:
        approval_rejected = (
            (getattr(row, "approval_status", None) or "").strip().lower() == "rejected"
        )
        # Approval rejection is the user-visible state on the portal - fold it into ``status``
        # so the portal badge + editable gate behave like complaint/stock_inquiry rejections.
        effective_status = "rejected" if approval_rejected else row.status
        is_editable = bool(row.portal_draft_at) or row.status == "rejected" or approval_rejected
        rejection_reason = row.approval_comments if approval_rejected else None
        return {
            "id": str(row.id),
            "kind": row.request_type,
            "title": row.project_title or row.sponsor_subject or "Request",
            "reference": display_document_number(row) or row.request_number,
            "document_number": display_document_number(row) or row.request_number,
            "status": effective_status,
            "approval_status": row.approval_status,
            "rejection_reason": rejection_reason,
            "is_editable": is_editable,
            "is_draft": row.portal_draft_at is not None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_revised_at": row.last_revised_at.isoformat() if row.last_revised_at else None,
            "revision_no": int(row.revision_no or 0),
            # See _serialize_stock_inquiry_summary - same convention here.
            "has_revision_draft": has_revision_draft,
            "project_title": row.project_title,
            "customer_name": row.customer_name,
            "sponsor_subject": row.sponsor_subject,
            "purpose": row.purpose,
        }

    def _project_code(self, project_id) -> Optional[str]:
        """The human-readable reference for a linked project, or None.

        Looked up unscoped on purpose: the portal request is authorised by the contact's
        token, and the contact's company set was already checked when the link was
        accepted, so re-filtering here would only hide a link that is legitimately theirs.
        """
        if not project_id:
            return None
        from app.models.projects import Project

        row = (
            self.db.query(Project.project_code)
            .filter(Project.id == str(project_id))
            .first()
        )
        return row[0] if row else None

    def _serialize_request_detail(self, row: PurchaseRequestHeader) -> dict:
        base = self._serialize_request_summary(row)
        requested_by_contact_id = getattr(row, "requested_by_contact_id", None)
        base.update(
            {
                "request_type": row.request_type,
                "request_date": row.request_date.isoformat() if row.request_date else None,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "customer_name": row.customer_name,
                # Must be echoed back or a saved draft reopens with PIC blank: the
                # write allowlist and the read serializer are two separate lists and
                # a field has to be in BOTH.
                "pic": getattr(row, "pic", None),
                "project_title": row.project_title,
                "purpose": row.purpose,
                "sales_type": row.sales_type,
                "delivery_address": row.delivery_address,
                "total_project_value": str(row.total_project_value) if row.total_project_value is not None else None,
                "total_project_value_text": row.total_project_value_text,
                "sponsor_subject": row.sponsor_subject,
                "sponsor_subject_other": getattr(row, "sponsor_subject_other", None),
                "expected_delivery_date": row.expected_delivery_date.isoformat() if row.expected_delivery_date else None,
                "expected_po_date": row.expected_po_date.isoformat() if row.expected_po_date else None,
                "requested_by": row.requested_by,
                "requested_by_contact_id": requested_by_contact_id,
                "requested_by_contact_name": self._resolve_contact_name_by_id(requested_by_contact_id),
                "external_reference": row.external_reference,
                # AC-F3. The id round-trips the picker; the code and title are what the
                # portal shows, because a UUID in the UI is never the answer.
                "project_id": str(row.project_id) if row.project_id else None,
                "project_code": self._project_code(row.project_id),
                "products": [
                    {
                        "id": str(line.id),
                        "item_code": line.item_code,
                        "quantity": str(line.quantity) if line.quantity is not None else None,
                        "unit_price": str(line.unit_price) if line.unit_price is not None else None,
                        "total": str(line.total) if line.total is not None else None,
                        "remark": line.remark,
                    }
                    for line in sorted(row.lines or [], key=lambda l: l.sort_order or 0)
                ],
            }
        )
        return base
