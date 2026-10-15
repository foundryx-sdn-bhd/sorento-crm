"""S1 (#1267): `GET /api/v1/sales/analysis`, the chatbot's seam onto the sales dataset.

UAC: AC-S1-6 (422s), AC-S1-7 (n, total_count, whole-set totals, no paging keys), AC-S1-8
(401 / 403 / reveal key), AC-S1-21 (Sorento or Mocha?), AC-S1-23 (dealer refusal),
AC-R4-1 / AC-R4-4 (every answer carries the Excel: attachments in the turn, else pending
with the delivery claim), AC-R4-3 (a question or a refusal carries no file and writes no
My Downloads row), AC-R4-6 (the file's figures are the text's: same params, same view).

The wait is patched at `_await_download` (the low stock route's seam), so nothing sleeps.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.order import SalesOrder, SalesOrderLine
from app.models.user import User
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from tests._mc_lookup_seed import MOCHA_ID, customer, product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

ROUTE = "/api/v1/sales/analysis"
SLUG = "sales.reports.view"
GRANT = "sales_orders.sales_report"


class _CdnBackend:
    def get_cdn_base_url(self, key):
        return f"https://cdn.test.invalid/{key}"

    def get_cloudfront_base_url(self, key):
        return f"https://cdn.test.invalid/{key}"

    def get_signed_url(self, key, expires_in=0):
        return f"https://cdn.test.invalid/{key}?signed"


@pytest.fixture(autouse=True)
def _no_cloud(monkeypatch):
    from app.services import storage_router

    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _CdnBackend())


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, None)
        seed_mocha(s)
        yield s


@pytest.fixture
def queue(monkeypatch):
    from app.services import queue_service

    calls = []

    def _enqueue(fn, *args, **kwargs):
        calls.append((fn, args, kwargs))
        return type("J", (), {"id": "job"})()

    monkeypatch.setattr(queue_service, "enqueue_job", _enqueue)
    return calls


def _actor(db):
    user = User(id=str(uuid.uuid4()), email=f"{unique_code('A')}@integrations.local",
                name="Act as", status="ACTIVE")
    db.add(user)
    db.flush()
    return {"id": user.id, "email": user.email}


def _contact(db, companies=(DEFAULT_COMPANY_ID,), granted=(GRANT,)):
    from app.services.contact_field_reveal_service import set_granted_keys

    contact = RespondContact(id=str(uuid.uuid4()), respond_io_id=unique_code("C"),
                             phone_number=f"+6010{uuid.uuid4().hex[:7]}", name="Staff",
                             workspace_id=None)
    db.add(contact)
    db.flush()
    for company_id in companies:
        db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=company_id))
    db.flush()
    if granted:
        set_granted_keys(db, contact.id, list(granted), actor_id=None)
    return contact


@contextmanager
def _client(db, principal, contact_companies, allow=(SLUG,), headers=None):
    from app.services.user_service import UserPermissionService

    def _override_db():
        yield db

    async def _scope():
        scope = frozenset(contact_companies)
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _scope
    original = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in allow
    try:
        # The chatbot's route answers API-key callers only (security review B2).
        yield TestClient(app, headers={"X-API-Key": "k"} if headers is None else headers)
    finally:
        UserPermissionService.check_user_has_permission = original
        app.dependency_overrides.clear()


def _sale(db, amount, when, demand_class="retail", company_id=DEFAULT_COMPANY_ID):
    so = SalesOrder(id=str(uuid.uuid4()), so_number=unique_code("SO"), order_date=when,
                    status="open", demand_class=demand_class, company_id=company_id)
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id,
                          product_id=product(db, company_id=company_id).id, qty_ordered=1,
                          qty_delivered=1, line_total=Decimal(amount), line_status="open",
                          company_id=company_id))
    db.flush()


def _q(contact, **extra):
    params = {"contact_id": contact.respond_io_id, "space_id": "364817", "basis": "delivered",
              "rows": "month", "cols": "year", "channel": "dealer",
              "date_from": "2025-01-01", "date_to": "2026-09-26"}
    params.update(extra)
    return params


def _patch_wait(monkeypatch, result):
    from app.api.v1.sales import analysis

    async def _fake(download_id, *a, **kw):
        if isinstance(result, BaseException):
            raise result
        return result(str(download_id)) if callable(result) else result

    monkeypatch.setattr(analysis, "_await_download", _fake)


def _mark_ready(db):
    def _do(download_id):
        db.execute(text("UPDATE user_downloads SET status='ready', storage_provider='r2', "
                        "storage_key=:k WHERE id=:id"),
                   {"k": f"exports/report-xlsx/{download_id}/Yearly comparison.xlsx", "id": download_id})
        db.flush()
        return {"status": "ready"}
    return _do


def _downloads(db):
    return db.execute(text("SELECT count(*) FROM user_downloads")).scalar()


# ------------------------------------------------------------------------- access


def test_ac_s1_8_no_principal_is_401(db):
    with TestClient(app) as client:
        assert client.get(ROUTE, params={"contact_id": "x", "space_id": "y"}).status_code == 401


def test_ac_s1_8_without_the_slug_is_403(db, queue):
    contact = _contact(db)
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID], allow=()) as client:
        assert client.get(ROUTE, params=_q(contact)).status_code == 403
    assert not queue


def test_ac_s1_8_a_contact_without_the_sales_report_key_is_403_and_writes_nothing(db, queue):
    contact = _contact(db, granted=())
    before = _downloads(db)
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        response = client.get(ROUTE, params=_q(contact))
    assert response.status_code == 403
    assert response.json()["code"] == "sales_report_not_enabled"
    assert _downloads(db) == before and not queue


def test_ac_s1_23_a_dealer_contact_is_refused_and_nothing_is_fetched(db, queue):
    from app.models.access import RespondContactCustomer

    contact = _contact(db)
    db.add(RespondContactCustomer(contact_id=contact.id,
                                  customer_id=customer(db, company_id=DEFAULT_COMPANY_ID).id))
    db.flush()
    before = _downloads(db)
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    assert body == {"status": "refused",
                    "message": "Sorry, I can only share sales figures for your own account."}
    assert _downloads(db) == before and not queue


def test_security_b2_a_bearer_caller_is_refused_whatever_contact_it_names(db, queue):
    """A JWT caller must not read a company through someone else's contact."""
    seed_mocha  # noqa: B018
    contact = _contact(db, companies=(MOCHA_ID,))
    before = _downloads(db)
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID], headers={"Authorization": "Bearer x"}) as client:
        response = client.get(ROUTE, params=_q(contact))
    assert response.status_code == 403
    assert response.json()["code"] == "api_key_required"
    assert _downloads(db) == before and not queue


def test_security_b1_a_dealer_is_refused_under_the_empty_scope_production_gives(db, queue):
    """A NULL-workspace contact resolves to an EMPTY company scope on an API-key request;
    the dealer link (company-scoped) must still be found."""
    from app.models.access import RespondContactCustomer

    contact = _contact(db)
    db.add(RespondContactCustomer(contact_id=contact.id,
                                  customer_id=customer(db, company_id=DEFAULT_COMPANY_ID).id,
                                  company_id=DEFAULT_COMPANY_ID))
    db.flush()
    with _client(db, _actor(db), []) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    assert body == {"status": "refused",
                    "message": "Sorry, I can only share sales figures for your own account."}
    assert not queue


# ------------------------------------------------------------------------ the 422s


@pytest.mark.parametrize(
    "extra, code",
    [
        ({"rows": "year", "cols": "year"}, "rows_equal_cols"),
        ({"rows": "agent_shoe_size"}, "unknown_axis"),
        ({"date_from": "2026-05-01", "date_to": "2026-04-01"}, "date_range_inverted"),
        ({"basis": None}, "basis_required"),
        ({"basis": "shipped"}, "basis_required"),
        ({"n": 0}, "n_out_of_range"),
        ({"n": 101}, "n_out_of_range"),
    ],
)
def test_ac_s1_6_a_malformed_ask_is_422_with_a_named_code(db, queue, extra, code):
    contact = _contact(db)
    params = {k: v for k, v in _q(contact, **extra).items() if v is not None}
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        response = client.get(ROUTE, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert not queue


# ------------------------------------------------------------------- the questions


def test_ac_s1_21_a_contact_in_both_companies_naming_neither_is_asked(db, queue):
    contact = _contact(db, companies=(DEFAULT_COMPANY_ID, MOCHA_ID))
    before = _downloads(db)
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID, MOCHA_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    # Chat fix PR-B S7 (AC-B9): the names also ride as data, the company question's options.
    assert body == {"status": "clarify", "message": "Sorento or Mocha?", "options": ["Sorento", "Mocha"]}
    assert _downloads(db) == before and not queue


def test_naming_the_company_answers_for_it(db, queue, monkeypatch):
    _sale(db, "10.00", date(2026, 1, 3))
    _sale(db, "700.00", date(2026, 1, 3), company_id=MOCHA_ID)
    contact = _contact(db, companies=(DEFAULT_COMPANY_ID, MOCHA_ID))
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID, MOCHA_ID]) as client:
        body = client.get(ROUTE, params=_q(contact, company="mocha")).json()
    assert body["status"] == "ready"
    assert body["company"] == "Mocha"
    assert body["totals"]["total"] == "700.00"


def test_a_company_the_contact_is_not_in_is_403(db, queue):
    contact = _contact(db, companies=(DEFAULT_COMPANY_ID,))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        response = client.get(ROUTE, params=_q(contact, company="Mocha"))
    assert response.status_code == 403


# ------------------------------------------------------------------- the answers


def test_ac_s1_20_ac_r4_1_compare_by_month_is_the_whole_table_and_the_file(db, queue, monkeypatch):
    _sale(db, "100.00", date(2025, 1, 10))
    _sale(db, "60.00", date(2026, 1, 10))
    _sale(db, "999.00", date(2026, 1, 10), demand_class="project")
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    assert body["status"] == "ready"
    assert body["report"] == "Sales"
    assert body["company"] == "Sorento"
    assert body["channel"] == "Dealer"
    assert body["basis"] == "Delivered (transferred to DO)"
    assert body["period"] == "01/01/2025 to 26/09/2026"
    assert body["rows_label"] == "Month" and body["cols_label"] == "Year"
    assert body["columns"] == ["2025", "2026", "Difference"]
    assert body["total_count"] == 12
    jan = body["rows"][0]
    assert jan == {"label": "JAN", "values": ["100.00", "60.00", "-40.00"], "total": "160.00"}
    assert body["rows"][11]["label"] == "DEC"
    assert body["totals"] == {"values": ["100.00", "60.00", "-40.00"], "total": "160.00"}
    for key in ("page", "page_size", "has_more"):
        assert key not in body
    [attachment] = body["attachments"]
    assert attachment["filename"].endswith(".xlsx")
    assert attachment["mimeType"].endswith("spreadsheetml.sheet")
    (fn, args, kwargs), = queue
    assert fn.__name__ == "generate_report_xlsx"
    assert kwargs["company_grants"] == [DEFAULT_COMPANY_ID]


def test_ac_r4_6_the_file_is_the_same_query_as_the_text(db, queue, monkeypatch):
    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        client.get(ROUTE, params=_q(contact))
    (fn, args, kwargs), = queue
    _download_id, key, params, view, _user = args
    assert key == "sales_yearly"
    assert params["channel"] == ["dealer"] and params["basis"] == ["delivered"]
    assert params["company"] == [DEFAULT_COMPANY_ID]
    assert params["period"] == {"kind": "custom", "from": "2025-01-01", "to": "2026-09-26"}
    # Month by year is the report's own year by month layout on its side: the file uses
    # the PDF's orientation, with the VARIANCE row carrying the text's Difference column.
    assert view["pivot"] == {"rows": "year", "cols": "month_of_year", "measures": ["sales_value"]}


def test_ac_r4_6_the_files_figures_are_the_texts(db, queue, monkeypatch):
    """Render the queued file and compare its cells with the text, figure for figure
    (review round 2 B2: the rendered file, not only the engine's data)."""
    import io

    from openpyxl import load_workbook

    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine, registry as reg
    from app.services.reports.xlsx_renderer import render_workbook

    _sale(db, "100.00", date(2025, 1, 10))
    _sale(db, "80.00", date(2025, 2, 10))
    _sale(db, "300.00", date(2025, 3, 10))
    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact, date_to="2026-12-31")).json()
    (fn, args, kwargs), = queue
    _download_id, key, params, view, _user = args
    definition = reg.get(key)
    data = engine.run_workbook(db, definition, params, ReportViewConfig.model_validate(view),
                               company_grants=frozenset(kwargs["company_grants"]))
    sheet = load_workbook(io.BytesIO(render_workbook(definition, data)))["SUMMARY"]
    file_rows = {}
    for r in range(1, sheet.max_row + 1):
        label = sheet.cell(row=r, column=1).value
        if label in ("2025", "2026", "VARIANCE"):
            file_rows[label] = [
                None if (v := sheet.cell(row=r, column=c).value) is None
                else str(Decimal(str(v)).quantize(Decimal("0.01")))
                for c in range(2, 15)
            ]
    assert set(file_rows) == {"2025", "2026", "VARIANCE"}
    by_label = {r["label"]: r["values"] for r in body["rows"]}
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    for index, month in enumerate(months):
        in_2025, in_2026, difference = by_label[month]
        assert file_rows["2025"][index] == in_2025, month
        assert file_rows["2026"][index] == in_2026, month
        assert file_rows["VARIANCE"][index] == difference, month
    assert file_rows["VARIANCE"][2] == "-300.00"  # March 2026 sold nothing
    assert file_rows["VARIANCE"][12] == body["totals"]["values"][2] == "-420.00"
    assert body["period"].endswith(reg.today_malaysia().strftime("%d/%m/%Y"))


def test_reviewer_r2_b1_the_difference_counts_a_past_month_this_year_sold_nothing(
    db, queue, monkeypatch
):
    """B1 (review round 2), the reviewer's probe: 60 this year against 100 + 300 last year
    is (340), not (40). FEB sold nothing either year, OCT is after the as-at date."""
    _sale(db, "100.00", date(2025, 1, 10))
    _sale(db, "300.00", date(2025, 3, 10))
    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    by_label = {r["label"]: r["values"] for r in body["rows"]}
    assert by_label["JAN"] == ["100.00", "60.00", "-40.00"]
    assert by_label["FEB"] == [None, None, None]
    assert by_label["MAR"] == ["300.00", None, "-300.00"]
    assert by_label["OCT"] == [None, None, None]
    assert body["totals"]["values"] == ["400.00", "60.00", "-340.00"]


def test_reviewer_r2_b1_by_channel_the_difference_is_the_same_months_last_year(
    db, queue, monkeypatch
):
    """One line per channel, the years across: the Difference is this year to date against
    the SAME months last year, so last December's 500 is not in it."""
    _sale(db, "100.00", date(2025, 1, 10))
    _sale(db, "500.00", date(2025, 12, 10))
    _sale(db, "60.00", date(2026, 1, 10))
    _sale(db, "7.00", date(2025, 2, 10), demand_class="project")
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        params = _q(contact, rows="channel")
        params.pop("channel")  # both channels
        body = client.get(ROUTE, params=params).json()
    assert body["columns"] == ["2025", "2026", "Difference"]
    by_label = {r["label"]: r["values"] for r in body["rows"]}
    assert by_label["Dealer"] == ["600.00", "60.00", "-40.00"]
    assert by_label["Project team"] == ["7.00", None, "-7.00"]
    assert body["totals"]["values"] == ["607.00", "60.00", "-47.00"]


def test_ac_r4_1_one_figure_is_still_text_and_file(db, queue, monkeypatch):
    _sale(db, "1234.50", date(2026, 3, 3), demand_class="project")
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact, rows="channel", cols="year", channel="project",
                                           date_from="2026-01-01", date_to="2026-09-26")).json()
    assert body["columns"] == ["2026"]
    assert body["rows"] == [{"label": "Project team", "values": ["1234.50"], "total": "1234.50"}]
    assert len(body["attachments"]) == 1


def test_ac_s1_7_n_cuts_after_the_count_and_the_totals(db, queue, monkeypatch):
    for month, amount in ((1, "5.00"), (2, "50.00"), (3, "30.00"), (4, "30.00"), (5, "1.00")):
        _sale(db, amount, date(2026, month, 2))
    contact = _contact(db)
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact, n=3, date_from="2026-01-01")).json()
    assert [r["label"] for r in body["rows"]] == ["FEB", "APR", "MAR"]  # total desc, label asc
    assert body["total_count"] == 12
    assert body["totals"]["total"] == "116.00"


def test_ac_r4_4_a_slow_file_is_pending_and_claims_delivery_for_the_worker(db, queue, monkeypatch):
    import asyncio

    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    _patch_wait(monkeypatch, asyncio.TimeoutError())
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    assert body["status"] == "pending"
    assert body["attachments"] == []
    assert body["rows"], "the text does not wait for the file"
    row = db.execute(text("SELECT kind, deliver_to_contact_id::text AS d FROM user_downloads "
                          "WHERE id = :id"), {"id": body["download_id"]}).mappings().one()
    assert row["kind"] == "report_xlsx" and row["d"] == contact.id


def test_ac_r4_4_the_file_belongs_to_the_contacts_linked_user(db, queue, monkeypatch):
    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    owner = User(id=str(uuid.uuid4()), email=f"{unique_code('O')}@t.test", name="Owner",
                 status="ACTIVE", respond_contact_id=contact.id)
    db.add(owner)
    db.flush()
    _patch_wait(monkeypatch, _mark_ready(db))
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        client.get(ROUTE, params=_q(contact))
    (fn, args, kwargs), = queue
    user_id = db.execute(text("SELECT user_id FROM user_downloads WHERE id = :id"),
                         {"id": args[0]}).scalar()
    assert str(user_id) == owner.id


def test_ac_r4_4_a_failed_build_is_an_error_line_not_pending(db, queue, monkeypatch):
    _sale(db, "60.00", date(2026, 1, 10))
    contact = _contact(db)
    _patch_wait(monkeypatch, {"status": "failed"})
    with _client(db, _actor(db), [DEFAULT_COMPANY_ID]) as client:
        body = client.get(ROUTE, params=_q(contact)).json()
    assert body["status"] == "error"
    assert body["rows"], "the text still goes"
    assert body["attachments"] == []


def _run_export(db, monkeypatch, download_id, *, fail=False):
    from unittest.mock import patch

    from app.tasks import export_tasks, report_export_tasks as tasks

    pushed, told = [], []
    monkeypatch.setattr(export_tasks, "_push_download_to_chat",
                        lambda _db, did, **kw: pushed.append((did, kw)))
    monkeypatch.setattr(export_tasks, "_tell_chat_the_download_failed",
                        lambda _db, did, **kw: told.append((did, kw)))

    class _Backend:
        def upload_file(self, file_content, file_path, content_type=None, **_kw):
            if fail:
                raise RuntimeError("bucket down")
            return file_path, "https://cdn.test.invalid/x"

    params = {"date_basis": "order_date", "company": [DEFAULT_COMPANY_ID], "channel": ["dealer"],
              "basis": ["delivered"], "period": {"kind": "custom", "from": "2026-01-01", "to": "2026-09-26"}}
    with patch.object(tasks, "SessionLocal", lambda: db), patch.object(
        tasks, "get_backend", lambda _p: _Backend()
    ), patch.object(tasks, "default_provider", lambda: "r2"), patch.object(db, "close", lambda: None):
        out = tasks.generate_report_xlsx(download_id, "sales_yearly", params, None, "u",
                                         company_grants=[DEFAULT_COMPANY_ID])
    return out, pushed, told


def test_ac_r4_4_the_report_job_hands_a_ready_file_to_the_chat_push(db, monkeypatch):
    from app.services.download_service import DownloadService

    actor = _actor(db)
    download = DownloadService(db).create(user_id=actor["id"], kind="report_xlsx", filename="y.xlsx")
    out, pushed, told = _run_export(db, monkeypatch, str(download.id))
    assert out["status"] == "ready"
    assert [p[0] for p in pushed] == [str(download.id)]
    assert pushed[0][1]["provider"] == "r2"
    assert not told


def test_ac_r4_4_a_failed_report_job_tells_the_chat_in_text(db, monkeypatch):
    from app.services.download_service import DownloadService

    actor = _actor(db)
    download = DownloadService(db).create(user_id=actor["id"], kind="report_xlsx", filename="y.xlsx")
    out, pushed, told = _run_export(db, monkeypatch, str(download.id), fail=True)
    assert out["status"] == "failed"
    assert not pushed
    assert told and told[0][0] == str(download.id)
    assert told[0][1]["text"] == "Could not build the sales report Excel right now."


def test_the_one_shot_claim_pushes_only_a_row_the_turn_handed_over(db):
    """The push is a no-op for a screen export (no deliver_to_contact_id) and fires once
    for a claimed row, whatever the RQ retry count."""
    from app.services.download_service import DownloadService
    from app.tasks.export_tasks import _claim_chat_delivery

    actor = _actor(db)
    contact = _contact(db)
    screen = DownloadService(db).create(user_id=actor["id"], kind="report_xlsx", filename="a.xlsx")
    chat = DownloadService(db).create(user_id=actor["id"], kind="report_xlsx", filename="b.xlsx")
    db.execute(text("UPDATE user_downloads SET deliver_to_contact_id = :c WHERE id = :id"),
               {"c": contact.id, "id": str(chat.id)})
    db.flush()
    assert _claim_chat_delivery(db, str(screen.id)) is None
    assert _claim_chat_delivery(db, str(chat.id)) == contact.id
    assert _claim_chat_delivery(db, str(chat.id)) is None
