"""The low stock report over chat (S5, AC-40..AC-50).

`PLAN-low-stock-report.md`. Staff type "low stock report" into WhatsApp; the bot ALWAYS
runs a FRESH plan (owner ruling 1, 14 Sep), so this route creates a run, a download row and
two queued jobs, then holds the turn open for
`system_settings.low_stock_sync_wait_seconds` waiting for the workbook. If it lands in
time the turn carries it; if it does not, the route hands delivery to the worker and
answers `pending`.

**Its own module, not a second handler in `order_summary.py`**: this is the chatbot's
route, it is reached with an API key, and it carries a gate none of the others do.

-- SECURITY: THE SECOND GATE -------------------------------------------------
`require_permission_with_api_key("scm.reorder.run")` answers "may this integration run
plans". That is NOT the question this route has to answer, which is "may THIS CONTACT have
the low stock report" - and this route WRITES (a run, a download row, two jobs). The
`require_permission_with_api_key` docstring's own rule for a writing endpoint is that it
carries a second, caller-specific gate; here that gate is the per-contact reveal key
`scm.low_stock_report`, resolved and checked IN-ROUTE before anything is created. Without
it: 403 `low_stock_report_not_enabled`, no run, no download row, nothing enqueued (AC-41).

Two consequences worth stating plainly:

* The act-as principal behind `EXTERNAL_API_KEY` (or an integration's `act_as_user_id`)
  MUST hold `scm.reorder.run`, or every contact - granted or not - gets a 403 from the
  FIRST gate and the feature is silently off. That is a DoD item on this lane.
* `include_supplier` follows a SECOND reveal key, `purchase_orders.supplier` (AC-47). A
  contact without it gets a workbook with no Supplier column at all, rather than a blank
  one: a blank column still says "there is a supplier and you may not see it".

**GET, not POST.** The MCP compiler injects `view=render` only on tools with no body
params (`server.py:1604-1612`) and the chatbot lane sends `view=render` on every call, so a
POST tool would never reach the presenter. `ToolSpec.read_only` already says METHOD IS
TRANSPORT, NOT SEMANTICS, and `crm_portal_link_get` is the precedent for a read-listed tool
that mints an artefact.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional, Union

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.services.download_service import DownloadService
from app.services.error_handler import AppException
from app.services.scm import low_stock_report_service, reorder_run_service

log = logging.getLogger(__name__)

router = APIRouter()

_RUN = require_permission_with_api_key("scm.reorder.run")

#: The per-contact reveal key this route's second gate reads (S6 adds it to
#: `contact_field_reveal_service.FIELD_REVEAL_KEYS`, where the admin UI lists it).
LOW_STOCK_GRANT = "scm.low_stock_report"
#: The other key the workbook's shape follows - a dealer must never read a PO's supplier.
SUPPLIER_GRANT = "purchase_orders.supplier"

DEFAULT_SYNC_WAIT_SECONDS = 40
#: How often `_await_download` re-reads the row while it waits. Each poll opens its own
#: connection, and the whole budget is 7 s (`_sync_wait_seconds`), so 0.25 s bought 28
#: connections for a quarter-second of latency nobody reads on WhatsApp. Half a second is
#: still well inside the 3 s a render takes.
_POLL_SECONDS = 0.5

#: B2 (security review): per-contact rate limit on this side-effecting route.
#: `rate_limit.hit` fails OPEN (allows) when Redis is unreachable, so an infra blip never
#: locks a staffer out of the report.
#:
#: FIVE, not three (console round 3, defect C): a real session is a bare ask, a scoped ask
#: and a corrected scope before anyone has done anything wrong - three would lock the
#: staffer out on their first ordinary conversation, and the console case alone makes four.
_RATE_LIMIT = 5
_RATE_WINDOW_SECONDS = 600

#: N5: a chat scope is a handful of codes, never a list. Over this the route REFUSES (422)
#: rather than truncating: silently planning the first 100 of 500 codes answers a question
#: nobody asked, and the caller has no way to tell it happened.
_MAX_CODES = 100
#: `suppliers.supplier_name` is String(255); a longer value names nothing and only bloats
#: the queued job's arguments (security N2).
_MAX_NAME_LENGTH = 255

#: Console round 3, defect A: how far INSIDE the lane's MCP client timeout this route must
#: answer, so `pending` + the delivery claim land before the client hangs up. See
#: `_sync_wait_seconds`.
_TRANSPORT_MARGIN_SECONDS = 3

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _csv_list(values: Optional[list[str]]) -> Optional[list[str]]:
    """`["BRW,PJ", "DC1"]` -> `["BRW", "PJ", "DC1"]`; `None`/empty -> `None`.

    The tool may send a repeated query param or one comma-separated value (the outstanding
    report's own parsing), and `create_run` reads `None` as "no scope, plan everything" -
    which is exactly what an omitted filter means here.

    More than `_MAX_CODES` is a 422, not a truncation: a report built from the first 100 of
    500 codes looks complete and is not, and the caller never learns the difference.
    """
    if not values:
        return None
    out = [part.strip() for value in values for part in str(value).split(",")]
    out = [part for part in out if part]
    if len(out) > _MAX_CODES:
        raise AppException(
            status_code=422,
            message=f"Narrow the scope - at most {_MAX_CODES} codes per report.",
            code="too_many_codes",
        )
    return out or None


def _name_list(values: Optional[list[str]]) -> Optional[list[str]]:
    """Supplier NAMES, one per repeated param, never split on commas: a name like
    "Foshan Co., Ltd" is one supplier. Same `_MAX_CODES` cap as `_csv_list`."""
    out = [str(v).strip() for v in values or () if str(v).strip()]
    if any(len(v) > _MAX_NAME_LENGTH for v in out):
        raise AppException(
            status_code=422,
            message=f"A supplier name is at most {_MAX_NAME_LENGTH} characters.",
            code="name_too_long",
        )
    if len(out) > _MAX_CODES:
        raise AppException(
            status_code=422,
            message=f"Narrow the scope - at most {_MAX_CODES} suppliers per report.",
            code="too_many_codes",
        )
    return out or None


def _sync_wait_seconds(db: Session) -> int:
    """How long to hold the turn: `system_settings.low_stock_sync_wait_seconds`, read LIVE
    per request (AC-43), CAPPED BY THE TRANSPORT TIMEOUT.

    Not cached, and not a constant: the owner asked for a System Setting on the lavish
    page, so moving it must take effect on the next turn rather than on the next deploy.

    **The cap is the whole point** (console round 3, defect A, measured 14 Sep). The
    chatbot lane's MCP client gives up after `settings.chatbot_mcp_timeout_seconds`
    (10 s by default, `lanes/business/services.py`), which is SHORTER than this setting's
    40 s default. A full unscoped run took over 10 s: the lane raised `httpx.ReadTimeout`
    and rendered its generic "ran into a problem" line, while this route - still waiting -
    never reached its own timeout branch and so never set `deliver_to_contact_id`. The
    worker then built a workbook nobody delivered. Answering `pending` BEFORE the lane
    hangs up is what makes the claim happen, so the worker pushes the file.

    `chatbot_mcp_timeout_seconds` is deliberately NOT raised here: it is every tool's knob,
    and lengthening it would slow every other turn's failure. The MCP server's own
    CRM-facing timeout (`CRM_MCP_TIMEOUT`, default 60 s) is far longer than either, so the
    lane's client is the binding one; if it is ever lowered below this cap it becomes
    binding instead and this margin has to follow it.
    """
    from app.config import settings as app_settings
    from app.models.user import SystemSetting

    row = db.query(SystemSetting).first()
    value = getattr(row, "low_stock_sync_wait_seconds", None) if row else None
    configured = int(value) if value else DEFAULT_SYNC_WAIT_SECONDS

    transport = int(getattr(app_settings, "chatbot_mcp_timeout_seconds", 0) or 10)
    # Three seconds of margin: enough for the route to build the pending answer, claim
    # delivery and get the response back over the wire before the lane's client gives up.
    capped = transport - _TRANSPORT_MARGIN_SECONDS
    if capped < 1:
        # A pathologically small transport timeout - never wait a negative or zero budget;
        # one second still lets a fast run answer `ready` in the turn.
        capped = 1
    return min(configured, capped)


def _adopt_company_scope(db: Session, *, resolved_contact_id: Optional[str],
                         owner_user_id: str) -> Optional[str]:
    """Resolve the company this chat turn writes under, and stamp it on the session.

    An API-key request has no company scope of its own - `_resolve_api_key_scope` reads the
    contact identity for READS, but the ambient scope a WRITE needs to stamp `company_id`
    is not set by it - so every company-scoped insert refuses with 400
    `company_scope_required` until one is chosen. The rule, in order:

    0. **The scope the session already carries**, when it is exactly one company. A
       caller that resolved one upstream has already answered this question, and
       overriding it here would let this route plan a different company's book from the
       one the rest of the request reads.
    1. **The contact's own company**, when it has exactly one - read off
       `respond_contact_companies` for the contact this route ALREADY resolved, so the
       plan is built for the company whose stock the asker can actually see. Keyed on that
       resolved id rather than through `resolve_contact_company_scope`, which re-resolves
       the identity with the STRICT workspace join: the 16 NULL-workspace contacts (the
       ones this chatbot actually serves) drop out of it, which is the whole reason
       `resolve_contact_with_null_workspace_fallback` exists. One identity, resolved once.
    2. **The owner user's active company** when the contact maps to several or none - the
       CRM user linked to the contact, else the act-as principal. `last_active_company_id`
       when it is still granted, else the single grant, else the lowest granted id: the
       fail-closed tail of `company_scope_resolver._resolve_user_scope`, minus the JWT
       claim, because an API-key request carries no token to read one from. When the
       contact DOES carry companies (just not exactly one), those grants are first
       INTERSECTED with the contact's own companies (security N-b): the owner may be the
       act-as principal, whose grants say nothing about this contact, and a company only
       the owner can see would put another company's book on that contact's phone.
    3. **Neither, or an empty intersection** -> return None, nothing written. `_prepare` turns that into the error
       miss shape (reviewer S1/AC-44a) rather than an HTTP 400, so the bot says "Could not
       run the low stock report right now." instead of surfacing a status code. A run
       stamped with a guessed company would plan another company's book.

    Returns the company id (and leaves it set on `db` for the caller's own writes), or
    None when no company could be resolved.
    """
    from app.models.base import get_company_scope, set_company_scope
    from app.models.company import RespondContactCompany
    from app.models.user import User
    from app.services.company_scope_resolver import resolve_user_grant_ids

    # `get_company_scope` returns one of UNSET / None / frozenset - only the last is a
    # scope, and only a single-company one answers this question (None means "every
    # company", which cannot stamp a `company_id`).
    ambient = get_company_scope(db)
    if isinstance(ambient, frozenset) and len(ambient) == 1:
        return str(next(iter(ambient)))

    contact_companies: list[str] = []
    if resolved_contact_id:
        contact_companies = [
            str(c)
            for (c,) in db.query(RespondContactCompany.company_id)
            .filter(RespondContactCompany.respond_contact_id == resolved_contact_id)
            .all()
            if c
        ]
    company_id: Optional[str] = None
    if len(set(contact_companies)) == 1:
        company_id = contact_companies[0]
    else:
        grants = {str(g) for g in resolve_user_grant_ids(db, owner_user_id)}
        # Security N-b: when the contact maps to SEVERAL companies, the choice is made
        # from the INTERSECTION of the owner's grants and the contact's own companies -
        # never from the owner's grants alone. The owner here can be the act-as principal,
        # whose grants have nothing to do with this contact, so the unintersected rule
        # could pick a company the contact is not a member of and push them its book. An
        # EMPTY intersection is a refusal, not a fallback: there is no company both sides
        # may see. A contact with NO companies at all keeps the owner's grants, which is
        # the pre-existing unlinked-contact path and leaks nothing new.
        if contact_companies:
            grants &= set(contact_companies)
        last_active = db.query(User.last_active_company_id).filter(
            User.id == owner_user_id
        ).scalar()
        if last_active and str(last_active) in grants:
            company_id = str(last_active)
        elif len(grants) == 1:
            company_id = next(iter(grants))
        elif grants:
            company_id = sorted(grants)[0]

    if not company_id:
        return None
    set_company_scope(db, frozenset({company_id}))
    return company_id


def _read_download_row(download_id: str) -> Optional[dict]:
    """ONE poll of `user_downloads`, on its OWN short-lived session.

    Its own session per read, not the request's: the request session sits inside the
    transaction that created the download row, and the worker's `mark_ready` commits on a
    different connection - re-reading through the request session would keep returning the
    snapshot this turn started with and never see it land.

    Synchronous on purpose: `_await_download` hands it to `asyncio.to_thread`, because
    opening a connection and running a query are both blocking calls and this loop runs
    inside a chat turn's request handler.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT status, storage_provider, storage_key, filename, "
            "       row_count_low, row_count_all "
            "FROM user_downloads WHERE id = :id"
        ), {"id": str(download_id)}).mappings().first()
    finally:
        db.close()
    return dict(row) if row else None


async def _await_download(download_id: str) -> Optional[dict]:
    """Poll `user_downloads` until the row is terminal, every read OFF the event loop.

    Security SF-1: the read itself is `_read_download_row`, dispatched through
    `asyncio.to_thread`. It used to run inline - `SessionLocal()` plus a query, both
    blocking - so up to fourteen times a turn this coroutine stalled the whole loop, which
    is exactly what security B1 moved every other blocking step out of.

    The caller bounds this with `asyncio.wait_for`, so this loop has no timeout of its own;
    it returns only when the row is ready (or terminal), and is cancelled otherwise.
    """
    while True:
        row = await asyncio.to_thread(_read_download_row, download_id)
        if row and row["status"] in ("ready", "failed"):
            return row
        await asyncio.sleep(_POLL_SECONDS)


def _as_of_for_run(db: Session, run_id: str) -> Optional[str]:
    """The date the run's book was frozen for, off the rows themselves - the same stamp
    the workbook's own filename carries. None when the run froze no rows, because inventing
    today's date would label a book that was never built."""
    stamp = db.execute(text(
        "SELECT MAX(as_of) FROM scm.order_summary_row WHERE run_id = :r"
    ), {"r": str(run_id)}).scalar()
    return stamp.isoformat() if stamp else None


def _ready_payload(db: Session, *, run_id: str, download_id: str) -> Optional[dict]:
    """The `ready` answer, read off the download ROW - never by reopening the workbook.

    `row_count_low` / `row_count_all` are stamped by `generate_low_stock_report` at
    `mark_ready` for exactly this reason (AC-43): the route is holding a chat turn open and
    has no business parsing a spreadsheet on the request thread. Returns None when the row
    is not ready, so the caller can fall through to the pending path, and the `error` answer
    when the row IS ready but no URL can be minted for it.
    """
    row = db.execute(text(
        "SELECT status, storage_provider, storage_key, filename, "
        "       row_count_low, row_count_all "
        "FROM user_downloads WHERE id = :id"
    ), {"id": str(download_id)}).mappings().first()
    if not row or row["status"] != "ready" or not row["storage_key"]:
        return None
    key = row["storage_key"]
    filename = key.rsplit("/", 1)[-1] or (row["filename"] or "low-stock.xlsx")
    try:
        url = low_stock_report_service.attachment_url(row["storage_provider"], key)
    except Exception:  # noqa: BLE001 - a chat turn must never answer with a 500
        # `r2_service` / `s3_service` raise a bare `ValueError` when their credentials are
        # incomplete, which would leave this handler as an unhandled 500: the lane would
        # render its generic failure line and, worse, the turn would never claim delivery,
        # so the worker's file would reach nobody. AC-44a's rule applies - something the
        # route cannot produce is `error`, said plainly, once.
        log.exception(
            "low_stock_report: no attachment URL for download %s (provider %s)",
            download_id, row["storage_provider"],
        )
        return _error_answer()
    return {
        "status": "ready",
        "run_id": run_id,
        "as_of": _as_of_for_run(db, run_id),
        "low_count": row["row_count_low"],
        "all_count": row["row_count_all"],
        "attachments": [{
            "url": url,
            "filename": filename,
            "mimeType": MIME_XLSX,
            "attachmentType": "file",
        }],
    }


@dataclass
class _Prepared:
    """The handoff from `_prepare` (the blocking phase) to the wait. Everything the caller
    needs is copied out here BEFORE the wait, so nothing reads the request session across
    it (security B1)."""

    run_id: str
    download_id: str
    resolved_contact_id: str
    sync_wait_seconds: int


def _error_answer() -> dict:
    """The one answer the bot gives when the report could not be produced at all - a company
    it cannot resolve, an excluded named product, a broker that would not take the job, or a
    run the worker marked `failed` (over the row cap, a render error). The presenter renders
    it as a MISS ("Could not run the low stock report right now."), never as `pending`
    (reviewer S1 / AC-44a): a failure that reads as "on its way" leaves the contact waiting
    for a file that is never coming. A fresh dict per call, so no caller can mutate a shared
    one."""
    return {"status": "error", "message": "Could not run the low stock report right now."}


def _prepare(
    db: Session,
    *,
    warehouse_codes: Optional[list[str]],
    product_codes: Optional[list[str]],
    date_from: Optional[date],
    date_to: Optional[date],
    contact_id: str,
    space_id: str,
    principal_id: str,
    categories: Optional[list[str]] = None,
    suppliers: Optional[list[str]] = None,
    split: str = "none",
) -> Union[dict, _Prepared]:
    """Every BLOCKING step of a turn, in ONE function run on a worker thread (security B1 /
    reviewer S4): the reveal-key read, the rate-limit hit, the company resolve, `create_run`,
    the download-row insert and both `enqueue_job` calls. The async handler runs this via
    `asyncio.to_thread`, so none of it occupies the event loop - the shape
    `app/api/v1/external/media.py::_decide_meter_record_and_enqueue` uses.

    `create_run` and `DownloadService.create` commit, which returns the request session's
    connection to the pool; the handler then waits on `_await_download`, which polls its OWN
    short-lived sessions, so the request connection is not HELD across the wait either.

    Returns a terminal answer dict (`busy` / `error`) to send at once, or a `_Prepared` the
    caller waits on. Raises `AppException(403)` for the no-key refusal (AC-41), which stays
    an HTTP status; the lane maps it to its own refusal line.
    """
    from app.services import rate_limit
    from app.services.contact_field_reveal_service import granted_keys
    from app.services.field_access import resolve_contact_with_null_workspace_fallback
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_low_stock_report
    from app.tasks.reorder_tasks import run_reorder_job

    resolved_contact_id = resolve_contact_with_null_workspace_fallback(
        db, contact_id=contact_id, space_id=space_id
    )

    # --- B2: per-contact rate limit, BEFORE the gate (security N-e) -------------------
    # Metered ahead of the 403, not after it: a refusal that costs nothing lets a key
    # holder walk a contact list for free and read off which contacts hold the grant (403
    # vs an answer). Now the walk runs out of budget after five. Keyed on the resolved id
    # when there is one, else on the raw `contact_id`, so an unresolvable contact is
    # metered too rather than being the free path.
    if not rate_limit.hit(
        "low_stock_report", str(resolved_contact_id or contact_id),
        limit=_RATE_LIMIT, window_seconds=_RATE_WINDOW_SECONDS,
    ).allowed:
        # The `reason` is what lets the presenter say WHICH busy this is (defect C) - "too
        # many in the last 10 minutes" reads very differently from "a plan is running".
        return {"status": "busy", "reason": "rate_limited"}

    # --- the second gate (AC-41) ------------------------------------------------------
    keys = granted_keys(db, resolved_contact_id) if resolved_contact_id else []
    if LOW_STOCK_GRANT not in keys:
        raise AppException(
            status_code=403,
            message="Low stock report is not enabled for your account.",
            code="low_stock_report_not_enabled",
        )
    include_supplier = SUPPLIER_GRANT in keys
    # LOWSTOCK-FILTER-ASK: a supplier filter or a supplier split for a contact whose
    # workbook hides the Supplier column would leak, through the kept rows or the sheet
    # titles, what the column hides. The lane never offers either; refuse here before
    # anything is created rather than let `export_low_stock` fail after the run.
    if not include_supplier and (suppliers or split in ("supplier", "supplier_category")):
        return _error_answer()

    # --- who owns the run and the file (AC-42) ----------------------------------------
    # The CRM user the chatting contact is linked to, so the workbook lands in THEIR My
    # Downloads and the plan says who asked for it; no link falls back to the authenticated
    # principal (the act-as user). security N2: ACTIVE only, ordered, so a deactivated or
    # duplicate link resolves deterministically to a live owner.
    owner_user_id = str(db.execute(text(
        "SELECT id FROM users WHERE respond_contact_id = :c AND status = 'ACTIVE' "
        "ORDER BY created_at, id LIMIT 1"
    ), {"c": resolved_contact_id}).scalar() or principal_id)

    # --- the company this run belongs to (400 avoided; see `_adopt_company_scope`) -----
    company_id = _adopt_company_scope(
        db, resolved_contact_id=resolved_contact_id, owner_user_id=owner_user_id,
    )
    if company_id is None:
        # reviewer S1 / AC-44a: an unresolved company is an error the bot says, not a 400.
        return _error_answer()

    # --- the fresh run ----------------------------------------------------------------
    try:
        created = reorder_run_service.create_run(
            db,
            _csv_list(warehouse_codes),
            "warehouse",
            actor=owner_user_id,
            enqueue=False,
            product_codes=_csv_list(product_codes),
            plan_horizon_start=date_from,
            plan_horizon_date=date_to,
            requested_via="chat",
            refuse_if_in_flight=True,
        )
    except AppException as e:
        if e.status_code == 409:
            # B2 / AC-49: a plan is already running for this company.
            return {"status": "busy", "reason": "in_flight"}
        if e.status_code == 422:
            # An excluded named product (N5) or any other create-time refusal is an error
            # the bot reports, never a pending line.
            return _error_answer()
        raise
    run_id = created["run_id"]

    download = DownloadService(db).create(
        user_id=owner_user_id,
        kind="low_stock_xlsx",
        source_entity_type="reorder_run",
        source_entity_id=run_id,
        filename=None,
    )
    download_id = str(download.id)

    # The run job first, then the export with `depends_on` it: the workbook has to be
    # rendered from a COMPLETED plan, not a half-frozen one. `enqueue_job` forwards
    # `**kwargs` to `Queue.enqueue`, and RQ honours `depends_on` natively.
    try:
        run_job = enqueue_job(run_reorder_job, run_id, queue_name="imports")
        # CONTACT-BRAND-SCOPE: the worker opens a fresh session, so a brand-scoped contact's
        # brands ride the job. Unscoped sends nothing extra (the call is unchanged).
        from app.models.base import get_brand_scope

        brand_scope = get_brand_scope(db)
        scope_kwargs = {"brand_ids": sorted(brand_scope)} if brand_scope else {}
        enqueue_job(
            generate_low_stock_report,
            download_id,
            run_id,
            owner_user_id,
            include_supplier=include_supplier,
            split=split,
            suppliers=suppliers,
            categories=categories,
            **scope_kwargs,
            queue_name="imports",
            job_timeout=600,
            depends_on=run_job,
        )
    except Exception:  # noqa: BLE001 - a broker that will not take the job is an error the
        # bot reports, not a 500. The run/download rows exist; the delegated sweep and a
        # manual retry cover them.
        log.exception("low_stock_report: could not enqueue jobs for run %s", run_id)
        return _error_answer()

    sync_wait = _sync_wait_seconds(db)
    db.commit()
    return _Prepared(
        run_id=run_id,
        download_id=download_id,
        resolved_contact_id=str(resolved_contact_id),
        sync_wait_seconds=sync_wait,
    )


def _claim_delivery(db: Session, prepared: _Prepared) -> dict:
    """Hand delivery to the worker (AC-44), or answer with the file if the row won the race.

    ONE conditional UPDATE: `WHERE status <> 'ready'` makes this exclusive with the task's
    own claim. 0 rows means the row turned ready in the same breath and this turn LOST the
    race - so answer with the file rather than promising a push the task will never make
    (its claim would find `deliver_to_contact_id IS NULL`).
    """
    claimed = db.execute(text(
        "UPDATE user_downloads SET deliver_to_contact_id = :c "
        "WHERE id = :id AND status <> 'ready'"
    ), {"c": prepared.resolved_contact_id, "id": prepared.download_id}).rowcount
    db.commit()
    if not claimed:
        won = _ready_payload(db, run_id=prepared.run_id, download_id=prepared.download_id)
        if won is not None:
            return won
    return {"status": "pending", "run_id": prepared.run_id,
            "download_id": prepared.download_id}


@router.get("/low-stock-report")
async def low_stock_report(
    warehouse_codes: Optional[list[str]] = Query(None),
    product_codes: Optional[list[str]] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    contact_id: str = Query(...),
    space_id: str = Query(...),
    categories: Optional[list[str]] = Query(None),
    suppliers: Optional[list[str]] = Query(None),
    split: str = Query("none"),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_RUN),
):
    """Run a fresh plan for this scope and answer with the workbook, or with a promise.

    `contact_id` and `space_id` are REQUIRED (422 without): this route exists for the
    chatbot and for nothing else, and without a contact there is nobody to check the reveal
    key against or to push the file to (AC-40).

    Four answers, all HTTP 200, because each is something the bot can SAY:
      * `ready`   - the workbook landed inside the budget; the turn carries it.
      * `pending` - it did not; the worker will push it when it does (AC-44).
      * `busy`    - a plan is already running, or the contact is over the rate limit (AC-49).
      * `error`   - it could not be produced (no company, excluded product, broker down, or
                    the worker marked the run `failed`); the bot says so, never `pending`
                    (reviewer S1 / AC-44a).

    Every blocking step runs off the event loop via `asyncio.to_thread` (security B1 and
    SF-1, which moved the poll's own `SessionLocal()` + query out too); the request
    session's connection is returned to the pool by `_prepare`'s commit and is not used
    again until after the wait, and the wait polls on its own short-lived sessions.

    SECURITY - the trust boundary (security note N-a, 14 Sep)
    --------------------------------------------------------
    `contact_id` is BOTH the authorisation subject and the delivery target. The caller
    names a contact; this route checks THAT contact's reveal key and then sends THAT
    contact the workbook. So a holder of `EXTERNAL_API_KEY` whose act-as principal has
    `scm.reorder.run` can cause an unsolicited push to any contact that holds
    `scm.low_stock_report` - the contact did not ask, and a report arrives on their phone.

    What that caller CANNOT do is choose whose data it is: the book is always the
    contact's own company (`_adopt_company_scope`, and N-b's intersection above), the
    Supplier column still depends on that contact's own `purchase_orders.supplier` key,
    and the rate limit caps the volume at five per contact per ten minutes.

    This is accepted because `EXTERNAL_API_KEY` is a STAFF-LEVEL credential, not a
    per-contact one: it already reaches every read tool in the MCP catalog. If the key is
    ever handed to something less trusted, this route needs a per-contact proof of intent
    (a signed turn id, or the contact's own inbound message id) before it sends anything.
    """
    from app.services.scm.workbook_split import SPLIT_VALUES

    # LOWSTOCK-FILTER-ASK: the filters the chat settled before asking (category codes,
    # supplier names, the group-by), applied to the WORKBOOK of the whole-book run - the
    # same `categories` / `suppliers` / `split` the in-app page sends. An unknown split is
    # refused before anything is created.
    if split not in SPLIT_VALUES:
        raise AppException(422, f"split must be one of {', '.join(SPLIT_VALUES)}.")
    prepared = await asyncio.to_thread(
        _prepare, db,
        warehouse_codes=warehouse_codes, product_codes=product_codes,
        date_from=date_from, date_to=date_to,
        contact_id=contact_id, space_id=space_id,
        principal_id=str(current_user["id"]),
        categories=_csv_list(categories), suppliers=_name_list(suppliers), split=split,
    )
    if isinstance(prepared, dict):
        return prepared

    # --- the bounded wait (AC-43) -----------------------------------------------------
    try:
        snapshot = await asyncio.wait_for(
            _await_download(prepared.download_id), prepared.sync_wait_seconds
        )
    except asyncio.TimeoutError:
        snapshot = None
    except Exception:  # noqa: BLE001 - a failed poll falls through to the claim, never a 500
        log.exception(
            "low_stock_report: waiting on download %s failed", prepared.download_id
        )
        snapshot = None

    # reviewer S1 / AC-44a: a run the worker marked `failed` (over MAX_LOW_STOCK_ROWS, a
    # render error) is an ERROR the bot must NOT report as pending.
    if snapshot is not None and snapshot.get("status") == "failed":
        return _error_answer()

    ready = await asyncio.to_thread(
        _ready_payload, db, run_id=prepared.run_id, download_id=prepared.download_id
    )
    if ready is not None:
        return ready

    if dry_run:
        # A test turn (the Chatbot Console, `is_test`): the run and the file are real and
        # land in My Downloads, but delivery is NEVER handed to the worker, so nothing
        # reaches the contact's WhatsApp (LOWSTOCK-FILTER-ASK tester finding).
        return {"status": "pending", "run_id": prepared.run_id,
                "download_id": prepared.download_id, "dry_run": True}

    return await asyncio.to_thread(_claim_delivery, db, prepared)
