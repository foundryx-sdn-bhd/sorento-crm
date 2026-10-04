"""Phase 2 RED tests - the sales report's drill-down offer in the lane (lane SALES-REPORT, PR #1401).

`documentation/plans/chatbot/selfref-scope-acceptance-criteria.md` AC-SR-28 (the options are the
`sales_report_detail` open question; a number or a typed label re-runs the report with the
same window, links, location and channel and the matching `group_by`; an out-of-range number
re-asks the same options) and AC-SR-29 (an own-account miss stays a final answer).

Built on the harness `tests/chatbot/test_sales_report_lane.py` uses (`_run_turn`,
`_capturing_mcp`, `_seed_contact`, `_session_of`), imported rather than copied. Every drill is
armed by a REAL first turn (the fake MCP renders the route body through the real presenter,
exactly as production does), never by a hand-written session dict: the thing under test is the
arming itself, which reads the envelope's `options` and not a regex over the reply text.

Written BEFORE the lane reads `envelope["options"]`: today the offer is the one "Reply 1 for the
sales order list." regex (`fetch._outstanding_offer_from_text`) and a pick re-runs with
`detail=so`, so every test below fails on a missing option / `group_by`, not on the harness.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    _qf,
    _run_turn,
    _seed_contact,
    _session_of,
)
from tests.chatbot.test_sales_report_lane import (
    SALES_REPORT_HIT,
    SALES_REPORT_HIT_MULTI,
    SALES_REPORT_MISS,
    SALES_REPORT_REFUSED,
)

ATTRS = ["sales_orders.sales_report"]
CUSTOMER_MATCH = {CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}}

#: The same drilled body the route returns after a By product pick on a one-account scope:
#: two rows printed, so the one remaining option is numbered 3.
DRILLED_HIT: dict[str, Any] = {
    **SALES_REPORT_HIT,
    "group_by": "product",
    "rows": [
        {"rank": 1, "name": "PRD-A101", "qty": 6, "amount": 60.0, "date": None, "customer_name": None},
        {"rank": 2, "name": "PRD-A102", "qty": 4, "amount": 40.0, "date": None, "customer_name": None},
    ],
    "options": [{"key": "delivery_order", "label": "Delivery orders"}],
}


def _seed_warehouses(session_factory) -> None:
    from app.models.inventory import Warehouse

    db = session_factory()
    db.add_all(
        [
            Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW-IB", warehouse_name="BRW IB", is_active=True),
            Warehouse(id=str(uuid.uuid4()), warehouse_code="MWH-IB", warehouse_name="MWH IB", is_active=True),
        ]
    )
    db.commit()


def _ask(
    session_factory, monkeypatch, *, hit: Any = SALES_REPORT_HIT, msg_id: str, rich: bool = False,
    expect_armed: bool = True,
):
    """Turn 1: a real sales report ask. `rich` carries a window, a location and a channel so a
    later pick has something to keep. `expect_armed` (default) asserts the envelope's options
    became the open question, so a pick test that fails fails HERE, on the arming, and not three
    steps later on a tool the lane ran because nothing was open."""
    entities = [
        {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
    ]
    overrides: dict[str, Any] = {}
    if rich:
        _seed_warehouses(session_factory)
        entities.append(
            {"raw": "IB", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True}
        )
        overrides = {
            "date_filter_start": "2026-07-01",
            "date_filter_end": "2026-09-30",
            "sales_channel": "project",
        }
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="sales_report", entities=entities, **overrides),
        text_body="my sales this quarter",
        msg_id=msg_id,
        attributes=ATTRS,
        matches=CUSTOMER_MATCH,
        mcp_response=hit,
    )
    assert captured and captured[0][0] == "crm_sales_report", ("setup: the ask must run the report", captured)
    if expect_armed:
        open_question = _open_question(session_factory)
        assert open_question.get("kind") == "sales_report_detail" and any(
            (o.get("payload") or {}).get("value") in ("product", "customer", "delivery_order")
            for o in open_question.get("options") or []
        ), ("setup: the envelope's options must arm the drill offer", open_question)
    return result, captured[0][1]


def _pick(session_factory, monkeypatch, *, position: int, msg_id: str, hit: dict[str, Any] = SALES_REPORT_HIT):
    return _run_turn(
        session_factory, monkeypatch,
        qf=_parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[position],
        ),
        text_body=str(position),
        msg_id=msg_id,
        attributes=ATTRS,
        mcp_response=hit,
    )


def _typed(session_factory, monkeypatch, *, word: str, msg_id: str, hit: dict[str, Any] = SALES_REPORT_HIT):
    """A label typed back at the open question: the parser emits it as an ENTITY (the label-match
    arm of `decide.picked_positions`), with no position."""
    return _run_turn(
        session_factory, monkeypatch,
        qf=_parser_output(
            message_type="casual", intent_hint=None, domain_hint=None,
            entities=[
                {"raw": word, "hint": "order", "canonical_code": None, "current_message": True, "confident": True},
            ],
            reference_positions=[], entity_op="reuse",
        ),
        text_body=word,
        msg_id=msg_id,
        attributes=ATTRS,
        mcp_response=hit,
    )


def _open_question(session_factory) -> dict[str, Any]:
    return _session_of(session_factory).get("open_question") or {}


def _option_position(option: dict[str, Any]) -> Any:
    return option.get("position", option.get("idx"))


# --------------------------------------------------------------------------- #
# AC-SR-28 - the options are the open question
# --------------------------------------------------------------------------- #


class TestTheOffersAreTheEnvelopesOptions:
    def test_a_hit_arms_the_envelopes_options_in_order(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-arm-1", expect_armed=False)
        open_question = _open_question(session_factory)
        assert open_question.get("kind") == "sales_report_detail", open_question
        options = open_question.get("options") or []
        assert [(_option_position(o), o.get("label"), (o.get("payload") or {}).get("value")) for o in options] == [
            (1, "By product", "product"),
            (2, "Delivery orders", "delivery_order"),
        ], options

    def test_several_accounts_add_by_customer(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, hit=SALES_REPORT_HIT_MULTI, msg_id="ZZT-drill-arm-multi-1",
             expect_armed=False)
        options = _open_question(session_factory).get("options") or []
        assert [(o.get("payload") or {}).get("value") for o in options] == [
            "customer", "product", "delivery_order",
        ], options

    def test_a_drills_options_keep_counting_from_the_rows_printed(self, session_factory, monkeypatch) -> None:
        """Two rows printed, so the one offer left is numbered 3: the number the customer sees is
        the number the engine resolves."""
        _ask(session_factory, monkeypatch, hit=DRILLED_HIT, msg_id="ZZT-drill-arm-continued-1", expect_armed=False)
        options = _open_question(session_factory).get("options") or []
        assert [(_option_position(o), (o.get("payload") or {}).get("value")) for o in options] == [
            (3, "delivery_order"),
        ], options

    def test_the_offer_comes_from_options_not_from_a_regex_over_the_text(self, session_factory, monkeypatch) -> None:
        """An envelope with `options` and prose that has no offer sentence at all arms them; one
        whose prose still carries the retired sentence but no `options` arms nothing."""
        with_options = json.dumps(
            {
                "result_type": "sales_report",
                "response": "Total: Qty 4, RM 40.00",
                "has_result": True,
                "options": [
                    {"idx": 1, "label": "By product", "value": "product", "aliases": ["by product"]},
                ],
            }
        )
        _ask(session_factory, monkeypatch, hit=with_options, msg_id="ZZT-drill-no-regex-1", expect_armed=False)
        options = _open_question(session_factory).get("options") or []
        assert [(o.get("payload") or {}).get("value") for o in options] == ["product"], options

    def test_the_retired_sentence_without_options_arms_nothing(self, session_factory, monkeypatch) -> None:
        legacy = json.dumps(
            {
                "result_type": "sales_report",
                "response": "Total: Qty 4, RM 40.00\n\nReply 1 for the sales order list.",
                "has_result": True,
                "options": [],
            }
        )
        _ask(session_factory, monkeypatch, hit=legacy, msg_id="ZZT-drill-old-sentence-1", expect_armed=False)
        assert _open_question(session_factory).get("kind") != "sales_report_detail", _open_question(session_factory)

    def test_a_refused_body_arms_nothing_and_answers_with_the_refusal_line(self, session_factory, monkeypatch) -> None:
        result, _args = _ask(session_factory, monkeypatch, hit=SALES_REPORT_REFUSED, msg_id="ZZT-drill-refused-1",
                          expect_armed=False)
        reply = (result.reply or {}).get("text") or ""
        assert "Sorry, REPAIR isn't one of the locations you can check." in reply, reply
        assert "escalate" not in reply.lower(), reply
        assert _open_question(session_factory).get("kind") != "sales_report_detail", _open_question(session_factory)

    def test_a_miss_arms_nothing(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, hit=SALES_REPORT_MISS, msg_id="ZZT-drill-miss-1", expect_armed=False)
        assert _open_question(session_factory).get("kind") != "sales_report_detail", _open_question(session_factory)


# --------------------------------------------------------------------------- #
# AC-SR-28 - a pick re-runs the report with group_by
# --------------------------------------------------------------------------- #


class TestAPickReRunsTheReport:
    @pytest.mark.parametrize("position,group_by", [(1, "product"), (2, "delivery_order")])
    def test_a_number_picks_its_option(self, session_factory, monkeypatch, position, group_by) -> None:
        _ask(session_factory, monkeypatch, msg_id=f"ZZT-drill-num-arm-{position}")
        _result, captured = _pick(session_factory, monkeypatch, position=position, msg_id=f"ZZT-drill-num-{position}")
        assert captured, "the pick must re-run the report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("group_by") == group_by, args
        assert "detail" not in args, ("`detail=so` is retired", args)

    @pytest.mark.parametrize(
        "position,group_by", [(1, "customer"), (2, "product"), (3, "delivery_order")]
    )
    def test_by_customer_is_pickable_when_it_was_offered(self, session_factory, monkeypatch, position, group_by) -> None:
        _ask(session_factory, monkeypatch, hit=SALES_REPORT_HIT_MULTI, msg_id=f"ZZT-drill-multi-arm-{position}")
        _result, captured = _pick(
            session_factory, monkeypatch, position=position, msg_id=f"ZZT-drill-multi-{position}",
            hit=SALES_REPORT_HIT_MULTI,
        )
        assert captured and captured[0][1].get("group_by") == group_by, captured

    def test_a_continued_number_resolves_to_the_option_it_labels(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, hit=DRILLED_HIT, msg_id="ZZT-drill-cont-arm-1")
        _result, captured = _pick(
            session_factory, monkeypatch, position=3, msg_id="ZZT-drill-cont-1", hit=DRILLED_HIT,
        )
        assert captured and captured[0][1].get("group_by") == "delivery_order", captured

    def test_the_rerun_keeps_the_window_the_accounts_the_location_and_the_channel(
        self, session_factory, monkeypatch
    ) -> None:
        _result, first = _ask(session_factory, monkeypatch, msg_id="ZZT-drill-keep-arm-1", rich=True)
        for key in ("date_from", "date_to", "customer_ids", "warehouse_codes", "channel"):
            assert first.get(key), (f"setup: the ask itself must carry {key}", first)
        _result, captured = _pick(session_factory, monkeypatch, position=1, msg_id="ZZT-drill-keep-1")
        assert captured, "the pick must re-run the report"
        _name, args = captured[0]
        assert args.get("group_by") == "product", args
        for key in ("date_from", "date_to", "customer_ids", "warehouse_codes", "location_token", "channel"):
            assert args.get(key) == first.get(key), (key, args, first)

    @pytest.mark.parametrize(
        "word,group_by",
        [
            ("by product", "product"),
            ("By product", "product"),
            ("Delivery orders", "delivery_order"),
            ("delivery orders", "delivery_order"),
            ("delivery order", "delivery_order"),
            ("DO", "delivery_order"),
        ],
    )
    def test_a_typed_label_or_alias_picks_its_option(self, session_factory, monkeypatch, word, group_by) -> None:
        _ask(session_factory, monkeypatch, msg_id=f"ZZT-drill-typed-arm-{word.replace(' ', '-')}")
        _result, captured = _typed(
            session_factory, monkeypatch, word=word, msg_id=f"ZZT-drill-typed-{word.replace(' ', '-')}",
        )
        assert captured, (word, "a typed label must re-run the report")
        name, args = captured[0]
        assert name == "crm_sales_report", (word, name, args)
        assert args.get("group_by") == group_by, (word, args)

    def test_by_customer_typed_picks_it_when_offered(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, hit=SALES_REPORT_HIT_MULTI, msg_id="ZZT-drill-typed-cust-arm-1")
        _result, captured = _typed(
            session_factory, monkeypatch, word="by customer", msg_id="ZZT-drill-typed-cust-1",
            hit=SALES_REPORT_HIT_MULTI,
        )
        assert captured and captured[0][1].get("group_by") == "customer", captured

    def test_the_offer_survives_its_own_pick_and_follows_the_drill(self, session_factory, monkeypatch) -> None:
        """After By product the open options are the NEXT envelope's (Delivery orders, numbered
        after the rows printed): "DO" then works without asking again."""
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-follow-arm-1")
        _pick(session_factory, monkeypatch, position=1, msg_id="ZZT-drill-follow-1", hit=DRILLED_HIT)
        open_question = _open_question(session_factory)
        assert open_question.get("kind") == "sales_report_detail", open_question
        assert [(o.get("payload") or {}).get("value") for o in open_question.get("options") or []] == [
            "delivery_order",
        ], open_question

    def test_an_out_of_range_number_reasks_the_same_options_and_runs_nothing(
        self, session_factory, monkeypatch
    ) -> None:
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-oor-arm-1")
        before = _open_question(session_factory).get("options")
        result, captured = _pick(session_factory, monkeypatch, position=9, msg_id="ZZT-drill-oor-1")
        assert captured == [], f"an out-of-range pick must not run any report: {captured}"
        reply = (result.reply or {}).get("text") or ""
        assert "1. By product" in reply and "2. Delivery orders" in reply, (
            f"an out-of-range pick must re-ask the same options: {reply!r}"
        )
        open_question = _open_question(session_factory)
        assert open_question.get("kind") == "sales_report_detail", open_question
        assert open_question.get("options") == before, (before, open_question.get("options"))


def _named_document(
    session_factory, monkeypatch, *, document: list[str], msg_id: str, with_entity: bool,
    hit: dict[str, Any] = SALES_REPORT_HIT,
):
    """"DO" / "delivery orders" typed at the open offer: the parser emits `document`, with or
    without the typed word as an entity too (fix round 1, B1)."""
    entities = (
        [{"raw": document[0], "hint": "order", "canonical_code": None, "current_message": True, "confident": True}]
        if with_entity
        else []
    )
    return _run_turn(
        session_factory, monkeypatch,
        qf=_parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=entities,
            reference_positions=[], entity_op="reuse" if with_entity else "replace_combine",
            document=document,
        ),
        text_body=document[0],
        msg_id=msg_id,
        attributes=ATTRS,
        mcp_response=hit,
    )


class TestANamedDocumentPicksTheDeliveryOrders:
    """Fix round 1, B1 (AC-SR-28): the parser reads "DO" as `document: ["DO"]` (its
    document enum), which the outstanding report's named-document arm used to answer as a
    new ask with no `group_by`. On the sales report's drill offer it picks Delivery orders."""

    @pytest.mark.parametrize("with_entity", [False, True])
    def test_document_do_reruns_with_group_by_delivery_order(
        self, session_factory, monkeypatch, with_entity
    ) -> None:
        _r, first = _ask(session_factory, monkeypatch, msg_id=f"ZZT-drill-doc-arm-{with_entity}", rich=True)
        _result, captured = _named_document(
            session_factory, monkeypatch, document=["DO"], msg_id=f"ZZT-drill-doc-{with_entity}",
            with_entity=with_entity,
        )
        assert captured, "a named DO must re-run the report"
        name, args = captured[0]
        assert name == "crm_sales_report", (name, args)
        assert args.get("group_by") == "delivery_order", args
        for key in ("date_from", "date_to", "customer_ids", "warehouse_codes", "channel"):
            assert args.get(key) == first.get(key), (key, args, first)

    def test_document_so_is_not_an_option_and_reasks(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-doc-so-arm-1")
        before = _open_question(session_factory).get("options")
        result, captured = _named_document(
            session_factory, monkeypatch, document=["SO"], msg_id="ZZT-drill-doc-so-1", with_entity=False,
        )
        assert captured == [], f"SO is not an option of this offer: {captured}"
        reply = (result.reply or {}).get("text") or ""
        assert "1. By product" in reply and "2. Delivery orders" in reply, reply
        assert _open_question(session_factory).get("options") == before

    def test_document_do_when_not_offered_reasks(self, session_factory, monkeypatch) -> None:
        """After the Delivery orders drill the offer no longer holds that option."""
        no_do = {**SALES_REPORT_HIT, "options": [{"key": "product", "label": "By product"}]}
        _ask(session_factory, monkeypatch, hit=no_do, msg_id="ZZT-drill-doc-gone-arm-1")
        result, captured = _named_document(
            session_factory, monkeypatch, document=["DO"], msg_id="ZZT-drill-doc-gone-1", with_entity=False,
            hit=no_do,
        )
        assert captured == [], f"Delivery orders was not offered: {captured}"
        reply = (result.reply or {}).get("text") or ""
        assert "1. By product" in reply, reply
        assert _open_question(session_factory).get("kind") == "sales_report_detail"


class TestFixRound2:
    def test_a_named_document_with_its_own_status_is_a_new_ask_and_closes_the_offer(
        self, session_factory, monkeypatch
    ) -> None:
        """R1: `document: ["DO"]` WITH `status: "outstanding"` is the outstanding report asked
        for in words, not a pick of the sales drill: the outstanding report runs and the sales
        offer closes, so a following "1" never lands on the sales drill."""
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-r1-arm-1")
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], document=["DO"], status="outstanding",
            ),
            text_body="DO outstanding",
            msg_id="ZZT-drill-r1-1",
            attributes=ATTRS + ["sales_orders.outstanding"],
            mcp_response=SALES_REPORT_HIT,
        )
        assert not any(name == "crm_sales_report" for name, _a in captured), captured
        # PROMPT-DYNAMIC reviewer pass 3, S3: it IS the outstanding report that runs, in the
        # order domain, not merely "not the sales report".
        assert any(name == "crm_outstanding_report" for name, _a in captured), captured
        assert _open_question(session_factory).get("kind") != "sales_report_detail", _open_question(session_factory)
        _result, after = _pick(session_factory, monkeypatch, position=1, msg_id="ZZT-drill-r1-2")
        assert not any(name == "crm_sales_report" for name, _a in after), after

    @pytest.mark.parametrize(
        "word,document",
        [("DO", None), ("DO", ["DO"]), ("by product", None), ("delivery orders", None)],
    )
    def test_a_typed_pick_never_prints_a_could_not_find_line(
        self, session_factory, monkeypatch, word, document
    ) -> None:
        """R2: the typed label that settled the pick is not also a subject to resolve, so the
        reply carries no "I could not find DO." miss line."""
        slug = f"{word.replace(' ', '-')}-{bool(document)}"
        _ask(session_factory, monkeypatch, msg_id=f"ZZT-drill-r2-arm-{slug}")
        result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None,
                entities=[
                    {"raw": word, "hint": "order", "canonical_code": None, "current_message": True, "confident": True},
                ],
                reference_positions=[], entity_op="reuse", document=document,
            ),
            text_body=word,
            msg_id=f"ZZT-drill-r2-{slug}",
            attributes=ATTRS,
            mcp_response=SALES_REPORT_HIT,
        )
        assert captured and captured[0][0] == "crm_sales_report", captured
        reply = (result.reply or {}).get("text") or ""
        assert "could not find" not in reply.lower(), reply


# --------------------------------------------------------------------------- #
# AC-SR-28 - unchanged rules around the offer (AC-SR-29, the own-account miss staying a final
# answer, is pinned in `test_customer_scope_lane.py` through the new MISS body)
# --------------------------------------------------------------------------- #


class TestExistingRulesStillHold:
    def test_a_new_ask_drops_the_offer(self, session_factory, monkeypatch) -> None:
        _ask(session_factory, monkeypatch, msg_id="ZZT-drill-new-ask-arm-1")
        other_uuid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        _run_turn(
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
            msg_id="ZZT-drill-new-ask-1",
            attributes=ATTRS,
            matches={"hanlim": {"uuid": other_uuid, "entity_type": "customer", "canonical_code": "HANLIM TRADING SDN BHD"}},
        )
        assert _open_question(session_factory).get("kind") != "sales_report_detail", _open_question(session_factory)
