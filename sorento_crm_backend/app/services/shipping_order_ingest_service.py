"""Ingest DOCUMENTS pushed in by the ESB: shipping orders (D3/D5/D11, S3).

A shipping order has no header table. It is a GROUP of `spo_allocations` rows
sharing one `spo_number` (migration 420), so this is the one entity on the
ingest surface with no single `entity_id` to name in a verdict, and no
`integration_references` row to identify it by either: a push finds its rows
by `source_doc_ref` (AutoCount's DocKey), falling back to `spo_number` alone
for a document's first sync - exactly the way `DocumentIngestService._header`
adopts a v1 document by its number, without the reference lookup a header
row would otherwise give it.

Shares the ref/code/name/back-create ladder (`MasterRefResolver`) with
`DocumentIngestService` rather than duplicating it - both subclass it.
Everything else here is peculiar to a header-less document:

**A line still owns its identity.** AutoCount's DtlKey (`source_ref`) is the
upsert key within the document, same as a sales/purchase-order line - a line
keeps its row, and therefore its GRN links and order-link claims, across
every re-push.

**xlsx-era rows are adopted, not replaced (D11).** The purchase-history import
wrote thousands of `spo_allocations` rows before AutoCount owned any of them,
with no `source_ref` at all. The upload's OWN dedup key is `(product_id,
upper(location_code))` (`outstanding_import_service._spo_line_plans`), so
adoption matches on that same pair rather than on `warehouse_id` - the book
routinely names a location this system holds no warehouse row for. Exact
first (that pair plus `warehouse_id`-or-None and outstanding, all agreeing),
then the pair alone when exactly one candidate remains, then position
(`spo_line_number` order) when nothing else discriminates - the same
three-pass shape `document_ingest_service._adopt_lines` uses, with the roles
of "coarse, durable" and "tight, secondary" key components swapped to match
what a shipping-order row actually carries.

**A leftover line closes, it never deletes.** Same reason as a document line
(A3): `scm.order_link_claim.spo_allocation_id` and a GRN's picking line point
at the row's id, and removing it would either cascade a real receipt away or
orphan a pairing already made. `line_status='closed'` takes it out of
`scm.on_order_v` while leaving whatever points at it intact.

**Status is validated, not stored.** The row has no header column for it -
only `line_status` (`open`/`closed`), derived from quantities the same way
`outstanding_import_service._write_spo_lines` derives it. `cancelled` is the
one exception: it forces every line closed regardless of what is still
outstanding, since a cancelled shipment covers no demand however much of it
had already arrived. Every other canonical word is accepted for its
vocabulary check alone and changes nothing about how a line's own status is
computed.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.inventory import Warehouse
from app.models.procurement import SPOAllocation, Supplier
from app.models.product import Product
from app.schemas.canonical_documents import CanonicalShippingOrder
from app.services.document_ingest_service import SOURCE_SYSTEM
from app.services.integration_reference_service import (
    IntegrationReferenceService,
    ReferenceConflict,
)
from app.services.master_ingest_service import (
    INTERNAL_ERROR_MESSAGE,
    IngestOutcome,
    IngestResult,
    MissingReference,
    RecordResult,
    _field_errors,
    integrity_conflict_errors,
)
from app.services.master_ref_resolver import MasterRefResolver, dedupe_warnings
from app.services.rules import shipping_order_rules
from app.services.rules.document_rules import derive_document_status
from app.services.scm import order_link_service
from app.services.scm.outstanding_import_service import DEFAULT_PO_CURRENCY

logger = logging.getLogger(__name__)

# The entity name this service answers for. Read by the route so the one
# ingest endpoint dispatches to this service alongside `MasterIngestService`
# and `DocumentIngestService`, and by `deletion_service` for the same reason.
SHIPPING_ORDERS_ENTITY = "shipping_orders"
SHIPPING_ORDER_ENTITIES = frozenset({SHIPPING_ORDERS_ENTITY})

LINE_OPEN = "open"
LINE_CLOSED = "closed"
RECEIPT_PENDING = "pending"
RECEIPT_FULLY_RECEIVED = "fully_received"

# Canonical vocabulary check only - the row carries no header status column to
# map onto, so nothing here is ever stored verbatim. `cancelled` is the one
# word that changes what gets WRITTEN (every line forced closed); the other
# four are accepted and otherwise inert - a line's own `line_status` always
# comes from its quantities, never from this word.
STATUS_WORDS = frozenset({"open", "partial", "fulfilled", "closed", "cancelled"})

# D6/D7 (S3) verdict warnings - same fixed vocabulary convention as
# `master_ref_resolver`'s `WARN_*` constants.
WARN_CONTAINER_UNRESOLVED = "container_unresolved"
WARN_RECEIVED_LOCKED = "received_locked"
# D26a/D30 (spo-xlsx-supersede, review round): a first-push supersede that had
# to merge two containers into one line-set, and one that could not remove the
# rows it superseded because the calling principal holds no `.delete` grant.
WARN_SHIPMENT_MERGED = "shipment_merged"
WARN_SUPERSEDED_CLOSED_ONLY = "superseded_closed_only"


def _round_qty(value: Optional[Decimal]) -> int:
    """A canonical line quantity as a whole number, half-up (S3 review fix).

    `allocated_quantity`/`quantity_received` are INTEGER columns; the ESB's
    own figures are `Decimal` and occasionally fractional (a pack count, a
    weight-based line). `int(round(x))` is the exact technique
    `outstanding_import_service._spo_quantities` already uses for the same
    two columns, reused here rather than restated so the two channels that
    write this table cannot round differently.
    """
    return int(round(float(value or 0)))


def _document_status(rows: list[SPOAllocation]) -> Optional[str]:
    """Live-derived canonical status for a read-back (AC-V3-6).

    There is no header status to read, so the answer is computed off what the
    rows currently show. `closed` first (S5 review fix): every row closed -
    the whole-document shape a `cancelled` push (or a fully-superseded
    document) leaves behind - must read back `closed`, never re-derived as
    `open`/`fulfilled` from quantities that are no longer the reason the
    lines are closed. Short of that, the same three-way ladder a document's
    OWN status starts from before cancellation enters it: nothing received
    is `open`, everything received is `fulfilled`, anything in between is
    `partial`.
    """
    if not rows:
        return None
    if all(r.line_status == LINE_CLOSED for r in rows):
        return "closed"
    ordered = sum(int(r.allocated_quantity or 0) for r in rows)
    received = sum(int(r.quantity_received or 0) for r in rows)
    if ordered > 0 and received >= ordered:
        return "fulfilled"
    if received > 0:
        return "partial"
    return "open"


@dataclass(frozen=True)
class _Verdict:
    outcome: IngestOutcome
    warnings: list[str]
    line_counts: dict[str, int]


class ShippingOrderIngestService(MasterRefResolver):
    """Same constructor and ``ingest()``/``RecordResult`` contract as its siblings.

    Subclasses `MasterRefResolver` for the ref/code/name/back-create ladder,
    shared with `DocumentIngestService` rather than duplicated.
    """

    def __init__(
        self,
        db: Session,
        integration_id: Optional[str],
        *,
        company_id: str,
        may_delete: bool = False,
    ):
        super().__init__(db, integration_id, company_id=company_id)
        # D30: whether the calling principal holds
        # `DELETE_PERMISSIONS["shipping_orders"]` (`scm.shipping_orders.delete`).
        # A BOOLEAN, resolved by the route (`app.api.v1.external.ingest`,
        # `_principal_may_delete`) through the same `UserPermissionService`
        # every other guard on that surface uses - the service must not import
        # FastAPI or re-derive a principal of its own. D30a: the default is
        # FALSE - a caller with no opinion at all gets the safe half (rows
        # closed and annotated, never removed), and a caller entitled to
        # delete says so explicitly (the route passes the resolved grant, the
        # dedupe script passes True).
        self.may_delete = may_delete
        # D7 (S3): SPO numbers this batch touched, read by the route's
        # post-write forward-match hook (`app.api.v1.external.ingest
        # ._run_document_hooks`) after commit - same role
        # `DocumentIngestService.so_numbers` plays for its own hook.
        self.spo_numbers_touched: set[str] = set()
        # D27a: shipments whose allocations this batch changed, read by the
        # route's post-commit hook (`_run_shipping_order_shipment_refresh_hook`)
        # so `InboundShipmentService.refresh_shipment_line_statuses` runs the
        # way it does for every other writer of allocations. NOT called inside
        # `_apply_scoped`: that method runs in a per-record SAVEPOINT and the
        # refresh commits, which would land half a batch and defeat the dry-run
        # rollback.
        self.shipment_ids_touched: set[str] = set()
        # S5 (`PLAN-oi-replan-received-links.md`): every SPO allocation whose
        # `from_so_line_ref` changed on THIS push - the same shape and the same
        # reader (`follow_book_repairing`) as `DocumentIngestService.ref_moves`.
        # `_write_row` captures an in-place move; `_supersede_xlsx_rows` records
        # the superseded (xlsx-era) row's ref against the NEW allocation it made.
        #
        # AC-RL-49 (security review, 17 Sep): published ONLY once `_apply_scoped`
        # has fully succeeded, the same "stage per record, publish on success"
        # rule `DocumentIngestService._record_hook_state` follows and for the
        # same reason - a plain Python list is not part of the record's own
        # SAVEPOINT, so an append made mid-`_apply_scoped` would otherwise
        # survive a later failure in the SAME record's own rollback.
        self.ref_moves: list[dict[str, Optional[str]]] = []
        self._pending_ref_moves: list[dict[str, Optional[str]]] = []
        # S2 (`PLAN-oi-follow-book-chain.md`, AC-FB-22/25): every allocation this
        # push WROTE (created, updated or adopted - anything `_write_row` ran
        # over), so the route's hook can resolve each one's own `from_so_line_
        # ref`, or the ref of the purchase-order line its `from_po_line_ref`
        # names, to an order-inquiry row. Same "stage per record, publish on
        # success" rule as `ref_moves` above, for the same reason.
        self.written_spo_allocation_ids: set[str] = set()
        self._pending_spo_allocation_ids: list[str] = []

    # --------------------------------------------------------------- the batch
    def ingest(
        self, entity_type: str, records: list[dict], *, dry_run: bool = False
    ) -> IngestResult:
        result = IngestResult(dry_run=dry_run)
        try:
            for raw in records:
                result.records.append(self._ingest_one(raw))
        finally:
            if dry_run:
                # In a finally, so an error mid-batch cannot leave a partially
                # applied preview in the session for whatever commits next.
                self.db.rollback()
        # Security review (blocker 2): the forward-match batch-end sweep used
        # to run HERE, before the route's own `db.commit()`.
        # `forward_match_grn_lines_for_spo` commits on success and rolls back
        # on failure, so one exception mid-batch discarded every not-yet-
        # committed record of the batch while the route still answered 200
        # with entity ids. It now runs from the route's post-commit hook slot
        # (`app.api.v1.external.ingest._run_document_hooks`, dispatched off
        # `self.spo_numbers_touched`) instead, the same slot the SO/PO hooks
        # use - this service only RECORDS which SPO numbers were touched.
        return result

    def _ingest_one(self, raw: dict) -> RecordResult:
        source_ref = raw.get("source_ref") if isinstance(raw, dict) else None

        try:
            payload = CanonicalShippingOrder(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors=_field_errors(exc),
            )
        except TypeError as exc:
            logger.warning(
                "ingest.document_malformed entity=%s source_ref=%s",
                SHIPPING_ORDERS_ENTITY,
                source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

        if payload.status is None:
            # D20 (S3, AC-P3-6): absent derives via the same shared function
            # the SO/PO side uses - all allocations received = closed, else
            # open. There is no header row to read an EXISTING status off
            # (D3), so `existing` is always `None` here; `cancelled` is
            # therefore never derived, only ever explicitly sent.
            line_dicts = [
                {"qty_ordered": line.qty_ordered, "qty_received": line.qty_received}
                for line in payload.lines
            ]
            status_word = derive_document_status(line_dicts, None)
        else:
            status_word = (payload.status or "").strip().lower()
            if status_word not in STATUS_WORDS:
                return RecordResult(
                    source_ref=payload.source_ref,
                    outcome=IngestOutcome.FAILED,
                    errors={
                        "status": (
                            f"unknown status {payload.status!r}; expected one of: "
                            f"{', '.join(sorted(STATUS_WORDS))}"
                        )
                    },
                )
        force_closed = status_word == "cancelled"

        # One document per savepoint. Without it a failed flush poisons the
        # session and every later record in the file fails too.
        savepoint = self.db.begin_nested()
        try:
            verdict = self._apply(payload, force_closed)
            savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=verdict.outcome,
                # No header row exists for a shipping order (D3) - the verdict
                # cannot name one.
                entity_id=None,
                warnings=verdict.warnings,
                # Same shape as the sales / purchase order verdict: the fixed
                # keys are always present (zero included) so an ESB reading
                # `lines.created` never meets a missing key; only the optional
                # `superseded` count appears when a supersede happened (D27).
                lines=verdict.line_counts,
            )
        except MissingReference as exc:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.RETRYABLE,
                errors={exc.field_name: f"not found: {exc.code}"},
            )
        except ReferenceConflict as exc:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={exc.field_name: str(exc)},
            )
        except IntegrityError as exc:
            # Fix round 4, BUG B: a unique-constraint race (two companies, or a
            # concurrent push of the same number) - named by constraint, never
            # by `str(exc)`'s full SQL statement.
            savepoint.rollback()
            logger.warning(
                "ingest.integrity_conflict entity=%s source_ref=%s",
                SHIPPING_ORDERS_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors=integrity_conflict_errors(exc),
            )
        except Exception:  # noqa: BLE001 - one document's failure, not the file's
            savepoint.rollback()
            # SEC3: never echo a non-domain exception's own message - it
            # routinely quotes SQL, a table/column name or a raw UUID.
            logger.warning(
                "ingest.document_failed entity=%s source_ref=%s",
                SHIPPING_ORDERS_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    # ------------------------------------------------------------ one document
    def _apply(self, payload: CanonicalShippingOrder, force_closed: bool) -> _Verdict:
        """Pins `company_scope` for the WHOLE record (security review advisory
        a / S9), same reason and same shape as `MasterIngestService._apply`:
        the ladder resolves references and the adoption pass runs ordinary
        ORM queries against company-scoped tables, and without this those
        queries are filtered by whatever the ambient session scope happens to
        be - which the `X-API-Key` principal's `None`/all-companies scope
        never narrows on its own.
        """
        with company_scope(self.db, frozenset({self.company_id})):
            return self._apply_scoped(payload, force_closed)

    def _apply_scoped(self, payload: CanonicalShippingOrder, force_closed: bool) -> _Verdict:
        # AC-RL-49: fresh per record - THIS record's own ref moves, staged until
        # this method returns successfully, never a previous (possibly failed)
        # record's leftover.
        self._pending_ref_moves = []
        self._pending_spo_allocation_ids = []
        # S2 review fix: refused before anything else - a conflicting OPEN
        # claim on this spo_number is a fact about the DOCUMENT, not about
        # any one reference on it, so it is checked before the ladder runs.
        self._guard_spo_number_conflict(payload)
        # D28d: the guard has just established that any OTHER DocKey on this
        # number is fully closed - the delete-and-recreate path. Those rows
        # are history now, so they are retired here rather than left looking
        # like live fully received lines of this document.
        self._retire_other_dockey_rows(payload)

        # EVERYTHING is resolved before ANYTHING is written - same rule as
        # `DocumentIngestService._apply`, for the same reason: an unresolved
        # reference has to leave the database exactly as it found it.
        warnings: list[str] = []
        supplier_id = self._resolve_master(
            model=Supplier,
            ref_field="supplier_ref",
            ref=payload.supplier_ref,
            code_field="supplier_code",
            code=payload.supplier_code,
            name=payload.supplier_name,
            warnings=warnings,
        )
        currency = payload.currency or DEFAULT_PO_CURRENCY
        # D6 (S3): cleaned ONCE per document - every line of it stores the
        # same container, so there is no reason to re-clean per line.
        container_number = (
            shipping_order_rules.extract_container_number(payload.container_number)
            if payload.container_number
            else None
        )

        line_values = [
            self._line_values(payload, line, index, supplier_id, currency, warnings)
            for index, line in enumerate(payload.lines)
        ]

        rows = self._existing_rows(payload)
        # D25a: eligibility is decided per `(product_id, upper(location_code))`
        # GROUP, not per document - a group that already holds a DtlKey is
        # ESB-era (S4 applies there), one that holds none is still xlsx-era
        # whichever push first names it. An EMPTY key set means no DtlKey
        # exists on this number at all, which is also the D25 first-push test
        # the verdict reads: what lands then is the AutoCount line-set, not an
        # update of the rows the upload left behind.
        esb_group_keys = self._esb_group_keys(payload)
        outcome = (
            IngestOutcome.CREATED
            if not esb_group_keys or not rows
            else IngestOutcome.UPDATED
        )
        by_ref, pool, already_closed, supersede_pool = self._split_rows(
            rows, esb_group_keys=esb_group_keys
        )

        counts = {"adopted": 0, "created": 0, "updated": 0, "deleted": 0, "cancelled": 0}

        unmatched: list[dict[str, Any]] = []
        for values in line_values:
            row = by_ref.pop(values["source_ref"], None)
            if row is not None:
                # D7 (S3): a received quantity is a fact of what physically
                # arrived, and an ESB push cannot pull it out from under a
                # GRN that already drew against it - the line is left
                # exactly as it was, and the record still lands (rest of the
                # document unaffected).
                guard = shipping_order_rules.received_guard(
                    row, values["allocated_quantity"], values["quantity_received"]
                )
                if guard == shipping_order_rules.GUARD_RECEIVED_LOCKED:
                    warnings.append(WARN_RECEIVED_LOCKED)
                    # Nothing is recorded here, `stated_received` included
                    # (D28c ruling 1): the line is refused precisely because
                    # the push states LESS than already arrived, and the max
                    # rule would keep the higher stored statement anyway.
                    continue
                # S5: capture the move BEFORE `_write_row` overwrites `row`'s
                # own value - the in-place repush half of AC-RL-42. Presence,
                # never truthiness (`"from_so_line_ref" in values`), so an
                # explicit `null` is captured too.
                if "from_so_line_ref" in values:
                    old_ref = row.from_so_line_ref
                    new_ref = values["from_so_line_ref"]
                    if old_ref != new_ref:
                        # AC-RL-49: staged, not published - see `_pending_ref_moves`'s
                        # own docstring in `__init__`.
                        self._pending_ref_moves.append(
                            {
                                "target_kind": "spo",
                                "target_id": str(row.id),
                                "old_ref": old_ref,
                                "new_ref": new_ref,
                            }
                        )
                self._write_row(
                    row, values, force_closed,
                    container_number=container_number, warnings=warnings,
                )
                counts["updated"] += 1
            else:
                unmatched.append(values)

        if supersede_pool:
            # D25/D25a/D26/D26a/D27, before adoption: an xlsx-era group is
            # REPLACED by the pushed line-set, its receipt carried across the
            # group and its links moved. What comes back is the rows the plan
            # left alone - a group AutoCount named no line for (D27), or one
            # whose incoming total undercuts its receipt (D26a) - handed to
            # `already_closed`, the bucket that reaches the leftover sweep
            # WITHOUT becoming an adoption candidate: the supersede has
            # already ruled on those rows, and pass 3 (position only) would
            # otherwise claim one for an unrelated product's line.
            already_closed.extend(
                self._supersede_xlsx_rows(
                    payload, unmatched, supersede_pool, counts, force_closed,
                    container_number=container_number, warnings=warnings,
                    fallback_blocked_products={key[0] for key in esb_group_keys},
                )
            )

        if unmatched and pool:
            # D25a: `pool` now holds only rows the supersede is not entitled
            # to touch - a ref-less row from the CRM UI / n8n (source_system
            # NULL) or an open one in a group that already carries a DtlKey -
            # so adoption still runs for them and can run in the SAME push as
            # a supersede of a different group.
            #
            # D25b: passes 1 and 2 only, in that case. Both key on
            # (product, location), so they cannot mistake one product's row
            # for another's; pass 3 keys on POSITION alone and fires whenever
            # "the remaining counts agree" - and after a supersede has
            # consumed its own lines, the counts that remain are whatever is
            # left over, so an unrelated CRM / n8n row is exactly what it
            # would pair the leftover line with (AC-X29).
            self._adopt_lines(
                unmatched, pool, counts, force_closed,
                container_number=container_number, warnings=warnings,
                allow_positional="superseded" not in counts,
            )

        # S1 review fix: the NEXT number is the highest across every row this
        # `spo_number` has EVER carried, not just the rows THIS DocKey's own
        # query matched (`rows`, above, deliberately excludes another
        # DocKey's rows). A delete-and-recreate under a fresh DocKey - the
        # old one's rows now all closed, so S2's guard let this push through
        # - must not reuse a line number the retired DocKey already claimed.
        next_number = self._max_line_number(payload)
        for values in unmatched:
            next_number += 1
            row = SPOAllocation(
                id=str(uuid.uuid4()),
                company_id=self.company_id,
                spo_number=payload.spo_number,
                spo_line_number=next_number,
            )
            self.db.add(row)
            self._write_row(
                row, values, force_closed,
                container_number=container_number, warnings=warnings,
            )
            counts["created"] += 1

        # A leftover row - the payload no longer names it - is ALWAYS closed,
        # never deleted (D3/D11), unlike a document's own line sweep. A
        # shipping order's ref-less pool routinely holds rows another push
        # will restate later, and a claim or a picking line can point at ANY
        # of them; explicit removal is the deletion endpoint's job
        # (`DeletionService._delete_shipping_order`), not a side effect of a
        # re-push that simply stopped naming a line this time. `already_closed`
        # (S4) rows fall through here unchanged - they were excluded only
        # from adoption CANDIDACY, not from this sweep.
        retired_refs = list(by_ref.values())
        for row in [*retired_refs, *pool, *already_closed]:
            row.line_status = LINE_CLOSED
            counts["cancelled"] += 1
        for row in retired_refs:
            # D28d + D28c, for a REF row only: this push no longer names the
            # line, so it is RETIRED - marked as such (the group recompute
            # skips a retired row entirely, so it can neither take a share of
            # a sibling's GRN nor be reopened when one is deleted) and its
            # receipt is FROZEN as stated (belt and braces: even if some
            # future path did write to it, a line the GRN alone had received
            # cannot fall below the figure it was retired with, so it stays
            # closed). A sibling this push DOES still name is unaffected and
            # behaves per AC-X35. The ref-less `pool` / `already_closed` rows
            # are deliberately untouched here: they carry no DtlKey, so they
            # are not AutoCount lines and never enter the group recompute.
            if (row.source_system or "") != shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM:
                continue
            frozen = max(int(row.stated_received or 0), int(row.quantity_received or 0))
            if frozen > 0:
                row.stated_received = frozen
            if row.retired_at is None:
                row.retired_at = datetime.now(timezone.utc)
        self.db.flush()
        self._write_order_link_claims(payload)
        self.spo_numbers_touched.add(payload.spo_number)
        # AC-RL-49: THIS record has now fully succeeded (everything above this
        # point in `_apply_scoped` already ran without raising) - its own staged
        # ref moves are promoted to the batch-level list the route's hook reads,
        # and never before.
        if self._pending_ref_moves:
            self.ref_moves.extend(self._pending_ref_moves)
            self._pending_ref_moves = []
        if self._pending_spo_allocation_ids:
            self.written_spo_allocation_ids.update(self._pending_spo_allocation_ids)
            self._pending_spo_allocation_ids = []
        return _Verdict(outcome=outcome, warnings=dedupe_warnings(warnings), line_counts=counts)

    def _write_order_link_claims(self, payload: CanonicalShippingOrder) -> None:
        """V4 (plan section 2.5), the shipping-order side of
        `DocumentIngestService._write_order_link_claims` - see that
        docstring for the shared rule. Runs after every row is flushed, so
        it queries by `source_doc_ref` rather than holding onto row objects
        built mid-`_apply` (some are new, some adopted, some merely updated).
        The claim-writing loop itself is `order_link_service
        .write_claims_for_lines` (S7 dedup), shared with
        `DocumentIngestService`'s own line claims.

        V5 (AutoCount linkage widen): a line naming the EXACT sales-order
        line it was raised for, via `from_so_line_ref`, resolves through
        `order_link_service.write_line_ref_claims` - see
        `DocumentIngestService._write_order_link_claims` for the shared
        docstring, and `order_link_service.write_line_ref_claims`'s own for
        the resolution rule.

        CALL ORDER (B1 review fix): `write_line_ref_claims` runs FIRST, same
        rule and same reason as `DocumentIngestService`'s own call site - see
        its docstring.
        """
        number_wanted = [
            (line.source_ref, [n for n in (line.from_so_numbers or []) if n])
            for line in payload.lines
            if getattr(line, "from_so_numbers", None)
        ]
        ref_wanted = [
            (line.source_ref, line.from_so_line_ref)
            for line in payload.lines
            if getattr(line, "from_so_line_ref", None)
        ]
        if not number_wanted and not ref_wanted:
            return

        refs = {source_ref for source_ref, _ in number_wanted} | {
            source_ref for source_ref, _ in ref_wanted
        }
        rows = (
            self.db.query(SPOAllocation)
            .filter(
                SPOAllocation.company_id == self.company_id,
                SPOAllocation.source_doc_ref == payload.source_ref,
                SPOAllocation.source_ref.in_(list(refs)),
            )
            .all()
        )
        # N1 review fix: one row/product-code index, shared by both calls
        # below instead of each running its own identical `Product` query.
        index = order_link_service.index_claim_rows(self.db, rows)
        order_link_service.write_line_ref_claims(
            self.db,
            company_id=self.company_id,
            document_number=payload.spo_number,
            rows=rows,
            wanted=ref_wanted,
            id_attr="spo_allocation_id",
            index=index,
        )
        order_link_service.write_claims_for_lines(
            self.db,
            company_id=self.company_id,
            document_number=payload.spo_number,
            rows=rows,
            wanted=number_wanted,
            id_attr="spo_allocation_id",
            index=index,
        )

    def _guard_spo_number_conflict(self, payload: CanonicalShippingOrder) -> None:
        """S2 review fix: refuse a push whose `spo_number` still has OPEN rows
        under a DIFFERENT DocKey - mirrors `DocumentIngestService._header`'s
        "two AutoCount documents claiming one Sorento order" guard for a
        document adopted by number. Rows that are all CLOSED under an old
        DocKey do NOT block - that is the delete-and-recreate path, where a
        fresh DocKey continues a number a since-retired DocKey used - they
        only count toward `_max_line_number` below.
        """
        conflict = (
            self.db.query(SPOAllocation.id)
            .filter(
                SPOAllocation.company_id == self.company_id,
                SPOAllocation.spo_number == payload.spo_number,
                SPOAllocation.line_status == LINE_OPEN,
                SPOAllocation.source_doc_ref.isnot(None),
                SPOAllocation.source_doc_ref != payload.source_ref,
            )
            .first()
        )
        if conflict is not None:
            raise ReferenceConflict(
                f"spo_number {payload.spo_number!r} is already linked to another source",
                field_name="spo_number",
            )

    def _retire_other_dockey_rows(self, payload: CanonicalShippingOrder) -> None:
        """Retire the rows a DIFFERENT DocKey left on this `spo_number` (D28d).

        Reached only after `_guard_spo_number_conflict` has passed, which means
        every such row is CLOSED: the document was deleted and re-created under
        a fresh DocKey, so the old key's lines are history. They must not stay
        indistinguishable from this document's own fully received lines -
        `_autocount_group_members` would take them back into the
        `(product, location)` group, hand them a Seq-order share of a sibling's
        GRN, and reopen them the day that GRN is deleted (the reviewer's 58
        open units against a 29-unit order).

        Queried explicitly because `_existing_rows` excludes them BY DESIGN
        (it matches this DocKey, or the ref-less rows of the number) - which is
        exactly why nothing had ever marked them. Idempotent: an existing
        `retired_at` is left alone, and the receipt freeze only ever raises the
        stated floor.
        """
        rows = (
            self.db.query(SPOAllocation)
            .filter(
                SPOAllocation.company_id == self.company_id,
                SPOAllocation.spo_number == payload.spo_number,
                SPOAllocation.source_system == shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM,
                SPOAllocation.source_doc_ref.isnot(None),
                SPOAllocation.source_doc_ref != payload.source_ref,
            )
            .all()
        )
        if not rows:
            return
        now = datetime.now(timezone.utc)
        for row in rows:
            frozen = max(int(row.stated_received or 0), int(row.quantity_received or 0))
            if frozen > 0:
                row.stated_received = frozen
            if row.retired_at is None:
                row.retired_at = now
        self.db.flush()
        logger.info(
            "ingest.spo_dockey_retired spo_number=%s doc_ref=%s retired=%d",
            payload.spo_number,
            payload.source_ref,
            len(rows),
        )

    def _existing_rows(self, payload: CanonicalShippingOrder) -> list[SPOAllocation]:
        """This document's rows: by DocKey, or by DocNo when none carries one yet."""
        return (
            self.db.query(SPOAllocation)
            .filter(
                SPOAllocation.company_id == self.company_id,
                or_(
                    SPOAllocation.source_doc_ref == payload.source_ref,
                    and_(
                        SPOAllocation.spo_number == payload.spo_number,
                        SPOAllocation.source_doc_ref.is_(None),
                    ),
                ),
            )
            .all()
        )

    def _max_line_number(self, payload: CanonicalShippingOrder) -> int:
        """S1 review fix: the highest `spo_line_number` across EVERY row this
        `spo_number` has ever carried in this company - not just the rows
        `_existing_rows` matched for THIS DocKey, which deliberately excludes
        another DocKey's rows even when S2's guard has let them stay (all
        closed, under a retired DocKey).
        """
        return (
            self.db.query(func.max(SPOAllocation.spo_line_number))
            .filter(
                SPOAllocation.company_id == self.company_id,
                SPOAllocation.spo_number == payload.spo_number,
            )
            .scalar()
            or 0
        )

    def _esb_group_keys(
        self, payload: CanonicalShippingOrder
    ) -> set[shipping_order_rules.SupersedeKey]:
        """The `(product, warehouse-or-location)` groups of this `spo_number`
        that already carry a DtlKey (D25a, keyed per D25c).

        Asked as its own query rather than read off `_existing_rows`: that
        query deliberately excludes another DocKey's rows (S1/S2), and a ref
        row left by a since-retired DocKey is exactly the evidence that its
        group is no longer xlsx-era. An empty result also answers D25's own
        document-level question (no DtlKey anywhere on this number yet), which
        is what the verdict's `created` reads.
        """
        rows = (
            self.db.query(
                SPOAllocation.product_id,
                SPOAllocation.warehouse_id,
                SPOAllocation.location_code,
            )
            .filter(
                SPOAllocation.company_id == self.company_id,
                SPOAllocation.spo_number == payload.spo_number,
                SPOAllocation.source_ref.isnot(None),
            )
            .all()
        )
        return {
            key
            for product_id, warehouse_id, location_code in rows
            # EVERY identity the ref row answers to (D25c): a ref row resolves
            # both halves, an Excel row beside it may carry only one, and S4's
            # exclusion must hold whichever way that row is keyed.
            for key in shipping_order_rules.supersede_match_keys(
                product_id, warehouse_id, location_code
            )
        }

    def _split_rows(
        self,
        rows: list[SPOAllocation],
        *,
        esb_group_keys: Optional[set] = None,
    ) -> tuple[
        dict[str, SPOAllocation],
        list[SPOAllocation],
        list[SPOAllocation],
        list[SPOAllocation],
    ]:
        """`(by_ref, pool, already_closed, supersede_pool)`.

        A second row sharing one `source_ref` cannot happen - the partial
        unique index on `(company_id, source_ref)` forbids it - so there is
        no "duplicate ref" bucket to defend against here.

        `already_closed` (S4 review fix): a ref-less row this system already
        closed - by an earlier absence, or the deletion endpoint - is not a
        live Excel-era adoption candidate any more; matching a NEW DtlKey onto
        it would resurrect demand that was correctly retired. It still flows
        into `_apply`'s final leftover sweep unchanged.

        `supersede_pool` (D25/D25a/D25c): an Excel-era row - ref-less, and
        `source_system` either `scm_upload` or NULL, since all three writers
        of those rows load an aggregate - whose `(product, warehouse-or-
        location)` group carries no DtlKey yet, open OR closed. Those rows are
        the Excel-era representation of that group and are superseded by the
        AutoCount line-set instead of adopted: adoption pairs ONE row with ONE
        line, and an aggregate stands for N, which neither adoption nor S4's
        exclusion can express.

        A row with a `source_ref` keeps its own bucket, and S4 still draws the
        other line: once a group carries a DtlKey it is ESB-era, where S4 was
        earned - a closed ref-less row beside a ref row was retired on
        purpose, so it goes to `already_closed`, not to the supersede pool.
        """
        keys = esb_group_keys or set()
        by_ref: dict[str, SPOAllocation] = {}
        pool: list[SPOAllocation] = []
        already_closed: list[SPOAllocation] = []
        supersede_pool: list[SPOAllocation] = []
        for row in rows:
            if row.source_ref:
                by_ref[row.source_ref] = row
                continue
            group_key = shipping_order_rules.supersede_group_key(
                row.product_id, row.warehouse_id, row.location_code
            )
            if shipping_order_rules.is_xlsx_era_row(row) and group_key not in keys:
                supersede_pool.append(row)
            elif row.line_status == LINE_CLOSED:
                already_closed.append(row)
            else:
                pool.append(row)
        return by_ref, pool, already_closed, supersede_pool

    def _line_values(
        self,
        payload: CanonicalShippingOrder,
        line: Any,
        index: int,
        supplier_id: Optional[str],
        currency: str,
        warnings: list[str],
    ) -> dict[str, Any]:
        product_id = self._resolve_master(
            model=Product,
            ref_field=f"lines.{index}.product_ref",
            ref=line.product_ref,
            code_field=f"lines.{index}.product_code",
            code=line.product_code,
            name=line.product_name,
            warnings=warnings,
        )
        warehouse_id = self._resolve_master(
            model=Warehouse,
            ref_field=f"lines.{index}.warehouse_ref",
            ref=line.warehouse_ref,
            code_field=f"lines.{index}.warehouse_code",
            code=line.warehouse_code,
            name=None,
            warnings=warnings,
        )
        location_code = self._location_code(warehouse_id, line.warehouse_code)

        # S3 review fix: round, half-up, exactly the way
        # `outstanding_import_service._spo_quantities` does for the same two
        # INTEGER columns - `int(Decimal("10.6"))` truncates to 10, silently
        # dropping outstanding demand a book that states a fractional pack
        # count actually named. Outstanding is computed off the ROUNDED
        # whole numbers, same as `_spo_quantities`'s own caller does.
        ordered = _round_qty(line.qty_ordered)
        received = _round_qty(line.qty_received)
        outstanding = ordered - received

        values = {
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            "location_code": location_code,
            "allocated_quantity": ordered,
            "quantity_received": received,
            "unit_cost": line.unit_cost,
            # A line's own date, when sent, is more specific than the
            # document's - falling back to it keeps every row dated even when
            # only the header states one.
            "expected_date": line.expected_date or payload.expected_date,
            "receipt_status": RECEIPT_PENDING if outstanding > 0 else RECEIPT_FULLY_RECEIVED,
            "line_status": LINE_OPEN if outstanding > 0 else LINE_CLOSED,
            "source_system": SOURCE_SYSTEM,
            "source_ref": line.source_ref,
            "source_doc_ref": payload.source_ref,
            "supplier_id": supplier_id,
            "issue_date": payload.issue_date,
            "currency": currency,
            # AutoCount's Seq (D11), position only - popped before persistence
            # by `_write_row`. No column exists for it on this table either.
            "line_number": getattr(line, "line_number", None),
        }
        # V5 (AutoCount linkage widen, ingest-contract-2-2-so-links): raw
        # pass-through onto `spo_allocations`, never resolved into an id
        # (see the column comments in `app/models/procurement.py`). Left OUT
        # of `values` entirely when the payload does not mention the field -
        # `model_fields_set`, not truthiness - so an omitted field on a
        # re-push never clears what an earlier push recorded (absent_vs_null,
        # the same rule this whole surface follows for every other field).
        if "from_so_line_ref" in line.model_fields_set:
            values["from_so_line_ref"] = line.from_so_line_ref
        if "from_po_line_ref" in line.model_fields_set:
            values["from_po_line_ref"] = line.from_po_line_ref
        if "from_po_number" in line.model_fields_set:
            values["from_po_number"] = line.from_po_number
        # V5: the cross-book case - raw pass-through, same rule as the
        # fields above. See `PurchaseOrderLine.from_so_external`'s column
        # comment for why this is not a claim row.
        if "from_so_external" in line.model_fields_set:
            external = line.from_so_external
            # S4 review fix: `exclude_unset=True` so a partial object is
            # stored exactly as sent - see the identical comment in
            # `DocumentIngestService._line_values`.
            values["from_so_external"] = (
                external.model_dump(exclude_unset=True) if external is not None else None
            )
        return values

    def _location_code(
        self, warehouse_id: Optional[str], sent_code: Optional[str]
    ) -> Optional[str]:
        """The sent `warehouse_code`, or the resolved warehouse's own code.

        Adoption (D11) matches ref-less xlsx-era rows on this column - if a
        resolved-by-ref line wrote nothing here, its own resolved warehouse
        would never match the code the book already holds.
        """
        if sent_code:
            return sent_code
        if not warehouse_id:
            return None
        return (
            self.db.query(Warehouse.warehouse_code)
            .filter(Warehouse.id == warehouse_id)
            .scalar()
        )

    def _write_row(
        self,
        row: SPOAllocation,
        values: dict[str, Any],
        force_closed: bool,
        *,
        container_number: Optional[str] = None,
        warnings: Optional[list[str]] = None,
    ) -> None:
        # S2 (`PLAN-oi-follow-book-chain.md`): every call here is this push
        # writing (creating, updating or adopting) one allocation, whatever
        # branch of `_apply_scoped` reached it - staged, not published, same
        # rule as `_pending_ref_moves`.
        self._pending_spo_allocation_ids.append(str(row.id))
        values = dict(values)
        values.pop("line_number", None)
        if "quantity_received" in values:
            # D28c: the DECLARED receipt, recorded before the clamp below and
            # from the DECLARED figure only. This is AutoCount's own
            # TransferedQty on an ordinary push, and the group's carried share
            # on a supersede (the caller has already folded the carry into
            # `quantity_received` by then, which is what makes the carry a
            # statement too). It must never pick up a GRN-derived stored
            # value, which is the whole reason the column exists - so it is
            # read from `values`, not from the clamped result.
            #
            # Same MAX rule as `quantity_received` (D26): a re-push stating a
            # LOWER TransferedQty never lowers the floor, because a receipt
            # that physically arrived does not un-arrive. The by-ref update
            # path refuses such a line outright (`received_guard` ->
            # `received_locked` -> `continue`), so nothing is recorded there
            # at all, which is the same answer the max rule would give.
            declared = int(values["quantity_received"] or 0)
            stored_stated = int(getattr(row, "stated_received", 0) or 0)
            stated = max(stored_stated, declared)
            if stated > 0:
                # Only a POSITIVE statement is recorded. NULL reads 0
                # everywhere (D28c ruling 4), so writing a 0 would say
                # nothing extra while making an ESB-written row differ from
                # an xlsx-written one on a column neither has declared
                # anything on (the parity comparison in
                # tests/test_ingest_parity_s3_shipping_orders.py reads every
                # column literally). The max rule means this can never lower
                # an existing figure either.
                values["stated_received"] = stated
            # Security review (blocker 1), belt-and-suspenders on EVERY caller
            # of this method: `quantity_received` can never regress below what
            # the row already shows, even if some future path calls this
            # without first running `received_guard` - the guard call sites
            # (by-ref update, adoption) already refuse the whole write on a
            # regression, so this is normally a no-op, but a brand new row's
            # stored value is always 0/None and `max()` is a no-op there too.
            stored_received = int(getattr(row, "quantity_received", 0) or 0)
            values["quantity_received"] = max(stored_received, int(values["quantity_received"] or 0))
        for column, value in values.items():
            setattr(row, column, value)
        # D28d: the payload NAMES this row (a by-ref update, an adoption claim,
        # a create), so it is live again whatever it was before - the one and
        # only clearer of the retirement marker.
        row.retired_at = None
        if force_closed:
            # `cancelled` (D3/D9): every line closes regardless of what is
            # still outstanding - a cancelled shipment covers no demand
            # however much of it had already arrived.
            row.line_status = LINE_CLOSED
        # D6 (S3): every allocation of the pushed document stores the cleaned
        # container - `None` when the payload named none, so an absent
        # header field never clears one a previous push (or the xlsx import)
        # already set here.
        if container_number:
            linked = shipping_order_rules.link_allocation_to_shipment(
                self.db, row, container_number, company_id=self.company_id
            )
            if not linked and warnings is not None:
                warnings.append(WARN_CONTAINER_UNRESOLVED)

    def _supersede_xlsx_rows(
        self,
        payload: CanonicalShippingOrder,
        unmatched: list[dict[str, Any]],
        supersede_pool: list[SPOAllocation],
        counts: dict[str, int],
        force_closed: bool,
        *,
        container_number: Optional[str] = None,
        warnings: Optional[list[str]] = None,
        fallback_blocked_products: frozenset | set = frozenset(),
    ) -> list[SPOAllocation]:
        """D25a/D25c/D26/D26a/D27/D30: an Excel-era group REPLACED by the pushed lines.

        Grouped by `(product_id, warehouse_id)` when both sides carry a
        warehouse and by `(product_id, upper(location_code))` otherwise
        (`shipping_order_rules.supersede_group_key`) - the destination an
        aggregate row and the N AutoCount lines it stands for must agree on.
        Per group the plan
        allows: the lines are created as new rows in Seq order, the group's
        received quantity is carried across them (each up to its own
        allocated quantity, remainder onto the last), whatever pointed at the
        superseded rows is repointed onto the group's FIRST line, and the
        superseded rows are then removed - or, when the calling principal
        holds no `.delete` grant (D30), CLOSED and annotated instead, which
        keeps every carry and every link and only leaves the old rows behind.

        The plan itself is `shipping_order_rules.plan_xlsx_supersede` - the
        ONE algorithm, shared with `scripts/dedupe_spo_xlsx_superseded.py`,
        which applies the identical plan to ref rows a push has already
        appended. Only the writing differs: created rows here, updated rows
        there.

        Returns the ref-less rows the plan left alone (no incoming
        counterpart, or refused by D26a's group-total guard) for the caller to
        hand to the ordinary leftover sweep.
        """
        plan = shipping_order_rules.plan_xlsx_supersede(
            unmatched,
            supersede_pool,
            # Review B1: the D31 fallback runs only on a product's FIRST push.
            fallback_blocked_products=fallback_blocked_products,
        )
        by_id = {str(row.id): row for row in supersede_pool}
        kept = [by_id[row_id] for row_id in plan.kept_row_ids if row_id in by_id]

        if plan.locked_groups and warnings is not None:
            # D26a: the same word the by-ref and adoption paths raise when a
            # push would shrink a receipt - the reason is identical, so the
            # ESB must not have to learn a second one.
            warnings.append(WARN_RECEIVED_LOCKED)
        if not plan.groups:
            return kept

        next_number = self._max_line_number(payload)
        consumed: set[int] = set()
        superseded = 0

        for group in plan.groups:
            if group.dropped_shipment_ids and warnings is not None:
                warnings.append(WARN_SHIPMENT_MERGED)
            target: Optional[SPOAllocation] = None
            group_rows: list[SPOAllocation] = []
            for line_plan in group.lines:
                values = dict(unmatched[line_plan.index])
                allocated = int(values.get("allocated_quantity") or 0)
                received, closed = shipping_order_rules.carried_received(
                    allocated, values.get("quantity_received"), line_plan.carried_received
                )
                values["quantity_received"] = received
                values["line_status"] = LINE_CLOSED if closed else LINE_OPEN
                values["receipt_status"] = (
                    RECEIPT_FULLY_RECEIVED if closed else RECEIPT_PENDING
                )
                next_number += 1
                row = SPOAllocation(
                    id=str(uuid.uuid4()),
                    company_id=self.company_id,
                    spo_number=payload.spo_number,
                    spo_line_number=next_number,
                )
                self.db.add(row)
                self._write_row(
                    row, values, force_closed,
                    container_number=container_number, warnings=warnings,
                )
                if not row.inbound_shipment_id and line_plan.inbound_shipment_id:
                    # D26: the line's OWN container link wins when the push
                    # named a container this system knows; short of that the
                    # group keeps the shipment the xlsx row was already
                    # booked against, which is the only record of it left
                    # once that row is gone.
                    row.inbound_shipment_id = line_plan.inbound_shipment_id
                if not row.storage_zone_id and line_plan.storage_zone_id:
                    # D25c: same rule for the bin. The ESB states no zone at
                    # all, so the upload's is the only one there has ever
                    # been, and it would be lost with the row.
                    row.storage_zone_id = line_plan.storage_zone_id
                if not row.uom_id and line_plan.uom_id:
                    # D25c (security round 6): and for the unit.
                    row.uom_id = line_plan.uom_id
                if row.inbound_shipment_id:
                    self.shipment_ids_touched.add(str(row.inbound_shipment_id))
                counts["created"] += 1
                consumed.add(line_plan.index)
                group_rows.append(row)
                if target is None:
                    target = row
                    # S5 (`PLAN-oi-replan-received-links.md`): the superseded
                    # (xlsx-era) rows never carried a ref - `old_ref` is null -
                    # so this records THIS push's own ref against the NEW row
                    # `repoint_allocation_dependants` below is about to move
                    # every dependant onto, including our own order-inquiry
                    # links. `follow_book_repairing` reads it the same way as
                    # an in-place move; presence, never truthiness.
                    if "from_so_line_ref" in values:
                        # AC-RL-49: staged, not published - see `_pending_ref_moves`'s
                        # own docstring in `__init__`.
                        self._pending_ref_moves.append(
                            {
                                "target_kind": "spo",
                                "target_id": str(row.id),
                                "old_ref": None,
                                "new_ref": values["from_so_line_ref"],
                            }
                        )
                    # D25c (security round 6): the group's own facts land on
                    # its FIRST line, the same row the links move to - a
                    # rejection and a note are statements somebody made about
                    # this delivery and would be lost with the row.
                    #
                    # MAX, not a sum (security round 6 addendum): a close-only
                    # supersede leaves the Excel rows in place, so the dedupe
                    # re-selects the document later and this carry runs again.
                    # `max` makes the second run a no-op instead of doubling
                    # the rejected quantity.
                    if group.rejected_total:
                        row.quantity_rejected = max(
                            int(row.quantity_rejected or 0), group.rejected_total
                        )
                    if group.notes:
                        row.allocation_notes = shipping_order_rules.append_note(
                            row.allocation_notes, group.notes
                        )
            self.db.flush()
            removing = [
                by_id[row_id] for row_id in group.superseded_row_ids if row_id in by_id
            ]
            for row in removing:
                if row.inbound_shipment_id:
                    self.shipment_ids_touched.add(str(row.inbound_shipment_id))
            if target is not None:
                if group.split_receipts:
                    # D32: a product-level fallback group's lines sit in
                    # different locations, so its GRN picks are split over them
                    # by capacity rather than all landing on the first line.
                    shipping_order_rules.repoint_picking_lines_by_capacity(
                        self.db,
                        [str(row.id) for row in removing],
                        [
                            (str(row.id), row.warehouse_id, row.allocated_quantity)
                            for row in group_rows
                        ],
                        company_id=self.company_id,
                    )
                shipping_order_rules.repoint_allocation_dependants(
                    self.db,
                    [str(row.id) for row in removing],
                    str(target.id),
                    company_id=self.company_id,
                )
            if target is not None:
                # D33: proven before anything is deleted or zeroed - a failure
                # raises inside this record's savepoint and the push lands
                # nothing for this document.
                shipping_order_rules.assert_supersede_conserved(
                    self.db,
                    [str(row.id) for row in removing],
                    sum(int(row.quantity_received or 0) for row in removing),
                    sum(line_plan.carried_received for line_plan in group.lines),
                    sum(int(row.quantity_received or 0) for row in group_rows),
                )
            # S7 (D30): read BEFORE the rows go, so the trail names what was
            # actually taken out of the picture and with which quantities.
            trail = "; ".join(
                f"{row.id}(allocated={row.allocated_quantity},"
                f"received={row.quantity_received})"
                for row in removing
            )
            if self.may_delete:
                for row in removing:
                    self.db.delete(row)
                action = "deleted"
            else:
                # D30: no `.delete` grant, so the rows stay - closed, so they
                # no longer read as outstanding supply, and annotated so the
                # next reader can see which document replaced them.
                note = f"superseded by {payload.source_ref}"
                retired_at = datetime.now(timezone.utc)
                for row in removing:
                    row.line_status = LINE_CLOSED
                    # APPENDED, never overwritten (through the same helper the
                    # D25c carry uses): whatever the uploader or a planner
                    # wrote on this row is the only record of why it exists,
                    # and the row is being kept precisely so that record
                    # survives.
                    # D33: the receipt figure about to be zeroed is its own
                    # FRAGMENT, added only while the row still holds one, so a
                    # second pass over an already-retired row (received 0) adds
                    # nothing and `append_note`'s fragment dedupe holds.
                    row.allocation_notes = shipping_order_rules.append_note(
                        row.allocation_notes,
                        f"{note}; received {int(row.quantity_received)} carried"
                        if row.quantity_received
                        else note,
                    )
                    # D33 (SPO-XLSX-SUPERSEDE round 2): the row is RETIRED, not
                    # merely closed. Its receipt now lives on the replacement
                    # lines (carried above, picks moved), so it keeps none of
                    # its own - left in place it stayed visible (R2) and was
                    # counted a second time in the PL's "SPO allocated" - and
                    # `retired_at` takes it out of the GRN pool (D34), so a
                    # future GRN cannot FIFO onto it. The trail logged below
                    # and the note above keep the old figure.
                    # Frozen into the DECLARED column first (security review
                    # N1, the D28d freeze): a structured record of what the row
                    # held. Every reader that floors by `stated_received` (D35)
                    # or draws capacity (D34) skips a retired row, so nothing
                    # counts it a second time.
                    held = int(row.quantity_received or 0)
                    if held > int(row.stated_received or 0):
                        row.stated_received = held
                    row.quantity_received = 0
                    row.receipt_status = RECEIPT_PENDING
                    if row.retired_at is None:
                        row.retired_at = retired_at
                action = "closed"
                if warnings is not None:
                    warnings.append(WARN_SUPERSEDED_CLOSED_ONLY)
            superseded += len(removing)
            logger.info(
                "ingest.spo_supersede spo_number=%s doc_ref=%s group=%s action=%s "
                "target=%s rows=[%s] dropped_shipments=%s",
                payload.spo_number,
                payload.source_ref,
                group.key,
                action,
                target.id if target is not None else None,
                trail,
                ",".join(group.dropped_shipment_ids) or "-",
            )

        self.db.flush()
        # `plan.groups` is non-empty by the early return above, and every
        # group of it removes or closes at least one row, so this key is set
        # exactly when a supersede happened - absent otherwise, the rule
        # `lines.dropped` already follows on a document verdict (AC-X9).
        counts["superseded"] = counts.get("superseded", 0) + superseded
        for index in sorted(consumed, reverse=True):
            # Consumed here, so the ordinary create loop cannot write the
            # same line a second time.
            del unmatched[index]
        return kept

    def _adopt_lines(
        self,
        unmatched: list[dict[str, Any]],
        pool: list[SPOAllocation],
        counts: dict[str, int],
        force_closed: bool,
        *,
        container_number: Optional[str] = None,
        warnings: Optional[list[str]] = None,
        allow_positional: bool = True,
    ) -> None:
        """D11: claim ref-less POOL rows for ref-less UNMATCHED incoming lines.

        Three ordered passes, mirroring `document_ingest_service._adopt_lines`
        with the durable/secondary roles swapped: `(product_id,
        upper(location_code))` is the pair that survives into pass 2 (the
        upload's own dedup key - `outstanding_import_service._spo_line_plans`),
        `warehouse_id`-or-None is the secondary component that only pass 1
        requires, since a legacy row routinely carries a location this system
        holds no warehouse row for.

        1. exact `(product_id, location, warehouse_id-or-None, outstanding)`
           key, ties among rows/lines sharing one key broken by position;
        2. `(product_id, location)` alone, only where exactly one pool row
           remains for it;
        3. position alone (incoming `line_number` order against the rows' own
           `spo_line_number` order), only where the remaining counts agree -
           and only when `allow_positional` (D25b): a push that superseded a
           group has already consumed its own lines, so "the counts agree" no
           longer says the two sides describe the same document, and the pool
           it is left with is exactly the rows the supersede was not entitled
           to touch.
        """
        all_have_line_number = all(v.get("line_number") is not None for v in unmatched)

        def _position(idx: int, values: dict[str, Any]):
            return values["line_number"] if all_have_line_number else idx

        def _row_position(row: SPOAllocation):
            return (
                row.spo_line_number if row.spo_line_number is not None else 10**9,
                str(row.id),
            )

        def _location(code: Optional[str]) -> Optional[str]:
            return (code or "").strip().upper() or None

        def _row_key(row: SPOAllocation):
            outstanding = int(row.allocated_quantity or 0) - int(row.quantity_received or 0)
            return (
                str(row.product_id) if row.product_id else None,
                _location(row.location_code),
                str(row.warehouse_id) if row.warehouse_id else None,
                outstanding,
            )

        def _line_key(values: dict[str, Any]):
            outstanding = values["allocated_quantity"] - values["quantity_received"]
            return (
                str(values.get("product_id")) if values.get("product_id") else None,
                _location(values.get("location_code")),
                str(values.get("warehouse_id")) if values.get("warehouse_id") else None,
                outstanding,
            )

        def _coarse_row_key(row: SPOAllocation):
            return (
                str(row.product_id) if row.product_id else None,
                _location(row.location_code),
            )

        def _coarse_line_key(values: dict[str, Any]):
            return (
                str(values.get("product_id")) if values.get("product_id") else None,
                _location(values.get("location_code")),
            )

        claimed_lines: set[int] = set()
        claimed_rows: set[int] = set()

        def _claim(idx: int, values: dict[str, Any], row: SPOAllocation) -> bool:
            # Security review (blocker 1): the adoption path used to call
            # `_write_row` with no guard at all - a legacy xlsx-era row with a
            # real receipt on it could be adopted by an incoming line whose
            # `qty_ordered`/`qty_received` shrink below what was already
            # received, exactly the exposure `received_guard` already closed
            # on the by-ref update path. Guarded here the same way: on a
            # trip, this pairing is refused - the row is left completely
            # unchanged, and both the row and the incoming line remain
            # available to the next pass (or fall through to their normal
            # unmatched/leftover handling) rather than being claimed.
            guard = shipping_order_rules.received_guard(
                row, values["allocated_quantity"], values["quantity_received"]
            )
            if guard == shipping_order_rules.GUARD_RECEIVED_LOCKED:
                if warnings is not None:
                    warnings.append(WARN_RECEIVED_LOCKED)
                return False
            self._write_row(
                row, values, force_closed,
                container_number=container_number, warnings=warnings,
            )
            counts["adopted"] += 1
            claimed_lines.add(idx)
            claimed_rows.add(id(row))
            return True

        # ---- pass 1: exact (product, location, warehouse, outstanding) key ----
        pool_by_key: dict[tuple, list] = {}
        for row in pool:
            pool_by_key.setdefault(_row_key(row), []).append(row)
        for rows in pool_by_key.values():
            rows.sort(key=_row_position)

        lines_by_key: dict[tuple, list] = {}
        for idx, values in enumerate(unmatched):
            lines_by_key.setdefault(_line_key(values), []).append(idx)
        for indices in lines_by_key.values():
            indices.sort(key=lambda i: _position(i, unmatched[i]))

        for key, indices in lines_by_key.items():
            rows = pool_by_key.get(key, [])
            for idx, row in zip(indices, rows):
                _claim(idx, unmatched[idx], row)

        # ---- pass 2: (product, location) alone, exactly one row remaining ----
        remaining_indices = sorted(
            (i for i in range(len(unmatched)) if i not in claimed_lines),
            key=lambda i: _position(i, unmatched[i]),
        )
        coarse_pool: dict[tuple, list] = {}
        for row in pool:
            if id(row) in claimed_rows:
                continue
            coarse_pool.setdefault(_coarse_row_key(row), []).append(row)

        for idx in remaining_indices:
            values = unmatched[idx]
            key = _coarse_line_key(values)
            rows = coarse_pool.get(key)
            if rows and len(rows) == 1:
                row = rows[0]
                _claim(idx, values, row)
                coarse_pool[key] = []

        # ---- pass 3: position alone, only when the remaining counts agree ----
        remaining_indices = [i for i in range(len(unmatched)) if i not in claimed_lines]
        remaining_pool = [r for r in pool if id(r) not in claimed_rows]
        if allow_positional and remaining_indices and len(remaining_indices) == len(remaining_pool):
            remaining_indices.sort(key=lambda i: _position(i, unmatched[i]))
            remaining_pool.sort(key=_row_position)
            for idx, row in zip(remaining_indices, remaining_pool):
                _claim(idx, unmatched[idx], row)

        for idx in sorted(claimed_lines, reverse=True):
            del unmatched[idx]
        pool[:] = [r for r in pool if id(r) not in claimed_rows]


class ShippingOrderReadService:
    """Current shipping-order state for a batch of refs, in the ESB's vocabulary.

    Same contract as `DocumentReadService.current_state` - keyed by
    `source_doc_ref` rather than by `integration_references` (D3), since a
    shipping order has no single entity id to resolve one through.
    """

    def __init__(self, db: Session, *, company_id: str):
        self.db = db
        self.company_id = company_id
        self.refs = IntegrationReferenceService(db, company_id=self.company_id)

    def current_state(self, entity_type: str, source_refs: list[str]) -> dict[str, Any]:
        found: list[dict[str, Any]] = []
        not_found: list[str] = []

        for source_ref in source_refs:
            rows = (
                self.db.query(SPOAllocation)
                .filter(
                    SPOAllocation.company_id == self.company_id,
                    SPOAllocation.source_doc_ref == source_ref,
                )
                .order_by(SPOAllocation.spo_line_number)
                .all()
            )
            if not rows:
                not_found.append(source_ref)
                continue
            found.append(self._record(source_ref, rows))

        return {"records": found, "not_found": not_found}

    def _record(self, source_ref: str, rows: list[SPOAllocation]) -> dict[str, Any]:
        first = rows[0]
        return {
            "source_ref": source_ref,
            # No header row exists for a shipping order (D3).
            "entity_id": None,
            "spo_number": first.spo_number,
            "supplier_ref": self._ref_of(Supplier, first.supplier_id),
            "issue_date": first.issue_date,
            "expected_date": first.expected_date,
            "currency": first.currency,
            "status": _document_status(rows),
            "lines": [self._line(row) for row in rows],
        }

    def _line(self, row: SPOAllocation) -> dict[str, Any]:
        return {
            "entity_id": str(row.id),
            "source_ref": row.source_ref,
            "product_ref": self._ref_of(Product, row.product_id),
            "warehouse_ref": self._ref_of(Warehouse, row.warehouse_id),
            "qty_ordered": row.allocated_quantity,
            "qty_received": row.quantity_received,
            "unit_cost": row.unit_cost,
        }

    def _ref_of(self, model: type, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        origin = self.refs.origin_of(
            entity_type=model.__tablename__, entity_id=str(entity_id)
        )
        return str(origin.source_ref) if origin is not None else None
