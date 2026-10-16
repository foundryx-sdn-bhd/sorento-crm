# PLAN: CHAT-FIX F5 (CHAT-TYPED-AS) - the parser names the word it read each entity from

**Status:** planned (16 Oct 2026). Lane CHAT-ADVISOR-SPIKE (chat-fix LEAD), branch `crew/chat-fix-typed-as`, stacked
on F4 (#1737, frozen 4bb5b9562); retarget to main after F4 merges; ship with F1 (#1730) in one deploy. Gates:
red-first + kill proof, reviewer, parser-gold TRAIN x2 no-drop, walkthrough live check, crew held-out. Budget
approved by crew: about $1.9 (TRAIN x2 + live). UAC: `chat-fix-typed-as-acceptance-criteria.md`.

## Why
Owner rule (16 Oct 2026): the parser corrects misspellings; code only trusts registry-valid parser output; no fuzzy
code; no prompt text change. F4 binds the parser's correction in most shapes. One shape is left, seen live in 1 of 4
runs of the owner's own probe: after "any stock for undermount basin", the message "i mean any stock for sorento
undrmount basin" parses as category raw "undrmount basin" plus a specification "under counter" (mounting
under_counter) marked `current_message: false` (credited to turn 1). W3 (`turn_runtime.with_new_set_words`,
turn_runtime.py:1428-1455, AC-1368 attribute-first-asks-acceptance-criteria.md:398-401) drops carried set entities
before grounding (engine.py:4730 vs :4747), so the typo is said back. Code cannot tell which carried spec a typo
belongs to; only the parser knows which word it read. Crew decisions (16 Oct): Q1-Q5 of the F5 card, answer (a) each.

## Change
1. Parser schema (`head/parser.py`): entity property `typed_as` (string or null), LAST after `account` (:184), in the
   entity `required` list (:187-197), nullable like `quantity` (:169-172). Description (the model reads it):
   "The word or words of THIS message's text that name this entity, copied character for character as typed (a
   misspelling stays misspelt); null when THIS message's text does not name it, whatever earlier turns said."
2. W3 (`turn_runtime.with_new_set_words`): also keep a SPECIFICATION entity whose `typed_as` words are all exact words
   of this message (casefolded `[a-z0-9]+` tokens), whatever `current_message` says; every other entity exactly as
   today. engine.py:4730 passes the message's first line, as :4747 already does for grounding.
3. Grounding (F4 class branch): a specification entity whose `typed_as` word equals (`_same`) the class raw's single
   unheard word and whose value is registry-valid (`_bound`, non-numeric, phrased) accounts for that word, whatever
   `current_message` says; exactly one such entity, else F4's rule as it stands. Entities with `typed_as` null keep
   F4's behaviour exactly. No fuzzy matching.

Not changed: v94 prompt text, F4's rules for entities without `typed_as`, W3 for every non-spec entity.

## Known effects
- The schema is the cached prompt prefix: one cold-cache round after deploy (as F1). Parser cassettes re-record once
  (the key covers response_format, llm_cassette.py:157-162).
- Entities rebuilt from history may carry an old `typed_as` (turn_runtime.py:1405-1410, engine.py:3023): the exact
  this-message word check contains it.
- The model may leave `typed_as` null: then F4's behaviour.

## Gates
- TRAIN x2 (v94, items_TRAIN_lane.jsonl, worktree runner): no drop vs the lane v94/F1 baselines (noise about 2 items);
  TRAIN has no F5 shape, so this guards drift only.
- Walkthrough live (Kay 430229069, v94): the owner's row-6 sequence, a W3 regression ("which basin has cert" after a
  water closet set), a correctly typed carried spec; 2 reps.
- Crew held-out.
