"""PLACEHOLDER owned by PV2-CORE (CONTRACT-PV2-13oct.md section 1 and the ReportSpec of
section 5), transcribed verbatim so PV2-CARRY can code and test against the contract types
before CORE's commit 1 lands. On integration CORE's `schema.py` wins outright: this file
declares nothing CORE's does not.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

TurnAct = Literal["answers_bot_question", "refines_held", "new_topic", "switch_back", "small_talk", "unclear"]
Op = Literal["set", "add", "remove", "clear"]
Comparator = Literal["eq", "lt", "gt", "between", "about"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(_Strict):
    kind: str
    key: str | None
    phrase: str | None
    op: Op
    refers_to_held: bool


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
    preset: Literal[
        "today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter",
        "last_quarter", "this_year", "last_year", "ytd", "last_n_days", "last_n_months",
    ] | None
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
    cleared: list[str]
    report: ReportSpec | None
    low_stock: LowStock | None
    ideation: IdeationSignal | None


class Escalation(_Strict):
    requested: bool
    team_key: str | None


class Clarify(_Strict):
    needed: bool
    phrase: str | None
    about: Literal[
        "domain", "entity", "spec_attribute", "attachment_type", "report_measure", "report_period",
        "which_held", "other",
    ] | None


class ParserV2Output(_Strict):
    asks: list[Ask]
    clarify: Clarify
    language: Literal["en", "zh", "ms", "mixed"]
    escalation: Escalation | None
