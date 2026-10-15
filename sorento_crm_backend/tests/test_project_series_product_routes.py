"""S18 + S19 at the HTTP seam.

The wiring is its own risk. Two things are pinned here that no service test can see: the
series listing carrying the nominated products to the screen at all, and the import
answering with the unmatched codes rather than a bare count. A route that swallowed the
unmatched list would look exactly like a successful import.

Recompute is here too, for its refusals: a frozen version must come back 422 rather than
quietly rewriting quoted history, and a reader without edit rights must not be able to
press it.
"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-series-route"
BASE = "/api/v1/project-sales"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=f"{MARKER} Ali"))
    db.flush()
    return user_id


def _uom(db) -> str:
    row = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add(row)
    db.flush()
    return row.id


def _category(db) -> ProductCategory:
    row = ProductCategory(
        id=_uid(),
        category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} Basins",
    )
    db.add(row)
    db.flush()
    return row


def _product(db, code: str, category_id: str, uom_id: str) -> Product:
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        description="Wall-hung basin, white",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=Decimal("1000.00"),
    )
    db.add(row)
    db.flush()
    return row


def _client(db, user_id: str, *, slugs=None):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    held = set(
        slugs
        if slugs is not None
        else [
            "projects.projects.view",
            "projects.projects.create",
            "projects.projects.edit",
            "projects.projects.delete",
            "projects.projects.manage",
            "projects.types.view",
            "projects.types.edit",
        ]
    )
    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in held
    UserPermissionService.get_user_permission_slugs = lambda self, uid: sorted(held)
    return TestClient(app, raise_server_exceptions=False), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db)
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Tower {_uid()[:12]}",
        )
        db.commit()
        client, originals = _client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, user_id, project
        finally:
            _restore(originals)


def _series(client, name: str = "Sanitaryware template") -> dict:
    response = client.post(
        f"{BASE}/config/series", json={"name": f"{MARKER} {name}", "category_ids": []}
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ S18 import


def test_pasted_codes_are_loaded_and_the_misses_come_back(api):
    client, db, _company_id, _user_id, _project = api
    uom = _uom(db)
    category = _category(db)
    _product(db, "ZZTRT-CWB-242", category.id, uom)
    db.commit()

    series = _series(client)
    response = client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["  zztrt cwb242 ", "ZZTRT-NOT-STOCKED"], "mode": "append"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 1
    assert body["matched_codes"] == 1
    assert body["unmatched_codes"] == ["ZZTRT-NOT-STOCKED"]


def test_the_series_listing_names_the_products_it_now_carries(api):
    """No UUID reaches the UI, so the listing has to carry CODES rather than ids."""
    client, db, _company_id, _user_id, _project = api
    uom = _uom(db)
    category = _category(db)
    _product(db, "ZZTRT-LIST-1", category.id, uom)
    db.commit()

    series = _series(client)
    client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["ZZTRT-LIST-1"], "mode": "append"},
    )

    listed = client.get(f"{BASE}/config/series?include_inactive=true").json()["data"]
    mine = next(row for row in listed if row["id"] == series["id"])
    assert mine["product_count"] == 1
    assert mine["product_codes"] == ["ZZTRT-LIST-1"]


def _workbook() -> bytes:
    """The client's own layout: a title on row 1, the headings on row 2, codes below.

    Read by HEADING, never by position, which is why the test writes the title row at all -
    a positional reader would take "PROPOSED ITEMS" for a product code.
    """
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "wares"
    sheet.append(["PROPOSED ITEMS"])
    sheet.append(["ITEM ", "PRODUCT IMAGE ", " DESCRIPTION ", "BRAND ", "PRODUCT CODE"])
    sheet.append([1, None, "Wall-hung basin", "SORENTO", "ZZTRT-SHEET-1"])
    sheet.append([2, None, "Something else", "SORENTO", "ZZTRT-SHEET-MISSING"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_uploading_a_workbook_queues_it_and_returns_a_job_to_watch(api, monkeypatch):
    """202 and a job id, not a report.

    The client's workbook is 9.2 MB. Opening it inside this `async def` route ran the parse
    on the event loop and stalled every other request in the process, so the upload was slow
    for everybody, not only for the person who started it.

    The response deliberately carries NO counts: nothing has been read when it is written,
    and a zero would be indistinguishable from a sheet that matched nothing.
    """
    client, db, _company_id, _user_id, _project = api
    uom = _uom(db)
    category = _category(db)
    _product(db, "ZZTRT-SHEET-1", category.id, uom)
    db.commit()

    enqueued: dict = {}

    class _FakeJob:
        id = "zzt-rq-job"

    def _fake_enqueue(func, *args, **kwargs):
        enqueued["func"] = func
        enqueued["args"] = args
        enqueued["kwargs"] = kwargs
        # RQ pre-assigns the id to the DB job_id so a worker that finishes before this
        # request commits still lands on the row the browser is polling.
        _FakeJob.id = kwargs.get("job_id") or _FakeJob.id
        return _FakeJob()

    monkeypatch.setattr("app.services.queue_service.enqueue_job", _fake_enqueue)

    series = _series(client, "From the workbook")
    content = _workbook()
    response = client.post(
        f"{BASE}/config/series/{series['id']}/products/upload",
        files={
            "file": (
                "template.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": "append"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_id"]
    assert body["series_id"] == series["id"]
    assert body["mode"] == "append"
    assert "added" not in body and "unmatched_codes" not in body

    # The bytes reached the queue whole; the worker is not asked to fetch them again.
    assert enqueued["args"][1] == content
    assert enqueued["args"][3] == series["id"]
    assert enqueued["args"][4] == "append"
    # `project_docs`, this checkout's OWN queue. Every worktree shares one Redis, and a
    # worker running out of another checkout listens on `imports` without this task module -
    # it would claim the job and fail it on import, which reads as a bug in this code.
    assert enqueued["kwargs"]["queue_name"] == "project_docs"


def test_the_queued_read_applies_the_sheet_and_reports_what_missed(api, monkeypatch):
    """The task itself, against the database, because that is where the work moved.

    Same assertions the synchronous route used to carry - a code that matched, a code that
    did not, and the miss NAMED rather than counted - now made where they are still true.
    """
    from app.tasks import project_series_tasks

    client, db, company_id, user_id, _project = api
    uom = _uom(db)
    category = _category(db)
    _product(db, "ZZTRT-SHEET-1", category.id, uom)
    db.commit()

    series = _series(client, "Queued workbook")

    from app.services.job_service import JobService

    job = JobService(db).create_job(
        job_type=project_series_tasks.JOB_TYPE,
        user_id=user_id,
        filename="template.xlsx",
        company_id=str(company_id),
    )
    db.commit()

    # The worker opens its OWN session; the test's is the one holding the seeded rows, so
    # both must be the same session or the task reads an empty database.
    monkeypatch.setattr(project_series_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    project_series_tasks.process_series_product_import(
        str(job.id),
        _workbook(),
        "template.xlsx",
        series["id"],
        "append",
        user_id,
    )

    db.refresh(job)
    # `finished`, not `completed`: `JobStatus.FINISHED.value` is what `complete_job` writes,
    # and the browser has to accept BOTH spellings because RQ's own status is a `(str, Enum)`
    # whose `str()` is `'JobStatus.FINISHED'`. Pinned here so a poll loop that only knows one
    # of them cannot pass unnoticed.
    assert str(getattr(job.status, "value", job.status)) == "finished", job.error
    assert job.result["submitted"] == 2
    assert job.result["added"] == 1
    assert job.result["unmatched_codes"] == ["ZZTRT-SHEET-MISSING"]
    # The progress the browser draws while it waits, in the only unit that means anything
    # to somebody checking against their own spreadsheet.
    assert job.total_rows == 2
    assert job.skipped_rows == 1


def test_a_queued_read_of_a_deleted_series_fails_the_job_rather_than_raising(api, monkeypatch):
    """A job has nobody to throw at.

    The series can be deleted between the upload and the read. Failing the job records the
    reason where the person who uploaded is actually looking; an exception out of the task
    leaves them watching a spinner that never resolves.
    """
    from app.tasks import project_series_tasks
    from app.services.job_service import JobService

    _client_, db, company_id, user_id, _project = api
    job = JobService(db).create_job(
        job_type=project_series_tasks.JOB_TYPE,
        user_id=user_id,
        filename="template.xlsx",
        company_id=str(company_id),
    )
    db.commit()

    monkeypatch.setattr(project_series_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    project_series_tasks.process_series_product_import(
        str(job.id), _workbook(), "template.xlsx", _uid(), "append", user_id
    )

    db.refresh(job)
    assert job.status == "failed"
    assert "no longer exists" in (job.error or "")


def test_an_unknown_upload_mode_is_refused_before_anything_is_queued(api, monkeypatch):
    """Validated synchronously, because it is instant.

    A queued job that dies a second later on "unknown mode" is a worse way to say the same
    thing than a 422 the moment the button is pressed.
    """
    client, _db, _company_id, _user_id, _project = api

    def _explode(*_args, **_kwargs):
        raise AssertionError("nothing should be queued")

    monkeypatch.setattr("app.services.queue_service.enqueue_job", _explode)

    series = _series(client, "Bad mode")
    response = client.post(
        f"{BASE}/config/series/{series['id']}/products/upload",
        files={"file": ("template.xlsx", _workbook(), "application/octet-stream")},
        data={"mode": "obliterate"},
    )

    assert response.status_code == 422, response.text


def test_an_empty_paste_is_refused_rather_than_wiping_the_series(api):
    client, _db, _company_id, _user_id, _project = api
    series = _series(client, "Never emptied")

    response = client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["", "  "], "mode": "replace"},
    )

    assert response.status_code == 422, response.text


def test_importing_onto_a_series_that_does_not_exist_is_a_404(api):
    client, _db, _company_id, _user_id, _project = api

    response = client.post(
        f"{BASE}/config/series/{_uid()}/products",
        json={"codes": ["ANYTHING"], "mode": "append"},
    )

    assert response.status_code == 404, response.text


def test_a_reader_cannot_load_products_onto_a_series(api):
    client, db, _company_id, user_id, _project = api
    # Re-installed with the view grant alone; `_client` hands back whatever was patched
    # before it, so the fixture's own teardown still lands on the real methods.
    reader, originals = _client(db, user_id, slugs=["projects.types.view"])
    try:
        response = reader.post(
            f"{BASE}/config/series/{_uid()}/products",
            json={"codes": ["ANYTHING"], "mode": "append"},
        )
        assert response.status_code == 403, response.text
    finally:
        _restore(originals)


# --------------------------------------------------------------- S19 recompute


def _quotation(client, project_id: str) -> dict:
    response = client.post(
        f"{BASE}/projects/{project_id}/quotations", json={"scope_label": "House Units"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recompute_clears_a_stale_non_standard_flag_and_says_what_it_did(api):
    from app.models.projects import ProjectQuotationLine

    client, db, _company_id, _user_id, project = api
    uom = _uom(db)
    category = _category(db)
    product = _product(db, "ZZTRT-RECOMP-1", category.id, uom)
    db.commit()

    quotation = _quotation(client, project.id)
    version_id = quotation["current_version_id"]
    line = client.post(
        f"{BASE}/quotation-versions/{version_id}/lines",
        json={"product_id": product.id, "unit_price": "900.00", "quantity": "1"},
    ).json()
    # Stale by hand, exactly as the live rows are: judged once against a series the
    # quotation no longer nominates, and never re-asked.
    stored = (
        db.query(ProjectQuotationLine)
        .filter(ProjectQuotationLine.id == line["id"])
        .first()
    )
    stored.is_non_standard = True
    db.commit()

    response = client.post(f"{BASE}/quotation-versions/{version_id}/recompute")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["no_longer_non_standard"] == 1
    assert body["changed_count"] == 1
    assert body["changed_lines"] == ["ZZTRT-RECOMP-1"]
    # And the screen reads the corrected flag on its next fetch.
    lines = client.get(f"{BASE}/quotation-versions/{version_id}/lines").json()["data"]
    assert lines[0]["is_non_standard"] is False


def test_recompute_on_a_frozen_version_is_refused(api):
    client, db, _company_id, _user_id, project = api
    uom = _uom(db)
    category = _category(db)
    product = _product(db, "ZZTRT-RECOMP-2", category.id, uom)
    db.commit()

    quotation = _quotation(client, project.id)
    first_version = quotation["current_version_id"]
    client.post(
        f"{BASE}/quotation-versions/{first_version}/lines",
        json={"product_id": product.id, "unit_price": "900.00", "quantity": "1"},
    )
    revised = client.post(f"{BASE}/quotations/{quotation['id']}/revise")
    assert revised.status_code == 201, revised.text

    response = client.post(f"{BASE}/quotation-versions/{first_version}/recompute")

    assert response.status_code == 422, response.text


def test_recompute_of_an_unknown_version_is_a_404(api):
    client, _db, _company_id, _user_id, _project = api

    response = client.post(f"{BASE}/quotation-versions/{_uid()}/recompute")

    assert response.status_code == 404, response.text


def test_a_reader_cannot_recompute(api):
    client, db, _company_id, user_id, project = api
    quotation = _quotation(client, project.id)
    version_id = quotation["current_version_id"]

    reader, originals = _client(db, user_id, slugs=["projects.projects.view"])
    try:
        response = reader.post(f"{BASE}/quotation-versions/{version_id}/recompute")
        assert response.status_code == 403, response.text
    finally:
        _restore(originals)


# --------------------------------------------------------- live line verdict


def test_a_draft_line_is_judged_on_the_spot_without_saving_anything(api):
    """The client's requirement verbatim: 'cannot wait until I save then only compute'.

    BM107 outside the series must read non-standard the moment it is picked; C-FH14 inside
    it must read standard - and NOTHING may be written on the way to either answer.
    """
    from app.models.projects import ProjectQuotationLine

    client, db, _company_id, _user_id, project = api
    uom = _uom(db)
    category = _category(db)
    inside = _product(db, "ZZTRT-CFH14", category.id, uom)
    outside = _product(db, "ZZTRT-BM107", category.id, uom)
    db.commit()

    quotation = _quotation(client, project.id)
    series = _series(client, "Live verdict")
    # Scope the series to a category neither product is under, else no filters = everything.
    elsewhere = _category(db)
    db.commit()
    assert (
        client.put(
            f"{BASE}/config/series/{series['id']}", json={"category_ids": [elsewhere.id]}
        ).status_code
        == 200
    )
    client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["ZZTRT-CFH14"], "mode": "append"},
    )
    assert (
        client.put(
            f"{BASE}/quotations/{quotation['id']}", json={"series_id": series["id"]}
        ).status_code
        == 200
    )

    standard = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_id": inside.id, "unit_price": "900.00"},
    ).json()
    non_standard = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_id": outside.id, "unit_price": "900.00"},
    ).json()

    assert standard["is_non_standard"] is False
    assert non_standard["is_non_standard"] is True
    # A verdict is a question, never a write.
    assert db.query(ProjectQuotationLine).count() == 0


def test_a_draft_with_no_product_is_non_standard_only_under_a_series(api):
    """Off-catalog under a series = non-standard (AC-E5); with no series there is no
    allowlist to breach, so it judges clean - the dormant state must not invent flags."""
    client, _db, _company_id, _user_id, project = api

    quotation = _quotation(client, project.id)
    no_series = client.get(f"{BASE}/quotations/{quotation['id']}/line-verdict").json()
    assert no_series["is_non_standard"] is False

    series = _series(client, "Off-catalog verdict")
    client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["ZZTRT-ANYTHING-AT-ALL"], "mode": "append"},
    )
    client.put(f"{BASE}/quotations/{quotation['id']}", json={"series_id": series["id"]})

    under_series = client.get(f"{BASE}/quotations/{quotation['id']}/line-verdict").json()
    assert under_series["is_non_standard"] is True


def test_a_half_typed_price_is_judged_as_no_price_not_as_an_error(api):
    """`900.` mid-keystroke must not 422 - the person is still typing, and a toast per
    keystroke teaches them the flags are noise."""
    client, db, _company_id, _user_id, project = api
    uom = _uom(db)
    category = _category(db)
    product = _product(db, "ZZTRT-MIDTYPE", category.id, uom)
    db.commit()

    quotation = _quotation(client, project.id)
    response = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_id": product.id, "unit_price": "not-a-number"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_below_floor"] is False


def test_the_live_verdict_applies_the_series_floor(api):
    """The same `resolve_floor` the save runs: series price 100 at 6%% -> floor 94, so a
    draft at 90 is below it BEFORE any save, with the floor carried for the message."""
    client, db, _company_id, _user_id, project = api
    uom = _uom(db)
    category = _category(db)
    product = _product(db, "ZZTRT-FLOORED", category.id, uom)
    db.commit()

    quotation = _quotation(client, project.id)
    series = _series(client, "Floored verdict")
    client.post(
        f"{BASE}/config/series/{series['id']}/products",
        json={"codes": ["ZZTRT-FLOORED"], "mode": "append"},
    )
    client.patch(
        f"{BASE}/config/series/{series['id']}/products/{product.id}",
        json={"selling_price": "100.00", "max_discount_pct": "6.00"},
    )
    client.put(f"{BASE}/quotations/{quotation['id']}", json={"series_id": series["id"]})

    below = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_id": product.id, "unit_price": "90.00"},
    ).json()
    at_floor = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_id": product.id, "unit_price": "94.00"},
    ).json()

    assert below["is_below_floor"] is True
    assert below["floor_value"] == "94.00"
    assert at_floor["is_below_floor"] is False
