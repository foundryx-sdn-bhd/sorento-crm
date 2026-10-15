# PLAN: PV2 R6 "clarify + references + contract test" (15 Oct 2026, evening)

Status: Plan. Track M (LEAD pattern, red-first, kill-proof). Branch `crew/pv2-redesign-r6` from d08cd98c3 (R5 head,
PR #1707 unmerged), worktree sorento_crm-pv2-fix2; draft PR stacked on #1707. Brief crew/state/briefs/PV2-FIX-R6.md +
train-only brief crew/state/reports/PV2-R6-train-brief.md (root classes A-E; train only, by design).
No prompt change in R6 (crew option O2 is R7).

## Why
Crew measured R5 3x: train 15->18/55, held-out 6->8/19, fresh 2->2/18 (bar not met). Turns where the parser said
`unclear` or `clarify.needed` failed 72%; R5's pick code works only when the turn BEFORE left a question with numbered
options, and R5's fixtures always started from that shape, which the live parser rarely produces.

## Behaviours (each red test from a parser output with a fresh paraphrase; never battery wording or ids)
0. Live-parser contract test FIRST: replay recorded TRAIN parser outputs (masked: person names fake, business codes kept)
   through the real turn/apply; assert invariants on every turn, never expected replies:
   I1 reask line only when a question was open at turn start; I2 no entity / which-held / domain question with zero
   options when the parse carries an entity, a refers_to_held entity or a domain, or the last answer printed rows;
   I3 a pick never re-prints the same question; I4 every numbered line printed is an option of the stored pending
   question; I5 a product reference is never offered a document number; I6 no UUID / internal key in any PV2-turn reply.
1. Clarify turns are first-class (`act=unclear`): keep customer / domain / period from the same parse; numbered options
   for every question asked; a family confirm that prints accounts offers them as options; no open question -> a new
   ask, never "didn't catch your choice".
2. A pick removes the picked entity whatever its kind tag (`_without_picked`), so the question never comes back.
3. Follow-up references ("this product", "both", "the others", "that one") read a document's LINES, not its number;
   several -> ask with real names.
4. Customer group = one customer: print the company / group name, no account count; never split a group in asks or answers.
5. UUID / id leak, both halves: the bridge sends the product code or label, never a ref key; the id scrub runs on EVERY
   PV2-turn reply, including v1-rendered replies returned through `_answer_actions`.
6. Owner ruling CA-3 Q5(a): an unlinked contact holding chatbot.analytics gets chat reports like staff, a
   sales_orders.sales_report holder gets sales reports: exempt those GRANTED turns from slice G's hold (picker
   included), like v1 #1648.

## Code map (at d08cd98c3)
(mapping pass in progress)

## Gates
Touched tests -n 2, contract test green, reviewer READY, security CLEAN; crew report --done; crew runs the battery.
