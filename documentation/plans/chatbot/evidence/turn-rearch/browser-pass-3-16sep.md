# Browser verification pass 3, 16 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone of the prod copy. Lane
head throughout this run: `30b4c25ce` ("fix(chatbot): one Save on the chatbot settings page, and
the console's trace link opens the turn") - confirmed unchanged at both the start and the end of
the run (`git log --oneline -1`), so no coder activity landed mid-pass this time. Session:
agent-browser `--session rearch-browser-3`, headless, logged in via `E2E_EMAIL`/`E2E_PASSWORD`
from `sorento_crm_frontend/.env.local`. Closed cleanly at the end (`close`, not `close --all`).

Navigation: sidebar clicks from `/` throughout (System > Messaging > Chatbot Console / Chatbot
Domains / Chat History; Users & Access > Settings > Chatbot tab), including the mobile hamburger
at 375px. `get url` checked before trusting reads at every page transition. No cross-session
hijack observed - `get url` always matched the page I expected.

Traps from pass 2, re-checked: the Radix pointer-events trap (CLI `click @ref` off a fresh
`snapshot -i` used throughout, no `eval(...).click()` on any Radix control) did not reproduce
this pass - all Tier-order/Cross-domain-ladder reorder clicks landed cleanly on the first try.
`scrollintoview @ref` was run before every click, including Save/Move/Sent-tab clicks; no
off-screen no-op was hit.

| # | Step | Result | Evidence |
|---|------|--------|----------|
| 1 | Console, contact CONTACT N: Reset then 12-turn chain (stock / incoming / incoming-pick "1" / photo / photo-pick "2" / delivery roster / delivery-pick "1" / hello / "1" again / outstanding for chin chun / pick "4" / "yes" to escalate) | **10 of 12 turns PASS, 2 FAIL** | See breakdown below. |
| 2 | Settings > Chatbot: exactly one Save button; Move Dealer down; toggle Episode recall; Save once; reload; both persisted; restored | **PASS, full pass** - both prior pass-2 defects (2 Save buttons, broken "Move down") are fixed | See below. |
| 3 | Console "trace" link on the last turn | **PASS** - both prior pass-2 defects (dead-end trace link, missing Sent panel) are fixed | See below. |
| 4 | 375px: console chain's first two turns + Settings page Save | **PASS** | See below. |

## Step 1 detail - Console, contact CONTACT N (12-turn chain)

Reset clicked first (cleared any prior transcript); contact combobox set to "CONTACT N" via the
searchable combobox (types + click, no Radix trap here) before Reset, and it persisted across
the whole chain.

| Turn | Sent | Branch chip | Result | Notes / trace turn id |
|---|---|---|---|---|
| 1 | `check stock srtwc286` | business_query v23 | **PASS** | Real stock table, 46 rows across the 10 SRTWC286 variants (same shape as pass 2). `719ca35f-dfe1-42ea-b238-8888f3b23cb4` |
| 2 | `incoming` (no product) | business_query v23 | **PASS, with a shape note** | Not "No matching results" (the AC's hard failure bar), not a one-option roster either - a full 10-variant roster "Which product do you mean? 1. SRTWC286-SH-200 ... 10. SRTWC286-SH-NEW", but without the has/no-incoming stamps the brief's first alternative describes. Neither hard-failure shape from the AC, worth the captain's eye as a softer miss (roster present, stamps absent). `52c729ca-36e0-458d-ab24-173fd49312b5` |
| 3 | `1` (reply to turn 2's roster) | business_query v23 | **PASS - pass 2's UUID defect is fixed** | `*incoming stock* for SRTWC286-SH-200:\nNo matching results found.\n\n*stock* for SRTWC286-SH-200:...` - named by CODE, no UUID anywhere, no access_denied, no repeated roster. The empty incoming result is an honest answer bundled with a bonus stock table, not a broken-lookup shape. `9c2b0c4f-51a6-47cf-893d-b30b6b30a8a7` |
| 4 | `photo srtwc286` | business_query v23 | **PASS** | Roster "Which product do you mean?" listing all 10 variants. `1eefe1e6-ec19-46ee-be01-8b9bd1fea3b7` |
| 5 | `2` (reply to turn 4's roster) | business_query v23 | **PASS - pass 2's cosmetic UUID leak is fixed** | Header reads `*product attachments* for SRTWC286-SH-P, photo:` (code, not UUID) with 4 real attachments (spec sheet + 3 certs, one expired). `6025f26c-3ba6-445a-b208-b1add43510b8` |
| 6 | `delivery to hanlim` | business_query v23 | **PASS** | Customer roster, 6 real names, e.g. "HANLIM TRADING SDN BHD [A/C II]", never a code. `b5f251a9-106e-479b-b08b-2e45d5abdaa9` |
| 7 | `1` (reply to turn 6's roster) | business_query v23 | **PASS** | `*orders* for HANLIM TRADING SDN BHD [A/C II]:\nNo matching results found.\n\nWould you like me to escalate?` - header names the customer, no UUID; an honest empty for that specific subsidiary account, not the broken-lookup shape from pass 2. `dbefb80a-d219-4141-92d7-0babedd44b68` |
| 8 | `hello` (escalate? question still open from turn 7) | low_signal v23 | **PASS - pass 2's regression is fixed** | Reply: "Hi there! How can I help today?" - a genuine casual reply on its own `low_signal` branch, not a verbatim repeat of the prior business answer. `d9c09b50-3be2-433e-bb5b-5ba3d9539d6e` |
| 9 | `1` (answering the still-open "Would you like me to escalate?" question, per the brief's "if any question is still open answer it with 1") | business_query v23 | **FAIL** | Re-ran (or replayed) the exact same business_query verbatim: `*orders* for HANLIM TRADING SDN BHD [A/C II]:\nNo matching results found.\n\nWould you like me to escalate?` byte-for-byte identical to turn 7's answer, including re-offering the same escalate question. A bare "1" with no active numbered roster (only a yes/no escalate question was open) should not silently re-execute the last business lookup - it is neither an escalate "yes" nor a numbered pick. `67fcbb6a-05a5-49a3-850d-0c8ce5139353` |
| 10 | `outstanding for chin chun` | business_query v23 | **PASS, with a roster-consistency note** | Roster returned (not "No matching results"): `Which customer do you mean?\n1. 300-C043\n2. 300-C124\n3. 300-C001\n4. CHIN CHUN HARDWARE SDN BHD - [A/C I]`. Options 1-3 are shown as raw account codes, option 4 as a full name - an inconsistent roster (3 codes + 1 name) rather than a UUID leak, but still worth the captain's eye given the "no UUIDs, resolve to human-readable identifiers" cursor rule's spirit. `c16197ef-ec62-4eb3-a116-e536f721f80a` |
| 11 | `4` (pick "CHIN CHUN HARDWARE SDN BHD - [A/C I]") | business_query v23 | **PASS, with a field-binding note** | `*orders* for CHIN CHUN HARDWARE SDN BHD - [A/C I]:\nProduct: all\nCustomer: JIMMY - I\nLocation: all\nOrder date: all\n\n*Delivery order outstanding*\nNo outstanding delivery order.\n\nWould you like me to escalate?` - the header names the picked customer correctly and there is no UUID, but the body's own `Customer:` field reads "JIMMY - I" rather than the resolved customer name - looks like a mis-bound field (possibly a salesperson or a truncated label) inside an otherwise-correct answer; worth a second pass by whoever owns that answer template. `1f75f596-143e-4fb3-b32e-870375ae9dfa` |
| 12 | `yes` (to turn 11's "Would you like me to escalate?") | business_query v23 | **FAIL** | Expected the escalation lane (a named team or a team pick per the brief). Got the exact same business_query answer as turn 11, byte-for-byte, re-offering "Would you like me to escalate?" again - "yes" did not route to escalation at all. Screenshot: `pass3-step1-yes-escalate-no-op.png`. `3af6c1aa-3e36-4bbe-ace6-e01afdf7ef87` |

Summary for step 1: turns 1/2/3/4/5/6/7/8/10/11 pass (10 of 12, three with a shape/consistency
note for the captain but none violating the AC's literal hard-failure bar); turns 9 and 12 fail.
Both failures are the same root shape: **once a business_query answer has already asked "Would
you like me to escalate?", a plain "1" or a plain "yes" sent in reply does not resolve that open
question - it re-runs (or replays) the identical prior business answer and re-asks the same
escalate question**, rather than either routing to escalation (for "yes") or asking for
clarification (for an out-of-range "1"). This is a regression of the exact shape pass 2 found for
`hello`-while-open (turn 9 there), except this pass's `hello` case (turn 8 here) is now fixed -
the surviving bug is specifically the escalate-question's own yes/1 handling, not casual-message
routing. Every `/api/v1/*` call across all 12 turns returned 200 - both failures are
business-logic/routing defects, not network or auth errors.

## Step 2 detail - Settings > Chatbot

Reached via Users & Access > Settings > Chatbot tab (`/user-management/settings/chatbot`).

- **PASS against "exactly one Save button on the page"** (pass 2 found 2): `snapshot -i` grep
  for `"Save"` returns exactly 1 match on the whole page. The Cross-domain ladder card has its own
  `Reset`/`Add a rung...` controls but no Save button of its own - only the page-bottom bar saves
  it, confirmed via the same `POST /api/v1/user-management/settings/general` firing 3 times (one
  request per payload group: switches, memory, tier-order/ladder) inside a single page-bottom
  Save click.
- **PASS against "Move X down" (pass 2 found this broken)**: `scrollintoview` then `click @ref`
  off a fresh `snapshot -i` on "Move Dealer down" changed the order `Dealer, Office, End user` ->
  `Office, Dealer, End user` in one click, confirmed by re-snapshotting the button labels
  immediately after (Move Office up/down, Move Dealer up/down, Move End user up/down, with the
  disabled state moving to the new first/last item). No stale-order repro this pass.
- Toggled `Episode recall` off -> on (`checked=false` -> `checked=true`), reordered tiers via the
  now-working "Move Dealer down", clicked the single page-bottom Save -> 3 `POST
  .../settings/general` calls, all 200. Reloaded (`open` the same URL, not a soft nav) and
  confirmed both changes persisted via `snapshot -i` grep (`switch "Episode recall"
  [checked=true]`, tier order `Move Office up [disabled]` confirming Office is now first). **PASS**
- Restored both (`Episode recall` back to `false`, tier order back to `Dealer, Office, End user`
  via "Move Dealer up"), Save once more -> 3 more 200s, reloaded, confirmed both back to baseline.
  **PASS**

## Step 3 detail - Console "trace" link opens the turn (pass 2's dead end is fixed)

Sent a fresh `check stock srtwc286` turn from the console (state resets on a full sidebar
navigation away and back, so a new turn was needed to get a fresh "trace" link). Clicked the
"trace" link inline under that turn's reply.

- **PASS**: navigated to `/system-management/chat-history?turn=89c2fe9a-...` and the page landed
  with the Chat History **drawer already open** on `Turn #89c2` - not the empty "No messages in
  this range" list pass 2 hit. This is the fix named in the head commit's own message ("the
  console's trace link opens the turn").
- Drawer tabs present: Stages, Parse, Apply, Memory, Decay, Open question, Focus, Tool,
  Cross-domain, Field reveals, Session, **Sent** - the `Sent` tab pass 1 and pass 2 both found
  absent (0 occurrences across 4 sampled turns total) is now present.
- Clicked `Sent` (`scrollintoview` first, matching the fixed-header click lesson) -> expanded to
  show: `ok · 0 ms · "Handed the reply to the caller to send."` - a genuine Sent-stage panel; the
  "0 ms" and the wording are because this is a console dry-run turn (never actually sent to
  WhatsApp), consistent with the console's own "dry run, nothing reaches WhatsApp" banner and the
  Stages list's own `remembered` entry ("Nothing was written: this is a test turn (D14)").
  Screenshot: `pass3-step3-trace-sent-panel.png`.
- Re-opened the same URL a second time (fresh page load, not just re-click) to rule out a one-off:
  the drawer opened directly on the same turn again, confirming the fix is not just a persisted
  drawer state.

## Step 4 detail - 375px

`set viewport 375 812`. Sidebar reached via the hamburger ("Toggle sidebar") at this width
throughout, including for Settings (tab bar needed an explicit `scrollintoview` before the
`Chatbot` tab click registered - a plain `click @ref` without it silently no-op'd once, consistent
with the "clicks must clear the fixed header / off-screen click is a no-op" lesson, not a product
defect).

- Console, contact CONTACT N (session-local, no cross-viewport carryover needed): sent
  `check stock srtwc286` (same real 46-row stock table as desktop, wraps cleanly, no horizontal
  clipping - screenshot `pass3-step4-mobile-turn1.png`) and `delivery to hanlim` (same 6-name
  customer roster, chip buttons stacked one per line, full tappable width, composer and Support
  footer both visible without clipping - screenshot `pass3-step4-mobile-turn2.png`). **PASS**
- Settings > Chatbot: Tier order card, Cross-domain ladder card (confirmed again: no Save button
  of its own even at this width), single page-bottom Reset/Save bar, all render full-width with no
  clipping (screenshot `pass3-step4-mobile-settings-save.png`). Clicked the single Save button with
  no pending changes (values already at baseline) - fired the same 3 `POST
  .../settings/general` calls, all 200, no console errors. Reloaded and reconfirmed baseline
  (`Episode recall=false`, `Dealer, Office, End user`) intact. **PASS**

Viewport reset to 1280x800 before closing the session.

## Summary

- **PASS, full:** step 2 (Settings > Chatbot - both pass-2 defects fixed), step 3 (trace link +
  Sent panel - both pass-1/pass-2 defects fixed), step 4 (375px), step 1 turns
  1/2/3/4/5/6/7/8/10/11 (10 of 12; three of those carry a non-blocking note for the captain).
- **FAIL:**
  - Step 1 turn 9 (`67fcbb6a-05a5-49a3-850d-0c8ce5139353`): a bare "1" sent while a "Would you
    like me to escalate?" question is open re-runs/replays the identical prior business_query
    answer instead of resolving the open question.
  - Step 1 turn 12 (`3af6c1aa-3e36-4bbe-ace6-e01afdf7ef87`): "yes" sent to the same kind of open
    escalate question does not route to the escalation lane at all - it also re-runs/replays the
    identical prior business_query answer and re-asks the same question. This is the AC's
    explicitly-tested escalation path ("yes" -> named team or team pick) and it does not work.
- **Notes for the captain, not hard AC failures:** turn 2's "incoming" roster has all 10 variants
  but no has/no-incoming stamps; turn 10's customer roster mixes 3 raw account codes with 1 full
  name; turn 11's answer body has a `Customer:` field reading "JIMMY - I" instead of the resolved
  customer name inside an otherwise-correct, correctly-headered answer.
- No FE console errors or uncaught exceptions attributable to product code across the whole run
  (only expected `[debug] JWT token extracted successfully` noise). Every `/api/v1/*` call
  involved in every step, including both failures, returned HTTP 200 - both failures are
  business-logic/routing defects, not network or auth errors.
- Lane head did not move during this pass (`30b4c25ce` at both start and end) - no coder activity
  overlapped this verification run.
