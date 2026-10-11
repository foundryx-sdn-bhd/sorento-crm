"""Phase 2 RED tests - REPORTS-STEP2 gate fixes (dev gate, 11 Oct 2026).

Three defects, each driven through the parser's output fields, never the message text:

(i)   a sales ranking ask with only a brand and a top_n (no product, customer or metric) reaches
      `crm_sales_report` and the route answers 422 `subject_required`; main's outcome is the top
      selling path, which asks "quantity or amount" and then refines the ranking.
(ii)  a permanent 4xx from a report route becomes "I could not fetch ... try again"; only a 5xx or
      a raised call is retryable.
(iii) an outstanding ask naming TWO typed product codes reports only the first.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_report_ask_lane import (  # noqa: F401  (fixtures by name)
    _e,
    _parser_output,
    console,
)

RETRY_LINE = "try again"
COULD_NOT_FETCH = "could not fetch"
PRODUCT_CODES = ("CWC7601", "CBDR4003")


# ================================================================================ (i) a


def test_i_a_brand_and_top_n_under_sales_report_is_a_top_selling_ask() -> None:
    from app.services.chatbot.turn import apply as ap
    from app.services.chatbot.turn.decide import CARRY, Decision
    from app.services.chatbot.turn.plan import Trace
    from app.services.chatbot.turn.state import Focus

    focus = Focus()
    brand = entity("Sorento", "brand")
    v = verdict(order_status="sales_report", domain_hint="sales", top_n=10, entities=[brand], domain_in_message=True)
    ap._top_selling_rules(focus, v, Decision(CARRY, "x"), Trace())
    assert focus.status == "top_selling", focus.status
    assert (focus.top_selling or {}).get("top_n") == 10, focus.top_selling


# ================================================================================ (i) b


def _payload(
    *, order_status: str, entities: list[dict[str, Any]], parse: dict[str, Any] | None = None,
    attributes: tuple[str, ...] = ("sales_orders.sales_report", "sales_orders.outstanding"),
) -> dict[str, Any]:
    return {
        "gate": {"compatible_entities": entities},
        "tier_gate": None,
        "ctx": {
            "contact": {"id": "1"},
            "access": {"attributes": list(attributes)},
            "parse": {
                "output": {
                    "domain_hint": "order", "intent_hint": "check_order", "message_type": "business_query",
                    "order_status": order_status, "entities": [], **(parse or {}),
                }
            },
        },
    }


def _run(session_factory, payload: dict[str, Any], mcp) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    calls: list[tuple[str, dict[str, Any]]] = []

    def _mcp(name: str, args: dict[str, Any]) -> Any:
        calls.append((name, args))
        return mcp(name, args)

    db = session_factory()
    try:
        return run_fetch(payload, services=FetchServices(mcp_call=_mcp), db=db), calls
    finally:
        db.close()


def _reply_text(fragment: dict[str, Any]) -> str:
    return str((fragment.get("fetch") or {}).get("response") or fragment.get("error") or "")


def test_i_b_a_brand_only_ranking_ask_never_calls_the_sales_report_route(session_factory) -> None:
    brand_id = str(uuid.uuid4())
    payload = _payload(
        order_status="sales_report",
        entities=[{"uuid": brand_id, "entity_type": "brand", "code": "ZZT-SOR", "canonical_code": "ZZT-SOR"}],
        parse={
            "domain_hint": "sales", "top_n": 10, "domain_in_message": True,
            "entities": [{"raw": "Sorento", "hint": "brand", "canonical_code": None, "current_message": True}],
        },
    )
    fragment, calls = _run(session_factory, payload, lambda n, a: json.dumps({"has_result": False, "items": []}))
    names = [n for n, _ in calls]
    assert "crm_sales_report" not in names and "crm_report" not in names, names
    assert fragment.get("kind") != "error", fragment
    assert COULD_NOT_FETCH not in _reply_text(fragment).lower(), fragment


# ================================================================================ (i) c


def test_i_c_brand_top_n_no_metric_asks_the_metric_then_the_refine_ranks_the_brand(console, monkeypatch) -> None:
    from tests.chatbot import test_report_ask_lane as lane

    def _ask(**kw: Any) -> dict[str, Any]:
        base: dict[str, Any] = dict(
            domain_hint="sales", intent_hint="check_order", order_status="sales_report",
            entities=[lane._hit("Sorento", "brand")], top_n=10, domain_in_message=True,
            rank_by=None, basis=None, measure=None, group_by=None, **lane.THIS_YEAR,
        )
        base.update(kw)
        return _parser_output(**base)

    text, log = lane._say_all(console, monkeypatch, _ask(), "top 10 sorento dealer")
    names = [n for n, _ in log]
    assert "crm_sales_report" not in names, (names, text)
    assert "quantity" in text.lower() and "amount" in text.lower(), text

    answer = _ask(
        order_status="top_selling", domain_hint="order", entities=[], domain_in_message=False,
        top_n=None, rank_by="amount", basis="delivered", date_filter_start=None, date_filter_end=None,
    )
    text, log = lane._say_all(console, monkeypatch, answer, "amount")
    ranked = [a for n, a in log if n in ("crm_top_selling_report", "crm_report")]
    assert ranked, ([n for n, _ in log], text)
    assert console.ids["brand"] in (ranked[0].get("brand_ids") or []), ranked[0]


# ================================================================================ (ii)


def _http_error(status: int, code: str, message: str) -> str:
    return json.dumps(
        {
            "result_type": "x",
            "error": f"GET /api/v1/x returned {status} {code}",
            "status_code": status,
            "detail": {"code": code, "message": message},
            "response": "", "answers": [], "attachments": [], "has_result": False,
        }
    )


def _sales_payload() -> dict[str, Any]:
    cid = str(uuid.uuid4())
    return _payload(
        order_status="sales_report",
        entities=[{"uuid": cid, "entity_type": "customer", "code": "ZZT-C", "canonical_code": "ZZT-C"}],
        parse={"entities": [{"raw": "ZZT-C", "hint": "customer", "canonical_code": None, "current_message": True}]},
    )


def _outstanding_payload() -> dict[str, Any]:
    pid = str(uuid.uuid4())
    return _payload(
        order_status="outstanding",
        entities=[{"uuid": pid, "entity_type": "product", "code": PRODUCT_CODES[0], "canonical_code": PRODUCT_CODES[0]}],
        parse={"entities": [{"raw": PRODUCT_CODES[0], "hint": "product", "canonical_code": PRODUCT_CODES[0], "current_message": True}]},
    )


@pytest.mark.parametrize("make", [_sales_payload, _outstanding_payload], ids=["sales_report", "outstanding"])
def test_ii_a_422_subject_required_is_a_clarify_not_a_retry(session_factory, make) -> None:
    body = _http_error(422, "subject_required", "Name a product or a customer.")
    fragment, calls = _run(session_factory, make(), lambda n, a: body)
    assert calls, "the route was never reached, so the 4xx path is not exercised"
    text = _reply_text(fragment).lower()
    assert fragment.get("kind") != "error", fragment
    assert RETRY_LINE not in text and COULD_NOT_FETCH not in text, text
    assert "product" in text and "customer" in text, text


@pytest.mark.parametrize("make", [_sales_payload, _outstanding_payload], ids=["sales_report", "outstanding"])
def test_ii_b_another_4xx_is_a_clear_line_never_the_retry_sentence(session_factory, make) -> None:
    body = _http_error(400, "invalid_period", "That period is not valid.")
    fragment, calls = _run(session_factory, make(), lambda n, a: body)
    assert calls, "the route was never reached, so the 4xx path is not exercised"
    text = _reply_text(fragment).lower()
    assert fragment.get("kind") != "error", fragment  # the error arm is composed into the retry sentence
    assert RETRY_LINE not in text and COULD_NOT_FETCH not in text, text
    assert text.strip(), fragment


def test_ii_c_guard_a_503_keeps_the_error_arm(session_factory) -> None:
    body = _http_error(503, "unavailable", "Try later.")
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert fragment.get("kind") == "error", fragment


def test_ii_c_guard_a_raised_call_keeps_the_error_arm(session_factory) -> None:
    def _boom(name: str, args: dict[str, Any]) -> Any:
        raise RuntimeError("upstream down")

    fragment, _calls = _run(session_factory, _sales_payload(), _boom)
    assert fragment.get("kind") == "error", fragment


# ================================================================================ (iii)


def _typed_products() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parse = {"entities": [{"raw": c, "hint": "product", "canonical_code": c, "current_message": True} for c in PRODUCT_CODES]}
    gated = [
        {"uuid": str(uuid.uuid4()), "entity_type": "product", "code": c, "canonical_code": c} for c in PRODUCT_CODES
    ]
    return parse, gated


def test_iii_a_two_typed_codes_both_reach_the_semantic_input() -> None:
    from app.services.chatbot.lanes.business import _resolve_report_product_and_location

    parse, gated = _typed_products()
    si: dict[str, Any] = {}
    _resolve_report_product_and_location(parse, gated, si, db=None)
    assert si.get("outstanding_product_codes") == list(PRODUCT_CODES), si


def test_iii_b_one_typed_code_is_unchanged() -> None:
    from app.services.chatbot.lanes.business import _resolve_report_product_and_location

    parse, gated = _typed_products()
    parse["entities"], gated = parse["entities"][:1], gated[:1]
    si: dict[str, Any] = {}
    _resolve_report_product_and_location(parse, gated, si, db=None)
    assert si.get("outstanding_product_code") == PRODUCT_CODES[0], si
    assert "outstanding_product_codes" not in si, si


def test_iii_c_the_outgoing_outstanding_args_carry_both_codes(session_factory) -> None:
    parse, gated = _typed_products()
    payload = _payload(order_status="outstanding", entities=gated, parse=parse)
    _fragment, calls = _run(session_factory, payload, lambda n, a: json.dumps({"has_result": False, "items": []}))
    sent = [a for n, a in calls if n == "crm_outstanding_report"]
    assert sent, [n for n, _ in calls]
    assert sent[0].get("product_codes") == list(PRODUCT_CODES), sent[0]


# ================================================================================ review round


REPORT_TOOLS = ("crm_sales_report", "crm_outstanding_report", "crm_report", "crm_report_ask")
OWN_ACCOUNT_LINE = "You can only see sales for your own account."


def _order_list_payload() -> dict[str, Any]:
    """An ask in a non-report domain (the product master): its tool is not a report tool."""
    pid = str(uuid.uuid4())
    return _payload(
        order_status="",
        entities=[{"uuid": pid, "entity_type": "product", "code": "ZZT-P", "canonical_code": "ZZT-P"}],
        parse={
            "domain_hint": "master_products", "intent_hint": "check_product",
            "entities": [{"raw": "ZZT-P", "hint": "product", "canonical_code": "ZZT-P", "current_message": True}],
        },
    )


def test_r1_a_422_on_a_non_report_tool_keeps_the_error_arm(session_factory) -> None:
    body = _http_error(422, "validation_error", "bad filter")
    fragment, calls = _run(session_factory, _order_list_payload(), lambda n, a: body)
    assert calls, "no tool was called, so the 4xx path is not exercised"
    assert not {n for n, _ in calls} & set(REPORT_TOOLS), calls
    assert fragment.get("kind") == "error", fragment


def test_r2_a_401_on_the_sales_report_is_the_error_arm(session_factory) -> None:
    body = _http_error(401, "unauthorized", "Not authenticated.")
    fragment, calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert [n for n, _ in calls] == ["crm_sales_report"], calls
    assert fragment.get("kind") == "error", fragment


def _forbidden() -> str:
    return _http_error(403, "customer_not_permitted", OWN_ACCOUNT_LINE)


def test_r3_a_403_route_line_is_said_as_the_route_wrote_it(session_factory) -> None:
    fragment, calls = _run(session_factory, _sales_payload(), lambda n, a: _forbidden())
    assert [n for n, _ in calls] == ["crm_sales_report"], calls
    assert fragment.get("kind") != "error", fragment
    assert _reply_text(fragment) == OWN_ACCOUNT_LINE, fragment


def test_r3_a_ms_contact_gets_the_route_line_localized(session_factory) -> None:
    from app.services.chatbot.label_catalog import Localizer

    malay = "Anda hanya boleh melihat jualan untuk akaun anda sendiri."
    payload = _sales_payload()
    payload["ctx"]["localizer"] = Localizer("ms", {OWN_ACCOUNT_LINE: malay})
    fragment, _calls = _run(session_factory, payload, lambda n, a: _forbidden())
    assert _reply_text(fragment) == malay, fragment


def test_r4_a_scoped_dealer_with_no_named_subject_still_gets_the_report(session_factory) -> None:
    link = str(uuid.uuid4())
    payload = _payload(order_status="sales_report", entities=[], parse={"domain_hint": "sales"})
    payload["ctx"]["customer_scope"] = {
        "ids": [link], "enforced": True, "refusal": "Sorry, that isn't under your account.",
    }
    fragment, calls = _run(session_factory, payload, lambda n, a: json.dumps({"has_result": False, "items": []}))
    assert "crm_sales_report" in [n for n, _ in calls], (calls, _reply_text(fragment))
    assert "which product or customer" not in _reply_text(fragment).lower(), fragment
