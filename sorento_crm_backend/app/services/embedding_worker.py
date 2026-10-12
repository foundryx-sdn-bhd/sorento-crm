"""RQ worker for embedding queue processing."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import logging
from typing import Any

import httpx
from sqlalchemy import String, cast, exists
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.base import company_scope, set_company_scope
from app.models.certificate import Certificate, CertificateProduct
from app.models.embeddings import EmbeddingQueue, EmbeddingDocument, EmbeddingChunk
from app.models.product import Product, ProductCategory, Brand
from app.models.marketing import Promotion
from app.models.marketing import PromotionProduct, PromotionAttachment
from app.models.resources import Attachment
from app.models.forms import Form
from app.models.list_query_metadata import ListQueryResource, ListQueryField
from app.models.procurement import InboundShipment, InboundShipmentLine, SPOAllocation, PickingHeader, PickingLine
from app.models.order import Order, OrderStatus, OrderLine, Customer, Transporter
from app.models.product import ProductAttachment
from app.models.conversation_frame import ConversationFrame
from app.services.embedding_service import EmbeddingReadService, parse_debtor_seed_id

logger = logging.getLogger(__name__)


# source_type -> owned model carrying company_id (multi-company isolation). Types
# absent here are synthetic / company-less (mcp_tool, form, schema_doc,
# order_status) and stay company-less - shared knowledge visible under every
# scope. A ``debtor:<company>:<code>`` customer seed carries its company in the id.
# ``conversation_frame`` has no company column: its company is the frame
# contact's, resolved in ``_company_id_for_source``.
_SOURCE_MODEL_FOR_COMPANY = {
    "product": Product,
    # product_spec is keyed on product_id, so the product row carries its company.
    "product_spec": Product,
    "promotion": Promotion,
    "promotion_product": PromotionProduct,
    "promotion_attachment": PromotionAttachment,
    "product_attachment": ProductAttachment,
    "attachment": Attachment,
    "inbound_shipment": InboundShipment,
    "inbound_shipment_line": InboundShipmentLine,
    "spo_allocation": SPOAllocation,
    "picking_header": PickingHeader,
    "picking_line": PickingLine,
    "order": Order,
    "order_line": OrderLine,
    "customer": Customer,
    "transporter": Transporter,
    "certificate": Certificate,
}

# source_type -> the row an embedding is built from, for every type that has one.
# A document whose row is gone is an orphan (``purge_orphan_embeddings``).
_SOURCE_MODEL = {**_SOURCE_MODEL_FOR_COMPANY, "conversation_frame": ConversationFrame}


def _frame_company_id(db: Session, frame_id: str):
    """The company of a frame's contact, when the contact belongs to exactly one.

    A frame is one contact's conversation. A contact in several companies (or in
    none, or unknown) has no single company to stamp, so the frame stays NULL.
    Defence in depth: a company-scoped RAG search returns no frame at all
    (``embedding_service._embedding_company_filter``). Same contact resolution
    as the request-entry scope resolver, so the two cannot disagree."""
    from app.services.contact_access_type_service import ContactAccessTypeService

    frame = db.query(ConversationFrame).filter(ConversationFrame.id == str(frame_id)).first()
    if frame is None:
        return None
    company_ids = ContactAccessTypeService(db).resolve_contact_company_ids(
        frame.contact_id, frame.space_id
    )
    return company_ids[0] if len(company_ids) == 1 else None


def _company_id_for_source(db: Session, source_type: str, source_id: str):
    """company_id of an owned source entity, or None for synthetic / company-less
    sources. The worker runs system-scoped (``set_company_scope(None)``) so this
    read is not company-filtered and can resolve any company's row."""
    if source_type == "conversation_frame":
        try:
            return _frame_company_id(db, source_id)
        except Exception:
            return None
    model = _SOURCE_MODEL_FOR_COMPANY.get(source_type)
    if model is None:
        return None
    seed = parse_debtor_seed_id(source_id)
    if seed is not None:
        return seed[0]
    try:
        row = db.query(model.company_id).filter(model.id == source_id).first()
    except Exception:
        return None
    return row[0] if row and row[0] is not None else None


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _canonical_for_source(db: Session, source_type: str, source_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if source_type == "mcp_tool":
        capability = (payload or {}).get("capability") or {}
        body_text = str(capability.get("body_text") or "").strip()
        if not body_text:
            raise ValueError("mcp_tool payload.capability.body_text is required")
        metadata = capability.get("metadata") or {}
        return {
            "source_key": capability.get("source_key") or source_id,
            "title": capability.get("title") or source_id,
            "body_text": body_text,
            "visibility_scope": "internal",
            "source_updated_at": datetime.utcnow(),
            "metadata": metadata,
        }

    if source_type == "product":
        row = (
            db.query(Product, ProductCategory, Brand)
            .outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
            .outerjoin(Brand, Brand.id == Product.brand_id)
            .filter(Product.id == source_id)
            .first()
        )
        if not row:
            raise ValueError(f"Product not found: {source_id}")
        product, category, brand = row
        body = "\n".join(
            [
                "Source Type: Product",
                f"Product Code: {product.product_code}",
                f"Product Name: {product.product_name}",
                f"Category: {category.category_name if category else ''}",
                f"Brand: {brand.brand_name if brand else ''}",
                f"Description: {product.description or ''}",
                f"Item Type: {product.item_type or ''}",
            ]
        ).strip()
        return {
            "source_key": product.product_code,
            "title": product.product_name,
            "body_text": body,
            "visibility_scope": "customer",
            "source_updated_at": product.updated_at or product.created_at,
            "metadata": {"category": category.category_name if category else None, "brand": brand.brand_name if brand else None},
        }

    if source_type == "product_spec":
        # A SECOND product source type, deliberately not a replacement for "product".
        # The existing product body embeds the product code twice, the category code,
        # and the raw description (which contains OTHER products' codes). Rewriting it
        # in place would change order-filter fuzzy matching and assistant RAG in the
        # same commit as a new feature. See documentation/adr/0013.
        #
        # The body here is the rendered spec sentence and nothing else: built from spec
        # VALUES, never from products.description, with code-shaped tokens stripped.
        # That is what keeps the code-only product resolution rule intact while still
        # allowing semantic search.
        from app.models.product_spec import ProductSpecifications

        row = (
            db.query(ProductSpecifications, Product)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(ProductSpecifications.product_id == source_id)
            .first()
        )
        if not row:
            raise ValueError(f"Product specifications not found: {source_id}")
        spec, product = row
        if not (spec.rendered_text or "").strip():
            # An empty sentence embeds to a vector near everything, so the product
            # would surface for every query. Enqueue is guarded too; this is defence
            # in depth.
            raise ValueError(f"Product {source_id} has no spec text to embed")
        return {
            "source_key": product.product_code,
            "title": product.product_code,
            "body_text": spec.rendered_text,
            "visibility_scope": "customer",
            "source_updated_at": spec.updated_at or spec.created_at,
            "metadata": {
                "product_code": product.product_code,
                "spec_keys": sorted((spec.values or {}).keys()),
            },
        }

    if source_type == "promotion":
        promotion = db.query(Promotion).filter(Promotion.id == source_id).first()
        if not promotion:
            raise ValueError(f"Promotion not found: {source_id}")
        title = (promotion.description or f"Promotion {str(promotion.id)[:8]}").strip().splitlines()[0]
        body = "\n".join(
            [
                "Source Type: Promotion",
                f"Description: {promotion.description or ''}",
                f"Active From: {promotion.start_date}",
                f"Active Until: {promotion.end_date}",
            ]
        ).strip()
        return {
            "source_key": str(promotion.id),
            "title": title,
            "body_text": body,
            "visibility_scope": "customer",
            "source_updated_at": promotion.updated_at or promotion.created_at,
            "metadata": {"access_levels": promotion.access_levels or []},
        }

    if source_type == "attachment":
        attachment = db.query(Attachment).filter(Attachment.id == source_id).first()
        if not attachment:
            raise ValueError(f"Attachment not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Attachment",
                f"File Name: {attachment.original_filename}",
                f"Description: {attachment.description or ''}",
                f"Path: {attachment.full_directory_path or ''}",
                f"Entity Type: {attachment.entity_type or ''}",
                f"Entity ID: {attachment.entity_id or ''}",
                # TCK-2026-000015: include intent keywords so RAG tool-search
                # routes catalogue / brochure / price-list phrasings here.
                "Intent Keywords: catalog catalogue brochure price list spec sheet datasheet manual flyer",
                f"Linked to: {(attachment.entity_type or 'general')}",
            ]
        ).strip()
        return {
            "source_key": attachment.original_filename,
            "title": attachment.original_filename,
            "body_text": body,
            "visibility_scope": "customer",
            "source_updated_at": attachment.created_at,
            "metadata": {"entity_type": attachment.entity_type, "access_levels": attachment.access_levels or []},
        }

    if source_type == "certificate":
        certificate = db.query(Certificate).filter(Certificate.id == source_id).first()
        if not certificate:
            raise ValueError(f"Certificate not found: {source_id}")
        # Only the certificate's own company's products: the embedding carries ONE
        # company_id, so another company's code in it would be readable under this
        # certificate's scope. A shared (NULL-company) certificate is visible to
        # every scope, so it may carry company-less products only.
        product_company = (
            Product.company_id.is_(None)
            if certificate.company_id is None
            else Product.company_id == certificate.company_id
        )
        product_codes = [
            code
            for (code,) in (
                db.query(Product.product_code)
                .join(
                    CertificateProduct,
                    CertificateProduct.product_id == Product.id,
                )
                .filter(CertificateProduct.certificate_id == str(certificate.id), product_company)
                .order_by(Product.product_code)
                .all()
            )
            if code
        ]
        # Dates are deliberately NOT embedded: validity is a SQL question
        # (derive_validity off the current revision), and an embedded expiry
        # would go stale the day after it is written.
        identity_text = "\n".join(
            [
                "Source Type: Certificate",
                f"Scheme: {certificate.scheme}",
                f"Certificate Number: {certificate.certificate_number}",
                f"Certifying Body: {certificate.certifying_body or ''}",
                f"Issuer: {certificate.issuer or ''}",
                f"Title: {certificate.title or ''}",
                "Intent Keywords: certificate certification approval compliance scheme certifying body",
            ]
        ).strip()
        body = f"{identity_text}\nCovered Products: {', '.join(product_codes)}".strip()
        return {
            "source_key": f"{certificate.scheme} {certificate.certificate_number}",
            "title": f"{certificate.scheme} {certificate.certificate_number}",
            "body_text": body,
            "visibility_scope": "customer",
            "source_updated_at": certificate.updated_at or certificate.created_at,
            "metadata": {
                "scheme": certificate.scheme,
                "certificate_number": certificate.certificate_number,
                "certifying_body": certificate.certifying_body,
                "status": certificate.status,
                "identity_text": identity_text,
                "covered_products": product_codes,
                "covered_product_count": len(product_codes),
            },
        }

    if source_type == "form":
        form = db.query(Form).filter(Form.id == source_id).first()
        if not form:
            raise ValueError(f"Form not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Form",
                f"Form Code: {form.code}",
                f"Form Name: {form.name}",
                f"Form Type: {form.form_type}",
                f"Purpose: {form.purpose or ''}",
                f"Language: {form.language or ''}",
            ]
        ).strip()
        return {
            "source_key": form.code,
            "title": form.name,
            "body_text": body,
            "visibility_scope": "internal",
            "source_updated_at": form.updated_at or form.created_at,
            "metadata": {
                "language": form.language,
                "is_active": form.is_active,
                "form_type": form.form_type,
            },
        }

    if source_type == "schema_doc":
        resource = db.query(ListQueryResource).filter(ListQueryResource.id == source_id).first()
        if not resource:
            raise ValueError(f"Schema doc not found: {source_id}")
        fields = (
            db.query(ListQueryField)
            .filter(ListQueryField.resource_id == resource.id)
            .order_by(ListQueryField.sort_order.asc())
            .all()
        )
        field_lines = [f"{f.field_key}: {f.label}" for f in fields]
        body = "\n".join(
            ["Source Type: Schema Doc", f"Resource: {resource.resource_key}", f"Name: {resource.display_name}", "Fields:", *field_lines]
        ).strip()
        return {
            "source_key": resource.resource_key,
            "title": resource.display_name,
            "body_text": body,
            "visibility_scope": "internal",
            "source_updated_at": resource.updated_at or resource.created_at,
            "metadata": {"resource_key": resource.resource_key, "field_count": len(field_lines)},
        }

    if source_type == "promotion_product":
        row = db.query(PromotionProduct, Promotion, Product).join(Promotion, Promotion.id == PromotionProduct.promotion_id).join(Product, Product.id == PromotionProduct.product_id).filter(PromotionProduct.id == source_id).first()
        if not row:
            raise ValueError(f"Promotion product not found: {source_id}")
        line, promo, product = row
        promo_label = (promo.description or f"Promotion {str(promo.id)[:8]}").strip().splitlines()[0]
        body = "\n".join(
            [
                "Source Type: Promotion Product",
                f"Promotion: {promo_label}",
                f"Product Code: {product.product_code}",
                f"Product Name: {product.product_name}",
                f"Promo Selling Price: {line.promo_selling_price}",
                f"Discount Percent: {line.discount_percent}",
            ]
        ).strip()
        return {"source_key": f"{promo.id}:{product.product_code}", "title": f"{promo_label} / {product.product_name}", "body_text": body, "visibility_scope": "customer", "source_updated_at": line.updated_at or line.created_at, "metadata": {"promotion_id": line.promotion_id, "product_id": line.product_id}}

    if source_type == "inbound_shipment":
        shipment = db.query(InboundShipment).filter(InboundShipment.id == source_id).first()
        if not shipment:
            raise ValueError(f"Packing list not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Inbound Shipment",
                f"Shipment Number: {shipment.shipment_number}",
                f"Shipment Status: {shipment.shipment_status}",
                f"Shipment Date: {shipment.shipment_date}",
                f"Estimated Arrival: {shipment.estimated_arrival_date}",
                f"Notes: {shipment.notes or ''}",
            ]
        ).strip()
        return {"source_key": shipment.shipment_number, "title": shipment.shipment_number, "body_text": body, "visibility_scope": "internal", "source_updated_at": shipment.updated_at or shipment.created_at, "metadata": {"supplier_id": shipment.supplier_id, "access_levels": shipment.access_levels or []}}

    if source_type == "inbound_shipment_line":
        line = db.query(InboundShipmentLine).filter(InboundShipmentLine.id == source_id).first()
        if not line:
            raise ValueError(f"Inbound shipment line not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Inbound Shipment Line",
                f"Shipment ID: {line.shipment_id}",
                f"Product ID: {line.product_id}",
                f"Quantity Shipped: {line.quantity_shipped}",
                f"Line Status: {line.line_status}",
                f"Batch Number: {line.batch_number or ''}",
            ]
        ).strip()
        return {"source_key": f"{line.shipment_id}:{line.product_id}", "title": "Inbound Shipment Line", "body_text": body, "visibility_scope": "internal", "source_updated_at": line.updated_at or line.created_at, "metadata": {"shipment_id": line.shipment_id, "product_id": line.product_id}}

    if source_type == "spo_allocation":
        row = db.query(SPOAllocation).filter(SPOAllocation.id == source_id).first()
        if not row:
            raise ValueError(f"SPO allocation not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: SPO Allocation",
                f"SPO Number: {row.spo_number or ''}",
                f"Inbound Shipment ID: {row.inbound_shipment_id}",
                f"Product ID: {row.product_id}",
                f"Allocated Quantity: {row.allocated_quantity}",
                f"Receipt Status: {row.receipt_status}",
            ]
        ).strip()
        return {"source_key": row.spo_number or str(row.id), "title": row.spo_number or "SPO Allocation", "body_text": body, "visibility_scope": "internal", "source_updated_at": row.updated_at or row.created_at, "metadata": {"warehouse_id": row.warehouse_id, "storage_zone_id": row.storage_zone_id}}

    if source_type == "picking_header":
        row = db.query(PickingHeader).filter(PickingHeader.id == source_id).first()
        if not row:
            raise ValueError(f"Picking header not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Picking Header",
                f"Picking Number: {row.picking_number}",
                f"SPO Number: {row.spo_number or ''}",
                f"Picking Status: {row.picking_status}",
                f"Inspection Status: {row.inspection_status}",
                f"Notes: {row.notes or ''}",
            ]
        ).strip()
        return {"source_key": row.picking_number, "title": row.picking_number, "body_text": body, "visibility_scope": "internal", "source_updated_at": row.updated_at or row.created_at, "metadata": {"picking_type": row.picking_type}}

    if source_type == "picking_line":
        row = db.query(PickingLine).filter(PickingLine.id == source_id).first()
        if not row:
            raise ValueError(f"Picking line not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Picking Line",
                f"Picking Header ID: {row.picking_header_id}",
                f"Product ID: {row.product_id}",
                f"Quantity Expected: {row.quantity_expected}",
                f"Quantity Picked: {row.quantity_picked}",
                f"Condition: {row.picked_condition}",
            ]
        ).strip()
        return {"source_key": f"{row.picking_header_id}:{row.product_id}", "title": "Picking Line", "body_text": body, "visibility_scope": "internal", "source_updated_at": row.updated_at or row.created_at, "metadata": {"spo_allocation_id": row.spo_allocation_id}}

    if source_type == "product_attachment":
        row = db.query(ProductAttachment).filter(ProductAttachment.id == source_id).first()
        if not row:
            raise ValueError(f"Product attachment not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Product Attachment",
                f"Product ID: {row.product_id}",
                f"Attachment ID: {row.attachment_id}",
                f"Is Primary: {row.is_primary}",
                f"Sort Order: {row.sort_order}",
                "Intent Keywords: catalog catalogue brochure price list spec sheet datasheet product manual",
                "Linked to: product",
            ]
        ).strip()
        return {"source_key": f"{row.product_id}:{row.attachment_id}", "title": "Product Attachment", "body_text": body, "visibility_scope": "customer", "source_updated_at": row.updated_at or row.created_at, "metadata": {"access_levels": row.access_levels or []}}

    if source_type == "promotion_attachment":
        row = db.query(PromotionAttachment).filter(PromotionAttachment.id == source_id).first()
        if not row:
            raise ValueError(f"Promotion attachment not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Promotion Attachment",
                f"Promotion ID: {row.promotion_id}",
                f"Attachment ID: {row.attachment_id}",
                f"Is Primary: {row.is_primary}",
                f"Sort Order: {row.sort_order}",
                "Intent Keywords: catalog catalogue brochure price list promotion flyer leaflet",
                "Linked to: promotion",
            ]
        ).strip()
        return {"source_key": f"{row.promotion_id}:{row.attachment_id}", "title": "Promotion Attachment", "body_text": body, "visibility_scope": "customer", "source_updated_at": row.updated_at or row.created_at, "metadata": {}}

    if source_type == "order":
        row = db.query(Order).filter(Order.id == source_id).first()
        if not row:
            raise ValueError(f"Order not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Order",
                f"Order Number: {row.order_number}",
                f"Debtor Name: {row.debtor_name or ''}",
                f"Salesman: {row.salesman or ''}",
                f"Order Type: {row.order_type or ''}",
                f"Transporter: {row.transporter or ''}",
                f"Remarks: {row.remarks or ''}",
            ]
        ).strip()
        return {"source_key": row.order_number, "title": row.order_number, "body_text": body, "visibility_scope": "internal", "source_updated_at": row.updated_at or row.created_at, "metadata": {"order_status_id": row.order_status_id, "is_cancelled": row.is_cancelled}}

    if source_type == "customer":
        # Customer source supports two shapes:
        # 1. UUID source_id → Customer master row.
        # 2. `debtor:<company>:<code>` synthetic source_id → an orders.debtor_code with no
        #    Customer match in that company, named from that company's orders only.
        seed = parse_debtor_seed_id(source_id)
        if seed is not None:
            company_id, code = seed
            if company_id is None:
                # A legacy company-less seed would read as shared knowledge.
                raise ValueError(f"Company-less debtor seed refused: {source_id}")
            row = (
                db.query(Order.debtor_code, Order.debtor_name)
                .filter(Order.company_id == company_id)
                .filter(Order.debtor_code == code)
                .filter(Order.deleted_at.is_(None))
                .order_by(Order.created_at.asc())
                .first()
            )
            if not row:
                raise ValueError(f"Debtor not found: {source_id}")
            body = "\n".join(
                [
                    "Source Type: Customer (debtor seed)",
                    f"Customer Code: {row.debtor_code}",
                    f"Customer Name: {row.debtor_name or ''}",
                ]
            ).strip()
            return {
                "source_key": f"debtor:{code}",
                "title": row.debtor_name or row.debtor_code,
                "body_text": body,
                "visibility_scope": "internal",
                "source_updated_at": datetime.utcnow(),
                "metadata": {"debtor_code": row.debtor_code},
            }
        cust = db.query(Customer).filter(Customer.id == source_id).first()
        if not cust:
            raise ValueError(f"Customer not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Customer",
                f"Customer Code: {cust.customer_code}",
                f"Customer Name: {cust.customer_name}",
                f"Country: {getattr(cust, 'country', '') or ''}",
                f"Email: {getattr(cust, 'contact_email', '') or ''}",
                f"Phone: {getattr(cust, 'phone', '') or ''}",
            ]
        ).strip()
        return {
            "source_key": cust.customer_code,
            "title": cust.customer_name,
            "body_text": body,
            "visibility_scope": "internal",
            "source_updated_at": getattr(cust, "updated_at", None) or getattr(cust, "created_at", None),
            "metadata": {"customer_code": cust.customer_code},
        }

    if source_type == "transporter":
        trans = db.query(Transporter).filter(Transporter.id == source_id).first()
        if not trans:
            raise ValueError(f"Transporter not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Transporter",
                f"Code: {trans.code}",
                f"Name: {trans.name}",
            ]
        ).strip()
        return {
            "source_key": trans.code,
            "title": trans.name,
            "body_text": body,
            "visibility_scope": "internal",
            "source_updated_at": getattr(trans, "updated_at", None) or trans.created_at,
            "metadata": {"transporter_code": trans.code},
        }

    if source_type == "order_status":
        row = db.query(OrderStatus).filter(OrderStatus.id == source_id).first()
        if not row:
            raise ValueError(f"Order status not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Order Status",
                f"Status Code: {row.status_code}",
                f"Status Name: {row.status_name}",
                f"Description: {row.description or ''}",
                f"Sequence: {row.sequence}",
            ]
        ).strip()
        return {"source_key": row.status_code, "title": row.status_name, "body_text": body, "visibility_scope": "internal", "source_updated_at": row.created_at, "metadata": {"is_final_status": row.is_final_status}}

    if source_type == "order_line":
        row = db.query(OrderLine).filter(OrderLine.id == source_id).first()
        if not row:
            raise ValueError(f"Order line not found: {source_id}")
        body = "\n".join(
            [
                "Source Type: Order Line",
                f"Order ID: {row.order_id}",
                f"Product ID: {row.product_id}",
                f"Warehouse ID: {row.warehouse_id}",
                f"Quantity: {row.quantity}",
                f"Unit Price: {row.unit_price}",
            ]
        ).strip()
        return {"source_key": f"{row.order_id}:{row.line_sequence}", "title": "Order Line", "body_text": body, "visibility_scope": "internal", "source_updated_at": row.updated_at or row.created_at, "metadata": {"line_sequence": row.line_sequence}}

    if source_type == "conversation_frame":
        row = db.query(ConversationFrame).filter(ConversationFrame.id == source_id).first()
        if not row:
            raise ValueError(f"Conversation frame not found: {source_id}")
        body = (row.summary or "").strip()
        if not body:
            raise ValueError(f"Conversation frame {source_id} has empty summary; nothing to embed")
        title = f"Conversation: {row.domain or 'unspecified'} / {row.intent or 'unspecified'}"
        access_levels_list = list(row.access_levels_used or [])
        return {
            "source_key": str(row.id),
            "title": title,
            "body_text": body,
            "visibility_scope": "contact",
            "source_updated_at": row.closed_at or row.last_activity_at,
            "metadata": {
                "contact_id": row.contact_id,
                "space_id": row.space_id,
                "channel": row.channel,
                "domain": row.domain,
                "intent": row.intent,
                "access_levels_used": access_levels_list,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            },
        }

    raise ValueError(f"Unsupported source type: {source_type}")


def _embed_text_chunks(chunks: list[str]) -> list[list[float]]:
    from app.services import llm_cassette

    if llm_cassette.mode() != "replay" and not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for embedding worker")
    if not chunks:
        return []
    # Test runs only (LLM-TEST-COST): recorded and replayed; a plain call when unset.
    return llm_cassette.through(
        kind="embed",
        key=llm_cassette.embed_key(settings.embedding_model_name, json.dumps(chunks, ensure_ascii=False)),
        model=settings.embedding_model_name,
        secret=settings.openai_api_key or "",
        live=lambda: _post_embeddings(chunks),
        dump=lambda vectors: vectors,
        load=lambda vectors: [list(v) for v in vectors],
    )


def _post_embeddings(chunks: list[str]) -> list[list[float]]:
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {"model": settings.embedding_model_name, "input": chunks}
    with httpx.Client(timeout=30) as client:
        response = client.post(settings.openai_embeddings_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    vectors = [item["embedding"] for item in data.get("data", [])]
    if len(vectors) != len(chunks):
        raise ValueError("Embedding provider returned mismatched vector count")
    return vectors


def _chunks_for_source(source_type: str, source: dict[str, Any]) -> list[str]:
    if source_type == "mcp_tool":
        md = source.get("metadata") or {}
        title = str(source.get("title") or "").strip()
        body_text = str(source.get("body_text") or "").strip()
        questions = [str(q).strip() for q in (md.get("typical_user_questions") or []) if str(q).strip()]
        aliases = [str(a).strip() for a in (md.get("aliases") or []) if str(a).strip()]
        required_fields = [str(f).strip() for f in (md.get("required_fields") or []) if str(f).strip()]
        optional_fields = [str(f).strip() for f in (md.get("optional_fields") or []) if str(f).strip()]

        chunks: list[str] = []
        if body_text:
            chunks.append(body_text)
        if aliases:
            chunks.append(f"Tool aliases for {title}: " + ", ".join(aliases))
        chunks.extend(questions)
        if required_fields:
            chunks.append(f"Required fields for {title}: " + ", ".join(required_fields))
        if optional_fields:
            chunks.append(f"Optional fields for {title}: " + ", ".join(optional_fields))
        if chunks:
            return chunks

    if source_type == "certificate":
        md = source.get("metadata") or {}
        identity = str(md.get("identity_text") or source.get("title") or "").strip()
        codes = [str(c).strip() for c in (md.get("covered_products") or []) if str(c).strip()]
        if identity:
            # Identity chunk first, then the covered codes in batches that each
            # REPEAT the identity. A certificate covering 68 products would
            # otherwise chunk into fragments reading only "SRTWC8088, SRTWC8090",
            # which no query can match back to a certificate.
            chunks = [identity]
            batch = 25
            for start in range(0, len(codes), batch):
                chunks.append(
                    f"{identity}\nCovered Products: " + ", ".join(codes[start : start + batch])
                )
            return chunks

    return _chunk_text(source["body_text"], settings.embedding_chunk_size, settings.embedding_chunk_overlap)


def _spo_allocation_hidden(db: Session, source_id: str) -> bool:
    """R9 (PLAN-hide-retired-everywhere): True when this `spo_allocations` row
    still exists but is HIDDEN by `spo_supply.visible_line_clauses()` (retired,
    nothing received).

    Retiring a line is an UPDATE (the leftover sweep sets `retired_at`, never a
    DELETE), so `embedding_change_listener`'s `after_update` enqueues it the same
    as any other edit, and without this check `process_embedding_queue_item`
    re-embeds the row's now-stale text and sets its document `is_active = True` -
    the retirement marker never reaches the vector store, so a query can still
    quote a line the document no longer names. A row that has been genuinely
    deleted (no listener path exists for that today - out of this fix's scope)
    reads as `False` here, not hidden, and is left to the pipeline's existing
    `ValueError`/dead-letter behaviour.
    """
    from app.services.scm import spo_supply

    exists = db.query(SPOAllocation.id).filter(SPOAllocation.id == source_id).first()
    if exists is None:
        return False
    visible = (
        db.query(SPOAllocation.id)
        .filter(SPOAllocation.id == source_id, *spo_supply.visible_line_clauses())
        .first()
    )
    return visible is None


def _source_row_exists(db: Session, source_type: str, source_id: str) -> bool:
    """False only when ``source_type`` has a row model and that row is gone. A type
    with no row (mcp_tool, schema_doc, ...) and a ``debtor:`` seed read as present,
    so nothing synthetic is ever removed as "deleted"."""
    model = _SOURCE_MODEL.get(source_type)
    if model is None or str(source_id).startswith("debtor:"):
        return True
    try:
        # SAVEPOINT: a malformed id fails the statement (invalid uuid) and would
        # otherwise abort the worker's transaction. Read as present: not removed.
        with db.begin_nested():
            return db.query(model.id).filter(model.id == str(source_id)).first() is not None
    except DBAPIError:
        return True


def _delete_documents(db: Session, doc_ids_query) -> int:
    """Delete the documents ``doc_ids_query`` selects, chunks first."""
    ids = [str(r[0]) for r in doc_ids_query.all()]
    if not ids:
        return 0
    db.query(EmbeddingChunk).filter(EmbeddingChunk.document_id.in_(ids)).delete(synchronize_session=False)
    db.query(EmbeddingDocument).filter(EmbeddingDocument.id.in_(ids)).delete(synchronize_session=False)
    return len(ids)


def purge_orphan_embeddings(db: Session, *, apply: bool = False) -> dict[str, Any]:
    """Embedding documents whose source row no longer exists, per source_type.

    Covers rows deleted before the worker handled `.deleted` events, and deletes
    that bypass the ORM listeners (bulk ``query.delete()``, frame trims, FK
    cascades). Dry run by default: counts and ids only. ``apply=True`` deletes
    them (chunks first); the caller commits. Runs unscoped on purpose: under a
    company scope another company's live rows would read as missing.
    """
    counts: dict[str, int] = {}
    source_ids: list[str] = []
    with company_scope(db, None):
        for source_type, model in _SOURCE_MODEL.items():
            orphans = db.query(EmbeddingDocument.id, EmbeddingDocument.source_id).filter(
                EmbeddingDocument.source_type == source_type,
                ~EmbeddingDocument.source_id.startswith("debtor:"),
                ~exists().where(cast(model.id, String) == EmbeddingDocument.source_id),
            )
            rows = orphans.all()
            if not rows:
                continue
            counts[source_type] = len(rows)
            source_ids.extend(r.source_id for r in rows)
            if apply:
                _delete_documents(db, orphans.with_entities(EmbeddingDocument.id))
    return {"applied": apply, "counts": counts, "source_ids": source_ids}


def backfill_frame_embedding_companies(db: Session) -> int:
    """Stamp company-less ``conversation_frame`` embeddings with the frame contact's
    company (``_frame_company_id``); returns how many documents were stamped.

    Frames whose contact is in several companies, in none, or gone stay NULL.
    The caller commits."""
    stamped = 0
    with company_scope(db, None):
        docs = (
            db.query(EmbeddingDocument)
            .filter(
                EmbeddingDocument.source_type == "conversation_frame",
                EmbeddingDocument.company_id.is_(None),
            )
            .all()
        )
        for doc in docs:
            company_id = _frame_company_id(db, doc.source_id)
            if company_id is None:
                continue
            doc.company_id = company_id
            db.query(EmbeddingChunk).filter(EmbeddingChunk.document_id == doc.id).update(
                {EmbeddingChunk.company_id: company_id}, synchronize_session=False
            )
            stamped += 1
        db.flush()
    return stamped


def process_embedding_queue_item(queue_id: str) -> dict[str, Any]:
    db = SessionLocal()
    # The embedding pipeline is a system process that materializes embeddings for
    # EVERY company's owned entities. A fresh SessionLocal() starts at UNSET
    # (fail-closed) which would make owned source reads (Product, Order, ...)
    # return 0 rows ("... not found"). Run None-scoped (all companies) so any
    # source resolves; per-row isolation is enforced at SEARCH time via company_id.
    set_company_scope(db, None)
    try:
        queue_item = db.query(EmbeddingQueue).filter(EmbeddingQueue.id == queue_id).first()
        if not queue_item:
            return {"status": "missing", "queue_id": queue_id}
        if queue_item.status in {"completed", "skipped"}:
            return {"status": queue_item.status, "queue_id": queue_id}

        queue_item.status = "processing"
        db.commit()

        # A hard-deleted source takes its embedding with it. Without this the
        # `.deleted` event failed in `_canonical_for_source` ("not found") and
        # dead-lettered, leaving the row retrievable - under every scope when its
        # company was NULL. Checked against the table, not the event name alone,
        # so a stale delete event for a row that still exists removes nothing.
        if (queue_item.event_type or "").endswith(".deleted") and not _source_row_exists(
            db, queue_item.source_type, queue_item.source_id
        ):
            # product_spec is keyed on the product's id, so it goes with the product.
            source_types = [queue_item.source_type]
            if queue_item.source_type == "product":
                source_types.append("product_spec")
            removed = _delete_documents(
                db,
                db.query(EmbeddingDocument.id).filter(
                    EmbeddingDocument.source_type.in_(source_types),
                    EmbeddingDocument.source_id == queue_item.source_id,
                ),
            )
            queue_item.status = "completed"
            queue_item.processed_at = datetime.utcnow()
            db.commit()
            return {"status": "removed", "queue_id": queue_id, "documents": removed}

        # R9: a retired-and-hidden SPO line deactivates its document instead of
        # being re-embedded - checked before `_canonical_for_source` builds any
        # text off it, since the whole point is that this text must not persist
        # as retrievable.
        if queue_item.source_type == "spo_allocation" and _spo_allocation_hidden(
            db, queue_item.source_id
        ):
            doc = (
                db.query(EmbeddingDocument)
                .filter(
                    EmbeddingDocument.source_type == queue_item.source_type,
                    EmbeddingDocument.source_id == queue_item.source_id,
                )
                .first()
            )
            if doc is not None:
                doc.is_active = False
            queue_item.status = "completed"
            queue_item.processed_at = datetime.utcnow()
            db.commit()
            return {"status": "deactivated", "queue_id": queue_id}

        payload = queue_item.payload or {}
        source_payload = payload.get("payload") if isinstance(payload, dict) else None
        source = _canonical_for_source(db, queue_item.source_type, queue_item.source_id, payload=source_payload)
        # Company of the source entity (multi-company isolation). Prefer the
        # listener-copied value on the queue payload (guarantees a fresh embedding
        # is never company-less when its source has a company - AC-I5); fall back to
        # re-deriving from the loaded source row (bulk / backfilled enqueues).
        company_id = payload.get("company_id") if isinstance(payload, dict) else None
        if company_id is None:
            company_id = _company_id_for_source(db, queue_item.source_type, queue_item.source_id)
        read_svc = EmbeddingReadService(db)
        source_hash = read_svc.deterministic_hash(
            {
                "source_type": queue_item.source_type,
                "source_id": queue_item.source_id,
                "title": source.get("title"),
                "body_text": source.get("body_text"),
                "metadata": source.get("metadata", {}),
            }
        )
        queue_item.source_hash = source_hash

        distinct_current = read_svc.current_chunk_hashes_distinct(
            queue_item.source_type,
            queue_item.source_id,
            settings.embedding_model_name,
            settings.embedding_model_version,
        )
        # Skip only when a single current generation exists and it already matches this payload.
        # If multiple distinct source_hash values are current (duplicate batches), re-embed so
        # mark_previous_non_current + insert can consolidate.
        if len(distinct_current) == 1 and source_hash in distinct_current:
            # R9/AC-E15 (round 2 + round 3, security review): keyed on "the document
            # is inactive and this row is not hidden" - never on anything specific to
            # retirement - because TWO paths reach this branch with a document still
            # `is_active = False`. (1) An un-retire restates IDENTICAL values, so
            # `source_hash` is unchanged. (2) A retired row whose `quantity_received`
            # goes 0 -> >0 (R2) is VISIBLE again by `visible_line_clauses()` the
            # instant that happens - `_spo_allocation_hidden` above already returned
            # False for it - but the canonical body carries no receipt figure, so the
            # hash is unchanged too. Both land here because nothing else in the
            # pipeline ever sets `is_active` back to True. A body-text change (adding
            # `retired_at`) fixes neither: a HIDDEN row never reaches body building at
            # all (the branch above returns first), and a now-visible row's body is
            # byte-identical to its original, so the hash matches either way.
            # Re-activating here, before the skip, is the only place that can repair
            # it - for any source_type, not only `spo_allocation`.
            doc = (
                db.query(EmbeddingDocument)
                .filter(
                    EmbeddingDocument.source_type == queue_item.source_type,
                    EmbeddingDocument.source_id == queue_item.source_id,
                )
                .first()
            )
            if doc is not None and not doc.is_active:
                doc.is_active = True
            queue_item.status = "skipped"
            queue_item.processed_at = datetime.utcnow()
            db.commit()
            return {"status": "skipped", "queue_id": queue_id}

        doc = (
            db.query(EmbeddingDocument)
            .filter(
                EmbeddingDocument.source_type == queue_item.source_type,
                EmbeddingDocument.source_id == queue_item.source_id,
            )
            .order_by(EmbeddingDocument.created_at.desc())
            .first()
        )
        if doc is not None:
            doc.source_key = source.get("source_key")
            doc.title = source.get("title")
            doc.body_text = source["body_text"]
            doc.metadata_json = source.get("metadata", {})
            doc.visibility_scope = source.get("visibility_scope")
            doc.source_hash = source_hash
            doc.source_updated_at = source.get("source_updated_at")
            doc.company_id = company_id
            doc.is_active = True
        else:
            doc = EmbeddingDocument(
                source_type=queue_item.source_type,
                source_id=queue_item.source_id,
                source_key=source.get("source_key"),
                title=source.get("title"),
                body_text=source["body_text"],
                metadata_json=source.get("metadata", {}),
                visibility_scope=source.get("visibility_scope"),
                source_hash=source_hash,
                source_updated_at=source.get("source_updated_at"),
                company_id=company_id,
                is_active=True,
            )
            db.add(doc)
        db.flush()

        chunks = _chunks_for_source(queue_item.source_type, source)
        vectors = _embed_text_chunks(chunks)

        read_svc.mark_previous_non_current(
            queue_item.source_type,
            queue_item.source_id,
            settings.embedding_model_name,
            settings.embedding_model_version,
        )
        db.flush()

        for idx, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
            db.add(
                EmbeddingChunk(
                    document_id=doc.id,
                    source_type=queue_item.source_type,
                    source_id=queue_item.source_id,
                    chunk_index=idx,
                    chunk_text=chunk_text,
                    chunk_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                    embedding=vector,
                    model_name=settings.embedding_model_name,
                    model_version=settings.embedding_model_version,
                    embedding_provider=settings.embedding_provider,
                    source_hash=source_hash,
                    metadata_json=source.get("metadata", {}),
                    company_id=company_id,
                    is_current=True,
                )
            )

        queue_item.status = "completed"
        queue_item.processed_at = datetime.utcnow()
        db.commit()
        return {"status": "completed", "queue_id": queue_id, "chunks": len(chunks)}
    except Exception as exc:
        db.rollback()
        queue_item = db.query(EmbeddingQueue).filter(EmbeddingQueue.id == queue_id).first()
        if queue_item:
            queue_item.retry_count = int(queue_item.retry_count or 0) + 1
            queue_item.last_error = str(exc)
            if queue_item.retry_count >= settings.embedding_max_retries:
                queue_item.status = "dead_letter"
            else:
                queue_item.status = "pending"
                queue_item.available_at = datetime.utcnow() + timedelta(
                    seconds=settings.embedding_retry_backoff_seconds * queue_item.retry_count
                )
            db.commit()
        logger.exception("Embedding worker failed for queue item %s", queue_id)
        return {"status": "failed", "queue_id": queue_id, "error": str(exc)}
    finally:
        db.close()
