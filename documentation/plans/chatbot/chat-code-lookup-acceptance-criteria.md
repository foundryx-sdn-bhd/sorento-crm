# UAC: CHAT-CODE-LOOKUP

Status: AGREED for R1, R2, R4, R5, R6, Q5 (crew answers 17 Oct, card Q1/Q2/Q4/Q5 (a)); R3 DROPPED (owner ruling 17 Oct: keep the sibling picker after a full answer). Plan: `PLAN-chat-code-lookup-17oct.md`.

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
- U1.4 Parser says none (two or more codes equally close, or none fits): today's did-you-mean list, unchanged.
- U1.6 One clearly meant code: answered, no did-you-mean picker (owner, 17 Oct).
- U1.5 Code never picks: no score, rule or word list in code chooses between candidates.
- U1.7 (R2-6, crew 18 Oct) The parser picks a near code only when no other listed code is as close. A typed word with
  two listed codes about as close (one is the word without its last letter, another has a different last letter) gets
  no pick and today's did-you-mean. Swapped adjacent digits or letters still pick even when numbered siblings are listed.
- U1.8 (R2-4, crew 18 Oct) Code may REFUSE, never choose: a parser pick drops back to the typed word (then the
  did-you-mean) only when another listed near code has an equal D2 shortlist score, or a typed partial code is held by
  2+ listed codes with none exact. Never when the typed word is on file, never when one code beats the rest.
- Examples: "Check stock CBCM66631" -> CBMC66631 stock; "Check stock chs2072 x 72 units" -> CSH2072, 72 units.

## R2 set-only codes

- U2.1 A code that exists only in `product_sets` is shortlisted and resolves for product info and stock.
- U2.2 A set code plus an attribute ("SRTWC8608-RL P-TRAP") answers the set variant the parser picks (SRTWC8608-P-RL),
  no question, no unrelated suggestions.
- U2.3 The reply names the set code asked; a set answer carries no price (product-sets rule, `entity_resolver.py:898`).
- U2.4 A set is listed only when every member is visible to the contact (company and brand scope); gate off: today's
  forced miss, unchanged.

## R3 dropped (owner, 17 Oct)

- U3.1 Unchanged: an exact-code answer keeps today's sibling picker (`answer.py:4846-4848`).

## R4 asked attribute carried

- U4.1 An attribute the staff member names ("weight") reaches the verdict even when it is not on the prompt's attribute
  list.
- U4.2 No value on record: the reply says so for that code ("No weight is recorded for SRTKT1636SS-GM").

## R5 held family member

- U5.1 Held SRTWC286-SH-NEW + "Srtwc286 drawing" -> the drawing of SRTWC286-SH-NEW, no picker.
- U5.2 No held member: a multi-member family stays a pick (MWC7630).
- U5.3 (R2-2) A held member with no file of the asked type says so for that code in the contact's language (catalogue
  line "{code} has no {types}."), not a did-you-mean.

## R6 domain reuse, count = stock

- U6.1 A bare code after an answer reuses that answer's domain ("IBWC7601-ECO-S10" after an ETA answer -> ETA).
- U6.2 A count beside a code is a stock ask ("SRTWC286-SH 5unit" -> stock for 5 units).

## Q5 "when deliver"

- U7.1 "SRTKT73SS-BL when deliver to <customer>" -> parser status `outstanding` for that code and customer; no
  "Which period?"; the configured DO/SO scope policy applies unchanged (crew a19194868).

## Measurement

- M1 Each slice: TRAIN code-lookup slice, lane vs main, crew-approved live envelope (cap about $5), graded at $0 with
  the pinned grader; no item worse without a named reason.
- M2 Crew runs held-out; owner bar 97%.
