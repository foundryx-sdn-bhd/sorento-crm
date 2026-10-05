"""Cloud browser pass launcher (AVAIL-RENDER): the REAL backend (engine, lane, MCP hop,
route, presenter, renderer) with ONLY the parser's LLM call replaced, because this sandbox
holds no LLM key. Copied from the LOWSTOCK-FILTER-ASK harness.

Each hand-test message maps to the parser output the semantic parser prompt teaches for it
(a stock ask with the product and its quantity as entities; an ETA ask with the products).
Anything not in the table is read as a casual message with no entities.
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


def _eta(*entities: dict) -> dict:
    return {"domain_hint": "incoming", "intent_hint": "check_incoming", "entities": list(entities)}


TABLE = {
    # A: discontinued, 100 on hand, cap 100.
    "check stock avr1001 x 5": _stock(_p("AVR1001", 5)),
    "check stock avr1001 x 500": _stock(_p("AVR1001", 500)),
    # B: live, 30 on hand.
    "check stock avr1002 x 50": _stock(_p("AVR1002", 50)),
    # C: live, nothing on hand, no shipment.
    "check stock avr1003 x 5": _stock(_p("AVR1003", 5)),
    # D: discontinued, nothing on hand, shipment due 19/10/2026.
    "check stock avr1004 x 5": _stock(_p("AVR1004", 5)),
    "eta avr1004 and avr1003": _eta(_p("AVR1004"), _p("AVR1003")),
    # Malay and Chinese wordings of A x 5 and B x 50 (the reply language is detected from
    # the message, `app/services/chatbot/language.py`).
    "avr1001 x 5 ada stok tak": _stock(_p("AVR1001", 5)),
    "avr1002 x 50 ada stok tak": _stock(_p("AVR1002", 50)),
    "avr1001 x 5 有库存吗": _stock(_p("AVR1001", 5)),
    "avr1002 x 50 有库存吗": _stock(_p("AVR1002", 50)),
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
        f"{[(e['raw'], e.get('quantity')) for e in out['entities']]}",
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
