# CHAT-COMPOSER seam design: S4 files / attachments + promotions (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`, card `chat-composer-behaviour-card.md` section 4 "Files" and
"Promotions", rules R4, R7, R13; UAC S4 line. Builds on `chat-composer-seam-design.md` ("Lead decisions 17 Oct" binds),
`-s2.md` section 10a (dates on field lines, every tool) and `-s3.md` section 7a (M6 landed in S3a, M7 waits for #1744).
Read-only design. Line numbers on HEAD 0eb8361c1f (S0-S3a landed). Open PR hunks read with
`gh pr diff 1744|1747|1755 --repo foundryx-sdn-bhd/sorento-crm` on 17 Oct (heads 3f3bf8101 / f3b0c5dc2 / ef8f12f32).
Paths: B = `sorento_crm_backend/app/services/chatbot`, A = `B/lanes/business/answer.py`, C = `B/turn/compose.py`,
E = `B/engine.py`, F = `B/lanes/business/fetch.py`, AL = `B/lanes/business/alternatives.py`, AB = `B/answer_bridge.py`,
TR = `B/turn_runtime.py`, LC = `B/label_catalog.py`, K = `B/composers/`, P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.

**S4 card changes (crew-approved):** (1) the attached sentence and the de-duplication in one place; (2) one
missing-file sentence instead of two, "Here's what you want:" echo kept, R4 shape; (3) one marker "EXPIRED" on every
path, a re-sorted promo list keeps it; (4) dates dd/mm/yyyy outside field lines too; (5) R7 expired flyers still sent
beside the live one, unchanged.

**Binding rulings applied:** gate-on branches are `if composers.enabled()` with today's code untouched otherwise;
helpers land in the slice that first prints them (so `missing_line`, planned for S6, lands HERE); the MCP presenter
stays the single printer unless facts are missing (S4 needs no MCP edit); no hunk in a function an open PR changes
(exception asked in OI-5, the S3 M6 precedent); a changed sentence gets ms/zh in the same slice; a gate-off sentence is
never catalogued (the catalogue is not gated).

## Lead decisions 17 Oct (override anything below that disagrees)

- Build F1-F5, the two folds (`composers/files.py`) and the PX1/PX2 pins now. Wording: `No Product Photos on record
  for SRTBF11850.` (type keeps its official name, owner ATTACHMENT-MULTI 2 Oct; the "Here's what you want:" echo stays).
- OI-1 as recommended: the narrow label_catalog guard, so a running-text match never rewrites a sentence that is only
  filled at source; a red test pins that the gate-OFF ms/zh fit line "No seat cover is on record for X." is unchanged.
- OI-2: no seam in the promo picker reachable only from the retired `complete_answer` (no caller); card change (3)'s
  "re-sorted list loses EXPIRED" is not on the live path. The dead code goes in the flip PR.
- OI-3 (a), OI-4 (a): alternative lines and qualified misses (active/inactive, date window, other company, entitlement)
  keep today's sentence.
- OI-5: F4 and F5 land now (the M6 precedent crew accepted: #1744 changes two comment lines in that function).

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | **The promo picker is off the live path.** `promo_picker` (A:1798) is called only from `complete_answer` (`lanes/business/__init__.py:2928`), which has no caller in `app/` or `scripts/` (grep 17 Oct) and is called "the retired `complete_answer`" in TR:5485. Its three attached-sentence writers (A:2052, A:2176, A:2236), the end-date sort (A:1889-1919) and `_promo_render_blocks` (A:1756, raw `js_string`, no flags, no date format) run only in `tests/chatbot/test_s6c_answer_lane.py`'s corpus replay. TRAIN cp0-b90acd1184 confirms: its 20-row list is in backend order (End Dates 12-25, 12-25, 12-25, 12-14, 12-23 ...), opens with the presenter's bare "I have attached the file(s) below." (no "I found N promotions"), so it came from `output_structurer`, not the picker. | Card change (3)'s "re-sorted list loses the marker" and the picker's three writers are dead code. No seam (OI-2). |
| K2 | **The live promo printers already say EXPIRED and already get S2's dates gate on.** `_item_line` F:3377-3378 `⚠️  *({loc.text('EXPIRED')})*`; AL:203 promotion alternative line `⚠️  *(EXPIRED)*` with `fmt_value` dates (AL:199-200); the cross-domain row printer prints `⚠️  *(PROMO EXPIRED)*` (A:1043) gate OFF only, because S1/S2's gate-on branch (A:1024, `if gate_on:` for every origin) prints through `K/stock.block`, which writes `⚠️  *(EXPIRED)*` (K/stock.py:56-57). Start / End Date / Valid Until / Uploaded print through `format.value` (S2 I1). | Changes (3) and (4) need NO new seam on the live path: S4 pins them with tests (PX1, PX2). |
| K3 | **Live attached-sentence writers are two, plus one remover.** P:2035 (envelope `intro` when the envelope has an attachment; P never attaches a url-less file, P:355-357), printed by `output_structurer` as the section's first paragraph via `loc.text(intro)` (F:3285); C:1000-1001 appends it when the English sentence is not already in the text; E:9838-9840 removes it and its ms/zh in link mode. Defects: (a) two sections that each carry files (promotion + product files in one turn) print it twice (each lane text opens with it; C's check only prevents a third); (b) an in-lane-localized tool with files (incoming packing lists, `_LOCALIZED_TOOLS` F:2766-2778, P:925/971/1040) prints the ms/zh sentence at the top, C's English `not in text` check misses it and appends the English one, which the final pass translates: the sentence twice in ms/zh. UNVERIFIED on a recording (tester builds both shapes). | One backend writer (seam F1): compose keeps the first lane intro, drops later ones, appends only when no section printed it, and compares the PRINTED form, read from data (`lane_intro`), not by searching the text. |
| K4 | **Three dedupes, run in sequence.** P:2005-2017 per envelope, key `(url, filename)` or `fileHash`; C:781-793 across sections, key `fileHash or url or id or filename` (`_file_key` C:152); E:9964-9982 (`_label_attachments`, final send list), drop url-less, then drop when the url OR the hash was seen. The sent set is E's. C and E differ only when one file appears once with a hash and once without under the same url (C keeps both, E keeps one). | One backend rule in `K/files.py` (seam F2 + fold F-g); P's stays as a per-envelope pre-filter (other process, like `REFER_TO_SALESMAN`). No reply text changes; `Answer.files` / `actions` can lose a duplicate entry. |
| K5 | **The two missing-file sentences.** Hit path: C:711-731, `localizer.fill("{code} has no {types}.")` (DIRECT_ONLY LC:446/772), one line per product lacking an asked type, types = official `type_name` (`TR.attachment_type_labels`). Miss path: A:`not_found_error_message` product_attachment arm A:4228-4322: (i) product unfound -> `Couldn't find X.` + offer (a token miss, S3 grammar, stays); (ii) `use_breakdown` -> nested `build_breakdown_msg` A:3883 -> `Here's what you want:\n• product: X\n• attachment type: T\n\nBut no T matched these.` + offer (TRAIN cp0-7b068ed4da); (iii) else -> `Could not find a photo for product X.` + offer (A:4321; S3b M7 would make it "Couldn't find ..."). A third wording lives in AL:221-223 `Alternative: CODE: no <subject>.` / `no files.` (owner 9 Oct "show that we have tried"). | One canonical line `No {types} on record for {code}.` on (ii), (iii) and the hit path (seams F3, F4). S4 takes A:4321 from S3b M7 (it is a missing-FILE line, not a token miss). AL stays (OI-3). |
| K6 | **"No ... on record for ..." collides with the fit none-lines.** `product_find.FITS_NONE_LINE` "No seat cover is on record for {code}." (product_find.py:129-155) is a DIRECT_ONLY key, and `seat_link_lines` (E:5038, joined at E:5959) are built in English with `.format`. Any non-DIRECT_ONLY catalogue entry `No {x} on record for {y}.` (INLINE or whole-line template, LC:842-858) would match that English line in a ms/zh reply (`{x}` = "seat cover is") and garble it, gate OFF. | The new key is DIRECT_ONLY (filled at source). The miss path has no localizer today: OI-1. |
| K7 | **Raw ISO outside field lines.** `date_range` A:3407-3411 = `f" from {date_start} to {date_end}"` from the parser's `date_filter_start/end` (ISO), printed raw in the non-order miss sentences (attachment A:4321, promotion A:4449, every `build_breakdown_msg` miss whose `miss_window` falls back to it, A:3933-3937). Order-scope misses already use `_fmt_date`. No other promo / file text prints a date outside a field line (`access_level_choice_message` A:259, `No {levels} promotion for {subject}.` `__init__.py:2411`, promo misses A:2315-2342: none). | Seam F5, one value. It also reaches non-file misses that carry a date window (in scope of R13; UNVERIFIED count, tester greps fixtures for ` from 20`). |

## 1. Helpers that land in S4 (`K/files.py`, `K/format.py`)

| Helper | Output | Note |
|---|---|---|
| `files.ATTACHED_SENTENCE` | `"I have attached the file(s) below."` | moved from C:37 (fold F-f); C keeps `_ATTACHED_SENTENCE = files.ATTACHED_SENTENCE`, E:9785 import unchanged |
| `files.sendable(entries) -> list` | E's rule verbatim: non-dict kept, url-less dropped, then drop when the url OR the `fileHash` was seen | moved from E:9964-9982 (fold F-g); E calls it, the filename-collision labelling stays in E |
| `files.place_attached(parts, opened, printed, has_files) -> list[str]` | `parts` = section blocks, `opened[i]` = section i's lane text opens with `printed + "\n\n"` (or equals it); keeps the first such paragraph, strips it from every later section, and appends `printed` as a last part when none opened with it and `has_files` | pure; `printed` = `localizer.text(ATTACHED_SENTENCE)` (English when not localized) |
| `format.missing_line(what, product, *, fill=None) -> str` | `"No {what} on record for {product}."`, via `fill(key, what=, product=)` when given | the R4 line; S6 reuses it for spec / price ("No weight on record for SRTWC286-SH."), so the key is generic: `"No {what} on record for {product}."` |
| `format.date` | exists (S2) | F5 uses it |

`K/files.py` imports only `K` and `jsc` (extend `test_composers_import_boundary`). No `K/promotions.py`: nothing
promotion-specific needs a function (K1, K2). Trigger to create it: the flip PR, or the picker coming back live.

## 2. Canonical wording

| Kind | Gate on | Chosen because | ms / zh |
|---|---|---|---|
| Attached sentence | `I have attached the file(s) below.` once per reply, at the first section that opened with it, else last | card "Stays"; TRAIN cp0-d1977a0368 | LC:304 + INLINE LC:602 exist. No new key. |
| Missing file (product found) | `No {types} on record for {code}.`, one line per product, types joined by `_join_words` as today, type = official `type_name` VERBATIM (`No Product Photos on record for SRTBF11850.`) | R4 shape. Official name, not lower-cased: the ATTACHMENT-MULTI owner ruling (2 Oct) names official types, the echo line right above already prints "Product Photos", the hit path prints the same names, and case-folding a DB name is a word rule ("CIDB Certificate"). | NEW key `"No {what} on record for {product}."`, DIRECT_ONLY (K6). Proposed: ms `"Tiada {what} dalam rekod untuk {product}."`, zh `"{product} 没有{what}记录。"` (crew confirms wording; matches LC:715 "tiada rekod" / "没有记录"). `{what}` stays English like `{types}` in LC:446 today. |
| Missing file with a qualifier (active / inactive, date window, multi-company suffix, entitlement) | today's sentence (OI-4) | R4: a filtered miss is not "no files on record" | unchanged |
| Expired marker | `⚠️  *(EXPIRED)*` | already on every live printer gate on (K2) | LC:163 `EXPIRED`; `PROMO EXPIRED` never printed gate on, so not catalogued |
| Dates | `dd/mm/yyyy` | R13 | values, never translated |

## 3. Inventory: every file / promotion text producer

Verdicts: **C** canonical gate on already; **G** gate-on seam; **S** stays; **D** dead (retired path); **W#** waits.

| # | Producer (file:line) | Today | Verdict |
|---|---|---|---|
| 1 | P:2035 intro | `I have attached the file(s) below.` | S (source; placed by F1 downstream) |
| 2 | F:3285 `output_structurer` prints `loc.text(intro)` | section's first paragraph | S (no hunk, #1744's function) |
| 3 | C:994-1001 append | sentence at end when English absent | G (F1) |
| 4 | E:9836-9840 link-mode remover | strips sentence + ms/zh | S (reads `files.ATTACHED_SENTENCE` via C alias) |
| 5 | A:2052, A:2176, A:2236 promo picker intros | attached / `I found N promotions ... I have attached ...` | D (K1) |
| 6 | P:2005-2017 dedupe | `(url, filename)` + hash | S (MCP process) |
| 7 | C:781-793 dedupe + `aliasUrls` | `_file_key` | G (F2) |
| 8 | E:9964-9982 dedupe | url or hash | fold F-g (byte-identical) |
| 9 | C:711-731 gap line | `{code} has no {types}.` | G (F3) |
| 10 | A:4275-4279 product unfound | `Couldn't find X.` + offer | C (S3 grammar) |
| 11 | A:4280-4298 -> `build_breakdown_msg` A:3961-3969 | `But no {T} matched these.` + offer | G (F4) |
| 12 | A:4299-4322 | `Could not find a {t} for product X{dates}.` + offer | G (F4); S3b M7 no longer touches this line |
| 13 | A:3288-3293 (main numbering, per discovery) gate-miss ask | `Please provide the attachment type for product X - e.g. ...` | S (a question, S3 scope; uncatalogued gate off) |
| 14 | A:4015-4017 (main) unknown doc type | `I don't know '<term>' as a document type. Types I know: ...` | S (a clarify) |
| 15 | A:5110-5115 (main) picker stamps | ` - has Product Photos, no Technical Specifications` | S (picker text, owner rulings) |
| 16 | AL:216-223 alternative empty | `CODE: no <subject>.` / `CODE: no files.` | S (OI-3) |
| 17 | AL:229-237 alternative files | `Alternative: CODE: <File Name>` | S |
| 18 | file_links.py:130-140 link blocks | `*File:* n` / `*Link:* u` / `*Link:* (sent as a file)` | S (no date, no sentence change) |
| 19 | F:3377-3378 `_item_line` | `⚠️  *(EXPIRED)*` | C |
| 20 | AL:193-204 promotion alternative | `Alternative: X: name (start to end)` + EXPIRED | C (dates via S2) |
| 21 | A:1043 cross-domain row, gate off | `⚠️  *(PROMO EXPIRED)*` | C gate on (S1 G5 branch A:1024 prints `stock.block`); test PX1 |
| 22 | A:1756 `_promo_render_blocks` | raw values, no EXPIRED, ISO dates | D (K1) |
| 23 | `__init__.py:2380-2412` promo dedupe + `No {levels} promotion for {subject}.` | no date printed | S |
| 24 | A:259-380 `access_level_choice_message` | tier menu, `No current promotion[ for X].` | S (R10 own lane) |
| 25 | A:2315-2342 strict / partial promo miss | `No promotion found for ...` | D (inside `promo_picker`) |
| 26 | A:4437-4452 promo miss (live miss lane) | `Could not find promotion ...{date_range}` | S3b M7 (W1744); its date via F5 |
| 27 | A:3407-3411 `date_range` | ` from 2026-09-01 to 2026-09-30` | G (F5) |
| 28 | P `_promotions` / `_promotion_products` / `_product_attachments` / `_resource_attachments` field dates | through `format.value` | C (S2 I1) |
| 29 | media_intake.py:410-427, C:1180-1235 (`I read A, B from that photo.`) | inbound photo copy | S (not file sending) |

## 4. Gate-on seams

| Seam | Where | Facts read | Gate-on text | Downstream readers, how they keep working | ms/zh |
|---|---|---|---|---|---|
| **F1** attached sentence once | TR:`envelope_of` (TR:5547-5600) adds `"lane_intro": fetched.get("response_intro")` (additive key, read only gate on; F:3643 already returns it). C:`compose()`: gate on, record per section whether its `block` opens with `printed` (`printed = localizer.text(files.ATTACHED_SENTENCE)`, only when `env.get("lane_intro") == files.ATTACHED_SENTENCE`); at C:994-1001 replace the `not in text` append with `files.place_attached(text_parts, opened, printed, bool(files))` before the join (the join moves below it gate on only). | `lane_intro`, the final `files` | one sentence per reply; multi-section `I have attached the file(s) below.\n\n1. *Promotion:* ...\n\n1. *Product Code:* ...`; ms/zh incoming with packing list: one `Saya telah melampirkan fail di bawah.` | E:9838 link-mode strip finds the one copy (it strips every language). `turn_runtime` set-header prefix strip (TR:5503) reads the head of the FIRST section, which keeps its intro. A single-section reply is byte-identical (strip and keep at the same place). | none new (LC:304) |
| **F2** one dedupe | C:781-793: gate on, the `seen` test is `files.sendable`'s rule (url OR hash seen); the `aliasUrls` carry keeps working on the kept entry (match by url or hash). Gate off `_file_key` unchanged. | `url`, `fileHash` | no text change; `Answer.files` / `actions` equal E's send list | E's `_label_attachments` then finds nothing more to drop; n8n `attachments_src` contract unchanged (E builds it) | none |
| **F3** hit-path missing file | C:711-731: gate on, each gap line is `format.missing_line(_join_words(missing, localizer), code, fill=localizer.fill)` | `product_codes`, `attachment_types`, figures (unchanged `_types_without_files`) | `No Product Photos on record for SRTWC286-SH.` | none re-reads the gap line (grep `has no` 17 Oct) | NEW DIRECT_ONLY key, filled here |
| **F4** miss-path missing file | A:`not_found_error_message` product_attachment arm. Gate on: (a) `attach_names` (A:4246-4262) is computed for ONE type too (official `type_name`); (b) resolved products = `stored_by_key` values (A:4302-4308, hoisted above the branch); (c) when products resolved, no qualifier (`active_inactive`, `date_range`, `access`, `co_suffix` all empty) and no entitlement miss: lines = `format.missing_line(" or ".join(attach_names), code)` per product. Breakdown arm: nested `build_breakdown_msg` gains `missing_lines: list[str] \| None = None` (A:3883); when given, the `But no ... matched these{co_suffix}.` head (A:3961-3966) is `"\n".join(missing_lines)`, the offer joins as today (`f"{head} {esc_ask}"`). Else arm (A:4317-4322): `"\n".join(lines) + (f" {esc}" if esc else "")`. Otherwise today's text. | parser entities (hint), gate `compatible_entities` (uuid, code), resolutions `type_name`, qualifiers | cp0-7b068ed4da: `Here's what you want:\n• product: SRTBF11850\n• attachment type: Product Photos\n\nNo Product Photos on record for SRTBF11850. Please refer to your salesman.` | suffix unchanged, so `dealer_stock._ESCALATION` / refer marking, TC `MARKERS` ("Would you like me to escalate"), `order_list._OFFER_SENTENCE`, `reply_ladder._ESCALATE_TO_TEAM_RE` see the same tail. Nothing reads "matched these" as data (grep 17 Oct: comments only, TR:5045). Echo lines unchanged. | NEW key (same as F3); delivery mechanism = OI-1 |
| **F5** dates in miss sentences | A:3407-3411: gate on, `date_range` prints `format.date(v) or v` for both ends | parser `date_filter_start/end` | ` from 01/09/2026 to 30/09/2026` | `_DATE_RANGE` LC:794 already reads `dd/mm/yyyy to dd/mm/yyyy` (value range only); no reader of the miss text parses dates | none (dates are values) |
| **PX1** (test only) | cross-domain gate on prints EXPIRED | `flags.expired` | `⚠️  *(EXPIRED)*` | `_FLAG` LC:799 translates the flag (`EXPIRED` LC:163) | exists |
| **PX2** (test only) | promotion field dates gate on | Start / End Date | `*Start Date:* 25/09/2026` (cp0-b90acd1184) | as S2 | none |

## 5. Byte-identical folds, no gate (L1 replay 0 diffs)

| Fold | Copies | Into | Note |
|---|---|---|---|
| F-f | `_ATTACHED_SENTENCE` C:37 (and the literal at P:2035, other process: not folded) | `K/files.ATTACHED_SENTENCE`; C alias kept | E:9785 keeps importing from C, or switches; either is byte-identical |
| F-g | E:9964-9982 first loop of `_label_attachments` | `K/files.sendable` | E calls it; the collision labelling (E:9984-10000) stays |

Rejected: C `_file_key` vs `sendable` (not identical, K4: that difference is F2's gate-on change); the promo picker's
writers (dead, K1; deletion trigger: the PR that deletes `complete_answer`); P dedupe (MCP process); AL:221 vs C gap
line (different wording, OI-3).

## 6. Overlap with open PRs

| File | Open PR hunks (function) | S4 hunks | Same function? |
|---|---|---|---|
| K/files.py, K/format.py | none | helpers | n/a |
| C | #1747 `_ASK_HEADERS` dict only (module level, starts C:1016 on #1747's base; the hunk header names `compose` as context only) | `compose()` 711-731 (F3), 781-793 (F2), 994-1001 (F1) | no. F1's hunk ends about 15 lines above #1747's context window: textual merge expected clean; coder keeps F1's edit above `return Answer(` |
| TR | #1744 `runner` 3727-3752, `set_page_carry` 4295, `page_the_set` 4527-4567; #1755 `stamp_customer_options` 3136 | `envelope_of` 5547-5600 (F1 key) | no |
| A | #1744 `near_miss_reply` 2867 (context only, S3 K8), `unknown_values_reply` 2892, `build_set_header` 2952-2983, `not_found_error_message` comment-only hunk in the unknown-values arm (main 4409-4410 = HEAD ~4466) | `not_found_error_message`: F4 (A:3883 nested signature + 3961-3966, A:4228-4322), F5 (A:3407-3411) | **yes, same function** as #1744's two-line comment hunk; F4 sits about 145 lines and F5 about 1060 lines from it. Same shape as S3a's M6, which crew landed (S3 7a). OI-5. |
| E | #1744 / #1747 / #1755 `_run_stages_body`, `run_tail`, `turn_apply`, imports | F-g in `_label_attachments` (module level, E:9957) | no. `run_tail` (#1747) calls `_apply_file_mode` / `_attachments_src`, both untouched |
| AB | none | AB:1972 only if OI-1 picks (b) | no |
| LC | none | new key + DIRECT_ONLY entry | no |
| AL, P, F, `__init__.py`, file_links.py | #1744 F `entity_ids_transformer`, `output_structurer` | none | n/a |

**Waits on a PR:** nothing if crew accepts OI-5 (a). With OI-5 (b), F4 + F5 wait for #1744; F1, F2, F3 and the folds
ship now. The S3b M7 line A:4321 moves to S4's F4 (M7 keeps A:4424/4443/4449 and the promo miss A:4449).

## 7. Red tests (tester writes red first; gate on = `monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)`)

| Test | Assertion |
|---|---|
| `test_files_helpers` | `files.ATTACHED_SENTENCE == "I have attached the file(s) below."`; `sendable([{url:u1,fileHash:h},{url:u2,fileHash:h},{url:None},{url:u1}])` keeps only the first; `format.missing_line("Product Photos","X") == "No Product Photos on record for X."`; with `fill` uses the key `"No {what} on record for {product}."` |
| `test_place_attached` | two parts both opening with the sentence -> first kept, second stripped of `sentence + "\n\n"`; none opening + has_files -> appended last; none + no files -> unchanged; ms `printed` honoured |
| `test_attached_once_two_sections_gate_on` | `compose` over a promotion envelope and a product-attachment envelope, both `lane_intro == ATTACHED` and lane text opening with it: `text.count("I have attached the file(s) below.") == 1`, at the head of the first section |
| `test_attached_once_localized_gate_on` | ms localizer, incoming envelope whose lane text opens with `Saya telah melampirkan fail di bawah.` + files: after `compose` + `Localizer.reply`, the ms sentence appears once and the English one never |
| `test_attached_gate_off_unchanged` | same two inputs gate off: today's bytes (two copies on the two-section input; English appended on the ms input) |
| `test_attached_single_section_byte_identical_gate_on` | cp0-d1977a0368 shape: `I have attached the file(s) below.\n\n1. *Product Code:* SRTWC286-SH\n*Attachment Type:* Technical Specifications\n*File Name:* SRTWC286-SH.jpg\n\n_Data last updated: 02/10/2026 09:47:43_` identical in both gate states |
| `test_alternative_files_append_gate_on` | lane intro not the sentence, files present (alternative's files): sentence appended last, as gate off |
| `test_link_mode_strips_the_one_sentence` | `_apply_file_mode` in link mode over an F1 gate-on text: no attached sentence in any language remains |
| `test_compose_dedupe_matches_send_gate_on` | `[{url:u1,fileHash:h},{url:u1}]` across two sections: gate on `Answer.files` has 1 entry and equals `E._label_attachments(...)`; gate off keeps 2 (today); `aliasUrls` still carried for `{url:u2,fileHash:h}` |
| `test_label_attachments_fold` | `E._label_attachments` output identical before / after the fold over the PHOTO-SEND-CAP fixtures (existing tests stay green) |
| `test_gap_line_gate_on` / `_gate_off` | `compose` product_attachment, two codes, one lacking Product Photos: gate on line `No Product Photos on record for SRTWC286-SH.`; gate off literal `SRTWC286-SH has no Product Photos.`; placement above the footer unchanged |
| `test_gap_line_ms_zh_gate_on` | ms / zh fill of the new key: `Tiada Product Photos dalam rekod untuk SRTWC286-SH.` / `SRTWC286-SH 没有Product Photos记录。` (crew-confirmed wording) |
| `test_attachment_breakdown_miss_gate_on` | cp0-7b068ed4da shape (dealer, product resolved, type Product Photos): exact text `Here's what you want:\n• product: SRTBF11850\n• attachment type: Product Photos\n\nNo Product Photos on record for SRTBF11850. Please refer to your salesman.` |
| `test_attachment_breakdown_miss_gate_off` | same input gate off: `...\n\nBut no Product Photos matched these. Please refer to your salesman.` (literal) |
| `test_attachment_else_arm_gate_on` | staff, resolved SRT207-DIY, raw "photo", no breakdown lines: `No Product Photos on record for SRT207-DIY. Would you like me to escalate to <team> team?`; gate off literal `Could not find a photo for product SRT207-DIY. Would you like me to escalate to <team> team?` |
| `test_attachment_two_types_two_products_gate_on` | one line per product, types `Product Photos or Technical Specifications`, offer after the last line only |
| `test_attachment_miss_qualifiers_keep_today` | each of `is_active=False`, a date window, multi-company `co_suffix`, an entitlement miss: gate-on text equals gate-off text except F5's date format |
| `test_attachment_product_unfound_unchanged` | product unresolved: `Couldn't find X.` + offer in both gate states |
| `test_miss_date_range_gate_on` | promotion miss with `date_filter_start/end` ISO: gate on ` from 01/09/2026 to 30/09/2026`; gate off ` from 2026-09-01 to 2026-09-30` |
| `test_promo_dates_gate_on` (PX2) | cp0-b90acd1184 envelope through `output_structurer`: `*Start Date:* 25/09/2026` / `*End Date:* 25/12/2026`; gate off `2026-09-25` |
| `test_promo_expired_every_live_path_gate_on` (PX1) | expired promotion row via `_item_line`, via AL promotion alternative, via `crossdomain_render` gate on: each prints `⚠️  *(EXPIRED)*`; no `PROMO EXPIRED`; gate off `crossdomain_render` keeps `⚠️  *(PROMO EXPIRED)*` |
| `test_expired_flyer_still_sent` (R7) | envelope with one live and one expired promotion, both attached: gate on both attachments survive compose + `_label_attachments`, expired row marked |
| `test_fit_line_not_retranslated` | ms / zh `Localizer.reply("No seat cover is on record for SRTWC286-SH.")` equals main's output (guards K6) |
| `test_s4_output_clean` (property) | gate-on fixtures: no `has no `, no `matched these` on an attachment miss without qualifiers, no `PROMO EXPIRED`, no ISO date, no UUID, no " · " |
| `test_composers_import_boundary` (extend) | `K/files.py` imports nothing from engine / head / tail / dialogue / turn.compose / lanes |

Existing tests that pin gate-off text (stay green): `has no` (ATTACHMENT-MULTI compose tests), `matched these`
(rearch miss tests), `PROMO EXPIRED` / `_promo_render_blocks` (`test_s6c_answer_lane.py` corpus), PHOTO-SEND-CAP
dedupe tests. Tester greps and lists them in the red commit.

## 8. Open items for crew (max 5)

- **OI-1 ms/zh for the miss-path line (K6).** The key must not match running text (fit lines). (a) DIRECT_ONLY key,
  filled at source: `not_found_error_message` gains `localizer=None`, AB:1972 passes `ctx.get("localizer")`
  (UNVERIFIED that `answer_for`'s ctx carries it); cost: in zh the offer after the filled `。 ` is no longer matched by
  the INLINE lookbehind `(?<=[.?!] )` (LC:822) and stays English. (b) Running-text key plus a generic LC guard: an
  INLINE / template match never rewrites a span that fully matches a DIRECT_ONLY sentence; property test over every
  recorded ms/zh reply = 0 gate-off diffs. (c) Miss path stays English in ms/zh in S4 (as today), hit path filled.
  Recommend (b): one key serves F3, F4 and S6, and the guard states a rule DIRECT_ONLY already implies.
- **OI-2 Dead promo picker (K1).** (a) No seam; card change (3)'s "re-sorted list" item closes as "not on the live
  path"; deletion of `complete_answer` + `promo_picker` is its own cleanup PR (trigger: any edit to them). (b) Gate
  `_promo_render_blocks` anyway. Recommend (a) (simplest-thing rule: no machinery for code no customer reaches).
- **OI-3 Alternative empty line `Alternative: X-NEW: no Product Photos.` (AL:221).** (a) Leave (own owner ruling
  9 Oct, an alternative line, not the asked product's answer). (b) Converge to `Alternative: No Product Photos on
  record for X-NEW.` Recommend (a).
- **OI-4 Qualified misses (active / inactive, date window, other company, entitlement).** (a) Keep today's sentence
  when any qualifier is set (R4: a filtered miss is not "nothing on record"). (b) Carry the qualifier into the new
  line. Recommend (a); trigger for (b): a recorded qualified attachment miss.
- **OI-5 F4 / F5 timing.** Both sit in `not_found_error_message`, where #1744 changes two comment lines (K8 of S3).
  (a) Land now, as S3a's M6 did. (b) Wait for #1744. Recommend (a).

## 9. Risks (top 3)

| # | Risk | Mitigation |
|---|---|---|
| R1 | The new `No {what} on record for {product}.` key, if ever registered for running text without the guard, garbles English fit none-lines in ms/zh replies gate OFF (K6), an AC-G1 break the English replay cannot see. | DIRECT_ONLY or OI-1 (b) guard; `test_fit_line_not_retranslated`; gate-off ms/zh replay over recorded turns. |
| R2 | F1 drops a later section's intro by the `lane_intro` fact; a section whose lane text puts something before the intro (access notice, set header, `so_bucket_refusal` F:3640) does not open with it, so it is left in place and the sentence can still print twice there. | No worse than today; `place_attached` only strips an exact leading paragraph; tester adds a notice-prefixed fixture and records the result; widen only with evidence. |
| R3 | F4 tells a customer "No Product Photos on record" when the empty result came from a filter the qualifiers do not capture (brand gate, file type the contact may not see, `has_result` false for a tool error). | Lines only when a product is resolved and no qualifier / entitlement miss is set (OI-4); tester adds a brand-gated and an access-hidden attachment fixture and asserts today's text there. |
