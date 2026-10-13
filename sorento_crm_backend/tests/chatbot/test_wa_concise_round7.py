"""WA-CONCISE round 7 (red-first): the fold numbers from the reply's first number once a
primary block has been merged away. Placeholder data only."""
from __future__ import annotations

import re

from app.services.chatbot.answer_bridge import _fold_blocks

_XD = (
    "*Product Code:* SRTX\n*Stock:* 0\n*Container:* IAAU1907074\n*ETA:* 2026-09-09\n"
    "*Incoming Quantity:* 49"
)


def _numbers(kept: str, rest: str) -> list[tuple[str, str]]:
    return re.findall(
        r"^(\d+)\. \*Product Code:\* (\w+)", "\n\n".join(p for p in (kept, rest) if p), re.MULTILINE
    )


def test_fold_numbers_from_one_when_the_first_primary_block_merges_away():
    primary = "1. *Product Code:* SRTX\n*BRW:* 0\n\n2. *Product Code:* SRTA\n*BRW:* 5"

    kept, rest = _fold_blocks(primary, _XD)

    assert _numbers(kept, rest) == [("1", "SRTA"), ("2", "SRTX")], (kept, rest)


def test_fold_guard_counted_set_continuation_page_keeps_starting_at_its_offset():
    primary = "11. *Product Code:* SRTA\n*BRW:* 5\n\n12. *Product Code:* SRTX\n*BRW:* 0"

    kept, rest = _fold_blocks(primary, _XD)

    assert _numbers(kept, rest) == [("11", "SRTA"), ("12", "SRTX")], (kept, rest)
