# PLAN: chat-sparse-emission (lane CHAT-SPARSE)

Status: Built, PR #1603; awaiting the crew per-PR eval gate and the owner's label move. Track: standard by
size (M; one data-only migration, no auth/RBAC, no new ingest surface; no owner-visible change until a
label moves). UAC: `chat-sparse-emission-acceptance-criteria.md`.

## Problem (measured by CHAT-SPEED #1602, 12 Oct 2026)

The semantic parser call is most of a chatbot turn: p50 about 3.0 s, of which about 1.9 s is DECODING
the JSON emission (about 408 output tokens, no reasoning tokens, about 220 tok/s). About 70% of the
emission is keys whose value is null / [] / false. Option D gate: turn p95 <= 8 s (main with
embeddings: 10.9 s).

## Design (simplest thing)

1. **Absent == empty, in one place.** `head/parser.py::hydrate_emission` writes every absent declared
   key as its empty value, derived from the strict schema itself: null wherever the key can be null,
   otherwise [] for a list, false for a flag, and an object of its members' empties. A sparse emission
   hydrates to the dict the strict schema would have produced for the same reading, so no reader
   downstream changes. `TOLERATED_ABSENT` (`head/parser.py`) stays for recorded strict emissions.
2. **Per-version switch, not a global one.** A version whose text carries `SPARSE_OUTPUT_MARKER`
   (`chatbot_parser_prompt.py`) is called with `PARSE_OUTPUT_SPARSE_JSON_SCHEMA` in NON-strict mode
   (strict mode requires every key). Every other version, including the current production label,
   keeps the strict schema byte for byte, so nothing changes until the owner moves a label.
3. **The strict guarantee, kept locally.** A non-strict answer is checked against the strict schema's
   types, enums and maxItems (`_wire_errors`); a violation is a failed `understood` (R5), never a soft
   default. Unknown keys are still ignored. Strict answers are never hydrated: a missing key there is
   a fault to see.
4. **Keys whose false is not their null are always written.** Readers test `is False` on
   `is_affirmative` (`turn/decide.py:614`), `domain_in_message` (`lanes/business/low_stock_ask.py:457`),
   and per entity `current_message` / `confident` / `hint_confident` (`head/grounding.py:430,638`,
   `engine.py:496`). `entity_op` has no empty value ("reuse" is a value: `turn/decide.py:393`,
   `turn_runtime.py:1290`). These, `message_type`, and an entity's `raw` / `hint` are required in the
   sparse schema and named in the prompt. Every other flag is only ever read `is True` or truthy;
   `test_no_reader_tells_a_plain_flags_false_from_its_null` pins that.
5. **Prompt.** `sparse_prompt(text)`: the OUTPUT block (byte-identical on the code constant, v42, v50,
   the v52 candidate and v56) is replaced by `SPARSE_OUTPUT_BLOCK`, the addenda's "emit on every
   object" lines are told the same (three literal edits of prompt text), and `SPARSE_OUTPUT_ADDENDUM`
   goes last. Appending the rule alone only took tokens from 420 to 315 on the probe: the model copies
   the template it is shown.
6. **Publish, label unmoved.** Migration `chs_0001_parser_sparse_output` publishes two unlabelled
   versions: the text the `production` label points at when it runs (v52 on prod), made sparse; and
   the code constant plus policy blocks (`chatbot_rearch_s4._body`), made sparse.
   `scripts/publish_parser_sparse.py --from-version N` does the same for a version published later
   (SPEC-PROMO-PARAMS' specs-block version, #1582).

## Measurement (live, 12 Oct 2026; 60 parser calls, the cap)

gpt-5.4-mini, `head/parser.parse` production path, 20 made-up turns
(`scripts/chatbot_sparse_measure.py`), prompt `alembic/data/chatbot_semantic_parser.candidate-20261010.txt`.

| arm | p50 | p95 | max | mean completion tokens | wire errors |
|---|---|---|---|---|---|
| strict | 2.52 s | 2.94 s | 3.21 s | 396 | 0 |
| sparse (20 turns) | 1.54 s | 1.80 s | 2.08 s | 97 | 0 |
| sparse, final wording (12 turns) | 1.58 s | 2.28 s | 2.63 s | 104 | 0 |

After the `entity_op` fix, 12/12 turns match strict on `entity_op`, `domain_in_message` and
`is_affirmative`. Other per-turn differences are within the run-to-run noise CHAT-SPEED measured
(4/20 strict emissions byte-identical across two strict runs); the crew eval gate decides.

## Test list

- `tests/chatbot/test_parser_sparse_emission.py`: per key family (escalation, escalation declined,
  open_question_answer pick / fill / all, low_stock, low_stock all categories, sales keys, entities
  spec_key / quantity / account, lists and the rest, nothing said) a sparse wire form parses to the
  strict emission; written false stays false, absent flag is null; absent objects read as empty
  objects; strict call unchanged and still refuses a missing key; sparse call is non-strict with the
  sparse schema; off-schema answers fail; no message_type fails; unknown keys ignored; config reads
  the marker; `sparse_prompt` on four lines changes only the OUTPUT wording and refuses bad input; no
  reader tells a plain flag's false from its null.
- `tests/chatbot/test_parser_sparse_publish.py`: both lines published unlabelled, labels unmoved,
  idempotent, unbuildable production text skipped not failed, downgrade keeps labelled rows.

## Not doing

- No label move (owner's click, after the eval gate).
- No change to `SEMANTIC_PARSER_PROMPT` itself: the registry fallback stays strict.
- Harness mocks (`mock_reformulator_output`) stay full-shape: `test_harness_injections.py` pins that a
  bad mock names every missing key. A recorded sparse turn is stored hydrated anyway.
- Hedged parser requests (CHAT-SPEED option b): only if p95 is still over 8 s after both lanes.
