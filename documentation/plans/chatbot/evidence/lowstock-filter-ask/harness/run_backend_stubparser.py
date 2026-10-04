"""Cloud browser pass launcher: the REAL backend (engine, lane, route, MCP hop, worker
queue) with ONLY the parser's LLM call replaced, because this sandbox holds no LLM key.

Each message maps to the parser output the owner's dev copy recorded for it (crew trace
of turns 3dec9b68 / 4a90dd1d) or, for the hand-test script's other messages, the output
LOW_STOCK_ADDENDUM / the general entity rules teach. Anything not in the table is read
as a casual message with no entities, which is what the parser does with a bare "all",
"spaceship", "cancel" and so on.
"""
from __future__ import annotations

import copy
import re
import sys

sys.path.insert(0, ".")
import os
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


def _e(raw: str, hint: str) -> dict:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def _low(*entities: dict) -> dict:
    return {"domain_hint": "inventory", "intent_hint": "low_stock_report", "entities": list(entities)}


TABLE = {
    "low stock report water closet taiyang": _low(_e("water closet", "category"), _e("taiyang", "brand")),
    "low stock report by supplier, water closet only": _low(_e("water closet", "category"), _e("supplier", "supplier")),
    "sorento water tap low stock list": _low(_e("Sorento", "brand"), _e("water tap", "category")),
    "low stock report": _low(),
    "low stock water closet by supplier": _low(_e("water closet", "category")),
    "water closet": {"domain_hint": "master_products", "intent_hint": "check_product",
                     "entities": [_e("water closet", "product")]},
}
CASUAL = {"message_type": "casual"}


def _message(user_block: str) -> str:
    m = re.search(r"^Current user message: (.*)$", user_block, re.M)
    return (m.group(1) if m else "").strip()


def fake_parse(config, user_block):
    msg = _message(user_block)
    out = copy.deepcopy(BASE)
    out.update(copy.deepcopy(TABLE.get(msg.lower(), CASUAL)))
    print(f"[stub-parser] {msg!r} -> {out['intent_hint']} {[(e['raw'], e['hint']) for e in out['entities']]}",
          flush=True)
    return parser_mod.ParsedOutput(out)


def fake_resolve_config(db, *, current_date, override_version_id=None):
    return parser_mod.ParserConfig(system_prompt="stub", prompt_version=1, provider="openai",
                                   model="stub", api_key="stub")


parser_mod.parse = fake_parse
parser_mod.resolve_config = fake_resolve_config

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
