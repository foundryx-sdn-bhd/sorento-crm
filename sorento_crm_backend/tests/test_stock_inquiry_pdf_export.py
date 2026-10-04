"""Tests for the product inquiry PDF export pipeline (My Downloads).

Mirrors test_complaint_pdf_export.py: the download row lifecycle is deterministic,
the WeasyPrint render is skipped when the host lacks the native libs.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models.download import DownloadStatus
from app.models.procurement import StockInquiry
from app.services.download_service import DownloadService
from app.services.stock_inquiry_pdf_service import (
    PDFRenderingUnavailable,
    StockInquiryPDFService,
)


@pytest.fixture(autouse=True)
def _clean_state():
    # Wipe BOTH sides of the test: this suite runs against the shared dev database,
    # so leaving seeded rows behind puts fake product inquiries in the real UI.
    def _wipe():
        with engine.connect() as conn:
            for sql in (
                "DELETE FROM user_downloads WHERE kind = 'stock_inquiry_pdf' AND (user_id LIKE 'sipdftest-%' OR filename LIKE 'product-inquiry-SIPDF-%')",
                "DELETE FROM stock_inquiries WHERE inquiry_number LIKE 'SIPDF-%'",
            ):
                try:
                    conn.execute(text(sql))
                    conn.commit()
                except Exception:
                    conn.rollback()

    _wipe()
    yield
    _wipe()


@pytest.fixture
def db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_inquiry(db: Session) -> StockInquiry:
    row = StockInquiry(
        id=str(uuid.uuid4()),
        inquiry_number=f"SIPDF-{uuid.uuid4().hex[:6]}",
        status="pending_purchasing",
        salesperson="CONTACT Y",
        product_code="SRTWC8518-SH",
        item_description="One piece water closet",
        project_customer="ECO WORLD TRADING SDN BHD (PROJECT)",
        project_name="Eco Ardence Lyna",
        quantity="12",
        delivery_date="30/08/2026",
        remark="Site needs the full set.",
        purchasing_response="Stock available, 2 weeks lead time.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_download_lifecycle(db: Session) -> None:
    svc = DownloadService(db)
    uid = f"sipdftest-{uuid.uuid4().hex[:6]}"
    inquiry_id = str(uuid.uuid4())
    row = svc.create(
        user_id=uid,
        kind="stock_inquiry_pdf",
        source_entity_type="stock_inquiry",
        source_entity_id=inquiry_id,
    )
    assert row.status == DownloadStatus.PENDING.value

    svc.mark_ready(
        row.id,
        storage_provider="s3",
        storage_key=f"exports/product-inquiry-pdf/{row.id}/product-inquiry-X.pdf",
    )
    ready = svc.get(row.id)
    assert ready.status == DownloadStatus.READY.value
    assert ready.storage_key.endswith(".pdf")

    # The Print Count column reads exactly this map, scoped to the viewing user.
    counts = svc.count_map_for_user(uid, "stock_inquiry", [inquiry_id])
    assert counts.get(inquiry_id) == 1
    assert svc.count_map_for_user("someone-else", "stock_inquiry", [inquiry_id]) == {}


def test_render_pdf_produces_pdf_bytes(db: Session) -> None:
    row = _seed_inquiry(db)
    try:
        pdf_bytes, filename = StockInquiryPDFService(db).render_pdf(row.id)
    except PDFRenderingUnavailable as e:
        pytest.skip(f"WeasyPrint native libs unavailable on host: {e}")
        return
    assert pdf_bytes[:5] == b"%PDF-"
    assert filename == f"product-inquiry-{row.inquiry_number}.pdf"
    assert len(pdf_bytes) > 1000


def test_html_mirrors_the_on_screen_form(db: Session) -> None:
    row = _seed_inquiry(db)
    html = StockInquiryPDFService(db)._html(row)
    # Document title + every row label the detail page renders.
    # The document keeps the form heading the detail page prints...
    assert "Product Inquiry Form" in html
    # ...while the row labels mirror the on-screen wording.
    for label in (
        "Stock Inquiry Number",
        "Sales Person",
        "Product Code",
        "Item Description",
        "Project Customer",
        "Project Name",
        "Qty",
        "Delivery Date",
        "Remark",
        "Additional Remark",
        "Comment / Reply by Purchasing",
    ):
        assert label in html, label
    assert row.inquiry_number in html
    assert "One piece water closet" in html
    assert "Stock available, 2 weeks lead time." in html
    # Status prints as the same label the pill shows.
    assert "Pending purchasing" in html
    # Attachment sections always render, with explicit empty states.
    assert "No photo attachments." in html
    assert "Other Attachments" in html


def test_optional_reason_rows_only_render_when_set(db: Session) -> None:
    row = _seed_inquiry(db)
    svc = StockInquiryPDFService(db)
    assert "Rejection Reason" not in svc._html(row)

    row.rejection_reason = "Duplicate of SI26-0100"
    db.commit()
    html = svc._html(row)
    assert "Rejection Reason" in html
    assert "Duplicate of SI26-0100" in html


def test_build_filename_sanitizes(db: Session) -> None:
    row = _seed_inquiry(db)
    name = StockInquiryPDFService(db).build_filename(row)
    assert name.startswith("product-inquiry-SIPDF-")
    assert name.endswith(".pdf")


def test_stale_pending_rows_are_failed_on_read(db: Session) -> None:
    """A job that dies before the task body runs must not spin "Queued" forever.

    Reproduces the real incident: a worker booted from another worktree drained
    this stack's queue and died in ``import_attribute``, so nothing ever marked
    the row failed and the drawer showed a permanent spinner.
    """
    from datetime import timedelta

    from app.services.download_service import _STALE_AFTER, _utc_naive_now

    svc = DownloadService(db)
    uid = f"sipdftest-{uuid.uuid4().hex[:6]}"
    stuck = svc.create(user_id=uid, kind="stock_inquiry_pdf", filename="stuck.pdf")
    fresh = svc.create(user_id=uid, kind="stock_inquiry_pdf", filename="fresh.pdf")

    # Age one row past the cutoff; leave the other just-created.
    stuck.created_at = _utc_naive_now() - (_STALE_AFTER + timedelta(minutes=1))
    db.commit()

    rows = {r.filename: r for r in svc.list_for_user(uid)}
    assert rows["stuck.pdf"].status == DownloadStatus.FAILED.value
    assert "never completed" in (rows["stuck.pdf"].error or "")
    # A job still within its timeout window is left alone.
    assert rows["fresh.pdf"].status == DownloadStatus.PENDING.value


def test_stale_sweep_leaves_finished_rows_untouched(db: Session) -> None:
    from datetime import timedelta

    from app.services.download_service import _STALE_AFTER, _utc_naive_now

    svc = DownloadService(db)
    uid = f"sipdftest-{uuid.uuid4().hex[:6]}"
    done = svc.create(user_id=uid, kind="stock_inquiry_pdf", filename="done.pdf")
    svc.mark_ready(done.id, storage_provider="r2", storage_key="exports/x/done.pdf")
    done = svc.get(done.id)
    done.created_at = _utc_naive_now() - (_STALE_AFTER + timedelta(hours=2))
    db.commit()

    svc.fail_stale(uid)
    assert svc.get(done.id).status == DownloadStatus.READY.value
