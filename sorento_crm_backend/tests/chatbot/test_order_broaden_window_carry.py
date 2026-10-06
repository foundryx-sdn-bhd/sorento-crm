"""ORDER-BROADEN: when `domain_change_drops_date_window` fires, and when it must not.

Reviewer blocker on PR #1495: the first cut of the rule fired on ANY change to the set of
domains, which dropped a window two kinds of turn expect to keep:

  * a sales report's plain follow-up: "sales report srtwc286-sh august" is the `sales`
    domain (`turn_runtime._report_status_means_order_domain` maps the report status), and
    "and srtwc286-sh-new?" comes back as a bare `order` refinement; the August window
    must still reach the orders tool;
  * a subset or superset of a multi-domain ask: "stock and orders in august" then "just
    the orders" (or the reverse) still names a carried domain.

The rule now reads `_left_every_carried_domain`: the window goes only when this turn's
domains share nothing with the carried ones, counting `order` and `sales` as one family.
Pure unit tests over `_focus_rules` (`decide` + `_focus_rules`, no engine, no database).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.turn import apply as apply_mod
from app.services.chatbot.turn.decide import decide
from app.services.chatbot.turn.plan import Trace
from app.services.chatbot.turn.state import Focus

from tests.chatbot._turn_helpers import entity, verdict

AUGUST = {"mode": "month", "start": "2026-08-01", "end": "2026-08-31"}
RULE = "domain_change_drops_date_window"


def _run(focus: Focus, v: dict[str, Any], *, domain_override: str | None = None) -> tuple[Focus, Trace]:
    trace = Trace()
    decision = decide(v, focus, None)
    out = apply_mod._focus_rules(
        focus, v, list(v.get("entities") or []), decision,
        domain_locked=False, domain_override=domain_override, trace=trace,
    )
    return out, trace


def _carried(domains: list[str], **extra: Any) -> Focus:
    return Focus(
        domains=domains,
        products=[{"raw": "srtwc286-sh", "canonical_code": "SRTWC286-SH", "uuid": "11111111-1111-1111-1111-111111111111"}],
        date_window=dict(AUGUST),
        **extra,
    )


class TestTheWindowIsKept:
    def test_a_sales_reports_plain_follow_up_keeps_the_report_window(self):
        focus, trace = _run(
            _carried(["sales"]),
            verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False,
                    entities=[entity("srtwc286-sh-new", hint="product")], entity_op="replace_combine"),
        )
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_the_same_domain_keeps_the_window(self):
        focus, trace = _run(
            _carried(["order"]),
            verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False, entities=[],
                    entity_op="reuse", broaden_axis="product", broaden_to="all"),
        )
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_a_subset_of_a_multi_domain_ask_keeps_the_window(self):
        focus, trace = _run(
            _carried(["order", "inventory"]),
            verdict(domain_hint="order", intent_hint="check_order", domain_in_message=True,
                    asks=[{"domain": "order", "intent": "check_order"}], entities=[], entity_op="reuse"),
        )
        assert focus.domains == ["order"]
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_a_superset_of_the_carried_domain_keeps_the_window(self):
        focus, trace = _run(
            _carried(["order"]),
            verdict(domain_hint="order", intent_hint="check_order", domain_in_message=True,
                    asks=[{"domain": "order", "intent": "check_order"}, {"domain": "inventory", "intent": "check_stock"}],
                    entities=[], entity_op="reuse"),
        )
        assert set(focus.domains) == {"order", "inventory"}
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_a_reconciliation_override_inside_the_family_keeps_the_window(self):
        focus, trace = _run(
            _carried(["order"]),
            verdict(domain_hint=None, intent_hint=None, domain_in_message=False, entities=[], entity_op="reuse"),
            domain_override="sales",
        )
        assert focus.domains == ["sales"]
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_a_ranking_drilled_into_its_report_keeps_the_period(self):
        focus, trace = _run(
            _carried(["sales"], status=apply_mod.TOP_SELLING_STATUS),
            verdict(domain_hint="order", intent_hint="check_order", domain_in_message=True, entities=[],
                    entity_op="reuse", order_status="outstanding"),
        )
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired

    def test_no_carried_domain_is_not_a_move(self):
        focus, trace = _run(
            _carried([]),
            verdict(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=False, entities=[],
                    entity_op="reuse"),
        )
        assert focus.date_window == AUGUST, trace.rules_fired
        assert RULE not in trace.rules_fired


class TestTheWindowIsDropped:
    @pytest.mark.parametrize("carried", [["order"], ["sales"], ["order", "sales"]])
    def test_the_owners_move_from_the_order_family_to_incoming_drops_it(self, carried):
        focus, trace = _run(
            _carried(carried),
            verdict(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=False, entities=[],
                    entity_op="reuse", broaden_axis="product", broaden_to="all"),
        )
        assert focus.domains == ["incoming"]
        assert focus.date_window is None, trace.rules_fired
        assert RULE in trace.rules_fired

    def test_a_dated_move_keeps_the_window_this_message_names(self):
        focus, trace = _run(
            _carried(["order"]),
            verdict(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
                    entities=[entity("srtwc286-sh", hint="product")], entity_op="replace_combine",
                    date_mode="month", date_filter_start="2026-10-01", date_filter_end="2026-10-31"),
        )
        assert focus.date_window == {"mode": "month", "start": "2026-10-01", "end": "2026-10-31"}
