"""Incoming-stock service: user-facing business logic for "is there incoming stock?" enquiries.

This service enforces the rules from next_agents/incoming_stock_enquiries.txt:

- Only "still incoming" rows are considered:
    * `line_status != 'received'`
    * `remaining_incoming = max(quantity_shipped - quantity_received, 0) > 0`

- The response NEVER exposes:
    * internal UUIDs (shipment id, product id, spo allocation id, attachment id...)
    * `quantity_received`, `quantity_rejected`, `receipt_status`, SPO numbers, picking-line ids
    * any information about what has already been received

- The response DOES expose:
    * product_code, product_name
    * shipment_number, shipping_container_number, estimated_arrival_date, batch_number
    * warehouse_code, warehouse_name, allocated_quantity (aggregated per warehouse; in the
      emitted `warehouse_allocations` list it is what is STILL TO COME per warehouse -
      allocated less what that warehouse's allocations already received - and a warehouse
      with nothing left is omitted, see `_warehouse_allocations_for(outstanding_only=True)`)
    * attachment filename / file_path / mime_type (only when present)
    * remaining_incoming_quantity (computed server-side)
    * unallocated_quantity (computed server-side - the GAP only, never the shipped base;
      see `_unallocated_quantity`)

The source of truth for "still incoming" is `inbound_shipment_lines`; `spo_allocations` is used
only to aggregate warehouse allocation summaries. GRN / picking data is NEVER read here - it is
surfaced through a separate, explicit method only when the user asks.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import and_, case, func, or_, true
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from app.models.product import Product
from app.models.resources import Attachment
from app.services.field_access import GATED_FIELDS, NON_CLEARANCE_KEYS

#: The gated columns, selected unconditionally - the route strips whatever this
#: caller may not see. Leaving one out here reads as None on the row and would be
#: served to an ENTITLED caller as "not reached yet": a silent wrong answer rather
#: than a crash. That bug shipped for one build.
CLEARANCE_KEYS = tuple(k for k in GATED_FIELDS["incoming_stock"] if k not in NON_CLEARANCE_KEYS)
from app.services.identifier_resolver import resolve_identifier
from app.services.company_scope import stamp_lookup_companies
from app.services.contact_brand_scope import product_in_scope_clauses
from app.services.fuzzy_resolver import resolve_via_embedding_then_ilike
# Header OR any line: a container filled by two factories has no header supplier,
# so the header alone would hide it from both of them.
from app.services.procurement_service import _apportion, shipment_supplier_predicate
# Imported rather than redefined, so the value cannot drift between the two modules.
from app.services.scm.proforma_invoice_service import _DRAFT_SHIPMENT_STATUS


# Line statuses considered "received" and therefore excluded from incoming-stock results.
_RECEIVED_STATUSES = ("received",)


def _remaining_expr():
    """SQL expression: GREATEST(quantity_shipped - quantity_received, 0)."""
    diff = InboundShipmentLine.quantity_shipped - func.coalesce(InboundShipmentLine.quantity_received, 0)
    return case((diff > 0, diff), else_=0)


def _still_incoming_filter():
    """Filter clause: line is still incoming (status != received AND remaining > 0)."""
    remaining = _remaining_expr()
    return and_(
        ~InboundShipmentLine.line_status.in_(_RECEIVED_STATUSES),
        remaining > 0,
    )


def _not_draft_shipment_filter():
    """Filter clause: shipment is not a draft.

    A draft shipment is proforma-created (see `proforma_invoice_service.
    _DRAFT_SHIPMENT_STATUS`) and stays that way until someone actually places or confirms
    it. This whole service is the MCP/AI-assistant-facing surface (see module docstring),
    so a draft must never leak into a salesperson's answer as if it were a real incoming
    container. Applied everywhere in this service except `grn_records`, which needs no
    equivalent guard - see the note there.
    """
    return InboundShipment.shipment_status != _DRAFT_SHIPMENT_STATUS


def _region_filter(regions):
    """Filter clause: the shipment serves at least one of `regions`. `None` = no filter
    (staff, bare API key), so the clause is always safe to AND in."""
    if regions is None:
        return true()
    return InboundShipment.regions.overlap(sorted(regions))


def _unallocated_quantity(
    quantity_shipped: Any,
    allocations: list[dict[str, Any]],
    allocated: Optional[int] = None,
) -> Optional[int]:
    """How much of this incoming line is not yet claimed by any warehouse (SPO allocation).

    Only the GAP is returned, never the shipped base. `remaining_incoming_quantity`
    (= shipped - received) is already public, so exposing the base too would let any
    consumer derive `quantity_received`, which the rules at the top of this module forbid.

    The base is `quantity_shipped`, NOT `remaining_incoming_quantity`: allocations are
    made against the shipped amount and are never decremented as goods are received,
    so on a partially-received line the two have different bases.

    Returns None when there is nothing to flag:
      * no allocations at all - the empty `warehouse_allocations` list is itself the signal
      * fully allocated
      * over-allocated (data error; clamp rather than report a negative gap)
    """
    if not allocations:
        return None
    try:
        base = int(quantity_shipped or 0)
    except (TypeError, ValueError):
        return None
    # `allocations` is per (shipment, PRODUCT), and one product may now sit on two lines of
    # one container (the supplier's carton split, S4) - so the caller passes THIS line's own
    # apportioned share (`_allocated_by_line`). Without it both lines were measured against
    # the product's whole allocation and a genuinely unallocated line read as fully covered
    # (AC-D6).
    if allocated is None:
        allocated = sum(int(a.get("allocated_quantity") or 0) for a in allocations)
    gap = base - int(allocated or 0)
    return gap if gap > 0 else None


def _allocated_by_line(
    rows: list[Any], warehouse_map: dict[tuple[str, str], list[dict[str, Any]]]
) -> dict[str, int]:
    """`{shipment line id: its own share of the product's allocation}` (AC-D5/AC-D6).

    The same walk `refresh_shipment_line_statuses` uses for the stored figures: that
    product's lines on that container in `(created_at, id)` order, each taking up to its
    own `quantity_shipped`, the last taking whatever is left. 50 + 35 shipped with 60
    allocated reads 50 / 10, so the gaps read 0 and 25.
    """
    from app.services.procurement_service import _apportion

    by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in rows:
        by_pair[(str(r.shipment_id), str(r.product_id))].append(r)
    out: dict[str, int] = {}
    for pair, group in by_pair.items():
        total = sum(
            int(a.get("allocated_quantity") or 0) for a in warehouse_map.get(pair, [])
        )
        ordered = sorted(
            group, key=lambda r: (r.created_at or datetime.min, str(r.id))
        )
        out.update(_apportion(total, ordered))
    return out


def _attachment_payload(attachment: Optional[Attachment]) -> Optional[dict[str, Any]]:
    if attachment is None:
        return None
    return {
        "filename": attachment.original_filename,
        "file_path": attachment.file_path,
        "mime_type": attachment.mime_type,
    }


def earliest_packing_list_shipment(
    db: Session, product_ids: list[str], regions=None
) -> dict[str, tuple[str, date, str]]:
    """R5 (chatbot stock ask v2 S3): the earliest still-incoming shipment WITH a
    packing list, for each product id, at ANY location (AC-SA304 to AC-SA309).

    A candidate line must be `_still_incoming_filter()` (not received, remaining
    quantity > 0) on a `_not_draft_shipment_filter()` shipment that carries an
    `attachment_id` (the packing list) AND a non-NULL `estimated_arrival_date` -
    lavish review R10: a shipment lacking a date never qualifies, there is no
    "ETA to be confirmed" answer. `eta_delay_date` is never read (R5). No warehouse
    join: `spo_allocations` and purchase order lines are never a source for this
    read, so a shipment's line landing outside the asking contact's policy
    locations still qualifies (AC-SA307 - this read has no location dimension to
    filter by in the first place).

    Returns `{product_id: (shipment_id, estimated_arrival_date, attachment_id)}` -
    one entry per product id with a qualifying shipment; a product id with none is
    simply absent.
    """
    if not product_ids:
        return {}
    rows = (
        db.query(
            InboundShipmentLine.product_id,
            InboundShipment.id,
            InboundShipment.estimated_arrival_date,
            InboundShipment.attachment_id,
        )
        .join(InboundShipment, InboundShipment.id == InboundShipmentLine.shipment_id)
        .filter(
            InboundShipmentLine.product_id.in_(product_ids),
            _not_draft_shipment_filter(),
            _region_filter(regions),
            _still_incoming_filter(),
            *product_in_scope_clauses(db, InboundShipmentLine.product_id),
            InboundShipment.attachment_id.isnot(None),
            InboundShipment.estimated_arrival_date.isnot(None),
        )
        .order_by(
            InboundShipmentLine.product_id,
            InboundShipment.estimated_arrival_date.asc(),
            # Nit, review round 1: two qualifying shipments on the same
            # `estimated_arrival_date` otherwise pick an arbitrary one (Postgres
            # makes no promise about which `DISTINCT ON` row wins a tie) - id is
            # not a meaningful order, only a deterministic one.
            InboundShipment.id.asc(),
        )
        .distinct(InboundShipmentLine.product_id)
        .all()
    )
    return {
        str(product_id): (str(shipment_id), eta, str(attachment_id))
        for product_id, shipment_id, eta, attachment_id in rows
    }


class IncomingStockService:
    """User-facing incoming-stock retrieval."""

    def __init__(self, db: Session, regions=None):
        self.db = db
        # The regions the asking contact may be told about; None = no contact, no filter.
        self.regions = regions

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _warehouse_allocations_for(
        self,
        shipment_product_pairs: list[tuple[str, str]],
        *,
        outstanding_only: bool = False,
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Aggregate allocated_quantity by warehouse for each (shipment_id, product_id) pair.

        Returns {(shipment_id, product_id): [{warehouse_code, warehouse_name, allocated_quantity}]}.
        Only SPO allocations with allocated_quantity > 0 are included. No receipt data leaks.

        AC-H19 (round 5, PLAN-hide-retired-spo-lines): also excludes a RETIRED allocation
        (`spo_supply.visible_line_clauses()`) - this is the query behind the n8n incoming-
        stock badge and its `unallocated_quantity` gap (`_unallocated_quantity` above), a
        read with no persisted column and no purchasing arithmetic behind it, unlike
        `refresh_shipment_line_statuses`' stored `spo_allocated_quantity` (AC-H17, narrowed
        away from this file for exactly that reason). A line AutoCount stopped naming is
        not allocated supply for this container any more than it is anywhere else, so
        counting it here credited coverage that no longer exists and understated the gap.

        `outstanding_only` (D35, SPO-XLSX-SUPERSEDE round 2) is the list the caller SHOWS:
        each allocation counts only what is still to come on it - `allocated - max(received,
        stated_received)`, nothing at all once the line is closed or fully received - and a
        warehouse left at 0 is dropped. Without it a container with one warehouse's share
        already in read "Incoming 4, BRW-IB (22), BRW-NTC (77)". The default (full allocated
        totals) stays the base of the unallocated-gap arithmetic, which measures against
        `quantity_shipped` and so needs the undecremented allocation.
        """
        if not shipment_product_pairs:
            return {}
        from app.services.scm import spo_supply

        shipment_ids = list({sid for sid, _ in shipment_product_pairs})
        product_ids = list({pid for _, pid in shipment_product_pairs})
        if outstanding_only:
            received = func.greatest(
                func.coalesce(SPOAllocation.quantity_received, 0),
                func.coalesce(SPOAllocation.stated_received, 0),
            )
            quantity = case(
                (
                    or_(
                        SPOAllocation.line_status == "closed",
                        SPOAllocation.receipt_status.in_(spo_supply.RECEIVED_RECEIPT_STATUSES),
                    ),
                    0,
                ),
                else_=func.greatest(SPOAllocation.allocated_quantity - received, 0),
            )
        else:
            quantity = SPOAllocation.allocated_quantity
        rows = (
            self.db.query(
                SPOAllocation.inbound_shipment_id,
                SPOAllocation.product_id,
                Warehouse.warehouse_code,
                Warehouse.warehouse_name,
                func.coalesce(func.sum(quantity), 0).label("allocated_qty"),
            )
            .join(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
            .filter(
                SPOAllocation.inbound_shipment_id.in_(shipment_ids),
                SPOAllocation.product_id.in_(product_ids),
                SPOAllocation.allocated_quantity > 0,
                *spo_supply.visible_line_clauses(),
            )
            .group_by(
                SPOAllocation.inbound_shipment_id,
                SPOAllocation.product_id,
                Warehouse.warehouse_code,
                Warehouse.warehouse_name,
            )
            .order_by(Warehouse.warehouse_code.asc())
            .all()
        )
        result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for shipment_id, product_id, wh_code, wh_name, qty in rows:
            if outstanding_only and int(qty or 0) <= 0:
                continue
            key = (str(shipment_id), str(product_id))
            result[key].append(
                {
                    "warehouse_code": wh_code,
                    "warehouse_name": wh_name,
                    "allocated_quantity": int(qty or 0),
                }
            )
        return result

    def _attachments_for_shipments(
        self, shipment_ids: list[str]
    ) -> dict[str, Optional[dict[str, Any]]]:
        if not shipment_ids:
            return {}
        rows = (
            self.db.query(InboundShipment.id, Attachment)
            .outerjoin(Attachment, Attachment.id == InboundShipment.attachment_id)
            .filter(InboundShipment.id.in_(shipment_ids))
            .all()
        )
        return {str(sid): _attachment_payload(att) for sid, att in rows}

    # ------------------------------------------------------------------
    # Layer 1 + 2: by product
    # ------------------------------------------------------------------
    def incoming_for_product(
        self,
        *,
        product_id: Optional[str] = None,
        product_ids: Optional[list[str]] = None,
        query: Optional[str] = None,
        eta_from: Optional[date] = None,
        eta_to: Optional[date] = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Return incoming-stock summary grouped by product.

        Accepts one or many `product_ids` (UUID or product_code / SKU). Legacy
        single `product_id` parameter is folded into the list. A free-text
        `query` may be supplied instead; results are filtered to still-incoming
        lines only. `eta_from` / `eta_to` (inclusive) narrow to shipments whose
        `estimated_arrival_date` falls in the window - applied on top of the
        product hint, never as a standalone filter (an ETA alone would full-scan).
        """
        limit = max(1, min(limit, 50))

        raw_ids: list[str] = []
        if product_id:
            raw_ids.append(product_id)
        for value in product_ids or []:
            if value:
                raw_ids.append(value)
        # Dedupe while preserving order.
        raw_ids = list(dict.fromkeys(raw_ids))

        product_filters = []
        matched_candidates: list[str] = []
        resolved_ids: list[str] = []
        if raw_ids:
            for ident in raw_ids:
                ids = resolve_identifier(
                    self.db, ident, Product, code_fields=("product_code",)
                )
                if ids:
                    resolved_ids.extend(ids)
            resolved_ids = list(dict.fromkeys(resolved_ids))
            if not resolved_ids:
                # No `stamp_lookup_companies` here (nor on the two guards below):
                # this exit is reached only when NOTHING the caller asked for
                # resolved, so the requested product set is empty and there is no
                # company to name. The labelled empty path is the one at the end
                # of the method, where the products did resolve.
                return {"data": [], "empty": True, "matched_candidates": []}
            product_filters.append(Product.id.in_(resolved_ids))
        if query and query.strip():
            fuzzy_clause, canonical_values = resolve_via_embedding_then_ilike(
                self.db,
                query.strip(),
                source_type="product",
                ilike_columns=[Product.product_code, Product.product_name],
                canonical_model=Product,
                canonical_fields=("product_code", "product_name"),
            )
            if fuzzy_clause is not None:
                product_filters.append(fuzzy_clause)
            for v in canonical_values:
                if v and v not in matched_candidates:
                    matched_candidates.append(v)
        if not product_filters:
            # Require at least one product hint to avoid accidental full scans
            return {
                "data": [],
                "empty": True,
                "matched_candidates": matched_candidates,
                "message": "Provide product_id (UUID or product_code) or a query term.",
            }

        # ETA window - applied alongside the product hint, kept out of the
        # product_filters guard above so an ETA alone can't trigger a full scan.
        date_filters = []
        if eta_from is not None:
            date_filters.append(InboundShipment.estimated_arrival_date >= eta_from)
        if eta_to is not None:
            date_filters.append(InboundShipment.estimated_arrival_date <= eta_to)

        remaining = _remaining_expr().label("remaining_incoming")
        rows = (
            self.db.query(
                # The LINE's own identity, so the product's allocation can be apportioned
                # across the two lines a carton split leaves (AC-D6). Never emitted.
                InboundShipmentLine.id,
                InboundShipmentLine.created_at,
                InboundShipmentLine.shipment_id,
                InboundShipmentLine.product_id,
                InboundShipmentLine.batch_number,
                # Allocation base - used to derive the gap, never emitted (see
                # `_unallocated_quantity`).
                InboundShipmentLine.quantity_shipped,
                remaining,
                InboundShipment.shipment_number,
                InboundShipment.shipping_container_number,
                InboundShipment.estimated_arrival_date,
                Product.product_code,
                Product.product_name,
                # The row is rooted on the product, so the product's company is the
                # row's company for per-company labelling.
                Product.company_id,
            )
            .join(InboundShipment, InboundShipment.id == InboundShipmentLine.shipment_id)
            .join(Product, Product.id == InboundShipmentLine.product_id)
            .filter(
                _still_incoming_filter(),
                _not_draft_shipment_filter(),
                _region_filter(self.regions),
                *product_filters,
                *date_filters,
            )
            .order_by(
                Product.product_code.asc(),
                InboundShipment.estimated_arrival_date.asc().nulls_last(),
                InboundShipment.shipment_number.asc(),
            )
            .all()
        )

        if not rows:
            result: dict[str, Any] = {
                "data": [],
                "empty": True,
                "matched_candidates": matched_candidates,
            }
            # Nothing incoming anywhere, but the caller still deserves to know
            # which companies were searched.
            stamp_lookup_companies(self.db, result, [], product_ids=resolved_ids)
            # Data-miss (§3.3): the product resolved but has no incoming rows. Offer
            # data-bearing variant/neighbour alternatives on the empty path only.
            # Best-effort: never turn an empty result into a 500 (AC-R1).
            try:
                alternatives = self._incoming_entity_alternatives(resolved_ids)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "incoming alternatives probe failed", exc_info=True
                )
                alternatives = None
            if alternatives:
                result["alternatives"] = alternatives
                result["relaxed_axis"] = "entity"
            return result

        pairs = [(str(r.shipment_id), str(r.product_id)) for r in rows]
        warehouse_map = self._warehouse_allocations_for(pairs)
        outstanding_map = self._warehouse_allocations_for(pairs, outstanding_only=True)
        allocated_by_line = _allocated_by_line(rows, warehouse_map)
        attachment_map = self._attachments_for_shipments(
            [str(r.shipment_id) for r in rows]
        )

        grouped: dict[str, dict[str, Any]] = {}
        for r in rows:
            pkey = str(r.product_id)
            bucket = grouped.setdefault(
                pkey,
                {
                    "product_code": r.product_code,
                    "product_name": r.product_name,
                    "company_id": str(r.company_id) if r.company_id else None,
                    "nearest_estimated_arrival_date": None,
                    "shipments": [],
                },
            )
            if r.estimated_arrival_date is not None:
                current = bucket["nearest_estimated_arrival_date"]
                if current is None or r.estimated_arrival_date < current:
                    bucket["nearest_estimated_arrival_date"] = r.estimated_arrival_date

            ship_allocations = warehouse_map.get((str(r.shipment_id), pkey), [])

            bucket["shipments"].append(
                {
                    "shipment_number": r.shipment_number,
                    "shipping_container_number": r.shipping_container_number,
                    "estimated_arrival_date": r.estimated_arrival_date,
                    "batch_number": r.batch_number,
                    "remaining_incoming_quantity": int(r.remaining_incoming or 0),
                    "unallocated_quantity": _unallocated_quantity(
                        r.quantity_shipped,
                        ship_allocations,
                        allocated_by_line.get(str(r.id)),
                    ),
                    "warehouse_allocations": outstanding_map.get((str(r.shipment_id), pkey), []),
                    "attachment": attachment_map.get(str(r.shipment_id)),
                }
            )

        data: list[dict[str, Any]] = list(grouped.values())

        data.sort(key=lambda d: (d["product_code"] or "").lower())
        page_rows = data[:limit]
        result = {
            "data": page_rows,
            "empty": False,
            "pagination": {"total": len(data), "page": 1, "limit": limit},
            "matched_candidates": matched_candidates,
        }
        stamp_lookup_companies(self.db, result, page_rows, product_ids=resolved_ids)
        return result

    def _incoming_entity_alternatives(self, product_ids: list[str]) -> list[dict]:
        """Data-bearing variant/neighbour alternatives for an empty incoming result.

        Only fires when exactly ONE input product resolved. The has-data gate =
        product has at least one still-incoming shipment line (`eta row`), regardless
        of any ETA window the original query applied (a neighbour "has an eta row" per
        §3.5, so window-narrowing does not gate the substitute).
        """
        ids = list(dict.fromkeys(product_ids or []))
        if len(ids) != 1:
            return []
        prod = (
            self.db.query(Product.product_code)
            .filter(Product.id == ids[0])
            .first()
        )
        if not prod or not prod.product_code:
            return []

        def _has_incoming(candidate_ids: list[str]) -> set[str]:
            if not candidate_ids:
                return set()
            rows = (
                self.db.query(InboundShipmentLine.product_id)
                .join(InboundShipment, InboundShipment.id == InboundShipmentLine.shipment_id)
                .filter(
                    _still_incoming_filter(),
                    _not_draft_shipment_filter(),
                    _region_filter(self.regions),
                    InboundShipmentLine.product_id.in_(candidate_ids),
                )
                .distinct()
                .all()
            )
            return {str(row.product_id) for row in rows}

        from app.services.entity_resolver import find_entity_neighbours_with_data

        return find_entity_neighbours_with_data(
            self.db, prod.product_code, has_data=_has_incoming
        )

    # ------------------------------------------------------------------
    # Layer 1: shipments (T2)
    # ------------------------------------------------------------------
    def incoming_shipments(
        self,
        *,
        query: Optional[str] = None,
        shipment_ids: Optional[list[str]] = None,
        supplier_ids: Optional[list[str]] = None,
        eta_from: Optional[date] = None,
        eta_to: Optional[date] = None,
        page: int = 1,
        limit: int = 10,
    ) -> dict[str, Any]:
        """List shipments that still have incoming lines, user-facing fields only."""
        page = max(1, page)
        limit = max(1, min(limit, 50))

        incoming_lines = (
            self.db.query(
                InboundShipmentLine.shipment_id.label("sid"),
                func.count(func.distinct(InboundShipmentLine.product_id)).label("distinct_products"),
                func.sum(_remaining_expr()).label("total_remaining"),
            )
            .filter(_still_incoming_filter(), *product_in_scope_clauses(self.db, InboundShipmentLine.product_id))
            .group_by(InboundShipmentLine.shipment_id)
            .subquery()
        )

        q = (
            self.db.query(
                InboundShipment.id,
                InboundShipment.shipment_number,
                InboundShipment.shipping_container_number,
                InboundShipment.estimated_arrival_date,
                # Selected only so a two-company page can be labelled per company.
                InboundShipment.company_id,
                incoming_lines.c.distinct_products,
                incoming_lines.c.total_remaining,
            )
            .join(incoming_lines, incoming_lines.c.sid == InboundShipment.id)
            .filter(_not_draft_shipment_filter(), _region_filter(self.regions))
        )

        if shipment_ids:
            q = q.filter(InboundShipment.id.in_(shipment_ids))
        if supplier_ids:
            q = q.filter(shipment_supplier_predicate(supplier_ids))
        if query:
            term = f"%{query.strip()}%"
            q = q.filter(
                or_(
                    InboundShipment.shipment_number.ilike(term),
                    InboundShipment.shipping_container_number.ilike(term),
                    InboundShipment.bill_of_lading_number.ilike(term),
                    # `提单号` lands in `forwarder_order_ref` (the SO field) on an upload
                    # made through the supplier-documents dialog (Q1 ruling); a search on
                    # the B/L alone missed every container uploaded that way (S3, review
                    # round 1).
                    InboundShipment.forwarder_order_ref.ilike(term),
                    InboundShipment.invoice_number.ilike(term),
                )
            )
        if eta_from is not None:
            q = q.filter(InboundShipment.estimated_arrival_date >= eta_from)
        if eta_to is not None:
            q = q.filter(InboundShipment.estimated_arrival_date <= eta_to)

        total = q.count()
        rows = (
            q.order_by(
                InboundShipment.estimated_arrival_date.asc().nulls_last(),
                InboundShipment.shipment_number.asc(),
            )
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        if not rows:
            return {"data": [], "empty": True, "pagination": {"total": 0, "page": page, "limit": limit}}

        attachment_map = self._attachments_for_shipments([str(r.id) for r in rows])

        data = [
            {
                "shipment_number": r.shipment_number,
                "shipping_container_number": r.shipping_container_number,
                "estimated_arrival_date": r.estimated_arrival_date,
                "total_remaining_incoming_quantity": int(r.total_remaining or 0),
                "distinct_products_incoming": int(r.distinct_products or 0),
                "company_id": str(r.company_id) if r.company_id else None,
                "attachment": attachment_map.get(str(r.id)),
            }
            for r in rows
        ]
        result = {"data": data, "empty": False, "pagination": {"total": total, "page": page, "limit": limit}}
        # No product input on this tool: the company set is the returned rows' own.
        stamp_lookup_companies(self.db, result, data)
        return result

    # ------------------------------------------------------------------
    # Unified: shipment-rooted with nested product lines (MCP consolidation)
    # ------------------------------------------------------------------
    def incoming_list(
        self,
        *,
        product_ids: Optional[list[str]] = None,
        shipment_ids: Optional[list[str]] = None,
        supplier_ids: Optional[list[str]] = None,
        eta_from: Optional[date] = None,
        eta_to: Optional[date] = None,
        query: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Unified incoming-stock list - shipment-rooted with nested product lines.

        One row per still-incoming shipment. Each carries the still-incoming
        product lines (filtered to the requested products when `product_ids` is
        given), every line with its per-warehouse allocations, plus the
        shipment's packing-list attachment. NO aggregate totals - callers (n8n)
        sum the line quantities themselves. Requires at least one narrowing
        filter (product / shipment / supplier / eta / query) to avoid full scans.
        """
        page = max(1, page)
        limit = max(1, min(limit, 50))

        # Resolve product hints (UUID or product_code / SKU) to canonical ids.
        resolved_pids: list[str] = []
        for ident in product_ids or []:
            if not ident:
                continue
            ids = resolve_identifier(
                self.db, ident, Product, code_fields=("product_code",)
            )
            if ids:
                resolved_pids.extend(ids)
        resolved_pids = list(dict.fromkeys(resolved_pids))
        if (product_ids or []) and not resolved_pids:
            # Caller asked for products that don't resolve → nothing incoming.
            # Nothing resolved means no company to name either, so this exit
            # carries no `lookup_companies` (see incoming_for_product).
            return {
                "data": [],
                "empty": True,
                "pagination": {"total": 0, "page": page, "limit": limit},
            }

        line_filters = [_still_incoming_filter(), *product_in_scope_clauses(self.db, InboundShipmentLine.product_id)]
        if resolved_pids:
            line_filters.append(InboundShipmentLine.product_id.in_(resolved_pids))

        shipment_filters = []
        if shipment_ids:
            shipment_filters.append(InboundShipment.id.in_(shipment_ids))
        if supplier_ids:
            shipment_filters.append(shipment_supplier_predicate(supplier_ids))
        if eta_from is not None:
            shipment_filters.append(InboundShipment.estimated_arrival_date >= eta_from)
        if eta_to is not None:
            shipment_filters.append(InboundShipment.estimated_arrival_date <= eta_to)
        if query and query.strip():
            term = f"%{query.strip()}%"
            shipment_filters.append(
                or_(
                    InboundShipment.shipment_number.ilike(term),
                    InboundShipment.shipping_container_number.ilike(term),
                    InboundShipment.bill_of_lading_number.ilike(term),
                    # `提单号` lands in `forwarder_order_ref` (the SO field) on an upload
                    # made through the supplier-documents dialog (Q1 ruling); a search on
                    # the B/L alone missed every container uploaded that way (S3, review
                    # round 1).
                    InboundShipment.forwarder_order_ref.ilike(term),
                    InboundShipment.invoice_number.ilike(term),
                )
            )

        # Require at least one narrower so an empty call can't full-scan.
        if not resolved_pids and not shipment_filters:
            return {
                "data": [],
                "empty": True,
                "pagination": {"total": 0, "page": page, "limit": limit},
                "message": "Provide product_ids, shipment_ids, supplier_ids, an eta window, or query.",
            }

        # Step 1: distinct still-incoming shipments matching all filters, paged by ETA.
        ship_q = (
            self.db.query(
                InboundShipment.id,
                InboundShipment.shipment_number,
                InboundShipment.shipping_container_number,
                InboundShipment.estimated_arrival_date,
                # The row is rooted on the shipment, so the shipment's company is
                # the row's company for per-company labelling.
                InboundShipment.company_id,
                # Clearance columns are selected explicitly. This query returns
                # column tuples, not ORM instances, so a getattr on a column that
                # was not selected reads as None - which the entitlement gate would
                # then happily pass through as "not reached yet".
                *(getattr(InboundShipment, key) for key in CLEARANCE_KEYS),
            )
            .join(
                InboundShipmentLine,
                InboundShipmentLine.shipment_id == InboundShipment.id,
            )
            .filter(
                _not_draft_shipment_filter(),
                _region_filter(self.regions),
                *line_filters,
                *shipment_filters,
            )
            .distinct()
        )
        total = ship_q.count()
        ship_rows = (
            ship_q.order_by(
                InboundShipment.estimated_arrival_date.asc().nulls_last(),
                InboundShipment.shipment_number.asc(),
            )
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        if not ship_rows:
            result: dict[str, Any] = {
                "data": [],
                "empty": True,
                "pagination": {"total": 0, "page": page, "limit": limit},
            }
            # Nothing incoming anywhere, but the caller still deserves to know
            # which companies were searched.
            stamp_lookup_companies(self.db, result, [], product_ids=resolved_pids)
            # Data-miss (§3.3): a product resolved but nothing is incoming for it.
            # Offer data-bearing variant/neighbour alternatives - same probe the
            # by-product tool uses. Product-driven query only; best-effort (AC-R1:
            # never turn an empty result into a 500).
            if resolved_pids:
                try:
                    alternatives = self._incoming_entity_alternatives(resolved_pids)
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning(
                        "incoming-list alternatives probe failed", exc_info=True
                    )
                    alternatives = None
                if alternatives:
                    result["alternatives"] = alternatives
                    result["relaxed_axis"] = "entity"
            return result
        page_ship_ids = [str(s.id) for s in ship_rows]

        # Step 2: still-incoming lines for the paged shipments (same product filter).
        remaining = _remaining_expr().label("remaining_incoming")
        line2_filters = [
            _still_incoming_filter(),
            InboundShipmentLine.shipment_id.in_(page_ship_ids),
        ]
        if resolved_pids:
            line2_filters.append(InboundShipmentLine.product_id.in_(resolved_pids))
        line_rows = (
            self.db.query(
                # The LINE's own identity, for the same apportioning as above (AC-D6).
                InboundShipmentLine.id,
                InboundShipmentLine.created_at,
                InboundShipmentLine.shipment_id,
                InboundShipmentLine.product_id,
                InboundShipmentLine.batch_number,
                InboundShipmentLine.quantity_shipped,
                remaining,
                Product.product_code,
                Product.product_name,
            )
            .join(Product, Product.id == InboundShipmentLine.product_id)
            .filter(*line2_filters)
            .order_by(Product.product_code.asc())
            .all()
        )

        pairs = [(str(r.shipment_id), str(r.product_id)) for r in line_rows]
        warehouse_map = self._warehouse_allocations_for(pairs)
        outstanding_map = self._warehouse_allocations_for(pairs, outstanding_only=True)
        allocated_by_line = _allocated_by_line(line_rows, warehouse_map)
        attachment_map = self._attachments_for_shipments(page_ship_ids)

        lines_by_ship: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in line_rows:
            skey = str(r.shipment_id)
            allocations = warehouse_map.get((skey, str(r.product_id)), [])
            lines_by_ship[skey].append(
                {
                    "product_code": r.product_code,
                    "product_name": r.product_name,
                    "batch_number": r.batch_number,
                    "remaining_incoming_quantity": int(r.remaining_incoming or 0),
                    "unallocated_quantity": _unallocated_quantity(
                        r.quantity_shipped,
                        allocations,
                        allocated_by_line.get(str(r.id)),
                    ),
                    "warehouse_allocations": outstanding_map.get(
                        (skey, str(r.product_id)), []
                    ),
                }
            )

        data = [
            {
                "shipment_number": s.shipment_number,
                "shipping_container_number": s.shipping_container_number,
                "estimated_arrival_date": s.estimated_arrival_date,
                # Container clearance. Present for every caller HERE and stripped
                # by the entitlement gate at the route, so the gate has exactly one
                # implementation instead of one per query path.
                **{key: getattr(s, key, None) for key in CLEARANCE_KEYS},
                "company_id": str(s.company_id) if s.company_id else None,
                "attachment": attachment_map.get(str(s.id)),
                "lines": lines_by_ship.get(str(s.id), []),
            }
            for s in ship_rows
        ]
        result = {
            "data": data,
            "empty": False,
            "pagination": {"total": total, "page": page, "limit": limit},
        }
        stamp_lookup_companies(self.db, result, data, product_ids=resolved_pids)
        return result

    # ------------------------------------------------------------------
    # Layer 1 + 2: products in a shipment (T3)
    # ------------------------------------------------------------------
    def shipment_incoming_products(self, shipment_id: str) -> dict[str, Any]:
        """Return only the still-incoming products on a given shipment, with warehouse allocation."""
        resolved = resolve_identifier(
            self.db,
            shipment_id,
            InboundShipment,
            code_fields=(
                "shipment_number",
                "shipping_container_number",
                "bill_of_lading_number",
                "invoice_number",
            ),
            extra_filters=(_not_draft_shipment_filter(), _region_filter(self.regions)),
        )
        if resolved is None or not resolved:
            return {"data": None, "empty": True}
        # A business code can name several shipments; the region filter picks among them,
        # so an ambiguous ref resolves to the one this contact may see.
        shipment = (
            self.db.query(InboundShipment)
            .filter(
                InboundShipment.id.in_(resolved),
                _not_draft_shipment_filter(),
                _region_filter(self.regions),
            )
            .first()
        )
        if not shipment:
            return {"data": None, "empty": True}
        shipment_uuid = shipment.id

        remaining = _remaining_expr().label("remaining_incoming")
        line_rows = (
            self.db.query(
                InboundShipmentLine.id,
                InboundShipmentLine.product_id,
                InboundShipmentLine.quantity_shipped,
                InboundShipmentLine.created_at,
                remaining,
                Product.product_code,
                Product.product_name,
            )
            .join(Product, Product.id == InboundShipmentLine.product_id)
            .filter(
                InboundShipmentLine.shipment_id == shipment_uuid,
                _still_incoming_filter(),
            )
            .order_by(Product.product_code.asc())
            .all()
        )

        attachment = (
            self.db.query(Attachment)
            .filter(Attachment.id == shipment.attachment_id)
            .first()
            if shipment.attachment_id
            else None
        )

        if not line_rows:
            return {
                "data": {
                    "shipment_number": shipment.shipment_number,
                    "shipping_container_number": shipment.shipping_container_number,
                    "estimated_arrival_date": shipment.estimated_arrival_date,
                    "attachment": _attachment_payload(attachment),
                    "products": [],
                },
                "empty": True,
            }

        pairs = [(str(shipment_uuid), str(r.product_id)) for r in line_rows]
        warehouse_map = self._warehouse_allocations_for(pairs)
        outstanding_map = self._warehouse_allocations_for(pairs, outstanding_only=True)

        # S4/AC-D6: a product's own allocation is APPORTIONED across its own lines (the
        # same `(created_at, id)` order and the same `_apportion` `refresh_shipment_line_
        # statuses` uses), never the product's whole total read against one line's own
        # `quantity_shipped` - that read a container's second line of a split product as
        # under-allocated by everything the FIRST line already claimed.
        rows_by_product: dict[str, list] = {}
        for r in line_rows:
            rows_by_product.setdefault(str(r.product_id), []).append(r)
        allocated_share_by_line: dict[str, int] = {}
        for product_id, product_rows in rows_by_product.items():
            product_rows.sort(key=lambda r: (r.created_at or datetime.min, str(r.id)))
            allocations = warehouse_map.get((str(shipment_uuid), product_id), [])
            total_allocated = sum(int(a.get("allocated_quantity") or 0) for a in allocations)
            allocated_share_by_line.update(_apportion(total_allocated, product_rows))

        products = []
        for r in line_rows:
            allocations = warehouse_map.get((str(shipment_uuid), str(r.product_id)), [])
            share = allocated_share_by_line.get(str(r.id), 0)
            gap = int(r.quantity_shipped or 0) - share
            products.append(
                {
                    "product_code": r.product_code,
                    "product_name": r.product_name,
                    "remaining_incoming_quantity": int(r.remaining_incoming or 0),
                    "unallocated_quantity": gap if allocations and gap > 0 else None,
                    "warehouse_allocations": outstanding_map.get(
                        (str(shipment_uuid), str(r.product_id)), []
                    ),
                }
            )

        return {
            "data": {
                "shipment_number": shipment.shipment_number,
                "shipping_container_number": shipment.shipping_container_number,
                "estimated_arrival_date": shipment.estimated_arrival_date,
                "attachment": _attachment_payload(attachment),
                "products": products,
            },
            "empty": False,
        }

    # ------------------------------------------------------------------
    # Layer 3: attachment
    # ------------------------------------------------------------------
    def shipment_attachment(self, shipment_id: str) -> Optional[dict[str, Any]]:
        resolved = resolve_identifier(
            self.db,
            shipment_id,
            InboundShipment,
            code_fields=(
                "shipment_number",
                "shipping_container_number",
                "bill_of_lading_number",
                "invoice_number",
            ),
            extra_filters=(_not_draft_shipment_filter(), _region_filter(self.regions)),
        )
        if resolved is None or not resolved:
            return None
        row = (
            self.db.query(InboundShipment.shipment_number, Attachment)
            .outerjoin(Attachment, Attachment.id == InboundShipment.attachment_id)
            .filter(
                InboundShipment.id.in_(resolved),
                _not_draft_shipment_filter(),
                _region_filter(self.regions),
            )
            .first()
        )
        if not row:
            return None
        shipment_number, attachment = row
        return {
            "shipment_number": shipment_number,
            "attachment": _attachment_payload(attachment),
        }

    # ------------------------------------------------------------------
    # Layer 4: GRN (only on explicit request)
    # ------------------------------------------------------------------
    def grn_records(
        self,
        *,
        shipment_id: Optional[str] = None,
        product_id: Optional[str] = None,
        shipment_uuids: Optional[list[str]] = None,
        product_uuids: Optional[list[str]] = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Minimal GRN records for explicit 'has a GRN been created?' enquiries.

        No quantities are surfaced. GRN matches via two paths (union):
          (1) SPO path: PickingHeader.spo_number → SPOAllocation (shipment + product)
          (2) Direct path: PickingLine.product_id (covers GRNs without SPO linkage)
        Either path satisfies the filter; both run when product is supplied.

        Callers may pass either single string ids (legacy: resolved via `resolve_identifier`)
        or pre-resolved UUID lists (`shipment_uuids` / `product_uuids` from entity buckets).
        UUID lists take precedence when both are supplied.

        Deliberately NOT filtered by `_not_draft_shipment_filter()`: a draft shipment has
        no SPOAllocation and no picked GRN (nothing has been physically received against a
        proforma-created draft), so path (1) can never match one, and path (2) is keyed on
        product only, which is not shipment-scoped at all. There is nothing here for the
        filter to exclude.
        """
        limit = max(1, min(limit, 50))

        if shipment_uuids is None and shipment_id:
            shipment_uuids = resolve_identifier(
                self.db,
                shipment_id,
                InboundShipment,
                code_fields=(
                    "shipment_number",
                    "shipping_container_number",
                    "bill_of_lading_number",
                    "invoice_number",
                ),
            )
            if shipment_uuids is not None and not shipment_uuids:
                return {"data": [], "empty": True}
        if product_uuids is None and product_id:
            product_uuids = resolve_identifier(
                self.db, product_id, Product, code_fields=("product_code",)
            )
            if product_uuids is not None and not product_uuids:
                return {"data": [], "empty": True}

        if not shipment_uuids and not product_uuids:
            return {"data": [], "empty": True}

        shipment_number_expr = InboundShipment.shipment_number

        # Path 1: via SPO allocations + PickingHeader.spo_number.
        spo_alloc_filter = [SPOAllocation.spo_number.isnot(None)]
        if shipment_uuids:
            spo_alloc_filter.append(SPOAllocation.inbound_shipment_id.in_(shipment_uuids))
        if product_uuids:
            spo_alloc_filter.append(SPOAllocation.product_id.in_(product_uuids))
        spo_numbers = [
            s[0]
            for s in self.db.query(func.distinct(SPOAllocation.spo_number))
            .filter(*spo_alloc_filter)
            .all()
            if s[0]
        ]

        spo_rows: list = []
        if spo_numbers:
            spo_rows = (
                self.db.query(
                    PickingHeader.picking_number,
                    PickingHeader.picking_date,
                    PickingHeader.picking_status,
                    shipment_number_expr.label("shipment_number"),
                )
                .join(PickingLine, PickingLine.picking_header_id == PickingHeader.id)
                .join(SPOAllocation, SPOAllocation.id == PickingLine.spo_allocation_id)
                .join(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
                .filter(
                    PickingHeader.picking_type == "goods_received",
                    PickingHeader.spo_number.in_(spo_numbers),
                )
                .group_by(
                    PickingHeader.picking_number,
                    PickingHeader.picking_date,
                    PickingHeader.picking_status,
                    shipment_number_expr,
                )
                .all()
            )

        # Path 2: direct via PickingLine.product_id (no SPO required). Shipment_number
        # comes from SPOAllocation when picking_line links to one; otherwise NULL.
        direct_rows: list = []
        if product_uuids:
            direct_q = (
                self.db.query(
                    PickingHeader.picking_number,
                    PickingHeader.picking_date,
                    PickingHeader.picking_status,
                    func.max(shipment_number_expr).label("shipment_number"),
                )
                .join(PickingLine, PickingLine.picking_header_id == PickingHeader.id)
                .outerjoin(
                    SPOAllocation, SPOAllocation.id == PickingLine.spo_allocation_id
                )
                .outerjoin(
                    InboundShipment,
                    InboundShipment.id == SPOAllocation.inbound_shipment_id,
                )
                .filter(
                    PickingHeader.picking_type == "goods_received",
                    PickingLine.product_id.in_(product_uuids),
                )
                .group_by(
                    PickingHeader.picking_number,
                    PickingHeader.picking_date,
                    PickingHeader.picking_status,
                )
            )
            direct_rows = direct_q.all()

        # Merge + dedupe by picking_number. Sort by picking_date desc, NULL last.
        seen: dict[str, Any] = {}
        for r in list(spo_rows) + list(direct_rows):
            key = r.picking_number
            if key in seen:
                continue
            seen[key] = r
        merged = sorted(
            seen.values(),
            key=lambda r: (r.picking_date is None, r.picking_date),
            reverse=False,
        )
        merged.sort(key=lambda r: r.picking_date or date.min, reverse=True)
        merged = merged[:limit]

        data = [
            {
                "grn_number": r.picking_number,
                "grn_date": r.picking_date,
                "grn_status": r.picking_status,
                "shipment_number": r.shipment_number,
            }
            for r in merged
        ]
        return {"data": data, "empty": not data}
