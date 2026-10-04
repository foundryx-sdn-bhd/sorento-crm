"""Hand pass 11, finding 1 - LIVE REPLAY. The owner retested defect 1 live on :8081
@ e242b2e55: "check stock srtwc6022:" did NOT climb the ladder, while
`test_rearch_r11_zero_stock_ladder.py` is 14/14 green. That file's own fixtures do not
mirror the live path - see the module docstring below for the two measured seams.

Replays live turn `8781cd47-a357-4c44-85bc-679527daa87b` (contact `900000008`, clone DB
`sorento_ai_automation_rearch`, read-only SELECTs against `chatbot.turns.trace`, no
journey runner, no turn to :8081, no OpenAI call). Everything quoted below is copied
byte-for-byte from that turn's own trace:

* `kind: "apply"` event's own `verdict` - ONE product entity, raw "srtwc6022", hint
  "product", canonical_code null, domain_hint "inventory", intent_hint "check_stock",
  routing `{"suggested_team": "warehouse", "suggested_agent": "general_enquiries"}`,
  `domain_in_message: true`. The SAME event's `state_diff.products.after` shows the
  REAL resolver settled this ONE typed token onto TWO real products by prefix
  ("SRTWC6022-SH-UF" 526f352f-5413-490b-a807-57fbd8772860, "SRTWC6022-SH-UF-NEW"
  ad52f980-441c-408b-954c-cc4ab42a1205) - a family-prefix ask, not a single-code one.
* `kind: "tool"` event - ONE tool call, `crm_inventory_stock_balance_list`, both uuids
  in `product_ids`. Its own `envelope` (below, `_LIVE_STOCK_ENVELOPE_JSON`, quoted
  verbatim) is the REAL presenter output: `result_type: "stock_compact"`,
  `restricted_fields: {"total_on_hand": "inventory.sellable", "location_on_hand":
  "inventory.sellable"}`, and each zero row carries a `granted_value` field
  ("0 (O/S: 5)", "0 (O/S: 0)") alongside a clean numeric `value: 0`.
* The turn's own `kind: "reveals"` event grants `inventory.sellable` (among others) to
  this contact - so `output_structurer`'s restricted-field swap (`fetch.py:2132-2148`,
  `_keep_field`) DOES fire for this turn: `f["value"] = f.pop("granted_value")`
  overwrites the clean numeric `0` with the formatted string `"0 (O/S: 5)"` before
  ANY zero-check ever runs, and no second `kind: "tool"` event (a real rung probe)
  ever appears in the trace - the ladder never even attempted a call, and no warning
  was logged either.

**Two measured seams `test_rearch_r11_zero_stock_ladder.py`'s own fixtures do not
mirror** (this session, tracing `crossdomain_zeroset` / `_rows_all_zero` / `_row_qty`
in `lanes/business/answer.py` and the restricted-field swap in `lanes/business/
fetch.py`), stated here because this is the diagnosis a coder fix needs - NEITHER is
invented, both read directly off the live trace and the code that consumes it:

1. **`crossdomain_zeroset`'s own `requested`-set builder drops a multi-product
   PREFIX match entirely** (`answer.py` ~line 543-560): `exacts = [m for m in prods if
   match_tier == "exact"]`; `if exacts: ...`; `elif len(prods) == 1 and
   canonical_code: ...`; there is no THIRD arm for "more than one product match, none
   of them exact" - the exact shape a bare family-prefix ask like "srtwc6022"
   produces (TWO real products, both `match_tier == "prefix"`, per the trace's own
   `state_diff`). `test_rearch_r11_zero_stock_ladder.py`'s own `_bundle` always hands
   back exactly ONE `match_tier: "exact"` match per typed code (a single-code,
   1:1 resolve), which can never reach this gap - `requested` stays populated in
   every one of that file's 14 green tests, and empty here.

2. **The restricted-field VALUE SWAP happens upstream of the zero-check, in
   `output_structurer`** (`fetch.py:2132-2148`), and `_row_qty` (`answer.py:404-412`)
   reads exactly the key that swap overwrites (`total_on_hand`'s own `value`) - once a
   contact holds the granting permission (`inventory.sellable` here), `value` becomes
   the formatted string `"0 (O/S: 5)"` and `jsc.js_number(...)` on that string is NaN
   (`jsc.py`'s own `_NUMERIC_RE` does not match the trailing "(O/S: n)" suffix), so
   `_rows_all_zero` finds ZERO parseable quantities and reads the whole row set as
   "says nothing about quantity" rather than "all zero" - even for a SINGLE resolved
   code, this alone would silently defeat the ladder. `test_rearch_r11_zero_stock_
   ladder.py`'s own `_stock_row`/`_stock_hit` never sets a `restricted_fields` key on
   the envelope at all, so `output_structurer`'s swap is a no-op there (its own gate,
   `fetch.py`, `if isinstance(restricted, dict) and restricted:`, is never entered) -
   every row that file's own fixtures hand to `_rows_all_zero` keeps a clean numeric
   `value` throughout, which is not the live shape once the field is restricted AND
   granted.

Harness: `test_rearch_r5_production_decides.py::_run_turn_with_mcp_call` /
`test_rearch_r6_review_round.py::_run_turn_engine` (both drive `engine.run_turn`
through the REAL resolver/gate/narrower over seeded Postgres rows, `FetchServices.
mcp_call` and `AnswerServices.mcp_probe` the only doubles) - `_run_turn_engine` is used
here specifically because it is the one variant that takes a caller-supplied
`attributes` list (`test_rearch_r5`'s own helper hardcodes `attributes: []`), needed to
reproduce the live grant (`inventory.sellable`) that seam 2 above depends on. TWO real
products seeded with the trace's own literal codes ("SRTWC6022-SH-UF"/
"SRTWC6022-SH-UF-NEW") so the REAL resolver settles the bare token "srtwc6022" onto
both by prefix on its own - no hand-built `ResolveGateServices.resolve_entity` stub, no
invented `match_tier`.

Postgres only (`session_factory`, blank schema). No live parser, no :8766, no API key -
`text_body`/`qf` replay the recorded verdict. No live turn to :8081.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _capturing_probe, _run_turn_engine

STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_list"
PO_TOOL = "crm_procurement_po_placed_list"

# The trace's own literal codes - the STOCK_ENVELOPE below quotes them verbatim, so the
# seeded products must carry the SAME codes for `crossdomain_zeroset`'s code matching
# to line up (`_norm_code`, upper-cased, no separator change).
CODE_A = "SRTWC6022-SH-UF"
CODE_B = "SRTWC6022-SH-UF-NEW"

# The live turn's own recorded `kind: "reveals"` grant list, verbatim - the restricted-
# field swap (seam 2, module docstring) only fires once `inventory.sellable` is granted.
LIVE_GRANTED_ATTRIBUTES = [
    "inventory.sellable",
    "purchase_orders.cost",
    "purchase_orders.placed",
    "purchase_orders.supplier",
    "sales_orders.outstanding",
    "sales_orders.sales_report",
    "scm.low_stock_report",
]

# `kind: "tool"` event's own `envelope`, quoted byte-for-byte from `chatbot.turns.trace`
# for turn `8781cd47-a357-4c44-85bc-679527daa87b` (read-only SELECT against the clone,
# 21 Sep 2026) - never hand-built. `json.loads` rather than a Python dict literal, so a
# transcription slip shows up as a JSON parse error instead of silently changing shape.
_LIVE_STOCK_ENVELOPE_JSON = r'''{"intro": "Stock summary for the requested products.", "items": [{"flags": {"discontinued": true}, "title": "SRTWC6022-SH-UF", "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWC6022-SH-UF"}, {"key": "total_on_hand", "label": "Total", "value": 0, "granted_value": "0 (O/S: 5)"}, {"key": "location_on_hand", "label": "BRW", "value": 0, "granted_value": "0 (O/S: 5)"}]}, {"flags": {"discontinued": false}, "title": "SRTWC6022-SH-UF-NEW", "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWC6022-SH-UF-NEW"}, {"key": "total_on_hand", "label": "Total", "value": 0, "granted_value": "0 (O/S: 0)"}]}], "has_result": true, "attachments": [], "result_type": "stock_compact", "action_links": [], "fallback_used": false, "last_updated_at": "2026-09-14T17:25:40.036011", "stock_visibility": {"mode": "compact", "source": "contact", "warehouse_codes": null, "hide_zero_locations": false}, "restricted_fields": {"total_on_hand": "inventory.sellable", "location_on_hand": "inventory.sellable"}}'''
STOCK_ENVELOPE = json.loads(_LIVE_STOCK_ENVELOPE_JSON)

# The clone's own real incoming row for the -NEW product (read-only SELECT,
# `inbound_shipments`/`inbound_shipment_lines`, shipment `38e65b1c-e907-42e3-886b-
# e9aaee48877f`, 21 Sep 2026): container IAAU1697450, ETA 2026-09-09, one line shipped
# 9 / received 0 - the SAME container/ETA/qty the owner's prod paste (finding 1) names.
# Shape matches `test_rearch_r9_handpass9_replay.py`'s own `_other` convention for this
# tool (`crm_incoming_stock_list`'s real presenter reads nested `lines`, never a flat
# `product_code` key).
_INCOMING_ROW = {
    "company_name": "Sorento",
    "shipping_container_number": "IAAU1697450",
    "estimated_arrival_date": "2026-09-09",
    "lines": [
        {"product_code": CODE_B, "remaining_incoming_quantity": 9, "warehouse_allocations": []}
    ],
}

# Owner's prod copy, verbatim (same constants `test_rearch_r11_zero_stock_ladder.py`
# pins).
INCOMING_LEAD = "But there is INCOMING stock (ETA) for the requested products:"
NO_STOCK_FOR = f"No stock for {CODE_B}"
WAREHOUSE_OFFER = "escalate to warehouse team"

# The trace's own recorded verdict (`kind: "apply"` event's `verdict`), field for
# field - the class token stays the bare typed word "srtwc6022", never re-pointed at
# either seeded code, exactly as the customer typed it and exactly as the parser
# emitted it.
_RECORDED_VERDICT = _parser_output(
    intent_hint="check_stock",
    domain_hint="inventory",
    domain_in_message=True,
    entities=[
        {"raw": "srtwc6022", "hint": "product", "canonical_code": None,
         "current_message": True, "confident": True},
    ],
    routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries", "team_source": None},
)

# --------------------------------------------------------------------------- #
# Second live example - turn `cfee5933-7cda-4b4c-a482-7ec8b1b81737` (SAME contact
# 900000008, SAME session, the very next real message: "check stock
# srtwc6022-sh-uf-new" - the FULL exact code this time, no separator stripped, one
# clean `match_tier: "exact"` resolve, ONE product, no family-prefix ambiguity at
# all). Isolates seam 2 alone (module docstring): `crossdomain_zeroset`'s `exacts`
# arm DOES fire here (a single exact match populates `requested` correctly) - the
# ladder STILL never climbed live, because the restricted-field value swap alone
# (`fetch.py:2132-2148`, `inventory.sellable` granted) is enough to defeat
# `_rows_all_zero` on its own, with no multi-product gap needed to explain it.
# --------------------------------------------------------------------------- #

_LIVE_STOCK_ENVELOPE_EXACT_JSON = r'''{"intro": "Stock summary for the requested products.", "items": [{"flags": {"discontinued": false}, "title": "SRTWC6022-SH-UF-NEW", "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTWC6022-SH-UF-NEW"}, {"key": "total_on_hand", "label": "Total", "value": 0, "granted_value": "0 (O/S: 0)"}]}], "has_result": true, "attachments": [], "result_type": "stock_compact", "action_links": [], "fallback_used": false, "last_updated_at": "2026-09-14T17:25:40.036011", "stock_visibility": {"mode": "compact", "source": "contact", "warehouse_codes": null, "hide_zero_locations": false}, "restricted_fields": {"total_on_hand": "inventory.sellable"}}'''
STOCK_ENVELOPE_EXACT = json.loads(_LIVE_STOCK_ENVELOPE_EXACT_JSON)

_RECORDED_VERDICT_EXACT = _parser_output(
    intent_hint="check_stock",
    domain_hint="inventory",
    domain_in_message=True,
    entities=[
        {"raw": "srtwc6022-sh-uf-new", "hint": "product", "canonical_code": None,
         "current_message": True, "confident": True},
    ],
    routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries", "team_source": None},
)


def _set_crossdomain_ladder(session_factory) -> None:
    """`chatbot_crossdomain_ladder` (migration `491_chatbot_ladder_incoming_po`) - not
    part of `_enable_business_lane`'s own two-field switch, so set separately, same
    convention `test_rearch_r11_zero_stock_ladder.py::_run` uses."""
    from app.models.user import SystemSetting

    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    row.chatbot_crossdomain_ladder = {
        "inventory": ["incoming", "purchase_order"],
        "incoming": ["inventory", "purchase_order"],
    }
    db.commit()


def _said(result) -> str:
    return "\n".join(
        [((result.reply or {}).get("text") or "")]
        + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
    )


class TestLiveTurnZeroStockFamilyClimbsToIncoming:
    def test_replaying_turn_8781cd47_the_family_prefix_hit_still_climbs(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _set_crossdomain_ladder(session_factory)
        # REAL products, the trace's own literal codes - lets the REAL resolver settle
        # the bare token "srtwc6022" onto BOTH by prefix, on its own (no hand-built
        # resolver stub, per the module docstring).
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_A)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_B)

        def _stock_mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(STOCK_ENVELOPE)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call)
        answer_probe, probe_calls = _capturing_probe(
            {INCOMING_TOOL: [_INCOMING_ROW], PO_TOOL: []}
        )

        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_RECORDED_VERDICT,
            text_body="check stock srtwc6022:", msg_id="zzt-r11-live-8781cd47",
            mcp_call=mcp_call, answer_mcp_probe=answer_probe,
            attributes=LIVE_GRANTED_ATTRIBUTES,
        )
        said = _said(result)
        assert result.status == "done", result.error

        # Test setup sanity, matching the trace's own state_diff: ONE real stock call,
        # BOTH seeded products in its own `product_ids` - a genuine family-prefix HIT,
        # not a roster and not a single-code ask.
        stock_calls = [args for name, args in fetch_calls if name == STOCK_TOOL]
        assert len(stock_calls) == 1, (
            f"test setup sanity: the primary fetch must run exactly once: {fetch_calls!r}"
        )
        product_ids = {str(u) for u in (stock_calls[0].get("product_ids") or [])}
        assert len(product_ids) == 2, (
            f"test setup sanity: the REAL resolver must settle the bare token "
            f"'srtwc6022' onto BOTH seeded products by prefix, matching the live "
            f"turn's own state_diff (two products, one typed token): {stock_calls!r}"
        )

        # The stock block itself is KEPT - prod prints the rows first, both on hand 0.
        assert "*Total:* 0" in said, said
        assert CODE_A in said and CODE_B in said, said

        # Then the ladder, in the owner's prod copy (finding 1's own paste) - THIS is
        # what did not reproduce live (no second tool event, no warning): the incoming
        # rung must be probed for the zero code, exactly once.
        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"a HIT whose rows all read 0 must climb to the incoming rung EXACTLY "
            f"ONCE - live turn 8781cd47-a357-4c44-85bc-679527daa87b made ONE tool "
            f"event only (the primary stock fetch) and NO rung probe at all, no "
            f"warning logged either (see module docstring seams 1 and 2): "
            f"probe_calls={probe_calls!r}"
        )
        po_calls = [(n, a) for n, a in probe_calls if n == PO_TOOL]
        assert not po_calls, (
            "the ladder stops at the first rung with rows - the PO rung must never "
            f"be probed once incoming answers: {probe_calls!r}"
        )

        assert NO_STOCK_FOR in said, said
        assert INCOMING_LEAD in said, said
        assert "IAAU1697450" in said, said
        assert "2026-09-09" in said, said
        assert WAREHOUSE_OFFER in said, said


class TestLiveTurnCfee5933SingleExactCodeAlsoFailsToClimb:
    """Second live example (coordinator, same session): the very next real message,
    "check stock srtwc6022-sh-uf-new" - the FULL exact code, ONE product, no
    family-prefix ambiguity. Isolates seam 2 (module docstring) on its own: the
    restricted-field value swap alone is enough to defeat the ladder, with no
    multi-product `requested`-set gap needed to explain it."""

    def test_replaying_turn_cfee5933_the_single_exact_code_still_climbs(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _set_crossdomain_ladder(session_factory)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_B)

        def _stock_mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(STOCK_ENVELOPE_EXACT)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call)
        answer_probe, probe_calls = _capturing_probe(
            {INCOMING_TOOL: [_INCOMING_ROW], PO_TOOL: []}
        )

        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_RECORDED_VERDICT_EXACT,
            text_body="check stock srtwc6022-sh-uf-new", msg_id="zzt-r11-live-cfee5933",
            mcp_call=mcp_call, answer_mcp_probe=answer_probe,
            attributes=LIVE_GRANTED_ATTRIBUTES,
        )
        said = _said(result)
        assert result.status == "done", result.error

        # Test setup sanity: ONE stock call, ONE product - a genuine single-code exact
        # HIT, no family/prefix ambiguity anywhere in this scenario.
        stock_calls = [args for name, args in fetch_calls if name == STOCK_TOOL]
        assert len(stock_calls) == 1, (
            f"test setup sanity: the primary fetch must run exactly once: {fetch_calls!r}"
        )
        product_ids = {str(u) for u in (stock_calls[0].get("product_ids") or [])}
        assert len(product_ids) == 1, (
            f"test setup sanity: the REAL resolver must settle the full typed code "
            f"'srtwc6022-sh-uf-new' onto exactly the ONE seeded product: {stock_calls!r}"
        )

        assert "*Total:* 0" in said, said
        assert CODE_B in said, said

        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"a HIT whose rows all read 0 must climb to the incoming rung EXACTLY "
            f"ONCE - live turn cfee5933-7cda-4b4c-a482-7ec8b1b81737 (SAME contact "
            f"and session as 8781cd47..., the very next real message, one clean "
            f"exact-tier product resolve) ALSO made ONE tool event only and NO rung "
            f"probe at all - seam 2 alone (the restricted-field value swap) defeats "
            f"the ladder even with no multi-product gap in play: "
            f"probe_calls={probe_calls!r}"
        )
        po_calls = [(n, a) for n, a in probe_calls if n == PO_TOOL]
        assert not po_calls, (
            "the ladder stops at the first rung with rows - the PO rung must never "
            f"be probed once incoming answers: {probe_calls!r}"
        )

        assert NO_STOCK_FOR in said, said
        assert INCOMING_LEAD in said, said
        assert "IAAU1697450" in said, said
        assert "2026-09-09" in said, said
        assert WAREHOUSE_OFFER in said, said
