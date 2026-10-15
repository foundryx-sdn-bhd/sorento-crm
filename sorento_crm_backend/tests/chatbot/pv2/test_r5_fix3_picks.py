"""PV2 R5 fix round 3, red tests, no LLM, no DB: a number on a numbered question is its answer whatever domain the
parser tagged it with, and the question's own ask runs (UAC R5-1, R5-5: the question kind survives a pick)."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import Lookup
from tests.chatbot.pv2.carry_fakes import NOW, ask, out
from tests.chatbot.pv2.test_r5_fix2_picks import _products, _which_line_menu
from tests.chatbot.pv2.test_r5_d_doc_lines import _resolver


@pytest.mark.parametrize("answer", [None, {"option_key": None, "free_value": "2"}], ids=["row-pick", "free-value"])
@pytest.mark.parametrize("tag", ["order", "promotion"])
def test_a_number_mis_tagged_with_another_domain_still_picks_the_menu_and_runs_the_questions_ask(tag, answer):
    """The which-product menu came from a stock ask (origin inventory) after a two-line DO. The reply '2' (or 'the
    second one') is parsed as a new topic in `tag`: the menu's option 2 is picked (never the DO's first line, which
    is row 2 of the last answer) and the stock ask runs, in inventory, not in the parser's tagged domain."""
    st1 = _which_line_menu()
    menu = [o.label for o in st1.pending.options]
    parsed = out(ask(act="new_topic", domain=tag, row_pick=None if answer else 2, answer=answer))
    st2, acts = apply_turn(st1, parsed, resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _products(st2) == [menu[1]], (_products(st2), menu)
    assert acts[0].domain == "inventory" and st2.held.domain == "inventory", (acts[0].domain, st2.held.domain)
