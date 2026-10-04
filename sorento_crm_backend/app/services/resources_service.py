"""Resources service for business logic."""
import logging
import re
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
from typing import Any, Optional, List

logger = logging.getLogger(__name__)
from app.models.base import get_company_scope
from app.models.resources import Attachment, AttachmentType, AttachmentDirectory
from app.schemas.resources import (
    AttachmentCreate, AttachmentUpdate, AttachmentTypeCreate, AttachmentTypeUpdate,
    AttachmentDirectoryCreate, AttachmentDirectoryUpdate,
)
from app.services.error_handler import (
    handle_not_found,
    handle_conflict,
    handle_unprocessable,
    handle_validation_error,
)
from app.services.embedding_events import publish_embedding_event
from app.services.storage_router import extract_key


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Distinguishes "the caller didn't say" (look it up) from "the caller said
# None" (the attachment IS shared) on get_linked_entities' company_id kwarg.
_COMPANY_ID_NOT_GIVEN = object()


def _apply_company_filter(query, model, company: Optional[str]):
    """The `company` filter (AC-E1), shared by attachments and folders - both
    carry a `company_id` column and the exact same two-value contract:
    `"shared"` narrows to `company_id IS NULL`; a UUID narrows to that
    company only (excludes shared rows); omitted/blank leaves `query`
    untouched (today's scope-predicate default). Anything else - a typo, a
    non-UUID string - is a 422 rather than silently falling through to "no
    filter", which would show the caller everything instead of failing loud.
    """
    if company is None:
        return query
    value = str(company).strip()
    if not value:
        return query
    if value.lower() == "shared":
        return query.filter(model.company_id.is_(None))
    if not _UUID_RE.match(value):
        raise handle_unprocessable(
            f"`company` must be 'shared' or a company UUID, got {value!r}."
        )
    return query.filter(model.company_id == value.lower())


class AttachmentDirectoryService:
    """Service for attachment directory (folder) operations."""

    def __init__(self, db: Session):
        self.db = db

    def list_flat(self, parent_id: Optional[str] = None, include_deleted: bool = False):
        """List directories directly under parent_id (None = root). Excludes deleted unless include_deleted=True."""
        q = self.db.query(AttachmentDirectory).filter(
            AttachmentDirectory.parent_id == parent_id
        )
        if not include_deleted:
            q = q.filter(AttachmentDirectory.is_deleted == False)
        q = q.order_by(AttachmentDirectory.sort_order.asc().nullsfirst(), AttachmentDirectory.name.asc())
        return q.all()

    def get_tree(self, parent_id: Optional[str] = None, include_deleted: bool = False):
        """Return directory tree rooted at parent_id (None = root)."""
        dirs = self.list_flat(parent_id, include_deleted=include_deleted)
        result = []
        for d in dirs:
            node = {
                "id": str(d.id),
                "name": d.name,
                "parent_id": str(d.parent_id) if d.parent_id else None,
                "sort_order": d.sort_order,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "children": self.get_tree(str(d.id), include_deleted=include_deleted),
            }
            result.append(node)
        return result

    def get_directory(self, directory_id: str, include_deleted: bool = False):
        """Get a directory by ID."""
        q = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == directory_id)
        if not include_deleted:
            q = q.filter(AttachmentDirectory.is_deleted == False)
        d = q.first()
        if not d:
            raise handle_not_found("Attachment directory", directory_id)
        return d

    def create_directory(self, data: AttachmentDirectoryCreate):
        """Create a new directory.

        Multi-company (PLAN-shared-brand-attachments R20): a folder is
        ``__company_shared__``, so the before_insert auto-stamp never runs for
        it - this has to set ``company_id`` itself, or every new folder would
        land NULL (shared) regardless of where it was created. A folder under
        a shared parent is shared too (NULL company_id inherits as NULL,
        no scope resolution needed - the parent already answered the
        question). At ROOT there is no parent to inherit from, so this
        resolves the write exactly as the retired auto-stamp did for every
        other owned table: the one active company, ``DEFAULT_COMPANY_ID``
        under an all-companies (``None``) scope, or a 400 under an ambiguous
        one (``UNSET`` / several companies) - a root folder is never silently
        shared by a guess.
        """
        from app.services.company_scope import resolve_write_company_id

        parent = None
        if data.parent_id:
            parent = self.get_directory(data.parent_id, include_deleted=False)
        directory_dict = data.model_dump()
        directory_dict["company_id"] = (
            parent.company_id
            if parent is not None
            else resolve_write_company_id(get_company_scope(self.db))
        )
        d = AttachmentDirectory(**directory_dict)
        self.db.add(d)
        self.db.commit()
        self.db.refresh(d)
        return d

    def update_directory(self, directory_id: str, data: AttachmentDirectoryUpdate):
        """Update a directory."""
        d = self.get_directory(directory_id)
        payload = data.model_dump(exclude_unset=True)
        if "parent_id" in payload and payload["parent_id"] == directory_id:
            raise handle_conflict("Directory cannot be its own parent.")
        for k, v in payload.items():
            setattr(d, k, v)
        if "parent_id" in payload and payload["parent_id"] and d.company_id is None:
            self._share_ancestors_of(payload["parent_id"])
        self.db.commit()
        self.db.refresh(d)
        return d

    def _share_ancestors_of(self, directory_id: Optional[str]) -> None:
        """R19: reparenting a SHARED folder under an owned one pulls the new
        parent's ancestor chain to shared too, so the shared folder's path
        still resolves from every company."""
        if not directory_id:
            return
        from app.services.attachment_company_service import share_ancestor_chain

        share_ancestor_chain(self.db, directory_id)

    def move_directory(self, directory_id: str, parent_id: Optional[str], position: Optional[int]):
        """Reparent + reorder. Re-sequences sort_order on the new parent's children at 10-step intervals."""
        d = self.get_directory(directory_id)
        if parent_id == directory_id:
            raise handle_conflict("Directory cannot be its own parent.")
        if parent_id:
            descendant_ids = set(self.get_descendant_directory_ids(directory_id, include_deleted=False))
            if parent_id in descendant_ids:
                raise handle_conflict("Cannot move folder into one of its own subfolders.")
            self.get_directory(parent_id, include_deleted=False)

        setattr(d, "parent_id", parent_id)

        # PLAN-shared-brand-attachments R19: moving a SHARED folder under an
        # owned one pulls the new parent's ancestor chain to shared too.
        if parent_id and d.company_id is None:
            self._share_ancestors_of(parent_id)

        siblings_q = self.db.query(AttachmentDirectory).filter(
            AttachmentDirectory.is_deleted == False,
            AttachmentDirectory.id != directory_id,
        )
        if parent_id is None:
            siblings_q = siblings_q.filter(AttachmentDirectory.parent_id.is_(None))
        else:
            siblings_q = siblings_q.filter(AttachmentDirectory.parent_id == parent_id)
        siblings = siblings_q.order_by(
            AttachmentDirectory.sort_order.asc().nullsfirst(),
            AttachmentDirectory.name.asc(),
        ).all()

        if position is None or position >= len(siblings):
            new_order = siblings + [d]
        else:
            idx = max(0, position)
            new_order = siblings[:idx] + [d] + siblings[idx:]

        for i, sib in enumerate(new_order):
            sib.sort_order = (i + 1) * 10

        self.db.commit()
        self.db.refresh(d)
        return d

    def delete_directory(self, directory_id: str, deleted_by: str):
        """Soft-delete a directory and all descendants; archive attachments in them. Uses bulk update for speed."""
        from datetime import datetime

        self.get_directory(directory_id)  # validate exists
        dir_ids = self.get_descendant_directory_ids(directory_id)
        if not dir_ids:
            self.db.commit()
            return
        now = datetime.utcnow()
        self.db.query(AttachmentDirectory).filter(
            AttachmentDirectory.id.in_(dir_ids)
        ).update(
            {
                AttachmentDirectory.is_deleted: True,
                AttachmentDirectory.deleted_at: now,
                AttachmentDirectory.deleted_by: deleted_by,
            },
            synchronize_session=False,
        )
        self.db.commit()

    def get_directory_by_parent_and_name(self, parent_id: Optional[str], name: str, include_deleted: bool = False):
        """Get a directory by parent_id and name, or None if not found."""
        q = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.name == name)
        if parent_id is None:
            q = q.filter(AttachmentDirectory.parent_id.is_(None))
        else:
            q = q.filter(AttachmentDirectory.parent_id == parent_id)
        if not include_deleted:
            q = q.filter(AttachmentDirectory.is_deleted == False)
        return q.first()

    def get_or_create_directory(self, parent_id: Optional[str], name: str):
        """Get existing directory by parent_id and name, or create it."""
        existing = self.get_directory_by_parent_and_name(parent_id, name)
        if existing:
            return existing
        data = AttachmentDirectoryCreate(name=name, parent_id=parent_id, sort_order=None)
        return self.create_directory(data)

    def get_or_create_path(self, parent_id: Optional[str], path_parts: List[str]) -> Optional[str]:
        """Get or create a chain of directories and return the final directory id. Empty path_parts returns parent_id."""
        if not path_parts:
            return parent_id
        current_id = parent_id
        for part in path_parts:
            if not part.strip():
                continue
            d = self.get_or_create_directory(current_id, part.strip())
            current_id = str(d.id)
        return current_id

    def get_full_directory_path(self, directory_id: Optional[str]) -> Optional[str]:
        """Compute full path from root to directory, e.g. 'SORENTO CABANA (DEALER) --> SORENTO --> Product Photo --> Angle Valve'."""
        if not directory_id:
            return None
        parts: List[str] = []
        d = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == directory_id).first()
        while d:
            parts.append(d.name)
            d = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == d.parent_id).first() if d.parent_id else None
        parts.reverse()
        return " --> ".join(parts) if parts else None

    def get_descendant_directory_ids(self, directory_id: str, include_deleted: bool = True) -> List[str]:
        """Return all directory IDs in the subtree rooted at directory_id
        (including the directory itself).

        Delegates to the portable ORM walker rather than the raw ``id::text``
        recursive CTE it replaced. (The original note here said the walker existed
        to keep sqlite working; tests run on Postgres ONLY - see PRINCIPLES.md -
        so what it actually buys now is one readable code path, not portability.)
        ``include_deleted`` default stays ``True`` to preserve prior behaviour for
        restore/permanent-delete callers that must see already-soft-deleted rows.
        """
        return self.get_descendant_directory_ids_portable(
            directory_id, include_deleted=include_deleted
        )

    def get_descendant_directory_ids_portable(self, directory_id: str, include_deleted: bool = False) -> List[str]:
        """Portable descendant resolver (subtree rooted at directory_id, inclusive).

        Mirrors ``get_descendant_directory_ids`` but walks the tree with plain
        ORM queries instead of a Postgres-only ``id::text`` recursive CTE, so it
        runs unchanged on the sqlite test harness. Use this on any path shared by
        the unified Drive endpoint (which is exercised by pytest on sqlite).
        """
        root = str(directory_id)
        result: List[str] = []
        seen: set[str] = set()
        frontier = [root]
        while frontier:
            batch = [fid for fid in frontier if fid not in seen]
            if not batch:
                break
            seen.update(batch)
            q = self.db.query(AttachmentDirectory.id).filter(
                AttachmentDirectory.id.in_(batch)
            )
            if not include_deleted:
                q = q.filter(AttachmentDirectory.is_deleted == False)
            present = [str(r[0]) for r in q.all()]
            result.extend(present)
            child_q = self.db.query(AttachmentDirectory.id).filter(
                AttachmentDirectory.parent_id.in_(present) if present else False
            )
            if not include_deleted:
                child_q = child_q.filter(AttachmentDirectory.is_deleted == False)
            frontier = [str(r[0]) for r in child_q.all()] if present else []
        return result

    def build_directory_path_map(
        self, directory_ids: Optional[List[str]] = None, sep: str = " / "
    ) -> dict[str, str]:
        """Return ``{directory_id: 'Root / Child / Leaf'}`` for the given folders
        (or every non-deleted folder when ``directory_ids`` is None).

        Resolves the whole closure in ONE pass over a single fetch of all
        directories - O(folders), never per-row (UAC D2: no N+1). The path of a
        folder is its OWN chain root→self.
        """
        all_dirs = self.db.query(
            AttachmentDirectory.id,
            AttachmentDirectory.parent_id,
            AttachmentDirectory.name,
        ).all()
        by_id = {
            str(d.id): (str(d.parent_id) if d.parent_id else None, d.name or "")
            for d in all_dirs
        }
        cache: dict[str, str] = {}

        def _path(did: Optional[str]) -> str:
            if not did:
                return ""
            if did in cache:
                return cache[did]
            entry = by_id.get(did)
            if not entry:
                cache[did] = ""
                return ""
            parent_id, name = entry
            parent_path = _path(parent_id) if parent_id else ""
            full = f"{parent_path}{sep}{name}" if parent_path else name
            cache[did] = full
            return full

        targets = directory_ids if directory_ids is not None else list(by_id.keys())
        return {str(did): _path(str(did)) for did in targets if did}

    def get_deleted_tree(self) -> list:
        """Return tree of deleted directories only (for trash view). Roots are deleted dirs whose parent is not deleted (or has no parent)."""
        all_deleted = self.db.query(AttachmentDirectory).filter(
            AttachmentDirectory.is_deleted == True
        ).all()
        deleted_ids = {str(d.id) for d in all_deleted}
        deleted_by_parent: dict = {}
        for d in all_deleted:
            pid = str(d.parent_id) if d.parent_id else None
            deleted_by_parent.setdefault(pid, []).append(d)
        for pid, dirs in deleted_by_parent.items():
            dirs.sort(key=lambda x: (x.sort_order or 0, x.name or ""))

        def build_node(parent_key: Optional[str]) -> list:
            dirs = deleted_by_parent.get(parent_key, [])
            return [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "parent_id": str(d.parent_id) if d.parent_id else None,
                    "sort_order": d.sort_order,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    # Owning company (R14 / AC-E3), same as the live tree - no
                    # extra query, `d` is already the loaded row.
                    "company_id": str(d.company_id) if d.company_id else None,
                    "children": build_node(str(d.id)),
                }
                for d in dirs
            ]

        # Roots: deleted folders whose parent is None OR not in deleted set (parent not deleted)
        roots = [
            d for d in all_deleted
            if d.parent_id is None or str(d.parent_id) not in deleted_ids
        ]
        roots.sort(key=lambda x: (x.sort_order or 0, x.name or ""))
        return [
            {
                "id": str(d.id),
                "name": d.name,
                "parent_id": str(d.parent_id) if d.parent_id else None,
                "sort_order": d.sort_order,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "company_id": str(d.company_id) if d.company_id else None,
                "children": build_node(str(d.id)),
            }
            for d in roots
        ]

    def restore_directory(self, directory_id: str) -> dict:
        """Restore a directory, its descendants, and all attachments in them. Uses bulk updates for speed."""
        d = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == directory_id).first()
        if not d:
            raise handle_not_found("Attachment directory", directory_id)
        if not d.is_deleted:
            raise handle_conflict("Directory is not deleted.")
        dir_ids = self.get_descendant_directory_ids(directory_id, include_deleted=True)
        # Bulk update directories
        self.db.query(AttachmentDirectory).filter(
            AttachmentDirectory.id.in_(dir_ids),
            AttachmentDirectory.is_deleted == True,
        ).update(
            {
                AttachmentDirectory.is_deleted: False,
                AttachmentDirectory.deleted_at: None,
                AttachmentDirectory.deleted_by: None,
            },
            synchronize_session=False,
        )
        attachment_service = AttachmentService(self.db)
        restored = attachment_service.restore_attachments_in_directories(dir_ids)
        self.db.commit()
        return {"directories_restored": len(dir_ids), "attachments_restored": restored}

    def permanent_delete_directory(self, directory_id: str, deleted_by: str) -> dict:
        """Permanently delete a deleted directory, its descendants, and all attachments in them. Cannot be undone."""
        d = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == directory_id).first()
        if not d:
            raise handle_not_found("Attachment directory", directory_id)
        if not d.is_deleted:
            raise handle_conflict("Directory must be in Trash before permanent delete. Move to Trash first.")
        dir_ids = self.get_descendant_directory_ids(directory_id, include_deleted=True)
        attachment_service = AttachmentService(self.db)
        attachments = self.db.query(Attachment).filter(
            Attachment.directory_id.in_(dir_ids),
        ).all()
        attachment_ids = [str(a.id) for a in attachments]
        if attachment_ids:
            attachment_service.delete_attachments(attachment_ids, deleted_by)
        # Delete directories (leaf-first for parent_id FK)
        for did in reversed(dir_ids):
            child = self.db.query(AttachmentDirectory).filter(AttachmentDirectory.id == did).first()
            if child:
                self.db.delete(child)
        self.db.commit()
        return {"directories_deleted": len(dir_ids), "attachments_deleted": len(attachment_ids)}


class AttachmentTypeService:
    """Service for attachment type operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_types(self, page: int = 1, limit: int = 50, query: Optional[str] = None, sort: Optional[str] = None, dir: str = "desc"):
        """List attachment types with pagination and filtering."""
        from app.schemas.common import PaginationResponse
        
        q = self.db.query(AttachmentType)
        
        # Apply search filter
        if query:
            q = q.filter(
                (AttachmentType.type_name.ilike(f"%{query}%")) |
                (AttachmentType.description.ilike(f"%{query}%"))
            )
        
        # Apply sorting
        sort_column = None
        if sort:
            if sort == "type_name":
                sort_column = AttachmentType.type_name
            elif sort == "created_at":
                sort_column = AttachmentType.created_at
            elif sort == "max_file_size_mb":
                sort_column = AttachmentType.max_file_size_mb
        
        if sort_column:
            if dir == "desc":
                q = q.order_by(sort_column.desc())
            else:
                q = q.order_by(sort_column.asc())
        else:
            # Default sorting by created_at desc
            q = q.order_by(AttachmentType.created_at.desc())
        
        total = q.count()
        offset = (page - 1) * limit
        types = q.offset(offset).limit(limit).all()
        
        return {
            "data": types,
            "pagination": PaginationResponse(total=total, page=page, limit=limit),
            "empty": total == 0
        }
    
    def get_type(self, type_id: str):
        """Get an attachment type by ID."""
        attachment_type = self.db.query(AttachmentType).filter(AttachmentType.id == type_id).first()
        if not attachment_type:
            raise handle_not_found("Attachment Type", type_id)
        return attachment_type

    def get_type_by_code(self, code: str):
        """Get an attachment type by code, or None if not found."""
        if not (code or "").strip():
            return None
        return self.db.query(AttachmentType).filter(AttachmentType.code == code.strip()).first()
    
    def create_type(self, type_data: AttachmentTypeCreate):
        """Create a new attachment type."""
        existing = self.db.query(AttachmentType).filter(
            AttachmentType.type_name == type_data.type_name
        ).first()
        if existing:
            raise handle_conflict("Attachment type name already exists.")
        
        attachment_type = AttachmentType(**type_data.model_dump())
        self.db.add(attachment_type)
        self.db.commit()
        self.db.refresh(attachment_type)
        return attachment_type
    
    def update_type(self, type_id: str, type_data: AttachmentTypeUpdate):
        """Update an attachment type."""
        attachment_type = self.get_type(type_id)
        
        update_data = type_data.model_dump(exclude_unset=True)
        
        # Check for name uniqueness if type_name is being updated
        if "type_name" in update_data and update_data["type_name"] != attachment_type.type_name:
            existing = self.db.query(AttachmentType).filter(
                AttachmentType.type_name == update_data["type_name"]
            ).first()
            if existing:
                raise handle_conflict("Attachment type name already exists.")
        
        for key, value in update_data.items():
            setattr(attachment_type, key, value)
        
        self.db.commit()
        self.db.refresh(attachment_type)
        return attachment_type
    
    def delete_type(self, type_id: str):
        """Delete an attachment type."""
        attachment_type = self.get_type(type_id)
        self.db.delete(attachment_type)
        self.db.commit()
        return attachment_type


# Form/entity attachments stay company-less on purpose so they remain readable
# from every company (AC-G3). Everything else uploaded under one active company
# belongs to that company, folder or not.
_SHARED_FORM_ENTITY_TYPES = frozenset({"complaint", "purchase_request", "stock_inquiry"})


def _single_active_company(scope) -> Optional[str]:
    """The one company that may be stamped, or None. Only an unambiguous single
    active company counts; UNSET / all-companies / multi-company stay NULL rather
    than guess, because a wrong guess is worse than shared."""
    if isinstance(scope, frozenset) and len(scope) == 1:
        return next(iter(scope))
    return None


class AttachmentService:
    """Service for attachment operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_attachments(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort: Optional[str] = None,
        dir: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        is_deleted: Optional[bool] = None,
        attachment_type_id: Optional[str] = None,
        attachment_type_ids: Optional[list[str]] = None,
        attachment_type_code: Optional[str] = None,
        attachment_type_codes: Optional[list[str]] = None,
        mime_type: Optional[str] = None,
        mime_types: Optional[list[str]] = None,
        uploaded_by: Optional[str] = None,
        uploaded_at_from: Optional[Any] = None,
        uploaded_at_to: Optional[Any] = None,
        access_levels: Optional[List[str]] = None,
        access_levels_match: Optional[str] = "any",
        link_status: Optional[str] = None,
        storage_status: Optional[str] = None,
        entities: Optional[list[str]] = None,
        attachment_ids: Optional[list[str]] = None,
        direct_access_only: Optional[bool] = None,
        visible_attachment_type_ids: Optional[set[str]] = None,
        company: Optional[str] = None,
        contact_regions: Optional[frozenset] = None,
    ):
        """List attachments. Filter by directory_id when provided. Search by filename when query is provided. is_deleted=True returns trash.

        ``access_levels_match`` controls how multi-code filters combine:
      - ``any`` (default): row's access_levels overlap any selected code.
      - ``all``: row's access_levels contain every selected code (extras allowed).
      - ``exact``: row's access_levels equal the selected set.

        ``attachment_type_id`` / ``attachment_type_code`` are the singular forms;
        ``attachment_type_ids`` / ``attachment_type_codes`` take a list and union
        with their singular twin (OR within each pair, AND across the two pairs).

        ``mime_type`` / ``mime_types`` follow the same singular-plus-plural
        convention and filter on the FILE's own type, which is a different
        question from ``attachment_type_id`` (a document class: catalogue,
        certificate, ...). A picker that can only read PDFs asks this one.

        ``company``: ``"shared"`` narrows to ``company_id IS NULL`` only; a
        company UUID narrows to that company only (excludes shared rows);
        omitted keeps today's default (the session scope predicate: shared +
        the caller's own companies) (AC-E1).
        """
        from app.services.entity_filter_helpers import (
            attach_echo,
            empty_payload,
        )
        from app.schemas.common import PaginationResponse

        q, entity_buckets = self._build_list_query(
            query=query,
            sort=sort,
            dir=dir,
            entity_type=entity_type,
            entity_id=entity_id,
            directory_id=directory_id,
            is_deleted=is_deleted,
            attachment_type_id=attachment_type_id,
            attachment_type_ids=attachment_type_ids,
            attachment_type_code=attachment_type_code,
            attachment_type_codes=attachment_type_codes,
            mime_type=mime_type,
            mime_types=mime_types,
            uploaded_by=uploaded_by,
            uploaded_at_from=uploaded_at_from,
            uploaded_at_to=uploaded_at_to,
            access_levels=access_levels,
            access_levels_match=access_levels_match,
            link_status=link_status,
            storage_status=storage_status,
            entities=entities,
            attachment_ids=attachment_ids,
            direct_access_only=direct_access_only,
            visible_attachment_type_ids=visible_attachment_type_ids,
            company=company,
            contact_regions=contact_regions,
            with_joinedload=True,
        )
        if q is None:
            # entities filter resolved to an impossible set: short-circuit to an
            # empty payload (mirrors the original early-return contract).
            return empty_payload(entity_buckets, page=page, limit=limit)

        total = q.count()
        offset = (page - 1) * limit
        attachments = q.offset(offset).limit(limit).all()

        return attach_echo(
            {
                "data": attachments,
                "pagination": PaginationResponse(total=total, page=page, limit=limit),
                "empty": total == 0,
            },
            entity_buckets,
        )

    def company_name_map(self, attachments) -> dict:
        """``company_id`` -> company name for a page of attachment rows.

        ONE query per page, not per row: the serializers stamp the owning company
        on every attachment payload, and `Attachment` has no relationship to
        `Company` (the mixin only gives it the FK column), so without a batched
        lookup this would be an N+1 on the file library's busiest endpoint.

        Attachments are company-SHARED, so a NULL `company_id` is legitimate and
        simply contributes no entry. Best-effort: a failed lookup returns an empty
        map, which renders the row without a company rather than failing the list.
        """
        ids = {
            str(getattr(att, "company_id", None))
            for att in (attachments or [])
            if getattr(att, "company_id", None)
        }
        return self.company_name_map_for_ids(ids)

    def company_name_map_for_ids(self, company_ids) -> dict:
        """``company_id`` -> company name for an arbitrary set of ids.

        Same batched-lookup shape as ``company_name_map``, for callers (the
        linked-entity builder, S5) that already hold the ids rather than a
        page of attachment rows.
        """
        ids = {str(cid) for cid in (company_ids or []) if cid}
        if not ids:
            return {}
        try:
            from app.models.company import Company

            return {
                str(cid): name
                for cid, name in self.db.query(Company.id, Company.name)
                .filter(Company.id.in_(ids))
                .all()
            }
        except Exception:  # noqa: BLE001 - attribution is additive, never fatal
            logger.warning("Could not resolve company names for ids", exc_info=True)
            return {}

    def _resolve_attachment_type_code(self, code: str) -> Optional[str]:
        """Resolve one attachment-type code/name to its AttachmentType id.

        Lookup is permissive so the caller (MCP, n8n) does not need to know
        whether the type was seeded with `code` set or only `type_name`, what
        casing it uses, or whether the canonical label is "catalogue" vs
        "catalog":
          1. case-insensitive `code` match
          2. case-insensitive `type_name` match
          3. spelling variants (catalog / catalogue) tried against both
        Returns None when nothing matches.
        """
        code_norm = (code or "").strip()
        if not code_norm:
            return None
        variants = {code_norm}
        low = code_norm.lower()
        if low == "catalog":
            variants.add("catalogue")
        elif low == "catalogue":
            variants.add("catalog")
        for variant in variants:
            type_row = (
                self.db.query(AttachmentType)
                .filter(AttachmentType.code.ilike(variant))
                .first()
                or self.db.query(AttachmentType)
                .filter(AttachmentType.type_name.ilike(variant))
                .first()
            )
            if type_row is not None:
                return str(type_row.id)
        return None

    def _build_list_query(
        self,
        query: Optional[str] = None,
        sort: Optional[str] = None,
        dir: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        is_deleted: Optional[bool] = None,
        attachment_type_id: Optional[str] = None,
        attachment_type_ids: Optional[list[str]] = None,
        attachment_type_code: Optional[str] = None,
        attachment_type_codes: Optional[list[str]] = None,
        mime_type: Optional[str] = None,
        mime_types: Optional[list[str]] = None,
        uploaded_by: Optional[str] = None,
        uploaded_at_from: Optional[Any] = None,
        uploaded_at_to: Optional[Any] = None,
        access_levels: Optional[List[str]] = None,
        access_levels_match: Optional[str] = "any",
        link_status: Optional[str] = None,
        storage_status: Optional[str] = None,
        entities: Optional[list[str]] = None,
        attachment_ids: Optional[list[str]] = None,
        direct_access_only: Optional[bool] = None,
        visible_attachment_type_ids: Optional[set[str]] = None,
        company: Optional[str] = None,
        with_joinedload: bool = False,
        contact_regions: Optional[frozenset] = None,
    ):
        """Build the filtered + sorted attachments query shared by ``list_attachments``
        and ``neighbours`` so the two can never drift.

        Returns ``(query, entity_buckets)``. ``query`` is ``None`` when the
        ``entities`` free-text filter resolves to a definitely-empty set (the
        caller short-circuits to an empty payload). The ORDER BY always appends
        ``Attachment.id`` as a deterministic tie-breaker so offset position and
        prev/next neighbours are unambiguous when the primary sort column has
        equal values.
        """
        from sqlalchemy.orm import joinedload
        from sqlalchemy import cast, String
        from sqlalchemy.dialects.postgresql import JSONB, ARRAY
        from app.services.entity_filter_helpers import resolve_or_empty

        entity_buckets = resolve_or_empty(self.db, entities)
        if entity_buckets is not None and not (
            entity_buckets.attachment_filenames or entity_buckets.product_codes
        ):
            return None, entity_buckets

        q = self.db.query(Attachment)
        if with_joinedload:
            q = q.options(joinedload(Attachment.attachment_type))
        if attachment_ids:
            q = q.filter(Attachment.id.in_(attachment_ids))
        if entity_buckets is not None and entity_buckets.attachment_filenames:
            from sqlalchemy import or_ as _or
            terms = [f"%{f}%" for f in entity_buckets.attachment_filenames]
            q = q.filter(
                _or(*[Attachment.original_filename.ilike(t) for t in terms])
            )
        if is_deleted is not None:
            q = q.filter(Attachment.is_deleted == is_deleted)
        else:
            q = q.filter(Attachment.is_deleted == False)

        q = _apply_company_filter(q, Attachment, company)

        if storage_status and str(storage_status).strip():
            q = q.filter(Attachment.storage_status == str(storage_status).strip())

        if entity_type:
            q = q.filter(Attachment.entity_type == entity_type)
        if entity_id:
            q = q.filter(Attachment.entity_id == entity_id)
        if directory_id is not None:
            q = q.filter(Attachment.directory_id == directory_id)
        # Singular + plural type filters are unioned within each pair (OR), and the
        # id pair is ANDed with the code pair (passing an id and a mismatching code
        # still yields 0 rows, as it did when both were single-value filters).
        wanted_type_ids = {
            str(t).strip()
            for t in ([attachment_type_id] + list(attachment_type_ids or []))
            if t and str(t).strip()
        }
        if wanted_type_ids:
            q = q.filter(Attachment.attachment_type_id.in_(sorted(wanted_type_ids)))

        wanted_codes = [
            str(c).strip()
            for c in ([attachment_type_code] + list(attachment_type_codes or []))
            if c and str(c).strip()
        ]
        if wanted_codes:
            # Resolve each code → AttachmentType.id, then filter by those types.
            # No code resolves → impossible-id filter so the tool returns 0 rows
            # instead of silently dropping the filter (catalogue domain hint
            # must never leak non-catalogue attachments).
            resolved = {
                type_id
                for type_id in (self._resolve_attachment_type_code(c) for c in wanted_codes)
                if type_id
            }
            if not resolved:
                q = q.filter(Attachment.id == "00000000-0000-0000-0000-000000000000")
            else:
                q = q.filter(Attachment.attachment_type_id.in_(sorted(resolved)))

        # The FILE's type, not the document class above. Singular and plural union
        # exactly like the type pair, and both omitted leaves the query untouched -
        # this endpoint has many callers and none of them asked for a new filter.
        #
        # Compared case-insensitively and with any `;parameter` suffix dropped
        # from both sides: a mime type is case-insensitive per RFC 2045, and the
        # same PDF is recorded as `application/pdf` by one uploader and
        # `application/pdf; charset=binary` by another. A caller asking for
        # `application/pdf` means both, and a picker that missed the second would
        # hide a perfectly readable file.
        wanted_mimes = {
            str(m).split(";")[0].strip().lower()
            for m in ([mime_type] + list(mime_types or []))
            if m and str(m).split(";")[0].strip()
        }
        if wanted_mimes:
            base_mime = func.lower(
                func.trim(func.split_part(Attachment.mime_type, ";", 1))
            )
            q = q.filter(base_mime.in_(sorted(wanted_mimes)))
        if contact_regions is not None:
            # A contact never sees the file of a packing list outside its regions. A file is
            # linked to a shipment through `inbound_shipments.attachment_id` OR an
            # `entity_attachment_links` row; the linked shipments' regions decide. An
            # unlinked file falls back to its own `regions` (NULL = visible). Core tables:
            # the company scope must not make a shipment of another company read as unlinked.
            from sqlalchemy import Text, cast, exists, select
            from app.models.entity_attachment import EntityAttachmentLink
            from app.models.procurement import InboundShipment

            ship = InboundShipment.__table__
            link = EntityAttachmentLink.__table__
            via_link = exists(
                select(link.c.id).where(
                    link.c.attachment_id == Attachment.id,
                    link.c.entity_type == "inbound_shipment",
                    link.c.entity_id == cast(ship.c.id, Text),
                ).correlate_except(link)
            )
            linked = (
                select(ship.c.id)
                .where(or_(ship.c.attachment_id == Attachment.id, via_link))
                .correlate_except(ship)
            )
            visible = linked.where(ship.c.regions.overlap(sorted(contact_regions)))
            own = or_(
                Attachment.regions.is_(None),
                Attachment.regions.overlap(sorted(contact_regions)),
            )
            q = q.filter(or_(exists(visible), and_(~exists(linked), own)))
        if direct_access_only:
            if visible_attachment_type_ids is not None:
                # A contact was resolved and holds per-contact grants, so the
                # visible set is the direct-access baseline WIDENED by those
                # grants (see contact_attachment_access). Never narrower: the
                # caller already has the baseline today.
                q = q.filter(
                    Attachment.attachment_type_id.in_(list(visible_attachment_type_ids))
                )
            else:
                # Restrict to attachment types flagged is_direct_access. A subquery
                # avoids depending on whether AttachmentType is already joined.
                direct_type_ids = self.db.query(AttachmentType.id).filter(
                    AttachmentType.is_direct_access.is_(True)
                )
                q = q.filter(Attachment.attachment_type_id.in_(direct_type_ids))
        if uploaded_by:
            q = q.filter(Attachment.uploaded_by == uploaded_by)
        if uploaded_at_from is not None:
            q = q.filter(Attachment.uploaded_at >= uploaded_at_from)
        if uploaded_at_to is not None:
            q = q.filter(Attachment.uploaded_at <= uploaded_at_to)
        if access_levels:
            cleaned = sorted({lvl for lvl in access_levels if lvl and lvl.strip()})
            if cleaned:
                mode = (access_levels_match or "any").strip().lower()
                if mode == "exact":
                    payload = cast(cleaned, JSONB)
                    q = q.filter(
                        Attachment.access_levels.op('@>')(payload),
                        payload.op('@>')(Attachment.access_levels),
                    )
                elif mode == "all":
                    payload = cast(cleaned, JSONB)
                    q = q.filter(Attachment.access_levels.op('@>')(payload))
                else:
                    q = q.filter(
                        Attachment.access_levels.op('?|')(
                            cast(cleaned, ARRAY(String))
                        )
                    )
        if query and query.strip():
            term = f"%{query.strip()}%"
            q = q.filter(
                or_(
                    Attachment.original_filename.ilike(term),
                    Attachment.description.ilike(term),
                )
            )

        if link_status in ("linked", "unlinked"):
            from sqlalchemy import exists, and_ as _and, not_ as _not
            from app.models.product import ProductAttachment
            from app.models.marketing import PromotionAttachment
            from app.models.forms import Form
            from app.models.procurement import InboundShipment
            from app.models.entity_attachment import EntityAttachmentLink
            from app.models.attachment_field_link import AttachmentFieldLink

            # An attachment counts as "linked" if it is referenced from ANY of the
            # entity↔attachment join tables, a direct attachment_id FK, the generic
            # polymorphic link table, the field-level link table, or carries a legacy
            # entity_type/entity_id direct association.
            any_link = or_(
                exists().where(ProductAttachment.attachment_id == Attachment.id),
                exists().where(PromotionAttachment.attachment_id == Attachment.id),
                exists().where(Form.attachment_id == Attachment.id),
                exists().where(InboundShipment.attachment_id == Attachment.id),
                exists().where(EntityAttachmentLink.attachment_id == Attachment.id),
                exists().where(AttachmentFieldLink.attachment_id == Attachment.id),
                _and(Attachment.entity_type.isnot(None), Attachment.entity_id.isnot(None)),
            )
            q = q.filter(any_link if link_status == "linked" else _not(any_link))

        sort_desc = (dir or "desc").lower() == "desc"
        sort_alias = {
            "name": "original_filename",
            "type": "mime_type",
            "size": "file_size_bytes",
        }
        # Every ORDER BY appends ``Attachment.id`` as a deterministic tie-breaker
        # so offset position and prev/next neighbours are stable when the primary
        # sort column has equal values.
        if sort == "attachment_type":
            # AttachmentType already imported at module top; redundant local
            # import here shadowed the name and caused UnboundLocalError when
            # any earlier branch (e.g. attachment_type_code resolver) touched it.
            q = q.outerjoin(AttachmentType, Attachment.attachment_type_id == AttachmentType.id)
            order_col = AttachmentType.type_name
            q = q.order_by(
                order_col.desc() if sort_desc else order_col.asc(),
                Attachment.id.asc(),
            )
        elif sort:
            sort_key = sort_alias.get(sort, sort)
            sort_col = getattr(Attachment, sort_key, None)
            if sort_col is not None:
                q = q.order_by(
                    sort_col.desc() if sort_desc else sort_col.asc(),
                    Attachment.id.asc(),
                )
            else:
                q = q.order_by(
                    Attachment.sort_order.asc().nulls_last(),
                    Attachment.uploaded_at.desc(),
                    Attachment.id.asc(),
                )
        else:
            q = q.order_by(
                Attachment.sort_order.asc().nulls_last(),
                Attachment.uploaded_at.desc(),
                Attachment.id.asc(),
            )

        return q, entity_buckets

    # ------------------------------------------------------------------
    # Unified Drive (folders + files, one server-sorted/paginated stream).
    # docs/plans/PLAN-unified-drive-files.md (D6/D7/D10/D11) + UAC A8/B/C/D.
    # ------------------------------------------------------------------

    # Non-Name sorts have no folder-comparable value → folders are grouped FIRST
    # (Finder/macOS behavior): all folders at the top, name-asc among themselves,
    # then the files sorted by the requested column+direction.
    _DRIVE_FILE_ONLY_SORTS = {
        "type", "mime_type", "size", "file_size_bytes",
        "modified", "uploaded_at", "created_at",
        "uploaded_by", "attachment_type",
    }

    def _drive_filters_active(
        self,
        *,
        attachment_type_id: Optional[str],
        attachment_type_code: Optional[str],
        uploaded_by: Optional[str],
        uploaded_at_from: Optional[Any],
        uploaded_at_to: Optional[Any],
        access_levels: Optional[List[str]],
        link_status: Optional[str],
        storage_status: Optional[str],
    ) -> bool:
        """True when any FILE-ATTRIBUTE filter is set.

        Folders carry none of these attributes, so any such filter hides folders
        from the Drive listing (UAC C3 - "they can't match"). A plain text
        ``query`` is NOT a hide-folders trigger: folder NAMES are matched against
        it, so matching folders still appear in recursive search results (UAC B4 /
        plan D5). Plain browse (no filter) shows folders (UAC C4).
        """
        if attachment_type_id or (attachment_type_code and attachment_type_code.strip()):
            return True
        if uploaded_by:
            return True
        if uploaded_at_from is not None or uploaded_at_to is not None:
            return True
        if access_levels and any((lvl or "").strip() for lvl in access_levels):
            return True
        if link_status in ("linked", "unlinked"):
            return True
        if storage_status and str(storage_status).strip():
            return True
        return False

    def list_drive_contents(
        self,
        *,
        directory_id: Optional[str] = None,
        recursive: bool = False,
        query: Optional[str] = None,
        sort: Optional[str] = None,
        dir: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        is_deleted: Optional[bool] = None,
        attachment_type_id: Optional[str] = None,
        attachment_type_code: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        uploaded_at_from: Optional[Any] = None,
        uploaded_at_to: Optional[Any] = None,
        access_levels: Optional[List[str]] = None,
        access_levels_match: Optional[str] = "any",
        link_status: Optional[str] = None,
        storage_status: Optional[str] = None,
        direct_access_only: Optional[bool] = None,
        visible_attachment_type_ids: Optional[set[str]] = None,
        company: Optional[str] = None,
    ) -> dict:
        """Unified Drive listing: discriminated folder + file rows in ONE
        server-sorted, server-paginated stream.

        Scope:
          * ``recursive`` (or a non-empty ``query``) → current folder + ALL
            descendant subfolders (UAC B2/B3). Root recursive = whole drive.
          * otherwise → immediate children only: subfolders WHERE
            ``parent_id == directory_id`` + files WHERE
            ``directory_id == directory_id`` (root = ``parent_id IS NULL`` +
            ``directory_id IS NULL``) (UAC B1/A8/D10).

        Folders are HIDDEN when any file-attribute filter is set or a query is
        present (UAC C3); shown on plain browse (UAC C4). Sort: Name interleaves
        folders+files as one set (UAC C1); any non-Name sort groups folders FIRST
        (Finder/macOS), name-asc among themselves, then files sorted by the chosen
        column/direction (UAC C2). Pagination is computed over the combined (UNION) count
        so it is correct at any folder size with no duplicate/missing rows across
        pages (UAC C6).

        ``company`` applies to BOTH sides of the stream (folders and files),
        same values as ``list_attachments`` (AC-E1).
        """
        from sqlalchemy import literal, func as _sa_func
        from app.schemas.common import PaginationResponse

        dir_service = AttachmentDirectoryService(self.db)
        trash = is_deleted is True
        recursive = bool(recursive) or bool(query and query.strip())
        normalized_dir = (str(directory_id).strip() or None) if directory_id else None

        filters_active = self._drive_filters_active(
            attachment_type_id=attachment_type_id,
            attachment_type_code=attachment_type_code,
            uploaded_by=uploaded_by,
            uploaded_at_from=uploaded_at_from,
            uploaded_at_to=uploaded_at_to,
            access_levels=access_levels,
            link_status=link_status,
            storage_status=storage_status,
        )
        # Folders are shown only on a plain browse (no filters, no query). C3/C4.
        include_folders = not filters_active

        # ---- Resolve the file directory scope --------------------------------
        descendant_ids: Optional[List[str]] = None
        if recursive and normalized_dir:
            descendant_ids = dir_service.get_descendant_directory_ids_portable(
                normalized_dir, include_deleted=trash
            )
            if normalized_dir not in descendant_ids:
                descendant_ids.append(normalized_dir)

        # ---- File side -------------------------------------------------------
        file_q, _ = self._build_list_query(
            query=query,
            sort=None,  # ordering applied on the UNION below
            dir=dir,
            directory_id=None,  # scoped manually for recursive/exact below
            is_deleted=is_deleted,
            attachment_type_id=attachment_type_id,
            attachment_type_code=attachment_type_code,
            uploaded_by=uploaded_by,
            uploaded_at_from=uploaded_at_from,
            uploaded_at_to=uploaded_at_to,
            access_levels=access_levels,
            access_levels_match=access_levels_match,
            link_status=link_status,
            storage_status=storage_status,
            direct_access_only=direct_access_only,
            visible_attachment_type_ids=visible_attachment_type_ids,
            company=company,
        )
        if recursive:
            if normalized_dir:
                if descendant_ids:
                    file_q = file_q.filter(Attachment.directory_id.in_(descendant_ids))
                else:
                    file_q = file_q.filter(literal(False))
            # recursive at root => whole drive: no directory filter
        else:
            if normalized_dir:
                file_q = file_q.filter(Attachment.directory_id == normalized_dir)
            else:
                file_q = file_q.filter(Attachment.directory_id.is_(None))

        # ---- Folder side -----------------------------------------------------
        folder_ids: List[str] = []
        if include_folders:
            folder_q = self.db.query(AttachmentDirectory).filter(
                AttachmentDirectory.is_deleted == (True if trash else False)
            )
            if recursive:
                if normalized_dir:
                    # Descendants of the current folder (exclude the folder itself).
                    sub_ids = [d for d in (descendant_ids or []) if d != normalized_dir]
                    if sub_ids:
                        folder_q = folder_q.filter(AttachmentDirectory.id.in_(sub_ids))
                    else:
                        folder_q = folder_q.filter(literal(False))
                # recursive at root => all folders
            else:
                if normalized_dir:
                    folder_q = folder_q.filter(AttachmentDirectory.parent_id == normalized_dir)
                else:
                    folder_q = folder_q.filter(AttachmentDirectory.parent_id.is_(None))
            if query and query.strip():
                folder_q = folder_q.filter(
                    AttachmentDirectory.name.ilike(f"%{query.strip()}%")
                )
            folder_q = _apply_company_filter(folder_q, AttachmentDirectory, company)
            folders = folder_q.all()
            folder_ids = [str(f.id) for f in folders]
            folder_rows = {str(f.id): f for f in folders}
        else:
            folders = []
            folder_rows = {}

        # ---- Combined ordering key (interleave vs folders-first) --------------
        sort_key = (sort or "").strip().lower()
        sort_desc = (dir or "asc").lower() == "desc"
        folders_first = bool(sort_key) and sort_key in self._DRIVE_FILE_ONLY_SORTS

        # Pull lightweight (id, sort_value, name) tuples for both kinds, order in
        # Python over the small combined keyset, THEN page. The keyset is the
        # union of folder ids + file ids - pagination is over the true combined
        # set so no row is duplicated or dropped across pages (UAC C6). Full rows
        # are fetched only for the requested page.
        file_sort_attr = {
            "name": Attachment.original_filename,
            "type": Attachment.mime_type,
            "mime_type": Attachment.mime_type,
            "size": Attachment.file_size_bytes,
            "file_size_bytes": Attachment.file_size_bytes,
            "modified": Attachment.uploaded_at,
            "uploaded_at": Attachment.uploaded_at,
            "created_at": Attachment.created_at,
            "uploaded_by": Attachment.uploaded_by,
        }.get(sort_key, Attachment.original_filename)

        file_keys = file_q.with_entities(
            Attachment.id,
            file_sort_attr,
            Attachment.original_filename,
        ).all()

        # Numeric/date sorts need a type-consistent key; string sorts lower-case.
        numeric_sort = sort_key in {"size", "file_size_bytes"}

        # "Uploaded By" sorts on the uploader's DISPLAY NAME, not the raw UUID - 
        # sorting by UUID is meaningless AND the column returns a mix of UUID
        # objects / strings / None that raise "'<' not supported between 'UUID'
        # and 'str'" when compared. Batch-resolve id -> name up front.
        uploaded_by_names: dict[str, str] = {}
        if sort_key == "uploaded_by":
            from app.models.user import User
            uids = {str(r[1]) for r in file_keys if r[1]}
            if uids:
                for u in self.db.query(User.id, User.name, User.email).filter(User.id.in_(uids)).all():
                    uploaded_by_names[str(u.id)] = (
                        (u.name or "").strip() or (u.email or "") or str(u.id)
                    ).lower()

        def _norm(v):
            if isinstance(v, str):
                return v.lower()
            return v

        def _file_primary(v):
            # Coerce None to a type-consistent floor so files with a missing value
            # sort first (asc) without raising on mixed None/number/str compares.
            if v is None:
                return float("-inf") if numeric_sort else ""
            if sort_key == "uploaded_by":
                # Resolved uploader name (empty string for unknown/unresolved ids).
                return uploaded_by_names.get(str(v), "")
            if isinstance(v, str):
                return v.lower()
            if numeric_sort:
                return v
            # Any other non-string, non-numeric value (e.g. a UUID) - stringify so
            # the sort key stays type-consistent and never raises on mixed compares.
            return str(v).lower()

        # Build a homogeneous sortable list of (kind, id, primary, name).
        # For Name sort the primary is the lower-cased name (folders interleave);
        # for non-Name sorts folders are split out and ordered by name alone, so
        # their primary is unused.
        combined: list[tuple[str, str, Any, str]] = []
        for fid in folder_ids:
            fr = folder_rows[fid]
            name = (fr.name or "")
            combined.append(("folder", fid, name.lower(), name.lower()))
        for row in file_keys:
            name = (row[2] or "")
            primary = name.lower() if not folders_first else _file_primary(row[1])
            combined.append(("file", str(row[0]), primary, name.lower()))

        total = len(combined)

        if folders_first:
            # Finder/macOS behavior: all folders grouped at the TOP (name-asc among
            # themselves, regardless of direction), then files ordered by the
            # chosen non-Name key + direction. Folders fill the first page(s)
            # before any file, so the grouping holds across pagination (C2/C6).
            files_part = [c for c in combined if c[0] == "file"]
            folders_part = [c for c in combined if c[0] == "folder"]
            files_part.sort(key=lambda c: (c[2], c[3]), reverse=sort_desc)
            folders_part.sort(key=lambda c: c[3])
            ordered = folders_part + files_part
        else:
            # Name sort: fully interleaved across folders+files (C1). Folder-
            # before-file on exact-name ties for determinism.
            ordered = sorted(
                combined,
                key=lambda c: (c[2], c[3], 0 if c[0] == "folder" else 1),
                reverse=sort_desc,
            )

        offset = (page - 1) * limit
        page_slice = ordered[offset: offset + limit]

        # ---- Hydrate the requested page only ---------------------------------
        page_file_ids = [c[1] for c in page_slice if c[0] == "file"]
        page_folder_ids = [c[1] for c in page_slice if c[0] == "folder"]

        file_map: dict[str, Attachment] = {}
        if page_file_ids:
            from sqlalchemy.orm import joinedload
            rows = (
                self.db.query(Attachment)
                .options(joinedload(Attachment.attachment_type))
                .filter(Attachment.id.in_(page_file_ids))
                .all()
            )
            file_map = {str(r.id): r for r in rows}

        # Path map: resolve folder paths for the file rows' directories AND for
        # folder rows themselves (folder Location = its parent's path). One pass.
        path_map = dir_service.build_directory_path_map()

        items: list[dict] = []
        for kind, item_id, _primary, _name in page_slice:
            if kind == "folder":
                fr = folder_rows.get(item_id)
                if fr is None:
                    continue
                parent_id = str(fr.parent_id) if fr.parent_id else None
                items.append({
                    "kind": "folder",
                    "id": str(fr.id),
                    "name": fr.name,
                    "parent_id": parent_id,
                    "sort_order": fr.sort_order,
                    "created_at": fr.created_at,
                    "directory_path": path_map.get(parent_id) if parent_id else None,
                    # Owning company (R14 / AC-E3): a folder is __company_shared__
                    # same as an attachment, so NULL is legitimate - the route
                    # resolves the name from this id, same convention as file rows.
                    "company_id": str(fr.company_id) if fr.company_id else None,
                })
            else:
                att = file_map.get(item_id)
                if att is None:
                    continue
                items.append({
                    "kind": "file",
                    "attachment": att,
                    "directory_path": (
                        path_map.get(str(att.directory_id)) if att.directory_id else None
                    ),
                })

        return {
            "items": items,
            "total": total,
            "pagination": PaginationResponse(total=total, page=page, limit=limit),
            "empty": total == 0,
            "recursive": recursive,
        }

    def _get_attachment_any(self, attachment_id: str):
        """Get attachment by ID (active or archived)."""
        from sqlalchemy.orm import joinedload
        attachment = self.db.query(Attachment).options(
            joinedload(Attachment.attachment_type)
        ).filter(Attachment.id == attachment_id).first()
        if not attachment:
            raise handle_not_found("Attachment", attachment_id)
        return attachment

    def get_attachment(self, attachment_id: str):
        """Get an attachment by ID (active or archived)."""
        return self._get_attachment_any(attachment_id)

    def _active_scope_company_id(self) -> Optional[str]:
        """The caller's single active company, or None (multi/all-companies scope)."""
        from app.models.base import get_company_scope

        scope = get_company_scope(self.db)
        if isinstance(scope, frozenset) and len(scope) == 1:
            return next(iter(scope))
        return None

    def get_linked_entities(
        self,
        attachment_id: str,
        actor_id: Optional[str] = None,
        *,
        company_id: Any = _COMPANY_ID_NOT_GIVEN,
    ) -> dict:
        """
        Resolve linked product(s), promotion(s), and form from product_attachments,
        promotion_attachments, and forms.attachment_id.
        Returns dict with linked_products, linked_promotions, linked_form.

        On a SHARED attachment (``company_id`` NULL), the product/certificate
        link queries run under the CALLER's own granted companies rather than
        just their single active company, so a shared file's popup shows both
        twins (PLAN-shared-brand-attachments.md S5, UAC group G). Only kicks in
        when ``actor_id`` is passed - callers that don't (upload-activity's
        summary builder) keep today's single-scope result. A single-company
        attachment is never widened, so its payload is unchanged (AC-G3).

        ``company_id`` lets a caller that already holds the attachment ORM
        row (``_attachment_response_with_linked_entities``) pass its
        ``company_id`` straight through instead of this method re-querying
        ``Attachment`` for it. Left at the sentinel default, this method
        looks it up itself - the shape upload-activity's summary builder,
        which only ever has an id, still relies on.
        """
        from app.models.product import ProductAttachment, Product
        from app.models.marketing import PromotionAttachment, Promotion
        from app.models.forms import Form
        from app.models.base import company_scope

        active_company_id = self._active_scope_company_id()

        if company_id is _COMPANY_ID_NOT_GIVEN:
            att_row = (
                self.db.query(Attachment.company_id)
                .filter(Attachment.id == attachment_id)
                .first()
            )
            is_shared = bool(att_row) and att_row[0] is None
        else:
            is_shared = company_id is None

        widen_scope: Optional[frozenset] = None
        if is_shared and actor_id:
            from app.services.company_scope_resolver import resolve_user_grant_ids

            grants = {str(g) for g in resolve_user_grant_ids(self.db, actor_id)}
            if grants:
                widen_scope = frozenset(grants)

        def _widened(fn):
            if widen_scope:
                with company_scope(self.db, widen_scope):
                    return fn()
            return fn()

        def _query_products():
            q = (
                self.db.query(
                    Product.id,
                    Product.product_name,
                    Product.description,
                    Product.company_id,
                    ProductAttachment.id.label("link_id"),
                )
                .join(ProductAttachment, ProductAttachment.product_id == Product.id)
                .filter(ProductAttachment.attachment_id == attachment_id)
            )
            # Only the widened (shared-attachment) branch orders explicitly -
            # a single-company attachment's row order is UNCHANGED from
            # before this slice (AC-G3), which had no order_by here at all.
            if widen_scope:
                q = q.order_by(Product.company_id, Product.product_name)
            return q.all()

        product_rows = _widened(_query_products)

        # Company names for whatever companies actually showed up, in ONE
        # query - never per row.
        company_ids_seen = {
            str(row.company_id) for row in product_rows if row.company_id
        }

        def _in_scope(company_id: Optional[str]) -> bool:
            # No company on the row, or the caller's scope can't name a single
            # active company (admin/API-key): never mark it out-of-scope.
            return (
                company_id is None
                or active_company_id is None
                or company_id == active_company_id
            )

        linked_products = []
        for row in product_rows:
            cid = str(row.company_id) if row.company_id else None
            linked_products.append({
                "id": str(row.id),
                "name": row.product_name or str(row.id),
                "description": (row.description or "").strip() or None,
                "link_id": str(row.link_id),
                "company_id": cid,
                "in_scope": _in_scope(cid),
            })

        linked_promotions = []
        q = (
            self.db.query(
                Promotion.id,
                Promotion.description,
                PromotionAttachment.id.label("link_id"),
            )
            .join(PromotionAttachment, PromotionAttachment.promotion_id == Promotion.id)
            .filter(PromotionAttachment.attachment_id == attachment_id)
        )
        for row in q.all():
            desc = (row.description or "").strip()
            linked_promotions.append({
                "id": str(row.id),
                "name": desc or f"Promotion {str(row.id)[:8]}",
                "description": desc or None,
                "link_id": str(row.link_id),
            })

        linked_form = None
        form_row = (
            self.db.query(Form.id, Form.name, Form.purpose)
            .filter(Form.attachment_id == attachment_id)
            .first()
        )
        if form_row:
            linked_form = {
                "id": str(form_row.id),
                "name": form_row.name or str(form_row.id),
                "description": (form_row.purpose or "").strip() or None,
            }

        linked_packing_lists = []
        from sqlalchemy import cast, String
        from app.models.entity_attachment import EntityAttachmentLink
        from app.models.procurement import InboundShipment
        # 1) Packing lists linked via entity_attachment_links (e.g. from "Link" in attachment modal)
        q = (
            self.db.query(
                InboundShipment.id,
                InboundShipment.shipment_number,
                InboundShipment.shipping_container_number,
                InboundShipment.notes,
                EntityAttachmentLink.id.label("link_id"),
            )
            .join(
                InboundShipment,
                EntityAttachmentLink.entity_id == cast(InboundShipment.id, String),
            )
            .filter(
                EntityAttachmentLink.attachment_id == attachment_id,
                EntityAttachmentLink.entity_type == "inbound_shipment",
            )
        )
        seen_pl_ids = set()
        for row in q.all():
            seen_pl_ids.add(str(row.id))
            linked_packing_lists.append({
                "id": str(row.id),
                # Container number is the shipment's secondary identifier; fall back to
                # it (never the raw UUID) when the shipment_number is not yet known.
                "name": (
                    row.shipment_number
                    or row.shipping_container_number
                    or str(row.id)
                ).strip(),
                "description": (row.notes or "").strip() or None,
                "link_id": str(row.link_id),
            })
        # 2) Packing lists that reference this attachment via InboundShipment.attachment_id (e.g. from external API)
        #    so attachment detail shows the same linkage the packing list detail shows
        direct = (
            self.db.query(
                InboundShipment.id,
                InboundShipment.shipment_number,
                InboundShipment.shipping_container_number,
                InboundShipment.notes,
            )
            .filter(InboundShipment.attachment_id == attachment_id)
        )
        for row in direct.all():
            pl_id = str(row.id)
            if pl_id in seen_pl_ids:
                continue
            seen_pl_ids.add(pl_id)
            linked_packing_lists.append({
                "id": pl_id,
                # Container number is the shipment's secondary identifier; fall back to
                # it (never the raw UUID) when the shipment_number is not yet known.
                "name": (
                    row.shipment_number
                    or row.shipping_container_number
                    or pl_id
                ).strip(),
                "description": (row.notes or "").strip() or None,
                "link_id": None,
            })

        # Certificates. Unlike the four above, this linkage is not a join table a
        # user maintains: the file IS a revision of the certificate, so the link
        # exists because the document was filed. The FE renders it read-only for
        # that reason - unlinking here would leave a revision with no document.
        # A list, not a single ref: the same PDF can be filed under two
        # identities (PPS and SPAN both issue against one document).
        from app.models.certificate import Certificate, CertificateRevision

        def _query_certificates():
            return (
                self.db.query(
                    Certificate.id,
                    Certificate.scheme,
                    Certificate.certificate_number,
                    Certificate.certifying_body,
                    Certificate.title,
                    Certificate.company_id,
                    CertificateRevision.id.label("link_id"),
                    CertificateRevision.revision_no,
                    CertificateRevision.is_current,
                )
                .join(CertificateRevision, CertificateRevision.certificate_id == Certificate.id)
                .filter(CertificateRevision.attachment_id == attachment_id)
                .order_by(CertificateRevision.revision_no.desc())
                .all()
            )

        certificate_rows = _widened(_query_certificates)
        company_ids_seen |= {
            str(row.company_id) for row in certificate_rows if row.company_id
        }

        company_names = self.company_name_map_for_ids(company_ids_seen)
        for product in linked_products:
            cid = product["company_id"]
            product["company_name"] = company_names.get(cid) if cid else None

        linked_certificates = []
        for row in certificate_rows:
            name = " ".join(p for p in (row.scheme, row.certificate_number) if p).strip()
            # Say WHICH issue this file is, so a superseded document is not
            # mistaken for the live certificate when read from the attachment.
            issue = f"Revision {row.revision_no}"
            if not row.is_current:
                issue += " (superseded)"
            subject = (row.title or "").strip() or (row.certifying_body or "").strip()
            cid = str(row.company_id) if row.company_id else None
            linked_certificates.append({
                "id": str(row.id),
                "name": name or str(row.id),
                "description": " - ".join(p for p in (subject, issue) if p) or None,
                "link_id": str(row.link_id),
                "company_id": cid,
                "company_name": company_names.get(cid) if cid else None,
                "in_scope": _in_scope(cid),
            })

        return {
            "linked_products": linked_products,
            "linked_promotions": linked_promotions,
            "linked_form": linked_form,
            "linked_packing_lists": linked_packing_lists,
            "linked_certificates": linked_certificates,
        }

    def list_attachment_ids_in_directory_subtree(self, root_directory_id: str) -> List[str]:
        """Non-deleted attachments in root folder and all non-deleted descendant folders."""
        dir_service = AttachmentDirectoryService(self.db)
        dir_ids = dir_service.get_descendant_directory_ids(root_directory_id, include_deleted=False)
        if not dir_ids:
            return []
        rows = (
            self.db.query(Attachment.id)
            .filter(
                Attachment.directory_id.in_(dir_ids),
                Attachment.is_deleted == False,
            )
            .all()
        )
        return [str(r.id) for r in rows]

    def resolve_bulk_attachment_ids(
        self,
        attachment_ids: Optional[List[str]],
        directory_id: Optional[str],
    ) -> List[str]:
        """Normalize scope to a unique list of attachment UUID strings."""
        if directory_id and str(directory_id).strip():
            did = str(directory_id).strip()
            AttachmentDirectoryService(self.db).get_directory(did, include_deleted=False)
            return self.list_attachment_ids_in_directory_subtree(did)
        ids = [str(a) for a in (attachment_ids or []) if a]
        return list(dict.fromkeys(ids))

    def preview_access_propagation(self, attachment_ids: List[str]) -> dict:
        """Deduplicated linked entities (product / promotion / form / packing list) that have propagatable access_levels."""
        unique_aids = list(dict.fromkeys([a for a in (attachment_ids or []) if a]))
        if not unique_aids:
            return {"attachment_count": 0, "targets": []}

        from sqlalchemy import cast as sql_cast, String
        from app.models.product import Product, ProductAttachment
        from app.models.marketing import Promotion, PromotionAttachment
        from app.models.forms import Form
        from app.models.procurement import InboundShipment
        from app.models.entity_attachment import EntityAttachmentLink

        targets: List[dict] = []
        seen = set()

        q = (
            self.db.query(Product.id, Product.product_code, Product.product_name)
            .join(ProductAttachment, ProductAttachment.product_id == Product.id)
            .filter(ProductAttachment.attachment_id.in_(unique_aids))
            .distinct()
        )
        for row in q.all():
            key = ("product", str(row.id))
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "kind": "product",
                "entity_id": str(row.id),
                "code": row.product_code,
                "name": row.product_name or None,
            })

        q = (
            self.db.query(Promotion.id, Promotion.description)
            .join(PromotionAttachment, PromotionAttachment.promotion_id == Promotion.id)
            .filter(PromotionAttachment.attachment_id.in_(unique_aids))
            .distinct()
        )
        for row in q.all():
            key = ("promotion", str(row.id))
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "kind": "promotion",
                "entity_id": str(row.id),
                "code": None,
                "name": (row.description or "").strip() or f"Promotion {str(row.id)[:8]}",
            })

        q = self.db.query(Form.id, Form.code, Form.name).filter(Form.attachment_id.in_(unique_aids))
        for row in q.all():
            key = ("form", str(row.id))
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "kind": "form",
                "entity_id": str(row.id),
                "code": row.code,
                "name": row.name or None,
            })

        q = (
            self.db.query(InboundShipment.id, InboundShipment.shipment_number)
            .join(
                EntityAttachmentLink,
                EntityAttachmentLink.entity_id == sql_cast(InboundShipment.id, String),
            )
            .filter(
                EntityAttachmentLink.attachment_id.in_(unique_aids),
                EntityAttachmentLink.entity_type == "inbound_shipment",
            )
            .distinct()
        )
        for row in q.all():
            key = ("packing_list", str(row.id))
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "kind": "packing_list",
                "entity_id": str(row.id),
                "code": row.shipment_number,
                "name": None,
            })

        direct = self.db.query(InboundShipment.id, InboundShipment.shipment_number).filter(
            InboundShipment.attachment_id.in_(unique_aids)
        )
        for row in direct.all():
            key = ("packing_list", str(row.id))
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "kind": "packing_list",
                "entity_id": str(row.id),
                "code": row.shipment_number,
                "name": None,
            })

        kind_order = {"product": 0, "promotion": 1, "form": 2, "packing_list": 3}
        targets.sort(key=lambda t: (kind_order.get(t["kind"], 9), t["code"] or ""))

        return {"attachment_count": len(unique_aids), "targets": targets}

    def bulk_set_attachment_type(
        self,
        attachment_ids: List[str],
        attachment_type_id: str,
    ) -> dict:
        """Set attachment_type_id on N attachments. No webhook side-effect.

        Used by single-edit (one ID in the list) and bulk-edit toolbar.
        """
        unique_aids = list(dict.fromkeys([a for a in attachment_ids if a]))
        if not unique_aids:
            raise handle_validation_error("No attachments in scope.")

        # Validate the type exists.
        type_id = (attachment_type_id or "").strip()
        if not type_id:
            raise handle_validation_error("attachment_type_id is required.")
        type_row = (
            self.db.query(AttachmentType)
            .filter(AttachmentType.id == type_id)
            .first()
        )
        if not type_row:
            raise handle_validation_error("Invalid attachment_type_id.")

        count = (
            self.db.query(Attachment)
            .filter(Attachment.id.in_(unique_aids), Attachment.is_deleted == False)
            .update({"attachment_type_id": type_id}, synchronize_session=False)
        )
        self.db.commit()
        return {
            "updated_attachments": int(count or 0),
            "attachment_type_id": type_id,
        }

    def apply_bulk_access_levels(
        self,
        attachment_ids: List[str],
        access_levels: List[str],
        propagate_to_linked: bool,
    ) -> dict:
        """Set access_levels on attachments; optionally cascade to linked product links, promotions, forms, packing lists."""
        from sqlalchemy import update
        from app.services.contact_access_type_service import ContactAccessTypeService
        from app.models.product import ProductAttachment
        from app.models.marketing import Promotion, PromotionAttachment
        from app.models.forms import Form
        from app.models.procurement import InboundShipment
        from app.models.entity_attachment import EntityAttachmentLink

        access_svc = ContactAccessTypeService(self.db)
        validated = access_svc.validate_access_levels(access_levels, field_name="access_levels")

        unique_aids = list(dict.fromkeys([a for a in attachment_ids if a]))
        if not unique_aids:
            raise handle_validation_error("No attachments in scope.")

        count_att = (
            self.db.query(Attachment)
            .filter(Attachment.id.in_(unique_aids), Attachment.is_deleted == False)
            .update({"access_levels": validated}, synchronize_session=False)
        )

        propagated: dict = {
            "product_links": 0,
            "promotions": 0,
            "forms": 0,
            "packing_lists": 0,
        }

        if propagate_to_linked:
            r1 = self.db.execute(
                update(ProductAttachment)
                .where(ProductAttachment.attachment_id.in_(unique_aids))
                .values(access_levels=validated)
            )
            propagated["product_links"] = r1.rowcount or 0

            promo_ids = [
                str(x[0])
                for x in self.db.query(PromotionAttachment.promotion_id)
                .filter(PromotionAttachment.attachment_id.in_(unique_aids))
                .distinct()
                .all()
            ]
            if promo_ids:
                r2 = self.db.execute(
                    update(Promotion).where(Promotion.id.in_(promo_ids)).values(access_levels=validated)
                )
                propagated["promotions"] = r2.rowcount or 0

            r3 = self.db.execute(
                update(Form).where(Form.attachment_id.in_(unique_aids)).values(access_levels=validated)
            )
            propagated["forms"] = r3.rowcount or 0

            link_ids = [
                str(x[0])
                for x in self.db.query(EntityAttachmentLink.entity_id)
                .filter(
                    EntityAttachmentLink.attachment_id.in_(unique_aids),
                    EntityAttachmentLink.entity_type == "inbound_shipment",
                )
                .distinct()
                .all()
            ]
            direct_ids = [
                str(x[0])
                for x in self.db.query(InboundShipment.id)
                .filter(InboundShipment.attachment_id.in_(unique_aids))
                .all()
            ]
            ship_ids = list(dict.fromkeys(link_ids + direct_ids))
            if ship_ids:
                r4 = self.db.execute(
                    update(InboundShipment)
                    .where(InboundShipment.id.in_(ship_ids))
                    .values(access_levels=validated)
                )
                propagated["packing_lists"] = r4.rowcount or 0

        self.db.commit()
        return {"updated_attachments": count_att, "propagated": propagated if propagate_to_linked else None}

    def get_entity_display_name(self, entity_type: Optional[str], entity_id: Optional[str]) -> Optional[str]:
        """Fallback: resolve entity_type + entity_id to a display name when not found via junction tables."""
        if not entity_type or not entity_id:
            return None
        kind = (entity_type or "").strip().lower()
        try:
            if kind == "product":
                from app.models.product import Product
                row = self.db.query(Product).filter(Product.id == entity_id).first()
                return row.product_name if row else None
            if kind == "promotion":
                from app.models.marketing import Promotion
                row = self.db.query(Promotion).filter(Promotion.id == entity_id).first()
                return row.name if row else None
            if kind == "form":
                from app.models.forms import Form
                row = self.db.query(Form).filter(Form.id == entity_id).first()
                return row.name if row else None
            if kind == "inbound_shipment":
                from app.models.procurement import InboundShipment
                row = self.db.query(InboundShipment).filter(InboundShipment.id == entity_id).first()
                return row.shipment_number if row else None
        except Exception:
            return None
        return None
    
    def create_attachment(self, attachment_data: AttachmentCreate, uploaded_by: Optional[str]):
        """Create a new attachment."""
        from sqlalchemy.orm import joinedload
        import uuid as uuid_module
        
        attachment_dict = attachment_data.model_dump()
        # Drop a None id so the model's default uuid generator runs; honor an explicit one.
        if not attachment_dict.get("id"):
            attachment_dict.pop("id", None)
        # Ensure uploaded_by is a string (convert UUID if needed)
        if isinstance(uploaded_by, uuid_module.UUID):
            attachment_dict["uploaded_by"] = str(uploaded_by)
        else:
            attachment_dict["uploaded_by"] = str(uploaded_by) if uploaded_by else None
        # Validate access_levels against catalog or use default
        from app.services.contact_access_type_service import ContactAccessTypeService
        access_svc = ContactAccessTypeService(self.db)
        if attachment_dict.get("access_levels"):
            attachment_dict["access_levels"] = access_svc.validate_access_levels(
                attachment_dict["access_levels"], field_name="access_levels"
            )
        else:
            attachment_dict["access_levels"] = access_svc.get_default_access_levels()

        # Compute and set full_directory_path when directory_id is provided
        directory_id = attachment_dict.get("directory_id")
        if directory_id:
            dir_service = AttachmentDirectoryService(self.db)
            attachment_dict["full_directory_path"] = dir_service.get_full_directory_path(directory_id)
        else:
            attachment_dict["full_directory_path"] = None

        # Multi-company: stamp the ACTIVE company on the upload. Attachments are
        # ``__company_shared__``, so the before_insert auto-stamp deliberately skips
        # them entirely and every upload, in every company, landed with company_id
        # NULL. For attachments NULL is not neutral, it means SHARED (the predicate
        # is ``company_id IS NULL OR company_id IN (scope)``), so a file uploaded
        # while switched into Mocha was visible from Sorento too, and because
        # ``scope_to_attachment_company`` pins the n8n binding scope off this column
        # the packing list n8n created from it stamped the incumbent company instead
        # of Mocha.
        #
        # Anything uploaded while exactly one company is active belongs to that
        # company, folder or not: a file dropped at the root of All files is just as
        # owned as one filed away. Only the shared form entity types keep NULL.
        #
        # PLAN-shared-brand-attachments R11: an upload of a TYPE flagged
        # `is_shared` is written NULL regardless of the active company - the
        # type decides, never the folder (AC-D2). R19: filing it into an owned
        # folder pulls that folder's ancestor chain to shared too, so the path
        # to it resolves from every company.
        upload_type_is_shared = False
        if attachment_dict.get("attachment_type_id"):
            upload_type_is_shared = bool(
                self.db.query(AttachmentType.is_shared)
                .filter(AttachmentType.id == attachment_dict["attachment_type_id"])
                .scalar()
            )

        if upload_type_is_shared:
            attachment_dict["company_id"] = None
        elif attachment_dict.get("company_id") is None:
            entity_type = (attachment_dict.get("entity_type") or "").strip().lower()
            if entity_type not in _SHARED_FORM_ENTITY_TYPES:
                attachment_dict["company_id"] = _single_active_company(
                    get_company_scope(self.db)
                )

        attachment = Attachment(**attachment_dict)
        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        # A shared FORM attachment (complaint/PR/stock-inquiry) is NULL by the
        # pre-existing form-sharing convention (AC-G3), not a `Set company…`
        # decision - it must never pull a folder's ancestor chain along.
        upload_entity_type = (attachment_dict.get("entity_type") or "").strip().lower()
        if (
            upload_type_is_shared
            and directory_id
            and upload_entity_type not in _SHARED_FORM_ENTITY_TYPES
        ):
            from app.services.attachment_company_service import share_ancestor_chain

            share_ancestor_chain(self.db, directory_id)
            self.db.commit()

        # Reload with relationship
        attachment = self.db.query(Attachment).options(
            joinedload(Attachment.attachment_type)
        ).filter(Attachment.id == attachment.id).first()
        publish_embedding_event(
            self.db,
            source_type="attachment",
            source_id=attachment.id,
            source_key=attachment.original_filename,
            source_updated_at=attachment.created_at,
            event_type="attachment.created",
            changed_fields=["original_filename", "description", "entity_type", "entity_id", "access_levels"],
            triggered_by=str(uploaded_by) if uploaded_by else None,
        )
        return attachment
    
    def update_attachment(self, attachment_id: str, attachment_data: AttachmentUpdate):
        """Update an attachment. Recalculates full_directory_path when directory_id changes."""
        attachment = self.get_attachment(attachment_id)
        
        update_data = attachment_data.model_dump(exclude_unset=True)
        if "access_levels" in update_data and update_data["access_levels"]:
            from app.services.contact_access_type_service import ContactAccessTypeService
            access_svc = ContactAccessTypeService(self.db)
            update_data["access_levels"] = access_svc.validate_access_levels(
                update_data["access_levels"], field_name="access_levels"
            )

        # Recalculate full_directory_path when directory_id is updated
        if "directory_id" in update_data:
            new_directory_id = update_data["directory_id"]
            dir_service = AttachmentDirectoryService(self.db)
            update_data["full_directory_path"] = dir_service.get_full_directory_path(new_directory_id)

        # Rename is DB-only and edits stored_filename (the user-facing label). original_filename
        # is immutable (it is the object-key basename - uuid-segregated key, independent of the
        # name, like portal submissions). The generic setattr loop applies stored_filename
        # directly; no storage work. See docs/plans/PLAN-attachment-key-uuid-segregation.md.
        for key, value in update_data.items():
            setattr(attachment, key, value)

        # PLAN-shared-brand-attachments R10: a move never re-stamps the FILE's
        # own company - only `Set company…` (AttachmentCompanyService) does
        # that now. R19: moving a SHARED file into an owned folder instead
        # pulls that folder's ancestor chain to shared, so the path it now
        # lives under still resolves from every company. A shared FORM
        # attachment's NULL is the pre-existing form-sharing convention
        # (AC-G3), not a `Set company…` decision, so it is excluded here too.
        changed_fields = list(update_data.keys())
        if (
            update_data.get("directory_id")
            and attachment.company_id is None
            and (attachment.entity_type or "").strip().lower() not in _SHARED_FORM_ENTITY_TYPES
        ):
            from app.services.attachment_company_service import share_ancestor_chain

            share_ancestor_chain(self.db, update_data["directory_id"])

        self.db.commit()
        self.db.refresh(attachment)
        publish_embedding_event(
            self.db,
            source_type="attachment",
            source_id=attachment.id,
            source_key=attachment.original_filename,
            source_updated_at=attachment.created_at,
            event_type="attachment.updated",
            changed_fields=changed_fields,
        )
        return attachment
    
    def bulk_move(self, attachment_ids: list[str], directory_id: Optional[str]) -> int:
        """Move many attachments into the same folder in one transaction.

        Resolves full_directory_path once for the target folder so all rows
        share the same denormalised path. Skips ids that no longer exist.
        """
        rows = (
            self.db.query(Attachment)
            .filter(Attachment.id.in_(attachment_ids))
            .all()
        )
        if not rows:
            return 0
        dir_service = AttachmentDirectoryService(self.db)
        full_path = dir_service.get_full_directory_path(directory_id) if directory_id else None
        for row in rows:
            row.directory_id = directory_id
            row.full_directory_path = full_path

        # PLAN-shared-brand-attachments R10/R19: a move never re-stamps a
        # file's own company - only `Set company…` does that now. A SHARED
        # file among the movers instead pulls the destination folder's
        # ancestor chain to shared, once for the whole batch - EXCLUDING a
        # shared FORM attachment, whose NULL is the pre-existing form-sharing
        # convention (AC-G3), not a `Set company…` decision.
        def _is_set_company_shared(row: Attachment) -> bool:
            return row.company_id is None and (
                (row.entity_type or "").strip().lower() not in _SHARED_FORM_ENTITY_TYPES
            )

        if directory_id and any(_is_set_company_shared(row) for row in rows):
            from app.services.attachment_company_service import share_ancestor_chain

            share_ancestor_chain(self.db, directory_id)

        self.db.commit()
        for row in rows:
            self.db.refresh(row)
            changed_fields = ["directory_id", "full_directory_path"]
            publish_embedding_event(
                self.db,
                source_type="attachment",
                source_id=row.id,
                source_key=row.original_filename,
                source_updated_at=row.created_at,
                event_type="attachment.updated",
                changed_fields=changed_fields,
            )
        return len(rows)

    def reorder_attachments(self, attachment_ids: list[str], directory_id: Optional[str] = None):
        """Set sort_order to index for each attachment id (within the same directory)."""
        for index, attachment_id in enumerate(attachment_ids):
            attachment = self.db.query(Attachment).filter(
                Attachment.id == attachment_id,
                Attachment.is_deleted == False,
            ).first()
            if not attachment:
                continue
            if directory_id is not None and attachment.directory_id != directory_id:
                continue
            attachment.sort_order = index
        self.db.commit()
        return {"message": "Order updated", "attachment_ids": attachment_ids}

    def archive_attachment(self, attachment_id: str, archived_by: str):
        """Archive an attachment (soft delete). Data remains for retention."""
        attachment = self.get_attachment(attachment_id)
        from datetime import datetime
        attachment.is_deleted = True
        attachment.deleted_at = datetime.utcnow()
        attachment.deleted_by = archived_by
        self.db.commit()
        return {"message": "Attachment archived successfully"}

    def archive_attachments(self, attachment_ids: list[str], archived_by: str):
        """Archive multiple attachments by ID. Skips not-found and already-archived."""
        from datetime import datetime
        count = 0
        for aid in attachment_ids:
            attachment = self.db.query(Attachment).filter(
                Attachment.id == aid,
                Attachment.is_deleted == False,
            ).first()
            if attachment:
                attachment.is_deleted = True
                attachment.deleted_at = datetime.utcnow()
                attachment.deleted_by = archived_by
                count += 1
        self.db.commit()
        return {"message": f"{count} attachment(s) archived successfully", "archived_count": count}

    def restore_attachment(self, attachment_id: str):
        """Restore an archived attachment."""
        attachment = self.get_attachment(attachment_id)
        attachment.is_deleted = False
        attachment.deleted_at = None
        attachment.deleted_by = None
        self.db.commit()
        return {"message": "Attachment restored successfully"}

    def restore_attachments_in_directories(self, directory_ids: list[str]) -> int:
        """Restore all archived attachments whose directory_id is in the given list. Returns count."""
        if not directory_ids:
            return 0
        q = self.db.query(Attachment).filter(
            Attachment.directory_id.in_(directory_ids),
            Attachment.is_deleted == True,
        )
        count = q.update(
            {
                Attachment.is_deleted: False,
                Attachment.deleted_at: None,
                Attachment.deleted_by: None,
            },
            synchronize_session=False,
        )
        return count

    def _s3_key_from_file_path(self, file_path: str) -> str:
        """Extract S3 key from file_path (URL or raw key)."""
        from urllib.parse import urlparse
        if file_path.startswith("https://"):
            parsed = urlparse(file_path)
            return parsed.path.lstrip("/")
        return file_path

    def delete_attachment(self, attachment_id: str, deleted_by: str):
        """Hard delete an attachment (permanent). Removes DB record; storage delete runs in background."""
        attachment = self.get_attachment(attachment_id)
        file_path = attachment.file_path
        provider = getattr(attachment, "storage_provider", None) or "s3"
        keys = [self._s3_key_from_file_path(file_path)] if file_path else []
        self.db.delete(attachment)
        self.db.commit()
        if keys:
            try:
                from app.tasks.s3_tasks import delete_storage_files
                from app.services.queue_service import enqueue_job
                enqueue_job(
                    delete_storage_files,
                    [(provider, k) for k in keys],
                    queue_name="imports",
                    job_timeout=300,
                )
            except Exception as e:
                logger.warning("Failed to enqueue storage delete for %s: %s", attachment_id, e)
        return {"message": "Attachment deleted successfully"}

    def delete_attachments(self, attachment_ids: list[str], deleted_by: str):
        """Hard delete multiple attachments by ID. Skips not-found. Storage deletes run in background."""
        items: list[tuple[str, str]] = []  # (provider, key)
        count = 0
        for aid in attachment_ids:
            attachment = self.db.query(Attachment).filter(Attachment.id == aid).first()
            if attachment:
                if attachment.file_path:
                    items.append(
                        (
                            getattr(attachment, "storage_provider", None) or "s3",
                            self._s3_key_from_file_path(attachment.file_path),
                        )
                    )
                self.db.delete(attachment)
                count += 1
        self.db.commit()
        if items:
            try:
                from app.tasks.s3_tasks import delete_storage_files
                from app.services.queue_service import enqueue_job
                enqueue_job(delete_storage_files, items, queue_name="imports", job_timeout=600)
            except Exception as e:
                logger.warning("Failed to enqueue storage deletes for %s keys: %s", len(items), e)
        return {"message": f"{count} attachment(s) deleted successfully", "deleted_count": count}

    def archive_attachments_in_directories(self, directory_ids: list[str], archived_by: str) -> int:
        """Archive all non-archived attachments whose directory_id is in the given list. Returns count."""
        from datetime import datetime
        if not directory_ids:
            return 0
        q = self.db.query(Attachment).filter(
            Attachment.directory_id.in_(directory_ids),
            Attachment.is_deleted == False,
        )
        count = q.update(
            {
                Attachment.is_deleted: True,
                Attachment.deleted_at: datetime.utcnow(),
                Attachment.deleted_by: archived_by,
            },
            synchronize_session=False,
        )
        self.db.commit()
        return count
    
    def get_file_content(self, attachment_id: str) -> bytes:
        """Retrieve file bytes from whichever storage provider hosts this attachment."""
        return self.get_file_content_for(self.get_attachment(attachment_id))

    def get_file_content_for(self, attachment) -> bytes:
        """The same fetch, for a caller that is already holding the row.

        Split out so a caller that has just loaded and validated an attachment
        (the dealer-kit from-attachment read does exactly that) does not pay a
        second SELECT for the row it is holding. One implementation of the
        provider dispatch, two ways in.
        """
        if not attachment.file_path:
            raise Exception("Attachment has no file path")

        from app.services.storage_router import extract_key, get_backend

        provider = getattr(attachment, "storage_provider", None)
        key = extract_key(attachment.file_path)
        if not key:
            raise Exception("Could not extract storage key from file_path")
        try:
            return get_backend(provider).download_file(key)
        except Exception as e:
            raise Exception(f"Failed to retrieve file from storage: {str(e)}")
