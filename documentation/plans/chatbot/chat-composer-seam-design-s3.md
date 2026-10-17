# CHAT-COMPOSER seam design: S3 not found / clarify / hand-over (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`, card `chat-composer-behaviour-card.md` section 4 "Not found /
clarify / hand-over", UAC S3 line. Builds on `chat-composer-seam-design.md` (S0+S1, "Lead decisions 17 Oct" binds) and
`chat-composer-seam-design-s2.md` (section 10a hands S3 the bare-vs-named quantity question and the word "inventory").
Read-only design. Line numbers on HEAD 2831bc58e5 (S0+S1 landed, S2 in red). Open PR hunks read with
`gh pr diff 1744|1747|1755 --repo foundryx-sdn-bhd/sorento-crm` on 17 Oct.
Paths: B = `sorento_crm_backend/app/services/chatbot`, A = `B/lanes/business/answer.py`, C = `B/turn/compose.py`,
E = `B/engine.py`, AB = `B/answer_bridge.py`, F = `B/lanes/business/fetch.py`, G = `B/lanes/business/gate.py`,
T = `B/turn/task.py`, TA = `B/turn/apply.py`, TR = `B/turn_runtime.py`, LC = `B/label_catalog.py`, K = `B/composers/`,
P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.

**Binding rulings applied:** sibling-variant picker after an exact answer STAYS (A:4864-4876 today, owner 17 Oct);
quantity fill-in lines `N. CODE - ` STAY (T:394-405, owner round 6 ruling 2); pickers uncapped (#1436); R5 no internal
names; R12 narrowed; R10 tier-question order; every reworded sentence gets ms/zh in the same slice; gate pattern
`if composers.enabled(): <new>` else today's code; no hunk in a function an open PR changes.

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | **A bare "more" does not page today.** `TA:_apply` 3660-3714: "No paging (owner ruling, 26 Sep 2026) ... Anything else, a "more", a new question, closes it". Only "another N" / "N more" continues a part-listed set (`TR:set_continue_count` 1506, regex `_CONTINUE_COUNT_RE` needs a NUMBER). The parser's `continuation` flag (head/parser.py:119-124) is "declared and still emitted, but nothing pages on it". | `Reply "more" to see the next N.` would invite a reply that closes the set. The more line cannot print until a continuation reads it: `TA:_apply` (changed by #1747 and #1755) and set paging `TR:page_the_set` / `set_page_carry` + `A:build_set_header` (#1744). **The more line moves out of S3 to the Sets slice** (OI-1). |
| K2 | **The offer and picker sentences need no rewording for S3's card changes.** Card S3 = one miss grammar, one more line, no internal names, R12, R10. The readers that re-read English (`dealer_stock._ESCALATION` dealer_stock.py:27, `order_list._OFFER_SENTENCE` order_list.py:231 / `_without_picker` :269, `AB:with_alternative_lines` 2176, `tail/compose.MARKERS` tail/compose.py:30, `reply_ladder._ESCALATE_TO_TEAM_RE` tail/reply_ladder.py:74, `member_offer` :358, `sub_answer._ESCALATE_OFFER_RE` lanes/business/sub_answer.py:190, `AB:_NUMBERED_LINE` 488, `pickers._annotate_lines` pickers.py:71, `A:_PICKER_LINE_RE` 4508, `F:_outstanding_offer_from_text` 2169 / `_OFFER_BLOCK_RE` 2259) key on the offer sentence, the closers ("Reply with a code to continue", "Reply a number to pick"), the markers ("Related products:", "Did you mean", "Here are the closest matches:", "Try:") and numbered lines. S3 changes none of those strings. | Every one of those readers sees byte-identical input gate on. They switch to `Reply.offer` / `Reply.question` in the flip PR, when the old text paths go. S3's one new coupling (compose's miss line, read by `E:_COMPOSE_MISS` 7631) is replaced by data in the same slice (M1). |
| K3 | **The two "Couldn't find" heads are both catalogued; the others are not.** `Couldn't find {names}.` LC:359 + INLINE LC:640 and `Couldn't find {shown}. Did you mean:` / `... Did you mean {label}?` LC:328-329 + INLINE LC:620-621. `Couldn't find: {names}.` LC:419 + INLINE LC:664. `I could not find {names}.` LC:303 + INLINE LC:598. "Could not find ..." (A:4289, A:4417), "Here's what you want:", "But no {x} matched these." / "But none matched." have 0 LC entries (ms/zh get English today, existing gap). | Canonical miss = `Couldn't find {names}.` (section 2). Converging "I could not find" and "Couldn't find:" onto it keeps ms/zh working with **no new key**. A reworded "But no {x} matched these." needs new keys (section 2). |
| K4 | **Two different absences.** (i) a typed token nothing placed ("not found") and (ii) a found product whose asked fact is empty ("No stock found for X." C:652/682, "No stock for X." AB:1663, "No incoming for X." A:~1104, "No {label} found for X." alternatives.py:275). S1 kept (ii) byte-identical as a FACT, not a miss; S6 brings `No <attr> on record for <product>.` (Q3). | S3's one grammar is for (i) only; (ii) stays (OI-3 if crew reads the card's "nine grammars" as including (ii)). |
| K5 | **The bare quantity question reaches a customer in three places** (T:784-788: the engine replaces the presenter's bare question only on a single-domain stock turn). (1) a MULTI-domain reply: `E:_stock_ask_reply` 7545 returns at the `domains != ["inventory"]` check, so compose prints the inventory lane text verbatim, which is the presenter intro P:121 alone (items suppressed, F:3404-3412). Recorded cp0-b1f73c72c0: `How many units do you need?\n\nI have attached the file(s) below.\n\n1. *Promotion:* CABANA ...` (focus domains inventory + promotion, message "Dealer" answering a tier pick). The stock task is still opened: `state_out.focus.tasks = reply.tasks` runs BEFORE that check. (2) a SET dealer reply (F:3474-3488 set question, inside `output_structurer`): recorded cp0-958b275668 `Here's what you want: kitchen sinks with stock (240, showing 1 to 1)\n\n1. AP1050A\n\nHow many units do you need?`. (3) a single-domain stock turn with no product named (`after_reply` `named_products=False`, T:~932, SEC-S2: no task opened, text None) keeps the bare intro with no task behind it. UNVERIFIED that (3) is reached by a live parse. | R12 + the S2 hand-over item: (1) gate on drops the question and opens no task (Q1, S3a); (2) belongs to `output_structurer` (#1744) and the Sets slice; (3) OI-4. No gate-on path ends up printing the bare question beside an answer, so the named-vs-bare "unification" is the drop, not a reword. |
| K6 | **"master_products" / "inventory" come from `domain_hint`.** `A:build_breakdown_msg` 3938 prints `But no{active} {_plain_words(domain_word)}... matched these`, called at A:4401 with `f"{status_label}{domain_hint}"`; recorded cp0-75e516bd40: `But no master_products matched these.` (prod 26 Sep; `_plain_words` A:2590 now prints "master products", still a table name). Same key in `Could not find ... {_plain_words(domain_hint)}` A:4417. A customer word map already exists: `_SCOPE_WORD` A:2431-2441 (`inventory` -> "stock", `master_products` -> "product", `spo_allocation` -> "SPO line"). | R5 fix = read the key through `_SCOPE_WORD` for the internal keys only (M6). Both sites are inside `not_found_error_message` (A:3135-~4500), which #1744 changes (section 5). |
| K7 | **R10 is control flow, not wording.** The tier ask is decided by the resolver exit `access_ask` / fetch arm `tier-ask` and acted on in `E:_run_stages_body` 6172-6410 (the function all three open PRs change); rendered by `A:access_level_choice_message` 259 via `lanes/business/__init__.py:2827` and `AB:723`. | A composer cannot reorder questions (AC-G4: a composer is a pure function of facts). R10 waits for #1744 + #1747 + #1755 and is a decision change, not S3 wording (OI-2). |
| K8 | **#1744 touches `not_found_error_message` with a comment only** (base A:4409-4410, the unknown-values arm: 2 comment lines). Its real code hunks are `unknown_values_reply`, `build_set_header`, `entity_ids_transformer`, `output_structurer` (nested `_co_of_row` region), `resolve_entity_body`, `_run_stages_body`, `runner`, `set_page_carry`, `page_the_set`. `near_miss_reply` is NOT changed (the hunk header names it only as context). #1747 touches C only in the `_ASK_HEADERS` dict (two new keys), not `compose()` / `_did_you_mean_per_code` / `class Answer`. #1755 changes `G:run_gate` (386-~1700, which holds the gate product picker ~981 and the `Couldn't find:` line 1598) and `TA:_apply` / `_narrow_and_plan`. | S3a has no hunk in any PR function. S3b sits 400+ lines from #1744's comment hunk except M7 (about 15 lines): crew call (OI-5). |

## 1. Helpers that land in S3 (`K/__init__.py`, `K/misses.py`)

| Helper | Output | Note |
|---|---|---|
| `Reply` (frozen dataclass, `K/__init__.py`) | `text: str`, `question: Pending \| None = None`, `offer: Any = None` | Shape as S0/S1 design 1.3. In S3 its one consumer is M1 (`Answer.miss_text` carries `Reply.text` as data). The offer readers of K2 adopt `.offer` in the flip PR. |
| `misses.not_found(names: list[str], *, join=None) -> Reply` | `Reply(f"Couldn't find {joined}.")`; `join` defaults to `", ".join` (S1's `stock.miss_line`); compose passes its own localized `_join_words` | `stock.miss_line` gate-on becomes `misses.not_found(...).text` (byte-identical, so an internal fold, no gate change). |
| `misses.MISS_WORD = {"inventory": "stock", "master_products": "product", "spo_allocation": "SPO line"}` and `misses.domain_word(key) -> str` | the `_SCOPE_WORD` value for those three keys, else `key.replace("_", " ")` (today's `_plain_words`) | Three keys, each with recorded evidence (cp0-75e516bd40; S2 section 10a; fetch.py:193 "no spo_allocation matched these"). Every other domain prints exactly as today, so "But no incoming matched these." (S2 keeps it) is unchanged. Not `DOMAIN_LABELS` (A:240): it pluralises ("orders", "incoming stock") and would change every order miss. |
| `misses.quantity_question_rides(domains: list[str]) -> bool` | `domains == ["inventory"]` | The R12 rule, one copy, read by compose (Q1a) and the engine (Q1b). |
| `format.more_line(n)` | NOT in S3 (K1). Lands in the Sets slice with the continuation that answers it. | Lead decision "helpers land in the slice that first prints them". |

`K/misses.py` imports only `K`, `jsc` and (TYPE_CHECKING) `turn.pending.Pending` (S0 boundary test). It holds no "Did you
mean" string (the sentence stays where it is today), so `test_rearch_r4_deletions_and_cap.py:59` is untouched.

## 2. Canonical wording per kind

| Kind | Canonical (gate on) | Chosen from | ms / zh key |
|---|---|---|---|
| Token nothing placed (one or several) | `Couldn't find X.` / `Couldn't find X, Y.` (compose keeps its localized `X, Y or Z` joiner) | dealer / S1 wording, compose C:1216 | LC:359 + INLINE LC:640 exist. No new key. |
| Token nothing placed, with candidates | `Couldn't find X. Did you mean:` + numbered real names, or `... Did you mean Y?` | dealer_stock.py:112-115, A:4695 `did_you_mean_head` (quoted + "(type)" form) | LC:328-329 / INLINE LC:620-621 exist. **Stays as is** (card: did-you-mean Stays); the quoted `"X" (product)` form already matches the INLINE key. |
| Set / predicate miss (owner structure 28 Sep, A:2825) | `Here's what you want: ...` + `But no {word} matched these.` with `{word}` = `misses.domain_word(key)`: `But no stock matched these.`, `But no product matched these.` | `what_you_want_reply` / `build_breakdown_msg` | NEW keys (AC-G5) for the REWORDED sentences only: `"But no stock matched these."`, `"But no product matched these."`, `"But no SPO line matched these."` as whole INLINE sentences (per word, not `{word}`, so the noun translates; these strings never print gate off, so gate-off ms/zh stay byte-identical). `"Here's what you want:"`, `"But no incoming matched these."`, `"But none matched."` print gate off too: cataloguing them changes gate-off ms/zh (risk R3), so they stay uncatalogued in S3 (existing gap, crew call). ms/zh text from crew/translation memory. A sentence with a date window, access or company suffix stays English (UNVERIFIED count; existing gap). |
| Domain miss naming a subject | `Couldn't find {active} {word} for X{dates}{access}.` (verb `Couldn't`, word through `domain_word`) | A:4289 / A:4417 "Could not find ..." | Matches INLINE `Couldn't find {names}.` (names = the rest, English inside). No new key; partial translation as A:4182 has today. |
| Quantity question | named `How many units of X?` (one product) / `How many units for each?` + `N. CODE - ` lines (several), unchanged | T:383-405 (owner rulings) | LC:322-323 exist. The bare `How many units do you need?` (LC:50) is never printed beside an answer gate on (Q1). |
| More line | `Reply "more" to see the next N.` | card Q4 | Sets slice (K1). Key added there. |
| Offer / closers / pickers | unchanged | | unchanged |

## 3. Inventory: every miss / clarify / picker / offer / trailer producer

Verdicts: **C** canonical already; **G** converge gate on (seam); **S** stays; **W#** waits for that PR's merge.

| # | Producer (file:line) | Today | Verdict |
|---|---|---|---|
| 1 | C:462 `_did_you_mean_per_code` (no-suggestion arm) | `\nI could not find X or Y.` | G (M1) |
| 2 | C:486 `_did_you_mean_per_code` unsuggested line | `\n\nI could not find X.` | G (M1) |
| 3 | C:1216 / C:1231 `entities_only_reply` | `Couldn't find A and B.` | C |
| 4 | C:612 / C:657 / C:682 `compose` | `No {label} found for X.` / `No stock found for X.` | S (fact, K4) |
| 5 | C:885-896 offer in `compose` | `Would you like me to escalate to T team?` | S (K2) |
| 6 | C:1105 `compose_question` + `_ASK_HEADERS` C:~1003 | roster headers + `N. label` | S (pickers uncapped; `_ASK_HEADERS` is #1747's hunk anyway) |
| 7 | A:4595-4601 `_unsuggested_line` | `\n\nI could not find X.` | G (M2) |
| 8 | A:2779-2787 `not_understood_line` | `Couldn't find: "a" and "b".` | G (M3) |
| 9 | A:2825-2843 `what_you_want_reply` | `Couldn't find: {missing}.` / `But no {leg} matched these.` / `But none matched.` | G (M4: colon; keys for the matched lines) |
| 10 | A:2866 `near_miss_reply`, A:2845 `described_members_reply` | call #9 | C via #9 (no hunk) |
| 11 | A:2884 `unknown_values_reply` | calls #9 | W1744 (its function; reaches the canonical through #9 anyway) |
| 12 | A:3906 `build_breakdown_msg` | `Couldn't find: {nf}.` | G (M6), W1744 (OI-5) |
| 13 | A:3938 `build_breakdown_msg` | `But no {_plain_words(domain)} matched these.` (cp0-75e516bd40) | G (M6: `domain_word`), W1744 |
| 14 | A:4182 / A:4237 | `Couldn't find X with ...` / `Couldn't find X.` | C |
| 15 | A:4289 | `Could not find{active} {subject}{dates}{access}.` | G (M7), W1744 |
| 16 | A:4392 / A:4411 | `Couldn't find: X.` | G (M7), W1744 |
| 17 | A:4417 | `Could not find{active} {status}{_plain_words(domain)}{for}{dates}{access}.` | G (M7: verb + `domain_word`), W1744 |
| 18 | A:3273 `_esc_offer`, A:4793 `_cont` closers | offer + `Reply with a code/number to continue, or ...` | S (K2) |
| 19 | A:4695 `did_you_mean_head`, A:~5249 / ~5286 / ~5354 / ~5363 D1/D2 pickers | `Couldn't find "X" (type). Did you mean:` / `Here are the closest matches:` / `Did you mean A, B, or C?` | S (card: did-you-mean stays; markers read by TC) |
| 20 | A:4887 `build_suggest_offer` sibling picker | `No incoming stock (ETA) found for X. Related products:` + `N. CODE - has incoming` | S (owner 17 Oct) |
| 21 | A:2331 / A:2354 promo misses | `No promotion found for X.` | S (S4 promotions) |
| 22 | A:259 `access_level_choice_message` | tier menu | S (text); order = R10, W1744+W1747+W1755 (K7) |
| 23 | A:~3619 / ~3627 entitlement miss | `<label> has ended ...` / `... not available at your access level` | S |
| 24 | AB:646-686 `no_live_kind_reply` | `There is no <brand> <kind> right now.` | S (a fact, K4) |
| 25 | AB:1622 / AB:1663 crossdomain notes | `No {word} for X.` / `No stock for X.` | S (fact; S1 fold F-b) |
| 26 | AB:426 silent-company offer | `*Company* T team?` | S (K2) |
| 27 | dealer_stock.py:112-115 | `Couldn't find X. Did you mean ...` | C |
| 28 | E:7616 / E:7644 via `K/stock.miss_line` | gate-on `Couldn't find X.` (S1) | C (helper re-pointed at `misses.not_found`, no text change) |
| 29 | E:7631 `_COMPOSE_MISS` regex reader | reads "I could not find" | G (M1: reads `answer.miss_text` gate on) |
| 30 | so_status.py:278 / :293 | `I could not find PO ref X.` / `I could not find A, B or C.` | G (M5) |
| 31 | G:1598 `run_gate` | `Couldn't find: {df}.` | G, W1755 |
| 32 | G:~981 `run_gate` gate product picker | `Found: A, B.\nWhich product do you mean? Please choose:` | S (picker), and W1755 if ever touched |
| 33 | F:3490-3508 `output_structurer` silent-company line | `*{names}:* no {result_type} records found for X.` (R5: `spo allocation records`) | G (R5), W1744 |
| 34 | F:3474-3488 `output_structurer` set dealer question | `How many units do you need?` under a set row (cp0-958b275668) | G (R12), W1744 + Sets slice |
| 35 | P:121 / P:1745 `_availability_intro` | `How many units do you need?` | S at the source (gate-off path, MCP); dropped downstream gate on (Q1) |
| 36 | T:383-405 `StockQtyTask.question` | named question + `N. CODE - ` lines | S (owner rulings) |
| 37 | T:865-891 `pick_question`, T:~1067 `picks_question` | `X matches N products. Which one?` + numbered + `and N others, reply with the full code.` | S (picker uncapped; the tail counts matches past `MAX_SLOTS`, which no "more" can page, K1; OI-1) |
| 38 | TA:3084 / :3130 `STOCK_PICK_NOT_ALL`, `Is N for all M products ...` | quantity clarify | S |
| 39 | lanes/escalation.py:413-434, :837-843, :1313-1327; lanes/canned.py:155-170; tail/member_offer.py:75-83 | hand-over clarifies, team / member pickers | S (registry copy, hand-over unchanged by the card) |
| 40 | escalation.py:1324 | `I could not find anyone called X. Which team ...` | S (a person, not a product; one sentence with its question) |
| 41 | ref_status.py:62 | `and N more lines` | S in S3 (no pager; S5 or Sets slice with K1) |
| 42 | P:1652 crm_find | `Showing X of Y.` | S in S3 (MCP; no pager) |
| 43 | P:2280 outstanding report | `... and N more products` | S (reports out of lane, card s.5; `F:_OFFER_BLOCK_RE` relies on the leading "...") |
| 44 | E:`_run_stages_body` 6172-6410 tier ask | tier menu before the product is known | R10, W1744+W1747+W1755 |

## 4. Gate-on seams

| Seam | Where | Facts read | Gate-on text | Downstream readers, how they keep working | ms/zh |
|---|---|---|---|---|---|
| **M1** (S3a) | C:`_did_you_mean_per_code` lines 462 and 486: the sentence comes from `composers.misses.not_found(plain, join=lambda n: _join_words(n, localizer))` gate on. C:`class Answer` (C:63) gains `miss_text: str = ""`; `compose()` copies the miss `Reply.text` into it gate on only (the dataclass default keeps gate-off `Answer` equal). E:`_dealer_names_the_miss` 7634-7654: gate on, the strip is `text[: -len(miss)]` when `miss := answer.miss_text` and `text.rstrip().endswith(miss)`, else today's `_COMPOSE_MISS` path. | unplaced tokens (`plain`), already data | `I could not find X or Y.` -> `Couldn't find X or Y.` | `_COMPOSE_MISS` no longer needed gate on (exact recorded bytes, the S2 I3 `only_other_note` pattern). TC `MARKERS` do not include either verb. `dealer_stock._ESCALATION` unaffected. `_line_number` C:385 reads only `N. `. Replay of a dealer ETA miss (S1 `test_eta_miss_gate_on_refer_stays_last`) must stay green. | LC:359 / INLINE LC:640 (`{names}` takes the localized " or "). No new key. |
| **M2** (S3a) | A:`_unsuggested_line` 4595-4601 | the missed tokens | `\n\nCouldn't find X.` | same compose-sibling shape as M1; nothing re-reads it | as M1 |
| **M3** (S3a) | A:`not_understood_line` 2787 | words the set reader dropped | `Couldn't find "a" and "b".` | none | INLINE `Couldn't find {names}.` matches (quoted names stay English, as today). No new key. |
| **M4** (S3a) | A:`what_you_want_reply` 2832-2836 | `missing`, `leg`, `lines` | `Couldn't find {missing}.`; the matched-these lines unchanged | `refer.after` / offer suffix unchanged, so TC MARKERS and `_ESCALATION` see the same tail | the colon drop needs no key (INLINE LC:640); M4 rewords no matched-these line (the leg word is already a customer word here, `_MISS_NOUN` A:~2803), so no new key |
| **M5** (S3a) | so_status.py:278 and :293 | `ref`, `result.missing` | `Couldn't find PO ref X.` / `Couldn't find A, B or C.` | none re-reads (UNVERIFIED for so_status tests that pin the text; tester lists them) | INLINE LC:640 matches. No new key. |
| **Q1** (S3a) | (a) C:`compose()` section loop, at `block = lane_words.strip()` (C:~595): gate on and NOT `misses.quantity_question_rides(section domains)` and the envelope is a stock quantity ask (`result_type == "stock_availability"` and an item flag `needs_quantity`, the F:3404-3412 condition, read from envelope keys) and another section printed a block: skip this block. (b) E:`_stock_ask_reply` 7545, the `state_out.focus.tasks = reply.tasks` line: gate on and not `quantity_question_rides(plan domains)`, adopt `reply.tasks` minus a `stock_qty` task opened this turn (`opened_at_turn == turn_no`), so no question is open that the reply did not ask. | envelope `result_type`, `items[].flags.needs_quantity`, plan domains, task `opened_at_turn` | cp0-b1f73c72c0 gate on: `I have attached the file(s) below.\n\n1. *Promotion:* CABANA ...` (the bare question and its task gone) | the parser hint (`StockQtyTask.hint`) sees no new task, so a later bare number is not read as a quantity; `after_reply` closing answered tasks still applies (only the new task is dropped). A stock-only turn is unchanged. | none (nothing printed) |
| **M6** (S3b, W1744 per OI-5) | A:`not_found_error_message` / nested `build_breakdown_msg`: line 3906 colon drop; line 3938 the word via `misses.domain_word`; the caller A:4401 passes the key separately (`status_label`, `domain_hint`) gate on so the map sees the bare key | `domain_hint`, `status_label`, `nf` | `But no product matched these. Would you like me to escalate to warehouse team?` (cp0-75e516bd40); `But no stock matched these.` | offer suffix unchanged (TC MARKERS, `_ESCALATION`, `order_list._OFFER_SENTENCE` intact); S2 I3's fold keys on `_xdBlock`, not this text | new keys (section 2) |
| **M7** (S3b, W1744 per OI-5) | A:`not_found_error_message` lines 4289, 4392, 4411, 4417 | as above | `Couldn't find X.`; `Couldn't find {word} for X ...` | offer suffix unchanged | INLINE LC:640 |
| **M8** (S3c, W1755) | G:`run_gate` 1598 | `df_lines` | `Couldn't find X.` | gate clarification text read by nothing (UNVERIFIED) | INLINE LC:640 |
| **R5f** (S3b, W1744) | F:`output_structurer` 3490 silent-company noun | `result_type` | the customer word for the tool (reuse `DOMAIN_LABELS` or per-tool word, crew call) | none | key per word |

## 5. Byte-identical folds, no gate (L1 replay 0 diffs)

| Fold | Copies | Into | Note |
|---|---|---|---|
| F-d | `f"I could not find {_join_words(plain, localizer)}."` at C:462 and C:486 | one local builder in C (`_unplaced_line(plain, localizer)`), which M1 then gates | same f-string, different leading newlines kept at the call sites |
| F-e | `stock.miss_line` gate-on text and `misses.not_found` | `stock.miss_line` calls `misses.not_found(...).text` | lane-owned code, byte-identical |

Rejected: `so_status` joiner (`" or "` literal) vs C `_join_words` (localized " or "): differ in ms/zh. `_unsuggested_line`
A:4595 vs C:486: joiner UNVERIFIED equal, so no fold until the tester pins both outputs.

## 6. Proposed sub-slices (PR-free first)

| Sub-slice | Seams | Waits on | Note |
|---|---|---|---|
| **S3a** | M1, M2, M3, M4, M5, Q1 (+ folds F-d, F-e, helpers `Reply`, `misses.*`, new LC keys) = 6 seams | nothing | Every hunk is outside the open PRs' functions (K8). C hunks are in `compose()`, `_did_you_mean_per_code`, `class Answer`; #1747 touches only `_ASK_HEADERS` in C. |
| **S3b** | M6, M7, R5f = 3 seams | #1744 merge (or OI-5 (b)) | `not_found_error_message` (#1744 comment-only hunk), `output_structurer` (#1744 code hunks). |
| **S3c** | M8 = 1 seam | #1755 merge | `run_gate`. |
| **Moved out** | more line (K1), set dealer question #34 (K5 (2)), R10 (K7) | Sets slice (after #1744, plus a continuation hunk in `TA:_apply` after #1747/#1755); R10 after all three | see OI-1, OI-2 |

## 7. Red tests (S3a; tester writes red first, gate on = `monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)`)

| Test | Assertion |
|---|---|
| `test_misses_not_found` | `misses.not_found(["A"]).text == "Couldn't find A."`; `(["A","B"])` -> `Couldn't find A, B.`; custom join honoured; `Reply.question is None and Reply.offer is None` |
| `test_stock_miss_line_uses_misses` | gate on `stock.miss_line(["X"]) == misses.not_found(["X"]).text`; gate off still `Couldn't find: X.` |
| `test_compose_unplaced_line_gate_on` | `_did_you_mean_per_code` with one unsuggested token: text ends `Couldn't find X.`; with suggestions + one plain token: `\n\nCouldn't find X.` sits above the closing line |
| `test_compose_unplaced_line_gate_off_unchanged` | same inputs gate off: `I could not find X.` exactly; `Answer.miss_text == ""` |
| `test_answer_miss_text_stamped_gate_on_only` | gate on `compose()` with an unplaced token sets `miss_text == "Couldn't find X."`; gate off leaves the default |
| `test_dealer_eta_miss_reads_miss_text_gate_on` | `_dealer_names_the_miss` over an answer whose text ends with `miss_text` and refer: compose's line removed, `Couldn't find X.` placed before `Please refer to your salesman.`; no double miss line |
| `test_unsuggested_line_gate_on` / `_gate_off` | A:`_unsuggested_line(["X"])`: `\n\nCouldn't find X.` / `\n\nI could not find X.` |
| `test_not_understood_line_gate_on` | `Couldn't find "pink" and "xx".`; gate off keeps the colon |
| `test_what_you_want_missing_gate_on` | `what_you_want_reply("taps", [], missing="pink (finish)", team="sales")` ends `Couldn't find pink (finish). Would you like me to escalate to sales team?` |
| `test_so_status_miss_gate_on` | `po_ref_reply_text` miss: `Couldn't find PO ref X.`; `reply_text` with two missing: `Couldn't find A or B.`; gate off unchanged |
| `test_miss_sentences_translate_ms_zh` | `localizer.reply` (ms, zh) of every S3a gate-on miss sentence starts with the LC:359 translation (`Tidak dapat menemui` / `找不到`) |
| `test_matched_these_keys_translate` (S3b) | each new key (section 2) translates in ms and zh; gate-off sentences (`But no incoming matched these.`) are NOT translated (gate-off byte identity) |
| `test_multi_domain_drops_bare_quantity_question_gate_on` | compose over a promotion envelope with an attachment + a `stock_availability` envelope whose item needs a quantity (cp0-b1f73c72c0 shape): text has no `How many units`; starts `I have attached the file(s) below.` |
| `test_multi_domain_opens_no_stock_task_gate_on` | `_stock_ask_reply` with plan domains `["inventory","promotion"]` and a `needs_quantity` block: no `stock_qty` task with `opened_at_turn == turn_no` on `state_out.focus.tasks`; an answered older task is still closed as gate off |
| `test_multi_domain_gate_off_unchanged` | same inputs gate off: text starts `How many units do you need?\n\n`; task opened |
| `test_stock_only_question_unchanged_gate_on` | single-domain stock turn: named question / `How many units for each?` + `N. CODE - ` lines identical in both gate states |
| `test_sibling_picker_unchanged_gate_on` | `build_suggest_offer` incoming sibling arm (A:4864-4876 shape): identical in both gate states |
| `test_offer_readers_input_unchanged_gate_on` (property) | for each S3a gate-on fixture with an offer: `dealer_stock.refers_to_salesman`, `order_list._OFFER_SENTENCE.search`, `TC.MARKERS` first-hit index and `with_alternative_lines` give the same result as on the gate-off text |
| `test_s3_output_clean` (property) | no `I could not find`, no `Couldn't find:`, no `master_products` / `master products` / `inventory` word in any S3 gate-on fixture output; no UUID; no " · " |
| `test_composers_import_boundary` (extend S0) | `K/misses.py` imports nothing from engine / head / tail / dialogue / turn.compose / lanes |

S3b adds: `test_breakdown_miss_word_gate_on` (cp0-75e516bd40 shape -> `But no product matched these.`, inventory ->
`But no stock matched these.`, incoming unchanged), `test_not_found_colon_and_verb_gate_on` (A:4289/4392/4411/4417),
`test_silent_company_noun_gate_on`. S3c adds `test_gate_dropped_line_gate_on`.

Existing tests that pin gate-off text (stay green): `Couldn't find: ` (S1 section 7 list), `I could not find`
(test_rearch_* compose tests, test_multi_ask_runner.py, so_status tests; tester greps and lists them), `How many units
do you need?` (test_stock_ask_ht26_family_pick.py, `_r8_open_question.py`, `_r9_open_question_kinds.py`).

## 7a. Lead decisions 17 Oct (override sections 1-8 where they disagree)

- Build S3a now: M1-M5 + Q1 + the two folds. Q1 is NARROWED (risk 1): the bare quantity question is dropped only
  when the stock leg was carried from an earlier turn, read from the parser's verdict (an inventory entity with
  `current_message` not true), never from the message words; a stock leg asked in the current message keeps it.
- M6 lands with S3a (OI-5 (b)); M7 waits for #1744; S3c waits for #1755.
- OI-1: asked of crew (two owner rulings meet: 26 Sep "no paging" and the 17 Oct CP0 "reply to see more", Q4). The
  "more" line moves to the Sets slice either way.
- OI-2 (a): R10 leaves this lane for its own small lane after #1744 / #1747 / #1755 merge.
- OI-3 (a), OI-4 (a) as recommended.
- Reviewer to confirm M1's `turn/compose.py` hunk is outside every function #1747 changes.

## 8. Open items for crew (max 5)

- **OI-1 The "more" line has no continuation (K1).** (a) Move `more_line` and the five-trailer convergence to the Sets
  slice, with a continuation that reads the parser's `continuation` flag (no word rule) in `TA:_apply` once #1747 and
  #1755 merge, and set paging after #1744. (b) Print it in S3 on lists without a pager (a reply "more" then closes
  the list: an untrue line). (c) Ask the owner whether the 26 Sep "no paging" ruling still stands next to R9.
  Recommend (a); (c) in the same ask if crew is unsure R9 overrides 26 Sep.
- **OI-2 R10 (tier question order) is control flow (K7).** (a) Take R10 out of the composer lane into its own small
  lane after #1744 / #1747 / #1755 merge (it lives in `_run_stages_body`). (b) Keep it in S3 and wait. Recommend (a).
- **OI-3 Scope of "one miss grammar" (K4).** (a) Token misses only; fact lines ("No stock for X.", "No {label} found
  for X.") stay and S6 brings `No <attr> on record for <product>.`. (b) Fold fact lines into `Couldn't find {fact} for
  X.` too. Recommend (a): (b) says a found product was not found, against R4.
- **OI-4 Bare question on a single-domain stock turn with no product named (K5 (3)).** (a) Leave it (UNVERIFIED that a
  live parse reaches it). (b) Gate on, drop it like Q1 (no task stands behind it). Recommend (a) until a TRAIN or
  replay case shows it; trigger: one recorded reply with the bare question and no task.
- **OI-5 S3b timing.** #1744 touches `not_found_error_message` with two comment lines only (K8), and #1744 is parked.
  (a) Wait for #1744 to merge. (b) Land M6 now (400+ lines from that hunk) and hold only M7 (about 15 lines from it).
  Recommend (b): the cp0-75e516bd40 R5 leak is the visible defect and M6 merges cleanly; R5f (`output_structurer`)
  waits either way.

## 9. Risks (top 3)

| # | Risk | Mitigation |
|---|---|---|
| R1 | Q1 drops a question a dealer actually wanted (a promo + stock ask in ONE message). | The stock leg was asked; the rule then hides it. Tester adds a single-message promo + stock fixture; if crew wants the question there, narrow Q1 to a carried inventory domain (focus domain not named in this message), a fact the verdict holds. |
| R2 | M1's `miss_text` exact-suffix strip misses when a later pass (localizer, scope block) edits the tail before `_dealer_names_the_miss` runs, leaving two miss lines. | Fallback to `_COMPOSE_MISS` stays for gate off; gate-on test runs the real order of passes (E:7022/7204 call sites unchanged); property test "one miss line". |
| R3 | New LC keys for "But no {word} matched these." change ms/zh output gate OFF too (the Localizer is not gated), turning today's English into ms/zh for every audience. | Intended (fixes an existing gap) but it breaks AC-G1 byte identity for ms/zh replies. Either add the keys only in S3b with the gate-on wording ("stock"/"product"), keeping today's "inventory"/"master products" forms uncatalogued, or record the ms/zh diffs as intended in the replay review. Crew to confirm at the S3a red commit. |
