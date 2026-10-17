# UAC: CHAT-CODE-LOOKUP

Status: DRAFT, written against the card's recommended options (`CARD-chat-code-lookup-17oct.md` Q1-Q5); revised when
crew relays the answers. Plan: `PLAN-chat-code-lookup-17oct.md`.

Every criterion runs with the lane gate ON (parser prompt carrying the lane marker) unless it says gate OFF.

## Gate

- U0.1 Gate OFF: the $0 L1 replay (`scripts/chatbot_eval/replay.py diff`) of the lane against main reports 0 unintended
  diffs on every stage (grounded, decision, tools, reply).
- U0.2 The production parser prompt label does not move; lane prompts are unlabelled versions on the dev copy.

## R1 near code (parser chooses)

- U1.1 A code word with no exact catalogue hit puts its near spellings (top 5, trigram similarity >= 0.30) and its
  prefix family on the parser's candidate block.
- U1.2 A parser pick that is on the candidate block is kept as the product code; the resolver looks it up exactly and
  the bot answers that product, naming the code used, with no did-you-mean.
- U1.3 A parser pick that is NOT on the candidate block is not trusted as a correction: the turn behaves as gate OFF.
- U1.4 Parser says none: today's did-you-mean list, unchanged.
- U1.5 Code never picks: no score, rule or word list in code chooses between candidates.
- Examples: "Check stock CBCM66631" -> CBMC66631 stock; "Check stock chs2072 x 72 units" -> CSH2072, 72 units.

## R2 set-only codes

- U2.1 A code that exists only in `product_sets` is shortlisted and resolves for product info and stock.
- U2.2 A set code plus an attribute ("SRTWC8608-RL P-TRAP") answers the set variant the parser picks (SRTWC8608-P-RL),
  no question, no unrelated suggestions.

## R3 no picker after a full answer

- U3.1 An exact-code answer (stock, ETA, product info) leaves no open product_pick question; siblings, if named, are
  one plain line.
- Example: "Srtbf11415 ETA" -> ETA of SRTBF11415; SRTBF11415-NEW named, nothing to pick.

## R4 asked attribute carried

- U4.1 An attribute the staff member names ("weight") reaches the verdict even when it is not on the prompt's attribute
  list.
- U4.2 No value on record: the reply says so for that code ("No weight is recorded for SRTKT1636SS-GM").

## R5 held family member

- U5.1 Held SRTWC286-SH-NEW + "Srtwc286 drawing" -> the drawing of SRTWC286-SH-NEW, no picker.
- U5.2 No held member: a multi-member family stays a pick (MWC7630).

## R6 domain reuse, count = stock

- U6.1 A bare code after an answer reuses that answer's domain ("IBWC7601-ECO-S10" after an ETA answer -> ETA).
- U6.2 A count beside a code is a stock ask ("SRTWC286-SH 5unit" -> stock for 5 units).

## Q5 "when deliver"

- U7.1 "SRTKT73SS-BL when deliver to <customer>" -> outstanding order status for that code and customer, no
  "Which period?".

## Measurement

- M1 Each slice: TRAIN code-lookup slice, lane vs main, crew-approved live envelope (cap about $5), graded at $0 with
  the pinned grader; no item worse without a named reason.
- M2 Crew runs held-out; owner bar 97%.
