"""`scripts/customer_groups_auto.py` (lane CUST-GROUP-AUTO).

Owner, 5 Oct 2026: "I need script to set automatically, not sheet". Restores the name rule the
dev seed used (ec2dd5b79 `plan_groups`): per company, a family of 2+ ledgers sharing
`ledger_family_key(name)` with at least one numbered account, CASH / SHOPEE / LAZADA left out.
It only fills ledgers with no group, reuses a same-named group, and never splits a debtor code
across two groups (PR #1475 treats that as a merge conflict). Fixtures only, no real data.
"""
from __future__ import annotations

import csv
import uuid

import pytest
from sqlalchemy import text

import scripts.customer_groups_auto as auto
from app.models.base import set_company_scope
from app.models.order import Customer, CustomerGroup
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session

CO = "11111111-1111-1111-1111-111111111111"
CO2 = "22222222-2222-2222-2222-222222222222"


def _row(name, level=None, code=None, company=CO, group=None):
    return (str(uuid.uuid4()), company, code or f"ZZT-{uuid.uuid4().hex[:6]}", name, level, group)


def _names(plan):
    return sorted((g.company_id, g.name) for g in plan.groups)


# ------------------------------------------------------------------------------- pure plan


def test_numbered_family_of_two_or_more_becomes_one_group():
    rows = [
        _row("HANLIM TRADING SDN BHD [A/C I]", 1),
        _row("HANLIM TRADING SDN BHD [A/C II]", 2),
        _row("HANLIM TRADING SDN BHD"),
    ]
    plan = auto.plan(rows, {})
    assert _names(plan) == [(CO, "HANLIM TRADING SDN BHD")]
    assert sorted(plan.groups[0].member_ids) == sorted(r[0] for r in rows)


def test_family_without_a_numbered_account_or_alone_is_not_grouped():
    rows = [
        _row("JUBIN BMS SDN BHD (NS)"),
        _row("JUBIN BMS SDN BHD (JB)"),
        _row("SOLO HARDWARE SDN BHD [A/C I]", 1),
    ]
    assert auto.plan(rows, {}).groups == []


def test_cash_shopee_lazada_families_are_never_grouped():
    rows = [
        _row("CASH (ZZT NORTH)", 1),
        _row("CASH (ZZT SOUTH)", 2),
        _row("SHOPEE [A/C I]", 1),
        _row("SHOPEE [A/C II]", 2),
        _row("LAZADA (A/C I)", 1),
        _row("LAZADA (KL)"),
    ]
    assert auto.plan(rows, {}).groups == []


def test_group_name_keeps_brackets_every_member_shares_and_drops_markers():
    rows = [
        _row("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I]", 1),
        _row("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C II]", 2),
    ]
    assert _names(auto.plan(rows, {})) == [(CO, "CHENG HUAT HARDWARE (SENTUL) SDN BHD")]


def test_name_is_taken_from_lowest_numbered_member():
    rows = [
        _row("SCR MARKETING (M) SDN BHD (CERAMIC & ELLECI)"),
        _row("SCR MARKETING (M) SDN BHD [A/C I]", 1),
    ]
    assert _names(auto.plan(rows, {})) == [(CO, "SCR MARKETING (M) SDN BHD")]


def test_lead_is_the_lowest_numbered_member_and_its_spelling_names_the_group():
    rows = [
        _row("Hanlim Trading Sdn Bhd [A/C II]", 2),
        _row("HANLIM TRADING SDN BHD [A/C I]", 1),
    ]
    assert _names(auto.plan(rows, {})) == [(CO, "HANLIM TRADING SDN BHD")]


def test_account_marker_shared_by_every_member_is_not_part_of_the_name():
    rows = [
        _row("ALPHA TILES SDN BHD (KL) [A/C I]", 1),
        _row("ALPHA TILES SDN BHD (JB) [A/C I]", 1),
    ]
    assert _names(auto.plan(rows, {})) == [(CO, "ALPHA TILES SDN BHD")]


def test_families_are_per_company():
    rows = [
        _row("HANLIM TRADING SDN BHD [A/C I]", 1, company=CO),
        _row("HANLIM TRADING SDN BHD [A/C II]", 2, company=CO2),
    ]
    assert auto.plan(rows, {}).groups == []


def test_family_with_a_member_already_grouped_is_skipped_whole():
    gid = str(uuid.uuid4())
    rows = [
        _row("HANLIM TRADING SDN BHD [A/C I]", 1, group=gid),
        _row("HANLIM TRADING SDN BHD [A/C II]", 2),
    ]
    plan = auto.plan(rows, {})
    assert plan.groups == []
    assert [s.reason for s in plan.skipped] == ["already_grouped"]


def test_existing_group_with_the_same_name_is_reused():
    gid = str(uuid.uuid4())
    rows = [_row("HANLIM TRADING SDN BHD [A/C I]", 1), _row("HANLIM TRADING SDN BHD [A/C II]", 2)]
    plan = auto.plan(rows, {(CO, "hanlim trading sdn bhd"): gid})
    assert plan.groups[0].existing_group_id == gid


def test_debtor_code_split_across_two_families_is_a_conflict_and_both_are_skipped():
    """PR #1475 merges rows of one (company, lower(btrim(code))); two different groups on them
    is a conflict it skips. So the plan must never put one code in two groups."""
    rows = [
        _row("ALPHA TILES SDN BHD [A/C I]", 1, code="300-A001"),
        _row("ALPHA TILES SDN BHD [A/C II]", 2),
        _row("BETA STONE SDN BHD [A/C I]", 1, code=" 300-a001 "),
        _row("BETA STONE SDN BHD [A/C II]", 2),
        _row("GAMMA BATH SDN BHD [A/C I]", 1),
        _row("GAMMA BATH SDN BHD [A/C II]", 2),
    ]
    plan = auto.plan(rows, {})
    assert _names(plan) == [(CO, "GAMMA BATH SDN BHD")]
    assert {s.reason for s in plan.skipped} == {"debtor_code_conflict"}
    assert len(plan.skipped) == 2
    assert [c.code for c in plan.conflicts] == ["300-a001"]


def test_debtor_code_shared_with_a_ledger_in_another_existing_group_is_a_conflict():
    other = str(uuid.uuid4())
    rows = [
        _row("ALPHA TILES SDN BHD [A/C I]", 1, code="300-A001"),
        _row("ALPHA TILES SDN BHD [A/C II]", 2),
        _row("SOMETHING ELSE", None, code="300-A001", group=other),
    ]
    plan = auto.plan(rows, {})
    assert plan.groups == []
    assert [s.reason for s in plan.skipped] == ["debtor_code_conflict"]


def test_code_shared_with_a_ledger_already_in_the_reused_group_is_not_a_conflict():
    gid = str(uuid.uuid4())
    rows = [
        _row("ALPHA TILES SDN BHD [A/C I]", 1, code="300-A001"),
        _row("ALPHA TILES SDN BHD [A/C II]", 2),
        _row("SOMETHING ELSE", None, code="300-A001", group=gid),
    ]
    plan = auto.plan(rows, {(CO, "alpha tiles sdn bhd"): gid})
    assert _names(plan) == [(CO, "ALPHA TILES SDN BHD")]
    assert plan.conflicts == []


def test_cash_debtor_code_never_conflicts():
    """`301-*` cash codes: #1475 never treats the customer group as a conflict there."""
    rows = [
        _row("ALPHA TILES SDN BHD [A/C I]", 1, code="301-S007"),
        _row("ALPHA TILES SDN BHD [A/C II]", 2),
        _row("BETA STONE SDN BHD [A/C I]", 1, code="301-S007"),
        _row("BETA STONE SDN BHD [A/C II]", 2),
    ]
    plan = auto.plan(rows, {})
    assert _names(plan) == [(CO, "ALPHA TILES SDN BHD"), (CO, "BETA STONE SDN BHD")]
    assert plan.conflicts == []


def test_same_code_in_the_same_family_is_not_a_conflict():
    rows = [
        _row("ALPHA TILES SDN BHD [A/C I]", 1, code="300-A001"),
        _row("ALPHA TILES SDN BHD [A/C II]", 2, code="300-A001"),
    ]
    plan = auto.plan(rows, {})
    assert _names(plan) == [(CO, "ALPHA TILES SDN BHD")]
    assert plan.conflicts == []


# ------------------------------------------------------------------------------- database


def _cust(db, name, level=None, code=None, group=None) -> str:
    row = Customer(
        id=str(uuid.uuid4()),
        customer_code=code or f"ZZT-{uuid.uuid4().hex[:8]}",
        customer_name=name,
        company_id=DEFAULT_COMPANY_ID,
        account_level=level,
        customer_group_id=group,
    )
    db.add(row)
    db.flush()
    return row.id


def _actor(db) -> str:
    """The operator id the audit rows carry (the CLI resolves it from --actor-email)."""
    return str(uuid.uuid4())


def _group_of(db, cid):
    return db.execute(text("SELECT customer_group_id::text FROM customers WHERE id = :i"), {"i": cid}).scalar()


def test_apply_creates_assigns_audits_and_rerun_changes_nothing(tmp_path):
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        actor = _actor(db)
        han = [_cust(db, "HANLIM TRADING SDN BHD [A/C I]", 1), _cust(db, "HANLIM TRADING SDN BHD [A/C II]", 2)]
        existing = CustomerGroup(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, name="Chin Chun Hardware Sdn Bhd")
        db.add(existing)
        db.flush()
        chin = [
            _cust(db, "CHIN CHUN HARDWARE SDN BHD - [A/C I]", 1),
            _cust(db, "CHIN CHUN HARDWARE SDN BHD - [A/C II]", 2),
        ]
        kept_group = CustomerGroup(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, name="OFFICE PICK")
        db.add(kept_group)
        db.flush()
        office = [
            _cust(db, "DELTA DOOR SDN BHD [A/C I]", 1, group=kept_group.id),
            _cust(db, "DELTA DOOR SDN BHD [A/C II]", 2),
        ]
        conn = db.connection()

        plan = auto.load_plan(conn)
        created, assigned = auto.apply(conn, plan, actor_user_id=actor, run_id="cga-test")
        assert (created, assigned) == (1, 4)

        hanlim_gid = _group_of(db, han[0])
        assert hanlim_gid is not None and _group_of(db, han[1]) == hanlim_gid
        assert conn.execute(text("SELECT name FROM customer_groups WHERE id = :i"), {"i": hanlim_gid}).scalar() == (
            "HANLIM TRADING SDN BHD"
        )
        assert [_group_of(db, c) for c in chin] == [existing.id, existing.id]
        # An office-made group is never touched, nor is its family's ungrouped ledger moved in.
        assert [_group_of(db, c) for c in office] == [kept_group.id, None]

        audit = conn.execute(
            text(
                "SELECT entity_type, count(*) FROM audit_logs WHERE correlation_id = 'cga-test' "
                "AND user_id = :u GROUP BY 1 ORDER BY 1"
            ),
            {"u": actor},
        ).all()
        assert audit == [("customer", 4), ("customer_group", 1)]

        again = auto.load_plan(conn)
        assert again.groups == []
        assert auto.apply(conn, again, actor_user_id=actor, run_id="cga-test-2") == (0, 0)

        # A ledger the office grouped after the plan was read is never moved by apply.
        late_group = CustomerGroup(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, name="LATE PICK")
        db.add(late_group)
        db.flush()
        late = [_cust(db, "EPSILON GLASS SDN BHD [A/C I]", 1), _cust(db, "EPSILON GLASS SDN BHD [A/C II]", 2)]
        stale = auto.load_plan(conn)
        conn.execute(text("UPDATE customers SET customer_group_id = :g WHERE id = :i"), {"g": late_group.id, "i": late[0]})
        assert auto.apply(conn, stale, actor_user_id=actor, run_id="cga-test-3") == (1, 1)
        assert _group_of(db, late[0]) == late_group.id
        old = conn.execute(
            text("SELECT old_values FROM audit_logs WHERE correlation_id = 'cga-test-3' AND entity_type = 'customer'")
        ).scalar()
        assert old == {"customer_group_id": None}

        out = auto.write_plan_csv(plan, tmp_path)
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert {"company", "group_name", "ledger_codes", "ledger_names", "reason"} <= set(rows[0])
        assert any(r["group_name"] == "HANLIM TRADING SDN BHD" for r in rows)


# ------------------------------------------------------------------------------- CLI


def test_cli_requires_db_url():
    with pytest.raises(SystemExit):
        auto.main([])


def test_cli_apply_requires_actor_email(tmp_path):
    with pytest.raises(SystemExit):
        auto.main(["--db-url", "postgresql://nowhere/x", "--apply", "--out", str(tmp_path)])
