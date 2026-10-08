"""The "Borrow from another order" switch (`PLAN-borrow-location-only-8oct.md` D1).

Off (the default, and the answer with no settings row): a borrow takes a LOCATION's stock,
names no donor order, and its order-back hangs on the borrowing line. On: the donor-named
order borrow, its order-back on the donor's line.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import SystemSetting


def borrow_from_order_enabled(db: Session) -> bool:
    enabled = db.query(SystemSetting.borrow_from_order_enabled).first()
    return bool(enabled and enabled[0])
