"""Market-segment catalog API (retail / project) for CS routing.

Admin CRUD over the ``market_segments`` catalog. Assignment of segments to
contacts / team members lives on the contacts + teams routers.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.schemas.market_segment import (
    MarketSegmentCreate,
    MarketSegmentResponse,
    MarketSegmentUpdate,
)
from app.services.error_handler import AppException, handle_internal_error
from app.services.market_segment_service import MarketSegmentService

router = APIRouter()


def _service(db: Session) -> MarketSegmentService:
    return MarketSegmentService(db)


@router.get("/", response_model=list[MarketSegmentResponse])
async def list_market_segments(
    active_only: bool = False,
    # Any signed-in user (owner ruling 1 Oct 2026, never-stuck L10): contact and
    # team-member segment pickers read it, and the rows are catalog config, no
    # personal data. Every write below keeps `reference_data.manage`.
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List market segments (all by default; ``active_only=true`` for pickers)."""
    try:
        return _service(db).list_segments(active_only=active_only)
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.post("/", response_model=MarketSegmentResponse, status_code=status.HTTP_201_CREATED)
async def create_market_segment(
    data: MarketSegmentCreate,
    current_user: dict = Depends(require_permission("user_management.reference_data.manage")),
    db: Session = Depends(get_db),
):
    """Create a market segment. Duplicate code → 409."""
    try:
        return _service(db).create_segment(
            data.code,
            data.name,
            description=data.description,
            is_active=data.is_active,
            sort_order=data.sort_order,
            is_requestor_selectable=data.is_requestor_selectable,
            portal_form_types=data.portal_form_types,
        )
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.put("/{code}", response_model=MarketSegmentResponse)
async def update_market_segment(
    code: str,
    data: MarketSegmentUpdate,
    current_user: dict = Depends(require_permission("user_management.reference_data.manage")),
    db: Session = Depends(get_db),
):
    """Rename / toggle active / reorder a market segment."""
    try:
        return _service(db).update_segment(
            code,
            name=data.name,
            description=data.description,
            is_active=data.is_active,
            sort_order=data.sort_order,
            is_requestor_selectable=data.is_requestor_selectable,
            portal_form_types=data.portal_form_types,
        )
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()


@router.delete("/{code}", status_code=status.HTTP_200_OK)
async def delete_market_segment(
    code: str,
    current_user: dict = Depends(require_permission("user_management.reference_data.manage")),
    db: Session = Depends(get_db),
):
    """Hard-delete a market segment. 409 when it is still assigned to a contact/member."""
    try:
        _service(db).delete_segment(code)
        return {"message": "Deleted"}
    except AppException:
        raise
    except Exception:
        raise handle_internal_error()
