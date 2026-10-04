# PLAN: stock ask with no placed subject never calls the stock tool unfiltered (hotfix)

Status: implemented, awaiting review. Track: small fix. Owner ask 22 Sep 2026 ("can we hotfix this?").
UAC: `chatbot-stock-no-subject-hotfix-22sep-acceptance-criteria.md`.

## Evidence (prod, 22 Sep 2026 12:52, contact 900000081, turns 59-60)

- Turn 59 "Srtwc8608-p-rl": miss + did-you-mean roster (SRTWC8601-P-RL, SRTWC8601-RL,
  SRTWC8602-RL). The token resolved only as a `product_set` (incompatible with
  inventory), so `turn/reconcile.py` rewrote its hint and `turn/apply.py::_focus_rules`
  stored it on `focus.extra["product_set"]` with no uuid.
- Turn 60 "Stock": parser `entities: []`, `entity_op: reuse`, domain inventory. The plan
  carried an inventory `FetchSpec` with zero entities (`_narrow_and_plan`'s
  `_REFUSES_EMPTY_SUBJECT` guard is multi-domain only). `with_carried_entities` handed
  the resolver the `product_set` token; it placed (so `unplaced` was empty) but is not in
  `gate.ALLOWED["inventory"]`, so `compatible_entities` was empty.
  `turn_runtime.make_tool_runner.runner`'s `would_be_unfiltered` guard needs
  `bool(unplaced)` and did not fire; `crm_inventory_stock_balance_list` is not in
  `policy_rows.ENTITY_FILTER_REQUIRED_TOOLS`; the tool ran with no filter and the reply
  listed 50 rows of the whole book ("Stock details found for the requested products").
- Pre-rearch behaviour (still the contract, `gate.py:120`): `ALLOWS_EMPTY["inventory"]
  is False`, "a bare 'stock?' must ask which product", reply
  "That would search every stock we have - I need at least one filter to narrow it
  down. Give me a ..., and I can look it up." (`answer.py` `needs_scope` branch).

## Fix (one seam)

`app/services/chatbot/turn_runtime.py::make_tool_runner.runner`: an inventory fetch whose
entities carry NO uuid at all (empty list, or every row unplaced / uuid-less) is refused
before the tool call, unless the intent is in `gate.INTENTS_ALLOWING_EMPTY`
(`low_stock_report`). Reads `gate.ALLOWS_EMPTY` / `gate.INTENTS_ALLOWING_EMPTY` (one
copy, never a second table). The refusal composes production's own scope-needed
wording (the `needs_scope` sentence in `answer.not_found_error_message`), not a new
string. AS BUILT: no helper extraction was needed - the runner asks `gate.run_gate` for
the verdict (so `ALLOWS_EMPTY` / `INTENTS_ALLOWING_EMPTY` and the reason string each stay
in one place) and stamps it on the refusal fragment as `scope_gate`;
`answer_bridge.answer_for` hands that to `not_found_error_message` in place of the
resolver's own gate, for the miss TEXT only, and its existing `needs_scope` branch
writes the sentence. `run_miss_lane` and the breakdown bullets keep the resolver's gate.
The stamp rides ONLY this hole - a `would_be_unfiltered` refusal (R6/R8) is excluded, so
a named-but-unresolved subject keeps its own "could not find it" miss.
Scope: `inventory` only. Trigger to widen: a second domain measured dumping unfiltered.

The scope-needed WORDING holds on a SINGLE-domain turn. A multi-domain fan-out ("stock
and promotions", no entity) still refuses the tool - the refusal is the runner's, one
`FetchSpec` at a time - but renders `fetch.NO_RESULT_INTRO` ("No matching results
found.") instead of the sentence, because `engine.py`'s `bridge_answers_a_miss` requires
`len(fetch_plan.fetch) == 1` before `answer_bridge.answer_for` composes a miss at all;
a two-domain plan is composed by `turn/compose.py`, which never reads this gate. The
no-dump guarantee (AC-1790) is unaffected. Widening the sentence to the fan-out means
changing who composes a multi-domain miss, which is not a hotfix.

Not in scope: making `ALLOWS_EMPTY` a `chatbot_domains` column (needs a migration; owner
asked, answered "not configurable today").

## Tests (pytest, engine-level, parser mocked, real gate, MCP stubbed)

New file `tests/chatbot/test_rearch_r13_stock_no_subject.py`, in the harness convention
`tests/chatbot/test_rearch_r12_phase3_fixes.py` documents, with the helpers imported from
the files that actually define them: `_run_turn_engine` from
`test_rearch_r6_review_round.py`; `_mcp_double` / `_seed_contact_and_get` from
`test_rearch_r5_production_decides.py`; `STOCK_TOOL` / `_focus` / `_said` / `_seed_state`
/ `_unknown_envelope` from `test_rearch_r12_handpass12.py`; `_parser_output` from
`test_engine.py`; `_seed_product` from `test_engine_company_scope.py`.

- T1 the live chain: focus `extra["product_set"]=[{"raw":"Srtwc8608-p-rl","hint":
  "product_set","canonical_code":None}]`, `products=[]`, `domains=["inventory"]`; open
  `product_pick` pending with three options; verdict entities `[]`, `entity_op reuse`,
  `intent_hint check_stock`, `domain_hint inventory`, `domain_in_message True`. Stock
  tool stub returns 50 rows if called. Assert: stock tool NOT called; reply starts
  "That would search every stock we have"; reply does not contain "Stock details found".
- T2 bare "stock?" on a fresh contact (no focus, no pending), same verdict shape: same
  assertions.
- T3 guard: `intent_hint low_stock_report`, no entities: stock/low-stock tool IS called
  (unchanged; `INTENTS_ALLOWING_EMPTY`).
- T4 guard (reviewer S1): a typed token that resolved to NOTHING ("Srtks8060-BL stock",
  the R6/R8 shape) still refuses the tool and keeps its OWN miss wording - the refusal's
  fragment carries no `scope_gate`, so the scope-needed sentence never replaces the
  "could not find it" text. Pinned at `make_tool_runner.runner` directly (red without
  the fix) and end to end through the engine.
- Existing guards that must stay green: `test_rearch_r12_phase3_fixes.py` (P3, P9),
  `test_low_stock_lane.py`, `test_turn_replay.py` (whole corpus).
