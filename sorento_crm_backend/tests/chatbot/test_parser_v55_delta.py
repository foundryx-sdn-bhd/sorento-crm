"""REPORTS-STEP2 S3a live round: the v55 parser text is v54 plus exactly three in-place edits."""
from __future__ import annotations

import difflib
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"


def _lines(name: str) -> list[str]:
    return (DATA / name).read_text(encoding="utf-8").splitlines()


def test_v55_is_v54_plus_exactly_the_three_edits():
    old, new = _lines("chatbot_semantic_parser.v54.txt"), _lines("chatbot_semantic_parser.v55.txt")
    added, changed = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "insert":
            added.append("\n".join(new[j1:j2]))
        elif tag == "replace":
            changed.append(("\n".join(old[i1:i2]), "\n".join(new[j1:j2])))
        elif tag == "delete":
            raise AssertionError("v55 removes lines of v54")
    # (c) the domain_hint enum line gains the two unsupported topics; nothing else is rewritten
    assert len(changed) == 1, changed
    before, after = changed[0]
    assert after == before.replace("| purchase_cost | null", "| purchase_cost | receivables | dealer_locator | null")
    # (b) then (a), in file order: single added lines
    assert len(added) == 2, added
    assert added[0].lstrip().startswith('An "overdue" amount, a payment balance or an account statement is MONEY owed')
    assert added[1].lstrip().startswith("- NOT a status: MONEY a customer owes.")
    text = "\n".join(added) + after
    assert chr(0x2014) not in text and chr(0x2013) not in text
