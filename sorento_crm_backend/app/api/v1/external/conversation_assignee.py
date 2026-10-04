"""
External API for n8n / external systems: set conversation assignee by contact phone and Respond user id.
Auth: X-API-Key header (get_external_api_user).
"""
from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.dependencies import get_external_api_user
from app.services.sla_service import ConversationSLATrackingService

router = APIRouter()


@router.post("", status_code=200)
async def set_conversation_assignee(
    body: dict,
    current_user: dict = Depends(get_external_api_user),
    db=Depends(get_db),
):
    """
    Update the assignee on Conversation SLA Tracking in our system (CRM only; no Respond.io API call).
    Use this from n8n or other external systems to set who is assigned to a conversation by contact phone.

    **Auth:** X-API-Key header (external API key).

    **Body:**
  - contact_phone_number (or contact_phone): Contact phone number, e.g. "+60160000509"
  - respond_user_id (or assignee_respond_user_id): Respond.io user id for the assignee, e.g. "1023495".
      Use empty string or omit to unassign.

    **Example (n8n):**
    ```json
    {
      "contact_phone_number": "+60160000509",
      "respond_user_id": "1023495"
    }
    ```

    **Returns:** Updated tracking info and assignee details, or 404 if no SLA tracking exists for that contact.

    **AC-F1 caution (multi-open consumer audit):** resolves "the" tracking for the
    phone via `get_tracking_by_contact_phone` (documented MOST-RECENT-OPEN pick -
    see `ConversationSLATrackingService.get_preferred_tracking_for_contact`), then
    MUTATES its assignee. A contact holding 2+ open tickets only ever gets the
    newest one's assignee changed by this call; siblings are untouched. Kept as-is
    for backward compat (regression net 3) - a per-ticket contract (tracking_id in
    the body) is the S3.2 cutover target.
    """
    contact_phone = (
        (body.get("contact_phone_number") or body.get("contact_phone") or "").strip()
    )
    if not contact_phone:
        raise HTTPException(
            status_code=400,
            detail="contact_phone_number (or contact_phone) is required.",
        )
    respond_user_id = (
        (body.get("respond_user_id") or body.get("assignee_respond_user_id") or "")
    )
    if isinstance(respond_user_id, (int, float)):
        respond_user_id = str(int(respond_user_id))
    else:
        respond_user_id = (respond_user_id or "").strip()

    sla_service = ConversationSLATrackingService(db)
    tracking = sla_service.get_tracking_by_contact_phone(contact_phone)
    if not tracking:
        raise HTTPException(
            status_code=404,
            detail=f"No conversation SLA tracking found for contact phone: {contact_phone}. "
            "Ensure the contact exists in Respond and has an active tracking.",
        )
    try:
        result = sla_service.set_assignee_for_tracking(
            str(tracking.id),
            respond_user_id,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
