# PLAN: prepare the prod parser label move from v52 (PROMPT-PROD-LABEL)

Status: Plan, awaiting card answers (track: small fix size by diff, one data file + one migration + one static
test; no auth/RBAC; brief size M so card + red-first + reviewer apply).

UAC: `prompt-prod-label-acceptance-criteria.md`.

## Problem (measured 10 Oct 2026)

Prod labels `chatbot_semantic_parser` v52. Migrations publish the bare code constant as new unlabelled
versions, so no single version carries everything: the hand addenda #1495 / #1506 live only in
`alembic/data/*.addendum.txt`, the owner's console edits (installation guide) are not in the constant, and the
constant-built versions (report_engine_0001, report_drill_0001, chatbot_canonical_code, pk_0001) carry no
policy blocks, so their prompt has no `Specification <key>` lines (`chatbot_parser_prompt.py:495,1105`).

## Design (simplest thing)

1. Build the candidate once with the existing appliers, freeze it as a text file:
   constant (`SEMANTIC_PARSER_PROMPT`) -> `_order_broaden_prompt.patched_text` -> `_rogue_convo_prompt.patched_text`
   -> owner edits (b) applied as literal line replacements -> `alembic/data/chatbot_semantic_parser.candidate-20261010.txt`.
2. Migration `ppl_0001_parser_candidate` (down_revision = main's head at push time): read the file, refuse on a
   missing marker, append `BLOCKS_BEGIN + render_prompt_blocks(session) + BLOCKS_END` (lss_0001 formula), insert
   max+1 unlabelled with a commit_message; downgrade deletes own unlabelled row.
3. Static test `tests/chatbot/test_prompt_prod_label_candidate.py`: AC-1..AC-4 on the file, AC-5/AC-6 on the
   migration's pure helpers. Written red first by the tester.
4. Replay on a crew test copy of the snapshot (or the CHATBOT-EVAL runner): v52 vs candidate.

## Not doing

No label move (owner's click). No change to the constant itself. No runtime injection of policy blocks (named
trigger: if a constant-built version is ever labelled again, a publish-time guard that refuses a body without
blocks is the fix).
