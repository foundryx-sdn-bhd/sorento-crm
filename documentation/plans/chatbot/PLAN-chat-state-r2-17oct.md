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

### train2 baseline (crew (a), 18 Oct, x1 each, eval-sets grade_sets.py with main's targets.py)
Real 49 lane / 50 main of 58; synthetic 29 / 21 of 53 (refine 10 / 0 of 28, answer 19 / 21 with 3 lane `unknown`).
Main baseline for reuse: scratchpad evalrun3/s_main1.jsonl (crew-note on #1772). Root cause: evalrun3/rootcause-train2.md.
- **R3-B STATE-VIEW-ROSTER-OFFER (this lane, 9 TRAIN items: cp0-12e64c3f37, cp0-b2f537d9b6, cp0-2d60b19a93,
  es-33ba35d807, es-bf24a3e2e3, es-s2bd9aaeea, es-safcbef216, es-s6fd301ccd):** after a roster pick the lookup misses and
  the reply offers a hand-over; the engine keeps the roster as the stored question with the team and `escalate_offered`
  (answer_bridge.py:2091-2139, engine.py:8677-8681) and accepts a later "yes", but the State view adds `offer` /
  `hand_over_to` only for the hand-over kinds (chat_state.py:197-205), so the next parser sees `pick_one, owed [pick]`.
  Card R5 / UAC C3 (the question closes) + C8 (the last list stays pickable). Fix in the view only; the store stays.
- Held, not fixed (class bar >= 2, singletons across 11 runs): HELD-FILTERS-NOT-FETCHED (es-s2bd9aaeea: `promo_filter`
  builds from this turn's entities only, turn_runtime.py:582-646); CANCEL-CLEARS-NAMED (es-3e53ca255a:
  `task_close_clears_products` empties a product the message names, apply.py:3530-3538).
- To #1755: SIZE-AS-CODE (3 refines; candidates.py:263-283 substring match, no numeric spec line), near-code
  es-e1f8fae46c. To EVAL-BENCH: 3 OWED-QTY refines (targets.py:536-543); answered roster read as a fresh question
  (targets.py:1166, :784, `_oq_sig` 272-276).

### Round 3 proof
- R3-A: red 3007a7bf2c (16 failed / 6 guards pass); fix dbc0557644 (22 passed; chat_state suites 274); kill-proof:
  the 3 fix files reverted 16 failed / 6 passed, restored 22 passed. L1 replay vs ba6c344e9e: 0/1506 + 0/585.
  PR gate red on ba6c344e (run 39052575920) was exactly these 16 red tests; ci re-run 39053431961 on dbc0557644.
- R3-B: red 85b1cba598 (37 failed / 10 guards pass).
- R3-A review (reviewer NOT READY on dbc0557644: B1 the `[]` branch carried `shared` onto family codes; S1 a vacuous
  test; S2 card R8; N1, N3): red bcc4319fc3 (B1 3 failed), fix c271f536af, card d20a506e39; kill-proof B1 reverted
  3 failed, restored 74 passed (r3a + r3b). Reviewer re-check: READY.
- R3-B fix 8c38347f1d; kill-proof reverted 37 failed. Reviewer: NOT READY: `escalate_offered` does not mean "the last
  reply offered a hand-over": did-you-mean rosters carry it from the ask (answer_bridge.py:1303,1345; compose.py:499),
  and it survives a later pick that finds stock (pending.py:147-160, apply.py:940), so the view showed an offer the
  reply never made (card R13) and hid a fresh roster's owed pick. **Crew ruling 18 Oct (a):** stamp the turn the
  hand-over offer was made (answer_bridge.py:2133-2139, compose.py:906-916); the State shows the offer only when it was
  made on the last reply and the roster was answered; no change to what "yes" does (crew logs for the owner that a
  "yes" after a later stock hit still hands over on main, turn_runtime.py:1226-1238).
- PR gate red on 8b1d6047 (run 39054912036) = exactly the 37 R3-B red tests; pushed d20a506e39, ci run 39056082640.
- L1 replay gate (d20a506e39 vs 8b1d604705): grounding 0/1506; turn tier first run 2 "grounded" rows in cand-0161,
  not reproducible (10 replays per row match on every field; those 14 Oct parses carry no state_changes, so no r3 code
  runs on them; replay.py:818-828 names a row "grounded" when one arm errored); turn tier re-run 0/585. CI green on
  d20a506e39. R3-B review red 00209278af (7 failed: 4 fresh rosters, 3 stale-flag seam cases; 48 pass).
- R3-B fix 1d30ec6732 (offered_at_turn stamp, answer_bridge.py ~2139 / compose.py ~917; view gate chat_state.py
  ~194-210): 7 red green, kill reverted 7 failed; gate vs d20a506e39 0/1506 + 0/585. Reviewer: code right, NOT READY
  on tests (no positive next-turn test; S1 trace after-view at the offer turn's number; S2 dead fallback).
- Red f907faaae4 (S1 2 failed; positive seam tests both stamp paths; fixtures carry offered_at_turn); fix b59bb0ce02
  (after-view at turn_no + 1; fallback dropped): 386 passed; S1 kill reverted 2 failed. Reviewer re-check: READY
  (drop compose stamp 2 red, drop answer_bridge stamp 4 red, == to <= 3 red, revert S1 2 red); guard a7d38a0aa7 for
  a stamped roster never picked from (the one surviving mutation). Nit kept: the stamp is also stored on main-arm
  rosters (read by nothing; gating it would add code for no behaviour).
- Gate vs d20a506e39: grounding 0/1506; turn tier 1 "grounded" row cand-0102 (a first ask, no state_changes, no r3
  code reachable; 10 replays per arm match); a re-run with the temp output kept: 0/585. The L1 gate's turn tier shows
  intermittent "grounded" rows (3 rows over 2 runs, none reproducible); the patched gate copy now prints each arm's
  row shape on the next one.

### R4 (crew via CHAT-CODE-LOOKUP, 18 Oct): no slice
On main + v63 the parser never sees `Current subject:` (vocabulary line over the 350 L2 cap, context.py:29; only the
subject shrinks, context.py:437-443; train2 s_main1 108/108, cp0 r2main 86/86). With the state section on, `_render_l2`
returns the vocabulary line + the State block uncut (context.py:394-396; engine.py:4568-4570): all 617 held-focus lane
rows carry the State subject line, which holds every L2 subject field and more. Crew 18 Oct (a): no R4 slice here; the
subject reaches main when the state section's prompt version is labelled after the owner's hand test; crew raises the
main-side L2 budget fix with the owner as its own lane.
