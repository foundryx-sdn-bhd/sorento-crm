"""OI-PRODUCT-FOLLOW (`documentation/plans/scm/PLAN-oi-product-follow-2oct.md`).

An AutoCount product swap on an existing SO line (same `source_ref`) reaches the board's
mirror line and, through the planning board apply, the order inquiry row - the same way a
qty / date change already does, with the old value kept as "was".
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.project_so import (
    SO_STATUS_ADOPTED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from tests.test_ingest_documents import INGEST_SO, _so_line, _so_record, env  # noqa: F401

MARKER = "ZZT-OIPF"
D1 = date(2026, 11, 2)


def _adopt_with_mirror(env, record):
    """The ESB-created order adopted onto `projects.sales_orders` with one mirror line,
    the shape front-planning reconciliation leaves behind."""
    header = env.header("sales_orders", record["source_ref"])
    core_line = env.so_lines(header["id"])[0]
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
    order = ProjectSalesOrder(
        id=str(uuid.uuid4()), company_id=env.company_a, project_id=None,
        provisional_ref=f"{MARKER}-{uuid.uuid4().hex[:8]}", status=SO_STATUS_ADOPTED,
        so_id=str(header["id"]), autocount_doc_no=record["so_number"],
    )
    env.db.add(order)
    env.db.flush()
    mirror = ProjectSalesOrderLine(
        id=str(uuid.uuid4()), company_id=env.company_a, project_sales_order_id=order.id,
        core_sales_order_line_id=str(core_line["id"]), line_no=1, product_id=product_id,
        qty=Decimal("10"), delivery_date=D1,
    )
    env.db.add(mirror)
    env.db.commit()
    return header, mirror


def test_mirror_line_follows_an_esb_product_swap(env):
    """S1: the ESB swaps the product on a line it already sent (same DtlKey). The mirror
    line the board and the OI read must name the NEW product, as the manual SO edit
    already does (`sales_order_service.py:1806-1815`) - today `_sync_mirror_line` copies
    only date and qty, so the board keeps sourcing the old product."""
    line = _so_line(env, qty_ordered=10, required_date=D1.isoformat())
    record = _so_record(env, lines=[line])
    res = env.post(INGEST_SO, [record])
    assert res.json()["records"][0]["outcome"] == "created", res.text
    _header, mirror = _adopt_with_mirror(env, record)

    swapped = dict(record, lines=[dict(line, product_ref=env.product2_ref)])
    res2 = env.post(INGEST_SO, [swapped])
    assert res2.json()["records"][0]["outcome"] == "updated", res2.text

    env.db.expire_all()
    new_product_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
    refreshed = env.db.get(ProjectSalesOrderLine, mirror.id)
    assert str(refreshed.product_id) == str(new_product_id), (
        "S1: the mirror line must follow the AutoCount line's product on a re-push"
    )
    # Date and qty were not in the change and stay as they were.
    assert refreshed.delivery_date == D1
    assert refreshed.qty == Decimal("10")


def test_mirror_line_product_untouched_by_a_repush_that_keeps_the_product(env):
    """Kill guard for S1: an identical re-push leaves the mirror's product alone."""
    line = _so_line(env, qty_ordered=10, required_date=D1.isoformat())
    record = _so_record(env, lines=[line])
    env.post(INGEST_SO, [record])
    _header, mirror = _adopt_with_mirror(env, record)
    old_product_id = mirror.product_id

    res2 = env.post(INGEST_SO, [record])
    assert res2.json()["records"][0]["outcome"] in ("updated", "unchanged"), res2.text

    env.db.expire_all()
    assert str(env.db.get(ProjectSalesOrderLine, mirror.id).product_id) == str(old_product_id)
