"""Work calendar and public holidays API routes."""
from datetime import date, time
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.calendar import PublicHoliday
from app.schemas.calendar import (
    PublicHolidayCreate,
    PublicHolidayUpdate,
    PublicHolidayResponse,
    WorkCalendarConfigResponse,
    WorkCalendarConfigUpdate,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, PaginationResponse
from app.services.calendar_service import CalendarService
from app.services.error_handler import handle_internal_error, handle_not_found, handle_conflict

router = APIRouter()

# The writes carry the registry's work-calendar slugs (`permission_registry.py`,
# `_crud("system", "work_calendar", ...)`); every SLA deadline is computed from this
# calendar. The reads stay open to any signed-in caller.
ADD = "system.work_calendar.add"
EDIT = "system.work_calendar.edit"
DELETE = "system.work_calendar.delete"


def _validate_working_hours(start: time, end: time) -> None:
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Working day end time must be after start time.",
        )


@router.get("/work-calendar-config", response_model=WorkCalendarConfigResponse)
async def get_work_calendar_config(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the default work calendar configuration."""
    try:
        service = CalendarService(db)
        return service.get_or_create_work_calendar()
    except HTTPException:
        raise
    except Exception as exc:
        raise handle_internal_error(str(exc))


@router.put("/work-calendar-config", response_model=WorkCalendarConfigResponse)
async def update_work_calendar_config(
    payload: WorkCalendarConfigUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Update the default work calendar configuration."""
    try:
        service = CalendarService(db)
        config = service.get_or_create_work_calendar()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(config, field, value)
        work_day_start_time = getattr(config, "work_day_start_time", None)
        work_day_end_time = getattr(config, "work_day_end_time", None)
        if not isinstance(work_day_start_time, time) or not isinstance(work_day_end_time, time):
            raise handle_internal_error("Invalid work calendar time configuration.")
        _validate_working_hours(work_day_start_time, work_day_end_time)
        db.commit()
        db.refresh(config)
        return config
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise handle_internal_error(str(exc))


@router.get("/public-holidays", response_model=ListResponse[PublicHolidayResponse])
async def list_public_holidays(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List public holidays (optionally filter by year)."""
    try:
        query = db.query(PublicHoliday)
        if year:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            query = query.filter(PublicHoliday.date >= start, PublicHoliday.date <= end)
        total = query.count()
        items = (
            query.order_by(PublicHoliday.date.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return ListResponse(
            data=items,
            pagination=PaginationResponse(total=total, page=page, limit=limit),
            empty=total == 0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise handle_internal_error(str(exc))


@router.post("/public-holidays", response_model=PublicHolidayResponse, status_code=status.HTTP_201_CREATED)
async def create_public_holiday(
    payload: PublicHolidayCreate,
    current_user: dict = Depends(require_permission(ADD)),
    db: Session = Depends(get_db),
):
    """Create a public holiday."""
    try:
        existing = db.query(PublicHoliday).filter(PublicHoliday.date == payload.date).first()
        if existing:
            raise handle_conflict(f"Public holiday already exists for {payload.date}")
        holiday = PublicHoliday(
            date=payload.date,
            name=payload.name,
            description=payload.description,
        )
        db.add(holiday)
        db.commit()
        db.refresh(holiday)
        return holiday
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise handle_internal_error(str(exc))


@router.put("/public-holidays/{holiday_id}", response_model=PublicHolidayResponse)
async def update_public_holiday(
    holiday_id: str,
    payload: PublicHolidayUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Update a public holiday."""
    try:
        holiday = db.query(PublicHoliday).filter(PublicHoliday.id == holiday_id).first()
        if holiday is None:
            raise handle_not_found("Public holiday", holiday_id)

        updates = payload.model_dump(exclude_unset=True)
        if "date" in updates and updates["date"] != holiday.date:
            existing = db.query(PublicHoliday).filter(PublicHoliday.date == updates["date"]).first()
            if existing is not None and str(getattr(existing, "id")) != holiday_id:
                raise handle_conflict(f"Public holiday already exists for {updates['date']}")

        for field, value in updates.items():
            setattr(holiday, field, value)
        db.commit()
        db.refresh(holiday)
        return holiday
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise handle_internal_error(str(exc))


@router.delete("/public-holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_public_holiday(
    holiday_id: str,
    current_user: dict = Depends(require_permission(DELETE)),
    db: Session = Depends(get_db),
):
    """Delete a public holiday."""
    try:
        holiday = db.query(PublicHoliday).filter(PublicHoliday.id == holiday_id).first()
        if holiday is None:
            raise handle_not_found("Public holiday", holiday_id)
        db.delete(holiday)
        db.commit()
        return None
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise handle_internal_error(str(exc))
