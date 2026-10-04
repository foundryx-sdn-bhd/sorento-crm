"""Phase 2 RED tests - S4 lane wiring for the sales report.

`documentation/plans/chatbot/PLAN-chatbot-sales-report.md` "S4 wiring" (10 numbered
points) + "Tester's list"; `chatbot-sales-report-acceptance-criteria.md` AC-1641
(backend half) and AC-1650 to AC-1660. Written BEFORE any of the wiring exists - S1
(presenter) and S2 (route/service) are already merged on this branch; S3 (MCP
catalog/reveal-key/bootstrap) is covered by `test_catalog_sales_report.py` and
`test_sales_report_bootstrap.py`.

Built on the SAME fake-fetch / lane-function harness
`tests/chatbot/test_outstanding_lane.py` uses, imported directly rather than copied:
`_qf`, `_resolve_services`, `_capturing_mcp`, `_enable_business_lane`,
`_wire_business_services`, `_session_of`, `_run_turn`, `_seed_contact`,
`_ambiguous_hanlim_resolve_services`, `_all_pick_parser_output`, the constants
`PRODUCT_UUID`/`PRODUCT_CODE`/`CUSTOMER_UUID`/`CUSTOMER_NAME`/`HANLIM_UUID_1`/
`HANLIM_UUID_2`/`HANLIM_CODE_1`/`HANLIM_CODE_2`, and `REPORT_HIT` (the outstanding
report's own mock body, reused for the pinning half of AC-1650). These names carry a
leading underscore by CONVENTION in that file, not by any Python enforcement, so the
import is a plain cross-module import, same as `test_field_reveal_keys_pinned_to_
catalog.py` importing the MCP catalogue across the tree boundary. Nothing here is
copied that is actually importable; the module docstring says so per file at each
point new fixture data was authored instead.

**Tester's own choices, made explicit** (the plan/UAC describe the behaviour, not
every session-var name - same latitude `test_outstanding_lane.py` itself took):

* The plan's S4 point 7 says the detail offer's "stored filter set" gains a `tool`
  key so the pick knows which tool to re-run "absent = crm_outstanding_report". This
  file assumes that stored filter set is the SAME session variable the outstanding
  lane already writes, `outstanding_filters` - not a second, sales-report-only key -
  because S4 point 7 explicitly asks for ONE shared mechanism, not two. `filters_out
  ["tool"] == "crm_sales_report"` is this file's own contract for that field.
* `DETAIL_OFFER_KINDS` (S4 point 7's own proposed name) is asserted only indirectly,
  through `contracts.PENDING_KINDS` gaining the literal `"sales_report_detail"` (the
  `Pending.kind` field is a `Literal[PENDING_KINDS]`, so an ungeneralised set would
  reject the write outright) and through the parametrized lifecycle tests below,
  which exercise `output_exchange._apply_outstanding_pending`, `tail/pending.py::
  derive` and `compile_state._offer_carry`'s own literal checks (all three grepped
  and confirmed still reading `in ("outstanding_scope", "outstanding_detail")`
  verbatim as of this writing) without asserting a private constant name for it.
* S4 point 2's "Carry" step persists `sales_channel` beside `order_status` on the
  SAME R16 arm that already persists `order_status` for an interrupted outstanding
  ask (see `_seed_open_outstanding_customer_pick`'s own docstring in the outstanding
  file: "the seed carries order_status... and pending: None"). This file's own
  picker-continuation seed (`_seed_open_sales_report_customer_pick`) therefore
  persists `sales_channel` at the SAME top level `order_status` already sits at, as
  the shape a real turn would leave behind once that carry is generalised.
* `crm_sales_report`'s own `_capturing_mcp` responses are NOT rendered through the
  real MCP presenter here (unlike `test_outstanding_lane.py`'s `REPORT_HIT`, which
  goes through `_present_response()` because `crm_outstanding_report` already
  dispatches inside `present_response`): S3 wires that dispatch
  (`test_catalog_sales_report.py`), and until it lands a generic-tool response is
  just JSON-passthrough the same way `test_outstanding_lane.py`'s own
  `TestWarehouseOnAPlainOrderAsk` feeds a bare dict to a non-special-cased tool. Every
  assertion below reads the MCP CALL ARGS and the SESSION STATE, never the exact
  composed reply text for a hit - the same discipline the outstanding file's module
  docstring states for the same reason.

Postgres only (`session_factory`, blank schema). Every row seeded here.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    HANLIM_CODE_1,
    HANLIM_CODE_2,
    HANLIM_UUID_1,
    HANLIM_UUID_2,
    PRODUCT_CODE,
    PRODUCT_UUID,
    REPORT_HIT,
    _all_pick_parser_output,
    _ambiguous_hanlim_resolve_services,
    _capturing_mcp,
    _qf,
    _run_turn,
    _seed_contact,
    _session_of,
)
from tests.chatbot.test_engine import _parser_output

SALES_REPORT_DENIAL = "Sales report is not enabled for your account."

# The route's own response shape since the delivered basis (lane SALES-REPORT, PR #1401,
# AC-SR-23): `total`, `periods[]`, and the drill `options[]`. One account, so no "By customer".
# `_capturing_mcp` pushes it through the real presenter, exactly as production does.
SALES_REPORT_HIT: dict[str, Any] = {
    "status": "ok",
    "message": None,
    "basis": "delivered",
    "customer_name": CUSTOMER_NAME,
    "customer_count": 1,
    "product_code": None,
    "product_codes": [],
    "channel": None,
    "channel_shown": False,
    "location_token": None,
    "warehouse_codes": [],
    "date_from": None,
    "date_to": None,
    "grain": "month",
    "total": {"qty": 10, "amount": 100.0},
    "periods": [{"from": "2026-09-01", "to": "2026-09-30", "qty": 10, "amount": 100.0}],
    "group_by": None,
    "rows": [],
    "more": 0,
    "options": [
        {"key": "product", "label": "By product"},
        {"key": "delivery_order", "label": "Delivery orders"},
    ],
}

#: The same body for several accounts: By customer joins the options.
SALES_REPORT_HIT_MULTI: dict[str, Any] = {
    **SALES_REPORT_HIT,
    "customer_count": 2,
    "options": [
        {"key": "customer", "label": "By customer"},
        {"key": "product", "label": "By product"},
        {"key": "delivery_order", "label": "Delivery orders"},
    ],
}

#: The same body with NOTHING delivered (AC-1658): no periods, no options.
SALES_REPORT_MISS: dict[str, Any] = {
    **SALES_REPORT_HIT,
    "total": {"qty": 0, "amount": 0.0},
    "periods": [],
    "options": [],
}

#: A refused body (AC-SR-25): a location outside the contact's policy. Arms nothing.
SALES_REPORT_REFUSED: dict[str, Any] = {
    **SALES_REPORT_MISS,
    "status": "refused",
    "message": "Sorry, REPAIR isn't one of the locations you can check.",
}


# --------------------------------------------------------------------------- #
# AC-1650 - tool pick: sales_report vs every outstanding order_status
# --------------------------------------------------------------------------- #


class TestToolPickSalesVsOutstanding:
    """Through `run_fetch` directly - the real call site, not a re-implementation of
    the pick."""

    def _payload(self, *, order_status: str, entities: list[dict[str, Any]], attributes: list[str]):
        return {
            "gate": {"compatible_entities": entities},
            "tier_gate": None,
            "ctx": {
                "parse": {"output": _qf(order_status=order_status, entities=[])},
                "contact": {"id": 437264483},
                "access": {"attributes": attributes},
            },
        }

    def test_sales_report_with_resolved_customer_picks_sales_report_tool(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(SALES_REPORT_HIT)
        payload = self._payload(
            order_status="sales_report",
            entities=[{"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}],
            attributes=["sales_orders.sales_report"],
        )
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, _args = captured[0]
        assert name == "crm_sales_report", (
            f"domain=order + a resolved customer + order_status=sales_report must pick "
            f"crm_sales_report, not {name!r} (S4 wiring point 3)"
        )

    def test_sales_report_with_resolved_product_picks_sales_report_tool(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(SALES_REPORT_HIT)
        payload = self._payload(
            order_status="sales_report",
            entities=[{"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}],
            attributes=["sales_orders.sales_report"],
        )
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, _args = captured[0]
        assert name == "crm_sales_report", name

    @pytest.mark.parametrize(
        "order_status",
        ["outstanding", "so_outstanding", "do_outstanding", "outstanding_both"],
    )
    def test_every_outstanding_status_still_picks_the_outstanding_report(
        self, session_factory, order_status: str
    ) -> None:
        """Pin: none of the four existing outstanding asks may be redirected to the
        new tool. Expected GREEN today (this is the outstanding lane's own, already
        shipped, S4 point 2 override) - a red here would mean the new branch was
        written too broadly."""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        payload = self._payload(
            order_status=order_status,
            entities=[{"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}],
            attributes=["sales_orders.outstanding"],
        )
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, (order_status, "no MCP tool was ever called")
        name, _args = captured[0]
        assert name == "crm_outstanding_report", (order_status, name)


class TestDateParamsTable:
    def test_crm_sales_report_uses_date_from_to_params(self) -> None:
        assert fetch_mod.DATE_PARAMS.get("crm_sales_report") == ("date_from", "date_to"), (
            f"S4 wiring point 5: {fetch_mod.DATE_PARAMS.get('crm_sales_report')!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1651 - the sales_orders.sales_report gate, total denial, before any fetch
# --------------------------------------------------------------------------- #


class TestNoKeyDeniesBeforeFetch:
    def test_no_key_denies_before_any_fetch(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="sales_report"),
            text_body="sales report for SRTWT7445",
            msg_id="ZZT-sales-report-no-key-1",
            attributes=[],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        )
        assert captured == [], (
            f"there is no fallback scope here (S4 wiring point 4) - absent the grant "
            f"NOTHING may be fetched: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.strip() == SALES_REPORT_DENIAL, reply
        # Session-shape port (AC-1592, 16-17 Sep 2026, see test_outstanding_lane.py's
        # module docstring): `pending`/`selection_context` are this file's OLD names -
        # `_session_of` now returns the flat session_vars dict directly (no `variables`
        # nesting) and an armed offer lives at `open_question`. A total denial before
        # any fetch must leave NO open question at all.
        stored = _session_of(session_factory)
        assert not stored.get("open_question"), stored.get("open_question")

    def test_with_the_key_the_fetch_runs_once(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="sales_report"),
            text_body="sales report for SRTWT7445",
            msg_id="ZZT-sales-report-with-key-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert len(captured) == 1, captured
        assert captured[0][0] == "crm_sales_report", captured


# --------------------------------------------------------------------------- #
# AC-1652 - the parser's channel value only, never the message text
# --------------------------------------------------------------------------- #


class TestChannelFromParserOnly:
    def _payload(self, *, sales_channel: Any, attributes=("sales_orders.sales_report",)):
        return {
            "gate": {
                "compatible_entities": [
                    {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": _qf(order_status="sales_report", entities=[], sales_channel=sales_channel)
                },
                "contact": {"id": 437264483},
                "access": {"attributes": list(attributes)},
            },
        }

    @pytest.mark.parametrize("sales_channel,expected", [("dealer", "dealer"), ("project", "project")])
    def test_channel_maps_to_the_tool_param(self, session_factory, sales_channel, expected) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(SALES_REPORT_HIT)
        run_fetch(self._payload(sales_channel=sales_channel), services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        _name, args = captured[0]
        assert args.get("channel") == expected, args

    def test_absent_channel_sends_no_param_at_all(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(SALES_REPORT_HIT)
        run_fetch(self._payload(sales_channel=None), services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        _name, args = captured[0]
        assert "channel" not in args, args

    def test_the_word_dealer_in_the_message_is_ignored_when_the_parser_sends_none(
        self, session_factory, monkeypatch
    ) -> None:
        """The lane never reads the message text for the channel - only
        `sales_channel` in the parser's own output."""
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="sales_report", sales_channel=None),
            text_body="dealer sales report for SRTWT7445",
            msg_id="ZZT-sales-report-channel-text-trap-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must still run"
        _name, args = captured[0]
        assert "channel" not in args, (
            f"the word 'dealer' in the text must never reach the tool on its own: {args}"
        )


# --------------------------------------------------------------------------- #
# AC-1653 - parsed dates/customer/location/product map to the tool's own params
# --------------------------------------------------------------------------- #


class TestParamMapping:
    def test_dates_customer_warehouse_and_product_all_map(self, session_factory, monkeypatch) -> None:
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add_all(
            [
                Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW-IB", warehouse_name="BRW IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="MWH-IB", warehouse_name="MWH IB", is_active=True),
            ]
        )
        db.commit()
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="sales_report",
                date_filter_start="2026-01-01",
                date_filter_end="2026-01-31",
                entities=[
                    {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                    {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                    {"raw": "IB", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="dealer sales report for SRTWT7445 hanlim IB january 2026",
            msg_id="ZZT-sales-report-param-map-1",
            attributes=["sales_orders.sales_report"],
            matches={
                PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME},
            },
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert args.get("date_from") == "2026-01-01", args
        assert args.get("date_to") == "2026-01-31", args
        assert args.get("customer_ids") == [CUSTOMER_UUID], args
        assert set(args.get("warehouse_codes") or []) == {"BRW-IB", "MWH-IB"}, args
        assert args.get("product_code") == PRODUCT_CODE, (
            f"product through outstanding_product_code's typed-code-wins rule: {args}"
        )


# --------------------------------------------------------------------------- #
# AC-1654 - a hit arms the sales_report_detail offer
# --------------------------------------------------------------------------- #


class TestHitArmsSalesReportDetail:
    def test_hit_arms_sales_report_detail(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="sales_report"),
            text_body="sales report for SRTWT7445",
            msg_id="ZZT-sales-report-hit-arm-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the sales report must be fetched"
        reply = (result.reply or {}).get("text") or ""
        assert reply.strip(), "a hit must produce a non-empty reply"
        assert "would you like me to escalate" not in reply.lower(), (
            f"a hit must never also offer to escalate: {reply!r}"
        )
        # Session-shape port (AC-1592, see test_outstanding_lane.py's module
        # docstring): `pending`/`selection_context`/`last_result_set`/
        # `outstanding_filters` are this file's OLD names for what `open_question` now
        # carries whole - `.kind` is both the old `selection_context` and
        # `pending.kind`, `.options` is `last_result_set` (one option per row, its
        # `payload.value` the old row's bare `value`), and `.payload.filters` is
        # `outstanding_filters` (measured directly, not guessed).
        stored = _session_of(session_factory)
        open_question = stored.get("open_question") or {}
        assert open_question.get("kind") == "sales_report_detail", open_question
        # AC-SR-28: the offer is the envelope's own `options` (one account, so no By
        # customer), never a regex over the reply text.
        options = open_question.get("options") or []
        assert [o.get("label") for o in options] == ["By product", "Delivery orders"], options
        assert [o.get("payload", {}).get("value") for o in options] == ["product", "delivery_order"], options
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("tool") == "crm_sales_report", (
            f"tester's own naming choice (module docstring): the stored filter set "
            f"must carry which tool the offer belongs to: {filters_out}"
        )
        assert filters_out.get("product_code") == PRODUCT_CODE, filters_out


# --------------------------------------------------------------------------- #
# AC-1655 - the detail offer's lifecycle, parametrized so the outstanding_detail
# arm PINS today's behaviour and only sales_report_detail is expected red.
# --------------------------------------------------------------------------- #

KINDS = ("outstanding_detail", "sales_report_detail")

_KIND_TOOL = {"outstanding_detail": "crm_outstanding_report", "sales_report_detail": "crm_sales_report"}
_KIND_ATTRS = {
    "outstanding_detail": ["sales_orders.outstanding"],
    "sales_report_detail": ["sales_orders.sales_report"],
}
_KIND_MOCK_HIT = {"outstanding_detail": REPORT_HIT, "sales_report_detail": SALES_REPORT_HIT}
_KIND_ROWS = {
    "outstanding_detail": [
        {"idx": 1, "label": "Sales order list", "value": "so"},
        {"idx": 2, "label": "Delivery order list", "value": "do"},
    ],
    "sales_report_detail": [
        {"idx": 1, "label": "By product", "value": "product"},
        {"idx": 2, "label": "Delivery orders", "value": "delivery_order"},
    ],
}
#: What a pick of position 1 re-runs the tool with, per kind: the outstanding report's
#: `detail` argument, the sales report's `group_by` (`detail=so` is retired, AC-SR-23).
_KIND_PICK_ARG = {
    "outstanding_detail": ("detail", "so"),
    "sales_report_detail": ("group_by", "product"),
}
_PRODUCT_MATCH = {PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}}


def _seed_open_detail(
    session_factory, kind: str, *, subject: str = "product", channel: str | None = None
) -> None:
    """One open detail offer, either kind. `subject="customer"` seeds a customer
    subject with no product - used by the refinement test that names a product
    under it (AC-1656's third case). `channel`, given, stores it on the filter
    set the SAME way `_sales_report_filters_from_ctx` (fetch.py) writes it when
    a hit first arms the offer (R-B3, Phase 3 fix round) - default None keeps
    every existing call site (which never passes it) byte-identical to before."""
    filters: dict[str, Any] = {
        "product_code": PRODUCT_CODE if subject == "product" else None,
        "date_filter_start": None,
        "date_filter_end": None,
        "customer_ids": [] if subject == "product" else [CUSTOMER_UUID],
        "warehouse_codes": [],
        "location_token": None,
    }
    if kind == "sales_report_detail":
        filters["tool"] = "crm_sales_report"
    if kind == "outstanding_detail":
        # `turn/apply.py:298`/`turn/decide.py:321` read `filters["scope"]` on a
        # refinement re-run (R15) - `TestDateNarrowingUnderAnOpenOffer`'s own seed in
        # test_outstanding_lane.py (`_seed_open_outstanding_detail`) carries it for the
        # same reason; both `KINDS` rows here offer "so"/"do" from a report that read
        # BOTH documents, matching `REPORT_HIT`'s own shape.
        filters["scope"] = "both"
    if channel is not None:
        filters["channel"] = channel
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [],
            "selection_context": kind,
            "last_result_set": _KIND_ROWS[kind],
            "outstanding_filters": filters,
            "pending": {"kind": kind},
        },
    )


class TestDetailOfferLifecycle:
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_pick_reruns_the_kinds_own_tool_with_its_pick_argument(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        _seed_open_detail(session_factory, kind)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id=f"ZZT-{kind}-pick-1",
            attributes=_KIND_ATTRS[kind],
            matches=_PRODUCT_MATCH,
            mcp_response=_KIND_MOCK_HIT[kind],
        )
        assert captured, (kind, "the detail pick must re-run the tool")
        name, args = captured[0]
        assert name == _KIND_TOOL[kind], (kind, name)
        arg, value = _KIND_PICK_ARG[kind]
        assert args.get(arg) == value, (kind, args)
        if kind == "sales_report_detail":
            assert "detail" not in args, ("`detail=so` is retired", args)

    @pytest.mark.parametrize("kind", KINDS)
    def test_offer_survives_a_pick(self, session_factory, monkeypatch, kind: str) -> None:
        _seed_open_detail(session_factory, kind)
        _r1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1", msg_id=f"ZZT-{kind}-sticky-1",
            attributes=_KIND_ATTRS[kind], matches=_PRODUCT_MATCH,
            mcp_response=_KIND_MOCK_HIT[kind],
        )
        assert captured1 and captured1[0][1].get(_KIND_PICK_ARG[kind][0]) == _KIND_PICK_ARG[kind][1], (
            kind, captured1,
        )
        # Session-shape port: `pending`/`variables` -> `open_question` (see
        # test_outstanding_lane.py's module docstring).
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == kind, (
            kind, "the offer must still be open after one pick", open_question,
        )

    @pytest.mark.parametrize("kind", KINDS)
    def test_offer_survives_a_casual_turn(self, session_factory, monkeypatch, kind: str) -> None:
        """KEPT RED, confirmed a real still-open defect, not ported around (20 Sep
        2026): `test_rearch_s6_open_question_parser.py::
        TestAbsentAnswerCarriesThePendingWithoutReprinting` proves `apply()` itself
        already returns an EMPTY `plan.fetch` for a casual message over an open
        pending (its own AC-1593 docstring names the finding this test polices) - but
        a REAL two-turn probe through `engine.run_turn` (armed by a genuine turn 1,
        not `_seed_open_detail`'s hand-written dict, to rule out a seeding artifact)
        still shows `crm_order_management_orders_list` fetched on the casual turn,
        and the `sales_report_detail`/`outstanding_detail` `open_question` gets
        REPLACED by a fresh `team_pick` escalate offer - the original offer is lost,
        not merely re-fetched. The gap sits somewhere between `apply()` and the
        fetch call (`turn/decide.py`/`engine.py` orchestration), not inside `apply()`
        itself. Flagged for a coder pass; not this tester's fix to make."""
        _seed_open_detail(session_factory, kind)
        _r1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying thanks",
            ),
            text_body="thanks", msg_id=f"ZZT-{kind}-casual-1",
            attributes=_KIND_ATTRS[kind],
        )
        assert captured1 == [], (kind, captured1)
        # Session-shape port: `pending`/`variables` -> `open_question`.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == kind, (kind, open_question)

    @pytest.mark.skip(
        reason=(
            "retired premise, not a port gap (20 Sep 2026): this test's whole claim "
            "is 'an unreadable turn reprints the offer once, then a second one closes "
            "it' - the COORDINATOR RULING of 17 Sep 2026 (test_outstanding_lane.py's "
            "module docstring, 'S6 cluster 4') amends this away for EVERY pending kind "
            "including sales_report_detail: an open question is answered only when "
            "the PARSER says so, and a casual aside carries the pending SILENTLY, "
            "never re-prints it and never closes it. The same-shaped tests this file "
            "mirrors in test_outstanding_lane.py (test_detail_offer_survives_a_casual_"
            "turn, test_a_casual_turn_under_an_open_offer_reprints_it_rather_than_"
            "greeting, test_the_reprint_uses_the_same_offer_form_the_report_used) were "
            "already retired the same way, with the ruling's own replacement coverage "
            "named: test_rearch_s6_open_question_parser.py::"
            "TestAbsentAnswerCarriesThePendingWithoutReprinting, parametrized over "
            "every PENDING_KINDS value (sales_report_detail included). Kept here, "
            "skipped rather than silently deleted, so the retirement is traceable."
        )
    )
    @pytest.mark.parametrize("kind", KINDS)
    def test_an_unreadable_turn_reprints_once_then_the_second_closes_it(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        _seed_open_detail(session_factory, kind)
        result1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying something else",
            ),
            text_body="hmm", msg_id=f"ZZT-{kind}-reprint-1",
            attributes=_KIND_ATTRS[kind],
        )
        assert captured1 == [], (kind, captured1)
        reply1 = (result1.reply or {}).get("text") or ""
        assert "Sales order list" in reply1, (kind, reply1)
        stored1 = _session_of(session_factory)["variables"]
        assert (stored1.get("pending") or {}).get("kind") == kind, (kind, stored1.get("pending"))

        from app.services.chatbot.lanes import casual as casual_mod

        monkeypatch.setattr(casual_mod, "resolve_clarifier_config", lambda db, **_: object())
        monkeypatch.setattr(
            casual_mod, "call_clarifier",
            lambda config, user_prompt: '{"response": "Hi! How can I help you today?"}',
        )
        result2, captured2 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying hi",
            ),
            text_body="hi", msg_id=f"ZZT-{kind}-reprint-2",
            attributes=_KIND_ATTRS[kind],
            extra_completed_lanes=["low_signal"],
        )
        assert captured2 == [], (kind, captured2)
        reply2 = (result2.reply or {}).get("text") or ""
        assert "Sales order list" not in reply2, (kind, reply2)
        stored2 = _session_of(session_factory)["variables"]
        assert stored2.get("pending") is None, (kind, stored2.get("pending"))

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_decline_closes_it_with_the_offer_declined_copy(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        """Measured 20 Sep 2026, a real still-open porting gap, kept RED rather than
        forced: `run_fetch`'s own `outstanding_offer_declined` branch (`lanes/business/
        __init__.py:999`, R22(a)'s "Okay, noted." registry copy) is old-engine code the
        new turn engine never reaches for this shape - `turn/apply.py:617` routes ANY
        `is_affirmative: false` with no entities to the generic `escalation_declined`
        lane ("Escalation declined.") regardless of what `open_question` kind was
        open, even with `outstanding_offer_declined: True` also set on the verdict
        (probed directly). Declining a SPECIFIC open detail offer (as opposed to a
        generic escalation offer) has no seam in the new engine yet - flagged for a
        coder pass, not this tester's fix to make."""
        _seed_open_detail(session_factory, kind)
        result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", is_affirmative=False, reference_positions=[],
                entities=[], domain_hint=None, entity_op="reuse",
                outstanding_offer_declined=True,
            ),
            text_body="no", msg_id=f"ZZT-{kind}-decline-1",
            attributes=_KIND_ATTRS[kind],
        )
        assert captured == [], (kind, captured)
        reply = (result.reply or {}).get("text") or ""
        assert reply.strip() == "Okay, noted.", (
            kind, "R22(a)'s own registry copy, chatbot_reply_copy's offer_declined key", reply,
        )
        # Session-shape port: `pending`/`variables` -> `open_question`.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != kind, (kind, open_question)


# --------------------------------------------------------------------------- #
# AC-1656 - refinement (dates / location / a second axis) vs a genuine new ask
# --------------------------------------------------------------------------- #


class TestRefinementAndNewAsk:
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_date_only_turn_reruns_with_no_detail_and_rearms_the_offer(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        _seed_open_detail(session_factory, kind)
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id=f"ZZT-{kind}-date-refine-1",
            attributes=_KIND_ATTRS[kind],
            mcp_response=_KIND_MOCK_HIT[kind],
        )
        assert captured, (kind, "a date-only refinement must re-run the report")
        name, args = captured[0]
        assert name == _KIND_TOOL[kind], (kind, name)
        assert "detail" not in args, (kind, args)
        assert "group_by" not in args, (kind, "a date-only refinement re-runs the default report", args)
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") == kind, (kind, stored.get("open_question"))

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_location_word_reruns_with_no_detail(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW", warehouse_name="BRW", is_active=True))
        db.commit()
        _seed_open_detail(session_factory, kind)
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None,
                entity_op="replace_combine",
                entities=[
                    {"raw": "BRW", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
                ],
                reference_positions=[],
            ),
            text_body="only BRW",
            msg_id=f"ZZT-{kind}-location-refine-1",
            attributes=_KIND_ATTRS[kind],
            mcp_response=_KIND_MOCK_HIT[kind],
        )
        assert captured, (kind, "a location-word refinement must re-run the report")
        name, args = captured[0]
        assert name == _KIND_TOOL[kind], (kind, name)
        assert "detail" not in args, (kind, args)

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_product_named_on_a_customer_report_is_a_refinement(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        """Measured while writing this test (not assumed): this sub-case is RED for
        BOTH kinds today, `outstanding_detail` included - it is not a pre-existing
        pin. `_apply_outstanding_pending` freezes a refinement's own entities as
        `outstanding_refinement_entities` and keeps them OFF `o["entities"]" (R17),
        but `run_fetch`'s product-code resolution only ever reads `parse_output.
        get("entities")` for a typed product code - `outstanding_refinement_entities`
        is read ONLY for a `hint == "warehouse"` entity (see the loop right after "D5/
        AC-1105: one location word per turn" in `lanes/business/__init__.py::
        run_fetch`). So a location word survives a refinement and a PRODUCT named on
        a customer-subject report does not - it resolves nothing and the report reruns
        with the stored customer alone. This is a shared gap in the R15 mechanism
        itself, not something this lane introduces; fixing it (reading a product hint
        out of `outstanding_refinement_entities` too) benefits both kinds at once."""
        _seed_open_detail(session_factory, kind, subject="customer")
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None,
                entity_op="replace_combine",
                entities=[
                    {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                ],
                reference_positions=[],
            ),
            text_body=f"only {PRODUCT_CODE}",
            msg_id=f"ZZT-{kind}-product-refine-1",
            attributes=_KIND_ATTRS[kind],
            matches=_PRODUCT_MATCH,
            mcp_response=_KIND_MOCK_HIT[kind],
        )
        assert captured, (kind, "a product on a customer report is a refinement, not a drop")
        name, args = captured[0]
        assert name == _KIND_TOOL[kind], (kind, name)
        assert args.get("customer_ids") == [CUSTOMER_UUID], (kind, args)
        assert args.get("product_code") == PRODUCT_CODE, (kind, args)
        assert "detail" not in args, (kind, args)
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") == kind, (kind, stored.get("open_question"))

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_business_query_with_a_domain_hint_is_a_new_ask_and_drops_the_offer(
        self, session_factory, monkeypatch, kind: str
    ) -> None:
        _seed_open_detail(session_factory, kind)
        other_uuid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        _result, _captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", domain_hint="order", intent_hint="check_order",
                requested_attributes=["delivery"], order_status=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
                entity_op="replace_combine", reference_positions=[],
            ),
            text_body="delivery status for hanlim",
            msg_id=f"ZZT-{kind}-r24-new-ask-1",
            attributes=_KIND_ATTRS[kind],
            matches={"hanlim": {"uuid": other_uuid, "entity_type": "customer", "canonical_code": "HANLIM TRADING SDN BHD"}},
        )
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != kind, (
            kind, "a business question of its own (R24) must drop the offer", stored.get("open_question"),
        )


# --------------------------------------------------------------------------- #
# AC-1657 - the ambiguous-customer picker on a sales_report ask
# --------------------------------------------------------------------------- #


def _seed_open_sales_report_customer_pick(session_factory) -> None:
    """UNUSED as of 20 Sep 2026 - `TestPickerNoDoHintAndPickContinues`'s two pick
    tests were re-armed with a REAL turn 1 instead (measured: this hand-written
    `variables` dict never reaches a real `customer_pick` answer, the same
    "invisible to the current engine" class of gap `test_outstanding_lane.py`'s
    module docstring names for its own hand-seeded fixtures). Kept, not deleted -
    the shape is still useful reference for what the OLD engine's picker-
    continuation session looked like."""
    roster = [
        {"idx": 1, "label": "HANLIM TRADING SDN BHD (SRT)", "uuid": HANLIM_UUID_1, "product": HANLIM_CODE_1, "entity_type": "customer"},
        {"idx": 2, "label": "HANLIM TRADING (JB) SDN BHD (SRT)", "uuid": HANLIM_UUID_2, "product": HANLIM_CODE_2, "entity_type": "customer"},
    ]
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [],
            "selection_context": "disambiguation",
            "last_result_set": roster,
            "picker_last_result_set": roster,
            "picker_selection_context": "disambiguation",
            "picker_domain": "order",
            "outstanding_filters": {
                "product_code": None,
                "date_filter_start": "2026-01-01",
                "date_filter_end": "2026-12-31",
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
            },
            "order_status": "sales_report",
            # Tester's own naming choice (module docstring): persisted by the SAME
            # R16 carry arm that already persists order_status here.
            "sales_channel": "dealer",
            # SEED CHANGED BY THE CODER (measured against
            # `test_outstanding_lane.py::_seed_open_outstanding_three_family_picker`,
            # every assertion below left untouched): the reuse arm's date carry
            # (`head/output_exchange.py` ~2046-2051) reads the TOP-LEVEL
            # `date_filter_start`/`date_filter_end` session vars, never the nested
            # `outstanding_filters` copy - that is the shape a genuine "... in 2026"
            # ask persists BEFORE a picker interrupts it, and the sibling outstanding
            # file's own three-family-picker seed carries both top-level fields for
            # the identical reason (its own docstring names the same R16 key).
            "date_filter_start": "2026-01-01",
            "date_filter_end": "2026-12-31",
            "pending": None,
        },
    )


class TestPickerNoDoHintAndPickContinues:
    def test_sales_report_ask_picker_has_no_do_hint_and_does_not_probe(
        self, session_factory, monkeypatch
    ) -> None:
        probe_calls: list[dict[str, Any]] = []

        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: Any, user_prompt: Any) -> Any:
            probe_calls.append({"tool": tool})
            return {
                "items": [
                    {
                        "title": "SO1",
                        "fields": [{"key": "customer_name", "label": "Customer", "value": "HANLIM TRADING SDN BHD"}],
                    }
                ],
                "has_result": True,
            }

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="sales_report",
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim",
            msg_id="ZZT-sales-report-r20-picker-1",
            attributes=["sales_orders.sales_report"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        assert captured == [], captured
        reply = (result.reply or {}).get("text") or ""
        assert "Which customer do you mean?" in reply, reply
        assert " - no DO" not in reply, reply
        assert " - has DO" not in reply, reply
        assert "None of these have" not in reply, reply
        assert probe_calls == [], (
            f"a sales_report ask's picker must not probe for a delivery order at all: {probe_calls}"
        )

    def test_pick_one_continues_the_sales_report_with_the_original_filters(
        self, session_factory, monkeypatch
    ) -> None:
        """Re-armed with a REAL turn 1 (20 Sep 2026), not `_seed_open_sales_report_
        customer_pick`'s hand-written `variables` dict - see the class module's own
        history for that reasoning. `tool`/`customer_ids` FIXED (coder 30:
        `answer_bridge._offer_answer` stamps `payload["status"]` from the asking
        turn's own `order_status`, and `turn/apply.py::_answer_pending` carries it back
        onto `focus.status`), GREEN on those two axes.

        DEFECT ADJUDICATION (tester 34, 20 Sep 2026, coder 30's own report): the
        `date_from`/`date_to`/`channel` assertions below were themselves wrong, not a
        code gap. MEASURED verbatim: `lanes/business/fetch.py:672-689`'s own S18 comment
        ("a PRODUCT-ONLY ask ... with no date window defaults to the CURRENT CALENDAR
        YEAR ... a customer ask, or a customer+product ask, with no date stays all
        dates (S4, unchanged)") and its own code guard
        (`jsc.truthy(out.get("product_code")) and not jsc.truthy(out.get("customer_ids"))`)
        - this scenario is a CUSTOMER-only ask (no product_code), so the current-year
        default explicitly does NOT apply; production correctly leaves `date_from`/
        `date_to` unset. `channel` has no default mechanism anywhere in the codebase
        (`semantic_input.sales_channel` or `.outstanding_carried_channel`, both absent
        here, are the only two readers, `fetch.py:661-665`) - no access-type-to-channel
        mapping exists for `crm_sales_report`. Re-pinned to what production actually,
        correctly does."""
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="sales_report",
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim",
            msg_id="ZZT-sales-report-pick-arm-1",
            attributes=["sales_orders.sales_report"],
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        assert (_session_of(session_factory).get("open_question") or {}).get("kind") == (
            "customer_pick"
        ), "setup: the customer picker must be armed before the pick answer"

        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse",
                order_status=None,
            ),
            text_body="1", msg_id="ZZT-sales-report-pick-1",
            attributes=["sales_orders.sales_report"],
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, (
            "the pick must directly re-run the sales report - there is no scope "
            "question to arm first (S4 wiring point 6)"
        )
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("customer_ids") == [HANLIM_UUID_1], args
        assert not args.get("date_from") and not args.get("date_to"), (
            "S18 (fetch.py:672-689): a customer-only ask with no date stays ALL DATES - "
            f"a current-year default only applies to a product-only ask: {args!r}"
        )
        assert not args.get("channel"), (
            f"no channel-default mechanism exists for this ask's inputs: {args!r}"
        )

    def test_pick_all_continues_the_sales_report_for_every_family(
        self, session_factory, monkeypatch
    ) -> None:
        """Re-armed with a REAL turn 1, same reasoning as
        `test_pick_one_continues_the_sales_report_with_the_original_filters` above - see
        its docstring, including the `date_from`/`date_to`/`channel` re-pin to S18's own
        documented "customer ask stays all dates" rule."""
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="sales_report",
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim",
            msg_id="ZZT-sales-report-pick-all-arm-1",
            attributes=["sales_orders.sales_report"],
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_all_pick_parser_output(),
            text_body="all", msg_id="ZZT-sales-report-pick-all-1",
            attributes=["sales_orders.sales_report"],
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the pick-all must directly re-run the sales report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("customer_ids") == [HANLIM_UUID_1, HANLIM_UUID_2], args
        assert not args.get("date_from") and not args.get("date_to"), (
            "S18 (fetch.py:672-689): a customer-only ask with no date stays ALL DATES - "
            f"a current-year default only applies to a product-only ask: {args!r}"
        )
        assert not args.get("channel"), (
            f"no channel-default mechanism exists for this ask's inputs: {args!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1658 - a miss takes the presenter's own not-found path
# --------------------------------------------------------------------------- #


class TestMissTakesNotFoundPath:
    def test_miss_takes_the_not_found_path(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(
                order_status="sales_report",
                entities=[
                    {"raw": "SRTWT9999", "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for SRTWT9999",
            msg_id="ZZT-sales-report-miss-1",
            attributes=["sales_orders.sales_report"],
            matches={"SRTWT9999": {"uuid": "ffffffff-0000-0000-0000-000000000000", "entity_type": "product", "canonical_code": "SRTWT9999"}},
            mcp_response=SALES_REPORT_MISS,
        )
        assert captured, "the report must still be fetched"
        reply = (result.reply or {}).get("text") or ""
        assert "escalate" in reply.lower(), (
            f"a total miss must reach the existing escalate offer: {reply!r}"
        )
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != "sales_report_detail", (
            f"a miss offers no detail list: {stored.get('pending')!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1659 - the generic search-scope header is skipped for crm_sales_report
# --------------------------------------------------------------------------- #


class TestHeaderSkipped:
    def test_generic_search_scope_header_is_skipped(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(order_status="sales_report"),
            text_body="sales report for SRTWT7445",
            msg_id="ZZT-sales-report-header-skip-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith("*Customer:*"), (
            f"the report's own header must be the first thing in the reply: {reply!r}"
        )
        assert "Dates: all dates" not in reply, (
            f"the generic search-scope header must not print above the report: {reply!r}"
        )
        assert "Customer: all customers" not in reply, (
            f"the generic header's own wording must not print above the report's: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1660 - the parser prompt teaches sales_report / sales_channel, and the
# contracts that must allowlist them
# --------------------------------------------------------------------------- #


class TestParserPromptAndContractsTeachSalesReport:
    def test_prompt_teaches_the_sales_report_status_and_channel_field(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        prompt = prompt_mod.SEMANTIC_PARSER_PROMPT
        assert "sales_report" in prompt, "the prompt must teach the sales_report order_status value"
        assert "sales_channel" in prompt, "the prompt must teach the new sales_channel output field"
        assert "dealer" in prompt, prompt
        assert "project" in prompt, prompt

    def test_prompt_states_dealer_inside_an_outstanding_ask_is_not_a_channel(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        prompt = prompt_mod.SEMANTIC_PARSER_PROMPT.lower()
        assert "not a channel" in prompt or "never a channel" in prompt, (
            "S12/UAC S8: the prompt must say in words that 'dealer' inside an "
            "outstanding ask is not this field - no word table in Python may stand "
            "in for this sentence"
        )

    def test_sales_channel_is_declared_on_the_parser_output_schema(self) -> None:
        """Retired `head/output_exchange.py` (AC-1592, S0): the retired module's
        `_EXEMPT_FROM_REQUIRED` is now `head/parser.py::TOLERATED_ABSENT`, the SAME
        set both `parser.parse`'s own emission check and `engine.run_turn`'s harness
        bypass read through the module-level `assert_emission` (coder 25's own
        pins table)."""
        from app.services.chatbot.head import parser as parser_mod

        schema = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
        assert "sales_channel" in schema["properties"], (
            f"sales_channel must be a declared parser output key: {sorted(schema['properties'])}"
        )
        # `additionalProperties: false` + the provider's strict schema mode
        # (`llm_provider.py`'s `strict: True`) rejects a `properties` key absent from
        # `required` outright, so `sales_channel` MUST be required at the wire - and,
        # exactly like `group_by` / `top_n`, exempted from the post-processor's OWN
        # required-key check so an ask that never emits it (every ask that is not a
        # sales report, and any published prompt that predates it) still post-processes.
        assert "sales_channel" in schema["required"], (
            "sales_channel must be required in the strict json_schema sent to the "
            "provider, or the live parser call 400s"
        )
        assert "sales_channel" in parser_mod.TOLERATED_ABSENT, (
            "sales_channel must be exempted from assert_emission's own required-key "
            "check the same way group_by/top_n are, or a turn that never emits it "
            "(every non-sales-report ask) fails to post-process"
        )

    def test_an_emission_without_sales_channel_still_post_processes(self) -> None:
        """The wire compatibility half: `sales_channel` is required-but-exempt (schema
        `required`, `parser.TOLERATED_ABSENT`), mirroring `group_by` / `top_n`
        (`test_parser_growth_r1_reachability.py::
        test_a_pre_growth_r1_emission_still_post_processes`) - a published prompt
        version that predates the key, or simply a non-sales-report ask, must not fail
        a turn for lacking it."""
        from app.services.chatbot.head.parser import assert_emission

        # `asks`/`topic_reset` are supplied explicitly: `_parser_output`'s shared base
        # dict predates both (an unrelated, pre-existing fixture gap) and they are NOT
        # in `TOLERATED_ABSENT` - only `sales_channel` is meant to be absent here.
        emission = _parser_output(asks=[], topic_reset=False)
        assert "sales_channel" not in emission
        assert_emission(emission)  # must not raise

    def test_sales_channel_is_allowlisted_on_session_vars(self) -> None:
        """S4 wiring point 2's 'Carry' step, re-homed onto the turn re-architecture's
        session shape (AC-1504): `SessionVars` (the five-key shape, `extra='forbid'`)
        carries no bare `sales_channel` of its own any more - the axis lives on the
        SAME `Focus` (`session_vars.focus`) that carries `status`, `products`,
        `customers`, ... (contracts.py:504-543). `LegacyVariables.sales_channel`
        (the pre-rearch flat shape) also still exists, kept for the parked #930 lane -
        both are checked so neither the legacy nor the current home silently drops the
        field."""
        from app.services.chatbot import contracts as contracts_mod

        assert "sales_channel" in contracts_mod.Focus.model_fields, (
            "sales_channel must be a declared Focus field (extra='forbid') - the "
            "carried-axis home `session_vars.focus` gives it in the turn "
            "re-architecture - or the tail's write of it is silently dropped"
        )
        assert "sales_channel" in contracts_mod.LegacyVariables.model_fields, (
            "sales_channel must stay a declared LegacyVariables field too, for the "
            "parked #930 lane's own five-key shape"
        )

    def test_sales_report_detail_is_an_allowed_pending_kind(self) -> None:
        from app.services.chatbot import contracts as contracts_mod

        assert "sales_report_detail" in contracts_mod.PENDING_KINDS, (
            f"Pending.kind is Literal[PENDING_KINDS] - writing "
            f"pending={{'kind': 'sales_report_detail'}} raises a validation error "
            f"until this tuple is generalised (S4 wiring point 7's DETAIL_OFFER_KINDS): "
            f"{contracts_mod.PENDING_KINDS}"
        )

    def test_the_addendum_is_appended_to_the_single_body(self) -> None:
        """Coder 25's merge resolution (`e421d6127`): `SEMANTIC_PARSER_PROMPT_SLIM`
        stays RETIRED on this lane (S0 dropped the dev/prod dual-body split; only ONE
        body ships now, `SEMANTIC_PARSER_PROMPT`) - main's own edit to the SLIM body
        is deliberately dropped, so the original "both texts ship" premise (dev vs
        prod split) no longer applies. `STOCK_TASK_ADDENDUM`, `SALES_ANALYSIS_ADDENDUM`
        (#1267 S1), `QUANTITY_ADDENDUM` (issue #1262 slice 5, 26 Sep 2026),
        `KNOWN_BRANDS_ADDENDUM` (issue #1262 slice 9, 26 Sep 2026), then
        `TOP_SELLING_ADDENDUM` (PLAN-chatbot-top-x-hot-selling-24sep.md S4), then
        `ESCALATION_CONFIRMATION_ADDENDUM` (#1323) and `MEMORY_ADDENDUM` (chatbot memory
        lane A, migration `mem_0002_parser_memory`) now stack after it, newest outermost,
        so `SALES_REPORT_ADDENDUM` is the tail of the one body that exists once those are
        stripped, checked by `.removesuffix` rather than a bare `.endswith`."""
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            ESCALATION_CONFIRMATION_ADDENDUM,
            SELF_REFERENCE_ADDENDUM,
            KNOWN_BRANDS_ADDENDUM,
            MEMORY_ADDENDUM,
            LOW_STOCK_FILTERS_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            QUANTITY_ADDENDUM,
            SALES_ANALYSIS_ADDENDUM,
            SALES_REPORT_ADDENDUM,
            SEMANTIC_PARSER_PROMPT,
            SPECIFICATION_ADDENDUM,
            STOCK_TASK_ADDENDUM,
            TOP_SELLING_ADDENDUM,
        )

        assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM), (
            "SEMANTIC_PARSER_PROMPT does not end with MEMORY_ADDENDUM - it is "
            "the newest addendum, so it is the tail"
        )
        # `STOCK_TASK_ADDENDUM` (ported from PR #1118, not merged, chatbot-stock-ask-v2
        # S3), `SALES_ANALYSIS_ADDENDUM` (#1267 S1), `SPECIFICATION_ADDENDUM` (fix round
        # 8 on PR #833), the #1262 pair, `TOP_SELLING_ADDENDUM`, then
        # ESCALATION_CONFIRMATION_ADDENDUM (#1323), then MEMORY_ADDENDUM stacked after this
        # one, newest outermost, so they come off first.
        assert SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM).endswith(
            ESCALATION_CONFIRMATION_ADDENDUM
        )
        assert (
            SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM)
            .removesuffix(ESCALATION_CONFIRMATION_ADDENDUM).removesuffix(SELF_REFERENCE_ADDENDUM)
            .removesuffix(TOP_SELLING_ADDENDUM)
            .removesuffix(KNOWN_BRANDS_ADDENDUM)
            .removesuffix(QUANTITY_ADDENDUM)
            .removesuffix(SPECIFICATION_ADDENDUM)
            .removesuffix(SALES_ANALYSIS_ADDENDUM)
            .removesuffix(STOCK_TASK_ADDENDUM)
            .endswith(SALES_REPORT_ADDENDUM)
        ), (
            "SEMANTIC_PARSER_PROMPT does not end with SALES_REPORT_ADDENDUM once the "
            "newer MEMORY_ADDENDUM/ESCALATION_CONFIRMATION_ADDENDUM/TOP_SELLING_ADDENDUM/"
            "KNOWN_BRANDS_ADDENDUM/QUANTITY_ADDENDUM/SALES_ANALYSIS_ADDENDUM/STOCK_TASK_ADDENDUM "
            "are stripped - SALES_REPORT_ADDENDUM must stay the tail beneath them"
        )

    def test_the_addendum_stacks_after_low_stock(self) -> None:
        """The ORDER the existing pins' strip chains assume (`test_parser_prompt_is_live`,
        `test_parser_growth_r1_reachability`, `test_parser_low_stock_words`). A new
        addendum inserted anywhere but the end makes all of them wrong.

        `SEMANTIC_PARSER_PROMPT_SLIM` stays retired on this lane (see
        `test_the_addendum_is_appended_to_the_single_body` above) - one body, not two."""
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            KNOWN_BRANDS_ADDENDUM,
            LOW_STOCK_ADDENDUM,
            ESCALATION_CONFIRMATION_ADDENDUM,
            MEMORY_ADDENDUM,
            LOW_STOCK_FILTERS_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            SELF_REFERENCE_ADDENDUM,
            QUANTITY_ADDENDUM,
            SALES_ANALYSIS_ADDENDUM,
            SALES_REPORT_ADDENDUM,
            SEMANTIC_PARSER_PROMPT,
            SPECIFICATION_ADDENDUM,
            STOCK_TASK_ADDENDUM,
            TOP_SELLING_ADDENDUM,
        )

        assert (
            SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM)
            .removesuffix(ESCALATION_CONFIRMATION_ADDENDUM).removesuffix(SELF_REFERENCE_ADDENDUM)
            .removesuffix(TOP_SELLING_ADDENDUM)
            .removesuffix(KNOWN_BRANDS_ADDENDUM)
            .removesuffix(QUANTITY_ADDENDUM)
            .removesuffix(SPECIFICATION_ADDENDUM)
            .removesuffix(SALES_ANALYSIS_ADDENDUM)
            .removesuffix(STOCK_TASK_ADDENDUM)
            .removesuffix(SALES_REPORT_ADDENDUM)
            .endswith(LOW_STOCK_ADDENDUM)
        ), "SALES_REPORT_ADDENDUM must stack AFTER LOW_STOCK_ADDENDUM"


# --------------------------------------------------------------------------- #
# R-B3 (reviewer finding, Phase 3 fix round): the stored `outstanding_filters`
# for a `sales_report_detail` offer carries no `channel` key restoration at all
# - `head/output_exchange.py::_apply_outstanding_pending` restores product_code
# (as `outstanding_carried_product_code`), customer_ids, warehouse_codes and
# location_token from the stored filter set on a pick or a refinement, but
# never `channel`; `fetch.py`'s own arg builder (`channel = jsc.get(semantic_
# input, "sales_channel")`) only ever reads THIS TURN's own parser output, so a
# "project" channel report silently answers "all channels" the moment the
# customer picks the detail list or narrows the dates. Three fresh turns (one
# insert per `_seed_contact` call - the fixture has no upsert), not one
# re-seeded turn.
# --------------------------------------------------------------------------- #


class TestChannelSurvivesPickAndRefinement:
    def test_a_pick_carries_the_stored_channel(self, session_factory, monkeypatch) -> None:
        _seed_open_detail(session_factory, "sales_report_detail", channel="project")
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1", msg_id="ZZT-sales-report-channel-pick-1",
            attributes=["sales_orders.sales_report"], matches=_PRODUCT_MATCH,
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the pick must re-run the report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("channel") == "project", (
            "the stored channel must survive a pick of the detail offer", args,
        )
        assert args.get("group_by") == "product", args
        assert "detail" not in args, args

    def test_a_dates_only_refinement_carries_the_stored_channel(self, session_factory, monkeypatch) -> None:
        _seed_open_detail(session_factory, "sales_report_detail", channel="project")
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-sales-report-channel-date-refine-1",
            attributes=["sales_orders.sales_report"],
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "a date-only refinement must re-run the report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("channel") == "project", (
            "the stored channel must survive a dates-only refinement", args,
        )
        assert "detail" not in args, args

    def test_a_refinement_turns_own_channel_overlays_the_stored_one(self, session_factory, monkeypatch) -> None:
        """The turn's OWN `sales_channel` already wins today - `fetch.py` reads it
        directly and nothing currently clears it - so this sub-case is reported
        green-by-design; it is written because the captain's list asked for it as
        the third leg of the SAME finding, not because it is expected red."""
        _seed_open_detail(session_factory, "sales_report_detail", channel="project")
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                sales_channel="dealer",
                user_goal="trying to switch to dealer this month",
            ),
            text_body="dealer only, this month",
            msg_id="ZZT-sales-report-channel-overlay-1",
            attributes=["sales_orders.sales_report"],
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "a refinement naming its own channel must still re-run the report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("channel") == "dealer", (
            "the turn's OWN sales_channel must overlay the stored one, not be "
            f"overridden by it: {args}"
        )


# --------------------------------------------------------------------------- #
# R-S4 (reviewer finding, Phase 3 fix round): `_resolve_report_product_and_
# location`'s LAST fallback (`lanes/business/__init__.py`, right after the
# typed-code match loop) takes the FIRST product-hint entity's RAW TEXT off
# `outstanding_refinement_entities` UNCONDITIONALLY - it never checks whether
# the entity resolver actually matched it to a real product. A refinement
# naming a word the resolver could not match ("cheaper") is sent straight
# through as `product_code=cheaper` instead of leaving the stored customer
# subject alone. Shared by BOTH detail kinds (the same function resolves the
# outstanding override's own product too), so parametrized like the sibling
# `test_a_product_named_on_a_customer_report_is_a_refinement` this mirrors.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS)
def test_an_unresolvable_product_word_in_a_refinement_is_ignored(
    session_factory, monkeypatch, kind: str
) -> None:
    """KEPT RED, measured 20 Sep 2026 for BOTH kinds equally (so not a sales-report-
    only artifact, and not a seeding gap - `_seed_open_detail`'s own `scope` fix
    already lands the sibling refinement tests green for outstanding_detail): the
    resolver's own diagnostics correctly flag "cheaper" as `missing_or_bad_uuid`
    (`skipped`), but the fetch args still carry `product_code: "cheaper"` verbatim -
    the raw unresolved token reaches the tool call instead of being dropped. A real,
    still-open defect in the shared refinement product_code derivation, not a fixture
    bug; flagged for a coder pass."""
    _seed_open_detail(session_factory, kind, subject="customer")
    _result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_parser_output(
            message_type="business_query", intent_hint=None, domain_hint=None,
            entity_op="replace_combine",
            entities=[
                {"raw": "cheaper", "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ],
            reference_positions=[],
        ),
        text_body="something cheaper",
        msg_id=f"ZZT-{kind}-unresolvable-product-refine-1",
        attributes=_KIND_ATTRS[kind],
        matches={},  # "cheaper" resolves to nothing
        mcp_response=_KIND_MOCK_HIT[kind],
    )
    assert captured, (kind, "a refinement the resolver cannot match must still re-run with the stored subject")
    name, args = captured[0]
    assert name == _KIND_TOOL[kind], (kind, name)
    assert args.get("customer_ids") == [CUSTOMER_UUID], (
        kind, "the stored customer subject must survive an unresolvable refinement", args,
    )
    assert not args.get("product_code"), (
        kind, "an unresolved product word must never be sent verbatim as product_code", args,
    )


# --------------------------------------------------------------------------- #
# R-nit (reviewer finding, Phase 3 fix round): the sales_report no-key denial
# (`_sales_report_not_enabled()`) never calls `trace.add`, unlike the
# whole-domain grant refusal it sits beside in the same module
# (`trace.add("domain_grant", {"domain": domain, "skipped": "not_granted",
# "needs": need})`) - mirrors `test_last_cost_gate.py`'s own assertion of that
# same shape for a different key (`purchase_orders.cost`).
# --------------------------------------------------------------------------- #


def _turn_row(session_factory, turn_id: str):
    from app.models.chatbot_turn import ChatbotTurn

    return session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


def test_denial_records_a_trace_event(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="sales_report"),
        text_body="sales report for SRTWT7445",
        msg_id="ZZT-sales-report-trace-denial-1",
        attributes=[],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
    )
    assert captured == [], captured
    trace = (_turn_row(session_factory, result.turn_id).trace) or []
    skip_events = [
        e
        for e in trace
        if isinstance(e, dict)
        and e.get("skipped") == "not_granted"
        and e.get("needs") == "sales_orders.sales_report"
    ]
    assert skip_events, f"no not_granted trace event found for the sales_report denial: {trace!r}"


# --------------------------------------------------------------------------- #
# S18 (owner ruling, 19 Sep 2026, mid-lane): a PRODUCT-ONLY sales report ask (a
# resolved product, NO customer) with NO date window defaults to the CURRENT
# CALENDAR YEAR (Malaysia time); a customer ask, or a customer+product ask,
# with no date stays all dates (S4 unchanged). The default is built by the
# LANE (param building for crm_sales_report), never the route - n8n calling the
# route directly with no dates still means all dates. "All dates" in words
# (`broaden_axis == "date"`, the SAME field R15's date-window-drop already
# reads) turns the default off; no word table.
#
# The expected year is computed the SAME way the engine computes "today" for
# the parser prompt (`engine._current_date_directive`: UTC+8, no DST) rather
# than hardcoded, so this file does not go stale on 1 January.
# --------------------------------------------------------------------------- #


def _current_myt_year() -> int:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=8)).year


class TestProductOnlyDefaultsToCurrentYear:
    def test_product_only_ask_defaults_to_the_current_year(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        year = _current_myt_year()
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(order_status="sales_report"),
            text_body="sales report for SRTWT7445",
            msg_id="ZZT-sales-report-s18-year-default-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert args.get("date_from") == f"{year}-01-01", args
        assert args.get("date_to") == f"{year}-12-31", args

        stored = _session_of(session_factory)
        open_question = stored.get("open_question") or {}
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("date_filter_start") == f"{year}-01-01", (
            "the stored filter set must carry the same default window, so a later "
            f"'1' lists that window only: {filters_out}"
        )
        assert filters_out.get("date_filter_end") == f"{year}-12-31", filters_out

    def test_customer_ask_without_dates_stays_all_dates(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(
                order_status="sales_report",
                entities=[
                    {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim",
            msg_id="ZZT-sales-report-s18-customer-all-dates-1",
            attributes=["sales_orders.sales_report"],
            matches={CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert "date_from" not in args, args
        assert "date_to" not in args, args

    def test_customer_and_product_without_dates_stays_all_dates(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(
                order_status="sales_report",
                entities=[
                    {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                    {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim SRTWT7445",
            msg_id="ZZT-sales-report-s18-both-all-dates-1",
            attributes=["sales_orders.sales_report"],
            matches={
                PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME},
            },
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert "date_from" not in args, args
        assert "date_to" not in args, args

    def test_product_only_all_dates_in_words_is_honoured(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(order_status="sales_report", broaden_axis="date"),
            text_body="sales report for SRTWT7445, all dates",
            msg_id="ZZT-sales-report-s18-all-dates-in-words-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert "date_from" not in args, (
            "broaden_axis == 'date' must turn the current-year default off", args,
        )
        assert "date_to" not in args, args

    def test_product_only_with_its_own_window_is_untouched(self, session_factory, monkeypatch) -> None:
        """Pin, green-by-design: an EXPLICIT window already flows through the
        generic `DATE_PARAMS` mechanism today (`fetch.py`) - unaffected by S18's
        new default, which only ever fires when the turn supplies no dates."""
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(
                order_status="sales_report",
                date_filter_start="2026-06-01", date_filter_end="2026-06-30",
            ),
            text_body="sales report for SRTWT7445 in june 2026",
            msg_id="ZZT-sales-report-s18-own-window-1",
            attributes=["sales_orders.sales_report"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert args.get("date_from") == "2026-06-01", args
        assert args.get("date_to") == "2026-06-30", args


# --------------------------------------------------------------------------- #
# S19 item 3 (captain brief, 19 Sep 2026): the typed-code-wins rule sends the
# raw STEM to `crm_sales_report`, which the ROUTE then prefix-matches - never
# a resolver-picked family member.
# --------------------------------------------------------------------------- #


def _family_resolve_services(*, token: str, matches: list[dict[str, Any]]) -> ResolveGateServices:
    """ONE token resolving to SEVERAL product matches under an OR-mode probe -
    the shape a real prefix/family resolution returns (`_ambiguous_hanlim_
    resolve_services`'s own pattern, ported for a product family instead of
    an ambiguous customer)."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": [token],
            "resolutions": [{"token": token, "matches": matches}],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


class TestTypedStemWinsOverFamilySibling:
    def test_a_stem_that_is_itself_a_product_wins_over_its_sibling(
        self, session_factory, monkeypatch
    ) -> None:
        """The must-have case (S19's own live bug): "Srt5674" is itself a real
        product AND a family stem - the resolver's OR-mode probe also matches
        its sibling SRT5674-N under the same token. The typed-code-wins rule
        (`outstanding_product_code`, AC-1119) must send the RAW TYPED STEM to
        the tool, never the sibling - the ROUTE's own S19 prefix rule is what
        then re-expands it back to both."""
        stem_uuid = "33333333-3333-3333-3333-333333333333"
        sibling_uuid = "44444444-4444-4444-4444-444444444444"
        resolve_services = _family_resolve_services(
            token="SRT5674",
            matches=[
                {"entity_type": "product", "canonical_code": "SRT5674", "uuid": stem_uuid},
                {"entity_type": "product", "canonical_code": "SRT5674-N", "uuid": sibling_uuid},
            ],
        )

        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="sales_report",
                entities=[
                    {
                        "raw": "SRT5674", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="sales report for SRT5674",
            msg_id="ZZT-sales-report-s19-stem-1",
            attributes=["sales_orders.sales_report"],
            resolve_services=resolve_services,
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured, "the report must run"
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert args.get("product_code") == "SRT5674", (
            "typed-code-wins (AC-1119) must send the RAW TYPED STEM, never a "
            f"family sibling the OR-mode probe also matched: {args}"
        )

    def test_a_stem_that_is_not_itself_a_product_now_rosters_instead_of_guessing(
        self, session_factory, monkeypatch
    ) -> None:
        """The edge case the captain's brief originally asked to REPORT rather than
        force: "SRT567" is a prefix of several products but not itself one of them.

        RE-PINNED AGAIN (owner ruling 21 Sep 2026, hand pass 12, S12): the owner
        reversed `order.product`'s own narrowing to `list_all` - a typed family stem
        now answers over EVERY variant it names, the same way `inventory`/
        `purchase_cost` already do, rather than rostering an ambiguity that `list_
        all` no longer treats as one. Re-measured directly (this pass, tester 55):
        the turn runs exactly ONE fetch, `crm_sales_report`, and that call's own
        `product_code` arg is the RAW TYPED STEM ("SRT567") - `outstanding_product_
        code`'s typed-code-wins rule (AC-1119, the SAME rule the sibling test above
        pins for a stem that IS itself a product) fires here too, unconditionally
        on the raw text, not conditioned on whether the stem itself resolves to a
        product. There is no `product_ids` key on this call at all - a captain's
        brief guess ("product_ids = every variant of the stem") did not survive
        measurement; flagged here rather than forced. No roster is left open - the
        report's own normal `sales_report_detail` follow-up offer ("Reply 1 for the
        sales order list") is a different, unrelated pending shape and is not what
        this test refuses."""
        first_uuid = "55555555-5555-5555-5555-555555555555"
        second_uuid = "66666666-6666-6666-6666-666666666666"
        resolve_services = _family_resolve_services(
            token="SRT567",
            matches=[
                {"entity_type": "product", "canonical_code": "SRT5679", "uuid": first_uuid},
                {"entity_type": "product", "canonical_code": "SRT5670", "uuid": second_uuid},
            ],
        )

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="sales_report",
                entities=[
                    {
                        "raw": "SRT567", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="sales report for SRT567",
            msg_id="ZZT-sales-report-s19-stem-2",
            attributes=["sales_orders.sales_report"],
            resolve_services=resolve_services,
            mcp_response=SALES_REPORT_HIT,
        )
        assert result.status == "done", result.error
        assert len(captured) == 1, (
            f"a typed family stem must answer over every variant it names with ONE "
            f"fetch, never a roster (owner ruling 21 Sep 2026, list_all): {captured!r}"
        )
        name, args = captured[0]
        assert name == "crm_sales_report", name
        assert args.get("product_code") == "SRT567", (
            "typed-code-wins (AC-1119) must send the RAW TYPED STEM here too, the "
            f"SAME rule the sibling test above pins for an exact-match stem: {args!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "product_pick", (
            f"no product roster may be left open once the family answers directly - "
            f"a stem that is not itself a product must never guess a single sibling "
            f"NOR still roster: {open_question!r}"
        )


# --------------------------------------------------------------------------- #
# Second defect (captain brief, 19 Sep 2026, live testing after S19): "dealer
# Srt5674-N August total sale quantity" answered the GENERIC "no order ...
# matched" miss with NO TOOL CALL AT ALL, even though August sales existed.
# The parser hinted "Srt5674-N" as a CUSTOMER (the word "dealer" sits in front
# of it); the resolver's fallback then found it as a PRODUCT instead
# (`fallback_to_all_types`); `resolve_gate.py::if3_miss` clause 3 ("the domain
# accepts a customer, the parser named one, and nothing customer-shaped
# survived the gate") declares this a miss regardless of the product that DID
# resolve, so the turn never reaches `run_fetch` at all - the report is never
# even attempted. Fix A: the fix is at that ONE seam - clause 3 must not fire
# for a REPORT ask whose OWN subject rule (at least one of customer/product)
# is already satisfied by the fallback match. Parametrized over
# `order_status="sales_report"` and one legacy outstanding status
# (`so_outstanding`) - both share `resolve_gate.OUTSTANDING_ORDER_STATUS` and
# both share this same gate.
# --------------------------------------------------------------------------- #


def _customer_hinted_product_fallback_resolve_services() -> ResolveGateServices:
    """The measured shape: ONE token ("Srt5674-N") the PARSER hinted "customer"
    resolves to NOTHING of type customer - only a PRODUCT match, the resolver's
    own `fallback_to_all_types` behaviour when the requested type misses."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": ["Srt5674-N"],
            "resolutions": [
                {
                    "token": "Srt5674-N",
                    "matches": [
                        {
                            "entity_type": "product", "canonical_code": "SRT5674-N",
                            "uuid": "77777777-7777-7777-7777-777777777777",
                        },
                    ],
                },
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


class TestReportAskNeverAnsweredByTheDeliveryOrderMissProbe:
    @pytest.mark.parametrize(
        "order_status,attributes,tool",
        [
            ("sales_report", ["sales_orders.sales_report"], "crm_sales_report"),
            ("so_outstanding", ["sales_orders.outstanding"], "crm_outstanding_report"),
        ],
    )
    def test_a_customer_hinted_token_that_resolves_as_a_product_still_runs_the_report(
        self, session_factory, monkeypatch, order_status, attributes, tool
    ) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", domain_hint="order", intent_hint="check_order",
                order_status=order_status, sales_channel="dealer",
                date_filter_start="2026-08-01", date_filter_end="2026-08-31",
                entities=[
                    {
                        "raw": "Srt5674-N", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="dealer Srt5674-N August total sale quantity",
            msg_id=f"ZZT-report-hint-mismatch-{order_status}-1",
            attributes=attributes,
            resolve_services=_customer_hinted_product_fallback_resolve_services(),
            mcp_response=SALES_REPORT_HIT,
        )
        reply = (_result.reply or {}).get("text") or ""
        assert captured, (
            f"the {order_status} report must run off the fallback PRODUCT match, not "
            f"answer a miss with no tool call at all: reply={reply!r}"
        )
        name, args = captured[0]
        assert name == tool, (name, args)
        if tool == "crm_sales_report":
            assert args.get("product_code") == "SRT5674-N", (
                "typed-code-wins must send the raw stem the customer typed, folded "
                f"through the resolved match: {args}"
            )
            assert args.get("channel") == "dealer", args
            assert args.get("date_from") == "2026-08-01", args
            assert args.get("date_to") == "2026-08-31", args
        assert "matched these" not in reply, (
            f"the generic delivery-order miss probe text must not appear: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Finding 3(b) (owner live testing, 19 Sep 2026, PLAN-chatbot-sales-report.md):
# "Srt5674 August total sale quantity" (no "dealer") sometimes came back
# `domain_hint: "master_products"` alongside a correct `order_status:
# "sales_report"` - the parser knew the ask was a report and still named the
# wrong domain, and the sales_report tool-pick override in
# `lanes/business/__init__.py` only fires for `domain == "order"`, so the turn
# fell through to the product-master listing instead of the report.
# `head/output_exchange.py::_post_process` now forces `domain_hint` back to
# "order" whenever `order_status` is a report status (`OUTSTANDING_ORDER_
# STATUS`), read off the parser's own structured field alone. Parametrized
# over the sales report AND one legacy outstanding status, since both share
# that one set and that one correction.
# --------------------------------------------------------------------------- #


class TestReportOrderStatusAlwaysReachesTheReportDespiteAWrongDomainHint:
    @pytest.mark.parametrize(
        "order_status,attributes,tool,mock_hit",
        [
            ("sales_report", ["sales_orders.sales_report"], "crm_sales_report", SALES_REPORT_HIT),
            ("so_outstanding", ["sales_orders.outstanding"], "crm_outstanding_report", REPORT_HIT),
        ],
    )
    def test_a_master_products_domain_hint_still_reaches_the_report(
        self, session_factory, monkeypatch, order_status, attributes, tool, mock_hit
    ) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", domain_hint="master_products",
                intent_hint="check_product", order_status=order_status,
                requested_attributes=["quantity"],
                entities=[
                    {
                        "raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="Srt5674 August total sale quantity",
            msg_id=f"ZZT-report-domain-fix-{order_status}-1",
            attributes=attributes,
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=mock_hit,
        )
        assert captured, (
            f"the {order_status} report must run even though domain_hint was "
            f"master_products, not answer a product-master listing"
        )
        name, _args = captured[0]
        assert name == tool, (order_status, name)
