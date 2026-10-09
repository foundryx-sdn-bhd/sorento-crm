"""ROGUE-CONVO-REVIEW class F: the two addenda apply cleanly to the shipped parser text.

Static (no provider call): the anchors exist exactly once, the patched text carries both
addenda once, and nothing else moved. Whether the MODEL honours the lines is decided by
`test_rogue_convo_live_parser.py`, which is opt-in.
"""
from __future__ import annotations

from tests.chatbot import _rogue_convo_prompt as rc


def test_both_anchors_occur_exactly_once_in_the_shipped_text() -> None:
    text = rc.base_text()
    assert text.count(rc.ANCHOR_CODE_IN_DESCRIPTION) == 1
    assert text.count(rc.ANCHOR_BARE_COUNT) == 1


def test_patched_text_carries_each_addendum_once_right_after_its_anchor() -> None:
    base = rc.base_text()
    out = rc.patched_text(base)
    code = rc.CODE_IN_DESCRIPTION.read_text(encoding="utf-8")
    count = rc.BARE_COUNT.read_text(encoding="utf-8")
    assert out.count(code) == 1
    assert out.count(count) == 1
    assert rc.ANCHOR_CODE_IN_DESCRIPTION + code in out
    assert rc.ANCHOR_BARE_COUNT + count in out
    # Nothing else changed: removing the two addenda gives the base text back.
    assert out.replace(code, "", 1).replace(count, "", 1) == base


def test_each_addendum_can_be_applied_alone() -> None:
    base = rc.base_text()
    only_code = rc.patched_text(base, bare_count=False)
    only_count = rc.patched_text(base, code_in_description=False)
    assert rc.CODE_IN_DESCRIPTION.read_text(encoding="utf-8") in only_code
    assert rc.BARE_COUNT.read_text(encoding="utf-8") not in only_code
    assert rc.BARE_COUNT.read_text(encoding="utf-8") in only_count
    assert rc.CODE_IN_DESCRIPTION.read_text(encoding="utf-8") not in only_count
