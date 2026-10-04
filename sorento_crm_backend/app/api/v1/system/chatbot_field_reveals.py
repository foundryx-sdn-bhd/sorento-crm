"""System API: per-contact chatbot field reveals (chatbot growth r1, Slice C).

One mechanism for two owner requirements: sellable stock defaults off for every
contact (D3), and a PO's supplier must never reach a dealer by default (D4). A
presenter marks a field `restricted=<key>` in `field_vocabulary`; these three
routes are the admin surface over the grant table:

* `GET /field-reveal-keys` - every restricted key that exists, with a label, for
  the Contacts > Access checklist. Sourced from the frozen
  `contact_field_reveal_service.FIELD_REVEAL_KEYS` literal, NOT a live read of
  `mcp_tools.restricted_fields`: the deployed backend image cannot import
  `sorento_crm_mcp` (compose builds it with `context: ./sorento_crm_backend`, the
  package is not in `requirements.txt`), so the startup catalog sync that writes
  that column never runs in production and the column stays at its migration-488
  default of `[]` forever. `mcp_tool_registry_service.sync_catalog` still writes
  it in an environment that CAN import the catalogue (a local checkout, the seed
  script); this route just does not depend on that having happened. A CI test
  pins the literal to the catalogue so the two cannot drift (AC-964).
* `GET /contacts/{respond_contact_id}/field-reveals` - the keys this contact
  currently holds.
* `PUT /contacts/{respond_contact_id}/field-reveals` - full-list replace.

Guarded by the same permission the Contacts > Access tab's other sections
already use (`ContactMediaAccessSection`, `AgentFieldAccessCard`'s pattern):
`user_management.contacts.view` to read, `.edit` to write. This is admin
configuration, not the chatbot turn path - nothing here is reached by a turn,
live or dry-run (AC-982).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.models.access import RespondContact
from app.schemas.chatbot_field_reveal import (
    ContactFieldRevealsResponse,
    ContactFieldRevealsUpdate,
    FieldRevealKeysResponse,
)
from app.services import contact_field_reveal_service as service
from app.services.error_handler import handle_not_found, handle_unprocessable

router = APIRouter(prefix="/chatbot")

CONTACT_VIEW = "user_management.contacts.view"
CONTACT_EDIT = "user_management.contacts.edit"


def _require_contact(db: Session, respond_contact_id: str) -> RespondContact:
    contact = db.query(RespondContact).filter(RespondContact.id == respond_contact_id).first()
    if contact is None:
        raise handle_not_found("Contact", respond_contact_id)
    return contact


@router.get("/field-reveal-keys", response_model=FieldRevealKeysResponse)
def list_field_reveal_keys(
    current_user: dict = Depends(require_permission(CONTACT_VIEW)),
):
    """Every restricted key that exists, with its label (AC-963, AC-964)."""
    _ = current_user
    return FieldRevealKeysResponse(items=service.field_reveal_keys())


@router.get(
    "/contacts/{respond_contact_id}/field-reveals", response_model=ContactFieldRevealsResponse
)
def get_contact_field_reveals(
    respond_contact_id: str,
    current_user: dict = Depends(require_permission(CONTACT_VIEW)),
    db: Session = Depends(get_db),
):
    _ = current_user
    _require_contact(db, respond_contact_id)
    return ContactFieldRevealsResponse(granted=service.granted_keys(db, respond_contact_id))


@router.put(
    "/contacts/{respond_contact_id}/field-reveals", response_model=ContactFieldRevealsResponse
)
def set_contact_field_reveals(
    respond_contact_id: str,
    payload: ContactFieldRevealsUpdate,
    current_user: dict = Depends(require_permission(CONTACT_EDIT)),
    db: Session = Depends(get_db),
):
    """Full-list replace: exactly `payload.granted` ends up granted (AC-963)."""
    _require_contact(db, respond_contact_id)

    allowed = {item["key"] for item in service.field_reveal_keys()}
    # A key the contact already holds but this build does not know (seeded by another
    # lane's migration, merge order) comes back from the screen on every save. It is
    # tolerated and left untouched (`set_granted_keys` only writes known keys); only a
    # NEW unknown key is refused.
    held = set(service.granted_keys(db, respond_contact_id))
    unknown = sorted(set(payload.granted) - allowed - held)
    if unknown:
        raise handle_unprocessable(
            f"Unknown field reveal key(s): {', '.join(unknown)}. "
            f"Allowed keys: {', '.join(sorted(allowed)) or 'none'}."
        )

    granted = service.set_granted_keys(
        db,
        respond_contact_id,
        [key for key in payload.granted if key in allowed],
        actor_id=str(current_user.get("id") or ""),
    )
    return ContactFieldRevealsResponse(granted=granted)
