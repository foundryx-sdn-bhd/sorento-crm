"""The standalone one-off (`scripts/oneoff/dedupe_spo_standalone.py`) on the
SPO-2026/08-0074 shape: the owner runs it on production without deploying
#1411, so it must reach the same end state as the in-app repair on its own.

Fixture reused from `tests/test_spo_xlsx_product_fallback.py`: Excel rows 95
(on PL) + 4 (no PL), HQ, no warehouse; GR picks 22 @ IB + 73 @ NTC + 4 @ NTC;
AutoCount lines IB 22 / NTC 77; plus orphan rows for a product AutoCount does
not list (the L23/L26 shape).
"""
from __future__ import annotations

import pathlib
import uuid

from sqlalchemy import text

from tests.test_spo_xlsx_product_fallback import (
    MARKER,
    OWNER_SPO,
    _orphan_row,
    _owner_case,
    _pl_figures,
    _picked_on,
    _seed_pre_repair_state,
    _spo_rows,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "oneoff" / "dedupe_spo_standalone.py"


def _run(env, *, apply: bool, spo=OWNER_SPO):
    from scripts.oneoff import dedupe_spo_standalone as standalone

    lines: list[str] = []
    code = standalone.run(env.db, env.company_a_code, [spo], apply=apply, out=lines.append)
    return code, "\n".join(lines)


def _ids(env):
    return {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)}


class TestStandaloneOwnerCase:
    def test_dry_run_prints_the_full_plan_and_writes_nothing(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        ib, ntc = _seed_pre_repair_state(env, case)
        o23 = _orphan_row(env, case, line=23)
        o26 = _orphan_row(env, case, line=26)
        before = _ids(env)

        code, out = _run(env, apply=False)

        assert code == 0, out
        assert "DRY-RUN (no writes)" in out
        assert f"delete {case.excel_95.id} line 1 (allocated 95, received 95; suggested links cascaded 0)" in out
        assert f"delete {case.excel_4.id} line 2 (allocated 4, received 4; suggested links cascaded 0)" in out
        assert f"carry 22 -> {ib.id}" in out
        assert f"carry 77 -> {ntc.id}" in out
        assert f"move pick {case.pick_ib_22.id} (22) {case.excel_95.id} -> {ib.id}" in out
        assert f"move pick {case.pick_ntc_73.id} (73) {case.excel_95.id} -> {ntc.id}" in out
        assert f"move pick {case.pick_ntc_4.id} (4) {case.excel_4.id} -> {ntc.id}" in out
        assert f"orphan delete {o23.id} line 23" in out
        assert f"orphan delete {o26.id} line 26" in out
        assert "superseded 2, orphans removed 2, orphans BLOCKED 0" in out
        assert _ids(env) == before
        assert _picked_on(env, case.excel_95.id) == 95

    def test_apply_reaches_the_follow_autocount_end_state_and_is_idempotent(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        ib, ntc = _seed_pre_repair_state(env, case)
        _orphan_row(env, case, line=23)
        _orphan_row(env, case, line=26)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert _ids(env) == {str(ib.id), str(ntc.id)}
        assert _picked_on(env, ib.id) == 22
        assert _picked_on(env, ntc.id) == 77
        assert f"container {case.container} ({case.shipment_id})" in out
        assert _pl_figures(env, case)[:2] == (99, 99)

        code, again = _run(env, apply=False)
        assert code == 0
        assert "no Excel-era rows: already follows AutoCount" in again

    def test_an_orphan_with_a_receipt_or_link_is_blocked(self, env):
        from app.models.scm import OrderLinkClaim

        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        with_receipt = _orphan_row(env, case, line=23, received=5)
        with_claim = _orphan_row(env, case, line=26)
        env.db.add(
            OrderLinkClaim(
                company_id=env.company_a,
                so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
                po_number=OWNER_SPO,
                source="autocount",
                spo_allocation_id=with_claim.id,
            )
        )
        env.db.commit()

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert f"ORPHAN-BLOCKED {with_receipt.id} line 23 (allocated 99, received 5; picks 0, claims 0" in out
        assert f"ORPHAN-BLOCKED {with_claim.id} line 26 (allocated 99, received 0; picks 0, claims 1" in out
        assert {str(with_receipt.id), str(with_claim.id)} <= _ids(env)

    def test_a_guard_failure_rolls_the_spo_back(self, env, monkeypatch):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        real = standalone.distribute

        def lossy(total, quantities):
            shares = real(total, quantities)
            shares[-1] = max(shares[-1] - 1, 0)
            return shares

        monkeypatch.setattr(standalone, "distribute", lossy)
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        before = _ids(env)

        code, out = _run(env, apply=True)

        assert code == 3, out
        assert "ABORTED, rolled back" in out
        assert _ids(env) == before
        assert _picked_on(env, case.excel_95.id) == 95

    def test_company_guard_and_unknown_spo(self, env):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        before = _ids(env)
        lines: list[str] = []
        code = standalone.run(env.db, env.company_b_code, [OWNER_SPO], apply=True, out=lines.append)
        assert code == 0
        assert "no AutoCount lines on this SPO" in "\n".join(lines)
        assert _ids(env) == before


class TestStandaloneSafetyRails:
    def test_preflight_passes_on_a_migrated_database(self, env):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        assert standalone.preflight(env.db) == []

    def test_preflight_aborts_on_a_missing_column(self, env, monkeypatch):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        required = dict(standalone.REQUIRED)
        required[("public", "spo_allocations")] = (
            *required[("public", "spo_allocations")],
            "column_from_a_future_migration",
        )
        monkeypatch.setattr(standalone, "REQUIRED", required)
        code, out = _run(env, apply=True)
        assert code == 2
        assert "PREFLIGHT FAILED" in out
        assert "spo_allocations.column_from_a_future_migration is missing" in out

    def test_cli_requires_company_and_spo_and_defaults_to_dry_run(self):
        import pytest

        from scripts.oneoff import dedupe_spo_standalone as standalone

        args = standalone.build_parser().parse_args(
            ["--company", "SRT", "--spo", OWNER_SPO, "--spo", "SPO-2026/09-0001"]
        )
        assert args.spo == [OWNER_SPO, "SPO-2026/09-0001"]
        assert args.apply is False
        with pytest.raises(SystemExit):
            standalone.build_parser().parse_args(["--company", "SRT"])

    # The import rule (nothing from the app outside the post-apply refresh)
    # lives in tests/test_dedupe_spo_all.py::TestCli.


class TestStandalonePaths:
    def test_claims_move_to_the_first_autocount_line(self, env):
        from app.models.scm import OrderLinkClaim

        case = _owner_case(env, number=OWNER_SPO)
        ib, _ntc = _seed_pre_repair_state(env, case)
        claim = OrderLinkClaim(
            company_id=env.company_a,
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            po_number=OWNER_SPO,
            source="autocount",
            spo_allocation_id=case.excel_95.id,
        )
        env.db.add(claim)
        env.db.commit()
        claim_id, excel_id, ib_id = str(claim.id), str(case.excel_95.id), str(ib.id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert f"move claim {claim_id} {excel_id} -> {ib_id}" in out
        env.db.expire_all()
        assert str(env.db.get(OrderLinkClaim, claim_id).spo_allocation_id) == ib_id

    def test_a_pick_spanning_two_lines_is_split(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        ib, ntc = _seed_pre_repair_state(env, case)
        case.pick_ib_22.quantity_picked = 30
        case.pick_ib_22.quantity_expected = 32
        case.pick_ntc_73.quantity_picked = 65
        case.pick_ntc_73.quantity_expected = 65
        env.db.commit()
        pick_id, header_id = str(case.pick_ib_22.id), str(case.pick_ib_22.picking_header_id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert f"split pick {pick_id} (30): keep 22 on {ib.id}" in out
        assert _picked_on(env, ib.id) == 22
        assert _picked_on(env, ntc.id) == 77
        expected = env.db.execute(
            text(
                "SELECT quantity_picked, quantity_expected FROM picking_lines "
                "WHERE picking_header_id = :h AND source_warehouse_id = :w ORDER BY quantity_picked DESC"
            ),
            {"h": header_id, "w": case.ib_id},
        ).all()
        assert [tuple(r) for r in expected] == [(22, 22), (8, 10)]

    def test_an_excel_row_at_the_same_warehouse_supersedes_whole(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        ib, ntc = _seed_pre_repair_state(env, case)
        # Excel 95 names BRW-NTC itself; AutoCount NTC is 77 - a keyed group the
        # owner's data does not have, checked so the keyed path is covered too.
        case.excel_95.warehouse_id = case.ntc_id
        case.excel_95.allocated_quantity = 77
        case.excel_95.quantity_received = 77
        # Its sibling keyed too (IB 4 within IB 22), so the product is settled by
        # the keyed pass and never pooled (D37 pools a product only when some
        # row of it is left unsettled).
        case.excel_4.warehouse_id = case.ib_id
        env.db.commit()
        excel_id = str(case.excel_95.id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert out.count("group same destination: 1 Excel row(s) -> 1 AutoCount line(s)") == 2
        assert "product fallback" not in out
        assert excel_id not in _ids(env)


class TestStandaloneSecurityRound:
    def _foreign_claim(self, env, allocation_id):
        from app.models.scm import OrderLinkClaim

        claim = OrderLinkClaim(
            company_id=env.company_b,
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            po_number=OWNER_SPO,
            source="autocount",
            spo_allocation_id=allocation_id,
        )
        env.db.add(claim)
        env.db.commit()

    def test_another_companys_link_on_a_row_to_delete_aborts_dry_run_and_apply(self, env):
        """S1: such a claim would be silently cleared by the FK; the SPO is
        refused instead - and the DRY RUN already says so."""
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        self._foreign_claim(env, case.excel_95.id)
        before = _ids(env)

        for apply in (False, True):
            code, out = _run(env, apply=apply)
            assert code == 3, out
            assert "another company's links point at rows to delete (picks 0, claims 1" in out
        assert _ids(env) == before
        assert _picked_on(env, case.excel_95.id) == 95

    def test_an_orphan_with_a_note_is_blocked(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        orphan = _orphan_row(env, case, line=23)
        orphan.allocation_notes = "planner: hold for project X"
        env.db.commit()
        orphan_id = str(orphan.id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert f"ORPHAN-BLOCKED {orphan_id} line 23" in out
        assert "notes yes" in out
        assert orphan_id in _ids(env)

    def test_header_names_the_database_and_snapshots_frame_the_writes(self, env):
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        excel_id = str(case.excel_95.id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        database = env.db.execute(text("SELECT current_database()")).scalar()
        assert f"=== database {database} at" in out
        assert "alembic head" in out
        before = out.split("[before]", 1)[1]
        assert f'"id":"{excel_id}"' in before
        assert "[after]" in out
        assert f'"id":"{excel_id}"' not in out.split("[after]", 1)[1]

    def test_an_unexpected_error_rolls_back_and_stops_the_run(self, env, monkeypatch):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        def boom(*args, **kwargs):
            raise RuntimeError("simulated database error")

        monkeypatch.setattr(standalone, "_move_links", boom)
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        before = _ids(env)
        lines: list[str] = []

        code = standalone.run(
            env.db, env.company_a_code, [OWNER_SPO, "SPO-NEVER-REACHED"], apply=True, out=lines.append
        )

        out = "\n".join(lines)
        assert code == 4, out
        assert "FAILED, rolled back, run STOPPED: RuntimeError" in out
        assert "SPO-NEVER-REACHED" not in out
        assert _ids(env) == before



# ============================================================================ #
# Review B1: the standalone copies the rules, so it is held to the in-app
# repair by a DIFFERENTIAL test on a rich document, plus targeted cases.
# ============================================================================ #
from datetime import date, datetime, timedelta  # noqa: E402

from app.models.procurement import (  # noqa: E402
    InboundShipment,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from tests._pg_fixture import unique_code  # noqa: E402


class _Rich:
    """Every rule in one document. Seeded twice (two spo_numbers) so the in-app
    script repairs one and the standalone the other."""

    def __init__(self, env):
        self.env = env
        self.ib = env.refs.resolve(entity_type="warehouses", source_ref=env.link_warehouse(env.company_a))
        self.ntc = env.refs.resolve(entity_type="warehouses", source_ref=env.link_warehouse(env.company_a))
        self.codes = {
            wh: env.db.execute(text("SELECT warehouse_code FROM warehouses WHERE id = :i"), {"i": wh}).scalar()
            for wh in (self.ib, self.ntc)
        }
        self.p = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        self.r = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
        self.q = env.refs.resolve(entity_type="products", source_ref=env.link_product(env.company_a))
        from tests.test_spo_xlsx_supersede import _seed_po_line

        self._seed_po_line = _seed_po_line

    def _alloc(self, number, line, product, *, wh=None, loc=None, alloc, recv=0, status=None,
               ref=None, doc=None, created=None, **extra):
        env = self.env
        row = SPOAllocation(
            company_id=env.company_a, spo_number=number, spo_line_number=line, product_id=product,
            warehouse_id=wh, location_code=loc if loc is not None else (self.codes.get(wh) if wh else None),
            allocated_quantity=alloc, quantity_received=recv,
            receipt_status="fully_received" if recv >= alloc and alloc else "pending",
            line_status=status or ("closed" if recv >= alloc and alloc else "open"),
            source_system="autocount" if ref else extra.pop("source_system", "scm_upload"),
            source_ref=ref, source_doc_ref=doc, **extra,
        )
        env.db.add(row)
        env.db.flush()
        if created is not None:
            env.db.execute(text("UPDATE spo_allocations SET created_at = :c WHERE id = :i"), {"c": created, "i": row.id})
        return row

    def _pick(self, header, alloc, product, wh, qty, expected=None, accepted=None, dtl=None):
        line = PickingLine(
            id=str(uuid.uuid4()), company_id=self.env.company_a, picking_header_id=header,
            spo_allocation_id=alloc, product_id=product, source_warehouse_id=wh,
            quantity_expected=qty if expected is None else expected, quantity_picked=qty,
            qty_accepted=accepted, dtl_key=dtl,
        )
        self.env.db.add(line)
        self.env.db.flush()
        return line

    def seed(self, number):
        env = self.env
        shipment = InboundShipment(
            id=str(uuid.uuid4()), company_id=env.company_a, shipment_number=unique_code(f"{MARKER}-PL"),
            shipping_container_number=f"ZZTU{uuid.uuid4().int % 10**7:07d}", shipment_date=date(2026, 9, 1),
            shipment_status="pending",
        )
        env.db.add(shipment)
        env.db.flush()
        t0 = datetime(2026, 9, 1, 8, 0, 0)
        # Excel-era rows.
        self._alloc(number, 1, self.p, loc="HQ", alloc=95, recv=95, inbound_shipment_id=shipment.id,
                    quantity_rejected=2, allocation_notes="excel note")
        e2 = self._alloc(number, 2, self.p, loc="HQ", alloc=4, recv=4)
        e3 = self._alloc(number, 3, self.r, wh=self.ntc, alloc=10, recv=6)
        self._alloc(number, 4, self.q, loc="HQ", alloc=99, recv=0)  # orphan, removable
        self._alloc(number, 5, self.p, loc="HQ", alloc=7, po_line_id=self._seed_po_line(env, product_id=self.p))
        # An older DocKey's closed row, then the live DocKey's unreceived lines.
        self._alloc(number, 9, self.p, wh=self.ib, alloc=5, recv=5, ref=f"{MARKER}:OLD-{uuid.uuid4().hex[:6]}",
                    doc=f"{MARKER}:D1-{number}", created=t0)
        doc = f"{MARKER}:D2-{number}"
        l10 = self._alloc(number, 10, self.p, wh=self.ib, alloc=22, ref=f"{MARKER}:A-{uuid.uuid4().hex[:6]}",
                          doc=doc, created=t0 + timedelta(days=1))
        self._alloc(number, 11, self.p, wh=self.ntc, alloc=77, ref=f"{MARKER}:B-{uuid.uuid4().hex[:6]}",
                    doc=doc, created=t0 + timedelta(days=1))
        self._alloc(number, 12, self.r, wh=self.ntc, alloc=10, ref=f"{MARKER}:C-{uuid.uuid4().hex[:6]}",
                    doc=doc, created=t0 + timedelta(days=1))
        e1 = env.db.execute(
            text("SELECT id FROM spo_allocations WHERE spo_number = :n AND spo_line_number = 1"), {"n": number}
        ).scalar()
        approved = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_a, picking_number=unique_code(MARKER),
                                 picking_type="goods_received", picking_status="approved", spo_number=number)
        rejected = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_a, picking_number=unique_code(MARKER),
                                 picking_type="goods_received", picking_status="rejected", spo_number=number)
        env.db.add_all([approved, rejected])
        env.db.flush()
        self._pick(approved.id, e1, self.p, self.ib, 30, expected=32, accepted=25)  # splits 22 + 8
        self._pick(approved.id, e1, self.p, self.ntc, 65)
        self._pick(approved.id, e2.id, self.p, self.ntc, 4, dtl=900000 + uuid.uuid4().int % 99999)  # never split
        self._pick(approved.id, e2.id, self.p, None, 0)  # zero quantity: first line
        self._pick(approved.id, e3.id, self.r, self.ntc, 6)  # keyed group: moves whole
        self._pick(rejected.id, l10.id, self.p, self.ib, 10)  # holds no capacity
        env.db.commit()

    def state(self, number):
        """The document's end state, keyed by line number (ids differ per seed)."""
        rows = self.env.db.execute(
            text(
                "SELECT id, spo_line_number, allocated_quantity, quantity_received, stated_received, line_status, "
                "receipt_status, inbound_shipment_id IS NOT NULL AS has_pl, quantity_rejected, allocation_notes, "
                "retired_at IS NOT NULL AS retired FROM spo_allocations WHERE spo_number = :n ORDER BY spo_line_number"
            ),
            {"n": number},
        ).mappings().all()
        by_id = {str(r["id"]): r["spo_line_number"] for r in rows}
        picks = self.env.db.execute(
            text(
                "SELECT pl.spo_allocation_id, pl.quantity_picked, pl.quantity_expected, pl.qty_accepted, "
                "pl.source_warehouse_id = :ib AS at_ib, pl.dtl_key IS NOT NULL AS ac, ph.picking_status "
                "FROM picking_lines pl JOIN picking_headers ph ON ph.id = pl.picking_header_id "
                "WHERE ph.spo_number = :n"
            ),
            {"n": number, "ib": self.ib},
        ).all()
        return (
            [tuple(v for k, v in r.items() if k != "id") for r in rows],
            sorted(
                ((by_id.get(str(a)), q, e, acc, ib, ac, st) for a, q, e, acc, ib, ac, st in picks),
                key=repr,
            ),
        )


class TestStandaloneParity:
    def test_standalone_reaches_exactly_the_in_app_end_state(self, env):
        from scripts import dedupe_spo_xlsx_superseded as inapp
        from scripts.oneoff import dedupe_spo_standalone as standalone

        rich = _Rich(env)
        a, b = f"{MARKER}-PAR-A-{uuid.uuid4().hex[:6]}", f"{MARKER}-PAR-B-{uuid.uuid4().hex[:6]}"
        rich.seed(a)
        rich.seed(b)

        inapp.run(env.db, env.company_a, dry_run=False, spo_numbers=[a])
        lines: list[str] = []
        code = standalone.run(env.db, env.company_a_code, [b], apply=True, out=lines.append)
        assert code == 0, "\n".join(lines)

        state_a, state_b = rich.state(a), rich.state(b)
        assert state_b == state_a
        rows, picks = state_b
        # Pin the end state itself, so a shared mistake cannot pass as parity.
        assert [r[0] for r in rows] == [5, 9, 10, 11, 12]  # po_line_id row kept, old DocKey kept (retired)
        by_line = {r[0]: r for r in rows}
        assert by_line[9][-1] is True  # older DocKey row retired
        assert by_line[10][2:5] == (22, 22, "closed")  # carried 22, stated floor 22
        assert by_line[11][2:5] == (77, 77, "closed")
        assert by_line[12][2:4] == (6, 6)  # keyed group carried 6
        assert by_line[10][7:9] == (2, "excel note")  # rejected + note onto the first line
        assert (10, 22, 22, 22, True, False, "approved") in picks  # split chunk 1
        assert (11, 8, 10, 3, True, False, "approved") in picks  # split chunk 2 (shortfall + acceptance)
        assert (11, 4, 4, None, False, True, "approved") in picks  # AutoCount GRN pick moved whole
        assert (10, 0, 0, None, None, False, "approved") in picks  # zero pick on the FIRST line
        assert (12, 6, 6, None, False, False, "approved") in picks  # keyed group moved whole


class TestStandaloneTargetedRules:
    def _doc(self, env, rich, rows, lines):
        number = f"{MARKER}-T-{uuid.uuid4().hex[:6]}"
        doc = f"{MARKER}:D-{number}"
        for spec in rows:
            rich._alloc(number, **spec)
        for spec in lines:
            rich._alloc(number, ref=f"{MARKER}:L-{uuid.uuid4().hex[:6]}", doc=doc, **spec)
        env.db.commit()
        return number

    def _apply(self, env, number):
        from scripts.oneoff import dedupe_spo_standalone as standalone

        lines: list[str] = []
        code = standalone.run(env.db, env.company_a_code, [number], apply=True, out=lines.append)
        return code, "\n".join(lines), {
            r[0] for r in env.db.execute(
                text("SELECT spo_line_number FROM spo_allocations WHERE spo_number = :n"), {"n": number}
            )
        }

    def test_received_locked_group_is_kept(self, env):
        """D26a: an Excel row at IB (30 received) against a single IB line of 10."""
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.p, wh=rich.ib, alloc=30, recv=30)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=10)])
        code, out, left = self._apply(env, number)
        assert code == 0, out
        assert "received locked" in out
        assert left == {1, 2}

    def test_unequal_quantities_follow_autocount_when_its_lines_hold_the_receipt(self, env):
        """Follow AutoCount: Excel 99 (nothing received) against an AutoCount line
        of 95 is superseded - AutoCount's quantity is the truth and no receipt is
        at stake. Before the ruling this pool needed equal quantities."""
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.p, loc="HQ", alloc=99, recv=0)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=95)])
        code, out, left = self._apply(env, number)
        assert code == 0, out
        assert left == {2}

    def test_unequal_quantities_that_cannot_hold_the_receipt_are_kept(self, env):
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.p, loc="HQ", alloc=99, recv=99)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=95)])
        code, out, left = self._apply(env, number)
        assert code == 0, out
        assert "received locked (AutoCount lines 95 cannot hold the 99 received)" in out
        assert left == {1, 2}

    def test_a_po_line_row_is_never_touched(self, env):
        rich = _Rich(env)
        po_line = rich._seed_po_line(env, product_id=rich.q)
        number = self._doc(env, rich, [dict(line=1, product=rich.q, loc="HQ", alloc=7, po_line_id=po_line)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=10)])
        code, out, left = self._apply(env, number)
        assert code == 0, out
        assert left == {1, 2}

    def test_an_orphan_held_by_another_companys_pick_is_blocked(self, env):
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.q, loc="HQ", alloc=99, recv=0)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=10)])
        orphan = env.db.execute(
            text("SELECT id FROM spo_allocations WHERE spo_number = :n AND spo_line_number = 1"), {"n": number}
        ).scalar()
        header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_b, picking_number=unique_code(MARKER),
                               picking_type="goods_received", picking_status="approved")
        env.db.add(header)
        env.db.flush()
        env.db.add(PickingLine(id=str(uuid.uuid4()), company_id=env.company_b, picking_header_id=header.id,
                               spo_allocation_id=orphan, product_id=rich.q, quantity_expected=1, quantity_picked=1))
        env.db.commit()
        code, out, left = self._apply(env, number)
        assert code == 0, out
        assert f"ORPHAN-BLOCKED {orphan} line 1" in out
        assert "picks 1" in out
        assert left == {1, 2}

    def test_a_pick_from_another_company_on_a_superseded_row_aborts(self, env):
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.p, loc="HQ", alloc=10, recv=0)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=10)])
        excel = env.db.execute(
            text("SELECT id FROM spo_allocations WHERE spo_number = :n AND spo_line_number = 1"), {"n": number}
        ).scalar()
        header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_b, picking_number=unique_code(MARKER),
                               picking_type="goods_received", picking_status="approved")
        env.db.add(header)
        env.db.flush()
        env.db.add(PickingLine(id=str(uuid.uuid4()), company_id=env.company_b, picking_header_id=header.id,
                               spo_allocation_id=excel, product_id=rich.p, quantity_expected=3, quantity_picked=3))
        env.db.commit()
        code, out, left = self._apply(env, number)
        assert code == 3, out
        assert "another company's links point at rows to delete (picks 1" in out
        assert left == {1, 2}


    def test_an_autocount_grn_line_that_would_span_two_lines_moves_whole(self, env):
        """A 30-pick carrying `dtl_key` at IB over IB 22 + NTC 8 stays one row on
        IB (30); without the rule it would split 22 + 8."""
        rich = _Rich(env)
        number = self._doc(env, rich, [dict(line=1, product=rich.p, loc="HQ", alloc=30, recv=30)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=22),
                            dict(line=3, product=rich.p, wh=rich.ntc, alloc=8)])
        excel = env.db.execute(
            text("SELECT id FROM spo_allocations WHERE spo_number = :n AND spo_line_number = 1"), {"n": number}
        ).scalar()
        header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_a, picking_number=unique_code(MARKER),
                               picking_type="goods_received", picking_status="approved", spo_number=number)
        env.db.add(header)
        env.db.flush()
        rich._pick(header.id, excel, rich.p, rich.ib, 30, dtl=123456)
        env.db.commit()
        code, out, _left = self._apply(env, number)
        assert code == 0, out
        picks = env.db.execute(
            text("SELECT quantity_picked FROM picking_lines WHERE picking_header_id = :h"), {"h": str(header.id)}
        ).scalars().all()
        assert picks == [30]

    def test_a_pick_the_move_left_behind_aborts_before_any_delete(self, env, monkeypatch):
        """The post-move guard: if a pick were still on a superseded row, the SPO
        is refused and nothing is deleted."""
        from scripts.oneoff import dedupe_spo_standalone as standalone

        monkeypatch.setattr(standalone, "_move_picks_by_capacity", lambda *a, **k: 0)
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        before = _ids(env)
        code, out = _run(env, apply=True)
        assert code == 3, out
        assert "still pointing at Excel rows after the move (picks 3" in out
        assert _ids(env) == before


class TestStandaloneFollowAutocountAcrossWarehouses:
    def test_excel_rows_at_another_warehouse_are_superseded(self, env):
        case = _owner_case(env, excel_warehouse=True, number=OWNER_SPO)
        ib, ntc = _seed_pre_repair_state(env, case)
        excel_ids = {str(case.excel_95.id), str(case.excel_4.id)}
        ib_id, ntc_id = str(ib.id), str(ntc.id)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert "product listed by AutoCount at another warehouse" not in out
        assert _ids(env) == {ib_id, ntc_id}
        assert not excel_ids & _ids(env)
        assert _picked_on(env, ib_id) == 22
        assert _picked_on(env, ntc_id) == 77

    def test_autocount_lines_too_small_for_the_receipt_are_kept_as_received_locked(self, env):
        case = _owner_case(env, excel_warehouse=True, number=OWNER_SPO)
        _ib, ntc = _seed_pre_repair_state(env, case)
        ntc.allocated_quantity = 50
        env.db.commit()
        before = _ids(env)

        code, out = _run(env, apply=True)

        assert code == 0, out
        assert "received locked" in out
        assert _ids(env) == before

    def test_parity_with_the_in_app_repair_across_warehouses(self, env):
        from scripts import dedupe_spo_xlsx_superseded as inapp

        rich = _Rich(env)
        a, b = f"{MARKER}-XW-A-{uuid.uuid4().hex[:6]}", f"{MARKER}-XW-B-{uuid.uuid4().hex[:6]}"
        for number in (a, b):
            doc = f"{MARKER}:D-{number}"
            excel = rich._alloc(number, 1, rich.p, wh=rich.ib, alloc=30, recv=30)
            rich._alloc(number, 2, rich.p, wh=rich.ntc, alloc=20, ref=f"{MARKER}:X-{uuid.uuid4().hex[:6]}", doc=doc)
            rich._alloc(number, 3, rich.p, wh=rich.ntc, alloc=10, ref=f"{MARKER}:Y-{uuid.uuid4().hex[:6]}", doc=doc)
            header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_a,
                                   picking_number=unique_code(MARKER), picking_type="goods_received",
                                   picking_status="approved", spo_number=number)
            env.db.add(header)
            env.db.flush()
            rich._pick(header.id, excel.id, rich.p, rich.ib, 30, accepted=30)
        env.db.commit()

        inapp.run(env.db, env.company_a, dry_run=False, spo_numbers=[a])
        lines: list[str] = []
        from scripts.oneoff import dedupe_spo_standalone as standalone

        code = standalone.run(env.db, env.company_a_code, [b], apply=True, out=lines.append)
        assert code == 0, "\n".join(lines)
        assert rich.state(b) == rich.state(a)
        rows, picks = rich.state(b)
        assert [r[0] for r in rows] == [2, 3]
        assert {r[0]: r[2] for r in rows} == {2: 20, 3: 10}


class TestFollowAutocountWholeProductPool:
    """Review of #1424 (SF1/SF2/SF3/SF4): a product the keyed pass cannot settle
    is planned as ONE pool, in both scripts, with the same end state."""

    def _twin(self, env, rich, rows, lines, picks=()):
        numbers = []
        for tag in ("A", "B"):
            number = f"{MARKER}-WP{tag}-{uuid.uuid4().hex[:6]}"
            doc = f"{MARKER}:D-{number}"
            created = {}
            for spec in rows:
                created[spec["line"]] = rich._alloc(number, **spec)
            for spec in lines:
                rich._alloc(number, ref=f"{MARKER}:L-{uuid.uuid4().hex[:6]}", doc=doc, **spec)
            if picks:
                header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company_a,
                                       picking_number=unique_code(MARKER), picking_type="goods_received",
                                       picking_status="approved", spo_number=number)
                env.db.add(header)
                env.db.flush()
                for line, wh, qty in picks:
                    rich._pick(header.id, created[line].id, created[line].product_id, wh, qty)
            numbers.append(number)
        env.db.commit()
        return numbers

    def _run_both(self, env, a, b):
        from scripts import dedupe_spo_xlsx_superseded as inapp
        from scripts.oneoff import dedupe_spo_standalone as standalone

        summary = inapp.run(env.db, env.company_a, dry_run=False, spo_numbers=[a])
        lines: list[str] = []
        code = standalone.run(env.db, env.company_a_code, [b], apply=True, out=lines.append)
        assert code == 0, "\n".join(lines)
        return summary, "\n".join(lines)

    def test_sf1_same_warehouse_line_too_small_pools_the_whole_product(self, env):
        """Excel at NTC 99/99; AutoCount NTC 77 + IB 22. The keyed pass alone
        would lock it; the pool holds 99 and supersedes."""
        rich = _Rich(env)
        a, b = self._twin(env, rich,
                          [dict(line=1, product=rich.p, wh=rich.ntc, alloc=99, recv=99)],
                          [dict(line=2, product=rich.p, wh=rich.ntc, alloc=77),
                           dict(line=3, product=rich.p, wh=rich.ib, alloc=22)],
                          picks=[(1, rich.ntc, 77), (1, rich.ib, 22)])
        summary, out = self._run_both(env, a, b)
        assert summary["rows_removed"] == 1
        assert rich.state(a) == rich.state(b)
        rows, picks = rich.state(b)
        assert [(r[0], r[2], r[3]) for r in rows] == [(2, 77, 77), (3, 22, 22)]
        assert sorted((p[0], p[1]) for p in picks) == [(2, 77), (3, 22)]

    def test_sf2_rows_left_behind_by_a_keyed_group_join_the_pool(self, env):
        """Excel NTC 50 + BRW 49 (nothing received); one AutoCount NTC line of
        99. Both Excel rows go; no open Excel supply is left beside it."""
        rich = _Rich(env)
        brw = env.refs.resolve(entity_type="warehouses", source_ref=env.link_warehouse(env.company_a))
        a, b = self._twin(env, rich,
                          [dict(line=1, product=rich.p, wh=rich.ntc, alloc=50),
                           dict(line=2, product=rich.p, wh=brw, alloc=49)],
                          [dict(line=3, product=rich.p, wh=rich.ntc, alloc=99)])
        summary, _out = self._run_both(env, a, b)
        assert summary["rows_removed"] == 2
        assert rich.state(a) == rich.state(b)
        assert [r[0] for r in rich.state(b)[0]] == [3]

    def test_a_settled_product_keeps_the_keyed_result_beside_a_pooled_one(self, env):
        """Product R fully settled by the keyed pass (whole-pick move to its own
        line); product P pooled across warehouses - in the same document."""
        rich = _Rich(env)
        brw = env.refs.resolve(entity_type="warehouses", source_ref=env.link_warehouse(env.company_a))
        a, b = self._twin(env, rich,
                          [dict(line=1, product=rich.r, wh=rich.ntc, alloc=10, recv=6),
                           dict(line=2, product=rich.p, wh=brw, alloc=30, recv=30)],
                          [dict(line=3, product=rich.r, wh=rich.ntc, alloc=10),
                           dict(line=4, product=rich.p, wh=rich.ntc, alloc=30)],
                          picks=[(1, rich.ntc, 6), (2, brw, 30)])
        summary, _out = self._run_both(env, a, b)
        assert summary["rows_removed"] == 2
        assert rich.state(a) == rich.state(b)
        rows, picks = rich.state(b)
        assert [(r[0], r[3]) for r in rows] == [(3, 6), (4, 30)]

    def test_inapp_reports_received_locked(self, env, capsys):
        """SF3: the in-app script names the lock, not "no AutoCount line"."""
        rich = _Rich(env)
        a, _b = self._twin(env, rich,
                           [dict(line=1, product=rich.p, wh=rich.ntc, alloc=99, recv=99)],
                           [dict(line=2, product=rich.p, wh=rich.ib, alloc=50)])
        from scripts import dedupe_spo_xlsx_superseded as inapp

        summary = inapp.run(env.db, env.company_a, dry_run=False, spo_numbers=[a])
        out = capsys.readouterr().out
        assert summary["rows_removed"] == 0
        assert "(allocated 99, received 99): received locked" in out

    def test_inapp_unequal_quantities_with_nothing_received_follow_autocount(self, env):
        """SF4: Excel 99 / 0 received against AutoCount 95 - superseded."""
        rich = _Rich(env)
        a, b = self._twin(env, rich,
                          [dict(line=1, product=rich.p, loc="HQ", alloc=99, recv=0)],
                          [dict(line=2, product=rich.p, wh=rich.ib, alloc=95)])
        summary, _out = self._run_both(env, a, b)
        assert summary["rows_removed"] == 1
        assert rich.state(a) == rich.state(b)

    def test_inapp_orphan_with_a_warehouse_under_follow_autocount(self, env):
        """SF4: a BRW row for a product AutoCount does not list is an orphan
        (removed: nothing received, nothing linked) in both scripts."""
        rich = _Rich(env)
        brw = env.refs.resolve(entity_type="warehouses", source_ref=env.link_warehouse(env.company_a))
        a, b = self._twin(env, rich,
                          [dict(line=1, product=rich.q, wh=brw, alloc=99)],
                          [dict(line=2, product=rich.p, wh=rich.ib, alloc=10)])
        summary, out = self._run_both(env, a, b)
        assert summary["orphans_removed"] == 1
        assert "orphan delete" in out
        assert rich.state(a) == rich.state(b)
        assert [r[0] for r in rich.state(b)[0]] == [2]
