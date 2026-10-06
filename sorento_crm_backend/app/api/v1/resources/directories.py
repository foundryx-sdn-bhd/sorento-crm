"""Files (folders) API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime
from typing import cast

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    get_current_user,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.resources_service import AttachmentDirectoryService, AttachmentService
from app.schemas.resources import (
    AttachmentDirectoryCreate,
    AttachmentDirectoryUpdate,
    AttachmentDirectoryMoveRequest,
    AttachmentDirectoryResponse,
    AttachmentDirectoryTreeNode,
)
from app.services.error_handler import handle_internal_error

router = APIRouter()


def _build_tree_node(service: AttachmentDirectoryService, dir_id: str) -> AttachmentDirectoryTreeNode:
    """Build a tree node with children from a directory ID."""
    d = service.get_directory(dir_id)
    parent_id_raw = getattr(d, "parent_id", None)
    sort_order_raw = getattr(d, "sort_order", None)
    created_at = cast(datetime, getattr(d, "created_at"))
    company_id_raw = getattr(d, "company_id", None)
    children = [
        _build_tree_node(service, str(c.id))
        for c in service.list_flat(dir_id)
    ]
    return AttachmentDirectoryTreeNode(
        id=str(d.id),
        name=str(getattr(d, "name", "")),
        parent_id=str(parent_id_raw) if parent_id_raw is not None else None,
        sort_order=(int(sort_order_raw) if sort_order_raw is not None else None),
        created_at=created_at,
        # Owning company (R14 / AC-E3): read straight off the already-loaded
        # row - no extra query, no company_name lookup here (the tree/
        # breadcrumb surfaces do not resolve names today; the id alone is
        # enough for a future consumer to widen this, and NULL stays legible
        # as Shared either way).
        company_id=str(company_id_raw) if company_id_raw is not None else None,
        children=children,
    )


@router.get("/", response_model=list[AttachmentDirectoryResponse])
async def list_directories(
    parent_id: str | None = None,
    current_user: dict = Depends(require_permission_with_api_key("resource.attachment_directories.view")),
    db=Depends(get_db),
):
    """List directories directly under parent_id (omit for root)."""
    try:
        service = AttachmentDirectoryService(db)
        dirs = service.list_flat(parent_id)
        return list(dirs)
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/tree", response_model=list[AttachmentDirectoryTreeNode])
async def get_directory_tree(
    deleted: bool = Query(False, description="If true, return tree of deleted directories only (for Trash)"),
    current_user: dict = Depends(require_session_or_api_key_permission("resource.attachment_directories.view")),
    db=Depends(get_db),
):
    """Get full directory tree. Use deleted=true for Trash (deleted folders only)."""
    try:
        service = AttachmentDirectoryService(db)
        if deleted:
            return service.get_deleted_tree()
        roots = service.list_flat(None)
        return [
            _build_tree_node(service, str(r.id))
            for r in roots
        ]
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{directory_id}", response_model=AttachmentDirectoryResponse)
async def get_directory(
    directory_id: str,
    current_user: dict = Depends(require_permission_with_api_key("resource.attachment_directories.view")),
    db=Depends(get_db),
):
    """Get a single directory by ID."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        service = AttachmentDirectoryService(db)
        return service.get_directory(directory_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=AttachmentDirectoryResponse, status_code=201)
async def create_directory(
    data: AttachmentDirectoryCreate,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new directory."""
    try:
        service = AttachmentDirectoryService(db)
        return service.create_directory(data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{directory_id}", response_model=AttachmentDirectoryResponse)
async def update_directory(
    directory_id: str,
    data: AttachmentDirectoryUpdate,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update a directory."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        service = AttachmentDirectoryService(db)
        return service.update_directory(directory_id, data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{directory_id}/move", response_model=AttachmentDirectoryResponse)
async def move_directory(
    directory_id: str,
    data: AttachmentDirectoryMoveRequest,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Reparent and reorder a folder. Used by drag-and-drop: drop on folder = nest, drop between = reorder."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        service = AttachmentDirectoryService(db)
        return service.move_directory(directory_id, data.parent_id, data.position)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{directory_id}", status_code=200)
async def delete_directory(
    directory_id: str,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft-delete a directory, its subfolders, and archive all attachments in them. Can be restored from Trash."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        dir_service = AttachmentDirectoryService(db)
        dir_ids = dir_service.get_descendant_directory_ids(directory_id)
        attachment_service = AttachmentService(db)
        deleted_count = attachment_service.archive_attachments_in_directories(dir_ids, current_user["id"])
        dir_service.delete_directory(directory_id, deleted_by=current_user["id"])
        return {
            "message": "Directory moved to trash",
            "attachments_deleted": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{directory_id}/restore", status_code=200)
async def restore_directory(
    directory_id: str,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Restore a deleted directory, its subfolders, and all attachments in them."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        service = AttachmentDirectoryService(db)
        result = service.restore_directory(directory_id)
        return {
            "message": "Directory restored",
            "directories_restored": result["directories_restored"],
            "attachments_restored": result["attachments_restored"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{directory_id}/permanent-delete", status_code=200)
async def permanent_delete_directory(
    directory_id: str,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Permanently delete a directory in Trash and all its contents. Cannot be undone."""
    try:
        validate_uuid_path(directory_id, resource="Attachment Directory")
        service = AttachmentDirectoryService(db)
        result = service.permanent_delete_directory(directory_id, deleted_by=current_user["id"])
        return {
            "message": "Directory permanently deleted",
            "directories_deleted": result["directories_deleted"],
            "attachments_deleted": result["attachments_deleted"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
