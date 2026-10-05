"""Inventory service for business logic."""
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, or_, and_, tuple_, select
from typing import Optional, TYPE_CHECKING
import time
import uuid
from datetime import datetime

if TYPE_CHECKING:
    from app.services.stock_visibility import Policy
from app.models.inventory import Warehouse, StorageZone, Stock, StockBatch, StockLedger
from app.models.product import Product
from app.schemas.inventory import (
    WarehouseCreate, WarehouseUpdate, StorageZoneCreate, StorageZoneUpdate,
    StockCreate, StockUpdate, StockBatchCreate, StockBatchUpdate
)
from app.services.error_handler import handle_not_found, handle_conflict, handle_validation_error, AppException
from app.services.company_scope import get_company_scope, stamp_lookup_companies
from app.services.import_log_service import ImportLogService
from app.services.identifier_resolver import resolve_identifier
from app.services.rules.master_rules import resolve_master_by_code


def _resolve_stock_product_id(db: Session, product_id: Optional[str]) -> Optional[str]:
    """Map API product filter to ``Stock.product_id`` (UUID string).

    Accepts a UUID string or ``Product.product_code`` (case-insensitive exact match).
    Returns ``None`` if *product_id* is blank, or if a non-UUID value does not match any product.
    """
    if product_id is None:
        return None
    s = product_id.strip()
    if not s:
        return None
    try:
        return str(uuid.UUID(s))
    except ValueError:
        row = (
            db.query(Product.id)
            .filter(func.lower(Product.product_code) == s.lower())
            .first()
        )
        return str(row.id) if row else None


class WarehouseService:
    """Service for warehouse operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_warehouses(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        is_active: Optional[bool] = None,
        sort_field: Optional[str] = None,
        sort_dir: Optional[str] = None,
        warehouse_ids: Optional[list[str]] = None,
        segment: Optional[str] = None,
    ):
        """List warehouses. Supports sort by warehouse_code, warehouse_name, location, is_active, created_at, updated_at, zones_count, stock_count."""
        from sqlalchemy import select

        # Correlated count subqueries for derived columns (zones_count, stock_count).
        zones_count_sq = (
            select(func.count(StorageZone.id))
            .where(StorageZone.warehouse_id == Warehouse.id)
            .correlate(Warehouse)
            .scalar_subquery()
        )
        stock_count_sq = (
            select(func.count(Stock.id))
            .where(Stock.warehouse_id == Warehouse.id)
            .correlate(Warehouse)
            .scalar_subquery()
        )

        q = self.db.query(
            Warehouse,
            zones_count_sq.label("zones_count"),
            stock_count_sq.label("stock_count"),
        )

        if is_active is not None:
            q = q.filter(Warehouse.is_active == is_active)

        if warehouse_ids is not None:
            q = q.filter(Warehouse.id.in_(warehouse_ids))

        if segment:
            q = q.filter(Warehouse.segment == segment)

        if query:
            q = q.filter(
                or_(
                    Warehouse.warehouse_code.ilike(f"%{query}%"),
                    Warehouse.warehouse_name.ilike(f"%{query}%"),
                )
            )

        sort_map = {
            "warehouse_code": Warehouse.warehouse_code,
            "warehouse_name": Warehouse.warehouse_name,
            "location": Warehouse.location,
            "is_active": Warehouse.is_active,
            # The Warehouses list carries a Fulfilment planning column, so it carries its
            # sort too (borrow ladder v7.1 S1): "show me the bins that are in" is the
            # admin's first question after flagging one.
            "fulfilment_planning": Warehouse.fulfilment_planning,
            "created_at": Warehouse.created_at,
            "updated_at": Warehouse.updated_at,
            "zones_count": zones_count_sq,
            "stock_count": stock_count_sq,
        }
        key = (sort_field or "created_at").strip()
        col = sort_map.get(key, Warehouse.created_at)
        direction = (sort_dir or "asc").lower()
        q = q.order_by(col.desc() if direction == "desc" else col.asc())

        total = q.with_entities(func.count(Warehouse.id)).order_by(None).scalar() or 0
        offset = (page - 1) * limit
        rows = q.offset(offset).limit(limit).all()

        warehouses = []
        for warehouse, zc, sc in rows:
            setattr(warehouse, "zones_count", zc or 0)
            setattr(warehouse, "stock_count", sc or 0)
            warehouses.append(warehouse)
        self._attach_pool_codes(warehouses)

        return {
            "data": warehouses,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    
    def _attach_pool_codes(self, warehouses: list) -> None:
        """Resolve `pool_warehouse_id` to a readable code for display.

        The UI must never render a bare UUID, and the pool is configuration a person picks
        by name. Batched into one query rather than a lookup per row.
        """
        pool_ids = {str(w.pool_warehouse_id) for w in warehouses
                    if getattr(w, "pool_warehouse_id", None)}
        codes: dict[str, str] = {}
        if pool_ids:
            codes = {
                str(wid): code
                for wid, code in self.db.query(Warehouse.id, Warehouse.warehouse_code)
                .filter(Warehouse.id.in_(list(pool_ids)))
                .all()
            }
        for w in warehouses:
            pid = getattr(w, "pool_warehouse_id", None)
            setattr(w, "pool_warehouse_code", codes.get(str(pid)) if pid else None)

    def get_warehouse(self, warehouse_id: str):
        """Get a warehouse by UUID or warehouse_code/name."""
        resolved_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if not resolved_ids:
            raise handle_not_found("Warehouse", warehouse_id)
        warehouse = self.db.query(Warehouse).filter(Warehouse.id.in_(resolved_ids)).first()
        if not warehouse:
            raise handle_not_found("Warehouse", warehouse_id)
        self._attach_pool_codes([warehouse])
        return warehouse
    
    def create_warehouse(self, warehouse_data: WarehouseCreate):
        """Create a new warehouse.

        Security review (should-fix 4): a case/whitespace variant used to be
        SILENTLY ADOPTED here - every `WarehouseBase` default on the request
        (`is_active`, `counts_as_available`, `pool_warehouse_id`, `segment`,
        `fulfilment_planning`, `manager_id`, ...) overwrote the existing row's
        own values, and the call still returned 201 as if a new warehouse had
        been created. D17's case/whitespace-insensitive match stays the rule
        for the xlsx import and the ESB push (both correct a book that spells
        a code two ways), but a human's manual Create click is a mistake to
        surface, not silently rewrite - the same conflict standard S1 already
        applies to `create_supplier`/`create_category`/`create_uom`.
        """
        existing_id = resolve_master_by_code(self.db, Warehouse, warehouse_data.warehouse_code)
        if existing_id:
            raise handle_conflict("Warehouse code already exists.")

        warehouse = Warehouse(**warehouse_data.model_dump())
        self.db.add(warehouse)
        self.db.commit()
        self.db.refresh(warehouse)
        return warehouse
    
    def update_warehouse(self, warehouse_id: str, warehouse_data: WarehouseUpdate):
        """Update a warehouse."""
        warehouse = self.get_warehouse(warehouse_id)

        update_data = warehouse_data.model_dump(exclude_unset=True)
        new_code = update_data.get("warehouse_code")
        if new_code is not None:
            new_code = (new_code or "").strip()
            if not new_code:
                raise handle_validation_error("Warehouse code cannot be empty.")
            if new_code != (warehouse.warehouse_code or "").strip():
                # S3 (review re-check, 2026-09-06): case/whitespace-insensitive
                # (D17), same as `create_warehouse` - an EXACT-match query let a
                # rename to a case variant of another warehouse's code through
                # unrefused, the same conflict-vs-adopt gap security should-fix
                # 4 closed on create.
                conflict_id = resolve_master_by_code(self.db, Warehouse, new_code)
                if conflict_id and conflict_id != warehouse_id:
                    raise handle_conflict("Warehouse code already exists.")
            update_data["warehouse_code"] = new_code

        for key, value in update_data.items():
            setattr(warehouse, key, value)

        # Stamped here because the column has no ``onupdate``: without this an edit leaves
        # "Last Updated" showing the creation date forever. Naive UTC, matching how every
        # other datetime column in this codebase is stored.
        warehouse.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(warehouse)
        # Re-resolve after the write. `pool_warehouse_code` is a plain attribute set by
        # `_attach_pool_codes`, not a mapped column, so neither `commit()` nor `refresh()`
        # touches it and the value the read at the top of this method attached survives.
        # Without this, clearing a pool answers `pool_warehouse_id: null` next to the OLD
        # `pool_warehouse_code`, and setting one answers the new id next to a stale null -
        # one response contradicting itself in the only half of the pair the screen shows.
        self._attach_pool_codes([warehouse])
        return warehouse

    def delete_warehouse(self, warehouse_id: str) -> dict:
        """Hard delete one warehouse by id (UUID or code/name). 409 if referenced by stock/zones/etc."""
        from sqlalchemy.exc import IntegrityError
        warehouse = self.get_warehouse(warehouse_id)
        wid = warehouse.id
        try:
            self.db.delete(warehouse)
            self.db.commit()
        except IntegrityError as ex:
            self.db.rollback()
            raise handle_conflict(
                "Warehouse has linked stock, zones, allocations, or picking lines. "
                "Remove or reassign them before deleting."
            ) from ex
        return {"id": wid, "message": "Warehouse deleted."}

    def bulk_delete_warehouses(self, warehouse_ids: list[str]) -> dict:
        """Delete multiple warehouses by id. Returns deleted_count, failed[], message."""
        from sqlalchemy.exc import IntegrityError
        if not warehouse_ids:
            return {"deleted_count": 0, "failed": [], "message": "No warehouses to delete."}

        # Resolve mixed UUIDs/codes/names → UUIDs.
        resolved: list[str] = []
        unresolved: list[str] = []
        for raw in warehouse_ids:
            ids = resolve_identifier(
                self.db,
                raw,
                Warehouse,
                code_fields=("warehouse_code", "warehouse_name"),
            )
            if ids:
                resolved.extend(ids)
            else:
                unresolved.append(raw)
        resolved = list(dict.fromkeys(resolved))

        failed: list[dict] = [{"id": raw, "reason": "not found"} for raw in unresolved]
        deleted_count = 0
        # Delete one-by-one so FK conflicts surface per-row instead of aborting whole batch.
        for wid in resolved:
            row = self.db.query(Warehouse).filter(Warehouse.id == wid).first()
            if not row:
                failed.append({"id": wid, "reason": "not found"})
                continue
            try:
                self.db.delete(row)
                self.db.flush()
                deleted_count += 1
            except IntegrityError as ex:
                self.db.rollback()
                failed.append({"id": wid, "reason": "referenced by stock / zones / allocations"})
            except Exception as ex:
                self.db.rollback()
                failed.append({"id": wid, "reason": str(ex)})
        self.db.commit()
        return {
            "deleted_count": deleted_count,
            "failed": failed,
            "message": f"Deleted {deleted_count} warehouse(s); {len(failed)} failed."
            if failed
            else f"Deleted {deleted_count} warehouse(s).",
        }

    def bulk_import_warehouses(
        self,
        rows: list[dict],
        user_id: str,
        validate_only: bool = False,
        outcome=None,
    ):
        """Upsert warehouses from Excel data, keyed by warehouse_code (case-insensitive).

        Column mapping (Excel header → DB field):
        - "Sytem Location" / "System Location" / "warehouse_code" → warehouse_code
        - "System Location Descriptions" / "System Location Description" / "warehouse_name" → warehouse_name
        - "Warehouse Location" / "location" → location
        - "Status" / "is_active" → is_active (Active|true → True; Inactive|false → False)

        Returns dict with created/updated/skipped counts + errors/warnings; if
        validate_only=True returns valid/errors/warnings/summary without writes.

        ``outcome``: optional ImportOutcome recorder so every row's fate is captured
        for the job detail page. None for non-job callers.
        """
        from app.services import import_outcome_codes as _oc
        from app.services.import_outcome import ImportOutcome as _ImportOutcome

        if outcome is None:
            outcome = _ImportOutcome(None, persist=False)

        column_mapping = {
            "Sytem Location": "warehouse_code",
            "System Location": "warehouse_code",
            "system location": "warehouse_code",
            "warehouse_code": "warehouse_code",
            "Warehouse Code": "warehouse_code",
            "Code": "warehouse_code",
            "code": "warehouse_code",
            "System Location Descriptions": "warehouse_name",
            "System Location Description": "warehouse_name",
            "system location description": "warehouse_name",
            "Description": "warehouse_name",
            "description": "warehouse_name",
            "warehouse_name": "warehouse_name",
            "Warehouse Name": "warehouse_name",
            "Warehouse Location": "location",
            "warehouse location": "location",
            "Location": "location",
            "location": "location",
            "Status": "is_active",
            "status": "is_active",
            "Active": "is_active",
            "is_active": "is_active",
        }

        def _norm_status(value) -> Optional[bool]:
            if value is None:
                return None
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if not s:
                return None
            if s in {"active", "true", "1", "yes", "y", "enabled"}:
                return True
            if s in {"inactive", "false", "0", "no", "n", "disabled"}:
                return False
            return None

        def _map_row(raw: dict) -> dict:
            mapped: dict = {}
            for raw_key, value in raw.items():
                key = str(raw_key).strip()
                db_key = column_mapping.get(key) or column_mapping.get(key.lower())
                if db_key is None:
                    norm = key.lower().replace(" ", "")
                    for ck, cv in column_mapping.items():
                        if ck.lower().replace(" ", "") == norm:
                            db_key = cv
                            break
                if db_key is None:
                    continue
                if isinstance(value, str):
                    value = value.strip()
                if value == "":
                    value = None
                if db_key == "is_active":
                    value = _norm_status(value)
                mapped[db_key] = value
            return mapped

        errors: list[str] = []
        warnings: list[dict] = []
        created = 0
        updated = 0
        skipped = 0
        import_session_id = str(uuid.uuid4())

        # Pass 1: parse + validate; collect codes for bulk lookup.
        parsed: list[tuple[int, dict]] = []
        codes_seen: dict[str, int] = {}
        for idx, raw in enumerate(rows, start=1):
            row = _map_row(raw)
            code = row.get("warehouse_code")
            if not code:
                msg = "missing warehouse_code / Sytem Location."
                errors.append(f"Row {idx}: {msg}")
                outcome.skip(row=idx, code=_oc.MISSING_REQUIRED_FIELD, message=msg)
                continue
            code = str(code).strip()
            row["warehouse_code"] = code
            key = code.lower()
            if key in codes_seen:
                warnings.append({
                    "row": idx,
                    "message": f"Duplicate warehouse_code '{code}' (first at row {codes_seen[key]}); later row wins.",
                })
            codes_seen[key] = idx
            parsed.append((idx, row))

        if not parsed:
            result = {
                "valid": len(errors) == 0,
                "created": 0,
                "updated": 0,
                "skipped": skipped,
                "errors": errors,
                "warnings": warnings,
                "import_session_id": import_session_id,
                "summary": {"rows": len(rows), "parsed": 0},
            }
            return result

        # Bulk lookup existing by code (case-insensitive).
        all_codes = [r["warehouse_code"] for _, r in parsed]
        existing = self.db.query(Warehouse).filter(
            func.lower(Warehouse.warehouse_code).in_([c.lower() for c in all_codes])
        ).all()
        existing_by_code = {w.warehouse_code.lower(): w for w in existing}

        if validate_only:
            for idx, row in parsed:
                if row["warehouse_code"].lower() in existing_by_code:
                    warnings.append({"row": idx, "message": f"Will update existing warehouse '{row['warehouse_code']}'."})
                else:
                    warnings.append({"row": idx, "message": f"Will create new warehouse '{row['warehouse_code']}'."})
            return {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "summary": {
                    "rows": len(rows),
                    "parsed": len(parsed),
                    "to_update": sum(1 for _, r in parsed if r["warehouse_code"].lower() in existing_by_code),
                    "to_create": sum(1 for _, r in parsed if r["warehouse_code"].lower() not in existing_by_code),
                },
                "import_session_id": import_session_id,
            }

        # Pass 2: apply upsert. Later duplicate wins (overwrite earlier).
        seen_keys: set[str] = set()
        for idx, row in parsed:
            key = row["warehouse_code"].lower()
            target = existing_by_code.get(key)
            try:
                if target is not None:
                    for col in ("warehouse_code", "warehouse_name", "location"):
                        if col in row:
                            setattr(target, col, row[col])
                    if "is_active" in row and row["is_active"] is not None:
                        target.is_active = row["is_active"]
                    if key not in seen_keys:
                        updated += 1
                    outcome.updated(
                        row=idx,
                        message=f"Warehouse updated: {row['warehouse_code']}",
                        value=row["warehouse_code"],
                        identity={"warehouse_code": row["warehouse_code"],
                                  "warehouse_name": row.get("warehouse_name")},
                        entity_type="warehouse",
                        entity_id=getattr(target, "id", None),
                    )
                else:
                    new = Warehouse(
                        warehouse_code=row["warehouse_code"],
                        warehouse_name=row.get("warehouse_name"),
                        location=row.get("location"),
                        is_active=row["is_active"] if row.get("is_active") is not None else True,
                    )
                    self.db.add(new)
                    existing_by_code[key] = new
                    if key not in seen_keys:
                        created += 1
                    outcome.success(
                        row=idx,
                        message=f"Warehouse created: {row['warehouse_code']}",
                        value=row["warehouse_code"],
                        identity={"warehouse_code": row["warehouse_code"],
                                  "warehouse_name": row.get("warehouse_name")},
                        entity_type="warehouse",
                    )
                seen_keys.add(key)
            except Exception as ex:
                errors.append(f"Row {idx}: {ex}")
                skipped += 1
                outcome.fail(
                    row=idx,
                    code=_oc.ROW_ERROR,
                    message=str(ex),
                    value=row.get("warehouse_code"),
                )

        try:
            self.db.commit()
        except Exception as ex:
            self.db.rollback()
            errors.append(f"Commit failed: {ex}")
            return {
                "valid": False,
                "created": 0,
                "updated": 0,
                "skipped": len(parsed),
                "errors": errors,
                "warnings": warnings,
                "import_session_id": import_session_id,
            }

        return {
            "valid": len(errors) == 0,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "warnings": warnings,
            "import_session_id": import_session_id,
        }


class StorageZoneService:
    """Service for storage zone operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_zones(self, warehouse_id: Optional[str] = None, page: int = 1, limit: int = 50):
        """List storage zones."""
        q = self.db.query(StorageZone)

        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
            q = q.filter(StorageZone.warehouse_id.in_(warehouse_ids))

        total = q.count()
        offset = (page - 1) * limit
        zones = q.offset(offset).limit(limit).all()
        
        return {
            "data": zones,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def list_zones_tree(self, warehouse_id: Optional[str] = None):
        """List storage zones for tree view with warehouse info."""
        from sqlalchemy.orm import joinedload

        q = self.db.query(StorageZone).options(joinedload(StorageZone.warehouse))
        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return []
            q = q.filter(StorageZone.warehouse_id.in_(warehouse_ids))
        return q.all()
    
    def get_zone(self, zone_id: str):
        """Get a storage zone by ID."""
        zone = self.db.query(StorageZone).filter(StorageZone.id == zone_id).first()
        if not zone:
            raise handle_not_found("Storage Zone", zone_id)
        return zone
    
    def create_zone(self, zone_data: StorageZoneCreate):
        """Create a new storage zone."""
        # Check unique constraint
        existing = self.db.query(StorageZone).filter(
            StorageZone.warehouse_id == zone_data.warehouse_id,
            StorageZone.zone_code == zone_data.zone_code
        ).first()
        if existing:
            raise handle_conflict("Zone code already exists for this warehouse.")
        
        zone = StorageZone(**zone_data.model_dump())
        self.db.add(zone)
        self.db.commit()
        self.db.refresh(zone)
        return zone
    
    def update_zone(self, zone_id: str, zone_data: StorageZoneUpdate):
        """Update a storage zone."""
        zone = self.get_zone(zone_id)
        
        update_data = zone_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(zone, key, value)
        
        self.db.commit()
        self.db.refresh(zone)
        return zone


class StockService:
    """Service for stock operations."""
    
    def __init__(self, db: Session):
        self.db = db
        # The stock-visibility policy the last `list_stock` call resolved for its contact
        # (None on the staff path). `_with_sellable` reads it so the product Total's O/S
        # is narrowed by the same location rule the lines are (STOCK-TOTAL-OS-SCOPE).
        self.resolved_policy = None
        # The warehouses the question itself narrowed to (`warehouse_ids` AND the singular
        # `warehouse_id`), None when it named none. Read beside `resolved_policy`.
        self.requested_warehouse_ids: Optional[set[str]] = None
    
    def list_stock(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort: Optional[str] = None,
        dir: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        warehouse_ids: Optional[list[str]] = None,
        product_id: Optional[str] = None,
        product_ids: Optional[list[str]] = None,
        quantity_operator: Optional[str] = None,
        quantity_value: Optional[str] = None,
        status: Optional[str] = None,
        entities: Optional[list[str]] = None,
        exclude_zero_system_adjustment: bool = False,
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        requested_qty: Optional[int] = None,
        requested_quantities: Optional[dict] = None,
    ):
        """List stock with product and warehouse info.

        Args:
            sort: Column to sort by (e.g. product_code, product_name, available).
            dir: 'asc' or 'desc'.
            status: Filter by computed status: critical, low, normal, overstock.
            entities: Free-text bag (product codes, customer/transporter/etc.) - resolved
                via the entity_resolver and applied as additional filters. For stock balance
                only product matches translate into a filter (Stock.product_id IN ...). Any
                other resolved type is echoed back but does not narrow the listing because
                stock rows are keyed by product + warehouse only.
            contact_id: The contact this question is being asked ON BEHALF OF. Its
                presence is what switches the stock-visibility policy ON - the staff
                web grid calls with no contact and is deliberately untouched, so
                flipping the DEFAULT policy row to `compact` for the chatbot cannot
                break /inventory/stock (PLAN "Enforcement", AC-A7).
            space_id: Respond.io workspace id, only to disambiguate `contact_id` when
                it is a Respond.io id.
            requested_qty: How many units the contact asked for. Only read in
                `availability` mode, where it turns "needs_quantity" into a yes/no.
                A value below 1 is read as NOT PROVIDED: the number is parsed out
                of a sentence by an LLM, so a 0 is a parse artefact rather than a
                demand, and refusing the call would lose the question ("how many
                units do you need?") along with the number.
            requested_quantities: Ported from PR #1118 (feat/chatbot-dealer-stock-
                verdict, not merged, owner ruling 24 Sep 2026): product UUID -> the
                quantity asked for THAT product, one turn's whole ask rather than a
                single scalar. Only read in `availability` mode. Per product the map
                wins; `requested_qty` fills any product it does not name. A mapped
                value below 1 is read as NOT PROVIDED, the same rule `requested_qty`
                follows above and for the same reason.
        """
        from sqlalchemy import or_, func
        from app.services.stock_visibility import resolve_policy, warehouse_criterion

        if requested_qty is not None and requested_qty < 1:
            requested_qty = None

        # --- stock visibility policy (chatbot path only) ----------------------
        # Resolved FIRST so an unresolvable contact costs nothing: same fail-closed
        # answer company scope already gives when contact params name nobody, and
        # with NO `stock_visibility` block, which would otherwise claim a policy was
        # applied when none resolved (AC-A5).
        policy = None
        resolved_contact_id = None
        if contact_id:
            policy = resolve_policy(self.db, contact_id, space_id)
            self.resolved_policy = policy
            # Chatbot stock ask v2 S3, R7: the SAME internal id `resolve_policy`
            # itself resolves `contact_id`/`space_id` against - resolved again here
            # (one cheap extra lookup) so `_apply_stock_visibility` can read that
            # contact's own `packing_list_allowed` toggle without threading a bigger
            # change through `resolve_policy`'s other callers.
            #
            # Issue #1328: resolved through `eta_policy.resolve_request_contact` (with the
            # NULL-workspace fallback), the SAME resolution the incoming routes use, so a
            # contact's ETA and packing list switches read identically on both. Only the
            # switches read this id; the policy above keeps its own lookup.
            from app.services.eta_policy import resolve_request_contact

            resolved_contact_id = resolve_request_contact(self.db, contact_id, space_id)
            if policy is None:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
        from sqlalchemy.orm import selectinload
        from app.models.product import Product, ProductCategory
        from app.services.entity_resolver import (
            EntityFilterBuckets,
            resolve_entities_to_filters,
        )

        # D27, ported from PR #1118 (not merged), review round 8: in the DEALER mode a
        # named product id stands for its CODE. The availability block answers one
        # entry per code across the contact's companies, keyed on the first of the
        # merged ids - so the task's slot carries that one id and the next turn's fetch
        # names it alone. Measured on the live database (turn 38d74c62): MWT5727SS-CR's
        # 177 open PO lines sit on the SORENTO row, the fetch carried only the MOCHA id,
        # and the dealer was told "Not available" with no purchase disclaimer at all -
        # the same call with both ids answered correctly. A code is one product to this
        # reader, so it is one product to every read behind the answer too.
        #
        # `availability` only: `compact` and `detailed` name locations and quantities per
        # product ROW, and the staff grid / n8n callers of those modes ask for the id
        # they mean.
        if policy is not None and policy.mode == "availability" and product_ids:
            named_ids = [str(pid) for pid in product_ids if pid]
            code_rows = (
                self.db.query(Product.id, Product.product_code)
                .filter(
                    func.lower(Product.product_code).in_(
                        self.db.query(func.lower(Product.product_code)).filter(
                            Product.id.in_(named_ids)
                        )
                    )
                )
                .all()
            )
            siblings: dict[str, list[str]] = {}
            code_by_id: dict[str, str] = {}
            for row_id, row_code in code_rows:
                key = (row_code or "").strip().lower()
                siblings.setdefault(key, []).append(str(row_id))
                code_by_id[str(row_id)] = key
            expanded: list[str] = []
            seen: set[str] = set()
            for pid in named_ids:
                # The named id first, so the asked order (review round 6) and the merged
                # entry's own `product_id` (review round 5) are both unchanged.
                for candidate in [pid] + siblings.get(code_by_id.get(pid, ""), []):
                    if candidate not in seen:
                        seen.add(candidate)
                        expanded.append(candidate)
            product_ids = expanded

        # Resolved input product id(s) - used on the data-miss (empty) path to find
        # data-bearing variant/neighbour alternatives (section 3.3), and by the
        # per-company labelling on EVERY exit, including the early returns below,
        # so an empty answer can still name the companies it searched.
        resolved_input_product_ids: set[str] = {
            str(pid) for pid in (product_ids or []) if pid
        }

        entity_buckets: Optional[EntityFilterBuckets] = None
        if entities:
            entity_buckets = resolve_entities_to_filters(
                self.db,
                entities,
                allowed_entity_types=(
                    "product", "customer", "customer_order", "transporter",
                    "inbound_shipment", "spo_allocation", "grn", "promotion",
                    "attachment", "form",
                ),
            )
            if not entity_buckets.product_codes:
                # Stock balance can only filter on product. If no product resolved
                # from `entities`, return empty rather than fall through to the
                # unfiltered full listing - caller would otherwise read it as
                # "no match" while seeing every row.
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                    "resolved_entities": entity_buckets.as_echo(),
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=resolved_input_product_ids
                )
                return payload

        q = self.db.query(Stock).options(
            selectinload(Stock.product),
            selectinload(Stock.warehouse),
        ).filter(Stock.warehouse.has(Warehouse.is_active.is_(True)))

        if warehouse_ids:
            q = q.filter(Stock.warehouse_id.in_(warehouse_ids))

        # The policy narrows what company scope already allowed; it never widens.
        # An empty allow-list is a real configuration ("this contact is told about
        # no stock at all"), so it filters to nothing rather than being ignored.
        # `warehouse_criterion` also carries the "all except these" exclusion
        # (PLAN stock-visibility-exclude-locations) - a no-op when the policy
        # names neither list.
        if policy is not None:
            q = q.filter(warehouse_criterion(policy, Stock.warehouse_id))

        # `hide_zero_locations` on the DETAILED mode is a row filter: a location
        # holding none of the product is a line the reader has no use for. The two
        # summary modes clear `data` anyway and drop their zero LOCATION LINES in
        # `_apply_stock_visibility`, where the product still keeps its block - so
        # this filter must not reach `policy_q`, which those modes aggregate over.
        #
        # `!= 0`, never `> 0`. A negative on-hand is not "none left", it is a count
        # that cannot be true, and the person who can fix it is the one reading
        # this listing. Every row filtering out simply takes the empty path the
        # caller already handles.
        #
        # D5 (12 Sep 2026, finding 5): a zero row drops ONLY for a product that HAS
        # stock somewhere the contact can see. A product zero at every visible
        # location keeps its rows - the same "none left, not never found" rule
        # `_apply_stock_visibility` states for compact/availability mode two
        # sections below (its own comment there). Without this, "SRT6550-DIY ETA"
        # read as "No incoming and no stock" (unknown product) in detailed mode
        # while compact correctly printed "*Total:* 0 (O/S: 21) ... but PO is
        # placed" for the same all-zero product.
        #
        # The EXISTS carries the SAME warehouse criterion AND the same active-warehouse
        # restriction as the outer query, plus an EXPLICIT `company_id` equality, all
        # mandatory: issue #832 is a correlated EXISTS escaping the `do_orm_execute`
        # company-scope filter, so a sibling row in another company, a warehouse this
        # policy excludes, or a warehouse the outer query never shows at all because it
        # is `is_active=False` must not count as "has stock somewhere" - an inactive
        # location is outside the visible set, so stock sitting there is as unseen as
        # stock in an excluded one, and it counts only inside this filter's own
        # subquery, never through the ORM-level auto-filter, which a correlated EXISTS
        # does not go through.
        if (
            policy is not None
            and policy.hide_zero_locations
            and policy.mode == "detailed"
        ):
            s2 = aliased(Stock)
            has_stock_elsewhere = (
                select(s2.id)
                .where(
                    s2.product_id == Stock.product_id,
                    s2.company_id == Stock.company_id,
                    warehouse_criterion(policy, s2.warehouse_id),
                    s2.warehouse.has(Warehouse.is_active.is_(True)),
                    s2.quantity_on_hand != 0,
                )
                .exists()
            )
            q = q.filter(or_(Stock.quantity_on_hand != 0, ~has_stock_elsewhere))

        resolved_wh_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if resolved_wh_ids is not None:
            if not resolved_wh_ids:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=resolved_input_product_ids
                )
                return payload
            q = q.filter(Stock.warehouse_id.in_(resolved_wh_ids))

        # Both narrowings filter the rows (ANDed), so the Total's O/S answers the same set.
        if warehouse_ids:
            self.requested_warehouse_ids = {str(w) for w in warehouse_ids}
        if resolved_wh_ids is not None:
            named = {str(w) for w in resolved_wh_ids}
            self.requested_warehouse_ids = (
                named if self.requested_warehouse_ids is None
                else self.requested_warehouse_ids & named
            )

        if product_id:
            resolved_pid = _resolve_stock_product_id(self.db, product_id)
            if resolved_pid is None:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=resolved_input_product_ids
                )
                return payload
            resolved_input_product_ids.add(str(resolved_pid))
            q = q.filter(Stock.product_id == resolved_pid)

        if product_ids:
            q = q.filter(Stock.product_id.in_(product_ids))

        if entity_buckets is not None and entity_buckets.product_codes:
            lowered = [c.lower() for c in entity_buckets.product_codes]
            code_id_rows = (
                self.db.query(Product.id)
                .filter(func.lower(Product.product_code).in_(lowered))
                .all()
            )
            resolved_input_product_ids.update(str(row.id) for row in code_id_rows)
            q = q.filter(
                Stock.product.has(func.lower(Product.product_code).in_(lowered))
            )

        # Join Product once when needed for search, status filter, or product-related sort
        sort_key = (sort or '').replace('product.category.category_name', 'category_name').replace('product.reorder_level', 'reorder_level').replace('warehouse.warehouse_name', 'warehouse_name').replace('product.product_code', 'product_code').replace('product.product_name', 'product_name')
        need_product_join = bool(query) or bool(status) or (sort and dir in ('asc', 'desc') and sort_key in ('product_code', 'product_name', 'category_name', 'reorder_level'))
        if need_product_join:
            q = q.join(Stock.product)

        if query:
            q = q.filter(
                or_(
                    Product.product_code.ilike(f"%{query}%"),
                    Product.product_name.ilike(f"%{query}%"),
                )
            )

        if quantity_operator and quantity_value:
            try:
                value = int(float(quantity_value))
                if quantity_operator == 'gt':
                    q = q.filter(Stock.quantity_available > value)
                elif quantity_operator == 'gte':
                    q = q.filter(Stock.quantity_available >= value)
                elif quantity_operator == 'lt':
                    q = q.filter(Stock.quantity_available < value)
                elif quantity_operator == 'lte':
                    q = q.filter(Stock.quantity_available <= value)
                elif quantity_operator == 'eq':
                    q = q.filter(Stock.quantity_available == value)
            except (ValueError, TypeError):
                pass

        if status and status in ('critical', 'low', 'normal', 'overstock'):
            reorder = func.coalesce(Product.reorder_level, 0)
            if status == 'critical':
                q = q.filter(Stock.quantity_available <= 0)
            elif status == 'low':
                q = q.filter(Stock.quantity_available > 0, Stock.quantity_available < reorder)
            elif status == 'normal':
                q = q.filter(Stock.quantity_available >= reorder)
            elif status == 'overstock':
                q = q.filter(Stock.quantity_available > reorder * 2)

        # Hide rows whose on-hand is 0 ONLY because the most recent ledger movement
        # was a SYSTEM_ADJUSTMENT (e.g. "missing from full stock take") - a real 0
        # (last movement a genuine import/sale to zero, or no ledger) is still shown.
        if exclude_zero_system_adjustment:
            latest_txn = (
                self.db.query(StockLedger.transaction_type)
                .filter(
                    StockLedger.product_id == Stock.product_id,
                    StockLedger.warehouse_id == Stock.warehouse_id,
                )
                .order_by(StockLedger.created_at.desc())
                .limit(1)
                .correlate(Stock)
                .scalar_subquery()
            )
            q = q.filter(
                or_(
                    Stock.quantity_on_hand != 0,
                    func.coalesce(latest_txn, '') != 'SYSTEM_ADJUSTMENT',
                )
            )

        # Snapshot of the FILTERED query before the sort block adds its joins and
        # ordering. The compact / availability blocks aggregate over exactly the
        # rows this listing would have returned - same company scope, same policy
        # warehouses, same product filters - without paging or re-deriving any of
        # it, which is what keeps the two answers from ever disagreeing.
        policy_q = q

        sort_col = None
        if sort and dir in ('asc', 'desc'):
            if sort_key in ('product_code', 'product_name', 'category_name', 'reorder_level'):
                if sort_key == 'category_name':
                    q = q.outerjoin(Product.category)
                    sort_col = ProductCategory.category_name
                elif sort_key == 'product_code':
                    sort_col = Product.product_code
                elif sort_key == 'product_name':
                    sort_col = Product.product_name
                elif sort_key == 'reorder_level':
                    sort_col = Product.reorder_level
            elif sort_key == 'warehouse_name':
                q = q.join(Stock.warehouse)
                sort_col = Warehouse.warehouse_name
            elif sort_key == 'available':
                sort_col = Stock.quantity_available
            elif sort_key == 'reserved_quantity':
                sort_col = Stock.quantity_reserved
            elif sort_key == 'quantity':
                sort_col = Stock.quantity_on_hand
            elif sort_key == 'status':
                sort_col = Stock.quantity_available
            if sort_col is not None:
                q = q.order_by(sort_col.desc() if dir == 'desc' else sort_col.asc(), Stock.id.asc())
        if sort_col is None:
            # No/unknown sort: deterministic default so results (and offset
            # pagination) are stable - product code, then warehouse name.
            if not need_product_join:
                q = q.join(Stock.product)
            q = q.join(Stock.warehouse)
            q = q.order_by(Product.product_code.asc(), Warehouse.warehouse_name.asc(), Stock.id.asc())

        total = q.count()
        offset = (page - 1) * limit
        stock_items = q.offset(offset).limit(limit).all()

        # "Data last updated" semantics: report when the stock dataset was last
        # CONFIRMED - the latest of the company's last genuine UPLOAD (BULK_IMPORT)
        # and its last accepted AutoCount push batch (`companies.
        # stock_push_confirmed_at`) - NOT the raw Stock.updated_at. Reasons the raw
        # column is wrong here:
        #  1. It is also bumped by SYSTEM_ADJUSTMENT zeroing ("missing from full stock
        #     take"), so a stock-take that zeros a discontinued row masquerades as a
        #     fresh upload.
        #  2. A discontinued SKU that is simply absent from every recent upload file
        #     gets no new BULK_IMPORT row, so its own last-import time is frozen far in
        #     the past and reads as stale even though stock data as a whole is fresh.
        #  3. The push (contract 2.5) skips `updated_at` when a value is unchanged
        #     and writes no ledger row, yet every batch confirms the stock is current.
        # So the time is per COMPANY, not per row, and every returned row is stamped
        # with its own company's time: a Mocha row never borrows Sorento's push. The
        # MCP render envelope's `last_updated_at` maxes over row `updated_at`, so the
        # footer reports the freshest company in the answer. FE never renders
        # `updated_at` (type-only field), so this override is invisible there.
        last_import_at = None
        if stock_items or policy is not None:
            answer_company_ids = {str(s.company_id) for s in stock_items if s.company_id}
            if policy is not None:
                # The summary modes carry no rows: the answer covers every company
                # the policy query spans, plus the companies of products named.
                answer_company_ids.update(
                    str(cid)
                    for (cid,) in policy_q.with_entities(Stock.company_id).distinct().all()
                    if cid
                )
                answer_company_ids.update(
                    self.company_id_by_product(list(resolved_input_product_ids or [])).values()
                )
            times = self.stock_last_updated_by_company(answer_company_ids)
            last_import_at = max(times.values(), default=None)
            for s in stock_items:
                company_time = times.get(str(s.company_id))
                if company_time is not None:
                    # Bypass the ORM instrumented descriptor so this transient
                    # override is never marked dirty / flushed to the stock row.
                    s.__dict__["updated_at"] = company_time

        payload = {
            "data": stock_items,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
        # Per-company labelling when the lookup spans more than one company - on the
        # empty path too, so "nothing in stock" can name the companies searched.
        stamp_lookup_companies(
            self.db, payload, stock_items, product_ids=resolved_input_product_ids
        )
        if entity_buckets is not None:
            payload["resolved_entities"] = entity_buckets.as_echo()
        if policy is not None:
            self._apply_stock_visibility(
                payload,
                policy=policy,
                policy_q=policy_q,
                last_import_at=last_import_at,
                requested_qty=requested_qty,
                requested_quantities=requested_quantities,
                requested_product_ids=resolved_input_product_ids,
                # Ported from PR #1118 (not merged), review round 6 (finding B): the
                # caller's own list, in order - the set above is for membership tests
                # and cannot carry one.
                product_id_order=[str(pid) for pid in (product_ids or []) if pid],
                page=page,
                limit=limit,
                # Ported from PR #1118 (not merged), SEC-S1: the SAME location
                # narrowing the on-hand query above ran with ("stock at BRW"), so the
                # supply reads cannot answer from a warehouse the question itself
                # excluded. Merged with `resolved_wh_ids` (review round 2): the
                # singular `warehouse_id` param narrows the on-hand read above but,
                # unmerged, left the three supply reads unscoped - a question asking
                # about ONE warehouse by its singular param still counted supply
                # parked at every other one.
                warehouse_ids=list({*(warehouse_ids or []), *(resolved_wh_ids or [])})
                or None,
                resolved_contact_id=resolved_contact_id,
            )

        # Data-miss (§3.3): the query resolved to a real product but returned 0 stock
        # rows. Offer data-bearing variant/neighbour alternatives on the empty path
        # ONLY - a non-empty result is byte-identical to before (AC-R1).
        #
        # LAST, and after the visibility blocks on purpose. The probe is best-effort
        # by intent but not by mechanism: its trigram/variant queries swallow their
        # own exceptions, and on Postgres a failed statement aborts the transaction,
        # so every query AFTER it raises InFailedSqlTransaction. With the blocks
        # built first, a probe that trips can lose only its own suggestions instead
        # of taking the contact's whole stock answer down with it.
        #
        # A contact on `availability` gets NO probe at all (AC-B14). Its answer
        # names OTHER products that do have stock, which is the one thing the
        # dealer mode exists to withhold: a dealer told "we have none, but try
        # these three" has been handed the product list and the fact that stock
        # exists in a location the policy hides. `compact` gets the probe like
        # `detailed` (WA-MSG-TRIM, owner 3 Oct 2026: every contact moved to
        # compact and keeps the suggestions); the has-data gate still counts only
        # the policy's locations.
        suppress_alternatives = policy is not None and policy.mode == "availability"
        if total == 0 and not suppress_alternatives:
            # Best-effort: a suggestion probe must never turn a legitimately-empty
            # listing into a 500 (AC-R1). The pre-lookup + neighbour query run after
            # the result is already computed.
            try:
                alternatives = self._stock_entity_alternatives(
                    resolved_input_product_ids,
                    policy=policy,
                )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "stock alternatives probe failed", exc_info=True
                )
                alternatives = None
            if alternatives:
                payload["alternatives"] = alternatives
                payload["relaxed_axis"] = "entity"
        return payload

    def stock_last_updated_by_company(self, company_ids) -> dict[str, datetime]:
        """`{company_id: when its stock was last confirmed}` = the latest of the
        company's last BULK_IMPORT ledger row and its last accepted AutoCount push
        batch. SYSTEM_ADJUSTMENT never counts (see the note in `list_stock`). A
        company with neither is absent. AUTOCOUNT_PUSH ledger rows are not read:
        the batch that writes one stamps the push time in the same transaction,
        at least as late, and the stamp also covers unchanged batches.

        One `max()` per company rather than a GROUP BY: each can walk
        `ix_stock_ledger_created_at` backwards and stop at the first hit, where a
        grouped max reads every BULK_IMPORT row ever written. There are a handful
        of companies."""
        from app.models.company import Company

        ids = sorted({str(c) for c in company_ids if c})
        if not ids:
            return {}
        times: dict[str, datetime] = {}
        for cid in ids:
            imported = (
                self.db.query(func.max(StockLedger.created_at))
                .filter(
                    StockLedger.transaction_type == "BULK_IMPORT",
                    StockLedger.company_id == cid,
                )
                .scalar()
            )
            if imported is not None:
                times[cid] = imported
        for cid, pushed in (
            self.db.query(Company.id, Company.stock_push_confirmed_at)
            .filter(Company.id.in_(ids), Company.stock_push_confirmed_at.isnot(None))
            .all()
        ):
            cid = str(cid)
            if cid not in times or pushed > times[cid]:
                times[cid] = pushed
        return times

    def warehouse_ids_by_code(self, codes: list[str]) -> dict[str, str]:
        """`{warehouse_code: id}` for the codes given (D1): the compact stock block names
        locations by code, and the per-warehouse open SO is keyed by id."""
        codes = [str(c) for c in codes if c]
        if not codes:
            return {}
        rows = self.db.query(Warehouse.warehouse_code, Warehouse.id).filter(Warehouse.warehouse_code.in_(codes)).all()
        return {str(code): str(wid) for code, wid in rows}

    def visible_warehouse_ids(self, policy) -> dict[str, str]:
        """`{warehouse_id: warehouse_code}` for the active warehouses a stock-visibility
        policy lets the contact see, narrowed to the ones the question named
        (`requested_warehouse_ids`): the same `warehouse_criterion` + active + requested
        filters the location lines are read through, so a total built on this set covers
        exactly the lines the reply can name."""
        from app.services.stock_visibility import warehouse_criterion

        rows = (
            self.db.query(Warehouse.id, Warehouse.warehouse_code)
            .filter(Warehouse.is_active.is_(True), warehouse_criterion(policy, Warehouse.id))
            .all()
        )
        return {
            str(wid): code
            for wid, code in rows
            if self.requested_warehouse_ids is None or str(wid) in self.requested_warehouse_ids
        }

    def on_hand_total_by_product(self, product_ids: list[str], policy=None) -> dict[str, int]:
        """`quantity_on_hand` summed over EVERY warehouse row of each product (review round
        2, S2): the per-product "Available" line must never be a sum over the returned
        PAGE, which is short of the truth for a product held in more warehouses than the
        page limit. Company scope ANDed in by hand - a column-only aggregate is where the
        session listener's scope is lost (`order_service.stamp_order_summary`)."""
        from app.services.company_scope import build_company_predicate, get_company_scope

        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}
        q = self.db.query(Stock.product_id, func.sum(Stock.quantity_on_hand)).filter(
            Stock.product_id.in_(ids)
        )
        pred = build_company_predicate(Stock, get_company_scope(self.db))
        if pred is not None:
            q = q.filter(pred)
        if policy is not None:
            # Under a contact's policy the total covers the locations it may see, never a
            # hidden one (STOCK-TOTAL-OS-SCOPE): the same rule the rows were filtered by.
            q = q.filter(Stock.warehouse_id.in_(list(self.visible_warehouse_ids(policy))))
        return {str(pid): int(qty or 0) for pid, qty in q.group_by(Stock.product_id).all()}

    def no_feed_company_ids(self) -> set[str]:
        """Every company whose AutoCount SO feed is not connected
        (``companies.so_feed_live = false``) - the gate ``_with_sellable`` uses to
        withhold ``open_so_qty`` / ``sellable`` for their rows (PLAN
        company-so-feed-flag). No ``company_ids`` filter: the table holds a
        handful of rows, so this is cheaper called once than filtered by
        candidate ids on every stock page - and an empty result lets the caller
        skip `company_id_by_product` entirely on the common (all-feed-on) path."""
        from app.models.company import Company

        rows = self.db.query(Company.id).filter(Company.so_feed_live.is_(False)).all()
        return {str(r[0]) for r in rows}

    def company_id_by_product(self, product_ids: list[str]) -> dict[str, str]:
        """``{product_id: company_id}`` for the products given - a compact or
        synthesised summary entry carries no company of its own, so the feed gate
        resolves it through the product (PLAN company-so-feed-flag)."""
        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}
        rows = (
            self.db.query(Product.id, Product.company_id)
            .filter(Product.id.in_(ids))
            .all()
        )
        return {str(pid): str(cid) for pid, cid in rows if cid}

    def open_so_qty_by_product(self, product_ids: list[str]) -> dict[str, int]:
        """Open (not-yet-DO'd) SO quantity per PRODUCT, across every warehouse (A2).

        The product TOTAL. `open_so_qty_by_product_warehouse` is the per-warehouse
        split; both exist because a per-warehouse row and the product summary row
        are two different questions and the review found the first cut answering
        the second one everywhere (should-fix 5).

        An open DO (created, not yet delivered) is NOT subtracted here - AutoCount
        deducts stock at DO creation, so it is already out of `on_hand` (AC-904b).
        """
        from app.models.order import SalesOrderLine

        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}
        delta = SalesOrderLine.qty_ordered - SalesOrderLine.qty_delivered
        rows = (
            self.db.query(SalesOrderLine.product_id, func.sum(delta).label("open_qty"))
            .filter(
                SalesOrderLine.product_id.in_(ids),
                SalesOrderLine.line_status == "open",
                delta > 0,
            )
            .group_by(SalesOrderLine.product_id)
            .all()
        )
        return {str(pid): int(qty or 0) for pid, qty in rows}

    def open_so_qty_by_product_warehouse(
        self, product_ids: list[str]
    ) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        """The same open SO quantity, split the way the plan says to spend it (A2).

        Returns `({(product_id, warehouse_id): qty}, {product_id: unlocated_qty})`.

        The first cut subtracted the PRODUCT-WIDE open SO from EVERY per-warehouse row,
        so a product with 100 open SO across two warehouses read as 100 unsellable in
        each - "Sellable 0 (oversold by 80)" against a warehouse holding 20, which is
        arithmetic the customer can see is wrong. The plan is explicit: per warehouse
        where the SO line has one, and the remainder (lines with no `warehouse_id`) on
        the PRODUCT TOTAL row only, never spread across the warehouse rows (under a
        contact's policy it is not shown at all, STOCK-TOTAL-OS-SCOPE). A0 measured
        that remainder at 0.8% of open lines, which is why it is a small correction and
        not a redesign - but a small correction applied to every row is still wrong on
        every row.
        """
        from app.models.order import SalesOrderLine

        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}, {}
        delta = SalesOrderLine.qty_ordered - SalesOrderLine.qty_delivered
        rows = (
            self.db.query(
                SalesOrderLine.product_id,
                SalesOrderLine.warehouse_id,
                func.sum(delta).label("open_qty"),
            )
            .filter(
                SalesOrderLine.product_id.in_(ids),
                SalesOrderLine.line_status == "open",
                delta > 0,
            )
            .group_by(SalesOrderLine.product_id, SalesOrderLine.warehouse_id)
            .all()
        )
        by_pair: dict[tuple[str, str], int] = {}
        unlocated: dict[str, int] = {}
        for pid, wid, qty in rows:
            if wid:
                by_pair[(str(pid), str(wid))] = int(qty or 0)
            else:
                unlocated[str(pid)] = unlocated.get(str(pid), 0) + int(qty or 0)
        return by_pair, unlocated

    # ------------------------------------------------------ stock visibility

    def _apply_stock_visibility(
        self,
        payload: dict,
        *,
        policy,
        policy_q,
        last_import_at,
        requested_qty: Optional[int],
        requested_product_ids: set[str],
        page: int,
        limit: int,
        requested_quantities: Optional[dict] = None,
        warehouse_ids: Optional[list[str]] = None,
        product_id_order: Optional[list[str]] = None,
        resolved_contact_id: Optional[str] = None,
    ) -> None:
        """Attach the visibility block(s) and, for the two summary modes, empty `data`.

        `compact` and `availability` return NO rows at all rather than rows with the
        quantity stripped: a stripped row still names every location it was found in
        and how many there were, and the raw (non-render) response is readable by any
        direct MCP caller, so empty is the only shape that cannot leak.
        """
        from sqlalchemy import func, or_ as sa_or
        from app.services.stock_visibility import warehouse_criterion

        payload["stock_visibility"] = {
            "mode": policy.mode,
            "source": policy.source,
            # Echoed in every mode, `availability` included: it names no location,
            # so it discloses nothing, and n8n reads it to phrase the reply.
            "hide_zero_locations": policy.hide_zero_locations,
        }
        if policy.mode != "availability":
            # NULL stays null on the wire ONLY when the policy names neither list -
            # "every location" is a different answer from "these named ones", and
            # collapsing it to a list would make the admin card show a snapshot
            # that silently stops tracking new warehouses. An exclude-only policy
            # is a NAMED set too (every active warehouse except these), so it
            # echoes the same way an include list does - the same
            # `warehouse_criterion` the balance itself filters with, so the
            # echoed codes are exactly what the balance can cover. Inactive
            # locations are dropped: the listing never answers from one, so
            # naming it promises a place no row can come from.
            #
            # The dealer mode omits the key entirely. It is a list of the exact
            # locations that mode exists to keep out of the reply, and an echo is
            # still a disclosure.
            warehouse_codes = None
            if policy.warehouse_ids is not None or policy.excluded_warehouse_ids is not None:
                warehouse_codes = sorted(
                    code
                    for (code,) in self.db.query(Warehouse.warehouse_code)
                    .filter(
                        Warehouse.is_active.is_(True),
                        warehouse_criterion(policy, Warehouse.id),
                    )
                    .all()
                )
            payload["stock_visibility"]["warehouse_codes"] = warehouse_codes

        # n8n's "_Data last updated_" footer reads the MCP envelope's
        # `last_updated_at`, which is walked out of the body. The summary modes
        # carry no rows to walk, so the payload states it directly.
        payload["last_updated_at"] = last_import_at

        if policy.mode == "detailed":
            return

        # The products to answer for = the ones with stock the policy allows, PLUS
        # every product the contact actually named. A named product with no row in
        # any allowed location would otherwise vanish, and its absence is
        # unreadable: "we have none left" and "I never found what you asked for"
        # arrive as the same silence, which is precisely the question a dealer
        # asks. A named id that resolves to no product at all still gets nothing -
        # inventing a block would answer about a product that does not exist.
        #
        # PAGED, over products, before anything is aggregated. "What stock do you
        # have?" names no product, so the candidate set is the whole catalogue -
        # thousands of blocks in one reply, and a `pagination.total` of 0 beside
        # them. The caller's page/limit has to mean the same thing here as it does
        # for rows.
        named_ids = {str(pid) for pid in (requested_product_ids or set()) if pid}
        with_stock = policy_q.with_entities(Stock.product_id).distinct().subquery()
        candidate_filter = Product.id.in_(self.db.query(with_stock.c.product_id))
        if named_ids:
            candidate_filter = sa_or(candidate_filter, Product.id.in_(named_ids))
        candidates = self.db.query(Product.id).filter(candidate_filter)

        total_products = candidates.count()
        page_ids = [
            str(row[0])
            for row in candidates.order_by(
                Product.product_code.asc(), Product.id.asc()
            )
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        ]

        rows = (
            policy_q.with_entities(
                Stock.product_id.label("product_id"),
                Warehouse.warehouse_code.label("warehouse_code"),
                func.sum(Stock.quantity_on_hand).label("on_hand"),
            )
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .filter(Stock.product_id.in_(page_ids))
            .group_by(Stock.product_id, Warehouse.warehouse_code)
            .all()
            if page_ids
            else []
        )
        products = (
            self.db.query(Product).filter(Product.id.in_(page_ids)).all()
            if page_ids
            else []
        )
        products_by_id = {str(p.id): p for p in products}

        per_product: dict[str, list] = {pid: [] for pid in page_ids}
        for row in rows:
            per_product.setdefault(str(row.product_id), []).append(row)

        # `page_ids` is already in product_code order - the same order the query
        # paged on, so page 2 continues where page 1 stopped.
        ordered_ids = [pid for pid in page_ids if pid in products_by_id]

        payload["data"] = []
        payload["pagination"] = {"total": total_products, "page": page, "limit": limit}
        # `empty` is what the MCP escalation hint reads. These modes clear `data`
        # by design, so a real answer - 500 units in BRW, or a yes - was being
        # labelled empty and shipped with "We don't have that information".
        payload["empty"] = total_products == 0

        if policy.mode == "compact":
            payload["stock_summary"] = [
                {
                    "product_id": pid,
                    "product_code": getattr(products_by_id.get(pid), "product_code", None),
                    "product_name": getattr(products_by_id.get(pid), "product_name", None),
                    # Summed over EVERY allowed location, including the ones the
                    # flag withholds. A withheld line held none of the product, so
                    # the total it fed is the same number either way - and the
                    # total is the answer to the question that was asked.
                    "total_on_hand": sum(int(r.on_hand or 0) for r in per_product[pid]),
                    "locations": [
                        {
                            "warehouse_code": r.warehouse_code,
                            "quantity_on_hand": int(r.on_hand or 0),
                        }
                        for r in sorted(
                            per_product[pid], key=lambda r: r.warehouse_code or ""
                        )
                        # `hide_zero_locations`: a line reading `BRW-BB: 0` is noise
                        # in a WhatsApp message. A NEGATIVE line stays - it is an
                        # anomaly, not an absence - and a product whose lines all go
                        # keeps its block, reading `Total: 0`, because dropping the
                        # block would say "I never found it" instead of "none left".
                        if not (
                            policy.hide_zero_locations and int(r.on_hand or 0) == 0
                        )
                    ],
                    "flags": {
                        "discontinued": bool(
                            getattr(products_by_id.get(pid), "is_discontinued", False)
                        )
                    },
                }
                for pid in ordered_ids
            ]
            return

        # Ported from PR #1118 (not merged), review round 6, finding B: when the
        # CALLER named the products, that list is the order the answer is read out in -
        # the dealer hears their own question back. `ordered_ids` above is
        # `product_code` asc, which is the right order for the catalogue case ("what
        # stock do you have?", nothing named) and the wrong one here: the reply noted
        # "MHS1028 x 60, MWT5727SS-CR x 5" for a dealer who had asked the other way
        # round. Anything the caller did not name (a product with stock that the page
        # picked up) keeps its page position, after the named ones.
        # Chatbot stock ask v2 S3 fix round 1, Blocking 2 (R10/AC-SA315): this sort
        # runs AFTER the `compact` return above, not before it - `compact`'s
        # `stock_summary` order is product_code order (R10 "Compact mode ... unchanged
        # by v2"), never the caller's asked order, which is availability-only.
        asked_order = [str(pid) for pid in (product_id_order or []) if pid]
        if asked_order:
            rank = {pid: index for index, pid in enumerate(asked_order)}
            unranked = len(rank)
            ordered_ids = [
                pid
                for _, pid in sorted(
                    ((rank.get(pid, unranked), pid) for pid in ordered_ids),
                    key=lambda pair: pair[0],
                )
            ]

        # Ported from PR #1118 (not merged), D35, review round 11: a DEALER question is
        # always about a product, so a stock ask that names none is answered by asking
        # for the code - never by returning the catalogue page as a question. The
        # tool's own contract makes every filter optional ("call with none to span
        # every product"), which is right for the staff grid and for n8n and wrong
        # here: the block came back with one `needs_quantity` entry per catalogue row
        # and the dealer was asked to quantify fifty products they had never mentioned.
        #
        # The empty block is also what keeps the engine honest: no entries, no slots,
        # no task (`turn/task.py::tasks_after_reply`). `needs_product` is what the MCP
        # presenter renders its one sentence from - the same division of labour D25
        # already sets, the server deciding and the presenter saying.
        #
        # `compact` and `detailed` return above and are untouched: they answer with
        # rows and locations, and a caller asking either of them for a page means it.
        if not named_ids:
            payload["stock_visibility"]["needs_product"] = True
            payload["stock_availability"] = []
            return

        # availability: one of four fixed branches (chatbot stock ask v2 S3, R6/R14),
        # judged against the allowed locations only. No quantity of OURS reaches the
        # block - not the total, not the per-location split, not on hand/incoming
        # themselves - because a number here is exactly what the dealer policy exists
        # to withhold; only the dealer's own asked quantity (echoed back) and the ETA
        # date ever appear. Replaces #1118's verdict()/spo_allocations/purchase-order/
        # threshold read (ported as-is in an earlier step of this slice to reach
        # #1118 parity; superseded here, `app/services/stock_verdict.py` deleted).
        from app.models.order import SalesOrderLine
        from app.models.product import ProductCategory
        from app.models.resources import Attachment
        from app.services.incoming_stock_service import (
            _attachment_payload,
            earliest_packing_list_shipment,
        )
        from app.services.eta_policy import rules_for_contact, visible_eta
        from app.services.stock_ask_branch import branch as compute_branch
        from app.services.stock_ask_branch import short_of
        from app.services.stock_ask_limits import effective as effective_limits

        # SEC-S1 (security review, round 1, kept from #1118): `warehouse_criterion` is only HALF of what
        # the on-hand read filters by. That query also carries `Warehouse.is_active`
        # (a retired location is not somewhere this contact can be supplied from) and
        # the caller's own `warehouse_ids=` narrowing ("stock at BRW"), and a supply
        # read that skipped both counted an allocation bound for a retired warehouse,
        # or bound for MWH under a question that asked only about BRW, towards a
        # verdict the dealer then read as a promise. Nit, review round 1: the
        # incoming and purchase-order reads this scope also applied to under #1118
        # are gone by R5 (this slice reads incoming stock a different way, ANY
        # location, never scoped here) - only the open SO read below still uses it.
        def _supply_scope(column):
            scoped = [
                column.isnot(None),
                warehouse_criterion(policy, column),
                column.in_(
                    self.db.query(Warehouse.id).filter(Warehouse.is_active.is_(True))
                ),
            ]
            if warehouse_ids:
                scoped.append(column.in_(warehouse_ids))
            return scoped

        # D2: open SO subtracted from on-hand, per the SAME warehouse_criterion as
        # `on hand` itself. A line with no destination is never subtracted - it
        # cannot be placed at any warehouse the policy names, allowed or not.
        so_rows = (
            self.db.query(
                SalesOrderLine.product_id,
                func.sum(SalesOrderLine.qty_ordered - SalesOrderLine.qty_delivered).label(
                    "open_qty"
                ),
            )
            .filter(
                SalesOrderLine.product_id.in_(page_ids),
                *_supply_scope(SalesOrderLine.warehouse_id),
                SalesOrderLine.line_status == "open",
                SalesOrderLine.qty_ordered > SalesOrderLine.qty_delivered,
            )
            .group_by(SalesOrderLine.product_id)
            .all()
            if page_ids
            else []
        )
        open_so_by_product = {str(r.product_id): float(r.open_qty or 0) for r in so_rows}

        def _resolve_ask(pid: str) -> Optional[int]:
            # D20 (kept from #1118): the per-product map wins; the scalar fills
            # whatever it does not name. Either way, below 1 reads as NOT PROVIDED -
            # the same rule the scalar alone has always followed (a 0 is a parse
            # artefact, not a demand).
            if requested_quantities and pid in requested_quantities:
                ask = requested_quantities[pid]
            else:
                ask = requested_qty
            if ask is not None and ask < 1:
                ask = None
            return ask

        # R2: X and Y resolved per product against its OWN category - one query for
        # every category referenced on this page, no parent walk (R12).
        category_ids = {
            getattr(products_by_id.get(pid), "category_id", None) for pid in page_ids
        }
        category_ids.discard(None)
        categories_by_id = (
            {
                str(c.id): c
                for c in self.db.query(ProductCategory).filter(
                    ProductCategory.id.in_(category_ids)
                )
            }
            if category_ids
            else {}
        )

        # R5: the earliest still-incoming shipment with a packing list, any
        # location, per product on this page.
        contact_rules = rules_for_contact(self.db, resolved_contact_id)
        eta_by_product = earliest_packing_list_shipment(
            self.db, page_ids, regions=contact_rules.regions
        )

        # R7: the asking contact's own "Packing list allowed" toggle, resolved once
        # off `resolved_contact_id` - the SAME internal id this whole call's policy
        # was resolved against (AC-SA311/AC-SA312: a raw GET never carries the file
        # for a contact who may not have it, the same rule that already withholds
        # every quantity of ours).
        #
        # Issue #1328: read through `eta_policy.rules_for_contact`, the one place the
        # incoming routes read it too, together with the contact's ETA offset switch.
        # (`contact_rules` is read above: its regions also bound the read in R5.)

        # Review round 5 (kept from #1118): ONE entry per product CODE. A dealer
        # contact whose companies both carry the same code resolves it to two
        # products, and the block answered for each - the rows collapse HERE, where
        # the figures are, every number summed across the merged ids before
        # `branch()` judges them.
        #
        # A row with no `product_code` merges with nothing (the sentinel key):
        # "unnamed" is not an identity, and two of them are not the same product.
        # `detailed` and `compact` return above and are untouched - they name
        # locations and quantities per row, and a merged row has no one location to
        # name.
        merged_ids: dict[str, list[str]] = {}
        for pid in ordered_ids:
            code = getattr(products_by_id.get(pid), "product_code", None)
            # A NUL cannot appear in a product code, so a codeless row's key is
            # unique to that row and merges with nothing.
            key = code.strip().lower() if code else "\x00" + pid
            merged_ids.setdefault(key, []).append(pid)

        entries = []
        for group_ids in merged_ids.values():
            # `ordered_ids` is already in page order, so the first id of a group is the
            # first row of it on this page. That id is what the entry, the task's slot
            # and the next fetch's `requested_quantities` key all carry - and, below,
            # what X/Y/category are resolved against.
            pid = group_ids[0]
            # D20, across the merge: a quantity given for ANY of the merged ids is a
            # quantity for the merged product. The scalar `requested_qty` fallback
            # resolves the same for every id, so the first answer found is the answer.
            ask = next(
                (a for a in (_resolve_ask(gid) for gid in group_ids) if a is not None),
                None,
            )
            product = products_by_id.get(pid)
            category = categories_by_id.get(str(getattr(product, "category_id", None)))
            entry = {
                "product_id": pid,
                "product_code": getattr(product, "product_code", None),
                "product_name": getattr(product, "product_name", None),
                "needs_quantity": ask is None,
                "requested_qty": ask,
                "branch": None,
                "cap_unset": None,
                "category_name": None,
                "eta": None,
                "packing_list": None,
                # AVAIL-MODE-REPLIES rule 2: set only on an `in_stock` answer short of
                # the ask (within X); the one figure of ours this mode ever returns.
                "available_qty": None,
            }
            if ask is not None and product is not None and category is not None:
                x, y = effective_limits(product, category)
                # R2: X unset (product AND its own category both NULL) is a fact the
                # B1 agent-notification reason needs ("no cap set for <category>"),
                # distinct from a category that explicitly opted in with X = 0.
                cap_unset = (
                    product.chatbot_max_qty is None
                    and category.chatbot_max_qty is None
                )
                on_hand_total = sum(
                    int(r.on_hand or 0) for gid in group_ids for r in per_product[gid]
                )
                net_available = on_hand_total - int(
                    sum(open_so_by_product.get(gid, 0) for gid in group_ids)
                )
                shipment_candidates = [
                    eta_by_product[gid] for gid in group_ids if gid in eta_by_product
                ]
                # D10-style, across the merge: the earliest of the merged ids' own
                # shipments is the one that answers - the same "earliest wins" rule
                # `earliest_packing_list_shipment` already applies per product id.
                shipment = (
                    min(shipment_candidates, key=lambda s: s[1])
                    if shipment_candidates
                    else None
                )
                shipment_date = shipment[1] if shipment else None
                entry["branch"] = compute_branch(ask, x, net_available, shipment_date)
                entry["available_qty"] = short_of(ask, x, net_available)
                entry["cap_unset"] = cap_unset
                entry["category_name"] = category.category_name
                if entry["branch"] == "incoming" and shipment is not None:
                    _shipment_id, eta_date, attachment_id = shipment
                    # Issue #1328: `+ y` only when this contact's ETA offset switch is on.
                    entry["eta"] = visible_eta(eta_date, y, contact_rules).strftime("%d/%m/%Y")
                    if contact_rules.packing_list_allowed:
                        attachment = (
                            self.db.query(Attachment)
                            .filter(Attachment.id == attachment_id)
                            .first()
                        )
                        entry["packing_list"] = _attachment_payload(attachment)
            entries.append(entry)

        payload["stock_availability"] = entries

    def _stock_entity_alternatives(
        self,
        product_ids: set[str],
        *,
        policy: Optional["Policy"] = None,
    ) -> list[dict]:
        """Data-bearing variant/neighbour alternatives for an empty stock result.

        Only fires when exactly ONE input product resolved (otherwise "which product's
        neighbours?" is undefined). The has-data gate = product has any active-warehouse
        stock row with quantity_on_hand > 0, which inherently respects the same
        SYSTEM_ADJUSTMENT-zero exclusion the listing uses (a system-adjusted-to-0 row is
        qoh == 0, so excluded).

        ``policy`` narrows that gate to the locations the caller's visibility policy
        allows (None = all of them, the staff/legacy case), through the same
        `warehouse_criterion` the main listing filters with - include list AND/OR
        exclusion. A suggestion judged on hidden stock is a promise the next question
        cannot keep: the contact asks about the neighbour and is told there is none.
        """
        from app.services.stock_visibility import warehouse_criterion

        if len(product_ids) != 1:
            return []
        pid = next(iter(product_ids))
        prod = (
            self.db.query(Product.product_code)
            .filter(Product.id == pid)
            .first()
        )
        if not prod or not prod.product_code:
            return []

        def _has_stock(candidate_ids: list[str]) -> set[str]:
            if not candidate_ids:
                return set()
            q = self.db.query(Stock.product_id).filter(
                Stock.product_id.in_(candidate_ids),
                Stock.quantity_on_hand > 0,
                Stock.warehouse.has(Warehouse.is_active.is_(True)),
            )
            if policy is not None:
                q = q.filter(warehouse_criterion(policy, Stock.warehouse_id))
            rows = q.distinct().all()
            return {str(row.product_id) for row in rows}

        from app.services.entity_resolver import find_entity_neighbours_with_data

        return find_entity_neighbours_with_data(
            self.db, prod.product_code, has_data=_has_stock
        )

    def bulk_delete_stock(self, stock_ids: list[str]) -> dict:
        """Delete multiple stock records by id. Returns deleted_count and message."""
        if not stock_ids:
            return {"deleted_count": 0, "message": "No stock records to delete."}
        deleted = self.db.query(Stock).filter(Stock.id.in_(stock_ids)).delete(synchronize_session=False)
        self.db.commit()
        return {"deleted_count": deleted, "message": f"Deleted {deleted} stock record(s)."}

    def list_stock_ledger(
        self,
        page: int = 1,
        limit: int = 50,
        product_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        transaction_type: Optional[str] = None
    ):
        """List stock ledger entries with pagination and filtering."""
        from app.schemas.common import ListResponse
        from app.schemas.inventory import StockLedgerResponse
        from app.models.user import User
        from sqlalchemy.orm import selectinload
        
        q = self.db.query(StockLedger).options(
            selectinload(StockLedger.product),
            selectinload(StockLedger.warehouse)
        ).filter(StockLedger.warehouse.has(Warehouse.is_active.is_(True)))
        if product_id:
            resolved_pid = _resolve_stock_product_id(self.db, product_id)
            if resolved_pid is None:
                return ListResponse(
                    data=[],
                    pagination={"total": 0, "page": page, "limit": limit},
                )
            q = q.filter(StockLedger.product_id == resolved_pid)
        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return ListResponse(
                    data=[],
                    pagination={"total": 0, "page": page, "limit": limit},
                )
            q = q.filter(StockLedger.warehouse_id.in_(warehouse_ids))
        if transaction_type:
            q = q.filter(StockLedger.transaction_type == transaction_type)

        total = q.count()
        offset = (page - 1) * limit
        entries = q.order_by(StockLedger.created_at.desc()).offset(offset).limit(limit).all()

        user_ids = {entry.created_by for entry in entries if entry.created_by}
        user_map = {}
        if user_ids:
            users = self.db.query(User).filter(User.id.in_(user_ids)).all()
            user_map = {user.id: user.name or user.email for user in users}

        response_entries = []
        for entry in entries:
            response = StockLedgerResponse.model_validate(entry)
            response.created_by_name = user_map.get(entry.created_by)
            response_entries.append(response)

        return ListResponse(
            data=response_entries,
            pagination={"total": total, "page": page, "limit": limit}
        )

    def get_stock_ledger_by_stock(self, product_id: str, warehouse_id: str, page: int = 1, limit: int = 50):
        """Get stock ledger entries for a specific product-warehouse combination."""
        from app.schemas.common import ListResponse
        from app.schemas.inventory import StockLedgerResponse
        from app.models.user import User
        from sqlalchemy.orm import selectinload

        resolved_pid = _resolve_stock_product_id(self.db, product_id)
        if resolved_pid is None:
            return ListResponse(
                data=[],
                pagination={"total": 0, "page": page, "limit": limit},
            )

        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is None or not warehouse_ids:
            return ListResponse(
                data=[],
                pagination={"total": 0, "page": page, "limit": limit},
            )

        q = self.db.query(StockLedger).options(
            selectinload(StockLedger.product),
            selectinload(StockLedger.warehouse)
        ).filter(
            StockLedger.product_id == resolved_pid,
            StockLedger.warehouse_id.in_(warehouse_ids),
            StockLedger.warehouse.has(Warehouse.is_active.is_(True)),
        )

        total = q.count()
        offset = (page - 1) * limit
        entries = q.order_by(StockLedger.created_at.desc()).offset(offset).limit(limit).all()

        user_ids = {entry.created_by for entry in entries if entry.created_by}
        user_map = {}
        if user_ids:
            users = self.db.query(User).filter(User.id.in_(user_ids)).all()
            user_map = {user.id: user.name or user.email for user in users}

        response_entries = []
        for entry in entries:
            response = StockLedgerResponse.model_validate(entry)
            response.created_by_name = user_map.get(entry.created_by)
            response_entries.append(response)

        return ListResponse(
            data=response_entries,
            pagination={"total": total, "page": page, "limit": limit}
        )

    def get_all_stock_for_export(
        self,
        warehouse_id: Optional[str] = None,
        product_id: Optional[str] = None,
        product_ids: Optional[list[str]] = None,
        quantity_operator: Optional[str] = None,
        quantity_value: Optional[str] = None,
        entities: Optional[list[str]] = None,
    ):
        """Get all stock for export (no pagination).

        Args:
            warehouse_id: Optional warehouse filter
            product_id: Optional product filter
            quantity_operator: One of 'gt', 'gte', 'lt', 'lte', 'eq' for available quantity filtering
            quantity_value: Numeric value to compare against available quantity
            entities: Free-text bag - resolved product matches narrow Stock.product_id
                (other resolved types are echoed but do not filter).
        """
        from sqlalchemy import func
        from sqlalchemy.orm import selectinload
        from app.models.product import Product
        from app.services.entity_resolver import (
            EntityFilterBuckets,
            resolve_entities_to_filters,
        )

        entity_buckets: Optional[EntityFilterBuckets] = None
        if entities:
            entity_buckets = resolve_entities_to_filters(
                self.db,
                entities,
                allowed_entity_types=(
                    "product", "customer", "customer_order", "transporter",
                    "inbound_shipment", "spo_allocation", "grn", "promotion",
                    "attachment", "form",
                ),
            )
            if not entity_buckets.product_codes:
                return []

        # Use selectinload to eagerly load relationships and avoid N+1 queries
        # selectinload is better for one-to-many/many-to-one and doesn't cause duplicate rows
        q = self.db.query(Stock).options(
            selectinload(Stock.product),
            selectinload(Stock.warehouse)
        ).filter(Stock.warehouse.has(Warehouse.is_active.is_(True)))

        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return []
            q = q.filter(Stock.warehouse_id.in_(warehouse_ids))

        if product_id:
            resolved_pid = _resolve_stock_product_id(self.db, product_id)
            if resolved_pid is None:
                return []
            q = q.filter(Stock.product_id == resolved_pid)

        if product_ids:
            q = q.filter(Stock.product_id.in_(product_ids))

        if entity_buckets is not None and entity_buckets.product_codes:
            lowered = [c.lower() for c in entity_buckets.product_codes]
            q = q.filter(
                Stock.product.has(func.lower(Product.product_code).in_(lowered))
            )

        # Filter by available quantity using the quantity_available column
        if quantity_operator and quantity_value:
            try:
                value = int(float(quantity_value))
                
                if quantity_operator == 'gt':
                    q = q.filter(Stock.quantity_available > value)
                elif quantity_operator == 'gte':
                    q = q.filter(Stock.quantity_available >= value)
                elif quantity_operator == 'lt':
                    q = q.filter(Stock.quantity_available < value)
                elif quantity_operator == 'lte':
                    q = q.filter(Stock.quantity_available <= value)
                elif quantity_operator == 'eq':
                    q = q.filter(Stock.quantity_available == value)
            except (ValueError, TypeError):
                # Invalid quantity value, ignore filter
                pass
        
        # Fetch all stock items without pagination
        stock_items = q.all()
        
        return stock_items

    def bulk_import_stock(self, stock_data: list[dict], user_id: str, validate_only: bool = False, outcome=None):
        """Bulk import stock from Excel data using bulk operations for performance.

        Args:
            stock_data: List of dictionaries containing stock data from Excel
            user_id: ID of the user performing the import
            validate_only: If True, run validation only (no DB writes); return errors/warnings.

        Returns:
            dict with created, updated, skipped counts and errors; if validate_only, valid/errors/warnings/summary.

        outcome: optional ImportOutcome recorder so every row's fate is captured for
            the job detail page. None for non-job callers.
        """
        from app.services import import_outcome_codes as _oc
        from app.services.import_outcome import ImportOutcome as _ImportOutcome

        if outcome is None:
            outcome = _ImportOutcome(None, persist=False)

        def _stock_identity(_row: dict) -> dict:
            return {
                "product_code": _row.get("_product_code") if isinstance(_row, dict) else None,
                "warehouse": _row.get("_warehouse_name") if isinstance(_row, dict) else None,
            }

        created = 0
        updated = 0
        errors = []
        error_records = []
        warnings = []
        skipped_rows = 0
        import_session_id = str(uuid.uuid4())
        start_time = time.monotonic()
        
        # Column mapping from Excel headers to database fields
        # Include all variations that might appear in exported Excel files
        column_mapping = {
            'id': 'id',
            'ID': 'id',
            'product_id': 'product_id',
            'Product ID': 'product_id',
            'product_code': 'product_code',  # For lookup
            'Product Code': 'product_code',
            'Item Code': 'product_code',
            'Item code': 'product_code',
            'item code': 'product_code',
            'ItemCode': 'product_code',
            'warehouse_id': 'warehouse_id',
            'Warehouse ID': 'warehouse_id',
            'warehouse': 'warehouse_name',  # For lookup
            'Warehouse': 'warehouse_name',
            'warehouse_name': 'warehouse_name',
            'Warehouse Name': 'warehouse_name',
            'warehouse_code': 'warehouse_code',
            'Warehouse Code': 'warehouse_code',
            'Location': 'warehouse_code',
            'location': 'warehouse_code',
            'zone_id': 'zone_id',
            'Zone ID': 'zone_id',
            'quantity_on_hand': 'quantity_on_hand',
            'Quantity On Hand': 'quantity_on_hand',
            'quantity': 'quantity_on_hand',  # Alias for quantity_on_hand (frontend uses 'quantity')
            'Quantity': 'quantity_on_hand',
            'Total': 'quantity_on_hand',
            'Total Quantity': 'quantity_on_hand',
            'total quantity': 'quantity_on_hand',  # Lowercase variant
            'total': 'quantity_on_hand',  # Lowercase variant
            'On Hand Qty': 'quantity_on_hand',
            'On hand qty': 'quantity_on_hand',
            'on hand qty': 'quantity_on_hand',
            'On Hand': 'quantity_on_hand',
            'reserved_quantity': 'quantity_reserved',
            'Reserved Quantity': 'quantity_reserved',
            'quantity_reserved': 'quantity_reserved',
            'Quantity Reserved': 'quantity_reserved',
            'reserved quantity': 'quantity_reserved',  # Lowercase variant
            'Reserved': 'quantity_reserved',
            'quantity_available': 'quantity_available',
            'Available': 'quantity_available',
            'available': 'quantity_available',
            'quantity_damaged': 'quantity_damaged',
            'Quantity Damaged': 'quantity_damaged',
            'quantity damaged': 'quantity_damaged',  # Lowercase variant
            'reorder_point': 'reorder_point',
            'Reorder Point': 'reorder_point',
            'reorder point': 'reorder_point',  # Lowercase variant
            'Item Description': 'item_description',
            'Item description': 'item_description',
            'item description': 'item_description',
        }
        
        def parse_int(value):
            """Parse integer value, handling Excel formatting."""
            if value is None or value == '':
                return 0
            # Handle string values that might be formatted numbers
            if isinstance(value, str):
                # Remove common formatting characters
                value = value.strip().replace(',', '').replace('$', '').replace('RM', '').replace(' ', '')
                if value == '' or value == '-':
                    return 0
            try:
                # Try to parse as float first (handles decimals), then convert to int
                return int(float(str(value)))
            except (ValueError, TypeError):
                return 0
        
        # Step 1: Build lookup dictionaries for products and warehouses (bulk lookup)
        product_codes_to_lookup = set()
        warehouse_names_to_lookup = set()
        warehouse_codes_to_lookup = set()
        stock_ids_to_lookup = set()
        
        # First pass: collect all lookup values
        for row_data in stock_data:
            for excel_key, value in row_data.items():
                db_key = column_mapping.get(excel_key, excel_key.lower())
                if db_key == 'product_code' and value:
                    product_codes_to_lookup.add(str(value).strip())
                elif db_key == 'warehouse_name' and value:
                    warehouse_names_to_lookup.add(str(value).strip())
                elif db_key == 'warehouse_code' and value:
                    warehouse_codes_to_lookup.add(str(value).strip())
                elif db_key == 'id' and value:
                    stock_ids_to_lookup.add(str(value).strip())
        
        # Bulk lookup products by code (case-insensitive)
        product_code_map = {}
        product_code_lower_map = {}
        if product_codes_to_lookup:
            # Use case-insensitive matching for product codes
            from sqlalchemy import func
            products = self.db.query(Product).filter(
                func.lower(Product.product_code).in_([code.lower() for code in product_codes_to_lookup])
            ).all()
            # Create maps: one with original case, one with lowercase
            for p in products:
                product_code_map[p.product_code] = p.id
                product_code_lower_map[p.product_code.lower()] = p.id
        
        # Bulk lookup warehouses by code (case-insensitive)
        warehouse_code_map = {}
        warehouse_code_lower_map = {}
        if warehouse_codes_to_lookup:
            warehouses = self.db.query(Warehouse).filter(
                func.lower(Warehouse.warehouse_code).in_([code.lower() for code in warehouse_codes_to_lookup])
            ).all()
            for w in warehouses:
                warehouse_code_map[w.warehouse_code] = w.id
                warehouse_code_lower_map[w.warehouse_code.lower()] = w.id

        # Bulk lookup warehouses by name (case-insensitive) - fallback
        warehouse_name_map = {}
        warehouse_name_lower_map = {}
        if warehouse_names_to_lookup:
            warehouses = self.db.query(Warehouse).filter(
                func.lower(Warehouse.warehouse_name).in_([name.lower() for name in warehouse_names_to_lookup])
            ).all()
            for w in warehouses:
                warehouse_name_map[w.warehouse_name] = w.id
                warehouse_name_lower_map[w.warehouse_name.lower()] = w.id
        
        # Bulk lookup existing stock by IDs
        existing_stock_by_id = {}
        if stock_ids_to_lookup:
            stocks_by_id = self.db.query(Stock).filter(Stock.id.in_(stock_ids_to_lookup)).all()
            existing_stock_by_id = {s.id: s for s in stocks_by_id}
        
        # Step 2: Process all rows and prepare data
        rows_to_create = []
        rows_to_update = []
        product_warehouse_pairs = set()  # Track (product_id, warehouse_id) pairs for bulk lookup
        
        for idx, row_data in enumerate(stock_data, start=1):
            try:
                # Map Excel columns to database fields
                mapped_data = {}
                product_code = None
                warehouse_name = None
                warehouse_code = None
                stock_id = None
                
                for excel_key, value in row_data.items():
                    # Normalize the Excel key
                    excel_key_normalized = str(excel_key).strip()
                    excel_key_lower = excel_key_normalized.lower()
                    
                    # Try exact match first
                    db_key = column_mapping.get(excel_key_normalized)
                    if db_key is None:
                        # Try case-insensitive match
                        db_key = column_mapping.get(excel_key_lower)
                    if db_key is None:
                        # Try matching with common variations (remove extra spaces, handle variations)
                        for key, val in column_mapping.items():
                            key_normalized = key.lower().strip().replace(' ', '')
                            excel_normalized = excel_key_lower.replace(' ', '')
                            if key_normalized == excel_normalized:
                                db_key = val
                                break
                    if db_key is None:
                        # Fallback: use lowercase key
                        db_key = excel_key_lower
                    
                    if db_key in ['quantity_on_hand', 'quantity_reserved', 'quantity_available', 'quantity_damaged', 'reorder_point']:
                        mapped_data[db_key] = parse_int(value)
                    elif db_key == 'product_code':
                        product_code = str(value).strip() if value and str(value).strip() else None
                    elif db_key == 'item_description':
                        # Not persisted; kept for future logging if needed
                        pass
                    elif db_key == 'warehouse_name':
                        warehouse_name = str(value).strip() if value and str(value).strip() else None
                    elif db_key == 'warehouse_code':
                        warehouse_code = str(value).strip() if value and str(value).strip() else None
                    elif db_key in ['product_id', 'warehouse_id', 'zone_id']:
                        mapped_data[db_key] = str(value).strip() if value and str(value).strip() else None
                    elif db_key == 'id':
                        stock_id = str(value).strip() if value and str(value).strip() else None
                
                # Look up product_id from product_code (case-insensitive)
                if not mapped_data.get('product_id') and product_code:
                    product_code_lower = product_code.lower()
                    if product_code in product_code_map:
                        mapped_data['product_id'] = product_code_map[product_code]
                    elif product_code_lower in product_code_lower_map:
                        mapped_data['product_id'] = product_code_lower_map[product_code_lower]
                    else:
                        message = f"Row {idx}: Product not found (code '{product_code}')"
                        errors.append(message)
                        error_records.append({"row": idx, "error": message, "data": row_data})
                        outcome.skip(
                            row=idx, code=_oc.PRODUCT_NOT_FOUND, message=message,
                            value=product_code,
                            identity={"product_code": product_code, "warehouse": warehouse_code or warehouse_name},
                        )
                        continue
                
                # Look up warehouse_id from warehouse_code (case-insensitive)
                if not mapped_data.get('warehouse_id') and warehouse_code:
                    warehouse_code_lower = warehouse_code.lower()
                    if warehouse_code in warehouse_code_map:
                        mapped_data['warehouse_id'] = warehouse_code_map[warehouse_code]
                    elif warehouse_code_lower in warehouse_code_lower_map:
                        mapped_data['warehouse_id'] = warehouse_code_lower_map[warehouse_code_lower]
                    else:
                        message = (
                            f"Row {idx}: Warehouse not found (code '{warehouse_code}', "
                            f"product '{product_code or '-'}')"
                        )
                        errors.append(message)
                        error_records.append({"row": idx, "error": message, "data": row_data})
                        outcome.skip(
                            row=idx, code=_oc.WAREHOUSE_NOT_FOUND, message=message,
                            value=warehouse_code,
                            identity={"product_code": product_code, "warehouse": warehouse_code},
                        )
                        continue

                # Look up warehouse_id from warehouse_name (case-insensitive) - fallback
                if not mapped_data.get('warehouse_id') and warehouse_name:
                    warehouse_name_lower = warehouse_name.lower()
                    if warehouse_name in warehouse_name_map:
                        mapped_data['warehouse_id'] = warehouse_name_map[warehouse_name]
                    elif warehouse_name_lower in warehouse_name_lower_map:
                        mapped_data['warehouse_id'] = warehouse_name_lower_map[warehouse_name_lower]
                    else:
                        message = (
                            f"Row {idx}: Warehouse not found (name '{warehouse_name}', "
                            f"product '{product_code or '-'}')"
                        )
                        errors.append(message)
                        error_records.append({"row": idx, "error": message, "data": row_data})
                        outcome.skip(
                            row=idx, code=_oc.WAREHOUSE_NOT_FOUND, message=message,
                            value=warehouse_name,
                            identity={"product_code": product_code, "warehouse": warehouse_name},
                        )
                        continue
                
                # Validate required fields
                if not mapped_data.get('product_id'):
                    message = f"Row {idx}: Product is required (code '{product_code or '-'}')"
                    errors.append(message)
                    error_records.append({"row": idx, "error": message, "data": row_data})
                    outcome.skip(
                        row=idx, code=_oc.MISSING_ITEM_CODE, message=message,
                        value=product_code,
                        identity={"product_code": product_code, "warehouse": warehouse_code or warehouse_name},
                    )
                    continue
                
                if not mapped_data.get('warehouse_id'):
                    message = (
                        f"Row {idx}: Warehouse is required "
                        f"(code '{warehouse_code or '-'}', name '{warehouse_name or '-'}', "
                        f"product '{product_code or '-'}')"
                    )
                    errors.append(message)
                    error_records.append({"row": idx, "error": message, "data": row_data})
                    outcome.skip(
                        row=idx, code=_oc.MISSING_LOCATION, message=message,
                        value=warehouse_code or warehouse_name,
                        identity={"product_code": product_code, "warehouse": warehouse_code or warehouse_name},
                    )
                    continue
                
                # Handle quantity logic
                # quantity_available is a GENERATED column in the database, so we don't update it
                # We only update quantity_on_hand (from "Total Quantity") and quantity_reserved (from "Reserved Quantity")
                # The database will automatically calculate quantity_available = quantity_on_hand - quantity_reserved
                
                quantity_available_raw = mapped_data.get('quantity_available', 0)
                quantity_on_hand_raw = mapped_data.get('quantity_on_hand', 0)
                quantity_reserved_raw = mapped_data.get('quantity_reserved', 0)
                
                # Ensure they are integers
                quantity_available = int(quantity_available_raw) if quantity_available_raw else 0
                quantity_on_hand = int(quantity_on_hand_raw) if quantity_on_hand_raw else 0
                quantity_reserved = int(quantity_reserved_raw) if quantity_reserved_raw else 0
                
                # Logic: If "Available" is provided but "Total Quantity" and "Reserved Quantity" are empty,
                # use Available as quantity_on_hand (since Available = Total - Reserved, if Reserved=0, then Available = Total)
                if quantity_available > 0 and quantity_on_hand == 0 and quantity_reserved == 0:
                    # Only Available is provided - use it as quantity_on_hand
                    mapped_data['quantity_on_hand'] = quantity_available
                    mapped_data['quantity_reserved'] = 0
                elif quantity_on_hand > 0:
                    # quantity_on_hand is provided - use it as is
                    # quantity_reserved is already set from mapped_data
                    pass
                elif quantity_available > 0:
                    # Only Available provided (fallback case)
                    mapped_data['quantity_on_hand'] = quantity_available
                    mapped_data['quantity_reserved'] = 0
                else:
                    # Nothing provided - default to 0
                    mapped_data['quantity_on_hand'] = 0
                    mapped_data['quantity_reserved'] = 0
                
                # Prepare row data - only set fields we can update (NOT quantity_available - it's generated)
                row_dict = {
                    'product_id': mapped_data['product_id'],
                    'warehouse_id': mapped_data['warehouse_id'],
                    'zone_id': mapped_data.get('zone_id'),
                    'quantity_on_hand': mapped_data.get('quantity_on_hand', 0) or 0,
                    'quantity_reserved': mapped_data.get('quantity_reserved', 0) or 0,
                    # Note: quantity_available is NOT included - it's a generated column
                    'quantity_damaged': mapped_data.get('quantity_damaged', 0) or 0,
                    'reorder_point': mapped_data.get('reorder_point'),
                    '_row_idx': idx,
                    '_stock_id': stock_id,
                    '_product_code': product_code,
                    '_warehouse_name': warehouse_name,
                }
                
                # Check if exists by ID first
                if stock_id and stock_id in existing_stock_by_id:
                    row_dict['_existing_id'] = stock_id
                    rows_to_update.append(row_dict)
                else:
                    # Will check by product_id + warehouse_id later
                    # Ensure both IDs are strings for consistent tuple matching
                    product_warehouse_pairs.add((str(mapped_data['product_id']), str(mapped_data['warehouse_id'])))
                    rows_to_create.append(row_dict)
                
            except Exception as e:
                message = f"Row {idx}: {str(e)}"
                errors.append(message)
                error_records.append({"row": idx, "error": message, "data": row_data})
                outcome.fail(row=idx, code=_oc.ROW_ERROR, message=message)
                continue
        
        # Step 3: Bulk lookup existing stock by product_id + warehouse_id
        existing_stock_by_pair = {}
        if product_warehouse_pairs:
            pair_list = list(product_warehouse_pairs)
            existing_stocks: list[Stock] = []
            # Chunk to keep IN list manageable for very large imports
            for chunk_start in range(0, len(pair_list), 1000):
                chunk = pair_list[chunk_start:chunk_start + 1000]
                stocks_chunk = self.db.query(Stock).filter(
                    tuple_(Stock.product_id, Stock.warehouse_id).in_(chunk)
                ).all()
                existing_stocks.extend(stocks_chunk)
            existing_stock_by_pair = {(str(s.product_id), str(s.warehouse_id)): s for s in existing_stocks}
        
        # Step 4: Separate creates and updates (new stock records are created automatically)
        final_creates = []
        final_updates = []
        ledger_entries = []
        
        for row_dict in rows_to_create:
            # Ensure tuple uses string UUIDs for consistent matching
            pair = (str(row_dict['product_id']), str(row_dict['warehouse_id']))
            if pair in existing_stock_by_pair:
                # Move to updates
                row_dict['_existing_id'] = existing_stock_by_pair[pair].id
                final_updates.append(row_dict)
            else:
                # Create new stock record for this product + warehouse
                final_creates.append(row_dict)

        for row_dict in rows_to_update:
            final_updates.append(row_dict)

        # Attribute per source row now the create/update split is final. `_row_idx`
        # was carried on each row_dict precisely so the outcome can point back at
        # the spreadsheet row.
        if not validate_only:
            for row_dict in final_creates:
                outcome.success(
                    row=row_dict.get("_row_idx"),
                    message=f"Stock created: {row_dict.get('_product_code') or ''}".strip(),
                    value=row_dict.get("_product_code"),
                    identity=_stock_identity(row_dict),
                    entity_type="stock",
                )
            for row_dict in final_updates:
                outcome.updated(
                    row=row_dict.get("_row_idx"),
                    message=f"Stock updated: {row_dict.get('_product_code') or ''}".strip(),
                    value=row_dict.get("_product_code"),
                    identity=_stock_identity(row_dict),
                    entity_type="stock",
                )

        create_dict = {}
        for row_dict in final_creates:
            pair = (str(row_dict['product_id']), str(row_dict['warehouse_id']))
            create_dict[pair] = row_dict

        seen_stock_pairs = {
            (str(r['product_id']), str(r['warehouse_id']))
            for r in [*create_dict.values(), *final_updates]
        }

        # Find stocks "missing from import" (will be system-adjusted to 0).
        # Pulling seen_stock_pairs into SQL as a tuple-NOT-IN explodes parameter
        # count (>20k params on a full snapshot) and produces a pathological plan;
        # SELECT all candidates and set-diff in Python instead.
        zero_q = self.db.query(Stock).filter(
            Stock.warehouse.has(Warehouse.is_active.is_(True)),
            Stock.quantity_on_hand != 0,
        )
        all_zero_candidates: list[Stock] = zero_q.all()
        seen_set = seen_stock_pairs  # already string-tuple keys
        stocks_to_zero_cached: list[Stock] = [
            s for s in all_zero_candidates
            if (str(s.product_id), str(s.warehouse_id)) not in seen_set
        ]
        snapshot_zero_count = len(stocks_to_zero_cached)
        if validate_only:
            # Don't need the instance refs further in validate-only path.
            stocks_to_zero_cached = []

        if validate_only:
            warnings_str = [
                f"Row {w.get('row', '?')}: {w.get('product_code', '-')} / {w.get('warehouse', '-')}: {w.get('reason', '')}"
                for w in warnings
            ]
            # Dedupe creates by (product_id, warehouse_id) for count
            create_keys = {(str(r['product_id']), str(r['warehouse_id'])) for r in final_creates}
            return {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings_str,
                "summary": {
                    "total_rows": len(stock_data),
                    "would_create": len(create_keys),
                    "would_update": len(final_updates),
                    "would_system_adjust_to_zero": snapshot_zero_count,
                    "would_skip": skipped_rows,
                    "error_count": len(errors),
                },
            }

        # Build existing stock map for ledger calculations
        existing_by_id = {**existing_stock_by_id}
        for stock in existing_stock_by_pair.values():
            existing_by_id[stock.id] = stock
        
        now = datetime.utcnow()

        # Step 5: Create new stock records (dedupe by product_id + warehouse_id, last wins).
        # Stock.id has a python-side UUID default, so ids are assigned at construction
        # time - no per-row flush needed. Build all instances, add together, single flush.
        if create_dict:
            try:
                new_stocks: list[tuple[Stock, int]] = []
                for (_pid, _wid), row_dict in create_dict.items():
                    qoh = row_dict.get('quantity_on_hand', 0) or 0
                    qres = row_dict.get('quantity_reserved', 0) or 0
                    new_stock = Stock(
                        product_id=row_dict['product_id'],
                        warehouse_id=row_dict['warehouse_id'],
                        zone_id=row_dict.get('zone_id'),
                        quantity_on_hand=qoh,
                        quantity_reserved=qres,
                        quantity_damaged=row_dict.get('quantity_damaged', 0) or 0,
                        reorder_point=row_dict.get('reorder_point'),
                        updated_at=now,
                    )
                    new_stocks.append((new_stock, qoh))

                self.db.add_all(s for s, _ in new_stocks)
                self.db.flush()

                for new_stock, qoh in new_stocks:
                    if qoh != 0:
                        ledger_entries.append({
                            'id': str(uuid.uuid4()),
                            'product_id': new_stock.product_id,
                            'warehouse_id': new_stock.warehouse_id,
                            'transaction_type': 'BULK_IMPORT',
                            'quantity_change': qoh,
                            'previous_quantity': 0,
                            'new_quantity': qoh,
                            'reference_type': 'bulk_import',
                            'reference_id': new_stock.id,
                            'notes': 'Bulk import (new stock record)',
                            'created_by': user_id,
                        })
                created = len(create_dict)
            except Exception as e:
                message = f"Bulk create error: {str(e)}"
                errors.append(message)
                error_records.append({"row": None, "error": message, "data": None})
                created = 0

        # Step 6: Update existing records (use individual updates for reliability)
        if final_updates:
            try:
                # Group updates by ID (if same ID appears multiple times, use the last one)
                # Note: quantity_available is a GENERATED column, so we don't update it
                # The database will automatically calculate it as quantity_on_hand - quantity_reserved
                update_dict = {}
                for row_dict in final_updates:
                    stock_id = row_dict['_existing_id']
                    qoh = row_dict.get('quantity_on_hand', 0) or 0
                    qres = row_dict.get('quantity_reserved', 0) or 0

                    # Store update data - only fields we can update (NOT quantity_available)
                    update_dict[stock_id] = {
                        'zone_id': row_dict.get('zone_id'),
                        'quantity_on_hand': qoh,
                        'quantity_reserved': qres,
                        # quantity_available is NOT included - it's a generated column
                        'quantity_damaged': row_dict.get('quantity_damaged', 0) or 0,
                        'reorder_point': row_dict.get('reorder_point'),
                    }

                # Perform individual updates for reliability
                for stock_id, update_data in update_dict.items():
                    stock = existing_by_id.get(stock_id)
                    if not stock:
                        # Try to fetch from database if not in cache
                        stock = self.db.query(Stock).filter(Stock.id == stock_id).first()
                        if not stock:
                            message = f"Stock record with ID '{stock_id}' not found for update"
                            errors.append(message)
                            error_records.append({"row": None, "error": message, "data": None})
                            continue
                        existing_by_id[stock_id] = stock
                    
                    previous_qty = stock.quantity_on_hand
                    # Update the stock record - only update fields that are not generated columns
                    # quantity_available will be automatically calculated by the database
                    for key, value in update_data.items():
                        # Skip quantity_available if it somehow got into update_data (shouldn't happen)
                        if key != 'quantity_available':
                            setattr(stock, key, value)
                    stock.updated_at = now
                    # Mark as modified to ensure SQLAlchemy tracks the change
                    self.db.add(stock)
                    new_qty = update_data['quantity_on_hand']
                    quantity_change = new_qty - previous_qty
                    # Add ledger entry if quantity changed
                    if quantity_change != 0:
                        # Get the calculated quantity_available after update (will be refreshed from DB)
                        # For ledger, we'll use the calculated value: new_qty - quantity_reserved
                        calculated_available = new_qty - update_data.get('quantity_reserved', 0)
                        ledger_entries.append({
                            'id': str(uuid.uuid4()),
                            'product_id': stock.product_id,
                            'warehouse_id': stock.warehouse_id,
                            'transaction_type': 'BULK_IMPORT',
                            'quantity_change': quantity_change,
                            'previous_quantity': previous_qty,
                            'new_quantity': new_qty,
                            'reference_type': 'bulk_import',
                            'reference_id': stock_id,
                            'notes': 'Bulk import update',
                            'created_by': user_id
                        })
                
                updated = len(update_dict)
            except Exception as e:
                message = f"Bulk update error: {str(e)}"
                errors.append(message)
                error_records.append({"row": None, "error": message, "data": None})

        system_adjusted_to_zero = 0
        try:
            stocks_to_zero = stocks_to_zero_cached
            for stock in stocks_to_zero:
                previous_qty = stock.quantity_on_hand or 0
                if previous_qty == 0:
                    continue
                stock.quantity_on_hand = 0
                stock.quantity_reserved = 0
                stock.quantity_damaged = 0
                stock.updated_at = now
                self.db.add(stock)
                ledger_entries.append({
                    'id': str(uuid.uuid4()),
                    'product_id': stock.product_id,
                    'warehouse_id': stock.warehouse_id,
                    'transaction_type': 'SYSTEM_ADJUSTMENT',
                    'quantity_change': -previous_qty,
                    'previous_quantity': previous_qty,
                    'new_quantity': 0,
                    'reference_type': 'stock_snapshot_import',
                    'reference_id': import_session_id,
                    'notes': 'System adjustment: missing from full stock import; set on-hand quantity to 0',
                    'created_by': user_id
                })
                system_adjusted_to_zero += 1
        except Exception as e:
            message = f"Snapshot zero-adjustment error: {str(e)}"
            errors.append(message)
            error_records.append({"row": None, "error": message, "data": None})
        
        # Step 7: Commit all changes
        commit_successful = False
        try:
            if ledger_entries:
                # bulk_insert_mappings BYPASSES the before_insert auto-stamp, so the
                # rows would fall to migration 306's DB DEFAULT (Sorento) and leak a
                # scoped import into the wrong company. Resolve the single active
                # company and stamp every entry explicitly (AC company-scope C1).
                scope = get_company_scope(self.db)
                if not (isinstance(scope, frozenset) and len(scope) == 1):
                    raise AppException(
                        status_code=400,
                        message="Stock ledger write requires a single active company",
                        code="company_scope_required",
                    )
                cid = next(iter(scope))
                for entry in ledger_entries:
                    entry.setdefault("company_id", cid)
                self.db.bulk_insert_mappings(StockLedger, ledger_entries)
            # Flush to ensure all changes are sent to database
            self.db.flush()
            # Commit the transaction
            self.db.commit()
            commit_successful = True
        except Exception as e:
            self.db.rollback()
            message = f"Database error: {str(e)}"
            errors.append(message)
            error_records.append({"row": None, "error": message, "data": None})

        # Step 8: Create import log entry (only if commit was successful)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        if commit_successful:
            try:
                import_log_service = ImportLogService(self.db)
                import_log_service.create_import_log(
                    entity_type="stock",
                    entity_table="stock",
                    import_session_id=import_session_id,
                    filename=None,
                    import_type="BULK_IMPORT",
                    total_rows=len(stock_data),
                    successful_rows=created + updated,
                    created_rows=created,
                    updated_rows=updated,
                    failed_rows=len(error_records),
                    skipped_rows=skipped_rows + system_adjusted_to_zero,
                    warnings=warnings,
                    errors=error_records,
                    summary={"system_adjusted_to_zero": system_adjusted_to_zero},
                    imported_by=user_id,
                    duration_ms=duration_ms,
                )
            except Exception:
                # Import logging should not break the main import flow
                pass

        return {
            "import_session_id": import_session_id,
            "created": created,
            "updated": updated,
            "skipped": skipped_rows,
            "errors": errors,
            "warnings": warnings,
            "system_adjusted_to_zero": system_adjusted_to_zero,
        }
    
    def get_stock(self, stock_id: str):
        """Get stock by ID."""
        stock = self.db.query(Stock).filter(
            Stock.id == stock_id,
            Stock.warehouse.has(Warehouse.is_active.is_(True)),
        ).first()
        if not stock:
            raise handle_not_found("Stock", stock_id)
        return stock
    
    def get_stock_dashboard(self, limit: int = 10):
        """Stock dashboard sourced from the `stock` table (same source as the Stock listing UI).

        `limit` caps the size of every list returned (top warehouses, top low-stock rows).
        Hard-bounded to [1, 50] inside the call. Counts/totals are NOT affected by `limit`.

        StockBatch is only populated when batch-tracking is in use; the previous implementation
        summed StockBatch.quantity which produced zeros for every account that doesn't track
        batches. Source-of-truth for on-hand quantity is `Stock.quantity_on_hand`.
        """
        from datetime import datetime, timedelta

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        active_wh = Stock.warehouse.has(Warehouse.is_active.is_(True))

        total_skus = (
            self.db.query(func.count(func.distinct(Stock.product_id)))
            .filter(active_wh)
            .scalar()
            or 0
        )

        total_quantity = (
            self.db.query(func.coalesce(func.sum(Stock.quantity_on_hand), 0))
            .filter(active_wh)
            .scalar()
            or 0
        )

        # Per-warehouse aggregation (top N by on-hand)
        wh_rows = (
            self.db.query(
                Warehouse.id.label("warehouse_id"),
                Warehouse.warehouse_code.label("warehouse_code"),
                Warehouse.warehouse_name.label("warehouse_name"),
                func.coalesce(func.sum(Stock.quantity_on_hand), 0).label("on_hand"),
                func.coalesce(func.sum(Stock.quantity_reserved), 0).label("reserved"),
                func.coalesce(func.sum(Stock.quantity_available), 0).label("available"),
                func.count(func.distinct(Stock.product_id)).label("sku_count"),
            )
            .join(Stock, Stock.warehouse_id == Warehouse.id)
            .filter(Warehouse.is_active.is_(True))
            .group_by(Warehouse.id, Warehouse.warehouse_code, Warehouse.warehouse_name)
            .order_by(func.sum(Stock.quantity_on_hand).desc())
            .limit(limit)
            .all()
        )
        stock_by_warehouse = [
            {
                "warehouse_id": str(r.warehouse_id),
                "warehouse_code": r.warehouse_code,
                "warehouse_name": r.warehouse_name,
                "quantity_on_hand": int(r.on_hand or 0),
                "quantity_reserved": int(r.reserved or 0),
                "quantity_available": int(r.available or 0),
                "sku_count": int(r.sku_count or 0),
            }
            for r in wh_rows
        ]

        # 30-day movement from stock_ledger (net change per day)
        since = datetime.utcnow() - timedelta(days=30)
        movement_rows = (
            self.db.query(
                func.date(StockLedger.created_at).label("day"),
                func.coalesce(func.sum(StockLedger.quantity_change), 0).label("net_change"),
                func.count(StockLedger.id).label("txn_count"),
            )
            .filter(StockLedger.created_at >= since)
            .group_by(func.date(StockLedger.created_at))
            .order_by(func.date(StockLedger.created_at).asc())
            .all()
        )
        stock_movement_30_days = [
            {
                "date": r.day.isoformat() if hasattr(r.day, "isoformat") else str(r.day),
                "net_change": int(r.net_change or 0),
                "transaction_count": int(r.txn_count or 0),
            }
            for r in movement_rows
        ]

        # Current Stock List attachment (singleton; matches /resources/attachments/current-stock-list).
        # Stock imports upload a single "Stock List" file; previous versions are soft-deleted, so
        # this returns only the newest non-archived row. Provides a signed download URL.
        from app.models.resources import Attachment, AttachmentType
        from app.services.storage_router import resolve_signed_url

        latest_stock_list_attachment = None
        stock_list_type = (
            self.db.query(AttachmentType)
            .filter(AttachmentType.type_name.in_(("Stock List", "Stock_List")))
            .first()
        )
        if stock_list_type is not None:
            attachment = (
                self.db.query(Attachment)
                .filter(
                    Attachment.attachment_type_id == str(stock_list_type.id),
                    Attachment.is_deleted.is_(False),
                )
                .order_by(Attachment.uploaded_at.desc())
                .first()
            )
            if attachment is not None:
                try:
                    signed_url = resolve_signed_url(
                        attachment.file_path,
                        provider=getattr(attachment, "storage_provider", None),
                    )
                except Exception:
                    signed_url = attachment.file_path
                latest_stock_list_attachment = {
                    "id": str(attachment.id),
                    "original_filename": attachment.original_filename,
                    "stored_filename": attachment.stored_filename,
                    "file_size_bytes": attachment.file_size_bytes,
                    "mime_type": attachment.mime_type,
                    "uploaded_at": attachment.uploaded_at.isoformat() if attachment.uploaded_at else None,
                    "uploaded_by": attachment.uploaded_by,
                    "description": attachment.description,
                    "file_path": signed_url,
                    "storage_provider": getattr(attachment, "storage_provider", None),
                }

        return {
            "total_skus": int(total_skus),
            "total_quantity": int(total_quantity),
            "stock_by_warehouse": stock_by_warehouse,
            "stock_movement_30_days": stock_movement_30_days,
            "latest_stock_list_attachment": latest_stock_list_attachment,
            "limit": limit,
        }
    
    def get_stock_alerts(self):
        """Get low stock alerts."""
        # TODO: Implement based on reorder levels
        return []


class StockBatchService:
    """Service for stock batch operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_batches(
        self,
        page: int = 1,
        limit: int = 50,
        product_id: Optional[str] = None,
        warehouse_id: Optional[str] = None
    ):
        """List stock batches."""
        q = self.db.query(StockBatch).filter(
            StockBatch.warehouse.has(Warehouse.is_active.is_(True))
        )
        
        if product_id:
            resolved_pid = _resolve_stock_product_id(self.db, product_id)
            if resolved_pid is None:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
            q = q.filter(StockBatch.product_id == resolved_pid)
        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
            q = q.filter(StockBatch.warehouse_id.in_(warehouse_ids))
        
        total = q.count()
        offset = (page - 1) * limit
        batches = q.offset(offset).limit(limit).all()
        
        return {
            "data": batches,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_batch(self, batch_id: str):
        """Get a stock batch by ID."""
        batch = self.db.query(StockBatch).filter(
            StockBatch.id == batch_id,
            StockBatch.warehouse.has(Warehouse.is_active.is_(True)),
        ).first()
        if not batch:
            raise handle_not_found("Stock Batch", batch_id)
        return batch
    
    def create_batch(self, batch_data: StockBatchCreate):
        """Create a new stock batch."""
        existing = self.db.query(StockBatch).filter(
            StockBatch.batch_code == batch_data.batch_code
        ).first()
        if existing:
            raise handle_conflict("Batch code already exists.")
        
        batch = StockBatch(**batch_data.model_dump())
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)
        return batch
    
    def update_batch(self, batch_id: str, batch_data: StockBatchUpdate):
        """Update a stock batch."""
        batch = self.get_batch(batch_id)
        
        update_data = batch_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(batch, key, value)
        
        self.db.commit()
        self.db.refresh(batch)
        return batch
