"""Parser v2 OUTPUT: the single declaration (CONTRACT-PV2-13oct.md section 1).

One turn-act enum everywhere (asks, fixtures, traces, eval). The model returns INDEX keys;
code exact-matches keys and never reads the user's words (owner rule 13 Oct). A key the
pinned index does not list becomes null here, and its phrase is logged by the caller.

`PV2_JSON_SCHEMA` is the strict json_schema sent on the parser call (same path as
`head/parser.py`'s `PARSE_OUTPUT_JSON_SCHEMA`): every property required, nullable groups
emit null, no extra keys.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

TurnAct = Literal[
    "answers_bot_question", "refines_held", "new_topic", "switch_back", "small_talk", "unclear"
]
Op = Literal["set", "add", "remove", "clear"]
Comparator = Literal["eq", "lt", "gt", "between", "about"]
ClearableField = Literal["attachment_type", "period", "report", "specs"]

TURN_ACTS: tuple[str, ...] = TurnAct.__args__  # type: ignore[attr-defined]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quantity(_Strict):
    number: float
    unit: str | None


class Entity(_Strict):
    kind: str
    key: str | None
    phrase: str | None
    op: Op
    refers_to_held: bool
    quantity: Quantity | None = None
    doc_number: str | None = None


class Spec(_Strict):
    attribute: str | None
    value_key: str | None
    number: float | None
    number_to: float | None
    unit: str | None
    comparator: Comparator
    op: Op
    phrase: str | None


class Period(_Strict):
    preset: (
        Literal[
            "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
            "this_quarter", "last_quarter", "this_year", "last_year", "ytd",
            "last_n_days", "last_n_months",
        ]
        | None
    )
    n: int | None
    date_from: date | None
    date_to: date | None


class Answer(_Strict):
    option_key: str | None
    free_value: str | None


class Filter(_Strict):
    dimension: str
    op: Literal["in", "not_in", "gt", "lt", "between"]
    keys: list[str]
    values: list[float]


class ReportSpec(_Strict):
    dataset: str | None
    measures: list[str]
    group_by: list[str]
    top_n_per_group: int | None
    filters: list[Filter]
    period: Period | None
    compare_to: Literal["prev_period", "last_year", "custom"] | None
    compare_period: Period | None
    grain: Literal["day", "week", "month", "quarter", "year"] | None
    threshold: float | None
    sort: Literal["desc", "asc"] | None
    top_n: int | None
    basis: str | None


class LowStock(_Strict):
    group_by: Literal["category", "brand", "supplier"] | None
    all_categories: bool


class IdeationSignal(_Strict):
    idea_count: int
    adds_to_held_idea: bool


class Ask(_Strict):
    act: TurnAct
    domain: str | None
    switch_back_to: str | None
    entities: list[Entity]
    specs: list[Spec]
    attachment_type: str | None
    period: Period | None
    row_pick: int | None
    answer: Answer | None
    cleared: list[ClearableField]
    report: ReportSpec | None
    low_stock: LowStock | None
    ideation: IdeationSignal | None


class Escalation(_Strict):
    requested: bool
    team_key: str | None


class Clarify(_Strict):
    needed: bool
    phrase: str | None
    about: (
        Literal[
            "domain", "entity", "spec_attribute", "attachment_type", "report_measure",
            "report_period", "which_held", "other",
        ]
        | None
    )


class ParserV2Output(_Strict):
    asks: list[Ask]
    clarify: Clarify
    language: Literal["en", "zh", "ms", "mixed"]
    escalation: Escalation | None


# --------------------------------------------------------------------------- strict schema


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_refs(copy.deepcopy(defs[name]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


_DROP = ("title", "default", "description")


def _strictify(node: Any) -> Any:
    """Every object: all properties required, no extra keys. Titles/defaults dropped
    (strict structured output rejects `default`), `format: date` kept as a plain string
    with a pattern (providers differ on `format`)."""
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strictify(v) for k, v in node.items() if k not in _DROP}
    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"].keys())
        out["additionalProperties"] = False
    if out.get("format") == "date":
        out.pop("format")
        out["pattern"] = r"^\d{4}-\d{2}-\d{2}$"
    return out


def build_strict_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    return _strictify(_inline_refs(raw, defs))


PV2_JSON_SCHEMA: dict = build_strict_schema(ParserV2Output)
PV2_SCHEMA_NAME = "chatbot_parser_v2"


# --------------------------------------------------------------------------- pinned-key check


@dataclass(frozen=True)
class IndexKeys:
    """The keys one PINNED index lists (parsed back from the published text by
    `index.parse_index_keys`). Empty set for a section = nothing accepted there."""

    domains: frozenset[str] = frozenset()
    entity_kinds: frozenset[str] = frozenset()
    # kind -> keys for index-listed entity kinds (brand, product_type, ...).
    entity_keys: dict[str, frozenset[str]] = field(default_factory=dict)
    attachment_types: frozenset[str] = frozenset()
    spec_attributes: frozenset[str] = frozenset()
    # attribute -> enum value keys.
    spec_values: dict[str, frozenset[str]] = field(default_factory=dict)
    report_datasets: frozenset[str] = frozenset()
    report_measures: frozenset[str] = frozenset()
    report_dimensions: frozenset[str] = frozenset()
    report_bases: frozenset[str] = frozenset()
    teams: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DroppedKey:
    """One model-emitted key the pinned index does not list: nulled, logged."""

    where: str  # e.g. "asks[0].entities[1].key"
    kind: str  # brand | attachment_type | spec_attribute | domain | ...
    value: str
    phrase: str | None


class SchemaError(ValueError):
    """The emission is not a ParserV2Output (shape, enum or extra keys)."""


def validate(raw: dict, keys: IndexKeys) -> tuple[ParserV2Output, list[DroppedKey]]:
    """Shape-check `raw`, then null every index key the pinned index does not list.

    Never maps a dropped key to a near one: the dropped key is returned for the
    unknown-words log and the downstream resolver clarifies on the null.
    """
    try:
        out = ParserV2Output.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(str(exc)) from exc
    if not out.asks:
        raise SchemaError("asks must hold at least one ask")
    dropped: list[DroppedKey] = []

    def keep(value: str | None, allowed: frozenset[str], where: str, kind: str, phrase=None):
        if value is None or value in allowed:
            return value
        dropped.append(DroppedKey(where, kind, value, phrase))
        return None

    for i, ask in enumerate(out.asks):
        at = f"asks[{i}]"
        ask.domain = keep(ask.domain, keys.domains, f"{at}.domain", "domain")
        ask.attachment_type = keep(
            ask.attachment_type, keys.attachment_types, f"{at}.attachment_type", "attachment_type"
        )
        kept_entities = []
        for j, ent in enumerate(ask.entities):
            where = f"{at}.entities[{j}]"
            if keys.entity_kinds and ent.kind not in keys.entity_kinds:
                # A kind the index does not list: nothing downstream can look it up.
                dropped.append(DroppedKey(f"{where}.kind", "entity_kind", ent.kind, ent.phrase))
                continue
            if ent.key is not None:
                if ent.kind in keys.entity_keys:
                    ent.key = keep(
                        ent.key, keys.entity_keys[ent.kind], f"{where}.key", ent.kind, ent.phrase
                    )
                else:
                    # Non-index kinds are looked up by their phrase only (contract s.1):
                    # a model-made "key" for a product or customer is never trusted.
                    dropped.append(DroppedKey(f"{where}.key", ent.kind, ent.key, ent.phrase))
                    ent.key = None
            kept_entities.append(ent)
        ask.entities = kept_entities
        for j, spec in enumerate(ask.specs):
            spec.attribute = keep(
                spec.attribute, keys.spec_attributes, f"{at}.specs[{j}].attribute",
                "spec_attribute", spec.phrase,
            )
            if spec.value_key is not None:
                allowed = keys.spec_values.get(spec.attribute or "", frozenset())
                spec.value_key = keep(
                    spec.value_key, allowed, f"{at}.specs[{j}].value_key", "spec_value",
                    spec.phrase,
                )
        if ask.report is not None:
            r = ask.report
            r.dataset = keep(r.dataset, keys.report_datasets, f"{at}.report.dataset", "report_dataset")
            r.basis = keep(r.basis, keys.report_bases, f"{at}.report.basis", "report_basis")
            kept_measures = []
            for m in r.measures:
                parts = m.split("/")
                if len(parts) <= 2 and all(p in keys.report_measures for p in parts):
                    kept_measures.append(m)
                else:
                    dropped.append(DroppedKey(f"{at}.report.measures", "report_measure", m, None))
            r.measures = kept_measures
            kept_dims = []
            for d in r.group_by:
                if d in keys.report_dimensions:
                    kept_dims.append(d)
                else:
                    dropped.append(DroppedKey(f"{at}.report.group_by", "report_dimension", d, None))
            r.group_by = kept_dims
            kept_filters = []
            for f in r.filters:
                if f.dimension in keys.report_dimensions:
                    kept_filters.append(f)
                else:
                    dropped.append(
                        DroppedKey(f"{at}.report.filters", "report_dimension", f.dimension, None)
                    )
            r.filters = kept_filters
    if out.escalation is not None:
        out.escalation.team_key = keep(
            out.escalation.team_key, keys.teams, "escalation.team_key", "team"
        )
    return out, dropped
