#!/usr/bin/env python3
"""Re-link the order inquiry rows the PO-document fallback put on another line's SPO.

OI-SPO-EXACT (`documentation/plans/scm/PLAN-oi-spo-exact-13oct.md`, owner 13 Oct 2026:
"AutoCount linkage is the truth, explicit links only, no matching guesses").

Until this lane, `pair_needs._through_po` reached a purchase order line's shipping orders
by the exact line when an allocation named it, and OTHERWISE by any allocation of the same
PO number and product. That fallback linked OI-2601-9001 (SO900001, SRTTEST01) to
SPO-2026/01-9001 line 25, a container of ANOTHER line of 202601-S9001 bought for another
sales order line. The pairing no longer falls back; this script repairs the links it wrote.

WHAT IT CHANGES
---------------
An AUTO link (`order_inquiry_links.auto`, never a person's click) on an SPO allocation is
repaired when it carries the fallback's signature, read off AutoCount's own columns:

* the row's core sales order line's book names a purchase order line (its
  `from_so_line_ref` is the line's `source_ref`, or an `autocount` / `po_upload` claim
  pairs them) on the PO the allocation came from (`from_po_number`), AND
* the allocation names none of the line's book documents: not the line itself
  (`from_so_line_ref`), not a book-named PO line (`from_po_number` + `from_po_line_ref`),
  not a book claim.

A PO line the book names counts whatever its status: a link written through a line that
was cancelled afterwards is not the fallback's.

Each such link is removed through the service's own `_remove_links` (its audit claim with
it), and the row's freed need is paired again through `pair_needs`, the one rule the
importer and `follow_book_for_rows` both call, and written through `_write_link` with
`auto: oi_spo_exact_repair` - so the row lands on its own line's container, or on the PO
line itself when no allocation names it. Manual links are never read, removed or moved.

READ THE DRY RUN BEFORE `--apply`. The signature is the fallback's, but it is not only
the fallback's: an older cascade auto link (`auto_place_for_products`) that placed the
remainder of a line on a sibling PO line's open SPO looks the same, and is moved too
(the owner's rule: explicit links only). A row whose re-pair cannot place everything it
gave back is printed with `SHORT BY <qty>`; it is left partly linked, not re-cascaded.
This script runs `pair_needs` only: not `follow_book_for_rows`' linkable-row filter nor
its D3 displacement of another line's holder.

SAFETY / IDEMPOTENCY
--------------------
`--dry-run` is the DEFAULT and writes nothing: each company runs through the real code path
inside a SAVEPOINT, is reported, then rolled back. `--apply` commits per company. A second
run finds no link with the signature (the first removed them all and the pairing cannot
write one any more), so it reports zero.

It also prints how many allocations state a PO number but no PO line: the new rule never
reaches those through a PO line, and the owner asked for the count before any fallback for
them is considered (14 Sep prod copy: 0).

Run from sorento_crm_backend/:
    venv/bin/python scripts/repair_oi_spo_exact.py            # dry run
    venv/bin/python scripts/repair_oi_spo_exact.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.models.base import company_scope, set_company_scope
from app.models.company import Company
from app.models.order import SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation
from app.models.product import Product
from app.models.project_so import (
    INQUIRY_CANCELLED,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrderLine,
)
from app.services.project_order_inquiry_import_service import (
    _BOOK_CLAIM_SOURCES,
    _Need,
    _bought_rows,
    pair_needs,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm import order_link_service

TRIGGER = "oi_spo_exact_repair"
_ZERO = Decimal("0")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else _ZERO


def _all_company_ids(db) -> List[str]:
    with company_scope(db, None):
        rows = (
            db.query(OrderInquiryRow.company_id)
            .filter(OrderInquiryRow.company_id.isnot(None))
            .distinct()
            .all()
        )
    return sorted({str(company_id) for (company_id,) in rows})


def _company_label(db, company_id: str) -> str:
    row = db.query(Company.name, Company.code).filter(Company.id == company_id).first()
    if not row:
        return company_id
    name, code = row
    return f"{name} ({code})" if code else name


def _auto_spo_links(db, company_id: str):
    """Every live auto link on an SPO allocation, with its row, core line and allocation,
    in a stable order (row age, then link age) so two runs repair in the same order."""
    return (
        db.query(OrderInquiryLink, OrderInquiryRow, SalesOrderLine, SPOAllocation)
        .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
        .join(ProjectSalesOrderLine, ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id)
        .join(
            SalesOrderLine,
            SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
        )
        .join(SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
        .filter(
            OrderInquiryRow.company_id == company_id,
            OrderInquiryRow.state != INQUIRY_CANCELLED,
            OrderInquiryLink.auto.is_(True),
            OrderInquiryLink.reserve_request_row_id.is_(None),
        )
        .order_by(
            OrderInquiryRow.created_at.asc(),
            OrderInquiryRow.id.asc(),
            OrderInquiryLink.linked_at.asc(),
            OrderInquiryLink.id.asc(),
        )
        .all()
    )


def _book_documents(db, core_line: SalesOrderLine) -> Dict[str, set]:
    """What AutoCount's book names for this sales order line: the allocations that name it
    (directly or through a book claim), and the (PO number, PO line ref) of every purchase
    order line that does (by ref or through a book claim). Read through the pairing's own
    readers (`_bought_rows`, `_claim_rows`), not a second query of the same columns."""
    allocations, po_lines = _bought_rows(db, [core_line])
    allocation_ids = {str(a.id) for a in allocations}
    po_line_ids = {str(line.id) for line in po_lines}
    ref = (core_line.source_ref or "").strip()
    if ref:
        # Every status, unlike `_bought_rows`: a line cancelled after the link was written
        # still is the line the link was written through.
        po_line_ids |= {
            str(line_id)
            for (line_id,) in db.query(PurchaseOrderLine.id).filter(
                PurchaseOrderLine.from_so_line_ref == ref,
                PurchaseOrderLine.product_id == core_line.product_id,
            )
        }
    for claim in order_link_service._claim_rows(db, so_line_ids={str(core_line.id)}):
        if claim["source"] not in _BOOK_CLAIM_SOURCES:
            continue
        # A claim names a PO line OR an allocation; each set is only ever compared
        # against ids of its own kind, so the other half of this pair matches nothing.
        allocation_ids.add(str(claim["target_id"]))
        po_line_ids.add(str(claim["target_id"]))
    po_line_keys: set = set()
    if po_line_ids:
        for number, source_ref in (
            db.query(PurchaseOrder.po_number, PurchaseOrderLine.source_ref)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(
                PurchaseOrderLine.id.in_(sorted(po_line_ids)),
                PurchaseOrderLine.product_id == core_line.product_id,
            )
            .all()
        ):
            po_line_keys.add((str(number), (source_ref or "").strip()))
    return {"allocations": allocation_ids, "po_lines": po_line_keys}


def _is_fallback_link(
    allocation: SPOAllocation, core_line: SalesOrderLine, book: Dict[str, set]
) -> bool:
    """The fallback's signature: an allocation of the line's own item, from a PO the book
    names a line on, that names none of the line's book documents."""
    if str(allocation.product_id or "") != str(core_line.product_id or ""):
        return False
    if str(allocation.id) in book["allocations"]:
        return False
    number = str(allocation.from_po_number or "")
    if number not in {key[0] for key in book["po_lines"]}:
        return False
    return (number, (allocation.from_po_line_ref or "").strip()) not in book["po_lines"]


def _target_label(take_or_link: Dict[str, Any]) -> str:
    kind = "SPO" if take_or_link.get("spo_allocation_id") else "PO line"
    qty = format(_dec(take_or_link["qty"]).normalize(), "f")
    return f"{take_or_link.get('document') or '?'} ({kind}) x {qty}"


def run_company(db, company_id: str) -> List[Dict[str, Any]]:
    """Repair one company's fallback links. The caller owns the transaction."""
    changes: List[Dict[str, Any]] = []
    by_row: Dict[str, Dict[str, Any]] = {}
    books: Dict[str, Dict[str, set]] = {}
    for link, row, core_line, allocation in _auto_spo_links(db, company_id):
        core_id = str(core_line.id)
        if core_id not in books:
            books[core_id] = _book_documents(db, core_line)
        if not _is_fallback_link(allocation, core_line, books[core_id]):
            continue
        entry = by_row.setdefault(
            str(row.id), {"row": row, "core_line": core_line, "links": []}
        )
        entry["links"].append(link)

    for entry in by_row.values():
        row, core_line, wrong = entry["row"], entry["core_line"], entry["links"]
        # A fresh service per row: it memoises link tallies per instance, and the row
        # before this one just moved capacity.
        service = ProjectOrderInquiryService(db)
        old = [
            {
                "document": link.document,
                "spo_allocation_id": str(link.spo_allocation_id),
                "qty": _dec(link.qty),
            }
            for link in wrong
        ]
        service._remove_links(row, wrong)
        need = service._unlinked_need(row)
        new: List[Dict[str, Any]] = []
        if need > _ZERO:
            links_by_key, _not_linkable = pair_needs(
                db,
                [_Need(key=str(row.id), need_qty=need, core_line=core_line)],
                _bought_rows(db, [core_line]),
            )
            held = links_by_key.get(str(row.id))
            for take in held.takes if held else []:
                service._write_link(
                    row, take, take["qty"], actor_user_id=None, auto_trigger=TRIGGER
                )
                new.append(take)
        db.flush()
        service.refresh_link_state([row])
        db.flush()
        inquiry_no = (
            db.query(OrderInquiry.inquiry_no)
            .filter(OrderInquiry.id == row.order_inquiry_id)
            .scalar()
        )
        product_code = (
            db.query(Product.product_code)
            .filter(Product.id == core_line.product_id)
            .scalar()
        )
        gave_back = sum((t["qty"] for t in old), _ZERO)
        placed = sum((_dec(t["qty"]) for t in new), _ZERO)
        changes.append({
            "row_id": str(row.id),
            "short": max(gave_back - placed, _ZERO),
            "inquiry_no": inquiry_no,
            "product_code": product_code or row.item_code,
            "old": old,
            "new": [
                {
                    "document": take["document"],
                    "spo_allocation_id": take["spo_allocation_id"],
                    "po_line_id": take["po_line_id"],
                    "qty": _dec(take["qty"]),
                }
                for take in new
            ],
        })
    return changes


def _unnamed_line_allocations(db, company_id: str) -> int:
    """Allocations that state a source PO number but no PO line (owner's ask: report the
    count before any fallback for them is considered)."""
    return (
        db.query(func.count(SPOAllocation.id))
        .filter(
            SPOAllocation.company_id == company_id,
            func.coalesce(SPOAllocation.from_po_number, "") != "",
            func.coalesce(func.btrim(SPOAllocation.from_po_line_ref), "") == "",
        )
        .scalar()
        or 0
    )


def run(db, *, apply: bool, company_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    ids = list(company_ids) if company_ids is not None else _all_company_ids(db)
    out: Dict[str, Any] = {}
    for company_id in ids:
        with company_scope(db, frozenset({company_id})):
            savepoint = None if apply else db.begin_nested()
            changes = run_company(db, company_id)
            unnamed = _unnamed_line_allocations(db, company_id)
            if apply:
                db.commit()
            else:
                savepoint.rollback()
        out[company_id] = {"changes": changes, "unnamed_line_allocations": unnamed}
        print(f"\n{_company_label(db, company_id)}:")
        print(f"  rows re-linked: {len(changes)}")
        print(f"  allocations naming a PO number but no PO line: {unnamed}")
        for change in changes:
            old = "; ".join(_target_label(t) for t in change["old"])
            new = "; ".join(_target_label(t) for t in change["new"]) or "(nothing)"
            short = (
                f"  SHORT BY {format(change['short'].normalize(), 'f')}"
                if change["short"] > _ZERO
                else ""
            )
            print(
                f"  {change['inquiry_no']}  {change['product_code']}  {old}  ->  {new}{short}"
            )
    return out


def main(argv: Optional[Sequence[str]] = None, db=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write the changes (default is a dry run that writes nothing)",
    )
    args = parser.parse_args(argv)

    owns_db = db is None
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        # A script has no request and no principal: scope each company explicitly.
        set_company_scope(db, None)
        print(
            "OI links on another PO line's SPO "
            f"({'APPLYING' if args.apply else 'DRY-RUN, nothing is written'}):"
        )
        out = run(db, apply=args.apply)
        total = sum(len(company["changes"]) for company in out.values())
        print(f"\n=== {total} row(s) {'re-linked' if args.apply else 'would be re-linked'} ===")
        if not args.apply and total:
            print("Re-run with --apply to write these changes.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
