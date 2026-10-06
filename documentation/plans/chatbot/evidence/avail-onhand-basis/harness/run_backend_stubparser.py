"""Cloud console replay launcher (AVAIL-ONHAND-BASIS): the REAL backend (engine, lane, MCP
hop, route, presenter, renderer) with ONLY the parser's LLM call replaced, because this
sandbox holds no LLM key. Copied from the AVAIL-RENDER harness.

Each replayed message maps to the parser output the semantic parser prompt teaches for it:
the prod case is a bare code ("MWC7632-SH-P", a stock ask with the product and no quantity)
followed by a bare number ("10", the quantity answer to the open question). Anything not in
the table is read as a casual message with no entities.
"""
from __future__ import annotations

import copy
import os
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_storage  # noqa: F401,E402

from app.main import app  # noqa: E402
from app.services.chatbot.head import parser as parser_mod  # noqa: E402

BASE = {
    "message_type": "business_query", "intent_hint": None, "domain_hint": None,
    "scope_intent": "specific", "is_affirmative": None, "user_goal": "",
    "access_levels": [], "broaden_axis": None, "date_mode": None,
    "date_filter_start": None, "date_filter_end": None, "match_mode": "and",
    "demand_qty": None, "entities": [], "entity_op": "replace_combine",
    "scope_exclusive": False, "requested_attributes": [], "contains_flyer": False,
    "reference_positions": [], "reference_target": None, "person_mention": None,
    "is_active": None, "order_status": None, "correction": False,
    "routing": {"suggested_team": None, "suggested_agent": None, "team_source": None},
    "escalation": {"is_escalation_confirmation": False, "company_pick": None},
    "continuation": None, "group_by": None, "top_n": None, "document": None, "status": None,
}


def _p(raw: str, quantity: int | None = None) -> dict:
    out = {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
    if quantity is not None:
        out["quantity"] = quantity
    return out


def _stock(*entities: dict) -> dict:
    return {"domain_hint": "inventory", "intent_hint": "check_stock", "entities": list(entities)}


def _qty(n: int) -> dict:
    """A bare number answering the open quantity question (the shape
    `tests/chatbot/_r9_engine_console.py::reply(demand_qty=n)` pins)."""
    return {"domain_hint": "inventory", "intent_hint": "check_stock", "entities": [], "demand_qty": n}


TABLE = {
    # The prod case, turn 1: the bare code. 6 on hand in MOCHA-WH, 13 on open SO.
    "mwc7632-sh-p": _stock(_p("MWC7632-SH-P")),
    "check stock mwc7632-sh-p": _stock(_p("MWC7632-SH-P")),
    # Turn 2: the bare quantity.
    "10": _qty(10),
    "5": _qty(5),
    # One-turn forms of the same asks.
    "mwc7632-sh-p x 10": _stock(_p("MWC7632-SH-P", 10)),
    "mwc7632-sh-p x 5": _stock(_p("MWC7632-SH-P", 5)),
    # Control: nothing on hand, nothing outstanding, nothing incoming.
    "ohb0000 x 5": _stock(_p("OHB0000", 5)),
}
CASUAL = {"message_type": "casual"}


def _message(user_block: str) -> str:
    m = re.search(r"^Current user message: (.*)$", user_block, re.M)
    return (m.group(1) if m else "").strip()


def fake_parse(config, user_block):
    msg = _message(user_block)
    out = copy.deepcopy(BASE)
    out.update(copy.deepcopy(TABLE.get(msg.lower(), CASUAL)))
    print(
        f"[stub-parser] {msg!r} -> {out['intent_hint']} "
        f"{[(e['raw'], e.get('quantity')) for e in out['entities']]} demand_qty={out['demand_qty']}",
        flush=True,
    )
    return parser_mod.ParsedOutput(out)


def fake_resolve_config(db, *, current_date, override_version_id=None):
    return parser_mod.ParserConfig(system_prompt="stub", prompt_version=1, provider="openai",
                                   model="stub", api_key="stub")


parser_mod.parse = fake_parse
parser_mod.resolve_config = fake_resolve_config

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
