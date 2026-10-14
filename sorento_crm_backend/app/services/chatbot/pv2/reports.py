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

from pydantic import BaseModel, ConfigDict

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
    error: bool = False  # the call failed (not a refusal): the caller traces it as a fetch error
    v1_sales: bool = False  # answered by v1's delivery-order report (`crm_sales_report`)


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
_CHAT_ERROR_TEXT = "Could not run the chat report right now."
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
    `cleared` holding "report" drops the held spec first; "period" clears the held period.
    Never mutates `held`."""
    if "report" in cleared:
        held = None
    if held is None:
        return incoming
    if incoming is None:
        return held.model_copy(update={"period": None}, deep=True) if "period" in cleared else held
    update: dict[str, Any] = {k: getattr(incoming, k) for k in _SCALARS if getattr(incoming, k) is not None}
    if "period" in cleared and incoming.period is None:
        update["period"] = None
    if incoming.measures:
        update["measures"] = list(incoming.measures)
    if incoming.group_by:
        update["group_by"] = list(incoming.group_by)
    if incoming.filters:
        named = {f.dimension for f in incoming.filters}
        update["filters"] = [f for f in held.filters if f.dimension not in named] + list(incoming.filters)
    return held.model_copy(update=update, deep=True)


class DrillRefused(ValueError):
    """A picked row the report cannot narrow by; `line` is v1's own wording for it
    (`lanes/business/report_ask.py` NOT_A_SOURCE_LINES / NO_KEYS_LINE / TOO_MANY_LINE)."""

    def __init__(self, line: str) -> None:
        super().__init__(line)
        self.line = line


def drill_row(spec: Any, row: Any, *, incoming: Any = None) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    """"The 2nd one" (`row_pick` -> a LAST ANSWER ROWS option): the held spec narrowed to that
    row (a filter on its dimension, replacing any held one), grouped by the axis this turn names,
    else a total for the row. Returns the spec and the refs the filter rides on (live keys).
    Raises ValueError for an option that is not a report row, and `DrillRefused` (say its
    `line`) for a row with nothing to narrow by, as the v1 drill down says it."""
    from app.services.chatbot.lanes.business import report_ask
    from app.services.chatbot.pv2.schema import Filter

    payload = _get(row, "payload") or {}
    picked = payload.get(ROW_PAYLOAD) if isinstance(payload, Mapping) else None
    if not isinstance(picked, Mapping) or not picked.get("dimension"):
        raise ValueError("not a report row")
    dim, label = str(picked["dimension"]), str(picked.get("label") or "")
    keys = [str(k) for k in picked.get("keys") or []]
    plural = report_ask.AXIS_NOUNS.get(dim, (dim, dim + "s"))[1]
    if dim in report_ask.NOT_A_SOURCE_LINES:
        raise DrillRefused(report_ask.NOT_A_SOURCE_LINES[dim])
    if not keys:
        raise DrillRefused(report_ask.NO_KEYS_LINE.format(name=label, plural=plural))
    if len(keys) > report_ask.DRILL_MAX_KEYS:
        raise DrillRefused(report_ask.TOO_MANY_LINE.format(max=report_ask.DRILL_MAX_KEYS, plural=plural))
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


def _say(spec: Any, line: str, notes: Sequence[str] = ()) -> ReportPlan:
    return ReportPlan(status="say", spec=spec, reply=line, notes=list(notes))


def _fits(ds: rm.Dataset, spec: Any) -> bool:
    measures = [m for m in spec.measures if "/" not in m]
    dims = [*spec.group_by, *(f.dimension for f in spec.filters)]
    return all(m in ds.measures for m in measures) and all(d in ds.dimensions for d in dims)


def _dataset_for(spec: Any) -> Optional[rm.Dataset]:
    """The spec's dataset key; none named: the policy default when the spec's measure and
    dimension KEYS fit it, else the one dataset they do fit (a chat-only key such as `topic`
    or `asks`). Keys only, never words."""
    if spec.dataset:
        return rm.dataset(spec.dataset)
    default = rm.dataset(rp.default_for("dataset"))
    if default is not None and _fits(default, spec):
        return default
    fitting = [ds for ds in rm.DATASETS.values() if _fits(ds, spec)]
    return fitting[0] if len(fitting) == 1 else default


def _axis(ds: rm.Dataset, spec: Any) -> tuple[list[str], Optional[str]]:
    """The breakdown keys (a grain the dataset lists as an axis stands in for a missing
    group_by) and the "not available" line for a grain that cannot run."""
    groups = list(spec.group_by)
    if spec.grain is None:
        return groups, None
    axis = rm.GRAIN_AXES.get(ds.key, {}).get(spec.grain)
    if axis is None:
        return groups, f"Breakdown by {spec.grain} is not available yet."
    if not groups:
        return [axis], None
    if groups[0] != axis and axis not in groups:
        groups.append(axis)  # said as a second breakdown below
    return groups, None


def dealer_refusal(spec: Any) -> Optional[str]:
    """The line a customer-scoped (dealer) contact gets for a staff breakdown, from the spec's
    KEYS alone, or None. Call it BEFORE resolving any filter, so a dealer can never probe
    sales agent or location names (v1 `report_ask._dealer_outside`, 1b fix round F1);
    `plan_report(dealer=True)` applies the same check."""
    from app.services.chatbot.lanes.business import report_ask

    ds = _dataset_for(spec)
    if ds is None:
        return None
    groups, _ = _axis(ds, spec)
    if groups and groups[0] in ds.dimensions and groups[0] not in ds.dealer_keys:
        return report_ask.DEALER_REFUSAL
    if ds.key == "sales" and any(f.dimension in _DEALER_FILTERS for f in spec.filters):
        return report_ask.DEALER_REFUSAL
    return None


def _question(ds: rm.Dataset, missing: list[str]) -> tuple[str, str, list[ReportOption]]:
    """ONE question for every missing field (the policy table's lines, in its order), with
    picks when the missing fields are enumerable (measure and/or basis)."""
    lines = [rp.question_line(name, ds.key) for name in rp.FIELD_ORDER if name in missing]
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


def _is_uuid(value: str) -> bool:
    from app.services.chatbot.lanes.business.fetch import is_uuid

    return bool(is_uuid(value))


def _filter_values(ds: rm.Dataset, dim: rm.Dimension, keys: list[str]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The route params a filter's resolved keys ride on, `(None, None)` when the keys are not
    the kind the param takes (the resolver must settle them), or `(None, line)` to say."""
    from app.services.reports.ask import NO_AGENT_KEY

    param = dim.filter_param or ""
    if dim.key == "location" or param in ("subjects", "askers"):
        return {param: keys}, None  # codes, as the route takes them
    if dim.key == "product" and ds.key == "sales" and not any(_is_uuid(k) for k in keys):
        codes = keys
        if len(keys) == 1 and len(codes[0]) >= 3:
            return {"product_code": codes[0]}, None  # the sales report's prefix rule (v1 S4)
        return None, "Filtering by several product codes at once is not available yet."
    allowed = {NO_AGENT_KEY} if param in ("sales_agent_ids", "customer_agent_ids") else set()
    if not all(_is_uuid(k) or k in allowed for k in keys):
        return None, None
    return {param: keys}, None


def plan_report(
    spec: Any, *, refs: Mapping[str, Sequence[Any]], today: date, dealer: bool = False,
    delivered_total: bool = False,
) -> ReportPlan:
    """The held spec as `crm_report_ask` params (status run), or what to send instead: the one
    question for the missing fields (ask), a truthful line (say: a part the engine cannot run
    that would change the rows, a dealer's staff breakdown, an unknown dataset), or the filter
    dimensions the resolver has not settled yet (unresolved). Modifiers the engine cannot run
    (compare, ratio, grain, threshold, a second breakdown) run the rest and are named in a note.
    `refs` maps a filter dimension to its resolved refs (`key` live id or code, `label` name).

    `plan.spec` is the spec to HOLD next turn: every part this plan said is not available is
    taken out of it, so the line is said once, never on every follow-up. An `unresolved` plan
    holds the spec as it came (only the dataset filled), so those lines are said on the turn
    that runs; its `notes` are informational for the caller, not yet said."""
    from app.services.chatbot.lanes.business import report_ask
    from app.services.chatbot.pv2.schema import Period
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    if not spec.measures and _delivered_breakdown(spec, dealer):
        # v1's delivery-order report takes this breakdown itself: no measure / top-N question.
        base = plan_report(
            spec.model_copy(update={"group_by": [], "top_n": None}, deep=True),
            refs=refs, today=today, dealer=dealer, delivered_total=True,
        )
        if base.status == "run" and _delivered_route_ok(base.route_args):
            base.spec = base.spec.model_copy(update={"group_by": list(spec.group_by)}, deep=True)
            return base
    ds = _dataset_for(spec)
    if ds is None:
        return _say(spec.model_copy(update={"dataset": None}, deep=True), _NO_DATASET)
    notes: list[str] = []
    held: dict[str, Any] = {"dataset": ds.key}
    # An unresolved plan holds the spec UNSTRIPPED: its notes are said when it runs.
    original = spec.model_copy(update={"dataset": ds.key}, deep=True)

    # The axis: group_by, else a grain the dataset lists as an axis.
    groups, grain_line = _axis(ds, spec)
    if spec.grain is not None:
        held["grain"] = None
        if grain_line:
            notes.append(grain_line)
    group = groups[0] if groups else None
    dim = ds.dimensions.get(group) if group else None
    if group is not None and (dim is None or dim.group_by is None):
        held["group_by"] = []
        line = f"Breakdown by {rm.dimension_label(group)} is not available yet.\n{_catalogue_line(ds)}"
        return _say(spec.model_copy(update=held, deep=True), line, notes)
    held["group_by"] = [group] if group else []
    for extra in groups[1:]:
        notes.append(f"A second breakdown (by {rm.dimension_label(extra)}) is not available yet.")
    if spec.top_n_per_group is not None:
        notes.append("Top per group is not available yet.")
        held["top_n_per_group"] = None

    refusal = dealer_refusal(spec.model_copy(update={"dataset": ds.key, "grain": None, "group_by": held["group_by"]}))
    if dealer and refusal:
        kept = [f for f in spec.filters if not (ds.key == "sales" and f.dimension in _DEALER_FILTERS)]
        if group is not None and group not in ds.dealer_keys:
            held["group_by"] = []
        return _say(spec.model_copy(update={**held, "filters": kept}, deep=True), refusal, notes)

    # Filters: only `in` runs; anything else, or a dimension the dataset has no filter for,
    # would change the rows, so it is said, nothing runs, and the filter is not held.
    filter_args: dict[str, Any] = {}
    filter_parts: list[str] = []
    unresolved: list[str] = []
    for f in spec.filters:
        fdim = ds.dimensions.get(f.dimension)
        label = rm.dimension_label(f.dimension)
        line = None
        if fdim is None or fdim.filter_param is None:
            line = f"Filtering by {label} is not available yet."
        elif f.op == "not_in":
            line = f"Excluding {_PLURALS.get(f.dimension, label + 's')} is not available yet."
        elif f.op != "in":
            line = f"Filtering {label} by a number is not available yet."
        if line is not None:
            held["filters"] = [g for g in spec.filters if g is not f]
            return _say(spec.model_copy(update=held, deep=True), line, notes)
        assert fdim is not None
        if f.dimension == "channel":
            named = [k for k in dict.fromkeys(f.keys) if k in _CHANNEL_NAMES]
            if not named:
                unresolved.append(f.dimension)
            elif len(named) == 1:  # both channels is every channel: no filter
                filter_args["channel"] = named[0]
                filter_parts.append(f"{label} {_CHANNEL_NAMES[named[0]]}")
            continue
        got = [r for r in refs.get(f.dimension) or () if _get(r, "key")]
        keys = list(dict.fromkeys(str(_get(r, "key")) for r in got))
        values, line = _filter_values(ds, fdim, keys) if keys else (None, None)
        if line is not None:
            held["filters"] = [g for g in spec.filters if g is not f]
            return _say(spec.model_copy(update=held, deep=True), line, notes)
        if values is None:
            unresolved.append(f.dimension)
            continue
        filter_args.update(values)
        names = list(dict.fromkeys(str(_get(r, "label") or "") for r in got if _get(r, "label")))
        filter_parts.append(f"{label} {', '.join(names)}" if names else label)

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
            held["measures"] = [x for x in spec.measures if x != m and "/" not in x]
            line = f"{rm.measure_label(m).capitalize()} is not available for this report yet."
            return _say(spec.model_copy(update=held, deep=True), line, notes)
    for extra in measures[1:]:
        if not ds.both_measures_shown:
            notes.append(f"A second measure ({ds.measures[extra].label}) is not available yet.")
    held["measures"] = measures if ds.both_measures_shown else measures[:1]
    if spec.threshold is not None:
        notes.append("Thresholds are not available yet.")
        held["threshold"] = None
    if spec.compare_to is not None:
        notes.append(f"Comparison with {_COMPARE_WORDS[spec.compare_to]} is not available yet.")
        held["compare_to"] = None
        held["compare_period"] = None
    spec = spec.model_copy(update=held, deep=True)

    if group is None and not spec.filters and not dealer and not delivered_total and ds.key == "sales":
        # Not a valid ask: no axis and nothing to total (v1 `report_ask.settle`, :623-630).
        return _say(spec, report_ask.CATALOGUE_LINE, notes)

    # The policy table decides every field that is still missing: asked, or its default.
    ranking = dim is not None and not dim.trend
    missing: list[str] = []
    measure = measures[0] if measures else ds.default_measure
    if measure is None:
        if ranking:
            measure = rp.default_for("measure")
            if measure is None:
                missing.append("measure")
        else:
            measure = "amount"  # a total or a trend prints every measure; the route's default
    basis: Optional[str] = None
    if ds.bases:
        basis = spec.basis if spec.basis in ds.bases else rp.default_for("basis")
        if basis is None:
            missing.append("basis")
    elif measure is not None:
        basis = ds.measures[measure].basis
    period = spec.period
    span = period_dates(period, today)
    if span is None and rp.default_for("period"):
        period = Period(preset=rp.default_for("period"), n=None, date_from=None, date_to=None)
        span = period_dates(period, today)
    if span is None:
        missing.append("period")
    top_n: Optional[int] = None
    if ranking:
        top_n = spec.top_n if isinstance(spec.top_n, int) and spec.top_n >= 1 else rp.default_for("top_n")
        if top_n is not None and top_n > TOP_SELLING_N_CEILING:
            # v1 says the ceiling and asks again (`report_ask.top_n_from`), never a silent clamp.
            from app.services.chatbot.lanes.business import top_selling_ceiling_note

            notes.append(top_selling_ceiling_note({"top_n": top_n}) or "")
            top_n = None
        if top_n is None:
            missing.append("top_n")
    elif dim is not None and ds.key == "sales":
        top_n = report_ask.MONTH_TOP_N  # a month trend lists every month (`report_ask.py:70`)

    notes = [n for n in notes if n]
    if unresolved:
        return ReportPlan(status="unresolved", spec=original, notes=notes, unresolved=unresolved)
    if missing:
        missing = [name for name in rp.FIELD_ORDER if name in missing]
        text, about, options = _question(ds, missing)
        return ReportPlan(status="ask", spec=spec, notes=notes, reply=text, missing=missing, about=about, options=options)

    assert span is not None and measure is not None
    sort = spec.sort or "desc"
    args: dict[str, Any] = {"basis": basis, "measure": measure, "sort": sort}
    if ds.route_dataset:
        args["dataset"] = ds.route_dataset
    if dim is not None:
        args["group_by"] = dim.group_by
    if top_n is not None:
        args["top_n"] = top_n
    args.update(filter_args)
    args["date_from"], args["date_to"] = span[0].isoformat(), span[1].isoformat()

    shows_both = ds.both_measures_shown and not ranking and not measures
    parts: list[str] = []
    if not (shows_both and ds.bases and basis):
        parts.append("sales" if shows_both else ds.measures[measure].label)
    if ds.bases and basis:
        parts.append(ds.bases[basis])
    parts.extend(filter_parts)
    if dim is not None:
        parts.append(f"by {dim.label.lower()}")
    parts.append(period_label(period, today))
    if ranking and top_n is not None:
        parts.append(f"{'bottom' if sort == 'asc' else 'top'} {top_n}")
    header = "*Read as:* " + ", ".join(p for p in parts if p)
    return ReportPlan(status="run", spec=spec, route_args=args, header=header, notes=notes)


# --------------------------------------------------------------------------- #
# Resolution handed in by the resolver (contract section 4, `resolve_report_spec`)
# --------------------------------------------------------------------------- #


class ReportResolution(BaseModel):
    """What `Resolver.resolve_report_spec` returns: the refs per filter dimension (the
    `refs` `plan_report` takes), or the candidates to ask about. Labels are display names;
    keys stay internal."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["exact", "ambiguous", "none"]
    refs: dict[str, list[dict[str, Any]]] = {}
    options: list[dict[str, Any]] = []
    unresolved: list[str] = []
    phrase: Optional[str] = None


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


_SALES_REPORT_ARGS = frozenset(
    {"basis", "measure", "sort", "date_from", "date_to", "customer_ids", "product_code", "channel"}
)


def _delivered_route_ok(a: Mapping[str, Any]) -> bool:
    return (
        a.get("basis") == "delivered"
        and a.get("measure") == "amount"
        and a.get("dataset") in (None, "sales")
        and set(a) <= _SALES_REPORT_ARGS
    )


def _delivered_sales_report_args(
    plan: Any, fetch: Any, contact_id: Any, space_id: Any, detail_pick: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """`crm_sales_report` args exactly as v1's `entity_ids_transformer` builds them, for a
    delivered amount total (no ranking, no breakdown) over what that tool takes: customer,
    product code, channel and the date window. Anything else stays on `crm_report_ask`."""
    from app.services.chatbot import jsc

    a = plan.route_args
    if not _delivered_route_ok(a):
        return None
    semantic: dict[str, Any] = {"date_filter_start": a.get("date_from"), "date_filter_end": a.get("date_to")}
    if a.get("channel"):
        semantic["sales_channel"] = a["channel"]
    if detail_pick:
        semantic["outstanding_detail_pick"] = detail_pick  # v1 sets args["group_by"] from it
    entities = [{"entity_type": "customer", "uuid": str(c)} for c in a.get("customer_ids") or []]
    args = fetch.entity_ids_transformer(
        {"tool": "crm_sales_report", "entities": entities, "semantic_input": semantic},
        space_id=fetch.space_id_or_default(space_id),
    )
    if a.get("product_code"):
        args["product_code"] = a["product_code"]
    if a.get("channel"):
        args["channel"] = jsc.js_string(a["channel"])
    args["contact_id"] = jsc.nullish_str(contact_id).strip()
    return args


_DELIVERED_BREAKDOWNS = ("customer", "product", "delivery_order", "sales_agent")


def _delivered_breakdown(spec: Any, dealer: bool = False) -> Optional[str]:
    """The one breakdown of a delivered sales spec that v1's `crm_sales_report` takes."""
    group = list(getattr(spec, "group_by", None) or [])
    if (
        len(group) == 1
        and group[0] in _DELIVERED_BREAKDOWNS
        and not (dealer and group[0] == "sales_agent")  # a dealer gets PV2's refusal line
        and getattr(spec, "basis", None) == "delivered"
        and getattr(spec, "dataset", None) in (None, "sales")
        and getattr(spec, "top_n", None) is None
    ):
        return group[0]
    return None


def _delivered_reply(
    plan: Any, fetch: Any, mcp_call: Any, contact_id: Any, space_id: Any, detail_pick: Optional[str]
) -> Optional[ReportReply]:
    """The reply of v1's delivery-order sales report, with its breakdown offer as the pending;
    None when the plan is not one that report takes."""
    from app.services.chatbot.lanes.business import _McpSeam

    sales_args = _delivered_sales_report_args(plan, fetch, contact_id, space_id, detail_pick)
    if sales_args is None:
        return None
    envelope = fetch.call_tool("crm_sales_report", sales_args, mcp=_McpSeam(mcp_call))
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), str):
        return ReportReply(text=fetch._REPORT_ASK_ERROR_TEXT, plan=plan, error=True)
    out = fetch._sales_report_output(envelope, {"tool": "crm_sales_report"})
    text = "\n".join([plan.header, *plan.notes, out["response"]])
    ask = out.get("outstanding_ask") or {}
    rows = ask.get("last_result_set") or []
    pending = None
    if rows:
        pending = {
            "kind": "clarify_report_breakdown",
            "asked": (ask.get("filters") or {}).get("offer_text") or "",
            "options": [
                {
                    "key": str(row.get("idx")),
                    "label": str(row.get("label")),
                    "payload": {"report": {"group_by": [row.get("value")]}},
                }
                for row in rows
            ],
        }
    return ReportReply(text=text, plan=plan, pending=pending, v1_sales=True)


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
    `contact_id` / `space_id`, passed exactly as `fetch.entity_ids_transformer` passes them.
    An `unresolved` plan returns empty text: the caller clarifies those dimensions through the
    resolver. An exception from `mcp_call` propagates: the caller (`turn.run_turn_v2`) catches
    it as v1's `run_fetch` does. A failed call sets `error` (a route refusal is an answer)."""
    from app.services.chatbot import jsc
    from app.services.chatbot.lanes.business import _McpSeam
    from app.services.chatbot.lanes.business import fetch
    from app.services.chatbot.lanes.business import report_ask

    detail_pick = _delivered_breakdown(spec, dealer)
    if detail_pick:
        # A delivered breakdown v1's report takes runs there, never asking a top N (v1 does not).
        base = spec.model_copy(update={"group_by": [], "top_n": None}, deep=True)
        plan = plan_report(base, refs=refs, today=today, dealer=dealer, delivered_total=True)
        if plan.status == "run":
            plan.spec = plan.spec.model_copy(update={"group_by": list(spec.group_by)}, deep=True)
            reply = _delivered_reply(plan, fetch, mcp_call, contact_id, space_id, detail_pick)
            if reply is not None:
                return reply
    plan = plan_report(spec, refs=refs, today=today, dealer=dealer)
    if plan.status == "ask":
        pending = {
            "kind": f"clarify_{plan.about}",
            "asked": plan.reply,
            "options": [{"key": o.key, "label": o.label, "payload": o.payload} for o in plan.options],
        }
        return ReportReply(text="\n".join([*plan.notes, plan.reply or ""]), plan=plan, pending=pending)
    if plan.status != "run":
        return ReportReply(text="\n".join([*plan.notes, plan.reply or ""]) if plan.reply else "", plan=plan)

    reply = _delivered_reply(plan, fetch, mcp_call, contact_id, space_id, None)
    if reply is not None:
        return reply

    args = {
        "view": "render",
        **plan.route_args,
        "contact_id": jsc.nullish_str(contact_id).strip(),
        "space_id": fetch.space_id_or_default(space_id),
    }
    envelope = fetch.call_tool(report_ask.TOOL, args, mcp=_McpSeam(mcp_call))
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), str):
        refusal = report_ask.error_line(envelope)
        if refusal:
            return ReportReply(text=refusal, plan=plan)
        failed = _CHAT_ERROR_TEXT if plan.route_args.get("dataset") == "chat" else fetch._REPORT_ASK_ERROR_TEXT
        return ReportReply(text=failed, plan=plan, error=True)
    out = fetch._report_ask_output(envelope, {"semantic_input": {"report_ask_args": plan.route_args}})
    text = "\n".join([plan.header, *plan.notes, out["response"]])
    rows = _rows(envelope) if isinstance(envelope, Mapping) else []
    return ReportReply(text=text, plan=plan, frame=out.get("sales_ranking_frame"), rows=rows)
