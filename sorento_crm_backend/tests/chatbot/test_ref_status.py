"""REF-PROBES S5: the purchase order and purchase request cards (AC-17).

New module `app/services/chatbot/ref_status.py`, API mirroring `so_status`:

    lookup_purchase_orders(db, words)    -> RefLookup(cards: list[str], missing: list[str])
    lookup_purchase_requests(db, words)  -> RefLookup(cards: list[str], missing: list[str])
    MAX_LINES                            -> the per-card line cap

so_status's SO card prints no lines, so there is no existing cap to reuse; the cap is the
new module's own constant. Quantities print without trailing zeros ("x 5", not "x 5.0000").
Every row is seeded here on a blank Postgres schema.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.models.procurement import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequestHeader,
    PurchaseRequestLine,
    Supplier,
)
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product as seed_product
from tests._pg_fixture import unique_code


def _seed_po(db, number: str, lines: list[tuple[str, int]], *, supplier="ZZT Supplier Bhd", status="active"):
    sup = Supplier(
        id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID,
        supplier_code=unique_code("SUP")[:50], supplier_name=supplier,
    )
    db.add(sup)
    db.flush()
    po = PurchaseOrder(
        id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, po_number=number,
        supplier_id=sup.id, issue_date=date(2026, 9, 5), status=status,
    )
    db.add(po)
    db.flush()
    for code, qty in lines:
        prod = seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.add(
            PurchaseOrderLine(
                id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, purchase_order_id=po.id,
                product_id=prod.id, qty_ordered=qty,
            )
        )
    db.flush()
    return po


def _seed_pr(db, number: str, lines: list[tuple[str, int]], *, customer="ZZT Project Sdn Bhd"):
    pr = PurchaseRequestHeader(
        id=str(uuid.uuid4()), request_type="purchase_request", request_number=number,
        customer_name=customer, status="processed_by_cs", approval_status="approved",
    )
    db.add(pr)
    db.flush()
    for i, (item, qty) in enumerate(lines):
        db.add(PurchaseRequestLine(id=str(uuid.uuid4()), purchase_request_id=pr.id, item_code=item, quantity=qty, sort_order=i))
    db.flush()
    return pr


def test_po_card_fields(session_factory):
    from app.services.chatbot import ref_status

    db = session_factory()
    number = f"ZZT-PO-{unique_code()[:6].upper()}"
    _seed_po(db, number, [("ZZTSKUA", 5), ("ZZTSKUB", 12)])
    out = ref_status.lookup_purchase_orders(db, [number])
    assert out.missing == []
    assert len(out.cards) == 1
    card = out.cards[0]
    for want in ("Purchase order", number, "ZZT Supplier Bhd", "5 Sep 2026", "ZZTSKUA x 5", "ZZTSKUB x 12"):
        assert want in card, (want, card)
    assert "active" in card.lower(), card


def test_unknown_po_is_missing(session_factory):
    from app.services.chatbot import ref_status

    out = ref_status.lookup_purchase_orders(session_factory(), ["ZZT-NOPE-0000"])
    assert out.cards == [] and out.missing == ["ZZT-NOPE-0000"]


def test_pr_card_fields(session_factory):
    from app.services.chatbot import ref_status

    db = session_factory()
    number = f"ZZTPR{unique_code()[:5].upper()}"
    _seed_pr(db, number, [("ZZT BASIN UNDER COUNTER", 10)])
    out = ref_status.lookup_purchase_requests(db, [number])
    assert out.missing == []
    assert len(out.cards) == 1
    card = out.cards[0]
    for want in ("Purchase request", number, "ZZT Project Sdn Bhd", "ZZT BASIN UNDER COUNTER x 10"):
        assert want in card, (want, card)
    assert "processed_by_cs" in card.lower() or "processed by cs" in card.lower(), card
    assert "approved" in card.lower(), card


def test_duplicate_pr_prints_each(session_factory):
    from app.services.chatbot import ref_status

    db = session_factory()
    number = f"ZZTPR{unique_code()[:5].upper()}"
    _seed_pr(db, number, [("ZZT ITEM ONE", 1)])
    _seed_pr(db, number, [("ZZT ITEM TWO", 2)], customer="ZZT Other Customer")
    out = ref_status.lookup_purchase_requests(db, [number])
    assert len(out.cards) == 2, out.cards


def test_lines_capped(session_factory):
    from app.services.chatbot import ref_status

    db = session_factory()
    extra = 3
    n = ref_status.MAX_LINES + extra
    number = f"ZZTPR{unique_code()[:5].upper()}"
    _seed_pr(db, number, [(f"ZZTITEM{i:03d}", 1) for i in range(n)])
    card = ref_status.lookup_purchase_requests(db, [number]).cards[0]
    assert card.count(" x 1") == ref_status.MAX_LINES, card
