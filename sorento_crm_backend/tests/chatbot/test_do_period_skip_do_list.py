"""DO-PERIOD-SKIP AC-7 to AC-9 and AC-13: the DO list ask for a contact with "Skip date-range
questions" on. Driven through `run_fetch` like `test_do_ask_range.py`, with `ctx.access`."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.services.chatbot import do_ask
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business.services import FetchServices

_CID = "6b52807a-537b-437d-9f55-12f7fda29df8"
_ROWS = json.dumps(
    {
        "intro": "Here are the orders I found.",
        "items": [{"title": "202609-0916", "fields": [{"label": "Order Number", "value": "202609-0916"}]}],
        "has_result": True,
    }
)
_ORDER_TOOLS = {"crm_order_management_orders_list", "crm_order_management_orders_by_product_list"}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))


def _ctx(*, skip, start=None, end=None, access="default"):
    ctx = {
        "parse": {
            "output": {
                "message_type": "business_query",
                "domain_hint": "order",
                "date_filter_start": start,
                "date_filter_end": end,
                "requested_attributes": [],
                "order_status": None,
            }
        },
        "contact": {"id": "900000008"},
        "customer_scope": {
            "enforced": True,
            "ids": [_CID],
            "linked": [[_CID, "ZETAWARE TILES SDN BHD [A/C I]", "300-H001"]],
        },
    }
    if access == "default":
        ctx["access"] = {"skip_period_questions": skip}
    return ctx


def _run(ctx):
    calls: list[tuple[str, dict]] = []

    def mcp_call(name, args):
        calls.append((name, dict(args)))
        return _ROWS

    payload = {
        "_exit_kind": "continue",
        "gate": {
            "compatible_entities": [
                {"uuid": _CID, "entity_type": "customer", "code": "300-H001",
                 "display_name": "ZETAWARE TILES SDN BHD [A/C I]"}
            ]
        },
        "ctx": ctx,
    }
    fragment = business.run_fetch(payload, services=FetchServices(mcp_call=mcp_call), dry_run=False)
    return json.dumps(fragment, default=str), [c for c in calls if c[0] in _ORDER_TOOLS]


def test_ac8_on_and_no_dates_asks_nothing_and_fetches_with_no_date_filter():
    said, calls = _run(_ctx(skip=True))
    assert calls, said
    assert "Which period" not in said, said
    dated = {k: v for k, v in calls[0][1].items() if "date" in k.lower() and v}
    assert dated == {}, calls[0]


def test_ac9_on_and_a_nine_month_range_is_fetched_with_that_range_no_cap_reply():
    said, calls = _run(_ctx(skip=True, start="2026-01-01", end="2026-09-30"))
    assert calls, said
    assert "I can show up to" not in said and "Which period" not in said, said
    sent = json.dumps(calls[0][1])
    assert "2026-01-01" in sent and "2026-09-30" in sent, calls[0]


def test_ac9_on_and_a_start_with_no_end_far_back_is_fetched():
    said, calls = _run(_ctx(skip=True, start="2026-01-01"))
    assert calls and "I can show up to" not in said, said


def test_ac7_off_and_no_dates_still_asks_which_period():
    said, calls = _run(_ctx(skip=False))
    assert not calls, calls
    assert "Which period for ZETAWARE TILES SDN BHD [A/C I]?" in said, said


def test_ac7_off_and_a_nine_month_range_still_gets_the_cap_reply():
    said, calls = _run(_ctx(skip=False, start="2026-01-01", end="2026-09-30"))
    assert not calls, calls
    assert "I can show up to 31 days of delivery orders at a time" in said, said


def test_ac7_a_ctx_with_no_access_behaves_as_off():
    said, calls = _run(_ctx(skip=True, access=None))
    assert not calls and "Which period" in said, said


def test_ac8_range_reply_returns_none_when_the_helper_is_true_and_the_reply_when_false():
    base = dict(
        tool_name="crm_order_management_orders_list",
        order_tools=_ORDER_TOOLS,
        entities=[],
        semantic_input={"date_filter_start": None, "date_filter_end": None},
    )
    assert do_ask.range_reply(ctx={"access": {"skip_period_questions": True}}, **base) is None
    assert "Which period" in do_ask.range_reply(ctx={"access": {"skip_period_questions": False}}, **base)
    long = dict(base, semantic_input={"date_filter_start": "2026-01-01", "date_filter_end": "2026-09-30"})
    assert do_ask.range_reply(ctx={"access": {"skip_period_questions": True}}, **long) is None
    assert do_ask.range_reply(ctx={"access": {}}, **long) is not None


def test_ac13_the_customer_scope_backstop_still_refuses_first_with_the_switch_on(session_factory, monkeypatch):
    from tests.chatbot.test_customer_scope_lane import (
        OWN_A, _ask, _hanlim_services, _link_customers, refusal,
    )
    from tests.chatbot.test_customer_scope_lane import _turn
    from tests.chatbot.test_customer_scope_lane import _seed_contact
    from tests.chatbot._dps_helpers import SkipSwitchMonkeypatch

    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN_A)
    from tests.chatbot.test_customer_scope_lane import _give_access_type

    _give_access_type(session_factory, "Sorento Dealer")
    from tests.chatbot.test_customer_scope_lane import _ent

    qf = _ask([_ent("zetaware")], order_status=None, date_filter_start=None, date_filter_end=None)
    reply, captured = _turn(
        session_factory, SkipSwitchMonkeypatch(monkeypatch, True), qf, resolve_services=_hanlim_services(),
    )
    assert reply.strip() == refusal(OWN_A), reply
    assert captured == []
