"""Contact access types catalog API (for promotion/attachment access levels and contact classification)."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.schemas.user import (
    ContactAccessTypeCreate,
    ContactAccessTypeUpdate,
    ContactAccessTypeResponse,
)
from app.services.contact_access_type_service import ContactAccessTypeService
from app.services.error_handler import handle_internal_error
from app.services.error_handler import AppException

router = APIRouter()

# The catalog's write slug, shared with the market-segment catalog
# (`market_segments.py`). Until 1 Oct 2026 the three writes below took only
# `get_current_user`, so any signed-in user could add or delete an access type.
MANAGE = "user_management.reference_data.manage"


def _service(db: Session) -> ContactAccessTypeService:
    return ContactAccessTypeService(db)


# Open to any signed-in user (owner ruling 1 Oct 2026, never-stuck L10): the
# cross-module consumers (marketing promotions, forms, resource-management files,
# master-data brands and products, contact dialogs) are pickers, and the rows are a
# catalog (code, name, description, sort order), no personal data. It was gated on
# `reference_data.view` until then, which left those pickers silently empty. The admin
# reads below keep `access_agents.view`; every write needs `reference_data.manage`.
@router.get("/", response_model=list)
async def list_contact_access_types(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List active contact access types for use in access_levels (promotions, attachments). Returns [{code, name, description, sort_order}]."""
    try:
        return _service(db).list_types_for_api()
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


# --- Admin: contact access type catalog CRUD ---

@router.get("/all", response_model=list[ContactAccessTypeResponse])
async def list_all_contact_access_types(
    current_user: dict = Depends(require_permission("user_management.access_agents.view")),
    db: Session = Depends(get_db),
):
    """List all contact access types (including inactive) for admin UI."""
    try:
        return _service(db).list_all_types()
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.get("/{code}", response_model=ContactAccessTypeResponse)
async def get_contact_access_type(
    code: str,
    current_user: dict = Depends(require_permission("user_management.access_agents.view")),
    db: Session = Depends(get_db),
):
    """Get a single contact access type by code."""
    try:
        row = _service(db).get_type_by_code(code)
        if not row:
            from app.services.error_handler import handle_not_found
            raise handle_not_found("Contact access type", code)
        return row
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.post("/", response_model=ContactAccessTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_contact_access_type(
    data: ContactAccessTypeCreate,
    current_user: dict = Depends(require_permission(MANAGE)),
    db: Session = Depends(get_db),
):
    """Create a new contact access type."""
    try:
        return _service(db).create_type(data.model_dump())
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.put("/{code}", response_model=ContactAccessTypeResponse)
async def update_contact_access_type(
    code: str,
    data: ContactAccessTypeUpdate,
    current_user: dict = Depends(require_permission(MANAGE)),
    db: Session = Depends(get_db),
):
    """Update a contact access type."""
    try:
        return _service(db).update_type(code, data.model_dump(exclude_unset=True))
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.delete("/{code}", status_code=status.HTTP_200_OK)
async def delete_contact_access_type(
    code: str,
    current_user: dict = Depends(require_permission(MANAGE)),
    db: Session = Depends(get_db),
):
    """Delete a contact access type."""
    try:
        _service(db).delete_type(code)
        return {"message": "Deleted"}
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()
