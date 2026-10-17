# PLAN: CHAT-CODE-LOOKUP (chatbot code lookups toward the owner's 97% bar)

Status: Merge-ready pending crew (crew-done 17 Oct at the last fix 36b75db496: reviewer READY, security PASS, L1 0 vs #1755, live TRAIN arm 2 89.4% vs main 86.2%); stacked on #1755, retarget after it merges. Track: L, LEAD pattern, local lane. Branch
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
  without a customer) expecting status "outstanding", domain_hint "order", the product entity, no "delivery" attribute,
  plus negative controls (an arrival / ETA ask stays incoming; "delivered" stays delivered; a dated DO list stays a DO
  list). A parser-only runner `scripts/chatbot_eval/parser_gold.py` calls only the semantic parser with a pinned
  unlabelled prompt version, N repeats, metered with a USD cap; its pure grading is unit-tested red-first. Then the
  section's rule names its words as additions to the ORDER_STATUS FILTER list; measured old (v161 lineage) vs new, N=3.
- R2-2 the TRAIN code failures left after EVAL-BENCH rounds 32-34 regrade (crew counts: parse 4, retrieval 1, answer 1).
- R2-3 generalise: crew sends held-out failure CLASSES after the 18 Oct combined run; EVAL-SETS adds ~150 real TRAIN
  code-lookup items. Never held-out items.
- Live: cap USD 3 per run; at most 2 stacks machine-wide via crew/state/live-slots/<LANE> (mkdir to take, rmdir on stop).
