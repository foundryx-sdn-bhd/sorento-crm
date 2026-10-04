"""Apply a customer (debtor) listing to `customers`.

Three decisions carry this file, all from UAC-customer-importer.

**The key is (company, debtor code).** DEBTOR-CODE-UNIQUE (owner, 4 Oct 2026): one customer per
debtor code per company, like AutoCount (`customer_identity`). A row whose code is already held
updates that customer and never renames it; a row under another name only fills the customer's
blanks, and its name is kept as an alias (`customer_aliases`) so search still finds it. Only a code nobody in the company holds
is inserted. A code twice in one file is one customer: the later row is a duplicate.

**What a person curates is never overwritten.** Account owners, notes and the active flag are
untouched by any re-import; the market segment is filled when blank and never replaced,
because it decides SCM demand class and a silent change re-prioritises live orders. A blank
cell means "not supplied", never "clear the field" (AC-3). `customer_type` is set on insert
and never moved after: it is the discriminator the app branches on, and a debtor listing's
`Debtor Type` column speaks a different vocabulary (Trade / Cash / Local).

**Company comes from the job's scope, never from the file.** Every write goes through the ORM
so `CompanyScopedMixin`'s `before_insert` stamps it and scoped reads isolate. Raw SQL here
would bypass the stamp and the row would either violate the NOT NULL or land invisible to
every scoped read, which presents as "the import silently did nothing" (AC-2).

**What the preview promises, the import keeps.** Both run through `_run`, and the one check
that reads the schema - the over-length pre-check - reads it from the DATABASE, once per
import (`column_limits`), not from the model. It read the model until a real 4,196-row export
met a `phone_number` column the model called String(50) and Postgres called `varchar(20)`: 58
rows passed a preview promising 0 failures and were then refused at apply time. Migration 355
realigned the two and `tests/test_customers_schema_drift.py` fails on the next divergence,
but sourcing the check from the schema is what makes a future drift an honest preview rather
than a surprise (AC-7).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import func, inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.order import Customer
from app.services import customer_identity
from app.services import import_outcome_codes as oc
from app.services.customer_import_reader import (
    CustomerReadResult,
    CustomerRow,
    read_workbook,
)
from app.services.import_outcome import ImportOutcome

logger = logging.getLogger(__name__)

#: Freely updatable: the file is the source of truth for these (AC-3.1).
UPDATABLE_FIELDS = (
    "email",
    "phone_number",
    "mobile_number",
    "registered_name",
    "trading_name",
    "registration_number",
    "industry",
    "website",
    "country",
    "tax_id",
    "salutation",
    "first_name",
    "last_name",
)

#: Written on INSERT only, never by a re-import. `customer_type` is the discriminator
#: the app branches on, and a real AutoCount listing's `Debtor Type` column carries
#: Trade / Cash / Local - values nothing in the app recognises. All 3,284 live rows are
#: `company`, so letting a file move an existing row's type would silently rewrite the
#: discriminator with vocabulary from another system (UAC AC-3/AC-4, decision D1). No
#: alias ships for it either, so today only an admin-added alias row can fill it at all.
INSERT_ONLY_FIELDS = ("customer_type",)

#: Filled when blank, never replaced. `market_segment_code` decides SCM demand class and
#: fulfilment priority: filling a NULL is a gift, overwriting a curated one silently
#: re-prioritises live orders (AC-3).
FILL_IF_EMPTY_FIELDS = ("market_segment_code",)

#: NEVER written by an import, at insert or update: identity, provenance, the key itself,
#: the human sales assignment, free text, and the active flag. Named so the list is
#: reviewable rather than implied by omission.
NEVER_WRITTEN_FIELDS = (
    "id",
    "created_at",
    "created_by",
    "company_id",
    "customer_code",
    "customer_name",
    "account_owner_user_id",
    "notes",
    "is_active",
    "billing_address",
)


# A field cannot be both writable and protected. Asserted rather than trusted to review:
# adding `notes` to UPDATABLE_FIELDS is a one-word change that would silently start
# overwriting human free text on every re-import.
assert not (
    set(UPDATABLE_FIELDS + FILL_IF_EMPTY_FIELDS + INSERT_ONLY_FIELDS)
    & set(NEVER_WRITTEN_FIELDS)
)
# Insert-only means insert-only: a field in both lists would be updatable after all.
assert not (set(INSERT_ONLY_FIELDS) & set(UPDATABLE_FIELDS + FILL_IF_EMPTY_FIELDS))

#: Rows sampled into the preview payload.
_SAMPLE = 8

#: What the MODEL declares. Used only as a fallback when the real schema cannot be read -
#: never as the truth, because the two have already disagreed once (see `column_limits`).
_MODEL_LENGTHS: dict[str, int] = {
    name: column.type.length
    for name, column in Customer.__table__.columns.items()
    if getattr(column.type, "length", None)
}


def _customers_schema(bind: Any) -> Optional[str]:
    """The schema `customers` actually lives in for this connection.

    Reflection is a catalog read and does NOT honour `schema_translate_map`, which is how
    the test suite points the same models at a scratch schema. Without this the guard would
    reflect `public` while the import wrote to `zzt_blank_*`, and the two limits could
    disagree in exactly the place the drift is being tested.
    """
    try:
        options = bind.get_execution_options()
    except AttributeError:  # a bind that does not carry execution options
        return None
    return ((options or {}).get("schema_translate_map") or {}).get(None)


def column_limits(db: Session) -> dict[str, int]:
    """Every `customers` varchar limit as the DATABASE has it, not as the model declares it.

    The pre-check that fails an over-length row before the write used to read the model's
    lengths. Those lengths drifted: `phone_number` was declared String(50) and the column was
    `varchar(20)`, so 58 rows of a real 4,196-row export passed a preview promising 0 failures
    and were then rejected by Postgres at apply time. Migration 355 realigned the two, but a
    check sourced from the model rots silently on the next drift, whereas one sourced from the
    schema degrades into an honest preview: the rows are named BEFORE the operator confirms.

    Read ONCE per import (from `_run`), never per row, and deliberately not cached across
    imports - a cached limit would outlive the migration that widened the column, in a
    long-lived worker process, and start failing rows that the database now accepts.

    Best effort: if the catalog cannot be read the model's lengths are used with a warning.
    Losing the pre-check entirely would send an over-length value to Postgres, whose DataError
    poisons the enclosing transaction.
    """
    bind = db.get_bind()
    try:
        reflected = sa_inspect(bind).get_columns(
            Customer.__tablename__, schema=_customers_schema(bind)
        )
    except Exception:  # noqa: BLE001 - any reflection failure falls back, never fails the import
        logger.warning(
            "could not read the customers column widths; falling back to the model's",
            exc_info=True,
        )
        return dict(_MODEL_LENGTHS)
    limits = {
        column["name"]: column["type"].length
        for column in reflected
        if isinstance(getattr(column.get("type"), "length", None), int)
        and column["type"].length > 0
    }
    return limits or dict(_MODEL_LENGTHS)


def _key(value: str) -> str:
    """The index's own comparison key: `lower(btrim(value))`.

    Python's `strip()` removes a slightly wider set of whitespace than `btrim`, which trims
    spaces. The difference can only make this key MORE eager to match, so the worst case is
    updating a held row where Postgres would have allowed a second one - never an insert
    that takes a 23505, which is the failure that matters.
    """
    return value.strip().lower()


def preview(db: Session, file_data: bytes) -> dict[str, Any]:
    """What the file says and what it would do, having written nothing.

    Runs the same resolution `apply` runs, so the Test button and Confirm cannot disagree
    about the same file.
    """
    outcome = ImportOutcome(None, persist=False)
    return _run(db, file_data, outcome, write=False, actor=None)


def apply(
    db: Session,
    file_data: bytes,
    outcome: ImportOutcome,
    *,
    actor: Optional[str] = None,
    on_total_rows: Optional[Callable[[int], None]] = None,
) -> dict[str, Any]:
    """Write the file. Does not commit; the caller owns the transaction.

    `on_total_rows` is called ONCE, as soon as the sheet is read and before any row is
    written, with the number of data rows the file holds. The job's `total_rows` is
    otherwise first set when the job completes, so the upload drawer reads 0/0 for the
    whole run, which looks stuck.
    """
    return _run(db, file_data, outcome, write=True, actor=actor, on_total_rows=on_total_rows)


def _run(
    db: Session,
    file_data: bytes,
    outcome: ImportOutcome,
    *,
    write: bool,
    actor: Optional[str],
    on_total_rows: Optional[Callable[[int], None]] = None,
) -> dict[str, Any]:
    parsed = read_workbook(file_data, db=db)
    out = _empty_result(parsed)
    if on_total_rows is not None:
        try:
            on_total_rows(parsed.total_rows)
        except Exception:  # noqa: BLE001 - progress reporting never costs the import
            logger.warning("could not report the customer import row total", exc_info=True)
    if not parsed.ok:
        return out

    # Rows the reader could not use are outcomes too - a skip without a reason is exactly
    # what `ImportOutcome` exists to make impossible.
    for problem in parsed.problems:
        code = (
            oc.MISSING_REQUIRED_FIELD
            if "no customer" in problem.reason
            else oc.ROW_ERROR
        )
        outcome.skip(
            row=problem.row_number,
            code=code,
            message=problem.reason,
            value=problem.value or None,
        )

    dropped_segments = _resolve_market_segments(db, parsed.rows, out)
    held_by_code = _load_candidates(db, parsed.rows)
    # ONE reflection for the whole file, and the same one for preview and apply, so Test and
    # Confirm can never disagree about how long a column is.
    limits = column_limits(db)

    seen: set[str] = set()
    now = datetime.utcnow()

    for row in parsed.rows:
        code_key = _key(row.customer_code)
        identity = {"customer_code": row.customer_code, "customer_name": row.customer_name}

        dropped_segment = dropped_segments.get(row.row_number)

        if code_key in seen:
            # One debtor code is one customer, so a second row under it states nothing new
            # about the customer. Counting it as a write would make the preview disagree
            # with the import. Its name is kept as an alias, the way a held code's is.
            if write and code_key in held_by_code:
                customer_identity.record_alias(
                    db, held_by_code[code_key].id, row.customer_name, source="customer_import"
                )
            out["skipped"] += 1
            out["problems"].append(
                {"row": row.row_number, "reason": "the same debtor code appears earlier in the file"}
            )
            outcome.skip(
                row=row.row_number,
                code=oc.DUPLICATE_IN_FILE,
                message="the same debtor code appears earlier in the file",
                value=row.customer_code,
                identity=identity,
            )
            continue
        seen.add(code_key)

        too_long = _too_long(row, limits)
        if too_long:
            out["failed"] += 1
            out["problems"].append({"row": row.row_number, "reason": too_long})
            outcome.fail(
                row=row.row_number,
                code=oc.ROW_ERROR,
                message=too_long,
                value=row.customer_code,
                identity=identity,
            )
            continue

        held = held_by_code.get(code_key)

        if held is None:
            code, message = _written_row_code(
                oc.CREATED, None, flag=None, dropped_segment=dropped_segment
            )
            if not write:
                out["created"] += 1
                outcome.success(
                    row=row.row_number,
                    code=code,
                    message=message,
                    value=row.customer_code,
                    identity=identity,
                )
                continue

            created, failure = _insert(db, row, actor=actor)
            if created is None:
                reason = failure or _SAVE_FAILED
                out["failed"] += 1
                out["problems"].append({"row": row.row_number, "reason": reason})
                outcome.fail(
                    row=row.row_number,
                    code=oc.UPSERT_ERROR,
                    message=reason,
                    value=row.customer_code,
                    identity=identity,
                )
                continue
            out["created"] += 1
            held_by_code[code_key] = created
            outcome.success(
                row=row.row_number,
                code=code,
                message=message,
                value=row.customer_code,
                identity=identity,
                entity_type="customer",
                entity_id=created.id,
            )
            continue

        if write:
            customer_identity.record_alias(
                db, held.id, row.customer_name, source="customer_import"
            )
        changes = _changes(held, row)
        if not changes:
            code, message = _written_row_code(
                oc.UNCHANGED, None, flag=None, dropped_segment=dropped_segment
            )
            out["unchanged"] += 1
            outcome.unchanged(
                row=row.row_number,
                code=code,
                message=message,
                value=row.customer_code,
                identity=identity,
                entity_type="customer",
                entity_id=held.id,
            )
            continue

        failure = (
            _update(db, held, changes, now, row=row) if write else None
        )
        if failure:
            out["failed"] += 1
            out["problems"].append({"row": row.row_number, "reason": failure})
            outcome.fail(
                row=row.row_number,
                code=oc.UPSERT_ERROR,
                message=failure,
                value=row.customer_code,
                identity=identity,
            )
            continue

        code, message = _written_row_code(
            oc.UPDATED,
            f"changed: {', '.join(sorted(changes))}",
            flag=None,
            dropped_segment=dropped_segment,
        )
        out["updated"] += 1
        outcome.updated(
            row=row.row_number,
            code=code,
            message=message,
            value=row.customer_code,
            identity=identity,
            entity_type="customer",
            entity_id=held.id,
        )

    out["sample"] = [
        {"customer_code": r.customer_code, "customer_name": r.customer_name}
        for r in parsed.rows[:_SAMPLE]
    ]
    return out


def _written_row_code(
    base_code: str,
    base_message: Optional[str],
    *,
    flag: Optional[str],
    dropped_segment: Optional[str],
) -> tuple[str, Optional[str]]:
    """The single code and message a WRITTEN row carries.

    A row can be worth a human's eye for two reasons at once, and `import_job_rows`
    holds exactly one code per row. The identity signal wins the code - a possible
    duplicate customer is a worse problem than one blank optional column - and the
    message states both, so neither reason is lost. Neither is ever a skip: the row is
    created, updated or unchanged as it otherwise would have been.
    """
    code = base_code
    notes = [base_message] if base_message else []
    if flag:
        code = oc.CODE_EXISTS_UNDER_OTHER_NAME
        notes.append(f"similar name already on this code: {flag}")
    if dropped_segment:
        if not flag:
            code = oc.MARKET_SEGMENT_NOT_RECOGNISED
        notes.append(f"market segment not recognised, left unset: {dropped_segment}")
    return code, "; ".join(notes) or None


def _empty_result(parsed: CustomerReadResult) -> dict[str, Any]:
    return {
        "readable": parsed.ok,
        "missing_columns": list(parsed.missing_columns),
        "unmapped_headers": list(parsed.unmapped_headers),
        "problems": [
            {"row": p.row_number, "reason": p.reason} for p in parsed.problems
        ],
        "total_rows": parsed.total_rows,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": len(parsed.problems),
        "failed": 0,
        "needs_review": 0,
        "review_rows": [],
        "unknown_market_segments": [],
        "unknown_market_segment_rows": 0,
        "sample": [],
    }


def _resolve_market_segments(
    db: Session, rows: list[CustomerRow], out: dict[str, Any]
) -> dict[int, str]:
    """Fold each row's market segment onto a real `market_segments.code`, or drop it.

    The column is a foreign key, so an unrecognised value would fail the row on insert -
    losing a whole customer over one optional column. The value is dropped instead and the
    spelling is reported, which is the same "name it, do not guess" rule the unmapped
    headers follow.

    Returns row number -> the spelling that was dropped, so each affected row can carry
    its own outcome code. The file-level list alone was not enough: the segment decides
    SCM demand class and fulfilment priority, and 40 customers could land with a NULL
    segment under a job that reported "40 created" and nothing else.
    """
    supplied = {
        row.values["market_segment_code"]
        for row in rows
        if row.values.get("market_segment_code")
    }
    if not supplied:
        return {}

    # S2: the fold itself moved to `customer_rules.fold_market_segment`, shared
    # with the ESB customers push - memoised per distinct spelling here so a
    # 4,000-row file still costs one query per DISTINCT value, not one per row.
    from app.services.rules import customer_rules

    memo: dict[str, Optional[str]] = {}
    dropped: dict[int, str] = {}
    for row in rows:
        value = row.values.get("market_segment_code")
        if value is None:
            continue
        key = _key(value)
        if key not in memo:
            memo[key] = customer_rules.fold_market_segment(db, value)
        canonical = memo[key]
        if canonical is None:
            dropped[row.row_number] = value
            row.values.pop("market_segment_code")
        else:
            row.values["market_segment_code"] = canonical
    if dropped:
        out["unknown_market_segments"] = sorted(set(dropped.values()))
        out["unknown_market_segment_rows"] = len(dropped)
    return dropped


def _load_candidates(db: Session, rows: list[CustomerRow]) -> dict[str, Customer]:
    """The customer already held under each code the file names, in ONE scoped read.

    Keyed by `lower(btrim(code))`. A code that still holds several rows (before the
    DEBTOR-CODE-UNIQUE merge has run) resolves to the row the merge keeps.
    """
    code_keys = {_key(r.customer_code) for r in rows}
    if not code_keys:
        return {}

    by_code: dict[str, list[Customer]] = {}
    # ORM query: the company-scope predicate is applied by `do_orm_execute`, so this reads
    # only the active company's book. Chunked because a file can name thousands of codes
    # and Postgres has a bind-parameter ceiling.
    ordered = sorted(code_keys)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start:start + 500]
        held = (
            db.query(Customer)
            .filter(func.lower(func.btrim(Customer.customer_code)).in_(chunk))
            .all()
        )
        for customer in held:
            by_code.setdefault(_key(str(customer.customer_code or "")), []).append(customer)

    out: dict[str, Customer] = {}
    for code_key, customers in by_code.items():
        if len(customers) == 1:
            out[code_key] = customers[0]
            continue
        survivor = customer_identity.customer_by_code(db, code_key)
        out[code_key] = survivor if survivor is not None else customers[0]
    return out


def _too_long(row: CustomerRow, limits: dict[str, int]) -> Optional[str]:
    """The one shape of bad data that would abort the whole transaction if written.

    Postgres rejects an over-length varchar with a DataError that poisons the enclosing
    transaction, so the row is failed BEFORE the write with a reason naming the column.
    Truncating instead would store something the file did not say.

    `limits` come from the DATABASE (`column_limits`), not from the model. A limit read from
    the model is a promise the database has not made.
    """
    for field_name, value in row.values.items():
        limit = limits.get(field_name)
        if limit is not None and len(value) > limit:
            return f"{field_name.replace('_', ' ')} is longer than {limit} characters"
    return None


#: What a row says when the database refused it and said nothing useful about why. Every other
#: path appends the database's own message: "could not be saved" alone cost a reader a worker
#: traceback to diagnose a one-line problem (a `varchar(20)` phone column).
_SAVE_FAILED = "could not be saved"

#: How much of the database's message a row carries. Long enough for a DETAIL line, short
#: enough that the job detail stays readable.
_REASON_LIMIT = 300

#: `value too long for type character varying(20)` - the one error Postgres reports WITHOUT
#: naming the column, so the offending field is identified from the row instead.
_VARCHAR_LENGTH = re.compile(r"character varying\((\d+)\)")


def _db_failure_reason(exc: Exception, *, row: Optional[CustomerRow] = None) -> str:
    """The database's own complaint, in the row's message.

    `DataError` and `IntegrityError` both carry a psycopg2 error whose `diag` holds the
    primary message and, for most errors, the column and constraint. Those are the words that
    diagnose the row; "could not be saved" is the words that send someone to the worker log.

    Two cases the driver leaves incomplete are filled in here:

    * **A too-long value names no column** (Postgres genuinely does not report one), so the
      fields whose value exceeds the width in the message are named from the row.
    * **A message that already contains the column name** is not annotated twice.
    """
    orig = getattr(exc, "orig", None) or exc
    diag = getattr(orig, "diag", None)
    primary = (getattr(diag, "message_primary", None) or "").strip()
    if not primary:
        text_of = str(orig).strip()
        primary = text_of.splitlines()[0].strip() if text_of else ""

    named: list[str] = []
    column = (getattr(diag, "column_name", None) or "").strip()
    if column and column not in primary:
        named.append(f"column {column}")
    constraint = (getattr(diag, "constraint_name", None) or "").strip()
    if constraint and constraint not in primary:
        named.append(f"constraint {constraint}")
    if not column and row is not None:
        named.extend(_fields_over(primary, row))

    detail = primary
    if named:
        detail = f"{detail} ({', '.join(named)})" if detail else ", ".join(named)
    if not detail:
        return _SAVE_FAILED
    if len(detail) > _REASON_LIMIT:
        detail = detail[: _REASON_LIMIT - 3].rstrip() + "..."
    return f"{_SAVE_FAILED}: {detail}"


def _fields_over(primary: str, row: CustomerRow) -> list[str]:
    """This row's fields longer than the width Postgres just complained about.

    A shortlist, not a verdict: Postgres names no column for a too-long value, so the best
    that can be said is "one of these". In practice it is one field, because reaching this
    branch at all means the pre-check's limit for that column was wrong - which is precisely
    the drift this names instead of hiding.
    """
    match = _VARCHAR_LENGTH.search(primary)
    if not match:
        return []
    width = int(match.group(1))
    return [
        f"column {name} holds {len(value)} characters"
        for name, value in sorted(row.values.items())
        if len(value) > width
    ]


def _insert(
    db: Session, row: CustomerRow, *, actor: Optional[str]
) -> tuple[Optional[Customer], Optional[str]]:
    """Add one customer inside its own savepoint, so a failed row cannot fail the file.

    Returns the customer, or `None` plus the reason the database gave for refusing it.
    """
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "customer_code": row.customer_code,
        "customer_name": row.customer_name,
        # True on insert only: absence from a later file is not a deactivation (AC-3).
        "is_active": True,
        "created_by": actor,
    }
    for field_name in UPDATABLE_FIELDS + FILL_IF_EMPTY_FIELDS + INSERT_ONLY_FIELDS:
        value = row.values.get(field_name)
        if value is not None:
            payload[field_name] = value
    # company_id is deliberately absent: `before_insert` stamps it from the session scope.
    try:
        with db.begin_nested():
            customer = Customer(**payload)
            db.add(customer)
            db.flush()
        return customer, None
    except SQLAlchemyError as exc:
        reason = _db_failure_reason(exc, row=row)
        logger.warning(
            "customer import: insert failed for %s / %s: %s",
            row.customer_code,
            row.customer_name,
            reason,
            exc_info=True,
        )
        return None, reason


def _changes(held: Customer, row: CustomerRow) -> dict[str, str]:
    """The fields this row would actually move on a customer we already hold.

    Empty means the row states nothing new, which is `unchanged` and no write at all - not
    an update with the same values (AC-3.3).

    A row under the customer's code but another NAME (a buyer under a cash code, kept as an
    alias) only fills blanks: one buyer's phone or email must not replace the customer's own.
    """
    fill_only = _key(row.customer_name) != _key(str(held.customer_name or ""))
    changes: dict[str, str] = {}
    for field_name in UPDATABLE_FIELDS:
        value = row.values.get(field_name)
        if value is None:
            continue  # a blank cell is "not supplied", never "clear it"
        if fill_only and (getattr(held, field_name) or None):
            continue
        if (getattr(held, field_name) or None) != value:
            changes[field_name] = value
    for field_name in FILL_IF_EMPTY_FIELDS:
        value = row.values.get(field_name)
        if value is None:
            continue
        if getattr(held, field_name) or None:
            continue  # already curated: the file does not get to replace it
        changes[field_name] = value
    return changes


def _update(
    db: Session,
    held: Customer,
    changes: dict[str, str],
    now: datetime,
    *,
    row: Optional[CustomerRow] = None,
) -> Optional[str]:
    """Move the changed fields inside a savepoint. `None` on success, else the reason."""
    try:
        with db.begin_nested():
            for field_name, value in changes.items():
                setattr(held, field_name, value)
            held.updated_at = now
            db.flush()
        return None
    except SQLAlchemyError as exc:
        reason = _db_failure_reason(exc, row=row)
        logger.warning(
            "customer import: update failed for %s / %s: %s",
            held.customer_code,
            held.customer_name,
            reason,
            exc_info=True,
        )
        return reason
