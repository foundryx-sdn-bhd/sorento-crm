"""RQ task: build the folder bulk download ZIP and hand it to My downloads."""
import logging
import os
import tempfile
from datetime import datetime

from app.database import SessionLocal
from app.models.base import set_company_scope
from app.models.job import ImportJob, JobStatus
from app.services.attachment_bulk_download_service import (
    archive_filename,
    plan_archive,
    write_archive,
)
from app.services.download_service import DownloadService
from app.services.job_service import JobService
from app.services.storage_router import default_provider, extract_key, get_backend

logger = logging.getLogger(__name__)


def process_attachment_bulk_download(db_job_id: str):
    """Plan under the requester's company scope snapshot, write the zip to a temp file,
    upload it with a multipart stream and flip the job and its My downloads row."""
    db = SessionLocal()
    tmp_path = None
    job_service = JobService(db)
    download_service = DownloadService(db)
    download_id = None
    job_id_str = None
    try:
        job = job_service.get_job_by_db_id(db_job_id) if db_job_id else None
        if not job:
            logger.error("Attachment bulk download job not found: db_job_id=%s", db_job_id)
            return
        job_id_str = str(job.job_id)
        md = dict(job.job_metadata or {})
        download_id = md.get("download_id")

        # The FULL snapshot taken at enqueue: None = all companies, a list = exactly those.
        # A missing key fails closed (empty set), it never widens.
        if "company_scope" in md:
            snap = md["company_scope"]
            set_company_scope(db, None if snap is None else frozenset(str(c) for c in snap))
        else:
            set_company_scope(db, frozenset())

        def cancelled() -> bool:
            db.refresh(job)
            return job.status == JobStatus.CANCELLED.value

        def cancel_exit() -> None:
            if download_id:
                download_service.mark_failed(download_id, "Download cancelled")

        if cancelled():
            cancel_exit()
            return

        job_service.start_job(job_id_str)
        if download_id:
            download_service.mark_processing(download_id)

        plan = plan_archive(
            db,
            directory_id=md.get("directory_id"),
            folder_ids=list(md.get("folder_ids") or []),
            attachment_ids=list(md.get("attachment_ids") or []),
        )
        files = plan.files
        job_service.update_job_progress(job_id_str, total_rows=len(files))

        def read_bytes(att):
            return get_backend(att.storage_provider or "s3").download_file(extract_key(att.file_path))

        should_stop = cancelled

        def on_progress(n: int) -> None:
            job_service.update_job_progress(job_id_str, processed_rows=n)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
            stats = write_archive(plan, read_bytes, tmp, on_progress=on_progress, should_stop=should_stop)

        if stats.cancelled or cancelled():
            cancel_exit()
            return
        if stats.files_included == 0:
            job_service.fail_job(job_id_str, "No file could be read")
            if download_id:
                download_service.mark_failed(download_id, "No file could be read")
            return

        if cancelled():
            cancel_exit()
            return

        provider = default_provider()
        key = f"bulk-exports/{job.id}.zip"
        with open(tmp_path, "rb") as fh:
            get_backend(provider).upload_fileobj(fh, key, content_type="application/zip")

        if cancelled():
            try:
                get_backend(provider).delete_file(key)
            except Exception:  # noqa: BLE001
                logger.warning("Could not remove cancelled archive object %s", key)
            cancel_exit()
            return

        name = job.filename or archive_filename(None, datetime.utcnow())
        if download_id:
            try:
                download_service.mark_ready(
                    download_id, storage_provider=provider, storage_key=key, filename=name
                )
            except Exception:
                # The object is uploaded but no row will ever point at it: remove it now.
                try:
                    get_backend(provider).delete_file(key)
                except Exception:  # noqa: BLE001
                    logger.warning("Could not remove orphan archive object %s", key)
                raise
        job_service.complete_job(
            job_id_str,
            result={
                "archive_filename": name,
                "files_included": stats.files_included,
                "files_failed": stats.files_failed,
                "files_not_visible": plan.files_not_visible,
                "folders": plan.folders,
                "bytes": stats.bytes,
                "download_id": download_id,
            },
            successful_rows=stats.files_included,
            failed_rows=stats.files_failed,
            processed_rows=len(files),
            total_rows=len(files),
        )
    except Exception as exc:  # noqa: BLE001 - every failure ends in exactly one terminal state
        logger.exception("Attachment bulk download failed: db_job_id=%s", db_job_id)
        try:
            db.rollback()
            if job_id_str:
                job_service.fail_job(job_id_str, str(exc))
            if download_id:
                download_service.mark_failed(download_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Could not record bulk download failure: db_job_id=%s", db_job_id)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        db.close()
