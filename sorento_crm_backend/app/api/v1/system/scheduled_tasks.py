"""Scheduled tasks API: list, detail, update, run logs, run-now."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.scheduled_task import ScheduledTask
from app.schemas.scheduled_task import (
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
    ScheduledTaskRunResponse,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, PaginationResponse
from app.services.user_service import UserPermissionService
from app.services.scheduled_task_service import (
    DEFAULT_GRACE_PERCENT,
    compute_due_at,
    compute_grace,
    get_task,
    list_tasks,
    list_runs,
    resolve_grace_percent,
    update_task as update_task_config,
    run_task_now,
)

router = APIRouter()


def require_admin(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Admin / superadmin only, judged on the effective user (an admin viewing as someone
    else is that someone). The registry has no scheduled-tasks slug; the screen is
    `<RequireAccess superadmin>` and `superadminOnly` in the menu, so the writes take the
    same role gate. Turning a cron job off, rescheduling it or running it now is not for
    every signed-in caller."""
    roles = UserPermissionService(db).get_user_role_slugs(current_user["id"])
    if not roles & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}:
        raise HTTPException(status_code=403, detail="Admin access is required to manage scheduled tasks.")
    return current_user


def _task_to_response(task: ScheduledTask, grace_percent: Optional[int] = None) -> ScheduledTaskResponse:
    resp = ScheduledTaskResponse.model_validate(task)

    # Derived overdue state, from the same helper the health card and the watchdog
    # alert use - so the detail page can never disagree with either of them.
    now = datetime.utcnow()
    effective_percent = grace_percent if grace_percent is not None else DEFAULT_GRACE_PERCENT
    meta = getattr(task, "metadata_", None)
    if isinstance(meta, dict) and "grace_percent" in meta:
        try:
            effective_percent = int(meta["grace_percent"])
        except (TypeError, ValueError):
            pass

    due_at = compute_due_at(task)
    grace = compute_grace(task, grace_percent)

    resp.grace_percent = effective_percent
    resp.grace_seconds = int(grace.total_seconds())
    resp.due_at = due_at

    start_at = getattr(task, "start_at", None)
    not_started = isinstance(start_at, datetime) and start_at > now
    if due_at is not None and not not_started and bool(getattr(task, "enabled", False)):
        resp.late_by_seconds = max(0, int((now - due_at).total_seconds()))
        resp.is_overdue = now > due_at + grace

    return resp


@router.get("/scheduled-tasks", response_model=ListResponse[ScheduledTaskResponse])
async def list_scheduled_tasks(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all scheduled tasks."""
    tasks = list_tasks(db)
    grace_percent = resolve_grace_percent(db)  # one settings read for the whole list
    data = [_task_to_response(t, grace_percent) for t in tasks]
    return ListResponse(
        data=data,
        pagination=PaginationResponse(total=len(data), page=1, limit=len(data) or 50),
        empty=len(data) == 0,
    )


@router.get("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a scheduled task by id."""
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return _task_to_response(task, resolve_grace_percent(db))


@router.patch("/scheduled-tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
  task_id: str,
  body: ScheduledTaskUpdate,
  current_user: dict = Depends(require_admin),
  db: Session = Depends(get_db),
):
    """Update task config (enabled, frequency, start_at, etc.)."""
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    updated = update_task_config(
        db,
        task_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        interval_unit=body.interval_unit,
        interval_value=body.interval_value,
        timezone=body.timezone,
        start_at=body.start_at,
        metadata=body.metadata,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return _task_to_response(updated, resolve_grace_percent(db))


@router.get("/scheduled-tasks/{task_id}/runs", response_model=ListResponse[ScheduledTaskRunResponse])
async def list_task_runs(
    task_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List run logs for a scheduled task."""
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    runs, total = list_runs(db, task_id, page=page, limit=limit)
    data = [ScheduledTaskRunResponse.model_validate(r) for r in runs]
    return ListResponse(
        data=data,
        pagination=PaginationResponse(total=total, page=page, limit=limit),
        empty=len(data) == 0,
    )


@router.post("/scheduled-tasks/{task_id}/run-now")
async def trigger_run_now(
    task_id: str,
    current_user: dict = Depends(require_admin),
  db: Session = Depends(get_db),
):
    """Manually trigger a scheduled task run now."""
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    result = run_task_now(db, task_id, requested_by_user_id=current_user.get("id"))
    if result is None:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return result
