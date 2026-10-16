# UAC: CHAT-FIX F5 (CHAT-TYPED-AS)

Plan: `PLAN-chat-fix-typed-as-16oct.md`. Crew answers to the F5 card, 16 Oct 2026: Q1-Q5 (a).

- **AC-1 (schema).** The parser's entity schema has `typed_as` (string or null) as its LAST property, listed in the
  entity's required keys, with the description in the plan word for word. No other schema property, order or
  description changes; strict mode accepts it.
- **AC-2 (W3 keeps a spec typed in this message).** With a class word typed in this message, a specification entity
  with `current_message: false` whose `typed_as` words are all words of this message is kept; one with `typed_as`
  null, or naming a word not in this message, is dropped as today. Every non-specification entity is kept or dropped
  exactly as today.
- **AC-3 (grounding binds by typed_as).** Category raw "undrmount basin" plus a specification "under counter"
  (mounting under_counter, `current_message: false`, `typed_as` "undrmount"), message "i mean any stock for sorento
  undrmount basin": the class stays basin, mounting under_counter is bound and said "under counter", and
  "undrmount" is said back nowhere.
- **AC-4 (no typed_as, F4 unchanged).** Every F4 test passes unchanged; an entity with `typed_as` null grounds exactly
  as on F4.
- **AC-5 (typed_as must match).** A spec whose `typed_as` names another word ("pnk"), a word not in the message, or
  two specs naming the same unheard word: F4's path (the word is said back).
- **AC-6 (end to end).** Through the engine with a stubbed parse of AC-3's shape after a first turn that set mounting:
  the reply is the under counter set with stock (no unknown-value breakdown).
- **AC-7 (no drift).** Parser-gold TRAIN x2 shows no drop beyond noise against the lane baselines; v94 prompt text
  unchanged; no fuzzy code.
