"""WA-CONCISE review round 3 (red-first): the freshness footer closes the whole reply.
Placeholder data only."""
from __future__ import annotations

from tests.chatbot import test_wa_concise_review_round2 as r2
from tests.chatbot import test_wa_concise_crossdomain as xd
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot._wa_concise_helpers import INCOMING_TOOL, PO_TOOL, STOCK_TOOL


def test_footer_is_the_last_line_before_the_offer_after_a_fold(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = xd._ask_stock(
        **r2._fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTY1", "SRTX"],
        tools={
            INCOMING_TOOL: xd._incoming({"SRTX": r2.INC_X}),
            STOCK_TOOL: r2._stock_two,
            PO_TOOL: xd._po(),
        },
    )

    assert said.count("_Updated 11/09/2026 17:26_") == 1, said
    assert said == (
        "1. *Product Code:* SRTY1\n*BRW:* 5\n\n"
        "2. *Product Code:* SRTX\n*Product Name:* LAMP\n*BRW:* 0 (O/S: 233)\n"
        f"{r2.INC_X_LINES}\n{r2.FLAG}\n\n"
        "_Updated 11/09/2026 17:26_\n\n"
        f"{r2.WH_OFFER}"
    ), said
