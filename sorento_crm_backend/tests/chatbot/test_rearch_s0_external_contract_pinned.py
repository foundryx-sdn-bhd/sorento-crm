"""S0 - the external contract (`TurnRequest`/`TurnResponse`/`CompleteRequest`/
`CompleteResponse`/`ACTION_KINDS`) is byte-identical to what shipped on `main` before
this lane started (AC-1507, PLAN-chatbot-turn-rearch.md).

The fixture (`tests/chatbot/fixtures/external_contract_main_f14802a7c.json`) was
captured by the tester FROM `origin/main` (`model_json_schema()` on each class plus
`list(ACTION_KINDS)`) at this lane's real branch point, `f14802a7c` - the captain
corrected the filename from the brief's `3009b374c` to match (ruling, 16 Sep 2026).

One deliberate change since the capture: CHAT-SPEED (18 Oct 2026) added `respond_webhook` to
`Envelope.ingress` for turns the CRM's own respond.io webhook starts. Additive; no n8n
caller sends it, and nothing else in the contract moved.

THIS AC IS EXPECTED GREEN RIGHT NOW, by design - it is the guard that later S0/S2/S3
slices must not silently reshape the outer-loop contract, not a red waiting on new work.
Said explicitly per the brief's own instruction.
"""
from __future__ import annotations

import json
import pathlib

from app.services.chatbot.contracts import (
    ACTION_KINDS,
    CompleteRequest,
    CompleteResponse,
    TurnRequest,
    TurnResponse,
)

FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "external_contract_main_f14802a7c.json"
)


def _pinned() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_file_exists():
    assert FIXTURE.exists(), FIXTURE


def test_turn_request_schema_matches_pinned():
    assert TurnRequest.model_json_schema() == _pinned()["TurnRequest"]


def test_turn_response_schema_matches_pinned():
    assert TurnResponse.model_json_schema() == _pinned()["TurnResponse"]


def test_complete_request_schema_matches_pinned():
    assert CompleteRequest.model_json_schema() == _pinned()["CompleteRequest"]


def test_complete_response_schema_matches_pinned():
    assert CompleteResponse.model_json_schema() == _pinned()["CompleteResponse"]


def test_action_kinds_matches_pinned():
    assert list(ACTION_KINDS) == _pinned()["ACTION_KINDS"]
