"""SO-PO-REF (crew, 12 Oct 2026): the parser intermittently copied a delivery order number it
ALSO emitted as an order entity into `po_refs`, minus its letters ("PS202609-0469" ->
po_refs ["202609-0469"]); the turn then took the PO-ref arm and a contact without the SO
grant was told "Sales order figures are not enabled".

Reviewer ruling (12 Oct): a code guard that strips a letters-only prefix is a heuristic over
parser text (our own DO numbers often carry no letters), so the PARSER decides: the v56
rebuild's PO REFS section says a number emitted as an order/document entity is never a
po_ref (`alembic/data/chatbot_semantic_parser.refkinds-v56-20261011.txt`, pinned by
`test_ref_kinds_v56_publish.py`). This file replays the parser on that text.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker). Model
`gpt-5.4-mini` at temperature 0, the production parser model.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_external

TODAY = "Monday, 12 October 2026"
TEXT = (
    Path(__file__).resolve().parents[2] / "alembic" / "data" / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
)

ASKS = [
    "PS202609-0469 done deliver?",
    "status PS202610-0123",
    "PS 202610-0123 delivered?",
    "PS202610-0777 sudah hantar?",
    "PS202609-0555 已送货吗",
    "order PS202610-0888 status",
]


def _fold(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _parse(message: str) -> dict:
    from app.services.chatbot.head import parser

    config = parser.ParserConfig(
        system_prompt=TEXT.read_text(encoding="utf-8").replace("{{current_date}}", TODAY),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("PO_REFS_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )
    block = parser.build_user_block(previous_response="(none)", latest_user_message=message, pending_kind=None)
    return dict(parser.parse(config, block))


@pytest.mark.parametrize("message", ASKS)
def test_an_order_entity_number_is_never_also_a_po_ref(message: str) -> None:
    out = _parse(message)
    order_keys = {
        _fold(str(e.get("raw") or ""))
        for e in out.get("entities") or []
        if isinstance(e, dict) and e.get("hint") not in ("purchase_order", "customer_po_ref", "product")
    }
    for ref in out.get("po_refs") or []:
        ref_key = _fold(ref)
        assert not any(key.endswith(ref_key) for key in order_keys if ref_key), (message, out.get("po_refs"))
