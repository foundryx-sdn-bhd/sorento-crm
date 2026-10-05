"""ORDER-BROADEN: the LIVE parser reads "all products" after a delivery MISS as the same
order enquiry, widened (owner, 5 Oct 2026, AVAILABILITY contact, prompt v42).

The owner's journey: "check delivery for last week" over HANLIM + SRTWC286-SH missed
("But no order from 28/09/2026 to 04/10/2026 matched these. Reply with another month or
dates ..."), then "all products" came back as domain_hint incoming and the dealer read
ETA lines. The engine never infers a domain from leftover focus, so the fix is the prompt
rule in `alembic/data/chatbot_semantic_parser.order-broaden.addendum.txt`, and whether the
model honours it is decided HERE, against the real provider:

  * the v42 production text (`chatbot_semantic_parser.prod-20261001.txt`), expected RED
    (xfail, not strict: a model that happens to get it right is not a failure);
  * the same text with the addendum applied (`_order_broaden_prompt.patched_text`),
    expected GREEN.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`ORDER_BROADEN_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_low_stock_live_parser.py`. The user block is the engine's own
(`head/parser.build_user_block`), with the focus the real turn 2 carries. Each case prints
its verdict (domain_hint / intent_hint / broaden_axis / broaden_to / entity_op / entities)
so a run's trace can be pasted on the PR.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from tests.chatbot import _order_broaden_prompt as ob

pytestmark = pytest.mark.live_external

TODAY = "Monday, 05 October 2026"

MISS_REPLY = (
    "Customer: HANLIM TRADING SDN BHD\nProduct: SRTWC286-SH\nDates: 28/09/2026 to 04/10/2026\n\n"
    "Here's what you want:\n• customer: HANLIM TRADING SDN BHD\n• product: SRTWC286-SH\n\n"
    "But no order from 28/09/2026 to 04/10/2026 matched these. Reply with another month or "
    "dates (e.g. August, or 15 Sep to 10 Oct). Please refer to your salesman."
)

#: What the engine's focus carries into turn 2 (`head/parser.current_subject_line`).
FOCUS = SimpleNamespace(
    domains=["order"],
    customers=[{"raw": "hanlim", "name": "HANLIM TRADING SDN BHD", "canonical_code": "300-H030"}],
    products=[{"raw": "srtwc286-sh", "name": "SRTWC286-SH", "canonical_code": "SRTWC286-SH"}],
    document=[],
    status=None,
    date_window={"mode": "range", "start": "2026-09-28", "end": "2026-10-04"},
    tasks=[],
)

RECENT = [
    ("check delivery for last week", MISS_REPLY),
]


def _config(system_text: str):
    from app.services.chatbot.head import parser

    return parser.ParserConfig(
        system_prompt=system_text.replace("{{current_date}}", TODAY),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("ORDER_BROADEN_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(system_text: str, message: str, *, label: str) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    block = parser.build_user_block(
        previous_response=MISS_REPLY,
        latest_user_message=message,
        pending_kind=None,
        focus=FOCUS,
        recent_exchanges=RECENT,
    )
    out = dict(parser.parse(_config(system_text), block))
    print(
        json.dumps(
            {
                "prompt": label,
                "message": message,
                "domain_hint": out.get("domain_hint"),
                "intent_hint": out.get("intent_hint"),
                "broaden_axis": out.get("broaden_axis"),
                "broaden_to": out.get("broaden_to"),
                "entity_op": out.get("entity_op"),
                "entities": out.get("entities"),
                "date_filter_start": out.get("date_filter_start"),
                "date_filter_end": out.get("date_filter_end"),
            },
            ensure_ascii=False,
        )
    )
    return out


def _assert_same_enquiry_widened(out: dict[str, Any], axis: str) -> None:
    assert out.get("domain_hint") == "order", out
    assert out.get("intent_hint") == "check_order", out
    assert out.get("broaden_axis") == axis, out
    assert out.get("broaden_to") == "all", out
    assert out.get("entity_op") == "reuse", out
    assert not out.get("entities"), out
    # The window stands: the message named no dates, so none are emitted.
    assert not out.get("date_filter_start") and not out.get("date_filter_end"), out


V42 = pytest.param("v42", marks=pytest.mark.xfail(reason="v42 wanders to incoming after a miss", strict=False))
PATCHED = pytest.param("patched")


def _text(label: str) -> str:
    return ob.prod_text() if label == "v42" else ob.patched_text()


@pytest.mark.parametrize("label", [V42, PATCHED])
def test_all_products_after_the_delivery_miss_is_the_same_order_enquiry(label: str) -> None:
    out = _parse(_text(label), "all products", label=label)
    _assert_same_enquiry_widened(out, "product")


@pytest.mark.parametrize("label", [V42, PATCHED])
def test_all_customers_after_the_delivery_miss_widens_the_customer_axis(label: str) -> None:
    out = _parse(_text(label), "all customers", label=label)
    _assert_same_enquiry_widened(out, "customer")


def test_any_date_after_the_delivery_miss_widens_the_date_axis_only() -> None:
    out = _parse(_text("patched"), "any date", label="patched")
    assert out.get("domain_hint") == "order", out
    assert out.get("intent_hint") == "check_order", out
    assert out.get("broaden_axis") == "date", out
    assert out.get("broaden_to") == "all", out
    assert out.get("entity_op") == "reuse", out


def test_a_dated_answer_to_the_miss_still_names_its_own_window() -> None:
    """The miss's own invite, answered: new dates, same enquiry, nothing widened."""
    out = _parse(_text("patched"), "15 Sep to 10 Oct", label="patched")
    assert out.get("domain_hint") == "order", out
    assert out.get("broaden_axis") in (None, "date"), out
    assert out.get("date_filter_start") == "2026-09-15", out
    assert out.get("date_filter_end") == "2026-10-10", out


def test_an_eta_ask_after_the_miss_still_moves_to_incoming() -> None:
    """The control: a real shipment question is still a domain switch under the rule."""
    out = _parse(_text("patched"), "any incoming for srtwc286-sh?", label="patched")
    assert out.get("domain_hint") == "incoming", out
