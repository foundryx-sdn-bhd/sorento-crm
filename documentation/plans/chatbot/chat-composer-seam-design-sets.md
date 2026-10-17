# CHAT-COMPOSER seam design: Sets + "more" paging (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md` (slice "Sets"), card `chat-composer-behaviour-card.md` R2, R9,
R12, Q1 and Q4 (section 9). Builds on `chat-composer-seam-design.md` ("Lead decisions 17 Oct" binds), `-s3.md` 7a and
`-s5-s6.md`. Read-only design. Line numbers on HEAD 176d8684fe (`crew/chat-composer-sets` = #1760 + #1747 merged). Open
PR hunks read with `gh pr diff 1744|1755 --repo foundryx-sdn-bhd/sorento-crm` on 17 Oct (heads 3f3bf8101e, f0f94bc385).
TRAIN evidence from `crew/state/eval/cp0/train_followups/` only (items + recorded turns); no held-out file read.
Paths: B = `sorento_crm_backend/app/services/chatbot`, A = `B/lanes/business/answer.py`, F = `B/lanes/business/fetch.py`,
TR = `B/turn_runtime.py`, AP = `B/turn/apply.py`, CS = `B/turn/chat_state.py`, T = `B/turn/tail.py`,
C = `B/turn/compose.py`, E = `B/engine.py`, PA = `B/head/parser.py`, SA = `B/turn/sub_asks.py`, LC = `B/label_catalog.py`,
K = `B/composers/`, P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.

**Binding rules applied:** gate pattern `if composers.enabled(): <new>` else today's code untouched; gate off is
byte-identical to main (here: main AFTER #1744 merges, see section 2); helpers land in the slice that prints them
(`K/sets.py` lands here); no hunk in a function an open PR changes (S3 7a precedent: a hunk far from another PR's hunk
inside a 3,000-line function may land); every new sentence gets ms/zh in `LANE_LABELS` (gate-on only) in the same slice;
no word rule (owner R9: "more" pages via the parser's continuation flag); pickers stay uncapped (#1436); the numbered
sibling-variant picker after a full code answer stays (R12 exception).

## Lead decisions + crew answers 17 Oct (override anything below that disagrees)

- Q1 (c): this slice STACKS on #1744's head 3f3bf8101 (merged into crew/chat-composer-sets). #1744 is ungated and stays
  unmerged until the owner's robust chatbot hand test (owner rule 16 Oct); crew adds #1744 + this slice to the next combined
  build. Consequence: the "waits for #1744" seams (M2, F1) are built now; gate-off identity is proven against the stacked
  base (main + #1760 + #1747 + #1744), not main.
- Q2: a page lists up to 50 products AND never exceeds one WhatsApp message (~4,000 characters including the "more" line):
  the page ends at whichever comes first, and "showing X to Y" states the real range. Never split into several messages
  (owner D3).
- Q3 (a): "more" continues only the list the LAST reply showed (the set carry lasts one turn, as today); anything else is
  parsed as a new ask.
- Q4 (a): no "more" line on the last page; a later "more" gets "That is all N." (ms/zh catalogued in LANE_LABELS, gate on).
- Q5 (a): page only when the parse has `continuation` true AND names no product, attribute or ask of its own (parser fields,
  no word rule); no prompt change. (b) a prompt sentence only if TRAIN shows misses.
- Next slice (not this one): outstanding (20), ref_status (10), find "Showing X of Y", task.py "and N others".

## Design decisions (proposed, for the lead)

- D1. Build on #1744 (option (a), crew Q1 (a)): the coder works on a local merge of #1744's head 3f3bf8101e so the red
  tests are written against the real post-#1744 shape; the Sets PR opens after #1744 merges. Fallback if the owner
  rejects #1744 in its hand test: option (b), section 2.
- D2. The "more" line is printed by the lane (F `output_structurer`, the one place that already knows `shown`, `offset`
  and `qualifying_total`), as the last line above the footer; the envelope carries the same facts as a structured key
  `set_window`, so no reader re-reads the English (card coupling 2).
- D3. "more" is read in a new verdict adapter `TR.with_set_more` (parser fields only), called gate-on outside the
  `if not state_led or answering` block (E:4793), so it works on a state-led turn too. `AP._apply` gets NO hunk: the
  existing `set_continue` + `top_n` path (AP:3722-3746) pages the carry.
- D4. A "more" page is always `answer.SET_LIST_MAX` products; a named count ("show 10") sizes only the page it named.
- D5. On page 2+ the state's `last_list` positions follow the printed numbers (51, 52, ...), so a pick of "51" is the
  row printed "51." (seam M5).
- D6. No new end-of-list sentence: the last page prints no "more" line (its header already says "showing 151 to 182");
  a "more" after it gets today's exhausted reply "(182). That is all 182." (OI-3).

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | **Rows are withheld on exactly one condition** (F:3623-3626): no count named (`top_n` null) AND `qualifying_total > answer.SET_LIST_MAX` (50, A:2792) AND the predicate carries a `breakdown`. Then `shown = 0`, `set_withheld = True`, the reply is the header alone with the breakdown lines (A:3022-3027 via `breakdown_lines` A:2829-2839), `answers` and `attachments` are emptied (F:3701, F:3710), and the engine arms a carry with `shown=0` (E:6993-7000, E:7053-7066) for the answer to "how many should I show?". Without a breakdown the set lists the first 50 (F:3627-3630). | TRAIN cp0-665ba3e897 (linked dealer, "How about p trap"): `Here's what you want: P trap water closets (182)` then seven `• <Brand> water closets: N` lines and no product. Fails R9 and R2. |
| K2 | **#1744 removes the withholding outright (ungated)**: F set block always lists `min(SET_LIST_MAX, remaining)`, `build_set_header` loses `breakdown` and prints `(N, showing X to Y)` on every continuation page incl. the last, `unknown_values_reply` loses its breakdown lines, the engine's `withheld` boolean goes, `page_the_set` and `entity_ids_transformer` step by product CODE (a two-company twin counts once), the resolver stops computing breakdowns, plus a backend/MCP `hide_zero_locations` param. | Everything R9 needs except the "more" line and the continuation already sits in #1744, in the same functions this slice would touch (section 2). |
| K3 | **TRAIN cp0-75e516bd40 is a different path.** Recorded reply `Here's what you want:\n• product: SRTWC180-BL (+9 more)\n\nBut no master_products matched these.` is the miss path's found bullet (A:3740-3787, the `(+N more)` at A:3774-3778), not the set header: the product lane returned no section (trace `looked_up sections: 0`, 26 Sep, prompt v27). S3a already fixed the internal name. | Listing the 10 codes in a bullet would dress a routing defect. Not in this slice; a crew replay on main is owed to see whether it still answers a found set with a miss (risk R9). |
| K4 | **"more" closes the set today.** AP:3678-3681 clears `focus.set_page` every turn; a LISTED set (`shown > 0`) continues only when the verdict carries `set_continue` (AP:3722-3726), which only `TR.with_set_count_from_text` (TR:1301-1352) sets, off the regex `_CONTINUE_COUNT_RE` (TR:1501-1504: "another 40", "40 more", "lagi 10") or a bare count. A bare "more" has no digit, so nothing pages. | The 26 Sep "no paging" ruling is in comments at AP:3664-3668, A:3000-3002, F:1232-1236, TR:4294-4300; R9 supersedes it. The regex stays gate off (not this slice's to delete; the flip PR can). |
| K5 | **The parser's `continuation` field exists but means "a follow-up" in practice.** Schema PA:122-127 says "true when the message asks for more of the set the LAST answer counted"; the parser prompt never defines it (only the shape line `"continuation": false` in `chatbot_parser_prompt.py`). In TRAIN recorded parses it is true on 13 of 84 turns, none a "more": "Black also?", "got eta?", "GOT STOCK ?", "If p trap?", bare codes "SRTWC8608-RL", "MWB762". `SA.vague_token` (SA:415-429) reads `continuation is not False` as "a follow-up". | Paging on `continuation` alone would page "got eta?". The adapter needs a structured guard (no subject, no attribute, no ask of its own) AND the prompt needs one sentence giving the field its schema meaning (OI-4). |
| K6 | **The state path skips the count adapter.** E:4793 runs `with_set_count_from_text` only `if not state_led or answering`; a "more" the parser reads as `keep` is `state_led = "carry"` (CS:814-819), so even "another 10" does not page on a state-led turn today. CS `_DOMAIN_BOUND` (CS:63-65) drops `set_page` on a domain switch; `new_subject` rebuilds the Focus without it (CS:692-697). | M4's call sits after that block (D3). After a topic switch the carry is gone by construction (OI-2 (a)). |
| K7 | **`last_list` numbers a page from 1.** `T.last_list_after` (T:103-127) numbers the section's figures `enumerate(..., start=1)`; F prints `i + 1 + set_row_offset` (F:3370-3371, F:3480, F:3488). On page 2 the row printed "51." is position 1 of the state, so "51" fails CS's pick check (CS:660-669) and "1" picks the 51st product. | Seam M5 (gated). Gate off keeps today's numbering (an existing defect of "another N" pages, not this slice's). |
| K8 | **End-of-list wording exists.** A "more" (or count) after the last page: `page_the_set` sets `exhausted` (TR:4550-4553), the runner skips the tool and prints `build_set_header(..., exhausted=True)` (TR:3999-4018): `Here's what you want: P trap water closets (182). That is all 182.` Catalogued? `That is all` is not in LC LABELS (grep 0), so it is English in ms/zh today. | Reuse it (D6); add its ms/zh under OI-3 only if the owner wants it translated. |
| K9 | **Message size.** `E.WHATSAPP_MESSAGE_MAX_CHARS = 3900` (E:182); `E.split_marked_message` (E:155-180) splits only presenter-marked parts. TRAIN replies already exceed it in one message: cp0-b90acd1184 50 promotion blocks 5,788 chars, cp0-06d09632d1 45 blocks 6,544 chars; staff stock blocks run 116-264 chars each (cp0-4263fb1890 16 blocks 4,103 chars). #1744 notes a 50-product detailed reply is about 8,000 characters (delivery UNVERIFIED). | Cap N is a real decision (OI-1). |
| K10 | **Overlap.** #1744: F `entity_ids_transformer` (F:1229) and the `output_structurer` set block (F:3591-3715), A `build_set_header` / `unknown_values_reply` / a 2-line hunk in `build_breakdown_msg`, TR runner (TR:3727-3752), `set_page_carry` docstring, `page_the_set` (TR:4527-4567), E withheld block (E:6894-6961 on main). #1755: AP `_apply` (one hunk ~AP:3745, the `_narrow_and_plan(` call right after the set_page block), E `_run_stages_body` grounding block (~E:4836) and E:7199, TR:3136, PA `resolve_config`. #1747 (merged in this branch, to main tonight): E:4773-4812 guards, AP `_apply` 3418-3518, CS, T, C (2 lines). None of the three touches `TR.with_set_count_from_text` (TR:1301), `TR.envelope_of` (TR:5553), LC `LANE_LABELS` or K. | M2 and F1 wait #1744; M4's engine call line and M5 wait #1747; nothing waits #1755. |

## 1. The set path on main today (answer to Q1)

| Step | file:line | What it does |
|---|---|---|
| Cap | A:2789-2792 `SET_LIST_MAX = 50` | owner 26 Sep "a counted set that fits one WhatsApp message (about 50 rows) is listed in full"; read at call time |
| Fetch cut | F:603-610 `_set_list_size`, F:1228-1245 `entity_ids_transformer` | `product_ids` cut to the named count or 50; tool `limit` raised to the tool max (`SET_ROW_LIMIT` F:589-595) |
| Row numbering | F:3370-3371 | `set_row_offset` = predicate `offset` (a continuation page) |
| Dealer quantity render | F:3466-3482 | while a quantity is owed: `N. <title>` per product, then the tool's question (TRAIN cp0-958b275668 `kitchen sinks with stock (240, showing 1 to 1)` / `1. AP1050A` / `How many units do you need?`) |
| Row printing | F:3483-3495 | `_item_line(i + 1 + offset, it)` per item: the code answer's own block (S1 stock, S6 product blocks) |
| Withhold decision | F:3591-3634 | K1; `shown` clamped to what rendered (F:3635-3642, reviewer B3) |
| Header | F:3643-3654 -> A:2980-3031 `build_set_header` | `Here's what you want: <phrase> (<total>[, showing X to Y])`; `. It was N when you asked.` (W4); `. That is all N.` (exhausted); breakdown lines when withheld |
| Phrase | A:2941-2977 `described_set_phrase` | brand + choices + noun + measures + leg (`with stock`) |
| Closing lines | F:3659-3666, A:2795-2803 `not_understood_line`, A:3042-3066 `other_brands_line` | `Couldn't find "w".` / `Other brands with stock: Bravat 79, Cabana 57. Name one to see them.`, above the footer |
| Assembly | F:3667-3680 | withheld: header (+ other brands); else header, body, other brands above `_Data last updated: ..._` |
| Envelope | F:3687-3712 | `set_header`, `set_described`, `answers` (emptied when withheld), `attachments` (emptied when withheld) |
| Header strip | TR:5503-5526 `_lane_text_without_withheld_header`, called TR:5661 | strips the baked `set_header` prefix when the turn is not a counted set (coupling 2; unaffected: the new line is at the end) |
| Carry arming | E:6990-7092 | `withheld` / `listed_to_the_limit` / `partly_listed` (all spec tier) -> `TR.set_page_carry` (TR:4271-4364) with `shown` 0 / 50 / named; a page turn takes the runner's `next_carry` (E:7022-7034, TR:4172); offer-only carry for the other-brands line (E:7067-7089) |
| Page | AP:3678-3746 -> TR:3652-3662 runner -> TR:4457-4567 `page_the_set` | recount from `set_key`, `ids[offset:offset+limit]`, `offset = shown`, `exhausted` past the end, `next_carry` |
| Exhausted reply | TR:3998-4018 | header with `That is all N.`, no tool call |

**When rows are withheld so the reply shows no product (R9 fails):** only K1's condition. Every withheld reply is a
spec-tier set of more than 50 with a breakdown and no named count; cp0-665ba3e897 is that case (182 > 50, brand
breakdown, `top_n` null). After #1744 the condition no longer exists.

## 2. PR #1744 SET-STOCK-RENDER (answer to Q2)

| #1744 part | file | Needed by this slice? |
|---|---|---|
| List the first 50 instead of withholding; `answers` / `attachments` always kept; engine `withheld` gone | F:3591-3715, E:6894-6961 (main) | YES (R9 core) |
| No breakdown lines in `build_set_header` and `unknown_values_reply`; breakdown not requested or computed | A:2867-2983, `product_predicate_service`, `references.py`, `resolve_gate.py:841` | YES for the header (R2); the resolver/request removal is cleanup, not needed gate-on |
| `(N, showing X to Y)` on every continuation page incl. the last (`not offset and shown >= total`) | A:2971 | YES (D6 relies on it) |
| Step pages by product CODE (twins counted once): `entity_ids_transformer`, `page_the_set ids_by_code`, runner `code_of` | F:1229-1266, TR:3727-3752, TR:4527-4567 | YES ("next 50" must mean 50 products; else a page boundary splits a code's company rows) |
| Two-company twins added through `_base` | `product_predicate_service.py` | YES for code paging to be honest (same code in two companies, card section 5) |
| `hide_zero_locations` (stock route, `inventory_service`, MCP catalog) | backend + MCP | NO (a stock-fact rule; a second deploy unit) |

Options. (a) Depend on #1744 merging first (crew answered card Q1 (a)). (b) Rebuild the needed parts gated here: every
needed part sits in a function #1744 rewrites (F set block, `build_set_header`, `page_the_set`,
`entity_ids_transformer`, E withheld block), so (b) breaks the overlap rule five times and duplicates reviewed work.
(c) Stack the PR on #1744: gate off would differ from main until #1744 merges, and a stacked PR gets no CI until
retargeted (crew feedback). **Recommend (a)** with D1 (code on a local merge of #1744's head; open the PR after it
merges). The slice then adds only the line, the continuation, the state numbering and the fold. Trigger for (b): the
owner rejects #1744's hand test.

## 3. "more" today and the continuation (answer to Q3)

Today: K4 (closed on every turn; only "another N" or a bare count pages a listed set; a bare "more" closes it) and K6
(on a state-led turn even "another N" does not page).

Proposed flow, gate on, after #1744 and #1747:

1. Reply 1 lists 50 of 182 and ends `Reply "more" to see the next 50.` (M2); E arms the carry as #1744 leaves it
   (`listed_to_the_limit`, `shown = 50`). The envelope's `set_window` is `{"first": 1, "last": 50, "total": 182,
   "next": 50}`.
2. Message "more" (any language; the parser reads it, never a word list). `TR.with_set_more(verdict, carried=
   state_in.focus.set_page)` (M4) returns `{**verdict, "top_n": SET_LIST_MAX, "reference_positions": [],
   "set_continue": True}` when ALL hold: gate on; `continuation is True`; the carry is a dict with `0 < shown < total`
   and no `offer_only`; no entity with `current_message: True` (a reused echo is dropped as TR:1316-1335 does); no
   `requested_attributes`; no `asks` / `sub_asks`; `top_n` null; `is_affirmative` not true. Otherwise the verdict is
   returned unchanged.
3. AP:3722-3746 (untouched) sees `set_continue` and `top_n = 50` with no subject: plan `set_count_answered` with the
   carry; TR runner -> `page_the_set` (post-#1744, by code) lists products 51-100; header `(182, showing 51 to 100)`;
   line `Reply "more" to see the next 50.`; `next_carry.shown = 100`; `last_list` positions 51-100 (M5).
4. Last page (151-182): header `(182, showing 151 to 182)`, no line. A further "more": `exhausted` ->
   `Here's what you want: P trap water closets (182). That is all 182.` (existing, D6).

**"more" after a topic switch.** The carry lives one turn: AP:3681 clears it unless that turn continues it, CS:63-65
drops it on a domain switch, a `new_subject` rebuilds the Focus without it. So "more" continues the most recent capped
list only while the state still holds it, which is exactly "the list the last reply showed"; otherwise M4 finds no
carry and the message is parsed as any other (today's reply, usually the clarify menu). OI-2.

**Where the wiring touches open PRs.** No hunk in `AP._apply` (#1747 and #1755 both change it; D3 avoids it). One call
line in `E._run_stages_body` after E:4812, outside #1747's `if not state_led or answering` block and about 25 lines
above #1755's grounding hunk (~E:4836): lands after #1747 merges (tonight); re-check context against #1755 at the pre-PR
gate. `TR.with_set_more` is a new function beside TR:1301 (no PR touches it). M5 touches T and C, #1747's files: waits
#1747's merge only.

## 4. Spec-set answers (answer to Q4)

| Path that prints brand / size count lines | file:line | After #1744 | This slice |
|---|---|---|---|
| Withheld set header (stock, ETA, cert, promo, spec `price: described`) | A:3022-3027 | gone | nothing |
| `unknown_values_reply` (unknown value: `• <value> <noun>s: N` per value of the key) | A:2931 | gone | nothing |
| `described_members_reply` fallback when the resolver sent no codes | A:2878 | stays (no codes means no products to list) | stays: a miss reply that lists what exists |
| `near_miss_reply` (`gunmetal bathtubs` none exist: the key's other values with counts) | A:2886-2902 | stays | stays: alternatives on a miss, not a set answer; trigger to change: an owner flag |

The single-code block a spec set reuses is already the set's row printer: F:3488 `_item_line` over the products
envelope, whose field set comes from F `_project_product_specs` (S6 gate-on P1/P2: empty un-asked fields left out,
`No <attr> on record for <product>.`, money `MYR 1,234.00`). A stock set's rows are S1's `K/stock` blocks through the
same `_item_line`. So once #1744 lists the rows, R2 holds on every leg with no new seam: one block per product, numbered
from `offset + 1`, no count lines. Test S-10 pins it for a spec set.

## 5. Cap N and the other capped lists (answer to Q5)

Set cap: recommend `answer.SET_LIST_MAX` = 50 (ruling 26 Sep, #1744, crew Q4 (a)), the next-page count
`min(50, total - offset - shown)`, with K9's size risk named in OI-1.

| Other capped list (card Q4) | file:line | Has a carry to page? | Joins this slice? |
|---|---|---|---|
| Outstanding groups `... and N more customers` | P:2268-2281 (`OUTSTANDING_GROUP_CAP`) | no; MCP presenter (second deploy unit); reports are out of lane (card section 5) | no |
| Reference status `and N more lines` | `B/ref_status.py:62-66` (`MAX_LINES`) | no | no |
| Find groups `Showing X of Y.` | P:1650-1652 | no; MCP presenter | no |
| Picker tail `and N others, reply with the full code.` | `B/turn/task.py:885-895` | a picker: every carried option is printed (#1436); the tail counts matches past them | no, stays (pickers uncapped) |
| Also: `and N more` | P:2885, P:3380, P:3455 (reports) | no | no |

Recommend none joins (OI-5): each needs its own carry or re-fetch; the trigger is a bench failure on that list or an
owner ask. This amends crew Q4 (a) "one line on every capped list" to "every capped SET list" for this slice.

## 6. Seams

Verdicts: **G** gate-on seam; **F** byte-identical fold (no gate); **W** waits for a merge; **S** stays.

| # | Seam | file:line (today) | Gate on | Gate off | Overlap / waits |
|---|---|---|---|---|---|
| M1 | `K/sets.py` (new): `more_line(n: int) -> str` = `Reply "more" to see the next {n:,}.`; `next_count(total, offset, shown, cap) -> int` = `max(0, min(cap, total - offset - shown))`; `page_size(named: int \| None, cap: int) -> int` = `min(named, cap) if named else cap` | new | printed by M2 | not imported on the gate-off path | none |
| M2 | F `output_structurer` set block: when `n = next_count(...) > 0`, put `more_line(n)` after the rows, above the other-brands line and the footer (dealer quantity render: after the question, the same last-line rule); spread-in `out["set_window"] = {"first", "last", "total", "next"}` | F:3643-3712 (post-#1744 shape) | line + key | today's bytes and keys (G) | #1744 function: **W #1744** |
| M3 | LC `LANE_LABELS`: `Reply "more" to see the next {n}.` ms `Balas "more" untuk lihat {n} seterusnya.` zh `回复 "more" 查看接下来的 {n} 个。` (crew wording check) | LC:579-588 | translated | key absent from the gate-off table (LC:605) | none |
| M4 | `TR.with_set_more(verdict, *, carried)` (new, beside TR:1301) + one gated call after E:4812: `if composers.enabled(): verdict = turn_runtime.with_set_more(verdict, carried=state_in.focus.set_page)` | TR:~1353, E:~4813 | pages (section 3 step 2) | no call | E call: **W #1747** (context), re-check vs #1755 E:4836 |
| M5 | `TR.envelope_of` copies `set_window` into the envelope; C:571 passes `first` into `Section` (new field `first: int = 1`); `T.last_list_after` numbers from `first` | TR:5553+, C:51-56, C:571, T:103-127 | positions 51-100 on page 2 | `first` stays 1 (key absent gate off) | C, T are #1747 files: **W #1747** |
| F1 | `K/sets.page_size` replaces the two `min(named, SET_LIST_MAX)` copies: F:610 and TR:4536 | F:603-610, TR:4536 | same bytes | same bytes (F) | TR:4536 is in `page_the_set` (#1744): **W #1744** |
| S1 | `with_set_count_from_text` regex paging ("another 40") | TR:1301-1352, TR:1495-1517 | stays | stays | flip PR may retire it |
| S2 | `near_miss_reply`, `described_members_reply` count lines | A:2865-2902 | stay (section 4) | stay | none |
| W1 | cp0-75e516bd40 found-bullet `(+9 more)` on a product miss | A:3774-3778 | not changed | - | diagnosis owed (K3) |

Seam count: 5 gated seams (M1-M5) + 1 byte-identical fold (F1). The docstrings that state "No paging (owner ruling,
26 Sep 2026)" (A:3000-3002, F:1232-1236, AP:3664-3668, TR:4294-4300, PA:122-126) get a one-line "gate on: R9 pages"
note in the files the slice already touches; AP's comment is left alone (no `_apply` hunk).

## 7. Red tests (`tests/chatbot/test_composers_sets.py`, test(red) commit first)

Fixture: a spec set of 182 water closets, description `[{"key": "trap_type", "label": "Trap", "value": "P trap",
"kind": "enum"}, {"key": "class", "value": "Water Closet", "kind": "enum"}]`, require `{"price": "described"}`; a staff
compact contact and a linked dealer; post-#1744 base.

| # | Test | Assertion |
|---|---|---|
| S-1 | `test_gate_off_first_page_golden` | gate off: header line == `Here's what you want: P trap water closets (182, showing 1 to 50)`, 50 numbered blocks, no `Reply "more"`, envelope has no `set_window` |
| S-2 | `test_more_line_first_page` | gate on: the line after block 50 is `Reply "more" to see the next 50.`, followed by `\n\n_Data last updated: ` |
| S-3 | `test_more_line_counts_remaining` | offset 100, shown 50 -> `Reply "more" to see the next 32.`; offset 150, shown 32 -> no line |
| S-4 | `test_more_line_named_count` | `top_n` 10 of 240 -> `Reply "more" to see the next 50.` |
| S-5 | `test_more_line_above_other_brands` | with `other_brands`: rows, the line, `Other brands with ...`, footer, in that order |
| S-6 | `test_with_set_more_pages` | gate on, `{"continuation": True, "entities": [], "requested_attributes": [], "top_n": None}`, carry `{"set_key": {"shown": 50, "total": 182}}` -> `top_n == 50`, `set_continue is True`, `reference_positions == []` |
| S-7 | `test_with_set_more_gate_off_identity` | gate off: `with_set_more(v, carried=c) is v`; and the engine never calls it (spy) |
| S-8 | `test_follow_up_is_not_more` | TRAIN shapes: `continuation` true with `requested_attributes: ["eta"]` ("got eta?"), with a `current_message` colour entity ("Black also?"), with `domain_hint` and `asks` -> verdict unchanged |
| S-9 | `test_more_without_carry` | carry None, carry `offer_only`, carry `shown >= total` handled: None and offer_only -> unchanged; exhausted path below |
| S-10 | `test_spec_set_uses_code_blocks` | gate on, spec set page: every block equals the single-code `_item_line` block for that product (S6 rules), no line matches `^• .*: \d` |
| S-11 | `test_engine_more_pages_51_to_100` (engine level) | turn 1 lists 1-50; turn 2 "more" (parse fixture `continuation: true`) -> header `(182, showing 51 to 100)`, first block numbered `51.`, line `next 50` |
| S-12 | `test_engine_more_on_state_led_turn` | same as S-11 with `state_changes: [{"op": "keep", ...}]` (state_led `carry`) -> pages |
| S-13 | `test_engine_more_after_last_page` | after 151-182, "more" -> `Here's what you want: P trap water closets (182). That is all 182.` and no tool call |
| S-14 | `test_engine_more_after_topic_switch` | 50 listed, then a stock ask for one code, then "more" -> no page (no carry), reply equals the gate-off reply of that "more" |
| S-15 | `test_line_implies_carry` | every gate-on reply carrying `Reply "more"` leaves `focus.set_page.set_key.shown < total`; every reply without it leaves no page carry (offer-only carries excepted) |
| S-16 | `test_last_list_follows_printed_numbers` | page 2: `focus.last_list[0]["position"] == 51`; a pick of 51 resolves the 51st product; gate off `== 1` |
| S-17 | `test_more_line_ms_zh` | gate on, ms and zh contacts: the line is translated with `{n}` kept; gate off the key is not in the table |
| S-18 | `test_dealer_quantity_render_line` | linked dealer availability set of 240: titles 1-50, `How many units do you need?`, then the line |
| S-19 | `test_page_size_fold` | `page_size(None, 50) == 50`, `page_size(10, 50) == 10`, `page_size(80, 50) == 50`; F `_set_list_size` and `page_the_set` limit unchanged on 6 inputs |

Gate-off goldens are literals (S-1, S-7, S-13's exhausted line is today's text). Kill-proof: drop M4's
`requested_attributes` guard (S-8 red), drop M5 (S-16 red), print the line on the last page (S-3 red).

## 8. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | The line is printed by F, the carry armed by E (E:7053-7066): a set F lists in part but E does not carry (code tier, `miss_on_typed_product and not class_terms` E:7051, `set_page_carry` None TR:4328-4339) would offer a "more" nobody can answer | S-15; if it goes red on a real case, F reads one more ctx fact rather than E re-reading text |
| R2 | `continuation` drift: true on 13 of 84 TRAIN follow-ups (K5); the guard catches the TRAIN shapes, but a bare "GOT STOCK ?" after a stock set has no attribute and no entity | OI-4 (a) prompt sentence; bench TRAIN parse run before and after; S-8 |
| R3 | A prompt sentence that narrows `continuation` changes `SA.vague_token` (SA:429): 3 TRAIN bare-code follow-ups with `continuation: true` (SRTWC8608-RL, MWB762, a 3-line code list) could lose their carry | measured on the TRAIN bench with the prompt version; if it moves, `vague_token` reads `anaphora.backward_reference` alone (its own one-line change, crew decision) |
| R4 | 50 blocks exceed 3,900 characters (K9) | OI-1; today's sends of 5,788 and 6,544 characters already happen (TRAIN) |
| R5 | Page 2 pick numbering (K7) | M5 + S-16 |
| R6 | #1744 rejected or long parked | fallback (b), section 2; the design and tests stay valid with `breakdown` re-added as a gated branch |
| R7 | The new key `set_window` reaches n8n (envelope) | spread-in, gate-on only; n8n ignores unknown keys (as `set_described`) |
| R8 | Coupling 2: TR:5503 strips by the `set_header` prefix | the line is at the end and `set_header` bytes are unchanged; S-1 / S-2 assert `set_header` |
| R9 | cp0-75e516bd40 (a found set answered by the miss path) stays open | crew replay on main; if it still fails, it is a routing lane (CHAT-CODE-LOOKUP / spec tier), not a composer |

## 9. CARD DELTA (at most 5 open items)

- **OI-1 Cap N for a set page.** (a) 50 (`SET_LIST_MAX`, owner 26 Sep "about 50 rows fit one WhatsApp message", #1744,
  crew Q4 (a)); (b) 20 for detailed blocks (stock detailed, product info), 50 for compact and dealer lines; (c) as many
  rows as fit 3,900 characters. Recommend (a): one number, already ruled; TRAIN shows 5,788-character replies sent
  today (cp0-b90acd1184). Trigger for (c): a delivery failure on a set page.
- **OI-2 "more" after a topic switch.** (a) "more" continues only the capped list the last reply showed (the carry
  lives one turn, as today); with no carry the message is parsed as any other (today's clarify); (b) keep the most
  recent capped list until a newer one replaces it (a new carry lifetime across topics). Recommend (a): R9 asks for
  the next page of what was just shown; (b) adds state the owner never asked for (simplest-thing rule).
- **OI-3 End-of-list wording.** (a) The last page prints no "more" line (its header says `showing 151 to 182`); a
  "more" after it gets the existing `Here's what you want: <set> (182). That is all 182.`; (b) a new last-page line
  `That is the last of them.` (new ms/zh). Recommend (a): existing wording (round 3 W2), no new sentence. Sub-question:
  `That is all N.` is not catalogued (English in ms/zh today); add its ms/zh gate-on? Recommend yes, one LANE_LABELS
  entry.
- **OI-4 The parser signal.** (a) `continuation` with one prompt sentence giving it its schema meaning (PA:122-126:
  "true only when the message asks for more of the list the previous response showed and names nothing new"),
  published as a prompt version by crew, plus M4's structured guard; (b) a new `more` op in CHAT-STATE's
  `state_changes` table (CS ops; works only on state-path prompt versions); (c) `continuation` as emitted today plus
  the guard only, no prompt change. Recommend (a): R9 names the continuation flag; the guard alone pages some
  follow-ups (R2); (b) leaves the production label without paging. Cost: one prompt version and a TRAIN parse run.
- **OI-5 Other capped lists (card Q4).** (a) This slice pages sets only; outstanding (P:2280), reference status
  (`ref_status.py:65`), find groups (P:1652) and report `and N more` keep their trailers; the picker tail
  (`task.py:894`) stays (#1436); (b) each joins with its own carry and the same line. Recommend (a): none has a carry
  or a TRAIN failure today; trigger: a bench failure on one of them or an owner ask. Amends crew Q4 (a) "every capped
  list".
