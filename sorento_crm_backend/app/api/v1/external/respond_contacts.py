"""
External API for n8n / Respond.io: create or update internal respond contact by phone.
When a contact is created or updated in Respond.io, n8n can call this endpoint to sync
phone_number, name, and respond_io_id into our respond_contacts table. Access types are
managed in the CRM (M2M via respond_contact_access_types) and are NOT supplied by Respond.

Auth: X-API-Key header (get_external_api_user).
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.database import get_db
from app.dependencies import get_external_api_user
from app.schemas.external.contacts import (
    RespondContactSyncRequest,
    RespondContactSyncResponse,
)
from app.services.contact_service import ContactService
from app.schemas.user import RespondContactCreate, RespondContactUpdate
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=RespondContactSyncResponse, status_code=status.HTTP_200_OK)
async def sync_respond_contact(
    payload: RespondContactSyncRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """
    Create or update an internal respond contact by phone number.

    Call this from n8n when a contact is created or updated in Respond.io. The contact
    is matched by phone_number; if found, name fields are updated (if provided);
    otherwise a new contact is created. Access types are CRM-managed and not synced.

    **Auth:** X-API-Key header (external API key).

    **Body:**
  - phone_number (required): E.164 or similar, e.g. "+60160000509"
  - name (optional): Display name

    **Returns:** Contact id, phone_number, name, respond_io_id, and action ("created" | "updated").
    """
    phone = (payload.phone_number or payload.phone or "").strip()
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone_number or phone is required.",
        )
    respond_io_id = str(payload.id).strip() if payload.id is not None else None
    if respond_io_id == "":
        respond_io_id = None
    name = payload.name
    if name is None and (payload.firstName or payload.lastName):
        name = f"{(payload.firstName or '').strip()} {(payload.lastName or '').strip()}".strip() or None

    service = ContactService(db)
    existing = service.get_contact_by_phone(phone)

    if existing:
        update_dict: dict = {}
        if name is not None:
            update_dict["name"] = name
        if payload.firstName is not None:
            update_dict["first_name"] = (payload.firstName or "").strip() or None
        if payload.lastName is not None:
            update_dict["last_name"] = (payload.lastName or "").strip() or None
        if respond_io_id is not None:
            update_dict["respond_io_id"] = respond_io_id
        if not update_dict:
            return RespondContactSyncResponse(
                id=str(existing.id),
                phone_number=str(existing.phone_number),
                name=getattr(existing, "name", None),
                first_name=getattr(existing, "first_name", None),
                last_name=getattr(existing, "last_name", None),
                respond_io_id=getattr(existing, "respond_io_id", None),
                action="updated",
            )
        contact = service.update_contact(str(existing.id), RespondContactUpdate(**update_dict))
        return RespondContactSyncResponse(
            id=str(contact.id),
            phone_number=str(contact.phone_number),
            name=getattr(contact, "name", None),
            first_name=getattr(contact, "first_name", None),
            last_name=getattr(contact, "last_name", None),
            respond_io_id=getattr(contact, "respond_io_id", None),
            action="updated",
        )
    else:
        create_kwargs: dict = {
            "phone_number": phone,
            "name": name,
            "respond_io_id": respond_io_id,
        }
        if payload.firstName is not None:
            create_kwargs["first_name"] = (payload.firstName or "").strip() or None
        if payload.lastName is not None:
            create_kwargs["last_name"] = (payload.lastName or "").strip() or None
        create_data = RespondContactCreate(**create_kwargs)
        contact = service.create_contact(create_data)
        return RespondContactSyncResponse(
            id=str(contact.id),
            phone_number=str(contact.phone_number),
            name=getattr(contact, "name", None),
            first_name=getattr(contact, "first_name", None),
            last_name=getattr(contact, "last_name", None),
            respond_io_id=getattr(contact, "respond_io_id", None),
            action="created",
        )
