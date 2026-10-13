"""DO-BRANCH-NAME (owner, 13 Oct 2026): the synced `orders.branch_name` reaches the wire.

`GET /api/v1/order-management/orders` (and the single GET) serialise through `OrderResponse`;
a field it does not declare is silently dropped, which is why neither the chatbot DO list nor
the DO screens could show the branch. The value is AutoCount-owned, so it is read-only: it is
on the response, never on the create or update body.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.schemas.order import OrderCreate, OrderResponse, OrderUpdate


def _row(**overrides):
    base = dict(
        id="o-1",
        order_number="DO-ZZT-0001",
        created_at=datetime(2026, 10, 1),
        updated_at=datetime(2026, 10, 1),
        branch_code="KL01",
        branch_name="KLCC SITE OFFICE",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_order_response_carries_the_branch_name() -> None:
    dumped = OrderResponse.model_validate(_row()).model_dump()
    assert dumped["branch_name"] == "KLCC SITE OFFICE"


def test_order_response_branch_name_is_null_when_unset() -> None:
    dumped = OrderResponse.model_validate(_row(branch_name=None)).model_dump()
    assert dumped["branch_name"] is None


def test_branch_name_is_not_writable() -> None:
    assert "branch_name" not in OrderCreate.model_fields
    assert "branch_name" not in OrderUpdate.model_fields
