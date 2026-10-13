"""Turn "Borrow from another order" ON for a test (`PLAN-borrow-location-only-8oct.md` D1).

The switch defaults OFF, under which a borrow names no donor and its order-back hangs on the
borrowing line. A test that pins the donor-named behaviour (row on the donor's line, donor
decision superseded, donor named in the reason) describes the ON state and says so by
calling this. Get-or-create the singleton, the way `test_confirm_local_buy_no_oi.py` sets
`local_buy_routing_enabled`.
"""
from __future__ import annotations

import uuid

from app.models.user import SystemSetting


def borrow_from_order_on(db) -> None:
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting(id=str(uuid.uuid4()))
        db.add(row)
        db.flush()
    db.query(SystemSetting).filter(SystemSetting.id == row.id).update(
        {SystemSetting.borrow_from_order_enabled: True}
    )
    db.flush()
    db.expire(row)
