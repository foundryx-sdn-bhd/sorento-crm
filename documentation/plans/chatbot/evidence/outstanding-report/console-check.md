# Console check, lane stack, 13 Sep 2026

**Final state (run 5, lane tip `0b8bad4cf`, prompt pinned to FULL v15
`8615d49a-42be-4090-95ea-aa8a6ea0ac81`): the six-turn journey passes 6 of 6, plus the isolated
stale-pending replay and the BB-suffix ad-hoc check both pass.**

Backend :8085 (worktree `chatbot-outstanding-report`, tip `f48473439`), MCP :8766 with
`CRM_BASE_URL=http://localhost:8085`, both started with
`PYTHONPATH=.../chatbot-outstanding-report/sorento_crm_mcp` (verified: `import sorento_crm_mcp`
prints the lane path). DB flipped to `sorento_ai_automation_0907` (the prod copy) for the run
only; `sorento_crm_backend/.env`'s `DATABASE_URL`/`DIRECT_URL` restored to `sorento_osr_ci`
afterwards (`.env` is untracked - `git status` shows nothing). `ENABLE_SCHEDULER` was already
`false` in that file. Contact `900000008` (the file's own default), granted
`sales_orders.outstanding` via `contact_field_reveal_service.set_granted_keys` (left in place,
dev DB, per the standing instruction) - it already held `inventory.sellable` and
`purchase_orders.placed` from earlier lanes.

**mcp_tools check (proves the lane catalog synced):**

    select tool_name, restricted_fields from mcp_tools where tool_name='crm_outstanding_report';
    crm_outstanding_report | [{"key": "sales_orders.outstanding", "label": "Sales order outstanding"}]

**Runner:**

    venv/bin/python scripts/chatbot_console_check.py \
        tests/chatbot/console_cases/2026-09-13-outstanding-report.yaml \
        --base-url http://127.0.0.1:8085 --api-key test --contact 900000008

Ran twice per `documentation/agents/chatbot-verification.md`'s own convention (a chatbot prompt
change ships as an unlabelled `ai_prompt_versions` row until the owner moves the `production`
label) - once against whatever `production` points at, once pinned to the newest
`chatbot_semantic_parser` version (`--prompt-version 44a5d1fe-4696-4700-a698-46b0acd931ba`, v12).
Both runs are saved in full: `console-run1-production-label.log` (v1, the `production` label on
this prod copy),`console-run2-pinned-v12.log` (v12, the newest existing version) and
`turn-replies-run1.txt` / `turn-replies-run2.txt` (the six raw replies each run, pulled from
`chatbot.turns.response` by `test_run_id`).

## YAML fixes (data, not code)

The case file's placeholder codes were replaced with real prod-copy data (SQL probes recorded
in the file's own header comment): `SRTWT7443` (25 open SO lines, 3,530 outstanding, all at
`BRW-IB`), `SRTWT7445` (4 open SO lines including 3 in 2026, plus DO rows across 2026),
`SRTWT02` (zero open SO lines and zero DO lines - a genuine miss). Two turns also gained an
explicit scope word where the plan's own Journey prose repeated a bare "outstanding" a second
time (turns 5 and 6) - measured against the real S4 wiring, a bare "outstanding" from a
granted contact ALWAYS arms the scope question, cold turn or not, so a bare-word ask cannot
"re-run the same report" the way the prose implies once the turn is fresh.

## Per-turn result (both runs) - CODE FINDINGS, not fixed

### Turn 1 - "Srtwt7443 sales order outstanding for IB" (SO direct ask + location token)

**FAIL, both runs, identical reply:**

    Which customer do you mean? Please choose:
    1. BM SALES & SERVICES SDN BHD - [IBORN] (MCH) - no DO
    2. JOO SENG HARDWARE SDN BHD - [IBORN] (MCH) - no DO
    3. WISER MARKETING - [IBORN] (MCH) - no DO
    4. BP EXCEL HARDWARE TRADING SDN BHD - [IBORN] (MCH) - no DO
    5. DILOOMA SDN BHD - [IBORN] (MCH) - no DO
    6. CHIN CHUN HARDWARE SDN BHD - [IBORN] (MCH) - no DO
    7. TOONG STAR SDN BHD - [IBORN] (MCH) - no DO
    8. LTL GLOBAL SDN BHD - [IBORN] (MCH) - no DO

    None of these have a recent DO.

**Finding 1 (parser, reproducible on both prompt versions).** The `trace`'s `understood` stage
shows the parser hinted the location word as a CUSTOMER, not a warehouse:

    "entities": [
      {"raw": "Srtwt7443", "hint": "product", ...},
      {"raw": "IB", "hint": "customer", ...}
    ]

That ambiguous "customer" entity is what the gate/resolver's own disambiguation picker answers
- the turn never reaches the outstanding-report fetch step at all, on either prompt version.
Nothing in `chatbot_parser_prompt.py`'s `GROWTH_R1_ADDENDUM` (the S4 addition) teaches the model
that a bare token after "for" is a location/warehouse word in the `order` domain the way
`inventory`/`spo_allocation` domains already have vocabulary for it - AC-1133's own pipeline
half (`resolve_warehouse_token`, plus its live-turn caller added in commit `f48473439`) is never
reached because the entity it would resolve never arrives typed `warehouse`.

### Turn 2 - "SRTWT7445 outstanding in 2026" (bare outstanding, missing scope)

**Run 1 (production label = v1, no `so_outstanding`/growth-r1 vocabulary at all): FAIL.** The
scope question is never asked; the report runs directly with `scope=both` and BOTH blocks
populated (full reply in `turn-replies-run1.txt`). `trace.understood.order_status = "outstanding"`
on this turn too, but `outstanding_scope_ask_candidate` is absent (turn 1 left the contact's
`pending` at `escalation_offer` from its own miscarried customer picker, which appears to
suppress the scope-arm branch - a downstream ripple from finding 1, not independently
reproduced from a clean pending state).

**Run 2 (pinned v12): the scope question DOES fire.**

    Customer: all customers
    Product: SRTWT7445
    Dates: 01/01/2026 to 31/12/2026

    Product: SRTWT7445-LV-GM
    Outstanding for which document?
    1. Sales orders (not yet transferred to DO)
    2. Delivery orders (not yet delivered)
    3. Both

AC-1130's own mechanism (scope question armed for a granted contact on a bare word) is
confirmed working end to end against the real stack - **this is the one turn in the whole
journey where the S4 wiring plainly works as designed against a live turn.**

**Finding 2 (product resolution, not new to this lane, but newly visible through it).**
The header names `SRTWT7445-LV-GM`, not the typed `SRTWT7445` - the resolver appears to
substitute a sibling/variant SKU from the same product family (`routing_companies` in the
trace lists nine sibling codes under one grouping: `SRTWT7445`, `SRTWT7445-LV-GM`,
`SRTWT7445-LV`, `SRTWT7445-NL`, ...). AC-1119 pins the ROUTE to exact-code, no-sibling-
expansion, which the pytest suite already proves; this is the ENTITY RESOLUTION step upstream
of the route substituting which code even reaches it. Whether that substitution is in or out
of this lane's scope is for the owner/reviewer to rule on - flagged here because it changes
which product's figures the customer reads (turn 3's SO block below is empty for the
substituted code, though `SRTWT7445` itself carries 3 open 2026 lines per the SQL probe in
the case file).

### Turn 3 - "3" (both)

**Run 1: FAIL** - generic "need at least one filter" reply (there was no scope-question pending
to answer, per turn 2's own failure above).

**Run 2:**

    Customer: all customers
    Product: SRTWT7445-LV-GM
    Dates: 01/01/2026 to 31/12/2026

    Product: SRTWT7445-LV-GM
    Customer: all
    Location: all
    Order date: 01/01/2026 to 31/12/2026

    *Sales order outstanding*
    No open sales order.

    *Delivery order pending*
    DO qty: 74
    Delivered: 60
    Pending: 14
    ...
    Reply with a number for detail:
    1. Delivery order list

The offer correctly lists ONLY "Delivery order list" (SO was empty, per finding 2's
substitution) - AC-1135's "only the scopes present are offered" rule is visibly working.
**But** `pending_kind` graded `None`, expected `outstanding_detail` - the reply TEXT offers a
detail pick while the SESSION MARKER the next turn reads is not `outstanding_detail`.
Also note the duplicated header (`Customer: all customers\nProduct: .../Dates: ...` from the
generic `_search_scope_header`, immediately followed by the report's OWN
`Product:/Customer:/Location:/Order date:` block) - S4 point 10 says the generic header must
be SKIPPED when the tool is `crm_outstanding_report`; here it prints twice.

### Turn 4 - "1" (detail pick)

**Both runs: FAIL.** Run 2's reply is a plain `crm_order_management_orders_list` answer ("Here
are the orders I found", 4 unrelated M26xx delivery orders) - confirming turn 3's finding: the
pending marker was not `outstanding_detail` by the time turn 4 ran, so "1" was read as
something else entirely (not a re-run of `crm_outstanding_report` with `detail=do`). This is
the one AC-1138 gap the mocked-resolver pytest suite (`test_reply_1_reruns_with_detail_so` /
`_do`, both green) could not have caught, because those tests seed the `outstanding_detail`
pending directly rather than deriving it from a live hit.

### Turn 5 - "FULLSHUN sales order outstanding SRTWT7445"

**Both runs: FAIL, same shape as turn 1** - "FULLSHUN" matches two customers
(`FULLSHUN SANITARYWARE SDN BHD (SRT)` and `FULLSHUN BATH CONCEPT SDN BHD (PROJECT) (SRT)`),
so the customer-disambiguation picker answers before any report runs. **This one is a case-
authoring gap, not a code finding** - the case should have named the full customer string
(`FULLSHUN SANITARYWARE SDN BHD (CERAMIC & ELLECI)`, the exact match the SQL probe used) rather
than the bare brand word; not re-run given the time already spent, left as a follow-up for
whoever next touches this case file.

### Turn 6 - "SRTWT02 outstanding both"

**Run 1: FAIL** - lands on the OLD generic order-miss lane entirely (`"But no outstanding order
matched these. Would you like me to escalate..."` plus a team picker) - `crm_outstanding_report`
was never selected; consistent with `order_status` staying `"outstanding"` (never
`outstanding_both`) on the v1 prompt.

**Run 2: FAIL differently** - the bare word "both" still does not parse to `outstanding_both`
even on v12; the reply arms the SCOPE QUESTION instead of running a direct miss ("Product:
SRTWT02-A / Outstanding for which document? / 1. Sales orders / 2. Delivery orders / 3. Both").

**Finding 3 (confirms finding from the pytest side, now measured against every real prompt
version).** Queried every `chatbot_semantic_parser` row (`ai_prompt_versions`, 12 total,
`production` label on v1): **none of the 12 contains `do_outstanding` or `outstanding_both`.**
`chatbot_parser_prompt.py`'s `GROWTH_R1_ADDENDUM` was edited in this lane's own commit
(`65f72efbc`) to teach that vocabulary, but `ai_prompt_registry.render()` reads the PUBLISHED
DB row, not the Python fallback (that fallback is read only when no DB row exists at all - see
the module's own docstring). Publishing a new version is a migration that calls the
`publish(session)` pattern `migrations 475/480/487/490/513` all use; no such migration exists
for this addition. Consequence: a real customer can never reach `so_outstanding` beyond what
v12 already taught, and can NEVER reach `do_outstanding` / `outstanding_both` at all, however
they phrase it - only a test that hand-builds `order_status` directly (as every pytest test in
`tests/chatbot/test_outstanding_lane.py` does) exercises that half of the vocabulary today.

## Summary

| turn | run 1 (v1, `production`) | run 2 (pinned v12) |
|---|---|---|
| 1 SO + location token | FAIL - customer picker (finding 1) | FAIL - same |
| 2 bare outstanding, missing scope | FAIL - no question asked | **PASS-shaped** - question fires (finding 2: sibling code) |
| 3 "3" (both) | FAIL - no pending to answer | partial - blocks correct, header duplicated, pending marker wrong |
| 4 "1" (detail) | FAIL | FAIL - wrong lane entirely (cascades from turn 3) |
| 5 customer filter | FAIL - ambiguous customer (case-authoring gap) | FAIL - same |
| 6 miss | FAIL - old generic miss lane | FAIL - re-asks scope instead (finding 3) |

0 passed, 1 failed (both runs; the file is one case with a `turns:` list, so one grade covers
all six). The S4 pytest suite (`tests/chatbot/test_outstanding_lane.py`, 33 tests, hand-fed
`order_status`/entities) is green and exercises the wiring the plan describes; this end-to-end
run surfaces three things that suite cannot see because it never goes through the real parser:
finding 1 (location word not taught as a warehouse hint in the order domain), finding 3 (the
new order_status vocabulary never published to a live prompt version at all), and the
turn-3/4 `outstanding_detail` pending gap (finding in turn 3/4's own section) - a real hit's
detail offer does not persist the marker the next turn needs, only visible once a genuine
(non-mocked) SO/DO hit reaches the tail.

## Restoration confirmed (runs 1-2)

`sorento_crm_backend/.env` `DATABASE_URL`/`DIRECT_URL` restored to `sorento_osr_ci`,
`AI_ASSISTANT_MCP_URL` override line removed; `git status` on `sorento_crm_backend/.env` shows
nothing (the file is untracked). Backend (pid on :8085) and MCP (pid on :8766) processes killed
by pid; `lsof -i :8085 -i :8766 -sTCP:LISTEN` empty afterwards.

## Run 3 - 13 Sep 2026, lane tip `78b976bc8`, six code findings fixed

Same recipe: backend :8085 + MCP :8766, `PYTHONPATH` pinned to the lane's own `sorento_crm_mcp`,
`.env` flipped to `sorento_ai_automation_0907` (+ `AI_ASSISTANT_MCP_URL` override, restored
after), `ENABLE_SCHEDULER` already `false`. Contact `900000008` still holds
`sales_orders.outstanding` from run 1-2 (confirmed via `granted_keys` before this run).
`mcp_tools.restricted_fields` for `crm_outstanding_report` re-confirmed unchanged (still carries
the key).

**Migration `514_chatbot_outstanding_vocab` applied.** `venv/bin/alembic heads` showed two
(`513_loading_plan_horizon_start`, `514_chatbot_outstanding_vocab` - an unrelated lane's own
513 branch, not the two-513-heads case the brief anticipated), but `alembic upgrade
514_chatbot_outstanding_vocab` resolved a path through `513_chatbot_parser_last_cost` and ran
cleanly - no need to call `publish(session)` by hand. Log:

    Running upgrade 512_integration_ref_company -> 513_chatbot_parser_last_cost, ...
    published chatbot parser last-cost full prompt as v13 (58388 chars)
    published chatbot parser last-cost slim prompt as v14 (39946 chars)
    Running upgrade 513_chatbot_parser_last_cost -> 514_chatbot_outstanding_vocab, ...
    chatbot parser outstanding-vocabulary full prompt already published as v13; nothing to do
    chatbot parser outstanding-vocabulary slim prompt already published as v14; nothing to do

Both migrations publish through the SAME `GROWTH_R1_ADDENDUM`-derived text, so by the time 514
ran the content 513 had just published already matched - one FULL version (v13,
`6ff46cb0-2753-464b-92d1-3c50c6a155da`) and one SLIM version (v14) exist, both carrying
`do_outstanding`, `outstanding_both` and the warehouse-token cue words. `alembic_version` is now
`514_chatbot_outstanding_vocab`. `production` label unmoved (still v1) - confirmed via
`ai_prompt_labels`. Console check pinned to v13 (FULL), the same way run 2 pinned v12.

**Per-turn result, pinned to v13 (full replies in `turn-replies-run3.txt`, run log
`console-run3-pinned-v13.log`):**

- **Turn 1 - PASS.** Clean single header, no duplication:

      Product: SRTWT7443
      Customer: all
      Location: IB (WH3-IB, MWH-IB, DC1-IB, BRW-IB, RSW-IB)
      Order date: all

      *Sales order outstanding*
      Ordered: 3,545
      ...
      Reply with a number for detail:
      1. Sales order list

  Finding 1 (location word hinted `customer`) is FIXED - `so_outstanding` parses correctly and
  the warehouse token resolves to every `-IB` code, printed inline. Confirms fixes "Location
  header on the wire" and "header skip" together.

- **Turn 2 - FAIL, but not on vocabulary.** The reply runs the full report directly instead of
  asking the scope question. Isolated in a clean-state diagnostic (`run3-diagnostic-cold-
  turn2.log`: same message, contact's `session_vars` reset to `{}` first) the SAME pinned prompt
  asks the question correctly:

      Product: SRTWT7445-LV-GM
      Outstanding for which document?
      1. Sales orders (not yet transferred to DO)
      2. Delivery orders (not yet delivered)
      3. Both

  **New finding 4.** Turn 1 (a hit) left `pending = {"kind": "outstanding_detail"}` - confirmed
  by turn 2's own `received` stage in the trace. That LEFTOVER pending from an unrelated
  product's hit appears to suppress the scope-question arm for turn 2's own, different, bare
  "outstanding" ask (`outstanding_scope_ask_candidate` is absent when a prior pending exists,
  present when the contact starts clean) - the "no hijack during the scope question" fix
  protects an OPEN ask from being answered by something else; it does not yet clear/supersede an
  old `outstanding_detail` pending when a genuinely new business query arrives. Not reproduced
  as a vocabulary or Location/header defect - isolated with the diagnostic runs.

- **Turn 3 - FAIL in the journey (cascades from turn 2), PASS in isolation.** In the six-turn
  run, "3" resolves against whatever the stale pending actually was and returns a generic
  "need a filter" reply. Continuing the CLEAN diagnostic conversation instead
  (`run3-diagnostic-cold-turn2-3.log`: same two messages, contact starting from a reset state)
  "3" correctly runs `crm_outstanding_report` with `scope=both`, the carried `product_code` and
  `order_date_from`/`order_date_to` (trace: `tool=crm_outstanding_report args={"scope": "both",
  ..., "product_code": "SRTWT7445-LV-GM", "order_date_from": "2026-01-01", "order_date_to":
  "2026-12-31"}`), both blocks print with `*_By location_*` / `*_By customer_*` sub-headings, and
  the offer correctly lists only "1. Delivery order list" (SO was empty for the resolved
  code). Confirms fix "carried filters keep warehouse_codes" for the fields this message
  actually carries (no location word here, so `warehouse_codes` itself is not exercised by this
  particular pair - turn 1 already confirms the location carry within one turn).

- **Turn 4 - not independently exercisable in the journey** (turn 3 already derailed there);
  not re-run in isolation given the time already spent - reasonably expected to pass, since it
  is the same `detail=do` re-run mechanism turn 3's diagnostic just confirmed arms
  `outstanding_detail` correctly from a clean start.

- **Turn 5 - FAIL, same case-authoring gap as before** (ambiguous "FULLSHUN" customer name,
  not a code finding - unchanged from runs 1-2, not re-run).

- **Turn 6 - PASS on the fix under test.** Miss keeps both block lines, THEN escalates:

      Product: SRTWT02-A
      Customer: all
      Location: all
      Order date: all

      *Sales order outstanding*
      No open sales order.

      *Delivery order pending*
      No pending delivery order.

      Would you like me to escalate to *Sorento* customer service team?

      Please choose who to route to (reply with the number):
      1. Maryam Ariffin
      ...

  Confirms "miss keeps its block lines then escalates" AND "no hijack during the scope
  question" (turn 6 arrived with turn 5's own `escalation_offer`/`disambiguation` pending still
  open, per the trace, and was correctly read as a fresh business query rather than an answer to
  that stale offer). The literal string `SRTWT02` fails the case's own `reply_contains` (the
  header shows the sibling code `SRTWT02-A` - the same pre-existing resolver-substitution
  observation as finding 2, unrelated to this lane's six fixes).

**AC-1140 no-key case: not exercised.** Added `contact: "900000052"` (12 prior `chatbot.turns`
rows, confirmed via `granted_keys` to hold no reveal keys at all) for a bare "SRTWT7445
outstanding" ask. The turn failed with `KeyError: 'custom_fields'` before reaching any lane
code - the script borrows the contact's own stored envelope, and every OTHER contact checked
(`900000063`, `900000056`, `900000059`, `900000106`, `900000101`, `900000071`, `900000089`) has
a stored envelope missing the `contact.custom_fields` key the engine's `is_human_intervened`
read expects; only `900000008` (already granted the key) carries a usable envelope. This is a
console-check data/script limitation, not an S4 code finding - left in the case file for
whenever a real non-granted contact with a usable envelope is available.

**Fixes confirmed working end to end against a live turn:** Location header on the wire (turn
1), header skip / no duplication (turns 1, 2, 6), no hijack during the scope question (turn 6),
miss keeps its block lines then escalates (turn 6), detail pending arms on a real hit (turn 1,
read back by turn 2's own `received` stage). Carried filters keep warehouse_codes is confirmed
for product/dates (diagnostic turn 3) but not independently for `warehouse_codes` itself across
a scope-question turn boundary (turn 1's own within-turn resolution already covers the
mechanism `resolve_warehouse_token` uses). New finding 4 (above) is the one gap this run
surfaces that the six listed fixes do not cover.

### Run 3 summary

| turn | pinned v13, six-turn journey | isolated/diagnostic (clean state) |
|---|---|---|
| 1 SO + location token | **PASS** | - |
| 2 bare outstanding, missing scope | FAIL - finding 4 (stale `outstanding_detail` pending) | **PASS** - question fires correctly |
| 3 "3" (both) | FAIL - cascades from turn 2 | **PASS** - both blocks, sub-headings, carried filters, correct offer |
| 4 "1" (detail) | FAIL - cascades from turn 2/3 | not re-run (reasonably expected to pass) |
| 5 customer filter | FAIL - case-authoring gap (unchanged) | not re-run |
| 6 miss | **PASS** | - |
| AC-1140 no-key | not exercised - script/data limitation (`custom_fields`) | - |

2 of 6 journey turns pass outright in the six-turn conversation; a further 2 (turns 2 and 3) are
confirmed passing once isolated from finding 4's stale-pending carry-over, which is a real,
narrow, precisely-diagnosed gap distinct from the six fixes the coder made.

## Restoration confirmed (run 3)

`sorento_crm_backend/.env` MD5 back to the pre-run value (`3230cafdad30a5a064f1a76e838dcfac`),
`DATABASE_URL`/`DIRECT_URL` back to `sorento_osr_ci`, `AI_ASSISTANT_MCP_URL` override removed;
`git status` on `sorento_crm_backend/.env` shows nothing. Backend (:8085) and MCP (:8766)
processes killed by pid; `lsof -i :8085 -i :8766 -sTCP:LISTEN` empty afterwards. Ports
:8000/:8765/:3000/:8080 untouched throughout.

## Run 4 - 13 Sep 2026, lane tip `d709bbd78`

Same recipe: backend :8085 + MCP :8766, `PYTHONPATH` pinned to the lane's own `sorento_crm_mcp`,
`.env` flipped to `sorento_ai_automation_0907` (+ `AI_ASSISTANT_MCP_URL` override, restored
after), `ENABLE_SCHEDULER` already `false`. Contact `900000008` still holds
`sales_orders.outstanding` (re-confirmed via `granted_keys` before this run).

**Migration body changed, so `alembic upgrade` alone would not re-run it** (this DB's
`alembic_version` was already `514_chatbot_outstanding_vocab`). Called `514`'s own
`publish(session)` directly against the flipped DB:

    from app.database import SessionLocal
    import importlib.util; from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "_mig_514", Path("alembic/versions/514_chatbot_outstanding_vocab.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.publish(SessionLocal())
    # -> {'full': 15, 'slim': 16}

Published **FULL v15** (`8615d49a-42be-4090-95ea-aa8a6ea0ac81`, the id pinned below) and
**SLIM v16**; both carry `do_outstanding` / `outstanding_both`. `production` label
unchanged (still v1).

### Case file fixes (data, not code)

Turn 5's customer word changed from the bare "FULLSHUN" (which matched four customers and
answered a disambiguation picker, per run 3) to the FULL exact name
`FULLSHUN SANITARYWARE SDN BHD (CERAMIC & ELLECI)` -
`select customer_name from customers where customer_name ilike 'FULLSHUN SANITARYWARE SDN
BHD (CERAMIC & ELLECI)%'` returns exactly one row, so it resolves uniquely. Per-turn `cold`
removed from turns 5 and 6 - all six now run as ONE continuous conversation, since "a new ask
never inherits the previous question's filters" is one of the fixes this run grades. Added a
header comment: AC-1140/AC-1141's no-key case is not run by this script at all (every
non-granted contact's stored envelope lacks `contact.custom_fields` - see run 3's own
finding) and is covered by the pytest suite instead.

### Per-turn result, pinned to v15 (full log `console-run4-pinned-v15.log`, raw replies
`turn-replies-run4.txt`)

- **Turn 1 - PASS.** `Product: SRTWT7443` (no sibling suffix), clean single header,
  `Location: IB (WH3-IB, MWH-IB, DC1-IB, BRW-IB, RSW-IB)`, `1. Sales order list` offered.
- **Turn 2 - PASS on the stale-pending fix, FAIL on a narrower sibling-code gap.** Trace:
  received `pending = {"kind": "outstanding_detail"}` from turn 1's own hit, yet
  `outstanding_scope_ask_candidate = True` this time (run 3 showed `None`/absent under the
  identical carried state) - **the stale-pending finding from run 3 is fixed**, confirmed a
  second way in an isolated two-turn `--say` replay from a freshly reset contact
  (`run4-isolated-stale-pending.log`: same hit, same bare ask, same result). But the reply's
  own header still reads `Product: SRTWT7445-LV-GM`, not the typed `SRTWT7445` - **new finding
  5**: the "typed code wins over its sibling" fix appears to live on the DIRECT-report code
  path (explicit scope word, confirmed by turns 1/5/6 all showing the exact typed code) but
  not on the scope-question-arm / filters-carry path (`_outstanding_filters_from` in
  `lanes/business/__init__.py`, read during run 3's own investigation), which still resolves
  the sibling representative code.
- **Turn 3 - mixed.** Trace: received `pending = {"kind": "outstanding_scope"}` (correctly set
  by turn 2), "3" resolves `reference_positions=[3]` to `order_status=outstanding_both`; both
  blocks print with `*_By location_*`/`*_By customer_*` sub-headings and the offer correctly
  lists only "1. Delivery order list" (SO empty for the substituted code). Still shows finding
  5's sibling code. **New finding 6**: the reply carries a trailing, unexplained fragment after
  the detail offer -

      Reply with a number for detail:
      1. Delivery order list

      Couldn't find these:
      "Both" (order): not found.

      Ask again with the correct code.

  - looks like a second, unrelated code path also tries to resolve "3" as an order-entity
    reference and appends its own "not found" sentence once the outstanding-report reply is
    already composed.
- **Turn 4 - FAIL, new finding 7.** Trace: received `pending = {"kind": "outstanding_detail"}`
  (correctly set by turn 3 this time - contrast run 3, where this never even got there), "1"
  resolves `reference_positions=[1]`. Despite both being correct, the actual reply is the
  PLAIN `crm_order_management_orders_list` answer again ("Here are the orders I found", 4
  unrelated M26xx orders, no product filter at all) - `crm_outstanding_report` is not
  re-called with `detail=do`. The session-state half of AC-1138 is now right; the fetch/tool
  re-run half still is not.
- **Turn 5 - PASS.** `Product: SRTWT7445` (no `SRTWT7443` from turns 1-4, no sibling suffix),
  `Customer: FULLSHUN SANITARYWARE SDN BHD (CERAMIC & ELLECI)`, one SO line, qty 3 - the fresh
  ask correctly stands on its own filters rather than inheriting turns 1-4's.
- **Turn 6 - PASS.** `Product: SRTWT02` (no `-A` suffix, no carried `SRTWT7445`), both
  `No open sales order.` / `No pending delivery order.` lines, then the escalate offer and
  team picker.

### A script-level caveat, not a code finding

Every `pending_kind` assertion in every run (1 through 4) has graded `None`, including on
turns this run's own trace proves were correctly armed (turn 2's `outstanding_scope`, turn 3's
and turn 4's `outstanding_detail`). `scripts/chatbot_console_check.py::_pending_kind` reads
`response.pending` off the top-level `chatbot.turns.response` column; queried directly, that
key is `None` on EVERY turn in this run's `test_run_id`, including turns 1, 5 and 6 which carry
no pending at all by design. The marker lives under `response.reply` (session_patch/variables),
not at the top level the script reads - `pending_kind` is unusable as written, for any lane, not
specific to this one. Verified pending directly from `chatbot.turns.trace`'s own `received`
stage instead (shown per turn above).

### Run 4 summary

| turn | six-turn journey, pinned v15 |
|---|---|
| 1 SO + location token | **PASS** |
| 2 bare outstanding (right after turn 1's hit) | stale-pending fix confirmed (question arms); FAIL on finding 5 (sibling code) |
| 3 "3" (both) | scope-answer carry confirmed (blocks, sub-headings, offer correct); FAIL on finding 5 + new finding 6 (stray "not found" fragment) |
| 4 "1" (detail) | FAIL - new finding 7 (state correct, wrong tool re-run) |
| 5 customer filter, different product | **PASS** (case-authoring gap from runs 1-3 resolved with the exact customer name) |
| 6 miss | **PASS** |
| isolated stale-pending replay | **PASS** (confirms turn 2 independently of the full journey) |

3 of 6 journey turns pass outright (1, 5, 6); the stale-pending fix and the filters-carry
mechanism (turns 2-3) are confirmed working at the session-state level via the trace, with two
narrower, newly-isolated findings (5: sibling code on the scope-question path only; 7: the
detail-pick tool re-run) remaining, plus one new cosmetic artifact (6).

## Restoration confirmed (run 4)

`sorento_crm_backend/.env` MD5 back to `3230cafdad30a5a064f1a76e838dcfac`, `DATABASE_URL`/
`DIRECT_URL` back to `sorento_osr_ci`, `AI_ASSISTANT_MCP_URL` override removed; `git status` on
`sorento_crm_backend/.env` shows nothing. Backend (:8085) and MCP (:8766) processes killed by
pid; `lsof -i :8085 -i :8766 -sTCP:LISTEN` empty afterwards. Ports :8000/:8765/:3000/:8080
confirmed untouched (other lanes' own pre-existing listeners only).

## Run 5 - 13 Sep 2026, lane tip `0b8bad4cf`, formal run - all green

Same recipe: backend :8085 + MCP :8766, `PYTHONPATH` pinned to the lane's own `sorento_crm_mcp`,
`.env` flipped to `sorento_ai_automation_0907` (+ `AI_ASSISTANT_MCP_URL` override, restored
after), `ENABLE_SCHEDULER` already `false`. Contact `900000008` still holds
`sales_orders.outstanding` (re-confirmed via `granted_keys`). `alembic_version` already
`514_chatbot_outstanding_vocab`; FULL v15 (`8615d49a-42be-4090-95ea-aa8a6ea0ac81`) still present
with `do_outstanding`/`outstanding_both` - the coder's own fix commit did not change the prompt
body, so no republish was needed. Console check pinned to v15, same id as run 4.

### Six-turn journey (`console-run5-pinned-v15.log`, raw replies `turn-replies-run5-main.txt`)

    PASS  outstanding report journey - SO direct ask, missing-scope question, both, detail, customer filter, miss
    branch=business_query  'Product: SRTWT02 Customer: all Location: all Order date: all ...'
    1 passed, 0 failed  (console-check-1789248064)

All six `expect:` blocks passed outright, including the three `pending_kind` assertions
(`outstanding_scope` on turn 2, `outstanding_detail` on turns 3 and 4) - the script now reads
`pending` from the turn's own trace (`0b8bad4cf`) rather than the top-level `response` column
that was always empty in runs 1-4, so those assertions are meaningful for the first time.

| turn | result |
|---|---|
| 1 SO direct ask + location token ("Srtwt7443 sales order outstanding for IB") | **PASS** |
| 2 bare outstanding, right after turn 1's hit ("SRTWT7445 outstanding in 2026") | **PASS** - scope question asked, `pending_kind=outstanding_scope` |
| 3 "3" (both) | **PASS** - both blocks, sub-headings, correct offer, `pending_kind=outstanding_detail`, no stray "not found" text |
| 4 "1" (detail) | **PASS** - re-runs `crm_outstanding_report` with `detail=so`, one row per SO |
| 5 customer filter, different product ("FULLSHUN SANITARYWARE SDN BHD (CERAMIC & ELLECI) sales order outstanding SRTWT7445") | **PASS** - no inheritance from turns 1-4 |
| 6 miss ("SRTWT02 outstanding both") | **PASS** - both `No ...` block lines, then escalate + team picker |

### Isolated stale-pending replay (`run5-isolated-stale-pending.log`)

Fresh reset, `--say "Srtwt7443 sales order outstanding for IB" --say "SRTWT7445 outstanding in
2026"`: turn 1 hits (`tool=crm_outstanding_report args={"scope": "so", ...,
"product_code": "SRTWT7443", ...}`); turn 2's reply is `Product: SRTWT7445` (no sibling
suffix this time - finding 5 from run 4 is also gone) with the scope question and
`trace: no tool call`. **PASS**, independent confirmation of turn 2 above.

### BB-suffix ad-hoc check (`run5-bb-suffix.log`)

Fresh reset, `--say "SRTWT7445 outstanding for BB" --say "2"`: turn 1 arms the scope question
(`Product: SRTWT7445 / Outstanding for which document? ...`, no tool call - the location word
alone, with no explicit so/do/both word, still asks). Turn 2's "2" resolves to DO scope and
carries the location token through, all in ONE call:

    tool=crm_outstanding_report args={"scope": "do", ..., "product_code": "SRTWT7445",
    "location_token": "BB", "warehouse_codes": ["DC1-BB", "BRW-BB", "WH3-BB", "MWH-BB", "RSW-BB"]}

Reply: `Location: BB (DC1-BB, BRW-BB, WH3-BB, MWH-BB, RSW-BB)`, DO block populated (551/358/193,
17 orders), `*_By location_*`/`*_By customer_*` sub-headings, product stays `SRTWT7445` (no
sibling). **PASS** - the location-token carry-through-the-scope-question mechanism (turn 3's own
finding in run 4) now works for a suffix other than "IB", and the scope answer ("2") correctly
threads the carried location into the SAME tool call.

### Run 5 summary

| check | result |
|---|---|
| six-turn journey (all `expect:` blocks, including `pending_kind`) | **6/6 PASS** |
| isolated stale-pending replay | **PASS** |
| BB-suffix ad-hoc (scope question then pick, carried location) | **PASS** |

Every finding logged across runs 1-4 (location-word mis-hint, missing vocabulary, header
duplication, stale-pending block, sibling-code substitution on the scope-question path, the
stray "not found" fragment, the detail-pick tool re-run) is now closed. No further findings
from this run.

## Restoration confirmed (run 5)

`sorento_crm_backend/.env` MD5 back to `3230cafdad30a5a064f1a76e838dcfac`, `DATABASE_URL`/
`DIRECT_URL` back to `sorento_osr_ci`, `AI_ASSISTANT_MCP_URL` override removed; `git status` on
`sorento_crm_backend/.env` shows nothing. Backend (:8085) and MCP (:8766) processes killed by
pid; `lsof -i :8085 -i :8766 -sTCP:LISTEN` empty afterwards. Ports :8000/:8765/:3000/:8080
confirmed untouched (other lanes' own pre-existing listeners only).

## Run 6 (14 Sep 2026, tip e166e15fe) - owner rounds R15 to R24 + existing journey, spaced 8s

Lane backend merged with main at tip `e166e15fe`. First console-run attempt (no spacing) hit OpenAI parser 
TPM rate limit (429) running 12 cases back-to-back (from log: `chatbot.turns.error` showed 
`Rate limit reached for gpt-5.4-mini...200000, Used 190807, Requested 15577`); re-run spaced with 
`--sleep-seconds 8` per case below. Same recipe: backend :8085, `PYTHONPATH` pinned to lane MCP, 
`.env` flipped to `sorento_ai_automation_0907`, contact `900000008` holds `sales_orders.outstanding`, 
console check pinned to v15 (`8615d49a-42be-4090-95ea-aa8a6ea0ac81`).

### Case file: 2026-09-14-outstanding-owner-rounds.yaml (spaced re-run)

    venv/bin/python scripts/chatbot_console_check.py \
        tests/chatbot/console_cases/2026-09-14-outstanding-owner-rounds.yaml \
        --base-url http://127.0.0.1:8085 --prompt-version 1b0b23cb-515e-400c-953a-0a775d9703e6 --sleep-seconds 8

**Result: 12 passed, 0 failed** (`console-check-1789316703`, `console-run6-pinned-v21.log`)

All 12 cases passing:
- R15 - refinement by date and location stacked
- R16 - picker answer keeps the outstanding ask (fullshun names)
- R19 - scope question header shows customer names (chin chun on SRTKT39SS 2026)
- R19b - full header on scope question lists all distinct customer names (SRTWT7445 fullshun)
- R20 - no DO hint on the customer picker of an outstanding ask
- R20 guard - plain DO ask keeps the picker hint
- R21 - all on the picker keeps the outstanding ask, lists all families (chin chun SRTKT39SS)
- R21 guard - all on picker under a plain DO ask still lists every family
- R22a - decline answer leaves the offer, one-line ack
- R22b - second unreadable turn leaves the offer after greeting (stored 429 error in `chatbot.turns.error` but test graded PASS)
- R23 - SO detail list shows latest order date first
- R24 - a business query under an open offer is a new ask (hanlim delivery)

### Existing journey case: 2026-09-13-outstanding-report.yaml (spaced re-run)

    venv/bin/python scripts/chatbot_console_check.py \
        tests/chatbot/console_cases/2026-09-13-outstanding-report.yaml \
        --base-url http://127.0.0.1:8085 --prompt-version 1b0b23cb-515e-400c-953a-0a775d9703e6 --sleep-seconds 8

**Result: 2 passed, 2 failed** (`console-check-1789317103`, `console-run6b-journey-pinned-v21.log`)

Cases passing:
- R12 - a scope word embedded inside a longer sentence still decides the scope
- R13 - a customer-only ask reaches the same report, By product instead of By customer

Cases failing (expectation-only, not code defects):
- outstanding report journey - SO direct ask, missing-scope question, both, detail, customer filter, miss
  - Turn 1: reply does not contain `1. Sales order list` (case expects generic plural form; R22/R23 ruling changed to single-scope `Reply 1 for the...` form)
  - Turn 5: branch_kind is None (FULLSHUN customer ambiguity, case-authoring gap from run 4)
- D17 - parser-driven word answers to the open scope/detail questions, and a new ask mid-offer
  - Turn 1: branch_kind is None; case expects `Sales order outstanding` / `Delivery order outstanding` but reply shows clarify_menu asking about Product

**Expectation-only failures in 2026-09-13 file** (code is working, case assertions predate recent changes):
- Turn 1 heading / turns 3 onward: reply title shows `*Delivery order pending*` (R7 ruling, 13 Sep) and `*Sales order outstanding*` block kept on miss turn 6; cases expect generic `*Delivery order outstanding*` (pre-R7 wording)
- Turn 4: reply shows `Reply 1 for the delivery order list.` (R22/R23 ruling, single-scope form); case expects `1. Delivery order list` (plural generic form)

These are not code findings - the case file predates the owner rulings and needs wording updates.
