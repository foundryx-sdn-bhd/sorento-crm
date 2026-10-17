# CHAT-COMPOSER seam design: other capped lists + "more" (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md` (slice "Lists", after "Sets"). Stacks on PR #1771 (Sets, head
ae10ea38b); branch `crew/chat-composer-lists`. Owner rule 17 Oct (crew 18 Oct): every capped list ends
`Reply "more" to see the next N.`, one WhatsApp message (3900 chars, `composers/sets.py:6`), behind `CHAT_COMPOSER_ENABLED`.
Binding rules from `chat-composer-seam-design-sets.md` (gate pattern, gate off byte-identical, ms/zh in `LANE_LABELS`
gate-on only, no word rule, "more" continues only the LAST reply's list, "That is all N." past the end) apply unchanged.
Paths: B = `sorento_crm_backend/app/services/chatbot`, M = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.

## 0. Map (18 Oct, head ae10ea38b)

| List | Writer, cap, tail today | Rows held by | Carry for "more" today |
|---|---|---|---|
| L1 code-lookup cards (es-925fab8179, 4330 chars) | B/lanes/business/fetch.py:3621-3633 `output_structurer` plain loop, no cap; up to 20 rows from `entity_resolver._prefix_probe_product` (`PREFIX_LIMIT` 20, entity_resolver.py:2055) | backend (`out["answers"]`, fetch.py:3868) | none: `_set_page` runs only with a predicate (fetch.py:3734, 3803); `focus.set_page` = None (engine.py:7135) |
| L2 outstanding By customer / By product | M:2255 `_outstanding_capped_group`, `OUTSTANDING_GROUP_CAP` 20 (M:2228), tail `... and N more customers` (M:2280) | MCP only; backend gets text + `has_result` (fetch.py:2494-2523) | pending `outstanding_detail` (fetch.py:2527-2538), no offset |
| L3 ref_status PO/PR card (staff) | B/ref_status.py:61 `_lines_text`, `MAX_LINES` 10 (:31), tail `and N more lines` (:65), English only | backend (`.all()`, :97-101, :134-138) | none |
| L4 stock pick "which one?" | B/turn/task.py:865 `pick_question`, `MAX_SLOTS` 20 (:209), tail `and N others, reply with the full code.` (:894); owner PICKER-NO-CAP 2 Oct (:890) | backend group in memory; pending `product_pick` keeps 20 options + count (engine.py:7783-7787) | pending pick, first 20 only |
| L5 "Not checked: A, B ... and N others" | B/turn/task.py:213-219 `_named`, `MAX_NAMED` 10 (:210), used :389 (task over) | `focus.tasks.not_checked` (all) | n/a, closing sentence |
| L6 find "Showing X of Y" | M:1652 `_find_envelope` | MCP | UNREACHABLE from the chatbot since #1613 (35008d5a67): only `contracts.py:112` map entry left |

## Lead decisions 18 Oct (no new rule; override anything below that disagrees)

- LD1. L1 joins now: a code-lookup multi-match is a list like a set page. Gate on: the cards are cut on a card boundary to
  one message, the header gains the range (`Here are the matching products (13, showing 1 to 9).`, the Sets phrase), the
  page ends `Reply "more" to see the next N.`, and a carry of the matched product ids lets "more" print the rest; past
  the end "That is all N.". Gate off untouched.
- LD2. L6 is dropped from this slice (unreachable; see map). Revisit only if a chatbot path to `crm_find` returns.

## Crew answers 18 Oct (bind; per the owner's 17 Oct rule "every capped list ends with the more line")

- Q1 (a): optional `group_offset` tool argument, sent gate-on only; the presenter pages only when the argument is present,
  so gate off and other MCP clients (incl. the analytics connector) stay unchanged.
- Q2 (a): page the picker (options 21-40 numbered on, any shown number picks); keep "or reply with the full code" after
  the more line.
- Q3 (a): not a list, leave it; catalogue " and N others" for ms/zh.
- Q4 (a): page 10 lines per "more"; several cut cards: continue the first, then the next.
- Q5 (a): drop find "Showing X of Y" (unreachable since #1613).

## Crew answers 18 Oct, round 2 (bind; override the designer sections below)

- OL-1 (a): L4 stock pick DROPPED (nothing is cut in practice, KL3). Section 4 is not built.
- OL-4 (a): never a false count. When the L1 list holds the probe's cap (lead rule LD3 below), no total is printed
  anywhere and the last page ends `Those are the first 20 matches. Send more of the code to narrow it.` (ms/zh
  catalogued in LANE_LABELS, gate on).
- OL-2 (a): L1 pages 2+ reuse the Sets header and the end reuses `That is all N.` (DL2 as written), except under LD3.
- FYI: the MCP deploys with the backend before any flip; security-reviewer joins this slice.

**LD3 (lead, applying OL-4).** "Capped" = the L1 list holds `entity_resolver.PREFIX_LIMIT` (20) or more distinct codes
(the probe may have stopped there; exactly 20 real matches also counts, which costs at most a needless narrowing hint and
never a false count). The carry records `"capped": True`. Under capped: page 1 range `(showing 1 to 9)` (RANGE without
`{total}`, its own catalogue key), pages 2+ the Sets header with the same total-less range, no `That is all N.`; the
last page (no more line) ends with the narrow sentence; a "more" past the end replies the narrow sentence alone. The more
line itself is unchanged (`Reply "more" to see the next N.`, N is known from the carried ids).

**Tester ambiguities resolved (lead, 18 Oct).** Capped keys: `"capped": True` (and `"code_list": True`) on both the carry
and the predicate `page_the_set` builds; the total-less range is its own catalogue key `(showing {first} to {last})`.
The narrow sentence sits where the more line would (the last line above the `_Data last updated_` footer). `ref_cards`
lists only the cut cards, in printed order. A bool `group_offset` is treated as absent.

## CARD DELTA (crew ask 18 Oct, answered above)

- Q1 L2 outstanding: (a) backend sends an optional `group_offset` tool argument only gate-on; the presenter prints the
  more line and the next 20 groups only when the argument is present, so gate off and other MCP clients are unchanged;
  needs the MCP deploy with the backend. (b) leave out: the existing "Reply 1 for the ... list" offer (M:2440) already
  reaches every row. Recommend (a): owner rule names every capped list; the arg keeps gate off identical.
- Q2 L4 stock pick: (a) page the picker: tail `Reply "more" to see the next N.`, "more" prints options 21-40 numbered
  on, the pending pick grows so any shown number picks; (b) keep as is (owner PICKER-NO-CAP prints every carried option;
  "reply with the full code" already serves the rest). Recommend (a), keeping "or reply with the full code" after the
  more line since a dealer who knows the code should not page.
- Q3 L5 "Not checked ... and N others": (a) not a list (closing sentence, task over, nothing to page): leave, only
  catalogue " and N others" for ms/zh (stays English today); (b) list every name up to the message budget.
  Recommend (a).
- Q4 L3 ref_status (staff-only PO/PR card): (a) page 10 lines per "more"; with several cut cards, "more" continues the
  first cut card, the next "more" the next one; (b) leave out (staff-only internal card). Recommend (a).
- Q5 L6 find: confirm LD2 (drop). Recommend drop.

## Designer pass 18 Oct: L1 to L5 seams (coder-facing)

Read-only design on HEAD befc4ac53f. F = `B/lanes/business/fetch.py`, BI = `B/lanes/business/__init__.py`,
TR = `B/turn_runtime.py`, AP = `B/turn/apply.py`, E = `B/engine.py`, TK = `B/turn/task.py`, RS = `B/ref_status.py`,
LC = `B/label_catalog.py`, K = `B/composers/`, R = `sorento_crm_backend/app/api/v1/order_management/orders.py`,
MC = `sorento_crm_mcp/sorento_crm_mcp/catalog.py`, ER = `sorento_crm_backend/app/services/entity_resolver.py`.

### Findings that shape the design (code reading only, no DB read)

| # | Finding | Effect |
|---|---|---|
| KL1 | A product token places at most `PREFIX_LIMIT` 20 rows (tier 2, ER:2080/2089, balanced cap ER:5547-5551) or one exact hit plus up to 20 prefix siblings (variant expansion ER:5501-5516). | L1's list is at most 21 codes, all fetched on page 1: "more" pages ids already in hand. A family over 20 still shows its first 20 only (RL1). |
| KL2 | `page_the_set` recounts through `resolve_product_set`, which counts and lists DISTINCT VARIANT FAMILIES (`coalesce(parent.product_code, product_code)`, product_predicate_service.py:1004, :1073, :1108-1112). | 13 variants of one parent would recount as 1. L1 never reuses the recount: a `code_list` branch slices the carried ids. |
| KL3 | L4 is unreachable today: a family pick exists only for a token with no exact code (TK:952-956), i.e. tier 2, so its group is at most 20 = `MAX_SLOTS` (TK:209); `count = len(group)` (TK:997), so the tail (TK:893-895) prints only for rows lacking a product id or label. `PLAN-picker-no-cap-2oct.md:37`: "in practice nothing is cut". | Options 21-40 exist nowhere (not in the block, not in the resolver). Paging them is resolver work: crew rule OL-1. |
| KL4 | MCP tool args compile from `ToolSpec.query_params` (`server.py:1640-1700`); `present_response(tool_name, raw)` sees only the route BODY (`server.py:1781-1784`). | The presenter can read `group_offset` only as a route echo: the `detail` / `location_token` / `so_refused` precedent (R:1606-1631, R:1757-1764) and the `brand_ids` test pattern (`sorento_crm_mcp/tests/test_outstanding_brand_arg.py`). |
| KL5 | Open #1721 (CHAT-FIX PR-B) inserts a hunk in `AP._answer_pending` at AP:733, four lines above the outstanding call (AP:737); its R / MC / M hunks are sales-report and report_ask only. | L2's apply seam lives in `_answer_outstanding` (no open PR) plus one call line at AP:737 (OL-3). |
| KL6 | `envelope_of` copies `set_window` gate on (TR:5788); E reads `envelopes[0]["set_window"]` (E:7022) before the carry chain (E:7056-7135). | One engine arm lets the lane that printed the line write its own carry into `set_window["carry"]` (Sets D2: the writer that knows the facts). L1, L2, L3 share it. |

### Designer decisions (lead to check)

- DL1 (G1) One engine arm, first in the chain (before `if paged:` at E:7056): gate on and `window.get("carry")` a dict ->
  `state_out.focus.set_page = {"set_key": {**carry, "domain": plan.fetch[0].domain}}`; else the chain runs as today. The
  carry rides the existing `focus.set_page` slot, so the one-turn life (AP:3681), the domain drop (CS:63-65),
  `with_set_more` (TR:1380, unchanged) and the AP set_continue path (AP:3722-3746, unchanged) apply as they are. Three key
  families: `code_list` (L1), `report_groups` (L2), `ref_cards` (L3).
- DL2 L1 page 1 is the plain intro with the range (LD1); pages 2+ and the end reuse the Sets path unchanged:
  `Here's what you want: matching products (13, showing 10 to 13)` and (runner TR:4059-4085)
  `Here's what you want: matching products (13). That is all 13.` The alternative (plain intro on every page) costs a gated
  header switch in the F set block plus a runner exhausted branch (OL-2).
- DL3 L1's range is localized with one fill-only key `({total}, showing {first} to {last})` inside the contact's localized
  intro. The Sets header range stays English, as today.
- DL4 L2: one `group_offset` pages every second group of the report together (SO and DO blocks share it); the more line
  counts the longest group's next page; page 2+ re-renders the report (header, counts, By location) with the group slice;
  no range on the group heading (it stays the catalogued label line).
- DL5 L2 "more" is a REFINE with an offset inside `_answer_outstanding`: the open offer's own filters settle the subject
  (D10), so the re-run cannot widen. The offer prints on every page; "1" / "2" / "both" still pick the detail lists.
- DL6 L3 page 1 keeps today's cards byte-identical (trailers too) and adds ONE more line at the end, counting the first cut
  card's next 10. A continuation page is one card: head + range + 10 lines (+ `and N more lines`) + the more line while a
  page is left. English only (the card is English-only staff copy, RS:1-10).
- DL7 L4 recommended dropped (KL3); a conditional seam is in section 4 if crew keeps Q2 (a).
- DL8 L5 is a Localizer rule on the `{names}` value, keyed on a LANE_LABELS entry, so it is gate-on by construction.

### 1. L1 code-lookup list

Carry (F writes, G1 arms): `{"code_list": True, "set_noun": "matching products", "code_ids": [{"code": "SRTWC286-SH",
"ids": ["<uuid>", "<twin uuid>"]}, ...], "total": 13, "shown": 9}` + `domain`. At most 21 entries (KL1), so the "never
two hundred uuids" rule (TR:4377) holds.

| # | Seam | file:line | Gate on | Gate off |
|---|---|---|---|---|
| L1-a | K/sets.py: `RANGE = "({total}, showing {first} to {last})"`; `ranged(sentence, fragment)` puts `" " + fragment` (no space before a full-width `（`) ahead of the final `.` / `。` / `!` / `?`, else appends | new | used by L1-b | not imported |
| L1-b | F new `_code_list_page(...)` beside `_set_page` (F:612). Fires only when: tool in `CODE_LIST_TOOLS = {"crm_master_products_list", *SET_ROW_LIMIT} - {"crm_marketing_promotion_products_list"}` (promotion out: entitlement, TR:4579); `not plain_lines and not stock_ask_render` (an intro exists); 2+ distinct `_row_code`s over `row_items`, CONTIGUOUS in row order; every code maps to 1+ `entity_type == "product"` uuid in `ctx["entities"]` (by `code` / `canonical_code`, casefolded; twins grouped). Calls `_set_page` on `msg[rows_start:]` with every offset shifted by `-rows_start`, `question_at=None`, `other_brands=""`, `shown=total=len(codes)`, `offset=0`, `cap=answer.SET_LIST_MAX`, footer built as F:3799, `build_header=lambda n: intro if n >= total else ranged(lz.text(e["intro"]), lz.fill(RANGE, total=f"{total:,}", first="1", last=str(n)))`, `intro = msg[:rows_start].strip()` (today's bytes), `lz = ctx.get("localizer") or IDENTITY`. `_set_page` returns None when all fits: nothing changes (no range, no line, no window). | F:612 new; call after F:3846, before F:3848 | cut page; window `{first, last, total, next, carry}` written into the existing `set_window` / `page_items` locals (F:3731-3733), so `answers` (F:3868-3874) and `out["set_window"]` (F:3876) need no hunk | call under `if composers.enabled() and predicate is None` |
| L1-c | TR `page_the_set`, new branch after the miss_list one (TR:4592): `if key.get("code_list") and _composers_on():` no DB read; `offset = shown`, `limit = page_size(size, SET_LIST_MAX)`, `page = code_ids[offset:offset+limit]`, `page_ids` their ids in order; predicate `{"require": {}, "qualifying_total": total, "truncated": False, "unrecognized_terms": [], "class_labels": [], "set_noun": key["set_noun"], "code_list": True}`, `offset` when > 0, `exhausted` when `offset >= total`, `next_carry = {"set_key": {**key, "shown": min(offset + len(page), total) if page else offset}}` | TR:4592 | page 2 through the unchanged runner (TR:3787-3812 entities from `page_ids`), F set block, E `paged` branch (E:7056-7062 clamps `shown` to the printed `last`) | `_composers_on()` |
| L1-d | G1 (DL1) | E:7056 | arms | gated |
| L1-e | LC `LANE_LABELS` + `DIRECT_ONLY` (LC:801): `RANGE` (ms / zh under the table) | LC:579-591 | localized | key filtered (LC:878-879) |

Sentences: `Here are the matching products (13, showing 1 to 9).`; any intro takes the range the same way (`Stock details
found for the requested products (13, showing 1 to 9).`). RANGE ms `({total}, menunjukkan {first} hingga {last})`, zh
`（共 {total} 个，显示第 {first} 至 {last} 个）`. The more line and `That is all {total}.` are already in LANE_LABELS.

### 2. L2 outstanding By customer / By product

Carry: `{"report_groups": True, "total": 45, "shown": 20}` + `domain`. The SUBJECT is not carried: it is the open
`outstanding_detail` pending's `filters` (F:2525-2538), settled as an answer settles it (`AP._settle_question_subject`).

| # | Seam | file:line | Gate on | Gate off / other clients |
|---|---|---|---|---|
| L2-a | R: `group_offset: Optional[int] = Query(None, ge=0)`, ECHO ONLY (description as `detail`'s); `if group_offset is not None: body["group_offset"] = group_offset` after R:1764 | R:1606-1631, R:1764 | echoed | absent = today's body |
| L2-b | MC: `"group_offset"` appended to the `crm_outstanding_report` query tuple; description text unchanged | MC:797 | forwarded | optional; nobody else sends it |
| L2-c | M `_outstanding_capped_group(..., offset=None)` (M:2255): None = today's body; an int prints `rows[offset:offset+CAP]` and no `... and N more` trailer. `_outstanding_so_block` / `_outstanding_do_block` (M:2298, M:2327) take `offset=None` and pass it; an empty slice on a later page omits its heading. `_outstanding_report` (M:2391-2447) reads `report.get("group_offset")` (int >= 0, else None); with it, between the blocks and the offer: `Reply "more" to see the next {n:,}.`, n = min(CAP, longest second group - offset - CAP), only when > 0; offset > 0 and no row left in any group: header lines, `That is all {longest:,}.`, then the offer (blocks skipped). Both literals pinned equal to the backend's (`SO_NOT_ENABLED_MESSAGE` precedent, M:2170 / F:499) | M:2255-2447 | pages | `offset` None everywhere = byte-identical |
| L2-d | M `_outstanding_envelope` (M:2507-2516): with `group_offset` present add `"group_window": {"offset": o, "shown": k, "total": longest}` | M:2507 | data | key absent |
| L2-e | F `entity_ids_transformer` outstanding block, after F:926-928: `if composers.enabled() and detail_pick not in ("so", "do", "both"): out["group_offset"] = max(0, int(semantic_input.get("outstanding_group_offset") or 0))` (page 1 sends 0, so its line prints) | F:928 | sent | never sent |
| L2-f | F `_outstanding_report_output` return (F:2539-2565): gate on and `envelope.get("group_window")` a dict -> `"set_window": {"first": o+1, "last": o+k, "total": t, "next": n, "carry": {"report_groups": True, "total": t, "shown": o+k if k else o}}`, only when `n > 0 or o > 0` (the last page keeps a carry, so a further "more" says the end) | F:2539 | window | no key |
| L2-g | TR `outstanding_carry`, after TR:3437-3444: `if isinstance(answered.get("group_offset"), int): out["outstanding_group_offset"] = answered["group_offset"]` | TR:3444 | rides to F | key exists only gate on |
| L2-h | AP `_answer_outstanding(pending, decision, focus, trace, *, more: int \| None = None)` (AP:544), first statement: `if more is not None and pending.kind == "outstanding_detail":` the REFINE effects of AP:590-614 with `scope = decide._stored_scope(pending)` (decide.py:348) for `decision.scope`, plus `focus.set_page = None`, `trace.outstanding = {"kind": pending.kind, "scope": scope, "detail": None, "group_offset": more}`, rule `outstanding_more`; `return focus, pending, None, True`. Call site AP:737: `more=_report_more(verdict, state.focus)` = the carry's `shown` when gate on, `verdict.get(SET_CONTINUE_KEY) is True` (TR:1553, set by `with_set_more`) and `set_key.report_groups`, else None. AP:3820-3827 then stamps `group_offset` on the spec, unchanged | AP:544, AP:737 | pages | `more` None (KL5) |
| L2-i | G1 | E:7056 | arms | gated |

Deploy: MCP + backend BEFORE the flip (a gate-on page 1 always sends `group_offset`; an old MCP is assumed to reject an
unknown argument, UNVERIFIED). Offer coexistence: `_outstanding_offer_from_text` (F:2525) re-arms the offer from every
page with `_outstanding_filters_from_ctx`; a detail pick never carries `group_offset` (L2-e).

### 3. L3 ref_status PO / PR cards (staff)

Carry: `{"ref_cards": [{"kind": "po", "id": "<uuid>", "lines": 25}, {"kind": "pr", "id": "<uuid>", "lines": 14}],
"card": 0, "shown": 10, "total": 25}` + `domain`. It names the NEXT page (card, from line `shown`); past the last card it
stays at `shown == total`, so a further "more" gets the end.

| # | Seam | file:line | Gate on | Gate off |
|---|---|---|---|---|
| L3-a | RS `RefLookup.cut: list[dict]` (default empty), one entry per card with `len(lines) > MAX_LINES`, card order | RS:35-38, :94-106, :134-147 | data | text unchanged |
| L3-b | RS `card_page(db, ref, offset) -> str \| None`: re-read the PO / PR by id (None when gone); head as RS:103 / RS:142 plus ` ({total:,}, showing {offset+1} to {last})`; `lines[offset:offset+MAX_LINES]`; `and N more lines` when lines remain; `offset >= total` -> `{head} ({total:,}). That is all {total:,}.`. RS `next_ref(carry) -> dict`: (same card, shown + 10), else (next card, `MAX_LINES`), else unchanged at the end | RS new | used by L3-c | not called |
| L3-c | BI ref arm: condition `(po_numbers or pr_numbers or ref_page)`, `ref_page = parse_output.get("ref_page") if composers.enabled() else None`. With `ref_page`: audience + grant checked as today; refused -> `_error_fragment("reference page refused", outcome="not_found")` (BI:1039), never the fall-through (the page turn has no entity: the domain tool would run unfiltered); else `card_page` + the more line when `next_ref` leaves a page, window carry `next_ref(...)`. Page 1: today's `"\n\n".join(cards)`, then, gate on with any `cut`, the more line for `min(MAX_LINES, cut[0]["lines"] - MAX_LINES)` and `fragment["fetch"]["set_window"] = {first 1, last 10, total, next, carry {card 0, shown 10}}` | BI:1533-1552 | pages | `ref_page` None, no line, no window |
| L3-d | TR runner: TR:3711 `if isinstance(carry, dict) and not _lane_paged(carry):` (`ref_cards` / `report_groups` skip `page_the_set`; gated); after TR:3736 a ref_cards carry adds `lane_out["ref_page"] = carry["set_key"]`. `ref_staff` comes from `is_staff_profile` (TR:3864) as on page 1 | TR:3711, TR:3736 | the page reaches the arm | `_lane_paged` False |
| L3-e | G1 (runs before `paged`, so the ref page keeps its own carry) | E:7056 | arms | gated |

security-reviewer joins: L3 re-reads staff-only rows by id on a later turn (grant re-checked per page); L2 adds a route parameter.

### 4. L4 stock pick (only if crew keeps Q2 (a) after KL3)

Seam sketch: ER `_prefix_probe_product(db, token, *, offset=0, limit=PREFIX_LIMIT)` (keyword-only, defaults byte-identical)
plus a family count, which becomes the picker's `count`. "more" under an open `product_pick` is read in
`AP._answer_pending`'s `_stock_pick` branch (AP:745), not via `focus.set_page`: the options must grow on the SAME pending
(two carriers of one list would disagree). Options 21-40 append to `pending.options` and the group's `positions`; the
question re-asks through `picks_question` (`start` kept); tail `Reply "more" to see the next N.` then `Or reply with the
full code.` (new, ms/zh). The signal reuses `with_set_more`'s guard factored as `is_more(verdict)`. Cost: a shared
resolver change and an `_answer_pending` hunk beside #1721. Trigger to build: `PREFIX_LIMIT` above `MAX_SLOTS`, or a TRAIN
picker printing the tail.

### 5. L5 " and N others"

| # | Seam | file:line | Gate on | Gate off |
|---|---|---|---|---|
| L5-a | LC `LANE_LABELS` + `DIRECT_ONLY`: `" and {n} others"`, ms `" dan {n} lagi"`, zh `" 等另外 {n} 个"` | LC:579-591, LC:801 | in table | filtered (LC:878-879) |
| L5-b | LC `Localizer._names(value)`: when the table holds the key and `value` fullmatches `(.*) and (\d[\d,]*) others`, the tail becomes the entry with `{n}`; applied to `values["names"]` in `_inline_line` (LC:979) and `_text` (LC:1029) | LC:975-990, LC:1029 | `未检查：A, ..., J 等另外 3 个。` | no key: value verbatim |

How the fragment gets localized: `Not checked: {names}.` is INLINE, so the final pass (`E._localize_result` ->
`Localizer.reply`, E:3083) matches it and fills `{names}` with the English value verbatim; L5-b rewrites only that value's
`and N others` tail before the fill. TK:213-219 and TK:389 do not change; `_join`'s own " and " (TK:231-234) stays English
inside every `{names}` (existing, out of scope).

### 6. Gate-off identity (measured by reading)

| List | Every new branch sits under | Unchanged gate off |
|---|---|---|
| L1 | `composers.enabled()` (F), `_composers_on()` (TR), G1 gated, LC key filtered | F text, `answers`, E chain, page_the_set |
| L2 | the backend never sends `group_offset`; M branches only on the echo; AP `more` None; TR key absent | report text and envelope for every MCP client (the schema gains one optional argument) |
| L3 | `ref_page` None; `cut` is data only; runner `_lane_paged` gated | card bytes, fragment keys |
| L5 | the LC key exists only gate on | `reply()` output |

### 7. Red tests (test(red) commit first; fixtures and helpers as `test_composers_sets_*.py`)

`tests/chatbot/test_composers_lists_l1.py`
- `test_gate_off_code_list_golden` | `output_structurer`, 13 product cards, no predicate | today's literal, no `set_window`
- `test_cut_to_one_message` | same, gate on | len <= 3900; line 1 `Here are the matching products (13, showing 1 to 9).`; `Reply "more" to see the next 4.` above `_Data last updated`; `answers` = 9 rows
- `test_fits_is_unchanged` | 5 short cards, gate on | equals the gate-off response, no `set_window`
- `test_window_carry_shape` | as above | `set_window["carry"] == {"code_list": True, "set_noun": "matching products", "code_ids": [13 in printed order], "total": 13, "shown": 9}`
- `test_twins_ride_together` | two companies' rows of one code at the cut | never split; one `code_ids` entry, both uuids
- `test_no_page_without_ids_or_contiguity` | a printed code with no product entity; codes A, B, A | unchanged
- `test_order_list_is_not_a_code_list` | orders tool rows with Product Code fields | unchanged
- `test_header_ms_zh` | ctx localizer zh / ms | `以下是匹配的产品（共 13 个，显示第 1 至 9 个）。` / `Berikut ialah produk yang sepadan (13, menunjukkan 1 hingga 9).`; gate off RANGE not in `defaults("zh")`
- `test_page_the_set_code_list` | `page_the_set(<db that raises on use>, carry shown 9 of 13)` | ids of codes 10-13, offset 9, next shown 13; shown 13 -> `exhausted`, `[]`
- `test_engine_arms_and_pages` (engine level) | turn 1 cut; turn 2 `continuation: true` | carry with domain; `Here's what you want: matching products (13, showing 10 to 13)`, first block `10.`; turn 3 `... (13). That is all 13.`; gate off `set_page` None
- `test_dealer_pick_owns_a_family` | dealer availability family | no `set_window` carry

`tests/chatbot/test_composers_lists_l2.py` (backend) + `sorento_crm_mcp/tests/test_presenters_outstanding_more.py` (MCP)
- MCP `test_no_group_offset_golden` | `_outstanding_envelope`, 45 By customer rows | today's text incl. `... and 25 more customers`, no `group_window`
- MCP `test_page_one` | `group_offset` 0 | rows 1-20, no trailer, `Reply "more" to see the next 20.` directly above `Reply 1 for the delivery order list.`; `group_window` {0, 20, 45}
- MCP `test_last_page_and_past_the_end` | offset 40; offset 60 | rows 41-45, no line; header + `That is all 45.` + offer
- MCP `test_two_blocks_share_the_offset` | SO 45 / DO 30 at 20 | SO 21-40, DO 21-30, `next 5`
- MCP `test_catalog_exposes_and_forwards_group_offset` | as `test_outstanding_brand_arg.py` | listed and forwarded
- `test_route_echoes_group_offset` | R route | body `group_offset` 20; absent when not sent
- `test_arg_gate_on_only` | `entity_ids_transformer` | gate off no key; gate on 0; 20 from `outstanding_group_offset`; detail pick no key
- `test_outstanding_carry_offset` | `outstanding_carry` | key only when `answered` has it
- `test_report_output_window` | `_outstanding_report_output` | carry `{"report_groups": True, "total": 45, "shown": 20}`; gate off no key, bytes equal
- `test_answer_outstanding_more` | `_answer_outstanding(..., more=20)` | `trace.outstanding.group_offset` 20, `detail` None, pending kept, `set_page` None, subject from pending filters; `more=None` equals today on REFINE / answer / new ask
- `test_engine_more_reruns_with_offset` (engine level, MCP stubbed) | report, "more", "1" | 2nd call `group_offset` 20 + page 1's `customer_ids`; offer re-armed; "1" sends `detail`, no `group_offset`

`tests/chatbot/test_composers_lists_l3.py`
- `test_gate_off_golden` | BI ref arm, PO of 25 lines | today's card, no line, no `set_window`
- `test_page_one_more_line` | gate on, PO 25 + PR 14 | cards unchanged + `Reply "more" to see the next 10.`; carry card 0 shown 10
- `test_card_page` | `card_page(db, po, 10)` | `*Purchase order PO-1* - Supplier Bhd (25, showing 11 to 20)`, 10 lines, `and 5 more lines`
- `test_next_ref_moves_to_the_next_card` | `next_ref` card 0 shown 20 of 25 | (0, 25), then (1, 10) with `next 4`; the end stays
- `test_past_the_end` | last card shown 14 of 14 | `*Purchase request PR-77* - X (14). That is all 14.`
- `test_page_refused_never_falls_through` | `ref_page`, no grant / not staff | not_found; MCP stub never called
- `test_runner_skips_page_the_set` | runner, ref_cards carry (spy raises) | `lane_out["ref_page"]` set
- `test_engine_walks_the_cards` (engine level) | four "more" turns | PO 11-20; 21-25 + `next 4`; PR 11-14; `That is all 14.`

`tests/chatbot/test_composers_lists_l5.py`
- `test_gate_off_names_tail_verbatim` | zh `Localizer.reply("Not checked: A, ..., J and 3 others.")` | today's literal
- `test_names_tail_ms_zh` | gate on | ` dan 3 lagi` / ` 等另外 3 个` inside the localized sentence
- `test_no_tail_untouched` | `Couldn't find A and B.` | unchanged
- `test_catalogue_entries` | LC | RANGE and the names tail in `LANE_LABELS` and `DIRECT_ONLY`, `tokens_match`, absent gate off

Kill-proof: drop L1's contiguity check (`no_page...` red), make `more=None` page (`answer_outstanding_more` red), let a
refused page fall through (`page_refused...` red).

### 8. Risks

| # | Risk | Mitigation |
|---|---|---|
| RL1 | L1's total is the resolver's (KL1): a 35-code family prints `(20, showing 1 to 9)`; today's intro claims no count | owner call; trigger to fix: probe `PREFIX_LIMIT + 1` |
| RL2 | L1 page 2 runs on the "more" verdict: a spec card (`include_specs`, F:1254-1258) or asked attribute may render differently (UNVERIFIED; same as Sets pages) | engine test compares a page-2 block with page 1's; if red, the carry adds `intent_hint` / `requested_attributes` |
| RL3 | A cut list in the 2nd section of a multi-domain reply prints a line with no carry (G1 reads `envelopes[0]`, as Sets) | single-domain tests; trigger: a TRAIN fan-out with a cut list |
| RL4 | The parser may carry `order_status: "outstanding"` on a bare "more"; `with_set_more` refuses any `_OWN_ASK_FIELDS` value (TR:1363-1377) | engine test with the field; if red, crew rule on a field equal to the carried status |
| RL5 | Gate-on page 1 always sends `group_offset`; an old MCP fails the call | flip after the MCP and backend deploys |
| RL6 | #1721 hunk at AP:733 beside L2-h's call line (KL5) | land after #1721 or rebase; one line |
| RL7 | L2 worst case: 2 blocks x 20 rows of 60-character names near 3,900 | MCP test on that fixture; the page stays 20 |
| RL8 | L3 re-reads staff rows by id on a later turn | grant + staff re-check per page (L3-c); security-reviewer |
| RL9 | Hunk-level overlap UNVERIFIED: BI ref arm (#1761, #1742, #1721, #1648, #1582, #1572, #1570 touch BI) and TR runner (#1772, #1755, #1761, #1741, #1648, #1617) | `gh pr diff` at the pre-PR gate |

### 9. Build order and open items

L1 (lands G1 and `ranged`, which L2 / L3 reuse) -> L5 (LC only) -> L3 (backend only) -> L2 (MCP + route + backend; MCP
deploys first) -> L4 only on a crew rule. Open: OL-1 L4 drop (KL3) or build the resolver page (section 4); OL-2 DL2 page-2
header; OL-3 L2's call line in `_answer_pending` beside #1721.

## Review round 1 (18 Oct, head 4def98907): lead rulings for the fix round (override anything above)

Reviewer (Opus) NOT READY on B1; security-reviewer PASS with one should-fix.

- LD4 (B1, applies OL-4 "never a false count"). "Capped" is decided on PRODUCT ROWS per typed token, the unit the resolver
  caps, never on distinct codes: a token's list is capped when its product uuids reach the cap the resolver applied to
  it, `max(1, PREFIX_LIMIT // n_types)` where `n_types` = distinct entity types the resolver returned for that token
  (`entity_resolver.py:5541-5551`, the per-type balanced cap), so twins across companies (20 rows = 10 codes,
  `entity_resolver.py:2058-2067`) and a two-type token (10 product rows) both count as capped; the exact hit + siblings
  case (`:5501-5516`, up to 21 rows) is capped at 20+. If the per-token entity types are not visible where L1 decides,
  fall back to `product uuids >= PREFIX_LIMIT // 2` and say so in the commit.
- LD3 amended: the narrow sentence states the number of codes actually listed, never the literal 20:
  `Those are the first {n} matches. Send more of the code to narrow it.` (ms/zh with `{n}`). The literal "20" is false
  under twins (crew FYI, same rule).
- LD5 (S2). After a cut L1 code list the cards are numbered, so a bare number picks that card: `with_set_count_from_text`
  skips a `code_list` carry exactly as it skips `report_groups`.
- LD6 (security should-fix, this slice adds the `ref_page` key). The runner sets `ref_staff = is_staff_profile(profile)`
  both ways (never trusts the verdict) and drops any `ref_page` the verdict supplies; only the carry-derived page turn
  sets it. A `ref_page` turn with no db returns the error fragment (never falls through).
- S3: `card_page` and `lookup_purchase_*` share one line-query helper, ordered by product code then line id.
- Ungated semantic key: `outstanding_group_offset` is added to the lane semantic input only gate on.
- Test gaps to close (tester, red first): B1 twins + two-type token; LD3 `{n}`; LD5 bare number picks card 2; S1 a gone
  card through `run_fetch` returns the error fragment (K2); K3 a DO block longer than the SO block; LD6 a non-staff
  verdict with `ref_staff: true` and a verdict-supplied `ref_page` are both refused.

## Review round 2 (18 Oct, head 422859b78): crew decision + lead rulings (override anything above)

- LD7 (crew decision (a), replaces LD3's capped detection and LD4). A code-lookup list that needs more than one message
  NEVER states a total: the resolver caps a token at 20 product rows or `20 // n_types` (entity_resolver.py:5541-5551) and
  the rows reaching the reply carry no token or type count, so no total it could print is known to be true. Page 1 range
  `(showing 1 to 9)`, pages 2+ the Sets header with the same total-less range, no `That is all N.` for a code list; the
  last page (no more line) ends `Those are the first {n} matches. Send more of the code to narrow it.` ({n} = codes
  listed, the last line above the footer); a "more" past the end replies that sentence alone. The capped-detection
  code (`_code_list_ids` per-token cap, the `capped` flag) goes: a code-list carry is always total-less. A list that
  fits one message is unchanged (no range, no line). (b) (plumb a resolver cap flag) only if the owner later asks for
  `That is all N.` on complete code lists.
- LD8 (security re-check should-fix). `lane_parse_output` (turn_runtime.py:1990) drops `ref_staff` and `ref_page` from
  the verdict, so every `run_fetch` caller (the tool runner AND the engine's tier-ask probe, engine.py:6422) starts
  without them; the runner then sets `ref_staff` from the profile and `ref_page` only from the carry.
