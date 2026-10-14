"""PV2-FIX r7j (reviewer): the mirrored v1 question is the question line only, and bounded, so a v1 report
table never rides in `PENDING QUESTION: asked=...` on every turn while the question is open."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.chatbot.pv2 import history, turn
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2.carry_fakes import mk_state

ROWS = "\n".join(f"{d:02d}/09 to {d + 6:02d}/09: Qty {100 + d}, RM {10000 + d * 37:,}.00" for d in range(1, 61))
REPORT = f"*Read as:* delivered sales, 1 Sep 2026 to 30 Sep 2026\n*Total:* Qty 1,509, RM 117,472.12\n{ROWS}"
OFFER = "Reply 1 for the sales order list."
MENU_REPORT = f"{REPORT}\n\n*Drill down:*\n1. By customer\n2. By product\n3. Delivery orders"
NOW = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)


def _pending(kind, n):
    opts = [{"code": f"C{i}", "label": f"Option {i}", "position": i, "entity_type": "product"} for i in range(1, n + 1)]
    return v1p.ask(kind, opts, team="sales", expects=None, payload={}, asked_at_turn=2)


def test_the_fixture_reply_is_long():
    assert len(REPORT) > 2000 and len(MENU_REPORT) > 2000


def test_a_long_report_followed_by_an_offer_line_keeps_only_the_offer_line():
    state = turn.mirror_v1_pending(mk_state(), _pending("sales_report_detail", 1), last_reply=f"{REPORT}\n\n{OFFER}")
    asked = state.pending.asked
    assert len(asked) <= 400, len(asked)
    assert OFFER in asked
    assert "Qty" not in asked, asked


def test_a_long_report_followed_by_numbered_options_keeps_only_the_line_above_them():
    state = turn.mirror_v1_pending(mk_state(), _pending("sales_report_detail", 3), last_reply=MENU_REPORT)
    asked = state.pending.asked
    assert len(asked) <= 400, len(asked)
    assert "Drill down" in asked
    assert "Qty" not in asked, asked


def test_the_rendered_memory_pending_line_stays_bounded():
    state = turn.mirror_v1_pending(mk_state(), _pending("sales_report_detail", 3), last_reply=MENU_REPORT)
    block = history.render_memory(state, history.MemoryProfile(audience="staff"), NOW)
    line = next(ln for ln in block.splitlines() if ln.startswith("PENDING QUESTION"))
    assert len(line) <= 450, len(line)
    assert len(block) < 1500, len(block)
