# PLAN: chat fix PR-C, follow-up context (lane CHAT-ADVISOR-SPIKE, chat-fix LEAD)

Status: Plan (16 Oct). Track: small fix by diff (code only, expected under 300 lines, no migration, no auth change),
but gated on parser-gold because it changes the parser's per-turn input. Branch `crew/chat-fix-context` off
origin/main e19bc513fd, worktree `sorento_crm-chat-fix-context`.

## Goal

Owner problem statement 16 Oct: "the struggle comes from continuous messages, remembering the context". Parser gold
TRAIN: first asks about 89% right, follow-ups 49-51 of 81 (about 62%); 29 fail in both runs. Giving the parser the
whole raw chat did not help (59.9% vs 61.8%), so this is not "more history": the parser does not get the context the
system already holds. PR-C fixes two of the four causes in code (ARCH-advisor-16oct section 1, P1-F1 and P1-F3); the
two prompt fixes (P1-F4 short replies, P1-F2 seat cover is a product, which now also carries the fit misreads
G001/G006/G007/G009/G014 from the closed find-rule PR) follow as their own prompt versions.

## Facts (origin/main e19bc513fd)

- P1-F1 subject dropped: the current subject comes from the carried focus (`session_state.py:23`) through
  `head/parser.py:809-848` and `engine.py:4511-4522` into `turn/context.py` assemble. Since #1527 (9 Oct) the same
  layer also carries the brand / product type / product detail / promotion kind lists (about 629 tokens,
  `turn/context.py:147-158`), rendered first and kept whole (`:405-408`), against the L2 cap of 350 (`:29`); the
  shrink loop (`:426-431`) can only cut the subject, so it is dropped on 77 of 81 TRAIN follow-ups. The builder the
  tests cover (`head/parser.py:851-944`) has no live caller, so no test saw it.
- P1-F3 answered list still owed: lists stay open after a pick on purpose (sticky roster, PR #952, contract 36), so
  a second pick from the same list works. But the parser's view says owed ["pick"] (`turn/question.py:84-90`) and "the assistant is waiting
  for a ... reply" (`turn/context.py:400-402`) even after the pick, so a reply adding another listed item (TRAIN G070) picks a list position
  again (TRAIN G070).
- Parser gold variant A replays each item's RECORDED parser input (`crew/state/eval/parser-gold/tooling/variants.py`
  `v1_input`), so a change to how the input is assembled is invisible to it; a variant that applies the change to
  the recorded input is needed to measure it.

## Changes (red first)

1. S1 P1-F1: the vocabulary lines (brands, product types, product details, promotion kinds) do not count against the
   L2 cap; only the subject, task, open question and pending lines do. The subject is then cut only when THOSE
   exceed the cap. No new line, no prompt change.
2. S2 P1-F3: an open list whose pick was answered shows as answered to the parser (owed [], answered [the picked
   options]) and the "waiting for a ... reply" line is left out when nothing is owed. The list and its options stay
   (sticky roster): a second pick still works. No state change.

Out: P1-F4 and P1-F2 (prompt versions, next PRs); R21/R25 (PR-B slice) and W02-style asks are re-measured after S1.

## Measurement and gates

- Unit tests (red first): the rendered user block keeps "Current subject" with the full vocabulary lines present
  (S1); a picked list renders owed [] + answered and no "waiting for" line, while an unpicked one is unchanged, and
  contract 36 re-pick tests stay green (S2). One fixture per test built from a recorded TRAIN item's state.
- Parser gold: a lane-side variant "A+S1" / "A+S2" that applies exactly the PR's rendering change to each TRAIN
  item's recorded input (subject line from the item's recorded focus; owed/answered from its recorded open
  question), proven equal to the PR's own renderer on the unit fixtures. Run on the 81 TRAIN follow-ups, two runs
  per slice (about 162 parser calls, ~$0.6 per slice), then the whole TRAIN x2 once on both together (~$1.7).
  Pass bar (ARCH): at least +3 on both runs, none of today's right-in-both items wrong in both; first asks
  unchanged (their input does not change: proven by sha256 parity on the 22 report first asks + the 56 TRAIN first
  asks' recorded inputs).
- Report slice (PR-B's 25 chains) and walkthrough rows re-run on PR-C: no regression.
- reviewer; security-reviewer not required. Spend reported; crew approves the gate runs before they start.
