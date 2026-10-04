"""Respond contacts API routes."""
from fastapi import APIRouter, Depends, Query, status, HTTPException, Body, Request
from sqlalchemy.orm import Session
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
import logging
import uuid
import httpx
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.access import RespondContact
from app.models.respond_workspace import RespondWorkspace
from app.services.contact_service import ContactService
from app.services.portal_service import PortalService
from app.services.user_service import UserPermissionService
from app.services.user_contact_link import (
    is_salesperson_contact,
    linked_user_summary,
    suggested_role_slug,
)
from app.schemas.user import RespondContactResponse, RespondContactCreate, RespondContactUpdate, ContactAgentAccessResponse
from app.schemas.common import ListResponse
from app.schemas.market_segment import MarketSegmentCodesUpdate
from app.schemas.contact_brand import ContactBrandsResponse, ContactBrandsUpdate
from app.schemas.contact_customer import (
    ContactCustomerLinkCreate,
    ContactCustomerLinksResponse,
    ContactCustomersResponse,
)
from app.services import contact_customer_service
from app.services.company_scope_resolver import company_name_map, grants_scope
from app.services.error_handler import handle_internal_error, handle_not_found

logger = logging.getLogger(__name__)

router = APIRouter()


class ContactCompaniesUpdateRequest(BaseModel):
    company_ids: list[str]


class ContactAttachmentTypesUpdate(BaseModel):
    attachment_type_ids: list[str]


def _resolve_space_id(db: Session, contact_id: str) -> tuple[RespondContact, str]:
    """Look up contact and resolve its workspace's space_id.

    Raises 404 if the contact does not exist; raises 422 (with a "workspace"
    message) if the contact is not associated with a usable workspace.
    """
    contact = db.query(RespondContact).filter(RespondContact.id == contact_id).first()
    if contact is None:
        raise handle_not_found("Contact", contact_id)
    if not contact.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contact has no workspace; cannot mint portal link.",
        )
    workspace = (
        db.query(RespondWorkspace)
        .filter(RespondWorkspace.id == contact.workspace_id)
        .first()
    )
    if workspace is None or not workspace.space_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contact has no workspace; cannot mint portal link.",
        )
    return contact, workspace.space_id


def _effective_base_url(request: Request, payload_base_url: Optional[str]) -> Optional[str]:
    """Resolve the FE base URL for portal links.

    Order: explicit payload override > settings.frontend_base_url > derived from
    request (scheme://host). Ensures the response always carries an absolute URL
    so QR codes and Respond.io chat messages render correctly.
    """
    if payload_base_url:
        return payload_base_url
    from app.config import settings as _settings
    if (_settings.frontend_base_url or "").strip():
        return _settings.frontend_base_url
    base = str(request.base_url).rstrip("/")
    return base or None


@router.get("/", response_model=ListResponse[RespondContactResponse])
async def get_contacts(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    # AC-MEM028 (reviewer pass at d89110c0, S13): `own` lists the contacts that set
    # their own chatbot memory level, the Memory card's count link.
    chatbot_memory_level: Optional[Literal["own"]] = Query(None),
    # `none` lists the contacts with no linked customer (bulk Link customers worklist).
    customers: Optional[Literal["none"]] = Query(None),
    # CONTACT-BULK-ACCESS (UAC A2.3): access filters, each optional.
    access_type: Optional[str] = Query(None),
    tier: Optional[Literal["dealer", "office", "end_user", "none"]] = Query(None),
    cost: Optional[Literal["yes", "no"]] = Query(None),
    escalation: Optional[Literal["yes", "no"]] = Query(None),
    packing_list: Optional[Literal["yes", "no"]] = Query(None),
    stock: Optional[Literal["yes", "no"]] = Query(None),
    customer_id: Optional[uuid.UUID] = Query(None),
    access_differs_from: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db)
):
    """Get all respond contacts with pagination and filtering."""
    try:
        service = ContactService(db)
        can_view_users = UserPermissionService(db).check_user_has_permission(
            current_user["id"], "user_management.users.view"
        )
        result = service.list_contacts(
            page=page,
            limit=limit,
            query=query,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc",
            own_memory_level_only=chatbot_memory_level == "own",
            include_linked_users=can_view_users,
            no_customers_only=customers == "none",
            access_filters={
                "access_type": access_type,
                "tier": tier,
                "cost": cost,
                "escalation": escalation,
                "packing_list": packing_list,
                "stock": stock,
                "customer_id": str(customer_id) if customer_id else None,
                "access_differs_from": access_differs_from,
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_contacts: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


class BulkDeleteContactsRequest(BaseModel):
    ids: list[str]


class BulkSyncContactsRequest(BaseModel):
    ids: list[str]


class PortalLinkRequest(BaseModel):
    base_url: Optional[str] = None
    submission_type: Optional[str] = None


class PortalLinkResponse(BaseModel):
    token: str
    expires_at: str
    portal_url: str
    reused: bool


class PortalLinkSendResponse(BaseModel):
    token: str
    expires_at: str
    portal_url: str
    reused: bool
    sent: bool


class BulkCopyAccessRequest(BaseModel):
    """CONTACT-BULK-ACCESS (UAC A1): copy one contact's access set to many contacts."""

    model_config = ConfigDict(extra="forbid")

    source_contact_id: str
    # Duplicates are collapsed (the 500 cap counts unique ids); the list itself is bounded too.
    target_contact_ids: list[Annotated[str, Field(max_length=64)]] = Field(..., min_length=1, max_length=1000)
    dry_run: bool = True

    @field_validator("target_contact_ids")
    @classmethod
    def _at_most_500(cls, ids: list[str]) -> list[str]:
        from app.services.contact_access_copy_service import MAX_TARGETS

        if len(dict.fromkeys(ids)) > MAX_TARGETS:
            raise ValueError(f"At most {MAX_TARGETS} contacts per copy.")
        return ids


@router.post("/bulk-copy-access", status_code=status.HTTP_200_OK)
def bulk_copy_contact_access(
    body: BulkCopyAccessRequest,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Preview (`dry_run`) or apply a copy of the source's access set to every target.

    One result row per target in request order: changed / unchanged / skipped / failed. Each
    target is saved on its own, so one failure never blocks or undoes the others. Linked
    customers are never copied. Contract: PLAN-contact-bulk-access-3oct.md."""
    from app.services import contact_access_copy_service

    try:
        source = db.query(RespondContact).filter(RespondContact.id == body.source_contact_id).first()
        if source is None:
            raise handle_not_found("Contact", body.source_contact_id)
        result = contact_access_copy_service.copy_access(
            db,
            source,
            body.target_contact_ids,
            dry_run=body.dry_run,
            actor_id=str(current_user.get("id") or "") or None,
        )
        # A dry run wrote nothing (no savepoint was opened); `get_db` never commits.
        if not body.dry_run:
            db.commit()
        return result
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in bulk_copy_contact_access: {e}", exc_info=True)
        raise handle_internal_error(str(e))


@router.post("/bulk-sync", status_code=status.HTTP_200_OK)
async def bulk_sync_contacts(
    body: BulkSyncContactsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sync multiple contacts from Respond.io (name, first/last name, respond_io_id)."""
    try:
        service = ContactService(db)
        result = service.bulk_sync_contacts_from_respond(body.ids)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk_sync_contacts: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_contacts(
    body: BulkDeleteContactsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk delete contacts by ID. Related access records are removed."""
    try:
        service = ContactService(db)
        result = service.bulk_delete_contacts(body.ids)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk_delete_contacts: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


@router.get("/{contact_id}", response_model=RespondContactResponse)
async def get_contact(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db)
):
    """Get a single contact by ID."""
    try:
        service = ContactService(db)
        contact = service.get_contact(contact_id)
        data = ContactService.contact_to_response_dict(contact, db)
        data["is_salesperson"] = is_salesperson_contact(db, contact_id)
        data["suggested_role_slug"] = suggested_role_slug(db, contact_id)
        can_view_users = UserPermissionService(db).check_user_has_permission(
            current_user["id"], "user_management.users.view"
        )
        linked = linked_user_summary(db, contact_id) if can_view_users else None
        data["linked_user"] = linked
        data["linked_user_id"] = linked["id"] if linked else None
        data["linked_user_name"] = linked["name"] if linked else None
        return RespondContactResponse.model_validate(data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=RespondContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    contact_data: RespondContactCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new respond contact."""
    try:
        service = ContactService(db)
        contact = service.create_contact(contact_data)
        return RespondContactResponse.model_validate(ContactService.contact_to_response_dict(contact, db))
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}", response_model=RespondContactResponse)
async def update_contact(
    contact_id: str,
    contact_data: RespondContactUpdate,
    # Reviewer pass at d89110c0 (S8): the same edit slug every other write on the
    # contact's editable surface requires.
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db)
):
    """Update a respond contact."""
    try:
        service = ContactService(db)
        contact = service.update_contact(contact_id, contact_data)
        return RespondContactResponse.model_validate(ContactService.contact_to_response_dict(contact, db))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating contact {contact_id}: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


class ContactChatbotUpdate(BaseModel):
    """The Contact > Access "Chatbot" card (AC-1515, AC-1561; chatbot memory lane A,
    contract section 5).

    A PUT of its own rather than more fields on the contact PUT: these decide what the
    bot REMEMBERS about a person and how it may behave with them, which is a privacy
    decision with its own audience. Keeping it separate is what lets the card be
    granted, audited and reasoned about on its own.

    `memory_level` (round 3 renames the body key off `chatbot_memory_level` - the
    RESPONSE / dict-builder field keeps that name, unchanged) replaces
    `chatbot_recall_enabled` (DROPPED, model and column both, round 3 AC-MEM054):
    present and null means "follow the system default", absent means "leave it
    alone", same rule every other field on this card already follows. `extra="forbid"`
    is what turns a body still naming the retired `chatbot_recall_enabled` into a 422
    instead of a silently-ignored no-op.
    """

    model_config = ConfigDict(extra="forbid")

    chatbot_profile: dict | None = None
    memory_level: Literal["off", "conversation", "episodes", "full"] | None = None
    # S6: absent = leave alone, same rule as every field on this card.
    chatbot_stock_allowed: bool | None = None
    # Chatbot stock ask v2 S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): absent = leave
    # alone, same rule as every other field on this card.
    notify_salesman: bool | None = None
    packing_list_allowed: bool | None = None
    # Issue #1328: absent = leave alone.
    chatbot_eta_offset_applied: bool | None = None
    # ESCALATION-CONTROL: "Chatbot hands over to support teams". Absent = leave alone.
    escalation_allowed: bool | None = None


@router.put("/{contact_id}/chatbot", response_model=RespondContactResponse)
async def update_contact_chatbot(
    contact_id: str,
    body: ContactChatbotUpdate,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Set a contact's chatbot profile and recall toggle.

    Absent means "leave it alone", not "clear it": the card sends whichever half the
    operator touched, and a recall toggle must never be switched off as a side effect of
    saving a language.

    Guarded by the same `user_management.contacts.edit` the rest of the contact's
    editable surface is: this writes the stock-check and recall switches, which decide
    what the chatbot will tell that contact.
    """
    _ = current_user
    try:
        service = ContactService(db)
        contact = service.get_contact(contact_id)
        if body.chatbot_profile is not None:
            # Chatbot memory lane A (contract section 5): "`chatbot_profile` in the
            # body never touches `facts`" - see `write_chatbot_profile_keeping_facts`.
            service.write_chatbot_profile_keeping_facts(contact.id, body.chatbot_profile)
        # Present (including explicit null) sets it; absent leaves it alone - the same
        # rule every other field on this card follows. `model_fields_set` is the only
        # way to tell "sent as null" from "not sent at all" once both read as `None`.
        if "memory_level" in body.model_fields_set:
            contact.chatbot_memory_level = body.memory_level
        if body.chatbot_stock_allowed is not None:
            contact.chatbot_stock_allowed = body.chatbot_stock_allowed
        if body.notify_salesman is not None:
            contact.notify_salesman = body.notify_salesman
        if body.packing_list_allowed is not None:
            contact.packing_list_allowed = body.packing_list_allowed
        if body.chatbot_eta_offset_applied is not None:
            contact.chatbot_eta_offset_applied = body.chatbot_eta_offset_applied
        if body.escalation_allowed is not None:
            contact.escalation_allowed = body.escalation_allowed
        # `get_db` never commits (it only closes), so a flush here rolled back on
        # return: PUT 200, row untouched. Main's convention is the commit in the route.
        db.commit()
        db.refresh(contact)
        return RespondContactResponse.model_validate(
            ContactService.contact_to_response_dict(contact, db)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating contact chatbot settings {contact_id}: {e}", exc_info=True)
        raise handle_internal_error(str(e))


#: Security review 26 Sep 2026 (S3): a `GET .../chatbot/memory` caller who holds
#: only `user_management.contacts.view` still gets facts and open orders, but
#: `episodes` (free-text conversation summaries) is `null` unless they ALSO hold
#: this - the same "chat trace" slug `ai_assistant.py`'s prompt-test route already
#: requires on top of its own edit slug. The safer of the two options the review
#: offered: null out the one sensitive field rather than blocking the whole route.
_CHATBOT_EPISODES_VIEW = "system.chat_history.view"


@router.get("/{contact_id}/chatbot/memory")
async def get_contact_chatbot_memory(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """Facts, episodes and open orders (chatbot memory lane A, contract section 5).
    Router is HTTP only - `ContactService.get_chatbot_memory` owns every read."""
    from app.services.user_service import UserPermissionService

    can_view_episodes = UserPermissionService(db).check_user_has_permission(
        current_user["id"], _CHATBOT_EPISODES_VIEW
    )
    try:
        # Same grants as the facts PUT, so a CRM fact reads the same on every company.
        with grants_scope(db, current_user["id"]):
            return ContactService(db).get_chatbot_memory(contact_id, include_episodes=can_view_episodes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading chatbot memory for {contact_id}: {e}", exc_info=True)
        raise handle_internal_error(str(e))


class ContactFactUpdate(BaseModel):
    # S4 (security review 26 Sep 2026): every fact value in the vocabulary is
    # either free text or a short list of strings (`profile_facts.VOCABULARY`) -
    # `object` accepted anything, including a nested structure no fact ever needs.
    value: str | list[str]


@router.put("/{contact_id}/chatbot/facts/{key}")
async def put_contact_chatbot_fact(
    contact_id: str,
    key: str,
    body: ContactFactUpdate,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Sets a staff fact; 422 on an unknown key, a CRM-only key, a value outside its
    choices, or over its length/count limit (contract section 5)."""
    from app.services.user_service import UserPermissionService

    # Reviewer pass at d89110c0 (B4): the response is the memory GET's body, so it
    # hides `episodes` exactly as the GET does.
    can_view_episodes = UserPermissionService(db).check_user_has_permission(
        current_user["id"], _CHATBOT_EPISODES_VIEW
    )
    try:
        # Usual brands / sites are validated against the master lists of every company the
        # editor is granted, not only the active one: a contact belongs to no one company.
        with grants_scope(db, current_user["id"]):
            return ContactService(db).set_contact_fact(
                contact_id, key, body.value, user_id=current_user["id"], include_episodes=can_view_episodes
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting chatbot fact {key} for {contact_id}: {e}", exc_info=True)
        raise handle_internal_error(str(e))


@router.delete("/{contact_id}/chatbot/facts/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact_chatbot_fact(
    contact_id: str,
    key: str,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Hard delete through the deferred-action path - the FE fires this when the
    countdown lapses (`contact_chatbot_fact.delete` in `app/services/record_actions.py`
    already calls the same `ContactService.delete_contact_fact`); reachable directly
    too, for a caller that has already waited out its own window."""
    _ = current_user
    try:
        ContactService(db).delete_contact_fact(contact_id, key)
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting chatbot fact {key} for {contact_id}: {e}", exc_info=True)
        raise handle_internal_error(str(e))


# No permission dependency by design: the gate is in the handler body, which calls
# `_require_superadmin(db, current_user)` before reading anything. See
# `documentation/plans/security/PLAN-user-management-read-gates.md`.
@router.get("/{contact_id}/companies")
async def get_contact_companies(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List companies granted to a contact as [{id,name,code}]. Superadmin-only."""
    from app.api.v1.system.companies import _require_superadmin
    try:
        _require_superadmin(db, current_user)
        return ContactService(db).list_contact_companies(contact_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}/companies", status_code=status.HTTP_200_OK)
async def set_contact_companies(
    contact_id: str,
    body: ContactCompaniesUpdateRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Replace a contact's company grants with the given company_ids. Superadmin-only."""
    from app.api.v1.system.companies import _require_superadmin
    try:
        _require_superadmin(db, current_user)
        return ContactService(db).set_contact_companies(contact_id, body.company_ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a respond contact. Related access records are cascade-deleted; SLA tracking references are set to null."""
    try:
        service = ContactService(db)
        service.delete_contact(contact_id)
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting contact {contact_id}: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


@router.post("/{contact_id}/sync", response_model=RespondContactResponse)
async def sync_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Sync contact name from Respond.io API."""
    try:
        service = ContactService(db)
        contact = service.sync_contact_name(contact_id)
        return RespondContactResponse.model_validate(ContactService.contact_to_response_dict(contact, db))
    except HTTPException:
        raise
    except ValueError as e:
        # Configuration error - API key not set
        logger.error(f"Configuration error syncing contact {contact_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to sync contact. Respond.io API is not configured.",
                "detail": str(e),
                "code": "CONFIGURATION_ERROR"
            }
        )
    except Exception as e:
        logger.error(f"Error syncing contact {contact_id}: {str(e)}", exc_info=True)
        
        # Handle httpx exceptions
        if isinstance(e, httpx.HTTPStatusError):
            status_code = e.response.status_code
            if status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "message": "Contact not found in Respond.io",
                        "detail": "The contact does not exist in Respond.io system.",
                        "code": "CONTACT_NOT_FOUND"
                    }
                )
            elif status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "message": "Unauthorized access to Respond.io API",
                        "detail": "Invalid API key or insufficient permissions.",
                        "code": "UNAUTHORIZED"
                    }
                )
            else:
                error_detail = f"HTTP {status_code}: {e.response.text if hasattr(e.response, 'text') else str(e)}"
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "message": "Respond.io API error",
                        "detail": error_detail,
                        "code": f"HTTP_{status_code}"
                    }
                )
        elif isinstance(e, httpx.TimeoutException):
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "message": "Request to Respond.io API timed out",
                    "detail": "The API request took too long to complete.",
                    "code": "TIMEOUT_ERROR"
                }
            )
        elif isinstance(e, httpx.ConnectError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Failed to connect to Respond.io API",
                    "detail": "Unable to establish connection to Respond.io servers.",
                    "code": "CONNECTION_ERROR"
                }
            )
        
        # Generic error
        error_detail = str(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to sync contact from Respond.io",
                "detail": error_detail,
                "code": "SYNC_ERROR"
            }
        )


class CsRoutingPinRequest(BaseModel):
    cs_pic_user_id: str
    # Predicate set [{field, operator, value}], AND-combined, matched against the
    # form header fields. Empty/omitted = wildcard. Lower `priority` wins among
    # matching rows for a form (pure admin order).
    match_conditions: Optional[list] = None
    priority: int = 0


@router.get("/cs-routing/candidates")
async def list_cs_routing_candidates(
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """Tier-1 members of the procurement customer-service team (pin dropdown options)."""
    try:
        from app.services.cs_routing_service import CsRoutingService

        return {"candidates": CsRoutingService(db).list_candidates()}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{contact_id}/cs-routing")
async def get_contact_cs_routing(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """List a salesman's CS-PIC pins (one per procurement use_case)."""
    try:
        from app.services.cs_routing_service import CsRoutingService

        return {"pins": CsRoutingService(db).list_for_contact(contact_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}/cs-routing/{use_case}")
async def upsert_contact_cs_routing(
    contact_id: str,
    use_case: str,
    payload: CsRoutingPinRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pin this salesman to a CS PIC for ``use_case`` (purchase_request | sponsorship_form)."""
    try:
        from app.services.cs_routing_service import CsRoutingService

        row = CsRoutingService(db).upsert(
            contact_id,
            use_case,
            payload.cs_pic_user_id,
            match_conditions=payload.match_conditions,
            priority=payload.priority,
            created_by=current_user.get("id"),
        )
        return {
            "id": row.id,
            "use_case": row.use_case,
            "cs_pic_user_id": row.cs_pic_user_id,
            "match_conditions": row.match_conditions or [],
            "priority": row.priority or 0,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/cs-routing/fields")
async def list_cs_routing_fields(
    use_case: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """Routable predicate fields for a use_case's form (lookup + curated header fields)."""
    try:
        from app.services.cs_routing_service import CsRoutingService

        return {"fields": CsRoutingService(db).routable_fields(use_case)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{contact_id}/cs-routing/{use_case}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact_cs_routing(
    contact_id: str,
    use_case: str,
    row_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Clear routing rows for a use_case (or one ``row_id``) → reverts to round-robin."""
    try:
        from app.services.cs_routing_service import CsRoutingService

        CsRoutingService(db).delete(contact_id, use_case, row_id=row_id)
        return None
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{contact_id}/market-segments")
async def get_contact_market_segments(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """List a contact's assigned market segments (retail / project). Empty = matches all CS members."""
    try:
        from app.services.market_segment_service import MarketSegmentService

        return {"codes": MarketSegmentService(db).get_contact_segment_codes(contact_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}/market-segments")
async def set_contact_market_segments(
    contact_id: str,
    payload: MarketSegmentCodesUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace a contact's market segments with the supplied set (empty = clear → matches all)."""
    try:
        from app.services.market_segment_service import MarketSegmentService

        return {
            "codes": MarketSegmentService(db).set_contact_segments(contact_id, payload.codes)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{contact_id}/brands", response_model=ContactBrandsResponse)
async def get_contact_brands(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """The brands this contact may see through the chatbot / MCP. Empty = every brand."""
    try:
        from app.services.contact_brand_scope import get_contact_brands as read

        return read(db, contact_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}/brands", response_model=ContactBrandsResponse)
async def set_contact_brands(
    contact_id: str,
    payload: ContactBrandsUpdate,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Replace the contact's accessible brands (empty = clear, every brand)."""
    try:
        from app.services.contact_brand_scope import set_contact_brands as write

        return write(db, contact_id, payload.brand_ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{contact_id}/attachment-types")
async def get_contact_attachment_types(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """Document types this contact may retrieve, on top of the direct-access baseline.

    Returns the whole catalog with a `granted` flag so the dialog can show what is
    grantable, not only what is granted.
    """
    try:
        from app.services.contact_attachment_access import list_grants

        return list_grants(db, contact_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{contact_id}/attachment-types")
async def set_contact_attachment_types(
    contact_id: str,
    payload: ContactAttachmentTypesUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace this contact's attachment-type grants (empty = baseline only)."""
    try:
        from app.services.contact_attachment_access import set_grants

        return set_grants(
            db,
            contact_id,
            payload.attachment_type_ids,
            actor=str(current_user.get("id") or current_user.get("sub") or ""),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _require_contact(db: Session, contact_id: str) -> None:
    if contact_customer_service.get_contact(db, contact_id) is None:
        raise handle_not_found("Contact", contact_id)


@router.get("/{contact_id}/customers", response_model=ContactCustomersResponse)
async def get_contact_customers(
    contact_id: str,
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db),
):
    """The customer accounts this contact belongs to."""
    try:
        # A contact belongs to no one company: read every company the caller is granted.
        with grants_scope(db, current_user["id"]):
            _require_contact(db, contact_id)
            return contact_customer_service.contact_customers_payload(db, contact_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{contact_id}/customers",
    response_model=ContactCustomerLinksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_contact_customers(
    contact_id: str,
    payload: ContactCustomerLinkCreate,
    current_user: dict = Depends(require_permission("user_management.contacts.edit")),
    db: Session = Depends(get_db),
):
    """Link several customers to this contact in one request, all or nothing.

    Every id is resolved under the caller's scope BEFORE anything is written, so one
    unknown or hidden id answers 404 and links none. An already-linked customer is a no-op
    that answers its existing row. Rows come back in request order."""
    try:
        # Every id resolves under the caller's grants; the link keeps the customer's own company.
        with grants_scope(db, current_user["id"]):
            _require_contact(db, contact_id)
            customers = []
            for customer_id in dict.fromkeys(payload.customer_ids):
                customer = contact_customer_service.get_customer_in_scope(db, customer_id)
                if customer is None:
                    # Unknown and out-of-scope are the same answer: scope hides the row.
                    raise handle_not_found("Customer", customer_id)
                customers.append(customer)
            linked_by = str(current_user.get("id") or current_user.get("sub") or "") or None
            links = [
                contact_customer_service.link_customer(
                    db, contact_id, customer.id, customer=customer, linked_by=linked_by
                )
                for customer in customers
            ]
            db.commit()
            rows = []
            names = company_name_map(db)
            for link, customer in zip(links, customers):
                db.refresh(link)
                rows.append(contact_customer_service.link_row(link, customer, names))
            return {"data": rows}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.get("/{contact_id}/access-agents", response_model=ListResponse[ContactAgentAccessResponse])
async def get_contact_access_agents(
    contact_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    current_user: dict = Depends(require_permission("user_management.contacts.view")),
    db: Session = Depends(get_db)
):
    """Get all access agents for a specific contact."""
    try:
        from app.services.user_service import AccessAgentService
        from app.schemas.user import ContactAgentAccessResponse

        service = AccessAgentService(db)
        # List contact accesses filtered by respond_contact_id
        result = service.list_all_contact_accesses(
            page=page,
            limit=limit,
            respond_contact_id=contact_id,
        )

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{contact_id}/portal-link", response_model=PortalLinkResponse)
async def get_contact_portal_link(
    contact_id: str,
    request: Request,
    payload: PortalLinkRequest = Body(default_factory=PortalLinkRequest),
    current_user: dict = Depends(require_permission("user_management.contacts.portal_link")),
    db: Session = Depends(get_db),
):
    """Mint or reuse a 7-day user-submission portal token for the contact."""
    _, space_id = _resolve_space_id(db, contact_id)
    service = PortalService(db)
    token, reused = service.get_or_mint_token(contact_id, space_id)
    base_url = _effective_base_url(request, payload.base_url)
    return PortalLinkResponse(
        token=token.token,
        expires_at=token.expires_at.isoformat(),
        portal_url=service.build_portal_url(
            token.token, base_url, payload.submission_type, token_row=token
        ),
        reused=reused,
    )


@router.post("/{contact_id}/portal-link/send", response_model=PortalLinkSendResponse)
async def send_contact_portal_link(
    contact_id: str,
    request: Request,
    payload: PortalLinkRequest = Body(default_factory=PortalLinkRequest),
    current_user: dict = Depends(require_permission("user_management.contacts.portal_link")),
    db: Session = Depends(get_db),
):
    """Mint or reuse a portal token and send the link to the contact via Respond.io."""
    contact, space_id = _resolve_space_id(db, contact_id)
    if not (contact.respond_io_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contact has no Respond.io identifier; cannot send link.",
        )
    service = PortalService(db)
    base_url = _effective_base_url(request, payload.base_url)
    try:
        result = service.send_link_via_respond_io(
            contact_id, space_id, base_url, payload.submission_type
        )
    except httpx.HTTPStatusError as exc:
        upstream = ""
        try:
            upstream = exc.response.text[:500]
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"Respond.io upstream failure: {upstream or str(exc)}",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Respond.io upstream timed out.",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Respond.io upstream unreachable: {exc.__class__.__name__}",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )
    return PortalLinkSendResponse(**result)
