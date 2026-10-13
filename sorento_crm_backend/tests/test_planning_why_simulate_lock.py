"""Simulate confirm turns a lock timeout into a clear message (never a raw red error).

Simulate sets `SET LOCAL lock_timeout`, so a concurrent writer holding an order's row lock makes
that order fail with Postgres 55P03 LockNotAvailable (or 57014 QueryCanceled from the statement
timeout). Contract: that order answers ok=false with exactly MESSAGE, no per-line probing
(`simulated_errors` empty), and the other orders of the run are unaffected.
"""
from __future__ import annotations

import psycopg2.errors
import pytest
from sqlalchemy.exc import OperationalError

from .test_confirm_all_preview import (  # noqa: F401
    _core_line, _core_so, _line_payload, _project_line, _project_so, _two_line_order, api,
)
from .test_fulfilment_simulate_confirm import URL, _as_role

MESSAGE = (
    "Another change to this order is being saved right now. Nothing was simulated for it; "
    "run Simulate confirm again in a moment."
)


@pytest.mark.parametrize(
    "orig", [psycopg2.errors.LockNotAvailable, psycopg2.errors.QueryCanceled],
    ids=["lock_timeout_55P03", "statement_timeout_57014"],
)
def test_a_lock_timeout_on_one_order_is_a_clear_message_and_spares_the_others(
    api, monkeypatch, orig
):
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    _as_role(monkeypatch, "admin")
    locked, _c1, (la, lb) = _two_line_order(world)
    # The stock pool is shared: the second order rides it rather than seeding it again.
    db = world.db
    fine = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    fa, fb = [
        _project_line(db, fine, line_no=n, product=world.product,
                      core_line=_core_line(db, core_so, world.product, world.own_wh,
                                           qty_ordered="50"))
        for n in (10, 20)
    ]
    db.commit()
    original = ProjectSupplyService.confirm

    def wrapper(self, order, payload, *args, **kwargs):
        if str(order.id) == str(locked.id):
            raise OperationalError("SELECT ... FOR UPDATE", {}, orig("canceling statement"))
        return original(self, order, payload, *args, **kwargs)

    monkeypatch.setattr(ProjectSupplyService, "confirm", wrapper)

    response = client.post(URL, json={"orders": [
        {"pso_id": locked.id, "lines": [_line_payload(la.id, buy_qty="50"),
                                        _line_payload(lb.id, buy_qty="50")]},
        {"pso_id": fine.id, "lines": [_line_payload(fa.id, buy_qty="50"),
                                      _line_payload(fb.id, buy_qty="50")]},
    ]})

    assert response.status_code == 200, response.text
    first, second = response.json()["results"]
    assert first["ok"] is False
    assert first["error"] == MESSAGE
    assert not first.get("simulated_errors")
    assert second["ok"] is True, second
