# PLAN: CHAT-STATE round 2 - the weak follow-up kinds

**Status:** Build (r2-A..D green after 2 rounds, reviewer READY, L1 replay 0 unintended; origin/main 63f4fe47c merged
in after #1747 merged as e9ef7b416; live TRAIN next). Track: L, LEAD pattern. Branch `crew/chat-state-r2`.
**Base design:** `PLAN-chat-state-16oct.md`, `chat-state-behaviour-card.md` (R1-R16), `chat-state-seam-design.md`.

## Goal (crew, 17 Oct, owner "run as much parallel as possible")

CP0 held-out per kind (crew-only numbers): narrowing 17% (n10), reply to the bot's question 62.5% (n8), and-X 82,
switch 85, pick 91, hand-over 93; bar 90 overall. Lift the weak kinds, narrowing and reply-to-question first.

## Rules of the round

- TRAIN only: `crew/state/eval/cp0/train_followups` (84 items, round-31 export) + new follow-up items EVAL-SETS adds.
  Never held-out. Grade from a checkout pinned at the EVAL-BENCH sha (now #1758 edfa2c11c), never a live worktree.
- Root-cause every failing TRAIN follow-up by kind: parse / state / answer / grader.
- Card delta only if a rule changes what the owner sees.
- Red-first fixes in the state path; understanding stays in the parser (no keyword lists, no regex); kill-proof;
  reviewer; L1 replay $0 every slice; live TRAIN paired vs main, cap $3 per run.
- Every fix must hold on >= 8 varied real phrasings (TRAIN items + fresh paraphrase tests).
- Live stacks: at most 2 on the Mac; take `crew/state/live-slots/<LANE>` only if fewer than 2 entries; rmdir on stop.
- Overlap: spec/family matching over held products is step 2 (CHAT-CLOSED-LISTS #1755); this round fixes the
  state/parse parts and coordinates the rest there.

## Steps

1. $0: re-grade the r2 main and r3 lane runs on the 84-item set with grader edfa2c11c; per-kind failures; root cause.
2. Classes (>= 2 members) -> red tests (>= 8 phrasings per class) -> fix -> kill-proof -> L1 replay.
3. Reviewer (+ security if scope/auth touched).
4. Live TRAIN paired vs main (cap $3 per run), report per kind.

## Findings (17 Oct, $0 re-grade of r2 main / r3 lane on the 84 items, grader #1758 edfa2c11c)

Lane 71.3/84 (84.9%) vs main 69 (82.1%). Per kind lane/main: pick 20.67/20, answer 10/10 (every TRAIN answer item
passes both arms: no TRAIN signal for the held-out 62.5%), refine 1/1 of 5, switch 23/23, and_x 15.67/14.
- SPEC-OVER-HELD (4, step 2) + HELD-FAMILY (3, step 2): fixed in #1755, which only runs under its CLOSED LISTS prompt
  section; v157 lacks it -> r2-C publishes one unlabelled version with BOTH sections (combined schema exists, r2-INT).
- NAMED-HELD-VALUE (3, parse, this lane): STATE_SECTION "Never restate an item of the State" stops the parser emitting a
  spec the user repeats -> r2-B rewrite: an entity is what this message names, even when the State holds it.
- HELD-QTY (5, state, this lane): the held quantity lives only on the answered stock task, so keep/refine/swap re-ask
  "How many units?" -> r2-A carry the task quantity onto the refined items.
- BARE-CODE-DOMAIN (crew, CHAT-CODE-LOOKUP TRAIN cp0-df53e90ebf in cp0/train_code): a bare code after an ETA reply flips
  stock / ETA / product info -> r2-D the State shows the last answer's domain; prompt business rule: a message naming
  only another item asks the same fact as the last answer (owner rule; ETA ruling 16 Oct). Parser input only: no
  post-parser domain override (crew NO-GO).
- Grader/gold for EVAL-BENCH: targets.py:434/769/791 compare `note == "payload omitted"` while trace.py:99 writes
  "payload omitted: N bytes"; `applied` cannot credit a held spec applied as product ids; gold of 253d58ba8b and
  fcd44fe521 vs the family rule; round-32 gold on aad2aaec55 vs round 11 "pick drops the typed entity".
- Every class is below the >= 8 phrasings bar: fresh paraphrase tests per class + EVAL-SETS Deliverable A (crew relays path).
- Test DB: sorento_chatstate_throwaway2 (fresh, current schema).
- Live runs (crew 17 Oct, ports moved 18 Oct: a hand-test copy owns 8109): own stack CRM 8193 / MCP 8293 with an env
  file in this worktree (never cp0/stack.env); slot crew/state/live-slots/CHAT-STATE (max 2 entries machine-wide),
  rmdir on stop. Main arm = origin/main + production v63: without the state heading `parser_config.state_block` is
  off (engine.py:4540), so main runs the pre-state baseline.

## Proof log (posted on the r2 PR when it opens)

- r2-INT: red 459967eff9 (combined schema) + the read-once test red after the merge 697df41293; fix 7833e7365e.
- r2 round 1: red 18bb534d6c (22 red: r2-A held qty 9, r2-D last-answer domain 8, r2-B/D prompt text 2, r2-C combined publish 3);
  fix 18c6556187; kill-proof: reverted 22 failed / restored 196 passed (chat_state apply/block/parser_input/seam).
- L1 replay gate r2 (faa9d0f11e) vs integration base 697df41293, --split dev: grounding 0/1506, turn 0/585 unintended.
- Reviewer r2 (faa9d0f11e): NOT READY on r2-A (B-A1 clear quantity undone; S-A2 qty onto another product; S-A3 qty onto a
  new subject; nit label wording); merge / #1755 alone / combined schema / publish READY.
- r2 round 2: red 721f462bd1 (33 red); fix ff3ec689d9; kill-proof: reverted 33 failed / restored 191 passed.
- Reviewer re-check (ff3ec689d9): READY.
- L1 replay gate r2-2 (a17f1cf2b1) vs 697df41293, --split dev: grounding 0/1506, turn 0/585 unintended, exit 0.
- Merge origin/main 63f4fe47c (#1747 squash e9ef7b416 + #1762): add/add conflicts resolved 3-way against the r1 commit
  the branch already held (f3b0c5dc2); the merge adds only #1762's eval tooling and the import-boundary allowlist
  lines, no chatbot runtime change, so the r2-2 gate stands.
- Live TRAIN r2 (18 Oct, $1.485, 84 calls/run): lane d6be413990 + v168 (id 20d8165c) 72/84 x3 vs main x3 69/84
  (round-1 main runs, same pin): paired +3 [-1, +8], not significant. and_x 16/14, pick 21/20, switch 24/23,
  answer 10/10, escalation 1/1, refine 0/1 of 5. Worse: cp0-75e516bd40 (refine). Refine root cause next ($0).

## Round 3 (18 Oct): refine root cause on the r2 live rows ($0; scratchpad evalrun2/rootcause-r2r2.md)

No refine failure is a parse failure; r2-B now emits the named spec (fcd44fe521, 253d58ba8b).
- **R3-A HELD-QTY-NOT-FETCHED (this lane; cp0 b5eb30e1b9 + f14dc9c818, train2 ~4 UNVERIFIED):** r2-A carries the held
  quantity onto `focus.products` rows only (chat_state.py:821-825), but the fetch reads only the open task's
  `requested_quantities` or this turn's entity quantities (turn_runtime.py `_spec_quantities`), so a refine / keep still
  asks "How many units?". r2-A's tests stop at `apply_changes`, which is why they went green while the carry was dead.
  Fix: the quantities apply carried become the fetch's last source, by own code, with r2 round 2's limits (clear wins,
  own code only, never onto a new subject). Tests go through the fetch seam, >= 8 phrasings (TRAIN + fresh paraphrases).
- **QTY-ONTO-REFINED-SET (253d58ba8b, 665ba3e897): ruled (a), crew 18 Oct** (owner line "don't re-ask what the user
  gave"): the one held quantity rides onto every code a refine of the SAME held subject lands on; never onto a new
  subject or another named product (S-A2 keeps holding for those). Part of R3-A.
- **SPEC-OVER-HELD-NO-MEMBER (#1755, fcd44fe521; train2 up to 19 incl. 10 size refines):** no family member has the
  value -> `narrow_held_family` returns the verdict unchanged (held_family.py:86-88) and the turn widens to the catalogue;
  a numeric size never narrows (string `spec_value` only, held_family.py:71). Crew 18 Oct: (a) in principle, "held,
  none match" says so plainly (owner CP0: say plainly what is not on record); routed to CHAT-CLOSED-LISTS after
  EVAL-BENCH's gold card on fcd44fe521. Grader items queued for EVAL-BENCH round 35.
- **Grader (EVAL-BENCH):** cp0-75e516bd40 flipped only because r2-C turns CLOSED LISTS on and #1755 card R2
  (grounding.py:795-806) puts "Water Closet" for the typed "toilet"; same lookup (47 ids) and reply as main, the grader
  reads words only (targets.py:807-847). G-HELD-BEFORE targets.py:823-828. train2: 7 refines while the bot's own
  "How many units for each?" is owed, which `spec_refine` (targets.py:521) fails.
