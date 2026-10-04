"""Ingest master data pushed in by the ESB (Phase C).

Three semantics carry this module, and each exists because of a specific way
the naive version fails:

**Per-record isolation.** Masters quarantine, they never block (AC-AC-15): a
batch of 10,000 products with 12 bad ones must import 9,988. That needs more
than a try/except -- a failed flush leaves the SQLAlchemy session unusable, so
one bad record would take out every record after it. Each record therefore runs
inside its own SAVEPOINT.

**Retryable is not failed.** A record referencing a master that has not been
synced yet is a sequencing artefact, not bad data (AC-AC-16). Reported
distinctly so the ESB re-drains it automatically, and deliberately *not*
persisted -- a half-written record with a dangling reference is worse than none.
Retrying genuinely invalid data, by contrast, is a queue that never drains.

**Adoption over duplication.** On first sync a record usually already exists
locally, matched by its business code. Creating a second one under a new id
would corrupt master data in a way that is painful to unpick, so an unclaimed
local match is adopted and linked instead.

**Dry run is a real run that is taken back.** ``ingest(..., dry_run=True)``
resolves and applies every record exactly as a live sync would, then rolls the
transaction back. Simulating the resolution separately would create a second
code path that can disagree with the first, and a preview that disagrees with
the sync it predicts is worse than no preview: it is trusted and wrong. For
records that would overwrite an existing row -- an adoption above all, where the
row holds hand-entered data -- the result carries a field-level diff of what
would be replaced.

**One company per call.** The caller names it (``company_anchor.py``) and it is
required here, not defaulted: nearly every table below is partitioned per
company, and their business codes are unique only within one. Both halves of the
ingest have to honour it or the anchor is decorative -- the INSERT stamps it, and
adoption matches inside it, because adopting across companies would silently
retarget another company's hand-entered row. The exception is ``sales_agents``
(``SHARED_TABLES``), whose row deliberately carries no company at all: the same
agents sell for both, and splitting them would give one person two demand
classes. The anchor still bounds the call, it simply has nothing to stamp.

Ingest emits **no lifecycle events** (AC-AC-18). A record arriving *from*
AutoCount must never trigger a write back to it. Nothing here calls an emitter,
and nothing here should ever be given one.
"""
from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.inventory import Warehouse
from app.models.order import Customer
from app.models.procurement import ProductSupplier, Supplier
from app.models.product import Brand, ItemType, Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent
from app.models.user import SystemSetting
from app.schemas.canonical_masters import (
    CanonicalBrand,
    CanonicalCustomer,
    CanonicalProductCategory,
    CanonicalSalesAgent,
    CanonicalUnitOfMeasure,
    CanonicalProduct,
    CanonicalSupplier,
    CanonicalWarehouse,
)
from app.services.integration_reference_service import (
    DEFAULT_SOURCE_SYSTEM,
    SHARED_TABLES,
    IntegrationReferenceService,
    ReferenceConflict,
    _is_company_scoped,
    is_unclaimed_or_same_source,
)
from app.services.rules import product_rules
from app.services.rules import customer_rules
from app.services.rules.customer_rules import customer_identity
from app.services.rules.master_rules import clean_supplier_name, normalize_code, resolve_master_by_code
# The agent code's one normalisation, imported rather than restated: the master
# screen, the outstanding-SO import and this ingest all have to agree on what
# `sean i` is, or the captain's demand class lands on one of three rows.
from app.services.scm.sales_agent_service import derive_person_label
from app.services.scm.sales_agent_service import normalize_code as _normalize_agent_code

logger = logging.getLogger(__name__)

#: SEC3 (review round 1). The verdict body is read by the ESB and logged
#: wherever it forwards; a non-domain exception's own `str(exc)` routinely
#: quotes the failed SQL statement, a table/column name, or a raw UUID -
#: an internal detail an external caller has no business seeing. Every
#: generic `except Exception` across the three ingest surfaces and the
#: deletion service returns this fixed string instead and logs the real one
#: with `exc_info=True`. A DOMAIN exception (`MissingReference`,
#: `ReferenceConflict`, `_UnknownStatus`, a pydantic `ValidationError`) is
#: authored FOR the caller and keeps its own message - only the catch-all
#: is sanitised.
INTERNAL_ERROR_MESSAGE = "internal error; see server logs"


def integrity_conflict_errors(exc: IntegrityError) -> dict[str, str]:
    """A per-record verdict body for a unique-constraint violation (fix round 4,
    BUG B), shared by all three ingest surfaces (masters/documents/shipping
    orders) so a two-company push that races a code/number can name what
    collided instead of falling through to `INTERNAL_ERROR_MESSAGE`.

    Reads psycopg2's own diagnostics off ``exc.orig`` rather than ``str(exc)``,
    which quotes the failed statement in full - `constraint_name` says WHICH
    unique index collided (e.g. `uq_warehouses_company_warehouse_code`) and
    `message_detail` is the bare DETAIL line ("Key (company_id,
    warehouse_code)=(..., BRW) already exists."), never the SQL itself.

    `errors["code"]` when the constraint parsed (the expected shape for a
    natural-key collision on any of these surfaces - `code` is the wire field
    every `CanonicalXxx.code` masters payload and the v2 ladder's code rungs
    are spelled under); `errors["_"]` for the "unknown constraint" case where
    the DBAPI driver exposed no diagnostics at all to name one by.

    Security review advisory (d): every caller catches a bare
    ``except IntegrityError`` - not only a unique-constraint collision, but
    also a FK, NOT NULL or CHECK violation can raise one, and THOSE carry a
    DETAIL line that can name a value from a different row, table or
    caller's own request than the one this record wrote. `message_detail` is
    therefore only ever echoed for `pgcode == "23505"` (unique_violation);
    every other IntegrityError maps to a field-less `conflict` with no
    DETAIL text at all, constraint name included - the constraint name alone
    (still logged with `exc_info=True` at the call site) is enough for an
    operator, and is never sent to the ESB either way.
    """
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    pgcode = getattr(orig, "pgcode", None)
    if pgcode != "23505":
        return {"_": "conflict"}
    constraint = getattr(diag, "constraint_name", None) if diag else None
    detail = getattr(diag, "message_detail", None) if diag else None
    if constraint:
        message = f"conflict: {constraint}"
        if detail:
            message = f"{message} ({detail})"
        return {"code": message}
    return {"_": "conflict: unique constraint"}


class UnsupportedIngestEntity(ValueError):
    """Raised for an entity this endpoint does not ingest."""


class MissingReference(Exception):
    """A referenced master is not present yet. Retryable, not a data error."""

    def __init__(self, field_name: str, code: str):
        self.field_name = field_name
        self.code = code
        super().__init__(f"{field_name}={code!r} not found")


class IngestOutcome(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    FAILED = "failed"
    RETRYABLE = "retryable"
    # Contract 2.6 (finance S0, billing_documents only today): the record matched a stored
    # row that already says exactly this, or a newer version of it, so nothing was written.
    UNCHANGED = "unchanged"


@dataclass
class RecordResult:
    source_ref: Optional[str]
    outcome: IngestOutcome
    entity_id: Optional[str] = None
    # field -> reason. Machine-readable so the ESB quarantines per record
    # without parsing prose (AC-AC-13).
    errors: dict[str, str] = field(default_factory=dict)
    # column -> {"current": ..., "incoming": ...} for the values this record
    # overwrote (real run) or would overwrite (dry run) on an existing row -
    # populated on BOTH since C1 (`PLAN-autocount-pull-preview-perf.md`): a
    # real UPDATED record with `{}` here is the one that was skipped entirely,
    # not a diff nobody bothered to compute. `None` when nothing would be
    # overwritten (a create), which is a different statement from an empty dict
    # (an existing row matched, but no value actually changes).
    diff: Optional[dict[str, dict[str, Any]]] = None
    # Fixed-vocabulary notices that do not fail the record - e.g. a back-created
    # customer or an unresolved warehouse NULLed onto the line (D9/D10). Same
    # rule as `errors`: omitted from `as_dict()` when empty.
    warnings: list[str] = field(default_factory=list)
    # Documents only (D11): per-line outcome counts for this record - adopted
    # (an xlsx-era ref-less row claimed by the three-step match), created,
    # updated (matched by its own existing source_ref), deleted, cancelled.
    # None for a master record (there are no lines) and omitted from
    # `as_dict()` in that case, same rule as `diff`.
    lines: Optional[dict[str, int]] = None

    def as_dict(self, *, dry_run: bool = False) -> dict[str, Any]:
        """`dry_run` (fix round, PP-10 - captain's contract ruling): `diff` is
        now populated on a REAL run too (C1), but the wire shape at
        `/api/v1/ingest/*` must stay byte-identical to main for a real push -
        only a DRY RUN ever serializes it. `IngestResult.as_dict()` is the
        one caller and passes its own `dry_run` through; a direct call (the
        AC-V0-3 warnings tests) defaults to `False`, matching every field
        `diff` is unrelated to.
        """
        return {
            "source_ref": self.source_ref,
            "outcome": self.outcome.value,
            "entity_id": self.entity_id,
            **({"errors": self.errors} if self.errors else {}),
            **({"diff": self.diff} if dry_run and self.diff is not None else {}),
            **({"warnings": self.warnings} if self.warnings else {}),
            **({"lines": self.lines} if self.lines is not None else {}),
        }


@dataclass
class IngestResult:
    records: list[RecordResult] = field(default_factory=list)
    dry_run: bool = False
    # S5 (`PLAN-oi-replan-received-links.md`) review fix: `follow_book_repairing`'s own
    # `FOLLOW_BOOK_REPAIRING_MAX_MOVES` cap silently dropped anything past 200 moves in
    # one push - set by the ingest route AFTER the post-write hooks run (the hook itself
    # has no `result` to write into), never mutated by the ingest write path itself.
    # Zero and omitted from `as_dict()`'s summary on every ordinary push.
    book_repair_moves_dropped: int = 0
    # S2 (`PLAN-oi-follow-book-chain.md`, AC-FB-24): `follow_book_for_rows`' own
    # sibling cap, set by the ingest route the same way and for the same reason
    # as `book_repair_moves_dropped` above. Zero and omitted from `as_dict()`'s
    # summary on every ordinary push.
    book_follow_rows_dropped: int = 0

    @property
    def created(self) -> int:
        return sum(1 for r in self.records if r.outcome is IngestOutcome.CREATED)

    @property
    def updated(self) -> int:
        return sum(1 for r in self.records if r.outcome is IngestOutcome.UPDATED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if r.outcome is IngestOutcome.FAILED)

    @property
    def retryable(self) -> int:
        return sum(1 for r in self.records if r.outcome is IngestOutcome.RETRYABLE)

    @property
    def unchanged(self) -> int:
        return sum(1 for r in self.records if r.outcome is IngestOutcome.UNCHANGED)

    def as_dict(self) -> dict[str, Any]:
        return {
            # Echoed so a caller can never mistake a preview for a completed
            # sync -- the two responses are otherwise identical in shape, which
            # is deliberate but would be dangerous without this flag.
            "dry_run": self.dry_run,
            "summary": {
                "total": len(self.records),
                "created": self.created,
                "updated": self.updated,
                "failed": self.failed,
                "retryable": self.retryable,
                # Contract 2.6: only an entity that reports `unchanged` (billing_documents)
                # ever makes this non-zero, so every other entity's summary keeps its
                # pre-2.6 shape byte for byte.
                **({"unchanged": self.unchanged} if self.unchanged else {}),
                **(
                    {"book_repair_moves_dropped": self.book_repair_moves_dropped}
                    if self.book_repair_moves_dropped
                    else {}
                ),
                **(
                    {"book_follow_rows_dropped": self.book_follow_rows_dropped}
                    if self.book_follow_rows_dropped
                    else {}
                ),
            },
            "records": [r.as_dict(dry_run=self.dry_run) for r in self.records],
        }


@dataclass
class EntitySpec:
    """How one canonical shape maps onto a Sorento table."""

    table: str
    schema: type[BaseModel]
    code_column: str
    # canonical payload -> column values (present fields only, D14). May raise
    # MissingReference. The fourth argument is a mutable warnings list the
    # builder may append fixed-vocabulary notices to (`category_created`, ...) -
    # only `_product_columns` uses it today. Loosely typed (`Callable[..., ...]`,
    # not the fixed 4-arg shape every other builder keeps) because
    # `_product_columns` alone takes a 5th and 6th, `ref_cache` (C2,
    # `PLAN-autocount-pull-preview-perf.md`) and `settings` (Group 3, same
    # plan) - `_apply_scoped` passes both only for `entity_type == "products"`.
    to_columns: Callable[..., dict[str, Any]]
    # The ORM model class the D18 writer upserts through, so audit, embedding
    # and CompanyScopedMixin listeners fire on flush.
    model: type
    # Whether adoption matches ``upper(btrim())`` on BOTH sides instead of the
    # stored string. True only where the column has one canonical spelling that
    # the rows do not all carry yet -- see ``_lookup_id``.
    normalized_code: bool = False
    # Overrides the default code-only adoption match. None means the default
    # (``_lookup_id`` on ``code_column`` alone). Customers are the only user so
    # far (D13): the (code, name) pair, not the code alone.
    adopt_lookup: Optional[Callable[[Session, Any, str], Optional[str]]] = None


# `SHARED_TABLES` / `_is_company_scoped` (tables where a row serves every
# company, `company_id` NULL) moved to `integration_reference_service`
# (autocount-brands-ingest BL-056, D11): that service needs the same set for
# its own `company_id` column, and it cannot import this module (circular -
# this module already imports IT). Imported above, re-exported by being
# module-level names here, so `deletion_service.py` and
# `master_read_service.py` keep importing from where they always have.


def _present(payload: Any, columns: dict[str, Any], *names: str) -> None:
    """D14: absent vs null. Copies ``payload.<name>`` into ``columns`` only for
    fields the caller actually SET - an omitted field must never overwrite a
    stored value, only an explicit ``null`` may."""
    for name in names:
        if name in payload.model_fields_set:
            columns[name] = getattr(payload, name)


# Live fix, 2026-09-06: every one of these columns is NOT NULL with no
# server-side default the ORM insert can fall back on when the attribute is
# explicitly set to `None` (a column-level Python `default=` only fires when
# the attribute is never touched at all, so an explicit `None` bypasses it
# the same way a bare `INSERT` naming the column as NULL would). D14 says "an
# explicit null clears it" - but there is nothing to CLEAR a NOT NULL column
# TO except its own create default, so that is what a `null` (or, on a
# genuine create, an entirely ABSENT field - see `_insert`'s own use of this
# table) resolves to here. One table, checked against the SAME default the
# manual create form / bulk import already apply for that column - `EA`-style
# FK fallbacks (`products.category_id`/`base_uom_id`) are NOT here, because
# they need a lookup/creation rather than a static value; see
# `_fill_create_only_product_gaps`.
#: Distinguishes "never queried the singleton `system_settings` row yet" from
#: "queried it and there was no row" (perf review S6) - `None` is a real,
#: valid cached answer, not an unset marker.
_UNSET = object()

_NOT_NULL_DEFAULTS: dict[str, dict[str, Any]] = {
    "product_categories": {"is_active": True},
    "brands": {"is_active": True},
    "units_of_measure": {"is_active": True, "decimal_places": 0},
    "warehouses": {"is_active": True},
    "suppliers": {"is_active": True},
    "customers": {"is_active": True},
    "sales_agents": {"is_active": True},
    "products": {"is_active": True, "list_price": Decimal("0")},
}


def _apply_not_null_defaults(entity_type: str, columns: dict[str, Any]) -> None:
    """An explicit ``null`` on a NOT NULL column (with no server default the ORM
    can rely on) maps to that column's create default instead of reaching the
    DB - on BOTH the create and the update path, since an update's blind
    ``setattr`` would violate the constraint exactly the same way an insert
    does. Only touches a key already IN ``columns`` (D14: an absent field is
    untouched on update; the absent-on-CREATE case is `_insert`'s own
    ``setdefault`` pass over this same table)."""
    for column, default in _NOT_NULL_DEFAULTS.get(entity_type, {}).items():
        if column in columns and columns[column] is None:
            columns[column] = default


def _category_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    columns: dict[str, Any] = {"category_code": payload.code, "category_name": payload.name}
    _present(payload, columns, "description", "is_active")
    return columns


def _brand_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    columns: dict[str, Any] = {"brand_code": payload.code, "brand_name": payload.name}
    _present(payload, columns, "description", "is_active")
    return columns


def _uom_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    columns: dict[str, Any] = {"uom_code": payload.code, "uom_name": payload.name}
    # Canonical divisibility (plan 6.4). Absent (D14) leaves the row untouched on
    # update, or the model's own 0 default on create - never a value this
    # module invents.
    _present(payload, columns, "decimal_places", "description", "is_active")
    return columns


def _warehouse_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    columns: dict[str, Any] = {"warehouse_code": payload.code, "warehouse_name": payload.name}
    _present(payload, columns, "location", "is_active")
    return columns


def _supplier_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    # D2: AutoCount's trailing currency note (`"ACME (RMB)"`) is not part of
    # the legal name - same rule the manual create and the outstanding-PO
    # upload apply, via `master_rules.clean_supplier_name`.
    columns: dict[str, Any] = {
        "supplier_code": payload.code,
        "supplier_name": clean_supplier_name(payload.name),
    }
    # D15: the contact/address block AutoCount carries and this module used to
    # drop on the floor. D14: absent vs null on every one of them, plus
    # `payment_terms_days` (model default 30 fills an absent value on create -
    # see `_insert`). `payment_terms_code` is REMOVED (D15 end state, S4) -
    # an undeclared key, so it is dropped (name logged, owner decision 1 Oct
    # 2026) and never written; the payment-terms master it once waited for still does not
    # exist, and a supplier no longer needs a placeholder for it at all.
    _present(
        payload,
        columns,
        "contact_name",
        "email",
        "phone_number",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "payment_terms_days",
        "is_active",
    )
    # S2 (`PLAN-local-supplier-oi-routing.md`, AC-2.10): `country` is a NAME or a
    # 2-letter CODE, case-insensitively, resolved to `country_id` here rather than
    # carried through as free text. Unresolved -> a row warning, field left null,
    # row still imports (masters quarantine, they never block).
    if "country" in payload.model_fields_set:
        raw_country = payload.country
        columns["country_id"] = _resolve_country_id(db, raw_country) if raw_country else None
        if raw_country and columns["country_id"] is None:
            warnings.append(f"Country '{raw_country}' was not resolved; left blank.")
    return columns


def _resolve_country_id(db: Session, value: str) -> Optional[str]:
    from app.models.country import Country

    normalized = value.strip()
    if not normalized:
        return None
    row = (
        db.query(Country.id)
        .filter(
            or_(
                func.lower(Country.code) == normalized.lower(),
                func.lower(Country.name) == normalized.lower(),
            )
        )
        .first()
    )
    return str(row[0]) if row else None


def _customer_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    # `credit_limit` / `payment_terms_days` / `payment_terms_code` are REMOVED
    # (D15 end state, S4) - `customers` never had a matching column for any
    # of the three, and an undeclared key is dropped (name logged, owner
    # decision 1 Oct 2026) before this builder runs, so none is ever written.
    columns: dict[str, Any] = {"customer_code": payload.code, "customer_name": payload.name}
    _present(
        payload,
        columns,
        "email",
        "phone_number",
        "registration_number",
        "tax_id",
        "country",
        "is_active",
        "region",
    )
    # D16 (S2): folded through the same rule the customer importer uses
    # (`customer_rules.fold_market_segment`) so the two can never map a
    # spelling two different ways. An unrecognised value is dropped with
    # warning `segment_unknown` rather than failing the whole customer over
    # one optional column - `market_segment_code` is a foreign key.
    if "market_segment_code" in payload.model_fields_set and payload.market_segment_code:
        canonical = customer_rules.fold_market_segment(db, payload.market_segment_code)
        if canonical is None:
            warnings.append("segment_unknown")
        else:
            columns["market_segment_code"] = canonical
    # AutoCount `Debtor.SalesAgent`: blank is untouched like an absent key; an unknown code
    # warns and leaves the stored agent. (`agent_unresolved` is billing_document_ingest_service's
    # WARN_AGENT_UNRESOLVED; imported there, so the literal here avoids a circular import.)
    agent_code = payload.sales_agent_code if "sales_agent_code" in payload.model_fields_set else None
    if agent_code and agent_code.strip():
        agent_id = _lookup_id(db, "sales_agents", "sales_agent", agent_code, company_id, normalized=True)
        if agent_id is None:
            warnings.append("agent_unresolved")
        else:
            columns["sales_agent_id"] = agent_id
    return columns


def _lookup_id(
    db: Session,
    table: str,
    column: str,
    value: str,
    company_id: str,
    *,
    normalized: bool = False,
) -> Optional[str]:
    """A row matched by business code, WITHIN the anchored company.

    Unscoped this is a coin toss: ``warehouse_code`` and ``product_code`` are
    unique per company only (migration 305) and thousands of codes exist in both,
    so the row returned was whichever the scan reached first. A shared table has
    no company of its own, so its rows match on NULL as well.

    ``normalized`` compares ``upper(btrim())`` on both sides, which the agent
    master needs and the other five must not have. The agent code has one
    canonical spelling, but the rows do not all carry it: the AutoCount mirror
    wrote whatever AutoCount said, so a push spelled `sean i` that matched the
    stored string exactly would fail to find `SEAN I`, create a second agent and
    split one person's demand class - the duplicate the master exists to prevent.
    Turning this on everywhere would instead make `abc-1` adopt `ABC-1`, and for
    a product code those are two products.
    """
    if _is_company_scoped(table):
        scope = "company_id = :cid"
    else:
        scope = "(company_id IS NULL OR company_id = :cid)"
    # A shared table can hold both a shared and a company-owned row for one code: the
    # company's own wins, deterministically.
    order = "" if _is_company_scoped(table) else " ORDER BY company_id NULLS LAST"
    if normalized:
        match = f"upper(btrim({column})) = upper(btrim(:v))"
    else:
        match = f"{column} = :v"
    row = db.execute(
        text(f"SELECT id FROM {table} WHERE {match} AND {scope}{order} LIMIT 1"),
        {"v": value, "cid": company_id},
    ).first()
    return str(row[0]) if row else None


def _product_columns(
    payload: Any, db: Session, company_id: str, warnings: list[str], ref_cache: dict,
    settings: Any = None,
) -> dict[str, Any]:
    # D24 (captain 2026-09-06): `product_name` is ALWAYS the AutoCount item
    # code, matching the xlsx import's own convention (product_name = Item
    # Code); `description` holds the AutoCount Description text - the
    # payload's own `description` when it sends one, else `name`
    # (transitional: the ESB currently maps Item.Description onto `name`).
    # Both are forced on EVERY push, create and update, so an existing row
    # loaded under the old wrong mapping (product_name = the text,
    # description empty) is corrected the very next time it is pushed.
    columns: dict[str, Any] = {
        "product_code": payload.code,
        "product_name": payload.code,
        "description": payload.description if payload.description else payload.name,
    }
    _present(payload, columns, "is_active", "remark")

    # D3: an unknown category/uom/brand on a product push is CREATED (code =
    # name = the raw value), never retryable any more - `ensure_reference`
    # also gives this the case/whitespace-insensitive match D17 wants (D3
    # subsumes D17 here: a match is a match, whichever rule found it).
    if "category_code" in payload.model_fields_set:
        if not payload.category_code:
            raise MissingReference("category_code", "")
        category_id, created = product_rules.ensure_reference(
            db, ProductCategory, payload.category_code, company_id, cache=ref_cache
        )
        if created:
            warnings.append("category_created")
        columns["category_id"] = category_id

    if "uom_code" in payload.model_fields_set:
        if payload.uom_code:
            uom_id, created = product_rules.ensure_reference(
                db, UnitOfMeasure, payload.uom_code, company_id, cache=ref_cache
            )
            if created:
                warnings.append("uom_created")
        else:
            # A blank uom_code resolves to the configured default, exactly as
            # `bulk_import_products` does for a row with no uom column value.
            # `settings` (Group 3): the batch's own already-cached
            # `system_settings` row, so this never re-queries it per record.
            uom_id = product_rules.resolve_default_uom(
                db, company_id, settings, cache=ref_cache
            )
        if uom_id:
            columns["base_uom_id"] = uom_id

    if "brand_code" in payload.model_fields_set and payload.brand_code:
        brand_id, created = product_rules.ensure_reference(
            db, Brand, payload.brand_code, company_id, cache=ref_cache
        )
        if created:
            warnings.append("brand_created")
        columns["brand_id"] = brand_id

    # ITEM-TYPE-CRM: AutoCount ItemType, the brand rule above exactly.
    if "item_type_code" in payload.model_fields_set and payload.item_type_code:
        item_type_id, created = product_rules.ensure_reference(
            db, ItemType, payload.item_type_code, company_id, cache=ref_cache
        )
        if created:
            warnings.append("item_type_created")
        columns["item_type_id"] = item_type_id

    if "list_price" in payload.model_fields_set:
        columns["list_price"] = payload.list_price
    if "cost_price" in payload.model_fields_set:
        columns["cost_price"] = payload.cost_price

    # D4: dimensions_* are derived in `_finalize_product_derived` instead of
    # here, once the caller knows whether this is a create or an update and
    # can read the row's stored name/description for the effective-text
    # merge (live finding, 2026-09-06: gating on "description in columns"
    # missed the ESB, which sends its Description text as `name` and no
    # `description` at all).

    # D14: `barcode` is CRM-owned. Only written when the incoming value is
    # non-empty - the key is left OUT of the dict otherwise, so an update never
    # touches it and a manually entered barcode (or one from an earlier sync)
    # survives a push that carries none. On CREATE the same omission leaves the
    # column at its own NULL default.
    if payload.bar_code:
        columns["barcode"] = payload.bar_code
    return columns


def _sales_agent_columns(payload: Any, db: Session, company_id: str, warnings: list[str]) -> dict[str, Any]:
    """The four columns AutoCount owns on an agent, and no others.

    ``internal_note``, ``follow_up``, ``demand_class``, ``location_group`` and
    ``source`` are absent on purpose. They are the captain's annotations, made on
    the master screen; a weekly re-sync that restated them from a payload which
    never carried them would blank his classification every Monday and make
    fulfilment priority flap. Absent from the written set, they cannot be touched
    by any path through this module - which is a stronger promise than "we do not
    send them".

    ``source`` stays untouched for the same reason plus one more: it records how
    a row got here, and an agent an outstanding-SO upload created is still
    `import` even after AutoCount confirms it exists.
    """
    columns: dict[str, Any] = {"sales_agent": _normalize_agent_code(payload.code)}
    _present(payload, columns, "description", "is_active", "person_label")
    return columns


def _adopt_customer(db: Session, payload: Any, company_id: str) -> Optional[str]:
    """D13: adoption match for a customer is the (code, name) pair, never the
    code alone - the same key as `uq_customers_company_code_name_lower` and
    `order_service.CustomerService.create_customer`, via the shared
    `customer_identity` rule."""
    code_norm, name_norm = customer_identity(payload.code, payload.name)
    row = db.execute(
        text(
            "SELECT id FROM customers WHERE lower(btrim(customer_code)) = :code "
            "AND lower(btrim(customer_name)) = :name AND company_id = :cid LIMIT 1"
        ),
        {"code": code_norm, "name": name_norm, "cid": company_id},
    ).first()
    return str(row[0]) if row else None


ENTITY_SPECS: dict[str, EntitySpec] = {
    # Categories and UoMs first: products.category_id and base_uom_id are
    # NOT NULL, so a product whose category has not synced yet is retryable
    # and stays that way until these land.
    "product_categories": EntitySpec(
        "product_categories", CanonicalProductCategory, "category_code", _category_columns,
        ProductCategory,
    ),
    # Syncs before products (D2, plan section 3): a product's `brand_code`
    # auto-creates on miss regardless (D9, unchanged), but a proper brands push
    # gives it a real master row and an integration reference instead of only
    # that placeholder. Default adoption path (D3): code only, no name rung -
    # adopting by name would silently rewrite a hand-made brand_code.
    "brands": EntitySpec("brands", CanonicalBrand, "brand_code", _brand_columns, Brand),
    "units_of_measure": EntitySpec(
        "units_of_measure", CanonicalUnitOfMeasure, "uom_code", _uom_columns, UnitOfMeasure
    ),
    "warehouses": EntitySpec(
        "warehouses", CanonicalWarehouse, "warehouse_code", _warehouse_columns, Warehouse
    ),
    "suppliers": EntitySpec(
        "suppliers", CanonicalSupplier, "supplier_code", _supplier_columns, Supplier
    ),
    "customers": EntitySpec(
        "customers", CanonicalCustomer, "customer_code", _customer_columns, Customer,
        adopt_lookup=_adopt_customer,
    ),
    "products": EntitySpec("products", CanonicalProduct, "product_code", _product_columns, Product),
    # The only shared master here: the row carries no company (see SHARED_TABLES)
    # and its code is matched normalised, because the rows already in the table
    # carry AutoCount's spelling rather than ours.
    "sales_agents": EntitySpec(
        "sales_agents",
        CanonicalSalesAgent,
        "sales_agent",
        _sales_agent_columns,
        SalesAgent,
        normalized_code=True,
    ),
}


@dataclass(frozen=True)
class _PreloadedOrigin:
    """Round 2: stands in for `origin_of()`'s own `IntegrationReference` ORM
    row inside `_ProductBatchPreload.origin_by_entity`, carrying only what
    `is_unclaimed_or_same_source` (the one reader) actually looks at - the
    real row's other columns are never needed for this call site, and
    fetching a full ORM instance per candidate id would cost the very
    `do_orm_execute` tax this preload exists to avoid."""

    source_system: str


@dataclass
class _ProductBatchPreload:
    """Round 2 (`PLAN-autocount-pull-preview-perf.md`): one bulk pass over
    the batch's own codes/refs, built once in `ingest()` before the
    per-record loop, instead of the same three per-record queries
    (`resolve_master_by_code`, `IntegrationReferenceService.resolve`/
    `origin_of`) - a cProfile pass on the clone (after C1-C3, still missing
    the <=90s target) found these three responsible for most of the wall
    time, via a `company_scope.py` `do_orm_execute` quirk: a query against a
    table that is NOT `CompanyScopedMixin` (`integration_references`, which
    manages its own company anchor instead) reports no top-level scoped
    mapper and falls back to injecting `with_loader_criteria` for EVERY
    scoped model in the app (~134 of them) - see the PR body for the numbers.
    Products only; every other entity keeps its per-record path untouched.

    Every lookup here is a courtesy: a miss falls back to the exact same
    per-record query this batch would have run without a preload at all
    (`MasterIngestService._resolve_ref`/`_origin_of`/the adopt branch's own
    code lookup), so a gap in the preload costs a query, never correctness.
    `_insert`/`_link` maintain `code_to_id`/`ref_to_entity` as the batch
    runs (a later record can adopt one this batch itself just created); a
    record's own savepoint rollback drops whatever IT added
    (`MasterIngestService._pending_preload_additions`,
    `_revert_pending_preload_additions`) - the same shape T6 already pins
    for `product_rules.ensure_reference`'s own cache, applied to this map.
    """

    #: `normalize_code(code) -> product id`, D17's own matching rule
    #: (`master_rules.resolve_master_by_code`).
    code_to_id: dict[str, str] = field(default_factory=dict)
    #: `source_ref -> entity_id`, matching `IntegrationReferenceService.
    #: resolve`'s own (source_system=autocount, entity_type=products,
    #: this company) filter - excludes a ref whose target row no longer
    #: exists (an explicit JOIN against `products`), so an orphaned mapping
    #: is simply absent here and falls through to the real `resolve()` call,
    #: which is what actually self-heals it (unchanged from today).
    ref_to_entity: dict[str, str] = field(default_factory=dict)
    #: `entity_id -> origin` (a lightweight stand-in exposing `.source_system`
    #: only - the one attribute `is_unclaimed_or_same_source` reads), for
    #: every id `code_to_id` found. A key PRESENT with value `None` is a
    #: real "confirmed no origin"; a key ABSENT means "not preloaded, ask
    #: `origin_of` for real" (an id resolved via the per-record fallback,
    #: or one this batch created after the preload ran).
    origin_by_entity: dict[str, Optional[Any]] = field(default_factory=dict)
    #: The batch's one default-supplier id (`product_rules.
    #: resolve_default_supplier_id`, resolved once) - `None` when none is
    #: configured and no supplier exists to fall back to either.
    default_supplier_id: Optional[str] = None
    #: `product_id -> its EXISTING product_suppliers.standard_lead_time_days`
    #: for that supplier. Absent means "never confirmed either way" - the
    #: preload's own bulk query only ever WRITES a key for a product it found
    #: an actual row for (round-1 review, B2): it never `setdefault`s a "no
    #: link" `None` for a candidate id with none, unlike `origin_by_entity`
    #: above. `_post_write_product_hooks` therefore reads this with `product_
    #: rules.NOT_PRELOADED` as the `.get` default, never Python's bare
    #: `None` - the two are NOT the same answer here, and reading a miss as
    #: "confirmed no link" is exactly the bug that let a second same-batch
    #: write (an adopt right after a create, or a renamed code resolved via
    #: its ref rather than a code hit - neither ever appears in this dict at
    #: all) insert a SECOND `product_suppliers` row and violate `uq_product_
    #: suppliers_product_id_supplier_id`. `_post_write_product_hooks` writes
    #: the id it just resolved back in here after every create/update (with
    #: the same `_pending_preload_additions` revert bookkeeping every other
    #: preload map uses), so a later record in the SAME batch sharing the id
    #: (T12/T13) sees it without a query either.
    default_supplier_lead_time: dict[str, int] = field(default_factory=dict)


class MasterIngestService:
    def __init__(
        self, db: Session, integration_id: Optional[str] = None, *, company_id: str,
        stamp_user_id: Optional[str] = None,
    ):
        self.db = db
        self.integration_id = integration_id
        # Required, deliberately. A default would be the incumbent company, and a
        # push meant for the other one would land there silently -- the failure
        # this whole anchor exists to prevent.
        self.company_id = company_id
        # SR3 (PLAN-autocount-pull-review.md, AC-PC-4): the confirming user, for a real
        # ingest triggered by a pull Confirm only. None (the default) is the ordinary
        # FoundryX push - it stamps neither `created_by` nor `updated_by`, unchanged.
        self.stamp_user_id = stamp_user_id
        self.refs = IntegrationReferenceService(db, company_id=self.company_id)
        # Set for the duration of a dry-run ingest. Read by _apply to decide
        # whether to capture a before/after diff; the rollback that makes the
        # run harmless is handled in ingest().
        self._dry_run = False
        # Perf review (S6): `system_settings` is a single row read once per
        # BATCH, not once per product record - `_post_write_product_hooks`
        # used to re-query it for every product, the same N+1 shape
        # `bulk_import_products` already caches once for its own run.
        # `_UNSET` (not `None`) distinguishes "never queried yet" from "queried
        # and there is no row" - a real, if unusual, state on a fresh install.
        self._settings_cache: Any = _UNSET
        # C2 (`PLAN-autocount-pull-preview-perf.md`): `product_rules.
        # ensure_reference` (category/uom/brand) results for this batch,
        # keyed `(model, company_id, normalised code)` - see that function's
        # own docstring for why only a FOUND id is ever cached. Same lifetime
        # as `_settings_cache` (one instance = one batch), never cleared.
        self._ref_cache: dict[tuple[type, Optional[str], str], str] = {}
        # Round 2: built once per batch by `ingest()`, products only - see
        # `_ProductBatchPreload`'s own docstring. `None` for every other
        # entity type, or if the preload itself fails (best-effort: a bug in
        # this optimisation must never fail the whole batch).
        self._preload: Optional[_ProductBatchPreload] = None
        # `(map_name, key)` pairs THIS record's own `_insert`/`_link` added to
        # `self._preload` - reset at the start of every `_ingest_one` call,
        # walked back by `_revert_pending_preload_additions` in each of its
        # except branches so a rolled-back record's additions never leak to
        # the next one (T11; T6's own shape for the C2 cache).
        self._pending_preload_additions: list[tuple[str, Any]] = []

    #: B3 (small-fix track, PLAN-autocount-pull-review.md): how often `on_progress` fires
    #: mid-batch. A full-size products preview is thousands of records; calling back on
    #: every single one would be as noisy as never calling back at all.
    PROGRESS_REPORT_EVERY = 500

    def ingest(
        self,
        entity_type: str,
        records: list[dict],
        *,
        dry_run: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> IngestResult:
        """Apply a batch of canonical records.

        With ``dry_run`` the records are resolved and applied exactly as they
        would be for real -- adoption matching, reference conflicts, unique
        constraints and all -- and the whole transaction is then rolled back.
        Simulating the resolution instead would produce a preview that can
        disagree with the sync it claims to predict, which is worse than no
        preview at all; the only way to know what the database would do is to
        ask it and then take it back.

        ``on_progress`` (B3): called with ``(processed, total)`` every
        `PROGRESS_REPORT_EVERY` records and once more at the end with
        ``(total, total)`` - never more often than that, and never left out even when
        `records` is empty or shorter than the report interval. Best-effort: an
        exception from the callback is logged and swallowed, never allowed to fail the
        ingest itself (the same contract every other observability hook in this
        module keeps).
        """
        spec = ENTITY_SPECS.get(entity_type)
        if spec is None:
            raise UnsupportedIngestEntity(
                f"Unsupported ingest entity {entity_type!r}. "
                f"Expected one of: {', '.join(sorted(ENTITY_SPECS))}"
            )

        # Fix round (Group 3): a fresh dict per `ingest()` call, not just per
        # `MasterIngestService()` construction - every real caller already
        # builds one instance per batch, but a stale id surviving into an
        # unrelated later batch on the SAME instance would be silently wrong
        # rather than merely slow, so the reset lives here rather than
        # trusting that convention alone.
        self._ref_cache = {}
        if entity_type == "products":
            try:
                with company_scope(self.db, frozenset({self.company_id})):
                    self._preload = self._build_product_preload(records)
            except Exception:  # noqa: BLE001 - best-effort: a preload bug must
                # never fail the whole batch, only cost it the per-record
                # fallback queries a miss already costs.
                logger.warning("ingest.product_preload_failed", exc_info=True)
                # Fix round (Group 3): a REAL DB error (not a Python one)
                # leaves Postgres refusing every further statement on this
                # connection until a ROLLBACK - without this, the very first
                # record's own `self.db.begin_nested()` raises an UNCAUGHT
                # `PendingRollbackError` and the whole batch dies, not just
                # this best-effort optimisation. This `rollback()` discards
                # anything else uncommitted on `self.db` too, not only the
                # failed preload statement - both of today's callers
                # (`_preview_products`/`_apply_products` in
                # `autocount_pull_tasks.py`, and the `/api/v1/ingest/*`
                # external route) only ever READ before reaching `ingest()`,
                # so there is nothing of the caller's to lose; a future
                # caller that writes first would need to commit or its own
                # savepoint before calling in.
                self.db.rollback()
                self._preload = None
        else:
            self._preload = None

        total = len(records)
        result = IngestResult(dry_run=dry_run)
        self._dry_run = dry_run
        try:
            for index, raw in enumerate(records, start=1):
                result.records.append(self._ingest_one(entity_type, spec, raw))
                if on_progress is not None and index % self.PROGRESS_REPORT_EVERY == 0:
                    self._report_progress(on_progress, index, total)
        finally:
            self._dry_run = False
            if dry_run:
                # In a finally, so an unexpected error mid-batch cannot leave a
                # partially-applied preview sitting in the session for whatever
                # commits next.
                self.db.rollback()
        if on_progress is not None:
            self._report_progress(on_progress, total, total)
        return result

    @staticmethod
    def _report_progress(
        on_progress: Callable[[int, int], None], processed: int, total: int
    ) -> None:
        try:
            on_progress(processed, total)
        except Exception:  # pragma: no cover - defensive by design
            logger.warning("ingest progress callback failed", exc_info=True)

    #: Round 2: `IN (...)` chunk size for every bulk preload query - 1,000,
    #: same as the plan's own number, comfortably under Postgres' bind-
    #: parameter ceiling.
    _PRELOAD_CHUNK_SIZE = 1000

    @classmethod
    def _chunked(cls, values):
        values = list(values)
        for i in range(0, len(values), cls._PRELOAD_CHUNK_SIZE):
            yield values[i : i + cls._PRELOAD_CHUNK_SIZE]

    def _build_product_preload(self, records: list[dict]) -> _ProductBatchPreload:
        """T9/T10 (round 2): one pass over the batch's own codes/refs -
        `_ProductBatchPreload`'s own docstring has the full "why"."""
        codes: set[str] = set()
        refs: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict):
                continue
            code = raw.get("code")
            if code:
                normalized = normalize_code(str(code))
                if normalized:
                    codes.add(normalized)
            ref = raw.get("source_ref")
            if ref:
                refs.add(str(ref))

        preload = _ProductBatchPreload()

        # (a) code -> id, D17's exact matching rule (`resolve_master_by_code`).
        # Fix round (Group 3): `ORDER BY created_at, id` + `setdefault` (not
        # `=`) - two DIFFERENT products can normalize to the same code (the
        # unique constraint is on the exact stored string, not the
        # normalized one), and without an order a Postgres-chosen row order
        # picked whichever one happened to come back LAST; this instead
        # picks the OLDEST, the same answer `resolve_master_by_code`'s own
        # now-ordered `.first()` fallback would give for the same code.
        for chunk in self._chunked(sorted(codes)):
            rows = (
                self.db.query(Product.id, Product.product_code)
                .filter(
                    func.upper(func.btrim(Product.product_code)).in_(chunk),
                    Product.company_id == self.company_id,
                )
                .order_by(Product.created_at, Product.id)
                .all()
            )
            for product_id, product_code in rows:
                preload.code_to_id.setdefault(normalize_code(product_code), str(product_id))

        # (b) source_ref -> entity_id, joined against `products` so an
        # ORPHANED reference (target row deleted) is simply absent here -
        # raw SQL, not the ORM: `IntegrationReference` is not
        # `CompanyScopedMixin` (it manages its own anchor), and an ORM query
        # against it is exactly the query shape the profile found paying the
        # `do_orm_execute` "no scoped mapper -> inject every scoped class's
        # criteria" tax (see this class's own docstring).
        for chunk in self._chunked(sorted(refs)):
            rows = self.db.execute(
                text(
                    "SELECT ir.source_ref, ir.entity_id FROM integration_references ir "
                    "JOIN products p ON p.id::text = ir.entity_id "
                    "WHERE ir.source_system = :source_system AND ir.entity_type = 'products' "
                    "AND ir.company_id = :cid AND ir.source_ref = ANY(:refs)"
                ),
                {"source_system": DEFAULT_SOURCE_SYSTEM, "cid": self.company_id, "refs": chunk},
            ).mappings().all()
            for row in rows:
                preload.ref_to_entity[row["source_ref"]] = str(row["entity_id"])

        # (c) entity_id -> origin, for every id (a) found - the adopt branch's
        # own next query after a code hit. Same raw-SQL reasoning as (b); a
        # lightweight stand-in (not the full ORM row) since
        # `is_unclaimed_or_same_source` reads only `.source_system`. `entity_
        # id` is unique here (`uq_integration_ref_entity`), so no collision
        # is actually reachable - `ORDER BY` added anyway (Group 3) for the
        # same defensive determinism as (a)'s.
        candidate_ids = list(preload.code_to_id.values())
        for entity_id in candidate_ids:
            preload.origin_by_entity.setdefault(entity_id, None)
        for chunk in self._chunked(candidate_ids):
            rows = self.db.execute(
                text(
                    "SELECT entity_id, source_system FROM integration_references "
                    "WHERE entity_type = 'products' AND entity_id = ANY(:ids) "
                    "ORDER BY created_at, id"
                ),
                {"ids": chunk},
            ).mappings().all()
            for row in rows:
                preload.origin_by_entity[row["entity_id"]] = _PreloadedOrigin(
                    source_system=row["source_system"]
                )

        # (d) the batch's one default-supplier link set - `link_default_
        # supplier`'s own two settings-driven lookups, resolved ONCE instead
        # of once per record (`ProductSupplier` IS `CompanyScopedMixin`, so
        # this was never part of the do_orm_execute tax - still a real
        # per-record SELECT this batch no longer pays for a candidate id).
        settings = self._system_settings()
        preload.default_supplier_id = product_rules.resolve_default_supplier_id(self.db, settings)
        if preload.default_supplier_id and candidate_ids:
            for chunk in self._chunked(candidate_ids):
                rows = (
                    self.db.query(ProductSupplier.product_id, ProductSupplier.standard_lead_time_days)
                    .filter(
                        ProductSupplier.supplier_id == preload.default_supplier_id,
                        ProductSupplier.product_id.in_(chunk),
                    )
                    .all()
                )
                for product_id, lead_time_days in rows:
                    preload.default_supplier_lead_time[str(product_id)] = lead_time_days

        return preload

    def _resolve_ref(self, entity_type: str, source_ref: str) -> Optional[str]:
        """`self.refs.resolve()`, consulting the batch preload first
        (products only) - a miss falls back to the exact query `resolve()`
        would run anyway, so a preload gap costs a query, never correctness.
        """
        if entity_type == "products" and self._preload is not None:
            hit = self._preload.ref_to_entity.get(source_ref)
            if hit is not None:
                return hit
        return self.refs.resolve(entity_type=entity_type, source_ref=source_ref)

    def _origin_of(self, entity_type: str, entity_id: str) -> Optional[Any]:
        """`self.refs.origin_of()`, consulting the batch preload first
        (products only) - same shape as `_resolve_ref`, except a preloaded
        `None` (a KEY present with that value) is itself a trusted answer -
        see `_ProductBatchPreload.origin_by_entity`'s own docstring."""
        if entity_type == "products" and self._preload is not None:
            if entity_id in self._preload.origin_by_entity:
                return self._preload.origin_by_entity[entity_id]
        return self.refs.origin_of(entity_type=entity_type, entity_id=entity_id)

    def _revert_pending_preload_additions(self) -> None:
        """T11: a record's own savepoint rollback must undo whatever IT added
        to `self._preload` too - `_insert`/`_link` both append to
        `self._pending_preload_additions` right after the write that made
        the addition valid; called from every `_ingest_one` except branch."""
        if self._preload is not None:
            for map_name, key in self._pending_preload_additions:
                getattr(self._preload, map_name).pop(key, None)
        self._pending_preload_additions = []

    def _ingest_one(self, entity_type: str, spec: EntitySpec, raw: dict) -> RecordResult:
        source_ref = raw.get("source_ref") if isinstance(raw, dict) else None
        # Round 2: this record's own scratch pad for `_insert`/`_link`'s
        # preload-map additions - reset per record, walked back on any of
        # this method's own except branches below (T11).
        self._pending_preload_additions = []

        try:
            payload = spec.schema(**raw)
        except ValidationError as exc:
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors=_field_errors(exc),
            )
        except TypeError:
            # SEC3-style (fix-round-2): a malformed body, never the caller's
            # business - logged with exc_info, never echoed.
            logger.warning(
                "ingest.record_malformed entity=%s source_ref=%s",
                entity_type,
                source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

        # Each record commits or rolls back alone. Without this savepoint a
        # failed flush poisons the session and every later record in the batch
        # fails too -- turning "12 bad rows" into "nothing imported".
        savepoint = self.db.begin_nested()
        try:
            outcome, entity_id, diff, warnings = self._apply(entity_type, spec, payload)
            savepoint.commit()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=outcome,
                entity_id=entity_id,
                diff=diff,
                warnings=warnings,
            )
        except MissingReference as exc:
            savepoint.rollback()
            self._revert_pending_preload_additions()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.RETRYABLE,
                errors={exc.field_name: f"not found: {exc.code}"},
            )
        except ReferenceConflict as exc:
            savepoint.rollback()
            self._revert_pending_preload_additions()
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"source_ref": str(exc)},
            )
        except IntegrityError as exc:
            # Fix round 4, BUG B: a unique-constraint race (two companies, or a
            # concurrent push of the same code) - named by constraint, never by
            # `str(exc)`'s full SQL statement.
            savepoint.rollback()
            self._revert_pending_preload_additions()
            logger.warning(
                "ingest.integrity_conflict entity=%s source_ref=%s",
                entity_type,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors=integrity_conflict_errors(exc),
            )
        except Exception:  # noqa: BLE001 - one record's failure, not the batch's
            savepoint.rollback()
            self._revert_pending_preload_additions()
            # SEC3 (fix-round-2): never echo a non-domain exception's own
            # message - it routinely quotes SQL, a table/column name or a raw
            # UUID. Logged with exc_info=True instead.
            logger.warning(
                "ingest.record_failed entity=%s source_ref=%s",
                entity_type,
                payload.source_ref,
                exc_info=True,
            )
            return RecordResult(
                source_ref=payload.source_ref,
                outcome=IngestOutcome.FAILED,
                errors={"_": INTERNAL_ERROR_MESSAGE},
            )

    def _apply(
        self, entity_type: str, spec: EntitySpec, payload: Any
    ) -> tuple[IngestOutcome, str, Optional[dict[str, dict[str, Any]]], list[str]]:
        """Pins `company_scope` for the WHOLE record, not just `_insert`/`_update`'s
        own writes: `_product_columns` (`ensure_reference`/`resolve_master_by_code`)
        and the generic adopt-fallback below both run ordinary ORM queries against
        company-scoped tables, and without this those queries are filtered by
        whatever the ambient session scope happens to be - which a caller running
        two companies through one session (this module's own parity tests) never
        resets between calls. See `_insert`'s own docstring for the fuller version
        of this note.
        """
        with company_scope(self.db, frozenset({self.company_id})):
            return self._apply_scoped(entity_type, spec, payload)

    def _apply_scoped(
        self, entity_type: str, spec: EntitySpec, payload: Any
    ) -> tuple[IngestOutcome, str, Optional[dict[str, dict[str, Any]]], list[str]]:
        warnings: list[str] = []
        if entity_type == "products":
            # C2: only the product builder resolves category/uom/brand
            # references, so only it gets the per-batch cache. `self.
            # _system_settings()` (Group 3): the batch's own already-cached
            # row, so `resolve_default_uom`'s configured-default branch
            # never re-queries `system_settings` per record either.
            columns = spec.to_columns(
                payload, self.db, self.company_id, warnings, self._ref_cache,
                self._system_settings(),
            )
        else:
            columns = spec.to_columns(payload, self.db, self.company_id, warnings)
        _apply_not_null_defaults(entity_type, columns)

        # BL-056 (D15): `self.refs` is scoped to this anchor company, so a ref
        # linked under a DIFFERENT company simply never resolves here - the
        # same answer as one that was never linked at all. The cross-company
        # refusal this used to need (`_require_same_company`) is unreachable
        # through refs now and has been removed.
        existing_id = self._resolve_ref(entity_type, payload.source_ref)
        if existing_id is not None:
            product_row = None
            if entity_type == "products":
                # C3: one shared SELECT for `_finalize_product_derived` and
                # `_diff`, instead of one each.
                product_row = self._read_product_row(existing_id, columns)
                self._finalize_product_derived(payload, columns, existing_id, row=product_row)
            if entity_type == "customers":
                self._finalize_customer_segment_fill_only(columns, existing_id)
            diff = self._diff(spec, existing_id, columns, row=product_row)
            if diff != {}:
                # C1: `{}` is a real answer ("nothing to write"), not "diff
                # unavailable" - skipping `_update` here is the whole point,
                # not a guard against a missing value.
                self._update(spec, existing_id, columns)
            self._link(entity_type, existing_id, payload)
            self._post_write_product_hooks(entity_type, existing_id)
            self._fan_out_customer_agent(entity_type, spec, existing_id, columns)
            return IngestOutcome.UPDATED, existing_id, diff, warnings

        # First sync: adopt a local record with the same business identity
        # rather than creating a duplicate under a new id. Customers override
        # this with the (code, name) pair (D13); everything else matches on
        # the bare business code. Sales agents keep their own normalised-code
        # match (`_lookup_id`) - shared table, not company-scoped the way
        # `resolve_master_by_code` assumes. Every other master matches
        # case/whitespace-insensitively (D17), through the same function the
        # manual create services now use too.
        if spec.adopt_lookup is not None:
            adopted = spec.adopt_lookup(self.db, payload, self.company_id)
        elif spec.normalized_code:
            adopted = _lookup_id(
                self.db, spec.table, spec.code_column, payload.code, self.company_id, normalized=True
            )
        elif entity_type == "products" and self._preload is not None:
            # Round 2: the batch preload's own code_to_id map first - a miss
            # (this code is genuinely new, or the preload failed/didn't run)
            # falls back to the exact query the `else` branch below runs.
            adopted = self._preload.code_to_id.get(normalize_code(payload.code))
            if adopted is None:
                adopted = resolve_master_by_code(self.db, spec.model, payload.code, self.company_id)
        else:
            adopted = resolve_master_by_code(self.db, spec.model, payload.code, self.company_id)
        if adopted is not None:
            origin = self._origin_of(entity_type, adopted)
            if origin is not None:
                if entity_type == "products" and is_unclaimed_or_same_source(origin):
                    # Code-wins (ingest-products-code-wins, SR0): the same
                    # rule `MasterRefResolver` already applies to a document
                    # line's product rung (`WARN_REF_MISMATCH`) - the
                    # FoundryX AutoCount HTTP source exposes no numeric item
                    # key, so a product push always arrives keyed by item
                    # code even though the row is already claimed by an
                    # `AED_SORENTO:<numeric key>` reference SO/PO line ingest
                    # minted. The item code decides identity and the STORED
                    # reference is kept -- `_link` is deliberately never
                    # called here, so the incoming ref is never written.
                    from app.services.master_ref_resolver import WARN_REF_MISMATCH

                    # C3: this is the DOMINANT products path (FoundryX product
                    # rows carry no numeric key, so a push always arrives
                    # keyed by item code even for an already-linked row) - the
                    # one shared SELECT matters most here.
                    product_row = self._read_product_row(adopted, columns)
                    self._finalize_product_derived(payload, columns, adopted, row=product_row)
                    diff = self._diff(spec, adopted, columns, row=product_row)
                    if diff != {}:  # C1
                        self._update(spec, adopted, columns)
                    self._post_write_product_hooks(entity_type, adopted)
                    warnings.append(WARN_REF_MISMATCH)
                    return IngestOutcome.UPDATED, adopted, diff, warnings
                # Already claimed by a different source document -- surfacing
                # beats silently retargeting someone else's record.
                raise ReferenceConflict(
                    f"{spec.code_column}={payload.code!r} is already linked to another source"
                )
            product_row = None
            if entity_type == "products":
                product_row = self._read_product_row(adopted, columns)  # C3
                self._finalize_product_derived(payload, columns, adopted, row=product_row)
            if entity_type == "customers":
                self._finalize_customer_segment_fill_only(columns, adopted)
            # Captured before the UPDATE (dry run) or the skip (C1, real run):
            # an adoption overwrites a row somebody typed in by hand, and the
            # operator/audit trail gets no other chance to see what it replaces.
            diff = self._diff(spec, adopted, columns, row=product_row)
            if diff != {}:  # C1
                self._update(spec, adopted, columns)
            # T7: adoption still links the reference even on an empty diff -
            # the row already existed unclaimed, and this push is what claims
            # it, whether or not it changes a single column.
            self._link(entity_type, adopted, payload)
            self._post_write_product_hooks(entity_type, adopted)
            self._fan_out_customer_agent(entity_type, spec, adopted, columns)
            return IngestOutcome.UPDATED, adopted, diff, warnings

        if entity_type == "products":
            self._finalize_product_derived(payload, columns, None)
            self._fill_create_only_product_gaps(columns)
        new_id = self._insert(entity_type, spec, columns)
        self._link(entity_type, new_id, payload)
        self._post_write_product_hooks(entity_type, new_id)
        self._fan_out_customer_agent(entity_type, spec, new_id, columns)
        # Nothing existed to overwrite, so there is no diff to report. Distinct
        # from {} -- see RecordResult.diff.
        return IngestOutcome.CREATED, new_id, None, warnings

    def _fan_out_customer_agent(
        self, entity_type: str, spec: EntitySpec, customer_id: str, columns: dict[str, Any]
    ) -> None:
        """Set the resolved agent on every other ledger of this company with the same debtor
        code (back-created rows an order import made under the same code). Each sibling goes
        through `_update`, so the audit listener and `updated_at` fire. Runs inside the
        record's savepoint, so a dry run or a failed record rolls it back."""
        agent_id = columns.get("sales_agent_id")
        if entity_type != "customers" or agent_id is None:
            return
        with company_scope(self.db, frozenset({self.company_id})):
            sibling_ids = [
                str(r[0])
                for r in self.db.execute(
                    text(
                        "SELECT id FROM customers "
                        "WHERE company_id = :cid AND id <> :id "
                        "AND lower(btrim(customer_code)) = lower(btrim(:code)) "
                        "AND sales_agent_id IS DISTINCT FROM :aid ORDER BY id"
                    ),
                    {"aid": agent_id, "cid": self.company_id, "id": customer_id,
                     "code": columns["customer_code"]},
                )
            ]
        for sibling_id in sibling_ids:
            self._update(spec, sibling_id, {"sales_agent_id": agent_id})

    def _fill_create_only_product_gaps(self, columns: dict[str, Any]) -> None:
        """Live fix, 2026-09-06: `category_id`/`base_uom_id` are NOT NULL FKs
        `_product_columns` leaves OUT of `columns` entirely when the payload
        never names a category/uom at all - D14's "absent = untouched" is
        correct for an UPDATE, but there is nothing to leave untouched on a
        genuine CREATE, and the insert crashed with a `NotNullViolation`
        instead. Called ONLY from the CREATE branch, immediately before
        `_insert` - an UPDATE never reaches this and keeps D14's rule
        unchanged.

        `category_id` has no usable default - the manual create form
        requires it too, with none - so an absent category is retryable, the
        same verdict an unresolvable `category_code` already gets.
        `base_uom_id` DOES have one: the same configured-default/`EA`
        fallback `_product_columns` already applies when `uom_code` is sent
        BLANK, widened here to the ABSENT case too.
        """
        if "category_id" not in columns:
            raise MissingReference("category_code", "")
        if "base_uom_id" not in columns:
            columns["base_uom_id"] = product_rules.resolve_default_uom(
                self.db, self.company_id, self._system_settings(), cache=self._ref_cache
            )

    #: C3: `_finalize_product_derived`'s own four columns, unioned onto
    #: whatever `_read_product_row`'s caller already has in `columns` -
    #: never the full 36-column row (`products` has more than the two callers
    #: sharing this SELECT ever read - a first cut at this used `SELECT *`
    #: and the clone measurement showed it costing MORE wall time than the
    #: narrower two-query version it replaced: an extra ~28 columns'
    #: worth of UUID/Decimal/timestamp deserialisation per row, paid on
    #: every one of ~11,800 records, outweighed the one saved round trip on
    #: localhost's near-zero latency).
    #: Fix round (Group 3, both reviewers): `discontinued_notified_at` /
    #: `discontinued_notify_batch_id` joined the set - `_finalize_product_
    #: derived` writes both to `columns` on a True->False transition, but
    #: without them here `_diff` compared `row.get(column)` for a column
    #: `row` never selected, i.e. always `None`, against the SAME `None`
    #: `_finalize_product_derived` just wrote - looked unchanged regardless
    #: of the row's REAL stored value, so a discontinued -> live product's
    #: preview silently dropped the watermark reset from its own diff (the
    #: real `_update` wrote it correctly either way; this was a preview-
    #: fidelity bug, not a data one).
    _DERIVED_PRODUCT_COLUMNS = (
        "is_discontinued",
        "dimensions_length",
        "dimensions_width",
        "dimensions_height",
        "discontinued_notified_at",
        "discontinued_notify_batch_id",
    )

    def _read_product_row(self, product_id: str, columns: dict[str, Any]) -> Optional[Any]:
        """C3 (`PLAN-autocount-pull-preview-perf.md`, only built because C1+C2
        alone missed the clone target): the ONE SELECT `_finalize_product_
        derived` and `_diff` now share for an existing product, in place of
        one query each - exactly the columns either of them will read
        (`columns`' own keys, decided by which fields this payload set, plus
        the four `_finalize_product_derived` always looks at), never wider.
        """
        selected = ", ".join(dict.fromkeys((*columns, *self._DERIVED_PRODUCT_COLUMNS)))
        return (
            self.db.execute(
                text(f"SELECT {selected} FROM products WHERE id = :id"), {"id": product_id}
            )
            .mappings()
            .first()
        )

    def _finalize_product_derived(
        self,
        payload: Any,
        columns: dict[str, Any],
        existing_row_id: Optional[str],
        *,
        row: Optional[Any] = None,
    ) -> None:
        """D2/D4/D24: `is_discontinued` and `dimensions_*` are both derived
        from `description` ONLY, never `name` - D24 (captain 2026-09-06)
        makes `_product_columns` force `description` to the AutoCount
        Description text on every push (the payload's own `description`
        when it sends one, else `name`, transitionally), so both channels'
        rules can read the one column the xlsx import always used.

        An explicit `is_discontinued` flag still wins over the derived one;
        dimensions have no equivalent explicit-value override, so a parsed
        value is written whenever it differs from what the row already
        holds - which keeps an unrelated push (price-only, say) from
        re-deriving a no-op back onto a manually corrected dimension: the
        "differs" check is against the CURRENT stored value. True->False
        resets the notify watermark, same rule `product_service.update_product`
        applies manually.

        `row` (C3): the caller's own pre-fetched `_read_product_row` mapping,
        reused instead of this method running its own narrower SELECT - `None`
        (every caller but `_apply_scoped`'s three "existing product" branches)
        falls back to querying it here, unchanged.
        """
        current_discontinued = None
        current_length = current_width = current_height = None
        if existing_row_id is not None:
            if row is None:
                row = (
                    self.db.execute(
                        text(
                            "SELECT is_discontinued, dimensions_length, dimensions_width, "
                            "dimensions_height FROM products WHERE id = :id"
                        ),
                        {"id": existing_row_id},
                    )
                    .mappings()
                    .first()
                )
            if row is not None:
                current_discontinued = row["is_discontinued"]
                current_length = row["dimensions_length"]
                current_width = row["dimensions_width"]
                current_height = row["dimensions_height"]

        description = columns.get("description")

        if "is_discontinued" in payload.model_fields_set:
            new_flag = bool(payload.is_discontinued)
        else:
            new_flag = product_rules.is_discontinued(None, description)
        columns["is_discontinued"] = new_flag

        if existing_row_id is not None and current_discontinued and not new_flag:
            columns["discontinued_notified_at"] = None
            columns["discontinued_notify_batch_id"] = None

        length_mm, width_mm, height_mm = product_rules.parse_dimensions(description)
        if length_mm is not None and length_mm != current_length:
            columns["dimensions_length"] = length_mm
        if width_mm is not None and width_mm != current_width:
            columns["dimensions_width"] = width_mm
        if height_mm is not None and height_mm != current_height:
            columns["dimensions_height"] = height_mm

    def _finalize_customer_segment_fill_only(
        self, columns: dict[str, Any], existing_row_id: Optional[str]
    ) -> None:
        """D16: a segment already set by hand is never overwritten - the row's
        OWN value wins over whatever this push resolved, on an update only (a
        create has nothing to protect)."""
        if existing_row_id is None or "market_segment_code" not in columns:
            return
        current = self.db.execute(
            text("SELECT market_segment_code FROM customers WHERE id = :id"),
            {"id": existing_row_id},
        ).scalar()
        if current:
            columns.pop("market_segment_code")

    def _system_settings(self) -> Optional[SystemSetting]:
        """The singleton `system_settings` row, read ONCE per batch (perf
        review S6) - this hook used to re-query it for every product record,
        an N+1 the ESB's own 11.7k-product pushes pay for on every one of
        them. Cached on the instance, which lives for exactly one batch
        (`MasterIngestService` is constructed per request), the same
        lifetime `bulk_import_products` already caches its own settings read
        for."""
        if self._settings_cache is _UNSET:
            self._settings_cache = self.db.query(SystemSetting).first()
        return self._settings_cache

    def _post_write_product_hooks(self, entity_type: str, product_id: str) -> None:
        """D5: the default-supplier `product_suppliers` link, on create AND
        update - exactly as the Excel import applies it, moved to
        `product_rules.link_default_supplier` so this and the manual
        create/edit path (`ProductService._ensure_default_supplier_lead_time`)
        share the one body.

        Round 2 (fix round, B2): when the batch preload ran, its own already-
        resolved `default_supplier_id` and per-product `default_supplier_
        lead_time` are passed through - but `product_id` MISSING from that
        map is not "confirmed no link" (`_ProductBatchPreload.default_
        supplier_lead_time`'s own docstring has the full reasoning), so the
        `.get` default is `product_rules.NOT_PRELOADED`, never Python's bare
        `None`; only an id the preload map genuinely covers skips the real
        query. `link_default_supplier` returns the lead time now current for
        `product_id` (created, refreshed, or already matching) whenever a
        default supplier resolved at all - written straight back into the
        map, with the same `_pending_preload_additions` revert bookkeeping
        every other preload map uses, so a LATER record in this same batch
        sharing the id (T12: a second adopter right behind this one; T13: a
        duplicate code right behind this record's own create) sees it
        without a query and without re-inserting the row this call just
        made.
        """
        if entity_type != "products":
            return
        if self._preload is not None:
            lead_time_days = product_rules.link_default_supplier(
                self.db, product_id, self._system_settings(),
                default_supplier_id=self._preload.default_supplier_id,
                existing_lead_time_days=self._preload.default_supplier_lead_time.get(
                    product_id, product_rules.NOT_PRELOADED
                ),
            )
            if lead_time_days is not None:
                self._preload.default_supplier_lead_time[product_id] = lead_time_days
                self._pending_preload_additions.append(("default_supplier_lead_time", product_id))
        else:
            product_rules.link_default_supplier(self.db, product_id, self._system_settings())

    def _insert(self, entity_type: str, spec: EntitySpec, columns: dict[str, Any]) -> str:
        """D18: the ORM insert, so `before_insert` company-stamping, the audit
        `before_flush` listener and the embedding `after_insert` listener all
        fire - none of which a raw ``INSERT`` statement ever reached.

        ``company_scope`` is pinned to the anchor for the duration, not read
        off the ambient session scope: a caller (this parity test fixture,
        certainly a batch ingest) can run two companies through the same
        session without resetting global state between them, and an insert or
        the row lookup in ``_update`` must never drift onto whichever company
        happened to be ambient last.
        """
        insert_columns = dict(columns)
        # Live fix, 2026-09-06: a NOT NULL column absent from `columns`
        # entirely (never sent by the payload) reads as None to the ORM
        # constructor below the same way an explicit null does - the model's
        # own Python `default=` only fires when the attribute is never
        # assigned at all, so this MUST run before `spec.model(**insert_columns)`,
        # not rely on it. `_apply_not_null_defaults` (same table) already
        # fixed the "explicit null" case for both create and update, before
        # this method was ever called - `setdefault` here only ever fills a
        # key that is genuinely missing, so the two never fight.
        for column, default in _NOT_NULL_DEFAULTS.get(entity_type, {}).items():
            insert_columns.setdefault(column, default)
        # The audit `before_flush` listener reads a pending object's PK straight
        # off the instance attribute - a PK still waiting on its column default
        # reads as None there and the create goes unrecorded (same gap
        # `Customer`'s own `"init"` event exists to close, order.py:174). Every
        # entity here gets the same fix at the one call site that creates all
        # of them, rather than one `"init"` listener per model.
        insert_columns.setdefault("id", str(uuid.uuid4()))

        with company_scope(self.db, frozenset({self.company_id})):
            row = spec.model(**insert_columns)
            if _is_company_scoped(spec.table):
                row.company_id = self.company_id
            if entity_type == "sales_agents":
                # D18: only on create - an existing agent's provenance (manual,
                # import) is never overwritten by a later AutoCount confirmation.
                row.source = "autocount"
                if not (insert_columns.get("person_label") or "").strip():
                    row.person_label = derive_person_label(insert_columns.get("sales_agent"))
            if self.stamp_user_id:
                # AC-PC-4: only a pull Confirm sets `stamp_user_id` at all - the
                # ordinary FoundryX push leaves both columns untouched, same as today.
                if hasattr(row, "created_by"):
                    row.created_by = self.stamp_user_id
                if hasattr(row, "updated_by"):
                    row.updated_by = self.stamp_user_id
            self.db.add(row)
            self.db.flush()
            new_id = str(row.id)
            if entity_type == "products" and self._preload is not None:
                # Round 2 (T10): so a LATER record in this same batch sharing
                # this code adopts THIS row through the map, never a second
                # per-record query - and (T11) reverted if this record's own
                # savepoint later rolls back.
                normalized = normalize_code(insert_columns.get("product_code"))
                if normalized:
                    self._preload.code_to_id[normalized] = new_id
                    self._pending_preload_additions.append(("code_to_id", normalized))
            return new_id

    def _diff(
        self,
        spec: EntitySpec,
        entity_id: str,
        columns: dict[str, Any],
        *,
        row: Optional[Any] = None,
    ) -> Optional[dict[str, dict[str, Any]]]:
        """Values this record would replace (dry run) or is about to replace
        (real run) on an existing row.

        C1 (`PLAN-autocount-pull-preview-perf.md`): runs on a REAL ingest too,
        not only a dry run - the one SELECT it costs is less than the ORM
        SELECT + UPDATE + listener fan-out `_update` used to pay on every
        record regardless of whether anything actually changed. The caller
        skips `_update` entirely when this comes back `{}` (PP-1); an empty
        dict is still a real answer, not "no diff computed" - see
        `RecordResult.diff`'s own docstring for why that is a different
        statement from `None` (a create, nothing to diff against).

        Only columns whose value actually changes are reported. An operator
        reviewing a sync is asking "what am I about to lose?", and burying three
        real changes in twelve unchanged fields answers a different question.

        `row` (C3, built only because C1+C2 alone missed the clone target):
        the caller's own pre-fetched mapping (`_read_product_row`, products
        only) - reused instead of this method running its own SELECT. `None`
        (every non-product entity, unchanged) falls back to querying it here.
        """
        if row is None:
            # Column names come from the module's own to_columns mappings, never
            # from the payload, so interpolating them is safe -- same basis as
            # the UPDATE and INSERT below.
            selected = ", ".join(columns)
            row = (
                self.db.execute(
                    text(f"SELECT {selected} FROM {spec.table} WHERE id = :id"), {"id": entity_id}
                )
                .mappings()
                .first()
            )
        if row is None:
            return None

        return {
            column: {"current": row.get(column), "incoming": incoming}
            for column, incoming in columns.items()
            if _value_changed(row.get(column), incoming)
        }

    def _update(self, spec: EntitySpec, entity_id: str, columns: dict[str, Any]) -> None:
        """D18: setattr on the ORM row, not a blind ``UPDATE`` - so the audit
        `before_flush` listener and the embedding `after_update` listener both
        fire, and only the columns this record actually sent are touched (D14).

        `updated_at` is stamped explicitly rather than left to `onupdate` -
        several of these tables (``customers`` among them) declare the column
        plain-nullable with no `onupdate=func.now()`, the same gap their own
        manual-service `update_*` methods paper over ad hoc (see
        `product_service.update_product`, `inventory_service.update_warehouse`)."""
        with company_scope(self.db, frozenset({self.company_id})):
            row = self.db.query(spec.model).filter(spec.model.id == entity_id).first()
            if row is None:
                return
            for column, value in columns.items():
                setattr(row, column, value)
            if hasattr(row, "updated_at"):
                row.updated_at = datetime.utcnow()
            if self.stamp_user_id and hasattr(row, "updated_by"):
                # AC-PC-4: `created_by` is never touched on an update - only `_insert`
                # sets it, so a record's original creator survives every later sync.
                row.updated_by = self.stamp_user_id
            self.db.flush()

    def _link(self, entity_type: str, entity_id: str, payload: Any) -> None:
        self.refs.link(
            entity_type=entity_type,
            entity_id=entity_id,
            source_ref=payload.source_ref,
            source_doc_no=payload.source_doc_no,
            integration_id=self.integration_id,
        )
        if entity_type == "products" and self._preload is not None:
            if payload.source_ref:
                # Round 2 (T10/T11): same reasoning as `_insert`'s own map
                # update - a later same-batch record sharing this source_ref
                # (a genuine duplicate push) resolves it via the map, and a
                # rollback undoes this addition along with the row it named.
                self._preload.ref_to_entity[payload.source_ref] = entity_id
                self._pending_preload_additions.append(("ref_to_entity", payload.source_ref))
            # Fix round, B1 (round-1 review, both reviewers): the call above
            # just claimed `entity_id` for AutoCount (`self.refs.link`'s own
            # default `source_system`) - without recording that here too, a
            # SECOND record in this same batch that resolves the SAME
            # product by a normalize-equal code right after this one reads
            # the STALE preloaded origin (`None`, from before this call ran,
            # or altogether absent for one `_insert` just created) instead,
            # takes the unclaimed-adopt branch, and its own `self.refs.link`
            # call then raises `ReferenceConflict` against the ref THIS call
            # just wrote - a regression `_apply_scoped`'s own code-wins
            # branch exists specifically to avoid (T12/T13).
            self._preload.origin_by_entity[entity_id] = _PreloadedOrigin(
                source_system=DEFAULT_SOURCE_SYSTEM
            )
            self._pending_preload_additions.append(("origin_by_entity", entity_id))


def _value_changed(current: Any, incoming: Any) -> bool:
    """Whether writing ``incoming`` over ``current`` would change anything.

    Numbers are compared by value rather than by type. The database hands back
    ``Decimal('0.00')`` where the canonical payload carries ``Decimal('0')`` or
    an int, and reporting that as a change would fill an operator's diff with
    edits that are not edits -- which trains them to skim the one that is.

    A foreign key (``category_id``, ``brand_id``, ``base_uom_id``, ...) is the
    same shape of false positive, for a different reason: ``_diff``'s ``current``
    comes back from a raw ``text()`` SELECT, which the driver hands back as a
    native ``uuid.UUID`` for every postgres ``uuid`` column regardless of the
    ORM column's own ``as_uuid=False`` -- while every id this module resolves
    (``product_rules.ensure_reference``, ``resolve_master_by_code``, an
    incoming payload's own FK) is a plain ``str``. Left unguarded, an unchanged
    FK on an otherwise-identical record compared ``UUID(...) != "same value"``,
    which is always true, and reported the record as changed with a diff that
    named nothing real (caught by AC-PP-3's parity test, PLAN-autocount-pull-review.md).
    """
    if current is None or incoming is None:
        return (current is None) != (incoming is None)

    if isinstance(current, uuid.UUID) or isinstance(incoming, uuid.UUID):
        return str(current) != str(incoming)

    numeric = (int, float, Decimal)
    if (
        isinstance(current, numeric)
        and isinstance(incoming, numeric)
        # bool subclasses int, so without this guard Decimal(str(True)) raises
        # InvalidOperation. Booleans fall through to plain equality, which is
        # what they want -- and since is_active is on every canonical shape,
        # this is the common path, not an edge case.
        and not isinstance(current, bool)
        and not isinstance(incoming, bool)
    ):
        try:
            return Decimal(str(current)) != Decimal(str(incoming))
        except (InvalidOperation, ValueError):
            return str(current) != str(incoming)

    return current != incoming


def _field_errors(exc: ValidationError) -> dict[str, str]:
    """Flatten pydantic errors to field -> reason."""
    out: dict[str, str] = {}
    for err in exc.errors():
        location = ".".join(str(p) for p in err.get("loc", ())) or "_"
        out[location] = err.get("msg", "invalid")
    return out
