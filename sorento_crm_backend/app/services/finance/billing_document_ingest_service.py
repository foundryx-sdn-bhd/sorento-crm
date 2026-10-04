"""Ingest AutoCount BILLING DOCUMENTS pushed in by the shared service (finance S0, #1309).

Invoices, cash sales, credit notes and debit notes, one typed table
(`finance.billing_documents`, plan 3.1), through the existing `/external/ingest` door as
`billing_documents` (contract 2.6, plan 3.3). A sibling of `ShippingOrderIngestService`
(same constructor, same `ingest()` / `RecordResult` contract, same per-record SAVEPOINT and
dry-run rollback), deliberately simpler than `DocumentIngestService`:

**No adopt-by-number step.** No billing row predates the feed, so a header is found by its
`integration_references` row (the idempotency key), with the table's unique
`(company_id, document_type, source_ref)` as the database backstop, or it is created.

**Masters are linked, never made, never waited for.** Customer, sales agent and product
resolve ref then code inside the anchor company. One that does not resolve lands NULL with
its code kept and a warning (`customer_unresolved`, `agent_unresolved`,
`product_unresolved`); the record is never retryable and a master is never back-created. A
billing document is money: holding it back or dropping a line would change a total.

**A push is the whole document.** Lines not in the push are deleted (nothing references a
billing line); a line present keeps its id. A push identical to what is stored writes nothing
at all and answers `unchanged`, which is what makes a replay safe (UAC S0-5).

**Stale guard.** When both the stored and the incoming `source_modified_at` exist and the
incoming one is older, nothing is written: `unchanged` + `stale_ignored` (UAC S0-9). The
backfill and the live feed are two writers of the same documents that can overlap in time.

**Cancel is an update.** `status: "cancelled"` keeps the row and its lines (ruling Q13).

**The sales order type (S1, ruling Q14).** Every push that writes re-decides the header's
`demand_class` with `classify_document`, the ladder the weekly upload and the SO ingest
share: the class of the sales order its lowest-numbered linked line came from (for a note
with none, the class of the document it is against), then the document's OWN agent's class
(ruling Q15), then the customer's market segment. NULL when nothing classifies, with no new
warning (the contract 2.6 vocabulary is unchanged); the report reads it as `(blank)`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.finance import (
    CANCELLED,
    CASH_SALE,
    CREDIT_NOTE,
    DEBIT_NOTE,
    INVOICE,
    BillingDocument,
    BillingDocumentLine,
)
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.schemas.canonical_documents import (
    CanonicalBillingDocument,
    CanonicalBillingDocumentLine,
    billing_document_errors,
)
from app.services.integration_reference_service import (
    IntegrationReferenceService,
    ReferenceConflict,
)
from app.services.master_ingest_service import (
    INTERNAL_ERROR_MESSAGE,
    IngestOutcome,
    IngestResult,
    RecordResult,
    UnsupportedIngestEntity,
    _field_errors,
    integrity_conflict_errors,
)
from app.services.master_ref_resolver import (
    WARN_CUSTOMER_UNRESOLVED,
    MasterRefResolver,
    dedupe_warnings,
)
from app.services.scm.demand_class import classify_document
from app.services.scm.sales_agent_service import normalize_code

logger = logging.getLogger(__name__)

# The entity name this service answers for, read by the route and by `deletion_service`.
BILLING_DOCUMENTS_ENTITY = "billing_documents"
BILLING_DOCUMENT_ENTITIES = frozenset({BILLING_DOCUMENTS_ENTITY})

SOURCE_SYSTEM = "autocount"
DEFAULT_CURRENCY = "MYR"

# Fixed verdict-warning vocabulary (contract 2.6), rendered verbatim by the shared service.
# `customer_unresolved` is the existing one from `master_ref_resolver`.
WARN_AGENT_UNRESOLVED = "agent_unresolved"
WARN_PRODUCT_UNRESOLVED = "product_unresolved"
WARN_STALE_IGNORED = "stale_ignored"

# A credit or debit note names the document it is against (ruling Q4). What it can be
# against: an invoice, a debit note, or a cash sale (AutoCount credits a cash sale too).
AGAINST_SOURCE_TYPES = frozenset({CREDIT_NOTE, DEBIT_NOTE})
AGAINST_TARGET_TYPES = (INVOICE, CASH_SALE, DEBIT_NOTE)

_MONEY = Decimal("0.01")
_FOUR = Decimal("0.0001")
_RATE = Decimal("0.00000001")

# Header / line columns compared and written on every push, in one place so a column added
# to the model cannot be written on create and forgotten on update.
_HEADER_FIELDS = (
    "document_type",
    "doc_no",
    "doc_date",
    "status",
    "source_modified_at",
    "customer_id",
    "debtor_code",
    "customer_name",
    "sales_agent_id",
    "agent_code",
    "currency_code",
    "currency_rate",
    "net_total",
    "tax_total",
    "total",
    "local_net_total",
    "against_document_id",
    "against_doc_no",
    "ref",
    "description",
    "demand_class",
    "source_system",
)
_LINE_FIELDS = (
    "line_no",
    "product_id",
    "item_code",
    "description",
    "uom",
    "quantity",
    "unit_price",
    "discount_amount",
    "net_amount",
    "tax_code",
    "tax_rate",
    "tax_amount",
    "line_total",
    "sales_order_line_id",
    "from_doc_type",
    "from_doc_no",
    "from_line_ref",
)


def _q(value: Optional[Decimal], exp: Decimal) -> Optional[Decimal]:
    """Quantised to the column's scale, so a replay compares equal to what Postgres stored."""
    return None if value is None else value.quantize(exp, rounding=ROUND_HALF_UP)


class _DocumentTypeChanged(Exception):
    """The ref already names a document of another type (A2: refs carry the type)."""


@dataclass
class _Verdict:
    outcome: IngestOutcome
    entity_id: Optional[str]
    warnings: list[str]
    lines: dict[str, int] = field(
        default_factory=lambda: {"created": 0, "updated": 0, "deleted": 0}
    )


class BillingDocumentIngestService(MasterRefResolver):
    """Same constructor and ``ingest()`` / ``RecordResult`` contract as its siblings.

    Subclasses `MasterRefResolver` for its anchor-scoped refs service and its code lookups
    (`_resolve_by_code`), never for its back-creating rungs.
    """

    def __init__(self, db: Session, integration_id: Optional[str], *, company_id: str):
        super().__init__(db, integration_id, company_id=company_id)
        # Per batch, for `classify_document`'s customer-segment rung.
        self._segment_cache: dict = {}

    # --------------------------------------------------------------- the batch
    def ingest(
        self, entity_type: str, records: list[dict], *, dry_run: bool = False
    ) -> IngestResult:
        if entity_type != BILLING_DOCUMENTS_ENTITY:
            raise UnsupportedIngestEntity(f"Unsupported billing entity {entity_type!r}")
        result = IngestResult(dry_run=dry_run)
        try:
            for raw in records:
                result.records.append(self._ingest_one(raw))
        finally:
            if dry_run:
                # In a finally, so an error mid-batch cannot leave a partially applied
                # preview in the session for whatever commits next.
                self.db.rollback()
        return result

    def _ingest_one(self, raw: Any) -> RecordResult:
        source_ref = raw.get("source_ref") if isinstance(raw, dict) else None
        try:
            payload = CanonicalBillingDocument(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref, outcome=IngestOutcome.FAILED, errors=_field_errors(exc)
            )
        except TypeError:
            logger.warning(
                "ingest.document_malformed entity=%s source_ref=%s",
                BILLING_DOCUMENTS_ENTITY,
                source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )
        try:
            errors = billing_document_errors(payload)
        except ArithmeticError:
            # Unreachable through the schema's bounds; kept so one record's arithmetic can
            # never escape the batch loop and turn a 1000-record push into a 500.
            errors = {"total": "not computable"}
        if errors:
            return RecordResult(
                source_ref=payload.source_ref, outcome=IngestOutcome.FAILED, errors=errors
            )

        # One document per savepoint: a failed flush must not poison the rest of the batch.
        savepoint = self.db.begin_nested()
        try:
            verdict = self._apply(payload)
            savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=verdict.outcome,
                entity_id=verdict.entity_id,
                warnings=dedupe_warnings(verdict.warnings),
                lines=verdict.lines,
            )
        except _DocumentTypeChanged as exc:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"document_type": str(exc)},
            )
        except ReferenceConflict as exc:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={exc.field_name: str(exc)},
            )
        except IntegrityError as exc:
            savepoint.rollback()
            logger.warning(
                "ingest.integrity_conflict entity=%s source_ref=%s",
                BILLING_DOCUMENTS_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors=integrity_conflict_errors(exc),
            )
        except Exception:  # noqa: BLE001 - one document's failure, not the batch's
            savepoint.rollback()
            # SEC3: never echo a non-domain exception's own message.
            logger.warning(
                "ingest.document_failed entity=%s source_ref=%s",
                BILLING_DOCUMENTS_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    # ------------------------------------------------------------ one document
    def _apply(self, payload: CanonicalBillingDocument) -> _Verdict:
        """Pins `company_scope` to the anchor for the whole record, as the siblings do: the
        `X-API-Key` principal's ambient scope is all companies, and every lookup below must
        see the anchor's rows only."""
        with company_scope(self.db, frozenset({self.company_id})):
            return self._apply_scoped(payload)

    def _apply_scoped(self, payload: CanonicalBillingDocument) -> _Verdict:
        existing = self._find(payload)
        if existing is not None and existing.document_type != payload.document_type:
            raise _DocumentTypeChanged(
                f"{payload.source_ref!r} already names a {existing.document_type}; a ref must "
                "carry its document type ({database}:{IV|CS|CN|DN}:{DocKey})"
            )
        if (
            existing is not None
            and existing.source_modified_at is not None
            and payload.source_modified_at is not None
            and payload.source_modified_at < existing.source_modified_at
        ):
            return _Verdict(IngestOutcome.UNCHANGED, str(existing.id), [WARN_STALE_IGNORED])

        # Everything resolved before anything is written.
        warnings: list[str] = []
        header = self._header_values(payload, existing, warnings)
        lines = [self._line_values(line, warnings) for line in payload.lines]
        header["demand_class"] = self._demand_class(header, lines)

        if existing is None:
            return self._create(payload, header, lines, warnings)
        return self._update(payload, existing, header, lines, warnings)

    def _find(self, payload: CanonicalBillingDocument) -> Optional[BillingDocument]:
        entity_id = self.refs.resolve(
            entity_type=BILLING_DOCUMENTS_ENTITY, source_ref=payload.source_ref
        )
        if entity_id is not None:
            doc = self.db.get(BillingDocument, entity_id)
            if doc is not None and str(doc.company_id) == str(self.company_id):
                return doc
        # The backstop: a row with no reference (only reachable if the reference row was
        # removed by hand). Found by the table's own unique key and re-linked on write.
        return (
            self.db.query(BillingDocument)
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.document_type == payload.document_type,
                BillingDocument.source_ref == payload.source_ref,
            )
            .one_or_none()
        )

    def _create(
        self,
        payload: CanonicalBillingDocument,
        header: dict[str, Any],
        lines: list[dict[str, Any]],
        warnings: list[str],
    ) -> _Verdict:
        now = datetime.utcnow()
        doc = BillingDocument(
            company_id=self.company_id,
            source_ref=payload.source_ref,
            last_synced_at=now,
            **header,
        )
        self.db.add(doc)
        self.db.flush()
        for values in lines:
            self.db.add(
                BillingDocumentLine(company_id=self.company_id, document_id=doc.id, **values)
            )
        self.db.flush()
        self._link(doc, payload)
        self._fill_waiting_notes(doc)
        self._redecide_notes(doc)
        return _Verdict(
            IngestOutcome.CREATED,
            str(doc.id),
            warnings,
            {"created": len(lines), "updated": 0, "deleted": 0},
        )

    def _update(
        self,
        payload: CanonicalBillingDocument,
        doc: BillingDocument,
        header: dict[str, Any],
        lines: list[dict[str, Any]],
        warnings: list[str],
    ) -> _Verdict:
        counts = {"created": 0, "updated": 0, "deleted": 0}
        header_changes = {k: v for k, v in header.items() if getattr(doc, k) != v}

        stored = {line.source_ref: line for line in doc.lines}
        incoming = {values["source_ref"]: values for values in lines}
        line_changes: list[tuple[BillingDocumentLine, dict[str, Any]]] = []
        for ref, values in incoming.items():
            row = stored.get(ref)
            if row is None:
                counts["created"] += 1
                continue
            changes = {k: v for k, v in values.items() if getattr(row, k) != v}
            if changes:
                counts["updated"] += 1
                line_changes.append((row, changes))
        removed = [row for ref, row in stored.items() if ref not in incoming]
        counts["deleted"] = len(removed)

        if not header_changes and not line_changes and not counts["created"] and not removed:
            # Identical to what is stored: nothing is written, not even `last_synced_at`,
            # so a replay moves no `updated_at` and writes no audit row (UAC S0-5).
            return _Verdict(IngestOutcome.UNCHANGED, str(doc.id), warnings, counts)

        for key, value in header_changes.items():
            setattr(doc, key, value)
        doc.last_synced_at = datetime.utcnow()
        for row, changes in line_changes:
            for key, value in changes.items():
                setattr(row, key, value)
        for row in removed:
            doc.lines.remove(row)
        for ref, values in incoming.items():
            if ref not in stored:
                doc.lines.append(
                    BillingDocumentLine(company_id=self.company_id, document_id=doc.id, **values)
                )
        self.db.flush()
        self._link(doc, payload)
        self._fill_waiting_notes(doc)
        self._redecide_notes(doc)
        return _Verdict(IngestOutcome.UPDATED, str(doc.id), warnings, counts)

    def _link(self, doc: BillingDocument, payload: CanonicalBillingDocument) -> None:
        self.refs.link(
            entity_type=BILLING_DOCUMENTS_ENTITY,
            entity_id=str(doc.id),
            source_ref=payload.source_ref,
            source_system=SOURCE_SYSTEM,
            source_doc_no=payload.doc_no,
            integration_id=self.integration_id,
        )

    # ------------------------------------------------------------- resolution
    def _header_values(
        self,
        payload: CanonicalBillingDocument,
        existing: Optional[BillingDocument],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "document_type": payload.document_type,
            "doc_no": payload.doc_no,
            "doc_date": payload.doc_date,
            "status": payload.status,
            "source_modified_at": payload.source_modified_at,
            "customer_id": self._customer(payload, warnings),
            "debtor_code": payload.customer_code,
            "customer_name": payload.customer_name,
            "sales_agent_id": self._agent(payload.agent_code, warnings),
            "agent_code": payload.agent_code,
            "currency_code": payload.currency_code or DEFAULT_CURRENCY,
            "currency_rate": _q(payload.currency_rate or Decimal("1"), _RATE),
            "net_total": _q(payload.net_total, _MONEY),
            "tax_total": _q(payload.tax_total, _MONEY),
            "total": _q(payload.total, _MONEY),
            "local_net_total": _q(payload.local_net_total, _MONEY),
            "against_document_id": self._against(
                payload, str(existing.id) if existing is not None else None
            ),
            "against_doc_no": payload.against_doc_no,
            "ref": payload.ref,
            "description": payload.description,
            "source_system": SOURCE_SYSTEM,
        }

    def _line_values(
        self, line: CanonicalBillingDocumentLine, warnings: list[str]
    ) -> dict[str, Any]:
        return {
            "source_ref": line.source_ref,
            "line_no": line.line_number,
            "product_id": self._product(line, warnings),
            "item_code": line.product_code,
            "description": line.description,
            "uom": line.uom,
            "quantity": _q(line.quantity, _FOUR),
            "unit_price": _q(line.unit_price, _FOUR),
            "discount_amount": _q(line.discount_amount, _MONEY),
            "net_amount": _q(line.net_amount, _MONEY),
            "tax_code": line.tax_code,
            "tax_rate": _q(line.tax_rate, _FOUR),
            "tax_amount": _q(line.tax_amount, _MONEY),
            "line_total": _q(line.line_total, _MONEY),
            "sales_order_line_id": self._so_line(line.from_line_ref),
            "from_doc_type": line.from_doc_type,
            "from_doc_no": line.from_doc_no,
            "from_line_ref": line.from_line_ref,
        }

    def _by_ref(self, model: type, ref: Optional[str]) -> Optional[str]:
        """A master's id by its integration reference, inside the anchor; None on a miss.

        Unlike `MasterRefResolver._resolve_ref`, a miss is not `MissingReference`: nothing
        here is ever retryable.
        """
        if not ref:
            return None
        key = (model.__tablename__, "ref", ref)
        if key not in self._memo:
            self._memo[key] = self.refs.resolve(entity_type=model.__tablename__, source_ref=ref)
        return self._memo[key]

    def _customer(self, payload: CanonicalBillingDocument, warnings: list[str]) -> Optional[str]:
        entity_id = self._by_ref(Customer, payload.customer_ref)
        if entity_id is None and payload.customer_code:
            entity_id = self._resolve_by_code(Customer, payload.customer_code)
        if entity_id is None and (payload.customer_ref or payload.customer_code):
            warnings.append(WARN_CUSTOMER_UNRESOLVED)
        return entity_id

    def _agent(self, code: Optional[str], warnings: list[str]) -> Optional[str]:
        """A sales agent by code: a shared row (no company) or one of the anchor's own."""
        normalized = normalize_code(code)
        if not normalized:
            return None
        key = (SalesAgent.__tablename__, "code", normalized)
        if key not in self._memo:
            row = (
                self.db.query(SalesAgent.id)
                .filter(
                    func.upper(func.btrim(SalesAgent.sales_agent)) == normalized,
                    or_(
                        SalesAgent.company_id.is_(None),
                        SalesAgent.company_id == self.company_id,
                    ),
                )
                .order_by(SalesAgent.company_id.is_(None))
                .first()
            )
            self._memo[key] = str(row[0]) if row else None
        entity_id = self._memo[key]
        if entity_id is None:
            warnings.append(WARN_AGENT_UNRESOLVED)
        return entity_id

    def _product(self, line: CanonicalBillingDocumentLine, warnings: list[str]) -> Optional[str]:
        # PRODUCT-REF-COLLISION (owner ruling 3 Oct): product identity is the
        # code only; the ref is never consulted for a product.
        entity_id = self._resolve_by_code(Product, line.product_code) if line.product_code else None
        if entity_id is None and (line.product_ref or line.product_code):
            warnings.append(WARN_PRODUCT_UNRESOLVED)
        return entity_id

    def _so_line(self, ref: Optional[str]) -> Optional[str]:
        """The anchor's sales order line whose `source_ref` this is; None unless exactly one."""
        if not ref:
            return None
        key = (SalesOrderLine.__tablename__, "ref", ref)
        if key not in self._memo:
            rows = (
                self.db.query(SalesOrderLine.id)
                .filter(
                    SalesOrderLine.company_id == self.company_id,
                    SalesOrderLine.source_ref == ref,
                )
                .limit(2)
                .all()
            )
            self._memo[key] = str(rows[0][0]) if len(rows) == 1 else None
        return self._memo[key]

    def _demand_class(
        self, header: dict[str, Any], lines: list[dict[str, Any]]
    ) -> Optional[str]:
        """The sales order type this document was billed from, by the one ladder (plan
        3.4). The billing record states no order type, so the "stored order type" rung is
        the order its first linked line came from, else the document a note is against."""
        stored = self._order_class(lines) or self._document_class(header["against_document_id"])
        return self._classify(stored, header["sales_agent_id"], header["debtor_code"],
                              header["customer_id"])

    def _classify(
        self,
        stored: Optional[str],
        agent_id: Optional[str],
        debtor_code: Optional[str],
        customer_id: Optional[str],
    ) -> Optional[str]:
        return classify_document(
            self.db,
            stored_order_type=stored,
            stated_order_type=None,
            agent_demand_class=self._agent_class(agent_id),
            debtor_code=debtor_code or self._customer_code(customer_id),
            company_id=self.company_id,
            segment_cache=self._segment_cache,
        )

    def _redecide_notes(self, doc: BillingDocument) -> None:
        """The notes against this document follow its class (a CN reduces the block its
        invoice counts in), whichever arrived first and however the document was re-decided
        since. A note billed from a sales order of its own keeps that order's class.
        Through the ORM, so the audit listener records the change."""
        if doc.document_type not in AGAINST_TARGET_TYPES:
            return
        notes = (
            self.db.query(BillingDocument)
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.document_type.in_(AGAINST_SOURCE_TYPES),
                BillingDocument.against_document_id == doc.id,
                BillingDocument.id != doc.id,
            )
            .all()
        )
        changed = False
        for note in notes:
            if any(line.sales_order_line_id for line in note.lines):
                continue
            cls = self._classify(
                doc.demand_class, note.sales_agent_id, note.debtor_code, note.customer_id
            )
            if cls != note.demand_class:
                note.demand_class = cls
                changed = True
        if changed:
            self.db.flush()

    def _order_class(self, lines: list[dict[str, Any]]) -> Optional[str]:
        linked = [v for v in lines if v["sales_order_line_id"]]
        if not linked:
            return None
        first = min(linked, key=lambda v: (v["line_no"] is None, v["line_no"] or 0))
        line_id = first["sales_order_line_id"]
        key = (SalesOrder.__tablename__, "class_of_line", line_id)
        if key not in self._memo:
            self._memo[key] = (
                self.db.query(SalesOrder.demand_class)
                .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
                .filter(SalesOrderLine.id == line_id)
                .scalar()
            )
        return self._memo[key]

    def _document_class(self, document_id: Optional[str]) -> Optional[str]:
        if not document_id:
            return None
        return (
            self.db.query(BillingDocument.demand_class)
            .filter(BillingDocument.id == document_id)
            .scalar()
        )

    def _agent_class(self, agent_id: Optional[str]) -> Optional[str]:
        if not agent_id:
            return None
        key = (SalesAgent.__tablename__, "class", agent_id)
        if key not in self._memo:
            self._memo[key] = (
                self.db.query(SalesAgent.demand_class).filter(SalesAgent.id == agent_id).scalar()
            )
        return self._memo[key]

    def _customer_code(self, customer_id: Optional[str]) -> Optional[str]:
        if not customer_id:
            return None
        return self.db.query(Customer.customer_code).filter(Customer.id == customer_id).scalar()

    def _against(
        self, payload: CanonicalBillingDocument, self_id: Optional[str]
    ) -> Optional[str]:
        """A credit or debit note's document: `against_source_ref`, else the one document of
        a target type in the company whose `doc_no` is `against_doc_no`. None when nothing,
        or more than one, matches: the number is kept either way."""
        if payload.document_type not in AGAINST_SOURCE_TYPES:
            return None
        if payload.against_source_ref:
            target = self.refs.resolve(
                entity_type=BILLING_DOCUMENTS_ENTITY, source_ref=payload.against_source_ref
            )
            if target is not None and target != self_id:
                row = self.db.get(BillingDocument, target)
                # Only a document a note can be against; a ref naming another note (an
                # upstream mistake) falls through to the number.
                if row is not None and row.document_type in AGAINST_TARGET_TYPES:
                    return target
        if payload.against_doc_no:
            query = self.db.query(BillingDocument.id).filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.document_type.in_(AGAINST_TARGET_TYPES),
                BillingDocument.doc_no == payload.against_doc_no,
            )
            if self_id is not None:
                query = query.filter(BillingDocument.id != self_id)
            rows = query.limit(2).all()
            if len(rows) == 1:
                return str(rows[0][0])
        return None

    def _fill_waiting_notes(self, doc: BillingDocument) -> None:
        """When a target document lands, link the credit and debit notes already waiting on
        its number (UAC S0-12). Through the ORM, so the audit listener records the change.
        Skipped when its number is not unique among the targets: a guess is not a link."""
        if doc.document_type not in AGAINST_TARGET_TYPES:
            return
        same_number = (
            self.db.query(func.count(BillingDocument.id))
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.document_type.in_(AGAINST_TARGET_TYPES),
                BillingDocument.doc_no == doc.doc_no,
            )
            .scalar()
        )
        if same_number != 1:
            return
        waiting = (
            self.db.query(BillingDocument)
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.document_type.in_(AGAINST_SOURCE_TYPES),
                BillingDocument.against_document_id.is_(None),
                BillingDocument.against_doc_no == doc.doc_no,
                BillingDocument.id != doc.id,
            )
            .all()
        )
        for note in waiting:
            note.against_document_id = doc.id
        if waiting:
            self.db.flush()


class BillingDocumentReadService:
    """Current canonical state for a batch of refs (UAC S0-17), inside the anchor company."""

    def __init__(self, db: Session, *, company_id: str):
        self.db = db
        self.company_id = company_id
        self.refs = IntegrationReferenceService(db, company_id=self.company_id)

    def current_state(self, entity_type: str, source_refs: list[str]) -> dict[str, Any]:
        found: list[dict[str, Any]] = []
        not_found: list[str] = []
        with company_scope(self.db, frozenset({self.company_id})):
            for source_ref in source_refs:
                ref = source_ref if isinstance(source_ref, str) else str(source_ref)
                entity_id = self.refs.resolve(entity_type=BILLING_DOCUMENTS_ENTITY, source_ref=ref)
                doc = self.db.get(BillingDocument, entity_id) if entity_id else None
                if doc is None or str(doc.company_id) != str(self.company_id):
                    not_found.append(ref)
                    continue
                found.append(self._record(ref, doc))
        return {"records": found, "not_found": not_found}

    def _record(self, source_ref: str, doc: BillingDocument) -> dict[str, Any]:
        return {
            "source_ref": source_ref,
            "entity_id": str(doc.id),
            "document_type": doc.document_type,
            "doc_no": doc.doc_no,
            "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
            "status": doc.status,
            "source_modified_at": (
                doc.source_modified_at.isoformat() if doc.source_modified_at else None
            ),
            "customer_ref": self._ref_of("customers", doc.customer_id),
            "customer_code": doc.debtor_code,
            "customer_name": doc.customer_name,
            "agent_code": doc.agent_code,
            "currency_code": doc.currency_code,
            "currency_rate": _num(doc.currency_rate),
            "net_total": _num(doc.net_total),
            "tax_total": _num(doc.tax_total),
            "total": _num(doc.total),
            "local_net_total": _num(doc.local_net_total),
            "against_doc_no": doc.against_doc_no,
            "against_source_ref": self._ref_of(BILLING_DOCUMENTS_ENTITY, doc.against_document_id),
            "ref": doc.ref,
            "description": doc.description,
            "lines": [self._line(line) for line in doc.lines],
        }

    def _line(self, line: BillingDocumentLine) -> dict[str, Any]:
        return {
            "entity_id": str(line.id),
            "source_ref": line.source_ref,
            "line_number": line.line_no,
            "product_ref": self._ref_of("products", line.product_id),
            "product_code": line.item_code,
            "description": line.description,
            "uom": line.uom,
            "quantity": _num(line.quantity),
            "unit_price": _num(line.unit_price),
            "discount_amount": _num(line.discount_amount),
            "net_amount": _num(line.net_amount),
            "tax_code": line.tax_code,
            "tax_rate": _num(line.tax_rate),
            "tax_amount": _num(line.tax_amount),
            "line_total": _num(line.line_total),
            "from_doc_type": line.from_doc_type,
            "from_doc_no": line.from_doc_no,
            "from_line_ref": line.from_line_ref,
        }

    def _ref_of(self, entity_type: str, entity_id: Optional[str]) -> Optional[str]:
        if not entity_id:
            return None
        origin = self.refs.origin_of(entity_type=entity_type, entity_id=str(entity_id))
        return str(origin.source_ref) if origin is not None else None


def _num(value: Optional[Decimal]) -> Optional[float]:
    """Money and quantities read back as JSON numbers, the shape they were pushed in."""
    return None if value is None else float(value)


__all__ = [
    "BILLING_DOCUMENTS_ENTITY",
    "BILLING_DOCUMENT_ENTITIES",
    "CANCELLED",
    "WARN_AGENT_UNRESOLVED",
    "WARN_PRODUCT_UNRESOLVED",
    "WARN_STALE_IGNORED",
    "BillingDocumentIngestService",
    "BillingDocumentReadService",
]
