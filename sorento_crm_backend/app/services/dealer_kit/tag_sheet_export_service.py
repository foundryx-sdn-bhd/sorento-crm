"""Tag sheet PDF export: request, render payload, and promotion guard.

Follows the same snapshot pattern as ``export_service.py`` for catalogue pages:
the viewer context is decided at enqueue and never re-derived later. The print
page renders whatever it is handed and cannot accidentally resolve prices for
the wrong audience.

The key difference is the expired-promotion guard: a tag sheet's prices depend
on the request's ``promotion_id``, and printing stale prices is worse than
refusing to print. AC-H.2 says to return 409 with a reason.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.dealer_kit import ExportRequest, Page, PageVersion
from app.models.download import DownloadStatus, UserDownload
from app.models.price_tag import PriceTagRequest, PriceTagRequestLine
from app.services.error_handler import AppException
from app.services.price_tag_request_service import (
    STATUS_APPROVED,
    STATUS_COLLECTED,
    STATUS_READY_FOR_COLLECTION,
)

logger = logging.getLogger(__name__)

KIND = "dealer_kit_tag_sheet_pdf"


def _slugify(name: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in name.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "tag-sheet"


def _check_promotion_expired(db: Session, request: PriceTagRequest) -> None:
    """Raise 409 if any LINE's own promotion has expired (D1/D10, AC-S9-5).

    A promotion is a line fact since S6, so a request can have several - the
    guard walks every distinct one actually IN USE and names the first
    offending line ("line N", 1-based by ``sort_order``), rather than reading
    a header the request no longer carries. ``business_today()`` (MYT), the
    same clock the pricing engine itself checks a promotion's window against,
    not the server's own ``date.today()`` - a promotion the engine would
    already treat as over must not export as if it were still running.
    """
    from app.models.marketing import Promotion
    from app.services.dealer_kit.pricing import business_today, promotion_may_price

    # Queried fresh rather than through `request.lines` - a line's promotion
    # can be changed by the office (S11) between the request being loaded and
    # this guard running, and the ORM relationship on an already-loaded
    # request would otherwise still show the old value.
    lines = (
        db.query(PriceTagRequestLine)
        .filter(PriceTagRequestLine.request_id == request.id)
        .order_by(PriceTagRequestLine.sort_order, PriceTagRequestLine.id)
        .populate_existing()
        .all()
    )
    promotion_ids = {line.promotion_id for line in lines if line.promotion_id}
    if not promotion_ids:
        return

    promotions = {
        promo.id: promo
        for promo in db.query(Promotion).filter(Promotion.id.in_(promotion_ids)).all()
    }
    today = business_today()
    # The type table read once for the whole request, not once per line.
    from app.services import promotion_serving

    types = promotion_serving.load_types(db)

    for index, line in enumerate(lines, start=1):
        if not line.promotion_id:
            continue
        promo = promotions.get(line.promotion_id)
        if promo is None:
            raise AppException(
                status_code=409,
                message=(
                    f"Line {index}'s promotion no longer exists. Remove the "
                    "promotion or link a new one before exporting."
                ),
                code="PROMOTION_MISSING",
            )
        if promo.end_date and promo.end_date < today:
            # PRICE-TAG-A3-EXPIRED: an ended promotion still inside its type's
            # bound (A3 180 days, standard/PP/focus to year end) still prices
            # the tag on screen, so it exports too. Past the bound the refusal
            # stands. Checked
            # before `is_active` because the hourly window task switches an
            # ended promotion off, so the flag would refuse every one of them.
            if promotion_may_price(db, promo, today, types=types):
                continue
            raise AppException(
                status_code=409,
                message=(
                    f"Line {index}'s promotion '{promo.description or promo.id}' "
                    f"expired on {promo.end_date.isoformat()}. Extend the "
                    "promotion or remove it from the line before exporting."
                ),
                code="PROMOTION_EXPIRED",
            )
        if not promo.is_active:
            raise AppException(
                status_code=409,
                message=(
                    f"Line {index}'s promotion '{promo.description or promo.id}' "
                    "is inactive. Reactivate it or remove it from the line "
                    "before exporting."
                ),
                code="PROMOTION_INACTIVE",
            )


def request_tag_sheet_export(
    db: Session,
    *,
    request_id: str,
    user_id: str,
    sheet_ids: Optional[list[str]] = None,
) -> tuple[UserDownload, Optional[list[str]]]:
    """Queue a tag sheet PDF and snapshot what it should contain.

    Returns (download, sheet_ids) so the caller can pass sheet_ids to the RQ task.

    Validates:
    - Request must be in ``approved`` or ``ready`` status.
    - Promotion must not be expired.
    - The request must have a tag_sheet page with at least one version.

    On first successful export after ``approved``, transitions to ``ready``.
    """
    if not user_id:
        raise AppException(
            status_code=422,
            message="An export needs a requesting user.",
        )

    request = (
        db.query(PriceTagRequest)
        .filter(PriceTagRequest.id == request_id)
        .first()
    )
    if request is None:
        raise AppException(
            status_code=404,
            message="Price tag request not found.",
            code="NOT_FOUND",
        )

    # AC-S9-7: one export in flight per REQUEST, whoever asks - a double
    # click (or the portal's own poll racing a slow click) must not queue a
    # second render of the same request. The SAME in-flight download comes
    # back and nothing new is enqueued. Keyed on the ENTITY
    # (`source_entity_type`/`source_entity_id`), never the caller's own user
    # id (unlike `DownloadService.has_in_flight`): the CRM export route and
    # the portal export route can each trigger this same request's export,
    # and the second one to arrive must see the first's, not its own.
    in_flight = (
        db.query(UserDownload)
        .filter(
            UserDownload.source_entity_type == "price_tag_request",
            UserDownload.source_entity_id == str(request_id),
            UserDownload.kind == KIND,
            UserDownload.status.in_(
                [DownloadStatus.PENDING.value, DownloadStatus.PROCESSING.value]
            ),
        )
        .order_by(UserDownload.created_at.desc(), UserDownload.id.desc())
        .first()
    )
    if in_flight is not None:
        return in_flight, sheet_ids

    # Every finished status can be exported (D8): the office reprints a lost
    # sheet after collection, and asking for a PDF is not a step in the
    # hand-over.
    if request.status not in (
        STATUS_APPROVED,
        STATUS_READY_FOR_COLLECTION,
        STATUS_COLLECTED,
    ):
        raise AppException(
            status_code=409,
            message=(
                f"Cannot export a request in '{request.status}' status. "
                f"The request must be approved before export."
            ),
            code="INVALID_STATE",
        )

    # Promotion guard (AC-H.2).
    _check_promotion_expired(db, request)

    if not request.page_id:
        raise AppException(
            status_code=409,
            message="No tag sheet page exists for this request.",
            code="NO_PAGE",
        )

    page = db.query(Page).filter(Page.id == request.page_id).first()
    if page is None:
        raise AppException(
            status_code=404,
            message="Tag sheet page not found.",
            code="NOT_FOUND",
        )

    # Use the latest version (tag sheets do not have published/staging labels).
    version = (
        db.query(PageVersion)
        .filter(PageVersion.page_id == page.id)
        .order_by(PageVersion.version.desc())
        .first()
    )
    if version is None:
        raise AppException(
            status_code=409,
            message="The tag sheet has no saved designs yet.",
            code="NO_VERSION",
        )

    filename = f"{_slugify(request.doc_number)}-tags-v{version.version}.pdf"

    download = UserDownload(
        user_id=user_id,
        kind=KIND,
        source_entity_type="price_tag_request",
        source_entity_id=request_id,
        status=DownloadStatus.PENDING.value,
        filename=filename,
    )
    db.add(download)
    db.flush()

    # Store sheet_ids filter in the export request metadata if provided.
    db.add(
        ExportRequest(
            download_id=download.id,
            page_id=page.id,
            page_version_id=version.id,
            # Staff audience for tag sheets (they go to the printer).
            audience="staff",
            show_invoice_price=False,
            requested_by=user_id,
        )
    )

    # No transition (r9 D8). `approved -> ready` used to fire here, which said
    # a PDF existed and nothing about who had the tags; the hand-over is its
    # own two steps now and a PDF request is not one of them.
    db.commit()
    db.refresh(download)

    # Store sheet_ids filter as metadata on the download row for the render
    # payload to read. We use the download's error field temporarily (it is
    # null for pending downloads) - but that is fragile. Instead, we store it
    # in the export_request row by extending the pattern slightly.
    # For now, sheet_ids are passed via the print URL query parameter.
    # The render payload endpoint reads them from there.

    # B2: the enqueue lives HERE, not in each caller - the CRM export route
    # and transition_status's own auto-export on approve (D12) both call
    # this function, and a caller that only wrote the PENDING
    # UserDownload/ExportRequest rows and never queued the render left the
    # PDF permanently un-generated. On a queueing failure the download is
    # marked failed rather than left PENDING forever.
    from app.services.download_service import DownloadService
    from app.services.queue_service import enqueue_job
    from app.tasks.dealer_kit_export_tasks import generate_tag_sheet_pdf

    try:
        enqueue_job(
            generate_tag_sheet_pdf,
            str(download.id),
            sheet_ids,
            queue_name="catalogue_render",
            job_timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        DownloadService(db).mark_failed(
            str(download.id), f"Could not queue PDF generation: {exc}"
        )

    return download, sheet_ids


def latest_completed_export(db: Session, request_id: str) -> Optional[UserDownload]:
    """The request's most recent completed (READY) tag sheet PDF, or None.

    Keyed by ``source_entity_type``/``source_entity_id``, never by the download
    row's ``user_id`` - that is the marketing staffer who ran the export, not
    the portal contact who owns the request. The caller checks the request's
    ownership itself (``_require_own_request``); this only answers "does a
    finished PDF exist for it".

    ``id.desc()`` breaks a tie on ``created_at``: two rows seeded (or, in a
    real export, committed) inside the same transaction see the same Postgres
    ``now()`` (it is fixed at transaction start, not per-statement), so
    ``created_at`` alone cannot always tell "latest" from "simultaneous".
    """
    return (
        db.query(UserDownload)
        .filter(
            UserDownload.source_entity_type == "price_tag_request",
            UserDownload.source_entity_id == str(request_id),
            UserDownload.kind == KIND,
            UserDownload.status == DownloadStatus.READY.value,
        )
        .order_by(UserDownload.created_at.desc(), UserDownload.id.desc())
        .first()
    )


def latest_export_status(db: Session, request_id: str) -> Optional[str]:
    """r10 S9: `ready | pending | failed | None` - "never asked" from "in
    progress" from "failed", off the request's most recent tag sheet PDF
    download regardless of its status (``latest_completed_export`` above
    only ever answers a READY one). ``processing`` reads as `pending` too -
    the portal button has one waiting state, not two.
    """
    row = (
        db.query(UserDownload)
        .filter(
            UserDownload.source_entity_type == "price_tag_request",
            UserDownload.source_entity_id == str(request_id),
            UserDownload.kind == KIND,
        )
        .order_by(UserDownload.created_at.desc(), UserDownload.id.desc())
        .first()
    )
    if row is None:
        return None
    if row.status == DownloadStatus.READY.value:
        return "ready"
    if row.status == DownloadStatus.FAILED.value:
        return "failed"
    return "pending"


def render_inputs(db: Session, download_id: str) -> dict:
    """Everything the tag sheet render needs."""
    from app.services.dealer_kit.export_service import get_request as _get_export_request

    export_req = _get_export_request(db, download_id)
    version = (
        db.query(PageVersion)
        .filter(PageVersion.id == export_req.page_version_id)
        .first()
    )
    if version is None:
        raise AppException(
            status_code=404,
            message="The exported version no longer exists.",
        )

    page = db.query(Page).filter(Page.id == export_req.page_id).first()
    if page is None:
        raise AppException(
            status_code=404,
            message="Tag sheet page not found.",
        )

    # Find the price tag request from the page.
    request = (
        db.query(PriceTagRequest)
        .filter(PriceTagRequest.page_id == page.id)
        .first()
    )

    return {
        "page_id": export_req.page_id,
        "version_id": version.id,
        "version": version.version,
        "doc": version.doc,
        "request": request,
        "page": page,
    }


def resolve_tag_sheet_print_payload(db: Session, download_id: str) -> dict:
    """Everything the print page renders, resolved at render time (ADR 0008).

    Prices and product data come from ``tag_data_service`` - the SAME call the
    designer's left panel makes - so the proof marketing approved on screen and
    the PDF that reaches the printer carry the same figures. Artwork is signed
    HERE and sent with the payload rather than fetched by the page: the worker
    waits on one ready flag, and a picture that starts loading after it prints
    as a blank box.
    """
    from app.models.base import company_scope

    # Chicken and egg, and the sibling catalogue route solves it the same way.
    # The caller is unauthenticated, so the session sits at the fail-closed UNSET
    # scope and would find nothing at all; the page has to be read ACROSS
    # companies to learn which company this render is for. What used to happen
    # next was that the whole payload resolved there too - every product, price,
    # asset and font of every other company in reach of a token issued for this
    # one. So the widening stops at the lookup, and everything after it is pinned
    # to the page's own company.
    with company_scope(db, None):
        inputs = render_inputs(db, download_id)
    page = inputs["page"]
    scope = frozenset({page.company_id}) if page.company_id else None

    with company_scope(db, scope):
        return _resolved_payload(db, inputs)


def design_media(
    db: Session,
    request,
    doc: Optional[dict],
    rows: Optional[list[dict]] = None,
) -> tuple[list[dict], dict]:
    """Everything a tag sheet needs to DRAW itself, resolved once (r9 S1/D1).

    Returns ``(rows, media)``: the resolver's own line rows, and the three maps
    every surface that draws a sheet needs - ``assets`` (library artwork by
    asset id), ``images`` (product photos by attachment id) and ``fonts``.

    One function, three readers: the PDF payload below, the portal design
    preview and the CRM design preview. Before r9 only the PDF had the maps, so
    the two previews painted a grey box for every image layer and fell back to
    a system sans for every brand face - the same document, drawn three
    different ways. A second resolver anywhere here is how that comes back.

    ``rows`` is for the one caller whose lines do not come from the request as
    it stands now: a VERSION draws the pins it was written with (D19/S2), and
    the media maps then have to be built from those rows rather than today's.
    """
    from app.services.dealer_kit import asset_service, tag_data_service

    if rows is None:
        rows = (
            list(tag_data_service.resolve_request_line_data(db, request))
            if request is not None
            else []
        )
    # AC-S6-8/AC-S6-12: a tag marked Not printed is still open and editable
    # on the rail/canvas (those go straight through
    # `resolve_request_line_data`), but this is the ONE resolver behind the
    # PDF payload, the portal preview and the CRM design preview - a proof
    # of what will print must never carry a tag that will not, and an image
    # reachable only through that tag must not be signed into the export.
    if request is not None:
        excluded_tag_ids = {
            tag.id
            for line in (request.lines or [])
            for tag in (line.tags or [])
            if tag.print_excluded
        }
        if excluded_tag_ids:
            rows = [row for row in rows if row["tag_id"] not in excluded_tag_ids]
    images: dict[str, str] = {}
    for row in rows:
        for image in row["images"]:
            images[image["attachment_id"]] = image["url"]
        # R9 (reviewer B1): a layer may pick ANY part as its subject (D7),
        # so a part's own photo needs a signed URL in this map exactly like
        # the host's - walking only `row["images"]` left a part-bound image
        # layer with nothing to draw in the export payload at all.
        for part in row.get("parts") or []:
            for image in part.get("images") or []:
                images[image["attachment_id"]] = image["url"]

    return rows, {
        # assetId -> signed URL, for every library asset the document names.
        "assets": asset_service.urls_for(
            db, asset_service.tag_sheet_asset_ids(doc or {})
        ),
        # attachmentId -> signed URL, for every product photo a bound layer may
        # be showing. Gated by `product_images` before it ever gets here.
        "images": images,
        # Brand fonts, loaded through @font-face before anything draws with them.
        "fonts": asset_service.font_assets(db),
    }


def _resolved_payload(db: Session, inputs: dict) -> dict:
    """The payload itself, resolved under whatever scope the caller pinned."""
    request = inputs["request"]
    doc = inputs["doc"] or {}

    rows, media = design_media(db, request, doc)

    # AC-S6-12 (extended): `design_media` above only filters the resolver
    # ROWS - the SAVED doc's own placements pass straight through. A tag
    # marked Not printed AFTER the doc was last arranged leaves a stale
    # placement in `doc.sheets` that the print page draws from directly, so
    # the doc itself needs the same filter here, and a sheet a filter
    # empties out entirely is dropped rather than printing a blank page -
    # the export's own sheet list then counts printed tags only.
    if request is not None and doc.get("sheets"):
        excluded_tag_ids = {
            tag.id
            for line in (request.lines or [])
            for tag in (line.tags or [])
            if tag.print_excluded
        }
        if excluded_tag_ids:
            filtered_sheets = []
            for sheet in doc["sheets"]:
                tags = [
                    placed
                    for placed in (sheet.get("tags") or [])
                    if placed.get("request_tag_id") not in excluded_tag_ids
                ]
                if tags:
                    filtered_sheets.append({**sheet, "tags": tags})
            doc = {**doc, "sheets": filtered_sheets}

    resolved_data: dict[str, dict] = {}

    for row in rows:
        # Keyed by REQUEST TAG since the combos slice (D3) - a line may print
        # several tags, so a line id could no longer name one tile's data.
        resolved_data[row["tag_id"]] = {
            "tag_id": row["tag_id"],
            "line_id": row["line_id"],
            "tag_label": row["tag_label"],
            "open_groups": row["open_groups"],
            # D7 (S9): full product data per part, prices leaving as floats
            # the same way the tag's own do below.
            "parts": [
                {
                    **part,
                    "list_price": _as_float(part.get("list_price")),
                    "sell_price": _as_float(part.get("sell_price")),
                }
                for part in row["parts"]
            ],
            "code": row["code"],
            "name": row["name"],
            "dimensions": row["dimensions"],
            "spec_lines": row["spec_lines"],
            # Key by key, so a `{{spec.<key>}}` in a saved tag resolves in
            # the PDF exactly as it did on the canvas (D58).
            "specs": row["specs"],
            "set_members": row["set_members"],
            # Money leaves as a number the browser can format. The Decimal
            # arithmetic already happened, in the pricing engine.
            "list_price": _as_float(row["list_price"]),
            "sell_price": _as_float(row["sell_price"]),
            # D7 (S9): the host alone - what a price badge's `subjectPart:
            # -1` reads, never the roll-up above.
            "parent_list_price": _as_float(row.get("parent_list_price")),
            "parent_sell_price": _as_float(row.get("parent_sell_price")),
            "sell_price_basis": row.get("sell_price_basis"),
            "show_promo_price": row["show_promo_price"],
            "included_accessories": row["included_accessories"],
            "quantity": row["quantity"],
            # The barcode layer's binding (S7); null for a set line.
            "barcode": row["barcode"],
            # The photos themselves, not just their ids: a product-photo
            # slot follows the product's PRIMARY photo when the template
            # pinned none (D42), and only this list says which that is.
            "images": row["images"],
        }

    return {
        "doc": doc,
        "resolvedData": resolved_data,
        **media,
        "requestDocNumber": request.doc_number if request is not None else "",
        "version": inputs["version"],
    }


def _as_float(value) -> Optional[float]:
    return None if value is None else float(value)
