#!/usr/bin/env python3
"""Crew hand-test setup for PR #1679 (CANCEL-STOP-AT-SO). DEV COPIES ONLY.

Puts one real project SO line into the state the hand test needs: cancelled by an AutoCount
Cancel SO while a live order inquiry row still rests on it, with the Transfer to Cancel SO the
sync proposes for CS saved on its board line. It runs the cancel ingest the way the
`/external/ingest/cancel_sales_orders` route does (`AutocountCancelIngestService.ingest`, the
commit, then `run_cancel_hooks`), in this process, on the one database the guard below checked:
nothing goes over HTTP and no API key is needed.

Pick: an open line of an open or closed `project` SO that the Fulfilment Planning board would
read (`FulfilmentBoardService._crm_cancelled_rows`: SO status open / closed, demand_class
project, a project mirror line), with a live order inquiry row on that mirror line, not
cancelled by CS or by a cancel document, nothing cancelled or delivered against it in full, a
line key (`source_ref` ending in `:<DtlKey>`) unique on its SO, and no Cancel SO line naming
that SO yet. The XS (`XS-HT1679`, book `handtest`) cancels the whole ordered qty by that key,
so it matches exactly that line. Like every Cancel SO batch, it also retries the company's
cancel lines still waiting for their order, so those may link too.

Setup on a database where the hand-test XS is already live re-proposes the Transfer to Cancel
SO on its line (the state a line cancelled before the proposal existed needs) and changes
nothing else. `--undo` pushes the same XS again as voided (`Cancelled: T`): the cancel stops
counting, the line returns to the status its quantities give, `qty_cancelled` to 0 (plan R6)
and the proposal is withdrawn. The voided XS stays stored, like any voided AutoCount document;
setup after an undo makes it live again on the same line (if that line still qualifies).

Refuses unless the database this process is connected to is on this machine (the server
address Postgres itself reports, so a URL query string or PG* variable cannot point it
elsewhere) AND is a dev database by name: one of `KNOWN_DEV_DATABASES` (the crew shared dev DB,
`sorento_cagent_stack`), the exact name passed with `--allow-db`, or a name with a dev / ci /
test / copy segment. The same checks run for setup and for `--undo`.

Run from sorento_crm_backend/ with the copy's env (its DATABASE_URL):
    venv/bin/python scripts/handtest_1679_setup.py
    venv/bin/python scripts/handtest_1679_setup.py --undo
Optional: --so <SO number> to pick that SO's first eligible line. `--ingest-url` is accepted
and ignored (earlier versions posted to the copy's backend).
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEV_NAME = re.compile(r"(^|_)(dev|ci|test|copy)(_|$)", re.IGNORECASE)
#: Dev databases whose names say nothing about it, accepted by exact name only.
KNOWN_DEV_DATABASES = frozenset({"sorento_cagent_stack"})  # crew shared dev DB (crew.toml)
#: libpq reads these too, and they would move the connection away from the URL's host.
_PG_ENV_OVERRIDES = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGDATABASE")
BOOK = "handtest"
DOC_KEY = 167900001
DOC_NO = "XS-HT1679"
DTL_KEY = 167900002
ENTITY = "cancel_sales_orders"


class Refused(RuntimeError):
    pass


def _allowed_name(name: str, allow_db: Optional[str]) -> bool:
    return (name in KNOWN_DEV_DATABASES or (allow_db is not None and name == allow_db)
            or bool(DEV_NAME.search(name)))


def guard(database_url: str, allow_db: Optional[str] = None) -> str:
    """The URL as configured: a host on this machine, no query string that redirects libpq,
    a dev database name. Returns the name."""
    parts = urlsplit(database_url)
    host = (parts.hostname or "").lower()
    name = parts.path.lstrip("/")
    if host not in LOCAL_HOSTS:
        raise Refused(f"database host {host or '(none)'!r} is not this machine")
    if parts.query:
        raise Refused("the database URL carries a query string, which can redirect the "
                      "connection; remove it")
    moved = [var for var in _PG_ENV_OVERRIDES if os.getenv(var)]
    if moved:
        raise Refused(f"{', '.join(moved)} is set, which can redirect the connection; unset it")
    if not _allowed_name(name, allow_db):
        raise Refused(f"database {name!r} is not a dev / ci / test / copy database "
                      f"(pass --allow-db {name} if it is one)")
    return name


def guard_connected(name: Optional[str], server_addr: Optional[str],
                    allow_db: Optional[str] = None) -> str:
    """What Postgres says this session is connected to: a unix socket (no address) or a
    loopback address, and a dev database name."""
    if server_addr is not None:
        try:
            loopback = ipaddress.ip_address(str(server_addr).split("/")[0]).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise Refused(f"the database server answers from {server_addr}, not this machine")
    if not name or not _allowed_name(name, allow_db):
        raise Refused(f"connected to database {name!r}, which is not a dev / ci / test / copy "
                      f"database (pass --allow-db {name} if it is one)")
    return name


PICK_SQL = """
SELECT so.so_number, so.company_id, core.id AS core_line_id,
       core.source_ref, core.qty_ordered, pl.line_no AS project_line_no, p.product_code,
       (SELECT count(*) FROM projects.order_inquiry_rows r
         WHERE r.so_line_id = pl.id AND r.state <> 'cancelled') AS live_oi_rows
FROM public.sales_order_lines core
JOIN public.sales_orders so ON so.id = core.sales_order_id
JOIN projects.sales_order_lines pl ON pl.core_sales_order_line_id = core.id
JOIN public.products p ON p.id = core.product_id
WHERE so.status IN ('open', 'closed')
  AND so.demand_class = 'project'
  AND so.doc_type = 'sales_order'
  AND (CAST(:so AS text) IS NULL OR so.so_number = CAST(:so AS text))
  AND core.line_status = 'open'
  AND core.cancelled_at IS NULL
  AND NOT core.cancelled_by_cancel_doc
  AND core.qty_ordered > 0
  AND COALESCE(core.qty_cancelled, 0) = 0
  AND COALESCE(core.qty_delivered, 0) < core.qty_ordered
  AND p.product_code IS NOT NULL
  AND core.source_ref ~ ':[0-9]+$'
  AND EXISTS (SELECT 1 FROM projects.order_inquiry_rows r
              WHERE r.so_line_id = pl.id AND r.state <> 'cancelled')
  AND NOT EXISTS (SELECT 1 FROM public.autocount_cancel_lines l
                  WHERE l.company_id = so.company_id AND l.from_doc_no = so.so_number)
  AND (SELECT count(*) FROM public.sales_order_lines o
        WHERE o.sales_order_id = so.id
          AND (o.source_ref = substring(core.source_ref FROM ':([0-9]+)$')
               OR o.source_ref LIKE '%:' || substring(core.source_ref FROM ':([0-9]+)$'))) = 1
ORDER BY so.order_date DESC NULLS LAST, so.so_number, pl.line_no
LIMIT 1
"""

STATE_SQL = """
SELECT so.so_number, pl.line_no, p.product_code, core.line_status, core.qty_ordered,
       core.qty_cancelled, core.cancelled_by_cancel_doc,
       (SELECT count(*) FROM projects.order_inquiry_rows r
         WHERE r.so_line_id = pl.id AND r.state <> 'cancelled') AS live_oi_rows,
       (SELECT d.decision ->> 'reason' FROM projects.so_supply_decision_drafts d
         WHERE d.core_line_id = core.id AND d.decision ->> 'verdict' = 'cancelled')
         AS pending_transfer
FROM public.sales_order_lines core
JOIN public.sales_orders so ON so.id = core.sales_order_id
JOIN projects.sales_order_lines pl ON pl.core_sales_order_line_id = core.id
JOIN public.products p ON p.id = core.product_id
WHERE core.id = :line
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _held(db):
    """The hand-test XS on this database, with its company and matched SO line."""
    return db.execute(text(
        "SELECT d.source_record, d.is_void, d.company_id, l.sales_order_line_id "
        "FROM public.autocount_cancel_docs d "
        "LEFT JOIN public.autocount_cancel_lines l ON l.cancel_doc_id = d.id "
        "WHERE d.kind = 'cancel_so' AND d.source_book = :b AND d.doc_key = :k"
    ), {"b": BOOK, "k": DOC_KEY}).mappings().first()


def _actor(db) -> Optional[str]:
    """The FoundryX ESB integration's principal, who the real sync acts as (seeded by
    `integration_seed`), so the proposal reads as saved by it."""
    return db.execute(text(
        "SELECT id FROM users WHERE email = 'foundryx-esb@integrations.local'"
    )).scalar()


def _ingest(db, company_id: str, record: dict) -> None:
    """The route's own sequence: ingest, commit, then the post-commit cancel hooks."""
    from app.models.base import company_scope
    from app.services.autocount_cancel_ingest_service import (
        AutocountCancelIngestService,
        run_cancel_hooks,
    )

    with company_scope(db, frozenset({str(company_id)})):
        service = AutocountCancelIngestService(db, None, company_id=str(company_id), book=BOOK)
        result = service.ingest(ENTITY, [record])
        outcome = result.records[0]
        if str(getattr(outcome.outcome, "value", outcome.outcome)) in ("failed", "retryable"):
            db.rollback()
            raise SystemExit(f"ingest outcome {outcome.outcome}: {outcome.errors}")
        db.commit()
        run_cancel_hooks(db, service, actor=_actor(db))


def _line_of(db) -> str:
    db.expire_all()
    held = _held(db)
    if held is None or held["sales_order_line_id"] is None:
        raise SystemExit(f"{DOC_NO} matched no sales order line (the line no longer qualifies)")
    return str(held["sales_order_line_id"])


def _print_state(db, core_line_id: str, label: str) -> dict:
    db.expire_all()
    row = db.execute(text(STATE_SQL), {"line": core_line_id}).mappings().one()
    print(f"{label}: SO {row['so_number']} line {row['line_no']} item {row['product_code']}: "
          f"status {row['line_status']}, ordered {row['qty_ordered']}, cancelled "
          f"{row['qty_cancelled']}, by cancel doc {row['cancelled_by_cancel_doc']}, "
          f"live OI rows {row['live_oi_rows']}, pending transfer "
          f"{row['pending_transfer'] or 'none'}")
    return dict(row)


def _done(row: dict, again: str = "") -> None:
    print(f"\nSetup done{again}: SO {row['so_number']} line {row['line_no']} "
          f"(item {row['product_code']}) is cancelled by AutoCount {DOC_NO}, has "
          f"{row['live_oi_rows']} live OI row(s) and a pending Transfer to Cancel SO "
          f"({row['pending_transfer'] or 'none'}). Undo: --undo")


def setup(db, so_number: Optional[str]) -> None:
    held = _held(db)
    if held is not None:
        if not held["is_void"]:
            # Already live: re-propose the transfer (a line cancelled before the proposal
            # existed has none), nothing else.
            from app.models.base import company_scope
            from app.services import project_line_draft_service

            line_id = _line_of(db)
            with company_scope(db, frozenset({str(held["company_id"])})):
                project_line_draft_service.propose_autocount_cancel(
                    db, line_id, DOC_NO, actor_user_id=_actor(db))
                db.commit()
            _done(_print_state(db, line_id, "after "), " (already live, proposal re-made)")
            return
        # Undone earlier: the same XS goes live again on the same line.
        record = dict(held["source_record"])
        record["Cancelled"] = "F"
        record["LastModified"] = _now()
        _ingest(db, str(held["company_id"]), record)
        _done(_print_state(db, _line_of(db), "after "), " (again)")
        return
    pick = db.execute(text(PICK_SQL), {"so": so_number}).mappings().first()
    if pick is None:
        raise SystemExit("no eligible line (see the docstring for what qualifies)")
    dtl = int(re.search(r":([0-9]+)$", pick["source_ref"]).group(1))
    record = {
        "DocKey": DOC_KEY, "DocNo": DOC_NO, "DocDate": date.today().isoformat(),
        "Cancelled": "F", "LastModified": _now(),
        "Details": [{"DtlKey": DTL_KEY, "Seq": 1, "ItemCode": pick["product_code"],
                     "Qty": float(pick["qty_ordered"]), "FromDocType": "SO",
                     "FromDocNo": pick["so_number"], "FromDocDtlKey": dtl}],
    }
    _print_state(db, str(pick["core_line_id"]), "before")
    _ingest(db, str(pick["company_id"]), record)
    _done(_print_state(db, _line_of(db), "after "))


def undo(db) -> None:
    held = _held(db)
    if held is None:
        raise SystemExit(f"{DOC_NO} is not on this database: nothing to undo")
    if held["is_void"]:
        raise SystemExit(f"{DOC_NO} is already voided: nothing to undo")
    line_id = str(held["sales_order_line_id"]) if held["sales_order_line_id"] else None
    record = dict(held["source_record"])
    record["Cancelled"] = "T"
    record["LastModified"] = _now()
    _ingest(db, str(held["company_id"]), record)
    if line_id:
        _print_state(db, line_id, "after undo")
    print(f"\nUndo done: {DOC_NO} is voided; the line reads as its quantities give.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--so", help="pick this SO number's first eligible line")
    parser.add_argument("--undo", action="store_true", help="void the hand-test XS")
    parser.add_argument("--allow-db", metavar="NAME",
                        help="accept this exact database name as a dev database")
    parser.add_argument("--ingest-url", help="ignored (the ingest runs in this process)")
    args = parser.parse_args()

    from app.config import settings

    try:
        guard(settings.database_url, args.allow_db)
    except Refused as refusal:
        print(f"Refused: {refusal}.", file=sys.stderr)
        return 2

    from app.database import SessionLocal
    from app.services.audit_service import register_audit_listeners
    from app.services.company_scope import register_company_scope_listeners
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    db = SessionLocal()
    try:
        name, addr = db.execute(
            text("SELECT current_database(), host(inet_server_addr())")).one()
        try:
            guard_connected(name, addr, args.allow_db)
        except Refused as refusal:
            print(f"Refused: {refusal}.", file=sys.stderr)
            return 2
        db.rollback()
        # What the API registers at startup (as `autocount_cancel_redecide.py` does).
        register_audit_listeners()
        register_company_scope_listeners()
        register_order_inquiry_post_commit_dispatch()
        print(f"database {name} ({addr or 'unix socket'})")
        if args.undo:
            undo(db)
        else:
            setup(db, args.so)
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
