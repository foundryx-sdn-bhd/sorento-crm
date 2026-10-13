"""WA-CONCISE round 6 (red-first): numbering keeps a set page's offset and counts order
blocks. Placeholder data only."""
from __future__ import annotations

import re

from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row


def _compose(*sections: tuple[str, list[str], str, str | None]) -> str:
    """sections: (domain, product codes, lane_text, header_override)."""
    rows = [
        {**_domain_row(d, narrowing={"product": "list_all"}), "label": d} for d, _c, _t, _h in sections
    ]
    policy = Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    envs = []
    for domain, codes, text, header in sections:
        env = {
            "domain": domain, "denied": False, "entities": codes, "product_codes": codes,
            "figures": [{"fields": [{"label": "Product Code", "value": c}]} for c in codes],
            "files": [], "miss": [], "has_result": True, "tool_has_result": True,
            "unresolved": [], "error": None, "lane_text": text,
        }
        if header:
            env["header_override"] = header
        envs.append(env)
    return compose(envs, state, policy, ctx=None).text


def test_b1_counted_set_continuation_page_keeps_its_offset_numbers():
    header = "There are 25 wall basins, showing 11 to 12:"
    page = f"11. *Product Code:* SRTA\n*BRW:* 5\n\n12. *Product Code:* SRTB\n*BRW:* 6"

    text = _compose(("inventory", [], f"{header}\n\n{page}", header))

    assert re.findall(r"^(\d+)\. \*Product Code:\* (\w+)", text, re.MULTILINE) == [
        ("11", "SRTA"), ("12", "SRTB"),
    ], text


def test_s1_order_blocks_count_across_sections():
    orders = "1. *Order Number:* SO-1\n*Status:* Delivered\n\n2. *Order Number:* SO-2\n*Status:* Delivered"
    stock = "1. *Product Code:* SRTA\n*BRW:* 5\n\n2. *Product Code:* SRTB\n*BRW:* 6"

    text = _compose(("order", [], orders, None), ("inventory", ["SRTA", "SRTB"], stock, None))

    assert re.findall(r"^(\d+)\. \*(?:Order Number|Product Code):\* (\S+)", text, re.MULTILINE) == [
        ("1", "SO-1"), ("2", "SO-2"), ("3", "SRTA"), ("4", "SRTB"),
    ], text
