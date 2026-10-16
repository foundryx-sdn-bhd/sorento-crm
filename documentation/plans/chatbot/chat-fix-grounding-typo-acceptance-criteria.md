# UAC: CHAT-FIX F4 (CHAT-GROUNDING-TYPO)

Plan: `PLAN-chat-fix-grounding-typo-16oct.md`. Owner ruling 16 Oct 2026: a registry-valid parser value wins; the
typed word is irrelevant; no prompt change; no fuzzy code.

- **AC-1 (class entity with a valid spec).** A category-hinted entity whose raw holds a misspelt word next to the
  class word ("undrmount basin") and that carries `spec_key` mounting, `spec_value` under_counter grounds to the class
  (basin) plus mounting = under_counter. No unknown value, no keyless descriptor.
- **AC-2 (specification entity, parser put it right).** A specification entity with `spec_key` finish and
  `spec_value` gunmetal grounds to finish = gunmetal when the message says "gunmetl", both when the parser's raw is
  "gunmetal" and when it kept "gunmetl". Same for the mounting shape (raw "undermount" or "undrmount", value
  under_counter, message "undrmount").
- **AC-3 (invalid parser value unchanged).** A spec the registry does not know (unknown key, or a value outside the
  key's choices) keeps today's grounding: words matched exactly, else an unknown value of its key said as typed.
- **AC-4 (no typo said back).** For AC-1 and AC-2 the grounded entity's said-back words are the registry's words
  ("under counter", "gunmetal"), and the set answer's header names them; the typed word appears nowhere in the reply
  and there is no "Couldn't find" line.
- **AC-5 (the owner's stock ask).** A stock ask for basins with the AC-1 parse resolves with the mounting filter
  applied (the predicate carries mounting = under_counter, the class stays basin) and does not end in the
  unknown-value breakdown; the stock path is reached (no primary_tool_not_run for that reason).
- **AC-6 (exact words unchanged).** Descriptors whose words the message holds ground exactly as today; a turn with
  no descriptor is byte-identical to the parse.
- **AC-7 (no parser change).** Parser prompt, schema and user block are unchanged by this PR (parity), and no
  edit-distance or near-match code is added.
