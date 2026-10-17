# PLAN: CHAT-STATE round 2 - the weak follow-up kinds

**Status:** Plan (root cause by kind in progress; no code yet). Track: L, LEAD pattern. Branch `crew/chat-state-r2` off
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

## Findings

TBD (step 1).
