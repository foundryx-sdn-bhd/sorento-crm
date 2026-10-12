"""Backfill helpers to emit embedding rebuild events from existing records."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product import ProductAttachment
from app.models.marketing import Promotion, PromotionProduct, PromotionAttachment
from app.models.resources import Attachment
from app.models.forms import Form
from app.models.list_query_metadata import ListQueryResource
from app.models.procurement import InboundShipment, InboundShipmentLine, SPOAllocation, PickingHeader, PickingLine
from app.models.order import Order, OrderStatus, OrderLine, Customer, Transporter
from app.services.embedding_service import DEBTOR_SEED_ID_MAX, EmbeddingEventService, debtor_seed_id
from app.services.mcp_tool_capability_service import build_capability_documents

BackfillSource = Literal[
    "product",
    "promotion",
    "promotion_product",
    "inbound_shipment",
    "inbound_shipment_line",
    "spo_allocation",
    "picking_header",
    "picking_line",
    "product_attachment",
    "promotion_attachment",
    "attachment",
    "form",
    "schema_doc",
    "order",
    "order_status",
    "order_line",
    "customer",
    "transporter",
    "mcp_tool",
    "all",
]


@dataclass
class BackfillStats:
    source: str
    scanned: int = 0
    queued: int = 0
    failed: int = 0


class EmbeddingBackfillService:
    def __init__(self, db: Session):
        self.db = db
        self.event_service = EmbeddingEventService(db)

    def run(
        self,
        *,
        source: BackfillSource,
        batch_size: int = 500,
        max_rows: int | None = None,
        dry_run: bool = False,
        triggered_by: str | None = None,
    ) -> dict:
        sources = [
            "product",
            "promotion",
            "promotion_product",
            "inbound_shipment",
            "inbound_shipment_line",
            "spo_allocation",
            "picking_header",
            "picking_line",
            "product_attachment",
            "promotion_attachment",
            "attachment",
            "form",
            "schema_doc",
            "order",
            "order_status",
            "order_line",
            "customer",
            "transporter",
            "mcp_tool",
        ] if source == "all" else [source]
        results: list[BackfillStats] = []
        for one in sources:
            stats = self._backfill_one(
                source=one,
                batch_size=batch_size,
                max_rows=max_rows,
                dry_run=dry_run,
                triggered_by=triggered_by,
            )
            results.append(stats)
        return {
            "dry_run": dry_run,
            "batch_size": batch_size,
            "max_rows": max_rows,
            "sources": [
                {"source": r.source, "scanned": r.scanned, "queued": r.queued, "failed": r.failed}
                for r in results
            ],
        }

    def _backfill_one(
        self,
        *,
        source: str,
        batch_size: int,
        max_rows: int | None,
        dry_run: bool,
        triggered_by: str | None,
    ) -> BackfillStats:
        stats = BackfillStats(source=source)
        offset = 0
        remaining = max_rows
        while True:
            limit = min(batch_size, remaining) if remaining is not None else batch_size
            rows = self._fetch_rows(source=source, limit=limit, offset=offset)
            if not rows:
                break
            for row in rows:
                stats.scanned += 1
                if dry_run:
                    continue
                try:
                    self.event_service.queue_event(
                        source_type=source,
                        source_id=str(row["id"]),
                        source_key=row.get("source_key"),
                        source_updated_at=row.get("source_updated_at"),
                        event_type="embedding.rebuild_requested",
                        changed_fields=["manual_backfill"],
                        payload=row.get("payload"),
                        triggered_by=triggered_by or "system",
                    )
                    stats.queued += 1
                except Exception:
                    stats.failed += 1
            if remaining is not None:
                remaining -= len(rows)
                if remaining <= 0:
                    break
            offset += len(rows)
        return stats

    def _fetch_rows(self, *, source: str, limit: int, offset: int) -> list[dict]:
        if source == "product":
            rows = (
                self.db.query(Product.id, Product.product_code, Product.updated_at, Product.created_at)
                .filter(Product.is_active.is_(True))
                .order_by(Product.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": r.product_code, "source_updated_at": r.updated_at or r.created_at}
                for r in rows
            ]
        if source == "promotion":
            rows = (
                self.db.query(Promotion.id, Promotion.updated_at, Promotion.created_at)
                .filter(Promotion.is_active.is_(True))
                .order_by(Promotion.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at}
                for r in rows
            ]
        if source == "attachment":
            rows = (
                self.db.query(Attachment.id, Attachment.original_filename, Attachment.created_at)
                .filter(Attachment.is_deleted.is_(False))
                .order_by(Attachment.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": r.original_filename, "source_updated_at": r.created_at}
                for r in rows
            ]
        if source == "form":
            rows = (
                self.db.query(Form.id, Form.code, Form.updated_at, Form.created_at)
                .filter(Form.is_active.is_(True))
                .order_by(Form.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": r.code, "source_updated_at": r.updated_at or r.created_at}
                for r in rows
            ]
        if source == "schema_doc":
            rows = (
                self.db.query(ListQueryResource.id, ListQueryResource.resource_key, ListQueryResource.updated_at, ListQueryResource.created_at)
                .filter(ListQueryResource.is_active.is_(True))
                .order_by(ListQueryResource.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": r.resource_key, "source_updated_at": r.updated_at or r.created_at}
                for r in rows
            ]
        if source == "promotion_product":
            rows = self.db.query(PromotionProduct.id, PromotionProduct.created_at, PromotionProduct.updated_at).order_by(PromotionProduct.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "inbound_shipment":
            rows = self.db.query(InboundShipment.id, InboundShipment.shipment_number, InboundShipment.created_at, InboundShipment.updated_at).order_by(InboundShipment.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": r.shipment_number, "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "inbound_shipment_line":
            rows = self.db.query(InboundShipmentLine.id, InboundShipmentLine.created_at, InboundShipmentLine.updated_at).order_by(InboundShipmentLine.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "spo_allocation":
            # R9: a hidden line is never queued for a fresh embed - a backfill
            # run must not undo the deactivation `process_embedding_queue_item`
            # gives a retired row by re-activating its document.
            from app.services.scm import spo_supply

            rows = (
                self.db.query(SPOAllocation.id, SPOAllocation.spo_number, SPOAllocation.created_at, SPOAllocation.updated_at)
                .filter(*spo_supply.visible_line_clauses())
                .order_by(SPOAllocation.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [{"id": r.id, "source_key": r.spo_number or str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "picking_header":
            rows = self.db.query(PickingHeader.id, PickingHeader.picking_number, PickingHeader.created_at, PickingHeader.updated_at).order_by(PickingHeader.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": r.picking_number, "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "picking_line":
            rows = self.db.query(PickingLine.id, PickingLine.created_at, PickingLine.updated_at).order_by(PickingLine.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "product_attachment":
            rows = self.db.query(ProductAttachment.id, ProductAttachment.created_at, ProductAttachment.updated_at).order_by(ProductAttachment.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "promotion_attachment":
            rows = self.db.query(PromotionAttachment.id, PromotionAttachment.created_at, PromotionAttachment.updated_at).order_by(PromotionAttachment.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "order":
            rows = self.db.query(Order.id, Order.order_number, Order.created_at, Order.updated_at).order_by(Order.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": r.order_number, "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "order_status":
            rows = self.db.query(OrderStatus.id, OrderStatus.status_code, OrderStatus.created_at).order_by(OrderStatus.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": r.status_code, "source_updated_at": r.created_at} for r in rows]
        if source == "order_line":
            rows = self.db.query(OrderLine.id, OrderLine.created_at, OrderLine.updated_at).order_by(OrderLine.created_at.asc()).offset(offset).limit(limit).all()
            return [{"id": r.id, "source_key": str(r.id), "source_updated_at": r.updated_at or r.created_at} for r in rows]
        if source == "mcp_tool":
            docs = build_capability_documents(include_planned=True)
            sliced = docs[offset: offset + limit]
            return [
                {"id": d.source_id, "source_key": d.source_key, "source_updated_at": None, "payload": {"capability": {"title": d.title, "source_key": d.source_key, "body_text": d.body_text, "metadata": d.metadata}}}
                for d in sliced
            ]
        if source == "customer":
            # Master customers.
            cust_rows = (
                self.db.query(Customer.id, Customer.customer_code, Customer.created_at, Customer.updated_at)
                .order_by(Customer.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            out = [
                {
                    "id": r.id,
                    "source_key": r.customer_code,
                    "source_updated_at": r.updated_at or r.created_at,
                }
                for r in cust_rows
            ]
            # When the customer master is exhausted, also seed embeddings for each
            # company's distinct orders.debtor_code values that have no matching
            # Customer row in THAT company: one seed per (company, code), stamped with
            # the company so it resolves inside it only. The seeds page on from where
            # the master ends (offset past the customer count), so a later page never
            # repeats them and the run reaches an empty page.
            if len(cust_rows) < limit:
                from sqlalchemy import func as _sa_func

                seen = {
                    (str(c[0]), c[1])
                    for c in self.db.query(Customer.company_id, Customer.customer_code).all()
                    if c[0] and c[1]
                }
                debtor_rows = (
                    self.db.query(Order.company_id, Order.debtor_code)
                    .filter(Order.debtor_code.isnot(None))
                    .filter(Order.company_id.isnot(None))
                    .filter(Order.deleted_at.is_(None))
                    .group_by(Order.company_id, Order.debtor_code)
                    .order_by(_sa_func.min(Order.created_at).asc(), Order.company_id, Order.debtor_code)
                    .all()
                )
                seeds: list[dict] = []
                for company_id, raw_code in debtor_rows:
                    code = (raw_code or "").strip()
                    if not code or (str(company_id), code) in seen:
                        continue
                    seen.add((str(company_id), code))  # " X" and "X" are one seed
                    seed_id = debtor_seed_id(str(company_id), code)
                    if len(seed_id) > DEBTOR_SEED_ID_MAX:
                        continue  # would not fit embedding_*.source_id
                    seeds.append({"id": seed_id, "source_key": f"debtor:{code}", "source_updated_at": None})
                seed_offset = max(0, offset - self.db.query(Customer.id).count())
                out.extend(seeds[seed_offset : seed_offset + limit - len(out)])
            return out
        if source == "transporter":
            rows = (
                self.db.query(Transporter.id, Transporter.code, Transporter.created_at)
                .order_by(Transporter.created_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [
                {"id": r.id, "source_key": r.code, "source_updated_at": r.created_at}
                for r in rows
            ]
        raise ValueError(f"Unsupported source: {source}")
