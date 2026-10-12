# UAC: chat-sparse-emission (lane CHAT-SPARSE)

Plan: `PLAN-chat-sparse-emission-12oct.md`. No owner-visible change until the production label moves.
Round 2 (strict grouped emission) replaces round 1 (non-strict sparse, failed the crew eval).

- **AC-1** A parser version whose text carries the OUTPUT GROUPS marker is called with the grouped schema
  in STRICT mode; every other version is called exactly as before (flat strict schema).
- **AC-2** A grouped answer reaches the engine in the flat shape: a null group writes each member's empty
  value, a null escalation / open_question_answer / low_stock / anaphora writes its object of empties, a
  null entity `detail` writes null quantity / spec_key / spec_value / account. Pinned per key family:
  escalation, open_question_answer, low_stock, sales and ranking keys, dates, conversation flags, filters
  (is_active false included), entities detail.
- **AC-3** Inside a group a plain flag is true or null only; no reader tells a plain flag's false from
  its null. is_affirmative, is_active, domain_in_message, the entity flags and spec_value keep false.
- **AC-4** Strict mode makes a missing group impossible from the provider; if one is missing anyway, its
  members are missing after flattening and `assert_emission` treats them as it treats a flat answer (a
  failed parse, except for the members `TOLERATED_ABSENT` already lets an older prompt omit, e.g. the
  ranking keys).
- **AC-5** The grouped prompt differs from its source only in the OUTPUT block and the entity format
  line, plus the OUTPUT GROUPS section at the end. A text it cannot fold cleanly (a member left on a
  line of its own, a multi-line escalation or anaphora, a block not closing with `}` above its last line,
  an entity line ending otherwise) is refused, so the migration and the script skip it.
- **AC-6** Migration `chs_0001_parser_sparse_output` publishes the grouped build of the production-labelled
  text and of the code constant plus policy blocks, each as a new unlabelled version; no label moves;
  re-running publishes nothing; a text it cannot build is skipped with a log line, never a failed upgrade.
- **AC-7** Live measurement on 20 made-up turns: completion tokens and parser latency lower on the grouped
  build than on the strict one (measured: 396 to 246 tokens, p50 3.13 to 2.33 s, p95 4.64 to 3.03 s).
- **AC-8** Crew per-PR eval gate on the grouped version: no topic worse, no new errors, p95 down.
