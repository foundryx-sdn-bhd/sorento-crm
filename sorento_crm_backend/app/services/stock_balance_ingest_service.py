"""Ingest AutoCount stock balance pushes (contract 2.5, Foundryx SR5).

PLAN: `documentation/plans/autocount/PLAN-ingest-stock-balances-2-5.md` D1-D11.
UAC:  `documentation/plans/autocount/ingest-stock-balances-2-5-acceptance-criteria.md`.

`stock_balances` is not a master, a document or a shipping order - it never
back-creates, never adopts, never touches `integration_references` at all
(D3: `source_ref` is an echo key only, never stored). Its identity is the
(product, warehouse) pair, resolved fresh on every push - a small enough shape
that it gets its own service rather than another branch inside
`MasterIngestService`, which the module docstring there says is already
carrying three different fused semantics.

**Same envelope, different resolution.** `ingest()` returns the exact same
`IngestResult`/`RecordResult` dataclasses `MasterIngestService` does, so the
wire shape at `/external/ingest/stock_balances` is byte-identical in kind to
every other entity on this surface - only the resolution ladder underneath is
new. `delete()` does the same with `deletion_service`'s `DeletionResult`/
`DeletionRecordResult`.

**Only `quantity_on_hand` is ever written.** `reserved`, `damaged`,
`reorder_point` and `zone_id` are set by the CRM side (manual counts, picks,
zoning) and AutoCount has no opinion on any of them - D4's upsert touches one
column, full stop, on both a create (the model's own defaults handle the
rest) and an update (every other column is left exactly as it was).

**A deletion zeroes, it never removes.** D7: AutoCount deleting a stock
balance means "this pair no longer holds any quantity", not "forget this
pair ever existed" - the row (and whatever ledger/audit history points at
it) stays, at zero.

**Warehouse resolution never raises.** Unlike a product miss (retryable -
the row may simply not have synced yet), an unresolved or inactive warehouse
is `updated` with a fixed warning and nothing written - the same shape
`MasterRefResolver` already gives warehouses on every other ingest surface,
reused here via `WARN_WAREHOUSE_UNRESOLVED`.

**One preload per batch, plain dicts.** `_build_preload` reads every
warehouse of the anchor company, every product whose code the batch actually
names, and every stock row for those products - once, before the per-record
loop, the same "avoid the N+1" reasoning `MasterIngestService`'s own
`_ProductBatchPreload` documents at length, but as three bare dicts on
`self` rather than a second class: this service has one preload shape, not
several. A record's own write updates `self._stock_by_pair` in place, so a
duplicate pair later in the SAME batch sees it (AC-SB-13); a record whose
savepoint rolls back removes whatever IT added via `self._pending_stock_keys`,
the same revert-on-rollback shape `_pending_preload_additions` gives that
other preload.

**Exact code match wins; more than one normalised match is ambiguous.**
`warehouse_code`/`product_code` are unique per company only as the RAW stored
string (migration 305) - two rows spelled `"MBS"` and `"mbs "` can coexist.
An incoming code matching one of them exactly (after the pydantic layer's own
trim) is resolved to that row without further question; failing that, more
than one row sharing its case/whitespace-insensitive form cannot be told
apart and the record is refused rather than guessed at.

**A same-pair insert race is a conflict, not a retry loop.** Two pushes for
the same never-before-seen pair, close enough together, can both find no
existing row and both attempt the same INSERT - the DB's own
`uq_stock_product_id_warehouse_id` index catches the loser. The loser
re-reads (company-scoped) and switches to an UPDATE, so the last value still
wins; a row that STILL cannot be found company-scoped despite the conflict
means the pair's id pair already belongs to a different company (that
constraint carries no company column of its own), a data defect refused as
`failed` rather than silently adopted.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, StrictInt, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.ingest_extras import INGEST_MODEL_CONFIG, note_unknown_fields
from app.models.inventory import Stock, StockLedger
from app.services.deletion_service import (
    DeletionOutcome,
    DeletionRecordResult,
    DeletionResult,
)
from app.services.master_ingest_service import (
    INTERNAL_ERROR_MESSAGE,
    IngestOutcome,
    IngestResult,
    RecordResult,
    _field_errors,
)
from app.services.master_ref_resolver import WARN_WAREHOUSE_UNRESOLVED

logger = logging.getLogger(__name__)

#: D1. The one entity this service ingests, folded into `SUPPORTED_ENTITIES`
#: (`app/api/v1/external/ingest.py`) the same way `SHIPPING_ORDER_ENTITIES` is.
STOCK_BALANCE_ENTITIES = {"stock_balances"}

#: D2. The one new warning contract 2.5 introduces. A match WAS found (unlike
#: `WARN_WAREHOUSE_UNRESOLVED`) - it simply is not a place stock can be
#: booked into, so nothing is written for it either.
WARN_WAREHOUSE_INACTIVE = "warehouse_inactive"

#: Fix round 2 (#1257): the Stock Ledger `transaction_type` a push writes when
#: it CHANGES a quantity, beside the upload's `BULK_IMPORT`.
AUTOCOUNT_PUSH = "AUTOCOUNT_PUSH"

#: Postgres' own code for a unique-constraint violation, read off
#: `exc.orig.pgcode` (never `str(exc)` - see `integrity_conflict_errors`'s own
#: docstring in `master_ingest_service` for why).
_UNIQUE_VIOLATION_PGCODE = "23505"

#: Fix round 1 (security S1): Postgres `Integer` (int4) tops out at
#: 2,147,483,647 - the `stock.quantity_on_hand` column's own type. A qty
#: above it can never be stored, so it is refused at validation rather than
#: reaching a DB-level `IntegrityError` (or, on some drivers, silent
#: wraparound) at flush time.
_MAX_QTY = 2_147_483_647


class _StockBalanceRecord(BaseModel):
    """D3, as amended by the owner (1 Oct 2026): an unmapped key is dropped and
    its NAME logged once per request (`app.schemas.ingest_extras`), never
    refused. `item_description`/`uom_code` are declared and ignored, display
    fields Sorento already resolves the product/uom by code.

    `qty` is a strict, bounded, non-negative int: a bool, a float or a
    numeric string are all exactly the kind of upstream mapping slip this
    validation exists to catch (AC-SB-9) - `0` is a legitimate, and common,
    incoming value; a value above `_MAX_QTY` cannot be stored at all.
    """

    model_config = INGEST_MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _note_unknown(cls, data):
        note_unknown_fields(cls, data)
        return data

    source_ref: str = Field(..., min_length=1, max_length=255)
    item_code: str = Field(..., min_length=1, max_length=255)
    location_code: str = Field(..., min_length=1, max_length=255)
    qty: StrictInt = Field(..., ge=0, le=_MAX_QTY)
    item_description: Optional[str] = None
    uom_code: Optional[str] = None


def _is_stock_pair_conflict(exc: IntegrityError) -> bool:
    """Whether `exc` is the insert race this service knows how to retry
    around. Matches on pgcode alone (fix round 2, reviewer N-a) - not a
    specific constraint name: production's own copy of `uq_stock_product_id_
    warehouse_id` (`sb2_stock_pair_unique.py`) did not always carry that
    exact name, and the only OTHER unique key the create branch's own INSERT
    can violate is the primary key, which is server-generated per record and
    therefore never actually collides in practice. A unique-violation pgcode
    on this specific statement can only mean this pair's own unique key,
    whatever it is named on the database this code happens to be running
    against."""
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None)
    return pgcode == _UNIQUE_VIOLATION_PGCODE


class StockBalanceIngestService:
    """Same constructor shape as `MasterIngestService`/`DeletionService` -
    `integration_id` is accepted for parity with that call convention but
    unused: `stock_balances` never touches `integration_references` (D3)."""

    def __init__(
        self,
        db: Session,
        integration_id: Optional[str] = None,
        *,
        company_id: str,
        actor_user_id: Optional[str] = None,
    ):
        self.db = db
        self.integration_id = integration_id
        self.actor_user_id = actor_user_id
        # Required, not defaulted, for the same reason every other ingest
        # surface requires it (see `master_ingest_service`'s own docstring):
        # a default would be the incumbent company, and a push meant for the
        # other one would land there silently.
        self.company_id = company_id
        # Per-call preload (`_build_preload`, fix round 1 / reviewer S4) -
        # reset at the start of every `ingest()`/`delete()`/`current_state()`
        # call, never shared across calls on the same instance.
        self._warehouses_by_exact: dict[str, dict] = {}
        self._warehouses_by_normalized: dict[str, list[dict]] = {}
        self._products_by_exact: dict[str, dict] = {}
        self._products_by_normalized: dict[str, list[dict]] = {}
        self._stock_by_pair: dict[tuple[str, str], Stock] = {}
        # `(product_id, warehouse_id)` keys THIS record's own write added to
        # `self._stock_by_pair` - reset per record, walked back on that
        # record's own rollback (T11's shape in `master_ingest_service`,
        # applied to this service's one preload map).
        self._pending_stock_keys: list[tuple[str, str]] = []

    # ------------------------------------------------------------- preload
    def _build_preload(self, item_codes: set[str], location_codes: set[str]) -> None:
        """Every company warehouse, every product the batch's own codes name,
        and every stock row for those products - all explicitly scoped to
        `self.company_id`, never ambient session scope (fix round 1,
        reviewer S4). `location_codes` is accepted for symmetry with the
        caller's own scan but unused: the company's whole warehouse list is
        cheap enough that filtering it buys nothing a product catalogue's
        size would justify.
        """
        self._warehouses_by_exact = {}
        self._warehouses_by_normalized = {}
        rows = self.db.execute(
            text(
                "SELECT id, warehouse_code, is_active FROM warehouses WHERE company_id = :cid"
            ),
            {"cid": self.company_id},
        ).mappings().all()
        for row in rows:
            code = row["warehouse_code"]
            entry = {"id": str(row["id"]), "is_active": bool(row["is_active"])}
            self._warehouses_by_exact[code] = entry
            self._warehouses_by_normalized.setdefault(code.strip().upper(), []).append(entry)

        self._products_by_exact = {}
        self._products_by_normalized = {}
        product_ids: list[str] = []
        normalized_codes = sorted({c.strip().upper() for c in item_codes if c})
        if normalized_codes:
            rows = self.db.execute(
                text(
                    "SELECT id, product_code FROM products "
                    "WHERE company_id = :cid AND upper(btrim(product_code)) = ANY(:codes)"
                ),
                {"cid": self.company_id, "codes": normalized_codes},
            ).mappings().all()
            for row in rows:
                code = row["product_code"]
                entry = {"id": str(row["id"])}
                self._products_by_exact[code] = entry
                self._products_by_normalized.setdefault(code.strip().upper(), []).append(entry)
                product_ids.append(entry["id"])

        self._stock_by_pair = {}
        if product_ids:
            for stock_row in (
                self.db.query(Stock)
                .filter(Stock.company_id == self.company_id, Stock.product_id.in_(product_ids))
                .all()
            ):
                self._stock_by_pair[
                    (str(stock_row.product_id), str(stock_row.warehouse_id))
                ] = stock_row

    @staticmethod
    def _codes_from_records(records: list) -> tuple[set[str], set[str]]:
        item_codes: set[str] = set()
        location_codes: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("item_code"), str):
                item_codes.add(raw["item_code"])
            if isinstance(raw.get("location_code"), str):
                location_codes.add(raw["location_code"])
        return item_codes, location_codes

    @staticmethod
    def _codes_from_pairs(pairs: Any) -> tuple[set[str], set[str]]:
        item_codes: set[str] = set()
        location_codes: set[str] = set()
        if isinstance(pairs, dict):
            for pair in pairs.values():
                if not isinstance(pair, dict):
                    continue
                if isinstance(pair.get("item_code"), str):
                    item_codes.add(pair["item_code"])
                if isinstance(pair.get("location_code"), str):
                    location_codes.add(pair["location_code"])
        return item_codes, location_codes

    def _resolve_warehouse_pair(self, location_code: str) -> tuple[Optional[dict], bool]:
        """`(entry, ambiguous)` - `entry` is `None` on either a miss or an
        ambiguous match; the caller tells the two apart via the second value.
        An EXACT (trimmed, case-sensitive) match wins outright; otherwise
        more than one case/whitespace-insensitive match is ambiguous rather
        than guessed at (fix round 1, reviewer S3 / security N2)."""
        exact = self._warehouses_by_exact.get(location_code)
        if exact is not None:
            return exact, False
        candidates = self._warehouses_by_normalized.get(location_code.strip().upper(), [])
        if len(candidates) == 1:
            return candidates[0], False
        if len(candidates) > 1:
            return None, True
        return None, False

    def _resolve_product_pair(self, item_code: str) -> tuple[Optional[str], bool]:
        """`(product_id, ambiguous)` - same exact-then-normalised rule as
        `_resolve_warehouse_pair`."""
        exact = self._products_by_exact.get(item_code)
        if exact is not None:
            return exact["id"], False
        candidates = self._products_by_normalized.get(item_code.strip().upper(), [])
        if len(candidates) == 1:
            return candidates[0]["id"], False
        if len(candidates) > 1:
            return None, True
        return None, False

    def _add_ledger_row(
        self, stock_row: Stock, previous: int, new: int, source_ref: str, *, zeroed: bool = False
    ) -> None:
        """Fix round 2 (#1257): one `AUTOCOUNT_PUSH` Stock Ledger row for a push
        that CHANGES a quantity, the same shape an upload's `BULK_IMPORT` row
        has. An unchanged value writes nothing, so a push every 5 minutes adds
        no noise. Added before the caller's flush, inside the record's own
        savepoint: a record that rolls back takes its ledger row with it, and
        a dry run's rollback leaves none. `created_by` is the integration's
        act-as user, so the screen names who pushed."""
        if previous == new:
            return
        self.db.add(
            StockLedger(
                product_id=stock_row.product_id,
                warehouse_id=stock_row.warehouse_id,
                company_id=self.company_id,
                transaction_type=AUTOCOUNT_PUSH,
                quantity_change=new - previous,
                previous_quantity=previous,
                new_quantity=new,
                reference_type="autocount_push",
                reference_id=source_ref,
                notes="AutoCount push (deletion zeroed)" if zeroed else "AutoCount push",
                created_by=self.actor_user_id,
            )
        )

    def _revert_pending_stock_keys(self) -> None:
        for key in self._pending_stock_keys:
            self._stock_by_pair.pop(key, None)
        self._pending_stock_keys = []

    # ------------------------------------------------------------- ingest
    def ingest(
        self, entity_type: str, records: list[dict], *, dry_run: bool = False
    ) -> IngestResult:
        """D5/D6: one savepoint per record, input order, dry run resolves and
        applies exactly like a real run and is then rolled back - the same
        contract `MasterIngestService.ingest` documents at length, including
        the plain session-level `self.db.rollback()` on a dry run (fix round
        1, reviewer S1 / security N1): every ingester on this surface must
        roll back the same way, so a caller cannot tell `stock_balances`
        apart from any other entity by how its preview is undone.
        """
        item_codes, location_codes = self._codes_from_records(records)
        self._build_preload(item_codes, location_codes)

        result = IngestResult(dry_run=dry_run)
        try:
            for raw in records:
                result.records.append(self._ingest_one(raw))
        finally:
            if dry_run:
                # In a finally, so an unexpected error mid-batch cannot leave
                # a partially-applied preview sitting in the session for
                # whatever commits next.
                self.db.rollback()
        if not dry_run and any(
            r.outcome in (IngestOutcome.CREATED, IngestOutcome.UPDATED) for r in result.records
        ):
            self._stamp_push_confirmed()
        return result

    def _stamp_push_confirmed(self) -> None:
        """Record that AutoCount just confirmed this company's stock.

        The chatbot's "Data last updated" reads it (`StockService.
        stock_last_updated_by_company`). An unchanged value is still a
        confirmation, and it writes no ledger row and skips `updated_at`, so
        without this a push every 5 minutes never moved that time. Stamped
        when at least one record was accepted: a batch that is all
        `failed`/`retryable` confirmed nothing. Same transaction as the
        records, so the route's one commit (or a rollback) covers both.
        """
        self.db.execute(
            text("UPDATE companies SET stock_push_confirmed_at = :now WHERE id = :cid"),
            {"now": datetime.utcnow(), "cid": self.company_id},
        )

    def _ingest_one(self, raw: dict) -> RecordResult:
        source_ref = raw.get("source_ref") if isinstance(raw, dict) else None
        try:
            payload = _StockBalanceRecord(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors=_field_errors(exc),
            )
        except TypeError:
            logger.warning(
                "stock_balance_ingest.record_malformed source_ref=%s", source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

        # Each record commits or rolls back alone - without this a failed
        # flush poisons the session and every later record in the batch
        # fails too.
        self._pending_stock_keys = []
        savepoint = self.db.begin_nested()
        try:
            record = self._apply(payload)
            savepoint.commit()
            return record
        except IntegrityError as exc:
            # Fix round 1 (reviewer S2 / security N2): the ONE conflict this
            # write path can hit - another writer's insert for the same
            # never-before-seen pair landed between our preload and ours.
            savepoint.rollback()
            self._revert_pending_stock_keys()
            return self._handle_insert_conflict(payload, exc)
        except Exception:  # noqa: BLE001 - one record's failure, not the batch's
            savepoint.rollback()
            self._revert_pending_stock_keys()
            # SEC-style: never echo a non-domain exception's own message - it
            # routinely quotes SQL, a table/column name or a raw UUID.
            logger.warning(
                "stock_balance_ingest.record_failed source_ref=%s", payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    def _handle_insert_conflict(
        self, payload: "_StockBalanceRecord", exc: IntegrityError
    ) -> RecordResult:
        """The loser of a same-pair insert race re-reads, company-scoped, in
        a FRESH savepoint. Found: the winner's row is updated instead - the
        last value still wins, exactly as a duplicate pair within one batch
        already does. Still not found: the pair's (product_id, warehouse_id)
        already belongs to a DIFFERENT company (`uq_stock_product_id_
        warehouse_id` carries no company column of its own to protect
        against that) - a data defect, refused rather than silently adopted.
        """
        if not _is_stock_pair_conflict(exc):
            logger.warning(
                "stock_balance_ingest.record_failed source_ref=%s", payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

        warehouse_entry, _ = self._resolve_warehouse_pair(payload.location_code)
        product_id, _ = self._resolve_product_pair(payload.item_code)
        # Both resolved without ambiguity above - this handler is only ever
        # reached from the create branch of `_apply`, which only attempts an
        # insert once both already resolved cleanly.
        warehouse_id = warehouse_entry["id"]

        retry_savepoint = self.db.begin_nested()
        try:
            existing = (
                self.db.query(Stock)
                .filter(
                    Stock.company_id == self.company_id,
                    Stock.product_id == product_id,
                    Stock.warehouse_id == warehouse_id,
                )
                .first()
            )
            if existing is None:
                retry_savepoint.commit()
                return RecordResult(
                    source_ref=payload.source_ref,
                    outcome=IngestOutcome.FAILED,
                    errors={
                        "_": "stock row for this product and warehouse exists under another company"
                    },
                )
            self._add_ledger_row(
                existing, existing.quantity_on_hand, payload.qty, payload.source_ref
            )
            existing.quantity_on_hand = payload.qty
            existing.updated_at = datetime.utcnow()
            self.db.flush()
            key = (product_id, warehouse_id)
            self._stock_by_pair[key] = existing
            self._pending_stock_keys.append(key)
            retry_savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.UPDATED,
                entity_id=str(existing.id),
                diff=None,
            )
        except Exception:  # noqa: BLE001 - one record's failure, not the batch's
            retry_savepoint.rollback()
            logger.warning(
                "stock_balance_ingest.record_failed source_ref=%s", payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    def _apply(self, payload: _StockBalanceRecord) -> RecordResult:
        # D4 step 2: location, before item - an unresolved/inactive warehouse
        # never even looks at the item code. Ambiguous (fix round 1) is
        # checked ahead of both: a code that cannot be told apart is neither
        # confidently unresolved nor confidently found.
        warehouse_entry, warehouse_ambiguous = self._resolve_warehouse_pair(
            payload.location_code
        )
        if warehouse_ambiguous:
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"location_code": f"ambiguous: {payload.location_code}"},
            )
        if warehouse_entry is None:
            # D6: an `updated` verdict always carries SOME diff shape on a
            # dry run - `{}` here, the same "nothing changed" answer an
            # unchanged upsert gives, since nothing was written either way.
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.UPDATED,
                entity_id=None,
                diff={},
                warnings=[WARN_WAREHOUSE_UNRESOLVED],
            )
        if not warehouse_entry["is_active"]:
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.UPDATED,
                entity_id=None,
                diff={},
                warnings=[WARN_WAREHOUSE_INACTIVE],
            )
        warehouse_id = warehouse_entry["id"]

        # D4 step 3: item code, company-scoped, trimmed + case-insensitive.
        product_id, product_ambiguous = self._resolve_product_pair(payload.item_code)
        if product_ambiguous:
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"item_code": f"ambiguous: {payload.item_code}"},
            )
        if product_id is None:
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.RETRYABLE,
                errors={"item_code": f"not found: {payload.item_code}"},
            )

        # D4 step 4: upsert. `quantity_on_hand` ONLY, never reserved/damaged/
        # reorder_point/zone_id.
        key = (product_id, warehouse_id)
        existing = self._stock_by_pair.get(key)
        if existing is None:
            row = Stock(
                product_id=product_id,
                warehouse_id=warehouse_id,
                company_id=self.company_id,
                quantity_on_hand=payload.qty,
                updated_at=datetime.utcnow(),
            )
            self.db.add(row)
            self._add_ledger_row(row, 0, payload.qty, payload.source_ref)
            # May raise IntegrityError on `uq_stock_product_id_warehouse_id`
            # (fix round 1, reviewer S2) - handled by the caller
            # (`_ingest_one`/`_handle_insert_conflict`), never here.
            self.db.flush()
            self._stock_by_pair[key] = row
            self._pending_stock_keys.append(key)
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.CREATED,
                entity_id=str(row.id),
                diff=None,
            )

        current_qty = existing.quantity_on_hand
        if current_qty == payload.qty:
            # Fix round 1 (reviewer N3): nothing to write - skip the flush
            # and `updated_at` stamp entirely rather than writing the same
            # value back, so an unchanged re-push never bumps a row's own
            # last-modified marker.
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.UPDATED,
                entity_id=str(existing.id),
                diff={},
            )
        self._add_ledger_row(existing, current_qty, payload.qty, payload.source_ref)
        existing.quantity_on_hand = payload.qty
        existing.updated_at = datetime.utcnow()
        self.db.flush()
        return RecordResult(
            source_ref=payload.source_ref,
            outcome=IngestOutcome.UPDATED,
            entity_id=str(existing.id),
            diff={"qty": {"current": current_qty, "incoming": payload.qty}},
        )

    # ------------------------------------------------------------ deletions
    def delete(
        self, source_refs: list[str], pairs: dict, *, dry_run: bool = False
    ) -> DeletionResult:
        """D7. `pairs` is `{source_ref: {item_code, location_code}}` - unlike
        every other deletion entity there is no reference to resolve through;
        the pair IS the identity, exactly as on the ingest side.

        Same session-level dry-run rollback as `ingest()` (fix round 1).
        """
        item_codes, location_codes = self._codes_from_pairs(pairs)
        self._build_preload(item_codes, location_codes)

        result = DeletionResult(dry_run=dry_run)
        try:
            for ref in source_refs:
                key = ref if isinstance(ref, str) else str(ref)
                result.records.append(
                    self._delete_one(key, pairs.get(key) if isinstance(pairs, dict) else None)
                )
        finally:
            if dry_run:
                self.db.rollback()
        # A zeroing is AutoCount confirming the pair too (see `_stamp_push_confirmed`).
        if not dry_run and any(r.outcome == DeletionOutcome.DELETED for r in result.records):
            self._stamp_push_confirmed()
        return result

    def _delete_one(self, ref: str, pair: Any) -> DeletionRecordResult:
        if pair is None:
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)
        if not isinstance(pair, dict):
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.FAILED)

        item_code = pair.get("item_code")
        location_code = pair.get("location_code")
        if (
            not isinstance(item_code, str)
            or not item_code.strip()
            or not isinstance(location_code, str)
            or not location_code.strip()
        ):
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.FAILED)

        savepoint = self.db.begin_nested()
        try:
            warehouse_entry, warehouse_ambiguous = self._resolve_warehouse_pair(location_code)
            if warehouse_ambiguous:
                savepoint.commit()
                return DeletionRecordResult(
                    source_ref=ref,
                    outcome=DeletionOutcome.FAILED,
                    errors={"location_code": f"ambiguous: {location_code}"},
                )
            if warehouse_entry is None:
                savepoint.commit()
                return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)
            if not warehouse_entry["is_active"]:
                savepoint.commit()
                return DeletionRecordResult(
                    source_ref=ref,
                    outcome=DeletionOutcome.NOT_FOUND,
                    warnings=[WARN_WAREHOUSE_INACTIVE],
                )

            product_id, product_ambiguous = self._resolve_product_pair(item_code)
            if product_ambiguous:
                savepoint.commit()
                return DeletionRecordResult(
                    source_ref=ref,
                    outcome=DeletionOutcome.FAILED,
                    errors={"item_code": f"ambiguous: {item_code}"},
                )
            if product_id is None:
                savepoint.commit()
                return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)

            existing = self._stock_by_pair.get((product_id, warehouse_entry["id"]))
            if existing is None:
                savepoint.commit()
                return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)

            self._add_ledger_row(existing, existing.quantity_on_hand, 0, ref, zeroed=True)
            existing.quantity_on_hand = 0
            existing.updated_at = datetime.utcnow()
            self.db.flush()
            savepoint.commit()
            return DeletionRecordResult(
                source_ref=ref, outcome=DeletionOutcome.DELETED, entity_id=str(existing.id)
            )
        except Exception:  # noqa: BLE001 - one record's failure, not the batch's
            savepoint.rollback()
            logger.warning(
                "stock_balance_ingest.deletion_failed source_ref=%s", ref, exc_info=True
            )
            return DeletionRecordResult(
                source_ref=ref, outcome=DeletionOutcome.FAILED, errors={"_": INTERNAL_ERROR_MESSAGE}
            )

    # ------------------------------------------------------------- read-back
    def current_state(self, source_refs: list[str], pairs: dict) -> dict[str, Any]:
        """D8. `{records: [{source_ref, qty}], not_found: [...]}` for a batch
        of pairs - optional on the contract, implemented because the entity
        set on this surface drives the route regardless of whether any
        caller uses it yet. An ambiguous pair (fix round 1, reviewer S3)
        reads as `not_found` here - a read-back has no `errors` channel to
        refuse through the way ingest/delete do."""
        item_codes, location_codes = self._codes_from_pairs(pairs)
        self._build_preload(item_codes, location_codes)

        records: list[dict[str, Any]] = []
        not_found: list[str] = []
        for ref in source_refs:
            key = ref if isinstance(ref, str) else str(ref)
            pair = pairs.get(key) if isinstance(pairs, dict) else None
            row = None
            if isinstance(pair, dict):
                item_code = pair.get("item_code")
                location_code = pair.get("location_code")
                if isinstance(item_code, str) and isinstance(location_code, str):
                    warehouse_entry, warehouse_ambiguous = self._resolve_warehouse_pair(
                        location_code
                    )
                    if warehouse_entry is not None and not warehouse_ambiguous:
                        product_id, product_ambiguous = self._resolve_product_pair(item_code)
                        if product_id is not None and not product_ambiguous:
                            row = self._stock_by_pair.get((product_id, warehouse_entry["id"]))
            if row is None:
                not_found.append(key)
            else:
                records.append({"source_ref": key, "qty": row.quantity_on_hand})
        return {"records": records, "not_found": not_found}
