# PLAN: EVAL-SETS, more real TRAIN items + the 100-question rehearsal (17 Oct 2026)

Status: in review (small fix track, crew tooling, size S). Deliverable A delivered 17 Oct (458 TRAIN items:
code 198, spec 65 = 10 real + 55 synthetic, attachment 83, follow-ups 111 = 58 real + 53 synthetic), Deliverable B
delivered 18 Oct (100 conversations, 230 turns), both crew-side; runner change `replay.py live --chain` in PR #1773.

Parent: `PLAN-eval-bench-cp0-16oct.md` (CP0 bench, gold rules, grader). No product code changes. Every set lives
crew-side under `crew/state/eval/` (dir 700, files 600) and never enters git: the repo is treated as public, and the
sets carry real chat text. This file holds no message text, codes or names.

## Why

Fix lanes see only the small CP0 TRAIN exports (code 93, spec 26, attachment 27, follow-ups 84) and over-fit them:
held-out code lookups sit near 75% while TRAIN reads 86 to 89%. The hand test needs one build that clears the
held-out bars plus a crew-only 100-conversation rehearsal in the owner's testing style at 90% or better.

## Deliverable A: TRAIN expansion (lane-visible)

Targets: about +150 code lookups, +60 spec, +60 attachment type, +60 follow-ups, from real turns of the restored
prod copy (`sorento_cagent_stack`, read-only).

1. Pools (`eval-sets/pools.py`): CP0's own loader and conversation rule (`cp0/build_seeds.py`, `--since 2026-09-08`,
   seed 16). Excluded at conversation level (crew ruling 17 Oct): every held-out conversation (the seed-16 split, and
   the split field of every CP0 item), every conversation already used by any CP0 item (seeds, extra seeds, gold,
   probes, excluded gold, the four TRAIN exports). Reproduction check: 0 split mismatches against the CP0 items.
2. Candidates: `build_seeds.candidate_tags` per turn, plus a widened text rule for spec (spec word + class word, no
   code) and attachment (file word), since the parser-entity rule only finds what the bot itself tagged.
3. Sample stratified per slice, deterministic, one slice per turn, scarce slices (spec, attachment) first.
4. Gold with the bench pipeline unchanged: `label.py` passes A and B (gpt-5.4-mini, dev key asserted), pass C on
   disagreements, `adjudicate.py`, `build_gold.py` (DB-decided fields), then `code_families.py`, `cert_schemes.py` and
   `apply_gold_fixes.py` (rounds 28 to 34) run on the new items in their own directory. Spend cap 3 USD in total.
5. Provenance on every item: `recorded_turn_id`, `conv`, `source`, the label passes that agreed, and the fix rules
   that touched it.
6. Exports in the train_* layout (`items.jsonl`, `recorded.jsonl`, `turn_ids.txt`, README), plus masked copies for
   cloud lanes: phone numbers, emails and person names replaced by fake but stable values (same input, same fake);
   codes, quantities, dates, company and product names kept.
7. Disjointness check printed in the report: overlaps by item id, turn id and conversation id against held-out,
   probes, existing TRAIN and Deliverable B, each 0.

Known shortfall (measured 17 Oct): real spec asks are rare (about 19 left in free train conversations; 70 in the
whole corpus by the text rule), and follow-up answer / refine turns are nearly gone (4 / 0). Crew decides between
grounded synthetic top-ups (tagged `source: synthetic`) and reporting the shortfall.

## Deliverable B: rehearsal set (crew-only)

100 multi-turn conversations in the owner's testing style (typos, very short follow-ups, topic switches, replies to
the bot's question, Malay and Chinese mixes, spec words, attachment types and scheme names, set codes, near codes,
staff and dealer askers). Style from the owner's past test conversations in the copy and the owner-probes report;
every expected answer grounded in dev data; gold per turn with the same rules. Conversations disjoint from A.

Stored at `crew/state/eval/rehearsal/` with a README and a run command that chains the turns of one conversation
through the bench's live runner and grades each turn with main's `targets.grade_turn`. Not run live by this lane.
If chaining needs a runner change in the repo, that change ships as a small tooling PR with red-first tests.

## Out of scope

No grader rule changes, no live bot runs, no product code.
