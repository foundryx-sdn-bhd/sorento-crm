"""`scripts/preview_oi_email.py` (crew audit 12 Oct): the owner sees the exact email a
planning change would send on the test copy, without SMTP. Runs the real apply inside the
Simulate confirm dry run, so nothing is saved, sent or queued."""
from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

from app.models.planning_change import PlanningChangeBatch
from app.models.project_so import OrderInquiryRow

from ..test_planning_change_apply_on_board import NOW, WAS_1, api, world
from .test_oi_email_so_line_changes import _srtwb246_shape

__all__ = ["api", "world"]  # re-exported fixtures

WAS_FMT = WAS_1.strftime("%d/%m/%Y")
NOW_FMT = NOW.strftime("%d/%m/%Y")


def _script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "preview_oi_email.py"
    spec = importlib.util.spec_from_file_location("zzt_preview_oi_email", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_renders_the_srtwb246_date_move_and_saves_nothing(api):
    _client, world = api
    fixture = _srtwb246_shape(api)
    row = fixture["row"]
    db = world.db
    from ..test_email_layout_migrations import _run
    from ..test_oi_email_print import _load

    # The template row the automation renders (an empty test DB has none).
    _run(_load("oihr_0006_no_remark_column"), db)
    batches_before = db.query(PlanningChangeBatch).count()
    rows_before = db.query(OrderInquiryRow).filter(
        OrderInquiryRow.so_line_id == fixture["line"].id
    ).count()

    result = _script().preview(
        db,
        Namespace(
            so=fixture["core_so"].so_number, line=row.item_code, date=NOW, qty=None,
            item=None, actor_email=None, out=None,
        ),
    )

    mine = [l for l in result["lines"] if l["item_code"] == row.item_code]
    assert len(mine) == 1, result["lines"]
    assert mine[0]["delivery_date"] == NOW_FMT
    assert (mine[0]["was"] or {}).get("delivery_date") == WAS_FMT, mine[0]
    assert "NEW DATE" in result["body_html"] and "REMARK" not in result["body_html"]
    assert WAS_FMT in result["body_html"] and NOW_FMT in result["body_html"]

    db.expire_all()
    db.refresh(row)
    assert row.redirected_to_pool is False, "the dry run leaves the row as it was"
    assert row.delivery_date == WAS_1
    assert db.query(PlanningChangeBatch).count() == batches_before
    assert db.query(OrderInquiryRow).filter(
        OrderInquiryRow.so_line_id == fixture["line"].id
    ).count() == rows_before
