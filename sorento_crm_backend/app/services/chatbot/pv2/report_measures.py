"""PV2-REPORTS: the report spec's keys as data (contract `CONTRACT-PV2-13oct.md` section 5).

One table maps the parser v2 `ReportSpec` keys (dataset, measure, basis, dimension) onto the
existing `crm_report_ask` route's params (`reports/ask.py` CATALOGUE, DIMENSIONS, FILTERS,
CHAT_DIMENSIONS, CHAT_MEASURES). Nothing here runs a query and nothing reads user text: the model
returns these keys (printed in the index by `index_lines`), code looks them up exactly.

Keys the model may name that the engine cannot run TONIGHT are listed too (`NOT_YET`), so a
"by region" or "compare with last year" reaches code as a key and is answered with a truthful
"not available yet" line instead of being nulled by the schema and silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.services.reports import ask


@dataclass(frozen=True)
class Dimension:
    key: str  # the index key the model returns (`ReportSpec.group_by` / `Filter.dimension`)
    label: str  # as the "Read as" header says it
    group_by: Optional[str]  # the route's `group_by` value; None = filter only
    filter_param: Optional[str]  # the route's filter param; None = not a filter
    trend: bool = False  # a run of time: every bucket is listed, never "how many?"


@dataclass(frozen=True)
class Measure:
    key: str
    label: str  # as the header says it
    basis: Optional[str] = None  # a measure that fixes its basis (chat failed -> turns)


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    measures: dict[str, Measure]
    bases: dict[str, str]  # basis key -> label; empty = the measure decides (chat)
    dimensions: dict[str, Dimension]
    dealer_keys: frozenset[str]  # what a customer-scoped contact may group or filter by
    both_measures_shown: bool = False  # the reply prints every measure on each row (sales)
    default_measure: Optional[str] = None  # the lane's own default when none is named (chat)
    route_dataset: Optional[str] = None  # the route's `dataset` param; None = the route default
    extra_args: dict[str, str] = field(default_factory=dict)


#: The sales filter words -> the route's params (`api/v1/order_management/report_ask.py`
#: `_FILTER_PARAMS`). Location rides as warehouse codes, channel as one value.
_SALES_FILTER_PARAMS = {
    "customer": "customer_ids",
    "product": "product_ids",
    "brand": "brand_ids",
    "category": "category_ids",
    "sales_agent": "sales_agent_ids",
    "customer_agent": "customer_agent_ids",
    "location": "warehouse_codes",
    "channel": "channel",
}
_CHAT_FILTER_PARAMS = {
    "subject": "subjects",
    "product": "product_ids",
    "customer": "customer_ids",
    "asker": "askers",
    "category": "category_ids",
    "brand": "brand_ids",
}


def _dimensions(groups: tuple[str, ...], labels: dict[str, str], params: dict[str, str], trends) -> dict[str, Dimension]:
    keys = list(dict.fromkeys([*groups, *params]))
    return {
        k: Dimension(
            key=k,
            label=labels.get(k, k.replace("_", " ").capitalize()),
            group_by=k if k in groups else None,
            filter_param=params.get(k),
            trend=k in trends,
        )
        for k in keys
    }


SALES = Dataset(
    key="sales",
    label="Sales",
    measures={"amount": Measure("amount", "sales amount"), "qty": Measure("qty", "sales quantity")},
    bases={b: ask.CATALOGUE["sales"][b]["label"] for b in ask.CATALOGUE["sales"]},
    dimensions=_dimensions(ask.DIMENSIONS, ask.FILTER_LABELS, _SALES_FILTER_PARAMS, frozenset({"month"})),
    dealer_keys=ask.DEALER_KEYS,
    both_measures_shown=True,
)

CHAT = Dataset(
    key="chat",
    label="Chat questions",
    measures={
        "asks": Measure("asks", "asks", basis="asks"),
        "failed": Measure("failed", "failed turns", basis="turns"),
        "failure_rate": Measure("failure_rate", "failure rate", basis="turns"),
        "contacts": Measure("contacts", "contacts", basis="asks"),
    },
    bases={},
    dimensions=_dimensions(
        ask.CHAT_DIMENSIONS, {**ask.CHAT_GROUP_LABELS, **ask.CHAT_FILTER_LABELS}, _CHAT_FILTER_PARAMS, ask.CHAT_TRENDS
    ),
    dealer_keys=ask.CHAT_DEALER_KEYS,
    default_measure="asks",
    route_dataset="chat",
)

DATASETS: dict[str, Dataset] = {SALES.key: SALES, CHAT.key: CHAT}

#: Index keys the model may name that tonight's engine cannot run: said, never dropped.
#: Dimension keys (a group_by or a filter) and the label each "not available yet" line uses.
NOT_YET_DIMENSIONS = {"region": "region", "state": "state"}
#: `ReportSpec.grain` values the engine runs as a trend axis, per dataset (a grain names the
#: same buckets an existing dimension lists); any other grain is "not available yet".
GRAIN_AXES = {"sales": {"month": "month"}, "chat": {"day": "day", "week": "week", "month": "month"}}


def dataset(key: Optional[str]) -> Optional[Dataset]:
    return DATASETS.get(key or "")


def dimension_label(key: str) -> str:
    """A dimension's label in any dataset, for a "not available" line; the key itself never."""
    for ds in DATASETS.values():
        if key in ds.dimensions:
            return ds.dimensions[key].label.lower()
    return NOT_YET_DIMENSIONS.get(key, "that breakdown")


def measure_label(key: str) -> str:
    for ds in DATASETS.values():
        if key in ds.measures:
            return ds.measures[key].label
    return "that measure"


def index_lines() -> list[str]:
    """The `## REPORTS` section of the parser v2 index: keys and labels only, fixed order,
    no counts, no ids, no dates (contract section 2). Stable for a given code version."""
    lines: list[str] = []
    for ds in DATASETS.values():
        measures = ", ".join(f"{m.key} ({m.label})" for m in ds.measures.values())
        lines.append(f"dataset {ds.key} = {ds.label} | measures: {measures}")
        if ds.bases:
            lines.append(f"  basis: {', '.join(ds.bases)}")
        groups = ", ".join(d.key for d in ds.dimensions.values() if d.group_by)
        filters = ", ".join(d.key for d in ds.dimensions.values() if d.filter_param)
        lines.append(f"  group_by: {groups}")
        lines.append(f"  filters: {filters}")
    lines.append(
        "not available yet (still return the key, code says so): dimension "
        + ", ".join(NOT_YET_DIMENSIONS)
        + "; compare_to; ratio measures a/b; grain other than a listed group_by; threshold; "
        "filter op not_in/gt/lt/between; a second group_by"
    )
    return lines
