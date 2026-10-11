"""Forms API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Body
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import get_current_user, require_permission_with_api_key
from app.services.forms_service import FormService
from app.services.uuid_list_param import parse_uuid_list
from app.schemas.forms import (
    FormCreate,
    FormUpdate,
    FormResponse,
    FormSubmissionCreate,
    FormSubmissionUpdate,
    FormSubmissionResponse,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error


class BulkDeleteFormsRequest(BaseModel):
    ids: list[str]

router = APIRouter()


@router.get("/", response_model=ListResponse[FormResponse])
async def get_forms(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `form_ids`.",
    ),
    form_ids: Optional[list[str]] = Query(
        None,
        description="Canonical form UUIDs (csv/JSON/repeated).",
    ),
    query: Optional[str] = Query(None, description="Legacy free-text. Prefer `form_ids` or `crm_find_entity` first."),
    language: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    form_type: Optional[str] = Query(None, description="Filter by form type (e.g. marketing)"),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission_with_api_key("forms.forms.view")),
    db: Session = Depends(get_db)
):
    """Get forms with pagination and filtering.

    Requires at least one narrowing filter (`form_ids`, free-text `query`, or
    `entities`). Without any, returns an empty page so callers cannot
    accidentally enumerate the whole catalog.
    """
    try:
        from app.services.entity_filter_helpers import (
            normalize_entities_query_param,
            resolve_or_empty,
        )
        service = FormService(db)
        sort_field = (sort and sort.strip()) or "updated_at"
        sort_dir = (dir and dir.strip()) or "desc"

        parsed_form_ids = parse_uuid_list(form_ids, param_name="form_ids")
        norm = normalize_entities_query_param(entities)
        has_narrowing_filter = bool(
            parsed_form_ids
            or (query and query.strip())
            or norm
        )
        # API-key / MCP callers without a narrowing filter still get a real
        # page - caller-supplied `limit` is respected verbatim (already capped
        # to 100 by the Query() validator above). Default sort = updated_at
        # desc so newest forms surface first when the caller didn't pick one.
        is_api_key_caller = (current_user or {}).get("auth_method") == "api_key"
        if is_api_key_caller and not has_narrowing_filter:
            sort_field = sort_field or "updated_at"
            sort_dir = sort_dir or "desc"

        entity_form_codes: Optional[list[str]] = None
        entity_echo = None
        if norm:
            buckets = resolve_or_empty(db, norm)
            if buckets is not None:
                entity_echo = buckets.as_echo()
                if buckets.form_codes:
                    entity_form_codes = list(buckets.form_codes)
                else:
                    return {
                        "data": [],
                        "pagination": {"total": 0, "page": page, "limit": limit},
                        "empty": True,
                        "resolved_entities": entity_echo,
                    }

        result = service.list_forms(
            page=page,
            limit=limit,
            query=query,
            language=language,
            status=status,
            form_type=form_type,
            contact_access_codes=None,
            sort_field=sort_field,
            sort_dir=sort_dir,
            entity_form_codes=entity_form_codes,
            form_ids=parsed_form_ids,
        )
        if entity_echo is not None and isinstance(result, dict):
            result["resolved_entities"] = entity_echo
        return result
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_forms: {str(e)}")
        logger.error(traceback.format_exc())
        raise handle_internal_error(str(e))


@router.get("/{form_id}", response_model=FormResponse)
async def get_form(
    form_id: str,
    current_user: dict = Depends(require_permission_with_api_key("forms.forms.view")),
    db: Session = Depends(get_db)
):
    """Get a single form by ID."""
    try:
        validate_uuid_path(form_id, resource="Form")
        service = FormService(db)
        form = service.get_form(form_id, contact_access_codes=None)
        return form
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=FormResponse, status_code=status.HTTP_201_CREATED)
async def create_form(
    form_data: FormCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new form."""
    try:
        service = FormService(db)
        form = service.create_form(form_data, current_user["id"])
        return form
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{form_id}", response_model=FormResponse)
async def update_form(
    form_id: str,
    form_data: FormUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a form."""
    try:
        validate_uuid_path(form_id, resource="Form")
        service = FormService(db)
        form = service.update_form(form_id, form_data)
        return form
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_forms(
    body: BulkDeleteFormsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Bulk delete forms by ID."""
    try:
        service = FormService(db)
        return service.bulk_delete_forms(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{form_id}", status_code=status.HTTP_200_OK)
async def delete_form(
    form_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a form (cascades to sections, fields, versions, submissions)."""
    try:
        validate_uuid_path(form_id, resource="Form")
        service = FormService(db)
        return service.delete_form(form_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/submissions", response_model=FormSubmissionResponse, status_code=status.HTTP_201_CREATED)
async def create_form_submission(
    submission_data: FormSubmissionCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a form submission."""
    try:
        service = FormService(db)
        submission = service.create_submission(submission_data, current_user["id"])
        return submission
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/submissions/{submission_id}", response_model=FormSubmissionResponse)
async def update_form_submission(
    submission_id: str,
    submission_data: FormSubmissionUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a form submission."""
    try:
        service = FormService(db)
        submission = service.update_submission(submission_id, submission_data)
        return submission
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
