# Browser verification pass 2, 16 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone of the prod copy. Lane
head at pass start: `c3bfd6d6c`; the coder was actively committing during this run (a
`[Fast Refresh] rebuilding` fired mid-turn at step 1's turn 8) and the worktree head had
advanced to `bbc86cdd4` ("the cross domain ladder climbs again, inside the turn package") by
the time this file was written. Session: agent-browser `--session rearch-browser-2`, headless,
logged in via `E2E_EMAIL`/`E2E_PASSWORD` from `sorento_crm_frontend/.env.local`. Closed cleanly
at the end (`close`, not `close --all`).

Navigation: sidebar clicks from `/` throughout, except two same-origin `location.href`/`.click()`
jumps used to reach the chat-history drawer via the console's own "trace" link (documented inline
below) after the sidebar-driven nav to Chat History was already confirmed working. No OpenAI
429s hit during this run; 8s spacing held between every console turn.

**Radix trap note (per pass 1 finding 1a) confirmed again in a new shape:** driving a Radix
`Tab`/`Select` via `eval(...).click()` intermittently left `document.body.style.pointerEvents =
"none"` and the click silently no-op'd (Tier-order tab switch, "Move X down" reorder buttons).
Diagnosed via `document.body.style.pointerEvents` per the lesson; `press Escape` cleared it (once
it closed the whole dialog instead of just a stray popover - reopened and retried). Switching to
the CLI `click @ref` off a fresh `snapshot -i` (not `eval(...).click()`) fixed the Narrowing-tab
switch immediately. Flagging as an agent-browser/CDP timing quirk for the next verifier on this
lane, same class as pass 1's finding 1a, not a product defect.

| # | Step | Result | Evidence |
|---|------|--------|----------|
| 1 | Console, contact CONTACT N: 9-turn chain (check stock / incoming / incoming srtwc286 / "1" / photo srtwc286 / "2" / delivery to hanlim / "1" / hello-while-open) | **3 of 9 turns FAIL** | See breakdown below. |
| 2 | Chatbot Domains: Add/Edit/Delete visible; product_attachment 4 tabs; Narrowing Product = "Must narrow to one"; Prompt block server-rendered `<pre>` block, no client-preview text, no explanation sentences; zz-test throwaway delete (countdown+Cancel, then lapse) | **PASS** | See below. |
| 3 | Chat History: open newest console turn's drawer, confirm Sent panel with send_message action | **FAIL** (2nd sample, matches pass 1) | See below. |
| 4 | Settings > Chatbot: exactly one Save button; tier order move-one-down; toggle a switch; Save once; reload; both persisted; restored | **PARTIAL FAIL** (2 Save buttons, not 1; "Move X down" buttons broken) + **PASS** (persistence itself, once achieved via "Move X up" as a workaround) | See below. |
| 5 | Contact CONTACT N > Access > Chatbot card: Stock checks + Recall toggle persistence | **PASS** | See below. |
| 6 | 375px: repeat 2 console turns + Domains modal | **PASS** | See below. |

## Step 1 detail - Console, contact CONTACT N (9-turn chain)

Contact combobox is a searchable combobox (types + click, not a Radix `Select` - no trap here).
Set to "CONTACT N" once at the top of the run; contact selection persisted across turns and
survived one in-app re-navigation back to the console.

| Turn | Sent | Branch chip | Result | Notes / trace turn id |
|---|---|---|---|---|
| 1 | `check stock srtwc286` | business_query v23 | **PASS** | Real stock table, 46 rows across the 10 SRTWC286 variants. `b54e4ff8-74a4-45e8-946e-4c89b8f1a4c7` |
| 2 | `incoming` (no product) | business_query v23 | **PASS, with an odd shape** | Not "No matching results" (the AC's hard failure bar) but also not a direct incoming answer or the 10-variant roster: replied "Which product do you mean? 1. srtwc286" - a single-option roster naming the family root code, not a variant. Worth the captain's eye but does not violate the literal AC. `53aba0fe-ee11-42d5-99a1-a099cca16cb7` |
| 3 | `incoming srtwc286` | business_query v23 | **PASS** | Exact 10-variant roster with has/no incoming stamps, e.g. "8. SRTWC286-SH-NEW-P - has incoming". `2b921315-1774-44fe-b189-2d5abd385f15` |
| 4 | `1` (reply to turn 3's roster) | business_query v23 | **FAIL** | `*incoming stock* for 65514803-1609-4fe8-8b60-2e908c8f9bd4:` - a raw UUID in place of the product code, followed by "No matching results found. Would you like me to escalate?" Expected the incoming answer for SRTWC286-SH-200 (turn 3 stamped it "no incoming", so an honest empty would be acceptable, but not phrased as a broken lookup against a UUID). Not access_denied, not the roster again, but a third failure mode the AC didn't anticipate. All API calls returned 200 (not a server error - a business-logic bug in the numbered-pick-to-lookup path). Screenshot: `pass2-step1-turn4-fail-uuid.png`. Trace `c196ebd7-bdaf-4bd4-80c8-09755542f00a` |
| 5 | `photo srtwc286` | business_query v23 | **PASS** | Roster "Which product do you mean?" listing all 10 variants, not "No matching results". `67ada660-bde7-4a08-9313-6197a803141e` |
| 6 | `2` (reply to turn 5's roster) | business_query v23 | **PASS, with a cosmetic UUID leak** | Correctly resolved to SRTWC286-SH-P and returned 4 real attachments (spec sheet + 3 certs, one expired). Header line still reads `*product attachments* for d73a33f3-956b-401f-90c7-0ad6ff792ea1, photo:` - same raw-UUID-in-header defect as turn 4, but here the underlying data lookup succeeded, so it is cosmetic rather than functional. `fed0ecba-a42e-4401-ac58-305aac9b1f6f` |
| 7 | `delivery to hanlim` | business_query v23 | **PASS** | Customer roster with 6 real names, e.g. "HANLIM TRADING SDN BHD [A/C II]" - never a code like 300-H070. `2ba9f8c6-4882-453f-9cf6-4d3580321c13` |
| 8 | `1` (reply to turn 7's roster) | business_query v23 | **FAIL** | Same shape as turn 4: `*orders* for 6f5a419b-840a-4e4d-8107-291971dd3bb8:` (raw UUID) then "No matching results found. Would you like me to escalate?" Confirms the numbered-pick-to-lookup defect is general (hit stock/incoming AND orders, both product-pick and customer-pick), not isolated to one intent. Screenshot: `pass2-step1-turn8-fail-uuid.png`. Trace `08c88e3f-c9de-479f-9ab7-d2b8ee1cf30d` |
| 9 | `hello` (escalate? question still open from turn 8) | business_query v23 | **FAIL** | Reply was byte-for-byte identical to turn 8's answer (`*orders* for 6f5a419b-...: No matching results found. Would you like me to escalate?`) - "hello" was not routed to casual small talk at all; the system silently replayed the prior business_query answer instead. This is a distinct regression from turns 4/8's UUID bug: greeting text during an open question does not get its own casual reply, and there is no way to tell from the UI whether the open question ("Would you like me to escalate?") is even still answerable, since the bot never acknowledged the greeting. Screenshot: `pass2-step1-turn9-fail-hello.png`. Trace `f8fe14a4-c2da-4d20-8ef7-104578ad21c3` |

Summary for step 1: turns 1/2/3/5/6/7 pass (6 of 9); turns 4, 8, 9 fail (3 of 9). The three
failures share one root shape - a numbered reply ("1"/"2") to a roster, when it needs to re-run a
business lookup (incoming stock, orders) rather than a static list (attachments worked), surfaces
a raw internal UUID instead of the resolved code/name and can return "No matching results" even
though the roster it answered came from real data; and a plain "hello" sent while that kind of
answer left a question open gets no casual handling, just a verbatim repeat of the last business
answer.

## Step 2 detail - Chatbot Domains

- Admin (E2E user) sees Add domain / row-click-to-edit / Delete - confirmed.
- `product_attachment` row: General, Narrowing, Ladder, Prompt block tabs all present. Narrowing
  tab's Product row reads "Must narrow to one" (the migration-seeded value, unchanged - this pass
  did not edit it, only read it, per the brief's "Narrowing tab shows Product = Must narrow to
  one (seeded by migration)").
- Prompt block tab: `document.querySelectorAll('[role=tabpanel]')` shows the visible panel is a
  `<p>` label + a `<pre>` with server-rendered text: "Domain product_attachment ("product
  attachments"): intents check_product_attachment. Switch words: (none). attachment_type narrows
  narrow_by_type; product narrows must_narrow_one. Escalates to marketing_product." - no client
  preview language, no on-screen explanation sentence.
- No explanation sentences found under any tab heading.
- Created a throwaway `zz-test` domain, opened it, clicked Delete: a toast appeared
  ("Deleting in Ns... Cancel"), never a `confirm()`/AlertDialog. First attempt: the multi-command
  CLI round trip (click, wait, snapshot, grep) cost enough wall-clock time that the countdown
  (read at "3s" by the time it was checked) lapsed before Cancel could be clicked - `zz-test` was
  hard-deleted as a side effect. Not a product defect, an agent-browser-latency lesson worth
  carrying forward: **drive Delete-then-Cancel as a single `eval` batch**, not separate CLI
  round trips, or the countdown can lapse underneath you.
- Created a second throwaway `zz-test2`, this time clicked Delete then immediately clicked Cancel
  inside one `eval` call (`document.querySelectorAll('button')` find-by-text) - confirmed the
  "Deleting in..." toast disappeared and the row was still present. **PASS**: Cancel works when
  driven fast enough.
- Reopened `zz-test2`, clicked Delete, read the countdown immediately ("Deleting in 5s" - a
  believable duration; my earlier "3s" read on `zz-test` was very likely the same 5-10s window
  read a few seconds late by slow CLI round trips, not a genuinely short countdown), then let it
  lapse deliberately (`sleep 7`, confirmed the toast text was gone). Reloaded: `zz-test2` gone.
  **PASS** for the full delete lifecycle (countdown, Cancel, lapse-then-committed).

## Step 3 detail - Chat History Sent panel

The console's own "trace" link navigates to `/system-management/chat-history?turn=<id>`, but
that full navigation does not auto-open a drawer or auto-widen the date filter for the target
turn - landing on it shows the page's generic "No messages in this range" empty state regardless
of how recent the turn is (tested with a turn sent seconds earlier). Widening the date filter to
cover all of today did not surface the turn by its own message text either. This matches the
Chat History page's own copy: "chat history is written by the n8n WhatsApp flow" - console
dry-run turns are apparently never written to the table this list/drawer reads, so **the "trace"
link is effectively a dead end for a console-originated turn**: it takes you to a page that can
never show that turn.

To still verify whether a "Sent" panel exists at all, I widened the date range to Sept 1-17 on
real (n8n-sourced) WhatsApp traffic for CONTACT N, opened the newest real row's drawer, and expanded
a turn's "details" (`#0504`, a `business_query` turn). Counted occurrences of each documented
trace-panel label in the drawer's rendered text:

```
Received: 2   Understood: 4   Access: 4   Routed: 3   Looked up: 2   Replied: 2   Remembered: 2   Sent: 0
```

**FAIL, and now confirmed on a second independent sample** (pass 1 sampled 3 different turns,
same zero count): no "Sent" panel with a send_message action renders in the turn trace drawer, at
all, for any turn tried across two passes. Whether this is a genuine gap (the panel was meant to
render and doesn't) or the plan's naming for a panel that renders under a different label needs
the captain/plan author's call rather than another browser sample - two passes have now looked and
found the same zero. Screenshot: `pass2-step3-no-sent-panel.png`.

## Step 4 detail - Settings > Chatbot

Structure changed since pass 1 (the coder moved "Domains the bot does not answer" out to each
domain's own `Supported` switch, per an on-page note: "Moved to each domain's own row - Chatbot
Domains. Turn the Supported switch off there instead of listing the name here" - that migration
looks intentional and is not itself a finding).

- **FAIL against "exactly one Save button on the page":** there are still 2 - the Cross-domain
  ladder card kept a small dedicated Save button of its own (confirmed via network capture: it
  fires its own `POST .../settings/general` with only `{"chatbot_tier_order_or_ladder..."}` shape
  payload separately from the page bottom), and the page-bottom `Reset`/`Save` bar saves
  everything else. Screenshot: `pass2-step4-move-down-broken.png` (also shows the ladder's own
  Save button in the same shot).
- **FAIL: Tier order "Move X down" buttons are broken; "Move X up" works.** Verified with direct
  same-eval-call clicks (click + immediate readback in one `Runtime.evaluate`, ruling out a CDP
  timing artifact): clicking "Move Dealer down" and separately "Move Office down" left the
  `1.Dealer 2.Office 3.End user` order completely unchanged both times. "Move Office up" DID work
  and produced the intended reordering (`Office, Dealer, End user`). The net change I wanted
  ("move one down") was only achievable by using the up-arrow on the item below it instead - a
  real usability defect on the down-arrows specifically.
- **PASS for persistence, once achieved via the up-arrow workaround:** toggled `Ordering` off,
  reordered tiers to `Office, Dealer, End user` via "Move Office up", clicked the page-bottom
  Save - this fired 3 separate `POST /api/v1/user-management/settings/general` calls in one
  click (switches payload, memory payload, tier-order payload `{"chatbot_tier_order":
  ["office","dealer","end_user"]}` - all 200). Reloaded (landed straight on
  `/user-management/settings/chatbot`, tab preserved in the URL) and confirmed both changes via a
  DOM-order query (not `innerText`, which gave a stale/wrong read once mid-run and cost time
  chasing a false "tier order didn't persist" lead - the reliable check is walking each "Move X
  up" button's containing `<li>` in DOM order). Restored `Ordering` back on (this raised the
  documented "Let the CRM finish every turn? / Turn it on" confirm dialog, same as pass 1 -
  accepted it) and tier order back to `Dealer, Office, End user` via "Move Dealer up". Reloaded
  once more: both confirmed back to their original values.

## Step 5 detail - Contact CONTACT N > Access > Chatbot card

Reached via Users & Access > People > Internal Users > search "CONTACT N" > same contact id as pass
1 (`aefb2cb0-abec-490e-b74b-bf2f2d4a5b37`) > Access tab. Switch ids: `contact-chatbot-stock`
(baseline `true`), `contact-chatbot-recall` (baseline `false`).

- Toggled Stock checks off -> `PUT .../contacts/{id}/chatbot` 200 -> reload -> confirmed
  `aria-checked=false` -> toggled back on -> 200 -> reload -> confirmed `true`. **PASS**
- Toggled Recall on -> 200 -> reload -> confirmed `true` -> toggled back off -> 200 -> reload ->
  confirmed both switches back to baseline (`stock=true`, `recall=false`). **PASS**

## Step 6 detail - 375px

`set viewport 375 812`. Home page, sidebar-via-hamburger, Chatbot Console, and Chatbot Domains
all rendered without horizontal clipping.

- Console: sent `check stock srtwc286` (same real stock table as desktop, readable, branch chip
  + "trace" link visible under the reply) and `delivery to hanlim` (same 6-name customer roster,
  chip buttons stacked one per line, fully tappable width). **PASS**
- Domains modal: opened `product_attachment`, General tab one field per row, footer
  (Delete/Cancel/Save) visible without scrolling. Switching to the Narrowing tab first hit the
  same Radix pointer-events trap noted at the top of this file (`eval(...).click()` on the tab
  left `body.style.pointerEvents = "none"` and silently no-op'd twice, including once closing the
  whole dialog via Escape) - resolved by re-opening the modal and using the CLI `click @ref` off
  a fresh `snapshot -i` instead of `eval`. Narrowing tab then rendered one entity-kind row per
  line, label left / dropdown right, no clipping. **PASS** (mechanism note, not a product defect).
  Screenshot: `pass2-step6-mobile-narrowing.png`.

Viewport reset to 1280x800 before closing the session.

## Summary

- **PASS:** step 2 (Domains CRUD + delete lifecycle), step 5 (contact Access toggles), step 6
  (375px), step 1 turns 1/2/3/5/6/7 (6 of 9), step 4's persistence-once-achieved half.
- **FAIL:**
  - Step 1 turn 4 (`c196ebd7-bdaf-4bd4-80c8-09755542f00a`): numbered reply to an incoming-stock
    roster returns a raw UUID + "No matching results found" instead of the picked variant's
    incoming answer.
  - Step 1 turn 8 (`08c88e3f-c9de-479f-9ab7-d2b8ee1cf30d`): same defect shape on the orders
    intent after a customer-name roster pick.
  - Step 1 turn 9 (`f8fe14a4-c2da-4d20-8ef7-104578ad21c3`): "hello" sent while a question is open
    gets no casual reply at all - the bot verbatim-repeats the previous business answer.
  - Step 3: no "Sent" panel renders in the chat-history turn drawer, confirmed on a second
    independent sample (pass 1: 3 turns, 0 "Sent"; pass 2: 1 more turn, 0 "Sent"). Also: the
    console's own "trace" link is a practical dead end for console-originated turns (lands on an
    empty Chat History page that can never show a dry-run turn).
  - Step 4: 2 Save buttons on the Chatbot settings page (Cross-domain ladder's own + page-bottom),
    not the required 1; Tier order's "Move X down" buttons are non-functional ("Move X up" works
    and is the only usable reorder direction today).
- No FE console errors or uncaught exceptions attributable to product code across the whole run
  (only expected `[debug] JWT token extracted successfully` noise and the coder's own
  `[Fast Refresh]` lines). All `/api/v1/*` calls involved in every failure above returned HTTP
  200 - every failure found is a business-logic/UI defect, not a network or auth error.
