"""WA-CONCISE round 13 (red-first): the footer closes the body even after a "No stock found"
line was slotted above it. Placeholder data only."""
from __future__ import annotations

from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

FOOTER = "_Updated 11/09/2026 17:26_"


def _text(stock_footer: bool, incoming_footer: bool) -> str:
    rows = [
        {**_domain_row(d, narrowing={"product": "list_all"}), "label": d}
        for d in ("inventory", "incoming")
    ]
    policy = Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)

    def env(domain, codes, rows_for, text):
        return {
            "domain": domain, "denied": False, "entities": codes, "product_codes": codes,
            "figures": [{"fields": [{"label": "Product Code", "value": c}]} for c in rows_for],
            "files": [], "miss": [], "has_result": True, "tool_has_result": True,
            "unresolved": [], "error": None, "lane_text": text,
        }

    stock = "*Product Code:* SRTA\n*BRW:* 5" + (f"\n\n{FOOTER}" if stock_footer else "")
    incoming = "*Product Code:* SRTX\n*Container:* IAAU1907074" + (f"\n\n{FOOTER}" if incoming_footer else "")
    envs = [
        env("inventory", ["SRTA", "SRTB"], ["SRTA"], stock),
        env("incoming", ["SRTX"], ["SRTX"], incoming),
    ]
    return compose(envs, state, policy, ctx=None).text


def test_two_footers_collapse_to_one_at_the_end_and_the_no_stock_line_stays_above_it():
    text = _text(stock_footer=True, incoming_footer=True)

    assert "No stock found for SRTB." in text, text
    assert text.count(FOOTER) == 1, text
    assert text.rstrip().endswith(FOOTER), text
    assert text.index("No stock found for SRTB.") < text.index(FOOTER), text


def test_a_footer_on_the_stock_section_alone_still_closes_the_reply():
    text = _text(stock_footer=True, incoming_footer=False)

    assert "No stock found for SRTB." in text, text
    assert text.count(FOOTER) == 1, text
    assert text.rstrip().endswith(FOOTER), text
