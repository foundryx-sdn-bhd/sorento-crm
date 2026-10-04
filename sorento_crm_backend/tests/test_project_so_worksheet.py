"""Stage 1A: the AutoCount SO worksheet, read as JSON (PLAN-scm-front-planning 1.2, J02).

The worksheet screen shows the SAME document ``import_file`` writes as CSV, before anybody
downloads it. So the binding property here is not "the JSON looks reasonable" but "the JSON
and the CSV are the same document": one builder produces both, and the parity test at the
bottom is what stops them drifting the day a column is added to one of them.

``can_export`` is the server's own answer to "may this leave the building" (AC-A01): the
frontend never re-derives it. It is published-or-amended AND no unacknowledged hard finding,
which is the publish gate plus the fact that a draft is not a document yet.

Fixture builders are shared with ``test_project_so_draft`` rather than copied, for the same
reason ``test_project_so_delta`` shares them. Postgres only, via ``blank_session``; every row
carries the ``zzt-so`` marker those builders stamp.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.project_so import SODraftFinding
from app.services.project_so_draft_service import ProjectSODraftService

from ._pg_fixture import blank_session
from .test_project_so_draft import (
    _cell,
    _customer,
    _minimal,
    _party,
    _phase,
    _po,
    _po_version,
    _product,
    _project,
    _quotation,
    _schedule,
    _sorento,
    _uid,
    _user,
    project_seed_service,
)

MARKER = "zzt-worksheet"
BASE = "/api/v1/project-sales"
VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"
ALL_SLUGS = [VIEW, EDIT, "projects.projects.delete", "projects.projects.manage"]


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        yield db, company_id, owner


# --------------------------------------------------------------------- helpers


def _built(db, company_id, owner, **overrides):
    """The smallest publishable order, plus the service that made it."""
    _project_row, _product_row, po, schedule = _minimal(db, company_id, owner, **overrides)
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    return service, service.get_order(built["data"][0]["id"]), po


def _csv_rows(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body)))


def _csv_header_refs(body: str) -> dict:
    """The six labelled rows AutoCount prints above the lines, as {label: value}."""
    return {
        row[0]: row[1]
        for row in _csv_rows(body)
        if len(row) == 2 and row[0] and not row[0].startswith("***")
    }


def _csv_line_rows(body: str) -> list[list[str]]:
    """The data rows: everything after the column header and before the blank + Total."""
    rows = _csv_rows(body)
    start = next(index for index, row in enumerate(rows) if row and row[0] == "Item") + 1
    return [row for row in rows[start:] if row and row[0] != "Total"]


# ------------------------------------------------------------------ happy path


def test_the_worksheet_header_refs_are_the_csv_header_rows(seeded):
    """One document, two renderings: the six refs must not be able to disagree."""
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco")
    service, order, po = _built(db, company_id, owner, customer=customer)
    service.publish(order, actor_user_id=owner)

    worksheet = service.worksheet(order)
    _filename, body = service.import_file(order)
    refs = _csv_header_refs(body)

    assert worksheet["provisional_ref"] == refs["Provisional Ref"] == order.provisional_ref
    assert worksheet["header"]["debtor"] == refs["Debtor"] == customer.customer_name
    assert worksheet["header"]["your_ref_no"] == refs["Your Ref No."] == po.po_number
    assert worksheet["header"]["our_ref_no"] == refs["Our Ref No."]
    assert worksheet["header"]["our_qt_ref_no"] == refs["Our QT Ref No."]
    assert worksheet["header"]["terms"] == refs["Terms"] == "*Net 60 days"


def test_the_lines_are_in_line_no_order_and_reserve_is_zero_until_stage_1c(seeded):
    """`reserve_qty` is a real AutoCount column: 0 is honest, blank would not be."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    first = _product(db, "SRTWC8613-RL")
    second = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db, project, lines=[(first, "10", "392.85"), (second, "5", "295.85")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, first, "SRTWC8613-RL", "10", "UNIT", "392.85", "3928.50", False),
            (2, second, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, first, "10")
    _cell(db, schedule, phase, second, "5")

    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    order = service.get_order(built["data"][0]["id"])

    worksheet = service.worksheet(order)
    line_numbers = [line["line_no"] for line in worksheet["lines"]]
    assert line_numbers == sorted(line_numbers)
    assert len(line_numbers) == 2
    assert {line["reserve_qty"] for line in worksheet["lines"]} == {"0"}
    assert {line["item_code"] for line in worksheet["lines"]} == {
        "SRTWC8613-RL",
        "SRTUB206-BI",
    }
    # The document's own total, not a second addition the screen could disagree with.
    assert worksheet["total_amount"] == "5407.75"
    assert worksheet["area_group"] == "TOWER"


def test_the_worksheet_total_is_the_orders_own_total(seeded):
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)

    worksheet = service.worksheet(order)

    assert Decimal(worksheet["total_amount"]) == Decimal(order.total_amount).quantize(
        Decimal("0.01")
    )
    assert sum(Decimal(line["total"]) for line in worksheet["lines"]) == Decimal(
        worksheet["total_amount"]
    )


# ----------------------------------------------------------------- can_export


def test_a_draft_cannot_be_exported_and_has_no_file_yet(seeded):
    """Nothing uncommitted is importable into AutoCount (the `import_file_url` rule)."""
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)

    worksheet = service.worksheet(order)

    assert worksheet["status"] not in ("published", "amended")
    assert worksheet["can_export"] is False
    assert worksheet["import_file_url"] is None


def test_a_published_order_with_nothing_outstanding_can_be_exported(seeded):
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)
    service.publish(order, actor_user_id=owner)

    worksheet = service.worksheet(order)

    assert worksheet["can_export"] is True
    assert worksheet["import_file_url"] == (
        f"/api/v1/project-sales/sales-orders/{order.id}/import-file"
    )


def test_an_unacknowledged_hard_finding_refuses_the_export_even_once_published(seeded):
    """The url survives, the export does not: the publish gate, still holding after it.

    `import_file_url` stays non-null because the order IS in AutoCount already and the
    address of its file has not changed. What the finding takes away is permission to
    fetch it, which is `can_export` and is enforced on the route (below), not a hint the
    screen is trusted to obey.
    """
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)
    service.publish(order, actor_user_id=owner)
    db.add(
        SODraftFinding(
            id=_uid(),
            company_id=order.company_id,
            project_sales_order_id=order.id,
            severity="hard",
            code="line_arithmetic",
            detail=f"{MARKER} the line does not add up",
        )
    )
    db.flush()

    worksheet = service.worksheet(order)

    assert worksheet["can_export"] is False
    assert service.can_export(order) is False
    assert worksheet["import_file_url"] is not None
    assert any(row["severity"] == "hard" for row in worksheet["findings"])


def test_an_acknowledged_hard_finding_no_longer_blocks_the_export(seeded):
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)
    finding = SODraftFinding(
        id=_uid(),
        company_id=order.company_id,
        project_sales_order_id=order.id,
        severity="hard",
        code="line_arithmetic",
        detail=f"{MARKER} the line does not add up",
    )
    db.add(finding)
    db.flush()
    service.acknowledge_finding(
        finding.id,
        reason="Checked against the signed PO.",
        actor_user_id=owner,
        permissions=ALL_SLUGS,
    )
    service.publish(order, actor_user_id=owner)

    worksheet = service.worksheet(order)

    assert worksheet["can_export"] is True


def test_the_sales_order_row_answers_the_export_question_the_worksheet_does(seeded):
    """One rule, one owner. The detail screen gates its Import file button on this.

    Without it the button had only `import_file_url` to go on, which stays non-null on a
    published order carrying a hard finding -- so it offered a download the server refuses.
    """
    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner)

    assert service.serialize_row(order)["can_export"] is False

    service.publish(order, actor_user_id=owner)
    published = service.serialize_row(order)
    assert published["can_export"] is True
    assert published["can_export"] == service.worksheet(order)["can_export"]
    assert service.serialize_detail(order)["can_export"] is True

    db.add(
        SODraftFinding(
            id=_uid(),
            company_id=order.company_id,
            project_sales_order_id=order.id,
            severity="hard",
            code="line_arithmetic",
            detail=f"{MARKER} the line does not add up",
        )
    )
    db.flush()

    blocked = service.serialize_row(order)
    assert blocked["can_export"] is False
    # The address of the file is unchanged; permission to fetch it is what moved.
    assert blocked["import_file_url"] is not None


# --------------------------------------------------------------------- parity


def test_the_json_rows_are_the_csv_rows(seeded):
    """The whole point of one builder: the screen and the file cannot disagree.

    The only normalisation is CSV's blank cell for an absent value, which JSON carries as
    null. Everything else is compared character for character.
    """
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco")
    service, order, _po_row = _built(db, company_id, owner, customer=customer)
    service.publish(order, actor_user_id=owner)

    worksheet = service.worksheet(order)
    _filename, body = service.import_file(order)

    def cells(line):
        return [
            line["item_code"] or "",
            line["description"] or "",
            line["reserve_qty"],
            line["qty"],
            line["delivery_date"].isoformat() if line["delivery_date"] else "",
            line["uom"] or "",
            line["unit_price"],
            line["discount"] or "",
            line["total"],
        ]

    assert [cells(line) for line in worksheet["lines"]] == _csv_line_rows(body)


def test_the_csv_is_unchanged_by_the_shared_builder(seeded):
    """The pinned header string from ``test_project_so_draft``, re-pinned at this seam."""
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco")
    service, order, po = _built(db, company_id, owner, customer=customer)
    service.publish(order, actor_user_id=owner)

    filename, body = service.import_file(order)

    assert filename == f"{order.provisional_ref}.csv"
    assert f"Your Ref No.,{po.po_number}" in body
    assert "Terms,*Net 60 days" in body
    assert "***TOWER***" in body
    assert "Item,Description,Reserve Qty,Qty,Delivery Date,UOM,U/Price,Disc.,Total" in body


# ---------------------------------------------------------------------- route


def _client(db, user_id: str):
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

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(ALL_SLUGS)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture()
def api(seeded):
    from app.models.base import company_scope

    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner, customer=_customer(db, "Buimaco"))
    service.publish(order, actor_user_id=owner)
    db.commit()
    client, originals = _client(db, owner)
    try:
        with company_scope(db, frozenset({company_id})):
            yield client, service, order
    finally:
        _restore(originals)


def test_the_route_serves_the_worksheet(api):
    client, service, order = api

    response = client.get(f"{BASE}/sales-orders/{order.id}/worksheet")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == order.id
    assert body["provisional_ref"] == order.provisional_ref
    assert body["can_export"] is True
    assert body["header"]["terms"] == "*Net 60 days"
    assert body["lines"] and body["lines"][0]["reserve_qty"] == "0"
    # Money and quantities stay strings on the wire: a float round trip loses cents.
    assert isinstance(body["total_amount"], str)
    assert isinstance(body["lines"][0]["unit_price"], str)
    assert body["import_file_url"].endswith(f"/sales-orders/{order.id}/import-file")


def test_the_route_serves_the_import_file_of_an_exportable_order(api):
    client, _service, order = api

    response = client.get(f"{BASE}/sales-orders/{order.id}/import-file")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="{order.provisional_ref}.csv"' in (
        response.headers.get("content-disposition") or ""
    )
    assert "Item,Description,Reserve Qty,Qty,Delivery Date,UOM,U/Price,Disc.,Total" in (
        response.text
    )


def test_the_route_refuses_the_import_file_the_gate_has_not_cleared(api):
    """`can_export` is a rule, not advice: the server refuses the fetch itself.

    A screen that respects it is not the enforcement. Anybody with the url -- a bookmark, a
    second tab open since before the finding was raised, curl -- would otherwise walk a
    document AutoCount must not import straight out of the building.
    """
    client, service, order = api
    service.db.add(
        SODraftFinding(
            id=_uid(),
            company_id=order.company_id,
            project_sales_order_id=order.id,
            severity="hard",
            code="line_arithmetic",
            detail=f"{MARKER} the line does not add up",
        )
    )
    service.db.flush()

    response = client.get(f"{BASE}/sales-orders/{order.id}/import-file")

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "so_export_blocked"


def test_the_route_refuses_the_import_file_of_an_unpublished_order(seeded):
    """A draft is not a document yet, so there is nothing to import."""
    from app.models.base import company_scope

    db, company_id, owner = seeded
    service, order, _po_row = _built(db, company_id, owner, customer=_customer(db, "Buimaco"))
    db.commit()
    client, originals = _client(db, owner)
    try:
        with company_scope(db, frozenset({company_id})):
            response = client.get(f"{BASE}/sales-orders/{order.id}/import-file")
    finally:
        _restore(originals)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "so_export_blocked"


def test_the_route_404s_on_an_unknown_sales_order(api):
    client, _service, _order = api

    response = client.get(f"{BASE}/sales-orders/{uuid.uuid4()}/worksheet")

    assert response.status_code == 404, response.text


def test_the_route_refuses_a_caller_with_no_credentials():
    """No Bearer token and no X-API-Key: the same door every sibling route has.

    Built WITHOUT the `with TestClient(app)` context manager on purpose. Entering it runs
    the app's startup event, which registers the global SQLAlchemy audit listeners for the
    rest of the session -- and a later test that counts audit rows on its own writes then
    sees history it never made. Measured: this one line failed all three
    `test_project_task_checklist.py` history tests. A bare client routes the request
    exactly the same; the startup hooks are what we do not want.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    assert not app.dependency_overrides, "a sibling test left its overrides behind"
    response = TestClient(app).get(f"{BASE}/sales-orders/{uuid.uuid4()}/worksheet")

    assert response.status_code in (401, 403), response.text
