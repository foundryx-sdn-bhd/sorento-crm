"""R13 hotfix RED tests: a stock ask with NO placed subject must never call
`crm_inventory_stock_balance_list` unfiltered.

Plan: `documentation/plans/chatbot/PLAN-chatbot-stock-no-subject-hotfix-22sep.md`.
UAC: `chatbot-stock-no-subject-hotfix-22sep-acceptance-criteria.md` (AC-1790..AC-1796).

The live chain (prod, 22 Sep 2026 12:52, contact 900000081, turns 59-60):

* turn 59 "Srtwc8608-p-rl" missed and raised a did-you-mean roster. The token
  resolved ONLY as a `product_set` - a kind `gate.ALLOWED["inventory"]` does not
  take - so `turn/reconcile.py` rewrote its hint and `turn/apply.py::_focus_rules`
  parked it on `focus.extra["product_set"]` with no uuid.
* turn 60 "Stock" named no entity of its own (`entity_op: reuse`). The carry was
  handed to the resolver, which PLACED it (as a `product_set`), so `unplaced` came
  back EMPTY and `turn_runtime.make_tool_runner.runner`'s `would_be_unfiltered`
  guard - which needs `bool(unplaced)` - never fired. `compatible_entities` was
  empty (product_set is incompatible with inventory), so the stock tool ran with no
  filter at all and the customer got 50 rows of the whole book.

Pre-rearch behaviour is still the contract: `gate.ALLOWS_EMPTY["inventory"] is
False` ("a bare 'stock?' must ask which product"), answered with
`answer.not_found_error_message`'s own `needs_scope` sentence.

MEASURED while writing this file, and the reason T1's own carried subject never
reaches `unplaced` either: on a message whose every word is a predicate word
("Stock"), `api/v1/system/references.py`'s shape-B arm strips the query down to ""
and `derive_search_inputs` answers `understanding=None`, so its line ~2715 raises
`AttributeError: 'NoneType' object has no attribute 'bound_phrases'`. That call sits
inside `turn_runtime.resolve_kinds`'s own broad `except`, which degrades to "the
resolver did not answer" - `unplaced` empty, `compatible_entities` empty, the
identical inputs the guard hole needs, and the identical whole-book reply the live
turn produced. Pre-existing on `origin/main`, untouched by this hotfix.

Harness, all copied rather than invented - the SAME convention every hand pass 12 /
r12 file uses (`test_rearch_r12_phase3_fixes.py`'s own docstring): the REAL
resolver / gate / narrower over seeded Postgres rows, only `FetchServices.mcp_call`
doubled. Postgres only (`session_factory`), every row seeded fresh per test.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.models.product_set import ProductSet, ProductSetMember
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _run_turn_engine
from tests.chatbot.test_rearch_r12_handpass12 import (
    STOCK_TOOL,
    _focus,
    _said,
    _seed_state,
    _unknown_envelope,
)

LOW_STOCK_TOOL = "crm_low_stock_report"

#: The sentence the pre-rearch lane answered a scope-less stock ask with -
#: `lanes/business/answer.py::not_found_error_message`'s own `needs_scope` branch.
#: Asserted as a PREFIX (the filter list that follows is built from
#: `gate.ALLOWED["inventory"]` and is not what this hotfix pins).
NEEDS_SCOPE_OPENING = "That would search every stock we have"

#: What the whole-book answer looked like on the live turn - the reply the guard has
#: to stop producing.
LISTING_MARKER = "Stock details found"


def _seed_product_set(session_factory, *, set_code: str, member_code: str) -> None:
    """A flyer/set code that resolves as `product_set` and NOTHING else - the exact
    shape `entity_resolver._probe_product_set` answers (a `ProductSet` row whose
    `set_code` matches the token, carrying one real member). This is what makes the
    carry PLACE on turn 60, which is why `unplaced` was empty and the existing
    `would_be_unfiltered` guard could not fire."""
    member_id = _seed_product(
        session_factory, company_id=DEFAULT_COMPANY_ID, code=member_code
    )
    db = session_factory()
    try:
        product_set = ProductSet(
            set_code=set_code,
            name=f"ZZT R13 set {set_code}",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(product_set)
        db.flush()
        db.add(
            ProductSetMember(
                product_set_id=product_set.id,
                product_id=member_id,
                quantity=1,
                sort_order=0,
            )
        )
        db.commit()
    finally:
        db.close()


def _whole_book_stock_envelope(rows: int = 50) -> dict[str, Any]:
    """The unfiltered answer, in the live turn's own shape: the MCP presenter's
    `Stock details found for the requested products.` intro over a page of the whole
    catalogue. Returned ONLY if the tool is actually called, so a guarded turn can
    never accidentally read like a hit."""
    return {
        "intro": "Stock details found for the requested products.",
        "items": [
            {
                "flags": {},
                "title": f"ZZTBOOK{i:04d}",
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": f"ZZTBOOK{i:04d}"},
                    {"key": "total_on_hand", "label": "Total", "value": 7},
                ],
            }
            for i in range(rows)
        ],
        "has_result": True,
        "attachments": [],
        "result_type": "stock_compact",
        "action_links": [],
    }


def _stock_ask_verdict(**overrides: Any) -> dict[str, Any]:
    """Turn 60's own verdict: `business_query` / `check_stock` / `inventory`, the
    domain word IS in the message, and NO entity of its own (`entity_op: reuse`)."""
    base = dict(
        message_type="business_query",
        intent_hint="check_stock",
        domain_hint="inventory",
        domain_in_message=True,
        continuation=False,
        entities=[],
        entity_op="reuse",
        document=[],
        status=None,
        order_status=None,
        routing={
            "suggested_team": "warehouse",
            "suggested_agent": "general_enquiries",
            "team_source": None,
        },
    )
    base.update(overrides)
    return _parser_output(**base)


# --------------------------------------------------------------------------- #
# T1 (AC-1790, AC-1791, AC-1792) - the live chain.
# --------------------------------------------------------------------------- #


class TestT1CarriedIncompatibleSubjectNeverFetchesTheWholeBook:
    """Turn 60 of the live chain, reproduced end to end: a `product_set` carry that
    PLACES (so `unplaced` is empty) but is incompatible with inventory (so
    `compatible_entities` is empty) must not reach the stock tool at all, and must
    be answered with production's own scope-needed sentence."""

    OPTION_CODES = ("SRTWC8601-P-RL", "SRTWC8601-RL", "SRTWC8602-RL")

    def _roster_options(self) -> list[dict[str, Any]]:
        """The turn 59 did-you-mean roster, still open when "Stock" arrives."""
        return [
            {
                "code": code,
                "uuid": str(uuid.uuid4()),
                "label": code,
                "uuids": [str(uuid.uuid4())],
                "payload": {"value": code},
                "position": position,
                "entity_type": "product",
            }
            for position, code in enumerate(self.OPTION_CODES, start=1)
        ]

    def test_the_stock_tool_is_never_called_for_a_carry_that_placed_as_a_set(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        set_code = unique_code("ZZTR13SET")[:40]
        _seed_product_set(
            session_factory, set_code=set_code, member_code=unique_code("R13MEM")
        )

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["inventory"],
                products=[],
                extra={
                    "order": [],
                    "category": [],
                    "customer_order": [],
                    "attachment_type": [],
                    "inbound_shipment": [],
                    # Turn 59's own leftover: reconciled to `product_set`, no uuid.
                    "product_set": [
                        {"raw": set_code, "hint": "product_set", "canonical_code": None}
                    ],
                },
            ),
            open_question={
                "kind": "product_pick",
                "team": "warehouse",
                "expects": None,
                "options": self._roster_options(),
                "payload": {"domain": "inventory"},
                "asked_at_turn": 1,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_whole_book_stock_envelope())
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=_stock_ask_verdict(),
            text_body="Stock",
            msg_id="zzt-r13-t1-carry",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert not stock_calls, (
            f"AC-1790: an inventory fetch whose entities carry NO uuid at all (the "
            f"carry placed only as a product_set, a kind inventory does not take) "
            f"must never call {STOCK_TOOL} - it searches the whole book: "
            f"{stock_calls!r}"
        )

    def test_the_reply_is_productions_own_scope_needed_sentence(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        set_code = unique_code("ZZTR13SET")[:40]
        _seed_product_set(
            session_factory, set_code=set_code, member_code=unique_code("R13MEM")
        )

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["inventory"],
                products=[],
                extra={
                    "order": [],
                    "category": [],
                    "customer_order": [],
                    "attachment_type": [],
                    "inbound_shipment": [],
                    "product_set": [
                        {"raw": set_code, "hint": "product_set", "canonical_code": None}
                    ],
                },
            ),
            open_question={
                "kind": "product_pick",
                "team": "warehouse",
                "expects": None,
                "options": self._roster_options(),
                "payload": {"domain": "inventory"},
                "asked_at_turn": 1,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_whole_book_stock_envelope())
            return _unknown_envelope()

        mcp_call, _calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=_stock_ask_verdict(),
            text_body="Stock",
            msg_id="zzt-r13-t1-reply",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        said = _said(result)
        assert said.startswith(NEEDS_SCOPE_OPENING), (
            f"AC-1791/AC-1792: the refused turn must open with production's own "
            f"scope-needed wording ({NEEDS_SCOPE_OPENING!r}), never a listing: "
            f"{said!r}"
        )
        assert LISTING_MARKER not in said, (
            f"AC-1791: the whole-book listing intro must never reach the customer: "
            f"{said!r}"
        )


# --------------------------------------------------------------------------- #
# T2 (AC-1790, AC-1791, AC-1793) - a bare "stock?" on a fresh contact.
# --------------------------------------------------------------------------- #


class TestT2BareStockAskOnAFreshContact:
    """The same hole from the other side: turn 1, no focus, no pending, no entity.
    The resolver never runs at all (`turn_runtime.resolve_kinds` returns early for
    an entity-less turn outside `access_check`), so `unplaced` is empty for that
    reason instead - and `gate.ALLOWS_EMPTY["inventory"] is False` is exactly the
    rule that says this ask needs a filter."""

    def test_a_bare_stock_ask_never_calls_the_stock_tool(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_whole_book_stock_envelope())
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=_stock_ask_verdict(),
            text_body="stock?",
            msg_id="zzt-r13-t2-bare",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert not stock_calls, (
            f"AC-1790/AC-1793: a bare 'stock?' names no subject at all - "
            f"{STOCK_TOOL} must not be called: {stock_calls!r}"
        )

    def test_a_bare_stock_ask_replies_with_the_scope_needed_sentence(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_whole_book_stock_envelope())
            return _unknown_envelope()

        mcp_call, _calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=_stock_ask_verdict(),
            text_body="stock?",
            msg_id="zzt-r13-t2-reply",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        said = _said(result)
        assert said.startswith(NEEDS_SCOPE_OPENING), (
            f"AC-1791/AC-1793: a bare stock ask must be asked which product, in "
            f"production's own words ({NEEDS_SCOPE_OPENING!r}): {said!r}"
        )
        assert LISTING_MARKER not in said, (
            f"AC-1791: the whole-book listing intro must never reach the customer: "
            f"{said!r}"
        )


# --------------------------------------------------------------------------- #
# T3 (AC-1794) - GUARD: `low_stock_report` is a whole-book question by definition.
# --------------------------------------------------------------------------- #


class TestT3LowStockReportStillRunsWithNoEntities:
    """GUARD, not a target. `gate.INTENTS_ALLOWING_EMPTY` carries
    `low_stock_report` on purpose (PLAN-low-stock-report S6, owner ruling 14 Sep
    2026): a bare "low stock report" runs every site-pool warehouse and every
    admitted product. The new refusal must be keyed on the SAME intent set, so this
    ask still reaches its tool with no entities at all.

    LOWSTOCK-FILTER-ASK (owner ruling, 2 Oct 2026): a low stock ask now settles its
    product category before it runs, so the whole-book ask is "... all categories"; a
    bare "low stock report" asks the category (`test_low_stock_filter_ask.py`)."""

    def test_a_scopeless_low_stock_report_still_calls_its_tool(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == LOW_STOCK_TOOL:
                return json.dumps(
                    {
                        "result_type": "low_stock_report",
                        "response": "Low stock report - as of 22/09/2026",
                        "has_result": True,
                        "attachments": [],
                    }
                )
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=_stock_ask_verdict(intent_hint="low_stock_report"),
            text_body="low stock report all categories",
            msg_id="zzt-r13-t3-lowstock",
            mcp_call=mcp_call,
            attributes=["scm.low_stock_report"],
        )
        assert result.status == "done", result.error

        called = [name for name, _args in calls]
        assert LOW_STOCK_TOOL in called, (
            f"AC-1794: `low_stock_report` is in `gate.INTENTS_ALLOWING_EMPTY` - a "
            f"scope-less low stock ask must still reach {LOW_STOCK_TOOL}: "
            f"{calls!r}"
        )


# --------------------------------------------------------------------------- #
# T4 (reviewer S1) - GUARD: the R6/R8 refusal keeps its OWN miss text.
# --------------------------------------------------------------------------- #


class TestT4AnUnresolvedTypedTokenKeepsTheOrdinaryMiss:
    """GUARD, not a target. `would_be_unfiltered` (hand pass 12 R6/R8, turn
    328e8b00 "Srtks8060-BL stock") refuses the SAME tool call, and every entity it
    refuses is unplaced and therefore uuid-less - so the hotfix's own condition
    would fire on that turn too and stamp `scope_gate` on its fragment, re-pointing
    the miss at a gate whose `compatible_entities` is empty and losing the
    per-entity breakdown lines. It is not a scope gap either: the customer DID name
    a subject, it just did not resolve, which is exactly the distinction
    `not_found_error_message` draws with its own `and not has_unresolved`. So this
    turn must still refuse the tool AND still read as the ordinary "could not find
    it" miss, naming the code that was typed - never the scope-needed sentence.

    Fixture shape copied from `test_rearch_r12_handpass12_d.py::
    TestPickPathZeroStockLadderClimbsAfterADidYouMeanPick::test_zero_filter_the_
    miss_turn_never_calls_the_stock_tool_unfiltered`, the R8 turn's own."""

    MISSED_CODE = "Srtks8060-BL"

    def test_the_tool_is_refused_and_the_reply_is_not_the_scope_sentence(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[
                {
                    "raw": self.MISSED_CODE,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "warehouse",
                "suggested_agent": "general_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_whole_book_stock_envelope())
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=f"{self.MISSED_CODE} stock",
            msg_id="zzt-r13-t4-unresolved",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert not stock_calls, (
            f"R6/R8, unchanged: a typed token that resolved to nothing must still "
            f"refuse {STOCK_TOOL}: {stock_calls!r}"
        )

        said = _said(result)
        assert not said.startswith(NEEDS_SCOPE_OPENING), (
            f"reviewer S1: this turn NAMED a subject - it is a lookup miss, not a "
            f"scope gap - so `scope_gate` must not ride its fragment and it must "
            f"keep its own miss wording: {said!r}"
        )
        assert self.MISSED_CODE.lower() in said.lower(), (
            f"reviewer S1: the ordinary miss names the code the customer typed; "
            f"losing it is what stamping `scope_gate` on this refusal would cost: "
            f"{said!r}"
        )
        assert LISTING_MARKER not in said, said

    def test_the_r6_refusal_fragment_never_carries_a_scope_gate(self) -> None:
        """The kill test for reviewer S1, at the seam itself (the same level
        `test_rearch_r12_phase3_fixes.py`'s P4/P5/P6 pin their own findings at): with
        `would_be_unfiltered` already true, the hotfix's condition holds as well -
        every unplaced entity is uuid-less by definition, `_entity_is_unplaced`'s own
        rule - so without the exclusion this refusal leaves with a `scope_gate` on it
        and hands `not_found_error_message` a gate the R6/R8 turn never earned.
        `db=None` is safe here: the refusal returns before anything reads it
        (`fill_customer_names` leaves on an empty list, and no `run_fetch` call is
        made)."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.plan import FetchSpec
        from app.services.chatbot.turn.state import Focus

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[
                {
                    "raw": self.MISSED_CODE,
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
        )
        runner = turn_runtime.make_tool_runner(
            None,
            ctx={"parse": {"output": verdict}, "contact": {"id": "zzt-r13-t4"}},
            verdict=verdict,
            focus=Focus(domains=["inventory"]),
            compatible_entities=[],
            predicate=None,
            unplaced={self.MISSED_CODE.lower().replace("-", ""): self.MISSED_CODE},
            space_id=None,
            dry_run=True,
            turn_trace=None,
        )

        envelope = runner(
            "inventory",
            FetchSpec(domain="inventory", entities=[], filters={}, date_window=None),
        )

        # `runner` answers an ENVELOPE; the fragment itself rides on `raw_fragment`,
        # which is the key `answer_bridge.answer_for` reads, and where `_scope_gate`
        # looks for this stamp.
        fragment = envelope.get("raw_fragment") or {}
        assert fragment.get("outcome") == "not_found", envelope
        assert "scope_gate" not in fragment, (
            f"reviewer S1: `scope_gate` must ride ONLY the new no-subject hole - a "
            f"`would_be_unfiltered` refusal keeps the resolver's own gate, and its "
            f"miss text with it: {fragment!r}"
        )
