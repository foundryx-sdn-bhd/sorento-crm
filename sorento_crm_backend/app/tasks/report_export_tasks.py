"""Background task for report workbook exports (My Downloads).

Same shape as the other exports in app/tasks/export_tasks.py: the API creates a
UserDownload row in 'pending' and enqueues this; the task renders, uploads through the
storage router and flips the row to 'ready' or 'failed'. Never raises into RQ's failed
registry - a row stuck in 'processing' tells the user nothing.

The export path runs the engine UNCAPPED (``run_workbook`` never caps): the caps exist to
keep a runaway run off the request path, and this is not the request path. What comes back
is the SUMMARY plus one detail table per month of the period, which is the shape the client
keeps by hand.

RQ has no reload: restart the worker after editing this file.
"""
import logging
from typing import Optional

from app.database import SessionLocal
from app.services.company_scope import set_company_scope
from app.services.download_service import DownloadService
from app.services.storage_router import default_provider, get_backend
# The other exports' rollback-then-mark-failed rule, not a second copy of it: when the
# thing that failed was the DATABASE, psycopg2 leaves the transaction aborted and
# `mark_failed` would raise too, leaving the row spinning in 'processing' for good.
from app.tasks.export_tasks import _record_failure

logger = logging.getLogger(__name__)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: A job enqueued with no company grant (one queued before the grant travelled with the
#: job). A company-scoped report then reads NOBODY'S rows: the fail-closed answer.
NO_COMPANY_SNAPSHOT = "__no_company_snapshot__"

#: What the chat hears when the Excel it was promised cannot be built (AC-R4-4).
REPORT_BUILD_FAILED_TEXT = "Could not build the sales report Excel right now."


def generate_report_xlsx(
    download_id: str,
    key: str,
    params: dict,
    view: Optional[dict],
    user_id: str,
    company_grants=NO_COMPANY_SNAPSHOT,
    brand_ids=None,
) -> dict:
    """Render a report to a workbook, store it, and update the download row.

    The session runs at the all-companies scope, which is correct for every dataset
    declaring ``scope="none"``. A ``scope="company"`` dataset reads the ENQUEUER's grant,
    which the route snapshots into ``company_grants`` (a list of company ids, or None for
    the all-companies system caller): the worker has no request to resolve one from. A
    job with no snapshot reads nothing (fail closed).

    A download the chat turn handed over (``deliver_to_contact_id`` set, the chatbot's
    sales answer) is pushed to that contact once it is ready, or the contact is told in
    text that it could not be built; for any other download both are no-ops.
    """
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine, registry as reg
    from app.services.reports.xlsx_renderer import render_workbook

    db = SessionLocal()
    set_company_scope(db, None)
    if brand_ids:
        # CONTACT-BRAND-SCOPE: the requesting contact's brands (a fresh session carries none).
        from app.models.base import set_brand_scope

        set_brand_scope(db, frozenset(str(b) for b in brand_ids))
    svc = DownloadService(db)
    try:
        svc.mark_processing(download_id)

        definition = reg.get(key)
        if definition is None:
            raise ValueError(f"Unknown report '{key}'")

        config = ReportViewConfig.model_validate(view) if view else engine.view_config(definition)
        grants = frozenset() if company_grants == NO_COMPANY_SNAPSHOT else company_grants
        data = engine.run_workbook(db, definition, params or {}, config, company_grants=grants)
        content = render_workbook(definition, data)

        # The route already named the file from the period the user asked for; re-deriving
        # it here would be a second place for that name to be decided, and to drift.
        row = svc.get(download_id)
        filename = (row.filename if row else None) or f"{definition.key}.xlsx"

        provider = default_provider()
        backend = get_backend(provider)
        stored_key, _signed = backend.upload_file(
            file_content=content,
            file_path=f"exports/report-xlsx/{download_id}/{filename}",
            content_type=_XLSX_MIME,
        )

        svc.mark_ready(
            download_id,
            storage_provider=provider,
            storage_key=stored_key,
            filename=filename,
        )
        logger.info(
            "generate_report_xlsx: download %s ready (%s, %d bytes)",
            download_id,
            key,
            len(content),
        )
        outcome = {"download_id": download_id, "status": "ready", "bytes": len(content)}
    except Exception as e:  # noqa: BLE001 - mark failed, never poison the queue
        logger.exception("generate_report_xlsx failed for download %s", download_id)
        _record_failure(db, svc, download_id, e, "generate_report_xlsx")
        try:
            from app.tasks import export_tasks

            export_tasks._tell_chat_the_download_failed(
                db, download_id, text=REPORT_BUILD_FAILED_TEXT
            )
        except Exception:  # noqa: BLE001 - the export already failed; never raise here
            logger.exception("generate_report_xlsx: failure notice for %s failed", download_id)
        return {"download_id": download_id, "status": "failed", "error": str(e)}
    else:
        # OUTSIDE the render's try (reviewer nit): a push that fails must never mark a
        # rendered, ready file as failed or tell the chat it could not be built. The push
        # swallows and records its own failures; this guard is only belt and braces.
        try:
            from app.tasks import export_tasks

            export_tasks._push_download_to_chat(db, download_id, provider=provider, key=stored_key)
        except Exception:  # noqa: BLE001
            logger.exception("generate_report_xlsx: chat push for %s failed", download_id)
        return outcome
    finally:
        db.close()
