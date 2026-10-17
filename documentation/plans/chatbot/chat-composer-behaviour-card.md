# CHAT-COMPOSER behaviour card (v1, 17 Oct)

Chatbot step 3: one composer per answer type. Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`.
Paths: B = `sorento_crm_backend/app/services/chatbot`, P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`,
F = `B/lanes/business/fetch.py`, A = `B/lanes/business/answer.py`, C = `B/turn/compose.py`. Line numbers on main 20c191d86.
Examples are real TRAIN replies (`crew/state/eval/cp0/train_followups`, item ids given; no held-out item used).

## 1. What we found (discovery)

A reply is written in five layers, not one: MCP presenter builds an envelope (`present_response` P:1899) -> lane prints
it (`output_structurer` F:2933, row printer `_item_line` F:3382) -> `compose` prints the lane text verbatim and adds
miss / offer lines (C:506) -> engine post-passes rewrite dealer text (`_stock_ask_reply` engine.py:7545,
`_dealer_names_the_miss` engine.py:7631) -> final ms/zh pass (`Localizer.reply`, engine.py:3070).

| Answer type | Traffic (design s.1) | Distinct formatters today | Worst divergence (file:line) |
|---|---|---|---|
| Stock (single code) | 43.5% | ~9 | same fact as "*Quantity On Hand:*" (P:1362), "*Total:*" + location codes (P:1451), "Stock: N" (B/turn/sub_asks.py:750), unbolded "Stock:" (P:1628); dealer miss in 3 sentences (engine.py:7640, C `_did_you_mean_per_code`, B/dealer_stock.py:116) |
| ETA / incoming | 21% | ~8 | "❌ No stock, ETA d" (P:1586) vs "{code}: ✅ ETA d1, d2" (P:987) vs "Incoming: qty, ETA d" (sub_asks.py:774); raw ISO dates reach the reply (TRAIN cp0-0654e3e74a "*ETA:* 2026-09-27") |
| Price | (in product info 4.4%) | 3-4 | no price formatter: "List Price" field of the product card (P:1106) with `_money` "MYR 12.00", no thousands separator (P:168), vs "Last cost: MYR 1,234.00 / unit" (sub_asks.py:735) |
| Product info / spec | 4.4% | ~5 | missing value printed as "*Label:* not recorded for A, B (+N more)" (F:~2186), as "Not defined" (P:1106) or as an em dash (F `_fmt_value`); spec-set header adds brand / size count lines (`breakdown_lines` A:2795) |
| Files | 7.3% | ~6 | "attached" sentence written in 5 places; missing file as "{code} has no {types}." (C:703) vs "But no Product Photos matched these." (A:4168-4288) |
| Promotions | 2.3% | ~6 | "EXPIRED" (F:3423) vs "PROMO EXPIRED" (A:1028); a re-sorted promo list loses the marker (`_promo_render_blocks` A:1725); raw ISO dates (TRAIN cp0-b90acd1184 "*Start Date:* 2026-09-25") |
| Order / document status | 7.9% | 8+ header writers | customer header group-folded in 5 writers but raw ledger name in 3 (B/lanes/business/__init__.py:346, sales report header P:2927, order-miss lines A:3423/4324); A:3862-3885 is a line copy of `tail/scope_block.search_scope_header` |
| Not found / clarify / hand-over | 5.3% + 5.0% + 2.4% | 9 miss grammars, 19 picker wordings, 12+ offer sites | "Could not find" / "Couldn't find" / "Couldn't find:" / "I could not find" / "No X found for" / "No X for" side by side (A:4166-4400, C:612-682, answer_bridge.py:1622-1663); 5 "there is more" trailers; `task.pick_question` (B/turn/task.py:865) lists every option uncapped |

Two couplings any rewrite must handle: (1) translation is an exact-English allow-list (`label_catalog.py` LABELS :33,
INLINE, `Localizer.tail`); a reworded catalogued sentence silently falls back to English. (2) Code downstream re-reads
the composed English: `dealer_stock._ESCALATION` (dealer_stock.py:30), `order_list._OFFER_SENTENCE`,
`fetch._outstanding_offer_from_text` (F:2212-2325), `tail/compose.MARKERS`, `reply_ladder._ESCALATE_TO_TEAM_RE`, the
refer strip `.replace(" Please refer to your salesman.", "")` (P:1579), the set-header prefix strip
(turn_runtime.py:5503).

## 2. The composer contract

- One module per answer type under `B/compose/`: `stock.py`, `incoming.py`, `price.py`, `product_info.py`,
  `files.py`, `promotions.py`, `status.py`, `misses.py` (not found, clarify question, hand-over offer), plus
  `sets.py` (a list or set of products, any type) and `format.py` (the shared line rules below).
- Input = resolved facts, never text: a small typed record per item (code, name, company, the asked facts with their
  values, flags such as discontinued / expired / pending allocation), the audience view (staff numbers, staff compact,
  dealer availability), the misses (what was asked, what was not found, real-name candidates) and the set info (what
  was asked, total, page). Facts are read from the envelope's field KEYS (P `_Builder` fields carry `key`), not from
  labels or titles; where a presenter only emits a sentence today (dealer availability P:1545, dealer ETA P:987),
  the presenter adds the facts beside it (additive keys; the old sentence stays for the gate-off path).
- Output = `Reply{text, question, offer}`: the English text plus structured flags for the open question and the
  offer, so downstream code reads flags instead of re-reading the English (removes coupling 2 on the composer path).
- Translation stays the final pass. A composer keeps every catalogued sentence verbatim; a changed or new sentence
  gets its ms/zh catalogue entry in the same slice (pattern of CHAT-FIT-LINE), so ms/zh never fall back to English.
- No word rules, no fuzzy matching: the composer formats facts it is handed; it never reads the customer's message.

## 3. Rules every composer obeys (owner rulings, crew memory)

| Rule | Owner source | Applies to |
|---|---|---|
| R1 One fact per line, `*Label:* value`; no " · " | feedback-no-dot-separators (3 Oct) | all; " · " exists today only in an orders summary title (P:788) |
| R2 A set reuses the single-code block, one block per product; no brand / size count lines | design note 16 Oct + SET-STOCK-RENDER rulings (#1744) | sets.py for stock, ETA, price, spec, files |
| R3 Customer header: group / company name only, no account count, no "and N more" | feedback-customer-group-name-only (2 Oct) | status.py, every customer header |
| R4 Truthful: only data held; an asked attribute with no value says "No <attr> on record for <product>." | feedback-truthful-no-fabricated-replies (12 Oct); CP0 owner review 17 Oct | product_info, price, files, promotions |
| R5 No ids, UUIDs or internal codes as options; real names (a product code is the product's name) | feedback-chatbot-never-leak-ids (13 Oct) | misses.py; TRAIN cp0-75e516bd40 leaks the table name "master_products" |
| R6 Availability = on hand; ETA = incoming stock, product level | feedback-availability-onhand-basis (6 Oct); CHAT-STATE ruling 16 Oct | stock.py, incoming.py (wording unchanged) |
| R7 Expired flyers still sent, marked EXPIRED, beside the live one | feedback-promo-expired-shown (15 Oct) | promotions.py |
| R8 Fit line "Seat cover/Mirror for X is Y." first, then the usual answer | feedback-fit-line-wording (15 Oct) | product_info.py (wording unchanged; owner `find.fit_lines` B/lanes/business/find.py:405) |
| R9 A set reply always shows products: capped list + a "reply to see more" line | CP0 owner review 17 Oct | sets.py, misses.py |
| R10 Price-tier (access level) question first only once a spec-matching product is found | CP0 owner review 17 Oct | misses.py question order |
| R11 No holding / progress messages | design D3 (16 Oct) | all composers emit none. The two in code are report paths, out of scope (P:2521 "Preparing the low stock report ...", P:3272 "The Excel follows here."); "Please wait for a moment" (TRAIN cp0-a873ac5398) is not in the repo (registry copy or n8n) |
| R12 No needless question left open after a full exact answer. EXCEPT the numbered sibling-variant picker after a full answer to an exact code, which STAYS (owner ruling 17 Oct, `answer.py:4846-4848`); composers keep it | EVAL-BENCH TRAIN cp0-c7ec5f1c31; owner ruling 17 Oct | misses.py offer rule |
| R13 Dates dd/mm/yyyy, money `MYR 1,234.00`, empty un-asked fields omitted | lane (one format; today mixed, F:1647 vs A:854 vs P:168) | format.py |

## 4. Per type: what stays, what changes when the gate is on

Format baseline = today's main-path layout ("N. *Label:* value" blocks, footer "_Data last updated: ..._"), which the
owner called good on 15 Oct (project-chat-advisor-spike). Composers move the stray paths onto it; they do not invent a
new layout.

**Stock.** Stays: dealer lines `CODE x Q: ✅ Got stock.` / `✅ Only N got stock.` / `❌ No stock, ETA d.` /
`❌ No stock and no incoming. Please refer to your salesman.` (TRAIN cp0-7b70831ab3, cp0-2ec090127f, cp0-b5eb30e1b9);
staff compact and detailed views (picked by the contact's stock mode; TRAIN cp0-2e2ebf11cb, cp0-37456a72da).
Changes: (a) one miss wording for a dealer stock miss instead of three (TRAIN cp0-0498229323 "Couldn't find: cb890-bl,
cb890-GM- diy."); (b) WITHDRAWN 17 Oct (v1 error): the "CODE - " fill-in line (TRAIN cp0-29c26e8809)
is an owner ruling (round 6, ruling 2, `turn/task.py:394-397`) and stays; only the bare "How many units do you need?"
(P:121) vs the named "How many units of X?" (T:395) remains, moved to S3; (c) the stock block shown under an ETA miss uses the
same numbered block, not the "- *Product Code:*" variant (TRAIN cp0-5430419641, cp0-338d26fba4).

**ETA / incoming.** Stays: dealer `CODE: ✅ ETA 23/10/2026` + refer line (TRAIN cp0-dad9d8c2de); staff per-shipment
blocks. Changes: dates always dd/mm/yyyy (TRAIN cp0-0654e3e74a prints "2026-09-27"); one label "Container"; the ETA
miss says it once: today "But no incoming matched these.\nNo incoming for SRT5734-WH." says it twice
(TRAIN cp0-5430419641).

**Price.** Changes (amended 17 Oct, S5/S6 design OI-4: a filled Dimensions stays per owner ruling D12): a price
answer leaves out EMPTY un-asked fields (today
"*Dimensions:* Not defined", TRAIN cp0-245d689a0c); a missing price says "No list price on record for X."; money with
thousands separator everywhere.

**Product info / spec.** Stays: the fit line (R8). Changes: an asked attribute with no value is one plain line per
product, "No weight on record for SRTWC286-SH." (today "*Weight:* not recorded for A, B (+N more)"); a spec set uses
the single-code block with no count lines (R2). TRAIN has 0 spec items, so examples come from crew (section 8).

**Files.** Stays: "I have attached the file(s) below." + per-file block (TRAIN cp0-d1977a0368). Changes: the attached
sentence and the de-duplication in one place (today 5 writers, 3 dedupe keys); one missing-file sentence instead of
two ("{code} has no {types}." C:703 on a hit, "But no Product Photos matched these." A:4168-4288 on a miss, TRAIN
cp0-7b068ed4da). The "Here's what you want:" echo of what was understood stays (shared miss shape).

**Promotions.** Stays: expired flyers sent and marked (R7). Changes: one marker "EXPIRED" on every path (a re-sorted
list keeps it); dates dd/mm/yyyy (TRAIN cp0-b90acd1184).

**Order / document status.** Stays: header block `Customer:` / `Product:` / `Dates:` then order blocks (TRAIN
cp0-b2f537d9b6, cp0-3a72025f8c). Changes: every customer HEADER uses the group name (3 raw-name writers fixed); the
two period replies (`so_status.period_reply`, `do_ask.range_reply`) become one.

**Not found / clarify / hand-over.** Stays: did-you-mean with real names and the stock hint (TRAIN cp0-aad2aaec55,
cp0-1e8ccf892e); the access-level menu (TRAIN cp0-ec1b15795d). Changes: one miss grammar (`Couldn't find X.`) instead
of nine; one "there is more" line instead of five trailers; pickers stay uncapped (owner 2 Oct, PICKER-NO-CAP #1436)
but every option carries its name; no internal names (cp0-75e516bd40 "no master_products matched these"); a set miss that found products shows
them (R9; cp0-75e516bd40, cp0-665ba3e897); a question tacked onto a full answer is dropped (R12; TRAIN cp0-b1f73c72c0 "How many units
do you need?" under a complete promo answer, cp0-958b275668). The numbered sibling-variant picker after a full answer
to an exact code stays (owner ruling 17 Oct, `answer.py:4846-4848`; so TRAIN cp0-4430299976 is not a defect).

## 5. Edge cases

- Several codes in one ask, mixed hit / miss: hits as blocks, then one miss line, then at most one question.
- Two companies carry the same code: one block per company (TRAIN cp0-7ff78f4770), company line first.
- Discontinued: flag line kept under the block; alternatives keep their line.
- Access: a fact the contact may not see is left out, and only an asked one gets "I can't share the X." (today F:3043-3140).
- Multi-measure ask on one product ("stock and incoming and last cost", sub_asks.py): one block, one line per measure.
- Language ms/zh: every sentence a composer prints is in the catalogue (section 2).
- Reports (outstanding, sales, low stock, top selling): not in this lane (0 TRAIN items, six own renderers F:2941-2958);
  trigger to take them on: a report failure on the bench or an owner ask.

## 6. Gate, proof, slices

- Gate: `chat_composer_enabled: bool = False` in `app/config.py` Settings (env `CHAT_COMPOSER_ENABLED`), the pattern of
  `chat_topic_labeller_enabled` (config.py:190). Off = today's code path, byte-identical to main.
- Proof per slice: test(red) commit first; L1 replay gate-off vs main = 0 unintended diffs (baseline today: grounding
  0/1506, turn 0/585); gate-on replay over the same 585 recorded turns lists every changed reply (the intended set,
  reviewed type by type); kill-proof.
- Byte-identical duplicates (e.g. A:3862-3885 = `search_scope_header`; "No stock for X." built in A:1066 and
  answer_bridge.py:1663) are folded into the composer at once with no gate (replay 0 diffs).
- Slices, by traffic: S0 scaffolding (gate, `format.py`, contract tests) -> S1 stock -> S2 ETA -> S3 not found /
  clarify / hand-over -> S4 files + promotions -> S5 order status -> S6 product info + price -> sets (after Q1).
- Bench: TRAIN only; a live envelope from crew per PR run (cap about $5).

## 7. Overlap with open lanes (for merge order)

`engine.py` is touched by #1744, #1747 and #1755. `turn/compose.py` by #1747; `turn/apply.py` by #1747 and #1755;
`lanes/business/{answer,fetch}.py` by #1744; `resolve_gate.py`, `turn_runtime.py` by #1744 and #1755. This lane edits
composition only (C, F printing, A miss / header writers, P additive keys) and does not rewrite their files' logic;
every hunk in a shared file is listed in a crew-note on the PR.

## 8. Needs from crew

- TRAIN items for the code, spec and attachment slices (like `train_followups`): TRAIN follow-ups hold 0 spec, 0 fit
  line, 7 file items, so spec and files cannot be measured on TRAIN today.
- One live TRAIN envelope per PR run (about $5 cap).

## 9. Questions (at most 5)

**Crew answers 17 Oct (ask a17504629): Q1 (a), Q2 (a), Q3 (a), Q4 (a).** Q3/Q4 go to the owner as applied rulings.
TRAIN export for code / spec / attachment slices: crew asked EVAL-BENCH; path follows from crew.

- Q1 SET-STOCK-RENDER #1744 (parked, review-clean, ungated) rewrites the set stock reply in A/F, which our set slice
  replaces. (a) #1744 merges first when its hand-test hold clears; our set slice builds on it after S1-S3.
  (b) Close #1744 and fold its rules and tests into our set slice behind the gate. Recommend (a): half of #1744 is
  fetch behaviour, not wording (first 50 rows instead of withholding, zero-stock rows hidden, two-company rows in
  `inventory_service` / `product_predicate_service`), and it is already reviewed.
- Q2 Gate and deletion. (a) One env gate for the lane, default off; byte-identical duplicates deleted now; the old
  divergent formatters deleted in a follow-up flip PR (gate removed) after the bench and the owner hand test pass.
  (b) This PR ends with the flip (gate removed, old formatters deleted). Recommend (a): the owner hand-test bar
  (feedback-chatbot-handtest-gate-robust) may hold the flip for days, and a gated merge keeps the overlap with
  #1744 / #1747 / #1755 small.
- Q3 Empty fields (owner-visible). (a) An un-asked empty field is left out (no "Dimensions: Not defined"); an asked one
  says "No <attr> on record for <product>." (b) Keep "Not defined" lines. Recommend (a): R4 and less to read.
- Q4 "There is more" line (owner-visible). Today five trailers: "... and N more <noun>s" (P:2280), "and N more",
  "and N more lines" (B/ref_status.py:62), "Showing X of Y." (P:1652), "and N others, reply with the full code."
  (B/turn/task.py:891). (a) One line on every capped list: `Reply "more" to see the next N.` (set lists capped at 50 per
  #1744 Q1); pickers stay uncapped (#1436). (b) Keep each path's own trailer. Recommend (a): R9 asks for exactly this.
