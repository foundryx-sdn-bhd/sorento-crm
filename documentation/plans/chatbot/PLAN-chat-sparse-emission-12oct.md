# PLAN: chat-sparse-emission (lane CHAT-SPARSE)

Status: Round 3 built (STRICT grouped emission, flat decode order kept), PR #1603; awaiting the crew
per-PR eval rerun and the owner's label move. Round 1 (non-strict sparse) FAILED the crew eval and is removed. Track: standard by
size (M; one data-only migration, no auth/RBAC, no new ingest surface; no owner-visible change until a
label moves). UAC: `chat-sparse-emission-acceptance-criteria.md`.

## Problem (measured by CHAT-SPEED #1602, 12 Oct 2026)

The semantic parser call is most of a chatbot turn: p50 about 3.0 s, of which about 1.9 s is DECODING
the JSON emission (about 400 output tokens, no reasoning tokens). Most of the emission is key families
that are empty on nearly every turn. Option D gate: turn p95 <= 8 s (main with embeddings: 10.9 s).

## Round 1: non-strict sparse emission (FAILED, removed)

The parser emitted only non-empty keys, with the schema sent non-strict and a local check standing in.
Crew full eval of 48478b36 (eval v66 = v63 + sparse, baseline main+emb): hidden 175/214 vs 180, dev
85/107 vs 87; errors 9 vs 2 and 3 vs 0; p50 3.5 vs 4.1 s but p95 13.8 vs 10.9 s; output tokens per
case 780 to 222. Cause: off-schema values a strict call can never produce
(`open_question_answer.mode = "null"` as a string, `profile_statements[].key = "usual_products"`),
random across reruns; four deterministic losses (hidden cand-0052, cand-0232, cand-0294; dev cand-0044).
Lesson: the provider's constrained decoding is what guarantees the shape. Do not give it up for tokens.

## Round 2 design: STRICT grouped emission

1. **Same strict guarantee, a different wire shape.** A version whose text carries
   `GROUPED_OUTPUT_MARKER` (`chatbot_parser_prompt.py`) is held to `PARSE_OUTPUT_GROUPED_JSON_SCHEMA`
   (`head/parser.py`), still `strict: true`:
   - `OUTPUT_GROUPS`: `dates` (3 keys), `ranking` (12 sales/ranking/sort keys), `conversation`
     (scope / broaden / continuation / correction / topic_reset / proceed_anyway), `filters`
     (access_levels, demand_qty, contains_flyer, person_mention, is_active, promotion_kind), each one
     nullable object;
   - `ENTITY_OUTPUT_GROUPS`: `detail` (quantity, spec_key, spec_value, account) inside each entity;
   - `escalation`, `open_question_answer`, `low_stock`, `anaphora` may be null.
   Key names inside a group are unchanged, so every prompt rule applies as written. Every other version
   (the current production label included) keeps the flat strict schema byte for byte.
2. **Flatten at the parser, nothing else changes.** `flatten_grouped` turns the answer back into the
   flat shape before any reader: a null group writes each member's empty value (null, or [] for a
   list), a null object writes its object of empties. Strict mode makes a missing group impossible;
   if one were missing, its members are missing to `assert_emission` exactly as in a flat answer
   (the ranking keys are in `TOLERATED_ABSENT`, so those would read as null).
3. **Plain flags are true or null inside a group.** `PLAIN_FLAGS` (continuation, contains_flyer,
   correction, topic_reset, proceed_anyway, ranking_refine, is_escalation_confirmation,
   backward_reference, all_categories) carry `enum: [true, null]` in the grouped schema, so the model
   never writes a group just to say false. No reader tells their false from their null:
   `test_no_reader_tells_a_plain_flags_false_from_its_null` pins it. Flags whose false is a value
   (is_affirmative, is_active, domain_in_message, the entity flags, spec_value) keep their full type.
4. **Prompt.** `grouped_prompt(text)`: inside the OUTPUT block (byte-identical on the code constant,
   v42, v50, the v52 candidate and v56) each group's member lines fold into one line showing the group
   as null, each member keeping its own description; the entity format line gains
   `"detail": null, or {...}`; an OUTPUT GROUPS section goes last. Nothing else in the text changes
   (pinned per line). A text it cannot fold cleanly is refused, never published half-folded (review
   round 2: a blank line before the block's last line, a member at another indent, a multi-line
   escalation, an entity line ending otherwise). A plain flag keeps its description, only its `false`
   default becomes `true|null`. `_gemini_schema` drops a non-string enum (Gemini has no spelling for
   `[true, null]`). The model copies the template it is shown: with the section alone it still
   wrote every group as an object (probe, 12 Oct 2026).
5. **Publish, label unmoved.** Migration `chs_0001_parser_sparse_output` (revision id kept, body now
   grouped; a database that ran the round 1 body keeps those rows unlabelled) publishes two unlabelled
   versions: the text the `production` label points at when it runs (v52 on prod), grouped; the code
   constant plus policy blocks, grouped. `scripts/publish_parser_grouped.py --from-version N` does the
   same for any later version (e.g. v63 on the eval DB, or #1582's specs-block version).

## Round 2 eval and round 3 fix

Crew full eval of a6d54734 (eval v67 = v63 + grouped): schema errors 0; hidden 177/214 vs main 182, dev
89/107 = 89; p50 2.6 vs about 3.2 s; output tokens per case 515 vs 780. FAIL on answer-identical:
cand-0067 (inventory.availability) and cand-0119 (order.delivery_status) wrong in both reruns; cand-0104
(inventory.other) intermittently blank (also in round 1).

Cause found in the shape itself: strict decoding writes keys in schema order, and each key conditions
the next. The round 2 `conversation` and `filters` groups sat where their FIRST member was, so
correction, topic_reset, proceed_anyway, is_active and promotion_kind were decided before the entities,
entity_op and order_status instead of after them. Round 3 keeps only groups whose members are already
next to each other in the flat schema (`dates`, `ranking`, the four objects in place, the entity
`detail` at the end of each entity), so every decision is made in main's order;
`test_the_grouped_wire_keeps_the_flat_decode_order` pins it. The flat flags go back to their full type
and their OUTPUT lines are untouched; `PLAIN_FLAGS` (true or null) now covers only flags inside a group
or object. The cand-0104 blank reply is still open: no reader turns a null plain flag into a failure
(every read is truthiness or `is True`), and the per-turn outputs were asked of crew.

## Measurement (live, 12 Oct 2026, round 2: 66 parser calls)

gpt-5.4-mini, `head/parser.parse` production path, 20 made-up turns
(`scripts/chatbot_grouped_measure.py`), prompt `alembic/data/chatbot_semantic_parser.candidate-20261010.txt`.

| arm | p50 | p95 | max | mean completion tokens | errors |
|---|---|---|---|---|---|
| strict (same hour) | 3.13 s | 4.64 s | 5.63 s | 396 | 0 |
| grouped, final | 2.33 s | 3.03 s | 3.23 s | 246 | 0 |

Readings, flattened, vs strict on the same turns: 12 differing keys over 20 turns, against 8 between two
strict runs of the same turns (the noise floor). Watch item: one grouped turn ("sales report for KIM
HUAT March") dropped `status: sales_report` while keeping `order_status`; both strict runs kept it. The
round 1 sparse arm dropped it once too. Single sample; the eval gate decides.

## The round 1 p95 regression (13.8 vs 10.9 s), as far as this sandbox can see

The run dirs are on the crew machine, so this is reasoned from what was measured here, with the check
that would confirm it:
- Not decode: output tokens fell 72%, so the slow cases are not slow because of what was written.
- Not the errors: a failed parse ends the turn at `understood`, faster than a good one.
- Provider tail at normal token counts: seen here at 16.9 s (grouped, 326 tokens) and by CHAT-SPEED at
  10.5 s (strict). p95 over ~320 cases is the ~16 slowest, so two or three such calls move it by
  seconds. The strict baseline run had its own tail.
- Cold prompt cache on a NEW version: the parser's `prompt_cache_key` is per version, and the 40k-token
  prefix of a version no run has used is a cache miss on each worker's first calls (CHAT-SPEED: 5.8 s
  cold vs about 3 s warm; here the first call on a new version took 8.3 s). The baseline label is
  warm from earlier runs.
- Check: `ai_assistant_usage_logs.cached_tokens` and the parse duration of the 16 slowest cases. Near
  zero cached tokens, or 8-17 s with normal token counts, means cache/provider, not the change. A warm-up
  call per version before the timed run would make the p95 comparison fair.

## Test list

- `tests/chatbot/test_parser_grouped_emission.py`: per key family (nothing said, escalation, escalation
  declined, open_question_answer pick/fill/all, low_stock, low_stock all categories, sales and ranking
  keys, one ranking key, dates, conversation, conversation flags only false, filters, inactive records,
  entities detail, lists) a grouped wire form parses to the flat emission; null groups and objects read
  as empties; a missing group fails; flat versions called exactly as before; grouped schema strict-valid
  and leaf-identical; group membership sane; config reads the marker; the plain-flag guard; the prompt
  changes only the OUTPUT block and the entity line on four parser lines; refusals; every group named.
- `tests/chatbot/test_parser_grouped_publish.py`: both lines published unlabelled, labels unmoved,
  idempotent, unbuildable text skipped, downgrade keeps labelled rows.

## Not doing

- No label move (owner's click, after the eval gate).
- No change to `SEMANTIC_PARSER_PROMPT` itself: the registry fallback stays flat.
- No non-strict call anywhere (round 1 lesson).
- Hedged parser requests (CHAT-SPEED option b): only if p95 stays over 8 s; the provider tail above is
  exactly what it targets.
