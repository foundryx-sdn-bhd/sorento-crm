# PLAN: CHAT-CODE-LOOKUP (chatbot code lookups toward the owner's 97% bar)

Status: Plan, step 2 (behaviour card filed as a crew ask; build waits for the answers). Track: L, LEAD pattern, local lane. Branch
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

## Slices

Pending the card answers.
