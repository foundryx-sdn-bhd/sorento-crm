#!/usr/bin/env python3
"""One-off: make named shipping orders follow AutoCount, without deploying #1411.

WHAT IT DOES (per --spo, inside one company)
--------------------------------------------
When a shipping order (`spo_number`) carries AutoCount lines (`source_ref` set),
AutoCount is the truth for it (owner ruling, 1 Oct 2026). The Excel-era rows
(no `source_ref`, `source_system` NULL or `scm_upload`, no `po_line_id`) are:

1. SUPERSEDED where AutoCount lists the same product on the SPO: first at the
   same warehouse / location; otherwise, at ANY warehouse (owner ruling after
   #1411, e.g. Excel at BRW, AutoCount at BRW-NTC) or for an Excel row with no
   warehouse, onto that product's remaining AutoCount lines, as long as those
   lines can hold the receipt the Excel rows already carry (else "received
   locked"). AutoCount's quantities win. Their receipt is carried onto the
   AutoCount lines (each line up to its own quantity, the rest on the last,
   never lowering what a line already states), their GRN picks move onto those
   lines (split by capacity, same warehouse first, when the lines sit at
   different locations; an AutoCount GRN line is never split), their order-link
   claims and order-inquiry links move to the first line, and the Excel rows are
   deleted.
2. REMOVED as ORPHANS when AutoCount does not list their product at all - but
   only when received is 0 and no GRN pick, order-link claim or order-inquiry
   link (any company) points at them. Otherwise they are left untouched and
   reported ORPHAN-BLOCKED with the counts. No product substitution is guessed.
3. KEPT, and reported, in every other case: the AutoCount lines could not
   hold the receipt the Excel rows already carry ("received locked").

Only the newest AutoCount document version (DocKey) on the number is used; rows
of an older DocKey are reported and never touched.

SAFETY
------
- DRY-RUN unless --apply. The dry run prints exactly what --apply would write.
- One transaction per SPO. Before anything is deleted the script proves, in
  that transaction, that the carry equals what the Excel rows held, that the
  AutoCount lines now state at least that, and that no pick (any company)
  still points at a row about to be deleted. Any failure rolls the SPO back
  and the run moves to the next one (exit code 3).
- Preflight: refuses to run unless every table and column it reads or writes
  exists on the target DB (`stated_received` from migration 488, `retired_at`,
  `scm.order_link_claim`, `projects.order_inquiry_links`, ...).
- Plain SQL through the app's own DATABASE_URL; the plan and the writes import
  nothing from the app, so they run against whatever code the container
  holds. Raw SQL bypasses the ORM audit listeners, so KEEP THE LOG (tee it):
  it names every row deleted with its quantities, every link moved old -> new
  and every quantity carried.
- After each SPO commits, --apply refreshes the stored received figures of
  every packing list (inbound shipment) it touched, through the app's own
  `InboundShipmentService.refresh_shipment_line_statuses` (what opening the
  packing list page runs), and prints each line it changed. The dry run lists
  those packing lists. A refresh that fails is reported (REFRESH FAILED, exit 5)
  and the run goes on: that SPO is already committed, open that packing list
  by hand or run `refresh_container_received.py` for it.

--all scans the company for every SPO holding BOTH an Excel-era row and an
AutoCount row, in `spo_number` order. --limit N takes the first N; the run
then prints `next batch: --start-after <last>` for the next call. It ends with
the SPOs that (would) change, those with rows left for review (ORPHAN-BLOCKED
or kept) and those aborted by a guard. `grep '=>'` on the log gives one
summary line per SPO.

RUN IT IN THE PROD BACKEND CONTAINER (docker exec)
--------------------------------------------------
The prod host has NO git checkout (/opt/sorento-crm2 holds only the compose
file and what CI scp's there; PRINCIPLES.md "Ops quick-reference"), so the
file travels from a LOCAL checkout of this branch by scp. Production is
blue/green (scripts/blue_green_deploy.sh): the live backend service is
`backend_<colour>`, the colour is in /opt/sorento-crm2/.active_color. Every
backend container already has DATABASE_URL in its environment and the app at
/app. Run the server steps inside `tmux` (or `screen`): a dropped SSH session
kills the process and rolls back the SPO in flight.

    # 0. ON YOUR MACHINE, from a checkout of this branch: copy the script to the
    #    host (same SSH user/host CI deploys with; fill in your own).
    scp sorento_crm_backend/scripts/oneoff/dedupe_spo_standalone.py \
        <ssh-user>@<prod-host>:/tmp/dedupe_spo_standalone.py
    ssh <ssh-user>@<prod-host>

    # ON THE HOST
    tmux new -s spo-repair
    set -o pipefail            # so `| tee` keeps the script's exit code
    cd /opt/sorento-crm2
    COLOUR=$(cat .active_color)
    docker compose ps --services   # confirm the real service names first:
                                   # backend_${COLOUR} and the worker services
                                   # (worker / worker_fast in the deploy script)
    docker compose ps backend_${COLOUR}   # must show it running

    # 1. copy the file into the live backend container
    docker compose cp /tmp/dedupe_spo_standalone.py backend_${COLOUR}:/tmp/dedupe_spo_standalone.py

    # 2. DRY RUN (default) - read the plan, keep the log. The header names the
    #    database it connected to (current_database, server address, alembic head).
    docker compose exec -T -w /app backend_${COLOUR} \
        python /tmp/dedupe_spo_standalone.py --company SRT --spo SPO-2026/08-0074 \
        2>&1 | tee spo-0074-dryrun.log

    # 3. APPLY, only after the dry run has been read and approved. Stop the
    #    worker services first (use the names `ps --services` printed) so no
    #    AutoCount sync or GRN ingest writes the same rows meanwhile, and start
    #    them again whatever the outcome.
    docker compose stop worker worker_fast
    docker compose exec -T -w /app backend_${COLOUR} \
        python /tmp/dedupe_spo_standalone.py --company SRT --spo SPO-2026/08-0074 --apply \
        2>&1 | tee spo-0074-apply.log; echo "exit=$?"
    docker compose start worker worker_fast
    docker compose ps --services --filter status=running   # workers back up

    # 4. the apply refreshed each packing list it touched (grep REFRESH FAILED;
    #    open any such one in the CRM)
    # 5. run the dry run again. It should report nothing left to do. If it
    #    proposes ANY new carry or delete, do not apply it without review: a
    #    group the first run kept can only match lines the first run already
    #    carried a receipt onto.
    # 6. copy the logs off the host (scp back to your machine) and keep them:
    #    they are the audit record.

The log is the audit record (raw SQL writes no audit rows): before any write
it prints every row of the SPO and every pick, claim and order-inquiry link on
them as full JSON, and after an apply it prints the same set again.

Check the exit code (`echo $?`) or grep the log for `ABORTED|FAILED|PREFLIGHT`.
If the backend container has no DATABASE_URL in its environment (it reads it
from a file), the script stops with that message and writes nothing.

`--company` is `companies.code`; Sorento's is `SRT` (seeded by
alembic/versions/302_multi_company_scaffold.py:158). `--spo` is repeatable.
DATABASE_URL comes from the environment only (no flag, so no password lands in
`ps` or shell history).

Exit codes: 0 done, 1 bad arguments / unknown company / no DATABASE_URL,
2 preflight failed, 3 at least one SPO was refused by a guard (rolled back,
the run continued), 4 an unexpected database error (that SPO rolled back, the
run STOPPED; SPOs before it stay committed), 5 every SPO committed but at
least one packing list refresh failed (a run that also aborted an SPO exits
3; grep the log for REFRESH FAILED either way).
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, MetaData, Table, create_engine, func, select, text, update
from sqlalchemy.dialects.postgresql import UUID

XLSX_SOURCE_SYSTEM = "scm_upload"
RECEIVED_STATUSES = ("fully_received", "received")

#: (schema, table) -> columns this script reads or writes.
REQUIRED = {
    ("public", "companies"): ("id", "code"),
    ("public", "spo_allocations"): (
        "id", "company_id", "spo_number", "spo_line_number", "product_id",
        "warehouse_id", "location_code", "allocated_quantity", "quantity_received",
        "quantity_rejected", "stated_received", "retired_at", "receipt_status",
        "line_status", "source_system", "source_ref", "source_doc_ref", "po_line_id",
        "inbound_shipment_id", "storage_zone_id", "uom_id", "allocation_notes",
        "created_at",
    ),
    ("public", "picking_lines"): (
        "id", "picking_header_id", "spo_allocation_id", "product_id", "company_id",
        "quantity_expected", "quantity_picked", "qty_accepted", "source_warehouse_id",
        "destination_warehouse_id", "dtl_key", "uom_id", "picked_condition",
        "condition_remarks", "batch_number_picked", "expiry_date", "unit_cost",
        "spo_number_raw", "po_line_id", "item_code", "location_code", "created_at",
    ),
    ("public", "picking_headers"): ("id", "picking_status"),
    ("public", "inbound_shipments"): ("id", "shipment_number", "shipping_container_number"),
    ("scm", "order_link_claim"): ("id", "spo_allocation_id", "company_id"),
    ("projects", "order_inquiry_links"): ("id", "spo_allocation_id", "company_id"),
    # ON DELETE CASCADE from spo_allocations: counted and logged per deleted row.
    ("projects", "order_inquiry_suggested_links"): ("id", "spo_allocation_id"),
}


# The two link tables live outside `public`; declared as Core tables (not text)
# so a schema_translate_map, if the connection carries one, applies to them too.
_META = MetaData()
_LINK_TABLES = {
    "claim": Table(
        "order_link_claim", _META,
        Column("id", UUID), Column("spo_allocation_id", UUID), Column("company_id", UUID),
        schema="scm",
    ),
    "order-inquiry link": Table(
        "order_inquiry_links", _META,
        Column("id", UUID), Column("spo_allocation_id", UUID), Column("company_id", UUID),
        schema="projects",
    ),
}
_SUGGESTED = Table(
    "order_inquiry_suggested_links", _META,
    Column("id", UUID), Column("spo_allocation_id", UUID),
    schema="projects",
)


class GuardFailed(RuntimeError):
    """The SPO would lose a receipt; it is rolled back untouched."""


# --------------------------------------------------------------------- preflight
def preflight(db) -> list[str]:
    """Every missing table/column, as readable lines (empty = OK)."""
    missing = []
    for (schema, table), columns in REQUIRED.items():
        present = {
            row[0]
            for row in db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": schema, "t": table},
            )
        }
        if not present:
            missing.append(f"table {schema}.{table} is missing")
            continue
        for column in columns:
            if column not in present:
                missing.append(f"column {schema}.{table}.{column} is missing")
    return missing


# ------------------------------------------------------------------- pure rules
def _key(product_id, warehouse_id, location_code):
    product = str(product_id) if product_id else None
    if warehouse_id:
        return (product, f"wh:{warehouse_id}")
    location = (location_code or "").strip().upper()
    return (product, f"loc:{location}" if location else None)


def _match_keys(row) -> list:
    keys = [_key(row["product_id"], row["warehouse_id"], row["location_code"])]
    if row["warehouse_id"]:
        loc = _key(row["product_id"], None, row["location_code"])
        if loc[1] is not None and loc not in keys:
            keys.append(loc)
    return keys


def distribute(total: int, quantities: list[int]) -> list[int]:
    """Each up to its own quantity, any remainder on the LAST (never lost)."""
    remaining = max(int(total or 0), 0)
    shares = []
    for position, quantity in enumerate(quantities):
        take = remaining if position == len(quantities) - 1 else min(remaining, max(int(quantity or 0), 0))
        take = max(take, 0)
        remaining -= take
        shares.append(take)
    return shares


def _i(value) -> int:
    return int(value or 0)


@dataclass
class Group:
    rows: list
    lines: list
    split: bool  # lines at different locations: picks split by capacity


@dataclass
class Plan:
    groups: list = field(default_factory=list)
    kept: list = field(default_factory=list)  # (row, reason)
    orphans: list = field(default_factory=list)


def plan_spo(excel_rows: list, lines: list) -> Plan:
    """The follow-AutoCount plan for one SPO. `lines` = newest DocKey's rows in
    `spo_line_number` order; `excel_rows` = its Excel-era rows."""
    plan = Plan()
    rows = sorted(excel_rows, key=lambda r: (r["spo_line_number"] if r["spo_line_number"] is not None else 10**9, str(r["id"])))
    lines_by_key: dict = {}
    for index, line in enumerate(lines):
        for key in _match_keys(line):
            lines_by_key.setdefault(key, []).append(index)
    rows_by_key: dict = {}
    for row in rows:
        rows_by_key.setdefault(_key(row["product_id"], row["warehouse_id"], row["location_code"]), []).append(row)

    claimed: set = set()
    kept_groups = []
    keyed_locked = []
    for key in sorted(rows_by_key, key=lambda k: (0 if (k[1] or "").startswith("wh:") else 1, str(k[0]), str(k[1]))):
        group_rows = rows_by_key[key]
        indexes = [i for i in lines_by_key.get(key, ()) if i not in claimed]
        if not indexes:
            kept_groups.append((key, group_rows))
            continue
        received = sum(_i(r["quantity_received"]) for r in group_rows)
        allocated = sum(_i(r["allocated_quantity"]) for r in group_rows)
        claimed.update(indexes)
        if sum(_i(lines[i]["allocated_quantity"]) for i in indexes) < min(received, allocated):
            keyed_locked.append((key, group_rows))
            continue
        plan.groups.append(Group(group_rows, [lines[i] for i in indexes], split=False))

    # Follow AutoCount (owner rulings, D37): a product with ANY Excel row the
    # keyed pass could not settle - no line at its warehouse, no warehouse, a
    # same-warehouse line too small for its receipt, or a sibling group that
    # took every line - is planned as ONE pool: all its Excel rows against all
    # its AutoCount lines, superseded when the lines can hold the receipt the
    # rows carry, else received locked. A product AutoCount does not list at
    # all is an orphan. A product the keyed pass settled keeps that result.
    lines_by_product: dict = {}
    for index, line in enumerate(lines):
        if line["product_id"]:
            lines_by_product.setdefault(str(line["product_id"]), []).append(index)
    unsettled = {key[0] for key, _ in (*kept_groups, *keyed_locked) if key[0]}
    pooled = {product for product in unsettled if product in lines_by_product}
    plan.groups = [g for g in plan.groups if str(g.rows[0]["product_id"]) not in pooled]
    for key, group_rows in kept_groups:
        if key[0] not in pooled:
            plan.orphans.extend(group_rows)
    for key, group_rows in keyed_locked:
        if key[0] not in pooled:
            plan.kept.extend((r, "received locked (AutoCount lines too small for the receipt)") for r in group_rows)
    for product in sorted(pooled):
        group_rows = [r for r in rows if str(r["product_id"]) == product]
        indexes = lines_by_product[product]
        rows_total = sum(_i(r["allocated_quantity"]) for r in group_rows)
        rows_received = sum(_i(r["quantity_received"]) for r in group_rows)
        lines_total = sum(_i(lines[i]["allocated_quantity"]) for i in indexes)
        if lines_total < min(rows_received, rows_total):
            plan.kept.extend(
                (r, f"received locked (AutoCount lines {lines_total} cannot hold the {rows_received} received)")
                for r in group_rows
            )
        else:
            plan.groups.append(Group(group_rows, [lines[i] for i in indexes], split=True))
    return plan


# ------------------------------------------------------------------------ SQL
_ROW_COLUMNS = ", ".join(REQUIRED[("public", "spo_allocations")])


def _rows(db, company_id: str, spo_number: str, lock: bool = False) -> list:
    """The SPO's rows. `lock` (apply) takes FOR UPDATE: a concurrent AutoCount
    sync or GRN cannot overwrite them, and no new pick, claim or link can attach
    to them (a new FK reference needs FOR KEY SHARE on the parent) until this
    SPO's transaction ends."""
    return [
        dict(r._mapping)
        for r in db.execute(
            text(
                f"SELECT {_ROW_COLUMNS} FROM spo_allocations "
                "WHERE company_id = :c AND spo_number = :n ORDER BY spo_line_number, id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": company_id, "n": spo_number},
        )
    ]


def _newest_dockey(refs: list) -> tuple[list, list]:
    by_doc: dict = {}
    for row in refs:
        by_doc.setdefault(str(row["source_doc_ref"] or ""), []).append(row)
    if len(by_doc) <= 1:
        return sorted(refs, key=lambda r: (r["spo_line_number"] or 0, str(r["id"]))), []

    def rank(item):
        doc, rows = item
        created = max((r["created_at"] for r in rows if r["created_at"] is not None), default=datetime.min)
        return (created, max((r["spo_line_number"] or 0) for r in rows), doc)

    newest_doc, newest = max(by_doc.items(), key=rank)
    older = [r for doc, rows in by_doc.items() if doc != newest_doc for r in rows]
    return sorted(newest, key=lambda r: (r["spo_line_number"] or 0, str(r["id"]))), older


def _link_counts(db, allocation_id) -> tuple[int, int, int]:
    """(picks, claims, order-inquiry links) on one allocation, ANY company."""
    picks = _i(db.execute(
        text("SELECT count(*) FROM picking_lines WHERE spo_allocation_id = :a"), {"a": str(allocation_id)}
    ).scalar())
    claims, links = (
        _i(db.execute(
            select(func.count()).select_from(table).where(table.c.spo_allocation_id == str(allocation_id))
        ).scalar())
        for table in _LINK_TABLES.values()
    )
    return picks, claims, links


def _foreign_links(db, ids: list, company_id: str) -> tuple[int, int, int]:
    """(picks, claims, order-inquiry links) on `ids` stamped with ANOTHER company.
    The script may only move this company's (or company-less) links, so any of
    these means a superseded row cannot be deleted without losing a pairing."""
    picks = _i(db.execute(
        text(
            "SELECT count(*) FROM picking_lines WHERE spo_allocation_id = ANY(CAST(:ids AS uuid[])) "
            "AND company_id IS NOT NULL AND company_id <> CAST(:c AS uuid)"
        ),
        {"ids": ids, "c": company_id},
    ).scalar())
    claims, links = (
        _i(db.execute(
            select(func.count()).select_from(table).where(
                table.c.spo_allocation_id.in_(ids),
                table.c.company_id.isnot(None),
                table.c.company_id != company_id,
            )
        ).scalar())
        for table in _LINK_TABLES.values()
    )
    return picks, claims, links


def _any_links(db, ids: list) -> tuple[int, int, int]:
    """(picks, claims, order-inquiry links) on `ids`, ANY company."""
    picks = _i(db.execute(
        text("SELECT count(*) FROM picking_lines WHERE spo_allocation_id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": ids},
    ).scalar())
    claims, links = (
        _i(db.execute(
            select(func.count()).select_from(table).where(table.c.spo_allocation_id.in_(ids))
        ).scalar())
        for table in _LINK_TABLES.values()
    )
    return picks, claims, links


def _suggested_count(db, allocation_id) -> int:
    return _i(db.execute(
        select(func.count()).select_from(_SUGGESTED).where(_SUGGESTED.c.spo_allocation_id == str(allocation_id))
    ).scalar())


def _snapshot(db, ids: list, label: str, log) -> None:
    """Full JSON of the rows and of everything pointing at them."""
    log(f"[{label}] spo_allocations:")
    for (row,) in db.execute(
        text("SELECT row_to_json(a)::text FROM spo_allocations a WHERE id = ANY(CAST(:ids AS uuid[])) "
             "ORDER BY spo_line_number, id"),
        {"ids": ids},
    ):
        log(f"[{label}]   {row}")
    log(f"[{label}] picking_lines:")
    for (row,) in db.execute(
        text("SELECT row_to_json(p)::text FROM picking_lines p WHERE spo_allocation_id = ANY(CAST(:ids AS uuid[])) "
             "ORDER BY created_at, id"),
        {"ids": ids},
    ):
        log(f"[{label}]   {row}")
    for name, table in _LINK_TABLES.items():
        for row in db.execute(select(table).where(table.c.spo_allocation_id.in_(ids))):
            log(f"[{label}] {name}: id={row.id} spo_allocation_id={row.spo_allocation_id} company_id={row.company_id}")


def _move_picks_whole(db, from_ids, to_id, company_id, write, log) -> int:
    picks = db.execute(
        text(
            "SELECT id, spo_allocation_id, quantity_picked FROM picking_lines "
            "WHERE spo_allocation_id = ANY(CAST(:ids AS uuid[])) AND (company_id = :c OR company_id IS NULL) "
            "ORDER BY created_at, id"
        ),
        {"ids": [str(i) for i in from_ids], "c": company_id},
    ).all()
    for pick in picks:
        log(f"move pick {pick.id} ({_i(pick.quantity_picked)}) {pick.spo_allocation_id} -> {to_id}")
        if write:
            db.execute(text("UPDATE picking_lines SET spo_allocation_id = :t WHERE id = :p"), {"t": str(to_id), "p": pick.id})
    return len(picks)


def _move_picks_by_capacity(db, from_ids, targets: list, company_id, write, log) -> int:
    """Same warehouse first, then any, each line up to allocated less what
    already picks against it (rejected GRNs hold none), overflow on the last.
    A pick carrying `dtl_key` (an AutoCount GRN line) is never split."""
    target_ids = [str(t["id"]) for t in targets]
    already = {
        str(r[0]): _i(r[1])
        for r in db.execute(
            text(
                "SELECT pl.spo_allocation_id, coalesce(sum(pl.quantity_picked), 0) FROM picking_lines pl "
                "JOIN picking_headers ph ON ph.id = pl.picking_header_id "
                "WHERE pl.spo_allocation_id = ANY(CAST(:ids AS uuid[])) AND ph.picking_status <> 'rejected' "
                "AND (pl.company_id = :c OR pl.company_id IS NULL) GROUP BY pl.spo_allocation_id"
            ),
            {"ids": target_ids, "c": company_id},
        )
    }
    pool = [
        {"id": str(t["id"]), "wh": str(t["warehouse_id"]) if t["warehouse_id"] else None,
         "avail": max(0, _i(t["allocated_quantity"]) - already.get(str(t["id"]), 0))}
        for t in targets
    ]
    picks = db.execute(
        text(
            "SELECT * FROM picking_lines WHERE spo_allocation_id = ANY(CAST(:ids AS uuid[])) "
            "AND (company_id = :c OR company_id IS NULL) ORDER BY created_at, id"
        ),
        {"ids": [str(i) for i in from_ids], "c": company_id},
    ).mappings().all()
    for pick in picks:
        qty = _i(pick["quantity_picked"])
        wh = pick["source_warehouse_id"] or pick["destination_warehouse_id"]
        wh = str(wh) if wh else None
        draws, remaining = [], qty
        for same in (True, False):
            for entry in pool:
                if remaining <= 0:
                    break
                if (entry["wh"] == wh) is not same or entry["avail"] <= 0:
                    continue
                take = min(remaining, entry["avail"])
                draws.append([entry["id"], take, entry])
                entry["avail"] -= take
                remaining -= take
        if not draws:
            # Nothing to place (a zero-quantity pick): it follows the group's
            # first line, as the in-app repair does.
            draws.append([target_ids[0], qty, None])
        elif remaining > 0:
            # What no line has room for lands on the LAST line, never dropped.
            draws.append([target_ids[-1], remaining, None])
        chunks: list = []
        for allocation_id, take, entry in draws:
            if chunks and chunks[-1][0] == allocation_id:
                chunks[-1][1] += take
            else:
                chunks.append([allocation_id, take])
        if pick["dtl_key"] is not None and len(chunks) > 1:
            for allocation_id, take, entry in draws[1:]:
                if entry is not None:
                    entry["avail"] += take
            chunks = [[chunks[0][0], qty]]
        if len(chunks) == 1:
            log(f"move pick {pick['id']} ({qty}) {pick['spo_allocation_id']} -> {chunks[0][0]}")
            if write:
                db.execute(text("UPDATE picking_lines SET spo_allocation_id = :t WHERE id = :p"),
                           {"t": chunks[0][0], "p": pick["id"]})
            continue
        states_expected = _i(pick["quantity_expected"]) > 0
        expected = distribute(_i(pick["quantity_expected"]), [c[1] for c in chunks])
        accepted_left = _i(pick["qty_accepted"]) if pick["qty_accepted"] is not None else None
        for position, (allocation_id, chunk_qty) in enumerate(chunks):
            exp = expected[position] if states_expected else 0
            acc = None
            if accepted_left is not None:
                acc = min(accepted_left, chunk_qty)
                accepted_left -= acc
            if position == 0:
                log(f"split pick {pick['id']} ({qty}): keep {chunk_qty} on {allocation_id} "
                    f"(was {pick['spo_allocation_id']}; expected {_i(pick['quantity_expected'])} -> "
                    f"{exp if states_expected else _i(pick['quantity_expected'])}, accepted "
                    f"{pick['qty_accepted']} -> {acc})")
                if write:
                    db.execute(
                        text(
                            "UPDATE picking_lines SET spo_allocation_id = :t, quantity_picked = :q, "
                            "quantity_expected = CASE WHEN :se THEN CAST(:e AS integer) ELSE quantity_expected END, "
                            "qty_accepted = CASE WHEN qty_accepted IS NULL THEN NULL ELSE CAST(:a AS integer) END "
                            "WHERE id = :p"
                        ),
                        {"t": allocation_id, "q": chunk_qty, "se": states_expected, "e": exp, "a": acc, "p": pick["id"]},
                    )
                continue
            new_id = str(uuid.uuid4())
            log(f"split pick {pick['id']}: new pick {new_id} ({chunk_qty}, expected {exp}, accepted {acc}) "
                f"on {allocation_id}")
            if write:
                db.execute(
                    text(
                        "INSERT INTO picking_lines (id, picking_header_id, product_id, source_warehouse_id, "
                        "destination_warehouse_id, uom_id, picked_condition, condition_remarks, batch_number_picked, "
                        "expiry_date, unit_cost, spo_number_raw, po_line_id, item_code, location_code, company_id, "
                        "spo_allocation_id, quantity_expected, quantity_picked, qty_accepted) "
                        "SELECT :nid, picking_header_id, product_id, source_warehouse_id, destination_warehouse_id, "
                        "uom_id, coalesce(picked_condition, 'good'), condition_remarks, batch_number_picked, expiry_date, "
                        "unit_cost, spo_number_raw, po_line_id, item_code, location_code, company_id, :t, :e, :q, "
                        "CAST(:a AS integer) "
                        "FROM picking_lines WHERE id = :p"
                    ),
                    {"nid": new_id, "t": allocation_id, "e": exp, "q": chunk_qty, "a": acc, "p": pick["id"]},
                )
    return len(picks)


def _move_links(db, from_ids, to_id, company_id, write, log) -> int:
    moved = 0
    ids = [str(i) for i in from_ids]
    for label, table in _LINK_TABLES.items():
        rows = db.execute(
            select(table.c.id, table.c.spo_allocation_id).where(
                table.c.spo_allocation_id.in_(ids),
                (table.c.company_id == company_id) | table.c.company_id.is_(None),
            )
        ).all()
        for row in rows:
            log(f"move {label} {row.id} {row.spo_allocation_id} -> {to_id}")
            if write:
                db.execute(update(table).where(table.c.id == row.id).values(spo_allocation_id=str(to_id)))
        moved += len(rows)
    return moved


def _append_note(existing: Optional[str], note: Optional[str]) -> Optional[str]:
    present = [p.strip() for p in (existing or "").split(";") if p.strip()]
    for fragment in (p.strip() for p in (note or "").split(";") if p.strip()):
        if fragment not in present:
            present.append(fragment)
    return "; ".join(present) or None


# -------------------------------------------------------------------- one SPO
def process_spo(db, company_id: str, spo_number: str, *, write: bool, log) -> dict[str, Any]:
    """Plan (and with `write`, apply) one SPO. Raises GuardFailed before any
    delete when a receipt would be lost; the caller rolls back."""
    stats = {"deleted": 0, "orphans_removed": 0, "orphans_blocked": 0, "kept": 0, "retired": 0,
             "links_moved": 0, "carried": 0, "shipments": set()}
    rows = _rows(db, company_id, spo_number, lock=write)
    refs = [r for r in rows if r["source_ref"]]
    excel = [
        r for r in rows
        if not r["source_ref"] and (r["source_system"] or "") in ("", XLSX_SOURCE_SYSTEM) and not r["po_line_id"]
    ]
    if not refs:
        log("no AutoCount lines on this SPO: nothing to follow, untouched")
        return stats
    lines, older = _newest_dockey(refs)
    if not excel:
        log("no Excel-era rows: already follows AutoCount")
        return stats
    # D28d, as the in-app repair does (`_retire_older_dockeys`): a CLOSED row of
    # an older AutoCount document version is retired, its receipt frozen into
    # `stated_received`; an open one is left for its own push to settle.
    for row in older:
        if row["retired_at"] is not None:
            continue
        if row["line_status"] != "closed":
            log(f"older DocKey row {row['id']} line {row['spo_line_number']} is open: not touched")
            continue
        frozen = max(_i(row["stated_received"]), _i(row["quantity_received"]))
        log(f"retire older DocKey row {row['id']} line {row['spo_line_number']} ({row['source_doc_ref']}), "
            f"stated_received {row['stated_received']} -> {frozen if frozen > 0 else row['stated_received']}")
        stats["retired"] += 1
        if write:
            db.execute(
                text(
                    "UPDATE spo_allocations SET retired_at = now(), "
                    "stated_received = CASE WHEN :f > 0 THEN :f ELSE stated_received END WHERE id = :id"
                ),
                {"f": frozen, "id": row["id"]},
            )

    plan = plan_spo(excel, lines)
    if not plan.groups and not plan.orphans:
        for row, reason in plan.kept:
            log(f"keep {row['id']} line {row['spo_line_number']} (allocated {_i(row['allocated_quantity'])}, "
                f"received {_i(row['quantity_received'])}): {reason}")
            stats["kept"] += 1
        return stats
    all_ids = [str(r["id"]) for r in excel] + [str(r["id"]) for r in lines]
    _snapshot(db, all_ids, "before", log)
    superseded_ids = [str(r["id"]) for group in plan.groups for r in group.rows]
    if superseded_ids:
        foreign = _foreign_links(db, superseded_ids, company_id)
        if any(foreign):
            raise GuardFailed(
                f"another company's links point at rows to delete (picks {foreign[0]}, claims {foreign[1]}, "
                f"order-inquiry links {foreign[2]}) - fix those by hand first"
            )
    for group in plan.groups:
        removed_received = sum(_i(r["quantity_received"]) for r in group.rows)
        shares = distribute(removed_received, [_i(l["allocated_quantity"]) for l in group.lines])
        shipment = next((r["inbound_shipment_id"] for r in group.rows if r["inbound_shipment_id"]), None)
        zone = next((r["storage_zone_id"] for r in group.rows if r["storage_zone_id"]), None)
        uom = next((r["uom_id"] for r in group.rows if r["uom_id"]), None)
        rejected = sum(_i(r["quantity_rejected"]) for r in group.rows)
        notes = "; ".join((r["allocation_notes"] or "").strip() for r in group.rows if (r["allocation_notes"] or "").strip()) or None
        log(f"group {'product fallback' if group.split else 'same destination'}: "
            f"{len(group.rows)} Excel row(s) -> {len(group.lines)} AutoCount line(s)")
        after_total = 0
        for position, (line, share) in enumerate(zip(group.lines, shares)):
            allocated = _i(line["allocated_quantity"])
            received = max(_i(line["quantity_received"]), share)
            after_total += received
            closed = received >= allocated
            stats["carried"] += share
            log(f"carry {share} -> {line['id']} line {line['spo_line_number']} "
                f"(allocated {allocated}, received {_i(line['quantity_received'])} -> {received})")
            if line["inbound_shipment_id"] or shipment:
                stats["shipments"].add(str(line["inbound_shipment_id"] or shipment))
            if write:
                db.execute(
                    text(
                        "UPDATE spo_allocations SET quantity_received = :r, "
                        "stated_received = CASE WHEN :r > coalesce(stated_received, 0) THEN :r ELSE stated_received END, "
                        "line_status = :ls, receipt_status = :rs, "
                        "inbound_shipment_id = coalesce(inbound_shipment_id, :sh), "
                        "storage_zone_id = coalesce(storage_zone_id, :z), uom_id = coalesce(uom_id, :u) "
                        "WHERE id = :id"
                    ),
                    {"r": received, "ls": "closed" if closed else "open",
                     "rs": "fully_received" if closed else "pending",
                     "sh": shipment, "z": zone, "u": uom, "id": line["id"]},
                )
                if position == 0 and rejected:
                    db.execute(
                        text(
                            "UPDATE spo_allocations SET "
                            "quantity_rejected = GREATEST(coalesce(quantity_rejected, 0), :rej) WHERE id = :id"
                        ),
                        {"rej": rejected, "id": line["id"]},
                    )
                if position == 0 and notes:
                    db.execute(
                        text("UPDATE spo_allocations SET allocation_notes = :n WHERE id = :id"),
                        {"n": _append_note(line["allocation_notes"], notes), "id": line["id"]},
                    )
        removed_ids = [r["id"] for r in group.rows]
        first = group.lines[0]["id"]
        if group.split:
            stats["links_moved"] += _move_picks_by_capacity(db, removed_ids, group.lines, company_id, write, log)
        else:
            stats["links_moved"] += _move_picks_whole(db, removed_ids, first, company_id, write, log)
        stats["links_moved"] += _move_links(db, removed_ids, first, company_id, write, log)

        # The guard, in this transaction, before anything is deleted.
        if sum(shares) != removed_received:
            raise GuardFailed(f"carry {sum(shares)} != {removed_received} held")
        if after_total < removed_received:
            raise GuardFailed(f"AutoCount lines would state {after_total} < {removed_received} held")
        if write:
            stranded = _any_links(db, [str(i) for i in removed_ids])
            if any(stranded):
                raise GuardFailed(
                    f"still pointing at Excel rows after the move (picks {stranded[0]}, claims {stranded[1]}, "
                    f"order-inquiry links {stranded[2]}) - fix by hand first"
                )
        for row in group.rows:
            log(f"delete {row['id']} line {row['spo_line_number']} "
                f"(allocated {_i(row['allocated_quantity'])}, received {_i(row['quantity_received'])}; "
                f"suggested links cascaded {_suggested_count(db, row['id'])})")
            if row["inbound_shipment_id"]:
                stats["shipments"].add(str(row["inbound_shipment_id"]))
            if write:
                db.execute(text("DELETE FROM spo_allocations WHERE id = :id"), {"id": row["id"]})
            stats["deleted"] += 1

    for row in plan.orphans:
        received = _i(row["quantity_received"])
        picks, claims, links = _link_counts(db, row["id"])
        other = (_i(row["stated_received"]), _i(row["quantity_rejected"]), (row["allocation_notes"] or "").strip())
        facts = (f"{row['id']} line {row['spo_line_number']} (allocated {_i(row['allocated_quantity'])}, "
                 f"received {received}; picks {picks}, claims {claims}, order-inquiry links {links}; "
                 f"stated {other[0]}, rejected {other[1]}, notes {'yes' if other[2] else 'no'})")
        # Anything a person or a system recorded about the row blocks it: a
        # receipt, a stated receipt, a rejection, a note, or a link.
        if received == 0 and not (picks or claims or links) and not any(other):
            log(f"orphan delete {facts}: no AutoCount line for its product; "
                f"suggested links cascaded {_suggested_count(db, row['id'])}")
            if row["inbound_shipment_id"]:
                stats["shipments"].add(str(row["inbound_shipment_id"]))
            if write:
                db.execute(text("DELETE FROM spo_allocations WHERE id = :id"), {"id": row["id"]})
            stats["orphans_removed"] += 1
        else:
            log(f"ORPHAN-BLOCKED {facts}: left untouched")
            stats["orphans_blocked"] += 1

    for row, reason in plan.kept:
        log(f"keep {row['id']} line {row['spo_line_number']} (allocated {_i(row['allocated_quantity'])}, "
            f"received {_i(row['quantity_received'])}): {reason}")
        stats["kept"] += 1
    if write:
        _snapshot(db, all_ids, "after", log)
    return stats


# ------------------------------------------------------------------- --all scan
def candidate_spo_numbers(db, company_id: str) -> list[str]:
    """Every `spo_number` of the company holding BOTH an Excel-era row and an
    AutoCount row, by the same tests `process_spo` applies, in number order."""
    return [
        row[0]
        for row in db.execute(
            text(
                "SELECT spo_number FROM spo_allocations "
                "WHERE company_id = :c AND spo_number IS NOT NULL "
                "GROUP BY spo_number "
                "HAVING bool_or(coalesce(source_ref, '') <> '') "
                "AND bool_or(coalesce(source_ref, '') = '' AND coalesce(source_system, '') IN ('', :x) "
                "AND po_line_id IS NULL) "
                "ORDER BY spo_number"
            ),
            {"c": company_id, "x": XLSX_SOURCE_SYSTEM},
        )
    ]


# ------------------------------------------------------------- container refresh
def app_refresher(session=None):
    """The refresh the packing list page runs on open
    (`InboundShipmentService.refresh_shipment_line_statuses`, which commits),
    on `session` (default: a new app `SessionLocal`), scoped to the company.
    The ONLY place this script imports the app: the plan and the writes above
    stay import-free. Returns `refresh(company_id, shipment_id)`; a failure
    rolls `session` back and re-raises."""
    from app.database import SessionLocal
    from app.models.base import company_scope
    from app.services.company_scope import register_company_scope_listeners
    from app.services.procurement_service import InboundShipmentService

    # A plain `python` process never ran the app's startup: without this the
    # scope filter is not installed and `company_scope` narrows nothing.
    register_company_scope_listeners()
    session = session if session is not None else SessionLocal()
    service = InboundShipmentService(session)

    def refresh(company_id: str, shipment_id: str) -> None:
        try:
            with company_scope(session, frozenset({company_id})):
                service.refresh_shipment_line_statuses(shipment_id)
        except Exception:
            session.rollback()
            raise

    return refresh


def _stored_lines(db, shipment_id: str) -> dict:
    return {
        str(r[0]): (_i(r[1]), r[2])
        for r in db.execute(
            text("SELECT id, quantity_received, line_status FROM inbound_shipment_lines "
                 "WHERE shipment_id = :s ORDER BY created_at, id"),
            {"s": shipment_id},
        )
    }


def _shipment_label(db, shipment_id: str) -> str:
    number = db.execute(
        text("SELECT shipment_number, shipping_container_number FROM inbound_shipments WHERE id = :i"),
        {"i": shipment_id},
    ).one_or_none()
    return f"{number[0]} / container {number[1]} ({shipment_id})" if number else f"({shipment_id})"


def _refresh_shipments(db, company_id: str, shipment_ids, refresh, out) -> bool:
    """After a COMMITTED apply: refresh each shipment, printing every stored line
    figure it changed. False when any refresh failed (the SPO stays committed;
    that packing list then needs opening by hand)."""
    ok = True
    for shipment_id in sorted(shipment_ids):
        label = _shipment_label(db, shipment_id)
        if refresh is None:
            out(f"  packing list to re-open (refreshes its stored status): {label}")
            continue
        owner = db.execute(text("SELECT company_id FROM inbound_shipments WHERE id = :i"), {"i": shipment_id}).scalar()
        if owner is not None and str(owner) != company_id:
            out(f"  NOT REFRESHED: packing list {label} belongs to another company ({owner}) - open it by hand")
            db.rollback()
            continue
        before = _stored_lines(db, shipment_id)
        db.rollback()  # end this read: the refresh commits on its own session
        try:
            refresh(company_id, shipment_id)
        except Exception as exc:  # noqa: BLE001 - the SPO is committed; report and go on
            out(f"  REFRESH FAILED for packing list {label}: {type(exc).__name__}: {exc} - open it by hand")
            ok = False
            continue
        after = _stored_lines(db, shipment_id)
        db.rollback()
        changed = [(lid, before.get(lid, (0, None)), now) for lid, now in after.items() if before.get(lid) != now]
        out(f"  refreshed packing list {label}: {len(changed)} line(s) changed")
        for line_id, (old_r, old_s), (new_r, new_s) in changed:
            out(f"    line {line_id}: received {old_r} -> {new_r}, status {old_s} -> {new_s}")
    return ok


# ------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", required=True, help="companies.code (Sorento: SRT)")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--spo", action="append", metavar="SPO_NUMBER",
                       help="spo_number to repair (repeatable)")
    which.add_argument("--all", action="store_true",
                       help="every SPO of the company holding both Excel-era and AutoCount rows")
    parser.add_argument("--limit", type=int, metavar="N", help="with --all: at most N SPOs this run")
    parser.add_argument("--start-after", metavar="SPO_NUMBER",
                        help="with --all: only SPO numbers sorting after this one (the next batch)")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    return parser


def validate_args(args) -> str:
    """An argument error the parser cannot express, or '' when fine."""
    if (args.limit is not None or args.start_after) and not args.all:
        return "--limit and --start-after need --all"
    if args.limit is not None and args.limit < 1:
        return "--limit must be at least 1"
    return ""


def run(db, company_code: str, spo_numbers: Optional[list[str]], *, apply: bool, out=print,
        scan_all: bool = False, limit: Optional[int] = None, start_after: Optional[str] = None,
        refresh=None) -> int:
    """`db` is a SQLAlchemy Session or Connection. Returns the exit code.

    `scan_all` replaces `spo_numbers` with `candidate_spo_numbers` (after
    `start_after`, at most `limit`). `refresh(company_id, shipment_id)` runs
    after each committed SPO for every shipment it touched (`app_refresher`);
    None only prints those packing lists."""
    missing = preflight(db)
    if missing:
        out("PREFLIGHT FAILED - this database lacks what the script needs:")
        for line in missing:
            out(f"  {line}")
        return 2
    company_id = db.execute(text("SELECT id FROM companies WHERE code = :c"), {"c": company_code}).scalar()
    if company_id is None:
        out(f"no company with code {company_code!r}")
        return 1
    company_id = str(company_id)
    where = db.execute(text("SELECT current_database(), inet_server_addr()")).one()
    try:
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - informational only
        db.rollback()
        head = "unknown"
    out(f"=== database {where[0]} at {where[1] or 'local socket'}, alembic head {head} ===")
    out(f"=== {company_code} ({company_id}) {'APPLY' if apply else 'DRY-RUN (no writes)'} ===")
    more_after = None
    if scan_all:
        numbers = candidate_spo_numbers(db, company_id)
        if start_after:
            numbers = [n for n in numbers if n > start_after]
        if limit is not None and len(numbers) > limit:
            numbers = numbers[:limit]
            more_after = numbers[-1]
        out(f"=== --all: candidates {len(numbers)}"
            f"{f' after {start_after}' if start_after else ''}{f' (limit {limit})' if limit else ''} ===")
    else:
        numbers = [n.strip() for n in (spo_numbers or []) if n and n.strip()]
    exit_code = 0
    refresh_failed = False
    totals = {"deleted": 0, "orphans_removed": 0, "orphans_blocked": 0, "kept": 0, "links_moved": 0}
    changed, review, aborted = [], [], []
    for spo_number in numbers:
        out(f"\n--- {spo_number} ---")
        db.rollback()  # every SPO starts in its own transaction
        try:
            stats = process_spo(db, company_id, spo_number, write=apply,
                                log=lambda line: out(f"  {line}"))
        except GuardFailed as exc:
            db.rollback()
            out(f"  ABORTED, rolled back: {exc}")
            aborted.append(spo_number)
            exit_code = 3
            continue
        except Exception as exc:  # noqa: BLE001 - stop the run, report, never half-write
            db.rollback()
            out(f"  FAILED, rolled back, run STOPPED: {type(exc).__name__}: {exc}")
            return 4
        if apply:
            db.commit()
            if not _refresh_shipments(db, company_id, stats["shipments"], refresh, out):
                refresh_failed = True
        else:
            db.rollback()
            for shipment_id in sorted(stats["shipments"]):
                out(f"  packing list to refresh after apply: {_shipment_label(db, shipment_id)}")
            db.rollback()
        for key in totals:
            totals[key] += stats[key]
        if stats["deleted"] or stats["orphans_removed"] or stats["retired"]:
            changed.append(spo_number)
        if stats["orphans_blocked"] or stats["kept"]:
            review.append(spo_number)
        out(f"  => {spo_number}: Excel rows superseded {stats['deleted']}, orphans removed {stats['orphans_removed']}, "
            f"older-DocKey rows retired {stats['retired']}, "
            f"orphans BLOCKED {stats['orphans_blocked']}, kept {stats['kept']}, "
            f"links moved {stats['links_moved']}, received carried {stats['carried']}, "
            f"PLs touched {len(stats['shipments'])}")
    out(f"\n=== {'APPLIED' if apply else 'DRY-RUN'}: superseded {totals['deleted']}, "
        f"orphans removed {totals['orphans_removed']}, orphans BLOCKED {totals['orphans_blocked']}, "
        f"kept {totals['kept']}, links moved {totals['links_moved']} ===")
    if scan_all:
        verb = "changed" if apply else "would change"
        out(f"{verb} ({len(changed)}): {', '.join(changed)}")
        out(f"rows left for review (ORPHAN-BLOCKED / kept) ({len(review)}): {', '.join(review)}")
        out(f"aborted ({len(aborted)}): {', '.join(aborted)}")
        if more_after:
            out(f"next batch: --start-after {more_after}")
    if refresh_failed and exit_code == 0:
        exit_code = 5
    return exit_code


def main() -> int:
    args = build_parser().parse_args()
    problem = validate_args(args)
    if problem:
        print(problem)
        return 1
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set (the backend container sets it)")
        return 1
    # Pinned to UTC like the app's own engine (app/database.py): `now()` defaults
    # on naive timestamp columns (a split pick's created_at, which FIFO reads)
    # must not take the database server's local zone.
    engine = create_engine(url, connect_args={"options": "-c timezone=utc"})
    refresh = None
    if args.apply:
        # The post-apply refresh runs the app's own service, so the app must be
        # importable: the working directory (`-w /app`) and the backend root
        # this file sits under (`scripts/oneoff/`) both go on the path.
        for root in (os.getcwd(), os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
            if root not in sys.path:
                sys.path.insert(0, root)
        try:
            refresh = app_refresher()
        except Exception as exc:  # noqa: BLE001 - before any write: stop cleanly
            print(f"cannot load the app for the post-apply refresh ({type(exc).__name__}: {exc}); "
                  "run from the backend container with -w /app. Nothing was written.")
            return 1
    with engine.connect() as conn:
        return run(conn, args.company, args.spo, apply=args.apply, scan_all=args.all,
                   limit=args.limit, start_after=args.start_after, refresh=refresh)


if __name__ == "__main__":
    sys.exit(main())
