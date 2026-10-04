"""P6 delivery-schedule intake: extraction, reconciliation and phases (UAC group E).

Four rules carry the design, and every test below is one of them:

- **Reconciliation is per COLUMN, never per document** (AC-E3, measured 2026-08-02).
  29 of 37 columns reconciled on the first vision pass of the real R1 schedule. A
  document-level accept would have rejected it, and every other real schedule with it.
- **The checksum has two independent sources**: the schedule's own TOTAL QTY row,
  transcribed rather than computed, and the PO quantity for the same product.
- **It reconciles against the PO VERSION the schedule NAMES** (finding G1), not the
  current amended state. A schedule issued before a handwritten cancellation still
  reconciles, because the customer considers it correct.
- **Phase identity is `(area_group, sequence)`, never the label** (finding G6). The
  COMMON AREA rows carry no label at all, and matching by label collapsed three real
  phases into one.

The unit tests feed the persistence layer a canned page payload shaped exactly like the
extractor's output, so they never call the live model. One further test DOES call it,
against the client's own committed schedule, and is skipped without a key.
"""
from __future__ import annotations

import copy
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings as app_settings
from app.models.notification import Notification
from app.models.order import Customer
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    SO_STATUS_PUBLISHED,
    CustomerItemCodeMap,
    DeliverySchedule,
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    ProjectDeliveryPhase,
    ProjectPOLine,
    ProjectPOVersion,
    ProjectSalesOrder,
)
from app.models.projects import ProjectParty
from app.models.user import User
from app.services import document_extraction, project_schedule_service, project_seed_service
from app.services.document_extraction import RenderedPage
from app.services.error_handler import AppException
from app.services.llm_provider import ChatResult
from app.services.project_schedule_service import (
    ProjectScheduleService,
    _parse_date,
    parse_text_matrix,
)

from ._pg_fixture import blank_session

MARKER = "zzt-schedule"

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "sorento_crm_frontend"
    / "e2e"
    / "fixtures"
    / "project-cs"
)


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, code: str, name: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Set")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=name,
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, company_id: str) -> Customer:
    row = Customer(
        id=_uid(),
        company_id=company_id,
        customer_code=f"ZZT-{_uid()[:8]}",
        customer_name=f"{MARKER} Buimaco",
    )
    db.add(row)
    db.flush()
    return row


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tuju {_uid()[:12]}",
    )


def _po_with_version(db, project, owner: str, customer, lines):
    """A customer PO, its issuer bridged to a debtor, and one extracted document version.

    ``lines`` is ``[(product, qty)]``. The PO quantity a schedule column reconciles
    against comes from THESE rows: the version the schedule names, as printed.
    """
    from app.services import project_po_service as pos

    party = ProjectParty(
        id=_uid(),
        company_id=project.company_id,
        party_type="main_contractor",
        name=f"{MARKER} contractor {_uid()[:6]}",
        customer_id=customer.id,
    )
    db.add(party)
    db.flush()

    po = pos.create_po(
        db,
        project=project,
        actor_user_id=owner,
        payload={
            "po_source": "contractor_direct",
            "issuing_party_id": party.id,
            "po_number": f"ZZT-PO-{_uid()[:6]}",
            "po_date": date(2026, 1, 16),
        },
    )
    version = ProjectPOVersion(
        id=_uid(), company_id=project.company_id, purchase_order_id=po.id, version_no=1,
        extraction_state="done",
    )
    db.add(version)
    db.flush()
    for index, (product, qty) in enumerate(lines, start=1):
        db.add(
            ProjectPOLine(
                id=_uid(),
                company_id=project.company_id,
                po_version_id=version.id,
                line_no=index,
                stock_code_raw=product.product_code,
                qty=Decimal(qty),
                resolved_product_id=product.id,
                resolution_source="code",
            )
        )
    db.flush()
    return po, version


def _schedule_version(db, project, po, po_version, *, version_no=1, label=None):
    schedule = (
        db.query(DeliverySchedule)
        .filter(DeliverySchedule.purchase_order_id == po.id)
        .first()
    )
    if schedule is None:
        schedule = DeliverySchedule(
            id=_uid(),
            company_id=project.company_id,
            project_id=project.id,
            purchase_order_id=po.id,
        )
        db.add(schedule)
        db.flush()
    version = DeliveryScheduleVersion(
        id=_uid(),
        company_id=project.company_id,
        delivery_schedule_id=schedule.id,
        version_no=version_no,
        revision_label=label,
        po_version_id=po_version.id,
        extraction_state="queued",
    )
    db.add(version)
    db.flush()
    return version


def _page(products, phases, cells, totals) -> dict:
    """One page of the extractor's answer, in its own JSON shape."""
    return {
        "header": {"po_ref": "HQ 26/01/121", "schedule_date": "2026-03-04"},
        "products": products,
        "phases": phases,
        "cells": cells,
        "reported_totals": totals,
    }


def _two_column_page(*, wc_cells, basin_cells, wc_total, basin_total):
    """The shape of every unit test below: two products, two TOWER rows, two
    UNLABELED COMMON AREA rows. The unlabeled pair is finding G6 in miniature."""
    return _page(
        products=[
            {"col": 1, "customer_code": "BUI-HB-SRTWC8613-RL", "code": "SRTWC8613-RL",
             "name": "One-Piece WC"},
            {"col": 2, "customer_code": "BUI-HB-SRTWB7055", "code": "SRTWB7055",
             "name": "Counter-Top Basin"},
        ],
        phases=[
            {"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
             "delivery_date": "2026-07-01"},
            {"row": 2, "area_group": "TOWER", "label": "Level 8 & 10",
             "delivery_date": "2026-08-03"},
            {"row": 3, "area_group": "COMMON AREA", "label": None,
             "delivery_date": "2026-07-01"},
            {"row": 4, "area_group": "COMMON AREA", "label": None,
             "delivery_date": "2027-06-01"},
        ],
        cells=(
            [{"row": row, "col": 1, "qty": qty} for row, qty in wc_cells]
            + [{"row": row, "col": 2, "qty": qty} for row, qty in basin_cells]
        ),
        totals=[{"col": 1, "qty": wc_total}, {"col": 2, "qty": basin_total}],
    )


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        yield db, company_id, owner


@pytest.fixture()
def scenario(seeded):
    """A project, a PO version quoting 200 WC and 100 basins, and an empty schedule."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    customer = _customer(db, company_id)
    # The client's real codes: the whole point of the `BUI-HB-` prefix is that our code
    # is printed inside theirs, and a made-up code would not exercise that.
    wc = _product(db, "SRTWC8613-RL", "One-Piece WC")
    basin = _product(db, "SRTWB7055", "Counter-Top Basin")
    po, po_version = _po_with_version(
        db, project, owner, customer, [(wc, "200"), (basin, "100")]
    )
    version = _schedule_version(db, project, po, po_version)
    return {
        "db": db,
        "owner": owner,
        "project": project,
        "customer": customer,
        "wc": wc,
        "basin": basin,
        "po": po,
        "po_version": po_version,
        "version": version,
    }


def _columns(detail):
    return {c["customer_code_raw"]: c for c in detail["products"]}


def _phase(detail, area_group: str, sequence: int):
    """Phases are addressed by (area_group, sequence), never by sequence alone: every
    area group numbers its own rows from one."""
    return next(
        p
        for p in detail["phases"]
        if p["area_group"] == area_group and p["sequence"] == sequence
    )


# ------------------------------------------------------------------ reconciliation


def test_a_column_reconciles_against_the_reported_total_and_the_po(scenario):
    """AC-E3. Both checks agree, so the column is clean and nobody is asked anything."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    columns = _columns(detail)
    wc = columns["BUI-HB-SRTWC8613-RL"]

    assert wc["column_total"] == "200"
    assert wc["reported_total"] == "200"
    assert wc["po_qty"] == "200"
    assert wc["reconciled"] is True
    assert detail["reconciliation"] == {"reconciled_columns": 2, "total_columns": 2}


def test_a_failing_column_is_flagged_without_failing_the_others(scenario):
    """The measured reason the design is per column: 8 of 37 columns failed on the real
    R1 and a document-level reject would have thrown away the other 29."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    columns = _columns(service.get_version_detail(scenario["version"].id))
    assert columns["BUI-HB-SRTWC8613-RL"]["reconciled"] is False
    assert columns["BUI-HB-SRTWC8613-RL"]["column_total"] == "195"
    assert columns["BUI-HB-SRTWB7055"]["reconciled"] is True

    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert version.reconciled_columns == 1
    assert version.total_columns == 2


def test_a_schedule_short_of_the_po_is_a_warning_and_not_a_blocker(scenario):
    """Captain, 2026-08-18: "for short quantity is okay though, cause schedule can be
    partial".

    The customer schedules part of what they ordered now and the rest on a later
    document, so a column asking for less than the PO ordered is the NORMAL state of a
    live project. It says so, and it does not hold up the confirm.
    """
    db = scenario["db"]
    service = ProjectScheduleService(db)
    # The document's own TOTAL QTY row agrees with the cells: nothing was misread, the
    # sheet simply schedules 150 of the 200 ordered.
    page = _two_column_page(
        wc_cells=[(1, 100), (2, 50)], basin_cells=[(3, 60), (4, 40)],
        wc_total=150, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    wc = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert wc["reconciled"] is True
    assert wc["reason"] is None
    assert wc["warning"] == (
        "The schedule asks for 150 of the 200 on the purchase order; the remaining 50 "
        "is expected on a later schedule."
    )
    # It counts as reconciled, so the confirm goes through with no acknowledgement.
    assert detail["reconciliation"] == {"reconciled_columns": 2, "total_columns": 2}
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()
    assert db.get(DeliveryScheduleVersion, scenario["version"].id).confirmed_at is not None


def _staleify(db, version, *, index: int = 0, reason: str | None = None) -> None:
    """Put a version back in the state a read done under the OLD rules left behind.

    A blocking verdict on a column that is merely short, no ``warning`` key at all, and the
    counts to match. This is exactly what the live version held: the payload is written only
    on a WRITE, so the sentence the reader stored survives every later rule change.
    """
    payload = copy.deepcopy(version.reconciliation_json or {})
    entry = payload["columns"][index]
    entry["reconciled"] = False
    entry["reason"] = reason or (
        "the column adds up to 150, the purchase order says 200"
    )
    entry.pop("warning", None)
    payload["reconciled_columns"] = sum(
        1 for item in payload["columns"] if item.get("reconciled")
    )
    version.reconciliation_json = payload
    flag_modified(version, "reconciliation_json")
    version.reconciled_columns = payload["reconciled_columns"]
    db.flush()


def test_a_stale_stored_refusal_is_read_back_under_the_current_rules(scenario):
    """The bug the screen owner measured: reading a version nobody has written to served
    the refusal it was given when the document was read.

    The stored payload only ever changed on a write, so 31 of 44 columns "reconciled"
    until an unrelated dismissal happened to save the row. The read path refreshes now, and
    an OPEN version converges so the confirm cannot disagree with the screen.
    """
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 100), (2, 50)], basin_cells=[(3, 60), (4, 40)],
        wc_total=150, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()
    _staleify(db, scenario["version"])

    # What the screen was being told before the read path refreshed anything.
    stale = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert stale.reconciliation_json["columns"][0]["reconciled"] is False
    assert stale.reconciled_columns == 1

    detail = service.get_version_detail(scenario["version"].id)
    wc = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert wc["reconciled"] is True
    assert wc["reason"] is None
    assert "remaining 50" in (wc["warning"] or "")
    assert detail["reconciliation"] == {"reconciled_columns": 2, "total_columns": 2}

    # The open version converged, so the confirm sees what the screen saw.
    db.expire_all()
    stored = (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id == scenario["version"].id)
        .one()
    )
    assert stored.reconciliation_json["columns"][0]["reconciled"] is True
    assert stored.reconciled_columns == 2


def test_polling_a_version_that_has_not_been_read_writes_nothing(scenario):
    """The review screen polls this every three seconds while a document is being read.
    An empty payload must not be "refreshed" into a written one on every poll."""
    db = scenario["db"]
    service = ProjectScheduleService(db)

    detail = service.get_version_detail(scenario["version"].id)
    assert detail["products"] == []
    assert detail["reconciliation"] == {"reconciled_columns": 0, "total_columns": 0}

    db.expire_all()
    stored = (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id == scenario["version"].id)
        .one()
    )
    assert stored.reconciliation_json is None


def test_run_extraction_commits_progress_once_per_page(scenario, monkeypatch):
    """B3 (19 Aug follow-up): the schedule reader wires ``on_page`` the same way the
    PO reader does. Proved by counting commits: without a per-page commit there are
    exactly two (the RUNNING stamp, the final write); with it there is one more per
    page that actually answered.
    """
    db = scenario["db"]
    service = ProjectScheduleService(db)

    monkeypatch.setattr(
        ProjectScheduleService, "_document_bytes",
        lambda self, version: (b"ZZT", "application/pdf"),
    )
    monkeypatch.setattr(project_schedule_service, "parse_text_matrix", lambda *a, **k: {})
    monkeypatch.setattr(
        document_extraction, "_render_pages_rich",
        lambda *a, **k: [RenderedPage(image_b64="i", image_mime="image/jpeg") for _ in range(3)],
    )

    class _StubProvider:
        name = "stub"

        def chat(self, messages, **kwargs):
            return ChatResult(
                content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2
            )

    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _StubProvider())
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(app_settings, "document_ai_page_concurrency", 3, raising=False)

    commit_count = {"n": 0}
    original_commit = db.commit

    def counting_commit(*args, **kwargs):
        commit_count["n"] += 1
        return original_commit(*args, **kwargs)

    db.commit = counting_commit

    result = service.run_extraction(scenario["version"].id)

    assert result["status"] in ("done", "partial")
    # RUNNING stamp + 3 page commits + the final persist_pages commit.
    assert commit_count["n"] >= 5


def test_the_polled_detail_reports_each_page_as_it_is_read(scenario, monkeypatch):
    """W3 (PR #1265 round 3): the schedule screen's progress bar is the pages read of the
    total, and it only moves if the detail the screen polls says so WHILE the version is
    still RUNNING. Read through ``get_version_detail`` (the route's own read) after every
    commit, so a progress key the poller never sees fails here even if the commits happen.
    """
    db = scenario["db"]
    service = ProjectScheduleService(db)
    version = scenario["version"]
    version.extracted_json = {"page_count": 3}
    db.flush()

    monkeypatch.setattr(
        ProjectScheduleService, "_document_bytes",
        lambda self, version: (b"ZZT", "application/pdf"),
    )
    monkeypatch.setattr(project_schedule_service, "parse_text_matrix", lambda *a, **k: {})
    monkeypatch.setattr(
        document_extraction, "_render_pages_rich",
        lambda *a, **k: [RenderedPage(image_b64="i", image_mime="image/jpeg") for _ in range(3)],
    )

    class _StubProvider:
        name = "stub"

        def chat(self, messages, **kwargs):
            return ChatResult(content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _StubProvider())
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(app_settings, "document_ai_page_concurrency", 1, raising=False)

    polled: list[tuple[str, int, int]] = []
    original_commit = db.commit

    polling = {"now": False}

    def polling_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        if version.extraction_state == "running" and not polling["now"]:
            polling["now"] = True
            try:
                detail = service.get_version_detail(version.id)
            finally:
                polling["now"] = False
            polled.append(
                (detail["extraction_state"], detail["pages_extracted"], detail["page_count"])
            )
        return result

    db.commit = polling_commit

    result = service.run_extraction(version.id)
    db.commit = original_commit

    assert result["status"] in ("done", "partial")
    assert polled == [
        ("running", 0, 3),
        ("running", 1, 3),
        ("running", 2, 3),
        ("running", 3, 3),
    ]


def test_a_retry_after_a_failed_read_does_not_pin_progress_to_the_old_page_count(scenario, monkeypatch):
    """Same defect as the PO reader's sibling test: a retry runs `run_extraction` again
    on the SAME version row, and a failed attempt can leave stale entries in
    ``extracted_json['pages']``. Checked WHILE the retry is RUNNING, because
    ``persist_pages`` overwrites ``pages`` wholesale at the end regardless of what
    ``_on_page`` accumulated mid-read."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    version = scenario["version"]

    version.extracted_json = {
        "pages": [
            {"page_no": 1, "data": {"cells": []}},
            {"page_no": 2, "data": {"cells": []}},
        ]
    }
    db.flush()

    monkeypatch.setattr(
        ProjectScheduleService, "_document_bytes",
        lambda self, version: (b"ZZT", "application/pdf"),
    )
    monkeypatch.setattr(project_schedule_service, "parse_text_matrix", lambda *a, **k: {})
    monkeypatch.setattr(
        document_extraction, "_render_pages_rich",
        lambda *a, **k: [RenderedPage(image_b64="i", image_mime="image/jpeg") for _ in range(1)],
    )

    class _StubProvider:
        name = "stub"

        def chat(self, messages, **kwargs):
            return ChatResult(content="{}", prompt_tokens=1, completion_tokens=1, total_tokens=2)

    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _StubProvider())
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(app_settings, "document_ai_page_concurrency", 1, raising=False)

    progress_after_each_commit: list[int] = []
    original_commit = db.commit

    def snapshotting_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        progress_after_each_commit.append(
            len((version.extracted_json or {}).get("pages") or [])
        )
        return result

    db.commit = snapshotting_commit

    result = service.run_extraction(version.id)

    assert result["status"] in ("done", "partial")
    # The RUNNING-stamp commit, right after the reset, must show 0 - not the 2 stale
    # entries the failed attempt left behind.
    assert progress_after_each_commit[0] == 0
    # Never exceeds this retry's one real page - never 2 (stale alone) or 3 (stale + new).
    assert max(progress_after_each_commit) == 1
    assert len((version.extracted_json or {}).get("pages") or []) == 1


def test_reading_a_confirmed_version_refreshes_the_display_and_writes_nothing(scenario):
    """What was agreed is the record. A confirmed version reports the current reading of
    its numbers, and the row it was bound as stays exactly as it was bound."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 100), (2, 50)], basin_cells=[(3, 60), (4, 40)],
        wc_total=150, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()
    _staleify(db, scenario["version"])

    detail = service.get_version_detail(scenario["version"].id)
    wc = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert wc["reconciled"] is True
    assert "remaining 50" in (wc["warning"] or "")

    db.expire_all()
    stored = (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id == scenario["version"].id)
        .one()
    )
    assert stored.reconciliation_json["columns"][0]["reconciled"] is False
    assert stored.reconciled_columns == 1


def test_a_dismissal_survives_the_read_path_recompute(scenario):
    """The recompute re-judges the numbers; it does not re-open a decision a person made
    about them."""
    db = scenario["db"]
    service = _failing_scenario(scenario)
    service.dismiss_column_verdict(
        scenario["version"].id, 0, dismissed=True, reason="Confirmed by email",
        actor_user_id=scenario["owner"],
    )
    db.flush()
    # A stale entry that still carries the dismissal, which is what an old payload holds
    # once the rules underneath it have moved.
    _staleify(db, scenario["version"], reason="the column adds up to 195, the schedule's own total says 200")

    column = _columns(service.get_version_detail(scenario["version"].id))[
        "BUI-HB-SRTWC8613-RL"
    ]
    assert column["dismissed"] is True
    assert column["dismissed_reason"] == "Confirmed by email"
    assert column["reconciled"] is True
    # Still a genuine disagreement underneath, still reported.
    assert "own total" in (column["reason"] or "")


def test_document_url_is_none_when_the_schedule_file_cannot_be_shown(scenario, monkeypatch):
    """S2 (review of #1265, R13): the Documents tab shows "This PDF is not available yet" only
    on a falsy document_url. An attachment ROW can outlive its OBJECT, and a URL signed for
    a missing object (or the raw path handed back when signing fails) lands in an iframe as
    the storage error page. Same gate as the PO screen (#1237 B1): the object must exist and
    the URL must actually be signed."""
    from app.models.resources import Attachment
    from app.services import storage_router

    db = scenario["db"]
    version = scenario["version"]
    attachment = Attachment(
        original_filename="schedule.pdf",
        stored_filename="schedule.pdf",
        file_path=f"project-schedule/{version.id}/schedule.pdf",
        entity_type="delivery_schedule_version",
        entity_id=version.id,
        storage_provider="s3",
    )
    db.add(attachment)
    db.flush()
    version.attachment_id = attachment.id
    db.flush()

    class _FakeBackend:
        def __init__(self, exists: bool, signs: bool = True):
            self._exists = exists
            self._signs = signs

        def file_exists(self, key):
            return self._exists

        def get_signed_url(self, key, expires_in=3600):
            if not self._signs:
                raise RuntimeError("no credentials")
            return f"https://signed.example/{key}"

    service = ProjectScheduleService(db)

    storage_router.clear_signed_url_cache()
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend(False))
    assert service.document_url(version) is None

    storage_router.clear_signed_url_cache()
    monkeypatch.setattr(
        storage_router, "get_backend", lambda provider: _FakeBackend(True, signs=False)
    )
    assert service.document_url(version) is None

    storage_router.clear_signed_url_cache()
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend(True))
    assert service.document_url(version) == (
        f"https://signed.example/project-schedule/{version.id}/schedule.pdf"
    )


def test_the_schedule_version_names_its_attachment_for_the_in_app_pdf_viewer(
    scenario, monkeypatch
):
    """Merge of #1256: every in-app PDF draws through PdfViewer, which reads the bytes with a
    script. The signed document_url is cross-origin with no CORS headers, so the viewer
    reads them through /resource-management/attachments/{id}/download instead, the same way
    the PO screen does. The id rides along only when there is a file to show."""
    from app.models.resources import Attachment
    from app.schemas.project_schedule import DeliveryScheduleVersionResponse
    from app.services import storage_router

    db = scenario["db"]
    version = scenario["version"]
    service = ProjectScheduleService(db)

    body = service.get_version_detail(version.id)
    assert body["attachment_id"] is None
    assert body["document_url"] is None

    attachment = Attachment(
        original_filename="schedule.pdf",
        stored_filename="schedule.pdf",
        file_path=f"project-schedule/{version.id}/schedule.pdf",
        entity_type="delivery_schedule_version",
        entity_id=version.id,
        storage_provider="s3",
    )
    db.add(attachment)
    db.flush()
    version.attachment_id = attachment.id
    db.flush()

    class _FakeBackend:
        def file_exists(self, key):
            return True

        def get_signed_url(self, key, expires_in=3600):
            return f"https://signed.example/{key}"

    storage_router.clear_signed_url_cache()
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend())

    body = service.get_version_detail(version.id)
    assert body["document_url"]
    assert body["attachment_id"] == str(attachment.id)
    assert DeliveryScheduleVersionResponse.model_validate(body).attachment_id == str(
        attachment.id
    )


def test_a_schedule_asking_for_more_than_the_po_ordered_still_blocks(scenario):
    """The other direction is the one that is a concern: a schedule cannot commit
    quantity nobody bought."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 100)], basin_cells=[(3, 60), (4, 40)],
        wc_total=220, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    wc = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert wc["reconciled"] is False
    assert wc["warning"] is None
    assert "more than" in (wc["reason"] or "")
    assert detail["reconciliation"] == {"reconciled_columns": 1, "total_columns": 2}

    with pytest.raises(AppException) as excinfo:
        service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    assert excinfo.value.status_code == 409


def test_phases_disagreeing_with_the_schedules_own_total_still_blocks(scenario):
    """Not a partial schedule: the cells and the TOTAL QTY row on the SAME document
    disagree, so one of the two was misread and neither can be trusted yet."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    wc = _columns(service.get_version_detail(scenario["version"].id))[
        "BUI-HB-SRTWC8613-RL"
    ]
    assert wc["reconciled"] is False
    assert wc["warning"] is None
    assert "own total" in (wc["reason"] or "")


def test_a_column_with_no_po_quantity_still_blocks(scenario):
    """Silence is not agreement. An unmatched column is unchecked, not clean."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None, "name": "Mystery"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    column = service.get_version_detail(scenario["version"].id)["products"][0]
    assert column["reconciled"] is False
    assert column["warning"] is None
    assert "no purchase order quantity" in (column["reason"] or "")


def test_an_empty_cell_is_never_stored_as_a_zero(scenario):
    """A blank means this phase does not take this product. Writing zeroes would make
    every phase look like it was planned for every product."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    cells = (
        db.query(DeliveryScheduleCell)
        .filter(DeliveryScheduleCell.version_id == scenario["version"].id)
        .all()
    )
    # Four filled cells out of eight possible: the other four were blank on the page.
    assert len(cells) == 4
    assert all(cell.qty != 0 for cell in cells)


def test_the_schedule_reconciles_against_the_po_version_it_names(scenario):
    """Finding G1. The named version still carries the 200 the schedule was drawn from;
    a later version cancelling half of it must not retrospectively reject this document."""
    db = scenario["db"]
    service = ProjectScheduleService(db)

    later = ProjectPOVersion(
        id=_uid(),
        company_id=scenario["project"].company_id,
        purchase_order_id=scenario["po"].id,
        version_no=2,
        extraction_state="done",
    )
    db.add(later)
    db.flush()
    db.add(
        ProjectPOLine(
            id=_uid(),
            company_id=scenario["project"].company_id,
            po_version_id=later.id,
            line_no=1,
            stock_code_raw=scenario["wc"].product_code,
            qty=Decimal("100"),
            resolved_product_id=scenario["wc"].id,
        )
    )
    db.flush()

    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    columns = _columns(service.get_version_detail(scenario["version"].id))
    assert columns["BUI-HB-SRTWC8613-RL"]["po_qty"] == "200"
    assert columns["BUI-HB-SRTWC8613-RL"]["reconciled"] is True


def test_a_quantity_cancelled_on_the_named_version_still_reconciles_and_is_reported(
    scenario,
):
    """AC-E3a. The cancelled quantity stays visible as a note rather than silently
    changing the number the schedule is measured against."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    line = (
        db.query(ProjectPOLine)
        .filter(
            ProjectPOLine.po_version_id == scenario["po_version"].id,
            ProjectPOLine.resolved_product_id == scenario["wc"].id,
        )
        .first()
    )
    line.is_cancelled = True
    db.flush()

    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    columns = _columns(service.get_version_detail(scenario["version"].id))
    wc = columns["BUI-HB-SRTWC8613-RL"]
    assert wc["reconciled"] is True
    assert wc["cancelled_qty"] == "200"
    assert "cancelled" in (wc["note"] or "").lower()


# ------------------------------------------------------------------------- phases


def test_two_unlabeled_common_area_rows_stay_two_phases(scenario):
    """Finding G6, measured: matching on the label collapsed three real COMMON AREA
    rows into one. Identity is (area_group, sequence)."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    common = (
        db.query(ProjectDeliveryPhase)
        .filter(
            ProjectDeliveryPhase.project_id == scenario["project"].id,
            ProjectDeliveryPhase.area_group == "COMMON AREA",
        )
        .order_by(ProjectDeliveryPhase.sequence)
        .all()
    )
    assert [p.sequence for p in common] == [1, 2]
    assert [p.label for p in common] == [None, None]
    assert [p.delivery_date for p in common] == [date(2026, 7, 1), date(2027, 6, 1)]


def test_re_extracting_a_version_does_not_duplicate_its_phases(scenario):
    """Phases are upserted on their identity: a re-read after a prompt change must not
    leave the project with two of every row."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    phases = (
        db.query(ProjectDeliveryPhase)
        .filter(ProjectDeliveryPhase.project_id == scenario["project"].id)
        .all()
    )
    assert len(phases) == 4
    cells = (
        db.query(DeliveryScheduleCell)
        .filter(DeliveryScheduleCell.version_id == scenario["version"].id)
        .count()
    )
    assert cells == 4


# ------------------------------------------------------------- customer code map


def test_a_remembered_customer_code_resolves_the_next_schedule_by_itself(scenario):
    """AC-E4. A human identifies `BUI-HB-*` once per customer; the next schedule from
    that customer resolves silently and says that is what happened."""
    db = scenario["db"]
    service = ProjectScheduleService(db)

    # A code our catalogue cannot recover on its own: nothing in it matches a product.
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None,
                   "name": "Mystery item"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    columns = service.get_version_detail(scenario["version"].id)["products"]
    assert columns[0]["product_id"] is None
    assert columns[0]["resolution_source"] is None

    service.resolve_product_column(
        scenario["version"].id, 0, scenario["wc"].id, actor_user_id=scenario["owner"]
    )
    db.flush()

    mapped = (
        db.query(CustomerItemCodeMap)
        .filter(CustomerItemCodeMap.customer_code == "BUI-HB-XX9931")
        .first()
    )
    assert mapped is not None
    assert mapped.customer_id == scenario["customer"].id
    assert mapped.product_id == scenario["wc"].id

    # Second schedule from the same customer: nobody is asked again.
    second = _schedule_version(
        db, scenario["project"], scenario["po"], scenario["po_version"], version_no=2
    )
    service.persist_pages(second, [(1, page)])
    db.flush()

    columns = service.get_version_detail(second.id)["products"]
    assert columns[0]["product_id"] == scenario["wc"].id
    assert columns[0]["resolution_source"] == "map"
    assert columns[0]["product_code"] == scenario["wc"].product_code


def test_a_column_resolves_from_the_code_inside_the_customers_own(scenario):
    """`BUI-HB-SRTWC8613-RL` carries our code. Recovering it needs no map and no model."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": f"BUI-HB-{scenario['wc'].product_code}",
                   "code": None, "name": "One-Piece WC"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    column = service.get_version_detail(scenario["version"].id)["products"][0]
    assert column["product_id"] == scenario["wc"].id
    assert column["resolution_source"] == "code"


def test_an_unresolved_column_falls_through_to_trigram_similarity(scenario):
    """Neither the map nor an embedded code places `SRTWC8613-RX` (a typo of the real
    `SRTWC8613-RL`, no `BUI-HB-` prefix to peel), so it is the fuzzy tier or nothing."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "SRTWC8613-RX", "code": None,
                   "name": "One-Piece WC (typo)"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    column = service.get_version_detail(scenario["version"].id)["products"][0]
    assert column["product_id"] == scenario["wc"].id
    assert column["resolution_source"] == "trigram"


def test_the_trigram_match_is_batched_into_one_query(scenario):
    """B4 (19 Aug follow-up): every column left over after the exact tiers used to run
    its OWN `ORDER BY similarity(...) DESC` query. Two columns here both miss the exact
    tiers and both want the fuzzy one; only ONE statement naming `unnest` - the batched
    query's own signature - may run, and both must still resolve to the right product.
    """
    db = scenario["db"]
    hose = _product(db, "SRTFH1520-CR", "Flexible Hose")
    hose.company_id = scenario["wc"].company_id
    db.flush()

    page = _page(
        products=[
            {"col": 1, "customer_code": "SRTWC8613-RX", "code": None, "name": "WC typo"},
            {"col": 2, "customer_code": "SRTFH1520-CX", "code": None, "name": "Hose typo"},
        ],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}, {"row": 1, "col": 2, "qty": 50}],
        totals=[{"col": 1, "qty": 200}, {"col": 2, "qty": 50}],
    )
    service = ProjectScheduleService(db)

    calls: list[str] = []
    original_execute = db.execute

    def counting(statement, *args, **kwargs):
        calls.append(str(statement))
        return original_execute(statement, *args, **kwargs)

    db.execute = counting
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    trigram_selects = [c for c in calls if "unnest" in c]
    assert len(trigram_selects) == 1, trigram_selects

    columns = service.get_version_detail(scenario["version"].id)["products"]
    by_code = {c["customer_code_raw"]: c for c in columns}
    assert by_code["SRTWC8613-RX"]["product_id"] == scenario["wc"].id
    assert by_code["SRTWC8613-RX"]["resolution_source"] == "trigram"
    assert by_code["SRTFH1520-CX"]["product_id"] == hose.id
    assert by_code["SRTFH1520-CX"]["resolution_source"] == "trigram"


# --------------------------------------------------------------- addressing a column


def test_every_cell_says_which_column_it_belongs_to(scenario):
    """An unidentified column has no product id, so keying the grid on product alone
    leaves it rendering empty while still showing a total, which reads as a bug."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None, "name": "Mystery"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    assert detail["products"][0]["product_index"] == 0
    assert detail["products"][0]["product_id"] is None
    assert detail["cells"][0]["product_index"] == 0
    assert detail["cells"][0]["product_id"] is None


def test_naming_a_columns_product_reaches_the_database(scenario):
    """The correction has to survive the request, not just the response.

    ``reconciliation_json`` is a plain JSONB column, so mutating the loaded dict and
    assigning the SAME object back leaves SQLAlchemy comparing the value against a
    snapshot that IS the mutated object: equal, therefore no UPDATE. The PUT answered
    200 twice on the live stack while the column kept ``product_id`` null. Everything
    in-memory agrees with itself, so only a read that goes back to the row catches it.
    """
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None, "name": "Mystery"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    service.resolve_product_column(
        scenario["version"].id, 0, scenario["wc"].id, actor_user_id=scenario["owner"]
    )
    db.flush()

    # Throw away every in-memory value and read the row again.
    version_id = scenario["version"].id
    db.expire_all()
    stored = (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id == version_id)
        .one()
    )
    column = (stored.reconciliation_json or {})["columns"][0]
    assert column["product_id"] == scenario["wc"].id
    assert column["key"] == f"product:{scenario['wc'].id}"
    assert column["resolution_source"] == "manual"


def test_an_unidentified_column_can_be_corrected_by_its_index(scenario):
    """The column nobody has named yet is exactly the column somebody is fixing."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None, "name": "Mystery"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 190}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    phase_one = _phase(detail, "TOWER", 1)
    service.update_cells(
        scenario["version"].id,
        [{"phase_id": phase_one["id"], "product_index": 0, "qty": "200"}],
    )
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    assert detail["products"][0]["column_total"] == "200"
    assert [cell["qty"] for cell in detail["cells"]] == ["200"]


# ----------------------------------------------------------------------- listings


def test_a_project_lists_its_schedules_with_the_po_that_named_them(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    rows = service.list_schedules(scenario["project"].id)
    assert len(rows) == 1
    assert rows[0]["po_number"] == scenario["po"].po_number
    assert rows[0]["latest_version_no"] == 1
    assert rows[0]["version_count"] == 1
    assert (rows[0]["reconciled_columns"], rows[0]["total_columns"]) == (2, 2)

    second = _schedule_version(
        db, scenario["project"], scenario["po"], scenario["po_version"],
        version_no=2, label="REVISED 1",
    )
    db.flush()
    rows = service.list_schedules(scenario["project"].id)
    assert rows[0]["version_count"] == 2
    assert rows[0]["latest_version_id"] == second.id

    history = service.list_versions(rows[0]["id"])
    assert [row["version_no"] for row in history] == [2, 1]
    assert history[0]["revision_label"] == "REVISED 1"


# ----------------------------------------------------------------- cell correction


def test_correcting_a_cell_flips_the_column_to_reconciled(scenario):
    """The per-column correction path: CS fixes the cells the checksum named, and the
    column stops being a problem without anyone touching the other 36."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    assert _columns(detail)["BUI-HB-SRTWC8613-RL"]["reconciled"] is False
    phase_two = _phase(detail, "TOWER", 2)

    service.update_cells(
        scenario["version"].id,
        [{"phase_id": phase_two["id"], "product_id": scenario["wc"].id, "qty": "80"}],
    )
    db.flush()

    columns = _columns(service.get_version_detail(scenario["version"].id))
    assert columns["BUI-HB-SRTWC8613-RL"]["column_total"] == "200"
    assert columns["BUI-HB-SRTWC8613-RL"]["reconciled"] is True


def test_a_cell_set_to_zero_is_deleted_rather_than_stored(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    phase_two = _phase(detail, "TOWER", 2)
    service.update_cells(
        scenario["version"].id,
        [{"phase_id": phase_two["id"], "product_id": scenario["wc"].id, "qty": "0"}],
    )
    db.flush()

    remaining = (
        db.query(DeliveryScheduleCell)
        .filter(
            DeliveryScheduleCell.version_id == scenario["version"].id,
            DeliveryScheduleCell.phase_id == phase_two["id"],
            DeliveryScheduleCell.product_id == scenario["wc"].id,
        )
        .count()
    )
    assert remaining == 0


# ------------------------------------------------------------------------ confirm


def test_confirm_is_refused_while_a_column_is_unreconciled(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    assert excinfo.value.status_code == 409
    assert "BUI-HB-SRTWC8613-RL" in str(excinfo.value.detail)


def test_confirm_proceeds_when_the_failure_is_acknowledged_with_a_reason(scenario):
    """A person may still be right about a column the arithmetic doubts, but their name
    and their reason go on the record."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    service.confirm(
        scenario["version"].id,
        actor_user_id=scenario["owner"],
        acknowledge_unreconciled=True,
        reason="Customer confirmed 75 by email on 4 March",
    )
    db.flush()

    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert version.confirmed_at is not None
    assert version.confirmed_by == scenario["owner"]
    assert (
        version.reconciliation_json["acknowledgement"]["reason"]
        == "Customer confirmed 75 by email on 4 March"
    )


def test_acknowledging_without_a_reason_is_refused(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.confirm(
            scenario["version"].id,
            actor_user_id=scenario["owner"],
            acknowledge_unreconciled=True,
            reason="   ",
        )
    assert excinfo.value.status_code == 422


def test_confirm_is_refused_while_part_of_the_document_is_unread(scenario):
    """A page that did not answer takes its whole columns with it, so nothing flags
    them. Binding that would commit a schedule with products silently missing."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    scenario["version"].extraction_state = "partial"
    scenario["version"].extraction_error = "Pages 5, 6 could not be read."
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    assert excinfo.value.status_code == 409
    assert "Pages 5, 6" in str(excinfo.value.detail)

    service.confirm(
        scenario["version"].id,
        actor_user_id=scenario["owner"],
        acknowledge_unreconciled=True,
        reason="Pages 5 and 6 keyed in by hand from the fax",
    )
    db.flush()
    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert version.reconciliation_json["acknowledgement"]["partial_read"] is True


def test_confirm_promotes_the_dates_this_version_carries(scenario):
    """A revision moves dates. The phase row keeps the OLD date until somebody confirms
    the new document, so the delta engine still has both ends of the move."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    first = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, first)])
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()

    revised = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    revised["phases"][0]["delivery_date"] = "2027-01-07"
    second = _schedule_version(
        db, scenario["project"], scenario["po"], scenario["po_version"],
        version_no=2, label="REVISED 1",
    )
    service.persist_pages(second, [(1, revised)])
    db.flush()

    phase = (
        db.query(ProjectDeliveryPhase)
        .filter(
            ProjectDeliveryPhase.project_id == scenario["project"].id,
            ProjectDeliveryPhase.area_group == "TOWER",
            ProjectDeliveryPhase.sequence == 1,
        )
        .first()
    )
    assert phase.delivery_date == date(2026, 7, 1)

    detail = service.get_version_detail(second.id)
    row = [p for p in detail["phases"] if p["sequence"] == 1 and p["area_group"] == "TOWER"][0]
    assert row["delivery_date"] == "2027-01-07"
    assert row["promoted_delivery_date"] == "2026-07-01"

    service.confirm(second.id, actor_user_id=scenario["owner"])
    db.flush()
    db.refresh(phase)
    assert phase.delivery_date == date(2027, 1, 7)


# ----------------------------------------- section 9.2: confirm tells the planner


def _authored_order(db, project, po, schedule_version, owner) -> ProjectSalesOrder:
    """An authored SO built from ``schedule_version``, published and therefore live."""
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        schedule_version_id=schedule_version.id,
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        status=SO_STATUS_PUBLISHED,
        published_by=owner,
    )
    db.add(order)
    db.flush()
    return order


def test_confirming_a_later_version_notifies_the_stale_orders_planner(scenario):
    """AC (section 9.2): a version confirmed over an order's own baseline makes that
    order stale, and the planner who published it is told with a link to review it."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    first = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, first)])
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()

    order = _authored_order(
        db, scenario["project"], scenario["po"], scenario["version"], scenario["owner"]
    )

    revised = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    revised["phases"][0]["delivery_date"] = "2027-01-07"
    second = _schedule_version(
        db, scenario["project"], scenario["po"], scenario["po_version"],
        version_no=2, label="REVISED 1",
    )
    service.persist_pages(second, [(1, revised)])
    db.flush()

    service.confirm(second.id, actor_user_id=scenario["owner"])
    db.flush()

    notification = (
        db.query(Notification)
        .filter(
            Notification.user_id == scenario["owner"],
            Notification.type == "project_schedule_confirmed_stale_so",
        )
        .first()
    )
    assert notification is not None
    assert order.provisional_ref in notification.title
    assert "REVISED 1" in notification.title
    expected_link = (
        f"/project-sales/{order.project_id}/sales-orders/{order.id}/revisions"
        f"?schedule_version={second.id}"
    )
    assert notification.data["link"] == expected_link

    detail = service.get_version_detail(second.id)
    assert detail["amendment_preview_url"] == expected_link


def test_a_failing_notify_never_fails_the_confirm(scenario, monkeypatch):
    """Best-effort, per CLAUDE.md: a notification failure must not turn a schedule
    that WAS confirmed into an error."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    def _boom(self, version):
        raise RuntimeError("notification service unreachable")

    monkeypatch.setattr(ProjectScheduleService, "_notify_stale_orders", _boom)

    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()
    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert version.confirmed_at is not None


# --------------------------------------------- section 9.6: day-first slash dates


def test_the_normaliser_reads_a_slash_date_day_first():
    """Pinned 19 August 2026: the same document read '7/1/2027' as 2027-07-01 on one
    page and 2027-01-07 on the others. The customer writes day/month/year."""
    assert _parse_date("7/1/2027") == date(2027, 1, 7)
    assert _parse_date("23/7/2026") == date(2026, 7, 23)
    # ISO stays ISO: the extractor is told to emit yyyy-mm-dd, and this is not a
    # slash date to begin with.
    assert _parse_date("2027-01-07") == date(2027, 1, 7)


# ---------------------------------------------------- section addendum: prose notes


def test_a_pages_free_text_note_round_trips_to_the_version_detail(scenario):
    """The real R2 document (delivery_schedule_versions e36327d7) carries its
    revision as a margin sentence while the phase columns stay unchanged; the
    extractor must report it verbatim and never turn it into a date."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    page["notes"] = [
        "ONLY FOR FLOOR TRAP TO BE DELIVER IN 2026, START FROM 23/7/2026",
        "  ",  # blank notes are dropped, not shown as an empty remark
    ]
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    assert detail["notes"] == [
        {
            "page_no": 1,
            "text": "ONLY FOR FLOOR TRAP TO BE DELIVER IN 2026, START FROM 23/7/2026",
        }
    ]
    # Not inferred as a phase date: the columns this page carries are untouched.
    row = _phase(detail, "TOWER", 1)
    assert row["delivery_date"] == "2026-07-01"


# ------------------------------------------------------- dismissing a false signal


def _failing_scenario(scenario):
    """Two columns, one of which fails: the WC column's cells sum to 195 against a
    printed total of 200 and a PO quantity of 200."""
    service = ProjectScheduleService(scenario["db"])
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 75)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    scenario["db"].flush()
    return service


def test_a_dismissed_column_stops_blocking_the_confirm(scenario):
    """The checksum reads somebody else's paper and is sometimes wrong about a column
    that is fine. Overruling THAT column is not the same as acknowledging the sheet."""
    db = scenario["db"]
    service = _failing_scenario(scenario)

    detail = service.get_version_detail(scenario["version"].id)
    assert detail["reconciliation"] == {"reconciled_columns": 1, "total_columns": 2}
    failing = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert failing["reconciled"] is False

    service.dismiss_column_verdict(
        scenario["version"].id,
        failing["product_index"],
        dismissed=True,
        reason="Customer confirmed 195 by email on 4 March",
        actor_user_id=scenario["owner"],
    )
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    column = _columns(detail)["BUI-HB-SRTWC8613-RL"]
    assert column["reconciled"] is True
    assert column["dismissed"] is True
    assert column["dismissed_reason"] == "Customer confirmed 195 by email on 4 March"
    assert column["dismissed_by_name"] == f"{MARKER} CONTACT AH"
    # The verdict is overruled, not withdrawn: the screen still shows what it found.
    assert column["reason"]
    assert detail["reconciliation"] == {"reconciled_columns": 2, "total_columns": 2}

    # It was the only failing column, so confirm no longer needs the whole-sheet
    # acknowledgement.
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()
    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    assert version.confirmed_at is not None
    assert version.reconciliation_json.get("acknowledgement") is None


def test_dismissing_a_column_without_a_reason_is_refused(scenario):
    """Same rule as the whole-sheet acknowledgement: overruling a check is on the record."""
    service = _failing_scenario(scenario)
    with pytest.raises(AppException) as excinfo:
        service.dismiss_column_verdict(
            scenario["version"].id, 0, dismissed=True, reason="   ",
            actor_user_id=scenario["owner"],
        )
    assert excinfo.value.status_code == 422


def test_a_dismissal_survives_the_request(scenario):
    """``reconciliation_json`` is a plain JSONB column: without flag_modified the write
    is never flushed and the 200 is a lie."""
    db = scenario["db"]
    service = _failing_scenario(scenario)
    service.dismiss_column_verdict(
        scenario["version"].id, 0, dismissed=True, reason="Confirmed by email",
        actor_user_id=scenario["owner"],
    )
    db.flush()

    version_id = scenario["version"].id
    db.expire_all()
    stored = (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id == version_id)
        .one()
    )
    column = stored.reconciliation_json["columns"][0]
    assert column["dismissed"] is True
    assert column["dismissed_reason"] == "Confirmed by email"
    assert column["reconciled"] is True
    assert stored.reconciled_columns == 2


def test_undismissing_puts_the_column_back_under_its_verdict(scenario):
    db = scenario["db"]
    service = _failing_scenario(scenario)
    service.dismiss_column_verdict(
        scenario["version"].id, 0, dismissed=True, reason="Confirmed by email",
        actor_user_id=scenario["owner"],
    )
    db.flush()
    service.dismiss_column_verdict(scenario["version"].id, 0, dismissed=False)
    db.flush()

    column = _columns(service.get_version_detail(scenario["version"].id))[
        "BUI-HB-SRTWC8613-RL"
    ]
    assert column["dismissed"] is False
    assert column["dismissed_reason"] is None
    assert column["reconciled"] is False
    with pytest.raises(AppException) as excinfo:
        service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    assert excinfo.value.status_code == 409


def test_correcting_a_cell_clears_the_dismissal_on_that_column(scenario):
    """The dismissal said the check was wrong about THOSE numbers. Change them and it
    is a statement about something else."""
    db = scenario["db"]
    service = _failing_scenario(scenario)
    service.dismiss_column_verdict(
        scenario["version"].id, 0, dismissed=True, reason="Confirmed by email",
        actor_user_id=scenario["owner"],
    )
    db.flush()

    detail = service.get_version_detail(scenario["version"].id)
    phase_two = _phase(detail, "TOWER", 2)
    service.update_cells(
        scenario["version"].id,
        [{"phase_id": phase_two["id"], "product_index": 0, "qty": "80"}],
    )
    db.flush()

    column = _columns(service.get_version_detail(scenario["version"].id))[
        "BUI-HB-SRTWC8613-RL"
    ]
    assert column["dismissed"] is False
    # Judged again from scratch, and this time it genuinely adds up.
    assert column["reconciled"] is True
    assert column["column_total"] == "200"


def test_naming_the_columns_product_clears_the_dismissal(scenario):
    """A different product means a different PO quantity, so the old verdict, and the
    overruling of it, are both about numbers that no longer apply."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _page(
        products=[{"col": 1, "customer_code": "BUI-HB-XX9931", "code": None, "name": "Mystery"}],
        phases=[{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",
                 "delivery_date": "2026-07-01"}],
        cells=[{"row": 1, "col": 1, "qty": 200}],
        totals=[{"col": 1, "qty": 200}],
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()
    service.dismiss_column_verdict(
        scenario["version"].id, 0, dismissed=True, reason="Not on the PO, ordered verbally",
        actor_user_id=scenario["owner"],
    )
    db.flush()

    service.resolve_product_column(
        scenario["version"].id, 0, scenario["wc"].id, actor_user_id=scenario["owner"]
    )
    db.flush()

    column = service.get_version_detail(scenario["version"].id)["products"][0]
    assert column["dismissed"] is False
    assert column["dismissed_reason"] is None


def test_a_column_that_is_not_on_the_schedule_cannot_be_dismissed(scenario):
    service = _failing_scenario(scenario)
    with pytest.raises(AppException) as excinfo:
        service.dismiss_column_verdict(
            scenario["version"].id, 99, dismissed=True, reason="Whatever",
            actor_user_id=scenario["owner"],
        )
    assert excinfo.value.status_code == 404


def test_a_confirmed_version_cannot_have_a_column_dismissed(scenario):
    """Editing what was agreed is what a revision is for."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    page = _two_column_page(
        wc_cells=[(1, 120), (2, 80)], basin_cells=[(3, 60), (4, 40)],
        wc_total=200, basin_total=100,
    )
    service.persist_pages(scenario["version"], [(1, page)])
    service.confirm(scenario["version"].id, actor_user_id=scenario["owner"])
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.dismiss_column_verdict(
            scenario["version"].id, 0, dismissed=True, reason="Too late",
            actor_user_id=scenario["owner"],
        )
    assert excinfo.value.status_code == 409


def _client(db, user_id: str):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [
        "projects.projects.view",
        "projects.projects.edit",
    ]
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture()
def api(scenario):
    """The same world, reached over HTTP: the dismissal is a route as well as a rule."""
    from app.models.base import company_scope

    db = scenario["db"]
    _failing_scenario(scenario)
    db.commit()
    client, originals = _client(db, scenario["owner"])
    try:
        with company_scope(db, frozenset({scenario["project"].company_id})):
            yield client, scenario
    finally:
        _restore(originals)


def test_the_dismissal_route_overrules_one_column(api):
    client, scenario = api
    response = client.put(
        f"/api/v1/project-sales/delivery-schedule-versions/{scenario['version'].id}/columns/0/dismissal",
        json={"dismissed": True, "reason": "Customer confirmed 195 by email"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    column = next(
        item for item in body["products"] if item["customer_code_raw"] == "BUI-HB-SRTWC8613-RL"
    )
    assert column["dismissed"] is True
    assert column["reconciled"] is True
    assert column["dismissed_reason"] == "Customer confirmed 195 by email"
    assert body["reconciliation"]["reconciled_columns"] == 2

    undone = client.put(
        f"/api/v1/project-sales/delivery-schedule-versions/{scenario['version'].id}/columns/0/dismissal",
        json={"dismissed": False},
    )
    assert undone.status_code == 200, undone.text
    column = next(
        item for item in undone.json()["products"]
        if item["customer_code_raw"] == "BUI-HB-SRTWC8613-RL"
    )
    assert column["dismissed"] is False
    assert column["reconciled"] is False


def test_the_dismissal_route_refuses_a_reasonless_dismissal(api):
    client, scenario = api
    response = client.put(
        f"/api/v1/project-sales/delivery-schedule-versions/{scenario['version'].id}/columns/0/dismissal",
        json={"dismissed": True},
    )
    assert response.status_code == 422


def test_the_dismissal_route_is_refused_without_the_edit_right(api):
    """Rights live on the PROJECT: another salesperson's pursuit is not editable."""
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    client, scenario = api
    stranger = _user(scenario["db"], f"{MARKER} Farah")
    scenario["db"].commit()
    actor = {"id": stranger, "email": f"{stranger}@zzt.test", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)

    response = client.put(
        f"/api/v1/project-sales/delivery-schedule-versions/{scenario['version'].id}/columns/0/dismissal",
        json={"dismissed": True, "reason": "Not mine to say"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------- the text layer


@pytest.mark.parametrize(
    "filename, expected_columns, floor",
    [
        # MEASURED 2026-08-02 on the client's own documents. Pure vision managed 29/37
        # on R1 and 35/38 on R2 (PLAN 5c); the text layer is why intake is hybrid.
        ("delivery-schedule-buimaco-r1.pdf", 38, 36),
        ("delivery-schedule-slg-r2.pdf", 39, 37),
    ],
)
def test_the_text_layer_reproduces_the_real_schedule_matrix(
    filename, expected_columns, floor
):
    """The schedules HAVE a text layer, so quantities are read from it geometrically and
    vision is left to do structure.

    The remainders are not misreads: they are columns whose total the customer wrote as
    prose rather than as a number, so there is nothing to compare against.
    """
    content = (FIXTURES / filename).read_bytes()
    pages = parse_text_matrix(content, "application/pdf")

    assert len(pages) == 7
    matched = 0
    columns = 0
    for page in pages.values():
        assert len(page.rows) == 15  # 12 TOWER + 3 COMMON AREA, on every page
        for index in range(page.column_count):
            columns += 1
            total = sum(
                (row.get(index, Decimal(0)) for row in page.rows), Decimal(0)
            )
            if page.totals.get(index) is not None and total == page.totals[index]:
                matched += 1
    assert columns == expected_columns
    assert matched >= floor


def test_a_page_whose_printed_totals_all_agree_is_trusted_for_its_whole_grid():
    """The corroboration rule: a page's grid is proven by the columns that carry a
    printed total. Where every one of them agrees, the columns with no printed total
    are transcribed from the same proven grid rather than left to vision."""
    content = (FIXTURES / "delivery-schedule-buimaco-r1.pdf").read_bytes()
    pages = parse_text_matrix(content, "application/pdf")

    # Page 6 carries a column whose total the customer wrote as prose; every other
    # column on it agrees, so the page is proven.
    assert pages[6].grid_proven is True
    # Page 5 carries a row the label column does not show, so one column comes up short
    # and the page is NOT trusted: those columns stay with vision and reach CS.
    assert pages[5].grid_proven is False


# --------------------------------------- section 9.7(a): geometric highlight tagging


def test_a_coloured_fill_tags_its_cell_and_a_grey_fill_does_not():
    """Section 9.7(a). A rose fill DRAWN behind a number (never text formatting) is
    the customer's own way of marking a cell; a grey fill -- a border, a header
    band -- is not. Built as a real PDF with pymupdf rather than mocked geometry,
    since this is exactly the shape `page.get_drawings()` returns on the real R2."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((50, 30), "SRTWC8613-RL", fontsize=10)
    page.insert_text((150, 30), "SRTWB7055", fontsize=10)
    # A rose fill behind the WC quantity -- the real R2 colour, measured 19 August.
    page.draw_rect(fitz.Rect(45, 55, 95, 75), color=None, fill=(0.86, 0.59, 0.58))
    page.insert_text((50, 70), "135", fontsize=10)
    # A grey fill behind the basin quantity: a gridline, never a highlight.
    page.draw_rect(fitz.Rect(145, 55, 195, 75), color=None, fill=(0.5, 0.5, 0.5))
    page.insert_text((150, 70), "80", fontsize=10)
    page.insert_text((10, 70), "L1", fontsize=10)
    content = doc.tobytes()
    doc.close()

    pages = parse_text_matrix(content, "application/pdf")
    assert len(pages) == 1
    page_result = pages[1]
    assert len(page_result.rows) == 1
    row_highlights = page_result.highlights[0]
    assert row_highlights.get(0) == "#db9694"
    assert 1 not in row_highlights


# ------------------------------------------ section 9.7(b)-(c): revision proposals


def _highlighted_page(*, wc_cells, basin_cells, dates, note):
    """A three-or-four-phase TOWER page: WC's cells are tinted, basin's are not,
    and the page carries a dated margin note -- the shape of the real page 7."""
    return _page(
        products=[
            {"col": 1, "customer_code": "BUI-HB-SRTWC8613-RL", "code": "SRTWC8613-RL",
             "name": "One-Piece WC"},
            {"col": 2, "customer_code": "BUI-HB-SRTWB7055", "code": "SRTWB7055",
             "name": "Counter-Top Basin"},
        ],
        phases=[
            {"row": index + 1, "area_group": "TOWER", "label": f"Level {index + 1}",
             "delivery_date": d.isoformat()}
            for index, d in enumerate(dates)
        ],
        cells=(
            [
                {"row": row, "col": 1, "qty": qty, "highlighted": True}
                for row, qty in wc_cells
            ]
            + [
                {"row": row, "col": 2, "qty": qty, "highlighted": False}
                for row, qty in basin_cells
            ]
        ),
        totals=[],
    ) | {"notes": [note]}


def test_a_proposal_preserves_the_original_cadence_between_highlighted_phases(scenario):
    """Section 9.7(b): the first highlighted phase moves to the note's date; each
    later one moves by the ORIGINAL gap to its predecessor -- 14, 14, then 28 days,
    read off the dates already on the phases, never a constant."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    d1 = date(2026, 1, 7)
    d2 = d1 + timedelta(days=14)
    d3 = d2 + timedelta(days=14)
    d4 = d3 + timedelta(days=28)
    note_date = date(2026, 7, 23)
    page = _highlighted_page(
        wc_cells=[(1, 135), (2, 72), (3, 72), (4, 72)],
        basin_cells=[(1, 60), (2, 40), (3, 40), (4, 40)],
        dates=[d1, d2, d3, d4],
        note="ONLY FOR FLOOR TRAP TO BE DELIVER IN 2026, START FROM 23/7/2026",
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()

    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    proposals = version.revision_proposals
    assert len(proposals) == 1  # basin has no highlighted cells: no proposal for it
    proposal = proposals[0]
    assert proposal["product_id"] == scenario["wc"].id
    assert proposal["state"] == "proposed"
    assert proposal["decided_by"] is None
    assert "23/7/2026" in proposal["note_text"]
    cells = proposal["cells"]
    assert [c["old_date"] for c in cells] == [d.isoformat() for d in (d1, d2, d3, d4)]

    expected_new = [note_date]
    for gap in (14, 14, 28):
        expected_new.append(expected_new[-1] + timedelta(days=gap))
    assert [c["new_date"] for c in cells] == [d.isoformat() for d in expected_new[:4]]


def test_accepting_a_proposal_writes_overrides_and_refuses_a_second_decision(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    d1 = date(2026, 1, 7)
    d2 = d1 + timedelta(days=14)
    page = _highlighted_page(
        wc_cells=[(1, 135), (2, 72)],
        basin_cells=[(1, 60), (2, 40)],
        dates=[d1, d2],
        note="START FROM 23/7/2026",
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()
    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    phase_id = version.revision_proposals[0]["cells"][0]["phase_id"]

    accepted = service.accept_revision_proposal(
        version.id, 0, actor_user_id=scenario["owner"]
    )
    assert accepted.revision_proposals[0]["state"] == "accepted"
    assert accepted.revision_proposals[0]["decided_by"] == scenario["owner"]
    assert accepted.revision_proposals[0]["decided_at"] is not None

    cell = (
        db.query(DeliveryScheduleCell)
        .filter(
            DeliveryScheduleCell.version_id == version.id,
            DeliveryScheduleCell.product_id == scenario["wc"].id,
            DeliveryScheduleCell.phase_id == phase_id,
        )
        .first()
    )
    assert cell.delivery_date_override == date(2026, 7, 23)

    with pytest.raises(AppException) as excinfo:
        service.accept_revision_proposal(version.id, 0, actor_user_id=scenario["owner"])
    assert excinfo.value.status_code == 409


def test_rejecting_a_proposal_writes_no_override(scenario):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    d1 = date(2026, 1, 7)
    page = _highlighted_page(
        wc_cells=[(1, 135)], basin_cells=[(1, 60)], dates=[d1], note="START FROM 23/7/2026",
    )
    service.persist_pages(scenario["version"], [(1, page)])
    db.flush()
    version = db.get(DeliveryScheduleVersion, scenario["version"].id)
    phase_id = version.revision_proposals[0]["cells"][0]["phase_id"]

    rejected = service.reject_revision_proposal(
        version.id, 0, actor_user_id=scenario["owner"]
    )
    assert rejected.revision_proposals[0]["state"] == "rejected"

    cell = (
        db.query(DeliveryScheduleCell)
        .filter(
            DeliveryScheduleCell.version_id == version.id,
            DeliveryScheduleCell.product_id == scenario["wc"].id,
            DeliveryScheduleCell.phase_id == phase_id,
        )
        .first()
    )
    assert cell.delivery_date_override is None


# ------------------------------------------------------- the real document, live


@pytest.mark.skipif(
    not __import__("app.config", fromlist=["settings"]).settings.gemini_api_key,
    reason="needs GEMINI_API_KEY: this one reads the real document with the real model",
)
def test_the_real_r1_schedule_extracts_and_reconciles(seeded):
    """The golden set as an acceptance test (PLAN 5a), end to end and unmocked.

    MEASURED 2026-08-02, `gemini-2.5-flash` plus the text-layer cross-check, over the
    client's own R1: 37 columns after the two identically-headed Grab Bar columns merge,
    35 of them equal to the schedule's own printed TOTAL QTY row, 15 phases, no failed
    pages. Vision alone managed 29 of 37 (PLAN 5c). The floors below ARE that
    measurement, not an aspiration; raise them only after re-measuring.

    Products are NOT seeded, so every column is unresolved and the PO half of the
    checksum cannot fire. What this pins is the transcription of the matrix, which is
    the part of the job this slice owns.
    """
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    customer = _customer(db, company_id)
    po, po_version = _po_with_version(db, project, owner, customer, [])
    version = _schedule_version(db, project, po, po_version)
    content = (FIXTURES / "delivery-schedule-buimaco-r1.pdf").read_bytes()

    service = ProjectScheduleService(db)
    result = service.persist_pages(
        version,
        service.read_document(content, "application/pdf"),
        text_pages=parse_text_matrix(content, "application/pdf"),
    )
    db.flush()

    detail = service.get_version_detail(version.id)
    matching = [
        column
        for column in detail["products"]
        if column["reported_total"] is not None
        and column["column_total"] == column["reported_total"]
    ]
    print(
        f"\nreal R1: {len(detail['products'])} columns, {len(matching)} match the "
        f"printed TOTAL QTY row, {len(detail['phases'])} phases, "
        f"failed pages={result.get('failed_pages')}, "
        f"quantities from={detail['products'] and version.reconciliation_json['qty_source']}"
    )
    for column in detail["products"]:
        if column in matching:
            continue
        print(
            f"  unmatched: {column['customer_code_raw']} "
            f"page {column['qty_source']} sum={column['column_total']} "
            f"reported={column['reported_total']}"
        )
    assert len(detail["products"]) >= 37
    assert len(matching) >= 35
    assert len(detail["phases"]) == 15


# ------------------------------------------------------------ duplicate upload guard


@pytest.fixture()
def stored_document(monkeypatch):
    """Object storage stubbed out. The bytes going to S3 are not what these tests are
    about, and a unit test that needs credentials is a test nobody runs."""
    monkeypatch.setattr(
        ProjectScheduleService,
        "_store_document",
        lambda self, version, **kwargs: None,
    )


def test_uploading_the_same_bytes_twice_is_refused_naming_the_version_that_holds_them(
    scenario, stored_document
):
    """The same PDF re-attached is not a new revision - it is the first one again."""
    db = scenario["db"]
    service = ProjectScheduleService(db)
    content = b"%PDF-1.4 zzt duplicate schedule bytes"

    first = service.create_version(
        purchase_order=scenario["po"],
        content=content,
        filename="schedule-r1.pdf",
        mime="application/pdf",
        actor_user_id=scenario["owner"],
        delivery_schedule_id=scenario["version"].delivery_schedule_id,
    )
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.create_version(
            purchase_order=scenario["po"],
            content=content,
            filename="schedule-r1-again.pdf",
            mime="application/pdf",
            actor_user_id=scenario["owner"],
            delivery_schedule_id=scenario["version"].delivery_schedule_id,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "schedule_version_duplicate"
    assert f"version {first.version_no}" in excinfo.value.detail["message"]


def test_different_bytes_open_a_new_version_rather_than_refusing(scenario, stored_document):
    db = scenario["db"]
    service = ProjectScheduleService(db)

    first = service.create_version(
        purchase_order=scenario["po"],
        content=b"first revision bytes",
        filename="schedule-r1.pdf",
        mime="application/pdf",
        actor_user_id=scenario["owner"],
        delivery_schedule_id=scenario["version"].delivery_schedule_id,
    )
    db.flush()

    second = service.create_version(
        purchase_order=scenario["po"],
        content=b"second revision bytes, actually changed",
        filename="schedule-r2.pdf",
        mime="application/pdf",
        actor_user_id=scenario["owner"],
        delivery_schedule_id=scenario["version"].delivery_schedule_id,
    )

    assert second.version_no == first.version_no + 1


def test_force_overrides_the_duplicate_guard(scenario, stored_document):
    db = scenario["db"]
    service = ProjectScheduleService(db)
    content = b"schedule bytes uploaded twice on purpose"

    first = service.create_version(
        purchase_order=scenario["po"],
        content=content,
        filename="schedule-r1.pdf",
        mime="application/pdf",
        actor_user_id=scenario["owner"],
        delivery_schedule_id=scenario["version"].delivery_schedule_id,
    )
    db.flush()

    second = service.create_version(
        purchase_order=scenario["po"],
        content=content,
        filename="schedule-r1-forced.pdf",
        mime="application/pdf",
        actor_user_id=scenario["owner"],
        delivery_schedule_id=scenario["version"].delivery_schedule_id,
        force=True,
    )

    assert second.version_no == first.version_no + 1
