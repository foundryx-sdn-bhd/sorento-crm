# UAC: chat-sparse-emission (lane CHAT-SPARSE)

Plan: `PLAN-chat-sparse-emission-12oct.md`. No owner-visible change until the production label moves.

- **AC-1** A parser version whose text carries the SPARSE OUTPUT marker is called with the sparse schema
  in non-strict mode; every other version is called exactly as before (strict schema, strict mode).
- **AC-2** A sparse emission reaches the engine as the full shape: every absent declared key reads as
  its empty value (null; [] for a list; false for `self_reference`; an object of empties for routing,
  escalation, open_question_answer, low_stock, anaphora; null for chat_report), recursively inside
  entities and nested objects. Pinned per key family: escalation, open_question_answer, low_stock,
  sales keys, entities[].spec_key / quantity / account.
- **AC-3** A written `false` on is_affirmative, domain_in_message, current_message, confident or
  hint_confident stays false; an absent one is null. entity_op and message_type are always written.
- **AC-4** A sparse answer with a wrong type, an off-enum value or too many items fails the parse
  (failed `understood`, usage still recorded). A missing message_type fails it. Unknown keys are ignored.
- **AC-5** A strict answer missing a declared key still fails the parse (no hydration on the strict path).
- **AC-6** Migration `chs_0001_parser_sparse_output` publishes the sparse build of the production-labelled
  text and of the code constant plus policy blocks, each as a new unlabelled version; no label moves;
  re-running publishes nothing; a text it cannot build is skipped with a log line, never a failed upgrade.
- **AC-7** Live measurement on 20 made-up turns: completion tokens and parser latency lower on the sparse
  build than on the strict one (measured: 396 to 97 tokens, p50 2.52 to 1.54 s, p95 2.94 to 1.80 s).
- **AC-8** Crew per-PR eval gate on the sparse version: no topic worse, p95 down.
