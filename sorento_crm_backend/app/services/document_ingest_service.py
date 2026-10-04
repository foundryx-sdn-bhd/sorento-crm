"""Ingest DOCUMENTS pushed in by the ESB: sales orders and purchase orders (A3).

The three semantics `master_ingest_service` carries are the same here and are
imported from it rather than restated - per-record SAVEPOINT isolation,
retryable-is-not-failed, adoption over duplication, and a dry run that is a real
run taken back. What follows is only what a document adds.

**The document owns its lines.** A push is one authoritative statement about the
whole order, so a line the payload no longer carries has been deleted upstream
and goes with it - including a line an earlier extract import created with no
`source_ref` at all. Keeping those would double the demand on the first
AutoCount sync: the same physical line would count once under the ref-less row
and once under the pushed one.

**Unless something else points at that line.** `scm.loading_plan_line.po_line_id`
is `ON DELETE CASCADE`, so removing a purchase-order line takes loading-plan rows
with it; `stock_transfers.so_line_id` and four more are `SET NULL` and would be
silently orphaned - a transfer that has already physically moved stock, detached
from the order it moved it for. The first sync of an ADOPTED document is where
this bites hardest: every pre-existing line is ref-less, so all of them are
replaced at once. So a line that is going away is asked the same question the
deletion endpoint asks (`app/services/dependent_probe.py`), and one that anything
references is `cancelled` in place instead - out of the demand, still there for
whatever points at it.

**A line keeps its id.** The upsert is by the line's own `source_ref`
(AutoCount's DtlKey), not by position and not by wholesale replacement. Stock
allocations, transfers and plan decisions point at a line id; replacing every
line on every sync - which is what the stale `sorento_crm-autocount` branch did -
would break those links weekly for lines nobody touched.

**Every reference is resolved before anything is written.** A document points at
five masters, and one that has not synced yet is a sequencing artefact rather
than bad data (AC-AC-16), so the record is `retryable` and NOTHING lands. A
header written without its lines is worse than no header: it reads as an order
for nothing, which the netting treats as fully covered demand.

**Status is a vocabulary, not a string.** Five canonical words map onto two
different Sorento vocabularies, and an unmapped word is `failed` rather than
stored - `status` is what decides whether a row is still open demand, so a value
nothing can classify would quietly leave the order out of every plan.

The ORM is used for every read and every write here, deliberately, where the
master ingest uses raw SQL. Both `sales_orders` and `sales_order_lines` (and
their purchase-order twins) exist a SECOND time in the `projects` schema, and
unqualified raw SQL resolves those names through `search_path`. The ORM models
say which table they mean - which is also why a reference declares the MODEL it
resolves into rather than a table name, and the entity name is read back off that
model. The company anchor is still stamped by hand on top of the auto-stamp,
because a document must never depend on ambient session state for the one thing
that partitions it.

Ingest emits no lifecycle events, for the same reason the master ingest does
not: a record arriving FROM AutoCount must never trigger a write back to it.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.order import Customer, OrderInquiryConflict, SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.schemas.canonical_documents import CanonicalPurchaseOrder, CanonicalSalesOrder
from app.services.dependent_probe import is_referenced, referrers_of
from app.services.integration_reference_service import (
    IntegrationReferenceService,
    ReferenceConflict,
)
from app.services.rules import customer_rules
from app.services.rules.document_rules import derive_document_status
from app.services.scm import outstanding_diff
# `MasterRefResolver` is the ref/code/name/back-create ladder (D1/D2/D10),
# shared with `ShippingOrderIngestService` (S3) - lifted out in S3 rather than
# duplicated, see that module's docstring.
from app.services.master_ref_resolver import (
    WARN_UNCLASSIFIED_DEMAND,
    MasterRefResolver,
    dedupe_warnings,
)
from app.services.project_label_rules import (
    apply_project_label,
    label_from_note,
    label_from_ref,
)
from app.services.master_ingest_service import (
    INTERNAL_ERROR_MESSAGE,
    IngestOutcome,
    IngestResult,
    MissingReference,
    RecordResult,
    UnsupportedIngestEntity,
    _field_errors,
    _value_changed,
    integrity_conflict_errors,
)
from app.services.scm import order_link_service, plan_exception_service
from app.services.scm.customer_label import normalize_debtor_code
from app.services.scm.demand_class import classify_document
# `DEFAULT_PO_CURRENCY` is the upload's own rule (D1's CNY fill) - imported
# rather than restated so the two channels that read a purchase book from
# AutoCount cannot drift.
from app.services.scm.outstanding_import_service import DEFAULT_PO_CURRENCY
# D5: an `SPO-` numbered document belongs under `shipping_orders`, never
# `purchase_orders` - the same family test the PO listing importer uses.
from app.services.scm.po_listing_reader import FAMILY_SPO, doc_family

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "autocount"

# Canonical status -> stored status. Two vocabularies, because sales orders and
# purchase orders never shared one: an order that has been shipped in full is
# `fulfilled` on the sales side and `received` on the buying side, and a purchase
# order that is merely live is `active` where a sales order is `open`.
#
# Module constants rather than literals in the spec, because these ARE the
# cross-repo contract: the shared service's sink codes against exactly these five
# words, and section 7.2 of the plan records them.
SALES_ORDER_STATUS_MAP = {
    "open": "open",
    # D6a (S5): a `partial` sales order is stored `open`, not `partially_delivered`.
    # `qty_delivered` already carries the partial fact per line, and `open` is the
    # one word `scm.committed_v` and every other SCM reader already admits as
    # committed demand - mapping `partial` onto it needs zero view changes, where
    # widening every one of those readers to also admit `partially_delivered`
    # would (plan D6, option (a) over (b)). This makes the map NOT injective:
    # `partial` and `open` both stored as `open`. `_canonical_status` below
    # therefore reads back `open` for either - see its own docstring.
    "partial": "open",
    "fulfilled": "fulfilled",
    "closed": "closed",
    "cancelled": "cancelled",
}
PURCHASE_ORDER_STATUS_MAP = {
    "open": "active",
    "partial": "partial",
    "fulfilled": "received",
    "closed": "closed",
    "cancelled": "cancelled",
}

CANCELLED = "cancelled"

# The rest of the verdict-warning vocabulary (D9) - `customer_created`,
# `supplier_created`, `agent_created`, `warehouse_unresolved`,
# `customer_unresolved` - lives on `master_ref_resolver` next to the ladder
# that emits them (S3). `WARN_UNCLASSIFIED_DEMAND` is imported above since
# `_classify_sales_order` below is the only thing that raises it.


@dataclass(frozen=True)
class DocumentSpec:
    """How one canonical document maps onto a header table and its line table."""

    schema: type[BaseModel]
    header_model: type
    line_model: type
    # The business number, which is what a FIRST sync adopts an existing row by.
    # ONE name: the payload field and the column are spelled the same on both
    # documents (`so_number`, `po_number`), and two fields holding one string
    # could be given two values that no test would notice.
    number_field: str
    status_map: dict[str, str]
    # Only sales_orders carries `source_doc_no`; purchase_orders has no such
    # column, and writing one would be an AttributeError per record.
    doc_no_column: Optional[str]
    # (column, ref field, master MODEL, code field or None, name field or None)
    # for the header's FKs and the line's. The model is what the ref resolves
    # through - its `__tablename__` is the entity type - and it is also how the
    # resolved row's company is read, which is a table the ORM names rather
    # than one `search_path` chooses. `code field`/`name field` name the v2
    # fallback siblings on the SAME payload object (D1) - `None` where a model
    # has no such field (`sales_agent_ref` has no name step; `product`/
    # `warehouse` have no name step either).
    header_refs: tuple[tuple[str, str, type, Optional[str], Optional[str]], ...]
    line_refs: tuple[tuple[str, str, type, Optional[str], Optional[str]], ...]
    # (column, payload field) for the plain values.
    header_fields: tuple[tuple[str, str], ...]
    line_fields: tuple[tuple[str, str], ...]
    # The line's delivered/received quantity, which decides `line_status`.
    line_delivered_field: str
    # The FK from a line back to its header.
    line_fk: str

    @property
    def entity_type(self) -> str:
        """The name this document is ingested, read and deleted under.

        Read off the model rather than restated: the key in `DOCUMENT_SPECS`, the
        entity type in `integration_references` and the table name are one
        string, and a spec that could disagree with its own model about which
        would be a mismatch nothing reports.
        """
        return self.header_model.__tablename__


DOCUMENT_SPECS: dict[str, DocumentSpec] = {
    "sales_orders": DocumentSpec(
        schema=CanonicalSalesOrder,
        header_model=SalesOrder,
        line_model=SalesOrderLine,
        number_field="so_number",
        status_map=SALES_ORDER_STATUS_MAP,
        doc_no_column="source_doc_no",
        header_refs=(
            ("customer_id", "customer_ref", Customer, "customer_code", "customer_name"),
            ("sales_agent_id", "sales_agent_ref", SalesAgent, "agent_code", None),
        ),
        line_refs=(
            ("product_id", "product_ref", Product, "product_code", None),
            ("warehouse_id", "warehouse_ref", Warehouse, "warehouse_code", None),
        ),
        # `demand_class`, `demand_origin`, `priority` and
        # `order_type` are absent on purpose, the same way the agent master's
        # annotations are: they are set by the importers and by CS, AutoCount
        # holds no opinion about any of them, and a weekly re-sync that restated
        # them from a payload which never carried them would blank the captain's
        # classification. Absent from the written set, they cannot be touched.
        # `debtor_code` is the one exception (v2, D9): it is written from
        # `customer_code` in `_header_values`, not through this generic list,
        # because it fires on the payload field being SENT rather than on a
        # fixed column mapping.
        header_fields=(
            ("so_number", "so_number"),
            ("order_date", "doc_date"),
            ("requested_delivery_date", "requested_delivery_date"),
            ("internal_note", "internal_note"),
        ),
        line_fields=(
            ("qty_ordered", "qty_ordered"),
            ("qty_delivered", "qty_delivered"),
            ("unit_price", "unit_price"),
            ("discount", "discount"),
            ("line_total", "line_total"),
            ("uom", "uom"),
            ("required_date", "required_date"),
        ),
        line_delivered_field="qty_delivered",
        line_fk="sales_order_id",
    ),
    "purchase_orders": DocumentSpec(
        schema=CanonicalPurchaseOrder,
        header_model=PurchaseOrder,
        line_model=PurchaseOrderLine,
        number_field="po_number",
        status_map=PURCHASE_ORDER_STATUS_MAP,
        doc_no_column=None,
        header_refs=(
            ("supplier_id", "supplier_ref", Supplier, "supplier_code", "supplier_name"),
        ),
        line_refs=(
            ("product_id", "product_ref", Product, "product_code", None),
            ("warehouse_id", "warehouse_ref", Warehouse, "warehouse_code", None),
        ),
        header_fields=(
            ("po_number", "po_number"),
            ("issue_date", "issue_date"),
            ("expected_date", "expected_date"),
            ("currency", "currency"),
        ),
        line_fields=(
            ("qty_ordered", "qty_ordered"),
            ("qty_received", "qty_received"),
            ("unit_cost", "unit_cost"),
            ("discount", "discount"),
            ("line_total", "line_total"),
            ("uom", "uom"),
            ("currency", "currency"),
            ("expected_date", "expected_date"),
        ),
        # `moq_snapshot` and `order_multiple_snapshot` are what Sorento's planner
        # believed when it raised the line, not what AutoCount knows now.
        line_delivered_field="qty_received",
        line_fk="purchase_order_id",
    ),
}

# The entity names this service answers for. Read by the route so ONE endpoint
# dispatches between masters and documents rather than a second router existing.
DOCUMENT_ENTITIES = frozenset(DOCUMENT_SPECS)


def _canonical_status(spec: DocumentSpec, stored: Optional[str]) -> Optional[str]:
    """The canonical word for a stored status, or the stored one if it has none.

    A locally raised purchase order sits in `draft`, and `draft` is not part of
    the contract vocabulary. Inventing a canonical word for it would tell the ESB
    the document is in a state it is not; handing back what is actually stored
    lets it see a value it does not own and leave the row alone.

    D6a (S5) makes `SALES_ORDER_STATUS_MAP` NOT injective: `open` and `partial`
    both store `open`. The FIRST canonical word whose mapping matches wins - dict
    order is declaration order, and `open` is declared before `partial` - so a
    stored `open` row always reads back `open` (AC-V5-3), never `partial`, which
    is correct: once written, nothing on the row still says which canonical word
    produced it, and `open` is the more general of the two.
    """
    if stored is None:
        return None
    for canonical, mapped in spec.status_map.items():
        if mapped == stored:
            return canonical
    return stored


class _UnknownStatus(ValueError):
    """A status word outside the canonical vocabulary. Failed, never stored."""


class DocumentIngestService(MasterRefResolver):
    """Same constructor and same ``ingest()`` contract as ``MasterIngestService``.

    Deliberately interchangeable: the route picks one or the other on the entity
    name and calls the same method, so there is one ingest endpoint rather than
    two that can drift on batch caps, verdicts or the dry-run rollback.

    Subclasses `MasterRefResolver` for the ref/code/name/back-create ladder
    (`_resolve_master` and its helpers) - shared with `ShippingOrderIngestService`
    rather than duplicated (S3).
    """

    def __init__(
        self, db: Session, integration_id: Optional[str] = None, *, company_id: str
    ):
        # Required, for the reason the master ingest states: a default would be
        # the incumbent company, and a push meant for the other one would land
        # there silently.
        super().__init__(db, integration_id, company_id=company_id)
        self._dry_run = False
        # D7/S5 (plan section 2.6): what the ROUTE's post-write hooks need, read
        # off this instance after `ingest()` returns rather than threaded through
        # `IngestResult` - plain attributes, no new classes, and `MasterIngestService`
        # (which the route also constructs for masters) needs none of them.
        # Populated only by a record that actually WROTE (the last lines of `_apply`,
        # after everything else in it has already succeeded) - a savepoint rollback
        # is a Python-level no-op here since execution never reaches that point.
        self.touched_product_ids: set[str] = set()
        self.so_numbers: set[str] = set()
        self.written_header_ids: set[str] = set()
        # S2 (`PLAN-oi-follow-book-chain.md`, AC-FB-21/25): every `from_so_line_ref`
        # a purchase-order LINE carried on a line this push CREATED or UPDATED -
        # `ref_moves` only ever captures a ref that CHANGED, so a freshly created
        # line, or an existing line that names a sales-order line for the FIRST
        # time, is invisible to it. Read off `line_values` in `_record_hook_state`
        # (below), which already holds the resolved value for every line this
        # record wrote, so no second capture site is needed inside `_sync_lines`.
        self.written_po_line_refs: set[str] = set()
        # (product_id, supplier_id, po_number) triples, purchase_orders only -
        # what `supersede_crm_raised_pos` (the extracted shared function) wants.
        self.po_supersede_triples: set[tuple[str, str, str]] = set()
        # S5 (`PLAN-oi-replan-received-links.md`): every PO line whose
        # `from_so_line_ref` changed on THIS push (including to/from null),
        # `{"target_kind": "po", "target_id", "old_ref", "new_ref"}`. What the
        # route's `follow_book_repairing` hook reads to move our own order-inquiry
        # links the same way the book just moved the pairing - captured here,
        # inside `_sync_lines`'s matched-row branch, which is the only place both
        # the OLD value (still on `row`) and the NEW one (about to overwrite it)
        # are in hand at once.
        #
        # AC-RL-49 (security review, 17 Sep): `ref_moves` is published ONLY from
        # `_record_hook_state`, the last statement of `_apply` - reached only once
        # everything else in the record has already succeeded. `_pending_ref_moves`
        # is where `_sync_lines` actually appends: a plain Python list is not part
        # of the record's own SAVEPOINT, so a move captured for line 1 mid-`_sync_
        # lines` would otherwise survive the rollback a later line's own failure in
        # the SAME record triggers, and the post-commit hook would then apply a
        # move whose ref change was never actually persisted. Reset at the top of
        # `_apply` for every record, so a failed record's own leftover never leaks
        # into the next one's publish.
        self.ref_moves: list[dict[str, Optional[str]]] = []
        self._pending_ref_moves: list[dict[str, Optional[str]]] = []
        # sales_orders only: the BEFORE half of the route's plan-exception hook
        # (AC-V5-1), keyed by product id. Captured ONCE for the whole batch,
        # before the record loop runs (`ingest()` calls
        # `_capture_plan_exception_before`, S6 review fix) - the same "read
        # the old position while it is still the one in the database" rule
        # `outstanding_import_service.apply` follows for its own
        # `before_positions`.
        self.plan_exception_before: dict[str, Any] = {}
        # Perf round 5: per-batch memos for `_classify_sales_order` - a busy
        # batch reuses the same agent/customer across hundreds of documents,
        # and neither read changes mid-batch (nothing here writes to
        # `sales_agents` or `customers`). `_segment_cache` is handed to
        # `classify_document` (an otherwise pure, session-free function) as
        # its own optional keyword so `outstanding_import_service`'s call
        # site - which passes none - is unaffected.
        self._agent_class_cache: dict[str, Optional[str]] = {}
        self._customer_code_cache: dict[str, Optional[str]] = {}
        self._product_code_cache: dict[str, Optional[str]] = {}
        self._warehouse_code_cache: dict[str, Optional[str]] = {}
        self._segment_cache: dict[Any, Optional[str]] = {}
        # D10 (S2): the same before/after line picture
        # `outstanding_import_service.apply()` feeds `planning_change_service
        # .build_batch`, built here across the WHOLE batch (one call, not one
        # per record - `build_batch`'s own scoping rule) so the route's hook
        # can react to a sales_orders push exactly as a weekly upload does.
        self.so_diff_before: list[outstanding_diff.Line] = []
        self.so_diff_after: list[outstanding_diff.Line] = []
        self.so_header_id_by_number: dict[str, str] = {}

    # --------------------------------------------------------------- the batch
    def ingest(
        self, entity_type: str, records: list[dict], *, dry_run: bool = False
    ) -> IngestResult:
        spec = DOCUMENT_SPECS.get(entity_type)
        if spec is None:
            raise UnsupportedIngestEntity(
                f"Unsupported ingest entity {entity_type!r}. "
                f"Expected one of: {', '.join(sorted(DOCUMENT_SPECS))}"
            )

        result = IngestResult(dry_run=dry_run)
        self._dry_run = dry_run
        # S6 review fix: ONE plan-exception BEFORE snapshot for the whole
        # batch, taken before any record writes anything - not one per
        # record, which cost `plan_exception_service.snapshot` a query per
        # sales order in a busy batch for no reason (every snapshot in one
        # batch is read before ANY of them has written anything, so there is
        # nothing for a second one to see that the first one did not).
        if spec.entity_type == "sales_orders":
            self._capture_plan_exception_before(records)
        try:
            for raw in records:
                result.records.append(self._ingest_one(spec, raw))
        finally:
            self._dry_run = False
            if dry_run:
                # In a finally, so an error mid-batch cannot leave a partially
                # applied preview in the session for whatever commits next.
                self.db.rollback()
        return result

    def _ingest_one(self, spec: DocumentSpec, raw: dict) -> RecordResult:
        source_ref = raw.get("source_ref") if isinstance(raw, dict) else None

        try:
            # `Any`, because the parsed shape is whichever canonical model the
            # spec names and every field read below is declared on that one, not
            # on BaseModel.
            payload: Any = spec.schema(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors=_field_errors(exc),
            )
        except TypeError:
            logger.warning(
                "ingest.document_malformed entity=%s source_ref=%s",
                spec.entity_type,
                source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

        # D5/D6 (S3, AC-P3-7): an `SPO-` numbered document, OR one the payload
        # itself flags `is_shipping_order` (AutoCount's `UDF_ShipOrder`),
        # pushed as a purchase order is refused outright, before any
        # savepoint work - it is a shipping order wearing the wrong entity
        # name, and adopting it here would create a phantom purchase order
        # `spo_allocations` can never link back to. The flag exists because
        # the prefix alone misses a differently-numbered SPO AutoCount still
        # marks as one.
        if spec.entity_type == "purchase_orders" and (
            doc_family(payload.po_number) == FAMILY_SPO
            or getattr(payload, "is_shipping_order", False)
        ):
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"po_number": "shipping order; push under shipping_orders"},
            )

        # One document per savepoint. Without it a failed flush poisons the
        # session and every later record in the file fails too.
        savepoint = self.db.begin_nested()
        try:
            outcome, entity_id, diff, warnings, line_counts = self._apply(spec, payload)
            savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=outcome,
                entity_id=entity_id,
                diff=diff,
                warnings=warnings,
                lines=line_counts,
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
        except _UnknownStatus as exc:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"status": str(exc)},
            )
        except IntegrityError as exc:
            # Fix round 4, BUG B: a unique-constraint race (two companies, or a
            # concurrent push of the same number/code) - named by constraint,
            # never by `str(exc)`'s full SQL statement.
            savepoint.rollback()
            logger.warning(
                "ingest.integrity_conflict entity=%s source_ref=%s",
                spec.entity_type,
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
                spec.entity_type,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    # ------------------------------------------------------------ one document
    def _apply(
        self, spec: DocumentSpec, payload: Any
    ) -> tuple[
        IngestOutcome, str, Optional[dict[str, dict[str, Any]]], list[str], dict[str, int]
    ]:
        # EVERYTHING is resolved before ANYTHING is written. An unresolved
        # reference has to leave the database exactly as it found it, and a
        # header inserted before the line that fails would only be taken back by
        # the savepoint - which is a guarantee about this transaction, not about
        # the order the work happens in.
        #
        # AC-RL-49: fresh per record - THIS record's own ref moves, staged until
        # `_record_hook_state` below publishes them, never a previous (possibly
        # failed) record's leftover.
        self._pending_ref_moves = []
        #
        # Shared across the header and every line: a back-create triggered by
        # line 3 belongs on the SAME record verdict as one triggered by the
        # header, not a per-line list nothing reads (D9).
        warnings: list[str] = []
        # The header is resolved (not yet written into) BEFORE `_header_values`, unlike
        # every other value here: D4's fill-only `order_type` and never-downgraded
        # `demand_class` both have to read what the row ALREADY holds, which does not
        # exist to read until `_header` has found-or-created it. For a CREATE, `header`
        # is deliberately not in the session yet (see `_header`'s comment) - added just
        # below, once `header_values` is complete and about to be written, so no query
        # the ref/demand ladders run in between can autoflush a half-built row and fail
        # on its own not-null constraints.
        #
        # Also why `status` is derived AFTER `_header` now (D20, S2): an absent
        # `payload.status` falls back to `document_rules.derive_document_status`,
        # which for an UPDATE has to read the row's OWN existing status - not
        # readable until `_header` has found it.
        header, outcome = self._header(spec, payload)
        canonical_status = payload.status
        if canonical_status is None:
            existing_canonical = (
                _canonical_status(spec, getattr(header, "status", None))
                if outcome is IngestOutcome.UPDATED
                else None
            )
            line_dicts = [
                {
                    "qty_ordered": line.qty_ordered,
                    spec.line_delivered_field: getattr(line, spec.line_delivered_field, None),
                }
                for line in payload.lines
            ]
            canonical_status = derive_document_status(line_dicts, existing_canonical)
        status = self._status(spec, canonical_status)
        header_values = self._header_values(spec, payload, status, warnings, header)
        # PLAN-po-line-currency-follows-header-22sep.md: a line with no stated currency
        # takes the HEADER's own resolved currency (already filled by `_header_values`
        # above when the header itself stated none), never a hardcoded CNY. `.get()`
        # rather than `header.currency`: for a spec with no `currency` header column at
        # all (every non-PO document) this is simply `None` and `_line_values`' own
        # shape-driven guard never reads it.
        header_currency = header_values.get("currency")
        # D9: a line whose product does not resolve is DROPPED, not a reason
        # to fail the whole document - `_line_values`' only raise is the
        # ladder's Product rung (`line_refs` never resolves anything else that
        # can raise; warehouse is NULL+warning, never an exception). The rest
        # of the document still lands, and the drop is counted on the verdict
        # (`lines.dropped`) rather than silently disappearing.
        line_values: list[dict[str, Any]] = []
        dropped = 0
        # B1 review fix: the dropped line's own PERSISTED counterpart must be
        # left exactly as-is, never swept as a leftover just because this
        # push's incoming version of it never made it into `line_values`.
        # `source_ref` is a required field on every canonical line - always
        # present even when the reference on it failed to resolve.
        dropped_refs: list[str] = []
        for index, line in enumerate(payload.lines):
            try:
                line_values.append(
                    self._line_values(spec, line, index, status, warnings, header_currency)
                )
            except MissingReference:
                dropped += 1
                ref = getattr(line, "source_ref", None)
                if ref:
                    dropped_refs.append(ref)

        if spec.entity_type == "sales_orders" and outcome is IngestOutcome.UPDATED:
            self._capture_planning_diff_before(header, payload)

        # Only an update overwrites anything, and only a dry run needs to say so.
        diff = (
            self._diff(header, header_values)
            if outcome is IngestOutcome.UPDATED
            else None
        )
        self.db.add(header)
        for column, value in header_values.items():
            setattr(header, column, value)
        if spec.entity_type == "sales_orders":
            self._apply_project_label(header, payload)
        self.db.flush()

        line_counts = self._sync_lines(spec, header, line_values, dropped_refs=dropped_refs)
        if dropped:
            line_counts["dropped"] = dropped
        if spec.entity_type == "sales_orders":
            self._capture_planning_diff_after(header, payload)
        if spec.entity_type == "purchase_orders":
            self._write_order_link_claims(header, payload)
        self.refs.link(
            entity_type=spec.entity_type,
            entity_id=str(header.id),
            source_ref=payload.source_ref,
            source_doc_no=payload.source_doc_no,
            integration_id=self.integration_id,
        )
        # D7/S5: recorded LAST - everything above has already succeeded by here,
        # so a record whose ladder or status word failed earlier never reaches
        # this line and never pollutes the batch-level state the route's hooks
        # read after `ingest()` returns.
        self._record_hook_state(spec, header, payload, header_values, line_values)
        return outcome, str(header.id), diff, dedupe_warnings(warnings), line_counts

    def _capture_plan_exception_before(self, records: list[dict]) -> None:
        """The BEFORE half of AC-V5-1 - ONE snapshot for the WHOLE batch (S6
        review fix), taken before the record loop runs (so genuinely before
        anything in the batch has written) rather than once per record.

        Product ids are pre-resolved CHEAPLY rather than by running the real
        ladder a second time, and by CODE only (PRODUCT-REF-COLLISION, owner
        ruling 3 Oct: a product reference is never read): every
        `product_code` in the batch resolves through ONE query rather than one
        per line, and a line with no code has no product to snapshot. Best-effort
        like the upload's own equivalent: a defect here must cost the route's
        plan-exception hook (which simply has less to compare against), never
        any record in the batch - a document ingest is not the operation this
        diff is FOR.
        """
        product_ids: set[str] = set()
        codes: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict):
                continue
            for line in raw.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                # PRODUCT-REF-COLLISION (owner ruling 3 Oct): code only, a
                # ref-only line has no product to snapshot.
                code = (line.get("product_code") or "").strip()
                if code:
                    codes.add(code)

        if codes:
            rows = (
                self.db.query(Product.id, func.upper(func.btrim(Product.product_code)))
                .filter(
                    func.upper(func.btrim(Product.product_code)).in_(
                        [c.upper() for c in codes]
                    ),
                    Product.company_id == self.company_id,
                )
                .all()
            )
            by_normalized = {normalized: str(pid) for pid, normalized in rows}
            for code in codes:
                pid = by_normalized.get(code.upper())
                if pid:
                    product_ids.add(pid)
                    # Populates the SAME memo `_resolve_by_code` reads, so the
                    # record loop's own code rung for this product skips its
                    # query too.
                    self._memo[(Product.__tablename__, "code", code.upper())] = pid

        if not product_ids:
            return
        try:
            self.plan_exception_before.update(
                plan_exception_service.snapshot(self.db, list(product_ids))
            )
        except Exception:  # noqa: BLE001 - best-effort, see docstring
            logger.warning(
                "ingest.plan_exception_before_snapshot_failed product_ids=%s",
                sorted(product_ids),
                exc_info=True,
            )

    def _record_hook_state(
        self,
        spec: DocumentSpec,
        header: Any,
        payload: Any,
        header_values: dict[str, Any],
        line_values: list[dict[str, Any]],
    ) -> None:
        """What the route's post-write hooks (D7) need, gathered per record."""
        self.written_header_ids.add(str(header.id))
        # AC-RL-49: THIS record has now fully succeeded (everything above this call
        # in `_apply` already ran without raising) - its own staged ref moves are
        # promoted to the batch-level list the route's hook reads, and never before.
        if self._pending_ref_moves:
            self.ref_moves.extend(self._pending_ref_moves)
            self._pending_ref_moves = []
        for values in line_values:
            product_id = values.get("product_id")
            if product_id:
                self.touched_product_ids.add(str(product_id))

        if spec.entity_type == "sales_orders":
            self.so_numbers.add(payload.so_number)
        elif spec.entity_type == "purchase_orders":
            supplier_id = header_values.get("supplier_id")
            if supplier_id:
                for values in line_values:
                    product_id = values.get("product_id")
                    if product_id:
                        self.po_supersede_triples.add(
                            (str(product_id), str(supplier_id), payload.po_number)
                        )
            for values in line_values:
                ref = values.get("from_so_line_ref")
                if ref:
                    self.written_po_line_refs.add(str(ref))

    def _write_order_link_claims(self, header: Any, payload: Any) -> None:
        """V4 (plan section 2.5): a PO line dedicating its purchase against
        sales orders the ESB already knows the numbers of.

        Runs AFTER the lines are flushed, so every line named has a real id
        to claim against. Inside the SAME record savepoint as everything
        else in `_apply` - a dry run rolls this back with the rest of the
        record, never a special case of its own (AC-V4-4). The actual
        claim-writing loop is `order_link_service.write_claims_for_lines`
        (S7 dedup), shared with `ShippingOrderIngestService`'s own line
        claims - only the row fetch differs between the two.

        V5 (AutoCount linkage widen): a line naming the EXACT sales-order
        line it was raised for, via `from_so_line_ref`, resolves through
        `order_link_service.write_line_ref_claims` - the precise-match
        sibling of `write_claims_for_lines`, run over the SAME row fetch
        (one query covers both fields' refs) - see its own docstring.

        CALL ORDER (B1 review fix): `write_line_ref_claims` runs FIRST. It
        can write a claim already fully resolved; `write_claims_for_lines`'s
        own `resolve()` call only fills a STILL-missing `so_line_id` and,
        once set, that claim is out of `resolve()`'s reach for good - the
        reverse order let an exact ref on the same line as a
        `from_so_numbers` entry lose to whichever line the ambiguous
        `(so_number, item_code)` match happened to pick.
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
            self.db.query(PurchaseOrderLine)
            .filter(
                PurchaseOrderLine.purchase_order_id == str(header.id),
                PurchaseOrderLine.source_ref.in_(list(refs)),
            )
            .all()
        )
        # N1 review fix: one row/product-code index, shared by both calls
        # below instead of each running its own identical `Product` query.
        index = order_link_service.index_claim_rows(self.db, rows)
        order_link_service.write_line_ref_claims(
            self.db,
            company_id=self.company_id,
            document_number=payload.po_number,
            rows=rows,
            wanted=ref_wanted,
            id_attr="po_line_id",
            index=index,
        )
        order_link_service.write_claims_for_lines(
            self.db,
            company_id=self.company_id,
            document_number=payload.po_number,
            rows=rows,
            wanted=number_wanted,
            id_attr="po_line_id",
            index=index,
        )

    def _status(self, spec: DocumentSpec, canonical: str) -> str:
        """The stored status for a canonical word.

        Lower-cased first: AutoCount's exports are not consistent about case, and
        `Partial` naming a different state from `partial` would be a distinction
        nobody upstream intended.
        """
        mapped = spec.status_map.get((canonical or "").strip().lower())
        if mapped is None:
            raise _UnknownStatus(
                f"unknown status {canonical!r}; expected one of: "
                f"{', '.join(sorted(spec.status_map))}"
            )
        return mapped

    def _header(self, spec: DocumentSpec, payload: Any) -> tuple[Any, IngestOutcome]:
        """The row this document addresses: by reference, then by number, then new.

        BL-056 (D15): `self.refs` is scoped to this anchor company, so a
        header ref linked under a DIFFERENT company never resolves here - the
        same as a ref that was never linked - and falls through to
        adopt-by-number/create below, landing a brand new row in THIS company.
        The cross-company refusal this used to need
        (`MasterRefResolver._require_same_company`) is unreachable through
        refs now and has been removed.
        """
        existing_id = self.refs.resolve(
            entity_type=spec.entity_type, source_ref=payload.source_ref
        )
        if existing_id is not None:
            return self._load(spec, existing_id), IngestOutcome.UPDATED

        # Within the company, and through the model: `so_number` is unique per
        # company only (migration 305), and the same name in the `projects`
        # schema is a different table that raw SQL would let `search_path`
        # choose between.
        adopted = (
            self.db.query(spec.header_model.id)
            .filter(
                getattr(spec.header_model, spec.number_field)
                == getattr(payload, spec.number_field),
                spec.header_model.company_id == self.company_id,
            )
            .scalar()
        )
        if adopted is not None:
            if (
                self.refs.origin_of(entity_type=spec.entity_type, entity_id=adopted)
                is not None
            ):
                # Two AutoCount documents claiming one Sorento order is a
                # conflict a human settles; retargeting silently would move
                # somebody else's demand.
                raise ReferenceConflict(
                    f"{spec.number_field}="
                    f"{getattr(payload, spec.number_field)!r} "
                    "is already linked to another source"
                )
            return self._load(spec, adopted), IngestOutcome.UPDATED

        header = spec.header_model(id=str(uuid.uuid4()), company_id=self.company_id)
        # NOT `db.add()`-ed here on purpose. `_header_values` (D4) reads this
        # object's stored columns before the caller ever writes into it -
        # harmless for an UPDATE (`_load` returns an already-persisted row),
        # but a CREATE's row would otherwise sit in the session as a pending
        # INSERT with every NOT NULL column still empty. Any query anywhere
        # between here and the setattr loop - the ref/code ladder's lookups, a
        # back-create's own explicit flush - would autoflush that half-built
        # row and fail on its own constraints. `_apply` adds it once
        # `header_values` is complete and about to be written.
        return header, IngestOutcome.CREATED

    def _load(self, spec: DocumentSpec, entity_id: str):
        header = self.db.get(spec.header_model, str(entity_id))
        if header is None:
            # The reference resolved and the company check passed, so the row is
            # there; only a scope filter could hide it, and that would be a bug
            # worth naming rather than a silent create.
            raise ReferenceConflict(
                f"{spec.entity_type} {entity_id!r} resolved but is not readable in this company"
            )
        return header

    def _header_values(
        self,
        spec: DocumentSpec,
        payload: Any,
        status: str,
        warnings: list[str],
        header: Any,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            column: getattr(payload, field) for column, field in spec.header_fields
        }
        values["status"] = status
        for column, ref_field, model, code_field, name_field in spec.header_refs:
            resolved = self._resolve_master(
                model=model,
                ref_field=ref_field,
                ref=getattr(payload, ref_field),
                code_field=code_field,
                code=getattr(payload, code_field) if code_field else None,
                name=getattr(payload, name_field) if name_field else None,
                warnings=warnings,
            )
            # Seam 1b (PLAN-demand-class-agent-arrival.md): `sales_agent_id`
            # only, and only when this push named NO agent at all - measured
            # on the 0918 prod copy, of 2031 sales-order pushes an agent
            # followed by a null agent happened 0 times, a null then an agent
            # 5 times, agent A changed to a DIFFERENT agent B 7 times, so
            # nothing legitimate relies on an agent-less push blanking a
            # stored one. Dropping the key here (rather than writing `None`)
            # keeps it off the setattr loop below AND off `_diff`'s dry-run
            # report, so an agent-less re-push neither blanks the column nor
            # is reported as changing it. A push naming a DIFFERENT agent
            # still overwrites it (A -> B unchanged) and still does not
            # re-decide the demand class, since `header.sales_agent_id`
            # stays non-empty for that push.
            if (
                column == "sales_agent_id"
                and resolved is None
                and getattr(header, "sales_agent_id", None)
            ):
                continue
            values[column] = resolved
        # `debtor_code` (v2, D9): written from `customer_code` whenever it is
        # SENT, independent of whether the customer itself resolved - an
        # order whose debtor Sorento does not (yet) hold still carries the
        # code it was pushed with. Absent on `CanonicalPurchaseOrder`, so the
        # attribute simply is not there and this is a no-op for a PO.
        customer_code = getattr(payload, "customer_code", None)
        if customer_code:
            values["debtor_code"] = normalize_debtor_code(customer_code)
        # `debtor_name` (CUSTOMER-CODE-IDENTITY, owner decision 30 Sep 2026):
        # the customer name the SO was issued under is per DOCUMENT - AutoCount
        # lets the user edit it on the order - so it is stored on the order
        # whenever it is sent, and the master `customers.customer_name` is
        # never written from here. Same shape rule as `debtor_code` above: a
        # PO payload has no `customer_name`, so this is a no-op for a PO; a
        # push that sends none leaves the stored name alone.
        customer_name = (getattr(payload, "customer_name", None) or "").strip()
        if customer_name:
            values["debtor_name"] = customer_name
        # `is_transferable` (SO-TRANSFERABLE): AutoCount's `Transferable` flag, written
        # whenever it is SENT and left alone when it is not - the same shape rule as the two
        # above. Explicit `is not None`: an F is a False, and a falsy check would drop it.
        # A PO payload has no such field, so this is a no-op for a PO.
        transferable = getattr(payload, "transferable", None)
        if transferable is not None:
            values["is_transferable"] = transferable
        # PO currency default (D1): only a spec that carries a `currency`
        # header column reaches this, which today is `purchase_orders` alone -
        # so the fill is shape-driven rather than a hardcoded entity check.
        if "currency" in values and not values["currency"]:
            values["currency"] = DEFAULT_PO_CURRENCY
        # v2 demand classification (D4, sales_orders only). `order_type` only
        # exists on `CanonicalSalesOrder`, so this is a no-op for a PO - the
        # same shape-driven guard the currency fill above uses.
        if hasattr(payload, "order_type"):
            self._classify_sales_order(payload, values, warnings, header, customer_code)
        # D16 (S2, review B5): `customer_segment`/`customer_region` threaded
        # onto the customer this push just resolved-or-back-created -
        # `customer_rules.back_create_customer` already accepted `segment`/
        # `region` kwargs, but nothing on the document ladder ever called it
        # with them (confirmed: zero references to `payload.customer_segment`
        # in this file before this fix). Shape-driven the same way the
        # `order_type` classification above is (`CanonicalPurchaseOrder`
        # carries neither field) and fill-only, matching the masters push's
        # own `market_segment_code` rule: a hand-set value is never
        # overwritten.
        if hasattr(payload, "customer_segment"):
            self._apply_customer_segment_and_region(
                values.get("customer_id"), payload, warnings
            )
        # Adoption takes ownership: from here on the row is AutoCount's, and the
        # next push has to find it by reference rather than by number again.
        values["source_system"] = SOURCE_SYSTEM
        values["source_ref"] = payload.source_ref
        if spec.doc_no_column:
            values[spec.doc_no_column] = getattr(payload, spec.number_field)
        return values

    def _apply_project_label(self, header: Any, payload: Any) -> None:
        """PLAN-so-project-label.md: the note beats the AutoCount `Ref`.

        Runs on both create and update, after `internal_note` has already landed on
        `header` from the setattr loop above - a re-push with a corrected note has to
        move the label the same way a first push sets it. `apply_project_label`'s own
        precedence gate is what keeps an `inquiry`-sourced label (raised higher than
        either of these) from ever being overwritten here, and what keeps a no-note,
        no-`ref` re-push from touching `updated_at` at all (AC-I5): neither candidate
        below writes anything when both resolve to `None`.
        """
        note_label, note_source = label_from_note(getattr(header, "internal_note", None))
        if note_label:
            apply_project_label(header, note_label, note_source)
            return
        ref_label = label_from_ref(getattr(payload, "ref", None))
        apply_project_label(header, ref_label, "ref" if ref_label else None)

    def _apply_customer_segment_and_region(
        self, customer_id: Optional[str], payload: Any, warnings: list[str]
    ) -> None:
        """D16 (review B5): `customer_segment` folds through the SAME
        `customer_rules.fold_market_segment` the masters push uses, so the
        two entry points can never map a spelling two different ways - an
        unrecognised value drops with `segment_unknown` on the DOCUMENT
        record rather than failing it (`market_segment_code` is a foreign
        key). `region` is free text, written whenever sent. Both fill-only:
        a hand-set value on an existing customer is never overwritten,
        matching `_customer_columns`' own rule.
        """
        if not customer_id:
            return
        segment_raw = getattr(payload, "customer_segment", None)
        region = getattr(payload, "customer_region", None)
        if not segment_raw and not region:
            return
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            return
        if segment_raw:
            canonical = customer_rules.fold_market_segment(self.db, segment_raw)
            if canonical is None:
                warnings.append("segment_unknown")
            elif not customer.market_segment_code:
                customer.market_segment_code = canonical
        if region and not customer.region:
            customer.region = region

    def _classify_sales_order(
        self,
        payload: Any,
        values: dict[str, Any],
        warnings: list[str],
        header: Any,
        customer_code: Optional[str],
    ) -> None:
        """D4 (plan section 1/2.3): fill-only `order_type`, never-downgraded `demand_class`.

        A stored `demand_class` is a settled fact - possibly set by CS by hand,
        possibly by an order_type this same ladder decided on an earlier push -
        and this ingest never overwrites or blanks it, whatever a fresh run of
        the ladder would say today (AC-V2-6), WITH ONE EXCEPTION
        (PLAN-demand-class-agent-arrival.md): AutoCount pushes a new sales
        order before its agent is filled in - SO421912 first pushed with
        `agent_code: null` on 17 Sep 2026, then a second push 78 minutes later
        carried the agent. When the stored header still has no agent AND this
        push resolves one whose demand class is known, the ladder is run
        again exactly as for a new document (so a stored or stated order type
        still outranks the agent) and the answer, if any, is written - it is
        never blanked. That includes a class set by hand while the order had
        no agent yet: it is re-decided, once, the first time an agent with a
        demand class of its own arrives. A stored class with a stored agent,
        or an arriving agent with no demand class, is still settled and
        returns immediately - and `_header_values`' own fill-only guard on
        `sales_agent_id` (Seam 1b) is what keeps an agent-less re-push from
        ever blanking a stored agent and re-arming this exception by
        accident.
        """
        stored_order_type = getattr(header, "order_type", None)
        stated_order_type = payload.order_type
        if not stored_order_type and stated_order_type:
            values["order_type"] = stated_order_type

        agent_id = values.get("sales_agent_id")
        agent_demand_class = self._agent_demand_class(agent_id)
        stored_class = getattr(header, "demand_class", None)
        agent_arriving = bool(
            stored_class and not getattr(header, "sales_agent_id", None) and agent_demand_class
        )
        if stored_class and not agent_arriving:
            return

        debtor_code = customer_code or getattr(header, "debtor_code", None)
        customer_id = values.get("customer_id")
        if not debtor_code and customer_id:
            # No code was SENT and none is stored yet, but the ref resolved to a
            # real customer - its own code is the debtor code this document
            # names, just not spelled out in this particular payload.
            debtor_code = self._customer_code_of(customer_id)

        cls = classify_document(
            self.db,
            stored_order_type=stored_order_type,
            stated_order_type=stated_order_type,
            agent_demand_class=agent_demand_class,
            debtor_code=debtor_code,
            company_id=self.company_id,
            segment_cache=self._segment_cache,
        )
        if cls is not None:
            values["demand_class"] = cls
        else:
            warnings.append(WARN_UNCLASSIFIED_DEMAND)

    def _agent_demand_class(self, agent_id: Optional[str]) -> Optional[str]:
        """Perf round 5: memoised per batch - a busy batch names the same
        handful of agents on hundreds of documents, and nothing in this
        ingest writes `sales_agents.demand_class`."""
        if not agent_id:
            return None
        if agent_id not in self._agent_class_cache:
            self._agent_class_cache[agent_id] = (
                self.db.query(SalesAgent.demand_class)
                .filter(SalesAgent.id == agent_id)
                .scalar()
            )
        return self._agent_class_cache[agent_id]

    def _customer_code_of(self, customer_id: str) -> Optional[str]:
        """Perf round 5: memoised per batch, same reason as `_agent_demand_class`."""
        if customer_id not in self._customer_code_cache:
            self._customer_code_cache[customer_id] = (
                self.db.query(Customer.customer_code)
                .filter(Customer.id == customer_id)
                .scalar()
            )
        return self._customer_code_cache[customer_id]

    def _product_code_of(self, product_id: Optional[str]) -> Optional[str]:
        """Memoised per batch (same reason as `_customer_code_of`) - the D10
        planning diff identifies a line by its product CODE, matching
        `outstanding_import_service`'s own `outstanding_diff.Line.item_code`."""
        if not product_id:
            return None
        if product_id not in self._product_code_cache:
            self._product_code_cache[product_id] = (
                self.db.query(Product.product_code).filter(Product.id == product_id).scalar()
            )
        return self._product_code_cache[product_id]

    def _warehouse_code_of(self, warehouse_id: Optional[str]) -> Optional[str]:
        """Same as `_product_code_of`, for `outstanding_diff.Line.location`."""
        if not warehouse_id:
            return None
        if warehouse_id not in self._warehouse_code_cache:
            self._warehouse_code_cache[warehouse_id] = (
                self.db.query(Warehouse.warehouse_code)
                .filter(Warehouse.id == warehouse_id)
                .scalar()
            )
        return self._warehouse_code_cache[warehouse_id]

    def _capture_planning_diff_before(self, header: Any, payload: Any) -> None:
        """D10: the sales order's OWN lines, read before this record's write
        touches any of them - the same "read the old position while it is
        still the one in the database" rule `outstanding_import_service
        .apply()` follows for its own `before_positions`. UPDATE only: a
        CREATE has no before state, matching `outstanding_diff`'s own
        `added` classification for a line with nothing on the `before` side.
        """
        self.so_header_id_by_number[payload.so_number] = str(header.id)
        existing = (
            self.db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == str(header.id))
            .all()
        )
        for row in existing:
            if row.line_status == CANCELLED:
                continue
            self.so_diff_before.append(
                outstanding_diff.Line(
                    doc_number=payload.so_number,
                    item_code=self._product_code_of(row.product_id) or "",
                    location=self._warehouse_code_of(row.warehouse_id) or "",
                    qty=float(row.qty_ordered or 0),
                    required_date=row.required_date,
                    row_ref=str(row.id),
                    # Same identity `SalesOrderService._propagate_planning_change` sets
                    # (Slice A review round, R-S1): a product swap on the SAME line, if
                    # the ingest sync writes IN PLACE, pairs by `line_id` into one
                    # `product_changed` row instead of a `closed` plus an unrelated
                    # `added`.
                    line_id=str(row.id),
                )
            )

    def _capture_planning_diff_after(self, header: Any, payload: Any) -> None:
        """The AFTER half of `_capture_planning_diff_before` - read back post-sync
        so every line carries its real, persisted id (`row_ref`), which is what
        `_run_document_hooks` needs to tell `planning_change_service.build_batch`
        which `sales_order_lines` row a changed `Diff.Change` belongs to.
        """
        self.so_header_id_by_number[payload.so_number] = str(header.id)
        current = (
            self.db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == str(header.id))
            .all()
        )
        for row in current:
            if row.line_status == CANCELLED:
                continue
            self.so_diff_after.append(
                outstanding_diff.Line(
                    doc_number=payload.so_number,
                    item_code=self._product_code_of(row.product_id) or "",
                    location=self._warehouse_code_of(row.warehouse_id) or "",
                    qty=float(row.qty_ordered or 0),
                    required_date=row.required_date,
                    row_ref=str(row.id),
                    line_id=str(row.id),
                )
            )

    def _line_values(
        self,
        spec: DocumentSpec,
        line: Any,
        index: int,
        status: str,
        warnings: list[str],
        header_currency: Optional[str] = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            column: getattr(line, field) for column, field in spec.line_fields
        }
        for column, ref_field, model, code_field, name_field in spec.line_refs:
            ref_value = getattr(line, ref_field)
            code_value = getattr(line, code_field) if code_field else None
            resolved_id = self._resolve_master(
                model=model,
                ref_field=f"lines.{index}.{ref_field}",
                ref=ref_value,
                code_field=f"lines.{index}.{code_field}" if code_field else None,
                code=code_value,
                name=getattr(line, name_field) if name_field else None,
                warnings=warnings,
            )
            # D22: a line that names NO location at all leaves the column out of
            # `values` entirely rather than writing `None` - an UPDATE's
            # per-column setattr (`_sync_lines`) then leaves whatever warehouse
            # the row already carries untouched, instead of clearing a
            # warehouse the Order Inquiry sheet set and this push never
            # mentioned. A location that WAS sent and failed to resolve still
            # writes `None` (unchanged, D10) - only "nothing sent" is new.
            if model is Warehouse and not (ref_value or code_value):
                continue
            values[column] = resolved_id
        # Owner ruling 22 Sep 2026 ("we shouldn't assume CNY"): a line with no stated
        # currency takes its HEADER's currency, never a hardcoded default. `currency`
        # only exists on this dict for a purchase-order line (shape-driven, same guard
        # the header fill uses); `header_currency` is `None` when the header itself
        # named none either, which leaves the line NULL too.
        if "currency" in values and not values["currency"]:
            values["currency"] = header_currency
        # NOT NULL on both line tables, and an absent figure means none delivered.
        for column in ("qty_ordered", spec.line_delivered_field):
            if values.get(column) is None:
                values[column] = 0
        values["line_status"] = _line_status(
            status, values["qty_ordered"], values[spec.line_delivered_field]
        )
        values["source_system"] = SOURCE_SYSTEM
        values["source_ref"] = line.source_ref
        # AutoCount's Seq (D11), position only - popped before persistence by
        # every setattr site in `_sync_lines`/`_adopt_lines`. No column exists
        # for it on the purchase-order line table.
        values["line_number"] = getattr(line, "line_number", None)
        # PLAN-so-lines-autocount-order.md 3.1: the SAME `Seq` is now ALSO kept, as
        # `sales_order_lines.line_no` - the sales-order spec only, a purchase-order line
        # still has nowhere to put it. A separate key from `line_number` above (which
        # stays popped everywhere, unconditionally present, for the D11 position
        # tie-break) so this one flows through the ordinary `setattr`/constructor path at
        # every call site with no further change there.
        #
        # Absent_vs_null, the same rule the V5 fields just below already follow: `_sync_
        # lines`/`_adopt_lines` only ever see this dict, never `line` itself, so the
        # `model_fields_set` presence check has to happen here. An omitted `line_number`
        # on a re-push must leave a stored `line_no` alone - proven by an explicit `null`,
        # which DOES clear, since `model_fields_set` is presence, not truthiness.
        if spec.entity_type == "sales_orders" and "line_number" in line.model_fields_set:
            values["line_no"] = line.line_number
        # V5 (AutoCount linkage widen, B2 review fix - uniform on both line
        # tables): raw pass-through onto `purchase_order_lines` - see the
        # column comments in `app/models/procurement.py`.
        # `CanonicalSalesOrderLine` carries none of these four fields, so
        # `model_fields_set` never contains them for a sales-order line and
        # this whole block is a no-op there. Each is left OUT of `values`
        # entirely when absent (absent_vs_null): an omitted field on a
        # re-push must never clear what an earlier push recorded - proven
        # by an explicit `null`, which DOES clear (S1 review fix), since
        # `model_fields_set` is presence, not truthiness.
        if "from_so_line_ref" in line.model_fields_set:
            values["from_so_line_ref"] = line.from_so_line_ref
        if "from_po_line_ref" in line.model_fields_set:
            values["from_po_line_ref"] = line.from_po_line_ref
        if "from_po_number" in line.model_fields_set:
            values["from_po_number"] = line.from_po_number
        if "from_so_external" in line.model_fields_set:
            external = line.from_so_external
            # S4 review fix: `exclude_unset=True` so a partial object (the
            # ESB naming only `db` and `doc_key`, say) is stored exactly as
            # sent - `model_dump()` alone fills every OTHER field of the
            # nested model with `None`, which is not what "raw, verbatim"
            # means and would drop a key on a later, narrower re-push.
            values["from_so_external"] = (
                external.model_dump(exclude_unset=True) if external is not None else None
            )
        return values

    def _sync_mirror_line(self, core_line: SalesOrderLine, values: dict[str, Any]) -> None:
        """S2, `PLAN-esb-change-row-refresh.md`: the mirror `projects.sales_order_lines`
        row (`ProjectSalesOrderLine`) follows the core line's `required_date`/
        `qty_ordered` on every re-push, not just at creation - a later ESB push that moves
        or resizes an already-mirrored line otherwise leaves the mirror the board actually
        reads drifting behind the book (SO419122). A line with no mirror yet (the order
        was never adopted) is left alone, no error (AC-8).

        OI-PRODUCT-FOLLOW S1: the product follows too, as the manual SO edit already does
        (`sales_order_service.py` matched-line branch). A swap left on the old product kept
        the board, and `auto_place_products`, sourcing what the order no longer asks for.
        """
        if (
            "required_date" not in values
            and "qty_ordered" not in values
            and "product_id" not in values
        ):
            return
        from app.models.project_so import ProjectSalesOrderLine

        mirror = (
            self.db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.core_sales_order_line_id == str(core_line.id))
            .one_or_none()
        )
        if mirror is None:
            return
        if "required_date" in values:
            mirror.delivery_date = core_line.required_date
        if "qty_ordered" in values:
            mirror.qty = core_line.qty_ordered
        if "product_id" in values and core_line.product_id:
            mirror.product_id = core_line.product_id

    def _sync_lines(
        self,
        spec: DocumentSpec,
        header: Any,
        line_values: list[dict[str, Any]],
        *,
        dropped_refs: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """Make the header's lines equal to the payload's, by the line's own ref.

        Five outcomes per line, counted for the verdict (D11, AC-V7-5):
        `updated` (matched by its own existing `source_ref`), `adopted` (an
        xlsx-era ref-less row claimed by `_adopt_lines`'s three-step match),
        `created` (neither matched, so a fresh row), `deleted` (an unmatched
        leftover row nothing points at) and `cancelled` (an unmatched leftover
        row something still points at).

        A line whose `source_ref` is NULL was written by the extract importer
        before AutoCount owned this document, and leaving it counted would
        count the same physical line twice - but deleting one a loading plan or
        a stock transfer points at either destroys that row (CASCADE) or
        orphans it (SET NULL). Cancelled satisfies both: the demand is gone,
        the row a dependent needs is still there. Adoption (D11) exists so
        that row is not manufactured fresh in the first place: the SAME
        physical line, matched by what it actually is rather than replaced,
        keeps every allocation, claim and GRN link that already hangs off it.
        """
        existing = (
            self.db.query(spec.line_model)
            .filter(getattr(spec.line_model, spec.line_fk) == str(header.id))
            .all()
        )
        by_ref: dict[str, Any] = {}
        pool: list[Any] = []
        # A second row sharing a ref another row already claims (should never
        # happen, but the original code guarded it) - a leftover like any
        # other, never a D11 adoption candidate since it already has A ref.
        dup_ref: list[Any] = []
        # S4 review fix: a ref-less row this system already cancelled - by an
        # earlier absence, or the deletion endpoint - is not a live xlsx-era
        # adoption candidate any more; matching a NEW DtlKey onto it would
        # resurrect demand that was correctly retired. It still falls through
        # to the leftover sweep below unchanged.
        already_cancelled: list[Any] = []
        for row in existing:
            if row.source_ref and row.source_ref not in by_ref:
                by_ref[row.source_ref] = row
            elif row.source_ref:
                dup_ref.append(row)
            elif row.line_status == CANCELLED:
                already_cancelled.append(row)
            else:
                pool.append(row)

        # B1 review fix: a dropped line's own persisted counterpart is
        # protected from the leftover sweep, BY REF when one already exists
        # under that exact `source_ref` - popped out of `by_ref` here, before
        # the by-ref matching loop below even runs, so it is never touched by
        # anything and never reaches the sweep at the bottom of this method.
        # A drop whose ref matches NOTHING in `by_ref` (the ref-less/xlsx-era
        # counterpart case - there is no product/warehouse to key a POOL match
        # on for a line that never resolved) is counted instead and handed to
        # `_adopt_lines`, which protects that many POOL rows positionally,
        # the same tie-break its own pass 3 already trusts.
        unresolved_dropped = 0
        for ref in dropped_refs or ():
            if by_ref.pop(ref, None) is None:
                unresolved_dropped += 1

        counts = {"adopted": 0, "created": 0, "updated": 0, "deleted": 0, "cancelled": 0}
        # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-6/7, write site 2): every
        # SALES ORDER line this push cancels, captured only on the TRANSITION - two
        # collection points, both guarded the same way (prior status read BEFORE the
        # write, appended only when it was not already `cancelled`): the matched-by-ref
        # setattr loop just below (the same precedent `from_so_line_ref` capture-
        # before-setattr already uses a few lines down), and the leftover-cancel sweep
        # (3.2a, ~1481-1487) further down, which walks `already_cancelled` on EVERY
        # push and would re-flag a reconfirmed row without the same guard. A re-push
        # that flags nothing at either site never even calls
        # `flag_rows_for_cancelled_lines`.
        newly_cancelled_so_line_ids: list[str] = []

        unmatched: list[dict[str, Any]] = []
        for values in line_values:
            row = by_ref.pop(values["source_ref"], None)
            if row is not None:
                prior_line_status = row.line_status
                values.pop("line_number", None)
                # D22: a resolved warehouse that DIFFERS from what the row
                # already carries overwrites it (the AutoCount location wins)
                # but is also recorded, so the Order Inquiry worklist can
                # render the disagreement rather than it vanishing silently.
                if spec.entity_type == "sales_orders" and "warehouse_id" in values:
                    new_warehouse_id = values["warehouse_id"]
                    old_warehouse_id = row.warehouse_id
                    if (
                        new_warehouse_id
                        and old_warehouse_id
                        and str(new_warehouse_id) != str(old_warehouse_id)
                    ):
                        self.db.add(
                            OrderInquiryConflict(
                                id=str(uuid.uuid4()),
                                company_id=self.company_id,
                                sales_order_id=str(header.id),
                                sales_order_line_id=str(row.id),
                                previous_warehouse_id=old_warehouse_id,
                                new_warehouse_id=new_warehouse_id,
                                source="autocount_esb",
                            )
                        )
                # S5: capture the move BEFORE the setattr loop below overwrites
                # `row`'s own value - a PO line only, the one book `follow_book_
                # repairing` moves our own links to follow. `"from_so_line_ref"
                # in values` is presence (`model_fields_set`), never truthiness,
                # so a payload that explicitly sends `null` is captured too.
                if spec.entity_type == "purchase_orders" and "from_so_line_ref" in values:
                    old_ref = row.from_so_line_ref
                    new_ref = values["from_so_line_ref"]
                    if old_ref != new_ref:
                        # AC-RL-49: staged, not published - see `_pending_ref_moves`'s
                        # own docstring in `__init__`.
                        self._pending_ref_moves.append(
                            {
                                "target_kind": "po",
                                "target_id": str(row.id),
                                "old_ref": old_ref,
                                "new_ref": new_ref,
                            }
                        )
                for column, value in values.items():
                    setattr(row, column, value)
                counts["updated"] += 1
                if spec.entity_type == "sales_orders":
                    self._sync_mirror_line(row, values)
                if (
                    spec.entity_type == "sales_orders"
                    and prior_line_status != CANCELLED
                    and row.line_status == CANCELLED
                ):
                    newly_cancelled_so_line_ids.append(row.id)
            else:
                unmatched.append(values)

        if pool and (unmatched or unresolved_dropped):
            self._adopt_lines(
                spec, header, unmatched, pool, counts, dropped_count=unresolved_dropped
            )

        for values in unmatched:
            row = spec.line_model(
                id=str(uuid.uuid4()),
                company_id=self.company_id,
                **{spec.line_fk: str(header.id)},
            )
            self.db.add(row)
            values.pop("line_number", None)
            for column, value in values.items():
                setattr(row, column, value)
            counts["created"] += 1

        line_table = spec.line_model.__tablename__
        # Perf round 5: the catalogue query behind `referrers_of` answers the
        # same thing for every row of THIS line table in THIS sweep - fetched
        # once here rather than once per row inside `is_referenced`.
        line_referrers = referrers_of(self.db, line_table)
        for row in [*by_ref.values(), *pool, *dup_ref, *already_cancelled]:
            prior_line_status = row.line_status
            if is_referenced(self.db, line_table, row.id, referrers=line_referrers):
                # Quantities and prices are left exactly as they were: this row
                # is now evidence of what a transfer moved or a plan was built
                # from, and rewriting it would falsify that record.
                row.line_status = CANCELLED
                counts["cancelled"] += 1
                # PLAN-oi-cancelled-line-used-confirm.md (3.2a): this loop walks
                # `already_cancelled` on EVERY push (its whole reason to be there is
                # the ref-less xlsx-era adoption guard), so the TRANSITION guard has
                # to be here too, not just at the matched-by-ref site above - an
                # unconditional append would re-flag a row purchasing already
                # re-confirmed on every later push of the same reduced document.
                if spec.entity_type == "sales_orders" and prior_line_status != CANCELLED:
                    newly_cancelled_so_line_ids.append(row.id)
            else:
                self.db.delete(row)
                counts["deleted"] += 1
        self.db.flush()
        if newly_cancelled_so_line_ids:
            from app.services.project_order_inquiry_service import (
                flag_rows_for_cancelled_lines,
            )

            flag_rows_for_cancelled_lines(self.db, newly_cancelled_so_line_ids)
        # Self-heal (issue #969): the ESB push is the true writer behind almost every
        # unmirrored line measured live (394 of them, all `source_system = autocount`) -
        # `_upsert_lines`'s self-heal never sees this write, it is the manual FE edit's
        # own path. Same gate as the confirm-side heal: an adopted, unauthored mirror only.
        if spec.entity_type == "sales_orders" and counts.get("created"):
            from app.models.project_so import SO_STATUS_ADOPTED, ProjectSalesOrder
            from app.services.project_so_adoption_service import ProjectSOAdoptionService

            order = (
                self.db.query(ProjectSalesOrder)
                .filter(
                    ProjectSalesOrder.so_id == str(header.id),
                    ProjectSalesOrder.status == SO_STATUS_ADOPTED,
                    ProjectSalesOrder.project_id.is_(None),
                )
                .first()
            )
            if order is not None:
                ProjectSOAdoptionService(self.db).mirror_missing_lines(order)
        return counts

    def _adopt_lines(
        self,
        spec: DocumentSpec,
        header: Any,
        unmatched: list[dict[str, Any]],
        pool: list[Any],
        counts: dict[str, int],
        *,
        dropped_count: int = 0,
    ) -> None:
        """D11: claim ref-less POOL rows for ref-less UNMATCHED incoming lines.

        Three ordered passes over what the PREVIOUS pass left unclaimed:

        1. exact `(product_id, warehouse_id-or-None, outstanding)` key, ties
           among rows/lines sharing one key broken by position;
        2. `(product_id, warehouse_id-or-None)` alone, only where exactly one
           pool row remains for it;
        3. position alone (incoming `line_number` order against the rows' own
           `created_at, id` order), only where the remaining counts agree -
           widened by `dropped_count` (B1 review fix): a line dropped for an
           unresolvable reference never reaches `unmatched` at all, so
           without this its own would-be pool match is never attempted and
           the sweep below cancels/deletes a row that never changed. There is
           no product/warehouse to key an exact match on for a line that
           never resolved, so the protection is positional, same tie-break
           this pass already trusts: the trailing `dropped_count` pool rows
           (by the same `created_at, id` order) are excluded from the sweep
           entirely - never written, never counted as adopted.

        `outstanding` is `qty_ordered - qty_delivered|qty_received`, never
        `qty_ordered` alone - the upload writes `qty_ordered = outstanding` on
        an open xlsx-era line and `fulfilled + outstanding` on update, so
        `qty_ordered` on such a row is not AutoCount's Qty; outstanding is the
        one figure both sides agree on. Mutates `unmatched` and `pool` in
        place, removing whatever it claims - what is left in `unmatched` is a
        genuinely new line, what is left in `pool` is a genuinely stale row
        for the caller's existing delete-or-cancel step.
        """
        delivered_field = spec.line_delivered_field
        all_have_line_number = all(v.get("line_number") is not None for v in unmatched)

        def _position(idx: int, values: dict[str, Any]):
            return values["line_number"] if all_have_line_number else idx

        def _row_created(row: Any):
            return row.created_at or datetime.min

        def _row_key(row: Any):
            ordered = getattr(row, "qty_ordered", None) or Decimal("0")
            delivered = getattr(row, delivered_field, None) or Decimal("0")
            outstanding = (ordered - delivered).quantize(Decimal("0.0001"))
            return (
                str(row.product_id) if row.product_id else None,
                str(row.warehouse_id) if row.warehouse_id else None,
                outstanding,
            )

        def _line_key(values: dict[str, Any]):
            ordered = values.get("qty_ordered") or Decimal("0")
            delivered = values.get(delivered_field) or Decimal("0")
            outstanding = (ordered - delivered).quantize(Decimal("0.0001"))
            product_id = values.get("product_id")
            warehouse_id = values.get("warehouse_id")
            return (
                str(product_id) if product_id else None,
                str(warehouse_id) if warehouse_id else None,
                outstanding,
            )

        claimed_lines: set[int] = set()
        claimed_rows: set[int] = set()

        def _claim(idx: int, values: dict[str, Any], row: Any) -> None:
            values.pop("line_number", None)
            # D22 (review B4): the ref-less adoption path did a blind
            # `setattr` with none of the by-ref branch's conflict recording -
            # an adopted xlsx-era row's warehouse changing between what Order
            # Inquiry set and what AutoCount now states must land in
            # `order_inquiry_conflicts` here too, not only overwritten.
            if spec.entity_type == "sales_orders" and "warehouse_id" in values:
                new_warehouse_id = values["warehouse_id"]
                old_warehouse_id = row.warehouse_id
                if (
                    new_warehouse_id
                    and old_warehouse_id
                    and str(new_warehouse_id) != str(old_warehouse_id)
                ):
                    self.db.add(
                        OrderInquiryConflict(
                            id=str(uuid.uuid4()),
                            company_id=self.company_id,
                            sales_order_id=str(header.id),
                            sales_order_line_id=str(row.id),
                            previous_warehouse_id=old_warehouse_id,
                            new_warehouse_id=new_warehouse_id,
                            source="autocount_esb",
                        )
                    )
            for column, value in values.items():
                setattr(row, column, value)
            counts["adopted"] += 1
            claimed_lines.add(idx)
            claimed_rows.add(id(row))

        # ---- pass 1: exact (product, warehouse, outstanding) key ----
        pool_by_key: dict[tuple, list] = {}
        for row in pool:
            pool_by_key.setdefault(_row_key(row), []).append(row)
        for rows in pool_by_key.values():
            rows.sort(key=lambda r: (_row_created(r), str(r.id)))

        lines_by_key: dict[tuple, list] = {}
        for idx, values in enumerate(unmatched):
            lines_by_key.setdefault(_line_key(values), []).append(idx)
        for indices in lines_by_key.values():
            indices.sort(key=lambda i: _position(i, unmatched[i]))

        for key, indices in lines_by_key.items():
            rows = pool_by_key.get(key, [])
            for idx, row in zip(indices, rows):
                _claim(idx, unmatched[idx], row)

        # ---- pass 2: (product, warehouse) alone, exactly one row remaining ----
        remaining_indices = sorted(
            (i for i in range(len(unmatched)) if i not in claimed_lines),
            key=lambda i: _position(i, unmatched[i]),
        )
        pw_pool: dict[tuple, list] = {}
        for row in pool:
            if id(row) in claimed_rows:
                continue
            pw_pool.setdefault(
                (
                    str(row.product_id) if row.product_id else None,
                    str(row.warehouse_id) if row.warehouse_id else None,
                ),
                [],
            ).append(row)

        for idx in remaining_indices:
            values = unmatched[idx]
            pw_key = (
                str(values.get("product_id")) if values.get("product_id") else None,
                str(values.get("warehouse_id")) if values.get("warehouse_id") else None,
            )
            rows = pw_pool.get(pw_key)
            if rows and len(rows) == 1:
                row = rows[0]
                _claim(idx, values, row)
                pw_pool[pw_key] = []

        # ---- pass 3: position alone, only when the remaining counts agree ----
        # `+ dropped_count` (B1 review fix): a dropped line occupied a real
        # position slot in the incoming payload, so its would-be match is
        # still owed a pool row even though it never reached `unmatched`.
        remaining_indices = [i for i in range(len(unmatched)) if i not in claimed_lines]
        remaining_pool = [r for r in pool if id(r) not in claimed_rows]
        if remaining_pool and len(remaining_indices) + dropped_count == len(remaining_pool):
            remaining_indices.sort(key=lambda i: _position(i, unmatched[i]))
            remaining_pool.sort(key=lambda r: (_row_created(r), str(r.id)))
            claimable, protected = (
                remaining_pool[: len(remaining_indices)],
                remaining_pool[len(remaining_indices):],
            )
            for idx, row in zip(remaining_indices, claimable):
                _claim(idx, unmatched[idx], row)
            for row in protected:
                # Left EXACTLY as it was - never written, never counted as
                # adopted, and (via `claimed_rows` below) never reaching the
                # caller's own leftover sweep either.
                claimed_rows.add(id(row))

        for idx in sorted(claimed_lines, reverse=True):
            del unmatched[idx]
        pool[:] = [r for r in pool if id(r) not in claimed_rows]

    def _diff(self, header: Any, values: dict[str, Any]) -> Optional[dict[str, dict[str, Any]]]:
        """Header values this document would replace, dry run only.

        Lines are not diffed: an operator reviewing a sync is asking what the
        header would lose, and a per-line before/after over a 200-line order is a
        wall of text that buries the one change that matters. The header is where
        the hand-entered values are.
        """
        if not self._dry_run:
            return None
        current = {column: getattr(header, column, None) for column in values}
        return {
            column: {"current": current[column], "incoming": incoming}
            for column, incoming in values.items()
            if _value_changed(current[column], incoming)
        }


def _line_status(header_status: str, qty_ordered: Any, qty_delivered: Any) -> str:
    """`cancelled`, `closed` or `open`, in that order of precedence.

    Cancellation wins over completeness: a cancelled order's fully delivered line
    is still cancelled, and reading it as settled would leave it counted as
    supply that arrived.

    `closed`, not `fulfilled` (fix round 3, parity review): every OTHER writer
    of `sales_order_lines`/`purchase_order_lines.line_status` already uses
    `closed` for a settled line - `outstanding_import_service`'s own upload
    (`_preload_closed_lines`/`_match_closed_line`), `project_so_ingest_service
    .RETIRED_LINE_STATUS`, `project_so_reconciliation_service.CORE_LINE_CLOSED`
    - and the shipping-order side of THIS SAME ingest surface
    (`shipping_order_ingest_service.LINE_CLOSED`) already writes `closed` too.
    A push settling a line was the one writer still spelling it `fulfilled`,
    which is not a word any of those readers looked for.
    """
    if header_status == CANCELLED:
        return CANCELLED
    ordered = qty_ordered or 0
    delivered = qty_delivered or 0
    if ordered > 0 and delivered >= ordered:
        return "closed"
    return "open"


class DocumentReadService:
    """Current document state for a batch of refs, in the ESB's vocabulary.

    Same contract as ``MasterReadService.current_state`` - and the same reason
    for it: the ESB stages changes for human approval and renders a before/after,
    which is not a diff anyone can review unless both sides speak one language.
    So the answer carries `doc_date`, not `order_date`; the canonical status
    word, not the stored one; and the customer's integration REFERENCE, not a
    Sorento UUID the ESB has never seen.

    A master that carries no reference reads back as `null` rather than as an
    invented ref: the customer may have been created in Sorento by hand, and
    handing back something unresolvable would make the next approved sync push a
    link that does not exist.
    """

    def __init__(self, db: Session, *, company_id: str):
        self.db = db
        self.company_id = company_id
        self.refs = IntegrationReferenceService(db, company_id=self.company_id)

    def current_state(self, entity_type: str, source_refs: list[str]) -> dict[str, Any]:
        # The route 404s an unknown entity (`_entity`) and dispatches to this
        # class only for `DOCUMENT_ENTITIES`, so a missing spec here would mean
        # the two lists had come apart, not that a caller asked for something odd.
        spec = DOCUMENT_SPECS[entity_type]

        found: list[dict[str, Any]] = []
        not_found: list[str] = []

        for source_ref in source_refs:
            entity_id = self.refs.resolve(entity_type=entity_type, source_ref=source_ref)
            header = None
            if entity_id is not None:
                header = (
                    self.db.query(spec.header_model)
                    .filter(
                        spec.header_model.id == str(entity_id),
                        # Part of the lookup, not a check after it: another
                        # company's row must read exactly like a row that is not
                        # there, or the caller has two answers to act on.
                        spec.header_model.company_id == self.company_id,
                    )
                    .first()
                )
            if header is None:
                not_found.append(source_ref)
                continue
            found.append(self._record(spec, source_ref, header))

        return {"records": found, "not_found": not_found}

    def _record(self, spec: DocumentSpec, source_ref: str, header: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"source_ref": source_ref, "entity_id": str(header.id)}
        for column, field in spec.header_fields:
            record[field] = getattr(header, column)
        record["status"] = _canonical_status(spec, header.status)
        for column, field, model, _code_field, _name_field in spec.header_refs:
            record[field] = self._ref_of(model, getattr(header, column))
        # `order_type` (D4) is read-only here on purpose - NOT added to
        # `header_fields`, which `_header_values` also uses to build the write
        # side, where it would restate the payload's value unconditionally and
        # break the fill-only rule (AC-V2-1). `PurchaseOrder` has no such
        # column, hence the guard rather than a per-entity literal.
        if hasattr(header, "order_type"):
            record["order_type"] = header.order_type
        # Read back under the payload's own name. Not in `header_fields` for the same
        # reason as `order_type`: the write side is fill-when-sent, not unconditional.
        if hasattr(header, "is_transferable"):
            record["transferable"] = header.is_transferable
        record["lines"] = [
            self._line(spec, line) for line in self._lines(spec, header)
        ]
        return record

    def _lines(self, spec: DocumentSpec, header: Any) -> list[Any]:
        # Insertion order with the id as tie breaker: lines written in one
        # transaction share `created_at` (one now() per transaction), and without
        # the tie breaker Postgres returns them in whatever order it likes.
        return (
            self.db.query(spec.line_model)
            .filter(getattr(spec.line_model, spec.line_fk) == str(header.id))
            .order_by(spec.line_model.created_at, spec.line_model.id)
            .all()
        )

    def _line(self, spec: DocumentSpec, line: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "source_ref": line.source_ref,
            "entity_id": str(line.id),
        }
        for column, field, model, _code_field, _name_field in spec.line_refs:
            record[field] = self._ref_of(model, getattr(line, column))
        for column, field in spec.line_fields:
            record[field] = getattr(line, column)
        return record

    def _ref_of(self, model: type, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        origin = self.refs.origin_of(
            entity_type=model.__tablename__, entity_id=str(entity_id)
        )
        return str(origin.source_ref) if origin is not None else None
