# PLAN: CHAT-CODE-LOOKUP (chatbot code lookups toward the owner's 97% bar)

Status: Round 2 in progress (Build): R2-1, R2-2, R2-4, R2-5 committed; R2-6 prompt-only near-code rule and three round-2 review fixes in red-first; round 1 was crew-done at 36b75db496. Stacked on #1755, retarget after it merges. Track: L, LEAD pattern, local lane. Branch
`crew/chat-code-lookup`, base origin/main 20c191d861.

Owner approval: 17 Oct ~14:10 ("Ok" to a code-lookup lane, in parallel with steps 1-2).

## Goal

A staff message naming a product code (exact, partial, typo, family or variant, several codes, code + attribute) gets
the right product(s) and the asked fact, without needless pickers.

Yardstick: EVAL-BENCH bench (crew-only data, `crew/state/eval/cp0/`), grader `scripts/chatbot_eval/targets.py` on main
(identical to the pinned grader 230642edc: `git diff --stat 230642edc 8707b950e7 -- sorento_crm_backend/scripts/chatbot_eval/`
is empty). Held-out today 75.0 / 74.3 / 73.6% (3 runs); TRAIN on main (S6 main arm, c2a3b162e + parser v63) regraded
at $0 with main's grader: 81 / 94 judged = 86.2%, 3 unknown (price-tier access asks), 13 fail. Owner bar: 97%.

## Design rules (crew memory, owner)

- D2 (owner, 16 Oct): code only SHORTLISTS candidates (exact code, name and spec search, near spellings); the parser
  CHOOSES from them or says "none", and then we ask. Code never decides meaning.
- Trust parser registry output: the LLM fixes typos into registry values; code keeps a valid value, never reverts to the
  typed word. No fuzzy matching and no word rules in code.
- Clarify, don't assume (configurable ask / default policy).
- A held family member resolves to it (owner CP0 c08).
- No needless "which one?" after a full answer. Never show internal ids / UUIDs; real names for candidates.
- Resolve, then reuse the answer: new parameters only resolve products; the answer domain is reused.

## Coordination

- CHAT-CLOSED-LISTS (#1755, branch `claude/chat-closed-lists-x6ymb5`) owns the candidate-list + parser-picks mechanism
  (`app/services/chatbot/head/candidates.py`, `closed_lists_prompt.py`, `held_family.py`). Product-code candidates
  reuse it; sequencing (stack vs wait) is a crew ask.
- CHAT-COMPOSER: reply wording. CHAT-STATE (#1747): conversation state. Changes here stay separable.

## Steps

1. Root-cause every failing TRAIN code-lookup item into classes with counts (parse, candidate retrieval, family /
   variant, multi-code, answer, grader / gold). Grader / gold suspicions go to crew for EVAL-BENCH.
2. Behaviour card as ONE crew ask before code (rules for the top classes, real TRAIN examples, edge cases, at most 5
   questions with recommendations): `documentation/plans/chatbot/CARD-chat-code-lookup-17oct.md`.
3. Red-first slices behind a gate (gate off = byte-identical to main, proven by the $0 L1 replay vs main), each
   measured on TRAIN (crew approves each live envelope, cap about $5). Crew runs held-out.

## Root cause (step 1)

Done 17 Oct; table and evidence in `CARD-chat-code-lookup-17oct.md`. 13 TRAIN fails on main: bench / harness 3 (photo
rows time out in the rolled-back runner), grader / gold 3, parse 4 (2 typos with no choose step, a dropped attribute,
"when deliver" with no status), candidate retrieval 1 (set-only code), answer 1 (picker after a full answer), out of
scope 1 (DO number, #1648). Multi-code and family / variant: 0 fails. Harness and gold fixes alone: about 93.3%.

## Held-out weight (crew, 17 Oct, counts by failing check only, 149 items, runs 1/2/3)

no_picker 17/18/19, fact 14/14/14, tool_ran 4, no_clarify 3-4, clarified 3, named 2, no_qty_ask 1, code 1; failed rows
37/38/39. Rows failing only no_picker 13/13/14 (about 9 points): "did you mean" wording ~8, "which one" 2-3, other 3.
So S1 (near code) is the top lever, then the fact class (S6 domain reuse). EVAL-BENCH rulings: a typo with one clearly
meant existing code -> answer that code; a bare code after an ETA reply -> the incoming fact.

## Slices (red-first, each behind the CODE LOOKUP prompt marker, measured on TRAIN)

- S1 near code (R1, Q2): near-code floor 0.30, top 5, for a code word with no exact hit; a product pick must be on the
  candidate block. TRAIN: cp0-9aa22e4c4b, cp0-29d7825ee6.
- S2 set-only codes (R2, Q4). TRAIN: cp0-9793ea25dc.
- S3 asked attribute carried (R4), prompt. TRAIN: cp0-39e88ea65f (also needs the EVAL-BENCH grader fix).
- S4 "when deliver" = outstanding (Q5), prompt. TRAIN: cp0-3fa0985bd4.
- S5 held family member for a typed family code (R5). Owner c08.
- S6 domain reuse, count = stock (R6), prompt. #1755-arm TRAIN: cp0-7ce4ddc2da, cp0-df53e90ebf.
- R3 dropped: owner ruling 17 Oct keeps the sibling picker after a full answer (`answer.py:4846-4848`).

## Evidence

- Red-first per slice and round, kill-proof each (PR #1761 crew-notes): A ad4389c047 -> 4717509497 (+ lead-approved
  test fixes 9f8d713e91); B 1833644813 -> e6eb546af0; C 5ac8b3fec6 -> 50495b919e; round 1 94df7d3722 -> 9e36f4e7b8;
  round 2 bbd92f9851 -> 6192d8dedf (+ lead-approved G1 assertion 028fb6b07e); round 3 d39397d628 -> 24cb58be6b;
  last should-fix 1520f6039c -> 36b75db496 (one set named twice prints its members once).
- Review: reviewer rounds on 50495b919e, 9e36f4e7b8, 028fb6b07e, 24cb58be6b; security-reviewer NEEDS WORK twice, then
  PASS at 028fb6b07e. Lead-accepted nits: an alternative for a discontinued set member keeps its own price; the
  group_by path skips the set heading (no priced tool groups in a set domain today). Forwarded to CHAT-CLOSED-LISTS:
  first-line-only shortlist, same-line forgery in `_code_label`.
- L1 ($0, rolled-back replay_rows, clean sorento_eval_base, no rows written): vs #1755 0 unintended both tiers; vs main
  grounding 0, turn 3 (cand-0036, cand-0289, chatfind-05), the same three main vs #1755 shows.
- Live TRAIN (97 code-lookup turns x1, grader f58964a232): main 81/94 86.2%, arm 1 (50495b919e + v160) 83/94 88.3%,
  arm 2 (028fb6b07e + v161) 84/94 89.4%; spend USD 1.19 of the two USD 1 envelopes. "When deliver" passed 1 of 2 runs.
- Prompt: unlabelled v159, v160, v161 on sorento_cagent_stack; production label v63 untouched.

## Round 2 (crew, 17 Oct evening: "run as much parallel as possible")

Base: #1755 head f0f94bc385 merged in (c90872a779; a merge, not a rebase, because the repo forbids force-pushing a PR
branch). EVAL-BENCH's answer.py clash patch with #1760 is applied when crew relays it.

- R2-1 "when deliver" reliable (passed 1 of 2 live runs; the parser left status null in arm 2 although the CODE LOOKUP
  section names it, because v63 ORDER_STATUS FILTER says status comes ONLY from its listed words). Parser-level gold:
  `scripts/chatbot_eval/gold/parser_when_deliver.jsonl`, synthetic paraphrases (English, Malay, Chinese, typos, with and
  without a customer) expecting status "outstanding", domain_hint "order", the product entity (the "no delivery attribute" check was
  dropped: no reader of a "delivery" attribute exists, crew 17 Oct),
  plus negative controls (an arrival / ETA ask stays incoming; "delivered" stays delivered; a dated DO list stays a DO
  list). A parser-only runner `scripts/chatbot_eval/parser_gold.py` calls only the semantic parser with a pinned
  unlabelled prompt version, N repeats, metered with a USD cap; its pure grading is unit-tested red-first. Then the
  section's rule names its words as additions to the ORDER_STATUS FILTER list; measured old (v161 lineage) vs new, N=3.
- R2-2 the TRAIN code failures left after EVAL-BENCH rounds 32-34 regrade (crew counts: parse 4, retrieval 1, answer 1).
- R2-3 generalise: crew sends held-out failure CLASSES after the 18 Oct combined run; EVAL-SETS adds ~150 real TRAIN
  code-lookup items. Never held-out items.
- Live: cap USD 3 per run; at most 2 stacks machine-wide via crew/state/live-slots/<LANE> (mkdir to take, rmdir on stop).

### Round 2 progress (17 Oct, late)

- Regrade at $0, grader c744bfe9f1 (#1762, rounds 32-34) on EVAL-BENCH's train_code export (93 items, 90 judged):
  main 77 (85.6%), lane arm 1 80 (88.9%), arm 2 79 (87.8%). Arm 2 fails: grader / gold 6 (4 staff family stock asks
  stay list_all by owner S12 + F1, cp0-f61e0d4814 company scope, cp0-9793ea25dc set code; EVAL-BENCH round 35),
  parse 3 (csk11b class, cp0-df53e90ebf domain reuse -> CHAT-STATE r2, when deliver), answer 2 (cp0-232723529a file
  miss on the held member, cp0-d9971420da #1648).
- R2-1 red 661228fed6 + 9b0016b15a, fix 6e962eda8e (parser gold runner + when-deliver rule); live-path import bug red
  2b79a2da31, fix 498c3559b6. R2-2 red b3851e6310, fix 5ffa13b2da (code-pick rules, held member with no file).
- Gold fixes c735ac7c67. R2-4 red 7871575cd4, fix 545a00b8f7 (refuse an unsafe pick only on a true tie or several
  holders with none exact; csk11b 0.625 vs 0.556 is not a tie and stays red by design). #1755 merged in at 12fe75451b;
  L1 vs #1755 6402ad4af9: grounding 0, turn 0/585 unintended.
- parser_gold registers the company scope listeners: red 6c76f0636e, fix bed381ed79. Parser golds (scoped, base v163
  vs lane v169, N=3): when-deliver positives outstanding 0/42 -> 21/42; typo 11/18 -> 18/18; tie 0/12 and partial
  0/12 both (handled post-parse by R2-4); set + spec 0/9 both (prompt-only by design).
- R2-5: the rule at prompt END lost to the base ORDER_STATUS FILTER, so the publisher inserts the when-deliver class
  into that list (anchor must occur exactly once or it refuses): red 0de4466447, fix 22d32d88c3, published v170
  ee73a6b6 (= v163 + insertion + section). Parser gold on v170: pending.
- train2_code main arm (63f4fe47c + v63): REAL 158/192 (82.3%), USD 1.23. Lane arm and a main re-run wait for
  EVAL-BENCH's replay.py scope-listener fix (#1775): replay.py never registered the listeners, so cp0-f61e0d4814 was
  an unscoped eval read, not a prod leak. Live runs use stack-ccl.env (own ports 8192 / 8292).

### Round 2 progress (18 Oct)

- R2-5 measured (parser gold, N=3): when-deliver positives v163 0/42, v169 21/42, v170 31/42; negatives 30/30. Left:
  Malay "sampai ke kedai" (arrival-close) and a first-person recipient ("delivered to us"); not chased (anti-overfit).
- R2-3 made-up family codes: TRAIN $0 repro finds only deterministic resolver lists (did-you-mean, Related products,
  Which product) naming scoped catalogue candidates absent from tool_results; crew default is a grader gap (a59234050).
- Round-2 review: blocker (R2-4 tie refusal on an exact typed code) red 43573ebbec, fix 2dcb5c0eba; held member
  localised + miss extras red 9890016a2a, fix 66de1934e3, unplaced line parity red 7c9093a071, fix 7254e96dd0;
  parser_gold pin check red 3e90f663ce, fix 0210e3dced. Re-review READY; L1 at 0210e3dced 0 / 1506 grounding,
  0 / 585 turn. Security PASS.
- R2-6 (crew option (c), prompt-only "pick a near code only when no other listed code is as close") had no effect and
  was reverted (a6bbb39199). One experiment with the D2 shortlist score shown on each near-code line (v173, patch
  kept at scratchpad exp_c_score_lines.patch, never committed) had no effect either: with three near codes listed at an
  equal visible score 0.64, the parser still picked the first one 3/3. Parser gold N=3 on v170 / v171 / v172 / v173:
  amb 0/18, tie 0/12, part 0/12, trans 6/12 on every version.
- Finding: the parser (gpt-5.4-mini) picks the FIRST listed near code whatever the prompt says (it ignores an explicit
  equal-tie rule and visible equal scores; position bias). R2-4's post-parse refusal is the only effective guard for
  true ties and partials. The csk11b class (one near code scores higher) stays red. Crew raises the parser-model
  choice with the owner.
- The dead "Several listed codes" pick-none sentences were dropped (d422b3afce, v172).
