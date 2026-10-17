"""Dynamic list query metadata, advanced search, and export."""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.list_query_metadata import ListQueryField
from app.models.user import UserListColumnConfig, UserPermission
from app.modules.runtime.guards import module_allowed_with_api_key
from app.schemas.list_query import (
    ListExportRequest,
    ListQueryFieldResponse,
    ListQueryResourceResponse,
    ListSearchRequest,
    UserListColumnConfigPayload,
    UserListColumnConfigResponse,
)
from app.schemas.saved_view import SavedView, SavedViewCreate, SavedViewPublish, SavedViews
from app.services.error_handler import AppException, handle_internal_error, handle_not_found
from app.services.list_query_export_service import ListQueryExportService
from app.services.list_query_metadata_service import ListQueryMetadataService
from app.services.list_query_registry import get_adapter, require_adapter
from app.services.list_query_search_service import ListQuerySearchService
from app.services.saved_views_service import PUBLISH_PERMISSION, SavedViewsService
from app.services.user_service import UserPermissionService
from app.services.uuid_path_param import validate_uuid_path
from app.services.workflow_submission_dynamic_list_query import (
    build_dynamic_field_metas_for_definition,
    get_published_schema_for_definition,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _config_dict(raw: Any) -> Dict[str, Any] | None:
    return raw if isinstance(raw, dict) else None


def _orders_export_ui_meta(f: ListQueryField) -> tuple[str | None, str | None]:
    """Derive export dialog hierarchy for orders (order vs line → product / warehouse)."""
    if not bool(getattr(f, "is_line_field", False)):
        return "order", None
    fk = (str(getattr(f, "field_key", "") or "")).lower()
    if fk.startswith("line_product"):
        return "line", "product"
    if fk.startswith("line_warehouse"):
        return "line", "warehouse"
    return "line", None


def _infer_filter_ui_type(f: ListQueryField) -> str:
    ck = str(getattr(f, "compile_key", "") or "")
    dtype = str(getattr(f, "data_type", "string") or "string").lower()
    if ".id" in ck or dtype == "uuid":
        return "foreign_key"
    if dtype in ("number", "integer", "float", "decimal"):
        return "number"
    if dtype in ("date", "datetime"):
        return "date"
    if dtype in ("boolean",):
        return "select"
    return "text"


def _can_view(db: Session, user_id: str, resource_key: str) -> bool:
    adapter = get_adapter(resource_key)
    slug = adapter.view_slug if adapter else None
    if not slug:
        return False
    return UserPermissionService(db).check_user_has_permission(user_id, slug)


def _can_view_listing_key(
    db: Session, user_id: str, listing_key: str, *, fail_closed: bool = False
) -> bool:
    """
    Personalization configs are authorized by the listing key itself.

    This repo expects `listing_key` to be either:
  - the RBAC view permission slug (e.g. `order_management.orders.view`), or
  - a composite key prefixed with the RBAC permission slug, using `::`:
      `order_management.orders.view::orders-list`.

    `fail_closed` flips the unknown-slug fallback below: the saved-views routes pass
    `True` (S2, PR #489 review round) because a saved view is a SHARED, cross-user
    surface - a stray or renamed listing key there would silently let every caller
    read/create views under it, where column-config's per-user personalization blob
    has no such blast radius. Column-config call sites keep the default (permissive).
    """
    listing_key = (listing_key or "").strip()
    if not listing_key:
        return False
    perm_slug = listing_key.split("::", 1)[0].strip() or listing_key

    # If the permission slug does not exist in the RBAC catalog, treat the listing
    # as "module-auth only" (many routes in this repo use module guards instead
    # of fine-grained `require_permission`) - UNLESS the caller asked for the
    # fail-closed variant, in which case an unrecognised slug denies rather than
    # opening a shared surface to anyone who can reach the route.
    perm_exists = db.query(UserPermission).filter(UserPermission.slug == perm_slug).first()
    if not perm_exists:
        return not fail_closed

    return UserPermissionService(db).check_user_has_permission(user_id, perm_slug)


@router.get("/resources", response_model=List[ListQueryResourceResponse])
def list_resources(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ListQueryMetadataService(db)
    all_r = svc.list_resources()
    uid = current_user["id"]
    return [
        r
        for r in all_r
        if _can_view(db, uid, str(getattr(r, "resource_key", "") or ""))
    ]


@router.get("/resources/{resource_key}/fields", response_model=List[ListQueryFieldResponse])
def list_fields(
    resource_key: str,
    definition_id: Optional[str] = Query(
        None,
        description="For workflow_form_submissions: published schema of this definition adds form-field filters.",
    ),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_view(db, current_user["id"], resource_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    svc = ListQueryMetadataService(db)
    fields = svc.list_fields(resource_key)
    if not fields and not svc.get_resource(resource_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown resource")
    out: list[ListQueryFieldResponse] = []
    for f in fields:
        base = ListQueryFieldResponse.model_validate(f)
        sec, sub = (None, None)
        if resource_key == "orders":
            sec, sub = _orders_export_ui_meta(f)
        out.append(
            base.model_copy(
                update={
                    "export_section": sec,
                    "export_subgroup": sub,
                    "filter_ui_type": _infer_filter_ui_type(f),
                    "option_source": None,
                    "relation_resource_key": None,
                    "relation_label_field": None,
                    "is_generated": True,
                    "managed_by": "schema-introspection",
                }
            )
        )

    if resource_key == "workflow_form_submissions" and definition_id:
        schema = get_published_schema_for_definition(db, definition_id.strip())
        if schema:
            for m in build_dynamic_field_metas_for_definition(schema):
                out.append(
                    ListQueryFieldResponse(
                        field_key=m.field_key,
                        label=m.label,
                        data_type=m.data_type,
                        compile_key=m.compile_key,
                        allowed_operators=m.allowed_operators,
                        filterable=m.filterable,
                        exportable=m.exportable,
                        export_column_name=m.export_column_name,
                        is_line_field=m.is_line_field,
                        sort_order=m.sort_order,
                        export_section=None,
                        export_subgroup=None,
                        filter_ui_type="text",
                        option_source=None,
                        relation_resource_key=None,
                        relation_label_field=None,
                        is_generated=True,
                        managed_by="workflow-schema",
                    )
                )
    return out


@router.post("/search")
def advanced_search(
    body: ListSearchRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import os, sys, time
    _profile = os.environ.get("LIST_QUERY_PROFILE") == "1"
    _t0 = time.perf_counter() if _profile else 0.0

    require_adapter(body.resource)
    if not _can_view(db, current_user["id"], body.resource):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    _t1 = time.perf_counter() if _profile else 0.0
    try:
        result = ListQuerySearchService(db).search(body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))
    _t2 = time.perf_counter() if _profile else 0.0

    adapter = require_adapter(body.resource)
    data = adapter.serializer(result["data"])
    _t3 = time.perf_counter() if _profile else 0.0

    if _profile:
        print(
            f"[search-prof] resource={body.resource} "
            f"perm={1000*(_t1-_t0):.0f}ms "
            f"search={1000*(_t2-_t1):.0f}ms "
            f"serialize={1000*(_t3-_t2):.0f}ms "
            f"total={1000*(_t3-_t0):.0f}ms",
            file=sys.stderr,
            flush=True,
        )

    return {
        "data": data,
        "pagination": result["pagination"],
        "empty": result["empty"],
    }


@router.post("/export")
def export_rows(
    body: ListExportRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    adapter = require_adapter(body.resource)
    slug = adapter.export_slug
    if not slug or not UserPermissionService(db).check_user_has_permission(current_user["id"], slug):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Export permission required")
    if body.resource == "finance_billing_documents":
        # The export reads what the finance routes read: same module gate, and the view slug
        # that guards every billing figure on them.
        if not module_allowed_with_api_key(db, current_user, "finance"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Module not enabled: finance")
        if not UserPermissionService(db).check_user_has_permission(
            current_user["id"], "finance.billing_documents.view"
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        rows = ListQueryExportService(db).export_rows(body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        # Logged, never echoed: a database error's text carries the SQL and the schema.
        logger.exception("list_query.export_failed resource=%s", body.resource)
        raise handle_internal_error()
    return {"data": rows, "total": len(rows)}


@router.get("/column-config/{listing_key:path}", response_model=UserListColumnConfigResponse)
def get_list_column_config(
    listing_key: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing_key = (listing_key or "").strip()
    if not _can_view_listing_key(db, current_user["id"], listing_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    row = (
        db.query(UserListColumnConfig)
        .filter(
            UserListColumnConfig.user_id == current_user["id"],
            UserListColumnConfig.listing_key == listing_key,
        )
        .first()
    )
    cfg = _config_dict(getattr(row, "config", None)) if row else None
    return UserListColumnConfigResponse(listing_key=listing_key, config=cfg)


@router.put("/column-config/{listing_key:path}", response_model=UserListColumnConfigResponse)
def upsert_list_column_config(
    listing_key: str,
    body: UserListColumnConfigPayload,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing_key = (listing_key or "").strip()
    if not _can_view_listing_key(db, current_user["id"], listing_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        # A PARTIAL update, not a replace. Two writers share this row: DataGrid's
        # column hook writes columnOrder/Visibility/Sizing from inside the grid, the
        # page's view hook writes sorting/filters/filtersVersion from above it. A
        # whole-blob replace made each one wipe the other's keys, which reads as
        # flaky persistence rather than as a bug.
        #
        # `exclude_unset` (NOT exclude_none) is what separates "I am not writing that
        # key" from "clear that key to null" - the distinction the Clear affordance on
        # the active-filter chip needs.
        incoming = body.model_dump(exclude_unset=True, mode="json")
        row = (
            db.query(UserListColumnConfig)
            .filter(
                UserListColumnConfig.user_id == current_user["id"],
                UserListColumnConfig.listing_key == listing_key,
            )
            .first()
        )
        existing = (_config_dict(getattr(row, "config", None)) or {}) if row else {}
        merged = {**existing, **incoming}
        # An explicitly-null key is a clear, so it is dropped rather than stored.
        data = {k: v for k, v in merged.items() if v is not None}
        if row:
            setattr(row, "config", data)
        else:
            row = UserListColumnConfig(user_id=current_user["id"], listing_key=listing_key, config=data)
            db.add(row)
        db.commit()
        db.refresh(row)
        return UserListColumnConfigResponse(
            listing_key=listing_key,
            config=_config_dict(getattr(row, "config", None)),
        )
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.delete("/column-config/{listing_key:path}", status_code=status.HTTP_204_NO_CONTENT)
def reset_list_column_config(
    listing_key: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    listing_key = (listing_key or "").strip()
    if not _can_view_listing_key(db, current_user["id"], listing_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        row = (
            db.query(UserListColumnConfig)
            .filter(
                UserListColumnConfig.user_id == current_user["id"],
                UserListColumnConfig.listing_key == listing_key,
            )
            .first()
        )
        if row:
            db.delete(row)
            db.commit()
        return None
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


# --------------------------------------------------------------------------------- saved views
#
# Segments (S4, PLAN-scm-reorder-oi-feedback-1sep.md): a saved view of filters + sort +
# columns, generalised from `report_views`/`views_service.py` and keyed by the SAME
# `listing_key` the column-config routes above already authorise with
# `_can_view_listing_key`. Delete is deliberately absent here - it runs through the
# deferred-action registry (`saved_view.delete` in `app/services/record_actions.py`) so
# the frontend gets the standard countdown rather than a confirmation dialog.


def _require_saved_view_publish(db: Session, user: dict) -> None:
    if not UserPermissionService(db).check_user_has_permission(user["id"], PUBLISH_PERMISSION):
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            message=f"Permission required: {PUBLISH_PERMISSION}",
            code="FORBIDDEN",
        )


@router.get("/saved-views/{listing_key:path}", response_model=SavedViews)
def list_saved_views(
    listing_key: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedViews:
    listing_key = (listing_key or "").strip()
    if not _can_view_listing_key(db, current_user["id"], listing_key, fail_closed=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    return SavedViewsService(db).list_for(listing_key, str(current_user["id"]))


def _authorised_saved_view(db: Session, user: dict, view_id: str) -> str:
    """The view's listing key, or 404 - checked before anything else can be asked about it.

    A view id in someone else's hand is not a licence to learn a listing key exists, so
    an unknown view answers the same 404 the ownership checks further down already give.
    """
    listing_key = SavedViewsService(db).listing_key_of(view_id)
    if listing_key is None or not _can_view_listing_key(
        db, user["id"], listing_key, fail_closed=True
    ):
        raise handle_not_found("View", view_id)
    return listing_key


# `/{view_id}/publish` and `/{view_id}/set-default` MUST be declared before
# `/{listing_key:path}` below: Starlette matches routes in registration order, and a
# `:path` converter matches every remaining segment including slashes, so a POST route
# on `/{listing_key:path}` registered first swallows `POST /saved-views/<id>/publish`
# whole - the path "<id>/publish" becomes `listing_key`, `create_saved_view` runs
# instead, and the real handler is never reached (the same shape LESSONS-LEARNT.md's
# SLA route-shadowing entry names: static before the greedy path param).
@router.post("/saved-views/{view_id}/publish", response_model=SavedView)
def publish_saved_view(
    view_id: str,
    body: SavedViewPublish,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedView:
    validate_uuid_path(view_id, resource="View")
    _authorised_saved_view(db, current_user, view_id)
    _require_saved_view_publish(db, current_user)
    return SavedViewsService(db).publish(view_id, str(current_user["id"]), body.is_shared)


@router.post("/saved-views/{view_id}/set-default", response_model=SavedView)
def set_default_saved_view(
    view_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedView:
    """Make one shared view the listing's default for everyone. At most one per listing key."""
    validate_uuid_path(view_id, resource="View")
    _authorised_saved_view(db, current_user, view_id)
    _require_saved_view_publish(db, current_user)
    return SavedViewsService(db).set_default(view_id, str(current_user["id"]))


@router.post("/saved-views/{listing_key:path}", response_model=SavedView)
def create_saved_view(
    listing_key: str,
    body: SavedViewCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedView:
    listing_key = (listing_key or "").strip()
    if not _can_view_listing_key(db, current_user["id"], listing_key, fail_closed=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    return SavedViewsService(db).create(listing_key, str(current_user["id"]), body.name, body.view)
