"""OI-RESEND-PURCHASING, owner ruling 16 Oct 00:10: the handover email carries the OI's own
Excel export (the workbook the detail page's Export Excel makes), on the automatic confirm
send AND on "Send to purchasing", through ONE shared build.

TEST-FIRST. Storage is stubbed (`stored`): nothing leaves the sandbox. Dispatch is captured.
"""
from __future__ import annotations

import pytest

from app.models.project_so import OrderInquiry

from .test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from .test_order_inquiry_handshake import _raise_one_row, api, world  # noqa: F401 - fixtures

__all__ = ["api", "world"]


def _drain_on_test_connection(monkeypatch, world) -> None:
    """The post-commit drain opens its own `SessionLocal()`, which in production sees the
    committed write. Here the write lives in the test's never-committed transaction, so the
    drain's session is bound to that same connection (a savepoint) to read it."""
    from sqlalchemy.orm import Session

    connection = world.db.connection()
    monkeypatch.setattr(
        "app.database.SessionLocal",
        lambda: Session(bind=connection, join_transaction_mode="create_savepoint"),
    )


@pytest.fixture()
def stored(monkeypatch):
    """Every upload the handover makes, kept in memory instead of S3/R2."""
    uploads: list[dict] = []

    class _Backend:
        def upload_file(self, *, file_content, file_path, content_type=None, **_kw):
            uploads.append({"bytes": file_content, "key": file_path, "type": content_type})
            return file_path, None

    monkeypatch.setattr("app.services.storage_router.get_backend", lambda provider=None: _Backend())
    return uploads


def test_confirm_handover_attaches_the_oi_excel(api, monkeypatch, stored):
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    _drain_on_test_connection(monkeypatch, world)

    fixture = _raise_one_row(api, qty="10")

    inquiry = world.db.get(OrderInquiry, fixture["row"].order_inquiry_id)
    [call] = _handover_calls(calls)
    excel = [
        a for a in call["context"].get("email_attachments", [])
        if a["filename"] == f"{inquiry.inquiry_no}.xlsx"
    ]
    assert len(excel) == 1, call["context"].get("email_attachments")
    assert excel[0]["optional"] is True
    [upload] = [u for u in stored if u["key"] == excel[0]["storage_key"]]
    assert upload["bytes"][:2] == b"PK"  # an xlsx is a zip


def test_confirm_handover_still_sends_when_the_excel_cannot_be_stored(api, monkeypatch):
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)

    class _Down:
        def upload_file(self, **_kw):
            raise RuntimeError("storage down")

    monkeypatch.setattr("app.services.storage_router.get_backend", lambda provider=None: _Down())
    _drain_on_test_connection(monkeypatch, world)

    _raise_one_row(api, qty="10")

    [call] = _handover_calls(calls)
    assert not any(
        a["filename"].endswith(".xlsx") for a in call["context"].get("email_attachments", [])
    )
