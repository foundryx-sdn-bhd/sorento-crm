"""PV2-REPORTS: what a report spec's missing field becomes, as a table (contract section 5).

Every field a report needs to run is either asked or defaulted here, in ONE question for all of
them, never up to four sequential questions (`lanes/business/report_ask.py:8`).

CONFLICT, PENDING OWNER: `report_ask.py:8-9` records the owner ruling of 4 Oct 2026, "a ranking
never defaults the metric or the basis: it asks". This table mirrors it tonight: measure and basis
are ASKED (together, one question). The period and the count are asked as well (today's lanes ask
them); the defaults the crew proposes for them (`proposed_default`) are recorded, not applied,
until the owner rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.services.chatbot.lanes.business import ranking_ask
from app.services.chatbot.lanes.business import report_ask

Mode = Literal["ask", "default"]


@dataclass(frozen=True)
class Rule:
    mode: Mode
    default: Any = None  # applied when mode == "default"
    proposed_default: Any = None  # PENDING OWNER: not applied while mode == "ask"
    note: str = ""


POLICY: dict[str, Rule] = {
    # `reports.ask.DATASETS`: "the first is the default (a call naming none is sales)".
    "dataset": Rule("default", default="sales"),
    "measure": Rule("ask", note="owner 4 Oct 2026: a ranking never defaults the metric"),
    "basis": Rule("ask", note="owner 4 Oct 2026: a ranking never defaults the basis"),
    "period": Rule("ask", proposed_default="this_month", note="PENDING OWNER"),
    "top_n": Rule("ask", proposed_default=10, note="PENDING OWNER"),
}

#: The order the missing fields are listed in the one question.
FIELD_ORDER = ("period", "top_n", "measure", "basis")

#: One line per missing field, today's lane wording (`report_ask.py`, `ranking_ask.py`).
QUESTION_LINES = {
    "period": report_ask.PERIOD_QUESTION,
    "top_n": report_ask.TOP_N_QUESTION,
    "measure": ranking_ask.METRIC_LINE,
    "basis": ranking_ask.RANKING_BASIS_LINE,
}


def rule(name: str) -> Rule:
    return POLICY[name]


def default_for(name: str) -> Optional[Any]:
    """The value a field takes when missing, or None when the table says ask."""
    r = POLICY[name]
    return r.default if r.mode == "default" else None


def question_line(name: str, dataset: str) -> str:
    """The question line for a missing field; the chat report keeps its own period wording."""
    if dataset == "chat" and name == "period":
        from app.services.chatbot.lanes.business import chat_report

        return chat_report.PERIOD_QUESTION
    return QUESTION_LINES[name]
