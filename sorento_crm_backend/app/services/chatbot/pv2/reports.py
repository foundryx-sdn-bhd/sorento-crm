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


_CHANNEL_NAMES = {"dealer": "Dealer", "project": "Project team"}
#: The option labels for the measure question's picks (`ranking_ask.METRIC_LINE` words).
_OPTION_MEASURES = {"amount": "Amount", "qty": "Quantity"}
_PERIOD_WORDS = {
    "today": "today",
    "yesterday": "yesterday",
    "this_week": "this week",
    "last_week": "last week",
    "this_month": "this month",
    "last_month": "last month",
    "this_quarter": "this quarter",
    "last_quarter": "last quarter",
    "this_year": "this year",
    "last_year": "last year",
    "ytd": "this year to date",
}
_COMPARE_WORDS = {"last_year": "last year", "prev_period": "the previous period", "custom": "another period"}
_PLURALS = {"category": "categories"}
_DEALER_FILTERS = frozenset({"sales_agent", "customer_agent", "location", "channel"})
_NO_DATASET = "That report is not available yet. I can report on sales and on chat questions."


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _day(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def _month_start(d: date, back: int = 0) -> date:
    months = d.year * 12 + (d.month - 1) - back
    return date(months // 12, months % 12 + 1, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def period_dates(period: Any, today: date) -> Optional[tuple[date, date]]:
    """The period's first and last day, Malaysia `today`; None when it names no period yet.
    Explicit dates win over a preset (contract section 1). A start alone runs to today and an
    end alone is no period, as `report_ask.period_from` reads the v1 parser's dates."""
    if period is None:
        return None
    first, last = _get(period, "date_from"), _get(period, "date_to")
    if first is not None:
        last = last if last is not None else max(first, today)
        return (first, last) if first <= last else (last, first)
    if last is not None:
        return None
    preset, n = _get(period, "preset"), _get(period, "n")
    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "this_week":
        return today - timedelta(days=today.weekday()), today
    if preset == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if preset == "this_month":
        return _month_start(today), today
    if preset == "last_month":
        start = _month_start(today, 1)
        return start, _month_end(start)
    if preset in ("this_quarter", "last_quarter"):
        start = _month_start(today, (today.month - 1) % 3)
        if preset == "this_quarter":
            return start, today
        prev = _month_start(start, 3)
        return prev, start - timedelta(days=1)
    if preset in ("this_year", "ytd"):
        return date(today.year, 1, 1), today
    if preset == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if preset in ("last_n_days", "last_n_months"):
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return None
        if preset == "last_n_days":
            return today - timedelta(days=n - 1), today
        return _month_start(today, n), _month_start(today) - timedelta(days=1)
    return None


def period_label(period: Any, today: date) -> str:
    """The period as the header says it: words for a preset, days for explicit dates."""
    if period is None:
        return ""
    if _get(period, "date_from") is None and _get(period, "date_to") is None:
        preset, n = _get(period, "preset"), _get(period, "n")
        if preset in _PERIOD_WORDS:
            return _PERIOD_WORDS[preset]
        if preset == "last_n_days" and n:
            return f"last {n} days" if n != 1 else "last 1 day"
        if preset == "last_n_months" and n:
            return f"last {n} months" if n != 1 else "last 1 month"
    span = period_dates(period, today)
    return f"{_day(span[0])} to {_day(span[1])}" if span else ""


# --------------------------------------------------------------------------- #
# Held-spec edits (follow-ups): field by field, never a fresh spec
# --------------------------------------------------------------------------- #

_SCALARS = (
    "dataset", "period", "sort", "top_n", "basis", "compare_to", "compare_period", "grain", "threshold",
    "top_n_per_group",
)


def edit_report(held: Any, incoming: Any, *, cleared: Sequence[str] = ()) -> Any:
    """The held spec with this turn's report fields applied (contract section 5): a non-null
    scalar replaces, non-empty measures / group_by replace ("by qty", "by salesman instead"),
    a filter replaces the held filter on its own dimension and adds otherwise ("only Johor").
    `cleared` holding "report" drops the held spec first. Never mutates `held`."""
    if "report" in cleared:
        held = None
    if held is None:
        return incoming
    if incoming is None:
        return held
    update: dict[str, Any] = {k: getattr(incoming, k) for k in _SCALARS if getattr(incoming, k) is not None}
    if incoming.measures:
        update["measures"] = list(incoming.measures)
    if incoming.group_by:
        update["group_by"] = list(incoming.group_by)
    if incoming.filters:
        named = {f.dimension for f in incoming.filters}
        update["filters"] = [f for f in held.filters if f.dimension not in named] + list(incoming.filters)
    return held.model_copy(update=update, deep=True)


def drill_row(spec: Any, row: Any, *, incoming: Any = None) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    """"The 2nd one" (`row_pick` -> a LAST ANSWER ROWS option): the held spec narrowed to that
    row (a filter on its dimension, replacing any held one), grouped by the axis this turn names,
    else a total for the row. Returns the spec and the refs the filter rides on (live keys)."""
    from app.services.chatbot.pv2.schema import Filter

    payload = _get(row, "payload") or {}
    picked = payload.get(ROW_PAYLOAD) if isinstance(payload, Mapping) else None
    if not isinstance(picked, Mapping) or not picked.get("dimension"):
        raise ValueError("not a report row")
    dim, label = str(picked["dimension"]), str(picked.get("label") or "")
    keys = [str(k) for k in picked.get("keys") or []]
    filters = [f for f in spec.filters if f.dimension != dim]
    filters.append(Filter(dimension=dim, op="in", keys=[label], values=[]))
    group_by = list(incoming.group_by) if incoming is not None and incoming.group_by else []
    new = spec.model_copy(update={"filters": filters, "group_by": group_by}, deep=True)
    kind = REF_KINDS.get(dim, dim)
    return new, {dim: [{"kind": kind, "key": k, "label": label} for k in keys]}


# --------------------------------------------------------------------------- #
# Plan: spec -> route args, or the one line / one question instead
# --------------------------------------------------------------------------- #


def _catalogue_line(ds: rm.Dataset) -> str:
    if ds.key == "chat":
        from app.services.chatbot.lanes.business import chat_report

        return chat_report.CATALOGUE_LINE
    from app.services.chatbot.lanes.business import report_ask

    return report_ask.CATALOGUE_LINE


def _say(spec: Any, line: str) -> ReportPlan:
    return ReportPlan(status="say", spec=spec, reply=line)


def _question(ds: rm.Dataset, missing: list[str]) -> tuple[str, str, list[ReportOption]]:
    """ONE question for every missing field (the policy table's lines, in its order), with
    picks when the missing fields are enumerable (measure and/or basis)."""
    lines = [rp.QUESTION_LINES[name] for name in rp.FIELD_ORDER if name in missing]
    about = "report_measure" if ("measure" in missing or "basis" in missing) else "report_period"
    measures = [m for m in ds.measures if m in _OPTION_MEASURES] if "measure" in missing else [None]
    bases = list(ds.bases) if "basis" in missing else [None]
    options: list[ReportOption] = []
    if about == "report_measure":
        for m in measures:
            for b in bases:
                report: dict[str, Any] = {}
                labels: list[str] = []
                if m is not None:
                    report["measures"] = [m]
                    labels.append(_OPTION_MEASURES[m])
                if b is not None:
                    report["basis"] = b
                    label = ds.bases[b]
                    labels.append(label if labels else label.capitalize())
                key = "_".join(x for x in (m, b) if x)
                options.append(ReportOption(key=key, label=", ".join(labels), payload={"report": report}))
    return "\n".join(lines), about, options


def plan_report(
    spec: Any, *, refs: Mapping[str, Sequence[Any]], today: date, dealer: bool = False
) -> ReportPlan:
    """The held spec as `crm_report_ask` params (status run), or what to send instead: the one
    question for the missing fields (ask), a truthful line (say: a part the engine cannot run
    that would change the rows, a dealer's staff breakdown, an unknown dataset), or the filter
    dimensions the resolver has not settled yet (unresolved). Modifiers the engine cannot run
    (compare, ratio, grain, threshold, a second breakdown) run the rest and are named in a note.
    `refs` maps a filter dimension to its resolved refs (`key` live id or code, `label` name)."""
    from app.services.chatbot.lanes.business import report_ask
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    ds = rm.dataset(spec.dataset or rp.default_for("dataset"))
    if ds is None:
        return _say(spec, _NO_DATASET)
    spec = spec.model_copy(update={"dataset": ds.key}, deep=True)
    notes: list[str] = []

    # The axis: group_by, else a grain the dataset lists as an axis.
    groups = list(spec.group_by)
    if spec.grain is not None:
        axis = rm.GRAIN_AXES.get(ds.key, {}).get(spec.grain)
        if axis is not None and not groups:
            groups = [axis]
        elif axis is None or groups[0] != axis:
            notes.append(f"Breakdown by {spec.grain} is not available yet.")
    group = groups[0] if groups else None
    dim = ds.dimensions.get(group) if group else None
    if group is not None and (dim is None or dim.group_by is None):
        return _say(spec, f"Breakdown by {rm.dimension_label(group)} is not available yet.\n{_catalogue_line(ds)}")
    for extra in groups[1:]:
        notes.append(f"A second breakdown (by {rm.dimension_label(extra)}) is not available yet.")
    if spec.top_n_per_group is not None:
        notes.append("Top per group is not available yet.")

    # Filters: only `in` runs; anything else, or a dimension the dataset has no filter for,
    # would change the rows, so it is said and nothing runs.
    filter_args: dict[str, Any] = {}
    filter_parts: list[str] = []
    unresolved: list[str] = []
    for f in spec.filters:
        fdim = ds.dimensions.get(f.dimension)
        label = rm.dimension_label(f.dimension)
        if fdim is None or fdim.filter_param is None:
            return _say(spec, f"Filtering by {label} is not available yet.")
        if f.op == "not_in":
            return _say(spec, f"Excluding {_PLURALS.get(f.dimension, label + 's')} is not available yet.")
        if f.op != "in":
            return _say(spec, f"Filtering {label} by a number is not available yet.")
        if f.dimension == "channel":
            named = [k for k in dict.fromkeys(f.keys) if k in _CHANNEL_NAMES]
            if not named:
                unresolved.append(f.dimension)
            elif len(named) == 1:  # both channels is every channel: no filter
                filter_args["channel"] = named[0]
                filter_parts.append(f"{label} {_CHANNEL_NAMES[named[0]]}")
            continue
        got = [r for r in refs.get(f.dimension) or () if _get(r, "key")]
        if not got:
            unresolved.append(f.dimension)
            continue
        filter_args[fdim.filter_param] = list(dict.fromkeys(str(_get(r, "key")) for r in got))
        names = list(dict.fromkeys(str(_get(r, "label") or "") for r in got if _get(r, "label")))
        filter_parts.append(f"{label} {', '.join(names)}" if names else label)

    if dealer and (
        (group is not None and group not in ds.dealer_keys)
        or (ds.key == "sales" and any(f.dimension in _DEALER_FILTERS for f in spec.filters))
    ):
        return _say(spec, report_ask.DEALER_REFUSAL)

    # Measures: one runs; a ratio is said; a second is said unless the reply prints it anyway.
    measures: list[str] = []
    for m in spec.measures:
        if "/" in m:
            if "Ratios are not available yet." not in notes:
                notes.append("Ratios are not available yet.")
        elif m in ds.measures:
            if m not in measures:
                measures.append(m)
        else:
            return _say(spec, f"{rm.measure_label(m).capitalize()} is not available for this report yet.")
    for extra in measures[1:]:
        if not ds.both_measures_shown:
            notes.append(f"A second measure ({ds.measures[extra].label}) is not available yet.")
    if spec.threshold is not None:
        notes.append("Thresholds are not available yet.")
    if spec.compare_to is not None:
        notes.append(f"Comparison with {_COMPARE_WORDS[spec.compare_to]} is not available yet.")

    ranking = dim is not None and not dim.trend
    missing: list[str] = []
    measure = measures[0] if measures else None
    if measure is None:
        if ds.default_measure:
            measure = ds.default_measure
        elif ranking:
            missing.append("measure")
        else:
            measure = "amount"  # a total or a trend prints every measure; the route's default
    basis: Optional[str] = None
    if ds.bases:
        basis = spec.basis if spec.basis in ds.bases else None
        if basis is None:
            missing.append("basis")
    elif measure is not None:
        basis = ds.measures[measure].basis
    span = period_dates(spec.period, today)
    if span is None:
        missing.append("period")
    top_n: Optional[int] = None
    if ranking:
        if isinstance(spec.top_n, int) and spec.top_n >= 1:
            top_n = spec.top_n
            if top_n > TOP_SELLING_N_CEILING:
                from app.services.chatbot.lanes.business import top_selling_ceiling_note

                notes.append(top_selling_ceiling_note({"top_n": top_n}) or "")
                top_n = TOP_SELLING_N_CEILING
        else:
            missing.append("top_n")
    elif dim is not None and ds.key == "sales":
        top_n = report_ask.MONTH_TOP_N  # a month trend lists every month (`report_ask.py:70`)

    if unresolved:
        return ReportPlan(status="unresolved", spec=spec, notes=notes, unresolved=unresolved)
    if missing:
        missing = [name for name in rp.FIELD_ORDER if name in missing]
        text, about, options = _question(ds, missing)
        return ReportPlan(status="ask", spec=spec, notes=notes, reply=text, missing=missing, about=about, options=options)

    assert span is not None and measure is not None
    sort = spec.sort or "desc"
    args: dict[str, Any] = {"basis": basis, "measure": measure, "sort": sort}
    if ds.route_dataset:
        args["dataset"] = ds.route_dataset
    if group is not None:
        args["group_by"] = dim.group_by if dim else group
    if top_n is not None:
        args["top_n"] = top_n
    args.update(filter_args)
    args["date_from"], args["date_to"] = span[0].isoformat(), span[1].isoformat()

    shows_both = ds.both_measures_shown and not ranking and not measures
    parts = ["sales" if shows_both else ds.measures[measure].label]
    if ds.bases and basis:
        parts.append(ds.bases[basis])
    parts.extend(filter_parts)
    if dim is not None:
        parts.append(f"by {dim.label.lower()}")
    parts.append(period_label(spec.period, today))
    if ranking and top_n is not None:
        parts.append(f"{'bottom' if sort == 'asc' else 'top'} {top_n}")
    header = "*Read as:* " + ", ".join(p for p in parts if p)
    return ReportPlan(status="run", spec=spec, route_args=args, header=header, notes=[n for n in notes if n])


# --------------------------------------------------------------------------- #
# Answer: the one route call, the route's own presenter text
# --------------------------------------------------------------------------- #


def _rows(envelope: Mapping[str, Any]) -> list[ReportOption]:
    drill = envelope.get("drill_rows")
    if not isinstance(drill, Mapping) or not drill.get("group_by"):
        return []
    dim = str(drill["group_by"])
    out: list[ReportOption] = []
    for row in drill.get("rows") or []:
        name = str(_get(row, "name") or "")
        if not name:
            continue
        keys = [str(k) for k in _get(row, "keys") or []]
        out.append(ReportOption(key=name, label=name, payload={ROW_PAYLOAD: {"dimension": dim, "keys": keys, "label": name}}))
    return out


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
    """Plan, then (status run) ONE `crm_report_ask` call through the lane's read-only egress
    (`fetch.call_tool`) and the route's presenter text, under the `*Read as:*` header and the
    notes. Every gate (grant, audience, company, location policy) is the route's, from
    `contact_id`, exactly as the v1 lanes pass it. An `unresolved` plan returns empty text: the
    caller clarifies those dimensions through the resolver."""
    from app.services.chatbot.lanes.business import _McpSeam
    from app.services.chatbot.lanes.business import fetch
    from app.services.chatbot.lanes.business import report_ask

    plan = plan_report(spec, refs=refs, today=today, dealer=dealer)
    if plan.status == "ask":
        pending = {
            "kind": f"clarify_{plan.about}",
            "asked": plan.reply,
            "options": [{"key": o.key, "label": o.label, "payload": o.payload} for o in plan.options],
        }
        return ReportReply(text=plan.reply or "", plan=plan, pending=pending)
    if plan.status != "run":
        return ReportReply(text=plan.reply or "", plan=plan)

    args = {"view": "render", **plan.route_args, "contact_id": contact_id, "space_id": space_id}
    envelope = fetch.call_tool(report_ask.TOOL, args, mcp=_McpSeam(mcp_call))
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), str):
        return ReportReply(text=report_ask.error_line(envelope) or fetch._REPORT_ASK_ERROR_TEXT, plan=plan)
    out = fetch._report_ask_output(envelope, {"semantic_input": {"report_ask_args": plan.route_args}})
    text = "\n".join([plan.header, *plan.notes, out["response"]])
    rows = _rows(envelope) if isinstance(envelope, Mapping) else []
    return ReportReply(text=text, plan=plan, frame=out.get("sales_ranking_frame"), rows=rows)
