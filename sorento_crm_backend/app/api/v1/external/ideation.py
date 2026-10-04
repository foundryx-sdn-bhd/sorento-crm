"""External API: `ideate` brain-path turn endpoint (ideation pipeline, D7/D8, §5.1/§5.2).

n8n classifies a WhatsApp turn as `ideate` and POSTs it here. This endpoint runs one
`create_idea` call against shared-service, manages the ``session_vars.ideation`` pointer,
and returns ``{ status, reply_text, link?, session_vars }`` for n8n to relay + persist.

Every call writes an ``integration_log`` on success AND failure (AC-20), mirroring the
conversation-variables endpoint. A shared-service outage is handled inside the service
(graceful reply, never a 500 - AC-19); an unexpected error still logs then re-raises.
"""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_external_api_user
from app.schemas.external.ideation import IdeationTurnRequest, IdeationTurnResponse
from app.schemas.integration import IntegrationLogCreate
from app.services.ideation_capture_service import handle_capture_turn
from app.services.integration_service import (
    IntegrationLogService,
    sanitize_request_headers,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/turn", response_model=IdeationTurnResponse, status_code=status.HTTP_200_OK)
def ideation_turn(
    payload: IdeationTurnRequest,
    request: Request,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Handle one `ideate` turn: capture the idea from the one message, update session_vars, relay."""
    _ = current_user

    response_status = status.HTTP_200_OK
    error_message: str | None = None
    response_payload: IdeationTurnResponse | None = None
    _http_exc_to_reraise: HTTPException | None = None

    try:
        # media_selection / is_new_idea are accepted for old n8n callers and ignored.
        result = handle_capture_turn(
            db,
            respond_io_id=payload.respond_io_id,
            message_text=payload.message_text,
            submitter_name=payload.submitter_name,
            session_vars_in=payload.session_vars,
            ask_reply=payload.ask_reply,
            is_test=payload.is_test,
        )
        response_payload = IdeationTurnResponse(**result)
    except HTTPException as http_exc:
        response_status = http_exc.status_code
        error_message = str(http_exc.detail)
        _http_exc_to_reraise = http_exc
    except Exception as exc:  # noqa: BLE001 - log then surface a clean 500
        logger.exception("ideation turn failed: %s", exc)
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_message = "Failed to handle ideation turn."
        _http_exc_to_reraise = HTTPException(
            status_code=response_status, detail=error_message
        )

    try:
        request_headers = sanitize_request_headers(dict(request.headers))
        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n",
                business_table="respond_contacts.session_vars",
                business_id=str(uuid.uuid4()),
                external_reference=payload.respond_io_id,
                direction="inbound",
                endpoint=str(request.url.path),
                http_method=request.method,
                request_headers=json.dumps(request_headers),
                request_payload=payload.model_dump_json(),
                status_code=response_status,
                status="success" if response_status < 400 else "failed",
                error_message=error_message,
            )
        )
    except Exception as log_error:  # noqa: BLE001
        logger.warning(
            "Failed to create integration log for ideation turn: %s",
            log_error,
            exc_info=True,
        )

    if _http_exc_to_reraise is not None:
        raise _http_exc_to_reraise

    assert response_payload is not None
    return response_payload
