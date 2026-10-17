# CHAT-COMPOSER seam design: S2 ETA / incoming (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`, card `chat-composer-behaviour-card.md` section 4 "ETA /
incoming", UAC `chat-composer-acceptance-criteria.md` (S2 outline). Builds on `chat-composer-seam-design.md` (S0+S1),
whose "Lead decisions" block binds here too. Read-only design. Line numbers are on HEAD 59e3683b3a (= main 20c191d86
+ docs + S1 red tests). S0+S1 landed after this was drafted (1cdd9a8cf5); on that commit S1's G5 branch is
`if gate_on and origin_incoming:` at A:1024 and its `rendered_codes` stamp at A:1162, which I4 / I5 widen.
Paths: B = `sorento_crm_backend/app/services/chatbot`, P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`,
F = `B/lanes/business/fetch.py`, A = `B/lanes/business/answer.py`, C = `B/turn/compose.py`, E = `B/engine.py`,
AB = `B/answer_bridge.py`, LC = `B/label_catalog.py`, TC = `B/tail/compose.py`, K = `B/composers/`.

**S2 card changes (crew-approved):** (1) dates always dd/mm/yyyy in ETA replies; (2) one label "Container";
(3) an ETA miss says it once; (4) incoming rows under a STOCK miss use the numbered main-path block (the S1 rule,
other direction); (5) the dealer `CODE: ✅ ETA d` line is UNCHANGED.

**Binding rulings applied:** lead decisions 17 Oct (helpers land in the slice that first prints them, so `format.date`
lands here; the presenter stays the single printer of dealer sentences; no fact rebuild; gate-on branches are
`if composers.enabled()` with today's code untouched otherwise; no hunk in a function an open PR changes). Owner ruling
17 Oct: the numbered sibling-variant picker after a full answer to an exact code (A:4846-4848, text A:4864-4876,
`N. CODE - has incoming / - no incoming`) STAYS, unchanged in both gate states.

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | **Why the raw ISO date.** TRAIN cp0-0654e3e74a's rows (`*Container:*`, `*ETA:*`, `*ETA Delay:*`, `🚩 PENDING ALLOCATION`) come from `crm_incoming_stock_list` via `_incoming_list` P:892 (`_CLEARANCE_PAIRS` P:866-887 label `estimated_arrival_date` "ETA", `eta_delay_date` "ETA Delay"), printed by `_item_line` F:3382 -> `_fmt_value` F:1647 (now `K/format.value`). Every clearance date and `estimated_arrival_date` is a SQLAlchemy `Date` column (`app/models/procurement.py:140, 176-188`), returned unformatted by `IncomingStockService` (`incoming_stock_service.py:550, 705, 928`), so the JSON value is `"2026-09-27"` with no time. `_ISO_RE` F:1448 is `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}`: it REQUIRES the `T` time part (a port of the n8n node's regex, built for `last_updated_at` timestamps), so a date-only value never reaches `_fmt_ts` and prints as it came. `_fmt_ts` itself already accepts a date-only string (its own regex makes the time optional, F:1624-1626); only the gate in front of it misses. | The fix is one branch in `K/format.value` for a full-match `YYYY-MM-DD` string. No MCP change, no backend API change. |
| K2 | **It is not ETA-only.** Every tool whose backend sends a `Date` column prints raw ISO through the same printer. In-repo recorded fixtures (`tests/chatbot/replay_turns`, `fixtures`; occurrences of `*Label:* YYYY-MM-DD`, a reply can count twice): PO Date 290, Order Date 244, Actual Delivery Date 203, ETA 105, PO date 95 (xd PO rung, own printer), Valid Until 56, Start Date 48, End Date 48, Loading / ETD / ETC 36 each, ETA Delay 34, GR Date 28, Uploaded 12, SPO Date 11, CIDB Inspection 10, Expected Date 4, CIDB Approval 4, Warehouse Arrival 1, Gatepass 1. | The date scope decision (section 5) is a real choice: an unscoped fix changes order, PO, SPO/GR, promotion, certificate and file replies too. |
| K3 | `output_structurer` F:2933-3764 (which contains `_item_line` F:3382, the summary printer F:3356 and the miss-field printer F:3508) is #1744's function (hunks 3591-3715). Both printers `_item_line` calls are module level: `_field_label(loc, f)` F:2832 and `_fmt_value` (alias of `K/format.value` after S0 F-a). | Every S2 printer seam sits in those two module-level functions; `output_structurer` gets no hunk. `_fmt_value` sees only the VALUE (no key, no tool), which is why a per-tool date scope needs extra plumbing (section 5). |
| K4 | "Shipment Container" exists only in `_incoming_by_product` P:946 (`crm_incoming_stock_by_product`). `_incoming_list` P:907 says "Container"; `_incoming_shipments` P:1033 says "Container" (unkeyed). Both keyed fields carry `key: "shipping_container_number"`. No backend code reads the label "Shipment Container" (only LC:370 translates it). `Localizer.label` LC:847 translates by key when the label equals `FIELD_KEYS[key]`, and `FIELD_KEYS["shipping_container_number"] == "Container"` (LC:539). | Relabel BY KEY at print time in `_field_label`; no MCP edit, the gate-off MCP envelope stays byte-identical, and ms/zh "Kontena" / "货柜" (LC:369) apply with no new key. |
| K5 | **The two ETA-miss writers.** "Here's what you want:\n• product: X\n\nBut no incoming matched these." is `build_breakdown_msg` A:3847-3926, nested in `not_found_error_message` A:3119, which #1744 changes (hunk A:4409). "No incoming for X." is `only_other_note` in `crossdomain_render` A:1104 (now `composers.stock.none_for(primary_word, only_other)`, S1 fold F-b). The block is folded under the miss text by the ONE caller of `TC.crossdomain_compose`: `_apply_crossdomain_render` AB:1671, whose TOTAL-MISS branch (`answered=False`, called from `answer_for` AB:2141) prints `f"{miss}\n{block}"` (TC:120) or inserts the block above the first `MARKERS` hit (TC:30-37, e.g. "Would you like me to escalate"). The HIT arm (`apply_crossdomain_hit` AB:169, `answered=True`, AB:275) needs `only_other_note` (turn 858c9c54: two codes answered, a third named only inside the incoming block). TRAIN cp0-3a2cb27822 prints only the miss sentence because its stock rows are all zero, so the code lands in `nothing` (zero), never in `only_other`. | The miss sentence STAYS (it cannot be edited without a hunk in #1744's function, it carries the escalate offer, and it is S3's to reword). The crossdomain line GOES, on the total-miss fold only, through a structured stamp, not a text match. |
| K6 | The same double exists on the stock side: "But no inventory matched these.\nNo stock for X" (7 occurrences in in-repo fixtures, vs 17 for incoming), and the suggest-offer miss "No incoming stock (ETA) found for X. Related products:" (A:4871) gets the same "No incoming for X." block above its picker. | S2 covers the incoming origin (card). The stock-origin twin is one condition away: open item OI-3. |
| K7 | Under a stock miss the incoming probe is `crm_incoming_stock_list` (A:552), so the xd incoming rows already say "Container" and carry EVERY clearance field the probe returned (no requested-attribute projection on this path). Values go through `_fmt_xd_value` A:854, whose `_ISO_RE` (A:381) also requires a time, so dates print raw there too, and empty prints "-". | Change (4) reuses S1's block printer; the date fix reaches these rows through `format.value` with no extra seam. |
| K8 | `_prefix_zero_note` (AB:1619) runs on the HIT arm only (AB:270) and only for `origin_domain == "inventory"` (docstring AB:1657-1661): it reads the codes of the rendered INCOMING rows with `_RUNG_ROW_CODE_RE` AB:1592 (`^(?:-[ \t]*)?\*Product Code:\*`). S1's G6 stamps `rendered_codes` only when `origin_incoming`. | Once (4) numbers the inventory-origin rows, a row whose FIRST field is Product Code reads `1. *Product Code:*`, the regex misses it, `_block_product_codes` returns the "no opinion" empty set and the note names every zero code again (the hp11 defect, `tests/chatbot/journeys/hp11-zero-stock.json`). The `rendered_codes` stamp must widen to both origins in the same commit. |
| K9 | The PO rung under "No incoming and no stock for X, but PO is placed:" (A:1546-1554) prints `*PO date:* 2026-08-10` raw (`_crossdomain_rung_text` A:1402-1431, `_fmt_xd_value`). | Part of the ETA ladder reply, so S2 seam I6 (one value). |

## 1. Helpers that land in S2 (`K/format.py`)

| Helper | Output | Rule / replaces |
|---|---|---|
| `DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")` | used with `fullmatch` | anchored at both ends, so "2026-09-27 to 2026-10-01", "SRT-2026-09-27" and any text that merely starts with a date are not touched |
| `date(v) -> str \| None` | `dd/mm/yyyy` | card helper rule: from `YYYY-MM-DD` with or without a time part (`T` or space, optional seconds / fraction / `Z` / offset, components read verbatim like `timestamp`, no tz shift); an already `dd/mm/yyyy` string passes unchanged; anything else (None, "", non-str, "2026-9-7", "20260927") returns None. No calendar validation (parity with `timestamp`). |
| `value(v)` gate-on branch | `if isinstance(v, str) and DATE_ONLY_RE.fullmatch(v) and composers.enabled(): return date(v)` placed first inside the `str` arm | the settings read happens only when the regex matched, so every non-date value is untouched in both gate states. ISO datetimes keep today's `timestamp` path (prints `HH:MM:SS` when not midnight). Lists recurse through `value`, so a list of dates follows; dict values keep `jsc.js_string` (unchanged, not a printed case today, UNVERIFIED). |
| `label(f) -> str` | `"Container"` when `f.get("key") == "shipping_container_number"`, else `jsc.js_string(f.get("label"))` | card change (2). One key, so a constant, not a table (simplest-thing rule). Only called on gate-on paths. |
| `block(n, item)` (moved) | S1's `composers.stock.block`, unchanged except the label: `label_line(label(f), ...)` instead of the raw `f["label"]` | It already prints any item's fields plus the discontinued, alternative, expired and allocation flags, which is everything an incoming row carries. **No `incoming.block` is needed.** It moves to `format.py` because it now serves two answer types; the S1 caller in `crossdomain_render` is updated in the same commit (lane-owned code, unmerged, so no alias). If crew prefers no move, keep `stock.block` and only swap the label call. |

No `composers/incoming.py` module in S2: nothing ETA-specific needs a function of its own (the miss strip in I3 is
three lines inside AB). Create it when a later change gives it content (trigger: the flip PR, when the dealer ETA line
becomes a composer output).

## 2. S2 inventory: every ETA / incoming text producer, verdict per card section 4

| Producer (file:line) | Gate-on text | Seam |
|---|---|---|
| Staff incoming list `_incoming_list` P:892 (`crm_incoming_stock_list`), printed by `_item_line` F:3382 | dates dd/mm/yyyy (1); labels unchanged | **I1** |
| Staff by product `_incoming_by_product` P:929 (`crm_incoming_stock_by_product`) | "Shipment Container" -> "Container" (2); "Estimated Arrival Date" value dd/mm/yyyy (1) | **I2**, **I1** |
| Staff shipments `_incoming_shipments` P:1026 (`crm_incoming_stock_shipments`) | "Estimated Arrival Date" value dd/mm/yyyy (1); "Container" already | **I1** |
| Dealer ETA line `_incoming_dealer` P:987, `"{code}: ✅ ETA d1, d2"` / `"{code}: No ETA"`, dates already dd/mm/yyyy (`eta_policy._ddmmyyyy` eta_policy.py:393); refer closing P:2086 / F:3519 | byte-identical (5) | none. Its item has no fields, so neither I1 nor I2 can reach it. |
| Dealer stock `❌ No stock, ETA d.` P:1586 | byte-identical (S1 Stays) | none |
| Requested-attribute projection F:3043-3245: "not recorded yet" values, access note "I can't share the {label} {EM DASH} please check with the office." | byte-identical text; an appended "not recorded yet" field keyed `shipping_container_number` would print "Container" (I2) | none of its own |
| Chronological row sort F:3129-3150 and timeline field order F:3152-3175 (`_DATE_RE` on ENVELOPE values) | same order (sort runs before printing; I1 formats at print time) | none |
| Intros "Here is the incoming stock I found." / "Here are the incoming shipments I found." P:72-74; footer `_Data last updated: ts_` F:3576 | byte-identical | none |
| compose fallback "No {label} found for {codes}." C:600-612 / C:657 (replaces `NO_RESULT_INTRO` by exact equality) | byte-identical | none (S3) |
| `crossdomain_render` stock rows under an ETA miss (origin incoming) A:1014-1034 | numbered block (S1 G5); dates in those rows now dd/mm/yyyy through `format.value` | S1 G5 (no S2 hunk beyond the moved `block`) |
| `crossdomain_render` incoming rows under a STOCK miss (origin inventory), `- *Label:* value` | numbered main-path block (4): `1. *Company:* ...`, values through `format.value` (em dash for empty, dates dd/mm/yyyy) | **I4** |
| `crossdomain_render` `rendered_codes` stamp (S1 G6, origin incoming only) | stamped for both origins | **I5** |
| `only_other_note` "No incoming for X." A:1104 | dropped on the TOTAL-MISS fold, origin incoming (3); kept on the HIT fold | **I3** |
| `nothing_note` "No incoming and no stock for X." / "No incoming and stock is 0 at every location for X." A:1124-1130 | byte-identical | none |
| PO rung `_crossdomain_rung_text` A:1402, `*PO date:* 2026-08-10` | `*PO date:* 10/08/2026` (1) | **I6** |
| PO rung lead lines "No incoming and no stock for X, but PO is placed:" A:1530-1545 | byte-identical | none |
| `build_breakdown_msg` A:3847 "Here's what you want:\n• product: X\n\nBut no incoming matched these." | byte-identical: the writer that STAYS for (3) | none (#1744's function) |
| `not_found_error_message` A:4400 "Could not find incoming ... for X." | byte-identical | none (#1744's function; S3) |
| `build_suggest_offer` A:4719, "No incoming stock (ETA) found for X. Related products:\n1. CODE - has incoming ..." (A:4864-4876) | byte-identical (**owner ruling 17 Oct: STAYS**); the block above it loses "No incoming for X." through I3 | none |
| Roster annotation `pickers.py:121-127` " - has incoming / - no incoming", "None of these have incoming stock right now." | byte-identical (TRAIN cp0-4430299976, not a defect per card v1 change) | none |
| `sub_asks` "Incoming: {qty}, ETA {date}" B/turn/sub_asks.py:131 / :774, own `_date` sub_asks.py:854 already dd/mm/yyyy | byte-identical | none (not a fold, section 4) |
| alternatives.py:200-221 "{alt}: No ETA" / "{alt}: No incoming stock."; alternative incoming rows rendered through `output_structurer` | lines byte-identical; alternative rows get I1 / I2 like any row | none of its own |
| `_promo_render_blocks` A:1725 prints `jsc.js_string(value)` raw | byte-identical (not through `format.value`; S4 owns it) | none |
| compose `_render_row` C:143 (prints raw figure values; fallback only) | byte-identical (raw ISO stays if ever reached; UNVERIFIED that an incoming row reaches it) | none |
| `_dealer_names_the_miss` E:7631 | S1 G2 | none in S2 |

## 3. S2 gate-on seams

Every gate-on branch: `if composers.enabled(): <new>` else today's code, imports inside the function body.

| Seam | Where | Facts it reads | Gate-on text (card change) | Downstream readers and how they keep working | ms/zh keys |
|---|---|---|---|---|---|
| **I1** | `K/format.value` (moved from F:1647 by S0 F-a): the date-only branch in section 1. No hunk in fetch.py (the `_fmt_value` alias already points here). | the value string | `*ETA:* 27/09/2026`, `*ETA Delay:* 25/09/2026`, `*Estimated Arrival Date:* 27/09/2026` (1). Scope: every tool whose row printer meets a date-only value, see section 5. | sorts read envelope values (F:3132-3175, A:1005), unaffected; `answers` / figures keep ISO (envelope untouched); `LC._value` translates only `VALUE_WORDS` and `dd/mm/yyyy to dd/mm/yyyy` ranges (LC:785), a single date stays as printed in ms/zh; `alternatives.fold(fmt_value=fetch_mod._fmt_value)` (lanes/business/__init__.py:2526) prints a promotion alternative "(25/09/2026 to 30/09/2026)" gate-on. | none (dates are not translated) |
| **I2** | F:`_field_label` F:2832 (module level, outside `output_structurer`): `if isinstance(f, dict) and f.get("key") == "shipping_container_number" and composers.enabled(): f = {**f, "label": format.label(f)}` before the existing two lines. | field `key` (never the label) | `*Container:* DFSU7408507` on `crm_incoming_stock_by_product` (2) | `Localizer.label` translates by key because the new label equals `FIELD_KEYS[key]` (LC:847-857); the projection's `label_by_key` F:3186-3192 still reads the raw label but only feeds access notes, and the container field is identity, never denied; no backend reader of "Shipment Container" (K4). The summary printer F:3356 also uses `_field_label`, harmless (no summary field has that key). | "Container" exists (LC:369). No new key. |
| **I3** | (a) A:`crossdomain_render`, the `_xdBlock` literal A:1139: gate-on adds `"only_other_note": only_other_note` when `body` was prefixed with it (same gate-on-only pattern as S1's `rendered_codes`, so the gate-off key set graded by the s6c corpus stays). (b) AB:`_apply_crossdomain_render` AB:1671, after the `block` checks and before `sealed`: `if not answered and composers.enabled() and block.get("origin") == "incoming"`: if `note := block.get("only_other_note")` and `block["block"].startswith(note + "\n\n")`, fold `{**block, "block": block["block"][len(note) + 2:]}`. | `answered` (the arm, a fact the caller already passes), `_xdBlock.origin`, `_xdBlock.only_other_note` (the exact bytes this render prefixed, so no English is re-read) | cp0-5430419641 gate-on: `Here's what you want:\n• product: SRT5734-WH\n\nBut no incoming matched these.\nBut here are the stock details for the requested products:\n\n1. *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n*BRW:* 60 (O/S: 0)` + the offer as today. Same shape as cp0-3a2cb27822 today (3). | `TC.MARKERS` insertion unchanged (the block only got shorter at its head); `_apply_crossdomain_rung` swaps `nothing_note` by `endswith` (A:1546-1550), unaffected; `_crossdomain_offer_pending` reads `any` / `block` non-empty, unaffected (the lead and rows remain); HIT arm untouched (needs the line, K5); `_prefix_zero_note` HIT-only and inventory-only, never meets this branch. Dealers never reach it: `crossdomain_zeroset` is off for `stock_availability` / `dealer_view` (A:531, A:543). | "No incoming for {codes}." (LC:401, INLINE LC:655) is simply not printed on this path; it stays for the HIT path. No new key. |
| **I4** | A:`crossdomain_render`, S1's G5 branch: the condition `gate_on and origin_incoming` becomes `gate_on`, so origin inventory (incoming rows) prints `format.block(len(xd_rows) + 1, it)` too. | envelope items (`fields[].key/label/value`, `flags.unallocated/partially_allocated/discontinued`) | `But there is INCOMING stock (ETA) for the requested products:\n\n1. *Company:* Sorento\n*Product Code:* CB890SS-BL-DIY\n*Container:* ...\n*ETA:* 21/10/2026\n*Incoming Quantity:* 420\n🚩  *(PENDING ALLOCATION)*` (4). Lead, sort (qty desc, else ETA), `seen_rows` de-dupe and the field SET unchanged (no projection on this path, K7). Empty values print em dash (main path), not "-". | `_RUNG_ROW_CODE_RE`: I5. `localizer.reply` translates numbered label lines (`_NUMBERED` LC:788 accepts `\d+\. `; "Container", "Incoming Quantity", "ETA Delay", the flag lines are catalogued; "ETA"/"ETC"/"ETD" stay English by rule). Numbered block above a numbered sibling picker ("Related products:\n1. ...", inserted above that MARKER by TC:131-145): two lists starting at 1 in one reply, the same shape the owner just kept on the main path (exact answer + sibling picker); a reply "1" resolves against the pending picker, as today. OI-5. | lead (LC:398) and labels exist. No new key. |
| **I5** | A:`crossdomain_render` `_xdBlock` literal: S1's `**({"rendered_codes": ...} if gate_on and origin_incoming else {})` becomes `if gate_on`. AB G6 already prefers `rendered_codes`. | codes of the printed rows | none (keeps "No stock for X." naming only rendered zero codes) | replaces the English re-read on the numbered path (K8). | none |
| **I6** | A:`_crossdomain_rung_text` A:1424-1425: gate-on `*PO date:* {format.date(po_date) or _fmt_xd_value(po_date)}`. | row `po_date` | `*PO date:* 10/08/2026` (1) | none reads the rung text back (it becomes the new `nothing_note` at the block's end, A:1546-1554). | "PO date" label handling unchanged (test_chat_language_incoming.py:247-251 pins that the ms pass leaves it). No new key. |

## 4. Byte-identical folds, no gate

None in S2. Candidates checked and rejected:

| Candidate | Why not a fold |
|---|---|
| `sub_asks._date` sub_asks.py:854 vs `format.date` | not byte-identical: `_date` reads `str(v)[:10]` with `date.fromisoformat` (accepts `20260927`, validates the calendar, returns the raw string on failure); `format.date` is the card's regex rule and returns None. Both already print dd/mm/yyyy for every real value, so nothing is gained. |
| `_fmt_xd_value` A:854 vs `format.value` | "-" vs em dash for empty; stays for gate-off rows and the PO rung's non-date values; the flip PR deletes its row use. |
| "No ETA" in alternatives.py:207 vs P:1003 | two processes. |
| "None of these have incoming stock right now." pickers.py:127 vs resolve_gate.py:1707 | the resolve_gate copy is a docstring, not a writer. |
| `_dmy` F:2811, P `_sl_date` P:730 | report paths (out of lane) / MCP process. |

## 5. Date scope: per tool or all tools

`format.value` is the shared printer and sees no tool and no key (K3). Two ways to apply (1):

| Option | Mechanism | Gate-on changes beyond ETA | Moving parts |
|---|---|---|---|
| **(a) all tools (recommended)** | I1 as written: any full-match `YYYY-MM-DD` value on a printed field line | orders lists (`Order Date`, `Actual Delivery Date`, P:450-451 / P:668-669), PO placed and last cost (`PO Date` P:529 / P:634), SPO allocations / last receipt (`SPO Date` P:575, `GR Date` P:585), promotions (`Start Date` / `End Date` P:1061-1062, plus the promotion alternative tail), certificates and resources (`Valid Until` P:1161 / P:1258), product files (`Uploaded` P:1354), any miss-field line F:3508; counts in K2 | one branch in one function |
| (b) incoming tools only | a `ContextVar` in `format.py` set (try / finally) around the two `output_structurer` calls in lanes/business/__init__.py:2516 and :2552 when `tool_name` is an incoming tool, read by `value()`; I4 / I6 call `format.date` directly | none | a ContextVar, a tool set, two set/reset sites; S4 and S5 widen the set; the flip deletes all of it |

Recommend (a). R13 is a lane-wide rule (card section 3, "Applies to format.py"), S4 already lists promotion dates
(TRAIN cp0-b90acd1184 `*Start Date:* 2026-09-25`), and (b) is machinery built only to be widened and then removed,
which the simplest-thing rule rejects. Cost of (a): AC-G2 needs S2's change list to read "every date-only value on a
field line, every tool" (crew edit of the UAC S2 line, OI-1), and the gate-on replay review is done mechanically: map
`YYYY-MM-DD` to `DD/MM/YYYY` on the gate-off reply, then the only remaining diffs must be I2, I3, I4, I6 cases. S4
keeps `_promo_render_blocks` A:1725 (raw `js_string`, not this printer). If crew picks (b), the red tests in section 8
marked (a)/(b) flip to "unchanged".

## 6. Downstream readers of ETA text, and how each keeps working

| Reader | What it reads | S2 effect |
|---|---|---|
| Localizer keys (LC LABELS, INLINE, `Localizer.tail`) | exact English | no sentence reworded; "Container" catalogued; date values never translated; "No incoming for {codes}." still catalogued for the HIT path |
| `_LOCALIZED_TOOLS` F:2817 (incoming tools in) | labels translated inside `output_structurer` via `_field_label` | I2 sits in `_field_label` before `loc.label`, so the relabel is translated by key; the xd block is translated by the final `localizer.reply` (E:3070) |
| Dealer refer handling: P:2086 closing, F:3519 `refer.sentence()`, `_dealer_names_the_miss` E:7631 (`_COMPOSE_MISS`), `dealer_stock._ESCALATION`, pickers.py:117 regex on the dealer title `:\s+(?:✅\s*)?ETA\b` | the dealer line and refer line | untouched: the dealer line is a title (no fields), and the xd ladder is off for dealers (A:531/543) |
| compose `NO_RESULT_INTRO` swap C:600-612 | the lane block equal to "No matching results found." | no intro changes |
| Sort by `_DATE_RE` F:3139 / F:3163, xd `eta(it)` A:1005 | envelope values (ISO) | formatting is print-time only |
| `_RUNG_ROW_CODE_RE` AB:1592 / `_prefix_zero_note` | `- *Product Code:*` rows | I5 data stamp |
| `TC.MARKERS` TC:30-37, `with_alternative_lines` (AB, after the fold) | marker phrases | none changed |
| `_apply_crossdomain_rung` A:1546-1554 | `endswith(nothing_note)` | note untouched |
| head parser (next turn reads this reply as history) | dd/mm/yyyy dates instead of ISO | UNVERIFIED effect on a follow-up date parse; risk R4 |

## 7. Overlap with open PRs

| File | Open PR hunks (function) | S2 hunks | Same function? |
|---|---|---|---|
| K/format.py, K/stock.py | none (lane files) | I1, `date`, `label`, `block` move | n/a |
| F | #1744 `entity_ids_transformer` 1229-1266, `output_structurer` 3591-3715 | `_field_label` F:2832 (I2) | no (module-level function, outside `output_structurer`) |
| A | #1744 `near_miss_reply` 2867, `unknown_values_reply` 2892, `build_set_header` 2952-2983, `not_found_error_message` / `build_breakdown_msg` 4409 | `crossdomain_render` (I3a, I4, I5: S1's G5 lines plus the `_xdBlock` literal ~A:1139), `_crossdomain_rung_text` A:1424 (I6) | no. The miss writer that stays is chosen partly for this. |
| AB | none | `_apply_crossdomain_render` AB:1671 (I3b) | no |
| E, C, TC, turn_runtime, turn/apply, turn/context, turn/narrow, gate, predicate, resolve_gate, head/* | #1744 / #1747 / #1755 | none | n/a: S2 touches none of them |
| P (MCP), LC, inventory_service.py (#1744), replay.py (#1755) | #1744 `catalog.py` | none | n/a. No MCP edit needed for any S2 change (K1, K4). |

## 8. Red tests for the tester (test(red) commit first)

Under `tests/chatbot/`, Postgres-free (pure functions). Gate on = `monkeypatch.setattr(settings,
"chat_composer_enabled", True, raising=False)`. Fixture shapes can be lifted from recorded console cases
(`replay_turns/console/case-017-...`, `case-018-...`, `case-038-...`, and the prod_sample files that hold
`matched these.\nNo incoming for`).

**Helpers (`test_composers_s2_format.py`)**
| Test | Assertion |
|---|---|
| `test_date_matrix` | `format.date`: "2026-09-27" -> "27/09/2026"; "2026-09-27T10:11:12" and "2026-09-27 10:11" -> "27/09/2026"; "27/09/2026" -> "27/09/2026"; None, "", "x", "2026-9-7", "20260927", 20260927 -> None |
| `test_value_date_only_gate_off_unchanged` | gate off: `value("2026-09-27") == "2026-09-27"`; `value("2026-09-27T00:00:00") == "27/09/2026"`; `value("2026-09-27T10:11:12") == "27/09/2026 10:11:12"` |
| `test_value_date_only_gate_on` | gate on: `value("2026-09-27") == "27/09/2026"`; `value(["2026-09-27", "2026-10-07"]) == "27/09/2026, 07/10/2026"`; the two datetime cases above unchanged; "2026-09-27 to 2026-10-01", "SRT-2026-09-27", "2026-09-27x" unchanged |
| `test_label_by_key` | `format.label({"key": "shipping_container_number", "label": "Shipment Container"}) == "Container"`; any other key returns its own label |

**Changes (1) and (2) on the main path (`test_composers_s2_incoming.py`)**
| Test | Assertion |
|---|---|
| `test_incoming_list_dates_gate_on` | `output_structurer` over a `crm_incoming_stock_list` envelope shaped like cp0-0654e3e74a (keys `estimated_arrival_date` "2026-09-27", `eta_delay_date` "2026-09-25", requested_attributes both): response has `*ETA:* 27/09/2026` and `*ETA Delay:* 25/09/2026`; no `\d{4}-\d{2}-\d{2}` on any `*Label:*` line |
| `test_incoming_list_dates_gate_off_unchanged` | same input gate off: `*ETA:* 2026-09-27` exactly |
| `test_incoming_rows_order_unchanged_gate_on` | two rows (ETA 2026-10-07, 2026-09-27) and a timeline ask: row order and date-field order equal the gate-off order |
| `test_shipments_and_by_product_dates_gate_on` | `crm_incoming_stock_shipments` and `crm_incoming_stock_by_product`: `*Estimated Arrival Date:* 27/09/2026` |
| `test_by_product_container_label_gate_on` | by_product row: gate on `*Container:* DFSU7408507`, gate off `*Shipment Container:* DFSU7408507` |
| `test_container_label_ms_zh_gate_on` | same with the ms / zh catalogue localizer: `*Kontena:*` / `*货柜:*` |
| `test_dealer_eta_line_unchanged_gate_on` | `incoming_dealer` envelope (cp0-dad9d8c2de: `SRTMCB6088-BL: ✅ ETA 23/10/2026` + refer) prints identically in both gate states |
| `test_non_incoming_dates_gate_on` (option (a); under (b) assert unchanged) | orders list `*Order Date:* 10/08/2026`, promotions `*Start Date:* 25/09/2026`, PO `*PO Date:* ...` gate on |

**Changes (3), (4) and the PO rung (`test_composers_s2_xd.py`)**
| Test | Assertion |
|---|---|
| `test_eta_miss_says_it_once_gate_on` | `_apply_crossdomain_render(miss_text, result, answered=False)` where `miss_text` = cp0-5430419641's miss lines and `result` holds a `crossdomain_render` origin-incoming block with one non-zero stock row: "But no incoming matched these." appears once, "No incoming for SRT5734-WH." is absent, the lead and `1. *Product Code:* SRT5734-WH` follow |
| `test_eta_miss_gate_off_unchanged` | same gate off: contains `But no incoming matched these.\nNo incoming for SRT5734-WH.\n\nBut here are the stock details` |
| `test_eta_hit_keeps_only_other_line_gate_on` | `answered=True` (turn 858c9c54 shape): "No incoming for X." still printed |
| `test_suggest_offer_miss_once_and_picker_unchanged_gate_on` | miss text "No incoming stock (ETA) found for X. Related products:\n1. X-BL - has incoming\n2. ..." + block: "No incoming for X." absent; every picker line and the "Reply with a number..." line byte-identical to gate off (owner ruling) |
| `test_only_other_note_stamped_gate_on_only` | `_xdBlock` has `only_other_note` gate on when the note printed; no such key gate off |
| `test_stock_origin_double_unchanged_gate_on` | origin inventory total miss keeps "No stock for X." (S2 scope; flips if crew rules OI-3) |
| `test_stock_miss_incoming_block_numbered_gate_on` | origin inventory, one incoming list row: block contains `1. *Company:* Sorento\n*Product Code:* CB890SS-BL-DIY\n*Container:* ...\n*ETA:* 21/10/2026\n*Incoming Quantity:* 420\n🚩  *(PENDING ALLOCATION)*`; no line starts with `- *` |
| `test_xd_incoming_row_equals_main_path_row` | for an item whose fields the projection keeps whole (tester asserts that precondition), the gate-on xd row text equals the `output_structurer` row for `crm_incoming_stock_list` |
| `test_stock_miss_incoming_block_gate_off_unchanged` | gate off: `- *Company:*` dash form, `*ETA:* 2026-10-21`, "-" for empty |
| `test_rendered_codes_both_origins_gate_on` | origin inventory gate on: `_xdBlock["rendered_codes"] == ["CB890SS-BL-DIY"]` |
| `test_zero_note_numbered_incoming_rows_gate_on` | HIT arm, origin inventory, two zero stock codes, incoming rows (Product Code as FIRST field) for one: `_prefix_zero_note` names only that one (hp11 guard) |
| `test_xd_incoming_block_translates_ms` | `localizer.reply` (ms) of the gate-on block: `Kontena`, `Kuantiti Masuk`, the allocation flag line translated, `ETA` kept |
| `test_po_rung_date_gate_on` | `_crossdomain_rung_text([{"product_code": "X", "qty": 30, "po_date": "2026-08-10"}])`: gate on `*PO date:* 10/08/2026`, gate off `*PO date:* 2026-08-10` |

**Gate-off golden and property**
| Test | Assertion |
|---|---|
| extend S1 `test_gate_off_recorded_envelopes_golden` | add incoming list (cp0-0654e3e74a shape), by_product, shipments, dealer ETA, xd origin-inventory miss and HIT: gate off equals goldens captured from main at the red commit |
| `test_s2_output_clean` | gate-on outputs of every S2 fixture: no `YYYY-MM-DD` on a `*Label:*` line, no " · ", no UUID, no "Shipment Container" |

Outside pytest: L1 replay gate off vs main = 0 unintended (baseline turn 0/585, grounding 0/1506); gate-on replay
diffs reviewed after the date normalisation in section 5.

## 9. Existing tests that pin gate-off text (stay green, gate off by default)

`Shipment Container`: test_attribute_asks_round7.py, test_chat_language_incoming.py. Raw ISO dates and `No incoming
for` in recorded replays: `replay_turns/console/case-001`, `-004`, `-016`, `-018`, `-025`, `-038`, `-040`..`-042`,
`-044`, journey `handpass9-incoming-miss-after-pick-ladder.json`, ~10 `replay_turns/prod_sample/*`. `INCOMING stock
(ETA)` dash rows: `console_cases/2026-09-06.yaml`, `journeys/hp11-zero-stock.json`, console cases `-001`, `-016`,
`-018`, `-040`..`-044`, `-052`, `focus-001`, `focus-008`, plus the s6c corpus (`test_s6c_answer_lane.py`,
`test_s6c_engine_paths.py`, which grade the `_xdBlock` key set: I3a / I5 keys are gate-on only for that reason).
None is edited in S2; the flip PR retargets them.

## 10a. Lead decisions 17 Oct (override section 10 and anything above that disagrees)

- OI-1: (a) all tools. Card R13 (crew-answered card) already sets one date format for every reply, and S4's promotion
  dates are the same fix. UAC S2 reads "every date-only value on a field line prints dd/mm/yyyy (gate on)".
  Reviewer checks the gate-on diff with the section 5 normalised-diff method.
- OI-2: as recommended. The miss sentence stays; the crossdomain "No incoming for X." goes on the total-miss fold.
- OI-3: in S2, same mechanism (drop the `origin == "incoming"` condition in I3). The word "inventory" in
  "But no inventory matched these." is an internal name (R5) and is fixed with S3's one miss grammar.
- OI-4: crew ruled (a) 17 Oct: gate on, the key `estimated_arrival_date` prints the label "ETA" on every view
  (relabel by key at print time in I2, beside "Container"); ms/zh keep "ETA" (label_catalog.py:368-379).
- OI-5: acceptable (the owner kept the same shape on the main path; the picker text cannot change).
- OI-6: verified 17 Oct: `CHAT_COMPOSER_ENABLED=true` in the shell reaches the replay process (`replay.env` does not
  set it). The turn tier itself is vacuous until crew brings `sorento_eval_base` to main's schema (crew fixing).
- R3: the tester covers the entitlement-miss shape; if a miss text does not state the absence, I3 is restricted to
  `build_breakdown_msg` misses via a flag.
- R3 as built (review round 1, 17 Oct; deviation recorded): no flag is plumbed (the unit seam `_apply_crossdomain_render`
  sees only text + result). The duplicate absence line is dropped only when the miss text carries one of the writers'
  own absence phrases (`composers/misses.ABSENCE_PHRASES`) with the code. Fails safe (keeps main's duplicate) on
  drift; round 2 makes the writers share those constants and adds a drift-guard test.

## 10. Open items for crew

- **OI-1 (date scope, owner-visible).** Recommend (a) all tools (section 5); it brings S4's / S5's field-line dates
  into S2, so the UAC S2 line needs "every date-only value on a field line". (b) keeps S2 ETA-only at the cost of a
  ContextVar. Crew rules before the red commit (two tests flip).
- **OI-2 (which ETA-miss line stays).** Recommend keeping the miss sentence ("But no incoming matched these." /
  "No incoming stock (ETA) found for X." / "Could not find incoming ... for X.") and dropping the crossdomain
  "No incoming for X." on the total-miss fold. The opposite (keep the line that names the code) needs a hunk in
  `not_found_error_message` (#1744's function) or a text strip; S3's one miss grammar can revisit the wording.
- **OI-3 (stock-origin twin).** "But no inventory matched these.\nNo stock for X" has the same double (7 in-repo
  recordings) and also leaks the word "inventory". I3 covers it by dropping the `origin == "incoming"` condition.
  Not in the card; crew rules (S2 or S3).
- **OI-4 (not in card).** The same key `estimated_arrival_date` prints "ETA" (list) and "Estimated Arrival Date"
  (by_product, shipments). I2 could unify it the same way. Left unchanged unless crew rules.
- **OI-5.** A numbered xd block directly above the numbered sibling picker gives two lists starting at 1 (owner kept
  the same shape on the main path). Confirm acceptable; the picker text cannot change (owner ruling).
- **OI-6 (carried from S1 R5).** Gate-on replay depends on `CHAT_COMPOSER_ENABLED` in the shell taking precedence over
  `replay.env` (UNVERIFIED).

## 11. Risks (top first)

| # | Risk | Mitigation |
|---|---|---|
| R1 | Option (a) changes non-ETA replies gate on (orders, PO, SPO/GR, promotions, certificates, files): a large gate-on diff a reviewer could skim past a real regression in. | Mechanical normalised diff (section 5); `test_non_incoming_dates_gate_on`; crew rules OI-1 first. |
| R2 | I4 without I5 brings back the hp11 defect (the zero note names every zero code) on the numbered inventory-origin rows. | I4 and I5 in one commit; `test_zero_note_numbered_incoming_rows_gate_on` with Product Code as the FIRST field (the case the regex misses). |
| R3 | I3 keys on the total-miss fold. If some miss-arm text does not itself say the primary domain has nothing (an entitlement miss, `_entitlement_miss`, A:3924), dropping "No incoming for X." loses that fact. UNVERIFIED which miss texts reach AB:2141 with an `only_other` code. | Tester adds the entitlement-miss shape to `test_eta_miss_says_it_once_gate_on` (assert the absence is still stated, or restrict I3 to texts whose miss came from `build_breakdown_msg` via a flag, crew call). |
| R4 | The next turn's parser reads dd/mm/yyyy dates in history where it read ISO (S1 R1 shape); the L1 replay cannot see it. | One TRAIN live run per PR (card section 6). |
| R5 | A non-date field whose value is exactly `YYYY-MM-DD` (a batch number?) would print as a date gate on. UNVERIFIED that none exists. | Full-match only; property test over recorded fixtures (no `Batch` / `Shipment` value changes). |
