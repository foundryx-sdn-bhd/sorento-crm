"""ROGUE-CONVO-REVIEW class F: the two parser addenda, applied to a parser text.

Not itself a test file (no `test_` prefix). One applier, two readers: the static test
(`test_rogue_convo_prompt_patch.py`) and the live-parser eval
(`test_rogue_convo_live_parser.py`). Same shape as `_order_broaden_prompt.py`.

Two addenda, two anchors, both inside the section whose rule they extend:

* `code-in-description` goes right after the BARE FAMILY CODE bullet's last line (the
  "purchase order" example), so the line-with-a-code-inside rule sits beside the
  bare-code rule;
* `bare-count` goes right after the QUANTITY section's closing sentence ("never assumed
  to be 1."), so the no-code count sits beside the count-beside-a-code rule.

The production file is never edited here; the owner edits prod by hand once the eval
passes. `ROGUE_CONVO_PROMPT_FILE` points the appliers at an exported dev text (v60) so
the eval grades the text that will actually be edited; without it the shipped
`SEMANTIC_PARSER_PROMPT` is used. An anchor missing from a supplied text is an error,
never a silent append: a moved section means the addendum needs re-placing by hand.
"""
from __future__ import annotations

import os
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2]
DATA = BACKEND / "alembic" / "data"
CODE_IN_DESCRIPTION = DATA / "chatbot_semantic_parser.rogue-convo.code-in-description.addendum.txt"
BARE_COUNT = DATA / "chatbot_semantic_parser.rogue-convo.bare-count.addendum.txt"

#: The last line of the BARE FAMILY CODE bullet (prod text line 416).
ANCHOR_CODE_IN_DESCRIPTION = '    A product plus "purchase order" or "PO" is a business query, never casual.\n'
#: The last line of the QUANTITY section (prod text line 1373).
ANCHOR_BARE_COUNT = "be 1.\n"


def base_text() -> str:
    """The text the addenda are applied to: an exported dev version when
    `ROGUE_CONVO_PROMPT_FILE` names one, else the shipped parser prompt."""
    path = os.environ.get("ROGUE_CONVO_PROMPT_FILE")
    if path:
        return pathlib.Path(path).read_text(encoding="utf-8")
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


def _insert_after(text: str, anchor: str, addendum: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise ValueError(f"anchor line found {count} times, expected exactly once: {anchor!r}")
    head, tail = text.split(anchor)
    return head + anchor + addendum + tail


def patched_text(text: str | None = None, *, code_in_description: bool = True, bare_count: bool = True) -> str:
    """`text` with the chosen addenda inserted after their anchors."""
    out = base_text() if text is None else text
    if code_in_description:
        out = _insert_after(out, ANCHOR_CODE_IN_DESCRIPTION, CODE_IN_DESCRIPTION.read_text(encoding="utf-8"))
    if bare_count:
        out = _insert_after(out, ANCHOR_BARE_COUNT, BARE_COUNT.read_text(encoding="utf-8"))
    return out
