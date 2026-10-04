"""Attachments API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, File, Form, Response, Request, BackgroundTasks, Body
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
import hashlib
import logging
import os
import json
import uuid
import zipfile
import io
import mimetypes
from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import get_current_user, get_current_user_or_api_key, require_permission
from app.services.resources_service import AttachmentService, AttachmentTypeService, AttachmentDirectoryService
from app.services.attachment_company_service import AttachmentCompanyService
from app.services.storage_router import (
    PROVIDER_R2,
    normalize_provider,
    sanitize_storage_filename,
)
from app.services.uuid_list_param import parse_uuid_list
from app.services.entity_attachment_service import EntityAttachmentService
from app.services.integration_service import IntegrationLogService
from app.services.attachment_webhook_helper import build_signed_attachment_url_for_webhook
from app.services.excel_macro_stripper import (
    MacroWorkbookError,
    extract_macro_template_xlsx,
    is_xlsm_filename,
)
from app.services.image_normalizer import ensure_rgb_image
from app.services.image_thumbnailer import store_thumbnail
from app.services.n8n_webhook_settings import get_n8n_attachment_webhook_url
from app.utils.http import content_disposition
from app.schemas.resources import (
    AttachmentCreate,
    AttachmentUpdate,
    AttachmentResponse,
    AttachmentBulkDeleteRequest,
    AttachmentReorderRequest,
    AttachmentsBulkMoveRequest,
    AttachmentsBulkMoveResponse,
    BulkAccessLevelsPreviewRequest,
    BulkAccessLevelsPreviewResponse,
    BulkAccessLevelsApplyRequest,
    BulkAccessLevelsApplyResponse,
    BulkAttachmentTypeRequest,
    BulkAttachmentTypeResponse,
    BulkCompanyRequest,
    BulkCompanyResponse,
)
from app.schemas.resources import (
    DriveFolderItem,
    DriveFileItem,
    DriveListResponse,
)
from app.schemas.common import ListResponse
from app.services.error_handler import handle_internal_error

logger = logging.getLogger(__name__)

router = APIRouter()


import re as _re

_COPY_NUMBERED_RE = _re.compile(r"^(?P<stem>.*) - copy \((?P<n>\d+)\)$")
_COPY_PLAIN_SUFFIX = " - copy"


def _suffix_copy_name(filename: str) -> str:
    """Google-Drive style "- copy" suffix bump for the upload collision flow.

    Splits at the rightmost dot so multi-dot basenames like `archive.tar.gz`
    are treated as base=`archive.tar`, ext=`.gz`. Files without an extension
    (e.g. `README`) get the suffix appended directly. The marker is matched
    on the base only:

      * `"x"` → `"x - copy"`
      * `"x - copy"` → `"x - copy (2)"`
      * `"x - copy (N)"` → `"x - copy (N+1)"`
    """
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        ext = f".{ext}"
    else:
        base, ext = filename, ""
    m = _COPY_NUMBERED_RE.match(base)
    if m:
        n = int(m.group("n")) + 1
        new_base = f"{m.group('stem')} - copy ({n})"
    elif base.endswith(_COPY_PLAIN_SUFFIX):
        new_base = f"{base} (2)"
    else:
        new_base = f"{base}{_COPY_PLAIN_SUFFIX}"
    return f"{new_base}{ext}"


class LinkPackingListRequest(BaseModel):
    packing_list_id: str


def _create_and_send_webhook(
    db: Session,
    attachment,
    attachment_type,
    access_levels_payload: Optional[list],
    current_user_id: str,
    event_type: str = "attachment_uploaded",
):
    """Delegate to shared helper (used by single upload and bulk-import task)."""
    from app.services.attachment_webhook_helper import create_and_send_webhook
    create_and_send_webhook(
        db,
        attachment,
        attachment_type,
        access_levels_payload,
        current_user_id,
        event_type=event_type,
    )


def _find_filename_collision(db: Session, directory_id: Optional[str], display_name: str):
    """Return the live Attachment row colliding on (directory_id, lower(stored_filename)) or None.

    Scoped to the user-facing name (stored_filename) - that's what "a file with this name
    already exists in this folder" means to a user. Storage-key uniqueness is guaranteed
    separately by the uuid-segregated key, so this is a pure UX check.
    """
    from sqlalchemy import func as _sa_func
    from app.models.resources import Attachment

    # "No folder" (directory_id NULL) is its own scope - two same-named files
    # uploaded from the All-attachments view should still collide.
    scope = (
        Attachment.directory_id.is_(None)
        if not directory_id
        else Attachment.directory_id == directory_id
    )
    return (
        db.query(Attachment)
        .filter(
            scope,
            _sa_func.lower(Attachment.stored_filename) == (display_name or "").lower(),
            Attachment.is_deleted.is_(False),
        )
        .first()
    )


def _next_copy_name(db: Session, directory_id: Optional[str], display_name: str) -> str:
    """Loop _suffix_copy_name until the candidate display name is free in the directory."""
    candidate = _suffix_copy_name(display_name)
    while _find_filename_collision(db, directory_id, candidate) is not None:
        candidate = _suffix_copy_name(candidate)
    return candidate


def _enrich_uploaded_by_user(db, attachment) -> Optional[dict]:
    """Resolve uploaded_by UUID to user name/email for display. Returns UploadedByUser dict or None."""
    from app.schemas.resources import UploadedByUser
    from app.models.user import User

    uploaded_by = getattr(attachment, "uploaded_by", None)
    if not uploaded_by:
        return None
    try:
        user_id = str(uploaded_by)
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            display_name = (user.name or "").strip() or (user.email or None)
            return UploadedByUser(
                id=str(user.id),
                name=display_name,
                email=user.email or None,
            ).model_dump()
    except Exception as e:
        logger.warning("Could not resolve uploaded_by user for attachment %s: %s", getattr(attachment, "id"), e)
    return None


def _build_uploaded_by_user_map(db, attachments) -> dict:
    """Batch-resolve uploaded_by UUIDs → UploadedByUser dict for a list of rows.

    One ``IN`` query instead of one SELECT per row (avoids N+1 on list responses).
    Returns ``{user_id: UploadedByUser dict}``.
    """
    from app.schemas.resources import UploadedByUser
    from app.models.user import User

    ids = {
        str(uid)
        for att in attachments
        if (uid := getattr(att, "uploaded_by", None))
    }
    if not ids:
        return {}
    out: dict = {}
    try:
        users = db.query(User).filter(User.id.in_(ids)).all()
        for user in users:
            display_name = (user.name or "").strip() or (user.email or None)
            out[str(user.id)] = UploadedByUser(
                id=str(user.id),
                name=display_name,
                email=user.email or None,
            ).model_dump()
    except Exception as e:
        logger.warning("Could not batch-resolve uploaded_by users: %s", e)
    return out


def _stamp_company(data: dict, attachment, company_names: dict) -> None:
    """Put the owning company on one attachment payload (shape only, no DB).

    The company NAME is what a reader has to go on: two companies each hold a
    current "Container Status 2026.xlsx", and the customer-facing surfaces
    deliberately withhold the UUID. Names come pre-resolved from
    ``AttachmentService.company_name_map`` so this stays one query per page.

    A company-less (shared) attachment gets explicit ``None`` on both keys rather
    than a missing key, so every row in a list has the same shape.
    """
    company_id = getattr(attachment, "company_id", None)
    data["company_id"] = str(company_id) if company_id else None
    data["company_name"] = company_names.get(str(company_id)) if company_id else None


@router.get("/", response_model=ListResponse[AttachmentResponse])
async def get_attachments(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    query: Optional[str] = Query(None),
    entities: Optional[List[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `attachment_ids`.",
    ),
    attachment_ids: Optional[List[str]] = Query(
        None,
        description="Canonical attachment UUIDs (csv/JSON/repeated). Resolve free-text doc refs FIRST via `crm_find_entity`.",
    ),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    directory_id: Optional[str] = Query(None),
    is_deleted: Optional[bool] = Query(None),
    attachment_type_id: Optional[str] = Query(None),
    attachment_type_ids: Optional[List[str]] = Query(
        None,
        description="Canonical AttachmentType UUIDs (csv/JSON/repeated). Unions with `attachment_type_id`.",
    ),
    attachment_type_code: Optional[str] = Query(
        None,
        description="Filter by AttachmentType.code (canonical) or type_name (fallback, case-insensitive). Unknown code → 0 rows.",
    ),
    attachment_type_codes: Optional[List[str]] = Query(
        None,
        description="Several AttachmentType codes/names (csv/JSON/repeated). Unions with `attachment_type_code`; no code resolves → 0 rows.",
    ),
    mime_type: Optional[str] = Query(
        None,
        description="Filter by the file's own mime type (e.g. `application/pdf`). Case-insensitive; any `;charset=` suffix is ignored. Not the same as attachment_type_id, which is a document class.",
    ),
    mime_types: Optional[List[str]] = Query(
        None,
        description="Several mime types (csv/JSON/repeated). Unions with `mime_type`.",
    ),
    uploaded_by: Optional[str] = Query(None),
    uploaded_at_from: Optional[datetime] = Query(None),
    uploaded_at_to: Optional[datetime] = Query(None),
    access_levels: Optional[List[str]] = Query(None, description="Filter to attachments whose access_levels match these codes (see access_levels_match)."),
    access_levels_match: Optional[str] = Query("any", description="How to combine access_levels: 'any' (overlap, default), 'all' (contains every code), 'exact' (set equality)."),
    link_status: Optional[str] = Query(None, description="Filter by linkage: 'linked' (referenced by any product/promotion/form/packing-list/field link) or 'unlinked' (orphans)."),
    storage_status: Optional[str] = Query(None, description="Filter by storage accessibility audit: 'accessible', 'missing' (broken/lost object), or 'unchecked'."),
    direct_access_only: bool = Query(False, description="When true, restrict to attachment types flagged is_direct_access (dealer-downloadable documents)."),
    contact_id: Optional[str] = Query(
        None,
        description="Respond contact asking (internal id or respond_io_id). With direct_access_only, WIDENS the visible types by this contact's per-contact grants. Never narrows.",
    ),
    space_id: Optional[str] = Query(
        None,
        description="Respond.io workspace space_id. Disambiguates contact_id when the same respond_io_id exists in two workspaces.",
    ),
    resolve_signed_urls: bool = Query(False, description="When false, return stored file_path without CloudFront signing."),
    company: Optional[str] = Query(
        None,
        description="'shared' for company_id IS NULL only; a company UUID for that company only; omitted keeps the default (shared + the caller's own companies).",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get attachments with pagination and filtering (optional directory_id, query by filename, is_deleted for trash)."""
    try:
        service = AttachmentService(db)
        from app.services.entity_filter_helpers import (
            normalize_entities_query_param,
            normalize_list_query_param,
        )
        from app.services.contact_attachment_access import visible_type_ids
        from app.services.eta_policy import resolve_request_contact, rules_for_contact

        # A contact's list also stops at its packing list regions (unresolved = West).
        contact_regions = (
            rules_for_contact(db, resolve_request_contact(db, contact_id, space_id)).regions
            if contact_id
            else None
        )

        result = service.list_attachments(
            page=page,
            limit=limit,
            query=query,
            sort=sort,
            dir=dir or "desc",
            entity_type=entity_type,
            entity_id=entity_id,
            directory_id=directory_id,
            is_deleted=is_deleted,
            attachment_type_id=attachment_type_id,
            attachment_type_ids=parse_uuid_list(attachment_type_ids, param_name="attachment_type_ids"),
            attachment_type_code=attachment_type_code,
            attachment_type_codes=normalize_list_query_param(attachment_type_codes),
            mime_type=mime_type,
            mime_types=normalize_list_query_param(mime_types),
            uploaded_by=uploaded_by,
            uploaded_at_from=uploaded_at_from,
            uploaded_at_to=uploaded_at_to,
            access_levels=access_levels,
            access_levels_match=access_levels_match,
            link_status=link_status,
            storage_status=storage_status,
            entities=normalize_entities_query_param(entities),
            attachment_ids=parse_uuid_list(attachment_ids, param_name="attachment_ids"),
            direct_access_only=direct_access_only,
            visible_attachment_type_ids=visible_type_ids(db, contact_id, space_id),
            company=company,
            contact_regions=contact_regions,
        )
        # Enrich each attachment with uploaded_by_user for display.
        # Batch-resolve users in ONE query to avoid N+1 (was a per-row SELECT).
        user_map = _build_uploaded_by_user_map(db, result["data"])
        company_names = service.company_name_map(result["data"])
        enriched = []
        for att in result["data"]:
            data = AttachmentResponse.model_validate(att).model_dump()
            _stamp_company(data, att, company_names)
            uid = getattr(att, "uploaded_by", None)
            user_info = user_map.get(str(uid)) if uid else None
            if user_info:
                data["uploaded_by_user"] = user_info

            if resolve_signed_urls:
                # Optional on-demand signing for list responses.
                data["file_path"] = _resolve_attachment_file_path(
                    data.get("file_path"),
                    provider=getattr(att, "storage_provider", None),
                )
            enriched.append(data)

        result["data"] = enriched
        return result
    except HTTPException:
        # AppException (and any other HTTPException) has its own status - a
        # bare re-raise, not the generic 500 below. Without this, a 422 from
        # the `company` filter validator (S6, reviewer fix round) came back
        # as a 500 "INTERNAL_ERROR" - the same bug class every other route in
        # this file already guards against.
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/collision-check")
async def collision_check(
    filename: str = Query(..., description="Candidate display name (stored_filename) to test."),
    directory_id: Optional[str] = Query(None, description="Folder to check within; no folder → never collides."),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Pre-upload check: does a live attachment with this name already exist in this folder?

    Lets the upload UI prompt Replace / Create copy BEFORE handing the file to the
    background upload session (which otherwise can't surface a 409 interactively).
    Same scope as the upload-time guard (`directory_id`, lower(`stored_filename`)).
    """
    existing = _find_filename_collision(db, directory_id, filename)
    if existing is None:
        return {"collides": False}
    return {
        "collides": True,
        "existing_attachment_id": str(existing.id),
        "existing_file_name": existing.stored_filename or existing.original_filename,
    }


@router.get("/drive", response_model=DriveListResponse)
async def get_drive_contents(
    directory_id: Optional[str] = Query(
        None, description="Folder to list (omit/null = drive root)."
    ),
    recursive: bool = Query(
        False,
        description="When true (or any non-empty query), list the current folder + ALL descendant subfolders. Otherwise immediate children only.",
    ),
    query: Optional[str] = Query(
        None, description="Search term - matches both file names and folder names; forces recursive scope."
    ),
    sort: Optional[str] = Query(
        None, description="name (default, interleaves folders+files) | type | size | modified | uploaded_by | attachment_type (non-name sorts push folders to the end)."
    ),
    dir: Optional[str] = Query("asc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    is_deleted: Optional[bool] = Query(
        None, description="True = Trash (deleted folders + archived files)."
    ),
    attachment_type_id: Optional[str] = Query(None),
    attachment_type_code: Optional[str] = Query(None),
    uploaded_by: Optional[str] = Query(None),
    uploaded_at_from: Optional[datetime] = Query(None),
    uploaded_at_to: Optional[datetime] = Query(None),
    access_levels: Optional[List[str]] = Query(None),
    access_levels_match: Optional[str] = Query("any"),
    link_status: Optional[str] = Query(None),
    storage_status: Optional[str] = Query(None),
    direct_access_only: bool = Query(False),
    company: Optional[str] = Query(
        None,
        description="'shared' for company_id IS NULL only (folders and files); a company UUID for that company only; omitted keeps the default.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Unified Drive listing - folders + files as ONE server-sorted, server-paginated
    stream of discriminated rows (`{kind: 'folder'|'file', ...}`).

    Browse (empty query, no filter) = immediate children only and folders are
    shown. Search (non-empty query) or any file-attribute filter switches to a
    recursive subtree scan and hides folders. File rows carry a resolved
    `directory_path` (Location). See PLAN-unified-drive-files.md / its UAC.
    """
    try:
        service = AttachmentService(db)
        result = service.list_drive_contents(
            directory_id=directory_id,
            recursive=recursive,
            query=query,
            sort=sort,
            dir=dir or "asc",
            page=page,
            limit=limit,
            is_deleted=is_deleted,
            attachment_type_id=attachment_type_id,
            attachment_type_code=attachment_type_code,
            uploaded_by=uploaded_by,
            uploaded_at_from=uploaded_at_from,
            uploaded_at_to=uploaded_at_to,
            access_levels=access_levels,
            access_levels_match=access_levels_match,
            link_status=link_status,
            company=company,
            storage_status=storage_status,
            direct_access_only=direct_access_only,
        )

        # Hydrate uploaded_by_user for the file rows on this page in one IN query.
        file_attachments = [
            it["attachment"] for it in result["items"] if it["kind"] == "file"
        ]
        user_map = _build_uploaded_by_user_map(db, file_attachments)
        # ONE company-name lookup covers BOTH kinds (R14 / AC-E3): a Company
        # column shows on folder rows and file rows alike, so the id set is
        # the union of both, never file-only.
        folder_company_ids = {
            it.get("company_id") for it in result["items"]
            if it["kind"] == "folder" and it.get("company_id")
        }
        file_company_ids = {
            str(getattr(att, "company_id", None))
            for att in file_attachments
            if getattr(att, "company_id", None)
        }
        company_names = service.company_name_map_for_ids(folder_company_ids | file_company_ids)

        data: list[dict] = []
        for it in result["items"]:
            if it["kind"] == "folder":
                folder_company_id = it.get("company_id")
                data.append(
                    DriveFolderItem(
                        id=it["id"],
                        name=it["name"],
                        parent_id=it.get("parent_id"),
                        sort_order=it.get("sort_order"),
                        created_at=it.get("created_at"),
                        directory_path=it.get("directory_path"),
                        company_id=folder_company_id,
                        company_name=(
                            company_names.get(folder_company_id) if folder_company_id else None
                        ),
                    ).model_dump()
                )
            else:
                att = it["attachment"]
                row = DriveFileItem.model_validate(att).model_dump()
                _stamp_company(row, att, company_names)
                row["directory_path"] = it.get("directory_path")
                # Grid thumbnail URL. R2's CDN domain is public, so the stored
                # thumbnail_path is already a stable, cacheable URL - serve it
                # as-is. A per-load presigned signature would change every render
                # and bust the browser/CDN cache, forcing a re-download of every
                # thumbnail on every grid load (the "not instant" symptom).
                # S3/CloudFront is private, so those must still be signed.
                thumb_path = getattr(att, "thumbnail_path", None)
                if thumb_path:
                    prov = getattr(att, "storage_provider", None)
                    if normalize_provider(prov) == PROVIDER_R2:
                        row["thumbnail_url"] = str(thumb_path)
                    else:
                        row["thumbnail_url"] = _resolve_attachment_file_path(
                            str(thumb_path), provider=prov
                        )
                uid = getattr(att, "uploaded_by", None)
                user_info = user_map.get(str(uid)) if uid else None
                if user_info:
                    row["uploaded_by_user"] = user_info
                data.append(row)

        return {
            "data": data,
            "pagination": result["pagination"],
            "empty": result["empty"],
            "recursive": result["recursive"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


# Match attachment type by display name "Stock List" (UI) or legacy "Stock_List"
STOCK_LIST_TYPE_NAMES = ("Stock List", "Stock_List")


@router.get("/current-stock-list", response_model=AttachmentResponse)
async def get_current_stock_list(
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get the current (non-archived) Stock_List attachment, if any. For quick access from Stock page and n8n."""
    from app.models.resources import Attachment, AttachmentType

    attachment_type = db.query(AttachmentType).filter(AttachmentType.type_name.in_(STOCK_LIST_TYPE_NAMES)).first()
    if not attachment_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment type 'Stock List' not found")
    attachment = (
        db.query(Attachment)
        .filter(
            Attachment.attachment_type_id == str(attachment_type.id),
            Attachment.is_deleted == False,
        )
        .order_by(Attachment.uploaded_at.desc())
        .first()
    )
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Stock List file has been uploaded yet")
    service = AttachmentService(db)
    data = _attachment_response_with_linked_entities(service, attachment, current_user)
    user_info = _enrich_uploaded_by_user(db, attachment)
    if user_info:
        data["uploaded_by_user"] = user_info
    return data


def _resolve_attachment_file_path(
    file_path: Optional[str],
    provider: Optional[str] = None,
) -> Optional[str]:
    """Return a fresh signed URL for the stored file_path.

    Provider is taken from the attachment row when available so reads dispatch
    to S3+CloudFront or R2+Cloudflare CDN per record. Without a row, the
    storage_router falls back to URL-host sniffing then STORAGE_DEFAULT_PROVIDER.
    """
    if not file_path:
        return file_path
    from app.services.storage_router import resolve_signed_url

    return resolve_signed_url(file_path, provider=provider)


def _attachment_response_with_linked_entities(
    service: AttachmentService, attachment, current_user: Optional[dict] = None
) -> dict:
    """Build attachment response dict including linked entities from product_attachments, promotion_attachments, forms."""
    from app.schemas.resources import AttachmentResponse, LinkedEntityRef

    attachment_id = str(attachment.id) if attachment.id else attachment.id
    data = AttachmentResponse.model_validate(attachment).model_dump()
    _stamp_company(data, attachment, service.company_name_map([attachment]))
    # On-demand signing only for single-attachment metadata/detail responses.
    data["file_path"] = _resolve_attachment_file_path(
        data.get("file_path"),
        provider=getattr(attachment, "storage_provider", None),
    )
    actor_id = (current_user or {}).get("id")
    linked = service.get_linked_entities(
        attachment_id, actor_id=actor_id, company_id=getattr(attachment, "company_id", None)
    )
    data["linked_products"] = [LinkedEntityRef.model_validate(p).model_dump() for p in linked["linked_products"]]
    data["linked_promotions"] = [LinkedEntityRef.model_validate(p).model_dump() for p in linked["linked_promotions"]]
    data["linked_form"] = LinkedEntityRef.model_validate(linked["linked_form"]).model_dump() if linked["linked_form"] else None
    data["linked_packing_lists"] = [LinkedEntityRef.model_validate(p).model_dump() for p in linked["linked_packing_lists"]]
    data["linked_certificates"] = [LinkedEntityRef.model_validate(c).model_dump() for c in linked["linked_certificates"]]
    if linked["linked_products"]:
        data["entity_display_name"] = linked["linked_products"][0]["name"]
    elif linked["linked_promotions"]:
        data["entity_display_name"] = linked["linked_promotions"][0]["name"]
    elif linked["linked_form"]:
        data["entity_display_name"] = linked["linked_form"]["name"]
    elif linked["linked_packing_lists"]:
        data["entity_display_name"] = linked["linked_packing_lists"][0]["name"]
    # Certificates come LAST in the display-name chain: a certificate PDF is
    # normally also linked to the products it covers, and the product is the
    # more useful label. This only wins for a certificate covering nothing.
    elif linked["linked_certificates"]:
        data["entity_display_name"] = linked["linked_certificates"][0]["name"]
    else:
        data["entity_display_name"] = service.get_entity_display_name(
            attachment.entity_type, attachment.entity_id
        )
    user_info = _enrich_uploaded_by_user(service.db, attachment)
    if user_info:
        data["uploaded_by_user"] = user_info
    return data


@router.delete("/links/{link_id}", status_code=status.HTTP_200_OK)
async def delete_attachment_link(
    link_id: str,
    entity_type: str = Query(..., description="Entity type of the link, e.g. inbound_shipment"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an entity-attachment link (e.g. unlink a packing list from an attachment)."""
    try:
        service = EntityAttachmentService(db)
        service.delete_link(link_id, entity_type=entity_type)
        db.commit()
        return {"message": "Link removed."}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single attachment by ID."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        attachment = service.get_attachment(attachment_id)
        return _attachment_response_with_linked_entities(service, attachment, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{attachment_id}/link-packing-list", status_code=status.HTTP_200_OK)
async def link_attachment_to_packing_list(
    attachment_id: str,
    body: LinkPackingListRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Link an existing attachment to a packing list (inbound shipment)."""
    from app.models.procurement import InboundShipment
    packing_list_id = (body.packing_list_id or "").strip()
    if not packing_list_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="packing_list_id is required")
    shipment = db.query(InboundShipment).filter(InboundShipment.id == packing_list_id).first()
    if not shipment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Packing list not found")
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = EntityAttachmentService(db)
        link = service.link_existing_attachment(
            entity_type="inbound_shipment",
            entity_id=packing_list_id,
            attachment_id=attachment_id,
            created_by=current_user.get("id"),
        )
        db.commit()
        db.refresh(link)
        return {"message": "Attachment linked to packing list.", "link_id": str(link.id)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{attachment_id}/unlink-packing-list", status_code=status.HTTP_200_OK)
async def unlink_packing_list_from_attachment(
    attachment_id: str,
    body: LinkPackingListRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unlink a packing list from an attachment by clearing the packing list's attachment_id (for links created via direct FK, e.g. external API)."""
    from app.models.procurement import InboundShipment
    packing_list_id = (body.packing_list_id or "").strip()
    if not packing_list_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="packing_list_id is required")
    shipment = db.query(InboundShipment).filter(
        InboundShipment.id == packing_list_id,
        InboundShipment.attachment_id == attachment_id,
    ).first()
    if not shipment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Packing list not found or not linked to this attachment.",
        )
    setattr(shipment, "attachment_id", None)
    db.commit()
    return {"message": "Packing list unlinked from attachment."}


@router.post("/", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def create_attachment(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    attachment_type_id: Optional[str] = Form(None),
    entity_type: Optional[str] = Form(None),
    entity_id: Optional[str] = Form(None),
    directory_id: Optional[str] = Form(None),
    access_levels: Optional[str] = Form(None),
    regions: Optional[str] = Form(None, description="JSON array of 'west' / 'east'; Packing List uploads."),
    target_entity_type: Optional[str] = Form(
        None,
        description="Field-linkage template: target table this doc describes (product/promotion/packing_list/form). Used to fan field links when later linked to a row.",
    ),
    target_field_keys: Optional[str] = Form(
        None,
        description="Field-linkage template: JSON array of field keys this doc answers (validated against the registry).",
    ),
    on_conflict: Optional[str] = Form(
        None,
        description=(
            "Google-Drive style dup-filename behaviour (TCK-2026-000020). When a row already "
            "exists with the same (directory_id, lower(original_filename)): omit → 409 with "
            "collision detail; 'copy' → rename incoming to '<name> - copy.ext' (loop until free); "
            "'replace' → update existing row in place (same id, new bytes / hash / size / "
            "uploaded_by / uploaded_at) and retrigger webhook with event_type=attachment_replaced."
        ),
    ),
    upload_batch_id: Optional[str] = Form(
        None,
        description=(
            "Per-submit UUID generated by the Create-Attachment dialog so every row uploaded "
            "in one go shares a tag. Notification helpers read this back to coalesce per-"
            "attachment n8n callbacks into a single email."
        ),
    ),
    current_user: dict = Depends(require_permission("resource.attachments.upload")),
    db: Session = Depends(get_db)
):
    """Upload a new attachment to S3. If attachment_type_id is omitted, no webhook is triggered (e.g. promotion extra attachments)."""
    try:
        # Normalize empty string to None for optional type
        type_id = (attachment_type_id or "").strip() or None

        # Debug: Log request details
        logger.info(f"Content-Type: {request.headers.get('content-type')}")
        logger.info(f"File received: {file.filename if file else 'None'}")
        logger.info(f"Attachment type ID: {type_id}")

        # Validate file is provided
        if not file or not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File is required"
            )
        # Reject macOS resource-fork / AppleDouble files (._*); they are metadata-only and not viewable
        if file.filename.strip().startswith("._"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Files starting with ._ are macOS metadata files and cannot be uploaded. Please upload the actual file instead."
            )

        # Read file content
        file_content = await file.read()
        upload_filename = file.filename or "unknown"
        upload_mime = file.content_type

        # WhatsApp/Meta reject CMYK JPEGs (print-pipeline tech-spec drawings)
        # with a generic "Media upload error". Transcode CMYK/YCCK -> RGB JPEG
        # at the upload boundary so stored bytes are always WhatsApp-safe.
        # CPU-bound - run off the event loop so one upload can't freeze every
        # other request on this async worker.
        file_content, upload_filename, upload_mime = await run_in_threadpool(
            ensure_rgb_image, file_content, upload_filename, upload_mime
        )

        # Stock List macro pipeline (docs/plans/PLAN-stock-list-xlsm-macro-upload.md):
        # `.xlsm` uploads typed Stock List are stripped of VBA and reduced to the
        # values-only Template sheet before storage, so macro bytes never reach
        # S3/R2 or the n8n webhook. `.xls`/`.xlsx` pass through untouched.
        if type_id and is_xlsm_filename(upload_filename):
            from app.models.resources import AttachmentType

            is_stock_list_type = (
                db.query(AttachmentType)
                .filter(
                    AttachmentType.id == type_id,
                    AttachmentType.type_name.in_(STOCK_LIST_TYPE_NAMES),
                )
                .first()
                is not None
            )
            if is_stock_list_type:
                try:
                    file_content, upload_filename, upload_mime = await run_in_threadpool(
                        extract_macro_template_xlsx, file_content, upload_filename, upload_mime
                    )
                except MacroWorkbookError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=str(exc),
                    )

        file_size = len(file_content)

        # Calculate SHA-256 hash for duplicate detection
        file_hash = await run_in_threadpool(lambda: hashlib.sha256(file_content).hexdigest())

        # Pre-generate the row id so the object key can embed it (uuid-segregated key - 
        # collision-proof, independent of the editable name; see
        # PLAN-attachment-key-uuid-segregation.md).
        attachment_id = str(uuid.uuid4())
        # display_name = the user-facing, renameable label → stored_filename.
        display_name = upload_filename or "unknown"

        # ------------------------------------------------------------------
        # Google-Drive dup-filename behaviour (TCK-2026-000020).
        # Only relevant when a directory_id is supplied - that scopes the
        # collision check to "this folder". Resolved BEFORE the S3 upload so
        # 409 paths don't waste storage. Scoped to the user-facing display name.
        # ------------------------------------------------------------------
        on_conflict_clean = (on_conflict or "").strip().lower() or None
        if on_conflict_clean not in (None, "copy", "replace"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="on_conflict must be one of 'copy', 'replace', or omitted.",
            )
        collision = _find_filename_collision(db, directory_id, display_name)
        existing_to_replace = None
        if collision is not None:
            if on_conflict_clean is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "ATTACHMENT_FILENAME_COLLISION",
                        "existing_attachment_id": str(collision.id),
                        "existing_file_name": collision.stored_filename,
                        "existing_target_entity_type": collision.target_entity_type,
                        "existing_target_field_keys": collision.target_field_keys,
                    },
                )
            if on_conflict_clean == "copy":
                display_name = _next_copy_name(db, directory_id, display_name)
            else:  # "replace"
                existing_to_replace = collision

        # Split the resolved display name into the two canonical columns:
        #   stored_filename   = user-facing label (editable later via rename)
        #   original_filename = immutable, sanitized → the object-key basename
        stored_filename = display_name
        original_filename = sanitize_storage_filename(display_name)

        # Get attachment type: optional in DB (nullable); required by API for new uploads.
        # When entity_type is "promotion" and no type_id given, use the promotion attachment type (code='promotion').
        type_service = AttachmentTypeService(db)
        attachment_type = None
        if type_id:
            try:
                attachment_type = type_service.get_type(type_id)
            except HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid attachment type ID"
                )
        elif (entity_type or "").strip().lower() == "promotion":
            promotion_type = type_service.get_type_by_code("promotion")
            if promotion_type:
                type_id = str(promotion_type.id)
                attachment_type = promotion_type
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Promotion attachments require an attachment type with code 'promotion'. Please create one in Resource Management > Attachment Types, or run migrations to seed it."
                )
        if not type_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="attachment_type_id is required, or upload with entity_type 'promotion' (which uses the promotion attachment type)."
            )

        # Determine entity_type:
        # 1. Use provided entity_type if given
        # 2. Otherwise, use attachment_type.type_name (sanitized for S3 path)
        # 3. Fallback to "general"
        if entity_type:
            final_entity_type = entity_type.lower().replace(' ', '_')
        elif attachment_type:
            final_entity_type = attachment_type.type_name.lower().replace(' ', '_')
        else:
            final_entity_type = "general"

        # Construct storage key. Basename = immutable original_filename.
        # - promotion: already scoped by entity_id (unchanged).
        # - generic: uuid-segregated by attachment_id so same-name uploads across folders
        #    can NEVER share a key (the old flat {type}/{name} scheme could silently clobber).
        if existing_to_replace is not None:
            # Replace-in-place: overwrite the EXISTING object at its own key so we
            # don't orphan bytes or desync the uuid-segregated key from the row id.
            # Fall back to a computed key only when the prior path is blank/legacy.
            from app.services.storage_router import extract_key as _extract_key
            s3_file_path = _extract_key(existing_to_replace.file_path) or (
                f"{final_entity_type}/{existing_to_replace.id}/"
                f"{sanitize_storage_filename(existing_to_replace.original_filename) or original_filename}"
            )
        elif (entity_type or "").strip().lower() == "promotion" and entity_id:
            s3_file_path = f"promotion/{entity_id}/{original_filename}"
        else:
            s3_file_path = f"{final_entity_type}/{attachment_id}/{original_filename}"

        # Upload to whichever provider STORAGE_DEFAULT_PROVIDER points at (s3 or r2).
        from app.services.storage_router import (
            cdn_base_url,
            default_provider,
            get_backend,
        )
        provider = default_provider()
        backend = get_backend(provider)
        try:
            # Real network PUT via sync boto3 - must not run directly on the event
            # loop, or one slow/large upload freezes every other request this
            # worker is holding (the WORKER TIMEOUT / cascading-504 incident).
            s3_key, _ = await run_in_threadpool(
                backend.upload_file,
                file_content=file_content,
                file_path=s3_file_path,
                content_type=upload_mime,
            )
        except Exception as s3_error:
            logger.error("Storage upload failed (provider=%s): %s", provider, s3_error)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload file to storage: {str(s3_error)}"
            )

        is_promotion_upload = (entity_type or "").strip().lower() == "promotion"
        # Persist only a stable, non-signed CDN URL in DB; signing happens on read.
        stored_file_path = cdn_base_url(provider, s3_key)
        # Grid thumbnail (image uploads only) - small variant so the Files grid
        # paints a ~320px image instead of the full-resolution original. Same
        # blocking-upload concern as above.
        stored_thumbnail_path = await run_in_threadpool(
            store_thumbnail, backend, provider, s3_key, file_content, upload_mime
        )

        # Parse access levels for attachment record and webhook (JSON array string expected).
        # For promotion uploads, use the promotion's access_levels when entity_id is provided.
        from app.services.contact_access_type_service import ContactAccessTypeService
        access_svc = ContactAccessTypeService(db)
        access_levels_payload = None
        if access_levels:
            try:
                parsed = json.loads(access_levels)
                if isinstance(parsed, list):
                    access_levels_payload = access_svc.validate_access_levels(parsed, field_name="access_levels")
            except Exception:
                logger.warning("Invalid access_levels payload; expected JSON array.")
        if not access_levels_payload and (entity_type or "").strip().lower() == "promotion" and entity_id:
            from app.models.marketing import Promotion
            promo = db.query(Promotion).filter(Promotion.id == entity_id).first()
            if promo and getattr(promo, "access_levels", None) and isinstance(promo.access_levels, list):
                access_levels_payload = access_svc.validate_access_levels(list(promo.access_levels), field_name="access_levels")
        if not access_levels_payload:
            access_levels_payload = access_svc.get_default_access_levels()

        regions_payload = None
        if regions:
            from app.schemas.regions import normalize_regions

            try:
                regions_payload = normalize_regions(json.loads(regions))
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="regions must be a JSON array holding west and/or east.",
                )
            # Regions belong to Packing List files only; any other type stores NULL.
            if getattr(attachment_type, "code", None) != "packing_list":
                regions_payload = None

        # Field-linkage template: target_entity_type + target_field_keys (JSON array).
        target_entity_type_clean = (target_entity_type or "").strip() or None
        target_field_keys_parsed: Optional[list[str]] = None
        if target_field_keys:
            try:
                parsed_keys = json.loads(target_field_keys)
                if isinstance(parsed_keys, list):
                    target_field_keys_parsed = [
                        str(k).strip() for k in parsed_keys if str(k).strip()
                    ] or None
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="target_field_keys must be a JSON array of strings.",
                    )
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="target_field_keys must be valid JSON.",
                )

        service = AttachmentService(db)

        # Replace-in-place branch (TCK-2026-000020). Same attachment_id, new
        # bytes / hash / size / uploaded_by / uploaded_at. All four linkage
        # tables (inbound_shipments, promotion_attachments, product_attachments,
        # forms) stay valid via the preserved FK. Webhook re-fires with
        # event_type=attachment_replaced so n8n intake updates the linked
        # record instead of duplicate-rejecting.
        if existing_to_replace is not None:
            existing_to_replace.attachment_type_id = type_id  # type: ignore[assignment]
            existing_to_replace.stored_filename = stored_filename  # type: ignore[assignment]
            existing_to_replace.file_path = stored_file_path  # type: ignore[assignment]
            existing_to_replace.thumbnail_path = stored_thumbnail_path  # type: ignore[assignment]
            existing_to_replace.file_size_bytes = file_size  # type: ignore[assignment]
            existing_to_replace.mime_type = upload_mime or "application/octet-stream"  # type: ignore[assignment]
            existing_to_replace.file_hash = file_hash  # type: ignore[assignment]
            existing_to_replace.uploaded_by = current_user["id"]  # type: ignore[assignment]
            existing_to_replace.uploaded_at = datetime.utcnow()  # type: ignore[assignment]
            existing_to_replace.access_levels = access_levels_payload  # type: ignore[assignment]
            existing_to_replace.storage_provider = provider  # type: ignore[assignment]
            if target_entity_type_clean is not None:
                existing_to_replace.target_entity_type = target_entity_type_clean  # type: ignore[assignment]
            if target_field_keys_parsed is not None:
                existing_to_replace.target_field_keys = target_field_keys_parsed  # type: ignore[assignment]
            if regions_payload is not None:
                existing_to_replace.regions = regions_payload  # type: ignore[assignment]
            upload_batch_clean = (upload_batch_id or "").strip() or None
            if upload_batch_clean is not None:
                existing_to_replace.upload_batch_id = upload_batch_clean  # type: ignore[assignment]
            db.commit()
            db.refresh(existing_to_replace)
            attachment = existing_to_replace
            if attachment_type is not None and not is_promotion_upload:
                try:
                    _create_and_send_webhook(
                        db,
                        attachment,
                        attachment_type,
                        access_levels_payload,
                        current_user["id"],
                        event_type="attachment_replaced",
                    )
                except Exception as e:
                    logger.error(
                        "Failed to fire attachment_replaced webhook for %s: %s",
                        getattr(attachment, "id", None),
                        e,
                        exc_info=True,
                    )
            # Stamp the owning company by hand: returning the bare ORM row would
            # serialize company_name as None while company_id is populated, so the
            # write echo would disagree with the very next list read.
            data = AttachmentResponse.model_validate(attachment).model_dump()
            _stamp_company(data, attachment, service.company_name_map([attachment]))
            return data

        # Create attachment record. file_path stored as CDN base URL for consistency with other attachments.
        attachment_data = AttachmentCreate(
            id=attachment_id,  # same uuid embedded in the object key
            attachment_type_id=type_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=stored_file_path,
            thumbnail_path=stored_thumbnail_path,
            file_size_bytes=file_size,
            mime_type=upload_mime or "application/octet-stream",  # Default if None
            file_hash=file_hash,
            entity_type=entity_type,  # Store original entity_type if provided
            entity_id=entity_id,
            directory_id=directory_id,
            access_levels=access_levels_payload,
            regions=regions_payload,
            storage_provider=provider,
            target_entity_type=target_entity_type_clean,
            target_field_keys=target_field_keys_parsed,
            upload_batch_id=(upload_batch_id or "").strip() or None,
        )

        attachment = service.create_attachment(attachment_data, current_user["id"])
        # Trigger webhook for normal attachment uploads (including Files menu Promotion type).
        # Only skip promotion-module uploads where entity_type=promotion.
        if attachment_type is not None and not is_promotion_upload:
            try:
                webhook_access_levels = access_levels_payload
                if webhook_access_levels is None:
                    attachment_levels = getattr(attachment, "access_levels", None)
                    webhook_access_levels = (
                        list(attachment_levels) if isinstance(attachment_levels, list) else None
                    )
                _create_and_send_webhook(
                    db,
                    attachment,
                    attachment_type,
                    webhook_access_levels,
                    current_user["id"],
                )
            except Exception as e:
                logger.error(
                    "Failed to create integration log for attachment %s: %s",
                    getattr(attachment, "id", None),
                    e,
                    exc_info=True,
                )
        # Same reason as the replace-in-place branch above: the ORM row alone
        # carries company_id but no company_name.
        data = AttachmentResponse.model_validate(attachment).model_dump()
        _stamp_company(data, attachment, service.company_name_map([attachment]))
        return data

    except HTTPException:
        raise
    except ValueError as e:
        # S3 configuration error (missing or empty env vars)
        logger.error(f"S3 configuration error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e) or "S3 storage is not properly configured. Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME in the backend environment."
        )
    except Exception as e:
        import traceback
        error_msg = str(e)
        logger.error(f"Error in create_attachment: {error_msg}")
        logger.error(traceback.format_exc())
        
        # If it's a validation error, return more details
        if "validation" in error_msg.lower() or "422" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Validation error: {error_msg}"
            )
        
        raise handle_internal_error(error_msg)


@router.post("/replace-latest-stock-list", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def replace_latest_stock_list(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace the Stock_List attachment. Only one non-archived attachment with type Stock_List allowed; archives any existing, then creates new. For n8n AI agent."""
    if not file or not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is required")

    try:
        # Upload file (same flow as create_attachment); use sanitized original filename only (no UUID prefix)
        file_content = await file.read()
        upload_filename = file.filename
        upload_mime = file.content_type

        # Stock List macro pipeline (docs/plans/PLAN-stock-list-xlsm-macro-upload.md):
        # `.xlsm` → VBA stripped, values-only Template sheet, re-emitted as `.xlsx`
        # so the chatbot/n8n never receives macro bytes. `.xls`/`.xlsx` untouched.
        try:
            file_content, upload_filename, upload_mime = await run_in_threadpool(
                extract_macro_template_xlsx, file_content, upload_filename, upload_mime
            )
        except MacroWorkbookError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        # Archive-and-upload: PLAN-autocount-pull-review.md P7 - shared with the pull
        # Confirm's stock Apply (SR4), so both write the same attachment the same way.
        import app.services.stock_list_archive_service as stock_list_archive_service

        attachment = await run_in_threadpool(
            stock_list_archive_service.replace_latest_stock_list,
            db,
            file_bytes=file_content,
            filename=upload_filename,
            user_id=current_user["id"],
            mime_type=upload_mime,
        )

        service = AttachmentService(db)
        data = AttachmentResponse.model_validate(attachment).model_dump()
        _stamp_company(data, attachment, service.company_name_map([attachment]))
        user_info = _enrich_uploaded_by_user(db, attachment)
        if user_info:
            data["uploaded_by_user"] = user_info
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("replace_latest_stock_list failed")
        raise handle_internal_error(str(e))


def _normalize_zip_path(name: str) -> str:
    """Normalize zip entry name: strip slashes, use forward slash."""
    return name.replace("\\", "/").strip("/")


@router.post("/bulk-import", status_code=status.HTTP_202_ACCEPTED)
async def bulk_import_attachments(
    file: UploadFile = File(..., description="ZIP file containing folders and files"),
    attachment_type_id: str = Form(...),
    access_levels: Optional[str] = Form(None),
    parent_directory_id: Optional[str] = Form(None),
    on_conflict: Optional[str] = Form(
        "skip",
        description=(
            "TCK-2026-000020 ZIP collision behaviour (intra-zip AND zip-vs-system "
            "dupes detected on (resolved directory_id, lower(filename))). "
            "'skip' (default) → skip the colliding entry, keep existing. "
            "'copy' → rename incoming to '<name> - copy.ext' (loop until free). "
            "'replace' → update existing row in place (preserves attachment_id and links)."
        ),
    ),
    current_user: dict = Depends(require_permission("resource.attachments.bulk_import")),
    db: Session = Depends(get_db),
):
    """Queue a ZIP import job. Import runs in the background with batch processing. Poll GET /api/v1/system/jobs/{job_id}/status for progress."""
    import uuid as _uuid

    if not file or not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A ZIP file is required")

    on_conflict_clean = (on_conflict or "skip").strip().lower()
    if on_conflict_clean not in ("skip", "copy", "replace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="on_conflict must be one of 'skip', 'copy', or 'replace'.",
        )

    type_service = AttachmentTypeService(db)
    try:
        type_service.get_type(attachment_type_id)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attachment type ID")

    zip_content = await file.read()

    def _validate_zip() -> None:
        with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zf:
            zf.testzip()

    try:
        await run_in_threadpool(_validate_zip)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or corrupted ZIP file")

    # Upload to object storage so the RQ worker (separate pod, separate /tmp)
    # can fetch it. Transient key under a non-attachment prefix - NOT written to
    # the attachments table; worker deletes the object on completion.
    from app.services.storage_router import default_provider, get_backend

    storage_provider = default_provider()
    storage_backend = get_backend(storage_provider)
    storage_key = f"bulk-imports/{_uuid.uuid4().hex}.zip"
    try:
        await run_in_threadpool(
            storage_backend.upload_file, zip_content, storage_key, content_type="application/zip"
        )
    except Exception as exc:
        logger.exception("bulk-import zip upload to storage failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stage import zip in storage: {exc}",
        )

    from app.services.job_service import JobService
    from app.services.queue_service import enqueue_job
    from app.tasks.import_tasks import process_attachment_bulk_import

    job_service = JobService(db)
    job = job_service.create_job(
        job_type="attachment_bulk_import",
        user_id=current_user["id"],
        filename=file.filename,
        metadata={
            "attachment_type_id": attachment_type_id,
            "parent_directory_id": parent_directory_id,
            "storage_provider": storage_provider,
            "storage_key": storage_key,
        },
    )
    db.commit()

    rq_job = enqueue_job(
        process_attachment_bulk_import,
        str(job.id),
        storage_key,
        attachment_type_id,
        access_levels or "[]",
        parent_directory_id,
        current_user["id"],
        on_conflict_clean,
        storage_provider,
        queue_name="imports",
        job_timeout=7200,
        job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
    )
    job_service.update_job_with_rq_id(job, rq_job.id)

    return {
        "message": "Import started. Processing in the background. You can close this dialog.",
        "job_id": job.job_id,
        "id": str(job.id),
    }


@router.put("/{attachment_id}", response_model=AttachmentResponse)
async def update_attachment(
    attachment_id: str,
    attachment_data: AttachmentUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an attachment."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        attachment = service.update_attachment(attachment_id, attachment_data)
        # The bare ORM row serializes company_name as None; stamp it so the write
        # echo matches what the list and detail reads return.
        data = AttachmentResponse.model_validate(attachment).model_dump()
        _stamp_company(data, attachment, service.company_name_map([attachment]))
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-move", response_model=AttachmentsBulkMoveResponse)
async def bulk_move_attachments(
    body: AttachmentsBulkMoveRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move many attachments into the same target folder in one transaction (drag-drop multi-select)."""
    try:
        service = AttachmentService(db)
        updated = service.bulk_move(body.attachment_ids, body.directory_id)
        return AttachmentsBulkMoveResponse(updated=updated)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{attachment_id}/download")
async def download_attachment(
    attachment_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Download an attachment file from S3."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        attachment = service.get_attachment(attachment_id)
        
        # Get file content from S3
        file_content = service.get_file_content(attachment_id)
        
        # Return file as streaming response
        return Response(
            content=file_content,
            media_type=str(getattr(attachment, "mime_type", None) or "application/octet-stream"),
            headers={
                "Content-Disposition": content_disposition(
                    attachment.stored_filename or attachment.original_filename or ""
                ),
                "Content-Length": str(attachment.file_size_bytes or len(file_content))
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error in download_attachment: {str(e)}")
        logger.error(traceback.format_exc())
        raise handle_internal_error(str(e))


@router.get("/{attachment_id}/metadata", response_model=AttachmentResponse)
async def get_attachment_metadata(
    attachment_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get attachment metadata without downloading the file."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        attachment = service.get_attachment(attachment_id)
        return _attachment_response_with_linked_entities(service, attachment, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{attachment_id}/preview-url")
async def get_attachment_preview_url(
    attachment_id: str,
    variant: str = Query(
        "original",
        description="original (default) | thumb - thumb signs the grid thumbnail, falling back to the original when none exists.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get a fresh signed URL for preview/open action."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        attachment = service.get_attachment(attachment_id)
        file_path = getattr(attachment, "file_path", None)
        if (variant or "").strip().lower() == "thumb":
            thumb_path = getattr(attachment, "thumbnail_path", None)
            if thumb_path:
                file_path = thumb_path
        preview_url = _resolve_attachment_file_path(
            str(file_path) if file_path is not None else None,
            provider=getattr(attachment, "storage_provider", None),
        )
        return {"attachment_id": attachment_id, "preview_url": preview_url}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{attachment_id}", status_code=status.HTTP_200_OK)
async def delete_attachment(
    attachment_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an attachment permanently (hard delete). Use archive for retention."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        result = service.delete_attachment(attachment_id, current_user["id"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{attachment_id}/archive", status_code=status.HTTP_200_OK)
async def archive_attachment(
    attachment_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Archive an attachment (soft delete). Data remains for retention. Use restore to unarchive."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        result = service.archive_attachment(attachment_id, current_user["id"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{attachment_id}/restore", status_code=status.HTTP_200_OK)
async def restore_attachment(
    attachment_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Restore an archived attachment."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        service = AttachmentService(db)
        result = service.restore_attachment(attachment_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-archive", status_code=status.HTTP_200_OK)
async def bulk_archive_attachments(
    body: AttachmentBulkDeleteRequest,
    current_user: dict = Depends(require_permission("resource.attachments.delete")),
    db: Session = Depends(get_db)
):
    """Archive multiple attachments (soft delete)."""
    try:
        service = AttachmentService(db)
        result = service.archive_attachments(body.attachment_ids, current_user["id"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_attachments(
    body: AttachmentBulkDeleteRequest,
    current_user: dict = Depends(require_permission("resource.attachments.bulk_delete")),
    db: Session = Depends(get_db)
):
    """Mass delete attachments permanently (hard delete)."""
    try:
        service = AttachmentService(db)
        result = service.delete_attachments(body.attachment_ids, current_user["id"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/reorder", status_code=status.HTTP_200_OK)
async def reorder_attachments(
    body: AttachmentReorderRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Reorder attachments within a folder (sets sort_order by list position)."""
    try:
        service = AttachmentService(db)
        result = service.reorder_attachments(body.attachment_ids, body.directory_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/bulk-attachment-type",
    response_model=BulkAttachmentTypeResponse,
    status_code=status.HTTP_200_OK,
)
async def bulk_set_attachment_type(
    body: BulkAttachmentTypeRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set attachment_type_id on one or many attachments. No webhook resubmit.

    Same endpoint serves single-edit (one ID) and bulk-edit (many IDs)."""
    try:
        service = AttachmentService(db)
        return service.bulk_set_attachment_type(
            body.attachment_ids,
            body.attachment_type_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/bulk-company",
    response_model=BulkCompanyResponse,
    status_code=status.HTTP_200_OK,
)
async def bulk_set_company(
    body: BulkCompanyRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """`Set company…` on one or many attachments and/or folders (R4, R13).

    Same guard as `PUT /attachments/{attachment_id}` - no new permission slug
    (R13). The UI never calls this directly (R22): it parks a deferred action
    per selected row through `attachment.set_company` / `attachment_directory.
    set_company` (app/services/record_actions.py). This route is the popup's
    single-row Edit fallback, plus tests and n8n-style callers.
    """
    try:
        service = AttachmentCompanyService(db)
        result = service.apply(
            attachment_ids=body.attachment_ids,
            directory_ids=body.directory_ids,
            company_id=body.company_id,
            actor_id=current_user.get("id"),
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-access-levels/preview", response_model=BulkAccessLevelsPreviewResponse)
async def preview_bulk_access_levels(
    body: BulkAccessLevelsPreviewRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List linked products / promotions / forms / packing lists that would receive propagated access levels."""
    try:
        service = AttachmentService(db)
        aids = service.resolve_bulk_attachment_ids(body.attachment_ids, body.directory_id)
        if not aids:
            return BulkAccessLevelsPreviewResponse(attachment_count=0, targets=[])
        result = service.preview_access_propagation(aids)
        return BulkAccessLevelsPreviewResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-access-levels/apply", response_model=BulkAccessLevelsApplyResponse)
async def apply_bulk_access_levels(
    body: BulkAccessLevelsApplyRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set access levels on attachments in scope; optionally cascade to linked records."""
    try:
        service = AttachmentService(db)
        aids = service.resolve_bulk_attachment_ids(body.attachment_ids, body.directory_id)
        result = service.apply_bulk_access_levels(
            aids,
            body.access_levels,
            body.propagate_to_linked,
        )
        return BulkAccessLevelsApplyResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{attachment_id}/resubmit", status_code=status.HTTP_200_OK)
async def resubmit_attachment_webhook(
    attachment_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resubmit attachment webhook to n8n: refresh CloudFront signed URL (may have expired) then POST payload."""
    try:
        validate_uuid_path(attachment_id, resource="Attachment")
        # Verify attachment exists (ORM row; file_path is stable base URL or S3 key from DB)
        attachment_service = AttachmentService(db)
        attachment = attachment_service.get_attachment(attachment_id)

        # An untyped attachment is never sent to n8n (owner rule, 2 Oct 2026), even
        # one with a log from before that rule; say so rather than send it.
        if getattr(attachment, "attachment_type_id", None) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This file has no attachment type, so it is not sent to n8n.",
            )

        # Find the integration log for this attachment
        integration_service = IntegrationLogService(db)
        logs_result = integration_service.list_integration_logs(
            page=1,
            limit=1,
            business_table="attachments",
            business_id=attachment_id,
            integration_channel="n8n"
        )

        if not logs_result.get("data") or len(logs_result["data"]) == 0:
            # No prior log (e.g. local dev DB never imported integration_logs, or
            # attachment uploaded before webhook URL was configured). Fall back to
            # the same path the upload API uses: create a fresh integration log
            # and POST the webhook payload built from current attachment state.
            current_webhook_url = get_n8n_attachment_webhook_url(db)
            if not current_webhook_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "No attachment webhook URL configured. Set the n8n "
                        "attachment webhook in Settings before resubmitting."
                    ),
                )
            _create_and_send_webhook(
                db,
                attachment,
                getattr(attachment, "attachment_type", None),
                getattr(attachment, "access_levels", None),
                current_user["id"],
                event_type="attachment_resubmitted",
            )
            return {
                "message": "No prior integration log; created fresh webhook send.",
                "integration_log_id": None,
            }

        integration_log = logs_result["data"][0]
        log_id = str(integration_log.id)
        raw_log = integration_service.get_integration_log(log_id)

        attachment_file_path = str(getattr(attachment, "file_path", ""))
        signed_url = build_signed_attachment_url_for_webhook(
            attachment_file_path,
            provider=getattr(attachment, "storage_provider", None),
        )
        try:
            raw_request_payload = getattr(raw_log, "request_payload", None)
            payload_dict = json.loads(raw_request_payload) if raw_request_payload is not None else {}
        except (json.JSONDecodeError, TypeError):
            payload_dict = {}

        payload_dict["integration_log_id"] = log_id
        payload_dict["attachment_url"] = signed_url
        payload_dict["s3_url"] = signed_url
        payload_dict["file_path"] = attachment_file_path
        payload_dict["attachment_id"] = str(attachment.id)
        # User-facing name (stored_filename) so the downstream n8n record's filename
        # matches the display/rename - consistent with create_and_send_webhook.
        payload_dict["attachment_filename"] = attachment.stored_filename or attachment.original_filename
        payload_dict["attachment_mime_type"] = attachment.mime_type
        payload_dict["file_size"] = getattr(attachment, "file_size_bytes", None)
        if getattr(attachment, "attachment_type", None) is not None:
            payload_dict["attachment_type"] = attachment.attachment_type.type_name

        setattr(raw_log, "request_payload", json.dumps(payload_dict))

        # Re-resolve the CURRENT attachment webhook URL from settings so resubmit
        # honours the live "Attachment webhook URL" value, not the endpoint stored
        # on the log at upload time (which may be a stale env fallback).
        current_webhook_url = get_n8n_attachment_webhook_url(db)
        if current_webhook_url:
            setattr(raw_log, "endpoint", current_webhook_url)
        db.commit()

        # force_resend: actually POST again (even if status was sent/success) and do not hit max-retry guard
        success, error_msg = integration_service.send_webhook_for_log(log_id, force_resend=True)

        if success:
            return {
                "message": "Webhook resent with a fresh signed URL",
                "integration_log_id": log_id,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to resubmit webhook: {error_msg or 'Unknown error'}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resubmitting attachment webhook: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))
