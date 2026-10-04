"""Fold every (company, debtor code) group of customers onto one survivor (DEBTOR-CODE-UNIQUE).

Card: documentation/plans/autocount/CARD-debtor-code-unique-04oct.md (R5-R13, Q1-Q5 all (a)).
CLI: `scripts/merge_customers_by_code.py`. Never run by alembic or on deploy.

Per group (company + `lower(btrim(customer_code))` holding more than one row):

- Survivor: `customer_identity.SURVIVOR_ORDER_SQL` (code-form AutoCount ref, any ref, oldest,
  lowest id). It keeps its own name, code and ref.
- Held (owner 4 Oct, decision (b)): a group whose retired names are another business once
  normalised (`normalise_name`, `held_reason`) is not merged unless its CSV row says
  `release=yes`; `301-*` cash codes are never held. The code-unique index waits for them.
- Fields: a blank on the survivor is filled from the retired rows, oldest first. Where both hold
  a value and differ on a `CONFLICT_FIELDS` column the group is a CONFLICT: `apply` skips it
  unless the owner named a `keep` row for it, whose values then win on those columns.
- Documents: an order / sales order with no `debtor_name` gets the retired row's name first, so
  no document loses the buyer name it showed.
- Every FK onto `customers.id` (read from `pg_constraint` at run time, never a list) moves to the
  survivor, except WhatsApp contact links (`respond_contact_customers`, an external contact's
  access): those are dropped and listed for CS to re-link (owner, 4 Oct). Unique collisions are
  resolved first and every dropped row is kept in full: a second main contact becomes
  stakeholder, a duplicate item-code mapping keeps the survivor's, retired AutoCount refs are
  dropped (`uq_integration_ref_entity`: one per row; the survivor's wins).
- The retired names become `customer_aliases`; the retired row is deleted.
- `customer_merge_map` holds everything `revert` needs, and one `audit_logs` row
  `CUSTOMER_MERGE` per retired id names it on the survivor. Old audit rows are never rewritten.

Everything here is raw SQL against whatever `customers` the session's search_path resolves, so
the caller decides the transaction (`--apply` commits once per company).
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import customer_identity

logger = logging.getLogger(__name__)

#: Columns where two non-blank, different values are a conflict for the owner (card R7, Q2):
#: survivor against a retired row, or two retired rows against each other when the survivor
#: holds none. A conflict group is skipped until its `keep` row is named.
CONFLICT_FIELDS = (
    "customer_group_id",
    "is_active",
    "sales_agent_id",
    "market_segment_code",
    "account_owner_user_id",
)

#: Conflicts only survivor against a retired row, compared on digits (`+60-12 345` = `6012345`).
#: Not between retired rows: under a cash code every walk-in buyer has their own phone, and
#: none of them is ever carried (`PERSONAL_FIELDS`), so only a clash with the kept row's own
#: phone is a question for the owner (crew 4 Oct: phone conflicts listed).
PHONE_FIELDS = ("phone_number",)

#: Never carried from a retired row onto the survivor (review S1): under a cash code the
#: retired rows are walk-in buyers, and one buyer's phone, email or address must not become
#: the shared account's (the chatbot also matches customers by phone). Listed in the plan CSV
#: (`not_carried`) and kept in full in the merge map instead.
PERSONAL_FIELDS = frozenset(
    {
        "phone_number",
        "mobile_number",
        "email",
        "billing_address",
        "tax_id",
        "registered_name",
        "trading_name",
        "registration_number",
        "salutation",
        "first_name",
        "last_name",
        "notes",
        "website",
    }
)

#: Never filled from a retired row: identity, bookkeeping, and the AR snapshot (a retired row's
#: ageing is stale, the next AutoCount AR pull writes the survivor's).
_NOT_FILLED = frozenset(
    {
        "id",
        "company_id",
        "customer_code",
        "customer_name",
        "created_at",
        "created_by",
        "updated_at",
        "customer_type",
        "is_active",
        "ar_outstanding",
        "ar_ageing_json",
        "ar_as_of",
    }
) | PERSONAL_FIELDS

#: The code-unique index this lane ends on (card R13).
UNIQUE_INDEX = "uq_customers_company_code_lower"


class DuplicatesRemain(RuntimeError):
    """The code-unique index cannot be created while a code still holds several rows."""


@dataclass
class Group:
    company_id: str
    code_key: str
    survivor_id: str
    survivor_name: str
    retired_ids: list[str]
    retired_names: list[str]
    fk_counts: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    #: personal field -> how many retired rows hold a value the survivor will not take
    not_carried: dict[str, int] = field(default_factory=dict)
    #: WhatsApp contacts linked to a retired row: the link is dropped, CS re-links
    contact_links_dropped: list[str] = field(default_factory=list)
    #: why this row is kept: `code_form_ref` | `autocount_ref` | `oldest` (card R6, Q1 (a))
    kept_reason: str = ""
    #: the kept row's AutoCount ref, if any; AutoCount's masters push keeps its name current
    survivor_ref: Optional[str] = None
    #: retired names that differ from the kept name once normalised (likely another business
    #: under the same code: listed for the owner, never acted on)
    name_mismatch: list[str] = field(default_factory=list)
    #: `row id=ref` for every row of the group holding an AutoCount ref, when more than one does.
    #: Informational: Q1 (a) already decides (the kept row's ref stays, the others are dropped).
    ref_conflicts: list[str] = field(default_factory=list)
    #: why `--apply` holds the group (`name_mismatch`), empty when it merges
    held: str = ""
    #: customer group name -> how many retired rows hold it
    retired_groups: dict[str, int] = field(default_factory=dict)


@dataclass
class ApplyResult:
    merged: list[Group] = field(default_factory=list)
    #: conflict groups with no `keep` row named
    skipped: list[Group] = field(default_factory=list)
    #: name-mismatch groups not released (owner decision (b))
    held: list[Group] = field(default_factory=list)


# ------------------------------------------------------------------------------ schema reads
def _customers_oid(db: Session) -> int:
    oid = db.execute(text("SELECT to_regclass('customers')::oid")).scalar()
    if oid is None:
        raise RuntimeError("customers table not found on the search_path")
    return int(oid)


def _referencing_fks(db: Session) -> list[tuple[str, str, str, str]]:
    """`(qualified table, column, primary key column, its type)` for every FK onto `customers.id`.

    Read at run time so a table added after this file was written is still covered. Each
    referencing table must have a single-column primary key: the merge map records repointed
    rows by it, and a revert needs it to find them again.
    """
    rows = db.execute(
        text(
            """
            SELECT quote_ident(rn.nspname) || '.' || quote_ident(rc.relname) AS ref_table,
                   ra.attname AS ref_column,
                   (SELECT string_agg(pa.attname, ',')
                      FROM pg_index pi
                      JOIN pg_attribute pa ON pa.attrelid = pi.indrelid AND pa.attnum = ANY(pi.indkey)
                     WHERE pi.indrelid = c.conrelid AND pi.indisprimary) AS pk,
                   (SELECT format_type(pa.atttypid, pa.atttypmod)
                      FROM pg_index pi
                      JOIN pg_attribute pa ON pa.attrelid = pi.indrelid AND pa.attnum = pi.indkey[0]
                     WHERE pi.indrelid = c.conrelid AND pi.indisprimary) AS pk_type
            FROM pg_constraint c
            JOIN pg_class rc ON rc.oid = c.conrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            JOIN pg_attribute ra ON ra.attrelid = c.conrelid AND ra.attnum = c.conkey[1]
            WHERE c.contype = 'f' AND c.confrelid = :oid
            ORDER BY 1, 2
            """
        ),
        {"oid": _customers_oid(db)},
    ).all()
    # A multi-column FK, or one onto a column other than `id`, is a shape this merge cannot
    # repoint; with CASCADE its rows would vanish with the retired customer unnoticed.
    odd = db.execute(
        text(
            """
            SELECT c.conname FROM pg_constraint c
            WHERE c.contype = 'f' AND c.confrelid = :oid
              AND (array_length(c.conkey, 1) <> 1 OR (
                   SELECT attname FROM pg_attribute
                   WHERE attrelid = c.confrelid AND attnum = c.confkey[1]) <> 'id')
            """
        ),
        {"oid": _customers_oid(db)},
    ).scalars().all()
    if odd:
        raise RuntimeError(f"foreign keys onto customers this merge cannot repoint: {odd}")
    out = []
    for table, column, pk, pk_type in rows:
        if not pk or "," in pk:
            raise RuntimeError(f"{table} has no single-column primary key; cannot record a repoint")
        out.append((table, column, pk, pk_type))
    if not out:
        raise RuntimeError("no foreign keys reference customers.id; the schema is not what this expects")
    return out


def _table_of(db: Session, regclass: str) -> Optional[str]:
    """The qualified name of a table that may not exist (e.g. a migration-only ledger)."""
    oid = db.execute(text("SELECT to_regclass(:r)::oid"), {"r": regclass}).scalar()
    if oid is None:
        return None
    return db.execute(
        text(
            "SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = :o"
        ),
        {"o": oid},
    ).scalar()


def _short(qualified: str) -> str:
    """`"zzs_x".sales_orders` / `public.sales_orders` -> `sales_orders` for reports."""
    return qualified.split(".")[-1].strip('"')


def _customer_columns(db: Session) -> list[str]:
    return list(
        db.execute(
            text(
                "SELECT attname FROM pg_attribute WHERE attrelid = to_regclass('customers') "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
            )
        ).scalars()
    )


# ------------------------------------------------------------------------------------- plan
def plan(
    db: Session,
    *,
    company_id: Optional[str] = None,
    codes: Optional[Iterable[str]] = None,
    lock: bool = False,
) -> list[Group]:
    """Every group, its survivor, what would move, and its conflicts. Reads only."""
    where = ["btrim(customer_code) <> ''"]
    params: dict[str, Any] = {}
    if company_id is not None:
        where.append("company_id = :cid")
        params["cid"] = str(company_id)
    if codes is not None:
        params["codes"] = [customer_identity.code_key(c) for c in codes]
        where.append("lower(btrim(customer_code)) = ANY(:codes)")
    keys = db.execute(
        text(
            f"""
            SELECT company_id::text, lower(btrim(customer_code))
            FROM customers WHERE {' AND '.join(where)}
            GROUP BY 1, 2 HAVING count(*) > 1 ORDER BY 1, 2
            """
        ),
        params,
    ).all()
    fks = _referencing_fks(db) if keys else []
    groups = []
    for cid, key in keys:
        rows = db.execute(
            text(
                f"""
                SELECT c.id::text, c.customer_name FROM customers c
                WHERE c.company_id = :cid AND lower(btrim(c.customer_code)) = :k
                ORDER BY {customer_identity.SURVIVOR_ORDER_SQL}
                {"FOR UPDATE" if lock else ""}
                """
            ),
            {"cid": cid, "k": key},
        ).all()
        if len(rows) < 2:
            continue
        group = Group(
            company_id=cid,
            code_key=key,
            survivor_id=rows[0][0],
            survivor_name=rows[0][1],
            retired_ids=[r[0] for r in rows[1:]],
            retired_names=[r[1] for r in rows[1:]],
        )
        for table, column, _pk, _t in fks:
            n = db.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} = ANY(CAST(:ids AS uuid[]))"),
                {"ids": group.retired_ids},
            ).scalar()
            if n:
                group.fk_counts[f"{_short(table)}.{column}"] = int(n)
        n = db.execute(
            text(
                "SELECT count(*) FROM integration_references "
                "WHERE entity_type = 'customers' AND entity_id = ANY(:ids)"
            ),
            {"ids": group.retired_ids},
        ).scalar()
        if n:
            group.fk_counts["integration_references.entity_id"] = int(n)
        group.conflicts = _conflicts(db, group)
        refs = dict(
            db.execute(
                text(
                    "SELECT entity_id, source_ref FROM integration_references "
                    "WHERE entity_type = 'customers' AND entity_id = ANY(:ids) ORDER BY entity_id"
                ),
                {"ids": [group.survivor_id] + group.retired_ids},
            ).all()
        )
        group.survivor_ref = refs.get(group.survivor_id)
        if group.survivor_ref is None:
            group.kept_reason = "oldest"
        elif customer_identity.code_key(group.survivor_ref.split(":", 1)[-1]) == key:
            group.kept_reason = "code_form_ref"
        else:
            group.kept_reason = "autocount_ref"
        if len(refs) > 1:
            group.ref_conflicts = [f"{rid}={ref}" for rid, ref in refs.items()]
        kept = normalise_name(group.survivor_name)
        group.name_mismatch = [n for n in group.retired_names if normalise_name(n) != kept]
        group.held = held_reason(group) or ""
        group.retired_groups = {
            name: int(n)
            for name, n in db.execute(
                text(
                    "SELECT coalesce(g.name, r.customer_group_id::text), count(*) FROM customers r "
                    "LEFT JOIN customer_groups g ON g.id = r.customer_group_id "
                    "WHERE r.id = ANY(CAST(:ids AS uuid[])) AND r.customer_group_id IS NOT NULL "
                    "GROUP BY 1 ORDER BY 1"
                ),
                {"ids": group.retired_ids},
            ).all()
        }
        group.not_carried = _not_carried(db, group)
        links = next((t for t, c, _p, _t in fks if _short(t) == "respond_contact_customers"), None)
        if links:
            group.contact_links_dropped = list(
                db.execute(
                    text(
                        f"SELECT contact_id FROM {links} "
                        "WHERE customer_id = ANY(CAST(:ids AS uuid[])) ORDER BY contact_id"
                    ),
                    {"ids": group.retired_ids},
                ).scalars()
            )
        groups.append(group)
    return groups


#: Words stripped from the END of a business name only (repeatedly), because they state the
#: legal form or the business type, not who the business is (crew 4 Oct dry run: ~204 of 263
#: held groups differed only by these or by account tags):
#: - legal form: SDN, BHD, BERHAD, SENDIRIAN (Sdn Bhd / Sendirian Berhad), PLT, LLP, M (the
#:   `(M)` "Malaysia" marker when written without brackets);
#: - business-type suffixes a sole proprietorship / partnership is registered with: ENTERPRISE,
#:   ENTERPRISES, ENT, TRADING, CO, COMPANY.
#: Trailing only: `ALPHA TRADING HARDWARE` keeps TRADING, which is part of that name there.
_NAME_SUFFIXES = frozenset(
    {
        "sdn", "bhd", "berhad", "sendirian", "plt", "llp", "m",
        "enterprise", "enterprises", "ent", "trading", "co", "company",
    }
)

#: Codes AutoCount keeps as ONE debtor for many buyers (cash sales, Shopee, walk-in): their
#: retired rows are buyers' names, never the debtor's, so a name mismatch there is the normal
#: case and never holds the group (owner brief 4 Oct: `301-*` codes merge, each document keeps
#: its buyer in `debtor_name`).
CASH_CODE_PREFIXES = ("301-",)


def normalise_name(name: Optional[str]) -> str:
    """The core of a business name, for `name_mismatch` only (owner 4 Oct, decision (b); crew
    4 Oct dry run: hold only when the CORE names differ). In order:

    1. case-insensitive;
    2. bracketed and parenthesised segments dropped: `[A/C I]`, `(CERAMIC & ELLECI)`, `(M)`,
       `(KTN)`, including an unclosed one at the end;
    3. everything from the first ` - ` (space, dash, space) dropped: the account tag AutoCount
       ledgers carry, `ALPHA SDN BHD - SMT - I` -> `ALPHA SDN BHD`. A dash inside a word stays
       (`F-PLY`);
    4. everything from the first `&` dropped: `F-PLY & DESIGN` -> `F-PLY`, so an `& ...` variant
       is the same company only when the core before it matches;
    5. punctuation becomes a space: `SDN. BHD.` = `SDN BHD`, `ALPHA-BRAVO` = `ALPHA BRAVO`;
    6. a leading `THE` and trailing `_NAME_SUFFIXES` words dropped, repeatedly.

    A name that is nothing but brackets / suffixes keeps its punctuation-free full form, so it
    never compares equal to everything. Examples: `Alpha Bravo Hardware Sdn. Bhd. - [A/C I]` ->
    `alpha bravo hardware`; `CHARLIEMART SDN BHD` -> `charliemart`.
    """
    import re

    def _words(text_: str) -> list[str]:
        return re.sub(r"[^a-z0-9]+", " ", text_).split()

    raw = (name or "").lower()
    text_ = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", raw)
    text_ = re.sub(r"[\[(][^\])]*$", " ", text_)
    text_ = re.split(r"\s+-\s+", text_, maxsplit=1)[0]
    text_ = text_.split("&", 1)[0]
    words = _words(text_)
    if words and words[0] == "the":
        words = words[1:]
    while words and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words) or " ".join(_words(raw))


def is_cash_code(code_key: str) -> bool:
    return code_key.startswith(CASH_CODE_PREFIXES)


#: On a cash code the customer group is the AutoCount debtor's, not a buyer's (crew 4 Oct): the
#: survivor keeps a group only when it is the AutoCount-linked row and already holds one. It is
#: never filled from a retired row and never a conflict; the retired rows' groups are listed in
#: the plan CSV (`retired_groups`) so they stay visible.
CASH_GROUP_FIELD = "customer_group_id"


def held_reason(group: "Group") -> Optional[str]:
    """Why `--apply` holds this group back, or None (owner 4 Oct, decision (b)): a retired name
    that is another business under the same code. Released only by `release=yes` in the CSV."""
    if group.name_mismatch and not is_cash_code(group.code_key):
        return "name_mismatch"
    return None


def _blank(expr: str) -> str:
    return f"NULLIF(btrim({expr}::text), '') IS NULL"


def _conflicts(db: Session, group: Group) -> list[dict]:
    out = []
    for column in PHONE_FIELDS:
        digits_s = f"regexp_replace(s.{column}::text, '[^0-9]', '', 'g')"
        digits_r = f"regexp_replace(r.{column}::text, '[^0-9]', '', 'g')"
        rows = db.execute(
            text(
                f"""
                SELECT r.id::text, s.{column}::text, r.{column}::text
                FROM customers s, customers r
                WHERE s.id = :s AND r.id = ANY(CAST(:ids AS uuid[]))
                  AND {digits_s} <> '' AND {digits_r} <> '' AND {digits_s} <> {digits_r}
                """
            ),
            {"s": group.survivor_id, "ids": group.retired_ids},
        ).all()
        out.extend(
            {"field": column, "survivor_value": sv, "retired_id": rid, "retired_value": rv}
            for rid, sv, rv in rows
        )
    for column in CONFLICT_FIELDS:
        if column == CASH_GROUP_FIELD and is_cash_code(group.code_key):
            continue
        rows = db.execute(
            text(
                f"""
                SELECT r.id::text, s.{column}::text, r.{column}::text
                FROM customers s, customers r
                WHERE s.id = :s AND r.id = ANY(CAST(:ids AS uuid[]))
                  AND NOT {_blank('s.' + column)} AND NOT {_blank('r.' + column)}
                  AND lower(btrim(s.{column}::text)) <> lower(btrim(r.{column}::text))
                """
            ),
            {"s": group.survivor_id, "ids": group.retired_ids},
        ).all()
        for rid, survivor_value, retired_value in rows:
            out.append(
                {
                    "field": column,
                    "survivor_value": survivor_value,
                    "retired_id": rid,
                    "retired_value": retired_value,
                }
            )
        # The survivor holds nothing, but the retired rows disagree among themselves: filling
        # from the oldest would be a silent pick.
        rows = db.execute(
            text(
                f"""
                SELECT r.id::text, r.{column}::text FROM customers s, customers r
                WHERE s.id = :s AND r.id = ANY(CAST(:ids AS uuid[]))
                  AND {_blank('s.' + column)} AND NOT {_blank('r.' + column)}
                """
            ),
            {"s": group.survivor_id, "ids": group.retired_ids},
        ).all()
        if len({(v or "").strip().lower() for _r, v in rows}) > 1:
            out.extend(
                {"field": column, "survivor_value": None, "retired_id": rid, "retired_value": value}
                for rid, value in rows
            )
    return out


def _not_carried(db: Session, group: Group) -> dict[str, int]:
    out = {}
    for column in sorted(PERSONAL_FIELDS):
        n = db.execute(
            text(
                f"SELECT count(*) FROM customers r WHERE r.id = ANY(CAST(:ids AS uuid[])) "
                f"AND NOT {_blank('r.' + column)}"
            ),
            {"ids": group.retired_ids},
        ).scalar()
        if n:
            out[column] = int(n)
    return out


# ------------------------------------------------------------------------------------ apply
def apply(
    db: Session,
    *,
    run_id: str,
    company_id: Optional[str] = None,
    codes: Optional[Iterable[str]] = None,
    resolutions: Optional[dict[tuple[str, str], str]] = None,
    releases: Optional[Iterable[tuple[str, str]]] = None,
    actor_user_id: Optional[str] = None,
) -> ApplyResult:
    """Merge every group in scope. Does not commit. `resolutions` maps
    `(company_id, code_key)` to the row id whose values win on a conflict; `releases` names
    the held groups the owner cleared to merge anyway."""
    resolutions = {(str(k[0]), k[1].strip().lower()): v for k, v in (resolutions or {}).items()}
    released = {(str(c), k.strip().lower()) for c, k in (releases or ())}
    result = ApplyResult()
    groups = plan(db, company_id=company_id, codes=codes, lock=True)
    if not groups:
        return result
    fks = _referencing_fks(db)
    columns = _customer_columns(db)
    for group in groups:
        if group.held and (group.company_id, group.code_key) not in released:
            result.held.append(group)
            continue
        keep = resolutions.get((group.company_id, group.code_key))
        if group.conflicts and keep is None:
            result.skipped.append(group)
            continue
        if keep is not None and keep != group.survivor_id and keep not in group.retired_ids:
            raise ValueError(f"keep row {keep} is not in group {group.code_key}")
        _merge_group(db, run_id, group, keep, fks, columns, actor_user_id)
        result.merged.append(group)
    return result


def _jsonb(value: Any) -> str:
    return json.dumps(value, default=str)


def _merge_group(
    db: Session,
    run_id: str,
    group: Group,
    keep: Optional[str],
    fks: list[tuple[str, str, str, str]],
    columns: list[str],
    actor_user_id: Optional[str],
) -> None:
    survivor = group.survivor_id
    customers_table = _table_of(db, "customers")
    retired_rows = {
        rid: db.execute(text("SELECT to_jsonb(c) FROM customers c WHERE id = :i"), {"i": rid}).scalar()
        for rid in group.retired_ids
    }
    updated: dict[str, list[dict]] = {rid: [] for rid in group.retired_ids}
    tables = {_short(table): table for table, _c, _p, _t in fks}

    # Fields (R7): the owner's keep row first, then blanks filled oldest retired first.
    # A conflict field is decided by the owner's keep row alone, never by the fill below.
    conflict_fields = {c["field"] for c in group.conflicts}
    if keep is not None and keep != survivor:
        for column in sorted(conflict_fields):
            updated[keep].append(_set_from(db, customers_table, survivor, keep, column))
    fillable = [c for c in columns if c not in _NOT_FILLED and c not in conflict_fields]
    if is_cash_code(group.code_key):
        fillable = [c for c in fillable if c != CASH_GROUP_FIELD]
        if group.kept_reason == "oldest" and group.retired_ids:
            old = db.execute(
                text(f"SELECT to_jsonb({CASH_GROUP_FIELD}) FROM customers WHERE id = :s"),
                {"s": survivor},
            ).scalar()
            if old is not None:
                db.execute(
                    text(f"UPDATE customers SET {CASH_GROUP_FIELD} = NULL WHERE id = :s"),
                    {"s": survivor},
                )
                updated[group.retired_ids[0]].append(
                    {"table": customers_table, "pk": "id", "ids": [survivor],
                     "column": CASH_GROUP_FIELD, "old": old}
                )
    for rid in sorted(group.retired_ids, key=lambda r: (retired_rows[r].get("created_at") or "", r)):
        for column in fillable:
            hit = db.execute(
                text(
                    f"SELECT 1 FROM customers s, customers r WHERE s.id = :s AND r.id = :r "
                    f"AND {_blank('s.' + column)} AND NOT {_blank('r.' + column)}"
                ),
                {"s": survivor, "r": rid},
            ).first()
            if hit:
                updated[rid].append(_set_from(db, customers_table, survivor, rid, column))

    for rid, name in zip(group.retired_ids, group.retired_names):
        repointed: dict[str, dict] = {}
        dropped: list[dict] = []

        # Documents keep the buyer name they showed (R8).
        for table in ("orders", "sales_orders"):
            qualified = _table_of(db, table)
            olds = db.execute(
                text(
                    f"SELECT id::text, debtor_name FROM {qualified} WHERE customer_id = :r "
                    "AND NULLIF(btrim(debtor_name), '') IS NULL"
                ),
                {"r": rid},
            ).all()
            if not olds:
                continue
            db.execute(
                text(f"UPDATE {qualified} SET debtor_name = :n WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"n": name, "ids": [i for i, _old in olds]},
            )
            by_old: dict[Optional[str], list[str]] = {}
            for doc_id, old in olds:
                by_old.setdefault(old, []).append(doc_id)
            for old, ids in by_old.items():
                updated[rid].append(
                    {"table": qualified, "pk": "id", "ids": ids, "column": "debtor_name", "old": old}
                )

        # Unique collisions (R9), before the repoint.
        # A contact link is an external contact's access, so it never moves to the survivor
        # (owner, 4 Oct, security review B1 (a)): every retired row's link is dropped, kept
        # in the map, and listed for CS to re-link deliberately (`contact_links_dropped`).
        links = tables.get("respond_contact_customers")
        if links:
            dropped += _drop_where(db, links, "t.customer_id = :r", {"r": rid})
        contacts = tables.get("customer_contacts")
        if contacts and db.execute(
            text(f"SELECT 1 FROM {contacts} WHERE customer_id = :s AND contact_role = 'main'"),
            {"s": survivor},
        ).first():
            ids = db.execute(
                text(
                    f"UPDATE {contacts} SET contact_role = 'stakeholder' "
                    "WHERE customer_id = :r AND contact_role = 'main' RETURNING id::text"
                ),
                {"r": rid},
            ).scalars().all()
            if ids:
                updated[rid].append(
                    {"table": contacts, "pk": "id", "ids": ids, "column": "contact_role", "old": "main"}
                )
        code_map = tables.get("customer_item_code_map")
        if code_map:
            dropped += _drop_where(
                db,
                code_map,
                f"t.customer_id = :r AND EXISTS (SELECT 1 FROM {code_map} s WHERE s.customer_id = :s "
                "AND s.customer_code = t.customer_code "
                "AND s.company_id IS NOT DISTINCT FROM t.company_id)",
                {"r": rid, "s": survivor},
            )
        aliases = tables.get("customer_aliases")
        if aliases:
            dropped += _drop_where(
                db,
                aliases,
                f"t.customer_id = :r AND lower(btrim(t.alias_name)) IN "
                f"(SELECT lower(btrim(alias_name)) FROM {aliases} WHERE customer_id = :s)",
                {"r": rid, "s": survivor},
            )
        refs = _table_of(db, "integration_references")
        dropped += _drop_where(
            db, refs, "t.entity_type = 'customers' AND t.entity_id = CAST(:r AS text)", {"r": rid}
        )
        # The downgrade ledger of migration 425 (no FK, absent on a create_all schema).
        ledger = _table_of(db, "scm.market_segment_backfill_425")
        if ledger:
            dropped += _drop_where(db, ledger, "t.customer_id = :r", {"r": rid})

        # Every FK onto customers.id (R8).
        for table, column, pk, pk_type in fks:
            ids = db.execute(
                text(f"UPDATE {table} SET {column} = :s WHERE {column} = :r RETURNING {pk}::text"),
                {"s": survivor, "r": rid},
            ).scalars().all()
            if ids:
                repointed[f"{table}.{column}"] = {"pk": pk, "pk_type": pk_type, "ids": ids}

        # Embeddings are derived data: the retired row's go, the survivor's stay.
        for table in ("embedding_chunks", "embedding_documents", "embedding_queue"):
            if _table_of(db, table):
                db.execute(
                    text(f"DELETE FROM {table} WHERE source_type = 'customer' AND source_id = :r"),
                    {"r": rid},
                )

        db.execute(
            text(
                """
                INSERT INTO customer_merge_map
                    (id, company_id, run_id, code_key, survivor_id, retired_id, retired_row,
                     repointed, dropped, updated)
                VALUES (gen_random_uuid(), :cid, :run, :k, :s, :r, CAST(:row AS jsonb),
                        CAST(:rep AS jsonb), CAST(:drop AS jsonb), CAST(:upd AS jsonb))
                """
            ),
            {
                "cid": group.company_id,
                "run": run_id,
                "k": group.code_key,
                "s": survivor,
                "r": rid,
                "row": _jsonb(retired_rows[rid]),
                "rep": _jsonb(repointed),
                "drop": _jsonb(dropped),
                "upd": _jsonb(updated[rid]),
            },
        )
        _audit(db, group, survivor, rid, retired_rows[rid], run_id, "CUSTOMER_MERGE", actor_user_id)

        remaining = [
            f"{table}.{column}"
            for table, column, _pk, _t in fks
            if db.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :r LIMIT 1"), {"r": rid}).first()
        ]
        if remaining:
            raise RuntimeError(f"rows still point at retired customer {rid}: {remaining}")
        db.execute(text("DELETE FROM customers WHERE id = :r"), {"r": rid})
        customer_identity.record_alias(db, survivor, name, source="merge", merge_run_id=run_id)
    db.expire_all()


def _set_from(db: Session, table: str, survivor: str, source: str, column: str) -> dict:
    old = db.execute(
        text(f"SELECT to_jsonb(s.{column}) FROM customers s WHERE s.id = :s"), {"s": survivor}
    ).scalar()
    db.execute(
        text(f"UPDATE customers s SET {column} = r.{column} FROM customers r WHERE s.id = :s AND r.id = :r"),
        {"s": survivor, "r": source},
    )
    return {"table": table, "pk": "id", "ids": [survivor], "column": column, "old": old}


def _drop_where(db: Session, qualified: str, where: str, params: dict) -> list[dict]:
    """Delete the rows of `qualified` (aliased `t` in `where`) and return them in full."""
    rows = db.execute(text(f"SELECT to_jsonb(t) FROM {qualified} t WHERE {where}"), params).scalars().all()
    if not rows:
        return []
    db.execute(text(f"DELETE FROM {qualified} t WHERE {where}"), params)
    return [{"table": qualified, "row": row} for row in rows]


def _audit(db, group, survivor, retired_id, retired_row, run_id, action, actor_user_id) -> None:
    db.execute(
        text(
            """
            INSERT INTO audit_logs
                (id, entity_type, entity_id, action, user_id, company_id, old_values, new_values,
                 description, actor_type, source, root_entity_type, root_entity_id, event, correlation_id)
            VALUES (gen_random_uuid(), 'customer', :s, :a, :u, :cid, CAST(:old AS jsonb),
                    CAST(:new AS jsonb), :d, :actor_type, 'script', 'customer', :s, :e, :run)
            """
        ),
        {
            "s": survivor,
            "a": action,
            "u": actor_user_id,
            "actor_type": "user" if actor_user_id else "system",
            "cid": group.company_id if group else None,
            "old": _jsonb(retired_row),
            "new": _jsonb({"survivor_id": survivor, "retired_id": retired_id, "run_id": run_id}),
            "d": f"Debtor code {retired_row.get('customer_code', '').strip()}: "
            f"{retired_row.get('customer_name')} merged into this customer"
            if action == "CUSTOMER_MERGE"
            else f"Merge of {retired_row.get('customer_name')} undone",
            "e": "customer.merge" if action == "CUSTOMER_MERGE" else "customer.merge_revert",
            "run": run_id,
        },
    )


# ----------------------------------------------------------------------------------- revert
def revert(db: Session, run_id: str, *, actor_user_id: Optional[str] = None) -> int:
    """Undo a run: every retired row back with its links and values. Last merged first.
    Idempotent: a map row already reverted is skipped. Returns how many rows came back."""
    maps = db.execute(
        text(
            "SELECT id::text, company_id::text, survivor_id::text, retired_id::text, retired_row, "
            "repointed, dropped, updated FROM customer_merge_map "
            "WHERE run_id = :run AND reverted_at IS NULL ORDER BY created_at DESC, id DESC"
        ),
        {"run": run_id},
    ).all()
    customers_table = _table_of(db, "customers")
    allowed = _revert_targets(db)
    if maps:
        # The retired rows share their survivor's code, so the code-unique index (if a
        # clean apply created it) has to go first. `create_unique_index` puts it back once
        # the codes are unique again.
        db.execute(text(f"DROP INDEX IF EXISTS {_index_qualified(db)}"))
    for map_id, cid, survivor, retired, row, repointed, dropped, updated in maps:
        # Every identifier below is read back from the map and pasted into SQL, so each one
        # is checked against the live schema first (security review S2): a tampered row
        # fails here instead of running.
        for key, entry in repointed.items():
            _check_target(db, allowed, *key.rsplit(".", 1), pk=entry["pk"], pk_type=entry["pk_type"])
        for entry in updated:
            _check_target(db, allowed, entry["table"], entry["column"], pk=entry["pk"], pk_type="uuid")
        for entry in dropped:
            _check_target(db, allowed, entry["table"])
        db.execute(
            text(
                f"INSERT INTO {customers_table} "
                f"SELECT (jsonb_populate_record(NULL::{customers_table}, CAST(:row AS jsonb))).*"
            ),
            {"row": _jsonb(row)},
        )
        for key, entry in repointed.items():
            table, column = key.rsplit(".", 1)
            db.execute(
                text(
                    f"UPDATE {table} SET {column} = :r "
                    f"WHERE {entry['pk']} = ANY(CAST(:ids AS {entry['pk_type']}[]))"
                ),
                {"r": retired, "ids": entry["ids"]},
            )
        for entry in reversed(updated):
            table, column = entry["table"], entry["column"]
            db.execute(
                text(
                    f"UPDATE {table} SET {column} = (jsonb_populate_record(NULL::{table}, "
                    f"jsonb_build_object(CAST(:col AS text), CAST(:old AS jsonb)))).{column} "
                    f"WHERE {entry['pk']} = ANY(CAST(:ids AS uuid[]))"
                ),
                {"col": column, "old": _jsonb(entry["old"]), "ids": entry["ids"]},
            )
        for entry in dropped:
            table = entry["table"]
            db.execute(
                text(
                    f"INSERT INTO {table} "
                    f"SELECT (jsonb_populate_record(NULL::{table}, CAST(:row AS jsonb))).*"
                ),
                {"row": _jsonb(entry["row"])},
            )
        db.execute(
            text("UPDATE customer_merge_map SET reverted_at = now() WHERE id = :i"), {"i": map_id}
        )
        _audit(
            db,
            Group(cid, "", survivor, "", [retired], []),
            survivor,
            retired,
            row,
            run_id,
            "CUSTOMER_MERGE_REVERT",
            actor_user_id,
        )
    if maps:
        db.execute(text("DELETE FROM customer_aliases WHERE merge_run_id = :run"), {"run": run_id})
    db.expire_all()
    return len(maps)


_PK_TYPES = frozenset({"uuid", "text", "integer", "bigint", "character varying"})


def _revert_targets(db: Session) -> set[str]:
    """The only tables a revert may write: what a merge writes."""
    out = {table for table, _c, _p, _t in _referencing_fks(db)}
    for name in ("customers", "orders", "sales_orders", "integration_references",
                 "scm.market_segment_backfill_425"):
        qualified = _table_of(db, name)
        if qualified:
            out.add(qualified)
    return out


def _check_target(
    db: Session,
    allowed: set[str],
    table: str,
    column: Optional[str] = None,
    *,
    pk: Optional[str] = None,
    pk_type: Optional[str] = None,
) -> None:
    if table not in allowed:
        raise ValueError(f"merge map names a table a merge never writes: {table!r}")
    if pk_type is not None and pk_type not in _PK_TYPES:
        raise ValueError(f"merge map names an unexpected key type: {pk_type!r}")
    for name in (column, pk):
        if name is None:
            continue
        found = db.execute(
            text(
                "SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(:t) AND attname = :c "
                "AND attnum > 0 AND NOT attisdropped"
            ),
            {"t": table, "c": name},
        ).first()
        if found is None or not name.replace("_", "").isalnum() or name != name.lower():
            raise ValueError(f"merge map names an unknown column {name!r} on {table}")


# ------------------------------------------------------------------------------ unique index
def _index_qualified(db: Session) -> str:
    """The index's name in the schema that holds `customers`."""
    schema = db.execute(
        text(
            "SELECT quote_ident(n.nspname) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = to_regclass('customers')"
        )
    ).scalar()
    return f"{schema}.{UNIQUE_INDEX}"


def duplicate_groups(db: Session) -> int:
    return int(
        db.execute(
            text(
                "SELECT count(*) FROM (SELECT 1 FROM customers WHERE btrim(customer_code) <> '' "
                "GROUP BY company_id, lower(btrim(customer_code)) HAVING count(*) > 1) g"
            )
        ).scalar()
    )


def create_unique_index(db: Session) -> None:
    """The code-unique index (card R13), only once no code holds two rows. Blank codes are
    outside it: the schema already refuses a blank code on every create path."""
    remaining = duplicate_groups(db)
    if remaining:
        raise DuplicatesRemain(f"{remaining} (company, debtor code) groups still hold several rows")
    db.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_INDEX} "
            "ON customers (company_id, lower(btrim(customer_code))) WHERE btrim(customer_code) <> ''"
        )
    )


# ------------------------------------------------------------------------------------- CSV
CSV_FIELDS = (
    "company_id",
    "code_key",
    "survivor_id",
    "survivor_name",
    "held",
    "kept_reason",
    "survivor_ref",
    "retired_count",
    "retired_ids",
    "retired_names",
    "fk_counts",
    "conflicts",
    "ref_conflicts",
    "name_mismatch",
    "retired_groups",
    "not_carried",
    "contact_links_dropped",
    "keep",
    "release",
)


def write_plan_csv(groups: list[Group], path: Path | str) -> None:
    """One row per group. Holds real customer data: write it outside the repo
    (`scripts/out/` is gitignored) and never commit it."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for g in groups:
            writer.writerow(
                {
                    "company_id": g.company_id,
                    "code_key": g.code_key,
                    "survivor_id": g.survivor_id,
                    "survivor_name": g.survivor_name,
                    "retired_count": len(g.retired_ids),
                    "retired_ids": ";".join(g.retired_ids),
                    "retired_names": ";".join(g.retired_names),
                    "fk_counts": ";".join(f"{k}={v}" for k, v in sorted(g.fk_counts.items())),
                    "conflicts": ";".join(
                        f"{c['field']}: {c['survivor_value']} | {c['retired_id']}={c['retired_value']}"
                        for c in g.conflicts
                    ),
                    "held": g.held,
                    "kept_reason": g.kept_reason,
                    "survivor_ref": g.survivor_ref or "",
                    "ref_conflicts": ";".join(g.ref_conflicts),
                    "name_mismatch": ";".join(g.name_mismatch),
                    "retired_groups": ";".join(f"{k}={v}" for k, v in g.retired_groups.items()),
                    "not_carried": ";".join(f"{k}={v}" for k, v in sorted(g.not_carried.items())),
                    "contact_links_dropped": ";".join(g.contact_links_dropped),
                    "keep": "",
                    "release": "",
                }
            )


HELD_CSV_FIELDS = ("company_id", "code", "survivor_name", "other_names", "reason", "release")


def write_held_csv(groups: list[Group], path: Path | str) -> int:
    """The held groups only, for the owner / CS to review: one row per group, `other_names` the
    names whose core differs from the kept name, and an empty `release` column to write `yes`
    in to merge it anyway (read back by `read_releases`). Real data: keep it out of the repo."""
    held = [g for g in groups if g.held]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HELD_CSV_FIELDS)
        writer.writeheader()
        for g in held:
            writer.writerow(
                {
                    "company_id": g.company_id,
                    "code": g.code_key,
                    "survivor_name": g.survivor_name,
                    "other_names": "; ".join(g.name_mismatch),
                    "reason": g.held,
                    "release": "",
                }
            )
    return len(held)


def read_releases(path: Path | str) -> set[tuple[str, str]]:
    """`(company_id, code_key)` of every held group the owner marked `release=yes`, from the
    plan CSV or the held-review CSV (`write_held_csv`)."""
    with open(path, newline="", encoding="utf-8") as fh:
        return {
            (row["company_id"], (row.get("code_key") or row.get("code") or "").strip().lower())
            for row in csv.DictReader(fh)
            if (row.get("release") or "").strip().lower() in ("yes", "y", "true", "1")
        }


def read_resolutions(path: Path | str) -> dict[tuple[str, str], str]:
    """`(company_id, code_key) -> keep row id` from a plan CSV the owner filled in."""
    out: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            keep = (row.get("keep") or "").strip()
            if keep:
                out[(row["company_id"], row["code_key"])] = keep
    return out
