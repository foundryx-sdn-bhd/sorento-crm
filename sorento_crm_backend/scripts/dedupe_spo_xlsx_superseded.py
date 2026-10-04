#!/usr/bin/env python3
"""Dedupe the shipping orders the first ESB push DUPLICATED (D29,
spo-xlsx-supersede).

WHAT IT DOES
------------
Between the ESB's Shipping order task going live and D25 shipping, every
xlsx-loaded shipping order was APPENDED to rather than replaced on its first
push: the xlsx-era rows (`source_ref IS NULL`, closed by a Sorento GRN)
stayed, and AutoCount's own line-set landed beside them as fresh open rows.
Reorder planning then counted those open rows as incoming supply for goods
already in the warehouse (3,294 documents on production).

This script applies the rule the ingest now applies at push time, once, to
the rows already in the database: for every `spo_number` in the company that
holds BOTH ref-less rows AND ref rows, the existing ref rows (in
`spo_line_number` order) ARE the incoming line-set, so

- the xlsx-era receipt is carried onto them per `(product_id,
  upper(location_code))` group, in line order, each line up to its own
  allocated quantity and any remainder onto the last (D26);
- whatever pointed at a superseded xlsx-era row - a GRN pick, an order-link
  claim, an order-inquiry placement - is repointed onto the group's FIRST
  line, and the xlsx-era row is then deleted (D27);
- a ref-less group AutoCount named no line for is left exactly as it is: the
  push already closed it, and there is nothing to supersede it with.

ONE ALGORITHM, NOT A SECOND COPY
--------------------------------
The plan comes from `shipping_order_rules.plan_xlsx_supersede`, the same pure
function `ShippingOrderIngestService._supersede_xlsx_rows` runs at push time,
and the link move from the same `repoint_allocation_dependants`. Only the
writing differs: the push CREATES the incoming lines, this script UPDATES the
ref rows a push already appended.

SAFETY / IDEMPOTENCY
--------------------
- `--dry-run` (the default) computes and prints the whole plan, writes
  nothing, and never leaves the session dirty.
- One commit per document, so an interrupted run leaves whole documents done
  and the rest untouched.
- Re-running is a no-op: a document with no ref-less row left is not selected
  at all, and a ref-less group with no counterpart yields no group to apply.
- `--since` narrows to the documents an ESB push actually touched in the
  incident window (a document qualifies when ANY of its ref rows was created
  at or after the timestamp; its whole ref line-set is then the incoming
  side, since a document's lines arrive together).

USAGE
-----
    python scripts/dedupe_spo_xlsx_superseded.py --company SRT --dry-run
    python scripts/dedupe_spo_xlsx_superseded.py --company SRT \
        --since 2026-09-06T05:15:00 --apply

    # ONE document only (repeat --spo for more), the owner's production fix:
    python scripts/dedupe_spo_xlsx_superseded.py --company SRT \
        --spo SPO-2026/08-0074 --dry-run

`--company` takes `companies.code`. Sorento's is `SRT`, not `SORENTO`: the
multi-company scaffold seeds it as ('Sorento', 'SRT')
(alembic/versions/302_multi_company_scaffold.py:158), confirmed on the dev
database (id 00000000-0000-0000-0000-000000000001).

`--spo` narrows the sweep to the named `spo_number`s, still inside the one
company's scope - the same number under another company is never read.
Every scoped document prints its plan line by line: each Excel row it would
delete (id, allocated, received), the receipt carried onto each AutoCount
line, the links moved, and any Excel row it leaves alone with the reason
(`no AutoCount line` for a product AutoCount does not list on the document,
`received locked` for D26a). Nothing about the plan differs between
`--dry-run` and `--apply`; only the writes do.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_

from app.database import SessionLocal
from app.models.base import company_scope
from app.models.company import Company
from app.models.procurement import SPOAllocation
from app.services.company_scope import register_company_scope_listeners
from app.services.procurement_service import InboundShipmentService
from app.services.rules import shipping_order_rules
from app.services.rules.shipping_order_rules import (
    append_note,
    SupersedeNotConserved,
    assert_supersede_conserved,
    carried_received,
    plan_xlsx_supersede,
    repoint_allocation_dependants,
    repoint_picking_lines_by_capacity,
)
from app.services.shipping_order_ingest_service import (
    LINE_CLOSED,
    LINE_OPEN,
    RECEIPT_FULLY_RECEIVED,
    RECEIPT_PENDING,
)

logger = logging.getLogger("scripts.dedupe_spo_xlsx_superseded")

BATCH_SIZE = 200


def _document_numbers(db, company_id: str, after: Optional[str]) -> list[str]:
    """One keyset page of `spo_number`s holding at least one ref-less row.

    Keyset on `spo_number` rather than `Session.query(...).yield_per(...)`:
    this loop commits per document, and a `yield_per` server-side cursor does
    not survive the first commit (LESSONS-LEARNT).
    """
    query = (
        db.query(SPOAllocation.spo_number)
        .filter(
            SPOAllocation.company_id == company_id,
            SPOAllocation.spo_number.isnot(None),
            SPOAllocation.source_ref.is_(None),
            # D25c: an Excel-era row is the only candidate, and all three
            # writers of one qualify - the SCM outstanding upload stamps
            # `scm_upload`, the Procurement page's Upload SPO and the n8n
            # packing-list route stamp nothing. Keying on `scm_upload` alone
            # is what made the production sweep skip SPO-2026/09-0028, the
            # incident document itself.
            or_(
                SPOAllocation.source_system == shipping_order_rules.XLSX_SOURCE_SYSTEM,
                SPOAllocation.source_system.is_(None),
            ),
            # Security round 6: a row drawn against a PURCHASE ORDER LINE is
            # one row for one real line (the two SCM writers), never an
            # aggregate, and the ESB carries no `po_line_id` to hand on -
            # superseding one would sever the PO linkage. Mirrors
            # `is_xlsx_era_row`'s own guard, in SQL so such a number never
            # enters the sweep at all.
            SPOAllocation.po_line_id.is_(None),
        )
        .distinct()
    )
    if after is not None:
        query = query.filter(SPOAllocation.spo_number > after)
    return [row[0] for row in query.order_by(SPOAllocation.spo_number).limit(BATCH_SIZE).all()]


def _incoming_values(row: SPOAllocation) -> dict[str, Any]:
    """One already-appended ref row expressed as the "incoming" line it was.

    The same keys `ShippingOrderIngestService._line_values` produces for the
    planner to read, with `line_number` standing in for AutoCount's Seq - the
    order the push itself wrote them in.
    """
    return {
        "product_id": row.product_id,
        "location_code": row.location_code,
        "warehouse_id": row.warehouse_id,
        "allocated_quantity": int(row.allocated_quantity or 0),
        "quantity_received": int(row.quantity_received or 0),
        "inbound_shipment_id": row.inbound_shipment_id,
        "line_number": row.spo_line_number,
    }


def _retire_older_dockeys(rows: list[SPOAllocation], dry_run: bool) -> int:
    """Mark the rows a SUPERSEDED DocKey left behind as retired (D28d).

    `_newest_dockey_rows` picks one DocKey as the document AutoCount is
    stating now and passes over the rest. Those rows are history, and until
    they say so the group recompute keeps treating a closed, fully received
    one as a live line: it takes a Seq-order share of the live document's GRN
    and reopens when that GRN is deleted. The ingest stamps them on the next
    push of the number; this stamps the corpus the sweep is already walking,
    so production ends consistent instead of waiting for a push that may
    never come.

    Idempotent: an existing `retired_at` is left alone, and the freeze only
    ever raises the stated floor.
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    marked = 0
    for row in rows:
        if row.retired_at is not None:
            continue
        # The ingest's DocKey-change path runs right after the spo_number
        # guard proved the older DocKey's rows are all closed; historical
        # data gives this sweep no such guarantee, so it retires only a
        # closed row and leaves an open one for its own push to settle.
        if row.line_status != LINE_CLOSED:
            continue
        marked += 1
        if dry_run:
            continue
        frozen = max(int(row.stated_received or 0), int(row.quantity_received or 0))
        if frozen > 0:
            row.stated_received = frozen
        row.retired_at = now
    return marked


def _newest_dockey_rows(refs: list[SPOAllocation]) -> list[SPOAllocation]:
    """The ref rows of the NEWEST `source_doc_ref` only (D29 amended, S5).

    A number can carry the rows of a RETIRED DocKey (all closed) beside the
    live one - the delete-and-recreate path S2's guard allows. Those rows are
    not the document AutoCount is stating now, so they are neither a carry
    target nor a repoint target: folding them in would overwrite a retired
    row's own receipt and could move a live GRN link onto a dead line.

    Newest = the greatest `created_at` among a DocKey's rows, tie-broken by
    the greatest `spo_line_number` and then the key itself, so a set of rows
    written inside one transaction (identical `created_at`) still resolves to
    one deterministic answer.
    """
    by_dockey: dict[str, list[SPOAllocation]] = {}
    for row in refs:
        by_dockey.setdefault(str(row.source_doc_ref or ""), []).append(row)
    if len(by_dockey) <= 1:
        return sorted(refs, key=lambda r: (r.spo_line_number or 0, str(r.id)))

    def _rank(item):
        dockey, rows = item
        created = max(
            (row.created_at for row in rows if row.created_at is not None),
            default=datetime.min,
        )
        return (created, max((row.spo_line_number or 0) for row in rows), dockey)

    _, newest = max(by_dockey.items(), key=_rank)
    return sorted(newest, key=lambda r: (r.spo_line_number or 0, str(r.id)))


def _has_ref_row_since(
    db, company_id: str, spo_number: str, since: datetime
) -> bool:
    """Whether ANY ref row of this document was written at or after `since`.

    Asked as its own cheap EXISTS before the document's rows are loaded
    (reviewer cleanup): with `--since` narrowing the sweep to the incident
    window, most numbers are out of scope and must cost one indexed probe,
    not a full row load.
    """
    return (
        db.query(SPOAllocation.id)
        .filter(
            SPOAllocation.company_id == company_id,
            SPOAllocation.spo_number == spo_number,
            SPOAllocation.source_ref.isnot(None),
            SPOAllocation.created_at >= since,
        )
        .first()
        is not None
    )


def _link_counts(db, allocation_id: str) -> tuple[int, int, int]:
    """(picks, claims, order-inquiry links) pointing at one allocation, in ANY
    company: the orphan guard fails closed, and the read returns numbers only."""
    from sqlalchemy import func

    from app.models.procurement import PickingLine
    from app.models.project_so import OrderInquiryLink
    from app.models.scm import OrderLinkClaim

    with company_scope(db, None):
        return tuple(
            int(
                db.query(func.count(model.id))
                .filter(model.spo_allocation_id == allocation_id)
                .scalar()
                or 0
            )
            for model in (PickingLine, OrderLinkClaim, OrderInquiryLink)
        )


def _orphan_rows(plan, by_id: dict[str, SPOAllocation], incoming_rows) -> list[SPOAllocation]:
    """Owner ruling 1 Oct 2026, "just follow AutoCount": an Excel-era row whose
    PRODUCT the newest AutoCount line-set does not list at all is an orphan
    (SPO-2026/08-0074 L23/L26: SRTWCY8605, where AutoCount states
    SRTWCY8605-PJ). No substitution is guessed - a row whose product AutoCount
    DOES list is never an orphan: it is pooled with that product's lines
    (D37), superseded or `received locked`."""
    listed = {str(row.product_id) for row in incoming_rows if row.product_id}
    orphans = []
    for group in plan.kept_groups:
        for row_id in group.row_ids:
            row = by_id.get(row_id)
            if row is not None and str(row.product_id) not in listed:
                orphans.append(row)
    return orphans


def _kept_details(
    plan, by_id: dict[str, SPOAllocation], skip_ids: frozenset = frozenset()
) -> list[str]:
    """One line per Excel row the plan leaves alone, with the reason."""
    lines = []
    for groups, reason in (
        (plan.kept_groups, "no unclaimed AutoCount line for its product"),
        (plan.locked_groups, "received locked"),
    ):
        for group in groups:
            for row_id in group.row_ids:
                row = by_id.get(row_id)
                if row is None or row_id in skip_ids:
                    continue
                lines.append(
                    f"keep {row.id} line {row.spo_line_number} "
                    f"(allocated {int(row.allocated_quantity or 0)}, "
                    f"received {int(row.quantity_received or 0)}): {reason}"
                )
    return lines


def _apply_document(
    db,
    company_id: str,
    spo_number: str,
    since: Optional[datetime],
    dry_run: bool,
    report_kept: bool = False,
) -> Optional[dict[str, Any]]:
    """One document, or `None` when there is nothing to do for it.

    Returns `{rows_removed, lines_touched, links_moved, groups_kept,
    shipment_ids}`.
    """
    if since is not None and not _has_ref_row_since(db, company_id, spo_number, since):
        return None

    rows = (
        db.query(SPOAllocation)
        .filter(
            SPOAllocation.company_id == company_id,
            SPOAllocation.spo_number == spo_number,
        )
        .order_by(SPOAllocation.spo_line_number, SPOAllocation.id)
        .all()
    )
    # D25c: an Excel-era row is a ref-less row whose `source_system` is
    # `scm_upload` or NULL - every writer of these rows loads an aggregate.
    refless = [row for row in rows if shipping_order_rules.is_xlsx_era_row(row)]
    refs = [row for row in rows if row.source_ref]
    if not refless or not refs:
        return None

    incoming_rows = _newest_dockey_rows(refs)
    # D28d: everything the newest-DocKey choice passed over is a retired
    # document's rows. Marked whether or not this document has a group to
    # supersede, since the marker is about the OLD DocKey, not about the
    # dedupe.
    newest_ids = {str(row.id) for row in incoming_rows}
    older_rows = [row for row in refs if str(row.id) not in newest_ids]
    retired_marked = _retire_older_dockeys(older_rows, dry_run)

    # Owner ruling: the repair follows AutoCount across warehouses too.
    plan = plan_xlsx_supersede(
        [_incoming_values(row) for row in incoming_rows], refless, follow_autocount=True
    )
    by_id = {str(row.id): row for row in refless}
    orphans = _orphan_rows(plan, by_id, incoming_rows)
    if not plan.groups and not orphans:
        # Every candidate row is a group the newest DocKey names no line for,
        # or one D26a refuses - this document has nothing left to supersede.
        if retired_marked and not dry_run:
            db.commit()
        if not retired_marked and not report_kept:
            return None
        return {
            "rows_removed": 0,
            "lines_touched": 0,
            "links_moved": 0,
            "groups_kept": plan.groups_kept,
            "retired_marked": retired_marked,
            "fallback_groups": 0,
            "shipment_ids": set(),
            "orphans_removed": 0,
            "orphans_blocked": 0,
            # A scoped (--spo) run says why nothing happens to this document.
            "details": _kept_details(plan, by_id) if report_kept else [],
        }

    counts: dict[str, Any] = {
        "rows_removed": 0,
        "lines_touched": 0,
        "links_moved": 0,
        "groups_kept": plan.groups_kept,
        "retired_marked": retired_marked,
        "fallback_groups": 0,
        "shipment_ids": set(),
        "orphans_removed": 0,
        "orphans_blocked": 0,
        # The per-SPO plan, printed under the document's line (--spo runs).
        "details": [],
    }

    for group in plan.groups:
        target: Optional[SPOAllocation] = None
        group_rows = [incoming_rows[line_plan.index] for line_plan in group.lines]
        # Read before the carry raises them: the conservation guard (D33) compares
        # what the removed rows held against what the lines hold AFTER the carry.
        planned_received = 0
        for line_plan in group.lines:
            row = incoming_rows[line_plan.index]
            allocated = int(row.allocated_quantity or 0)
            received, closed = carried_received(
                allocated, row.quantity_received, line_plan.carried_received
            )
            planned_received += received
            counts["details"].append(
                f"carry {line_plan.carried_received} -> {row.id} line {row.spo_line_number} "
                f"(allocated {allocated}, received after {received})"
            )
            if not dry_run:
                row.quantity_received = received
                # D28c: the carry is a STATEMENT about this line's receipt,
                # exactly like AutoCount's own TransferedQty on a push - so it
                # is recorded as the floor the GRN recompute may raise but
                # never drop below. Same max rule as `quantity_received`.
                stated = max(int(row.stated_received or 0), received)
                if stated > 0:
                    row.stated_received = stated
                row.line_status = LINE_CLOSED if closed else LINE_OPEN
                row.receipt_status = RECEIPT_FULLY_RECEIVED if closed else RECEIPT_PENDING
                if not row.inbound_shipment_id and line_plan.inbound_shipment_id:
                    row.inbound_shipment_id = line_plan.inbound_shipment_id
                # D25c: the bin and the unit carry the same way as the
                # shipment - the ESB states neither, so the upload's are the
                # only ones there are.
                if not row.storage_zone_id and line_plan.storage_zone_id:
                    row.storage_zone_id = line_plan.storage_zone_id
                if not row.uom_id and line_plan.uom_id:
                    row.uom_id = line_plan.uom_id
            if row.inbound_shipment_id or line_plan.inbound_shipment_id:
                counts["shipment_ids"].add(
                    str(row.inbound_shipment_id or line_plan.inbound_shipment_id)
                )
            counts["lines_touched"] += 1
            if target is None:
                target = row
                # D25c (security round 6): the group's own facts onto its
                # FIRST line - same rule as the ingest's own supersede, and
                # the same reason for `max` rather than a sum: this sweep can
                # see the same document twice (a close-only supersede leaves
                # the Excel rows standing), and a rejection must not double.
                if not dry_run and group.rejected_total:
                    row.quantity_rejected = max(
                        int(row.quantity_rejected or 0), group.rejected_total
                    )
                if not dry_run and group.notes:
                    row.allocation_notes = append_note(row.allocation_notes, group.notes)
        removing = [by_id[row_id] for row_id in group.superseded_row_ids if row_id in by_id]
        for row in removing:
            if row.inbound_shipment_id:
                counts["shipment_ids"].add(str(row.inbound_shipment_id))
        if target is not None:
            # Nothing may be pending when the repoint widens its read under a
            # disabled company scope (same structural rule as the ingest).
            db.flush()
            split_moved = 0
            if group.split_receipts:
                # D32/D36: a product-level fallback group (Excel rows that named
                # no warehouse, the owner's GCXU6137164 shape) spans locations,
                # so its GRN picks are split over the lines by capacity.
                split_moved = repoint_picking_lines_by_capacity(
                    db,
                    [str(row.id) for row in removing],
                    [
                        (str(row.id), row.warehouse_id, row.allocated_quantity)
                        for row in group_rows
                    ],
                    company_id=company_id,
                    dry_run=dry_run,
                )
            dependants_moved = repoint_allocation_dependants(
                db,
                [str(row.id) for row in removing],
                str(target.id),
                company_id=company_id,
                dry_run=dry_run,
            )
            if dry_run:
                # Nothing moved in a dry run, so the dependants count still sees
                # the picks the split already counted - subtract them, or the
                # preview reports every pick twice.
                dependants_moved = max(dependants_moved - split_moved, 0)
            counts["links_moved"] += split_moved + dependants_moved
        # D30 (S7): the same trail the ingest's own supersede logs, read
        # BEFORE the rows go.
        trail = "; ".join(
            f"{row.id}(allocated={row.allocated_quantity},"
            f"received={row.quantity_received})"
            for row in removing
        )
        for row in removing:
            counts["details"].append(
                f"delete {row.id} (allocated {int(row.allocated_quantity or 0)}, "
                f"received {int(row.quantity_received or 0)}) line {row.spo_line_number}"
            )
        removed_received = sum(int(row.quantity_received or 0) for row in removing)
        carried_total = sum(line_plan.carried_received for line_plan in group.lines)
        if dry_run:
            # The pure half of D33 runs in the preview too (security review S3),
            # so a document `--apply` would refuse is reported here, not found
            # halfway through the owner-gated run. Picks are not moved in a dry
            # run, so the stranded-pick half can only be checked on apply.
            if carried_total != removed_received or planned_received < removed_received:
                raise SupersedeNotConserved(
                    f"planned carry {carried_total} / replacement {planned_received} "
                    f"vs {removed_received} held"
                )
        else:
            # D33: proven before the rows go; a failure raises out of this
            # document before it commits, so it is left exactly as it was.
            assert_supersede_conserved(
                db,
                [str(row.id) for row in removing],
                removed_received,
                carried_total,
                planned_received,
            )
            for row in removing:
                db.delete(row)
        counts["rows_removed"] += len(removing)
        if group.split_receipts:
            counts["fallback_groups"] += 1
        logger.info(
            "dedupe.spo_supersede spo_number=%s group=%s action=%s target=%s "
            "rows=[%s] dropped_shipments=%s",
            spo_number,
            group.key,
            "planned" if dry_run else "deleted",
            target.id if target is not None else None,
            trail,
            ",".join(group.dropped_shipment_ids) or "-",
        )

    # Owner ruling "just follow AutoCount": an orphan goes too, but only when
    # nothing would be lost with it - no receipt, and no pick, claim or
    # order-inquiry link in any company. Anything else is left exactly as it is
    # and reported ORPHAN-BLOCKED with what holds it.
    for row in orphans:
        received = int(row.quantity_received or 0)
        picks, claims, links = _link_counts(db, str(row.id))
        facts = (
            f"{row.id} line {row.spo_line_number} "
            f"(allocated {int(row.allocated_quantity or 0)}, received {received}; "
            f"picks {picks}, claims {claims}, order-inquiry links {links})"
        )
        if received == 0 and not (picks or claims or links):
            counts["details"].append(f"orphan delete {facts}: no AutoCount line for its product")
            counts["orphans_removed"] += 1
            if row.inbound_shipment_id:
                counts["shipment_ids"].add(str(row.inbound_shipment_id))
            if not dry_run:
                db.delete(row)
        else:
            counts["details"].append(f"ORPHAN-BLOCKED {facts}: left untouched")
            counts["orphans_blocked"] += 1
    counts["details"].extend(
        _kept_details(plan, by_id, frozenset(str(row.id) for row in orphans))
    )
    if not dry_run:
        db.commit()
        # D27a: same refresh every other writer of allocations does, once per
        # shipment this document touched, AFTER the document's own commit
        # (the refresh commits too).
        inbound = InboundShipmentService(db)
        for shipment_id in sorted(counts["shipment_ids"]):
            inbound.refresh_shipment_line_statuses(shipment_id)
    return counts


def run(
    db,
    company_id: str,
    since: Optional[datetime] = None,
    dry_run: bool = True,
    spo_numbers: Optional[list[str]] = None,
) -> dict[str, int]:
    """Every affected document of ONE company. Prints one line per document.

    `dry_run` defaults to `True` so a caller that forgets the argument
    previews rather than writes, and a dry run ends with `db.rollback()` so
    no read state survives the sweep.

    A timezone-AWARE `since` is refused here, before any query: the column it
    is compared against is `DateTime(timezone=False)` (naive DB-local), and
    an aware value would otherwise crash mid-sweep on the first comparison
    rather than telling the operator what is wrong with their argument.

    `spo_numbers` (--spo) replaces the keyset sweep with exactly those numbers,
    still read through this company's own filters and scope, and prints each
    one's plan line by line - including a document with nothing to do, so the
    operator sees why.
    """
    if since is not None and since.tzinfo is not None:
        raise ValueError(
            "--since must be a NAIVE timestamp in database-local time "
            "(e.g. '2026-09-07 05:15'); got a timezone-aware value "
            f"{since.isoformat()!r}. Drop the offset or the trailing 'Z'."
        )
    # S8: the sweep runs ORM reads against company-scoped tables, and a plain
    # `python scripts/...` process has never imported the app's startup path -
    # without this the scope filter is not installed at all and
    # `company_scope` below narrows nothing. Idempotent.
    register_company_scope_listeners()

    summary = {
        "documents": 0,
        "rows_removed": 0,
        "lines_touched": 0,
        "links_moved": 0,
        "groups_kept": 0,
        "retired_marked": 0,
        "fallback_groups": 0,
        "orphans_removed": 0,
        "orphans_blocked": 0,
        # D33: documents the conservation guard refused (left untouched).
        "aborted": 0,
        # D36: the scope count - distinct packing lists (inbound shipments) whose
        # allocations this run changes or would change.
        "shipments": 0,
    }
    shipment_ids: set[str] = set()
    scoped = [n.strip() for n in (spo_numbers or []) if n and n.strip()]
    with company_scope(db, frozenset({company_id})):
        after: Optional[str] = None
        while True:
            numbers = scoped if scoped else _document_numbers(db, company_id, after)
            if not numbers:
                break
            for spo_number in numbers:
                try:
                    counts = _apply_document(
                        db, company_id, spo_number, since, dry_run, report_kept=bool(scoped)
                    )
                except SupersedeNotConserved as exc:
                    # D33 refused this document: nothing of it is committed
                    # (rolled back here), it is named, counted, and the sweep
                    # moves on rather than ending on a traceback with half the
                    # corpus done and no summary (security review S3).
                    db.rollback()
                    summary["aborted"] += 1
                    print(f"  {spo_number}: ABORTED, receipt not conserved ({exc})")
                    continue
                if counts is None:
                    if scoped:
                        print(f"  {spo_number}: nothing to do (no Excel-era rows beside AutoCount lines)")
                    continue
                summary["documents"] += 1
                for key in (
                    "rows_removed",
                    "lines_touched",
                    "links_moved",
                    "groups_kept",
                    "retired_marked",
                    "fallback_groups",
                    "orphans_removed",
                    "orphans_blocked",
                ):
                    summary[key] += counts.get(key, 0)
                shipment_ids.update(counts["shipment_ids"])
                print(
                    f"  {spo_number}: xlsx rows removed {counts['rows_removed']}, "
                    f"lines touched {counts['lines_touched']}, "
                    f"links moved {counts['links_moved']}, "
                    f"groups kept {counts['groups_kept']}, "
                    f"product-fallback groups {counts['fallback_groups']}, "
                    f"PLs touched {len(counts['shipment_ids'])}, "
                    f"orphans removed {counts.get('orphans_removed', 0)}, "
                    f"orphans blocked {counts.get('orphans_blocked', 0)}, "
                    f"old-DocKey rows retired {counts['retired_marked']}"
                )
                if scoped:
                    for line in counts.get("details", []):
                        print(f"      {line}")
            if scoped:
                break
            after = numbers[-1]
            if len(numbers) < BATCH_SIZE:
                break
    summary["shipments"] = len(shipment_ids)
    if dry_run:
        # Nothing was written, but the sweep still opened a read transaction
        # per page - ended here so no snapshot is left held across the run.
        db.rollback()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", required=True, help="Company CODE to dedupe")
    parser.add_argument(
        "--since",
        default=None,
        help="Only documents whose ESB rows were created at or after this ISO timestamp",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Print the plan, write nothing (the default)"
    )
    mode.add_argument("--apply", action="store_true", help="Write the plan")
    parser.add_argument(
        "--spo",
        action="append",
        default=None,
        metavar="SPO_NUMBER",
        help="Only this spo_number (repeatable); prints its plan line by line",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    dry_run = not args.apply
    try:
        since = datetime.fromisoformat(args.since) if args.since else None
    except ValueError as exc:
        print(f"--since is not a timestamp: {exc}")
        return 2
    if since is not None and since.tzinfo is not None:
        # Refused here as well as in `run()` so the CLI answers with a line an
        # operator can act on rather than a traceback.
        print(
            "--since must be a NAIVE timestamp in database-local time "
            "(e.g. '2026-09-07 05:15'); drop the offset or the trailing 'Z'."
        )
        return 2

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.code == args.company).first()
        if company is None:
            print(f"no company with code {args.company!r}")
            return 1
        print(f"=== {args.company} ({'DRY-RUN (no writes)' if dry_run else 'APPLYING'}) ===")
        summary = run(db, str(company.id), since=since, dry_run=dry_run, spo_numbers=args.spo)
        print("\n=== summary ===")
        print(f"mode:                {'DRY-RUN (no writes)' if dry_run else 'APPLIED'}")
        print(f"documents:           {summary['documents']}")
        print(f"xlsx rows removed:   {summary['rows_removed']}")
        print(f"lines touched:       {summary['lines_touched']}")
        print(f"links moved:         {summary['links_moved']}")
        print(f"ref-less groups kept: {summary['groups_kept']}")
        print(f"old-DocKey rows retired: {summary['retired_marked']}")
        print(f"product-fallback groups: {summary['fallback_groups']}")
        print(f"PLs (shipments) touched: {summary['shipments']}")
        print(f"documents aborted (receipt not conserved): {summary['aborted']}")
        print(f"orphan Excel rows removed: {summary['orphans_removed']}")
        print(f"orphan Excel rows BLOCKED (receipt or links): {summary['orphans_blocked']}")
    except ValueError as exc:
        print(str(exc))
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
