# PLAN: CHAT-FIX F4 (CHAT-GROUNDING-TYPO) - a registry-valid parser value wins over the typed word

**Status:** planned (16 Oct 2026). Small fix track by diff size (no migration, no auth/RBAC, no new ingest), with the
crew lane gates on top: red-first + kill proof, reviewer, TRAIN no-drop, crew held-out. Lane CHAT-ADVISOR-SPIKE
(chat-fix LEAD), branch `crew/chat-fix-typo` off origin/main 8ceadb980. UAC: `chat-fix-grounding-typo-acceptance-criteria.md`.

## Owner ruling (16 Oct 2026, via crew, reverses the 28 Sep "exact only" ruling for parser corrections)
"The LLM is supposed to correct the wording when outputting ... to output within the subset of registry we gave it."
So: when the parser outputs a specification key and value the registry knows, that value binds. What the customer
typed (a misspelling, a short form) is irrelevant. No prompt change, no fuzzy matching in code.

## Problem (measured on the owner's probe of 16 Oct, row 6 of crew/state/reports/CHAT-ADVISOR-SPIKE-owner-probes-1720.md)
A misspelt mounting word in a stock ask for basins. The parser returned ONE category entity whose raw kept the typo,
with `spec_key` mounting and `spec_value` under_counter (a registry-valid value). Grounding dropped it:
- Class branch `app/services/chatbot/head/grounding.py:768-787` grounds only the raw words, exact match
  (`ground_words` :294, `_same`); the typo is an unheard word -> `Grounded("", "", word)` (:773), an unknown with no
  key. The entity's own `spec_key`/`spec_value` are never read on this branch.
- With no filter, `resolve_gate.py:751-762` sends an unknown value; `references.py:3331-3343` breaks the whole class
  down by size; the stock tool never runs (`answer_bridge.py:1910-1912` primary_tool_not_run); the header says the
  typo back (`answer.py:2880`, `unknown_values_reply`).
- Specification branch (:738-751): `_validate` (:594-624) already accepts a registry-valid key/value, but
  `_as_typed` (:627-668, fix round 10 on #833) turns any grounded descriptor whose words are not in the message into
  an unknown said as typed ("gunmetl basin" with value gunmetal -> unknown "gunmetl"). Under the new ruling that
  revert is wrong.

## Change (code only, `head/grounding.py`)
1. Class branch: when the entity carries a `spec_key`/`spec_value` that `_validate` binds to a registry value (value
   not None), that grounded spec replaces the entity's unheard words (they are what the parser read as that value);
   the class words stay the class. A spec that does not validate leaves today's behaviour.
2. `_as_typed`: a grounded descriptor with a registry value (value not None) is kept as the parser gave it, whatever
   the message holds. Unknown descriptors (value None) keep today's handling.
3. Said-back: a bound value is described with the registry's words (`Grounded.words` / the key's phrase), never the
   typed word, so headers ("under counter wash basins") and any said-back line carry no typo. Check `_validate`'s
   `said = raw or value` and `answer.py` described-set phrasing; fix at the grounding side if the typo reaches it.

Not in scope: the parser prompt (v94 untouched), fuzzy/edit-distance matching (none added), values outside the
registry (still unknown, "Couldn't find: X" unchanged), rows 1-5 of the owner's probes (they wait on other rulings).

## Tests (tester writes them red first; existing tests that pin the 28 Sep revert flip with this ruling)
See UAC. Includes the two crew-named shapes: "undrmount basin" (class entity + mounting/under_counter) and "gunmetl"
(finish gunmetal, raw spelt right and raw left as typed).

## Gates
- Red-first commit `test(red):` (tests only), red-proof note, then fix, kill proof (revert grounding.py -> red).
- Reviewer (Opus). Touched + grounding/head suites only, `-n 2`, lane DB `sorento_chatfix_ci`.
- TRAIN no-drop: the parser-gold gate scores the parser's output BEFORE grounding (adapters.py reads entity
  spec_key/spec_value), and this PR changes neither parser input nor parser code, so the gate cannot move: prove
  with parser-input parity (no LLM spend) plus an offline grounding diff: run `ground()` on each TRAIN item's recorded
  v94 baseline parse with its message on main vs this branch (read-only dev DB vocabulary) and list every item whose
  grounded entities change (ids only).
- Crew held-out on the branch head.

## Trigger for more
If the held-out shows parses where the parser's registry value is wrong and the typed word was right, that is a
parser defect to fix at the parser input, not a reason to restore the typed-word check.
