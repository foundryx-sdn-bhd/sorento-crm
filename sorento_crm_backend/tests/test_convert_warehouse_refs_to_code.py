"""RED tests for scripts/convert_warehouse_refs_to_code.py (WH-REF-CONVERT).

The script does not exist yet. It rewrites every WAREHOUSE integration reference
`<db>:<AutoKey>` to `<db>:<warehouse_code>` (the prefix of EACH ref kept), with a CSV
backup written before any change, idempotent, collision-safe, revertible, and it refuses
to apply while any ref is flagged (collision, missing warehouse, company mismatch,
unparseable, blank code) unless `--force`.

Modelled on the customer precedent (tests/test_convert_customer_refs_to_code.py,
DEBTOR-CODE-LINK R9), plus the flags this lane adds.

Substrate: `blank_session()`; every test seeds its own warehouses and refs. The script
module is imported inside each test (importlib) so a missing script fails each test with
ModuleNotFoundError rather than breaking collection.
"""
from __future__ import annotations

import csv
import importlib
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.inventory import Warehouse
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZWHC"
MODULE = "scripts.convert_warehouse_refs_to_code"
HEADER = ["ref_id", "company_id", "entity_id", "old_source_ref", "new_source_ref"]

OLD = datetime(2025, 1, 1, 9, 0, 0)
NEW = datetime(2026, 9, 1, 9, 0, 0)


def _script():
    return importlib.import_module(MODULE)


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@pytest.fixture()
def company_b(db) -> str:
    company = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
    db.add(company)
    db.flush()
    return str(company.id)


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _warehouse(
    db, code: str, name: str = "WH-A", created_at=OLD, company_id=DEFAULT_COMPANY_ID, id=None
) -> str:
    row = Warehouse(
        id=id or str(uuid.uuid4()),
        warehouse_code=code,
        warehouse_name=name,
        created_at=created_at,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _ref(
    db,
    entity_id: str,
    source_ref: str,
    *,
    entity_type: str = "warehouses",
    company_id: str | None = DEFAULT_COMPANY_ID,
    source_system: str = "autocount",
) -> str:
    row = IntegrationReference(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        company_id=company_id,
        source_system=source_system,
        source_ref=source_ref,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _stored(db, ref_id: str) -> str:
    return db.execute(
        text("SELECT source_ref FROM integration_references WHERE id = :i"), {"i": ref_id}
    ).scalar()


def _change_for(plan, ref_id: str):
    return next((c for c in plan.changes if str(c.ref_id) == ref_id), None)


def _skip_for(plan, ref_id: str):
    return next((s for s in plan.skipped if str(s.ref_id) == ref_id), None)


class _KeepOpenSession:
    """Hands the test session to the script's `SessionLocal()` call. close/rollback
    are no-ops and commit only flushes, so the outer test transaction survives."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def commit(self):
        self._s.flush()

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def patched_session(db, monkeypatch):
    mod = _script()
    monkeypatch.setattr(mod, "SessionLocal", lambda: _KeepOpenSession(db))
    return mod


class TestPlan:
    def test_w1_autokey_ref_becomes_prefix_colon_warehouse_code(self, db):
        mod = _script()
        code = _code("W1")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:7")

        plan = mod.plan_conversion(db)

        change = _change_for(plan, rid)
        assert change is not None, (plan.changes, plan.skipped)
        assert change.old_source_ref == "AED_SORENTO:7"
        assert change.new_source_ref == f"AED_SORENTO:{code}"
        assert change.warehouse_code == code
        assert str(change.entity_id) == wid
        assert str(change.company_id) == DEFAULT_COMPANY_ID
        assert plan.blocked is False

    def test_w2_ref_already_in_code_form_is_counted_not_changed(self, db):
        mod = _script()
        code = _code("W2")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, f"AED_SORENTO:{code}")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        assert _skip_for(plan, rid) is None
        assert plan.already_code == 1

    def test_w3_each_refs_own_prefix_is_kept_and_both_companies_convert(self, db, company_b):
        mod = _script()
        code = _code("W3")
        a = _warehouse(db, code, company_id=DEFAULT_COMPANY_ID)
        b = _warehouse(db, code, company_id=company_b)
        r_a = _ref(db, a, "AED_SORENTO:11", company_id=DEFAULT_COMPANY_ID)
        r_b = _ref(db, b, "AED_V2_MOCHA:17", company_id=company_b)

        plan = mod.plan_conversion(db)

        change_a, change_b = _change_for(plan, r_a), _change_for(plan, r_b)
        assert change_a is not None and change_a.new_source_ref == f"AED_SORENTO:{code}"
        assert change_b is not None and change_b.new_source_ref == f"AED_V2_MOCHA:{code}"
        assert str(change_b.company_id) == company_b
        assert _skip_for(plan, r_a) is None and _skip_for(plan, r_b) is None

    def test_w4_stale_code_form_ref_on_a_sibling_flags_the_candidate_as_a_collision(self, db):
        """Warehouse A (code CA) holds `P:1`; warehouse B (renamed, code CB) still holds the
        stale code-form `P:<CA>`. A's new ref is taken, so A is a collision, B converts."""
        mod = _script()
        ca, cb = _code("W4A"), _code("W4B")
        a = _warehouse(db, ca, "WH-A", OLD)
        b = _warehouse(db, cb, "WH-B", NEW)
        r_a = _ref(db, a, "AED_SORENTO:1")
        r_b = _ref(db, b, f"AED_SORENTO:{ca}")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, r_a) is None
        skip = _skip_for(plan, r_a)
        assert skip is not None and skip.reason == "collision"
        assert skip.old_source_ref == "AED_SORENTO:1"
        change_b = _change_for(plan, r_b)
        assert change_b is not None and change_b.new_source_ref == f"AED_SORENTO:{cb}"
        assert plan.blocked is True

    def test_w5_ref_whose_warehouse_no_longer_exists_is_flagged_missing(self, db):
        mod = _script()
        gone = str(uuid.uuid4())
        rid = _ref(db, gone, "AED_SORENTO:404")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        skip = _skip_for(plan, rid)
        assert skip is not None and skip.reason == "missing_warehouse"
        assert skip.old_source_ref == "AED_SORENTO:404"
        assert plan.blocked is True

    def test_w6_ref_whose_warehouse_sits_in_another_company_is_flagged(self, db, company_b):
        mod = _script()
        wid = _warehouse(db, _code("W6"), company_id=company_b)
        rid = _ref(db, wid, "AED_SORENTO:66", company_id=DEFAULT_COMPANY_ID)

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        skip = _skip_for(plan, rid)
        assert skip is not None and skip.reason == "company_mismatch"
        assert plan.blocked is True

    def test_w7_other_entity_types_are_never_planned(self, db):
        mod = _script()
        r_cust = _ref(db, str(uuid.uuid4()), "AED_SORENTO:1001", entity_type="customers")
        r_prod = _ref(db, str(uuid.uuid4()), "AED_SORENTO:9001", entity_type="products")
        r_sup = _ref(db, str(uuid.uuid4()), "AED_SORENTO:31", entity_type="suppliers")

        plan = mod.plan_conversion(db)

        for rid in (r_cust, r_prod, r_sup):
            assert _change_for(plan, rid) is None and _skip_for(plan, rid) is None
        assert plan.already_code == 0
        assert plan.blocked is False

    def test_w8_other_source_system_is_never_planned(self, db):
        mod = _script()
        wid = _warehouse(db, _code("W8"))
        rid = _ref(db, wid, "OTHER:8", source_system="other_erp")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None and _skip_for(plan, rid) is None

    def test_w9_code_with_surrounding_spaces_is_trimmed_in_the_new_ref(self, db):
        mod = _script()
        code = _code("W9")
        wid = _warehouse(db, f"  {code}  ")
        rid = _ref(db, wid, "AED_SORENTO:9")

        plan = mod.plan_conversion(db)

        change = _change_for(plan, rid)
        assert change is not None, (plan.changes, plan.skipped)
        assert change.new_source_ref == f"AED_SORENTO:{code}"

    def test_w10_ref_without_a_colon_is_flagged_unparseable(self, db):
        mod = _script()
        wid = _warehouse(db, _code("W10"))
        rid = _ref(db, wid, "NOCOLONKEY")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        skip = _skip_for(plan, rid)
        assert skip is not None and skip.reason == "unparseable"
        assert plan.blocked is True

    def test_w11_blank_code_is_flagged_not_turned_into_a_bare_prefix(self, db):
        mod = _script()
        wid = _warehouse(db, "   ")
        rid = _ref(db, wid, "AED_SORENTO:170")

        plan = mod.plan_conversion(db)

        assert _change_for(plan, rid) is None
        assert _skip_for(plan, rid).reason == "blank_code"
        assert plan.blocked is True


class TestApplyRevert:
    def test_w12_apply_rewrites_and_a_second_plan_has_no_changes(self, db, tmp_path):
        mod = _script()
        code = _code("W12")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:12")

        plan = mod.plan_conversion(db)
        count = mod.apply_conversion(db, plan, str(tmp_path / "out.csv"))

        assert count == 1
        assert _stored(db, rid) == f"AED_SORENTO:{code}"
        again = mod.plan_conversion(db)
        assert again.changes == []
        assert again.already_code == 1

    def test_w13_planning_alone_changes_nothing(self, db):
        mod = _script()
        wid = _warehouse(db, _code("W13"))
        rid = _ref(db, wid, "AED_SORENTO:13")

        mod.plan_conversion(db)

        assert _stored(db, rid) == "AED_SORENTO:13"

    def test_w14_apply_refuses_while_a_ref_is_flagged_unless_forced(self, db, tmp_path):
        mod = _script()
        code = _code("W14")
        wid = _warehouse(db, code)
        r_ok = _ref(db, wid, "AED_SORENTO:14")
        r_gone = _ref(db, str(uuid.uuid4()), "AED_SORENTO:1404")
        path = tmp_path / "out.csv"

        plan = mod.plan_conversion(db)
        assert plan.blocked is True
        with pytest.raises(mod.ConversionBlocked):
            mod.apply_conversion(db, plan, str(path))

        assert not path.exists(), "a refused apply writes no backup"
        assert _stored(db, r_ok) == "AED_SORENTO:14"
        assert _stored(db, r_gone) == "AED_SORENTO:1404"

        count = mod.apply_conversion(db, plan, str(path), force=True)

        assert count == 1
        assert _stored(db, r_ok) == f"AED_SORENTO:{code}"
        assert _stored(db, r_gone) == "AED_SORENTO:1404", "a flagged ref is never rewritten"

    def test_w15_collision_apply_with_force_skips_the_colliding_ref_only(self, db, tmp_path):
        mod = _script()
        ca, cb = _code("W15A"), _code("W15B")
        a = _warehouse(db, ca, "WH-A", OLD)
        b = _warehouse(db, cb, "WH-B", NEW)
        r_a = _ref(db, a, "AED_SORENTO:1")
        r_b = _ref(db, b, f"AED_SORENTO:{ca}")

        plan = mod.plan_conversion(db)
        count = mod.apply_conversion(db, plan, str(tmp_path / "out.csv"), force=True)

        assert count == 1
        assert _stored(db, r_a) == "AED_SORENTO:1"
        assert _stored(db, r_b) == f"AED_SORENTO:{cb}"

    def test_w16_revert_restores_old_refs_and_is_idempotent(self, db, tmp_path):
        mod = _script()
        code = _code("W16")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:16")
        path = str(tmp_path / "out.csv")
        mod.apply_conversion(db, mod.plan_conversion(db), path)
        assert _stored(db, rid) == f"AED_SORENTO:{code}"

        assert mod.revert(db, path) == 1
        assert _stored(db, rid) == "AED_SORENTO:16"
        assert mod.revert(db, path) == 0
        assert _stored(db, rid) == "AED_SORENTO:16"

    def test_w17_csv_backup_has_the_header_and_one_row_per_change(self, db, tmp_path):
        mod = _script()
        code = _code("W17")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:17")
        path = tmp_path / "out.csv"

        mod.apply_conversion(db, mod.plan_conversion(db), str(path))

        with open(path, newline="") as fh:
            reader = csv.reader(fh)
            assert next(reader) == HEADER
            rows = list(reader)
        assert rows == [[rid, DEFAULT_COMPANY_ID, wid, "AED_SORENTO:17", f"AED_SORENTO:{code}"]]

    def test_w18_apply_never_replaces_an_existing_backup(self, db, tmp_path):
        mod = _script()
        wid = _warehouse(db, _code("W18"))
        rid = _ref(db, wid, "AED_SORENTO:18")
        path = tmp_path / "out.csv"
        path.write_text("earlier run\n")

        with pytest.raises(FileExistsError):
            mod.apply_conversion(db, mod.plan_conversion(db), str(path))

        assert path.read_text() == "earlier run\n"
        assert _stored(db, rid) == "AED_SORENTO:18"

    def test_w19_apply_touches_no_other_entity_type(self, db, tmp_path):
        mod = _script()
        wid = _warehouse(db, _code("W19"))
        _ref(db, wid, "AED_SORENTO:19")
        r_cust = _ref(db, str(uuid.uuid4()), "AED_SORENTO:1901", entity_type="customers")
        r_prod = _ref(db, str(uuid.uuid4()), "AED_SORENTO:1902", entity_type="products")

        mod.apply_conversion(db, mod.plan_conversion(db), str(tmp_path / "out.csv"))

        assert _stored(db, r_cust) == "AED_SORENTO:1901"
        assert _stored(db, r_prod) == "AED_SORENTO:1902"


class TestCli:
    def test_w20_cli_without_apply_is_a_dry_run_that_lists_each_ref_per_company(
        self, db, patched_session, capsys
    ):
        mod = patched_session
        code = _code("W20")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:20")
        r_gone = _ref(db, str(uuid.uuid4()), "AED_SORENTO:2004")

        rc = mod.main([])

        assert rc == 0
        assert _stored(db, rid) == "AED_SORENTO:20"
        out = capsys.readouterr().out
        assert "AED_SORENTO:20" in out and f"AED_SORENTO:{code}" in out
        assert code in out, "the dry run names the warehouse code each ref maps to"
        assert DEFAULT_COMPANY_ID in out, "the dry run groups refs per company"
        assert "missing_warehouse" in out and r_gone in out
        assert "nothing changed" in out

    def test_w21_cli_apply_refuses_when_flagged_and_writes_nothing(
        self, db, patched_session, capsys, tmp_path
    ):
        mod = patched_session
        wid = _warehouse(db, _code("W21"))
        rid = _ref(db, wid, "AED_SORENTO:21")
        _ref(db, str(uuid.uuid4()), "AED_SORENTO:2104")
        path = tmp_path / "cli.csv"

        rc = mod.main(["--apply", "--csv", str(path)])

        assert rc != 0
        assert not path.exists()
        assert _stored(db, rid) == "AED_SORENTO:21"
        assert "--force" in capsys.readouterr().out

    def test_w22_cli_apply_then_revert_round_trip(self, db, patched_session, tmp_path):
        mod = patched_session
        code = _code("W22")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:22")
        path = str(tmp_path / "cli.csv")

        assert mod.main(["--apply", "--csv", path]) == 0
        assert _stored(db, rid) == f"AED_SORENTO:{code}"

        assert mod.main(["--revert", path]) == 0
        assert _stored(db, rid) == "AED_SORENTO:22"

    def test_w23_cli_force_converts_the_clean_refs_and_keeps_the_flagged_ones(
        self, db, patched_session, tmp_path
    ):
        mod = patched_session
        code = _code("W23")
        wid = _warehouse(db, code)
        rid = _ref(db, wid, "AED_SORENTO:23")
        r_gone = _ref(db, str(uuid.uuid4()), "AED_SORENTO:2304")
        path = str(tmp_path / "cli.csv")

        assert mod.main(["--apply", "--force", "--csv", path]) == 0

        assert _stored(db, rid) == f"AED_SORENTO:{code}"
        assert _stored(db, r_gone) == "AED_SORENTO:2304"
