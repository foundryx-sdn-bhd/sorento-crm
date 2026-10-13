"""PV2-REPORTS: one report spec onto the existing report engine (contract section 5, Reports).

The parser v2 returns ONE `ReportSpec` (`pv2/schema.py`); this module turns it, with the refs
the resolver settled, into the params of the existing `crm_report_ask` route
(`api/v1/order_management/report_ask.py` -> `reports/ask.py:202 run_ask` -> `reports/engine.py`)
and answers with that route's own presenter text (`lanes/business/fetch.py _report_ask_output`).
No new report code path, no new formatter: the only new text is the `*Read as:*` header line, the
one combined question for missing fields (today's question lines, `report_policy.py`) and the
"not available yet" lines naming each part the engine cannot run tonight.

Pure planning (`plan_report`, `edit_report`, `drill_row`) is separate from the one tool call
(`answer_report`), so the planning is table-tested without a database or a model.

Rules this module holds (contract section 5 + owner 13 Oct): code never reads the user's words
(the model returns index keys; code looks them up exactly); nothing missing is guessed (asked, per
the policy table); nothing unsupported is silently dropped (said, by name); no key, code or id
appears in a reply line (labels and display names only).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Literal, Mapping, Optional, Sequence

from app.services.chatbot.pv2 import report_measures as rm
from app.services.chatbot.pv2 import report_policy as rp

#: The dimension -> the entity kind its refs are held under (`Held.refs`); others are the same.
REF_KINDS = {"location": "warehouse"}

#: A drill row's payload key (`Option.payload`), read back by `drill_row`.
ROW_PAYLOAD = "report_row"

PlanStatus = Literal["run", "ask", "say", "unresolved"]


@dataclass
class ReportOption:
    """Same fields as `pv2/state.py Option` (CARRY): `key` internal, `label` shown."""

    key: str
    label: str
    payload: dict[str, Any]


@dataclass
class ReportPlan:
    status: PlanStatus
    spec: Any  # the spec as it is held next turn (dataset filled from the policy)
    route_args: dict[str, Any] = field(default_factory=dict)  # run: `crm_report_ask` params
    header: str = ""  # run: "*Read as:* ..."
    notes: list[str] = field(default_factory=list)  # "not available yet" lines, the ceiling note
    reply: Optional[str] = None  # say: the line; ask: the question
    missing: list[str] = field(default_factory=list)  # ask: the policy fields asked
    about: Optional[str] = None  # ask: `Clarify.about` (report_measure | report_period)
    options: list[ReportOption] = field(default_factory=list)  # ask: picks, when enumerable
    unresolved: list[str] = field(default_factory=list)  # unresolved: filter dimensions with no refs


@dataclass
class ReportReply:
    text: str
    plan: ReportPlan
    frame: Optional[dict[str, Any]] = None  # the args that ran (+ `shown`), v1 Focus compatible
    rows: list[ReportOption] = field(default_factory=list)  # LAST ANSWER ROWS candidates
    pending: Optional[dict[str, Any]] = None  # ask: {kind, asked, options} for a PendingQuestion


def period_dates(period: Any, today: date) -> Optional[tuple[date, date]]:
    raise NotImplementedError


def period_label(period: Any, today: date) -> str:
    raise NotImplementedError


def edit_report(held: Any, incoming: Any, *, cleared: Sequence[str] = ()) -> Any:
    raise NotImplementedError


def drill_row(spec: Any, row: Any, *, incoming: Any = None) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    raise NotImplementedError


def plan_report(
    spec: Any, *, refs: Mapping[str, Sequence[Any]], today: date, dealer: bool = False
) -> ReportPlan:
    raise NotImplementedError


def answer_report(
    spec: Any,
    *,
    refs: Mapping[str, Sequence[Any]],
    today: date,
    mcp_call: Callable[[str, dict[str, Any]], Any],
    contact_id: Optional[str],
    space_id: Optional[str],
    dealer: bool = False,
) -> ReportReply:
    raise NotImplementedError
