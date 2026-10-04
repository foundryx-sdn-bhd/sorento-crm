"""Hand pass 12 RED tests, batch 4 (tester 53) - the parser-hint-mismatch rerun
variant, HIT header ledger names, the pick-path zero-stock ladder, and a unit pin for
coder 44's already-landed MFG6661 fix (M2, `359b5327c`). No implementation exists yet
for the first three; the fourth documents a landed fix that had no pytest artifact.

Harness conventions, all copied rather than invented:

* `TestC1VariantParserHintMismatchResolvedKindWins` and
  `TestPickPathZeroStockLadderClimbsAfterADidYouMeanPick` use `test_rearch_r5_
  production_decides.py::_mcp_double`/`_seed_contact_and_get` + `test_rearch_r6_
  review_round.py::_run_turn_engine`/`_capturing_probe` - the REAL resolver/gate/
  narrower over seeded Postgres rows, `FetchServices.mcp_call` / `AnswerServices.
  mcp_probe` the only doubles, the SAME convention every hand pass 12 file already
  uses. `test_rearch_r12_handpass12.py`'s own `_seed_state`/`_focus`/`_state_of`/
  `_said`/`_unknown_envelope` are imported directly, and `test_rearch_r12_
  handpass12_b.py`'s own `_seed_shipment` is duplicated locally (three lines, not
  worth a cross-file import into a file that does not otherwise depend on `_b.py`).
* `TestM2MFG6661ScopeExactTokenRefusesThePrefixSweep` is a direct unit test of
  `crossdomain_zeroset`, the SAME level `test_crossdomain_ladder.py::
  TestOwner12SepTypedPrefixIsRequested` already pins the sibling "prefix but not
  exact" rule at - no engine, no DB, no resolver; the fix (`_token_requests` gaining
  `all_norm_codes`) lives entirely inside this one pure function.
* `TestGroupFHitHeaderNamesLedgers` reuses `test_rearch_r12_handpass12.py::
  TestGroupFCustomerPickHeaderNamesEveryLedgerNotACode`'s own seeding shape (a
  `customer_pick` pending, ledger rows seeded as real `Customer` rows) but over a
  HIT reply (real order rows returned) rather than that class's own MISS - a
  meaningfully different render path (`lanes/business/__init__.py`'s own scope-block
  builder, ~line 290, read on a HIT).

Postgres only (`session_factory`, blank schema). Every row seeded fresh per test; no
row is borrowed from another test or from any live/clone data.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from app.models.order import Customer
from app.models.procurement import InboundShipment
from app.services.chatbot.lanes.business.fetch import ORDER_TOOLS as ORDER_TOOLS_LOCAL
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _capturing_probe, _run_turn_engine
from tests.chatbot.test_rearch_r12_handpass12 import (
    INCOMING_TOOL,
    STOCK_TOOL,
    _focus,
    _order_envelope,
    _order_row,
    _said,
    _seed_state,
    _state_of,
    _unknown_envelope,
)

PO_TOOL = "crm_procurement_po_placed_list"


def _seed_shipment(session_factory: Any, *, container: str) -> str:
    """`test_rearch_r12_handpass12_b.py::_seed_shipment`, duplicated (three lines) -
    this file does not otherwise import from `_b.py`, and `_b.py` itself imports
    FROM `test_rearch_r12_handpass12.py`, so importing `_b.py` here risks a needless
    coupling for one helper."""
    db = session_factory()
    row = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=unique_code("SHIP")[:50],
        shipping_container_number=container,
        shipment_date=date(2026, 9, 1),
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.commit()
    return row.id


def _no_dash_code(prefix: str) -> str:
    """A token with NO separators, matching turn 50082c60's own recorded raw text
    ("TCNU3167091" - four letters, seven digits, no dash, no space) - MEASURED: `app/
    services/chatbot/turn/reconcile.py::apply_reconciliation` looks up `resolved.
    get(e.get("raw"))`, but `resolved` (`turn_runtime.resolve_kinds`'s own `by_token`)
    is keyed by the FOLDED token (`fold_token`, dashes and whitespace stripped) - a
    raw carrying a dash (`unique_code`'s own "ZZT-CONT-xxxx" shape) never finds its
    own resolver hit under that key, so the hint is never rewritten regardless of what
    the resolver actually matched. `unique_code(...)` would silently exercise THAT
    pre-existing key-fold gap rather than these tests' own target (the rerun gate
    reading the parser's hint instead of the resolved kind), so every typed entity
    token in this file is built dash-free instead, the same shape the recorded turn's
    own container number already has."""
    return f"ZZT{prefix}{uuid.uuid4().hex[:10].upper()}"


# --------------------------------------------------------------------------- #
# RERUN VARIANT - the record-key rerun reads the RESOLVED kind, not the parser's
# own (possibly wrong) hint.
# --------------------------------------------------------------------------- #


class TestC1VariantParserHintMismatchResolvedKindWins:
    def test_parser_hints_product_resolver_places_shipment_the_dropped_product_still_names_itself(
        self, session_factory, monkeypatch
    ) -> None:
        """Replays turn 50082c60's own shape, MEASURED off `.claude/handpass/
        hp12b-turns-21sep.json` directly (both this turn and the one before it,
        9904f2f9, which left the carried focus): focus carries product SRTWB1421 in
        domain incoming (`document: ["PO"]`, no open question needed - `decide.py::
        _subject_reading`'s REFINE row fires on `domain_in_message: false` plus
        entities alone, the SAME row `test_rearch_r12_handpass12_b.py`'s own C1
        reaches with no pending seeded either); the customer types "TCNU3167091"
        and the PARSER hints it `"product"` (turn 50082c60's own recorded verdict,
        byte for byte: `domain_in_message: false`, one entity, `hint: "product"`,
        `canonical_code: null`, `current_message: true`) even though it is a
        container number - the REAL resolver places it as an `inbound_shipment`
        regardless of the hint (`entity_resolver.py`, exact case/whitespace-
        insensitive match on `shipping_container_number`, the SAME real-resolver
        seeding C1 uses).

        CORRECTED (this file's own previous version of this test asserted the
        wrong shape - flagged, not carried forward): the live trace's own idx 10
        tool call already carries BOTH `product_ids` and `shipment_ids` on the
        FIRST try (`entities_in: 2`) - the resolver's real kind, not the parser's
        mishint, decides whether the typed entity COMBINES with or REPLACES the
        carried one, and a resolved `inbound_shipment` combines against a carried
        `product` (a different kind) exactly as C1's own correctly-hinted turn
        does. The combined call misses live (`envelope.has_result: false`) and the
        live bot never reruns - it answers "Here's what you want: ... But no
        incoming matched these. Would you like me to escalate?" - because `app/
        services/chatbot/turn_runtime.py::_record_key_rerun_split`'s own
        `current_kind_entities` gate reads the PARSER's hint
        (`e.get("hint") == record_kind`) to decide whether this turn even
        qualifies as record-key-typed, and the parser hinted "product", not
        "inbound_shipment" - so the gate never fires and the miss stands, no
        rerun at all, even though the SAME function's `keep`/`drop` split two
        lines down already reads the RESOLVED `entity_type` correctly. Owner
        ruling 5 (hand pass 12): rerun once on a REFINE miss when the entity typed
        THIS turn is the domain's record key by its RESOLVED kind, whatever the
        parser hinted - so this turn must still get the shipment-only rerun C1
        gets, dropping the carried product and naming it in the reply the same
        way."""
        _seed_contact_and_get(session_factory)
        code = unique_code("D1PROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        container = _no_dash_code("CONT")
        _seed_shipment(session_factory, container=container)
        other_code = unique_code("D1OTHER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=other_code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                products=[
                    {
                        "raw": code,
                        "hint": "product",
                        "uuid": product_id,
                        "company_name": "Sorento",
                        "canonical_code": code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            # Turn 50082c60's OWN recorded `continuation` is `False` (not the
            # prior version's `True`) - `_is_continuation` (`turn/apply.py`)
            # requires `continuation is True` AND no current-message entity, so
            # with a current-message entity present here either value reaches the
            # exact same REFINE decision; pinned to the recorded value rather than
            # the prior guess.
            continuation=False,
            entity_op="replace_combine",
            entities=[
                {
                    # THE MISHINT - turn 50082c60's own recorded verdict says
                    # "product", not "inbound_shipment", for a real container token.
                    "raw": container,
                    "hint": "product",
                    "canonical_code": None,
                    "hint_confident": True,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                if args.get("product_ids") and args.get("shipment_ids"):
                    return json.dumps(_unknown_envelope_with_rows([]))
                if args.get("shipment_ids") and not args.get("product_ids"):
                    return json.dumps(_unknown_envelope_with_rows([(other_code, container)]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=container,
            msg_id="zzt-d1-rerun-mishint",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        # (a) MEASURED off the live trace's own idx 10: the FIRST call already
        # combines both kinds - the resolver's real kind wins over the parser's
        # mishint for the combine-vs-replace reconciliation, same as C1.
        assert incoming_calls, f"no incoming call went out at all: {fetch_calls!r}"
        assert "product_ids" in incoming_calls[0], incoming_calls[0]
        assert "shipment_ids" in incoming_calls[0], incoming_calls[0]

        # (b) THE RED: a record-key miss must rerun exactly once, on the RESOLVED
        # kind, regardless of the parser's own mishint - today's code gates the
        # rerun on the parser's hint instead, so this never fires and the fetch
        # stops at one call.
        assert len(incoming_calls) == 2, (
            f"a REFINE miss on a mishinted record key must still rerun once, on "
            f"the RESOLVED kind (a shipment), the same as a correctly-hinted "
            f"turn does: {fetch_calls!r}"
        )
        assert "shipment_ids" in incoming_calls[1], incoming_calls[1]
        assert "product_ids" not in incoming_calls[1], (
            f"the rerun must drop every carried filter, keeping only the current "
            f"message's own record key: {incoming_calls[1]!r}"
        )

        # (c) the rerun's own HIT reply names the dropped product and the
        # container in one sentence (C1's own dropped-filter shape), then prints
        # the rerun's own rows, and stops offering to escalate.
        lines = said.split("\n")
        combo_lines = [ln for ln in lines if code in ln and container in ln]
        assert combo_lines, (
            f"the carried product AND the container must both appear in one "
            f"sentence, naming what the rerun dropped: {said!r}"
        )
        assert other_code in said, f"the rerun's own rows must print: {said!r}"
        assert "escalate" not in said.lower(), (
            f"a successful rerun must not still offer to escalate: {said!r}"
        )

        # (d) the dropped product must not still ride the focus into the next turn.
        state = _state_of(session_factory)
        products_after = (state.get("focus") or {}).get("products") or []
        assert not any(p.get("canonical_code") == code for p in products_after), (
            f"the dropped product must not still ride the focus: {products_after!r}"
        )

    def test_guard_parser_hints_shipment_wrongly_for_a_word_that_resolves_to_a_product_never_reruns(
        self, session_factory, monkeypatch
    ) -> None:
        """The mirror mishint: the PARSER wrongly hints `"inbound_shipment"` for a
        word that the REAL resolver places as a `product` - a typed FILTER over an
        incoming focus, never the domain's record key (`policy_rows.
        RECORD_KEY_KIND["incoming"] == "inbound_shipment"`), so it must never
        rerun even though the mishint happens to match the record-kind gate's own
        (buggy) hint check. This is a GUARD, not the fix's own target: `app/
        services/chatbot/turn_runtime.py::_record_key_rerun_split`'s `keep`/`drop`
        split already reads the RESOLVED `entity_type` (not the hint) for the
        keep-vs-drop question, so `keep` (entities whose RESOLVED kind is
        `inbound_shipment`) comes out empty here and the function returns `None`
        before any rerun - true today, and must stay true once the `current_kind_
        entities` gate above it is corrected to read the resolved kind too (a
        resolved `product` still never equals the domain's record key
        `inbound_shipment`, whichever gate decides it)."""
        _seed_contact_and_get(session_factory)
        carried_code = unique_code("D1GCARRY")
        carried_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=carried_code)
        typed_code = _no_dash_code("D1GTYPED")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=typed_code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                document=["PO"],
                products=[
                    {
                        "raw": carried_code,
                        "hint": "product",
                        "uuid": carried_id,
                        "company_name": "Sorento",
                        "canonical_code": carried_code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            continuation=False,
            entity_op="replace_combine",
            entities=[
                {
                    # THE REVERSE MISHINT - a real product code, hinted as a
                    # shipment (the domain's own record key).
                    "raw": typed_code,
                    "hint": "inbound_shipment",
                    "canonical_code": None,
                    "hint_confident": True,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                return json.dumps(_unknown_envelope_with_rows([]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=typed_code,
            msg_id="zzt-d1-guard-reverse-mishint",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"a typed FILTER (a resolved product, whatever the parser mishinted) "
            f"must never rerun, even on a miss: {fetch_calls!r}"
        )


class TestReconcileFindsADashedTokenByItsFoldedKey:
    def test_dashed_container_still_reconciles_to_inbound_shipment(
        self, session_factory, monkeypatch
    ) -> None:
        """Same shape as `TestC1VariantParserHintMismatchResolvedKindWins`'s own
        rewritten test, with exactly ONE fact changed: the typed record-key token
        carries a dash (`unique_code("CONT")`'s own "ZZT-CONT-xxxx" shape - the
        fixture this file's own C1-variant test first drafted, before measuring
        that `app/services/chatbot/turn/reconcile.py::apply_reconciliation` looks
        up the resolver's own `by_token` map (`turn_runtime.resolve_kinds`) by RAW
        text (`resolved.get(e.get("raw"))`), while that map is keyed by the FOLDED
        token (`fold_token`, dashes and whitespace stripped) - a dashed typed token
        never finds its own resolver hit under that key, so the parser's mishint is
        never rewritten. A real, general, pre-existing defect, separate from owner
        ruling 5's rerun-gate bug (`_record_key_rerun_split`'s own `current_kind_
        entities` gate, which the undashed sibling targets) - this test pins the
        reconcile step on its own, asserted FIRST, so it fails there today rather
        than at the rerun step (already red for the other reason).

        Expected once BOTH are fixed: identical to the undashed sibling -
        `apply_reconciliation` rewrites the parser's "product" mishint to
        "inbound_shipment" (turn 50082c60's own recorded state-diff shape,
        `focus.extra.inbound_shipment` carrying the container under its RESOLVED
        kind, `focus.products` untouched), one combined call, then the rerun."""
        _seed_contact_and_get(session_factory)
        code = unique_code("D1RPROD")
        product_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        # THE ONE CHANGED FACT vs the undashed sibling: a dashed token, not
        # `_no_dash_code`.
        container = unique_code("CONT")
        _seed_shipment(session_factory, container=container)
        other_code = unique_code("D1ROTHER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=other_code)

        _seed_state(
            session_factory,
            focus=_focus(
                domains=["incoming"],
                products=[
                    {
                        "raw": code,
                        "hint": "product",
                        "uuid": product_id,
                        "company_name": "Sorento",
                        "canonical_code": code,
                    }
                ],
            ),
        )

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_incoming",
            domain_hint="incoming",
            domain_in_message=False,
            continuation=False,
            entity_op="replace_combine",
            entities=[
                {
                    # THE MISHINT - same as the undashed sibling.
                    "raw": container,
                    "hint": "product",
                    "canonical_code": None,
                    "hint_confident": True,
                    "current_message": True,
                    "confident": True,
                }
            ],
            document=[],
            status=None,
            order_status=None,
            routing={
                "suggested_team": "purchasing",
                "suggested_agent": "incoming_stock_enquiries",
                "team_source": None,
            },
        )

        def _call(name: str, args: dict[str, Any]) -> str:
            if name == INCOMING_TOOL:
                if args.get("product_ids") and args.get("shipment_ids"):
                    return json.dumps(_unknown_envelope_with_rows([]))
                if args.get("shipment_ids") and not args.get("product_ids"):
                    return json.dumps(_unknown_envelope_with_rows([(other_code, container)]))
            return _unknown_envelope()

        mcp_call, fetch_calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body=container,
            msg_id="zzt-d1-rerun-mishint-dashed",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        # THE RECONCILE FACT, asserted FIRST: `apply_reconciliation` must rewrite
        # the mishint to the RESOLVED kind and land the container on `focus.extra.
        # inbound_shipment` (turn 50082c60's own recorded state-diff shape),
        # whatever separator the raw token carries - today it does not, because
        # `apply_reconciliation`'s own lookup key (raw) never matches the
        # resolver's own key (folded) for a token with a dash in it.
        state = _state_of(session_factory)
        focus_state = state.get("focus") or {}
        shipment_extra = (focus_state.get("extra") or {}).get("inbound_shipment") or []
        assert any(e.get("raw") == container for e in shipment_extra), (
            f"the dashed container must still reconcile onto focus.extra."
            f"inbound_shipment, the same as the undashed turn does: {focus_state!r}"
        )

        incoming_calls = [args for name, args in fetch_calls if name == INCOMING_TOOL]
        assert incoming_calls, f"no incoming call went out at all: {fetch_calls!r}"
        assert "product_ids" in incoming_calls[0], incoming_calls[0]
        assert "shipment_ids" in incoming_calls[0], incoming_calls[0]

        assert len(incoming_calls) == 2, (
            f"a REFINE miss on a mishinted record key must still rerun once, on "
            f"the RESOLVED kind (a shipment), the same as the undashed turn does: "
            f"{fetch_calls!r}"
        )
        assert "shipment_ids" in incoming_calls[1], incoming_calls[1]
        assert "product_ids" not in incoming_calls[1], (
            f"the rerun must drop every carried filter, keeping only the current "
            f"message's own record key: {incoming_calls[1]!r}"
        )

        lines = said.split("\n")
        combo_lines = [ln for ln in lines if code in ln and container in ln]
        assert combo_lines, (
            f"the carried product AND the container must both appear in one "
            f"sentence, naming what the rerun dropped: {said!r}"
        )
        assert other_code in said, f"the rerun's own rows must print: {said!r}"
        assert "escalate" not in said.lower(), (
            f"a successful rerun must not still offer to escalate: {said!r}"
        )


def _unknown_envelope_with_rows(rows: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "intro": "Here is the incoming stock I found." if rows else "No matching results found.",
        "items": [
            {
                "flags": {},
                "title": code,
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {
                        "key": "shipping_container_number",
                        "label": "Container",
                        "value": container,
                    },
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-20"},
                ],
            }
            for code, container in rows
        ],
        "has_result": bool(rows),
        "attachments": [],
        "result_type": "incoming",
        "action_links": [],
    }


# --------------------------------------------------------------------------- #
# HIT HEADER - the scope header names every ledger a pick covers, on a HIT reply.
# --------------------------------------------------------------------------- #


class TestGroupFHitHeaderNamesLedgers:
    """Turns 862f5032 ("1", a three-ledger option) and b0fc36aa ("3", a one-ledger
    option), both HITS (real order rows returned) - the scope header (`Customer:
    ... / Product: all products / Dates: all dates`, `lanes/business/__init__.py`
    ~line 290 / `tail/scope_block.py`) must name the ledger(s) the pick covers, never
    the option's own rollup code ("300-B110"/"300-B129" in the live traces) - the
    SAME rule `TestGroupFCustomerPickHeaderNamesEveryLedgerNotACode` already pins on
    a MISS reply, exercised here on a HIT instead (a different render path)."""

    def test_a_three_ledger_pick_names_all_three_on_a_hit(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        names = [
            "ZZT BATHIDEA MARKETING - IBORN",
            "ZZT BATHIDEA MARKETING - CERAMIC",
            "ZZT BATHIDEA MARKETING - A/C I",
        ]
        db = session_factory()
        for cid, name in zip(ids, names):
            db.add(
                Customer(
                    id=cid,
                    customer_code=unique_code("HDR")[:50],
                    customer_name=name,
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        db.commit()

        option_code = "ZZT-300-B110"
        options = [
            {
                "code": option_code,
                "name": "ZZT BATHIDEA MARKETING",
                "uuid": ids[0],
                "label": "ZZT BATHIDEA MARKETING",
                "uuids": list(ids),
                "payload": {},
                "position": 1,
                "entity_type": "customer",
            },
        ]
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"]),
            open_question={
                "kind": "customer_pick",
                "expects": None,
                "options": options,
                "team": "customer_service",
                "asked_at_turn": 1,
                "payload": {"domain": "order"},
            },
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[1],
            document=[],
            status=None,
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
        )

        order_number = unique_code("ORD")

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([_order_row(order_number, "ZZTPROD1")]))
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="1",
            msg_id="zzt-hdr-a-hit-3",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        assert "here are the" in said.lower() or order_number in said, (
            f"test setup sanity, a real HIT reply: {said!r}"
        )
        # DO-ASK-SIMPLIFY rule 1 (owner, 2 Oct 2026) replaces "name every ledger": the
        # header names each group once (owner, 2 Oct: group names only, no count). These three names carry
        # no bracketed ledger marker, so they are three families, not one.
        assert "Customer: ZZT BATHIDEA MARKETING - IBORN, ZZT BATHIDEA MARKETING - CERAMIC, ZZT BATHIDEA MARKETING - A/C I" in said, (
            f"the HIT reply's header must name each group once, no count: {said!r}"
        )
        assert option_code not in said, (
            f"no customer CODE must ever reach the reply text, header included: "
            f"{said!r}"
        )

    def test_b_a_one_ledger_pick_names_that_one_on_a_hit(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        the_id = str(uuid.uuid4())
        the_name = "ZZT BATH IDEA (KEMAMAN OUTLET)"
        db = session_factory()
        db.add(
            Customer(
                id=the_id,
                customer_code=unique_code("HDR")[:50],
                customer_name=the_name,
                company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.commit()

        option_code = "ZZT-300-B129"
        options = [
            {
                "code": "ZZT-OTHER",
                "name": "ZZT OTHER",
                "uuid": str(uuid.uuid4()),
                "label": "ZZT OTHER",
                "uuids": [str(uuid.uuid4())],
                "payload": {},
                "position": 1,
                "entity_type": "customer",
            },
            {
                "code": "ZZT-OTHER-2",
                "name": "ZZT OTHER 2",
                "uuid": str(uuid.uuid4()),
                "label": "ZZT OTHER 2",
                "uuids": [str(uuid.uuid4())],
                "payload": {},
                "position": 2,
                "entity_type": "customer",
            },
            {
                "code": option_code,
                "name": "ZZT BATH IDEA",
                "uuid": the_id,
                "label": "ZZT BATH IDEA",
                # ONE unique uuid, duplicated - the SAME shape the live turn's own
                # option carried (position 3, "48d59139-..." twice).
                "uuids": [the_id, the_id],
                "payload": {},
                "position": 3,
                "entity_type": "customer",
            },
        ]
        _seed_state(
            session_factory,
            focus=_focus(domains=["order"]),
            open_question={
                "kind": "customer_pick",
                "expects": None,
                "options": options,
                "team": "customer_service",
                "asked_at_turn": 1,
                "payload": {"domain": "order"},
            },
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            reference_target="result",
            reference_positions=[3],
            document=[],
            status=None,
            order_status=None,
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
        )

        order_number = unique_code("ORD")

        def _call(name: str, args: dict[str, Any]) -> str:
            if name in ORDER_TOOLS_LOCAL:
                return json.dumps(_order_envelope([_order_row(order_number, "ZZTPROD1")]))
            return _unknown_envelope()

        mcp_call, calls = _mcp_double(other=_call)
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="3",
            msg_id="zzt-hdr-b-hit-1",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        said = _said(result)

        # CUSTOMER-GROUP ruling (b): an ungrouped ledger prints its own full name.
        assert "Customer: ZZT BATH IDEA (KEMAMAN OUTLET)\n" in said, (
            f"the HIT reply's own header must name the single ledger the pick covers: {said!r}"
        )
        assert option_code not in said, (
            f"no customer CODE must ever reach the reply text, header included: "
            f"{said!r}"
        )


# --------------------------------------------------------------------------- #
# PICK-PATH ZERO-STOCK LADDER - a did-you-mean product pick that HITS zero still
# climbs to the full ladder sentence and the purchasing escalation offer.
# --------------------------------------------------------------------------- #


class TestPickPathZeroStockLadderClimbsAfterADidYouMeanPick:
    """Turn 328e8b00 ("Srtks8060-BL stock") misses with a product did-you-mean
    roster (3 options); turn 96cf765d ("1") picks SRTKS8050-BL - the stock tool
    HITS with total 0 (`.claude/handpass/hp12c-turns-21sep.json`'s own recorded
    compact-granted shape: `value: 0`, `granted_value: "0 (O/S: 0)"`, the SAME
    restricted-field swap `test_rearch_r11_zero_stock_live_replay.py`'s own module
    docstring names, copied here). Production's OWN prod-pasted behaviour (that
    file's own `INCOMING_LEAD`/`WAREHOUSE_OFFER` constants) is: the HIT climbs the
    ladder - incoming rung probed, then the PO rung - and, both missing, ends "Stock
    is 0 at every location, no incoming and nothing on order for SRTKS8050-BL." plus
    the escalation offer, pending becomes a team_pick.
    Expected RED: turn 96cf765d's own live trace shows NO second tool event at all
    (no crossdomain probe ran) - this pins the DESIRED ladder behaviour, not what
    the live trace happened to do.

    Owner ruling 22 Sep 2026, R6 (AC-EQ-5..11): a stock-origin ask (this is one -
    the picked offer's own `open_question.team` was "warehouse") is always suggested
    to the warehouse team, no PO-rung override, even after a did-you-mean pick. The
    pending offer AFTER the ladder answer carries that SAME team forward
    (`open_question_after["team"]`), which is what a later bare "yes" routes to -
    AC-EQ-11."""

    OPTION_A_CODE = "SRTKS8050-BL"
    OPTION_B_CODE = "SRTKS8046-BL"
    OPTION_C_CODE = "SRTKS8047-BL"

    def _options(self, option_a_uuid: str) -> list[dict[str, Any]]:
        return [
            {
                "code": self.OPTION_A_CODE,
                "uuid": option_a_uuid,
                "label": self.OPTION_A_CODE,
                "uuids": [option_a_uuid],
                "payload": {"value": self.OPTION_A_CODE},
                "position": 1,
                "entity_type": "product",
                "stamp": "no stock details",
            },
            {
                "code": self.OPTION_B_CODE,
                "uuid": str(uuid.uuid4()),
                "label": self.OPTION_B_CODE,
                "uuids": [str(uuid.uuid4())],
                "payload": {"value": self.OPTION_B_CODE},
                "position": 2,
                "entity_type": "product",
                "stamp": "no stock details",
            },
            {
                "code": self.OPTION_C_CODE,
                "uuid": str(uuid.uuid4()),
                "label": self.OPTION_C_CODE,
                "uuids": [str(uuid.uuid4())],
                "payload": {"value": self.OPTION_C_CODE},
                "position": 3,
                "entity_type": "product",
                "stamp": "no stock details",
            },
        ]

    def test_zero_filter_the_miss_turn_never_calls_the_stock_tool_unfiltered(
        self, session_factory, monkeypatch
    ) -> None:
        """The MISS turn itself (328e8b00, "Srtks8060-BL stock") - the only typed
        entity is unresolved, so the stock tool must not be called at all."""
        _seed_contact_and_get(session_factory)

        verdict = _parser_output(
            message_type="business_query",
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[
                {
                    "raw": "Srtks8060-BL",
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
        mcp_call, calls = _mcp_double(other=lambda name, args: _unknown_envelope())
        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="Srtks8060-BL stock",
            msg_id="zzt-ladder-miss",
            mcp_call=mcp_call,
        )
        assert result.status == "done", result.error

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert not stock_calls, (
            f"an unresolved-entity miss must never call the stock tool unfiltered: "
            f"{calls!r}"
        )

    def test_the_pick_hits_zero_climbs_the_ladder_and_offers_warehouse(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        option_a_uuid = _seed_product(
            session_factory, company_id=DEFAULT_COMPANY_ID, code=self.OPTION_A_CODE
        )
        _seed_state(
            session_factory,
            focus=_focus(domains=["inventory"]),
            open_question={
                "kind": "product_pick",
                "team": "warehouse",
                "expects": None,
                "options": self._options(option_a_uuid),
                "payload": {"domain": "inventory"},
                "asked_at_turn": 1,
            },
        )

        verdict = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            domain_in_message=False,
            entities=[],
            entity_op="reuse",
            reference_target="dym",
            reference_positions=[1],
            document=[],
            status=None,
            order_status=None,
            routing={"suggested_team": None, "suggested_agent": None, "team_source": None},
        )

        # The live turn's own recorded compact-granted stock envelope shape
        # (`test_rearch_r11_zero_stock_live_replay.py`'s own `STOCK_ENVELOPE_EXACT`
        # convention, copied): one product, `total_on_hand` restricted behind
        # `inventory.sellable`, `value: 0` swapped for `granted_value: "0 (O/S: 0)"`
        # once granted - `_on_hand_number` unwraps it back to zero.
        stock_envelope = {
            "intro": "Stock summary for the requested products.",
            "items": [
                {
                    "flags": {"discontinued": True},
                    "title": self.OPTION_A_CODE,
                    "fields": [
                        {
                            "key": "product_code",
                            "label": "Product Code",
                            "value": self.OPTION_A_CODE,
                        },
                        {
                            "key": "total_on_hand",
                            "label": "Total",
                            "value": 0,
                            "granted_value": "0 (O/S: 0)",
                        },
                    ],
                }
            ],
            "has_result": True,
            "attachments": [],
            "result_type": "stock_compact",
            "action_links": [],
            "restricted_fields": {"total_on_hand": "inventory.sellable"},
        }

        def _stock_mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(stock_envelope)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call)
        answer_probe, probe_calls = _capturing_probe({INCOMING_TOOL: [], PO_TOOL: []})

        result = _run_turn_engine(
            session_factory,
            monkeypatch,
            qf=verdict,
            text_body="1",
            msg_id="zzt-ladder-pick",
            mcp_call=mcp_call,
            answer_mcp_probe=answer_probe,
            attributes=[
                "inventory.sellable",
                "purchase_orders.cost",
                "purchase_orders.placed",
                "purchase_orders.supplier",
            ],
        )
        assert result.status == "done", result.error
        said = _said(result)

        stock_calls = [args for name, args in fetch_calls if name == STOCK_TOOL]
        assert stock_calls, f"the pick must reach the primary stock fetch: {fetch_calls!r}"

        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert incoming_calls, (
            f"a zero-stock HIT must climb to the incoming rung: probe_calls="
            f"{probe_calls!r}"
        )
        po_calls = [(n, a) for n, a in probe_calls if n == PO_TOOL]
        assert po_calls, (
            f"incoming ALSO missing must climb further, to the PO rung: "
            f"probe_calls={probe_calls!r}"
        )

        assert "no incoming" in said.lower(), said
        assert "nothing on order" in said.lower(), said
        assert self.OPTION_A_CODE in said, said
        # Owner ruling 22 Sep 2026, R6 (AC-EQ-5): a stock-origin ask stays warehouse
        # even after climbing the full ladder to the PO rung.
        assert "escalate to warehouse" in said.lower() or "warehouse team" in said.lower(), said

        state = _state_of(session_factory)
        open_question_after = state.get("open_question") or {}
        assert open_question_after.get("kind") == "team_pick", (
            f"the pending after must be the warehouse team_pick: "
            f"{open_question_after!r}"
        )
        # AC-EQ-11: the pending offer's carried team (what a later bare "yes" routes
        # to) is warehouse for a stock-origin ask.
        assert open_question_after.get("team") == "warehouse", open_question_after


# --------------------------------------------------------------------------- #
# M2 - MFG6661 scope (already-landed fix, `359b5327c`, no pytest artifact until now).
# --------------------------------------------------------------------------- #


_RESOLVED_MFG6661_FAMILY = {
    "tokens": ["MFG6661"],
    "intersection": [
        {"entity_type": "product", "canonical_code": "MFG6661-BL", "uuid": "U-BL", "match_tier": "and"},
        {"entity_type": "product", "canonical_code": "MFG6661-GY", "uuid": "U-GY", "match_tier": "and"},
        {"entity_type": "product", "canonical_code": "MFG6661-GM", "uuid": "U-GM", "match_tier": "and"},
        {"entity_type": "product", "canonical_code": "MFG6661-RG", "uuid": "U-RG", "match_tier": "and"},
        {"entity_type": "product", "canonical_code": "MFG6661", "uuid": "U-BASE", "match_tier": "and"},
        {"entity_type": "product", "canonical_code": "MFG6661-PP", "uuid": "U-PP", "match_tier": "and"},
    ],
}
_EMPTY_STOCK_ITEM = {"answers": [], "has_result": False}


class TestM2MFG6661ScopeExactTokenRefusesThePrefixSweep:
    """M2 (21 Sep 2026, hand pass 12 round 2, coder 44, `359b5327c`): turn 0c6730a2's
    own live "Mfg6661 eta" - the typed token "MFG6661" names a REAL candidate
    exactly (the bare MFG6661 SKU, no colour suffix), so `_token_requests`'s prefix
    half must be refused for every OTHER candidate in the intersection -
    `requested` narrows to the one thing actually typed, not all six colour
    siblings the resolver's AND-mode intersection carried (live, pre-fix: "No
    incoming for MFG6661-BL, MFG6661-GY, MFG6661-GM, MFG6661-RG, MFG6661,
    MFG6661-PP." - six things the customer never asked about). Expected GREEN -
    the fix already landed; a RED here is a finding, not the point of this test."""

    def test_exact_token_narrows_requested_to_itself_not_the_sibling_sweep(self) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        out = crossdomain_zeroset(
            _EMPTY_STOCK_ITEM,
            parser={"domain_hint": "incoming", "message_type": "business_query"},
            resolved=_RESOLVED_MFG6661_FAMILY,
            session_block=None,
        )
        xd = out["_xd"]
        assert xd["active"] is True
        assert xd["requested"] == ["MFG6661"], (
            f"the typed token names a real code exactly - the prefix sweep must be "
            f"refused for every other sibling in the pool: {xd!r}"
        )
        assert len(xd["missing"]) == 1
        assert xd["missing"][0]["uuid"] == "U-BASE", xd["missing"]
