"""DEV-FAILURES: the LIVE parser reads the shapes the RC-A and RC-B addenda teach
(documentation/plans/chatbot/PLAN-dev-failures-12oct.md).

Two texts: BASE is main's trimmed v56 body (`chatbot_semantic_parser.ablation-20261011.txt`),
PATCHED is what migration `devf_0001` publishes (v56 plus both addenda). The
messages are made up in the shapes of the DEV-set failures, never the real ones.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker), model
`DEV_FAILURES_LIVE_MODEL` (default `gpt-5.4-mini`, the production parser model), key
`OPENAI_API_KEY` or a placeholder where an egress proxy injects it. One call per case and text,
so a full run is 2 x len(CASES) calls (owner cost rule: at most 30 live calls per round).
`DEV_FAILURES_PROMPTS=patched` runs the patched text only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

pytestmark = pytest.mark.live_external

TODAY = "Monday, 12 October 2026"
DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"
BASE = DATA / "chatbot_semantic_parser.ablation-20261011.txt"
ADDENDA = ("chatbot_semantic_parser.dev-failures.addendum.txt", "chatbot_semantic_parser.portal-form.addendum.txt")
BRANDS = [{"brand_name": "Sorento", "brand_code": "SRT"}, {"brand_name": "Mocha", "brand_code": "MCH"}]
PROMPTS = [p for p in os.environ.get("DEV_FAILURES_PROMPTS", "base,patched").split(",") if p]


def _system_text(label: str) -> str:
    body = BASE.read_text(encoding="utf-8")
    if label == "patched":
        body = body.rstrip("\n") + "".join((DATA / a).read_text(encoding="utf-8") for a in ADDENDA)
    return body.replace("{{current_date}}", TODAY)


def _parse(label: str, message: str, previous: str) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    config = parser.ParserConfig(
        system_prompt=_system_text(label),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("DEV_FAILURES_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )
    block = parser.build_user_block(
        previous_response=previous, latest_user_message=message, pending_kind=None, brands=BRANDS
    )
    out = dict(parser.parse(config, block))
    print(json.dumps({
        "prompt": label, "message": message,
        **{k: out.get(k) for k in ("domain_hint", "intent_hint", "order_status", "status",
                                    "requested_attributes", "asks", "topic_reset", "portal_form")},
        "entities": [{k: e.get(k) for k in ("raw", "hint", "canonical_code")}
                     for e in (out.get("entities") or []) if isinstance(e, dict)],
    }, ensure_ascii=False))
    return out


def _hints(out: dict[str, Any], hint: str) -> list[dict[str, Any]]:
    return [e for e in (out.get("entities") or []) if isinstance(e, dict) and e.get("hint") == hint]


def _ask_domains(out: dict[str, Any]) -> set[str]:
    return {a.get("domain") for a in (out.get("asks") or []) if isinstance(a, dict)}


CASES: list[tuple[str, str, str, Callable[[dict[str, Any]], bool]]] = [
    ("eta_when_stock_ms", "MCX4410-GM bila boleh ada stok balik", "(none)",
     lambda o: o.get("domain_hint") == "incoming"),
    ("stock_and_last_in", "stock for MBT2207 and the last in pls",
     "Stock summary for the requested products.\n\n1. *Product Code:* MBT2205\n*Total:* 3",
     lambda o: {"inventory", "spo_allocation"} <= _ask_domains(o)),
    # Regression guard (crew gate 12 Oct, spo_allocation.last_in -20): a last-in-only ask stays ONE
    # spo_allocation ask, also right after a stock answer.
    ("last_in_only", "last in for MBT2207", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_in_count", "MBT2207 last 3 in", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_in_after_stock", "and the last in?",
     "Stock summary for the requested products.\n\n1. *Product Code:* MBT2207\n*Total:* 40",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_in_qty_words", "how many came in last time for MBT2207", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_stock_in", "when was the last stock in for MBT2207", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_in_stock_qty", "MBT2207 last in stock qty", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and not o.get("asks")),
    ("last_in_and_stock", "last in and stock for MBT2207", "(none)",
     lambda o: o.get("domain_hint") == "spo_allocation" and {"inventory", "spo_allocation"} <= _ask_domains(o)),
    ("did_customer_order_zh", "ZETA HARDWARE SDN BHD 有订过 SRTQ5501 吗", "(none)",
     lambda o: o.get("domain_hint") == "order" and bool(_hints(o, "customer"))
     and "quantity" in (o.get("requested_attributes") or [])),
    ("when_delivered_zh", "MCX7781-GM 什么时候安排送货？",
     "Product: MCX7781-GM\nCustomer: ZETA\n\n*Delivery order outstanding*\nOutstanding: 6",
     lambda o: o.get("domain_hint") == "order" and "outstanding" in (o.get("order_status"), o.get("status"))),
    ("brand_promotion_file", "Mocha promo file pls", "(none)",
     lambda o: o.get("domain_hint") == "promotion" and not _hints(o, "attachment")),
    ("sponsorship_form_typo", "sponsorshp form pls", "(none)",
     lambda o: o.get("domain_hint") == "portal_link" and o.get("portal_form") in (None, "sponsorship_form")),
    ("price_tag_request", "hello, need to order some price tags", "(none)",
     lambda o: o.get("domain_hint") == "portal_link" and o.get("portal_form") is None),
    ("portal_after_misses", "ok i want to submit a product inquiry",
     'Couldn\'t find: "QWE26-0011" (product). Would you like me to escalate to purchasing team?',
     lambda o: o.get("domain_hint") == "portal_link"
     and not any("QWE26" in str(e.get("raw")) for e in (o.get("entities") or []) if isinstance(e, dict))),
    ("two_brands_and", "best selling Mocha and Sorento basin this month", "(none)",
     lambda o: {"Sorento", "Mocha"} <= {e.get("canonical_code") for e in _hints(o, "brand")}),
]


@pytest.mark.parametrize("label", PROMPTS)
@pytest.mark.parametrize("name,message,previous,check", CASES, ids=[c[0] for c in CASES])
def test_dev_failure_shape(label: str, name: str, message: str, previous: str, check) -> None:
    out = _parse(label, message, previous)
    ok = bool(check(out))
    print(f"DEV-FAILURES-EVAL case={name} prompt={label} pass={int(ok)}/1")
    if label == "base" and not ok:
        pytest.xfail("v56 misses this shape (expected red on base)")
    assert ok, f"{name} on {label}"
