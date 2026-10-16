"""Contact tag catalog and tagging routes (CONTACT-TAGS S1).

`router` is the catalog (`/contact-tags`); `contact_router` adds the per-contact and bulk
tagging routes under `/contacts` and is mounted before the contacts router.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import ListResponse, PaginationResponse
from app.schemas.contact_tag import (
    BulkTagRequest,
    BulkTagResult,
    ContactTagCreate,
    ContactTagResponse,
    ContactTagSettingsResponse,
    ContactTagsSet,
    ContactTagsSetResponse,
    ContactTagUpdate,
)
from app.services.contact_tag_service import ContactTagService
from app.services.error_handler import AppException, handle_internal_error

router = APIRouter()


def _gates(db: Session, current_user: dict):
    """(can, is_superadmin) for the settings gates (`contact_tag_settings.assert_gates`)."""
    from app.api.v1.system.companies import _is_superadmin
    from app.services.user_service import UserPermissionService

    perms = UserPermissionService(db)
    uid = current_user["id"]
    return (lambda slug: perms.check_user_has_permission(uid, slug), lambda: _is_superadmin(db, uid))


contact_router = APIRouter()

VIEW = "user_management.contacts.view"
EDIT = "user_management.contacts.edit"


@router.get("/", response_model=ListResponse[ContactTagResponse])
async def list_contact_tags(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    search: Optional[str] = Query(None),
    # The DataGrid sends its search box as `query`; `search` is the same filter.
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query("asc"),
    is_active: Optional[bool] = Query(None),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        rows, total = ContactTagService(db).list_tags(page, limit, search or query, is_active, sort, dir)
        return ListResponse(
            data=rows, pagination=PaginationResponse(total=total, page=page, limit=limit), empty=total == 0
        )
    except AppException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/settings-catalog")
async def get_settings_catalog(
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The pick lists behind the tag settings editor (declared before `/{tag_id}`)."""
    from app.services import contact_tag_settings

    try:
        return contact_tag_settings.settings_catalog(db)
    except AppException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tag_id}", response_model=ContactTagResponse)
async def get_contact_tag(
    tag_id: str,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return ContactTagService(db).get_tag_dict(tag_id)
    except AppException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=ContactTagResponse, status_code=status.HTTP_201_CREATED)
async def create_contact_tag(
    data: ContactTagCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        return ContactTagService(db).create_tag(
            data.model_dump(), str(current_user.get("id") or "") or None, _gates(db, current_user)
        )
    except AppException:
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.patch("/{tag_id}", response_model=ContactTagResponse)
async def update_contact_tag(
    tag_id: str,
    data: ContactTagUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        return ContactTagService(db).update_tag(
            tag_id,
            data.model_dump(exclude_unset=True),
            str(current_user.get("id") or "") or None,
            _gates(db, current_user),
        )
    except AppException:
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact_tag(
    tag_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        ContactTagService(db).delete_tag(tag_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppException:
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@contact_router.post("/bulk-tags", response_model=BulkTagResult, status_code=status.HTTP_200_OK)
async def bulk_tag_contacts(
    body: BulkTagRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Preview (`dry_run`) or apply add/remove of tags across contacts; one row per contact."""
    try:
        return ContactTagService(db).bulk_tag(
            body.contact_ids,
            body.add_tag_ids,
            body.remove_tag_ids,
            body.dry_run,
            str(current_user.get("id") or "") or None,
        )
    except AppException:
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@contact_router.put("/{contact_id}/tags", response_model=ContactTagsSetResponse)
async def set_contact_tags(
    contact_id: str,
    body: ContactTagsSet,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        tags = ContactTagService(db).set_contact_tags(
            contact_id, body.tag_ids, str(current_user.get("id") or "") or None
        )
        return {"tags": tags}
    except AppException:
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@contact_router.get("/{contact_id}/tag-settings", response_model=ContactTagSettingsResponse)
async def get_contact_tag_settings(
    contact_id: str,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Expected (merged across the contact's tags) against current, per setting."""
    from app.services import contact_tag_settings
    from app.services.contact_service import ContactService

    try:
        contact = ContactService(db).get_contact(contact_id)
        return contact_tag_settings.tag_settings_for_contact(db, contact)
    except AppException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
