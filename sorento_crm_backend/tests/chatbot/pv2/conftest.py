"""PV2 test fixtures (CONTRACT-PV2-13oct.md section 7). Lane tests live beside this file as
`test_<lane>_*.py`; fixtures as `fixtures/<lane>_<topic>.jsonl`, replayed by `replay.py`.

No PV2 test calls a model. The parent `tests/chatbot/conftest.py` supplies the blank-schema
`session_factory` for the few tests that read the index from the database.
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import replay


@pytest.fixture()
def carry_ready() -> bool:
    return replay.carry_ready()


def minimal_ask(**over) -> dict:
    """One always-present Ask with every key, for handwritten parser outputs."""
    ask = {
        "act": "new_topic", "domain": None, "switch_back_to": None, "entities": [], "specs": [],
        "attachment_type": None, "period": None, "row_pick": None, "answer": None, "cleared": [],
        "report": None, "low_stock": None, "ideation": None,
    }
    ask.update(over)
    return ask


def minimal_output(*asks: dict, **over) -> dict:
    out = {
        "asks": list(asks) or [minimal_ask()],
        "clarify": {"needed": False, "phrase": None, "about": None},
        "language": "en",
        "escalation": None,
    }
    out.update(over)
    return out
