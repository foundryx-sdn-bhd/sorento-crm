# Test report - Attribute-first asks

Plan: `PLAN-attribute-first-asks.md`. UAC: `attribute-first-asks-acceptance-criteria.md`.
Branch `feat/chatbot-attribute-first-asks`. Lane backend on the restored prod copy
(`sorento_ai_automation`, dump 2026-09-10, migration 511), MCP on 8765, console contact 900000105
(promotion case: 900000052, which has access levels).

## Pytest (Phase 2, red first then green)

| Set | Result |
|-----|--------|
| `tests/test_product_spec_search.py`, `tests/test_product_predicate_service.py`, `tests/test_resolve_predicate.py`, `tests/test_migration_511_attribute_first_lookup_sets.py` | 118 passed |
| `tests/chatbot/test_lane_require.py` (S1 to S4 + two fix rounds) | 125 passed together with `test_product_spec_search.py` |
| Fix round 3 + owner test round (R14 to R34): the five lane files + migration test together | 263 passed, 0 failed (commit b71209bf4, origin/main merged, 511 reparented) |
| `tests/chatbot` full after R34 | 1677 passed, 72 skipped, 5 xfailed, 5 failed (the same `test_s7_*` baseline) |

The 5 failures are `test_s7_dispatch_edges.py` (3) and `test_s7_ordering_and_offload.py` (2): the
Redis-outage turns die at `route.py:224` (`is_stock_check_denied`, a None settings row), a line
this lane never touched. Reproduced on a clean `main` checkout with the same venv: see the
"Baseline" line below.

Baseline (main, detached checkout of d6cb5b624 with the lane venv, 11 Sep 2026): the same five tests fail, `5 failed, 21 passed` on those two files. Not this lane.

## AC status

| AC | Status | Evidence |
|----|--------|----------|
| 1301, 1302 | PASS | pytest, honest zero |
| 1303 | PASS | pytest, `derive_require` parametrize incl. scheme split and cert word from message |
| 1304 | PASS | pytest, body diff |
| 1305 | PASS | pytest, AND-mode code-shaped gate; console "check stock srtwc286" answers the forward block with no header |
| 1306 | PASS | pytest, class word scopes; console "which sink has incoming" → 47 kitchen sinks (was 620 products) |
| 1307, 1308 | PASS | pytest |
| 1309 | PASS | pytest shape lock |
| 1310 | PASS | pytest, five legs incl. incoming |
| 1311 | PASS | pytest; console "which sink has incoming" → 47 |
| 1312 | PASS | pytest, alias set; console "which basin has photo" clarifies as a document type (alias set empty by design, owner enters options) |
| 1313 | PASS | pytest, register spelling then set; console "which item has PPS cert" → 940 |
| 1314 | PASS | migration test, empty sets created on the prod copy |
| 1315 | PASS | pytest, first five ids and no row limit; console pages show 5 distinct products |
| 1316 | PASS | pytest; console "which tap has cert" → "908 taps have certificates. Showing 5.", "which item has PPS cert" → "940 products have PPS certificates. Showing 5." |
| 1317 | PASS | pytest (S4); console "which tap has cert" / "more" / "more" → Showing 5, 6 to 10, 11 to 15 |
| 1318 | PASS | pytest, expired-only counted and flagged |
| 1319 | PASS | pytest, miss names the set and the codes checked |
| 1320 | PASS | pytest; console "which water tap has cert" → "I don't know 'water tap' as a product type. Did you mean tap?" |
| 1321 | PASS | pytest, scheme miss names schemes on file |
| 1322 | PASS | body byte-identical without a leg intent; forward turns unchanged in the suite |
| 1323 | PASS | pytest, dealer stock parity |
| 1324 | PASS | `git diff --stat main..HEAD` touches neither `chatbot_parser_prompt.py`, `ai_prompt_registry.py` nor `sorento_crm_mcp/` |
| 1325 | PASS with notes | console pass 2 below |
| 1326, 1327 | PASS | pytest; console "which sorento bidet has cert" → "1 tap has certificates." plus the block for SRTWT5875 (a Sorento product whose product_type is bidet), no picker |
| 1328 | PASS | pytest; console PPS case |
| 1329 | PASS | console "which basin has photo" → "Types I know: Certification, Product Photos, Product Videos, Technical Specifications." (product-facing only) |
| 1330 | PASS | pytest; console sink case |
| 1331 | PASS | pytest (green on arrival: the AC-1326 bypass already covers the folded token); the pass-3 console miss for this utterance was a reload race, see below |
| 1332 | PASS | pytest, model reader never invoked on a HAS turn; phrase stripping green |
| 1333 | PASS | pytest (parity probe); console pass 6 trace: the promotion pick armed the carry and the "more" page's tool args carried `access_levels: ["Sorento Dealer", "Mocha Dealer", "Cabana Dealer"]` |
| 1334, 1342 | PASS | pytest; `_access_level_codes` accepts codes and bare tier tokens (the pick turn carried "Dealer") |
| 1335 | PASS | pytest, two-company scratch schema; security re-check closed S2 |
| 1336, 1343 | PASS | pytest, same-domain non-page answer and same-domain zero clarify both clear the carry; console pass 7 sequence 2: "more" after the clarify is not paged |
| 1337 | PASS with note | fixed paging phrases; "no more" declines but leaves the carry until the next business turn clears it |
| 1338 | PASS | pytest (register spelling, lookup keyword, regression); console "which item has PPS cert" -> "940 products have PPS certificates." on the head variant that drops PPS before the lane |
| 1339 | PASS | pytest; console "which bathroom accessory has stock" -> 837 (was a silent 0: every one of the 2,040 rows is category-sourced) |
| 1340 | PASS | pytest, category raw and "any product" fallbacks; no "a a match" |
| 1341 | PASS | pytest both directions plus the NULL shared arm; security re-check kill test |
| 1344 | PASS | dash scan of every lane file empty; pre-push guard |
| 1345 | PASS | pytest, seven inflections incl. "sijil" and "PPS certification" |
| 1346 | PASS | pytest; console pass 7 bathroom accessory "Showing 5." for five ids (pass 6 showed 4: ACC-SRT9012's only stock was in inactive warehouse SPARE/P) |
| 1348 | PASS | pytest, `["%"]`, `["%dealer"]`, `["d%r"]` select nothing (security re-check measured `["%"]` selecting seven codes before R24) |
| 1349 | PASS | pytest, `resolve_entity_body(tier_gate=...)` sends the recomposed names; single-tier contacts no longer count promotions of other tiers |
| 1350 | PASS | pytest, page-arm guard kill-tested by the tester; migration test asserts the chain, green after the reparent |
| 1351, 1352 | PASS | pytest (bound words leave the scope term; string bindings filter, numeric stay boosts); console "check stock water closet with s trap 250mm" -> "165 water closets have stock. Showing 5." and "any incoming for water closet with p trap" -> "13 water closets have incoming stock." (both clarified "water closet trap" before R27: a regression of the forward spec path found by the owner) |
| 1353 | PASS | pytest; console run 11 "which item has PPS cert" on the variant where the head drops the raw-"PPS" entity -> "940 products have PPS certificates." (run 10 answered the forward "Please provide the attachment type") |
| 1354 | PASS | pytest (resolver block, fetch args on turn 1 and the "more" page); console "any tap has PPS cert" -> "1 tap has PPS certificates." with the two PPS files only (was seven files across WCM, SPAN, WEPLS, IKRAM) |
| 1355, 1356 | PASS | pytest (matched_specs on the require-only arm, carry-shaped last_result_set, promotion rows ignored, no line without spec words); console "check stock sorento water closet with s trap 250mm" ends "_Matched on: Water Closet, trap length: 250 and trap type: S Trap._" |
| 1357 | PASS | pytest; console: SRTWC286-SH (trap length 250) renders first, was seventh by code |
| 1358 | PASS | pytest, "certainly" / "concert" stay forward, "sijil" and "certified?" are the leg, bare word without punctuation |
| 1359 | PASS | pytest (gate + answer); console "any tap has PPS cert" on the variant where the head drops both entities answers the set, not the attachment-type ask |
| 1347 | PASS | pytest, four paging terms; console pass 8 sequence 2: the carry-less "more" answers "2,704 products have certificates. Showing 5." (the reused certificate question, unscoped) instead of "I don't know 'more'" |

## Console pass 7 (AC-1325, FINAL after fix round 3), lane backend WITHOUT reload, commit d331bec78

| Utterance | Reply first line |
|-----------|------------------|
| check stock srtwc286 | Stock details found for the requested products. (forward path, no header) |
| which water tap has cert | I don't know 'water tap' as a product type. Did you mean tap? |
| which tap has cert | 1,256 taps have certificates. Showing 5. (908 before R15: category-filed taps now count) |
| which sorento bidet has cert | 1 tap has certificates. (SRTWT5875) |
| which basin got stock | 539 wash basins have stock. Showing 5. (active warehouses only, R22) |
| which item has PPS cert | 940 products have PPS certificates. Showing 5. |
| which basin has photo | I don't know 'photo' as a document type. Types I know: Certification, Product Photos, Product Videos, Technical Specifications. |
| which sink has incoming | 52 kitchen sinks have incoming stock. Showing 5. |
| which bathroom accessory has stock | 837 bathroom accessories have stock. Showing 5. (pass 5: "Couldn't find a a match with stock.") |
| any shower set on promo (contact 900000052) | Which access level do you need for shower set? |

Multi-turn sequences (`pass6-sequences.yaml` in the session scratchpad, three cases):

| Sequence | Result |
|----------|--------|
| which tap has cert / more / more | Showing 5. / Showing 6 to 10. / Showing 11 to 15. (AC-1317) |
| which tap has cert / which water tap has cert / more | set answer / clarify / NOT paged (AC-1343). The third reply on d331bec78 read "I don't know 'more' as a product type": the head's entity reuse re-asked the certificate question with "more" as the only remainder word (R23). Rerun on 242af5589 (console pass 8, all three sequences PASS): "2,704 products have certificates. Showing 5." |
| any shower set on promo / 1 / more | tier ask / "I found 4 promotions for shower set." on the check_promotion lane with `access_levels: ["Dealer"]` / page with the recomposed tiers in the tool args (AC-1333). Promotion sets page by product, so a promotion file attached to several products appears on both pages; accepted |

Console pass 9 (final, commit 2a4112354 with origin/main merged): the same ten utterances and three sequences, identical replies to pass 7/8 (`console-run-9.txt`, `console-run-9-sequences.txt`).

Transcripts: `console-run-7.txt`, `console-run-7-sequences.txt`, `console-run-8-sequences.txt`, `console-run-9*.txt` in the session scratchpad.

## Console runs 10 and 11: the committed case file (owner test round, R27 and R28)

`tests/chatbot/console_cases/2026-09-11-attribute-first-asks.yaml` (14 cases: the ten utterances, the two spec-word turns, the three sequences) run with `scripts/chatbot_console_check.py` against the lane backend on the prod copy. Run 10 (commit e678723fb, R27 in): 13 passed, 1 failed: "which item has PPS cert" answered the forward attachment-type ask on a parser variant the head empties (R28). Run 11 (commit 238adb4df): 14 passed, 0 failed. Transcripts `console-run-10.txt`, `console-run-11.txt` in the session scratchpad.

Console runs 12 to 14 (owner round continued, commits 4d7717422 -> b71209bf4): run 12 = 14 passed (R29/R30 in, Match line still absent live: the live cause was the carry-shaped `last_result_set`, R31); run 13 = 13 passed, 1 failed on an OpenAI 429 parser rate limit (turn status `failed`, not code); run 14 on b71209bf4 = 14 passed, plus "any tap has PPS cert" twice with PPS files only. Transcripts `console-run-12.txt` to `console-run-14.txt`, `console-strap-13.txt`, `console-pps-14-*.txt` in the session scratchpad.

Owner observations on the local stack that are DATA or configuration, not lane defects (plan row above the R27 table): "sorento bidet" is filed under category Tap (header noun), "water tap" and "valve" are missing category search synonyms, certificate PC 000373 (WCM Cold Tap) is linked to three Mocha kitchen sinks. Follow-up slice candidates: header echoes the customer's product_type word; the attachments listing filters by the recovered scheme.

## Console pass 4 (AC-1325, FINAL), lane backend started WITHOUT reload, commit 677d240b1

| Utterance | Reply first line |
|-----------|------------------|
| check stock srtwc286 | Stock details found for the requested products. (forward path, no header) |
| which water tap has cert | I don't know 'water tap' as a product type. Did you mean tap? |
| which tap has cert | 908 taps have certificates. Showing 5. |
| which sorento bidet has cert | 1 tap has certificates. (SRTWT5875, block with its WCM certificate file) |
| which basin got stock | 427 wash basins have stock. Showing 5. |
| which item has PPS cert | 940 products have PPS certificates. Showing 5. |
| which basin has photo | I don't know 'photo' as a document type. Types I know: Certification, Product Photos, Product Videos, Technical Specifications. |
| which sink has incoming | 47 kitchen sinks have incoming stock. Showing 5. |
| any shower set on promo (contact 900000052) | Which access level do you need for shower set? (existing promotion flow) |
| which tap has cert / more / more | Showing 5. / Showing 6 to 10. / Showing 11 to 15. |

Every page rendered five distinct products. Full transcript in the session scratchpad `console-run-4.txt`.

## Console pass 2 (AC-1325), lane backend, dry-run turns

| Utterance | Reply first line | Verdict |
|-----------|------------------|---------|
| check stock srtwc286 | Stock details found for the requested products. | forward path kept |
| which water tap has cert | I don't know 'water tap' as a product type. Did you mean tap? | clarify |
| which tap has cert | 908 taps have certificates. Showing 5. | set answer with files |
| which sorento bidet has cert | 1 tap has certificates. (SRTWT5875) | set answer; noun could read "Sorento bidet", accepted |
| which basin got stock | 427 wash basins have stock. Showing 5. | set answer |
| which item has PPS cert | 940 products have certificates. Showing 5. | correct set; header to gain "PPS" (polish) |
| which basin has photo | I don't know 'photo' as a document type. Types I know: ... | clarify; list to be product-facing only (polish) |
| which sink has incoming | 47 kitchen sinks have incoming stock. Showing 5. | set answer |
| any shower set on promo (contact 900000052) | Which access level do you need for shower set? 1. Dealer - has promotion | the promotion domain keeps its existing access-level flow |
| more, more (after the tap answer) | Showing 6 to 10. / Showing 11 to 15. | paging |

Pass 3 (after the polish commit) showed two regressions. "which sorento bidet has cert" (single
product entity variant) answered the miss copy once: its stored trace has NO fetch stage, while
three identical turns run afterwards on the same code all fetched and answered "1 tap has
certificates." The turn ran while uvicorn was reloading the polish commit; the final pass runs on a
backend started without reload. "which item has PPS cert" (attachment raw "PPS cert" variant)
answered "I don't know 'item pps'": the model phrase reader was invoked on the set path (R13,
AC-1332), fixed by the coder.

The parser emits different entities for the same sentence between runs (category vs product hint,
"PPS" vs "PPS cert", one entity "Sorento bidet" vs brand + category). The lane handles every
variant seen; the variants are listed under R2, R3, R4, R7, R12, R13 in the plan.

Pass 1 (before fix round 2) had five wrong turns; all are recorded with cause and rule in the plan
section "Console fix round 2".

## Latency (REV-N3)

`resolve_reference_post` for "which tap has cert" on the prod copy (908 qualifying families, 200
candidates emitted), three consecutive in-process calls: 203 ms, 91 ms, 133 ms. The model phrase
reader is off on the set path, so the resolve is SQL only.

## Review rounds

Fix round 3 re-check (11 Sep): security-reviewer closed B1 and S2, raised the certificate leg (R17, blocker) and the tier-token mismatch (R18); reviewer closed B1 and S4, found the same-domain carry gap (R19) and the certificate leg (N1). All adopted, red test first. Round 3 re-check verdicts (11 Sep, at d331bec78): security-reviewer closed the certificate blocker (all five legs 0 on a foreign child, 1 on an own child, shared NULL arm counts), R14 has no cross-company scheme oracle, R22 only narrows; open should-fix R25 (single-tier contacts) and nit R24 (LIKE wildcard), both fixed in 721d6313d / 2a4112354 with red tests first. Reviewer: R19 kill test bites (1 failed / 88 passed on revert), R17 both arms pinned (strict equality fails 16 tests), R15 has one caller and the AC-1308 repair still proves the union, R14 order of operations verified, R18 LIKE escaping (R24), R22 keeps the four scope tests meaningful. Verdict from both: ready.

Reviewer rounds 4 and 5 (11 Sep, owner test round): round 4 at 4d7717422 verified R27 to R30 with kill tests (OR to AND across keys pinned; bound-phrase strip and string membership load-bearing for each other; certificate_ids narrow only; forward spec_asked untouched) and raised R33 (substring cert regex). Round 5 at b71209bf4 closed R33 with its own probe sentences, kill-tested R31's third gate and R34's two arms, and measured R32's unbounded DISTINCT ON at 0.08 to 0.33 s on the prod copy (worst case the bare certificate ask over 2,704 families). Verdict: ready. Open nits, all coverage or watch items: R34 negative controls and a forward Match-line test (added after the verdict), and the R32 cost growing with catalogue size (mechanical fix noted).

Security review (11 Sep): one blocker (paging a promotion set dropped the tier filter), two
should-fix (promotion leg blind to access levels; class-label helpers cross-company). Correctness
review (11 Sep): one blocker (the legs' EXISTS subqueries escape the company listener; the AC-1310
tests could not see it), six should-fix. All adopted as rules in the plan (SEC-*, REV-*), each with a
red test before the fix. Repo-wide audit of the EXISTS pattern filed as #832.

## Review fix round (26 Sep 2026, reviewer pass on f2375f402)

Cloud lane (CI Postgres, empty seeded schema, `.env.ci-tests`). Every fix red first, then green,
then kill-tested: each repair was removed and the named tests went red.

| Finding | Tests (`tests/chatbot/test_counted_set_review_fixes.py` unless named) | Kill |
|---|---|---|
| B1 | `test_a_cert_property_phrase_is_the_bare_certificate_leg`, `test_which_tap_has_valid_cert_answers_the_whole_certified_set` | RED |
| B2 | `test_an_unknown_scheme_names_the_schemes_on_file_and_lists_nothing`, `..._as_an_attachment_type_entity_clarifies_too`, `..._on_a_class_word_no_code_carries_...`, `test_an_honest_zero_names_the_set_and_fetches_nothing` | RED (fetch refusal; did-you-mean skip) |
| B3 | `test_a_listed_set_asks_the_tool_for_enough_rows`, `test_a_row_cap_that_still_cuts_the_set_says_how_many_are_listed` | RED (row limit; rendered count) |
| B4 K4 | `test_the_recount_after_how_many_keeps_the_dealers_stock_visibility` | RED |
| B4 K5 | `test_an_availability_row_is_numbered_with_its_product_code` | RED |
| S1 | `test_a_bare_count_answers_the_question_whatever_the_parser_made_of_it`, `test_a_bare_count_message_is_read_...`, `test_anything_but_a_bare_count_is_left_to_the_parser` | RED |
| S2 | `test_counted_set_no_paging.py::test_every_leg_past_the_list_limit_states_the_count_and_asks` | RED |
| N2 | `test_a_count_that_names_nothing_still_arms_the_question` | RED |

Gates on the final tree: `tests/chatbot` (CI excludes, not serial_ddl) plus the predicate, resolver,
spec search, spec fallback and migration 511 files: 2949 passed, 214 skipped, 33 xfailed, 0 failed.
Console cases: 22 in the yaml (three added for B1, B2, S1); 0 run here (no parser key on a cloud
lane). The S1 live-parser pass is owed locally.

## Round 4: owner console test of round 3 (27 Sep 2026 00:03 MYT), R1 to R7

All new tests are in `tests/chatbot/test_attribute_asks_round4.py` (34), plus
`tests/test_brand_chatbot_weight_route.py`, `tests/test_migration_bcw_0001_brand_chatbot_weight.py`
and three Brands vitest files. Each was red on `8618622b1` before its fix. The eight exchanges
replay from `tests/chatbot/fixtures/owner_console_2026_09_27.json`; every reply of the round 2, 3
and 4 files is scanned for snake_case.

Kill tests (a throwaway worktree, one mutation each, the ruling's own tests run): 13 of 13 RED.

| Kill | Mutation | Result |
|---|---|---|
| K1 R1 | brands ranked by count, weight ignored | RED |
| K2 R2 | header filters back on one line | RED |
| K3 R2 | class head split off ("water closet p trap") | RED |
| K4 R3 | every tool field on the row | RED |
| K5 R4 | near miss not passed to the reply | RED |
| K6 R5 | clarify answer ignored | RED |
| K7 R5 | clarify carry never cleared | RED |
| K8 R6 | unknown values never found | RED |
| K9 R6 | covered-phrase guard removed ("wall hung" read as unknown) | RED |
| K10 R7 | slug display off | RED |
| K11 R7 | Specs line guard off | RED |
| K12 R7 | domain key printed raw | RED |
| K13 R1 | migration seeds Sorento 0 | RED |

Gates on the merged tree (origin/main `232182ae5` merged, 511 re-parented onto
`sales_0002_team_leader`, one alembic head `bcw_0001_brand_chatbot_weight`): `tests/chatbot`
(CI excludes, not serial_ddl) plus predicate, resolver, spec search, spec list, brand routes and
migrations 511 / bcd_0001 / bcw_0001: 3010 passed, 213 skipped, 33 xfailed, 0 failed (one
worker error in that run did not reproduce on a rerun of `tests/chatbot`). Brands vitest: 27
passed. `tests/test_cs_pinpoint_routing.py::test_resolver_valid_pin_returns_assignee` fails
identically on 1683cb2f1, outside this lane. Live parser: not run (no parser key on this VM).

## Round 5: reviewer pass at d6fa2b31 (26 Sep 2026), B1, S1 to S3, N1 to N4

New tests: `tests/chatbot/test_attribute_asks_round5.py` (17), two Brands vitest cases in
`BrandFormDialog.chatbotWeight.test.tsx` and one in `[id]/page.chatbotWeight.test.tsx`, one MCP
presenter test. Red commits `a9ba79f14` (backend and MCP: 16 of 17 red on d6fa2b31e code, the one
green is the "t trap" guard that must stay green) and `2d34a692d` (vitest: 3 red). N4 is a
missing test for code that exists; its red is the kill test K12.

Kill tests (a throwaway worktree, one mutation each): 12 of 12 RED.

| Kill | Mutation | Result |
|---|---|---|
| K1 B1 | value-position check always true | RED, 7 |
| K2 B1 | product-name check always false | RED, 1 |
| K3 S1 | list values dropped from the near miss | RED, 2 |
| K4 S1 | other total summed per value, not per product | RED, 2 |
| K5 S2 | tag reads the helper without title case | RED, 1 |
| K6 S3 | bcw_0001 test file loses `serial_ddl` | RED, 1 |
| K7 N1 | list display_value not joined | RED, 1 |
| K8 N2 | miss value always lower-cased | RED, 1 |
| K9 N3 | dialog zod `.max(9999)` removed | RED, 1 |
| K10 N3 | dialog input `max` removed | RED, 1 |
| K11 N3 | record page Save not bounded | RED, 1 |
| K12 N4 | presenter ignores `display_value` | RED, 1 |

Gates (`scripts/cloud-env-setup.sh`, `SORENTO_ENV_FILE=.env.ci-tests`, CI flags `-n 4 --dist
loadfile -m "not serial_ddl"`, `tests/ci_excluded.txt` ignores, `tests/test_migration_*.py`
ignored): `tests/chatbot` plus brand weight route, predicate service, spec search and boost, spec
registry, spec list, resolve predicate / spec fallback / unrecognized terms / raw text, company
scope, spec listener, lookup resolver and the three dealer kit tag data files: 3247 passed, 214
skipped, 33 xfailed, 0 failed. Serially (`-p no:xdist`): migrations bcw_0001, bcd_0001 and 511,
19 passed; `-m serial_ddl` over the two brand migration files, 10 passed. Brands vitest 30 passed.
MCP `tests/test_presenters.py` 117 passed. py3.12 compile on the touched .py files ok, dash guard
0 hits, 1 alembic head (`bcw_0001_brand_chatbot_weight`). Live parser: not run (no parser key on
this VM); the console yaml carries three round 5 B1 cases.

## Round 6: reviewer pass at 34cb4697 (26 Sep 2026), B1-r5, N-r5-1 to N-r5-3

New tests: `tests/chatbot/test_attribute_asks_round6.py` (17) and one case in
`[id]/page.chatbotWeight.test.tsx`. Red commit `bdc52cca6`: 12 of 17 backend red on 34cb4697e
code (B1-r5 x4, the reviewer's "Basket Trap" reproduction included; N-r5-2 x2; N-r5-3 x6), the
5 green are guards ("T Trap" and "Q TRAP" as whole words still name a product; "PVC", "Matt
black" and "Gunmetal" read as before); vitest 1 red.

Kill tests (one mutation each, restored after): 9 of 9 RED.

| Kill | Mutation | Result |
|---|---|---|
| KB1r5a | back to the substring ILIKE (`%t trap%`) | RED, 4 |
| KB1r5b | leading `\m` dropped | RED, 4 |
| KB1r5c | description branch dropped | RED, 1 |
| KB1r5d | regex fed to ILIKE | RED, 2 |
| KN2 | whole value lower-cased unless all capitals (round 5 code) | RED, 2 |
| KN3a | "any grease trap" case back to its one exact phrase | RED, 1 |
| KN3b | "any click clack waste" case missing | RED, 1 |
| KN1a | record page message not rendered | RED, 1 |
| KN1b | record page upper bound wrong | RED, 1 |

Gates (same flags as round 5): `tests/chatbot` plus the same touched files, 3271 passed, 214
skipped, 33 xfailed, 0 failed. Brands vitest 31 passed. py3.12 compile ok, dash guard 0 hits,
1 alembic head (`bcw_0001_brand_chatbot_weight`). Live parser: not run (no parser key on this
VM); the console yaml now sends all six reviewer phrases.

## Round 7: owner hand test of rounds 4 to 6 (27 Sep 2026 10:37 MYT), items 1 to 7

New tests: `tests/chatbot/test_attribute_asks_round7.py` (29), every one on Leena's exact
messages against seeded data (the 65502 sinks, the 8840 and 7604 families with their X bowls,
close coupled P trap water closets in three brands, kitchen taps with a certificate and a
product photo each). The MCP tools are stubbed in their real presenter shapes; the attachments
stub returns photos AND certificates unless the call narrows. Red commit `4cb25f978`: 22 of 28
red on 898833d62 (item 1 fails on the owner's own line `I did not understand "eta", so it is
not part of this search.`), 6 guards green on both sides; the 29th
(`test_f3_words_every_variant_carries_leave_the_code_alone`) guards the family pick itself.

Kill tests (one mutation each, restored after): 13 of 13 RED.

| Kill | Mutation | Result |
|---|---|---|
| K1 | leg words not stripped from the query | RED, 4 |
| K2 | incoming set keeps two-line rows | RED, 2 |
| K3 | certificate set keeps two-line rows | RED, 4 |
| K4 | the ask's own word projected as a missing field | RED, 2 |
| K5 | no code family narrowing in the resolver | RED, 8 |
| K6 | family without the X sibling | RED, 8 |
| K7 | the descriptor's category token kept | RED, 3 |
| K8 | two product type lines in the header | RED, 4 |
| K9 | a placed scope term still reported missing | RED, 6 |
| K10 | brand pick not read | RED, 4 |
| K11 | no carry for the "Other brands" offer | RED, 4 |
| K12 | the last answer's rows feed a page's zero stock ladder | RED, 4 |
| K13 | the bare certificate leg sends no certificate ids | RED, 3 |

Regression found and fixed in the lane: a bare "promo" ask lost the tier question once "promo"
was stripped as a leg word; a message made only of the ask's own words is now left as before
(`test_rearch_r6_review_round.py` green). Ten tests encoding the replaced rulings (two-line
certificate and incoming rows, no certificate ids on a bare leg) were amended with a note each.

Gates (`scripts/cloud-env-setup.sh`, `SORENTO_ENV_FILE=.env.ci-tests`, CI flags `-n 4 --dist
loadfile -m "not serial_ddl"`, `ci_excluded.txt` ignores, migration glob ignored):
`tests/chatbot` plus the resolver, predicate, spec search and certificate resolver files, 3481
passed, 214 skipped, 33 xfailed, 0 failed. py3.12 compile ok, dash guard 0 hits, 1 alembic head
on the branch (`bcw_0001_brand_chatbot_weight`, no migration this round). Parser prompt: not
changed. Live parser: not run (no parser key on this VM).

## Round 8: owner retest of round 7 (27 Sep 2026 12:59 MYT), F1 grounding, F2 one shape per domain

New tests: `tests/chatbot/test_spec_grounding.py` (45: the owner's four descriptor items, plus
every finish, mounting, trap and material choice the registry holds glued before and after a
product type, eight measurements with their key word, five unknown values, typos),
`tests/chatbot/test_spec_grounding_prompt.py` (7: the Specification lines rendered from the
registry, a staff word reaching them, the addendum, the strict schema, the kind row, the
migration publishing unlabelled and idempotent), `tests/chatbot/test_attribute_asks_round8.py`
(35: the owner's messages through `engine.run_turn` with the real resolver and registry, the
stock tool in detailed and compact mode with the footer stamp, one-shape comparisons of set rows
against the product-code answer for stock, incoming, certificates and price, 18 registry
descriptor asks across stock, incoming and certificate, and the retest in order). Red commits:
`d96d3feba` (45 of 45 red without the grounding module), `fd25ec1f4` (16 of 34 red on
`fe9cdc6b8`).

Kill tests (one mutation each, restored after): 13 of 13 RED.

| Kill | Mutation | Result |
|---|---|---|
| K1 | no grounding step in the engine | RED, 4 |
| K2 | category keeps the glued descriptor | RED, 22 |
| K3 | misfiled attachment_type kept | RED, 20 |
| K4 | document domain not corrected | RED, 3 |
| K5 | no unknown-value detection | RED, 9 |
| K6 | unknown values not sent to the resolver | RED, 2 |
| K7 | grounded numbers not membership | RED, 3 |
| K8 | a follow-up drops the spec fields | RED, 4 |
| K9 | near-miss empty list bound as a JSON string | RED, 3 |
| K10 | the intro does not name the set | RED, 16 |
| K11 | set rows not the product-code rows | RED, 13 |
| K12 | no Specification lines in the prompt | RED, 3 |
| K13 | no price set leg | RED, 1 |

Found in the lane: `_near_miss` bound its empty-list fallback as the JSON string `"[]"`, so any
zero set with a member missing the key crashed the resolver and the turn fell back to the generic
"Could not find incoming for category ..." miss (the owner's item 2 reply). Fixed and pinned.

Superseded tests amended to the round 8 ruling (a note on each): 78 across rounds 2, 3, 4 and 7,
`test_lane_require`, `test_counted_set_no_paging`, `test_counted_set_review_fixes` and
`test_reverse_asks_owner_phrasings` (bold header lines, count sentence and two-line rows
retired). The prompt golden file now pins domains and kinds only (the registry lines are pinned on
a blank schema); the addendum-order checks strip `SPECIFICATION_ADDENDUM` first. One flaky
assertion fixed: the availability dealer test checked `"37" not in text` while the seeded code is
random hex.

Gates (`scripts/cloud-env-setup.sh`, `SORENTO_ENV_FILE=.env.ci-tests`, CI flags `-n 4 --dist
loadfile -m "not serial_ddl"`, `ci_excluded.txt` ignores, migration glob ignored): `tests/chatbot`
plus the resolver, predicate, spec search, spec list and brand weight files, 3451 passed, 213
skipped, 33 xfailed, then the last 2 fixed (38 of 38 in their files). Parser prompt: new version
published unlabelled by `spk_0001_specification_kind`, template 95,278 to 106,386 characters.
Live parser: not run (no parser key on this VM).

## Deviations

- Phase 1 skipped by design: no UI surface. The lavish review page stands in for the mock.
- Console verification on the prod copy needed a minted integration key that is ALSO the `.env`
  `EXTERNAL_API_KEY` (scope resolver drift, issue #831, outside this lane).
- Alias and scheme lookup sets are created empty; the owner enters options on System > Lookup Sets.

## Round 9: one reply structure for every attribute ask (28 Sep 2026)

Red first: `17057c431` (`tests/chatbot/test_attribute_asks_round9.py`, the five turns of the
owner's 28 Sep test, all five red on `ac9c51157`). Green from `34da650ff`.

| Suite (CI way: `scripts/cloud-env-setup.sh`, `.env.ci-tests`) | Result |
|---|---|
| `test_attribute_asks_round9.py` | 9 passed |
| `test_spec_grounding_prompt.py` (spk_0002 publish, idempotence, chain) | 10 passed |
| `tests/chatbot` + predicate + spec search, xdist, at `a7c33311b` | 3,572 passed, 0 failed, 213 skipped, 33 xfailed |
| Backend main job (xdist, ci_excluded, no scm, not serial_ddl) on the main merge `e392a1c71` | 19,125 passed, 0 failed, 250 skipped, 33 xfailed |
| Migration tests, serial | 458 passed, 1 skipped |
| serial_ddl | 1 passed |
| Vitest: pre-push touched (3 brands files) + chatbot-console and brands | 56 passed (10 files) |

Earlier-round assertions of the retired sentences (near miss "I looked for", "I don't know ...
I know ...", "did you mean", "too many to list ... how many should I show", the default brand
and its "Other brands" line, the leg said twice in the intro) are amended to the round 9 ruling,
each with a note naming it.
