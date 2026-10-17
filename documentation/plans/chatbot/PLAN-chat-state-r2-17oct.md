# PLAN: CHAT-STATE round 2 - the weak follow-up kinds

**Status:** Build (r2-INT green 7833e7365e; root cause done; slices r2-A..D next). Track: L, LEAD pattern. Branch `crew/chat-state-r2` off
`crew/chat-state` (#1747; rebase onto main after its squash merge).
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

## Proof log (posted on the r2 PR when it opens)

- r2-INT: red 459967eff9 (combined schema) + the read-once test red after the merge 697df41293; fix 7833e7365e.
- r2 round 1: red 18bb534d6c (22 red: r2-A held qty 9, r2-D last-answer domain 8, r2-B/D prompt text 2, r2-C combined publish 3).
