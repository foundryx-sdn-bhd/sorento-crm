"""RED tests for lane GRN-PULL-CRM: the `goods_receive_notes` pull entity.

Plan: documentation/plans/autocount/PLAN-autocount-grn-pull-crm-02oct.md
UAC:  documentation/plans/autocount/autocount-grn-pull-crm-02oct-acceptance-criteria.md

A fourth entity on the pull machinery, built exactly like the DO one (PR #1383): preview and
apply both run `AutocountDocIngestService` (the GRN ingest, whose PO/SPO line linkage is
pinned in `test_ingest_autocount_grn_line_link.py`), so this file pins only what the pull
adds: the entity maps and permission, the batch order, the receipt hook after apply, the
rows / download, and the two-file compare against the GRN Excel the checkers use today
("DETAIL LISTING ....xlsx" and "GRN Listing - Macro Version ....xlsm", sheet Master).

Substrate reused by import from the DO pull tests (SR1 / SR3 helpers, the masters seed).
Snapshot rows are the raw vendor GRN dict + `source_ref` `{book}:GRN:{DocKey}` (contract
GRN-PULL-SS, as posted on #1427).
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

# MUST be the first app import (repo convention).
from app.main import app  # noqa: E402,F401

from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.test_autocount_pull_delivery_orders import _seed_masters
from tests.test_autocount_pull_sr1 import (  # noqa: F401 - env/task_db are fixtures
    PULLS_URL,
    _FakeFoundryX,
    _header,
    _job_count,
    _job_row,
    _job_rows,
    _patch_foundryx,
    _run_preview,
    _seed_pull_job,
    _stored,
    env,
    task_db,
)
from tests.test_autocount_pull_sr3 import _run_apply, _set_rows_page  # noqa: F401

MARKER = "ZZTGRP"
GRN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "autocount" / "grn_live_sample.json"
VERSIONS = (Path(__file__).resolve().parent / ".." / "alembic" / "versions").resolve()

ENTITY = "goods_receive_notes"
SLUG = "procurement.grn.autocount_pull"
PULL_JOB_TYPE = "autocount_grn_pull"
APPLY_JOB_TYPE = "autocount_grn_apply"
BOOK = "db1"
MIGRATION = "grn_pull_0001_perm"
JULY = {"fromDay": "2026-07-01", "toDay": "2026-07-31"}


# ================================================================== rows and headers
def _grn_rows(book: str | None = BOOK) -> list[dict]:
    """The live-shape GRN sample (ZZGRN-0001, DocKey 800001, two lines) as the snapshot
    serves it."""
    rows = []
    for rec in json.loads(GRN_FIXTURE.read_text())["records"]:
        row = copy.deepcopy(rec)
        if book is not None:
            row["source_ref"] = f"{book}:GRN:{rec['DocKey']}"
        rows.append(row)
    return rows


def _second(rows: list[dict], doc_key: int, doc_no: str, doc_date: str) -> dict:
    row = copy.deepcopy(rows[0])
    row.update({"DocKey": doc_key, "DocNo": doc_no, "DocDate": doc_date,
                "source_ref": f"{BOOK}:GRN:{doc_key}"})
    for offset, detail in enumerate(row["Details"]):
        detail["DtlKey"] = doc_key * 10 + offset
        detail["DocKey"] = doc_key
    return row


def _grn_header(rows: list[dict], **overrides) -> dict:
    header = _header(rows, **overrides)
    header["entity"] = ENTITY
    for key in ("zeroListPriceCount", "negativeListPriceCount", "enrichMissCount"):
        header.pop(key, None)
    return header


def _prepare_preview(db, fake: _FakeFoundryX, *, rows: list[dict]):
    snapshot_id = f"{MARKER}-snap-{uuid.uuid4().hex[:8]}"
    header = _grn_header(rows)
    fake.status = (200, {**header, "snapshotId": snapshot_id})
    _set_rows_page(fake, snapshot_id, rows)
    return _seed_pull_job(
        db, job_type=PULL_JOB_TYPE, user_id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID,
        entity=ENTITY, company_code="SRT", snapshot_id=snapshot_id, phase="previewing",
        header=_stored(header),
    )


def _prepare_apply(db, fake: _FakeFoundryX, *, rows: list[dict]):
    from app.models.job import ImportJob, JobStatus

    snapshot_id = f"{MARKER}-apply-{uuid.uuid4().hex[:8]}"
    fake.status = (200, {**_grn_header(rows), "snapshotId": snapshot_id})
    _set_rows_page(fake, snapshot_id, rows)
    job = ImportJob(
        id=uuid.uuid4(), job_id=str(uuid.uuid4()), job_type=APPLY_JOB_TYPE,
        status=JobStatus.QUEUED.value, user_id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID,
        job_metadata={"autocount_apply": {
            "pull_job_id": str(uuid.uuid4()), "snapshot_id": snapshot_id, "entity": ENTITY,
        }},
    )
    db.add(job)
    db.commit()
    return job.id


def _seed_review_job(env, *, owner, rows=None, scope=JULY):
    rows = rows if rows is not None else _grn_rows()
    snapshot_id = f"{MARKER}-rv-{uuid.uuid4().hex[:8]}"
    job_id = _seed_pull_job(
        env.db, job_type=PULL_JOB_TYPE, user_id=owner["id"], company_id=env.company_a,
        entity=ENTITY, company_code=env.company_a_code, snapshot_id=snapshot_id, phase="review",
        header=_stored(_grn_header(rows)), extra={"scope": scope} if scope else None,
    )
    _set_rows_page(env.fake, snapshot_id, rows)
    return job_id, rows


def _grns(db, *doc_keys: int) -> list[dict]:
    rows = db.execute(
        text("SELECT id, picking_number, doc_key, source_book, source_record FROM picking_headers "
             "WHERE company_id = :cid AND doc_key = ANY(:keys) ORDER BY doc_key"),
        {"cid": DEFAULT_COMPANY_ID, "keys": list(doc_keys)},
    ).mappings().all()
    return [dict(r) for r in rows]


def _spo_line(db, number: str, product_id: str, qty: int, line_no: int) -> str:
    from app.models.procurement import SPOAllocation

    row = SPOAllocation(spo_number=number, spo_line_number=line_no, product_id=product_id,
                        allocated_quantity=qty, company_id=DEFAULT_COMPANY_ID,
                        created_at=datetime(2026, 7, 1))
    db.add(row)
    db.commit()
    return str(row.id)


# ======================================================================= AC-GP-01..03
class TestEntityMaps:
    def test_gp01_maps_and_registry_slug(self):
        from app.rbac.permission_registry import PERMISSION_REGISTRY
        from app.services import autocount_pull_service as pull_service

        assert pull_service.ENTITY_PERMISSIONS[ENTITY] == SLUG
        assert pull_service.JOB_TYPES[ENTITY] == PULL_JOB_TYPE
        assert pull_service.APPLY_JOB_TYPES[ENTITY] == APPLY_JOB_TYPE
        assert SLUG in {p["slug"] for p in PERMISSION_REGISTRY}

    def test_gp02_start_requires_slug_and_takes_a_scope(self, env):
        nobody = env.user("order_management.orders.autocount_pull")
        env.as_user(nobody)
        before = _job_count(env.db)
        resp = env.client.post(PULLS_URL, json={"entity": ENTITY, "scope": JULY})
        assert resp.status_code == 403
        assert env.fake.calls == [] and _job_count(env.db) == before

        owner = env.user(SLUG)
        env.as_user(owner)
        resp = env.client.post(PULLS_URL, json={"entity": ENTITY, "scope": JULY})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity"] == ENTITY and body["phase"] == "building"
        assert body["scope"] == JULY
        assert _job_row(env.db, body["job_id"])["job_type"] == PULL_JOB_TYPE
        build = [c for c in env.fake.calls if c["method"] == "POST"]
        assert build[0]["json"] == {"companyCode": env.company_a_code, "entity": ENTITY, **JULY}

    @pytest.mark.parametrize("scope", [None, {}, {"fromDay": "2026-07-01"}])
    def test_gp02c_a_grn_pull_always_names_a_window_or_a_document(self, env, scope):
        """GRN-PULL-SS (ss#107): the gateway refuses a goods-receive-notes build with no
        scope, so the CRM refuses it first, in words, and calls nothing."""
        owner = env.user(SLUG)
        env.as_user(owner)
        before = _job_count(env.db)
        body = {"entity": ENTITY} if scope is None else {"entity": ENTITY, "scope": scope}
        resp = env.client.post(PULLS_URL, json=body)
        assert resp.status_code == 422, resp.text
        assert "From day and a To day" in resp.text
        assert env.fake.calls == [] and _job_count(env.db) == before
        one = env.client.post(PULLS_URL, json={"entity": ENTITY, "scope": {"docNo": "GR-2026/10-0006"}})
        assert one.status_code == 200, one.text

    def test_gp02b_a_do_only_user_cannot_read_a_grn_pull(self, env):
        do_only = env.user("order_management.orders.autocount_pull")
        env.as_user(do_only)
        job_id, _ = _seed_review_job(env, owner=do_only)
        assert env.get_pull(job_id).status_code == 403


class TestMigration:
    def _load(self):
        alembic_dir = str(VERSIONS.parent)
        if alembic_dir not in sys.path:
            sys.path.insert(0, alembic_dir)
        spec = importlib.util.spec_from_file_location(f"m_{MIGRATION}", VERSIONS / f"{MIGRATION}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_gp03a_revision_fits_and_sits_on_the_single_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        module = self._load()
        assert module.revision == MIGRATION and len(module.revision) <= 32
        script = ScriptDirectory.from_config(Config(str(VERSIONS.parent.parent / "alembic.ini")))
        heads = list(script.get_heads())
        assert len(heads) == 1, heads
        assert MIGRATION in {r.revision for r in script.walk_revisions(base="base", head=heads[0])}

    def test_gp03b_grants_to_grn_import_holders_and_admin_not_integrations(self):
        from app.database import engine

        module = self._load()
        stem = uuid.uuid4().hex[:8]
        with engine.connect() as conn:
            outer = conn.begin()
            try:
                def q(sql, **params):
                    return conn.execute(text(sql), params)

                q("INSERT INTO user_permissions (id, slug, name, description, created_at) "
                  "SELECT gen_random_uuid()::text, 'procurement.grn.import', 'i', '', now() "
                  "WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = 'procurement.grn.import')")
                import_perm = q("SELECT id FROM user_permissions WHERE slug = 'procurement.grn.import'").scalar()
                roles = {
                    "human": f"{MARKER.lower()}-human-{stem}",
                    "integration": f"integration_{MARKER.lower()}_{stem}",
                    "bystander": f"{MARKER.lower()}-bystander-{stem}",
                }
                role_ids = {}
                for key, slug in roles.items():
                    role_ids[key] = q(
                        "INSERT INTO user_roles (id, slug, name, description, is_trashed, "
                        "is_protected, is_default) "
                        "VALUES (gen_random_uuid()::text, :slug, :slug, '', false, false, false) "
                        "RETURNING id", slug=slug,
                    ).scalar()
                for key in ("human", "integration"):
                    q("INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
                      "VALUES (gen_random_uuid()::text, :r, :p, now()) ON CONFLICT DO NOTHING",
                      r=role_ids[key], p=import_perm)
                q("INSERT INTO user_roles (id, slug, name, description, is_trashed, is_protected, "
                  "is_default) SELECT gen_random_uuid()::text, 'admin', 'admin', '', false, true, false "
                  "WHERE NOT EXISTS (SELECT 1 FROM user_roles WHERE slug = 'admin')")

                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                holders = {
                    r[0] for r in q(
                        "SELECT r.slug FROM user_role_permissions rp "
                        "JOIN user_permissions p ON p.id = rp.permission_id "
                        "JOIN user_roles r ON r.id = rp.role_id WHERE p.slug = :slug", slug=SLUG,
                    )
                }
                assert roles["human"] in holders and "admin" in holders
                assert roles["integration"] not in holders and roles["bystander"] not in holders

                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                assert q("SELECT count(*) FROM user_permissions WHERE slug = :slug", slug=SLUG).scalar() == 0
            finally:
                outer.rollback()


# ======================================================================= AC-GP-10..14
class TestPreview:
    def test_gp10_sample_previews_created_and_writes_nothing(self, task_db, monkeypatch):
        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        job_id = _prepare_preview(db, fake, rows=_grn_rows())

        _run_preview(monkeypatch, factory, job_id)

        row = _job_row(db, job_id)
        assert row["status"] == "finished", row["error"]
        pull = row["metadata"]["autocount_pull"]
        assert pull["phase"] == "review" and pull["confirm_blocked_reason"] is None
        assert pull["counts"] == {
            "received": 1, "created": 1, "updated": 0, "adopted": 0, "unchanged": 0,
            "lines_to_delete": 0, "failed": 0, "retryable": 0, "with_warnings": 0,
            "lines_linked": 0, "lines_unlinked": 2,
        }
        written = _job_rows(db, job_id)
        assert [r["value"] for r in written] == ["ZZGRN-0001"]
        assert written[0]["outcome"] == "created"
        assert written[0]["message"].startswith("GRN created: ZZGRN-0001")
        assert _grns(db, 800001) == []

    def test_gp12_excel_grn_with_the_same_number_previews_as_adopted(self, task_db, monkeypatch):
        from app.models.procurement import PickingHeader

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        db.add(PickingHeader(picking_number="ZZGRN-0001", picking_type="goods_received",
                             picking_status="approved", company_id=DEFAULT_COMPANY_ID))
        db.commit()
        job_id = _prepare_preview(db, fake, rows=_grn_rows())

        _run_preview(monkeypatch, factory, job_id)

        counts = _job_row(db, job_id)["metadata"]["autocount_pull"]["counts"]
        assert counts["adopted"] == 1 and counts["created"] == 0
        assert "adopted by number" in _job_rows(db, job_id)[0]["message"]
        assert db.execute(text("SELECT doc_key FROM picking_headers WHERE picking_number = 'ZZGRN-0001' "
                               "AND company_id = :c"), {"c": DEFAULT_COMPANY_ID}).scalar() is None

    def test_gp13_linkage_counts_and_worded_warnings(self, task_db, monkeypatch):
        """Line 0 (P1) names an SPO holding only P2 (item not on the order), line 1 (P2)
        links to that SPO's line."""
        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        ids = _seed_masters(db)
        _spo_line(db, "SPO-ZZG-0013", ids["ZZAC-P2"], 20, 1)
        rows = _grn_rows()
        for detail in rows[0]["Details"]:
            detail.update({"FromDocType": "PO", "FromDocNo": "SPO-ZZG-0013", "FromDocDtlKey": 0})
        job_id = _prepare_preview(db, fake, rows=rows)

        _run_preview(monkeypatch, factory, job_id)

        counts = _job_row(db, job_id)["metadata"]["autocount_pull"]["counts"]
        assert counts["lines_linked"] == 1 and counts["lines_unlinked"] == 1
        assert counts["with_warnings"] == 1
        message = _job_rows(db, job_id)[0]["message"]
        assert "item not on the named PO / SPO" in message
        assert "item_not_on_order" not in message

    def test_gp14_preview_progress_reaches_total(self, task_db, monkeypatch):
        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        _seed_masters(db)
        job_id = _prepare_preview(db, fake, rows=_grn_rows())
        _run_preview(monkeypatch, factory, job_id)
        row = db.execute(text("SELECT processed_rows, total_rows FROM import_jobs WHERE id = :id"),
                         {"id": str(job_id)}).first()
        assert tuple(row) == (1, 1)


def _assert_outcome_agrees(db, job_id, *, skipped: int) -> None:
    job = db.execute(
        text("SELECT result, processed_rows, successful_rows, failed_rows, skipped_rows "
             "FROM import_jobs WHERE id = :id"), {"id": str(job_id)},
    ).mappings().first()
    counts = job["result"]["counts"]
    assert (counts["processed"], counts["successful"], counts["failed"], counts["skipped"]) == (
        job["processed_rows"], job["successful_rows"], job["failed_rows"], job["skipped_rows"])
    assert counts["skipped"] == skipped
    rows = _job_rows(db, job_id)
    assert [(r["outcome"], r["code"]) for r in rows] == [("skipped", "unchanged")] * skipped
    assert {e["code"] for e in job["result"]["breakdown"]["skipped"]} == ({"unchanged"} if skipped else set())


# ======================================================================= AC-GP-40
class TestApply:
    def test_gp40_apply_writes_through_the_ingest_and_runs_the_receipt_hook(self, task_db, monkeypatch):
        from app.models.procurement import PickingLine, SPOAllocation

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        ids = _seed_masters(db)
        alloc = _spo_line(db, "SPO-ZZG-0040", ids["ZZAC-P2"], 50, 1)
        rows = _grn_rows()
        rows[0]["Details"][1].update({"FromDocType": "PO", "FromDocNo": "SPO-ZZG-0040",
                                      "FromDocDtlKey": 0})
        job_id = _prepare_apply(db, fake, rows=rows)

        _run_apply(monkeypatch, factory, job_id)

        row = _job_row(db, job_id)
        assert row["status"] == "finished", row["error"]
        assert row["metadata"]["autocount_apply"]["counts"]["created"] == 1
        (grn,) = _grns(db, 800001)
        assert grn["source_book"] == BOOK
        assert "source_ref" not in grn["source_record"]
        # Crew e2e gap 4: the apply job carries the outcome envelope the job page's Outcome
        # card reads; without it a fresh job read "ran before per-row outcome capture".
        result = db.execute(text("SELECT result FROM import_jobs WHERE id = :id"),
                            {"id": str(job_id)}).scalar()
        assert result and set(result["breakdown"]) == {"successful", "skipped", "failed"}
        assert result["counts"]["successful"] == 1
        db.expire_all()
        line = db.query(PickingLine).filter(PickingLine.dtl_key == 810002).one()
        assert line.spo_allocation_id == alloc
        assert db.get(SPOAllocation, alloc).quantity_received == 20  # the hook ran

        # The same snapshot applied again changes nothing.
        again = _prepare_apply(db, fake, rows=rows)
        _run_apply(monkeypatch, factory, again)
        assert _job_row(db, again)["metadata"]["autocount_apply"]["counts"]["unchanged"] == 1
        # Crew e2e gap 4, round 2: an unchanged re-apply lists its documents as skipped
        # rows ("Already up to date") and the Outcome card's counts agree with the job's
        # own columns (it read Skipped 0 against skipped_rows 7).
        _assert_outcome_agrees(db, again, skipped=1)

    def test_gp41_documents_are_ingested_in_doc_date_order(self, task_db, monkeypatch):
        """Plan 1.3 rule 12: the snapshot serves the later GRN first, but the earlier receipt
        takes the SPO's first line."""
        from app.models.procurement import PickingLine

        db, factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)
        ids = _seed_masters(db)
        first = _spo_line(db, "SPO-ZZG-0041", ids["ZZAC-P2"], 20, 1)
        second = _spo_line(db, "SPO-ZZG-0041", ids["ZZAC-P2"], 20, 2)
        base = _grn_rows()[0]
        for detail in base["Details"]:
            detail.update({"FromDocType": "PO", "FromDocNo": "SPO-ZZG-0041", "FromDocDtlKey": 0})
        base["Details"] = base["Details"][1:]  # the P2 x 20 line only
        late = _second([base], 800412, "ZZGRN-0412", "2026-07-20T00:00:00")
        early = _second([base], 800411, "ZZGRN-0411", "2026-07-10T00:00:00")
        job_id = _prepare_apply(db, fake, rows=[late, early])

        _run_apply(monkeypatch, factory, job_id)

        assert _job_row(db, job_id)["status"] == "finished"
        db.expire_all()
        link = {l.dtl_key: l.spo_allocation_id for l in db.query(PickingLine)
                .filter(PickingLine.dtl_key.in_([8004110, 8004120])).all()}
        assert link == {8004110: first, 8004120: second}


# ======================================================================= AC-GP-50..52
def _detail_listing(rows: list[dict]) -> list[dict]:
    """"DETAIL LISTING ddmmyyyy.xlsx", sheet `Sheet`: one row per GRN line plus a totals
    row with no Doc No; `Our PO No.` carries AutoCount's FromDocNo. Made-up values."""
    out = []
    for rec in rows:
        for line in rec["Details"]:
            out.append({
                "Check": "", "Doc No": rec["DocNo"], "Doc Date": "27/07/2026",
                "Creditor Code": rec["CreditorCode"], "Creditor Name": rec["CreditorName"],
                "Our PO No.": line.get("FromDocNo") or "", "Cancelled": "F",
                "Item Code": line["ItemCode"], "Location": line["Location"], "Qty": line["Qty"],
                "UOM": line["UOM"], "Unit Price": line["UnitPrice"], "Total (Ex)": line["SubTotal"],
            })
    out.append({"Qty": sum(r["Qty"] for r in out)})  # totals row: blank cells are left out
    return out


def _grn_listing(rows: list[dict]) -> list[dict]:
    """"GRN Listing - Macro Version ....xlsm", sheet `Master`: one row per GRN."""
    return [{"Doc. No.": rec["DocNo"], "Transfer From": "", "Date": "27/07/2026",
             "Creditor Code": rec["CreditorCode"], "Creditor Name": rec["CreditorName"],
             "Cancelled": "F"} for rec in rows]


class TestRowsDownloadCompare:
    def test_gp50_rows_one_per_grn_line(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, _ = _seed_review_job(env, owner=owner)
        resp = env.client.get(f"{PULLS_URL}/{job_id}/rows")
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]
        assert len(items) == 2
        assert set(items[0]) >= {"doc_no", "doc_date", "creditor_code", "creditor_name",
                                 "item_code", "description", "location", "qty", "uom",
                                 "from_doc_no"}
        assert items[0]["doc_no"] == "ZZGRN-0001" and items[0]["qty"] == 100
        assert items[0]["doc_date"] == "2026-07-27"

    def test_gp51_download_header_row(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, _ = _seed_review_job(env, owner=owner)
        resp = env.client.get(f"{PULLS_URL}/{job_id}/download.xlsx")
        assert resp.status_code == 200, resp.text
        sheet = openpyxl.load_workbook(io.BytesIO(resp.content)).active
        assert [c.value for c in sheet[1]] == [
            "Doc No", "Doc Date", "Creditor Code", "Creditor Name", "Our PO No.", "Item Code",
            "Description", "Location", "Qty", "UOM",
        ]
        assert sheet.max_row == 3

    def test_gp52_lines_compare_qty_multiset_and_source_document(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        rows = _grn_rows()
        rows[0]["Details"][0].update({"FromDocType": "PO", "FromDocNo": "SPO-2026/07-0001"})
        # A second line of the same item at the same location: Qty compares as a multiset.
        extra = copy.deepcopy(rows[0]["Details"][0])
        extra.update({"DtlKey": 810003, "Qty": 24.0})
        rows[0]["Details"].append(extra)
        job_id, _ = _seed_review_job(env, owner=owner, rows=rows)
        sheet = _detail_listing(rows)
        # Rows are not in AutoCount order in the real file: same multiset, other order.
        sheet[0], sheet[2] = sheet[2], sheet[0]
        sheet[1]["Qty"] = 19  # P2: 20 in AutoCount
        sheet[2]["Our PO No."] = "spo-2026.07-0001"  # same number, other spelling

        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "DETAIL LISTING 01102026 pm.xlsx",
                                     "rows": sheet, "source": "lines"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "lines"
        assert body["only_in_excel"] == [] and body["only_in_pull"] == []
        fields = {(d["item_code"], d["field"]): (d["excel"], d["pull"]) for d in body["differences"]}
        assert fields == {("ZZAC-P2", "qty"): ("19", "20")}
        assert body["source_summary"]["matched"] == 1 and body["source_summary"]["different"] == 1

    def test_gp52d_lines_source_document_that_differs_is_a_difference(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        rows = _grn_rows()
        rows[0]["Details"][0].update({"FromDocType": "PO", "FromDocNo": "SPO-2026/07-0001"})
        job_id, _ = _seed_review_job(env, owner=owner, rows=rows)
        sheet = _detail_listing(rows)
        sheet[0]["Our PO No."] = "SPO-2026/07-0099"
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "d.xlsx", "rows": sheet, "source": "lines"})
        fields = {(d["item_code"], d["field"]): (d["excel"], d["pull"]) for d in resp.json()["differences"]}
        assert fields == {("ZZAC-P1", "source_doc"): ("SPO-2026/07-0099", "SPO-2026/07-0001")}

    def test_gp52e_listing_transfer_from_that_differs_is_a_difference(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        rows = _grn_rows()
        for detail in rows[0]["Details"]:
            detail.update({"FromDocType": "PO", "FromDocNo": "SPO-2026/07-0001"})
        job_id, _ = _seed_review_job(env, owner=owner, rows=rows)
        sheet = _grn_listing(rows)
        sheet[0]["Transfer From"] = "spo-2026.07-0001"  # same document: agrees
        same = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "l.xlsm", "rows": sheet, "source": "headers"})
        assert same.json()["differences"] == []
        sheet[0]["Transfer From"] = "SPO-2026/07-0001, SPO-2026/07-0002"
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "l.xlsm", "rows": sheet, "source": "headers"})
        fields = {(d["doc_no"], d["field"]): (d["excel"], d["pull"]) for d in resp.json()["differences"]}
        assert fields == {("ZZGRN-0001", "source_doc"): ("SPO-2026/07-0001, SPO-2026/07-0002",
                                                          "SPO-2026/07-0001")}

    def test_gp52f_the_totals_row_is_not_counted_as_a_line_or_a_document(self, env):
        """Crew e2e gap 3: the Detail Listing reported 72 lines in the window against
        AutoCount's 71 - the sheet's last row is a totals row with no Doc No (and so no
        date), which the window kept. Rows with no document number are neither in the
        window nor ignored outside it: they are not lines."""
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, rows = _seed_review_job(env, owner=owner)
        lines = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                                json={"filename": "d.xlsx", "rows": _detail_listing(rows),
                                      "source": "lines"}).json()
        assert (lines["rows_in_window"], lines["ignored_outside_window"]) == (2, 0)
        # The browser's parser leaves blank cells out: a totals row has no Doc No key at all.
        listing = _grn_listing(rows) + [{"Sub-Total (ex)": 745}, {"Doc. No.": " "}]
        headers = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                                  json={"filename": "l.xlsm", "rows": listing,
                                        "source": "headers"}).json()
        assert (headers["rows_in_window"], headers["ignored_outside_window"]) == (1, 0)

    def test_gp52b_split_quantity_is_a_difference(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, rows = _seed_review_job(env, owner=owner)
        sheet = _detail_listing(rows)[:-1]
        sheet[0]["Qty"] = 98
        sheet.append({**sheet[0], "Qty": 2})
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "d.xlsx", "rows": sheet, "source": "lines"})
        fields = {(d["item_code"], d["field"]): (d["excel"], d["pull"]) for d in resp.json()["differences"]}
        assert fields == {("ZZAC-P1", "qty"): ("2, 98", "100")}

    def test_gp52c_headers_compare_and_a_grn_missing_from_the_listing(self, env):
        """Live 2026-10-01: GR-2026/10-0006 is in AutoCount but not in the listing; it is an
        expected 'only in AutoCount' row, never a delete."""
        owner = env.user(SLUG)
        env.as_user(owner)
        rows = _grn_rows()
        rows.append(_second(rows, 800006, "ZZGRN-0006", "2026-07-28T00:00:00"))
        job_id, _ = _seed_review_job(env, owner=owner, rows=rows)
        sheet = _grn_listing(rows[:1])
        sheet[0]["Creditor Code"] = "400-OTHER"

        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "GRN Listing - Macro Version 01.10.2026 pm.xlsm",
                                     "rows": sheet, "source": "headers"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["only_in_pull"] == ["ZZGRN-0006"] and body["only_in_excel"] == []
        fields = {(d["doc_no"], d["field"]): (d["excel"], d["pull"]) for d in body["differences"]}
        assert fields == {("ZZGRN-0001", "creditor_code"): ("400-OTHER", "400-ZZAC01")}

    def test_gp53_compare_mappings_name_the_grn_sheets(self, env):
        from app.services import autocount_compare_mapping as mapping

        assert mapping.DEFAULT_MAPPINGS["grn_detail_listing"]["sheet_name"] == "Sheet"
        assert mapping.DEFAULT_MAPPINGS["grn_listing"]["sheet_name"] == "Master"
        assert mapping.kind_for(ENTITY, "lines") == "grn_detail_listing"
        assert mapping.kind_for(ENTITY, "headers") == "grn_listing"
        assert mapping.kind_for("delivery_orders", "lines") == "order_listing"
        owner = env.user(SLUG)
        env.as_user(owner)
        kinds = {m["kind"] for m in env.client.get(f"{PULLS_URL}/compare-mappings").json()["items"]}
        assert kinds == {"grn_detail_listing", "grn_listing"}
