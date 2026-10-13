"""PV2-REPORTS test-only shim: the contract's report types until PV2-CORE's `pv2/schema.py` lands.

Contract `CONTRACT-PV2-13oct.md` section 1 and 5 declare `Period`, `Filter` and `ReportSpec`; CORE
owns the real module. When it is importable and carries `ReportSpec`, this installs nothing and the
tests run against the real types. Delete this file once CORE's schema is on the base branch.
"""
from __future__ import annotations

import sys
import types
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


def install() -> types.ModuleType:
    try:
        from app.services.chatbot.pv2 import schema  # type: ignore[attr-defined]

        if hasattr(schema, "ReportSpec"):
            return schema
    except ImportError:
        pass

    class _Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Period(_Strict):
        preset: Literal[
            "today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter",
            "last_quarter", "this_year", "last_year", "ytd", "last_n_days", "last_n_months",
        ] | None = None
        n: int | None = None
        date_from: date | None = None
        date_to: date | None = None

    class Filter(_Strict):
        dimension: str
        op: Literal["in", "not_in", "gt", "lt", "between"] = "in"
        keys: list[str] = []
        values: list[float] = []

    class ReportSpec(_Strict):
        dataset: str | None = None
        measures: list[str] = []
        group_by: list[str] = []
        top_n_per_group: int | None = None
        filters: list[Filter] = []
        period: Period | None = None
        compare_to: Literal["prev_period", "last_year", "custom"] | None = None
        compare_period: Period | None = None
        grain: Literal["day", "week", "month", "quarter", "year"] | None = None
        threshold: float | None = None
        sort: Literal["desc", "asc"] | None = None
        top_n: int | None = None
        basis: str | None = None

    mod = types.ModuleType("app.services.chatbot.pv2.schema")
    mod.Period, mod.Filter, mod.ReportSpec = Period, Filter, ReportSpec
    sys.modules["app.services.chatbot.pv2.schema"] = mod
    import app.services.chatbot.pv2 as pkg

    pkg.schema = mod  # type: ignore[attr-defined]
    return mod
