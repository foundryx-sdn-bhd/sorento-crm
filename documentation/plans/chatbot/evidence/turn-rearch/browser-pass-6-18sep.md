# Browser verification pass 6, 18 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone DB
`sorento_ai_automation_rearch`, lane head 4427bb6bb. Parser version confirmed in the console UI:
`v27 · full · production`. Session: agent-browser `--session rearch-browser-6` (via
`AGENT_BROWSER_SESSION=rearch-browser-6`), headless, logged in via `E2E_EMAIL`/`E2E_PASSWORD`
from `sorento_crm_frontend/.env.local`. Contact used throughout: CONTACT N (`+60120000510`).

Navigation: sidebar clicks from `/` on first entry (System > Messaging > Chatbot Console), no
deep URL for the first navigation. `get url` checked repeatedly through the run; every read
matched either `http://localhost:3081/system-management/chatbot-console` or, for trace
inspection, `.../chat-history?turn=<id>` - no cross-session hijack observed. `console` / `errors`
were empty for the whole run (checked at the end).

**Process note (send mechanism):** `find role button click --name "Send message"` proved
unreliable in this pass (silently no-op'd twice, leaving the message sitting in the textbox).
The reliable pattern used for every turn from here on: `fill "[placeholder='Ask, or attach
voice/image']" "<msg>"` then `focus` the same selector then `press Enter`. Confirmed via
`network requests --filter "console/turn"` returning a fresh 200 after every send.

**Process defect found and worked around:** navigating the console tab away (even via a
`click --new-tab` on the `trace` link followed by closing that new tab) and then reopening
`/system-management/chatbot-console` in the same tab **loses the server-side session context**,
not just the client-side transcript display - a follow-up `1` that should have answered a
pending tier question was instead read fresh as `low_signal` ("Hi! How can I help today?").
Because of this, the first attempt at the promo chain (turn id `ec1f11f8...`) is **discarded**;
all chains below were re-run end-to-end in a single continuous tab per chain, with `trace` hrefs
read via `eval` (`document.querySelectorAll('a')` filtered on text `"trace"`) rather than by
clicking the link, so the working tab was never navigated away until a chain was fully done.

Each chain below started from a fresh `Reset` click. 8 seconds separated every turn (`sleep 8`
before each send). No 429s were hit.

## Chain 1 - promo tier + roster stamps + miss (`handpass3-owner-17sep-promo-tier.json`, item 1, item 9)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Promo for srtwc286` | `Which price tier applies to you? 1. dealer 2. office 3. end user` (setup, branch `check_promotion`) | `cc0494a1-c66d-450c-8877-123f07e7959e` |
| 2 | `1` | `*promotions*:\nNo matching results found.\n\nWould you like me to escalate?` branch `check_promotion` | `0bd47e62-3289-49b5-8b02-1157d9f3949a` |
| 3 | `2` | `I am sorry the provided answer does not meet your requirements. Would you like me to escalate to marketing promotion team?` branch `escalate_offer` | `8196d72c-9f44-4bb7-93f8-9e0b2394790d` |

**Item 1 - FAIL.** Ruling: "1" should return promotions FOR srtwc286 at that tier (not "No
matching results"), and a product roster under promotion should show has-promo/no-promo per
option. Observed: the tier pick still returns a bare "No matching results found" with no
product roster and no stamps at all - the same defect class as hand-pass-3 row 1 (fetch
envelope carries entities: [] after the tier pick). Unfixed.

**Item 9 - FAIL / inconclusive, traces to item 1.** Ruling: a pick that misses should keep the
roster and append the escalation offer; a following `2` should pick from the kept roster, not
re-print the offer. Since turn 2 never produced a roster (item 1's own defect), turn 3's `2` had
nothing to pick from - it landed on `escalate_offer` and re-printed the same "would you like me
to escalate" copy, which reproduces hand-pass-3 row 9's original symptom (`97fb7b49 "2"` ->
escalate offer re-printed) rather than confirming the fix. See the supplementary check below for
a cleaner precondition.

**Supplementary check for item 9** (fresh reset, not from a recorded file): `Incoming for
srtwc286` -> roster with stamps (`1. SRTWC286-SH-200 - no incoming` ... `8/9/10 - has incoming`).
Picking `1` (a genuine "no incoming" miss) returned `*incoming stock*: No matching results
found.` followed by a bonus `*stock*` table (the accepted ladder-append shape from pass 5) - **no
escalation offer was appended and the roster was not re-shown**, so this miss path does not
exercise item 9's escalation-offer branch at all; it is a different, unrelated miss handling
(ladder fallback). Item 9 could not be confirmed clean in either precondition this pass. Turn
ids: `0b563b35-f05c-49c9-b9b3-ebe828ae9ab6` (roster), `f861c768-5109-4df6-a57a-d9f20f9ae7b4`
(the "1" miss).

## Chain 2 - ledger family header + refinement + date window (`handpass3-owner-17sep-hanlim-chinchun-all-refinement-this-month.json`, items 2, 3, 4)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Delivery to hanlim` | `*orders* for HANLIM TRADING SDN BHD:` (single clean name), 20 order lines across all HANLIM ledgers, branch `business_query` | `a5a5da98-b3ad-43f1-b028-d3569e02e3a3` |
| 2 | `Chin chun` | `Which customer do you mean? 1. CHIN CHUN HARDWARE SDN BHD - has DO 2. CHIN CHUN HOMEMART SDN BHD - has DO 3. CHIN CHUN HARDWARE AND TIMBER TRADING - no DO 4. JIMMY - I - has DO` | `5e360b9b-09c8-49d5-91ea-86e6b9deadd6` |
| 3 | `All` | `*orders* for CHIN CHUN HARDWARE SDN BHD, CHIN CHUN HOMEMART SDN BHD, CHIN CHUN HARDWARE AND TIMBER TRADING, JIMMY - I:` (each family named once), 20 order lines | `6c306c48-6f85-4305-9eca-9b44cfff145f` |
| 4 | `4` | `*orders* for JIMMY - I:` (named once, not doubled), 4 order lines | `3c833cba-c770-4f6f-8758-b4db8d03eb8d` |
| 5 | `1` | `*orders* for CHIN CHUN HARDWARE SDN BHD:`, 20 order lines | `eb96995e-3343-4cef-91b3-cfcbe1bc909c` |
| 6 | `For srtwc286 only` | `Which product do you mean?` - fresh 10-variant SRTWC286 roster, no stamps | `67df5114-cd19-4cd5-a06f-9d13ded15882` |
| 7 | `1` | `*orders* for SRTWC286-SH-200:` - 20 order lines across MANY different customers, none of them CHIN CHUN | `2975211b-01d8-43be-b139-40c41d747262` |
| 8 | `This month only` | `*orders* for SRTWC286-SH-200:\nNo matching results found.\n\nWould you like me to escalate?` | `60927579-aecb-4b87-98ef-4a43fc247ef4` |

**Item 2 - PASS.** Every header in this chain names each customer family exactly once
("HANLIM TRADING SDN BHD:", "CHIN CHUN HARDWARE SDN BHD, CHIN CHUN HOMEMART SDN BHD, CHIN CHUN
HARDWARE AND TIMBER TRADING, JIMMY - I:", "JIMMY - I:") - no `"JIMMY - I, JIMMY - I"` duplication
anywhere. "All" over the 4-option customer roster fetched all four families in one answer.
Hand-pass-3 row 2 looks fixed.

**Item 3 - FAIL.** `For srtwc286 only`, sent while the subject was "orders for CHIN CHUN HARDWARE
SDN BHD", should narrow that same subject by product, not re-ask. Observed: `Which product do
you mean?` - a fresh, unstamped 10-variant roster, with the CHIN CHUN customer context dropped
entirely. Picking `1` then returned orders for SRTWC286-SH-200 **globally** (customers include
U BATH & KITCHEN, MIRAGE HARDWARE, V BATH CONCEPT, EUROTAN, ZHIN HENG, ...) - CHIN CHUN never
reappears. Trace for turn `67df5114` (Parse tab) shows the parser DID its job correctly:
`entities: [{"raw":"srtwc286","hint":"product","confident":true}]`, `entity_op:
"replace_combine"`, `scope_intent: "specific"`, `scope_exclusive: true`,
`user_goal: "trying to narrow the order enquiry to SRTWC286 only"`. The defect is downstream of
the parser, in the apply/narrow layer: a multi-match product entity triggers a fresh
disambiguation roster instead of combining with the existing customer-scoped subject. Same
defect class as hand-pass-3 row 3, **unfixed**. Screenshot:
`pass6-item3-refinement-roster-reask-fail.png`.

**Item 4 - FAIL (as stated in my brief: "header shows the window").** `This month only` returned
`*orders* for SRTWC286-SH-200:\nNo matching results found.` with no date-window text anywhere in
the header. Trace for turn `60927579` (Parse tab) shows the parser DID capture the window
correctly: `date_filter_start: "2026-09-01"`, `date_filter_end: "2026-09-30"`,
`user_goal: "trying to narrow the order query to this month only"` - this matches the UAC's own
"row 4 withdrawn: the parser returned the date window; only the header was wrong" note exactly.
So the underlying filter is applied; the header-display gap is the only remaining issue, and per
my brief's literal wording ("header shows the window") that gap is still present.

## Chain 3 - outstanding report, DO detail header, Sales order switch (`handpass3-owner-17sep-outstanding-hanlim-detail-sales-order-switch.json`, item 5)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Outstanding quantity to hanlim` | `*Delivery order outstanding*` summary (20 DOs, qty 559, by-location/by-product breakdowns), header names all 6 HANLIM ledgers once, `Reply 1 for the delivery order list.` | `1813b1ad-7683-486c-afe5-42b4f12bd867` |
| 2 | `2` | 20-line DO detail list, each entry carrying `*DO Number* / *Customer* / *Product* / *Location* / *DO Qty* / *Delivered* / *Outstanding* / *DO Date*` | `0cbb99bc-68a0-424a-8406-d3d05bbf4753` |
| 3 | `Sales order` | **Identical** 20-line DO detail list re-printed verbatim (same DO numbers, same order), branch still `business_query` | `e0d6459c-24c1-4b04-b6de-37b0aba7788f` |
| 4 | `Outstanding for hanlim sales order` | `Sales order figures are not enabled for your account.` followed by the DO outstanding summary fallback | `761911e2-24a5-4046-bef8-bfa6ab0a18b2` |
| 5 | `Outstandi delivry for hanlim` (typo) | Same DO outstanding summary, `Reply 1 for the delivery order list.` | `80dd92bc-d61a-44ac-a8e4-ca849d56c9c1` |
| 6 | `1` | 20-line DO detail list with the same full per-line header | `8bf40ed9-0492-44bc-949e-89f0bea601dd` |

**Item 5, first half (row 6) - PASS.** Both DO detail lists (turns 2 and 6) carry
Product/Customer/Location/DO Date per line, not a bare `1. DO Number` list. Hand-pass-3 row 6
looks fixed.

**Item 5, second half (row 5) - FAIL.** Bare `Sales order`, sent right after the DO detail list,
should be a new document scope and re-run the report for SO. Observed: it re-printed the exact
same DO detail list, byte-for-byte, with no scope switch and no denial message. Trace for turn
`e0d6459c` (Parse tab) shows `understood: "Understood as casual"`,
`message_type: "casual"`, but `document: ["SO"]` **was** extracted -
`user_goal: "trying to select the delivery order list"`, `reference_positions: [1]`,
`entity_op: "reuse"`. So the parser correctly flags the SO document token, but also emits
`reference_positions: [1]` pointing at the still-open "Reply 1 for the delivery order list"
offer, and the apply layer follows the position-answer path instead of the document-scope-switch
path - it answers "1" again rather than honoring `document: ["SO"]`. Confirmed this is a
precondition-sensitive bug, not a wholesale SO-support gap: the more explicit
`Outstanding for hanlim sales order` (turn 4) WAS recognized as SO scope (denied via
`Sales order figures are not enabled for your account`, correctly, since CONTACT N lacks that
grant). Same defect class as hand-pass-3 row 5, **unfixed** for the bare two-word phrasing.
Screenshot: `pass6-item5-sales-order-switch-fail.png`.

## Chain 4 - PO roster stamps (`handpass3-owner-17sep-purchase-cost-po.json`, item 6)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Purchase cost for wc286` | `Sorry, you are not allowed to access purchase cost` (CONTACT N lacks this grant, unlike the owner who recorded this chain) | `bb4ee0f6-3b2c-45a9-ac93-7f680fd63057` |
| 2 | `PO for this` | `Which one do you mean? 1. this (customer) 2. this (form)` branch `clarify_menu` | `b3e600c6-53cf-47cd-8845-9a492ab05677` |
| 3 | `All` | `Sorry, you are not allowed to access purchase cost` again | `b4a1bf04-81f7-4e4b-9be6-5295c99471f0` |

**Item 6, as recorded - inconclusive (precondition mismatch, not a defect).** Because turn 1 was
denied before any product ever resolved into focus, `this` in turn 2 had no product antecedent;
trace for `b3e600c6` shows `understood: "Understood as business query about purchase order
(this)"`, `routed: "Routed to Asked to clarify."` - the ambiguous "this (customer)/this (form)"
clarify_menu is a reasonable response to a genuinely dangling reference, not the roster-with-
stamps defect the ruling is about. This chain cannot exercise item 6 for a contact without the
purchase-cost grant.

**Supplementary check (fresh, not from a recorded file) - PASS.** `PO for srtwc286` ->
`Which product do you mean?\n1. SRTWC286-SH-200 - no PO\n2. SRTWC286-SH-P - no PO\n3. SRTWC286-SH-PP
- has PO\n4. SRTWC286-SH - no PO\n5. SRTWC286-SH-UF - no PO\n6. SRTWC286-SH-NEW-150 - no PO\n7.
SRTWC286-SH-150 - no PO\n8. SRTWC286-SH-NEW-P - no PO\n9. SRTWC286-SH-NEW-200 - no PO\n10.
SRTWC286-SH-NEW - has PO`. The roster carries has-PO/no-PO stamps exactly per the ruling, and the
picker stayed open (no premature close). Turn id: `616b3915-15c7-42d5-b239-5b9281db49b6`.

## Chain 5 - stock/incoming roster, multi-pick, word numbers (`handpass3-owner-17sep-stock-incoming-multipick-word-number.json`, item 7)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Check stock srtwc286` | `*stock* for` all 10 SRTWC286 variants, 46 location rows, no picker | `378998fa-c61d-40df-9782-33df94389f8e` |
| 2 | `Incoming` | `Which product do you mean?` roster of all 10 variants stamped has/no incoming (7 no, 3 has) | `31a90099-da07-41ab-89c4-8576066cf9e0` |
| 3 | `1` | `*incoming stock* for SRTWC286-SH-200: No matching results found.` + bonus `*stock*` table (2 rows) | `750381a0-1433-4e41-925b-56d46252580d` |
| 4 | `2 3 4` | `*incoming stock* for SRTWC286-SH-P, SRTWC286-SH-PP, SRTWC286-SH: No matching results found.` + bonus `*stock*` table across all three | `50557d18-dc41-4b61-b404-93e9d177b0b5` |
| 5 | `Eight` | `*incoming stock* for SRTWC286-SH-NEW-P:` 2 container rows (ETA 2026-09-08 / 2026-09-20), attachment note | `0567b850-9225-4248-affc-3f62a7616e67` |
| 6 | `The eigthh one` | Identical answer for SRTWC286-SH-NEW-P (typo tolerance confirmed) | `7f7f9821-6761-4631-a757-92213796e022` |

**Item 7 - PASS.** `Eight` resolved directly to position 8 in the still-open roster
(SRTWC286-SH-NEW-P) with no re-print of the clarify menu, and the deliberately misspelled
`The eigthh one` resolved identically. Hand-pass-3 row 8 confirmed fixed.

## Chain 6 - two-domain, ungranted domain in the mix (item 8, new ruling, not from a recorded file)

| # | Sent | Result | Turn id |
|---|------|--------|---------|
| 1 | `Purchase cost and stock for srtwc286` | ONE reply: `Sorry, you are not allowed to access purchase cost` followed immediately by the full `*stock*` section (46 location rows across all 10 SRTWC286 variants) | `0a880650-b133-4d9a-9161-7e9ac88d2df8` |
| 2 | `Incoming and stock for 7445` | `Which product do you mean? 1. SRTWT7445-LV-BL-NEW - has incoming 2. SRTWT7445-LV-NEW - has incoming 3. SRTWT7445-LV-WEPLS - has incoming 4. SRTWT7445-NEW - has incoming` | `768ac97d-b100-4d71-a302-00858d1369be` |
| 3 | `1` | `*incoming stock* for SRTWT7445-LV-BL-NEW:` 1 container row, followed by `*stock* for SRTWT7445-LV-BL-NEW:\nNo matching results found.` - both sections present | `7b25ed86-58db-4a8c-8008-7fac5321868b` |

**Item 8 - PASS on all three sub-checks.** The ungranted-domain refusal and the granted domain's
answer land in the SAME reply (turn 1). The roster for a two-domain ask carries stamps for the
domain that has them (turn 2). Picking off that roster answers BOTH named domains in one reply
(turn 3) - the `*stock*` section is present (as an explicit "No matching results found", not
silently dropped). This directly reverses pass 5's turn 15 FAIL (RULING 11 fan-out loss on
`Incoming and stock for 7445`) - fixed on this head.

## Summary

- **PASS:** item 2 (ledger family header), item 6 (PO roster stamps, via the supplementary
  product-first check - the recorded chain's precondition doesn't apply to CONTACT N), item 7 (word
  numbers), item 8 (two-domain fan-out with one ungranted domain, all 3 sub-checks), and the
  first half of item 5 (DO detail list carries the full filter header).
- **FAIL:** item 1 (promo tier pick still returns "No matching results" with no stamped roster),
  item 3 (a product-only refinement re-asks a fresh roster and drops the prior customer scope -
  parser is correct, apply/narrow layer is not), item 4 (date window is applied correctly per
  trace but the reply header never shows it, matching the UAC's own "withdrawn, header-only"
  characterization), the second half of item 5 (bare `Sales order` re-prints the DO list instead
  of switching scope; the parser DOES extract `document: ["SO"]` but `reference_positions: [1]`
  wins in apply), and item 9 (could not be confirmed in either observed miss path this pass - the
  promo miss has no roster to keep, in and of itself item 1's defect, and the incoming-stock miss
  takes a different, escalation-free ladder-fallback path).
- Turn ids for every turn driven this pass are listed inline above (30 total: 3 + 8 + 6 + 3 + 6 +
  3 + 1 supplementary PO turn + 2 supplementary miss-after-roster turns).
- Trace drawer detail: `branch_kind` is always visible inline under every console reply (the
  badge next to the parser-version pill); `lane` was read from the Stages tab's `routed` line
  where opened (5 turns: `67df5114`, `60927579`, `e0d6459c`, `b3e600c6`, plus the Parse-tab JSON
  for each). `rules_fired` was NOT located in this pass - the Apply tab closed the drawer instead
  of switching tabs on the one attempt made (possibly a stale-ref click landing outside the tab
  button); not chased further given the turn budget. No FE console errors or uncaught exceptions
  across the whole run; every `console/turn` POST returned HTTP 200; no 429 was hit.
- Screenshots (FAILs only, both under 200 KB): `pass6-item3-refinement-roster-reask-fail.png`
  (trace Parse tab for turn `67df5114`), `pass6-item5-sales-order-switch-fail.png` (trace Stages
  tab for turn `e0d6459c`).
- Session closed cleanly (`close`, not `close --all`) after this evidence was captured.
