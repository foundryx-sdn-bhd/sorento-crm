# PLAN: availability-mode stock replies + multi-code scenarios (AVAIL-MODE-REPLIES)

Status: Review. Slices 1 to 4 built to the owner-approved catalogue v2 (`documentation/mockups/avail-mode-scenarios/index.html`); reviewer round 1 fixed. Track: full (wording + logic, expected diff over 300 lines with the
scenario suite), no migration, no auth/RBAC change, no new ingest surface.

Owner ask (2 Oct 2026, after a stakeholder demo): emoji got/no stock, category-max qty logic,
compact ETA lines, no "all" in availability-mode pickers, multi-code combined reply +
regression scenario suite, scenario catalogue next to the tests. ONE parser prompt: every
mode difference lives in the answer/renderer code, no prompt change (lane PARSER-PER-AUDIENCE,
PR #1429, is not touched).

## Where the code is today (measured on main 7eb9767a)

Paths under `sorento_crm_backend/app/services/` unless marked `mcp:` (`sorento_crm_mcp/sorento_crm_mcp/`).

- Mode switch: `chatbot/turn_runtime.py:676-692` `_stock_availability_only` sets
  `Profile.stock_availability_only` (`chatbot/turn/state.py:191`) when the contact's policy is
  `mode == "availability"`; `eta_policy.py:339-345` `is_dealer` is the same test for incoming.
- Branch decision: `stock_ask_branch.py:24-36` `branch(q, x, available, shipment_date)`:
  `q > x` too_big; `available >= q` in_stock; shipment incoming; else no_incoming.
- Inputs: `inventory_service.py:1532-1731`. `available` = on hand in the policy's locations
  minus open SO (`:1700-1705`); X/Y from `stock_ask_limits.py:47-51` `effective()` (product's
  `chatbot_max_qty` wins, else its OWN category's, else 0; no parent walk).
- Sentences: `mcp:presenters.py:1466-1492` `_AVAILABILITY_TAILS` / `_availability_line`
  ("<code> x <Q>: ..."); dealer ETA ask `mcp:presenters.py:938-950` `_incoming_dealer` prints
  `"<code>\nETA: <dates>"`, parsed back by `chatbot/refer_asks.py:168-176` `_dealer_line` and
  `chatbot/lanes/business/pickers.py:93-99`.
- Category max today: `product_categories.chatbot_max_qty` (X). Above X the reply is
  too_big: "<code> x <Q>: the quantity is more than what I can confirm here. Please refer to
  your salesman." whatever the stock, and the salesman is notified (`stock_ask_service.py:32`).
  Q <= X with 0 < available < Q falls to incoming / no_incoming today, i.e. the dealer is told
  "no stock" although some is there: the demo defect.
- Pickers: family pick `chatbot/turn/task.py:873-920` (`pick_question` `:795-825`, payload
  `stock_pick: True`), did-you-mean `chatbot/dealer_stock.py:63-121`. "All" over a stock pick
  is accepted at `chatbot/turn/decide.py:294-301` (`broaden_all`) and
  `chatbot/turn/apply.py:2856-2889` (`mode: pick` over every position). Neither question
  text offers "all" today.
- Multi-code: one family pick fires only when exactly one family covers every row
  (`chatbot/turn/task.py:883-887`); otherwise every variant becomes a numbered quantity line
  (`:930-935`). When any row still needs a quantity, `engine.py:5556-5602` `_stock_ask_reply`
  replaces the whole reply with the question, so answered lines and the
  "I could not find X." line (`chatbot/turn/compose.py:459-465`) are lost.

## Behaviour card (proposed; owner confirms via crew-ask)

### Rules
1. Emoji, one line per product, line still starts "<code> x <Q>:" (Customer asks keys on it,
   `stock_ask_service.py:104`):
   - in stock, covers Q: `SRT5674 x 50: ✅ Please refer to your salesman.`
   - in stock, short of Q, Q <= X (NEW): `SRT5674 x 50: ✅ 30 available. Please refer to your salesman.`
   - no stock, shipment due: `SRTW2000 x 150: ❌ ETA 19/10/2026.`
   - no stock, nothing incoming: `SRT5674 x 150: ❌ No incoming. Please refer to your salesman.`
   - Q > X (reveals nothing; 🚫 added by v2 note 1): `CWCX604 x 300: 🚫 the quantity is more than what I can confirm here. Please refer to your salesman.`
2. Category max: new rule applies only when Q <= X. "Has stock" = available >= 1 after the
   open-SO subtraction. Stays branch `in_stock` (no new branch value, so no CHECK-constraint
   migration on `stock_asks.branch`); the entry gains `available_qty` only in that case.
   Salesman notification outcome reads "in stock, 30 of 50 available". Above X: unchanged.
3. Dealer ETA ask: one line per product, `SRTW2000: ETA 19/10/2026, 02/11/2026`, or
   `SRTW2000: ETA not confirmed yet`.
4. Availability-mode pickers never offer "all" (they do not today) and never accept it: an
   "all" over a stock pick or a did-you-mean keeps the same list open and replies
   "Please reply with the number of the code you need." "3 for all of them" (a quantity for
   products the dealer already named) is not a pick and keeps working.
5. Multi-code message, one combined reply, in this order:
   1. answered lines (exact codes, and a prefix that matches exactly ONE code, answered as that code), in asked order;
   2. `Couldn't find: FOO99, BAR12.` for codes with no match (no did-you-mean inside a mix; a whole-miss single code keeps today's did-you-mean);
   3. the open question, at most one: every vague token's picker in one message (v2), else the quantity question for codes still owed one.
   Duplicates of one code are one line. Nothing already answered is lost when a question is open.
6. Full mode (staff, compact / detailed) is untouched by every rule above.

### Examples (codes from the repo's hand tests and plan samples; no dev DB is reachable from this sandbox)
- (a) "SRT5674 x 50" (on hand 30, X 100): `SRT5674 x 50: ✅ 30 available. Please refer to your salesman.`
- (b) "SRT5674 x 50, CWCX604 x 300, FOO99 x 1" (CWCX604 X 200): `SRT5674 x 50: ✅ ...` / `CWCX604 x 300: the quantity is more than ...` / `Couldn't find: FOO99.`
- (c) "SRT5674 x 5, SRTWC286 x 10" (SRTWC286 is a family): `SRT5674 x 5: ✅ Please refer to your salesman.` then `SRTWC286 x 10: which one?` `1. SRTWC286-SH` `2. SRTWC286-SH-150`; reply "all" gives `Please reply with the number of the code you need.` and the list stays open.
- (d) "ETA SRTW2000 and MWT5727SS-CR": `SRTW2000: ETA 19/10/2026` / `MWT5727SS-CR: ETA not confirmed yet`.
- (e) "FOO99 and BAR12 got stock?": the shared `Couldn't find: "FOO99" (product), "BAR12" (product).` + refer line (owner kept it, S23).

### Edge cases
- available <= 0 (open SO over on hand): no stock, rules as before.
- Q == available: full in-stock line, no number.
- Q > X and some stock: too_big unchanged (the number is never shown above X).
- X unset (0): every Q is too_big, unchanged.
- Code in two companies: summed before the branch (unchanged, `inventory_service.py:1636-1650`).

## Owner rulings (2 Oct 2026, answers to the crew-ask)

- Q1 (a): a short in-stock line shows only "✅ N available", no ETA for the shortfall.
- Q2 (a): a code named twice adds up into one line ("SRT5674 x 2 ... SRT5674 x 3" is x 5).
- Q3 (a): one picker at a time. SUPERSEDED by the owner's v2 note 3 (2 Oct 2026): Q3 (b),
  every picker in ONE message, the numbering running on from one list to the next
  (SRTWC286 1-10, SRTWC6022 11-12); a list left unanswered is asked again with its numbers.
- Q4 (b): refuse ONLY the explicit "all" signal; a customer picking every number
  ("1,2,3,4,5,6,7") is allowed and answered. This supersedes rule 4's wider proposal.
- Q5 (a): above the category max keep "more than what I can confirm here", no count.
  v2 note 1: that line carries 🚫.
- v2 note 2: quantity + position replies to a picker ("2 of 3", "2 of the third one",
  "2 of 1 and 5 of 3"); the number after "of" is the option.
- Not found, every code (S12, S23): the shared `Couldn't find: "X" (product).` sentence
  stays (both modes). Inside a mix: `Couldn't find: X, Y.`, no did-you-mean (S42).
- One question at a time (reviewer round 1): pickers first; a code owed a quantity (an
  exact code typed without one, or the code just picked) is asked once no list is open.

## Owner hand test, 3 Oct 2026 (copy :3109 at 186f4f9ff)

1. Never dump the catalogue: an unknown code goes to the did-you-mean.
2. A number picked off a did-you-mean resolves to that option (SRT5713 x 10), through the
   same position reading every picker uses. Why it diverged: the 26 Sep rounds 4-5 rulings
   made a bare number under a STOCK pick its quantity (`apply._stock_pick_requantified`,
   and `engine._bare_roster_positions` skipping stock picks), so a "2" the parser read as a
   quantity re-asked the pick, and a "2" it read as a product was resolved against the
   catalogue. Now a bare number on the list is that option for every picker; a number past
   the list is still the quantity.
3. ETA: `CODE: ✅ ETA dd/mm/yyyy`, or `CODE: No ETA`.
4. Exact codes only for availability access, stock and ETA: an exact code never brings its
   family, so a bare "eta" follow-up asks only the previous turn's exact product(s)
   (`engine._exact_codes_only`, right after the resolver). Other access levels unchanged.

## Slices
1. Presenter wording (emoji + partial line + ETA one-liner) and its consumers (`refer_asks`, `pickers.annotate_incoming`).
2. Partial in-stock: `available_qty` on the entry, `stock_ask_branch` truth table, notification phrase.
3. No "all" over availability pickers.
4. Multi-code combined reply (`turn/task.py::after_reply`, `engine._stock_ask_reply`).
5. Scenario suite `sorento_crm_backend/tests/chatbot/test_avail_mode_scenarios.py` (S01-S42) + catalogue `sorento_crm_backend/tests/chatbot/AVAIL-MODE-SCENARIOS.md` + owner page `documentation/mockups/avail-mode-scenarios/index.html`.
