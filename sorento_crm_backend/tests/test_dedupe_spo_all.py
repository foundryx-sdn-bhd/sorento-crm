"""SPO-DEDUPE-ALL: `dedupe_spo_standalone.py --all` (scan the company for every
SPO holding both Excel-era and AutoCount rows) and the stored container figures
refreshed after a committed apply, so no packing list has to be opened by hand.

Fixtures are the SPO-2026/08-0074 shape (`tests/test_spo_xlsx_product_fallback.py`)
under made-up numbers.
"""
from __future__ import annotations

import ast
import uuid

import pytest
from sqlalchemy import text

from app.models.procurement import SPOAllocation
from tests.test_dedupe_spo_standalone import SCRIPT
from tests.test_spo_xlsx_product_fallback import (
    MARKER,
    _orphan_row,
    _owner_case,
    _picked_on,
    _seed_pre_repair_state,
    _spo_rows,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


def _standalone():
    from scripts.oneoff import dedupe_spo_standalone as standalone

    return standalone


def _number(stem: str) -> str:
    return f"{MARKER}-SPO-{stem}-{uuid.uuid4().hex[:6]}"


def _scan(env, *, apply: bool, refresh=None, **kwargs):
    lines: list[str] = []
    code = _standalone().run(
        env.db, env.company_a_code, None, apply=apply, out=lines.append,
        scan_all=True, refresh=refresh, **kwargs,
    )
    return code, "\n".join(lines)


def _ready(env, number: str):
    """One SPO in the owner's pre-repair shape: Excel 95 + 4, AutoCount 22 + 77."""
    case = _owner_case(env, number=number)
    _seed_pre_repair_state(env, case)
    return case


def _stored_line(env, case) -> tuple[int, str]:
    row = env.db.execute(
        text("SELECT quantity_received, line_status FROM inbound_shipment_lines WHERE id = :id"),
        {"id": case.shipment_line_id},
    ).one()
    return int(row[0] or 0), row[1]


def _make_stale(env, case, received=95, status="partially_received"):
    env.db.execute(
        text("UPDATE inbound_shipment_lines SET quantity_received = :r, line_status = :s WHERE id = :id"),
        {"r": received, "s": status, "id": case.shipment_line_id},
    )
    env.db.commit()


def _autocount_only(env, number: str):
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
    env.db.add(SPOAllocation(
        company_id=env.company_a, spo_number=number, spo_line_number=1, product_id=product_id,
        allocated_quantity=10, quantity_received=0, line_status="open", source_system="autocount",
        source_ref=f"{MARKER}:DTL-{uuid.uuid4().hex[:8]}", source_doc_ref=f"{MARKER}:DOC-{number}",
    ))
    env.db.commit()


def _excel_only(env, number: str):
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
    env.db.add(SPOAllocation(
        company_id=env.company_a, spo_number=number, spo_line_number=1, product_id=product_id,
        allocated_quantity=10, quantity_received=0, line_status="open", source_system="scm_upload",
    ))
    env.db.commit()


class TestCandidates:
    def test_only_spos_with_both_excel_and_autocount_rows_in_number_order(self, env):
        b, a = _number("B"), _number("A")
        _ready(env, b)
        _ready(env, a)
        _autocount_only(env, _number("C"))
        _excel_only(env, _number("D"))

        assert _standalone().candidate_spo_numbers(env.db, str(env.company_a)) == sorted([a, b])

    @pytest.mark.parametrize("other", [{"source_system": "manual"}, {"po_line_link": True}])
    def test_a_row_process_spo_never_treats_as_excel_does_not_make_a_candidate(self, env, other):
        """AutoCount + a ref-less row that is NOT Excel-era (another source system,
        or raised from a PO line): `process_spo` would leave it alone, so `--all`
        must not list it."""
        from tests.test_spo_xlsx_supersede import _seed_po_line

        number = _number("E")
        _autocount_only(env, number)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        row = SPOAllocation(
            company_id=env.company_a, spo_number=number, spo_line_number=2, product_id=product_id,
            allocated_quantity=10, quantity_received=0, line_status="open",
            source_system=other.get("source_system", "scm_upload"),
        )
        if other.get("po_line_link"):
            row.po_line_id = _seed_po_line(env, product_id=product_id)
        env.db.add(row)
        env.db.commit()

        assert number not in _standalone().candidate_spo_numbers(env.db, str(env.company_a))

    def test_another_companys_spos_are_never_candidates(self, env):
        _ready(env, _number("A"))

        assert _standalone().candidate_spo_numbers(env.db, str(env.company_b)) == []


class TestScanDryRun:
    def test_dry_run_summarises_each_spo_writes_nothing_and_lists_what_would_change(self, env):
        a, b = sorted([_number("A"), _number("B")])
        case_a, case_b = _ready(env, a), _ready(env, b)
        _orphan_row(env, case_b, line=23, received=5)  # blocked: carries a receipt
        before = {str(r["id"]) for n in (a, b) for r in _spo_rows(env, n)}

        code, out = _scan(env, apply=False)

        assert code == 0, out
        assert "DRY-RUN (no writes)" in out
        assert "candidates 2" in out
        assert out.index(f"--- {a} ---") < out.index(f"--- {b} ---")
        assert f"=> {a}: Excel rows superseded 2" in out
        assert f"=> {b}: Excel rows superseded 2" in out
        assert f"would change (2): {a}, {b}" in out
        assert f"rows left for review (ORPHAN-BLOCKED / kept) (1): {b}" in out
        assert "aborted (0)" in out
        assert {str(r["id"]) for n in (a, b) for r in _spo_rows(env, n)} == before
        assert _picked_on(env, case_a.excel_95.id) == 95
        assert "packing list to refresh after apply:" in out
        assert f"container {case_a.container} ({case_a.shipment_id})" in out

    def test_limit_takes_the_first_n_candidates(self, env):
        a, b = sorted([_number("A"), _number("B")])
        _ready(env, a)
        _ready(env, b)

        code, out = _scan(env, apply=False, limit=1)

        assert code == 0, out
        assert f"--- {a} ---" in out
        assert f"--- {b} ---" not in out
        assert f"next batch: --start-after {a}" in out

    def test_start_after_skips_up_to_and_including_that_number(self, env):
        a, b = sorted([_number("A"), _number("B")])
        _ready(env, a)
        _ready(env, b)

        code, out = _scan(env, apply=False, start_after=a)

        assert code == 0, out
        assert f"--- {a} ---" not in out
        assert f"--- {b} ---" in out
        assert "next batch" not in out


class TestScanApply:
    def test_apply_dedupes_every_candidate_and_refreshes_the_stale_container(self, env):
        a, b = sorted([_number("A"), _number("B")])
        case_a, case_b = _ready(env, a), _ready(env, b)
        _make_stale(env, case_a)
        _make_stale(env, case_b)

        code, out = _scan(env, apply=True, refresh=_standalone().app_refresher(env.db))

        assert code == 0, out
        for case in (case_a, case_b):
            assert len(_spo_rows(env, case.number)) == 2
            assert _stored_line(env, case) == (99, "received"), out
            assert (f"refreshed packing list" in out) and (case.shipment_id in out)
        assert "received 95 -> 99, status partially_received -> received" in out

        code, again = _scan(env, apply=False)
        assert code == 0
        assert "candidates 0" in again

    def test_a_guard_failure_rolls_back_only_that_spo_and_exits_3(self, env, monkeypatch):
        standalone = _standalone()
        a, b = sorted([_number("A"), _number("B")])
        case_a, case_b = _ready(env, a), _ready(env, b)
        real = standalone.process_spo

        def failing(db, company_id, spo_number, **kwargs):
            if spo_number == a:
                raise standalone.GuardFailed("simulated")
            return real(db, company_id, spo_number, **kwargs)

        monkeypatch.setattr(standalone, "process_spo", failing)

        code, out = _scan(env, apply=True, refresh=standalone.app_refresher(env.db))

        assert code == 3, out
        assert f"aborted (1): {a}" in out
        assert len(_spo_rows(env, a)) == 4
        assert len(_spo_rows(env, b)) == 2

    def test_a_failed_refresh_keeps_the_committed_spo_and_exits_5(self, env):
        a = _number("A")
        case = _ready(env, a)

        def broken(company_id, shipment_id):
            raise RuntimeError("refresh blew up")

        code, out = _scan(env, apply=True, refresh=broken)

        assert code == 5, out
        assert "REFRESH FAILED" in out and case.shipment_id in out
        assert len(_spo_rows(env, a)) == 2


class TestNamedSpoApplyRefreshes:
    def test_spo_apply_refreshes_the_touched_container(self, env):
        a = _number("A")
        case = _ready(env, a)
        _make_stale(env, case)
        lines: list[str] = []

        code = _standalone().run(
            env.db, env.company_a_code, [a], apply=True, out=lines.append,
            refresh=_standalone().app_refresher(env.db),
        )

        assert code == 0, "\n".join(lines)
        assert _stored_line(env, case) == (99, "received")


class TestCli:
    def test_all_and_spo_are_mutually_exclusive_and_one_is_required(self):
        parser = _standalone().build_parser()
        args = parser.parse_args(["--company", "SRT", "--all", "--limit", "20", "--start-after", "SPO-1"])
        assert args.all is True and args.limit == 20 and args.start_after == "SPO-1"
        assert args.apply is False
        with pytest.raises(SystemExit):
            parser.parse_args(["--company", "SRT", "--all", "--spo", "SPO-1"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--company", "SRT"])

    def test_limit_and_start_after_need_all(self):
        assert _standalone().validate_args(
            _standalone().build_parser().parse_args(["--company", "SRT", "--spo", "X", "--limit", "5"])
        )

    def test_app_imports_only_inside_the_refresher(self):
        """The plan and the writes still import nothing from the app (they must
        run against whatever code the container holds); only the post-apply
        refresh reaches for the app's own service, the code the packing list
        page runs."""
        tree = ast.parse(SCRIPT.read_text())
        allowed = {
            id(node)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef) and fn.name == "app_refresher"
            for node in ast.walk(fn)
        }
        offenders = [
            node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in allowed
        ]
        assert not any((m or "").split(".")[0] in {"app", "scripts", "tests"} for m in offenders), offenders
