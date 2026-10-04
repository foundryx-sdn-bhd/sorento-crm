"""External API for packing lists (inbound shipments)."""
import html
import json
import logging
from typing import Optional, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_external_api_user
from app.schemas.external.procurement import PackingListRequest, PackingListCreateResponse
from app.schemas.procurement import InboundShipmentCreate, InboundShipmentLineCreate, InboundShipmentResponse
from app.services.procurement_service import (
    DuplicatePackingListError,
    InboundShipmentService,
)
from app.models.integration import IntegrationLog
from app.models.procurement import Supplier, InboundShipment
from app.models.resources import Attachment
from app.api.v1.external.utils import (
    parse_date_value,
    scope_to_attachment_company,
)
from app.services.product_code_resolution import resolve_codes_to_products
from app.services.attachment_notification_helper import (
    build_packing_list_detail_url,
    notify_after_external_attachment_entity,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def stamp_duplicate_integration_log(
    db: Session, attachment_id: str, error_code: str, error_message: str
) -> None:
    """Write the rejection reason onto the attachment's latest integration_log.

    Every user-facing surface (upload-activity drawer, attachment integration
    panel) reads the ``error_code`` / ``error_message`` COLUMNS. n8n's error
    branch only posts ``{status, response_payload}``, leaving both NULL - so the
    drawer would show a bare "Integration failed" while the real reason sat
    unread inside response_payload. Stamping the columns here puts the
    explanation where the UI already looks.

    Safe against n8n's follow-up callback: IntegrationLogService updates with
    ``model_dump(exclude_unset=True)``, so the status POST cannot clobber these.
    Deliberately does NOT set ``status`` - that stays n8n's to report.
    """
    log = (
        db.query(IntegrationLog)
        .filter(
            IntegrationLog.business_table == "attachments",
            IntegrationLog.business_id == str(attachment_id),
        )
        .order_by(IntegrationLog.created_at.desc())
        .first()
    )
    if log is None:
        # Direct API call with no n8n leg - nothing to stamp.
        return
    log.error_code = error_code
    log.error_message = error_message
    db.commit()


def log_lines_skipped(
    db: Session,
    shipment_id: str,
    attachment_id: str,
    reason: str,
    *,
    created_by: Optional[str] = None,
) -> None:
    """Record that this update left the shipment's lines alone (AC-D4b).

    A SUCCESS row, not an error: the header was updated, and the lines were deliberately
    not - the shipment's lines are its proforma invoices' carton split, which this
    forwarder's own upload has no grain for. On file so the question "why did my re-upload
    not change the lines" has an answer that outlives the response.
    """
    db.add(
        IntegrationLog(
            integration_channel="external_api",
            business_table="inbound_shipments",
            business_id=str(shipment_id),
            external_reference=str(attachment_id) if attachment_id else None,
            direction="inbound",
            endpoint="/api/v1/external/packing-lists/",
            http_method="POST",
            status_code=201,
            status="success",
            # NOT `error_code`/`error_message`: every drawer in the app reads those two as
            # "this integration failed", and this one did not - the header landed exactly
            # as asked.
            response_payload=json.dumps(
                {
                    "lines_skipped_reason": reason,
                    "message": (
                        "Header updated; the lines come from this shipment's proforma "
                        "invoices and were left untouched."
                    ),
                }
            ),
            created_by=created_by,
        )
    )
    db.commit()


@router.post("/", response_model=PackingListCreateResponse, status_code=status.HTTP_201_CREATED)
def create_packing_list(
    payload: PackingListRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    # Validate supplier only when provided (supplier_id is optional for packing lists)
    if payload.packing_list.supplier_id is not None:
        supplier = db.query(Supplier).filter(Supplier.id == payload.packing_list.supplier_id).first()
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid supplier_id",
            )

    # Validate attachment
    attachment = db.query(Attachment).filter(Attachment.id == payload.packing_list.attachment_id).first()
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid attachment_id",
        )

    # Multi-company isolation (Group G): match products + create the shipment /
    # lines only within the attachment's company. NULL company (shared) leaves
    # the scope as-is. No product matches in-company -> the "invalid product code"
    # fall-through below fails rather than binding another company's product.
    scope_to_attachment_company(db, attachment)

    # Same resolver as product-attachment and promotion linking (D11 - one
    # helper, one behaviour): exact code, then a SET code fans out to its
    # members, then `+`-split, then substring. A set code that names no single
    # product ("WC7605" on the packing slip) therefore lands lines for every
    # member instead of the whole line being skipped.
    product_codes = [item.product_code for item in payload.packing_list_products]
    resolution = resolve_codes_to_products(db, product_codes)
    skipped_product_codes = list(resolution.unmatched)
    if skipped_product_codes:
        logger.warning(
            "Packing list external API: skipping missing product codes (request still processed): %s",
            skipped_product_codes,
        )

    # Group by product_id and sum quantity (one row per product per shipment).
    # A set code's line quantity is NOT split across its members or scaled by
    # each member's per-set quantity - every member the set names receives the
    # SAME quantity the packing-list line stated for the set code. The line
    # states physical cartons/units received for the code as printed; there is
    # no reliable signal on the slip for "how many complete sets" versus "how
    # many of this one part", so scaling by `ProductSetMember.quantity` would
    # be inventing a number no one on the slip actually wrote.
    by_product: dict[str, int] = {}
    for item in payload.packing_list_products:
        code = (item.product_code or "").strip()
        if not code:
            continue
        for matched_code in resolution.codes_for(code):
            product = resolution.products_by_code.get(matched_code)
            if product is None:
                continue
            product_id = cast(str, product.id)
            by_product[product_id] = by_product.get(product_id, 0) + item.quantity

    try:
        shipment_date = parse_date_value(payload.packing_list.shipment_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not shipment_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid shipment_date")

    try:
        estimated_arrival_date = parse_date_value(payload.packing_list.estimated_arrival_date)
        actual_arrival_date = parse_date_value(payload.packing_list.actual_arrival_date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    shipment = InboundShipmentCreate(
        shipment_number=payload.packing_list.shipment_number,
        supplier_id=payload.packing_list.supplier_id or None,
        shipment_date=shipment_date,
        estimated_arrival_date=estimated_arrival_date,
        actual_arrival_date=actual_arrival_date,
        bill_of_lading_number=payload.packing_list.bill_of_lading_number,
        shipping_container_number=payload.packing_list.shipping_container_number,
        invoice_number=payload.packing_list.invoice_number,
        shipment_status=payload.packing_list.shipment_status or "in_transit",
        total_items_shipped=payload.packing_list.total_items_shipped,
        total_cartons=payload.packing_list.total_cartons,
        notes=payload.packing_list.notes,
        attachment_id=payload.packing_list.attachment_id,
        # Payload, else the attachment's own, else None (the column default: West only).
        regions=payload.packing_list.regions or getattr(attachment, "regions", None),
        shipment_lines=[
            InboundShipmentLineCreate(
                product_id=pid,
                quantity_shipped=qty,
            )
            for pid, qty in by_product.items()
        ],
    )

    # The integration's principal is a real users row, so attribution is recorded.
    created_by = current_user["id"]
    service = InboundShipmentService(db)
    try:
        created = service.create_shipment(shipment, created_by=created_by)
    except DuplicatePackingListError as exc:
        # Stamp the log BEFORE raising and commit it, so the 409 propagating
        # through the global AppException handler can't roll the stamp back.
        # Best-effort: a stamping failure must never mask the real rejection nor
        # turn it into a 500.
        try:
            stamp_duplicate_integration_log(
                db, payload.packing_list.attachment_id, exc.error_code, exc.message
            )
        except Exception as stamp_error:  # noqa: BLE001
            logger.warning(
                "Failed to stamp duplicate packing-list reason on integration_log "
                "for attachment %s: %s",
                payload.packing_list.attachment_id,
                stamp_error,
                exc_info=True,
            )
        logger.info(
            "Rejected duplicate packing list for container %s (attachment %s): %s",
            payload.packing_list.shipping_container_number,
            payload.packing_list.attachment_id,
            exc.message,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message)

    try:
        sn = (payload.packing_list.shipment_number or "").strip() or "-"
        aid = payload.packing_list.attachment_id
        summary_plain = (
            f'Packing list / inbound shipment "{sn}" was created in Sorento CRM via the external integration API '
        )
        summary_html = (
            f"<p>Packing list / inbound shipment <strong>{html.escape(sn)}</strong> was created "
            "in Sorento CRM via the external integration API.</p>"
        )
        notify_after_external_attachment_entity(
            db,
            [aid],
            payload.notify_user_id,
            notif_type="external_packing_list_created",
            title=f"Packing list created: {sn}",
            summary_plain=summary_plain,
            summary_html=summary_html,
            entity_url=build_packing_list_detail_url(str(created.id)),
            entity_link_text="Open packing list in Sorento CRM",
            warnings=skipped_product_codes or None,
        )
    except Exception as e:
        logger.warning("External packing list notification failed: %s", e, exc_info=True)

    already_existed = bool(getattr(created, "_already_existed", False))
    lines_skipped_reason = getattr(created, "lines_skipped_reason", None)
    if lines_skipped_reason:
        # AC-D4b: the caller is told in the response AND the fact is on file, because the
        # caller here is n8n and nobody reads its console. Best-effort - a logging failure
        # must never turn a successful header update into a 500.
        try:
            log_lines_skipped(
                db,
                str(created.id),
                payload.packing_list.attachment_id,
                lines_skipped_reason,
                created_by=created_by,
            )
        except Exception as log_error:  # noqa: BLE001
            logger.warning(
                "Failed to log the skipped-lines reason for shipment %s: %s",
                created.id,
                log_error,
                exc_info=True,
            )
    return PackingListCreateResponse(
        shipment=InboundShipmentResponse.model_validate(created),
        skipped_product_codes=skipped_product_codes,
        unknown_product_codes=skipped_product_codes,
        already_existed=already_existed,
        lines_skipped_reason=lines_skipped_reason,
        message=("Packing list updated in place." if already_existed else None),
    )
