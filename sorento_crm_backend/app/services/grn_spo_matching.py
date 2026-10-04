"""One matcher, two directions: GRN lines <-> SPO allocations.

AutoCount emits the SPO allocation file and the GRN detail listing independently,
and the office receives them on whatever schedule the supplier and the warehouse
produce them. Which one lands first is not a decision anybody makes, so it must
not decide whether the link is ever made.

Going FORWARDS, the GRN lines import draws each line's quantity from the pool of
allocations for its SPO and product. Going BACKWARDS,
``forward_match_grn_lines_for_spo`` runs the same draw over the lines a previous
import stated (``picking_lines.spo_number_raw``) but could not place, the moment
an allocation for that SPO appears. Both call ``build_allocation_pool`` and
``draw_fifo`` here - a second copy of the two-pass rule is exactly how the two
directions would come to disagree, and disagreeing is the bug.

See ``documentation/plans/purchasing/PLAN-grn-spo-forward-matching.md``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import case, func, literal
from sqlalchemy.orm import Session

from app.models.procurement import PickingHeader, PickingLine, SPOAllocation
from app.services.procurement_service import _spo_match_key

logger = logging.getLogger(__name__)

# The SQL twin of `_spo_match_key`. Both MUST strip the same set - `[^A-Za-z0-9]`,
# then uppercase - or SQL offers a candidate line the Python comparison then
# rejects (or, worse, hides one it would have accepted). The partial functional
# index in migration 324 is built over this exact expression, so the
# forward-matching filter is an index scan rather than a sequential one.
_SPO_KEY_STRIP_PATTERN = "[^A-Za-z0-9]"


def _spo_match_key_sql(column):
    return func.upper(func.regexp_replace(column, _SPO_KEY_STRIP_PATTERN, "", "g"))


# ---------------------------------------------------------------------------
# THE CONVENTION, and why it is this one.
#
# The quantity a picking line drew from its allocation is ``quantity_picked``.
# Every writer also sets ``quantity_expected`` to the same value on the rows it
# creates by splitting one receipt across allocations, so the GRN import and
# ``_create_grn_lines_with_spo_fifo`` produce the same rows for the same receipt
# (AC-FM-19 compares ``quantity_expected``); a receipt split across allocations
# therefore cannot carry an expected-vs-picked discrepancy, while an unsplit line
# still can.
#
# The READERS - this pool and ``compute_received_for_allocation`` - measure
# ``quantity_picked``, because it is the only column that is right on the rows
# ALREADY in the database. ``_create_grn_lines_with_spo_fifo`` used to leave the
# whole document quantity on a split's first chunk and write 0 on every later one,
# so a reader measuring ``quantity_expected`` charged the entire draw to the first
# allocation and nothing to the rest: capacity that had already been given up was
# offered again, and the allocation that got the first chunk reported a receipt it
# had not had. ``quantity_picked`` always held the per-chunk draw, so choosing it
# needs no backfill of the existing corpus.
# ---------------------------------------------------------------------------
_DRAWN_QUANTITY = func.coalesce(PickingLine.quantity_picked, 0)

#: A GRN in either state received nothing, so its lines hold no SPO capacity: `rejected`
#: always, `cancelled` since GRN-PULL-CRM (owner Q4 a, 2 Oct) - an AutoCount GRN cancelled
#: in the book lands `cancelled` and keeps its links for the audit trail.
NON_CONSUMING_GRN_STATUSES = ("rejected", "cancelled")


@dataclass
class PoolEntry:
    """One allocation's remaining capacity. ``available`` is drawn down IN PLACE,
    so a single pool can serve a whole group of lines without re-querying."""

    allocation_id: str
    warehouse_id: Optional[str]
    available: int


@dataclass(frozen=True)
class Draw:
    """A quantity taken from one allocation. ``allocation_id is None`` is the
    remainder no allocation covers - it still becomes a picking line, it just
    reads as stated-but-unmatched."""

    allocation_id: Optional[str]
    quantity: int


@dataclass(frozen=True)
class ForwardMatchResult:
    spo_number: str
    candidate_lines: int
    linked_lines: int
    created_lines: int


def build_allocation_pool(
    db: Session,
    *,
    product_id: str,
    spo_number: Optional[str],
    exclude_header_ids: Iterable[str] = (),
    company_id: Optional[str] = None,
) -> list[PoolEntry]:
    """The capacity available under ``spo_number`` for ``product_id``, FIFO.

    ``company_id`` confines the pool to one company. The session scope normally
    does that, but the ``X-API-Key`` principal resolves to a NULL scope ("all
    companies") and then adds no predicate at all, so without this an allocation
    belonging to one company is offered to another company's GRN line. Passing it
    is how the caller says which company's capacity it is asking about.

    Availability is COMPUTED, not read off ``spo_allocations.quantity_received``:

  - stored ``quantity_received`` is only written when a GRN is APPROVED, and an
      imported GRN normally is not, so a line already linked to an allocation
      would leave the stored figure at 0 and forward matching would hand the same
      capacity out a second time;
  - ``compute_received_for_allocation`` counts approved headers only, so it has
      the same hole.

    So each allocation's consumption is the sum of the DRAWN quantity (see the
    convention note above) over the picking lines linked to it, REGARDLESS of
    approval status - except lines on a REJECTED or CANCELLED GRN, which must not consume
    capacity (the same rule the forward-match candidate filter states) - plus
    whatever part of the stored receipt neither a picking line nor the statement
    (`stated_received`) explains.

    `stated_received` is a statement recorded on the allocation (AutoCount mirror
    push, supersede carry, retire freeze - see `app/models/procurement.py` around
    line 521), not a second receipt: it names the SAME GRN the pool is trying to
    place, minutes or days before or after the actual GRN import lands, and once
    that GRN's picking lines link it is explained twice over
    (PLAN-grn-link-ignores-mirror-received). So it is subtracted alongside the
    linked total, never added to what still consumes capacity - only a stored
    `quantity_received` that is neither stated nor linked is an unexplained write
    (an integration with no line behind it, real stock, AC-FM-28) and still
    consumes. Subtracting the linked total before taking that excess is what stops
    a re-import of an APPROVED GRN seeing an empty pool and unlinking its own
    lines.

    ``exclude_header_ids`` is what makes a re-import idempotent: a GRN never
    competes with itself. Across DIFFERENT GRNs the exclusion does not apply, so a
    second GRN correctly sees the first one's draw.
    """
    key = _spo_match_key(spo_number)
    if not key:
        return []

    allocation_query = db.query(SPOAllocation).filter(
        SPOAllocation.product_id == str(product_id),
        SPOAllocation.spo_number.isnot(None),
    )
    if company_id:
        allocation_query = allocation_query.filter(SPOAllocation.company_id == str(company_id))
    allocations = (
        # FIFO, tie-broken by id so the order is deterministic when two rows were
        # written in the same transaction and share a created_at.
        allocation_query
        .order_by(SPOAllocation.created_at.asc(), SPOAllocation.id.asc())
        .all()
    )
    matched = [a for a in allocations if _spo_match_key(a.spo_number) == key]
    entries = _measure(db, matched, exclude_header_ids=exclude_header_ids, company_id=company_id)
    return [entry for entry in entries if entry.available > 0]


def spo_line_candidates(
    db: Session,
    *,
    product_id: str,
    spo_number: Optional[str],
    company_id: str,
    exclude_header_ids: Iterable[str] = (),
) -> list[PoolEntry]:
    """Every line of ``spo_number`` carrying ``product_id``, in LINE order, with what each
    has left - the AutoCount GRN ingest's candidates (GRN-PULL-CRM plan 1.3).

    Same capacity arithmetic as ``build_allocation_pool`` (one ``_measure``), with two
    differences that come from AutoCount writing ONE GRN line per SPO line in the SPO's
    Seq order: the order is ``spo_line_number`` (the SPO ingest numbers new lines in Seq
    order; Seq itself is not stored), not age; and a line with nothing left is KEPT, so an
    over-receipt can still land on the line it belongs to.
    """
    if not company_id:
        # Security review N1: no company would read every company's lines.
        raise ValueError("spo_line_candidates needs the company the GRN belongs to")
    key = _spo_match_key(spo_number)
    if not key:
        return []
    query = db.query(SPOAllocation).filter(
        SPOAllocation.product_id == str(product_id),
        SPOAllocation.spo_number.isnot(None),
        _spo_match_key_sql(SPOAllocation.spo_number) == key,
    )
    query = query.filter(SPOAllocation.company_id == str(company_id))
    rows = query.order_by(
        SPOAllocation.spo_line_number.asc().nulls_last(),
        SPOAllocation.created_at.asc(),
        SPOAllocation.id.asc(),
    ).all()
    return _measure(db, rows, exclude_header_ids=exclude_header_ids, company_id=company_id)


def spo_number_known(db: Session, spo_number: Optional[str], *, company_id: str) -> bool:
    """Whether any line of ``spo_number`` (matched the upload's way) exists in the company."""
    key = _spo_match_key(spo_number)
    if not key:
        return False
    return db.query(
        db.query(SPOAllocation.id)
        .filter(SPOAllocation.company_id == str(company_id),
                SPOAllocation.spo_number.isnot(None),
                _spo_match_key_sql(SPOAllocation.spo_number) == key)
        .exists()
    ).scalar()


def _measure(
    db: Session,
    allocations: list[SPOAllocation],
    *,
    exclude_header_ids: Iterable[str],
    company_id: Optional[str],
) -> list[PoolEntry]:
    """What each allocation has left, in the order given (see ``build_allocation_pool``'s
    docstring for the arithmetic). A retired line nothing picks against is dropped."""
    if not allocations:
        return []

    allocation_ids = [str(a.id) for a in allocations]
    excluded = {str(header_id) for header_id in exclude_header_ids}

    line_qty = _DRAWN_QUANTITY
    excluded_qty = (
        case((PickingLine.picking_header_id.in_(excluded), line_qty), else_=0)
        if excluded
        else literal(0)
    )
    consumption = (
        db.query(
            PickingLine.spo_allocation_id,
            func.coalesce(func.sum(line_qty), 0),
            func.coalesce(func.sum(excluded_qty), 0),
        )
        .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
        .filter(
            PickingLine.spo_allocation_id.in_(allocation_ids),
            PickingHeader.picking_type == "goods_received",
            PickingHeader.picking_status.notin_(NON_CONSUMING_GRN_STATUSES),
        )
        .group_by(PickingLine.spo_allocation_id)
    )
    if company_id:
        consumption = consumption.filter(PickingLine.company_id == str(company_id))
    rows = consumption.all()
    linked = {str(row[0]): (int(row[1]), int(row[2])) for row in rows}

    entries: list[PoolEntry] = []
    for allocation in allocations:
        linked_all, linked_excluded = linked.get(str(allocation.id), (0, 0))
        if allocation.retired_at is not None and linked_all == 0:
            # D34 (SPO-XLSX-SUPERSEDE round 2): a retired line nothing picks
            # against is not capacity - an Excel row a push superseded without a
            # delete grant (its picks moved to the AutoCount lines) or a line
            # AutoCount stopped naming. FIFO is by age, so the old row would
            # otherwise be drawn before the lines that replaced it. A retired
            # line that still CARRIES picks stays in the pool (review B2): a
            # re-import of the GRN those picks came from excludes its own header
            # and must find the same allocation again, or it draws a second
            # copy of the receipt onto a live sibling.
            continue
        linked_other = linked_all - linked_excluded
        stated = int(allocation.stated_received or 0)
        external_received = max(0, int(allocation.quantity_received or 0) - stated - linked_all)
        consumed = linked_other + external_received
        entries.append(
            PoolEntry(
                allocation_id=str(allocation.id),
                warehouse_id=str(allocation.warehouse_id) if allocation.warehouse_id else None,
                available=max(0, int(allocation.allocated_quantity or 0) - consumed),
            )
        )
    return entries


def draw_fifo(
    pool: list[PoolEntry], *, warehouse_id: Optional[str], quantity: int
) -> list[Draw]:
    """Take ``quantity`` from ``pool``: same warehouse first, then any, then the
    uncovered remainder as a single trailing unlinked draw.

    The two passes are the import's long-standing rule - a receipt is far more
    likely to be against the allocation that named the same warehouse, and age
    only decides between equals. ``pool`` is mutated, so consecutive calls for the
    lines of one group cannot both draw the same capacity.
    """
    remaining = int(quantity or 0)
    if remaining <= 0:
        return []

    draws: list[Draw] = []
    for same_warehouse in (True, False):
        for entry in pool:
            if remaining <= 0:
                break
            if (entry.warehouse_id == warehouse_id) is not same_warehouse:
                continue
            if entry.available <= 0:
                continue
            take = min(remaining, entry.available)
            draws.append(Draw(allocation_id=entry.allocation_id, quantity=take))
            entry.available -= take
            remaining -= take

    if remaining > 0:
        draws.append(Draw(allocation_id=None, quantity=remaining))
    return draws


def forward_match_grn_lines_for_spo(
    db: Session, spo_number: Optional[str], *, company_id: Optional[str] = None
) -> ForwardMatchResult:
    """Link the GRN lines that were waiting for this SPO's allocations.

    The other half of the journey: the GRN landed first, its lines recorded what
    the sheet said, and now the allocation has arrived. The lines are placed with
    the same draw a fresh import would have made, so the upload order leaves no
    trace.

    Only lines that are UNLINKED, that STATE this SPO, and whose GRN is not
    ``rejected`` are candidates - a rejected receipt must not consume allocation
    capacity. ``draft`` IS included: that is the normal state of an imported GRN,
    and the state the import itself links in.

    ``company_id`` is the company of the allocation that triggered this run. Every
    caller passes it, because the ``X-API-Key`` principal runs under a NULL scope
    ("all companies") where the scope layer adds no predicate: without it the
    candidate query hands company B's lines to company A's allocations.
    """
    key = _spo_match_key(spo_number)
    if not key:
        return ForwardMatchResult(spo_number or "", 0, 0, 0)

    # ORM, never raw SQL: PickingLine and PickingHeader are CompanyScopedMixin and
    # the scope listener only applies to ORM queries.
    candidate_query = (
        db.query(PickingLine)
        .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
        .filter(
            PickingLine.spo_allocation_id.is_(None),
            PickingLine.spo_number_raw.isnot(None),
            _spo_match_key_sql(PickingLine.spo_number_raw) == key,
            PickingHeader.picking_type == "goods_received",
            PickingHeader.picking_status.notin_(NON_CONSUMING_GRN_STATUSES),
            # An AutoCount GRN's line is ONE row per AutoCount line (unique DtlKey) and is
            # linked by the GRN ingest's own resolver and waiting fill (GRN-PULL-CRM plan
            # 1.3); a FIFO draw here would split it into rows AutoCount never wrote.
            PickingHeader.doc_key.is_(None),
        )
    )
    if company_id:
        candidate_query = candidate_query.filter(PickingLine.company_id == str(company_id))
    candidates = (
        candidate_query
        # Oldest GRN first, and deterministic within it, so a re-run draws the
        # same capacity in the same order.
        .order_by(
            PickingHeader.created_at.asc(),
            PickingHeader.picking_number.asc(),
            PickingLine.created_at.asc(),
            PickingLine.id.asc(),
        )
        .all()
    )
    if not candidates:
        return ForwardMatchResult(str(spo_number), 0, 0, 0)

    groups: dict[tuple[str, str], list[PickingLine]] = {}
    for line in candidates:
        groups.setdefault(
            (str(line.picking_header_id), str(line.product_id)), []
        ).append(line)

    linked_lines = 0
    created_lines = 0
    for (header_id, product_id), lines in groups.items():
        # NO `exclude_header_ids` here, deliberately, and this is the one place the
        # two directions differ in how they CALL the pool (not in what it does).
        # The import excludes the GRN it is rewriting, because a re-import must not
        # see the rows it is about to replace as capacity somebody already took.
        # Forward matching only ever ADDS links to lines that have none, so a
        # linked line on this same header is a genuine prior draw - excluding it
        # would hand its capacity out a second time, and the second run over an
        # SPO would keep drawing what the first one already placed.
        pool = build_allocation_pool(
            db, product_id=product_id, spo_number=spo_number, company_id=company_id
        )
        if not pool:
            # Nothing to place these lines against. Touch nothing, so a second run
            # over the same SPO is free.
            continue

        for line in lines:
            # The drawn quantity is `quantity_picked` (see the convention note
            # above), falling back to `quantity_expected` when nothing has been
            # picked yet - the same rule `_create_grn_lines_with_spo_fifo` applies
            # when it draws for a GRN that only has expected quantities.
            quantity = int(line.quantity_picked or line.quantity_expected or 0)
            if quantity <= 0:
                continue
            # A row the OLD splitter wrote carries 0 here because its sibling chunk
            # carries the whole document quantity. Raising it to the drawn quantity
            # would state that quantity twice on the one GRN, and the allocation
            # would report a receipt bigger than the one that happened.
            states_its_own_expected = int(line.quantity_expected or 0) > 0
            draws = draw_fifo(
                pool,
                warehouse_id=str(line.source_warehouse_id) if line.source_warehouse_id else None,
                quantity=quantity,
            )
            if not draws or draws[0].allocation_id is None:
                # No allocation covered any of it. The line stays exactly as it is,
                # stated but unmatched.
                continue

            # The existing row takes the first draw and the rest become new rows on
            # the same header - the row shape a fresh import would have written,
            # which is what makes the two upload orders agree.
            line.spo_allocation_id = draws[0].allocation_id
            line.quantity_picked = draws[0].quantity
            # ONLY when the receipt was actually SPLIT. The convention above says a
            # split cannot carry an expected-vs-picked discrepancy, because the
            # split is the fact - but it also says an UNSPLIT line still can, and
            # rewriting `quantity_expected` on a single draw erases exactly that:
            # a line that expected 100 and received 60 came out expecting 60, and
            # the 40 that never arrived vanished along with its discrepancy.
            if states_its_own_expected and len(draws) > 1:
                line.quantity_expected = draws[0].quantity
            linked_lines += 1

            for draw in draws[1:]:
                db.add(
                    PickingLine(
                        picking_header_id=line.picking_header_id,
                        product_id=line.product_id,
                        source_warehouse_id=line.source_warehouse_id,
                        destination_warehouse_id=line.destination_warehouse_id,
                        uom_id=line.uom_id,
                        picked_condition=line.picked_condition or "good",
                        spo_number_raw=line.spo_number_raw,
                        spo_allocation_id=draw.allocation_id,
                        quantity_expected=draw.quantity if states_its_own_expected else 0,
                        quantity_picked=draw.quantity,
                        # Explicit, never left to the insert hook: under the NULL
                        # ("all companies") scope the hook stamps the INCUMBENT
                        # company, so a row split off another company's GRN would
                        # land in Sorento - invisible on the GRN it belongs to
                        # while still consuming that GRN's allocation.
                        company_id=line.company_id,
                    )
                )
                created_lines += 1
        db.flush()

    db.commit()

    if linked_lines or created_lines:
        # What every other stakeholder is told automatically: the allocation's
        # quantity_received / receipt_status and the inbound shipment's line
        # statuses now agree with the new links.
        from app.services.procurement_service import PickingHeaderService

        PickingHeaderService(db).sync_received_for_spo_number(spo_number)

    return ForwardMatchResult(
        spo_number=str(spo_number),
        candidate_lines=len(candidates),
        linked_lines=linked_lines,
        created_lines=created_lines,
    )


def forward_match_grn_lines_for_spo_best_effort(
    db: Session, spo_number: Optional[str], *, company_id: Optional[str] = None
) -> Optional[ForwardMatchResult]:
    """Forward matching as a POST-COMMIT side effect, which is the only way it is
    ever called.

    A side effect that runs after the main row has committed must not turn a
    successful write into a 500: the caller would retry, take the idempotent path,
    and never backfill the missed effect. So this catches, rolls the failed
    transaction back so the caller's session is usable, warns, and returns None.
    """
    try:
        return forward_match_grn_lines_for_spo(db, spo_number, company_id=company_id)
    except Exception:
        logger.warning(
            "Forward matching GRN lines for SPO %s failed; the allocation write stands",
            spo_number,
            exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            logger.warning("Rollback after a failed forward match also failed", exc_info=True)
        return None
