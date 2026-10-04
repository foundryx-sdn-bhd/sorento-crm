"""LOWSTOCK-FILTER-ASK: the chat route forwards the settled filters to the workbook job.

`documentation/plans/chatbot/PLAN-lowstock-filter-ask-2oct.md`, behaviour card Q1 (a): the
category / supplier filters and the group-by apply to the WORKBOOK, through the same
`categories` / `suppliers` / `split` the in-app page already sends to
`generate_low_stock_report`. Before this lane the chat route never sent any of them, so a
"water tap low stock list" came back as the whole book.

A supplier filter or a supplier split for a contact WITHOUT `purchase_orders.supplier` is
refused BEFORE anything is created (no run, no download row, no job): the supplier column
is hidden from that contact, and a sheet title or a filtered row set would leak what the
column hides. The lane never offers it; this is the route's own defence.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.scm.conftest import requires_pg
from tests.scm.test_low_stock_report_chat import (  # noqa: F401
    GRANT_KEY,
    ROUTE,
    SUPPLIER_KEY,
    _api_key_caller,
    _contact,
    _counts,
    _fake_queue,
    _no_cloud_credentials_needed,
    _params,
    _patch_wait,
    _download_row,
    _timeout,
)

pytestmark = requires_pg


def _export_kwargs(calls: list[dict]) -> dict:
    assert len(calls) == 2, f"expected the run job then the export job, got {calls}"
    return calls[1]["kwargs"]


def test_filters_and_split_reach_the_export_job(scm_app, monkeypatch):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(
            contact,
            categories=["SRT-FT", "SRT-WT"],
            suppliers=["JINBAICHUAN"],
            split="supplier",
        ))

    assert resp.status_code == 200, resp.text
    kwargs = _export_kwargs(calls)
    assert kwargs.get("categories") == ["SRT-FT", "SRT-WT"], kwargs
    assert kwargs.get("suppliers") == ["JINBAICHUAN"], kwargs
    assert kwargs.get("split") == "supplier", kwargs


def test_csv_filters_are_split_like_the_other_list_params(scm_app, monkeypatch):
    """The MCP compiler may send a list as one csv value; `warehouse_codes` already
    accepts that (`_csv_list`), and the new lists must too."""
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(
            contact, categories="SRT-FT,SRT-WT", split="category",
        ))

    assert resp.status_code == 200, resp.text
    kwargs = _export_kwargs(calls)
    assert kwargs.get("categories") == ["SRT-FT", "SRT-WT"], kwargs
    assert kwargs.get("split") == "category", kwargs


def test_a_supplier_name_with_a_comma_stays_one_supplier(scm_app, monkeypatch):
    """Lists arrive as repeated params (the MCP compiler keeps a list a list); a supplier
    NAME is never split on its commas."""
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(
            contact, suppliers=["FOSHAN SANITARY CO., LTD", "JINBAICHUAN"],
        ))

    assert resp.status_code == 200, resp.text
    assert _export_kwargs(calls).get("suppliers") == ["FOSHAN SANITARY CO., LTD", "JINBAICHUAN"]


def test_no_filters_keeps_the_old_call_shape(scm_app, monkeypatch):
    """A bare call (every filter settled as "all") is today's two-sheet workbook."""
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact))

    assert resp.status_code == 200, resp.text
    kwargs = _export_kwargs(calls)
    assert kwargs.get("split", "none") == "none", kwargs
    assert not kwargs.get("categories"), kwargs
    assert not kwargs.get("suppliers"), kwargs


def test_unknown_split_is_refused_before_anything_is_created(scm_app, monkeypatch):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)
    before = _counts(db)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact, split="brand"))

    assert resp.status_code == 422, resp.text
    assert calls == []
    assert _counts(db) == before


@pytest.mark.parametrize("extra", [
    {"split": "supplier"},
    {"split": "supplier_category"},
    {"suppliers": ["JINBAICHUAN"]},
])
def test_supplier_filter_or_split_without_the_supplier_key_creates_nothing(
        scm_app, monkeypatch, extra):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY,))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)
    before = _counts(db)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact, **extra))

    assert resp.status_code == 200, resp.text
    assert resp.json().get("status") == "error", resp.json()
    assert calls == [], "nothing may be enqueued"
    assert _counts(db) == before, "no run and no download row may be created"


def test_category_split_without_the_supplier_key_is_allowed(scm_app, monkeypatch):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY,))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(
            contact, split="category", categories=["SRT-FT"],
        ))

    assert resp.status_code == 200, resp.text
    kwargs = _export_kwargs(calls)
    assert kwargs.get("split") == "category"
    assert kwargs.get("categories") == ["SRT-FT"]
    assert kwargs.get("include_supplier") is False


def test_a_supplier_name_longer_than_the_column_is_refused(scm_app, monkeypatch):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    calls = _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact, suppliers=["X" * 256]))

    assert resp.status_code == 422, resp.text
    assert calls == []


def test_a_dry_run_never_hands_delivery_to_the_worker(scm_app, monkeypatch):
    """Tester finding (console / dry run): the route claimed delivery unconditionally, so
    the worker pushed the workbook to the contact's WhatsApp for a TEST turn. A dry run
    still builds the file (My Downloads) but never claims the push."""
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact, dry_run="true"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending", body
    assert body.get("dry_run") is True, body
    row = _download_row(db, body["download_id"])
    assert row["deliver_to"] is None, "a dry run must never be pushed to WhatsApp"


def test_a_live_turn_still_hands_delivery_to_the_worker(scm_app, monkeypatch):
    app, db, key, _uid = _api_key_caller(scm_app)
    contact = _contact(db, granted=(GRANT_KEY, SUPPLIER_KEY))
    db.flush()
    _fake_queue(monkeypatch)
    _patch_wait(monkeypatch, on_wait=_timeout)

    with TestClient(app) as c:
        resp = c.get(ROUTE, headers={"X-API-Key": key}, params=_params(contact, dry_run="false"))

    body = resp.json()
    assert body["status"] == "pending" and not body.get("dry_run"), body
    assert _download_row(db, body["download_id"])["deliver_to"] == contact.id
