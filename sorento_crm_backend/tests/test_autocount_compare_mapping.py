"""RED tests for lane DO-COMPARE-SIM, slice S1 (backend): configurable compare mapping.

Plan: documentation/plans/autocount/PLAN-do-compare-mapping.md
UAC:  documentation/plans/autocount/do-compare-mapping-acceptance-criteria.md

Names this lane adds (`app.services.autocount_compare_mapping`, the `mapping` argument of the
pure compare functions, the `/compare-mappings` routes, the migration) are imported INSIDE a
test body so a missing piece reds one test and never collection.
"""
from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.main import app  # noqa: E402,F401  (first app import, repo convention)

from tests.test_autocount_pull_delivery_orders import (  # noqa: F401 - env is a fixture
    PULLS_URL,
    SLUG,
    VERSIONS,
    _do_rows,
    _headers_sheet,
    _lines,
    _lines_sheet,
    _seed_do_review_job,
    env,
)

URL = f"{PULLS_URL}/compare-mappings"
MIGRATION = "dcm_0001_compare_mappings"

DEFAULT_LISTING = [
    ("Doc No", "text", "doc_no"), ("Doc Date", "date", "doc_date"),
    ("Item Code", "text", "item_code"), ("Location", "text", "location"),
    ("Qty", "number", "qty"), ("Unit Price", "money", "unit_price"),
    ("Discount", "percent_text", "discount"), ("Total (Ex)", "money", "total_ex"),
]
DEFAULT_TRACKING = [
    ("Doc. No.", "text", "doc_no"), ("Date", "date", "doc_date"),
    ("Debtor Code", "text", "debtor_code"), ("Cancel", "cancel_flag", "cancel"),
]


def _cols(triples) -> list[dict]:
    return [{"excel_header": h, "transform": t, "field": f} for h, t, f in triples]


def _mapping(triples, sheet="Master") -> dict:
    return {"sheet_name": sheet, "columns": _cols(triples)}


def _listing_with(**overrides) -> list[tuple]:
    """DEFAULT_LISTING with one field's (header, transform) replaced."""
    out = []
    for h, t, f in DEFAULT_LISTING:
        if f in overrides:
            h, t = overrides[f]
        out.append((h, t, f))
    return out


def _by_kind(items: list[dict]) -> dict:
    return {i["kind"]: i for i in items}


# ============================================================ AC-CMM-1


class TestGetMappings:
    def test_cmm_1_returns_both_kinds_with_defaults(self, env):
        env.as_user(env.user(SLUG))
        resp = env.client.get(URL)
        assert resp.status_code == 200, resp.text
        items = _by_kind(resp.json()["items"])
        assert set(items) == {"order_listing", "order_tracking"}
        assert items["order_listing"]["sheet_name"] == "Master"
        assert items["order_tracking"]["sheet_name"] == "Master"
        assert items["order_listing"]["columns"] == _cols(DEFAULT_LISTING)
        assert items["order_tracking"]["columns"] == _cols(DEFAULT_TRACKING)

    def test_cmm_1_without_the_slug_is_403(self, env):
        env.as_user(env.user("master_data.products.autocount_pull"))
        assert env.client.get(URL).status_code == 403
        body = _mapping(DEFAULT_LISTING)
        assert env.client.put(f"{URL}/order_listing", json=body).status_code == 403

    def test_cmm_1_service_constants_and_defaults(self):
        from app.services.autocount_compare_mapping import (
            DEFAULT_MAPPINGS, FIELDS_BY_KIND, KINDS, TRANSFORMS,
        )

        # The two GRN kinds joined in GRN-PULL-CRM (plan 1.5); their pins live in
        # test_autocount_pull_goods_receive_notes.py.
        assert KINDS == ("order_listing", "order_tracking", "grn_detail_listing", "grn_listing")
        assert TRANSFORMS == ("text", "number", "money", "date", "percent_text",
                              "percent_fraction", "cancel_flag")
        assert tuple(FIELDS_BY_KIND["order_listing"]) == tuple(f for _, _, f in DEFAULT_LISTING)
        assert tuple(FIELDS_BY_KIND["order_tracking"]) == ("doc_no", "doc_date", "debtor_code", "cancel")
        assert DEFAULT_MAPPINGS["order_listing"] == _mapping(DEFAULT_LISTING)
        assert DEFAULT_MAPPINGS["order_tracking"] == _mapping(DEFAULT_TRACKING)

    def test_cmm_1_service_falls_back_to_defaults_without_a_row(self, env):
        from app.services.autocount_compare_mapping import DEFAULT_MAPPINGS, get_mapping

        env.db.execute(text("DELETE FROM autocount_compare_mappings"))
        env.db.flush()
        assert get_mapping(env.db, "order_listing")["columns"] == DEFAULT_MAPPINGS["order_listing"]["columns"]


# ============================================================ AC-CMM-2


class TestPutMappings:
    def test_cmm_2_put_round_trips(self, env):
        env.as_user(env.user(SLUG))
        saved = _mapping(_listing_with(doc_no=("Doc Number", "text"), discount=("Disc", "percent_fraction")),
                         sheet="Sheet X")
        resp = env.client.put(f"{URL}/order_listing", json=saved)
        assert resp.status_code == 200, resp.text
        assert resp.json()["sheet_name"] == "Sheet X"
        assert resp.json()["columns"] == saved["columns"]
        items = _by_kind(env.client.get(URL).json()["items"])
        assert items["order_listing"]["sheet_name"] == "Sheet X"
        assert items["order_listing"]["columns"] == saved["columns"]
        assert items["order_tracking"]["columns"] == _cols(DEFAULT_TRACKING)

    @pytest.mark.parametrize("case", [
        "bad_transform", "field_outside_kind", "duplicate_field", "blank_header",
        "missing_item_code", "missing_doc_no",
    ])
    def test_cmm_2_invalid_is_422_and_stores_nothing(self, env, case):
        env.as_user(env.user(SLUG))
        cols = _cols(DEFAULT_LISTING)
        if case == "bad_transform":
            cols[4]["transform"] = "formula"
        elif case == "field_outside_kind":
            cols[4]["field"] = "cancel"
        elif case == "duplicate_field":
            cols.append({"excel_header": "Qty 2", "transform": "number", "field": "qty"})
        elif case == "blank_header":
            cols[4]["excel_header"] = "   "
        elif case == "missing_item_code":
            cols = [c for c in cols if c["field"] != "item_code"]
        elif case == "missing_doc_no":
            cols = [c for c in cols if c["field"] != "doc_no"]
        resp = env.client.put(f"{URL}/order_listing", json={"sheet_name": "Nope", "columns": cols})
        assert resp.status_code == 422, resp.text
        after = _by_kind(env.client.get(URL).json()["items"])["order_listing"]
        assert after["sheet_name"] == "Master"
        assert after["columns"] == _cols(DEFAULT_LISTING)

    def test_cmm_2_tracking_requires_only_doc_no(self, env):
        env.as_user(env.user(SLUG))
        ok = env.client.put(f"{URL}/order_tracking", json=_mapping([("Doc", "text", "doc_no")]))
        assert ok.status_code == 200, ok.text
        bad = env.client.put(f"{URL}/order_tracking", json=_mapping([("Date", "date", "doc_date")]))
        assert bad.status_code == 422, bad.text

    def test_cmm_2_unknown_kind_is_404(self, env):
        env.as_user(env.user(SLUG))
        resp = env.client.put(f"{URL}/grn", json=_mapping(DEFAULT_LISTING))
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == "UNKNOWN_KIND", resp.text

    def test_cmm_2_service_raises_invalid_mapping(self, env):
        from app.services.autocount_compare_mapping import save_mapping
        from app.services.error_handler import AppException

        owner = env.user(SLUG)
        with pytest.raises(AppException) as exc:
            save_mapping(env.db, "order_listing", "Master",
                         [{"excel_header": "X", "transform": "bogus", "field": "doc_no"}], owner["id"])
        assert exc.value.status_code == 422
        assert exc.value.code == "INVALID_MAPPING"
        with pytest.raises(AppException) as exc:
            save_mapping(env.db, "grn", "Master", _cols(DEFAULT_LISTING), owner["id"])
        assert exc.value.status_code == 404 and exc.value.code == "UNKNOWN_KIND"


# ============================================ fix round 1: transform per field


class TestFieldTransformPairs:
    def test_fix1_table_is_exported(self):
        from app.services.autocount_compare_mapping import TRANSFORMS_BY_FIELD

        assert TRANSFORMS_BY_FIELD == {
            "doc_no": ("text",), "item_code": ("text",), "location": ("text",),
            "debtor_code": ("text",), "doc_date": ("date",), "qty": ("number",),
            "unit_price": ("money",), "total_ex": ("money",),
            "discount": ("percent_text", "percent_fraction"), "cancel": ("cancel_flag",),
            "creditor_code": ("text",), "source_doc": ("text",),
        }

    @pytest.mark.parametrize("kind,field,transform", [
        ("order_listing", "qty", "text"),
        ("order_listing", "doc_date", "text"),
        ("order_listing", "doc_date", "money"),
        ("order_listing", "unit_price", "text"),
        ("order_listing", "total_ex", "text"),
        ("order_tracking", "cancel", "text"),
        ("order_listing", "discount", "number"),
        ("order_listing", "doc_no", "date"),
    ])
    def test_fix1_refused_pairing_is_422_and_stores_nothing(self, env, kind, field, transform):
        env.as_user(env.user(SLUG))
        defaults = DEFAULT_LISTING if kind == "order_listing" else DEFAULT_TRACKING
        cols = _cols(defaults)
        for c in cols:
            if c["field"] == field:
                c["transform"] = transform
        if not any(c["field"] == field for c in cols):
            cols.append({"excel_header": "Extra", "transform": transform, "field": field})
        resp = env.client.put(f"{URL}/{kind}", json={"sheet_name": "Nope", "columns": cols})
        assert resp.status_code == 422, resp.text
        assert resp.json().get("code") == "INVALID_MAPPING", resp.text
        after = _by_kind(env.client.get(URL).json()["items"])[kind]
        assert after["sheet_name"] == "Master"
        assert after["columns"] == _cols(defaults)

    def test_fix1_allowed_pairing_still_saves(self, env):
        env.as_user(env.user(SLUG))
        body = _mapping(_listing_with(discount=("Discount", "percent_fraction")))
        assert env.client.put(f"{URL}/order_listing", json=body).status_code == 200

    def test_fix1_empty_field_says_a_sorento_field_is_required(self, env):
        from app.services.autocount_compare_mapping import save_mapping
        from app.services.error_handler import AppException

        owner = env.user(SLUG)
        cols = _cols(DEFAULT_LISTING) + [{"excel_header": "Extra", "transform": "text", "field": ""}]
        with pytest.raises(AppException) as exc:
            save_mapping(env.db, "order_listing", "Master", cols, owner["id"])
        assert exc.value.status_code == 422 and exc.value.code == "INVALID_MAPPING"
        message = str(exc.value.message).lower()
        assert "required" in message and "sorento field" in message
        assert "is not a field" not in message

    def test_fix1_long_sheet_name_says_too_long(self, env):
        from app.services.autocount_compare_mapping import save_mapping
        from app.services.error_handler import AppException

        owner = env.user(SLUG)
        with pytest.raises(AppException) as exc:
            save_mapping(env.db, "order_listing", "S" * 101, _cols(DEFAULT_LISTING), owner["id"])
        assert exc.value.status_code == 422
        assert "too long" in str(exc.value.message).lower()

    def test_fix1_route_caps_are_pydantic_422_and_store_nothing(self, env):
        env.as_user(env.user(SLUG))
        base = _cols(DEFAULT_LISTING)
        cases = {
            "sheet": {"sheet_name": "S" * 101, "columns": base},
            "transform": {"sheet_name": "Master", "columns": [{**base[0], "transform": "t" * 41}] + base[1:]},
            "field": {"sheet_name": "Master", "columns": [{**base[0], "field": "f" * 41}] + base[1:]},
            "columns": {"sheet_name": "Master",
                        "columns": base + [{"excel_header": f"H{i}", "transform": "text", "field": "doc_no"}
                                           for i in range(101 - len(base))]},
        }
        for name, body in cases.items():
            resp = env.client.put(f"{URL}/order_listing", json=body)
            assert resp.status_code == 422, (name, resp.text)
            # pydantic rejects before the service runs: its error list, not the service's code
            assert isinstance(resp.json().get("detail"), list), (name, resp.text)
        after = _by_kind(env.client.get(URL).json()["items"])["order_listing"]
        assert after["columns"] == _cols(DEFAULT_LISTING)


# ============================================================ AC-CMM-3


def _discount_excel(rows, value):
    sheet = _lines_sheet(rows)
    sheet[0]["Discount"] = value
    return sheet


def _pull_with_discount(discount):
    rows = _do_rows()
    rows[0]["Details"][0]["Discount"] = discount
    return rows


class TestTransforms:
    def _diffs(self, excel, pull, mapping=None):
        from app.services.autocount_pull_compare import compare_delivery_orders

        result = compare_delivery_orders(excel, pull, mapping)
        return [d for d in result["differences"] if d["field"] == "discount"]

    def test_cmm_3_percent_fraction_matches_percent_text(self):
        m = _mapping(_listing_with(discount=("Discount", "percent_fraction")))
        pull = _pull_with_discount("37%")
        assert self._diffs(_discount_excel(pull, 0.37), pull, m) == []
        pull = _pull_with_discount("100%")
        assert self._diffs(_discount_excel(pull, 1), pull, m) == []

    def test_cmm_3_percent_fraction_still_reports_a_real_difference(self):
        m = _mapping(_listing_with(discount=("Discount", "percent_fraction")))
        pull = _pull_with_discount("37%")
        assert len(self._diffs(_discount_excel(pull, 0.5), pull, m)) == 1

    def test_cmm_3_percent_text_matches_compound_discount(self):
        m = _mapping(DEFAULT_LISTING)
        pull = _pull_with_discount("40%+5%")
        assert self._diffs(_discount_excel(pull, "40%+5%"), pull, m) == []

    def test_cmm_3_percent_text_bare_number_reads_as_percent(self):
        m = _mapping(DEFAULT_LISTING)
        pull = _pull_with_discount("37%")
        assert self._diffs(_discount_excel(pull, 37), pull, m) == []

    def test_cmm_3_default_percent_text_reports_a_fraction_as_a_difference(self):
        """Proves the transform matters: the same 0.37 against "37%" under percent_text."""
        pull = _pull_with_discount("37%")
        assert len(self._diffs(_discount_excel(pull, 0.37), pull, _mapping(DEFAULT_LISTING))) == 1
        assert len(self._diffs(_discount_excel(pull, 0.37), pull, None)) == 1


# ============================================================ AC-CMM-4


class TestBlankLineTotal:
    def _master_rows(self):
        pull = _do_rows()
        # ZZDO-0001 / ZZAC-P1 is the promotion-package line: the pull has SubTotalExTax 0.
        pull[0]["Details"][0]["SubTotalExTax"] = 0.0
        pull[0]["Details"][0]["SubTotal"] = 0.0
        sheet = _lines_sheet(pull)
        for row in sheet:
            row["Total"] = 735.3  # the DOCUMENT total
            row["Total_1"] = row.pop("Total (Ex)")  # line total column the macro exports twice
        sheet[0]["Total (Ex)"] = None  # blank on the PP line
        sheet[0]["Total_1"] = 0
        return sheet, pull

    def test_cmm_4_blank_total_ex_is_not_compared_and_document_total_never_read(self):
        from app.services.autocount_pull_compare import compare_delivery_orders

        sheet, pull = self._master_rows()
        result = compare_delivery_orders(sheet, pull, _mapping(DEFAULT_LISTING))
        assert [d for d in result["differences"] if d["field"] == "total_ex"] == []
        assert result["summary"]["total"] == 3

    def test_cmm_4_absent_total_ex_column_is_not_compared_either(self):
        from app.services.autocount_pull_compare import compare_delivery_orders

        sheet, pull = self._master_rows()
        for row in sheet:
            row.pop("Total (Ex)", None)
        result = compare_delivery_orders(sheet, pull, _mapping(DEFAULT_LISTING))
        assert [d for d in result["differences"] if d["field"] == "total_ex"] == []


# ============================================================ AC-CMM-5


class TestCancelledDocuments:
    def _rows(self):
        rows = _do_rows()
        rows[1]["Cancelled"] = "T"  # ZZDO-0002 cancelled in AutoCount
        return rows

    def test_cmm_5_lines_compare_skips_cancelled_pull_documents(self):
        from app.services.autocount_pull_compare import compare_delivery_orders

        rows = self._rows()
        listing = [r for r in _lines_sheet(rows) if r["Doc No"] != "ZZDO-0002"]
        result = compare_delivery_orders(listing, rows, _mapping(DEFAULT_LISTING))
        assert result["only_in_pull"] == []
        assert result["summary"]["total"] == 2
        assert result["differences"] == []

    def test_cmm_5_headers_compare_still_reports_the_cancel_difference(self):
        from app.services.autocount_pull_compare import compare_delivery_order_headers

        rows = self._rows()
        result = compare_delivery_order_headers(_headers_sheet(rows), rows, _mapping(DEFAULT_TRACKING))
        cancel = [d for d in result["differences"] if d["field"] == "cancel"]
        assert cancel == [{"item_code": "", "doc_no": "ZZDO-0002", "location": "",
                           "field": "cancel", "excel": False, "pull": True}]

    def test_cmm_5_route_lines_source_skips_cancelled(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        rows = self._rows()
        job_id, _ = _seed_do_review_job(env, owner=owner, rows=rows)
        listing = [r for r in _lines_sheet(rows) if r["Doc No"] != "ZZDO-0002"]
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare",
                               json={"filename": "l.xlsm", "rows": listing, "source": "lines"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["only_in_pull"] == []


# ============================================================ AC-CMM-6


RENAMED = [
    ("Doc Number", "text", "doc_no"), ("Posting Date", "date", "doc_date"),
    ("Product Code", "text", "item_code"), ("Warehouse", "text", "location"),
    ("Quantity", "number", "qty"), ("Unit Price", "money", "unit_price"),
    ("Discount", "percent_text", "discount"), ("Total (Ex)", "money", "total_ex"),
]


def _renamed_sheet(rows, doc_date="27/09/2026") -> list[dict]:
    return [{"Doc Number": r["Doc No"], "Posting Date": r["Doc Date"], "Product Code": r["Item Code"],
             "Warehouse": r["Location"], "Quantity": r["Qty"], "Unit Price": r["Unit Price"],
             "Discount": r["Discount"], "Total (Ex)": r["Total (Ex)"]}
            for r in _lines_sheet(rows, doc_date)]


class TestRenamedColumns:
    def test_cmm_6_rows_are_keyed_by_the_renamed_columns(self):
        from app.services.autocount_pull_compare import compare_delivery_orders

        rows = _do_rows()
        result = compare_delivery_orders(_renamed_sheet(rows), rows, _mapping(RENAMED))
        assert result["summary"] == {"total": 3, "matched": 3, "different": 0}
        assert result["only_in_excel"] == [] and result["only_in_pull"] == []

    def test_cmm_6_a_column_that_is_not_mapped_is_never_read(self):
        """No alias fallback: the default mapping does not name `Doc Number`."""
        from app.services.autocount_pull_compare import compare_delivery_orders

        rows = _do_rows()
        result = compare_delivery_orders(_renamed_sheet(rows), rows, None)
        assert result["summary"]["total"] == 0
        assert len(result["only_in_pull"]) == 3

    def test_cmm_6_window_reads_the_mapped_date_column(self):
        from app.services.autocount_pull_compare import window_excel_rows

        rows = _do_rows()
        sheet = _renamed_sheet(rows)
        sheet[0]["Posting Date"] = "15/08/2026"
        kept, ignored = window_excel_rows(sheet, "2026-09-01", "2026-09-30", _mapping(RENAMED))
        assert ignored == 1 and len(kept) == 2
        # Default mapping reads `Doc Date`, which this sheet does not carry: nothing is cut.
        kept, ignored = window_excel_rows(sheet, "2026-09-01", "2026-09-30", None)
        assert ignored == 0 and len(kept) == 3

    def test_cmm_6_headers_window_and_compare_use_the_tracking_mapping(self):
        from app.services.autocount_pull_compare import (
            compare_delivery_order_headers, window_excel_rows,
        )

        rows = _do_rows()
        mapping = _mapping([("Doc", "text", "doc_no"), ("Day", "date", "doc_date"),
                            ("Customer", "text", "debtor_code"), ("Void", "cancel_flag", "cancel")])
        sheet = [{"Doc": r["DocNo"], "Day": "27/09/2026", "Customer": r["DebtorCode"], "Void": "F"}
                 for r in rows]
        sheet[0]["Day"] = "01/01/2026"
        kept, ignored = window_excel_rows(sheet, "2026-09-01", "2026-09-30", mapping)
        assert ignored == 1
        result = compare_delivery_order_headers(sheet, rows, mapping)
        assert result["summary"] == {"total": 2, "matched": 1, "different": 1}
        assert [d["field"] for d in result["differences"]] == ["doc_date"]

    def test_cmm_6_route_uses_the_saved_mapping(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, rows = _seed_do_review_job(env, owner=owner)
        put = env.client.put(f"{URL}/order_listing", json=_mapping(RENAMED))
        assert put.status_code == 200, put.text
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare", json={
            "filename": "renamed.xlsm", "rows": _renamed_sheet(rows), "source": "lines"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_summary"]["matched"] == 3
        assert body["only_in_excel"] == [] and body["only_in_pull"] == []

    def test_cmm_6_route_headers_source_uses_the_tracking_mapping(self, env):
        owner = env.user(SLUG)
        env.as_user(owner)
        job_id, rows = _seed_do_review_job(env, owner=owner)
        tracking = _mapping([("Doc", "text", "doc_no"), ("Customer", "text", "debtor_code")])
        assert env.client.put(f"{URL}/order_tracking", json=tracking).status_code == 200
        sheet = [{"Doc": r["DocNo"], "Customer": r["DebtorCode"]} for r in rows]
        resp = env.client.post(f"{PULLS_URL}/{job_id}/compare", json={
            "filename": "t.xlsm", "rows": sheet, "source": "headers"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["source_summary"]["matched"] == 2


# ============================================================ migration


class TestMigration:
    def _source(self) -> str:
        return (VERSIONS / f"{MIGRATION}.py").read_text()

    def test_cmm_migration_revision_fits_and_sits_on_the_single_head(self):
        """No literal `down_revision`: `scripts/alembic-reparent.sh` rewrites it whenever main's
        head moves. The property is: the id fits, the parent exists, the chain has one head and
        this revision is on its ancestry."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        src = self._source()
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', src, re.M).group(1)
        down = re.search(r'^down_revision\s*=\s*"([^"]+)"', src, re.M).group(1)
        assert rev == MIGRATION and len(rev) <= 32
        script = ScriptDirectory.from_config(Config(str(VERSIONS.parent.parent / "alembic.ini")))
        known = {r.revision for r in script.walk_revisions()}
        assert down in known
        heads = list(script.get_heads())
        assert len(heads) == 1, heads
        assert MIGRATION in {r.revision for r in script.walk_revisions(base="base", head=heads[0])}

    def test_cmm_migration_creates_the_table_and_seeds_two_rows(self):
        src = self._source()
        assert "autocount_compare_mappings" in src
        assert "order_listing" in src and "order_tracking" in src
        assert "Master" in src

    def test_cmm_model_table_name(self):
        from app.models.autocount_compare_mapping import AutocountCompareMapping

        assert AutocountCompareMapping.__tablename__ == "autocount_compare_mappings"
