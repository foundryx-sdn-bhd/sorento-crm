# Browser verification pass 7, 18 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone DB
`sorento_ai_automation_rearch`, lane head `6dc96c237`, alembic `chatbot_rearch_s6e`. Parser version
confirmed in the console UI: `v28 · full · production`. Worktree:
`/Users/tehjayson/Documents/foundryx/sorento_crm/.claude/worktrees/agent-aa7b10e854453193b`. Session:
agent-browser `--session rearch-browser-7`, headless, logged in via `E2E_EMAIL`/`E2E_PASSWORD` from
this worktree's `sorento_crm_frontend/.env.local`. Contact used throughout: CONTACT N
(`+60120000510`). Read first `browser-pass-6-18sep.md` and the "Coder 15 addendum" (head
`6dc96c237`) in `.claude/handoffs/20260915T171114Z-rearch-coder-s0-s2.md` before driving this pass.

Navigation: sidebar clicks from `/` (System > Messaging > Chatbot Console) on first entry, no deep
URL for the first navigation. `get url` checked before trusting every read; one mid-run auth flap
is recorded below (system congestion, not a hijack by another agent's session - `errors`/`console`
were clean at that point).

**Environmental note (load): this pass ran during severe machine-wide contention** - `uptime`
mid-run showed `load averages: 118.07 108.87 66.91` and `top` showed 68% user CPU with several
2+GB `node` processes from other agents' sessions. Backend request latency for
`POST /api/v1/system/chatbot/console/turn` climbed across the run from 2.6s to 130.5s (see
`.claude/handoffs/be-8081.log`, or its lane copy at
`.claude/worktrees/agent-aa7b10e854453193b/.claude/handpass/be-8081.log`), and one turn (row 2's
first "For srtwc286 only" attempt) returned a 500 whose traceback is an `httpx.ReadTimeout`
inside `lanes/business/services.py::call` -> `ai_assistant_service.py::_rpc` (the console's own
MCP call to itself). That 500 also dropped the browser session (a follow-up `get url` returned
`about:blank`, then a re-open redirected to `/signin`) - re-login was required mid-run. Retried
the same message once the request queue had drained and it returned normally at the same content
as a fresh attempt. This is recorded as environmental, not a reproducible app defect: not counted
as one of the five rows' verdicts, but is a real signal the console has no timeout/backpressure
handling for its own MCP round trip under load.

**Process note (send mechanism):** same reliable pattern as pass 6 - `fill "[placeholder='Ask, or
attach voice/image']" "<msg>"` then `focus` the same selector then `press Enter`, confirmed via
`network requests --filter "console/turn"`. Given the congestion above, `wait --load networkidle`
was used after every send instead of a fixed sleep, with an 8s floor between turns as briefed.

**Process defect reproduced (same as pass 6):** navigating the console tab away to inspect a trace
and then reopening `/system-management/chatbot-console` in the same tab loses the server-side
session context for that chain. Two rows below were re-driven end to end in one continuous run
after this was hit once (see row 2 and row 4).

## New defect found this pass, not one of the five rows: Apply tab crashes the trace drawer

Opening the **Apply** tab on the trace drawer for turn `56e10c36-19af-4669-ad4f-803e4090e792`
(row 1's "srtwt" tier-pick answer) threw a full-page error boundary ("Something went wrong").
Console showed:

```
TypeError: apply.state_diff.map is not a function
The above error occurred in the <ApplySection> component. It was handled by the
<ErrorBoundaryHandler> error boundary.
```

This is the "Apply tab closed the drawer instead of switching tabs" symptom pass 6 flagged as
unexplained ("possibly a stale-ref click landing outside the tab button") - it is not a misclick,
it is a real crash. Per the Coder 15 addendum / S3 addendum, `Trace.state_diff` is documented as a
dict (a subset-check shape, e.g. `state_diff.customers.after = []`), but `<ApplySection>` calls
`.map()` on it as if it were an array - a FE/BE shape mismatch. It reproduced identically on a
second turn later in the pass (row 4's "Sales order" -> empty `*orders*:` turn, `347a9ccd`), where
clicking **Field reveals** right after **Parse** also silently closed the drawer with no console
error that time (could not confirm whether that one crashed the same way or is a separate empty-
state issue - not chased further given the pass budget). Screenshot:
`pass7-row1-apply-tab-crash.png`. Not one of the five rows the brief asked for; reported because it
blocks reading `rules_fired` from the Apply tab for any turn whose `state_diff` isn't already an
array-shaped object, which is most of them.

## Row 1 - promo tier pick + ambiguous-token roster (item 1)

### Row 1a - `Promo for srtwc286` then `1`

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Promo for srtwc286` | `Which price tier applies to you? 1. dealer 2. office 3. end user`, branch `check_promotion` | `a0b31458-661d-4712-9c7a-5c3b31797853` |
| 2 | `1` | `*promotions* for SRTWC286-SH-200, ..., SRTWC286-SH-NEW:` - 3 promotions (UPDATED SORENTO A3 FLYER 2026 DEALER, SORENTO A3 FLYER_DEALER USE, JULY SORENTO WATER CLOSET PROMO DEALER), files attached | `f07ba570-b54a-43f1-af32-85c5f57f8fca` |

**Row 1a - PASS.** The tier pick now fetches for real: 3 dealer-tier promotions returned for
SRTWC286, not "No matching results found." Matches the Coder 15 addendum's own measured fix
exactly (3 rows from `PromotionService.list_promotions` where the old `access_levels` name/code
mismatch returned none). Hand-pass-3 row 1 / pass 6 item 1 confirmed fixed.

### Row 1b - fresh chain, `promo for srtwt` (ambiguous token)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `promo for srtwt` | `Which price tier applies to you? 1. dealer 2. office 3. end user`, branch `check_promotion` (no product roster at this step) | `2f6dfb41-91ca-4d92-bcbe-c26e0496d3cf` |
| 2 | `1` | `*promotions* for SRTWT9500H, SRTWT810-BL, SRTWT8212-BODY, SPSRTWT170049, SRTWT1918, SRTWT03CA, SRTWT1116, SRTWT1906, SRTWT175, SRTWT1908-BL-DIY, SRTWT1100H, SRTWT1506, SRTWT1805, SRTWT11SP, SRTWT1110:` - 2 promotions (both dealer flyers), files attached. No product roster, no stamps, at any point in the chain. | `56e10c36-19af-4669-ad4f-803e4090e792` |

**Row 1b - FAIL against the literal ruling** ("expect a product roster with has promo / no promo
stamps"). Observed: "srtwt" never produced a picker at all - it silently resolved to 15 distinct
SRTWT-prefixed product codes and answered promotions for all of them in one reply, with the tier
question asked first (unrelated to the product ambiguity). Trace for `56e10c36` (Parse tab) shows
`entities: []`, `entity_op: "reuse"`, `user_goal: "trying to select the dealer price tier"` -
confirming the product resolution for "srtwt" happened silently, outside this turn's own parse,
consistent with the Coder 15 addendum's own note that jsonb key order asks tier before product and
resolves the product "silently on the answering turn." What the addendum does NOT establish is
that a token resolving to several **distinct product families** (not variants of one code, as
srtwc286's 10 SKUs are) gets a roster at all under the current `product: optional_filter` policy
for the promotion domain - `narrow.py`'s own documented rule is "optional_filter narrows when
named, never asks," which is exactly what was observed here, and which contradicts the addendum's
separate claim that "a token THIS message named that resolves to several things asks its roster."
Measured, not reasoned: no roster, no stamps, for either the srtwc286 case (row 1a, 10 variants
under one code) or this srtwt case (15 distinct codes). Whether the ruling wants a NEW roster path
specifically for the multi-family case, or whether "optional_filter, never asks" is the intended
final behaviour and the ruling's expectation needs revisiting, is the captain's call - reporting
the measured gap, not narrowing it myself.

## Row 2 - customer-scoped refinement across a multi-step disambiguation (item 2/3 territory)

Precondition note: `orders for CHIN CHUN HARDWARE` (per the brief's literal text) resolves to a
3-way ambiguous customer roster, not a single settled customer - unlike pass 6's chain 2, which
had already narrowed to one customer (via a `4` pick) before sending `For srtwc286 only`. Driven
exactly as briefed; the different precondition is reported, not substituted.

First attempt hit the environmental 500 described above on the second message and lost the
browser session; the whole chain was re-driven end to end after re-login, with one extra
send-retry when a `fill`+`press Enter` silently no-op'd (textbox stayed empty, confirmed via
`get value`, no new POST in `network requests`).

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `orders for CHIN CHUN HARDWARE` | `Which customer do you mean? 1. CHIN CHUN HARDWARE SDN BHD - has DO 2. CHIN CHUN HARDWARE AND TIMBER TRADING - no DO 3. JIMMY - I - has DO`, branch `business_query` | `b5fd8956-bb6a-4174-8ebd-227ece8fca80` |
| 2 | `For srtwc286 only` | `Which product do you mean?` - 10-variant SRTWC286 roster, no stamps, **no mention of CHIN CHUN anywhere in the header or roster** | `c4f3fc13-b1e1-4e5b-9756-b8aed6848dcc` |
| 3 | `1` | `Which customer do you mean? 1. CHIN CHUN HARDWARE SDN BHD 2. CHIN CHUN HARDWARE AND TIMBER TRADING 3. JIMMY - I` - the ORIGINAL customer question, re-asked (no "has DO" stamps this time) | `d47a1246-808b-47cb-af2f-1c056a85c3bd` |
| 4 | `1` | `*orders* for SRTWC286-SH-200, CHIN CHUN HARDWARE SDN BHD:` - 2 order lines, both `*Customer:* CHIN CHUN HARDWARE SDN BHD [A/C III]`, both carrying `SRTWC286-SH-200` in `*Products:*` | `04246108-96ab-4759-af99-86015a0f4a2e` |

**Row 2 - PASS on substance, with a display gap noted.** The final answer is scoped to BOTH
SRTWC286-SH-200 AND CHIN CHUN HARDWARE SDN BHD, not the product globally - the customer question
was never actually dropped, it was correctly deferred behind the product disambiguation and
answered on turn 4. This is the outcome item 2/3's fix is supposed to produce (the customer
subject is not lost, and picking a product does not fall back to a global answer). The one gap
against the brief's literal check: the intermediate product roster (turn 2) does not mention
CHIN CHUN anywhere, so a user mid-conversation has no visual confirmation the customer context
survived until the very last reply - purely a display/wording issue, not a scope-loss defect,
since the final answer proves the state was carried correctly throughout.

## Row 3 - outstanding report, date-window header (item 3/4 - "the header shows the window")

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `outstanding for hanlim` | `*Delivery order outstanding*` summary (20 DOs, qty 559, by-location/by-product breakdowns), header names all 6 HANLIM ledgers, `Reply 1 for the delivery order list.` | `a7329590-4674-4650-af59-3148383c412f` |
| 2 | `1` | 20-line DO detail list, each entry carrying DO Number/Customer/Product/Location/DO Qty/Delivered/Outstanding/DO Date | `11350bc8-1432-41f7-a735-90362a690bd9` |
| 3 | `This month only` | Fresh outstanding SUMMARY (not the DO detail list) re-run with **`Order date: 01/09/2026 to 30/09/2026`** in the header, 2 DOs / qty 2, `Reply 1 for the delivery order list.` | `e54707b8-ea2c-4ddc-ab7c-8c8e52f88833` |

**Row 3 - PASS.** The date window now shows in the reply's own header
(`Order date: 01/09/2026 to 30/09/2026`), matching item 3's fix exactly ("the answer states the
date window it searched"). Pass 6 item 4's FAIL (parser captured the window but the header never
showed it) is fixed.

## Row 4 - a named document outranks a position riding along (item 4/5)

**Driven TWICE.** Run 1 (below) landed against a stale MCP server on `:8765` still serving an old
worktree's code (unknown to me at the time); the coordinator flagged this mid-task and restarted
it (`PID 7113`, confirmed via `ps aux` started at the moment of the restart message), then asked
for row 4 to be re-driven after the restart. Run 2 is the one that counts; run 1 is kept below for
the record since the diff between the two runs is itself informative.

### Run 1 (stale MCP server, superseded)

Fresh continuation from a reset (see Row 3's own precondition for why: item 4's check needed the
chain to be right after the DO detail list, not after the date-narrowed report row 3 had already
produced).

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `outstanding for hanlim` | Same summary as row 3 turn 1, `Reply 1 for the delivery order list.` | `6b40407e-a20a-413e-b5ec-1a025f0514e7` |
| 2 | `1` | 20-line DO detail list, **no `Product:`/`Customer:`/`Location:`/`Order date:` filter header block above the numbered lines** | `fb6a8a1a-e576-44fa-befc-2a45274a88d6` |
| 3 | `Sales order` | `*orders*:` - a bare domain header with **nothing after it**: no order lines, no "not enabled for your account" denial, no "No matching results found." | `347a9ccd-5926-42a7-8ee0-fceec6ac262d` |

Recorded at the time as a FAIL distinct from pass 6's own defect (trace for `347a9ccd` showed
`document: ["SO"]` extracted correctly, so the reply was NOT the re-printed DO list pass 6 hit -
but the SO-domain answer rendered as empty content instead of a denial). Superseded by run 2
below: this was very likely the stale-MCP-server artifact the coordinator flagged (`httpx.
ReadTimeout`/empty-envelope behaviour against old code), not a real code defect.

### Run 2 (after the coordinator's MCP restart, counts)

Full chain re-driven from a fresh Reset (a prior tab navigation had dropped the browser's login
session, so the console was reopened via sidebar clicks and CONTACT N re-selected before resending).

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `outstanding for hanlim` | Same summary shape as before, `Reply 1 for the delivery order list.` | `cff5a1a6-2899-4f0d-9ced-ed5b4f415da5` |
| 2 | `1` | 20-line DO detail list, **now WITH the filter header block**: `Product: all` / `Customer: HANLIM TRADING SDN BHD [A/C II], ... (CERAMIC & ELLECI)` / `Location: all` / `Order date: all`, then the 20 numbered DO lines | `4c6d9fa7-f4bd-4643-a8a4-888931f721f1` |
| 3 | `Sales order` | Filter header block (`Product: all` / `Customer: ...` / `Location: all` / `Order date: all`), then `Sales order figures are not enabled for your account.`, then the full DO outstanding summary fallback (20 DOs, qty 559, by-location/by-product breakdown), `Reply 1 for the delivery order list.` | `195d96a5-8c83-4b5b-8a59-1a4e2a17a95a` |

**Row 4 - PASS on run 2, run 1's verdict retracted.** Trace for `195d96a5` (Parse tab) confirms
`document: ["SO"]` extracted the same as run 1. This time the full pipeline renders correctly:
the SO-domain access denial fires (`Sales order figures are not enabled for your account.`,
matching pass 6's own correct denial copy for the fuller phrasing) followed by the ladder-fallback
DO summary - not the re-printed DO list (pass 6's original defect, still confirmed fixed) and not
an empty section (run 1's apparent defect, now shown to be a stale-server artifact, not real code
behaviour). The coordinator's diagnosis is confirmed by a second, independently useful signal: the
DO detail list itself (turn 2) went from missing its filter header block entirely in run 1 to
carrying it correctly in run 2 - the exact "outstanding DETAIL list header ... never live" gap the
coordinator named, now confirmed live post-restart. Screenshot from run 1
(`pass7-row4-sales-order-empty-fail.png`, trace Parse tab showing `document: ["SO"]`) is kept as
the record of the stale-server symptom, not as evidence of a current defect.

**Row 3 footnote, not re-driven:** row 3's own DO detail list turn (`11350bc8-1432-41f7-a735-
90362a690bd9`, the `1` pick) ran in the same pre-restart window as run 1 above and, by the same
mechanism, almost certainly also lacked the filter header block on its DO lines - it was not
specifically checked at the time since row 3's own pass/fail hinged on the SUMMARY report's own
header (`Order date: 01/09/2026 to 30/09/2026`), which is a separate reply already showing
correctly pre-restart and is unaffected by this gap. Not re-driven because the coordinator's ask
was scoped to row 4; flagging it here for completeness rather than silently leaving it unstated.

## Row 5 - regression spot-check (items 6, 7, 8)

### Row 5a - two-domain fan-out, one ungranted (item 8)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `purchase cost and stock for srtwc286` | ONE reply: `Sorry, you are not allowed to access purchase cost` immediately followed by the full `*stock*` section (46 location rows across all 10 SRTWC286 variants) | `b3ac8594-b87b-4294-8ac6-07e8d3eb2211` |

**Row 5a - PASS.** Identical shape to pass 6 item 8 turn 1 - the ungranted-domain refusal and the
granted domain's answer land in the same reply, stock section not silently dropped.

### Row 5b - word-number pick over a 10-option roster (item 7)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `PO for srtwc286` | `Which product do you mean?` - 10-variant SRTWC286 roster stamped has-PO/no-PO (identical to pass 6's supplementary check: `1. ...-SH-200 - no PO` ... `10. ...-SH-NEW - has PO`) | `1f2eb10c-fcdc-4e21-a2e1-93bcab1dcc11` |
| 2 | `Eight` | `*outstanding purchase orders* for SRTWC286-SH-NEW-P:\nNo matching results found.\n\nWould you like me to escalate?` - position 8 resolved correctly (a genuine miss, since position 8 is stamped "no PO") | `24b0091f-b17c-434b-b5e8-d7fedfab4236` |

**Row 5b - PASS.** `Eight` resolved directly to position 8 (SRTWC286-SH-NEW-P), matching pass 6
item 7's word-number confirmation exactly.

## Summary

- **PASS:** row 1a (promo tier pick fetches real promotions - item 1's core fix confirmed), row 2
  (customer scope survives a multi-step product/customer disambiguation - item 2/3's fix holds,
  with a cosmetic gap: the intermediate roster doesn't visually confirm the kept customer), row 3
  (date window now shows in the reply header - item 3's fix confirmed), row 4 run 2 (the fix
  landed clean: SO-domain access denial + DO summary fallback, DO detail list header confirmed
  live, no re-printed DO list - see "MCP restart mid-pass" below, run 1's FAIL is retracted), row
  5a (two-domain fan-out with one ungranted domain, unchanged from pass 6), row 5b (word-number
  picking, unchanged from pass 6).
- **FAIL:** row 1b (an ambiguous token resolving to several DISTINCT product families gets no
  roster and no stamps at all under the current `optional_filter` policy - contradicts the
  ruling's literal expectation; the Coder 15 addendum's own two descriptions of when a roster
  fires ("optional_filter never asks" vs "a token this message named that resolves to several
  things asks its roster") are themselves in tension, and the observed behaviour matches the
  narrower one).
- **MCP restart mid-pass (coordinator-flagged):** the coordinator reported restarting the MCP
  server on `:8765` (it had been serving an old worktree's code), confirmed independently via
  `ps aux` (fresh PID at the moment of the message). Row 4 run 1 - driven before this restart -
  had recorded a FAIL (a content-free `*orders*:` reply to `Sales order` instead of a denial or
  the DO list) that is now understood to be a stale-server artifact: run 2, driven after the
  restart with the full chain re-built from a fresh Reset, produced the correct denial + fallback
  summary AND, independently, the DO detail list's own filter header block (`Product:`/
  `Customer:`/`Location:`/`Order date:`) went from absent in run 1 to present in run 2 - the exact
  gap the coordinator named ("the outstanding DETAIL list header ... was never live"). Row 4's
  verdict is PASS on the counted run. Row 3's own DO detail turn ran in the same pre-restart
  window and likely shared the missing-header symptom, but was not re-driven (out of the
  coordinator's stated scope, and row 3's own PASS hinges on a different reply's header that was
  already correct pre-restart) - flagged in Row 3's footnote rather than left unstated.
- **New defect, not one of the five rows:** the trace drawer's Apply tab throws
  `TypeError: apply.state_diff.map is not a function` and crashes to an error boundary for at
  least one turn shape (confirmed via console log, not a misclick as pass 6 speculated).
- **Environmental:** severe machine-wide contention (load average 118) during this pass caused
  backend `console/turn` latency to climb from 2.6s to 130.5s over the run and produced one
  transient 500 (`httpx.ReadTimeout` inside the console's own MCP call) that also dropped the
  browser's login session; recovered via re-login and retry. Not counted toward the rows'
  verdicts.
- Turn ids for every turn driven this pass are listed inline above (23 total across 8 chains: 2 +
  2 + 4 + 3 + 3 + 3 + 1 + 2, plus 1 discarded/retried send that produced no new turn).
- Trace drawer detail: `branch_kind` visible inline under every console reply (the badge next to
  the parser-version pill, e.g. `check_promotion`, `business_query`). `document`/`entities`/
  `scope_exclusive`/`entity_op` read from the Parse tab's Raw JSON for every turn a defect
  needed measuring. `rules_fired` / the Apply tab could NOT be read for most turns this pass
  because of the crash documented above - only reachable for turns whose `state_diff` happens to
  already be array-shaped (not confirmed which, if any, are).
- Screenshots (one FAIL, one superseded-run record, plus the new Apply-tab defect, all under
  200 KB): `pass7-row1-apply-tab-crash.png` (Parse tab open, Apply tab crash reproduced via
  console log on the same turn), `pass7-row4-sales-order-empty-fail.png` (Parse tab for turn
  `347a9ccd`, run 1 / pre-restart, kept as the stale-server symptom record - not evidence of a
  current defect, since run 2 passed).
- No FE console errors at the final check (`errors` empty); one earlier console error captured
  and quoted above (the Apply-tab crash) was deliberately triggered while diagnosing row 1b, not
  a spontaneous background error.
- Session closed cleanly (`close`, not `close --all`) after this evidence was captured.
