"""Chat history admin API: message listing, per-contact thread, CSV export.

Message content is customer PII, so every route is gated on its own permission slug
(`system.chat_history.view` / `.export`) rather than a general system-admin grant.

Export goes through My Downloads rather than returning a file inline: a wide date range
over this table is far too large to build in a request, and the download row gives the
user a durable, re-fetchable artifact.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.chat_history_admin import (
    ChatHistoryExportRequest,
    ChatMessageListResponse,
    ChatMessageRowResponse,
    ChatThreadResponse,
)
from app.schemas.download import DownloadResponse
from app.services.chat_history_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    get_thread,
    list_messages_page,
)
from app.services.download_service import DownloadService
from app.services.error_handler import handle_internal_error
from app.services.queue_service import enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/chat-history", response_model=ChatMessageListResponse)
def list_chat_messages(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    # Repeatable. One value is the ordinary "this contact's messages"; several is S2b's
    # "Failed turns only", where the turn aggregate has already named the contacts whose
    # turns failed and the list asks the server for their messages - so the page, the
    # total and the pager all describe the filtered set.
    contact_id: Optional[list[str]] = Query(None),
    direction: Optional[str] = Query(None, pattern="^(incoming|outgoing)$"),
    # The DataGrid sends its free-text box as `query`; `search` is accepted too.
    query: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    breached_only: bool = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    sort: Optional[str] = Query(None),
    group_by: Optional[str] = Query(
        None,
        pattern="^(date|contact|contact_date)$",
        description="Server-orders rows so group members are contiguous across pages.",
    ),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: dict = Depends(require_permission("system.chat_history.view")),
    db: Session = Depends(get_db),
):
    """Offset page of messages for the DataGrid. Defaults to the last 24h."""
    settings_target = _p99_target(db)
    rows, total = list_messages_page(
        db,
        date_from=date_from,
        date_to=date_to,
        contact_id=contact_id,
        direction=direction,
        search=query or search,
        breached_only=breached_only,
        target_seconds=settings_target,
        page=page,
        limit=limit,
        sort=sort,
        group_by=group_by,
        dir_=dir,
    )
    data = [ChatMessageRowResponse.model_validate(r, from_attributes=True) for r in rows]
    return ChatMessageListResponse(
        data=data, pagination={"total": total, "page": page}, empty=not data
    )


@router.get("/chat-history/respond-io-calls")
def get_respond_io_call_counts(
    minutes: int = Query(10, ge=1, le=15),
    current_user: dict = Depends(require_permission("system.chat_history.view")),
):
    """Respond.io HTTP calls per minute, oldest first (lane CHAT-LOCAL-FIRST, R6).

    The hand-test figure: opening a thread with history is at most 1 call, the
    polls that follow are 0. Read from Redis (every process) when reachable, else
    this process's own count."""
    from app.services import respond_call_counter

    _ = current_user
    rows = respond_call_counter.counts(minutes=minutes)
    return {"minutes": rows, "total": sum(int(r["calls"]) for r in rows)}


@router.get("/chat-history/thread", response_model=ChatThreadResponse)
def get_chat_thread(
    contact_id: str = Query(...),
    anchor_id: Optional[int] = Query(None, description="Centre the transcript on this message"),
    # Wide by default so in-drawer search covers the whole conversation, not a slice.
    before: int = Query(200, ge=0, le=500),
    after: int = Query(200, ge=0, le=500),
    current_user: dict = Depends(require_permission("system.chat_history.view")),
    db: Session = Depends(get_db),
):
    """One contact's transcript, oldest-first, centred on `anchor_id` when given."""
    rows = get_thread(db, contact_id=contact_id, anchor_id=anchor_id, before=before, after=after)
    data = [ChatMessageRowResponse.model_validate(r, from_attributes=True) for r in rows]
    return ChatThreadResponse(
        data=data,
        contact_display=data[0].contact_display if data else "Unknown contact",
        empty=not data,
    )


@router.post("/chat-history/export", response_model=DownloadResponse, status_code=status.HTTP_202_ACCEPTED)
def export_chat_history(
    body: ChatHistoryExportRequest,
    current_user: dict = Depends(require_permission("system.chat_history.export")),
    db: Session = Depends(get_db),
):
    """Queue a CSV export of the current filter set into My Downloads."""
    from app.tasks.export_tasks import generate_chat_history_csv

    stamp = (body.date_from or datetime.utcnow()).strftime("%Y%m%d")
    download = DownloadService(db).create(
        user_id=str(current_user["id"]),
        kind="chat_history_export",
        filename=f"chat-history-{stamp}.csv",
    )

    filters = body.model_dump(mode="json")
    try:
        enqueue_job(
            generate_chat_history_csv,
            str(download.id),
            filters,
            queue_name="imports",
            job_timeout=900,
        )
    except Exception as e:  # noqa: BLE001
        # Mark failed rather than leaving a row stuck at 'pending' in the drawer.
        DownloadService(db).mark_failed(str(download.id), f"Could not queue export: {e}")
        raise handle_internal_error("Could not queue the export. Please try again.")

    return DownloadResponse.model_validate(download)


def _p99_target(db: Session) -> float:
    """SLA target used to decide what 'breached' means on the grid."""
    try:
        from app.models.user import SystemSetting

        row = db.query(SystemSetting).first()
        return float(getattr(row, "chat_latency_p99_target_seconds", 10) or 10)
    except Exception:  # noqa: BLE001
        return 10.0
