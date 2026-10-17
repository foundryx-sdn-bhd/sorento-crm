# PLAN: CHAT-COMPOSER FLIP - make the composers the only path

**Status:** Planned, waits for the held-out check of steps 1-3 + code lookups and the owner hand test (crew 17 Oct). Track: L follow-up PR on the CHAT-COMPOSER lane (PR #1760). No build before both gates pass.
**Parent:** `PLAN-chat-composer-17oct.md`, card Q2 (old divergent formatters stay until this FLIP PR). Seam designs `chat-composer-seam-design*.md` (their "Lead decisions" blocks bind).
**Line numbers** are on lane head `d096aed50`, files under `sorento_crm_backend/app/services/chatbot/` (call it `C/`). They drift; re-grep `composers.enabled` before editing.


## Lead resolutions 17 Oct (override anything below that disagrees)

- `lanes/business/gate.py:1598` "Couldn't find: ..." is S3c (waits for #1755), not part of the flip.
- Keep `complete_answer` (replay tests drive it); delete only the dead promo picker reachable from it.
- The empty-field rule outside product envelopes (S5/S6 OI-5) goes in the flip.

## 0. Rule of the flip

Keep every gate-ON arm exactly as it is; delete every gate-OFF arm and everything only it reached. The flip changes nothing a gate-on turn prints. Anything that would change gate-on text is out of scope (section 7).

## 1. Every `composers.enabled()` branch

29 direct calls plus 4 gate variables that hold the result. "Keep ON" = the gate-on arm survives as unconditional code.

### S0 / S1 stock

| File:function:line | Today | Flip |
|---|---|---|
| `C/composers/__init__.py:25 enabled` | reads `settings.chat_composer_enabled` | delete the function (section 4) |
| `C/composers/stock.py:12 miss_line` | ON: `misses.not_found` (UUID-only list prints ""), OFF: `Couldn't find: X.` (line 16) | keep ON, delete line 16 |
| `C/engine.py:7646 _stock_ask_reply` | `_stock_answer_lines(envelopes, full=composers.enabled())` | `full=True`, then drop the `full` parameter of `_stock_answer_lines` (def ~7699) and its OFF branch |
| `C/engine.py:7589 _carried_leg` (Q1) | `carried_leg = bool(composers.enabled() and ...)` | drop the `composers.enabled() and` term |
| `C/composers/format.py:83 value` | date-only string: ON `date(v)`, OFF falls to raw | keep ON, remove the `if` and the lazy import |
| `C/answer_bridge.py:1708 _apply_crossdomain_render` | `not answered and composers.enabled() and note...` | drop the `composers.enabled()` term |

### S2 ETA / incoming

| File:function:line | Today | Flip |
|---|---|---|
| `C/lanes/business/answer.py:949 crossdomain_render` (`gate_on`) | local flag | delete the local; branches below |
| `answer.py:1025` rows loop | ON: `composers_stock.block` numbered rows; OFF (1033-1053): `- *Label:* ...` dash rows, `PRODUCT DISCONTINUED` / `PROMO EXPIRED` / allocation flag lines | keep ON, delete 1033-1053 |
| `answer.py:1170` `_xdBlock` literal | gate-on-only keys `rendered_codes`, `only_other_note`, `only_other_codes` | make the keys unconditional (the s6c corpus key-set grade moves with section 5) |
| `answer.py:1454` PO rung `PO date` | ON `composers_format.date`, OFF `_fmt_xd_value` | keep ON; `_fmt_xd_value` stays for the other PO row values (1444-1460) |
| `C/lanes/business/fetch.py:2822 _field_label` | relabel by key only ON | keep ON |
| `C/turn_runtime.py:5550 _composers_on` (call at 5660) | `lane_intro` stamped only ON | delete `_composers_on` (5547-5550), stamp `lane_intro` unconditionally |

### S3 not found / clarify / quantity

| File:function:line | Today | Flip |
|---|---|---|
| `C/turn/compose.py:437 unplaced_line` | ON: UUID filter + `misses.not_found_text` + `miss_out`; OFF (447): `I could not find ...` | keep ON, delete 447 |
| `C/so_status.py:281 po_ref_reply` | ON `Couldn't find PO ref X.` / OFF `I could not find PO ref X.` | keep ON |
| `C/so_status.py:300 reply_text` | same pair for the missing words | keep ON |
| `answer.py:2805 not_found_words` | ON no colon / OFF `Couldn't find: X.` | keep ON |
| `answer.py:2852 what_you_want_reply` | same pair | keep ON, drop the lazy import |
| `answer.py:3960 build_breakdown_msg` | same pair for `nf` | keep ON |
| `answer.py:3997 build_breakdown_msg` miss noun | ON `misses.domain_word` when in `MISS_WORD`, else `_plain_words` | drop `composers.enabled() and` (the `in MISS_WORD` test stays) |
| `answer.py:4718 _miss_tail` | `Couldn't find N.` / `I could not find N.` | keep ON |
| `C/engine.py:7589`, `C/answer_bridge.py:1708` | Q1 and the R3 flag are S3 changes, counted in the S0/S1 and S2 tables | as listed there |

### S4 files / attachments / promotions

| File:function:line | Today | Flip |
|---|---|---|
| `C/turn/compose.py:550` (`composers_on`, uses 746, 813, 819, 842, 1049) | F1 attached sentence placement, F2 file dedupe, F3 gap line | delete the variable; 746 keep ON (delete the `{code} has no {types}.` arm at 752-756); 813, 819-828 keep ON (delete the OFF `seen_file_keys` path at ~829-840 and the `seen_file_keys` set at 547 if unused); 842 and 1049 keep ON |
| `answer.py:3413 _window_end` | ON dd/mm/yyyy | keep ON, delete the `jsc.js_string(v)` fallback only where `date()` cannot return None (keep the `or` for non-date text) |
| `answer.py:4307` F4 gate | ON: one "nothing on record" line per product | keep ON |
| `answer.py:4375` | ON drops UUID words | keep ON |

### S5 order / document status

| File:function:line | Today | Flip |
|---|---|---|
| `answer.py:3472` found-by-uuid customer label | ON group name | keep ON |
| `answer.py:3782` customer bullet | ON group name, no `(+N more)` | keep ON |
| `answer.py:3920` F1 miss header | ON `search_scope_header`; OFF `elif is_order_scope:` hand-built `Order:` / `Dates:` header (3925-3957) | keep ON, delete the `elif` block; then `_AXES` (2505), `_NO_LINE` (2503) and `axis_words` (3834) if grep shows no other caller |
| `answer.py:4449` H2 | ON order's customer as group | keep ON |
| `C/do_ask.py` `_month`, `_ddmmyyyy`, `_span` aliases (no gate) | aliases of `composers/status.py` | keep (not gated); optional inline in a later tidy, not this PR |

### S6 product info / price

| File:function:line | Today | Flip |
|---|---|---|
| `fetch.py:1959` (`composers_on`, uses 2085, 2102, 2150) | P1 empty un-asked field left out; P2 asked-empty line; spec miss as `missing_line` | delete the variable, keep ON; delete the OFF arm 2153-2154 and the `missed_codes` dict (2026) with the `spec_miss:` "not recorded for ... (+N more)" loop (2166-2174). The `spec_hidden:` loop stays |
| `fetch.py:3376 _item_line` (`composers_on`, uses 3379, 3393) | M1 money, P2 missing lines | delete the variable; `_fmt_value(composers_format.money_field(f))`; keep 3393 loop |
| `C/turn/sub_asks.py:879 _currency` | ON `MYR` / OFF `RM` | return `"MYR"` |

### Label catalogue

| File:line | Flip |
|---|---|
| `C/label_catalog.py:595-598 _lane_on` (uses 605, 873, 1074) | delete; see section 3 |

## 2. Dead after the flip: deleted

Verify each with a grep for a remaining caller first; a hit means keep and list it in the PR body.

| # | What | Where | Why dead |
|---|---|---|---|
| D1 | `Couldn't find: {names}.` colon form | `composers/stock.py:16`; `answer.py:2805`, `2852`, `3960` OFF arms; `lanes/business/gate.py:1598 dropped_line` (see section 8, not a lane hunk) | gate-on prints no colon |
| D2 | `I could not find {names}.` | `turn/compose.py:447`; `so_status.py:283`, `300`; `answer.py:4720` | S3 grammar |
| D3 | `_COMPOSE_MISS` regex and its fallback | `engine.py:7662`, uses at 7676 and 7681 in `_dealer_names_the_miss` | compose stamps `Answer.miss_text` (S3 M1); keep only the `stamped` path. Docstring (7667-7669) updated |
| D4 | dash-form crossdomain rows | `answer.py:1033-1053` (`- *Label:* ...`, `PRODUCT DISCONTINUED`, `PROMO EXPIRED`, allocation flags) | `composers_stock.block` prints the numbered rows and flags |
| D5 | `_fmt_xd_value` use in the rows loop | `answer.py:1034` (def 855 stays: the PO rung 1444-1460 still uses it for non-date values) | row use gone |
| D6 | `_field_render_value` | `answer.py:870` | only caller was 1034 |
| D7 | `PROMO EXPIRED` | `answer.py:1044` | with D4 |
| D8 | `_promo_render_blocks` and the promo picker it feeds | `answer.py:1757`, callers 2184 and 2258 inside `promo_picker` (1799), plus `_promo_reintro` (1772) and `_promo_*` helpers (1724-1755, 1783) used only there | reachable only from the retired `lanes/business/__init__.py:2730 complete_answer` (S4 OI-2). Deleting `complete_answer` itself is a separate call: `test_replay.py`, `divergences.py` and `test_crossdomain_ladder.py` still drive it. Default: delete the promo picker and its helpers, leave `complete_answer` |
| D9 | hand-built order miss header | `answer.py:3925-3957`, then `_AXES`, `_NO_LINE`, `axis_words` | F1 uses `search_scope_header` |
| D10 | `{code} has no {types}.` writer | `turn/compose.py:752-756` | F3 `missing_line` |
| D11 | spec miss "not recorded for X (+N more)" | `fetch.py:2026`, `2154`, `2166-2174` | P1/P2 `missing_lines`; ISO check against `Not defined` writers (S6 property test) |
| D12 | OFF `RM` | `turn/sub_asks.py:879` | M2 |
| D13 | bare `How many units do you need?` after a carried leg | no code to delete (`composers/misses.py:14 BARE_QUANTITY_QUESTION` is the ON drop); the MCP presenter `sorento_crm_mcp/presenters.py:121` is the single source and stays | listed so nobody deletes the MCP string |
| D14 | text re-readers that now have structured data | `engine.py` `_COMPOSE_MISS` (D3); `answer_bridge.py:1711 states_absence` + `composers/misses.py ABSENCE_PHRASES` stay (they read the miss text, no structured field exists, R3 as built); `Localizer` generic find-none regex path stays (still translates the fit none lines) | only D3 is dead |
| D15 | OFF-only `full=False` branch of `_stock_answer_lines` | `engine.py` def ~7699 | `full` always True |
| D16 | OFF-only branch variables | `seen_file_keys` OFF path in `turn/compose.py` (~829-840); `composers_on` locals; imports `from app.services.chatbot import composers` left unused | cleanup |

Dead catalogue keys (delete from `LABELS` and `INLINE`, only if grep shows no emitter left): `"{code} has no {types}."` (`label_catalog.py:443`) and its `DIRECT_ONLY` entry (796); `"I could not find {names}."` (303, INLINE 624); `"Couldn't find: {names}."` (419, INLINE 695) only if D1's `gate.py:1598` is also moved to the new grammar.

## 3. LANE_LABELS merged into LABELS

| Step | File:line |
|---|---|
| Move every entry of `LANE_LABELS` (`label_catalog.py:579-590`) into `LABELS`; delete `LANE_LABELS`, `LANE_KEYS` (592) and the comment block above them | `label_catalog.py` |
| `defaults()`: `entries = LABELS` | 605 |
| `Localizer.__init__`: delete the `if not _lane_on(): table = {...}` filter | 873-874 |
| seed query: `source_text.in_(list(LABELS))` | 1074 |
| delete `_lane_on` | 595-598 |
| Tests: `test_composers_review_r1.py` `test_b1_gate_off_*` (lines 77-102) pin the gate-off table; delete them | tests |

Seeding: the new keys reach `translation_memory` rows through the existing seed path; run `seed` as part of the deploy note, no migration.

## 4. Settings field and gate

| Step | File:line |
|---|---|
| Delete `chat_composer_enabled: bool = False` and its comment | `app/config.py:192-193` |
| Delete `def enabled()` | `C/composers/__init__.py:25-29`; update the module docstring (line 1 names the env var) |
| Remove `CHAT_COMPOSER_ENABLED` from any env file, compose file and the replay env notes | grep `sorento_crm/`, `.github/`, `documentation/` (none in code outside the files above on the lane head) |
| Remove every `from app.services.chatbot import composers` that only fed `enabled()` | all files in section 1 |
| `tests/chatbot/test_composers_s0.py:20 test_gate_defaults_off` | delete |

## 5. Tests

### 5a. Lane tests that pin gate-off: delete

| File | Action |
|---|---|
| `tests/chatbot/test_composers_gate_off_golden.py` | delete the file (189 lines) |
| tests named `*_gate_off*`, `*_gate_off_golden`, `*_gate_off_unchanged` in the other lane files | delete: `test_composers_review_r1.py` (70, 77, 86, 92, 102, 296), `_s2_dates.py` (102, 144, 161, 176, 208), `_s2_incoming.py` (78, 87, 97, 105, 115, 128), `_s3_quantity.py` (87), `_s3_misses.py` (100, 152), `_s4_files.py` (131, 146, 203), `_s4_miss.py` (122, 139, 239), `_s5_status.py` (121, 169, 245), `_s6_product.py` (78, 96, 140), `_stock_miss.py` (94, 111, 201, 217), `_stock_xd.py` (63, 148) |
| `@pytest.mark.parametrize("gate", [False, True])` tests (`_review_r1.py:257`, `_s2_dates.py:196`, `_s3_quantity.py:94`, `_s4_files.py:154, 167`, `_s4_miss.py:182, 253`, `_s5_status.py:57, 64, 181, 257`, `_s6_product.py:88, 173, 180`, `_stock_miss.py:148`, `_stock_xd.py:100`) | keep only the True case; remove the parameter |
| every `_set_gate(monkeypatch, ...)` / `monkeypatch.setattr(settings, "chat_composer_enabled", ...)` | remove (def at `test_composers_stock_xd.py:41`, imported by `_s2_dates.py` and others) |

### 5b. Existing tests that pin gate-off text: retarget

Source: the seam designs' "Existing tests that pin gate-off text" sections, plus a repo grep of `sorento_crm_backend/tests` (upper bound by grep: 238 py files and 85 fixture files touch one of the old strings; the exact set is the one that goes red when the gate is removed, so run the suite on the flip branch and fix reds). Expected real set is far smaller than the grep, about 45 py files and 40 fixtures.

| Old string | Named in seam design | Files (py, by grep) | Retarget to |
|---|---|---|---|
| `Couldn't find: ` | S1 section 7 | 30, e.g. `test_stock_miss_render.py`, `test_avail_mode_scenarios.py`, `test_chat_language_incoming.py`, `test_rearch_r11_catalogue.py`, `test_lane_require.py`, `test_multi_ask_runner.py`, `test_attribute_asks_round6.py`, `round9.py`, `test_so_number_ask.py`, `test_carried_miss.py` | `Couldn't find X.` (no colon) |
| `I could not find` | S3 section 7 | 18, e.g. `test_rearch_*` compose tests, `test_multi_ask_runner.py`, so_status tests | `Couldn't find X.` |
| bare `How many units do you need?` | S3 | 36 (e.g. `test_stock_ask_ht26_family_pick.py`, `_r8_open_question.py`, `_r9_open_question_kinds.py`, `_resume_narrows.py`) | only where Q1 drops it (carried stock leg); a current-message stock leg keeps it |
| stock block under an ETA miss, dash rows, `INCOMING stock (ETA)` | S1, S2 section 9 | 7 py + 23 fixtures: `test_crossdomain_ladder.py`, `test_rearch_r4_miss_engine.py`, `test_rearch_s3_compose_data.py`, `test_rearch_r5_production_decides.py`, `test_rearch_r9_handpass9_replay.py`, `test_chat_language_incoming.py`, `test_s6c_answer_lane.py`, `test_s6c_engine_paths.py` (graded `_xdBlock` key set), `console_cases/2026-09-06.yaml`, `journeys/hp11-zero-stock.json` | numbered rows from `composers_stock.block`, `_xdBlock` keys gate-on |
| `Shipment Container`, raw ISO dates, `No incoming for` | S2 section 9 | `test_attribute_asks_round7.py`, `test_chat_language_incoming.py`; replay fixtures `replay_turns/console/case-001, -004, -016, -018, -025, -038, -040..-042, -044`, `handpass9-incoming-miss-after-pick-ladder.json`, ~10 `replay_turns/prod_sample/*` | `Container`, `ETA`, dd/mm/yyyy, line dropped on a total miss |
| `{code} has no {types}.`, `has no` | S4 | ATTACHMENT-MULTI compose tests, `test_attachment_multi_type.py`, `test_attach_set_brand_followup.py` | `No <type> on record for <code>.` |
| `matched these`, `But no inventory matched these.` | S3, S4 | rearch miss tests (3 py + 30 fixtures) | `But no stock matched these.` etc. |
| `PROMO EXPIRED`, `_promo_render_blocks` | S4 | `test_s6c_answer_lane.py` corpus (3 fixtures) | delete with D8 |
| scope header lines, `hasn't been delivered yet`, DO period text | S5 | `test_do_ask_range.py`, `test_do_ask_range_engine.py`, `test_do_period_skip_do_list.py`, `test_do_period_skip_so_list.py`, `test_so_list.py`, `test_so_number_ask.py`, `test_do_reveal_bypasses.py`, `test_do_field_reveals.py`, `test_s6c_answer_lane.py`, `divergences.py`, `test_customer_scope_lane.py`, `test_rearch_r5_production_decides.py`, `test_rearch_r12_handpass12_d.py` | tester checks which assert moves (S5 A3: hit and miss headers) |
| `RM <n>`, `not recorded for`, `Not defined`, `(+N more)` | S6 | `test_product_spec_projection.py`, `test_spec_visibility_projection.py`, `test_chat_product_labels.py`, `test_discontinued_ask.py`, `test_growth_r1_review_fixes.py`, `test_attribute_asks_round9.py`, `test_multi_ask_runner.py`, `test_multi_ask_golden.py`, `test_chat_language_slice2.py`, `test_chat_language_slice3*.py` (44 py by grep for `RM <n>`) | `MYR 1,234.00`, `No <noun> on record for <product>.` |

Recorded-reply fixtures (`replay_turns/**`, `console_cases/*.yaml`, `journeys/*.json`) that pin reply text: regenerate the expected text from the gate-ON replay of the lane head, never by hand.

## 6. Proof plan

1. **L1 replay, flip head vs lane head with the gate ON: 0 diffs.** The flip changes nothing gate-on. Run the replay twice: lane head with `CHAT_COMPOSER_ENABLED=true` (OI-6: the shell var reaches the replay process), flip head with no variable. Compare the turn-tier reply sha256 (replay.py:339-347). Any diff is a defect in the flip, not a fixture to update.
2. **Full pytest suite through CI** (the `ci` label, one run on the final head; a push cancels a labelled run). Not run locally on the shared DB.
3. `alembic heads` single head (no migration here), compile gate, touched vitest (none), dash guard on the plan and diff.
4. grep gates before push: `rg "composers\.enabled|chat_composer_enabled|LANE_LABELS|_COMPOSE_MISS|PROMO EXPIRED"` over `app/` and `tests/` returns nothing.
5. No live LLM run: the flip is gate-on identical, so the TRAIN/held-out numbers of the parent lane stand.

## 7. Not in the flip

| Item | Waits for |
|---|---|
| Sets slice (set answers reuse the single-code format, O2) and the "more" paging line (`more_line`) | #1744 SET-STOCK-RENDER, #1747 CHAT-STATE, #1755 (card Q4 / S3 OI-1 ruling) |
| S3b M7 | #1744 |
| S3c | #1755 |
| R10 | its own small lane after #1744 / #1747 / #1755 |
| Empty-field rule outside product envelopes (S5/S6 OI-5) | the flip PR may take it only if the crew confirms; default: its own follow-up |
| Deleting `complete_answer` and the KEPT-node replay corpus | separate decision (D8) |
| Any wording change | none: gate-on text is frozen |

## 8. Unclear, ask the crew before building

1. `lanes/business/gate.py:1598` still prints `Couldn't find: ...` ungated (not a lane hunk). Move it to the no-colon grammar in the flip (changes live text) or leave it and keep the `Couldn't find: {names}.` catalogue key.
2. Whether the flip also deletes `complete_answer` (D8 default: no).
3. Exact retarget set (section 5b) is only known after the first CI run on the flip branch.
