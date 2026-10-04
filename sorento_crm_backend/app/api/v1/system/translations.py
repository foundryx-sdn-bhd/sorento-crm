"""Translation memory admin list (AC-G4, purchasing consolidation batch, lane C).

Rows are written by the supplier-document upload preview (a manual edit) and by the AI
fill (`app.services.translation_service`), never by a create route here - this page only
reads, corrects or removes one."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.translation_memory import TranslationMemoryResponse, TranslationMemoryUpdate
from app.services import translation_service

router = APIRouter()

_VIEW = require_permission("system.translations.view")
_EDIT = require_permission("system.translations.edit")


@router.get("/translations", response_model=ListResponse[TranslationMemoryResponse])
def list_translations(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    # The language a row translates INTO: `en` is the supplier-document memory, `ms` / `zh`
    # the chatbot's reply wording (CHAT-LANGUAGE).
    target_lang: Optional[str] = Query(None, pattern="^(en|ms|zh)$"),
    db: Session = Depends(get_db),
    _user: dict = Depends(_VIEW),
):
    rows, total = translation_service.list_memory(
        db, page=page, limit=limit, query=query, sort=sort, dir=dir, target_lang=target_lang,
    )
    return {
        "data": rows,
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }


@router.put("/translations/{translation_id}", response_model=TranslationMemoryResponse)
def update_translation(
    translation_id: str,
    payload: TranslationMemoryUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_EDIT),
):
    row = translation_service.update_target_text(
        db, translation_id, payload.target_text, user_id=(current_user or {}).get("id")
    )
    return translation_service.to_response_dict(db, row)


@router.delete("/translations/{translation_id}", status_code=204)
def delete_translation(
    translation_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(_EDIT),
):
    translation_service.delete_memory(db, translation_id)
    return Response(status_code=204)
