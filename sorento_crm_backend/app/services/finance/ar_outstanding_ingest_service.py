"""Ingest AutoCount OUTSTANDING pushed in by the shared service (AC-BILLING S5, contract 2.10).

One record per debtor, the whole truth for that debtor, through the existing
`/external/ingest` door as `ar_outstanding`. A sibling of `BillingDocumentIngestService` (same
constructor, same `ingest()` / `RecordResult` contract, same per-record SAVEPOINT and dry-run
rollback). It writes three things and creates none:

- `customers.ar_outstanding` / `ar_as_of`: AutoCount's own debtor figure (card Q1 (a)), the
  column the credit warning on a project SO draft already reads;
- `finance.billing_documents.outstanding` / `due_date` / `outstanding_as_of` for each listed
  document of that customer;
- 0 on a stored document of that customer that has an open balance and is not listed: paid.

**The key.** An `integration_references` row `ar_outstanding` -> the customer holds the debtor
ref, so the first push for a ref is `created` and later ones `updated`, and a read-back finds the
customer by it.

**Nothing is created.** The customer must resolve (ref, then code, inside the anchor company) or
the record fails by `customer_ref`. A listed document that is not in the CRM, or is another
customer's or company's, is left alone and counted in one `documents_unknown` warning.

**Stale guard.** An `as_of` older than the stored `customers.ar_as_of` writes nothing:
`unchanged` + `stale_ignored`. A push identical to what is stored is `unchanged` too.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.finance import BillingDocument
from app.models.order import Customer
from app.schemas.canonical_documents import CanonicalArOutstanding, ar_outstanding_errors
from app.services.finance.billing_document_ingest_service import (
    BILLING_DOCUMENTS_ENTITY,
    SOURCE_SYSTEM,
    WARN_STALE_IGNORED,
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
from app.services.master_ref_resolver import MasterRefResolver, dedupe_warnings

logger = logging.getLogger(__name__)

# The entity name this service answers for, read by the route.
AR_OUTSTANDING_ENTITY = "ar_outstanding"
AR_OUTSTANDING_ENTITIES = frozenset({AR_OUTSTANDING_ENTITY})

# Contract 2.10 verdict warning, beside `stale_ignored` (2.6).
WARN_DOCUMENTS_UNKNOWN = "documents_unknown"

_MONEY = Decimal("0.01")
_ZERO = Decimal("0.00")


def _q(value: Optional[Decimal]) -> Optional[Decimal]:
    """Quantised to the column's scale, so a replay compares equal to what Postgres stored."""
    return None if value is None else value.quantize(_MONEY, rounding=ROUND_HALF_UP)


class _CustomerUnresolved(Exception):
    """Neither `customer_ref` nor `customer_code` names a customer of the anchor company."""


class ArOutstandingIngestService(MasterRefResolver):
    """Same constructor and ``ingest()`` / ``RecordResult`` contract as its siblings."""

    # --------------------------------------------------------------- the batch
    def ingest(
        self, entity_type: str, records: list[dict], *, dry_run: bool = False
    ) -> IngestResult:
        if entity_type != AR_OUTSTANDING_ENTITY:
            raise UnsupportedIngestEntity(f"Unsupported outstanding entity {entity_type!r}")
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
            payload = CanonicalArOutstanding(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref, outcome=IngestOutcome.FAILED, errors=_field_errors(exc)
            )
        except TypeError:
            logger.warning(
                "ingest.document_malformed entity=%s source_ref=%s",
                AR_OUTSTANDING_ENTITY,
                source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )
        errors = ar_outstanding_errors(payload)
        if errors:
            return RecordResult(
                source_ref=payload.source_ref, outcome=IngestOutcome.FAILED, errors=errors
            )

        # One debtor per savepoint: a failed flush must not poison the rest of the batch.
        savepoint = self.db.begin_nested()
        try:
            outcome, customer_id, warnings = self._apply(payload)
            savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=outcome,
                entity_id=customer_id,
                warnings=dedupe_warnings(warnings),
            )
        except _CustomerUnresolved:
            savepoint.rollback()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"customer_ref": "no customer of this company matches the ref or code"},
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
                AR_OUTSTANDING_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors=integrity_conflict_errors(exc),
            )
        except Exception:  # noqa: BLE001 - one debtor's failure, not the batch's
            savepoint.rollback()
            # SEC3: never echo a non-domain exception's own message.
            logger.warning(
                "ingest.document_failed entity=%s source_ref=%s",
                AR_OUTSTANDING_ENTITY,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    # ------------------------------------------------------------ one debtor
    def _apply(self, payload: CanonicalArOutstanding) -> tuple[IngestOutcome, str, list[str]]:
        """Pins `company_scope` to the anchor for the whole record, as the siblings do: the
        `X-API-Key` principal's ambient scope is all companies, and every lookup below must
        see the anchor's rows only."""
        with company_scope(self.db, frozenset({self.company_id})):
            return self._apply_scoped(payload)

    def _apply_scoped(
        self, payload: CanonicalArOutstanding
    ) -> tuple[IngestOutcome, str, list[str]]:
        customer = self._customer(payload)
        customer_id = str(customer.id)
        known = self.refs.resolve(entity_type=AR_OUTSTANDING_ENTITY, source_ref=payload.source_ref)
        if (
            customer.ar_as_of is not None
            and payload.as_of < customer.ar_as_of
        ):
            return IngestOutcome.UNCHANGED, customer_id, [WARN_STALE_IGNORED]

        warnings: list[str] = []
        listed, unknown = self._listed_documents(payload, customer_id)
        if unknown:
            warnings.append(WARN_DOCUMENTS_UNKNOWN)

        changed = False
        total = _q(payload.total_outstanding)
        for doc, item in listed:
            values = {
                "outstanding": _q(item.outstanding),
                "due_date": item.due_date,
                "outstanding_as_of": payload.as_of,
            }
            changed |= self._set(doc, values)
        # A stored open balance the push does not list is paid.
        for doc in self._open_documents_not_in(customer_id, {d.id for d, _ in listed}):
            changed |= self._set(doc, {"outstanding": _ZERO, "outstanding_as_of": payload.as_of})
        changed |= self._set(customer, {"ar_outstanding": total, "ar_as_of": payload.as_of})

        if known is None:
            self.refs.link(
                entity_type=AR_OUTSTANDING_ENTITY,
                entity_id=customer_id,
                source_ref=payload.source_ref,
                source_system=SOURCE_SYSTEM,
                integration_id=self.integration_id,
            )
            outcome = IngestOutcome.CREATED
        else:
            if str(known) != customer_id:
                raise ReferenceConflict(
                    f"{payload.source_ref!r} is already linked to another customer",
                    field_name="customer_ref",
                )
            outcome = IngestOutcome.UPDATED if changed else IngestOutcome.UNCHANGED
        if changed:
            self.db.flush()
        return outcome, customer_id, warnings

    @staticmethod
    def _set(row: Any, values: dict[str, Any]) -> bool:
        """Sets what differs; whether anything did. Through the ORM, so the audit listener
        records a billing document's change and an identical push writes nothing."""
        changes = {k: v for k, v in values.items() if getattr(row, k) != v}
        for key, value in changes.items():
            setattr(row, key, value)
        return bool(changes)

    # ------------------------------------------------------------- resolution
    def _customer(self, payload: CanonicalArOutstanding) -> Customer:
        entity_id = None
        if payload.customer_ref:
            entity_id = self.refs.resolve(entity_type="customers", source_ref=payload.customer_ref)
        if entity_id is None and payload.customer_code:
            entity_id = self._resolve_by_code(Customer, payload.customer_code)
        customer = self.db.get(Customer, entity_id) if entity_id else None
        if customer is None or str(customer.company_id) != str(self.company_id):
            raise _CustomerUnresolved()
        return customer

    def _listed_documents(
        self, payload: CanonicalArOutstanding, customer_id: str
    ) -> tuple[list[tuple[BillingDocument, Any]], int]:
        """The listed documents that are this customer's, in the anchor company, and how many
        were not (not in the CRM, another customer's, another company's)."""
        listed: list[tuple[BillingDocument, Any]] = []
        unknown = 0
        for item in payload.documents:
            entity_id = self.refs.resolve(
                entity_type=BILLING_DOCUMENTS_ENTITY, source_ref=item.source_ref
            )
            doc = self.db.get(BillingDocument, entity_id) if entity_id else None
            if (
                doc is None
                or str(doc.company_id) != str(self.company_id)
                or str(doc.customer_id) != customer_id
            ):
                unknown += 1
                continue
            listed.append((doc, item))
        return listed, unknown

    def _open_documents_not_in(
        self, customer_id: str, listed_ids: set[str]
    ) -> list[BillingDocument]:
        rows = (
            self.db.query(BillingDocument)
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.customer_id == customer_id,
                BillingDocument.status != "cancelled",
                BillingDocument.outstanding.isnot(None),
                BillingDocument.outstanding != 0,
            )
            .all()
        )
        return [row for row in rows if row.id not in listed_ids]


class ArOutstandingReadService:
    """Current stored state per debtor ref, inside the anchor company."""

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
                entity_id = self.refs.resolve(entity_type=AR_OUTSTANDING_ENTITY, source_ref=ref)
                customer = self.db.get(Customer, entity_id) if entity_id else None
                if customer is None or str(customer.company_id) != str(self.company_id):
                    not_found.append(ref)
                    continue
                found.append(self._record(ref, customer))
        return {"records": found, "not_found": not_found}

    def _record(self, source_ref: str, customer: Customer) -> dict[str, Any]:
        docs = (
            self.db.query(BillingDocument)
            .filter(
                BillingDocument.company_id == self.company_id,
                BillingDocument.customer_id == customer.id,
                BillingDocument.outstanding.isnot(None),
            )
            .order_by(BillingDocument.source_ref)
            .all()
        )
        return {
            "source_ref": source_ref,
            "entity_id": str(customer.id),
            "customer_id": str(customer.id),
            "customer_code": customer.customer_code,
            "ar_outstanding": _num(customer.ar_outstanding),
            "ar_as_of": customer.ar_as_of.isoformat() if customer.ar_as_of else None,
            "documents": [
                {
                    "source_ref": doc.source_ref,
                    "outstanding": _num(doc.outstanding),
                    "due_date": doc.due_date.isoformat() if doc.due_date else None,
                    "as_of": doc.outstanding_as_of.isoformat() if doc.outstanding_as_of else None,
                }
                for doc in docs
            ],
        }


def _num(value: Optional[Decimal]) -> Optional[float]:
    """Money reads back as a JSON number, the shape it was pushed in."""
    return None if value is None else float(value)


__all__ = [
    "AR_OUTSTANDING_ENTITY",
    "AR_OUTSTANDING_ENTITIES",
    "WARN_DOCUMENTS_UNKNOWN",
    "ArOutstandingIngestService",
    "ArOutstandingReadService",
]
