"""SO-DELIVERIES-VIEW: the DOs and Cancel SO documents under each line of one sales order.

Pure placement over three reads (SO lines, DO lines, cancel lines), all filtered on the sales
order's own company. Plan: documentation/plans/scm/PLAN-so-deliveries-view-14oct.md.
"""
from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.autocount_cancel import KIND_CANCEL_SO, MATCH_MATCHED, AutocountCancelDoc, AutocountCancelLine
from app.models.inventory import Warehouse
from app.models.order import Order, OrderLine, OrderStatus
from app.services.autocount_cancel_ingest_service import _live_doc, _names_any
from app.services.autocount_cancel_matching import REASON_GROUP_WHOLE, REASON_UNDETERMINED


def _qty(value):
    return None if value is None else float(value)


def so_line_history(db: Session, so) -> dict:
    lines_by_product: dict[str, list[str]] = {}
    sku_by_product: dict[str, str] = {}
    for line in so.lines:
        lines_by_product.setdefault(str(line.product_id), []).append(str(line.id))
        if line.product is not None:
            sku_by_product[str(line.product_id)] = line.product.product_code
    line_ids = [str(line.id) for line in so.lines]
    line_id_set = set(line_ids)

    per_line = {lid: {"line_id": lid, "deliveries": [], "cancels": []} for lid in line_ids}
    groups = {
        pid: {"sku": sku_by_product.get(pid, ""), "line_ids": ids, "deliveries": [], "cancels": []}
        for pid, ids in lines_by_product.items() if len(ids) > 1
    }
    group_by_sku = {g["sku"]: g for g in groups.values()}

    # -- deliveries
    if line_ids:
        rows = (
            db.query(OrderLine, Order, OrderStatus.status_name, Warehouse.warehouse_code)
            .join(Order, Order.id == OrderLine.order_id)
            .outerjoin(OrderStatus, OrderStatus.id == Order.order_status_id)
            .outerjoin(Warehouse, Warehouse.id == OrderLine.warehouse_id)
            .filter(
                OrderLine.company_id == so.company_id,
                Order.company_id == so.company_id,
                Order.deleted_at.is_(None),
                Order.is_cancelled.is_(False),
                or_(
                    and_(OrderLine.from_doc_no == so.so_number,
                         or_(OrderLine.from_doc_type.is_(None), OrderLine.from_doc_type == "SO")),
                    and_(OrderLine.sales_order_line_id.in_(line_ids),
                         or_(OrderLine.from_doc_no.is_(None), OrderLine.from_doc_no == so.so_number)),
                ),
            )
            .order_by(Order.order_date, Order.order_number, OrderLine.line_sequence)
            .all()
        )
        for ol, order, status_name, location in rows:
            row = {
                "order_id": str(order.id), "do_number": order.order_number,
                "do_date": order.order_date, "location": location, "status": status_name,
                "actual_delivery_date": order.actual_delivery_date, "qty": _qty(ol.quantity),
            }
            link = str(ol.sales_order_line_id) if ol.sales_order_line_id else None
            if link in line_id_set:
                per_line[link]["deliveries"].append(row)
                continue
            ids = lines_by_product.get(str(ol.product_id))
            if not ids:
                continue
            if len(ids) == 1:
                per_line[ids[0]]["deliveries"].append(row)
            else:
                groups[str(ol.product_id)]["deliveries"].append(row)

    # -- Cancel SO documents
    if line_ids:
        crows = (
            db.query(AutocountCancelLine, AutocountCancelDoc)
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(
                AutocountCancelDoc.kind == KIND_CANCEL_SO,
                AutocountCancelDoc.company_id == so.company_id,
                _live_doc(),
                or_(
                    and_(AutocountCancelLine.match_status == MATCH_MATCHED,
                         AutocountCancelLine.sales_order_line_id.in_(line_ids)),
                    and_(AutocountCancelLine.match_status == MATCH_MATCHED, _names_any(line_ids)),
                    and_(AutocountCancelLine.match_reason == REASON_UNDETERMINED,
                         AutocountCancelLine.from_doc_no == so.so_number),
                ),
            )
            .order_by(AutocountCancelDoc.doc_date, AutocountCancelDoc.doc_no, AutocountCancelLine.seq)
            .all()
        )
        for cl, doc in crows:
            def row(qty):
                return {"doc_no": doc.doc_no, "doc_date": doc.doc_date,
                        "location": cl.location_code, "match_reason": cl.match_reason, "qty": qty}

            if cl.sales_order_line_id and str(cl.sales_order_line_id) in line_id_set:
                per_line[str(cl.sales_order_line_id)]["cancels"].append(row(_qty(cl.qty)))
            elif cl.match_reason == REASON_GROUP_WHOLE:
                for cid in (str(i) for i in cl.candidate_line_ids or []):
                    if cid in line_id_set:
                        per_line[cid]["cancels"].append(row(None))
            elif cl.match_reason == REASON_UNDETERMINED:
                group = group_by_sku.get(cl.item_code)
                if group is not None:
                    group["cancels"].append(row(_qty(cl.qty)))

    return {"lines": list(per_line.values()), "groups": list(groups.values())}
