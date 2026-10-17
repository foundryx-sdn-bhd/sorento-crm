"""Dynamic export rows (flattened for order lines) using same filter compiler as list search."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, exists, not_, or_
from sqlalchemy.orm import Session, joinedload

from app.models.marketing import Promotion, PromotionProduct
from app.models.order import Customer, Order, OrderLine
from app.models.product import Product
from app.models.procurement import Supplier
from app.models.workflow_forms import WorkflowFormDefinition, WorkflowSubmission
from app.schemas.list_query import ListExportRequest
from app.services.finance import billing_document_query_service
from app.services.list_query_metadata_service import ListQueryMetadataService
from app.services.list_query_registry import require_adapter
from app.services.product_service import malaysia_day_start_utc
from app.services.query.filter_compiler import compile_optional_filter
from app.services.sla_service import MALAYSIA_TZ
from app.services.workflow_submission_dynamic_list_query import merge_submission_field_maps


def _json_safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bool):
        return val
    return val


def _value_from_obj(obj: Any, compile_key: str) -> Any:
    _, attr = compile_key.split(".", 1)
    return _json_safe(getattr(obj, attr, None))


def _value_from_supplier(obj: Any, compile_key: str) -> Any:
    """S2 (`PLAN-local-supplier-oi-routing.md`): `country.name` is the joined name, read off
    the model's own `country_name` property - `suppliers.country` (the free-text column
    `_value_from_obj`'s plain `getattr(obj, attr)` used to read) is gone."""
    if compile_key == "country.name":
        return _json_safe(getattr(obj, "country_name", None))
    return _value_from_obj(obj, compile_key)


def _value_from_product(obj: Any, compile_key: str) -> Any:
    """Discontinued at (issue #1287): the exported value is the Malaysia calendar
    date (YYYY-MM-DD) the scheduler noticed, not the raw naive-UTC timestamp
    `_value_from_obj`'s plain `getattr` would emit."""
    if compile_key == "product.discontinued_notified_at":
        dt = getattr(obj, "discontinued_notified_at", None)
        if dt is None:
            return None
        return dt.replace(tzinfo=timezone.utc).astimezone(MALAYSIA_TZ).date().isoformat()
    return _value_from_obj(obj, compile_key)


def _value_from_workflow_def(obj: Any, compile_key: str) -> Any:
    _, attr = compile_key.split(".", 1)
    return _json_safe(getattr(obj, attr, None))


def _value_from_workflow_submission(sub: Any, compile_key: str) -> Any:
    if compile_key == "rel.definition_name":
        d = getattr(sub, "definition", None)
        return _json_safe(d.name if d is not None else None)
    if compile_key.startswith("wf_hdr:"):
        fid = compile_key.split(":", 1)[1]
        data = sub.header_data if isinstance(sub.header_data, dict) else {}
        return _json_safe(data.get(fid))
    if compile_key.startswith("wf_ln:"):
        parts = compile_key.split(":", 2)
        if len(parts) != 3:
            return None
        _, line_group_id, field_id = parts
        for ln in sub.lines or []:
            if getattr(ln, "line_group_id", None) == line_group_id:
                rd = ln.row_data if isinstance(ln.row_data, dict) else {}
                return _json_safe(rd.get(field_id))
        return None
    _, attr = compile_key.split(".", 1)
    return _json_safe(getattr(sub, attr, None))


def _value_from_line_export(line: Any, compile_key: str) -> Any:
    """Resolve line.* and line.product.* / line.warehouse.* for flattened order-line export."""
    parts = compile_key.split(".")
    if not parts or parts[0] != "line":
        return None
    if len(parts) == 2:
        return _json_safe(getattr(line, parts[1], None))
    if len(parts) == 3 and parts[1] == "product":
        prod = getattr(line, "product", None)
        return _json_safe(getattr(prod, parts[2], None) if prod is not None else None)
    if len(parts) == 3 and parts[1] == "warehouse":
        wh = getattr(line, "warehouse", None)
        return _json_safe(getattr(wh, parts[2], None) if wh is not None else None)
    return None


def _dedupe_record_ids(raw: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not raw:
        return None
    out: List[str] = []
    seen: set[str] = set()
    for x in raw:
        if x is None:
            continue
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out or None


class ListQueryExportService:
    def __init__(self, db: Session):
        self.db = db
        self.meta = ListQueryMetadataService(db)

    def export_rows(self, req: ListExportRequest) -> List[Dict[str, Any]]:
        require_adapter(req.resource)
        if req.resource == "finance_billing_documents":
            return billing_document_query_service.export_rows(
                self.db,
                [sel.field_key for sel in req.fields],
                quick_search=req.quick_search,
                record_ids=_dedupe_record_ids(req.record_ids),
                customer_id=req.customer_id if req.customer_id != "all" else None,
                document_type=req.document_type,
                status=req.status,
                agent_code=req.agent_code,
                date_from=req.date_from,
                date_to=req.date_to,
                open_only=bool(req.open_only),
            )
        resource = self.meta.get_resource(req.resource)
        if not resource:
            raise ValueError(f"Unknown resource: {req.resource}")

        field_by_key = self.meta.fields_by_key(req.resource)
        if req.resource == "workflow_form_submissions":
            wid = (req.workflow_form_definition_id or "").strip() or None
            if wid:
                field_by_key = merge_submission_field_maps(field_by_key, wid, self.db)
        selected = []
        for sel in req.fields:
            m = field_by_key.get(sel.field_key)
            if not m or not m.exportable:
                raise ValueError(f"Unknown or non-exportable field: {sel.field_key}")
            selected.append(m)

        clause = compile_optional_filter(req.resource, req.filter, field_by_key)

        handlers = {
            "orders": self._export_orders,
            "products": self._export_products,
            "suppliers": self._export_suppliers,
            "promotions": self._export_promotions,
            "workflow_form_definitions": self._export_workflow_form_definitions,
            "workflow_form_submissions": self._export_workflow_form_submissions,
        }
        handler = handlers.get(req.resource)
        if not handler:
            raise ValueError(f"Unsupported resource: {req.resource}")
        return handler(selected, clause, req)

    def _export_orders(
        self,
        selected: list,
        clause: Optional[Any],
        req: ListExportRequest,
    ) -> List[Dict[str, Any]]:
        q = self.db.query(Order).filter(Order.deleted_at.is_(None))
        if req.customer_id and req.customer_id != "all":
            q = q.filter(Order.customer_id == req.customer_id)
        if req.order_status_id and req.order_status_id != "all":
            q = q.filter(Order.order_status_id == req.order_status_id)
        hol = (req.has_order_lines or "").strip().lower()
        if hol == "yes":
            q = q.filter(exists().where(OrderLine.order_id == Order.id))
        elif hol == "no":
            q = q.filter(not_(exists().where(OrderLine.order_id == Order.id)))
        if req.quick_search:
            q = q.filter(
                or_(
                    Order.order_number.ilike(f"%{req.quick_search}%"),
                    Order.customer.has(Customer.customer_name.ilike(f"%{req.quick_search}%")),
                    Order.customer.has(Customer.customer_code.ilike(f"%{req.quick_search}%")),
                    Order.debtor_name.ilike(f"%{req.quick_search}%"),
                )
            )
        if clause is not None:
            q = q.filter(clause)

        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(Order.id.in_(id_list))

        orders = q.options(
            joinedload(Order.lines).joinedload(OrderLine.product),
            joinedload(Order.lines).joinedload(OrderLine.warehouse),
        ).all()
        if id_list:
            by_id = {str(o.id): o for o in orders}
            orders = [by_id[i] for i in id_list if i in by_id]
        else:
            orders = sorted(
                orders,
                key=lambda o: o.created_at or datetime.min,
                reverse=True,
            )

        parent_fields = [m for m in selected if not m.is_line_field]
        line_fields = [m for m in selected if m.is_line_field]

        rows: List[Dict[str, Any]] = []
        for order in orders:
            parent_vals = {}
            for m in parent_fields:
                key = m.export_column_name or m.field_key
                parent_vals[key] = _value_from_obj(order, m.compile_key)

            lines = list(order.lines or [])
            if not lines:
                row = dict(parent_vals)
                for m in line_fields:
                    key = m.export_column_name or m.field_key
                    row[key] = None
                rows.append(row)
                continue
            for line in lines:
                row = dict(parent_vals)
                for m in line_fields:
                    key = m.export_column_name or m.field_key
                    row[key] = _value_from_line_export(line, m.compile_key)
                rows.append(row)
        return rows

    def _export_products(self, selected: list, clause: Optional[Any], req: ListExportRequest) -> List[Dict[str, Any]]:
        from decimal import Decimal

        q = self.db.query(Product)
        if req.category_id and req.category_id != "all":
            q = q.filter(Product.category_id == req.category_id)
        if req.brand_id and req.brand_id != "all":
            q = q.filter(Product.brand_id == req.brand_id)
        if req.product_status and req.product_status != "all":
            q = q.filter(Product.is_active == (req.product_status == "active"))
        if req.item_type:
            q = q.filter(Product.item_type == req.item_type)
        # "Discontinued at" range (issue #1287): inclusive by Malaysia calendar day,
        # same bounds as `ProductService._build_list_query`.
        if req.discontinued_from is not None:
            q = q.filter(Product.discontinued_notified_at >= malaysia_day_start_utc(req.discontinued_from))
        if req.discontinued_to is not None:
            q = q.filter(
                Product.discontinued_notified_at
                < malaysia_day_start_utc(req.discontinued_to + timedelta(days=1))
            )
        if req.price_min or req.price_max:
            pf = []
            if req.price_min is not None:
                pf.append(Product.list_price >= Decimal(str(req.price_min)))
            if req.price_max is not None:
                pf.append(Product.list_price <= Decimal(str(req.price_max)))
            q = q.filter(and_(*pf))
        if req.quick_search:
            q = q.filter(
                or_(
                    Product.product_code.ilike(f"%{req.quick_search}%"),
                    Product.product_name.ilike(f"%{req.quick_search}%"),
                )
            )
        if clause is not None:
            q = q.filter(clause)

        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(Product.id.in_(id_list))

        products = q.all()
        if id_list:
            by_id = {str(p.id): p for p in products}
            products = [by_id[i] for i in id_list if i in by_id]
        else:
            products = sorted(
                products,
                key=lambda p: p.created_at or datetime.min,
                reverse=True,
            )
        out = []
        for p in products:
            row = {}
            for m in selected:
                key = m.export_column_name or m.field_key
                row[key] = _value_from_product(p, m.compile_key)
            out.append(row)
        return out

    def _export_suppliers(self, selected: list, clause: Optional[Any], req: ListExportRequest) -> List[Dict[str, Any]]:
        q = self.db.query(Supplier).options(joinedload(Supplier.country))
        if req.quick_search:
            q = q.filter(
                or_(
                    Supplier.supplier_code.ilike(f"%{req.quick_search}%"),
                    Supplier.supplier_name.ilike(f"%{req.quick_search}%"),
                )
            )
        if clause is not None:
            q = q.filter(clause)

        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(Supplier.id.in_(id_list))

        suppliers = q.all()
        if id_list:
            by_id = {str(s.id): s for s in suppliers}
            suppliers = [by_id[i] for i in id_list if i in by_id]
        else:
            suppliers = sorted(
                suppliers,
                key=lambda s: s.created_at or datetime.min,
                reverse=True,
            )
        out = []
        for s in suppliers:
            row = {}
            for m in selected:
                key = m.export_column_name or m.field_key
                row[key] = _value_from_supplier(s, m.compile_key)
            out.append(row)
        return out

    def _export_promotions(
        self,
        selected: list,
        clause: Optional[Any],
        req: ListExportRequest,
    ) -> List[Dict[str, Any]]:
        q = self.db.query(Promotion)

        pst = req.promotion_status
        if pst and pst != "all":
            if pst == "active":
                q = q.filter(Promotion.is_active.is_(True))
            elif pst == "inactive":
                q = q.filter(Promotion.is_active.is_(False))

        if req.promotion_access_level and (ut := (req.promotion_access_level or "").strip()):
            q = q.filter(Promotion.access_levels.contains([ut]))

        if req.quick_search and (qs := req.quick_search.strip()):
            search_term = f"%{qs}%"
            has_product_match = exists().where(
                PromotionProduct.promotion_id == Promotion.id
            ).where(
                PromotionProduct.product_id == Product.id
            ).where(
                Product.product_code.ilike(search_term)
            )
            q = q.filter(
                or_(
                    Promotion.description.ilike(search_term),
                    has_product_match,
                )
            )

        if clause is not None:
            q = q.filter(clause)

        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(Promotion.id.in_(id_list))

        promotions = q.all()
        if id_list:
            by_id = {str(p.id): p for p in promotions}
            promotions = [by_id[i] for i in id_list if i in by_id]
        else:
            promotions = sorted(
                promotions,
                key=lambda p: p.created_at or datetime.min,
                reverse=True,
            )
        out = []
        for p in promotions:
            row = {}
            for m in selected:
                key = m.export_column_name or m.field_key
                row[key] = _value_from_obj(p, m.compile_key)
            out.append(row)
        return out

    def _export_workflow_form_definitions(
        self,
        selected: list,
        clause: Optional[Any],
        req: ListExportRequest,
    ) -> List[Dict[str, Any]]:
        q = self.db.query(WorkflowFormDefinition)
        if req.workflow_definition_is_active is not None:
            q = q.filter(WorkflowFormDefinition.is_active.is_(req.workflow_definition_is_active))
        if req.quick_search:
            like = f"%{req.quick_search.strip()}%"
            q = q.filter(
                or_(
                    WorkflowFormDefinition.name.ilike(like),
                    WorkflowFormDefinition.code.ilike(like),
                )
            )
        if clause is not None:
            q = q.filter(clause)
        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(WorkflowFormDefinition.id.in_(id_list))
        rows = q.all()
        if id_list:
            by_id = {str(r.id): r for r in rows}
            rows = [by_id[i] for i in id_list if i in by_id]
        else:
            rows = sorted(rows, key=lambda r: r.updated_at or datetime.min, reverse=True)
        out: List[Dict[str, Any]] = []
        for d in rows:
            row = {}
            for m in selected:
                key = m.export_column_name or m.field_key
                row[key] = _value_from_workflow_def(d, m.compile_key)
            out.append(row)
        return out

    def _export_workflow_form_submissions(
        self,
        selected: list,
        clause: Optional[Any],
        req: ListExportRequest,
    ) -> List[Dict[str, Any]]:
        q = self.db.query(WorkflowSubmission).options(
            joinedload(WorkflowSubmission.definition),
            joinedload(WorkflowSubmission.lines),
        )
        wf_def_id = (req.workflow_form_definition_id or "").strip() or None
        if wf_def_id:
            q = q.filter(WorkflowSubmission.definition_id == wf_def_id)
        st = (req.workflow_submission_state_code or "").strip() or None
        if st:
            q = q.filter(WorkflowSubmission.current_state_code == st)
        if req.quick_search:
            like = f"%{req.quick_search.strip()}%"
            q = q.outerjoin(
                WorkflowFormDefinition,
                WorkflowSubmission.definition_id == WorkflowFormDefinition.id,
            )
            q = q.filter(
                or_(
                    WorkflowSubmission.current_state_code.ilike(like),
                    WorkflowFormDefinition.name.ilike(like),
                    WorkflowFormDefinition.code.ilike(like),
                )
            )
        if clause is not None:
            q = q.filter(clause)
        id_list = _dedupe_record_ids(req.record_ids)
        if id_list:
            q = q.filter(WorkflowSubmission.id.in_(id_list))
        rows = q.all()
        if id_list:
            by_id = {str(r.id): r for r in rows}
            rows = [by_id[i] for i in id_list if i in by_id]
        else:
            rows = sorted(rows, key=lambda r: r.updated_at or datetime.min, reverse=True)
        out: List[Dict[str, Any]] = []
        for s in rows:
            row = {}
            for m in selected:
                key = m.export_column_name or m.field_key
                row[key] = _value_from_workflow_submission(s, m.compile_key)
            out.append(row)
        return out
