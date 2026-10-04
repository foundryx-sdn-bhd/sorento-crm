"""`GET /api/v1/sales/analysis`: the chatbot's sales answer, as the whole table in text AND
the same query as an Excel (PLAN-retail-sales-reports-26sep 5.5, R4.2; Owner ruling 26 Sep
07:16 Q2, "always text + file, no cutoff").

**One query, two formats.** The figures are `engine.run` over the `sales_yearly`
definition with the view the ask names (rows and columns), so the text, the Excel and the
Yearly comparison screen are the same query (AC-R4-6). The Excel is the kernel's own
`generate_report_xlsx`, queued with the SAME params and view and the contact's company as
its grant.

**The file path is the low stock report's** (`app/api/v1/scm/low_stock_report.py`): a
`report_xlsx` My Downloads row owned by the CRM user linked to the contact (else the act-as
user), a bounded wait (`_await_download`, capped under the chatbot's MCP timeout), and when
the file is not ready in time the delivery is claimed for the worker, which pushes it once
(`export_tasks._push_download_to_chat`). The text never waits on the file.

**Gates, in order:** `sales.reports.view` on the act-as principal (the route dependency),
the `sales` module (the router guard), then the CONTACT's own `sales_orders.sales_report`
reveal key (403 `sales_report_not_enabled`, nothing written). A contact linked to a customer
account is a dealer and is refused (AC-S1-23). The company is one of the contact's own; a
contact in two companies who names neither is asked (AC-S1-21). A question or a refusal
carries no file and writes no row (AC-R4-3).

GET, not POST, for the reason the low stock route gives: the MCP compiler adds
`view=render` only to query-param tools.

SECURITY - the trust boundary (the low stock route's note N-a, restated for this route)
--------------------------------------------------------------------------------------
**API key only.** `require_permission_with_api_key` also accepts a Bearer token, and this
route answers with the figures of the CONTACT's company, which says nothing about the
caller's own grant: a staffer granted Sorento could name a Mocha contact and read Mocha. So a
request without `X-API-Key` is refused (403 `api_key_required`); the chatbot is the only
caller. `contact_id` is both the subject and the delivery target, so a holder of
`EXTERNAL_API_KEY` whose act-as principal holds `sales.reports.view` can cause a sales
figure answer and an Excel push to any contact holding `sales_orders.sales_report`; that
is accepted because the key is a staff-level credential that already reaches every read
tool. What the caller CANNOT choose is whose figures they are (the contact's own
companies), or make a dealer contact receive them (refused, looked up with the company
scope OFF so a NULL-workspace contact is not waved through). The rate limit caps the
volume: ten per contact per ten minutes. Ten, not the low stock report's five: an ask here
is one query and one small render (no reorder run), and a sales conversation refines the
same ask (company, channel, period, basis) several times in a row.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

# The low stock route's own wait and budget, not a second copy: one seam, one cap.
from app.api.v1.scm.low_stock_report import _await_download, _sync_wait_seconds  # noqa: F401
from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.services.error_handler import AppException

log = logging.getLogger(__name__)

router = APIRouter()

_GATE = require_permission_with_api_key("sales.reports.view")

REPORT_KEY = "sales_yearly"
GRANT = "sales_orders.sales_report"
REFUSAL = "Sorry, I can only share sales figures for your own account."
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The ask's axis words -> the dataset's dimensions.
AXES = {"month": "month_of_year", "year": "year", "channel": "channel"}
_MAX_N = 100
_RATE_LIMIT = 10
_RATE_WINDOW_SECONDS = 600


def _unprocessable(message: str, code: str) -> AppException:
    return AppException(status_code=422, message=message, code=code)


def _validate(rows: str, cols: str, basis: Optional[str], date_from, date_to, n) -> None:
    if rows not in AXES or cols not in AXES:
        raise _unprocessable(
            f"Unknown axis; use one of {', '.join(sorted(AXES))}", "unknown_axis"
        )
    if rows == cols:
        raise _unprocessable("Rows and columns cannot be the same", "rows_equal_cols")
    from app.services.reports.datasets.sales_order_lines import BASIS_WORDS

    if basis not in BASIS_WORDS:
        raise _unprocessable("Basis is ordered, delivered or invoiced", "basis_required")
    if date_from and date_to and date_from > date_to:
        raise _unprocessable("date_from is after date_to", "date_range_inverted")
    if n is not None and not 1 <= n <= _MAX_N:
        raise _unprocessable(f"n is 1 to {_MAX_N}", "n_out_of_range")


def _company_question(names: List[str]) -> str:
    return f"{', '.join(names[:-1])} or {names[-1]}?"


@dataclass
class _Prepared:
    answer: Dict[str, Any]
    download_id: str
    resolved_contact_id: str
    sync_wait_seconds: int


def _money(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value.quantize(Decimal("0.01")))


def _answer(result, *, by_month, period_start: date, as_at: date, company_name: str,
            channel: Optional[str], basis: str, row_label: str, col_label: str,
            n: Optional[int]) -> Dict[str, Any]:
    """The pivot as the text reads it: one line per row, the columns in order, a Difference
    column (last year minus the one before) when the columns are two or more years.

    The Difference is the VARIANCE row's rule (G5 (a), review round 2 B1): this year to
    date against the same months last year, a missing month read as 0, so a month this
    year sold nothing in counts. It is read off `by_month` (the same rows, by month of the
    period), because a year column alone cannot say which months it holds."""
    from app.services.reports.datasets.sales_order_lines import BASIS_WORDS, CHANNELS
    from app.services.reports.engine import variance_months

    pivot = result.layouts.summary
    labels = pivot.col_dim.value_labels or {}
    col_values = list(pivot.col_dim.values)
    columns = [labels.get(v, v) for v in col_values]
    difference = col_label == "Year" and len(col_values) >= 2
    months: List[str] = []
    if difference:
        last, previous = col_values[-1], col_values[-2]
        months = variance_months(period_start, as_at, int(last), int(previous))

    def _difference(row_value: str) -> Optional[Decimal]:
        per_month = by_month.cells.get(row_value, {})
        diff: Optional[Decimal] = None
        for month in months:
            now = per_month.get(f"{last}-{month}", {}).get("sales_value")
            before = per_month.get(f"{previous}-{month}", {}).get("sales_value")
            if now is None and before is None:
                continue
            diff = (diff or Decimal(0)) + Decimal(now or 0) - Decimal(before or 0)
        return diff

    def _cells(per_col: Dict[str, Dict[str, str]]) -> List[Optional[str]]:
        return [per_col.get(v, {}).get("sales_value") for v in col_values]

    row_labels = pivot.row_value_labels or {}
    rows = []
    diffs: List[Decimal] = []
    for value in pivot.row_values:
        values = _cells(pivot.cells.get(value, {}))
        if difference:
            diff = _difference(value)
            values.append(_money(diff))
            if diff is not None:
                diffs.append(diff)
        rows.append({
            "label": row_labels.get(value, value),
            "values": values,
            "total": pivot.row_totals.get(value, {}).get("sales_value"),
        })
    total_count = len(rows)
    if n is not None:
        # Ranked by the row's own total, then its label: the top X rule (PR #1263).
        rows = sorted(rows, key=lambda r: (-Decimal(r["total"] or 0), r["label"]))[:n]

    totals = _cells(pivot.col_totals)
    if difference:
        # Every row's, not only the top n: the total line is the whole set's (AC-S1-7).
        totals.append(_money(sum(diffs, Decimal(0))) if diffs else None)
    channel_words = dict(CHANNELS)
    return {
        "status": "ready",
        "report": "Sales",
        "company": company_name,
        "channel": channel_words.get(channel, channel) if channel else "All channels",
        "basis": BASIS_WORDS[basis],
        "period": result.period_label,
        "rows_label": row_label,
        "cols_label": col_label,
        "count_label": f"{row_label}s",
        "columns": columns + (["Difference"] if difference else []),
        "rows": rows,
        "totals": {"values": totals, "total": pivot.grand_total.get("sales_value")},
        "total_count": total_count,
        "attachments": [],
    }


def _prepare(
    db: Session,
    *,
    rows: str,
    cols: str,
    channel: Optional[str],
    basis: Optional[str],
    company: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    n: Optional[int],
    contact_id: str,
    space_id: str,
    principal_id: str,
) -> Union[Dict[str, Any], _Prepared]:
    """Every blocking step, on a worker thread (the low stock route's security B1 shape)."""
    from app.api.v1.reports.reports import _export_filename
    from app.config import settings
    from app.models.access import RespondContactCustomer
    from app.models.base import set_company_scope
    from app.models.company import Company, RespondContactCompany
    from app.schemas.report import ReportViewConfig
    from app.services import queue_service, rate_limit
    from app.services.contact_field_reveal_service import granted_keys
    from app.services.download_service import DownloadService
    from app.services.field_access import resolve_contact_with_null_workspace_fallback
    from app.services.reports import engine, registry as reg
    from app.tasks.report_export_tasks import generate_report_xlsx

    _validate(rows, cols, basis, date_from, date_to, n)
    if channel is not None and channel not in ("dealer", "project"):
        raise _unprocessable("Channel is dealer or project", "unknown_channel")

    resolved = resolve_contact_with_null_workspace_fallback(
        db, contact_id=contact_id, space_id=space_id
    )
    if not rate_limit.hit(
        "sales_analysis", str(resolved or contact_id),
        limit=_RATE_LIMIT, window_seconds=_RATE_WINDOW_SECONDS,
    ).allowed:
        return {"status": "busy", "message": "Too many sales reports in the last 10 minutes."}

    keys = granted_keys(db, resolved) if resolved else []
    if GRANT not in keys:
        raise AppException(
            status_code=403,
            message="Sales report is not enabled for your account.",
            code="sales_report_not_enabled",
        )
    # Scope OFF: the question is "is this contact linked to ANY customer account", and
    # `respond_contact_customers` is company-scoped, so under an API-key request's scope
    # (empty for a NULL-workspace contact) the lookup would find nothing and let a dealer
    # through (security review B1).
    from app.models.base import company_scope

    with company_scope(db, None):
        is_dealer = db.query(RespondContactCustomer.id).filter(
            RespondContactCustomer.contact_id == str(resolved)
        ).first()
    if is_dealer is not None:
        return {"status": "refused", "message": REFUSAL}

    # The contact's OWN companies, read off the contact this route resolved (the low
    # stock route's rule 1): never the act-as principal's, which say nothing about them.
    grants = {
        str(c)
        for (c,) in db.query(RespondContactCompany.company_id)
        .filter(RespondContactCompany.respond_contact_id == str(resolved))
        .all()
        if c
    }
    companies = {
        str(i): (str(name), str(code))
        for i, name, code in db.query(Company.id, Company.name, Company.code).all()
    }
    if company:
        wanted = company.strip().casefold()
        # The NAME first (what a person types), then the code; never an arbitrary pick.
        match = [cid for cid, (name, _code) in companies.items() if name.casefold() == wanted] or [
            cid for cid, (_name, code) in companies.items() if code.casefold() == wanted
        ]
        if not match:
            raise _unprocessable(f"Unknown company '{company}'", "unknown_company")
        if match[0] not in grants:
            raise AppException(status_code=403, message="You do not have access to that company",
                               code="COMPANY_FORBIDDEN")
        company_id = match[0]
    elif len(grants) == 1:
        company_id = next(iter(grants))
    elif grants:
        from app.services.company_scope import DEFAULT_COMPANY_ID

        names = [companies[c][0] for c in sorted(grants, key=lambda c: (c != DEFAULT_COMPANY_ID,
                                                                        companies[c][0]))]
        return {"status": "clarify", "message": _company_question(names)}
    else:
        return {"status": "error", "message": "Could not run the sales report right now."}

    definition = reg.get(REPORT_KEY)
    today = reg.today_malaysia()
    start = date_from or date(today.year, 1, 1)
    # "2025 vs 2026" asks through 31 December; the figures stop today, and the file's
    # "AS AT" line must say the day it was made, not a day that has not come.
    end = min(date_to or today, today)
    if start > end:
        raise _unprocessable("The period starts after today", "date_range_inverted")
    params = {
        "date_basis": "order_date",
        "period": {"kind": "custom", "from": start.isoformat(), "to": end.isoformat()},
        "company": [company_id],
        "channel": [channel] if channel else [],
        "basis": [basis],
    }
    view = ReportViewConfig.model_validate({
        "params": params,
        "detail": {"columns": [], "order": []},
        "pivot": {"rows": AXES[rows], "cols": AXES[cols], "measures": ["sales_value"]},
    })
    grant = frozenset({company_id})
    result = engine.run(db, definition, params, view, company_grants=grant)
    # The file is the same query. A month-by-year ask is the report's OWN layout turned
    # on its side, so the file uses that layout (years down, JAN to DEC across, and the
    # VARIANCE row): the PDF's page, carrying both years and the difference the text
    # prints, figure for figure (AC-R4-6).
    file_view = view
    if AXES[rows] == "month_of_year" and AXES[cols] == "year":
        file_view = view.model_copy(update={"pivot": view.pivot.model_copy(
            update={"rows": "year", "cols": "month_of_year"})})
    by_month = None
    if AXES[cols] == "year" and len(result.layouts.summary.col_dim.values) >= 2:
        # The Difference column's months (see `_answer`): the same rows, by month.
        month_view = view.model_copy(update={"pivot": view.pivot.model_copy(
            update={"cols": "year_month"})})
        by_month = engine.run(db, definition, params, month_view,
                              company_grants=grant).layouts.summary
    answer = _answer(
        result,
        by_month=by_month,
        period_start=start,
        as_at=end,
        company_name=companies[company_id][0],
        channel=channel,
        basis=basis,
        row_label=definition.dataset.column(AXES[rows]).label,
        col_label=definition.dataset.column(AXES[cols]).label,
        n=n,
    )

    # --- the file: the same params, the same view ------------------------------------
    owner_user_id = str(db.execute(text(
        "SELECT id FROM users WHERE respond_contact_id = :c AND status = 'ACTIVE' "
        "ORDER BY created_at, id LIMIT 1"
    ), {"c": str(resolved)}).scalar() or principal_id)
    set_company_scope(db, grant)
    ctx = engine.resolve(db, definition, params, company_grants=grant)
    download = DownloadService(db).create(
        user_id=owner_user_id,
        kind="report_xlsx",
        filename=_export_filename(definition, ctx.period),
    )
    download_id = str(download.id)
    answer["download_id"] = download_id
    from app.models.base import get_brand_scope

    brand_scope = get_brand_scope(db)
    # CONTACT-BRAND-SCOPE: the worker opens a fresh session; unscoped sends nothing extra.
    scope_kwargs = {"brand_ids": sorted(brand_scope)} if brand_scope else {}
    try:
        queue_service.enqueue_job(
            generate_report_xlsx,
            download_id,
            REPORT_KEY,
            params,
            file_view.model_dump(mode="json"),
            owner_user_id,
            queue_name=settings.report_export_queue,
            job_timeout=600,
            company_grants=[company_id],
            **scope_kwargs,
        )
    except Exception:  # noqa: BLE001 - the text still goes; the file is said not to
        log.exception("sales_analysis: could not queue the workbook for %s", download_id)
        DownloadService(db).mark_failed(download_id, "Could not queue the export")
        db.commit()
        answer["status"] = "error"
        return answer
    sync_wait = _sync_wait_seconds(db)
    db.commit()
    return _Prepared(answer=answer, download_id=download_id,
                     resolved_contact_id=str(resolved), sync_wait_seconds=sync_wait)


def _ready_attachments(db: Session, download_id: str) -> Optional[List[Dict[str, str]]]:
    from app.services.scm import low_stock_report_service

    row = db.execute(text(
        "SELECT status, storage_provider, storage_key, filename FROM user_downloads WHERE id = :id"
    ), {"id": download_id}).mappings().first()
    if not row or row["status"] != "ready" or not row["storage_key"]:
        return None
    key = row["storage_key"]
    url = low_stock_report_service.attachment_url(row["storage_provider"], key)
    return [{
        "url": url,
        "filename": key.rsplit("/", 1)[-1] or row["filename"] or "sales.xlsx",
        "mimeType": MIME_XLSX,
        "attachmentType": "file",
    }]


def _finish(db: Session, prepared: _Prepared, snapshot: Optional[dict]) -> Dict[str, Any]:
    answer = dict(prepared.answer)
    if snapshot is not None and snapshot.get("status") == "failed":
        answer["status"] = "error"
        return answer
    try:
        ready = _ready_attachments(db, prepared.download_id)
    except Exception:  # noqa: BLE001 - no URL is a file not sent, never a 500
        log.exception("sales_analysis: no attachment URL for %s", prepared.download_id)
        answer["status"] = "error"
        return answer
    if ready:
        answer["attachments"] = ready
        return answer
    claimed = db.execute(text(
        "UPDATE user_downloads SET deliver_to_contact_id = :c "
        "WHERE id = :id AND status <> 'ready'"
    ), {"c": prepared.resolved_contact_id, "id": prepared.download_id}).rowcount
    db.commit()
    if not claimed:
        ready = _ready_attachments(db, prepared.download_id)
        if ready:
            answer["attachments"] = ready
            return answer
    answer["status"] = "pending"
    return answer


@router.get("/analysis")
async def sales_analysis(
    request: Request,
    rows: str = Query("channel"),
    cols: str = Query("year"),
    channel: Optional[str] = Query(None),
    basis: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    n: Optional[int] = Query(None),
    contact_id: str = Query(...),
    space_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_GATE),
):
    """The answer (`ready` with the file, `pending` with the file on its way, `error` when
    the file failed but the figures stand), or `clarify` / `refused` / `busy` with one line
    and no file. All HTTP 200 except the 403s and 422s, because each is something the bot
    can say."""
    if not request.headers.get("X-API-Key"):
        # The chatbot's route only (security review B2): see the module docstring.
        raise AppException(status_code=403, message="This route is for the chatbot only",
                           code="api_key_required")
    prepared = await asyncio.to_thread(
        _prepare, db,
        rows=rows, cols=cols, channel=channel, basis=basis, company=company,
        date_from=date_from, date_to=date_to, n=n,
        contact_id=contact_id, space_id=space_id, principal_id=str(current_user["id"]),
    )
    if isinstance(prepared, dict):
        return prepared
    try:
        snapshot = await asyncio.wait_for(
            _await_download(prepared.download_id), prepared.sync_wait_seconds
        )
    except asyncio.TimeoutError:
        snapshot = None
    except Exception:  # noqa: BLE001 - a failed poll falls through to the claim
        log.exception("sales_analysis: waiting on %s failed", prepared.download_id)
        snapshot = None
    return await asyncio.to_thread(_finish, db, prepared, snapshot)
