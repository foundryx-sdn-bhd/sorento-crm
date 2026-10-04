"""AVAIL-MODE-REPLIES, cloud live-parser pass at 2ff7f5e9 (step 25, 'eta SRTWC286'): the
incoming roster stamped every variant "- has incoming" for an availability contact. The
dealer view now lists every asked code (a code with no shipment is "CODE: No ETA"), so
the probe counted each row as incoming; and a dealer is told nothing about a variant's
incoming before picking it (crew, 3 Oct). The dealer view leaves the lines unstamped.

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md.
"""
from __future__ import annotations

from app.services.chatbot.lanes.business import pickers

GATE = {
    "gate_clarification": "Multiple matches found. Please choose:\n1. SRTWC286-SH\n2. SRTWC286-SH-150",
    "compatible_entities": [
        {"code": "SRTWC286-SH", "entity_type": "product"},
        {"code": "SRTWC286-SH-150", "entity_type": "product"},
    ],
}


def _dealer_row(title: str) -> dict:
    return {"title": title, "flags": {"dealer_view": True}}


def test_a_dealer_roster_is_not_stamped():
    probe = {"answers": [_dealer_row("SRTWC286-SH: No ETA"), _dealer_row("SRTWC286-SH-150: \u2705 ETA 19/10/2026")]}
    out = pickers.annotate_incoming(dict(GATE), probe=probe)
    assert "incoming" not in out["escalate_message"].lower()
    assert out["escalate_message"] == GATE["gate_clarification"]
    assert out["incoming_by_code"] == {}


def test_a_staff_roster_is_still_stamped():
    probe = {"answers": [{"title": "SRTWC286-SH-150"}]}
    out = pickers.annotate_incoming(dict(GATE), probe=probe)
    assert "1. SRTWC286-SH - no incoming" in out["escalate_message"]
    assert "2. SRTWC286-SH-150 - has incoming" in out["escalate_message"]
