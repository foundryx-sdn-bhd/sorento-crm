"""Promotion attachments API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Path
from sqlalchemy.orm import Session
from typing import Any, Optional

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    get_current_user,
    require_any_permission_with_api_key,
    require_permission_with_api_key,
)

from app.services.marketing_service import PromotionAttachmentService
from app.services.uuid_list_param import parse_uuid_list
from app.schemas.marketing import (
    PromotionAttachmentCreate,
    PromotionAttachmentUpdate,
    PromotionAttachmentResponse,
    PromotionServingListResponse,
)
from app.schemas.common import MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error

router = APIRouter()


# Who reads one promotion's attachments (LIST-API-PERMS): the Promotion Attachments page and
# the promotion detail / edit page's attachments tab (`PromotionAttachmentsTab.tsx`).
PROMOTION_ATTACHMENT_READERS = ["marketing.promotion_attachments.view", "marketing.promotions.view"]


def _promotion_attachment_to_response(pa: Any) -> dict:
    """Serialize promotion attachment without mutating stored attachment file_path."""
    data = PromotionAttachmentResponse.model_validate(pa).model_dump()
    return data


@router.get("/", response_model=PromotionServingListResponse[PromotionAttachmentResponse])
async def get_promotion_attachments(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `promotion_ids` / `attachment_ids`.",
    ),
    promotion_ids: Optional[list[str]] = Query(
        None,
        description="Canonical promotion UUIDs (csv/JSON/repeated).",
    ),
    attachment_ids: Optional[list[str]] = Query(
        None,
        description="Canonical attachment UUIDs (csv/JSON/repeated).",
    ),
    promotion_id: Optional[str] = Query(
        None,
        description="Legacy: promotion UUID.",
    ),
    query: Optional[str] = Query(
        None,
        description="Free-text search across promotion header, product code/name, promotion group name, and attachment metadata.",
    ),
    attachment_id: Optional[str] = Query(None),
    active: Optional[bool] = Query(
        None,
        description=(
            "Parent-promotion active filter. Omit: full catalog for interactive "
            "callers; API-key/MCP callers default to active-first. true: attachments "
            "of active promotions (is_active and today within start/end), falling back "
            "to inactive-promotion attachments when a narrowing filter yields zero "
            "matches. false: inactive-promotion attachments only."
        ),
    ),
    serving_policy: bool = Query(
        False,
        description=(
            "Answer as the chatbot: gate attachments on the per-type promotion "
            "policy of their PARENT promotion instead of the plain active gate. "
            "Documents of an expired promotion whose type still honours it come "
            "back with `expired_but_usable: true`; documents of an expired "
            "special do not come back at all."
        ),
    ),
    access_levels: Optional[list[str]] = Query(
        None,
        description=(
            "Optional access-level NAMES filter (translated to codes via "
            "contact_access_types.name; intersection with parent promotion.access_levels)."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id when reading on behalf of a contact. With it, an empty or "
            "absent access_levels reads the contact's own access levels, never every level."
        ),
    ),
    current_user: dict = Depends(require_permission_with_api_key("marketing.promotion_attachments.view")),
    db: Session = Depends(get_db)
):
    """Get promotion attachments with pagination and filtering. Optional access_levels (names) intersection filter.

    Requires at least one narrowing filter (`promotion_ids`, `attachment_ids`,
    legacy `promotion_id` / `attachment_id`, or free-text `query`). Without any,
    returns an empty page so callers cannot accidentally enumerate the whole
    catalog.
    """
    try:
        from app.services.contact_access_type_service import ContactAccessTypeService
        from app.services.entity_filter_helpers import normalize_list_query_param
        access_levels = normalize_list_query_param(access_levels)
        contact_codes = ContactAccessTypeService(db).translate_names_to_codes_for_caller(
            access_levels, contact_id=contact_id
        )

        parsed_promotion_ids = parse_uuid_list(promotion_ids, param_name="promotion_ids")
        parsed_attachment_ids = parse_uuid_list(attachment_ids, param_name="attachment_ids")
        has_narrowing_filter = bool(
            parsed_promotion_ids
            or parsed_attachment_ids
            or (promotion_id and promotion_id.strip())
            or (attachment_id and attachment_id.strip())
            or (query and query.strip())
        )
        # Gate "no filter = empty page" guard to API-key / MCP callers only.
        # FE DataGrid lands on this endpoint with no filters and expects the
        # full catalog; only chatbot/MCP needs whole-catalog enumeration guard.
        is_api_key_caller = (current_user or {}).get("auth_method") == "api_key"
        if is_api_key_caller and not has_narrowing_filter:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        # MCP/API-key callers default to active-first + inactive fallback (parent
        # promotion). Interactive (JWT) callers keep the full catalog unless they
        # pass `active` explicitly - preserves the FE DataGrid's "show all" listing.
        if active is None and is_api_key_caller:
            active = True

        service = PromotionAttachmentService(db)
        from app.services.entity_filter_helpers import normalize_entities_query_param
        result = service.list_promotion_attachments(
            page=page,
            limit=limit,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc",
            promotion_id=promotion_id,
            attachment_id=attachment_id,
            promotion_ids=parsed_promotion_ids,
            attachment_ids=parsed_attachment_ids,
            query=query,
            contact_access_codes=contact_codes,
            entities=normalize_entities_query_param(entities),
            active=active,
            serving_policy=serving_policy,
        )
        result["data"] = [_promotion_attachment_to_response(pa) for pa in result["data"]]
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{promotion_attachment_id}", response_model=PromotionAttachmentResponse)
async def get_promotion_attachment(
    promotion_attachment_id: str,
    current_user: dict = Depends(require_permission_with_api_key("marketing.promotion_attachments.view")),
    db: Session = Depends(get_db)
):
    """Get a single promotion attachment by ID."""
    try:
        validate_uuid_path(promotion_attachment_id, resource="Promotion Attachment")
        service = PromotionAttachmentService(db)
        promotion_attachment = service.get_promotion_attachment(promotion_attachment_id)
        return _promotion_attachment_to_response(promotion_attachment)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=PromotionAttachmentResponse, status_code=status.HTTP_201_CREATED)
async def create_promotion_attachment(
    promotion_attachment_data: PromotionAttachmentCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new promotion attachment relationship."""
    try:
        service = PromotionAttachmentService(db)
        created_by = str(current_user.get("id", "")) if current_user else None
        promotion_attachment = service.create_promotion_attachment(promotion_attachment_data, created_by=created_by)
        return _promotion_attachment_to_response(promotion_attachment)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{promotion_attachment_id}", response_model=PromotionAttachmentResponse)
async def update_promotion_attachment(
    promotion_attachment_id: str,
    promotion_attachment_data: PromotionAttachmentUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a promotion attachment relationship."""
    try:
        validate_uuid_path(promotion_attachment_id, resource="Promotion Attachment")
        service = PromotionAttachmentService(db)
        promotion_attachment = service.update_promotion_attachment(promotion_attachment_id, promotion_attachment_data)
        return _promotion_attachment_to_response(promotion_attachment)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{promotion_attachment_id}", status_code=status.HTTP_200_OK)
async def delete_promotion_attachment(
    promotion_attachment_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a promotion attachment relationship."""
    try:
        validate_uuid_path(promotion_attachment_id, resource="Promotion Attachment")
        service = PromotionAttachmentService(db)
        service.delete_promotion_attachment(promotion_attachment_id)
        return {"message": "Promotion attachment deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/promotion/{promotion_id}")
async def get_promotion_attachments_by_promotion(
    promotion_id: str = Path(
        ...,
        description="Promotion UUID.",
    ),
    access_levels: Optional[list[str]] = Query(
        None,
        description=(
            "Optional access-level NAMES filter (translated to codes via "
            "contact_access_types.name; intersection with parent promotion.access_levels)."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id when reading on behalf of a contact. With it, an empty or "
            "absent access_levels reads the contact's own access levels, never every level."
        ),
    ),
    current_user: dict = Depends(require_any_permission_with_api_key(PROMOTION_ATTACHMENT_READERS)),
    db: Session = Depends(get_db)
):
    """Get all attachments for a specific promotion. Optional access_levels (names) intersection filter."""
    try:
        from app.services.contact_access_type_service import ContactAccessTypeService
        from app.services.entity_filter_helpers import normalize_list_query_param
        access_levels = normalize_list_query_param(access_levels)
        contact_codes = ContactAccessTypeService(db).translate_names_to_codes_for_caller(
            access_levels, contact_id=contact_id
        )
        service = PromotionAttachmentService(db)
        promotion_attachments = service.get_promotion_attachments_by_promotion(
            promotion_id,
            contact_access_codes=contact_codes,
        )
        return [_promotion_attachment_to_response(pa) for pa in promotion_attachments]
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
