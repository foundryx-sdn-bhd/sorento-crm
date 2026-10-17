# CHAT-STATE acceptance criteria

Plan: `PLAN-chat-state-16oct.md`. Card: `chat-state-behaviour-card.md` (rules R1-R13). Crew answered Q1, Q2, Q5
(16 Oct, as recommended); rows tied to the owner's Q3/Q4 are marked and change if he rules otherwise.

## A. The state record

- A1. One state per contact, in `respond_contacts.session_vars`: subject (products, customers, brands, filters/specs,
  period, document/status, report shape), open question (what was asked, its kind, its options), last list shown
  (position -> label + key), pending task. No migration.
- A2. After a turn whose reply shows a numbered list (picker options or a multi-item answer), the state's last list
  holds every shown item in shown order. A reply without a list leaves the last list as it was (Q4 rec a).
- A3. The state never holds an item the contact was not shown (built from the scoped answer only).

## B. The parser sees the state in full

- B1. The parser input carries one fixed-format state block with every part of A1 that is set. No cap, budget or
  trim removes or shortens any of it (today's L2 cut of the subject line is gone).
- B2. An empty state (first ask, or after a reset) adds nothing: the parser input of a first ask is byte-identical
  to main.
- B3. The block shows display labels and registry keys only, never UUIDs.

## C. The parser outputs changes; code applies them

- C1. The parse schema carries one optional state-change field: an ordered list of operations (keep, new subject,
  set axis, clear axis, pick positions, answer open question, switch domain, close question).
- C2. Applying operations to a state is deterministic: the same state + operations give the same new state, with no
  read of the message text.
- C3. Each rule behaves as the card says, one test per rule at least: R1 refine keeps the subject and changes one
  axis; R2 code-less domain ask keeps products/customer; R3 "and X" swaps one item and keeps qty/period/filters;
  R4 new full ask keeps only the customer; R5 an answer fills only the asked slot and closes the question;
  R6 customer then product keeps the customer; R7 product then customer (no reference) asks "same product or all
  products?" with real names; R8 a qty attaches to the held item; R9 a pick returns that item of the last list;
  R10 small talk changes nothing; R11 an unresolvable change asks, never guesses.
- C4. A value the registry does not hold, or a position outside the last list, is not applied: the turn asks
  (real-name options, no ids). A registry-valid value is kept as given, never reverted to the typed word.
- C5. When the parse carries the field, no other code path changes the carried subject in that turn.
- C6. When the parse does not carry the field, the turn runs exactly as on main (the $0 L1 replay gate shows zero
  unintended diffs on `--split dev`).
- C7. (R16, owner 16 Oct ~23:00) A held period is kept on follow-ups (domain switches included) and on a new subject
  unless the parse says `skip` / `clear` period; a period the message states replaces it, with or without a `set`.
- C9. (R14, owner 16 Oct ~22:45) When the State holds a customer or period, the parse's `keep` / `skip` / `clear` /
  `unclear` for that axis decides it: keep applies it to this ask; skip does not apply it and it stays held; clear
  drops it. No per-domain table and no word rule decide it. No statement = applies as today.
- C10. (R15) `unclear` for a held customer whose registry kind is `customer`: no question; the ask runs as a
  delivery (domain `order`) for that customer. When the type cannot settle it (e.g. `unclear` period), with the default
  `ask` (one named constant, pinned by a test): one question with real names (held value vs another), no ids, stored as
  the open question; the reply fills only that slot (R5); default `assume` -> the held slot applies; a second
  unresolved turn -> hand-over offer.
- C8. (Q4 a, owner confirmed 16 Oct) A new ask closes the bot's open question; the last list stays pickable until a new list replaces it.

## D. The log (design row G)

- D1. Every engine turn's trace carries the state before, the operations, and the state after.
- D2. The before is the state the parser was shown, not the raw stored row.

## E. Measurement

- E1. L1 replay gate (`--split dev`) green on every slice.
- E2. Follow-up TRAIN slice: >= 80% over 3 runs (design step 1 bar), first-ask TRAIN not worse than main beyond
  the run-to-run error bar. Held-out numbers are crew's.
- E3. Replies never contain UUIDs or internal ids (existing guard tests stay green).
