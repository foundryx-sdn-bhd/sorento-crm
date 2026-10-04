"""Background tasks for user-requested exports (My Downloads).

Decoupled from the request path: the API creates a UserDownload row (status
'pending') and enqueues one of these tasks. The task renders the artifact,
uploads it to the default storage provider, and flips the row to 'ready' (with a
storage key) or 'failed' (with an error). The "My Downloads" drawer polls the
per-user rows while any are in flight.
"""
import logging
from typing import Optional

from app.database import SessionLocal
from app.services.company_scope import set_company_scope
from app.services.download_service import DownloadService
from app.services.storage_router import default_provider, get_backend

logger = logging.getLogger(__name__)

#: R8 (security S3, review round 1): the fixed sentence a caller passes to `_record_
#: failure` via `message=` when the underlying exception's OWN text is not safe to show
#: the requesting user verbatim (a SQL fragment, a column name, a file path). Public -
#: the enqueuing ENDPOINT (`enqueue_packing_list_export`) stores the same sentence for
#: its own queue-time failure, so the drawer never shows two different wordings for
#: "this export did not work".
EXPORT_FAILURE_MESSAGE = "Export failed. Please try again."


def _record_failure(
    db, svc: DownloadService, download_id: str, error: Exception, label: str,
    *, message: Optional[str] = None,
) -> None:
    """Write the failure onto the download row, whatever it was that failed.

    The rollback is the point. When the thing that failed was the DATABASE - a query against a
    column the running code expects and the schema does not yet have, say - psycopg2 leaves the
    transaction aborted, and every later statement on that session raises
    `InFailedSqlTransaction`. `mark_failed` is a later statement on that session, so without a
    rollback first it raises too, and the row is left sitting in 'processing' for good: the
    drawer polls it forever, and its sweeper only reaps rows in 'sent'. The user is told nothing.

    `message`, when given, OVERRIDES what gets STORED on the row (R8) - the caller's own
    `logger.exception` right before this call is where the real exception detail goes;
    every other caller keeps storing `str(error)` unchanged, so this is opt-in per task.

    Marking the failure is itself best-effort - if even this cannot be written, log it and let
    the task return normally rather than poisoning RQ's failed registry.
    """
    try:
        db.rollback()
    except Exception:  # noqa: BLE001 - a session too broken to roll back is still worth trying
        logger.exception("%s: rollback before marking download %s failed", label, download_id)
    try:
        svc.mark_failed(download_id, message if message is not None else str(error))
    except Exception:  # noqa: BLE001
        logger.exception("%s: could not mark download %s failed", label, download_id)


def generate_complaint_pdf(download_id: str, complaint_id: str, user_id: str) -> dict:
    """Render a complaint PDF, store it, and update the download row.

    Best-effort and self-contained: any failure marks the download 'failed' with
    a readable message rather than raising into RQ's failed registry.
    """
    db = SessionLocal()
    # Worker sessions default to the fail-closed UNSET scope; complaints are a
    # global (non-owned) entity and their attachments are shared, so run this
    # export system-wide (all companies).
    set_company_scope(db, None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services.complaint_pdf_service import ComplaintPDFService

        pdf_bytes, filename = ComplaintPDFService(db).render_pdf(complaint_id)

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/complaint-pdf/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=pdf_bytes,
            file_path=key,
            content_type="application/pdf",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info("generate_complaint_pdf: download %s ready (%d bytes)", download_id, len(pdf_bytes))
        return {"download_id": download_id, "status": "ready", "bytes": len(pdf_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_complaint_pdf failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_complaint_pdf")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_packing_list_xlsx(download_id: str, shipment_id: str) -> dict:
    """Render the consolidated packing list workbook, store it, and update the download
    row (E1/E2, PLAN-pi-header-fields-convert-fixes-24sep.md) - the SAME bytes the
    synchronous GET export has always produced (`consolidated_packing_list.build` +
    `to_xlsx`), just rendered on the worker instead of the request path.

    Best-effort and self-contained: any failure marks the download 'failed' with a
    fixed, safe message rather than raising into RQ's failed registry - same pattern
    `generate_complaint_pdf` follows.

    R7 (security review round 1): `shipment_id` is an RQ job ARGUMENT - trusted input,
    replayable and, on a compromised worker queue, spoofable - never cross-checked
    against anything before this fix. Two checks, mirroring the fix already shipped for
    `generate_order_inquiry_xlsx` (security review fix round 2, item 2, sha 002fd3d2e on
    `fix/order-sheet-cells`):

    (a) the download row is the ONLY thing that says which shipment this render is FOR -
    it must name `source_entity_type="inbound_shipment"` and `source_entity_id ==
    shipment_id`, or the render never starts (a mismatched pair would otherwise store
    one shipment's data under a download that names a different one).

    (b) an `InboundShipment` is company-scoped data (`CompanyScopedMixin`), not a shared
    entity like a complaint - the OLD comment here claiming otherwise was simply wrong.
    The shipment is resolved under `None` (every company, the same shape `generate_
    order_inquiry_xlsx` uses to find a row before it knows the row's own company), then
    the session is RE-SCOPED to that shipment's own company before anything else touches
    it, and the render refuses outright unless the download's OWNING user
    (`user_downloads.user_id`) is a member of that company - closing the gap `None`
    would otherwise leave open for the rest of the render.
    """
    db = SessionLocal()
    from app.models.base import UNSET, get_company_scope
    from app.models.procurement import InboundShipment
    from app.services.company_scope_resolver import resolve_user_grant_ids

    caller_scope = get_company_scope(db)
    svc = DownloadService(db)
    try:
        row = svc.get(download_id)
        if (
            row is None
            or row.source_entity_type != "inbound_shipment"
            or str(row.source_entity_id) != str(shipment_id)
        ):
            raise ValueError(
                f"Download {download_id} does not name shipment {shipment_id}; "
                "refusing to export."
            )

        set_company_scope(db, None)
        shipment = db.get(InboundShipment, shipment_id)
        company_id = getattr(shipment, "company_id", None) if shipment is not None else None
        if company_id:
            set_company_scope(db, frozenset({str(company_id)}))
        else:
            set_company_scope(db, UNSET)
            raise ValueError(
                f"Shipment {shipment_id} could not be found or carries no company; "
                "refusing to export."
            )

        # S1 (review round 2, blocker): the PLATFORM'S OWN scope resolver, not a raw
        # `UserCompany` lookup - `resolve_user_grant_ids` treats a superadmin/admin as a
        # member of EVERY company (the same rule the active-company switcher and every
        # other screen already honour), so an admin's export of a shipment they hold no
        # explicit membership row for still renders, exactly as it would through the
        # normal request path.
        if str(company_id) not in resolve_user_grant_ids(db, str(row.user_id)):
            raise ValueError(
                f"User {row.user_id} is not a member of shipment {shipment_id}'s "
                "company; refusing to export."
            )

        svc.mark_processing(download_id)

        from app.services.scm import consolidated_packing_list

        payload = consolidated_packing_list.build(db, shipment_id)
        xlsx_bytes = consolidated_packing_list.to_xlsx(payload)
        filename = consolidated_packing_list.export_filename(payload)

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/packing-list-xlsx/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=xlsx_bytes,
            file_path=key,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_packing_list_xlsx: download %s ready (%d bytes)",
            download_id, len(xlsx_bytes),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(xlsx_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_packing_list_xlsx failed for download %s", download_id)
        # R8: a fixed sentence stored on the row - never `str(e)`, which could carry a
        # SQL fragment, a column name, or a file path this drawer shows the user.
        _record_failure(
            db, svc, download_id, e, "generate_packing_list_xlsx",
            message=EXPORT_FAILURE_MESSAGE,
        )
        return {"download_id": download_id, "status": "failed", "error": EXPORT_FAILURE_MESSAGE}
    finally:
        set_company_scope(db, caller_scope)
        db.close()


def generate_stock_inquiry_pdf(
    download_id: str,
    inquiry_id: str,
    user_id: str,
    revision_id: Optional[str] = None,
    include_revisions: bool = False,
) -> dict:
    """Render a product inquiry PDF, store it, and update the download row.

    Best-effort and self-contained: any failure marks the download 'failed' with
    a readable message rather than raising into RQ's failed registry.

    ``revision_id`` / ``include_revisions`` were added by
    PLAN-portal-submission-revisions 6.3/6.4. They are ordinary
    positional-or-keyword parameters WITH DEFAULTS, and the routes pass them by
    keyword; the defaults are what keeps a job queued by an older release -
    three positional args, no keywords - running here unchanged.
    """
    db = SessionLocal()
    # Worker sessions default to the fail-closed UNSET scope; stock inquiries are a
    # global (non-owned) entity and their attachments are shared, so run this
    # export system-wide (all companies) - same as the complaint export.
    set_company_scope(db, None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services.stock_inquiry_pdf_service import StockInquiryPDFService

        pdf_bytes, filename = StockInquiryPDFService(db).render_pdf(
            inquiry_id,
            revision_id=revision_id,
            include_revisions=bool(include_revisions),
        )

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/product-inquiry-pdf/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=pdf_bytes,
            file_path=key,
            content_type="application/pdf",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_stock_inquiry_pdf: download %s ready (%d bytes)", download_id, len(pdf_bytes)
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(pdf_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_stock_inquiry_pdf failed for download %s", download_id)
        try:
            svc.mark_failed(download_id, str(e))
        except Exception:
            logger.exception(
                "generate_stock_inquiry_pdf: could not mark download %s failed", download_id
            )
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_purchase_request_pdf(
    download_id: str,
    request_id: str,
    user_id: str,
    revision_id: Optional[str] = None,
    include_revisions: bool = False,
) -> dict:
    """Render a purchase request / sponsorship form PDF, store it, update the row.

    Best-effort and self-contained: any failure marks the download 'failed' with a
    readable message rather than raising into RQ's failed registry. Mirrors
    generate_stock_inquiry_pdf, including the optional revision parameters and
    their defaults.
    """
    db = SessionLocal()
    # Worker sessions default to the fail-closed UNSET scope. PR/SF are global
    # (non-owned) and their attachments are shared, so export system-wide - the
    # same choice the complaint and stock-inquiry exports make.
    set_company_scope(db, None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

        pdf_bytes, filename = PurchaseRequestPDFService(db).render_pdf(
            request_id,
            revision_id=revision_id,
            include_revisions=bool(include_revisions),
        )

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/purchase-request-pdf/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=pdf_bytes,
            file_path=key,
            content_type="application/pdf",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_purchase_request_pdf: download %s ready (%d bytes)",
            download_id,
            len(pdf_bytes),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(pdf_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_purchase_request_pdf failed for download %s", download_id)
        try:
            svc.mark_failed(download_id, str(e))
        except Exception:
            logger.exception(
                "generate_purchase_request_pdf: could not mark download %s failed", download_id
            )
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_promotions_pdf(
    download_id: str, promotion_ids: list, user_id: str, company_id: Optional[str] = None
) -> dict:
    """Compile the selected promotions' attachment flyers into one PDF, store it,
    and update the download row.

    Best-effort and self-contained: any failure marks the download 'failed' with
    a readable message rather than raising into RQ's failed registry.
    """
    db = SessionLocal()
    # Worker sessions default to the fail-closed UNSET scope, which would hide the
    # owned Promotion/PromotionAttachment rows. Re-establish the enqueuer's active
    # company (snapshotted at enqueue) so the export actually sees them; None =
    # system-wide (e.g. a system principal that enqueued without a single company).
    set_company_scope(db, frozenset({company_id}) if company_id else None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services.promotions_pdf_service import PromotionsPdfService

        pdf_bytes, filename, skipped = PromotionsPdfService(db).render_pdf(list(promotion_ids or []))

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/promotions-pdf/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=pdf_bytes,
            file_path=key,
            content_type="application/pdf",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_promotions_pdf: download %s ready (%d bytes, %d skipped)",
            download_id, len(pdf_bytes), len(skipped),
        )
        return {
            "download_id": download_id,
            "status": "ready",
            "bytes": len(pdf_bytes),
            "skipped": skipped,
        }
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_promotions_pdf failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_promotions_pdf")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def _quotation_issue_or_die(db, issue_id: str):
    """The issue row the export renders from, or a readable failure.

    The download row outlives what it points at - a document can be deleted between the
    enqueue and the render - so a missing revision has to become a failed download rather
    than an AttributeError deep inside the renderer.
    """
    from app.models.projects import ProjectQuotationIssue

    issue = (
        db.query(ProjectQuotationIssue)
        .filter(ProjectQuotationIssue.id == str(issue_id))
        .first()
    )
    if issue is None:
        raise ValueError(
            "This revision no longer exists, so it cannot be exported."
        )
    return issue


def generate_quotation_issue_pdf(
    download_id: str, issue_id: str, user_id: str, company_id: Optional[str] = None
) -> dict:
    """Render one issued quotation as a PDF, store it, and update the download row.

    The renderer is the SAME one the on-demand route uses and it still reads the ISSUE
    snapshot, so a download next year is what was sent. What is new is only that the bytes are
    persisted once, as this download job's artifact - nothing else ever reads that object, and
    the document itself keeps rendering on demand.

    Best-effort and self-contained: any failure marks the download 'failed' with a readable
    message rather than raising into RQ's failed registry.
    """
    db = SessionLocal()
    # Worker sessions default to the fail-closed UNSET scope, which would hide every
    # company-owned quotation row. Re-establish the enqueuer's active company (snapshotted at
    # enqueue); None = system-wide, for a principal with no single company.
    set_company_scope(db, frozenset({company_id}) if company_id else None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services import project_quotation_pdf_service as pdf

        pdf_bytes, filename = pdf.render_issue_pdf(
            db, _quotation_issue_or_die(db, issue_id)
        )

        provider = default_provider()
        backend = get_backend(provider)
        # Keyed by DOWNLOAD id, not by the reference: two exports of the same revision are two
        # rows, and a shared key would have the second silently overwrite the first's file.
        key = f"exports/quotation-pdf/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=pdf_bytes,
            file_path=key,
            content_type="application/pdf",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_quotation_issue_pdf: download %s ready (%d bytes)",
            download_id, len(pdf_bytes),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(pdf_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_quotation_issue_pdf failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_quotation_issue_pdf")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_quotation_issue_xlsx(
    download_id: str, issue_id: str, user_id: str, company_id: Optional[str] = None
) -> dict:
    """Render one issued quotation as a workbook, store it, and update the download row.

    Same snapshot as the PDF, so the two artifacts of one revision can never quote different
    money. Separate task rather than a format flag: they upload under different prefixes with
    different mime types, and an xlsx served as application/pdf downloads as a file Excel
    refuses to open.
    """
    db = SessionLocal()
    set_company_scope(db, frozenset({company_id}) if company_id else None)
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services import project_quotation_excel_service as excel

        payload, filename = excel.render_issue_xlsx(
            db, _quotation_issue_or_die(db, issue_id)
        )

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/quotation-xlsx/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=payload,
            file_path=key,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_quotation_issue_xlsx: download %s ready (%d bytes)",
            download_id, len(payload),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(payload)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_quotation_issue_xlsx failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_quotation_issue_xlsx")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_chat_history_csv(download_id: str, filters: dict) -> dict:
    """Stream a chat-history CSV to storage and update the download row.

    CSV rather than XLSX, and streamed from a keyset walk rather than materialised:
    message text is long and the row count over a wide date range is unbounded, so
    building a spreadsheet in memory is the wrong shape. Memory stays flat regardless
    of how much the filter matches.
    """
    import csv
    import io
    from datetime import datetime

    db = SessionLocal()
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        from app.services.chat_history_query import MAX_LIMIT, list_messages

        def _dt(value):
            if not value:
                return None
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "sent_at_utc", "respond_ts_utc", "direction", "contact", "phone",
            "message", "turn_id", "message_id", "delivery_status",
            "latency_seconds", "webhook_lag_seconds",
        ])

        cursor = None
        rows_written = 0
        while True:
            rows, cursor = list_messages(
                db,
                date_from=_dt(filters.get("date_from")),
                date_to=_dt(filters.get("date_to")),
                contact_id=filters.get("contact_id"),
                direction=filters.get("direction"),
                search=filters.get("search"),
                breached_only=bool(filters.get("breached_only")),
                limit=MAX_LIMIT,
                cursor=cursor,
            )
            for r in rows:
                writer.writerow([
                    r.sent_at.isoformat() if r.sent_at else "",
                    r.respond_ts.isoformat() if r.respond_ts else "",
                    r.type,
                    r.contact_display,
                    r.phone_number,
                    r.message,
                    r.turn_id or "",
                    r.message_id or "",
                    r.delivery_status or "",
                    f"{r.latency_seconds:.3f}" if r.latency_seconds is not None else "",
                    f"{r.webhook_lag_seconds:.3f}" if r.webhook_lag_seconds is not None else "",
                ])
                rows_written += 1
            if not cursor:
                break

        content = buffer.getvalue().encode("utf-8-sig")  # BOM so Excel reads UTF-8
        filename = f"chat-history-{rows_written}-rows.csv"

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/chat-history/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=content,
            file_path=key,
            content_type="text/csv",
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_chat_history_csv: download %s ready (%d rows, %d bytes)",
            download_id, rows_written, len(content),
        )
        return {"download_id": download_id, "status": "ready", "rows": rows_written}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_chat_history_csv failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_chat_history_csv")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        db.close()


def generate_order_sheet(download_id: str, run_id: str, fmt: str, user_id: str) -> dict:
    """Render the reorder run's order sheet, store it, and update the download row.

    S4, PLAN-po-spo-site-pool-and-order-sheet-downloads.md (AC-15..AC-17). Mirrors
    `generate_complaint_pdf` line for line: `mark_processing`, render, upload, `mark_ready`
    - `_record_failure` on any exception, never raising into RQ.

    The row's OWN filename - stamped by the route at creation time, `order-sheet-
    <as_of ddmmyyyy>.<ext>` (AC-15) - is read back and reused for the storage key and the
    `mark_ready` call, rather than `summary_order_service.export_report`'s own returned
    name: that function still carries the "order-summary-..." convention the retired
    synchronous GET used, and passing it through would silently rename the row.
    """
    db = SessionLocal()
    # Security S1 (review fix round A, A2): the worker has NO request-scoped company - a
    # bare `set_company_scope(db, None)` here would read every company's rows, which is
    # exactly the isolation break `_adopt_run_company_scope` exists to close for
    # `run_reorder` itself. The run row is the one thing that states which company this
    # export belongs to, so it is read under NO scope (the row itself is what tells us),
    # then its OWN company is adopted before anything company-scoped is touched -
    # `reorder_run_service._adopt_run_company_scope`, the same shape
    # `generate_promotions_pdf` above uses via its own snapshotted `company_id` param.
    from app.models.base import UNSET, get_company_scope
    from app.models.scm import ReorderRun
    from app.services.scm.reorder_run_service import _adopt_run_company_scope

    # C3 (review fix round C): restored in `finally`, the same shape `_execute_run` uses
    # for `run_reorder` (reorder_run_service.py ~L548-560) - a caller whose session this
    # task reuses (a synchronous test, `_NoCloseSession`) did not ask to have its scope
    # changed underneath it, and leaving the run's company adopted after return would
    # silently re-scope whatever that caller reads next.
    caller_scope = get_company_scope(db)
    set_company_scope(db, None)
    run = db.get(ReorderRun, run_id)
    if run is not None:
        _adopt_run_company_scope(db, run)
    if run is None or not getattr(run, "company_id", None):
        # AC-A10 (security should-fix, PLAN-order-sheet-oi-reports-22sep.md, 23 Sep
        # review): a run that does not exist, or whose OWN `company_id` is NULL (a
        # legacy row from before the column existed - `_adopt_run_company_scope`
        # deliberately leaves such a row's scope untouched rather than defaulting it),
        # must not export under the `None` scope set two lines up - `None` means "no
        # predicate, every company", the exact isolation break this export exists to
        # close. UNSET fails closed instead: every raw-SQL company predicate downstream
        # (`company_sql_predicate`, read by `_last_cost_map` / `_project_inquiry_map`)
        # renders `1=0`, and `ReorderRun` itself is company-scoped, so `export_report`'s
        # own `_run_for` lookup finds nothing and the export fails rather than leaking.
        set_company_scope(db, UNSET)
        logger.warning(
            "generate_order_sheet: run %s not found or has no company; "
            "failing closed rather than exporting under no company scope", run_id
        )
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        row = svc.get(download_id)
        filename = row.filename if row else None

        from app.services.scm import summary_order_service

        file_bytes, content_type, fallback_filename = summary_order_service.export_report(
            db, run_id=run_id, fmt=fmt,
        )
        filename = filename or fallback_filename

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/order-sheet/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=content_type,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_order_sheet: download %s ready (%d bytes)", download_id, len(file_bytes)
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(file_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_order_sheet failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_order_sheet")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        set_company_scope(db, caller_scope)
        db.close()


def generate_oi_worksheet(download_id: str, run_id: str, user_id: str) -> dict:
    """Render the run's OI worksheet, store it, and update the download row.

    Lane C, PLAN-order-sheet-oi-reports-22sep.md (AC-C1..AC-C4). `generate_order_sheet`'s
    own twin, line for line, including the company dance: the worker has NO request-scoped
    company, so the run row is read under NO scope (it is the one thing that states which
    company this export belongs to), its OWN company is adopted before anything company-
    scoped is touched, UNSET fails closed when the run is missing or carries no company
    (AC-A10's own reasoning), and the caller's scope is restored in `finally` - a
    synchronous caller whose session this reuses (a test) did not ask to have its scope
    changed underneath it.

    The row set is the run's own Start Plan scope (`demand.run_scope_oi_rows`, C2/C4a -
    the SAME helper the order sheet's Project qty column reads, so the two can never list
    a different row set for the same run), printed through the worklist's own writer with
    the worksheet's 10-column slice (`EXPORT_HEADINGS[:10]`, C3 - no ACKNOWLEDGED / TAKEN
    / REMAINING).

    `_record_failure` on any exception, never raising into RQ: a poisoned job retries for
    ever and the buyer's row sits `processing` until it goes stale.
    """
    db = SessionLocal()
    from app.models.base import UNSET, get_company_scope
    from app.models.scm import ReorderRun
    from app.services.scm.reorder_run_service import _adopt_run_company_scope

    caller_scope = get_company_scope(db)
    set_company_scope(db, None)
    run = db.get(ReorderRun, run_id)
    if run is not None:
        _adopt_run_company_scope(db, run)
    if run is None or not getattr(run, "company_id", None):
        set_company_scope(db, UNSET)
        logger.warning(
            "generate_oi_worksheet: run %s not found or has no company; "
            "failing closed rather than exporting under no company scope", run_id
        )
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        row = svc.get(download_id)
        filename = row.filename if row else None

        # Fix round 2 (S2): `generate_order_sheet`'s twin check raises the SAME 404
        # implicitly - under UNSET scope, `export_report`'s own `_run_for` lookup finds
        # nothing and raises `AppException(404, "That plan does not exist.")`, which this
        # function converts to a FAILED download. This task calls no ORM query on
        # `ReorderRun` past this point (`run_scope_oi_rows` is raw SQL, and its own
        # company predicate renders `1=0` under UNSET - EMPTY rows, not an exception), so
        # the same case has to be raised explicitly here, or a missing/company-less run
        # would render a "ready" workbook with nothing wrong reported.
        from app.services.error_handler import AppException

        if run is None or not getattr(run, "company_id", None):
            raise AppException(404, "That plan does not exist.")

        from app.services.order_inquiry_worklist_service import (
            EXPORT_HEADINGS,
            OrderInquiryWorklistService,
        )
        from app.services.scm.demand import run_scope_oi_rows

        # Fix round 1: `run.product_ids` unchanged, NOT `run.product_ids or None` - see
        # the twin comment in `order_summary.py`'s own guard.
        scope_rows = run_scope_oi_rows(
            db, run.product_ids, so_numbers=run.so_numbers,
            horizon_start=run.plan_horizon_start, horizon=run.plan_horizon_date,
        )
        row_ids = [r["row_id"] for r in scope_rows]

        fallback_filename, file_bytes = OrderInquiryWorklistService(db).export_xlsx(
            row_ids=row_ids, columns=EXPORT_HEADINGS[:10],
        )
        filename = filename or fallback_filename
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/oi-worksheet/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=content_type,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_oi_worksheet: download %s ready (%d bytes)", download_id, len(file_bytes)
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(file_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_oi_worksheet failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_oi_worksheet")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        set_company_scope(db, caller_scope)
        db.close()


#: Refusals the Respond helpers raise BEFORE they reach their own `log_respond_send`, so
#: this module has to write the outbox row itself. Every OTHER failure - notably
#: `respond_send_failed` (502), raised after the attempt was logged - is already in the
#: outbox, and logging again would double-count one send (reviewer item 5).
_PRE_LOG_REFUSAL_CODES = frozenset({"attachment_window_closed", "no_chat_template"})

#: What the contact is told when the workbook they were promised could not be built
#: (reviewer item 1). Sent as TEXT through `send_chat_message_for`, which has the
#: closed-window template fallback the attachment path lacks.
LOW_STOCK_BUILD_FAILED_TEXT = (
    "Could not build the low stock report - ask again in a minute."
)


def _app_exception_code(exc: BaseException) -> object:
    """`AppException` carries `code`/`message` inside `detail`, not as attributes - reading
    them off the instance silently yields None and a row records "failed: None"."""
    detail = getattr(exc, "detail", None)
    detail = detail if isinstance(detail, dict) else {}
    return detail.get("code") or getattr(exc, "status_code", None)


def _app_exception_reason(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None)
    detail = detail if isinstance(detail, dict) else {}
    return f"{_app_exception_code(exc)}: {detail.get('message') or ''}".strip()


def _record_chat_failure(db, download_id: str, *, prefix: str, reason: str,
                         identifier: str, request_payload: dict,
                         exc: BaseException, log_outbox: bool, append: bool = False) -> None:
    """Leave a swallowed chat-delivery failure where an operator will find it: on the
    download row AND (when the sender did not already) in the outbox.

    Both halves are best-effort and each is tried on its own - a logging failure must never
    be why the export job dies, and one of the two landing beats neither.

    `append` keeps an EXISTING `error` (the render failure that started all this) and adds
    the notice failure after it, so the reason the report died is not overwritten by the
    reason the apology did.
    """
    from sqlalchemy import text as _text

    from app.services.integration_service import log_respond_send

    try:
        if append:
            db.execute(_text(
                "UPDATE user_downloads "
                "SET error = LEFT(COALESCE(error || ' | ', '') || :err, 500) "
                "WHERE id = :id"
            ), {"err": f"{prefix}: {reason}", "id": str(download_id)})
        else:
            db.execute(_text(
                "UPDATE user_downloads SET error = :err WHERE id = :id"
            ), {"err": f"{prefix}: {reason}"[:500], "id": str(download_id)})
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "generate_low_stock_report: could not record %s on download %s",
            prefix, download_id,
        )
        db.rollback()

    if not log_outbox:
        return
    try:
        log_respond_send(
            db,
            business_table="user_downloads",
            business_id=str(download_id),
            identifier=str(identifier),
            request_payload=request_payload,
            exc=exc,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "generate_low_stock_report: could not write the outbox row for %s", download_id
        )


def _claim_chat_delivery(db, download_id: str) -> Optional[str]:
    """The ONE-SHOT claim, shared by the push and the failure notice.

    `delivered_at IS NULL` in the predicate is what makes a retried RQ job harmless, and
    `deliver_to_contact_id IS NOT NULL` is what keeps this to rows the chat turn actually
    handed over. Returns the resolved `respond_contacts.id`, or None when this worker did
    not win the claim (the turn delivered it, or another attempt already fired).
    """
    from sqlalchemy import text as _text

    claimed = db.execute(_text(
        "UPDATE user_downloads SET delivered_at = now() "
        "WHERE id = :id AND deliver_to_contact_id IS NOT NULL AND delivered_at IS NULL "
        "RETURNING deliver_to_contact_id::text"
    ), {"id": str(download_id)}).scalar()
    db.commit()
    return claimed


def _respond_io_id_for(db, contact_id: str, download_id: str) -> Optional[str]:
    from sqlalchemy import text as _text

    respond_io_id = db.execute(_text(
        "SELECT respond_io_id FROM respond_contacts WHERE id = :c"
    ), {"c": contact_id}).scalar()
    if not respond_io_id:
        logger.warning(
            "generate_low_stock_report: contact %s has no respond_io_id; nothing sent "
            "for download %s", contact_id, download_id,
        )
    return respond_io_id


def _tell_chat_the_report_failed(db, download_id: str) -> None:
    """The low stock report's own wording, kept as its caller always had it."""
    _tell_chat_the_download_failed(db, download_id, text=LOW_STOCK_BUILD_FAILED_TEXT)


def _tell_chat_the_download_failed(db, download_id: str, *, text: str) -> None:
    """Reviewer item 1: the contact was told "it will be sent here when ready" - tell them
    when it never will be.

    With the wait capped at 7 s (the MCP transport timeout, defect A) a whole-book ask
    nearly always answers `pending` and claims delivery, so a render failure after that
    point leaves a promise nobody keeps: `_record_failure` marks the row `failed` and the
    push is never reached. This closes that hole with the SAME one-shot claim the push
    uses, so the contact hears exactly one of {the workbook, this apology} and a retried
    job repeats neither.

    Sent as TEXT via `send_chat_message_for` rather than the attachment path: that one has
    the closed-window template fallback, so a contact outside the 24 h window still hears.
    """
    from app.services import respond_chat_template_service
    from app.services.error_handler import AppException

    claimed = _claim_chat_delivery(db, download_id)
    if not claimed:
        return
    respond_io_id = _respond_io_id_for(db, claimed, download_id)
    if not respond_io_id:
        return

    payload = {"message": {"type": "text", "text": text}}
    try:
        respond_chat_template_service.send_chat_message_for(
            db,
            identifier=str(respond_io_id),
            respond_contact_id=str(claimed),
            text=text,
            chat_use_case="conversation_chat",
            business_table="user_downloads",
            business_id=str(download_id),
            sender_name="Sorento",
        )
    except AppException as e:
        code = _app_exception_code(e)
        logger.warning(
            "generate_low_stock_report: failure notice for download %s refused (%s)",
            download_id, code,
        )
        _record_chat_failure(
            db, download_id, prefix="chat notice failed",
            reason=_app_exception_reason(e), identifier=str(respond_io_id),
            request_payload=payload, exc=e,
            log_outbox=code in _PRE_LOG_REFUSAL_CODES, append=True,
        )
    except Exception as e:  # noqa: BLE001 - the export already failed; never raise here
        logger.exception(
            "generate_low_stock_report: failure notice for download %s failed", download_id
        )
        _record_chat_failure(
            db, download_id, prefix="chat notice failed",
            reason=f"{type(e).__name__}: {e}", identifier=str(respond_io_id),
            request_payload=payload, exc=e, log_outbox=True, append=True,
        )


def _push_low_stock_to_chat(db, download_id: str, *, provider: str, key: str) -> None:
    """The low stock report's name for the one push every chat-delivered download uses."""
    _push_download_to_chat(db, download_id, provider=provider, key=key)


def _push_download_to_chat(db, download_id: str, *, provider: str, key: str) -> None:
    """Push the finished workbook to the contact the chat turn handed it over to (AC-45).

    The turn CLAIMS delivery for the worker by writing `deliver_to_contact_id` when its own
    budget lapses; this claims the push BACK with one conditional UPDATE, and sends only
    when that update touches a row. Between them the outcome is exactly one of {the turn
    returned the file, the worker pushed it} (AC-46) - an unclaimed row was delivered
    inside the turn, and pushing it would send the same workbook twice. `delivered_at IS
    NULL` in the same predicate is what makes a RETRIED RQ job harmless.

    A refusal from Respond is SWALLOWED rather than raised: raising would poison the job
    for a file that rendered perfectly well, and the row stays `ready` either way - the
    workbook exists and is still in My Downloads.

    **But a swallowed failure must never be silent** (console round 4, measured: download
    96f3c045 read `status=ready, delivered_at set, error empty` with NO `integration_log`
    row at all - the row said delivered, the contact got nothing, and nothing anywhere said
    otherwise). The earlier claim that "the send is already recorded in the outbox by
    `log_respond_send`" is FALSE on the refusal path: `send_chat_attachment_for` checks the
    24 h window UPFRONT and raises `AppException(422, attachment_window_closed)` before it
    ever reaches its own logging. So the reason goes onto `user_downloads.error`, and an
    outbox row is written HERE for exactly the refusals that pre-empt the sender's own
    logging (`_PRE_LOG_REFUSAL_CODES`) - a 502 `respond_send_failed` is already logged by
    the sender, and writing a second row would double-count one send (reviewer item 5).

    `delivered_at` is deliberately LEFT SET. It means "this worker has taken its one shot",
    which is what makes delivery exactly-once against a retried RQ job; the `error` column
    is what says the shot missed.
    """
    from app.services import respond_chat_template_service
    from app.services.error_handler import AppException
    from app.services.scm import low_stock_report_service

    claimed = _claim_chat_delivery(db, download_id)
    if not claimed:
        return
    respond_io_id = _respond_io_id_for(db, claimed, download_id)
    if not respond_io_id:
        return

    url = low_stock_report_service.attachment_url(provider, key)
    # Security SF-2: the outbox payload carries the STORAGE KEY, never the URL. On R2 that
    # URL is unauthenticated and never expires (see `attachment_url`), and
    # `GET /api/v1/integrations/logs/` hands `request_payload` to any authenticated user,
    # so logging it would turn an operator's debug view into a link to the workbook. The
    # key is what an operator actually needs to find the object; anyone who may fetch it
    # can mint the URL from the key.
    #
    # NOT fixed here: `send_chat_attachment_for` builds its OWN `request_payload` from the
    # `url=` argument and logs that on success and on a 502. That row is written by the
    # shared sender every chat attachment in the product goes through, so redacting it is
    # a change to every caller, not to this lane - filed with the logs-route finding.
    payload = {"message": {"type": "attachment",
                           "attachment": {"type": "file", "url": "<stored>",
                                          "key": str(key)}}}

    try:
        respond_chat_template_service.send_chat_attachment_for(
            db,
            identifier=str(respond_io_id),
            respond_contact_id=str(claimed),
            attachment_type="file",
            url=url,
            business_table="user_downloads",
            business_id=str(download_id),
        )
    except AppException as e:
        code = _app_exception_code(e)
        logger.warning(
            "generate_low_stock_report: push for download %s refused (%s); the file is "
            "still ready in My Downloads", download_id, code,
        )
        _record_chat_failure(
            db, download_id, prefix="chat push failed",
            reason=_app_exception_reason(e), identifier=str(respond_io_id),
            request_payload=payload, exc=e,
            log_outbox=code in _PRE_LOG_REFUSAL_CODES,
        )
    except Exception as e:  # noqa: BLE001 - a broken send never fails a rendered export
        logger.exception(
            "generate_low_stock_report: push for download %s failed", download_id
        )
        _record_chat_failure(
            db, download_id, prefix="chat push failed",
            reason=f"{type(e).__name__}: {e}", identifier=str(respond_io_id),
            request_payload=payload, exc=e, log_outbox=True,
        )


def generate_low_stock_report(download_id: str, run_id: str, user_id: str, *,
                              include_supplier: bool = True, split: str = "none",
                              suppliers: Optional[list] = None,
                              categories: Optional[list] = None,
                              brand_ids: Optional[list] = None) -> dict:
    """Render the run's low stock workbook, store it, and update the download row.

    PLAN-low-stock-report S3 (AC-36; `split` added PLAN-low-stock-export-split-25sep,
    AC-14). `generate_order_sheet`'s twin, down to the company dance: the worker has NO
    request-scoped company, so the run row is read under NO scope (that row is the one
    thing that states which company the export belongs to), its own company is adopted
    before anything company-scoped is touched, and the caller's scope is restored in
    `finally` - a synchronous caller whose session this reuses did not ask to have its
    scope changed underneath it.

    Three things it does that the order sheet does not:

    * `row_count_low` / `row_count_all` are stamped onto the download row at `mark_ready`,
      so S5's chat turn can answer "Low: 12 of 340 planned products" without opening the
      workbook on the request thread (AC-43). They are counted through the same `_split`
      the sheets are built from, so the figures are the workbook's own.
    * `include_supplier=False` (S5, for a contact without the `purchase_orders.supplier`
      reveal key) drops the Supplier column from both sheets.
    * `split` (default `"none"`, the chat route never sends one) is forwarded to
      `export_low_stock` unchanged. The route's own schema already refused an unknown
      value before this job was enqueued; a supplier split without the Supplier column is
      a combination the route can never produce (it never sends `include_supplier=False`,
      only the chat route does, and that route never sends a split), so the check for it
      lives inside `export_low_stock` itself (R5) rather than here.
    * `suppliers` / `categories` (PLAN-excel-preview-26sep AC-6) are the page's filters,
      forwarded only when set, so the chat route's call shape is unchanged.

    `_record_failure` on any exception, never raising into RQ: a poisoned job retries for
    ever and the buyer's row sits `processing` until it goes stale.
    """
    db = SessionLocal()
    if brand_ids:
        # CONTACT-BRAND-SCOPE: the requesting contact's brands; the file holds only their products.
        from app.models.base import set_brand_scope

        set_brand_scope(db, frozenset(str(b) for b in brand_ids))
    from app.models.base import UNSET, get_company_scope
    from app.models.scm import ReorderRun
    from app.services.scm.reorder_run_service import _adopt_run_company_scope

    caller_scope = get_company_scope(db)
    set_company_scope(db, None)
    run = db.get(ReorderRun, run_id)
    if run is not None:
        _adopt_run_company_scope(db, run)
    if run is None or not getattr(run, "company_id", None):
        # AC-A10 twin (security should-fix, fix round 3): the same fail-closed change
        # `generate_order_sheet` got - a run that does not exist, or whose OWN
        # `company_id` is NULL, must not export under the `None` scope set two lines up.
        # UNSET fails closed instead: `ReorderRun` is itself company-scoped, so
        # `low_stock_report_service.export_low_stock`'s own run lookup finds nothing and
        # the export fails rather than reading every company's rows.
        set_company_scope(db, UNSET)
        logger.warning(
            "generate_low_stock_report: run %s not found or has no company; "
            "failing closed rather than exporting under no company scope", run_id
        )
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        row = svc.get(download_id)
        filename = row.filename if row else None

        from app.services.scm import low_stock_report_service

        # The counts come back WITH the bytes (reviewer item 4) - the builder already has
        # both row sets, and a second `row_counts()` call re-serialised the whole frozen
        # run on the worker. `split` is always forwarded, `"none"` included (AC-14) - the
        # task states its own contract plainly rather than varying its call shape by value.
        filters = {}
        if suppliers:
            filters["suppliers"] = list(suppliers)
        if categories:
            filters["categories"] = list(categories)
        file_bytes, content_type, fallback_filename, counts = (
            low_stock_report_service.export_low_stock(
                db, run_id=run_id, include_supplier=include_supplier, split=split,
                **filters,
            )
        )
        filename = filename or fallback_filename

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/low-stock/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=content_type,
        )

        # reviewer S3: status and counts flip together, one transaction - a poll never
        # sees `ready` with NULL counts.
        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
            row_count_low=counts["low"],
            row_count_all=counts["all"],
        )
        logger.info(
            "generate_low_stock_report: download %s ready (%d bytes, %d low of %d, "
            "split=%s, %s sheets)",
            download_id, len(file_bytes), counts["low"], counts["all"], split,
            counts.get("sheets", 2),
        )
        ready = {"download_id": download_id, "status": "ready", "bytes": len(file_bytes),
                  "row_count_low": counts["low"], "row_count_all": counts["all"]}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_low_stock_report failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_low_stock_report")
        # Reviewer item 1: if the chat turn already answered `pending` and claimed
        # delivery, the contact is waiting for a file that is never coming. Tell them.
        # Only fires on a CLAIMED row, through the same one-shot claim the push uses, so
        # a plan-view export (nobody waiting) sends nothing.
        _tell_chat_the_report_failed(db, download_id)
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    else:
        # Security N-d: the push sits OUTSIDE the try. Inside it, a DB blip in the claim
        # update was caught by the `except` above, which then marked a download that had
        # just been stored and flipped to `ready` as `failed` and texted the contact an
        # apology for a file that exists. The push has its own belt and braces already -
        # every send failure is swallowed and recorded by `_record_chat_failure` - so
        # nothing here needs the outer handler.
        _push_low_stock_to_chat(db, download_id, provider=provider, key=stored_key)
        return ready
    finally:
        set_company_scope(db, caller_scope)
        db.close()


#: The workbook `openpyxl` produces for both order-inquiry exports below - same media
#: type the existing sync `GET /order-inquiries/export` route answers with.
_OI_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

def generate_order_inquiry_xlsx(download_id: str, inquiry_id: str, user_id: str) -> dict:
    """Render one OI header's own workbook (Lane B, AC-B1/AC-B2), store it, update the
    download row. Mirrors `generate_order_sheet` line for line: `mark_processing`,
    render, upload, `mark_ready` - `_record_failure` on any exception, never raising
    into RQ.

    The header names its own company (`OrderInquiry.company_id`), so - exactly the
    `_adopt_run_company_scope` shape `generate_order_sheet` uses for a reorder run -
    it is read under NO scope first (the row itself is what states the company), then
    that company is adopted before the render touches anything company-scoped.

    Security review fix round 2, item 2 (mirrors `generate_order_sheet`'s own fix,
    Lane A fix round 2, sha 002fd3d2e on `fix/order-sheet-cells`): a header that is
    missing, or carries a NULL `company_id` (should not exist post-isolation, but the
    fail-closed rule is never assumed away), sets the scope to `UNSET` explicitly -
    never left at the `None` (all-companies) scope used to look the header up - and
    the render is refused outright rather than silently producing an empty workbook
    under that `None` scope, which `OrderInquiryWorklistService.export_xlsx` would
    otherwise do without raising.
    """
    db = SessionLocal()
    from app.models.base import UNSET, get_company_scope
    from app.models.project_so import OrderInquiry

    caller_scope = get_company_scope(db)
    set_company_scope(db, None)
    header = db.get(OrderInquiry, inquiry_id)
    company_id = getattr(header, "company_id", None) if header is not None else None
    if company_id:
        set_company_scope(db, frozenset({str(company_id)}))
    else:
        set_company_scope(db, UNSET)
        logger.warning(
            "generate_order_inquiry_xlsx: order inquiry %s not found or carries no "
            "company; refusing to export", inquiry_id,
        )
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        if not company_id:
            raise ValueError(
                f"Order inquiry {inquiry_id} could not be found or carries no "
                "company; refusing to export."
            )
        row = svc.get(download_id)
        filename = row.filename if row else None

        from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService

        fallback_filename, file_bytes = OrderInquiryWorklistService(db).export_xlsx(
            inquiry_id=inquiry_id,
        )
        filename = filename or fallback_filename

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/order-inquiry/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=_OI_XLSX_CONTENT_TYPE,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_order_inquiry_xlsx: download %s ready (%d bytes)",
            download_id, len(file_bytes),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(file_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_order_inquiry_xlsx failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_order_inquiry_xlsx")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        set_company_scope(db, caller_scope)
        db.close()


def generate_order_inquiry_worklist_xlsx(
    download_id: str, filters: dict, user_id: str, *, company_id: Optional[str] = None,
) -> dict:
    """Render the OI worklist's filtered workbook (Lane B, AC-B2/AC-B6), store it,
    update the download row. Same shape as `generate_order_inquiry_xlsx` above, minus
    the single header to read a company off: the worklist export names no header a
    task could adopt a company from, so the enqueuing request's own single-company
    scope travels as `company_id`, snapshotted at enqueue time - the same shape
    `generate_promotions_pdf`'s own `company_id` param uses.

    No `company_id` (a direct call, or a caller with no single-company scope) sets
    the scope to `UNSET` explicitly - review fix round 4, matching
    `generate_order_inquiry_xlsx`'s own no-company branch - never merely left at
    whatever the session already carried: no rows, fail-closed, same rule every
    other export task in this module follows.
    """
    db = SessionLocal()
    from app.models.base import UNSET, get_company_scope

    caller_scope = get_company_scope(db)
    if company_id:
        set_company_scope(db, frozenset({str(company_id)}))
    else:
        set_company_scope(db, UNSET)
        logger.warning(
            "generate_order_inquiry_worklist_xlsx: download %s carries no company "
            "scope; export runs under no company", download_id,
        )
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        row = svc.get(download_id)
        filename = row.filename if row else None

        from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService

        fallback_filename, file_bytes = OrderInquiryWorklistService(db).export_xlsx(
            **(filters or {}),
        )
        filename = filename or fallback_filename

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/order-inquiry-worklist/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=_OI_XLSX_CONTENT_TYPE,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_order_inquiry_worklist_xlsx: download %s ready (%d bytes)",
            download_id, len(file_bytes),
        )
        return {"download_id": download_id, "status": "ready", "bytes": len(file_bytes)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception(
            "generate_order_inquiry_worklist_xlsx failed for download %s", download_id
        )
        _record_failure(db, svc, download_id, e, "generate_order_inquiry_worklist_xlsx")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        set_company_scope(db, caller_scope)
        db.close()


def generate_stock_debt_xlsx(
    download_id: str, user_id: str, params: dict, *, company_id: Optional[str] = None,
) -> dict:
    """Render the Stock Debt workbook for the board's own filters, store it, update the
    download row (PLAN-stock-debt-filters-totals-export-24sep.md, AC-12b).

    Same shape as `generate_order_inquiry_worklist_xlsx` above - a filtered export with no
    run or header of its own to adopt a company from, so the enqueuing request's own
    single-company scope travels as `company_id`, snapshotted at enqueue time
    (`export_stock_debt`'s own `acting_company_id(db)` call).

    A DIRECT call with no `company_id` (this module's own tests, which reuse the seeding
    session's already-resolved scope) leaves the session's scope exactly as it found it,
    rather than forcing `UNSET` the way the OI worklist twin does: a brand-new worker
    session already defaults to fail-closed on its own (`get_company_scope`'s own "absent
    key => UNSET"), so there is nothing this branch needs to enforce that resetting an
    ALREADY-resolved scope would not simply break.

    `row_count` / `sheet_count` (the export's own `counts` tuple) are stamped at
    `mark_ready` in the SAME transaction as the status - the generic pair
    (`516_low_stock_report`'s `row_count_low`/`row_count_all` twin), because this export's
    sheet count varies with its own `split` rather than being a fixed two.

    `_record_failure` on any exception, never raising into RQ: a poisoned job retries for
    ever and the buyer's row sits `processing` until it goes stale.
    """
    db = SessionLocal()
    from app.models.base import get_company_scope

    caller_scope = get_company_scope(db)
    if company_id:
        set_company_scope(db, frozenset({str(company_id)}))
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)
        row = svc.get(download_id)
        filename = row.filename if row else None

        from app.services.scm.stock_debt_service import StockDebtService

        file_bytes, content_type, fallback_filename, counts = StockDebtService(db).export(
            **(params or {}),
        )
        filename = filename or fallback_filename

        provider = default_provider()
        backend = get_backend(provider)
        key = f"exports/stock-debt-xlsx/{download_id}/{filename}"
        stored_key, _signed = backend.upload_file(
            file_content=file_bytes,
            file_path=key,
            content_type=content_type,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
            row_count=counts["rows"],
            sheet_count=counts["sheets"],
        )
        logger.info(
            "generate_stock_debt_xlsx: download %s ready (%d bytes, %d rows, %d sheets)",
            download_id, len(file_bytes), counts["rows"], counts["sheets"],
        )
        return {
            "download_id": download_id, "status": "ready", "bytes": len(file_bytes),
            "row_count": counts["rows"], "sheet_count": counts["sheets"],
        }
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_stock_debt_xlsx failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_stock_debt_xlsx")
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    finally:
        set_company_scope(db, caller_scope)
        db.close()
