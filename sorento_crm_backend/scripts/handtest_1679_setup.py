#!/usr/bin/env python3
"""Crew hand-test setup for PR #1679 (CANCEL-STOP-AT-SO). DEV COPIES ONLY.

Puts one real project SO line into the state the hand test needs: cancelled by an AutoCount
Cancel SO while it still has a live order inquiry row. It does that the way production does,
by POSTing one Cancel SO (XS) document to the copy's own external ingest URL with the copy's
API key, so the ingest, the matcher and the post-commit hooks all run for real.

Pick: an open line of an open or closed `project` SO that the Fulfilment Planning board would
read (`FulfilmentBoardService._crm_cancelled_rows`: SO status open / closed, demand_class
project, a project mirror line), with a live order inquiry row on that mirror line, not
cancelled by CS or by a cancel document, nothing cancelled or delivered against it in full, a
line key (`source_ref` ending in `:<DtlKey>`) unique on its SO, and no Cancel SO line naming
that SO yet. The XS cancels the whole ordered qty by that key, so it matches exactly that line.

`--undo` pushes the same XS again as voided (`Cancelled: T`): the cancel stops counting, the
line returns to the status its quantities give and `qty_cancelled` to 0 (plan R6). The voided
XS stays stored, like any voided AutoCount document; running setup again makes it live again
on the same line.

Refuses unless the database is on this machine AND its name says dev / ci / test / copy, and
unless the ingest URL is on this machine.

Run from sorento_crm_backend/ with the COPY's env (its DATABASE_URL and EXTERNAL_API_KEY):
    venv/bin/python scripts/handtest_1679_setup.py --ingest-url http://localhost:8106
    venv/bin/python scripts/handtest_1679_setup.py --ingest-url http://localhost:8106 --undo
Optional: --so <SO number> to pick that SO's first eligible line.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEV_NAME = re.compile(r"(dev|_ci$|_ci_|test|copy)", re.IGNORECASE)
BOOK = "handtest"
DOC_KEY = 167900001
DOC_NO = "XS-HT1679"
DTL_KEY = 167900002
ENTITY_PATH = "/api/v1/external/ingest/cancel_sales_orders"


class Refused(RuntimeError):
    pass


def guard(database_url: str, ingest_url: str) -> str:
    parts = urlsplit(database_url)
    host = (parts.hostname or "").lower()
    name = parts.path.lstrip("/")
    if host not in LOCAL_HOSTS:
        raise Refused(f"database host {host or '(none)'!r} is not this machine")
    if not DEV_NAME.search(name):
        raise Refused(f"database {name!r} is not a dev / ci / test / copy database")
    ingest_host = (urlsplit(ingest_url).hostname or "").lower()
    if ingest_host not in LOCAL_HOSTS:
        raise Refused(f"ingest URL host {ingest_host or '(none)'!r} is not this machine")
    return name


PICK_SQL = """
SELECT so.so_number, so.company_id, c.code AS company_code, core.id AS core_line_id,
       core.source_ref, core.qty_ordered, pl.line_no AS project_line_no, p.product_code,
       (SELECT count(*) FROM projects.order_inquiry_rows r
         WHERE r.so_line_id = pl.id AND r.state <> 'cancelled') AS live_oi_rows
FROM public.sales_order_lines core
JOIN public.sales_orders so ON so.id = core.sales_order_id
JOIN public.companies c ON c.id = so.company_id
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
         WHERE r.so_line_id = pl.id AND r.state <> 'cancelled') AS live_oi_rows
FROM public.sales_order_lines core
JOIN public.sales_orders so ON so.id = core.sales_order_id
JOIN projects.sales_order_lines pl ON pl.core_sales_order_line_id = core.id
JOIN public.products p ON p.id = core.product_id
WHERE core.id = :line
"""


def _post(ingest_url: str, api_key: str, company_code: str, record: dict) -> dict:
    response = httpx.post(
        ingest_url.rstrip("/") + ENTITY_PATH,
        headers={"X-API-Key": api_key},
        json={"companyCode": company_code, "book": BOOK, "records": [record]},
        timeout=120,
    )
    if response.status_code != 200:
        raise SystemExit(f"ingest answered {response.status_code}: {response.text[:500]}")
    body = response.json()
    outcome = body["records"][0]["outcome"]
    if outcome in ("failed", "retryable"):
        raise SystemExit(f"ingest outcome {outcome}: {body['records'][0]}")
    return body


def _print_state(db, core_line_id: str, label: str) -> None:
    db.expire_all()
    row = db.execute(text(STATE_SQL), {"line": core_line_id}).mappings().one()
    print(f"{label}: SO {row['so_number']} line {row['line_no']} item {row['product_code']}: "
          f"status {row['line_status']}, ordered {row['qty_ordered']}, cancelled "
          f"{row['qty_cancelled']}, by cancel doc {row['cancelled_by_cancel_doc']}, "
          f"live OI rows {row['live_oi_rows']}")


def setup(db, ingest_url: str, api_key: str, so_number: str | None) -> None:
    held = _held(db)
    if held is not None:
        if not held["is_void"]:
            raise SystemExit(f"{DOC_NO} is already live on this database: run --undo first")
        # Undone earlier: the same XS goes live again on the same line.
        record = dict(held["source_record"])
        record["Cancelled"] = "F"
        record["LastModified"] = _now()
        _post(ingest_url, api_key, held["code"], record)
        # A voided XS's line is matched to nothing, so the line is read back after the push.
        db.expire_all()
        line_id = str(_held(db)["sales_order_line_id"])
        _print_state(db, line_id, "after ")
        row = db.execute(text(STATE_SQL), {"line": line_id}).mappings().one()
        print(f"\nSetup done (again): SO {row['so_number']} line {row['line_no']} "
              f"(item {row['product_code']}) is cancelled by AutoCount {DOC_NO} and has "
              f"{row['live_oi_rows']} live OI row(s). Undo: --undo")
        return
    pick = db.execute(text(PICK_SQL), {"so": so_number}).mappings().first()
    if pick is None:
        raise SystemExit("no eligible line (see the docstring for what qualifies)")
    dtl = int(re.search(r":([0-9]+)$", pick["source_ref"]).group(1))
    record = {
        "DocKey": DOC_KEY, "DocNo": DOC_NO, "DocDate": date.today().isoformat(),
        "Cancelled": "F",
        "LastModified": _now(),
        "Details": [{"DtlKey": DTL_KEY, "Seq": 1, "ItemCode": pick["product_code"],
                     "Qty": float(pick["qty_ordered"]), "FromDocType": "SO",
                     "FromDocNo": pick["so_number"], "FromDocDtlKey": dtl}],
    }
    _print_state(db, str(pick["core_line_id"]), "before")
    _post(ingest_url, api_key, pick["company_code"], record)
    _print_state(db, str(pick["core_line_id"]), "after ")
    print(f"\nSetup done: SO {pick['so_number']} line {pick['project_line_no']} "
          f"(item {pick['product_code']}) is cancelled by AutoCount {DOC_NO} and has "
          f"{pick['live_oi_rows']} live OI row(s). Undo: --undo")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _held(db):
    """The hand-test XS on this database, with its company code and matched SO line."""
    return db.execute(text(
        "SELECT d.source_record, d.is_void, c.code, l.sales_order_line_id "
        "FROM public.autocount_cancel_docs d JOIN public.companies c ON c.id = d.company_id "
        "LEFT JOIN public.autocount_cancel_lines l ON l.cancel_doc_id = d.id "
        "WHERE d.kind = 'cancel_so' AND d.source_book = :b AND d.doc_key = :k"
    ), {"b": BOOK, "k": DOC_KEY}).mappings().first()


def undo(db, ingest_url: str, api_key: str) -> None:
    doc = _held(db)
    if doc is None:
        raise SystemExit(f"{DOC_NO} is not on this database: nothing to undo")
    if doc["is_void"]:
        raise SystemExit(f"{DOC_NO} is already voided: nothing to undo")
    record = dict(doc["source_record"])
    record["Cancelled"] = "T"
    record["LastModified"] = _now()
    _post(ingest_url, api_key, doc["code"], record)
    if doc["sales_order_line_id"]:
        _print_state(db, str(doc["sales_order_line_id"]), "after undo")
    print(f"\nUndo done: {DOC_NO} is voided; the line reads as its quantities give.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ingest-url", required=True,
                        help="the copy's backend base URL, e.g. http://localhost:8106")
    parser.add_argument("--api-key-env", default="EXTERNAL_API_KEY",
                        help="env var holding the copy's integration API key")
    parser.add_argument("--so", help="pick this SO number's first eligible line")
    parser.add_argument("--undo", action="store_true", help="void the hand-test XS")
    args = parser.parse_args()
    try:
        name = guard(settings.database_url, args.ingest_url)
    except Refused as refusal:
        print(f"Refused: {refusal}.", file=sys.stderr)
        return 2
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(f"Refused: {args.api_key_env} is not set.", file=sys.stderr)
        return 2
    print(f"database {name}, ingest {args.ingest_url}")
    db = SessionLocal()
    try:
        if args.undo:
            undo(db, args.ingest_url, api_key)
        else:
            setup(db, args.ingest_url, api_key, args.so)
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
