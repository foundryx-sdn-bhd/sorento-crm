"""SPO-DEDUPE-ALL: `scripts/oneoff/refresh_container_received.py` and the nightly
open-container refresh. The real symptom: SRTWCX8605-S-RL-PJ on GCXU6137164 read
4 incoming because the stored shipment line said 95 received while the packing
list (which recomputes on open) showed 99/99. Fixture: that shape after the
dedupe, with the stored line made stale again (made-up numbers).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from tests.test_spo_xlsx_product_fallback import (
    MARKER,
    _owner_case,
    _seed_pre_repair_state,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


def _script():
    from scripts.oneoff import refresh_container_received as script

    return script


def _deduped_and_stale(env):
    """The owner's container after the dedupe: AutoCount 22 + 77 on it, 99/99
    received, but the stored line left at 95 / partially_received."""
    from scripts.oneoff import dedupe_spo_standalone as standalone

    case = _owner_case(env, number=f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}")
    _seed_pre_repair_state(env, case)
    code = standalone.run(env.db, env.company_a_code, [case.number], apply=True, out=lambda _l: None)
    assert code == 0
    env.db.execute(
        text("UPDATE inbound_shipment_lines SET quantity_received = 95, line_status = 'partially_received' "
             "WHERE id = :id"),
        {"id": case.shipment_line_id},
    )
    env.db.commit()
    return case


def _stored(env, case) -> tuple[int, str]:
    row = env.db.execute(
        text("SELECT quantity_received, line_status FROM inbound_shipment_lines WHERE id = :id"),
        {"id": case.shipment_line_id},
    ).one()
    return int(row[0] or 0), row[1]


def _run(env, *, company=None, containers=None, all_open=False, apply=False):
    lines: list[str] = []
    code = _script().run(
        env.db, company or env.company_a_code, containers=containers, all_open=all_open,
        apply=apply, out=lines.append,
    )
    return code, "\n".join(lines)


class TestOneContainer:
    def test_dry_run_prints_stored_vs_computed_and_writes_nothing(self, env):
        case = _deduped_and_stale(env)

        code, out = _run(env, containers=[case.container])

        assert code == 0, out
        assert "DRY-RUN (no writes)" in out
        assert f"container {case.container} ({case.shipment_id})" in out
        assert f"line {case.shipment_line_id}" in out
        assert "received 95 -> 99, status partially_received -> received" in out
        assert "lines to change 1" in out
        assert _stored(env, case) == (95, "partially_received")

    def test_apply_stores_the_computed_figures_and_a_rerun_finds_nothing(self, env):
        case = _deduped_and_stale(env)

        code, out = _run(env, containers=[case.container.lower()], apply=True)

        assert code == 0, out
        assert "APPLY" in out
        assert _stored(env, case) == (99, "received")

        code, again = _run(env, containers=[case.container])
        assert code == 0
        assert "lines to change 0" in again

    def test_unknown_container_exits_1(self, env):
        code, out = _run(env, containers=["ZZTU0000000"])

        assert code == 1, out
        assert "no shipment for container 'ZZTU0000000'" in out

    def test_another_companys_container_is_not_found(self, env):
        case = _deduped_and_stale(env)

        code, out = _run(env, company=env.company_b_code, containers=[case.container])

        assert code == 1, out
        assert _stored(env, case) == (95, "partially_received")

    def test_unknown_company_exits_1(self, env):
        code, out = _run(env, company="ZZNOPE", containers=["X"])

        assert code == 1
        assert "no company with code 'ZZNOPE'" in out


class TestAllOpen:
    def test_all_open_covers_the_stale_container_and_skips_a_fully_received_one(self, env):
        stale = _deduped_and_stale(env)
        done = _deduped_and_stale(env)
        env.db.execute(
            text("UPDATE inbound_shipment_lines SET quantity_received = 99, line_status = 'received' "
                 "WHERE id = :id"),
            {"id": done.shipment_line_id},
        )
        env.db.commit()

        code, out = _run(env, all_open=True, apply=True)

        assert code == 0, out
        assert stale.shipment_id in out
        assert done.shipment_id not in out
        assert _stored(env, stale) == (99, "received")


class TestCli:
    def test_container_or_all_open_exactly_one(self):
        parser = _script().build_parser()
        args = parser.parse_args(["--company", "SRT", "--container", "GCXU6137164", "--container", "X"])
        assert args.container == ["GCXU6137164", "X"] and args.apply is False
        assert parser.parse_args(["--company", "SRT", "--all-open", "--apply"]).all_open is True
        with pytest.raises(SystemExit):
            parser.parse_args(["--company", "SRT"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--company", "SRT", "--all-open", "--container", "X"])


class TestNightly:
    def test_the_nightly_sweep_heals_a_stale_open_container(self, env):
        from app.services.rules.shipping_order_rules import nightly_refresh_open_containers

        case = _deduped_and_stale(env)
        set_company_scope(env.db, None)  # what `scheduler_session()` sets

        refreshed = nightly_refresh_open_containers(env.db)

        assert refreshed >= 1
        assert _stored(env, case) == (99, "received")

    def test_open_shipment_ids_skips_fully_received_shipments(self, env):
        from app.services.procurement_service import InboundShipmentService

        stale = _deduped_and_stale(env)
        done = _deduped_and_stale(env)
        env.db.execute(
            text("UPDATE inbound_shipment_lines SET line_status = 'received' WHERE id = :id"),
            {"id": done.shipment_line_id},
        )
        env.db.commit()
        set_company_scope(env.db, None)

        ids = InboundShipmentService(env.db).open_shipment_ids(company_id=env.company_a)

        assert stale.shipment_id in ids
        assert done.shipment_id not in ids
        assert InboundShipmentService(env.db).open_shipment_ids(company_id=env.company_b) == []


def test_the_nightly_tick_calls_the_open_container_refresh():
    """WIRING: the existing daily relink tick also runs the refresh."""
    from tests.test_spo_container_relink_sweep import SCHEDULER_SRC

    source = SCHEDULER_SRC.read_text()
    tick = source.split("def _spo_container_relink_sweep_tick")[1].split("\ndef ")[0]
    assert "nightly_refresh_open_containers(db)" in tick
