# PROMPT-PROD-LABEL acceptance criteria

Plan: `PLAN-prompt-prod-label-10oct.md`. Card: crew `state/files/PROMPT-PROD-LABEL-card.md`.

AC-1  One text file `sorento_crm_backend/alembic/data/chatbot_semantic_parser.candidate-20261010.txt`
      holds the candidate parser body (no policy blocks). It contains every marker:
      `CUSTOMER ACCOUNT NUMBER` (acct), `LOW STOCK REPORT FILTERS` (lowstock), `TRAP SPECS` (trap),
      `PRODUCT CODE: entities[].canonical_code` (canon), `"po_refs"` (porefs), `sales_ranking` (report),
      `ranking_drill` (drill), `== PROMOTION KIND ==` (promo_kind),
      `A MISS reply never moves the domain either` (#1495),
      `A COUNT WITH NO CODE BESIDE IT` and `A DESCRIPTION WITH A CODE INSIDE IT` (#1506),
      `installation guide` as an attachment_type (owner edit, v47), and `{{current_date}}` exactly once.
AC-2  The file carries none of the obsolete lines the later PRs rewrote on purpose:
      `Never emit BOTH an entity and a reference_position` (#1352), `"SPO", "shipment", "container" -> ["SPO"]`
      (#1373), `null (the default, delivered)` (#1447).
AC-3  Every line of the current `SEMANTIC_PARSER_PROMPT` outside the owner-edit lines is in the file
      (the candidate drops nothing the code ships).
AC-4  Each addendum sits once, at its documented anchor (`tests/chatbot/_order_broaden_prompt.py:26`,
      `tests/chatbot/_rogue_convo_prompt.py:33-35`).
AC-5  A migration publishes the file plus the policy blocks rendered at run time
      (`<<<CHATBOT POLICY BLOCKS>>>` ... `<<<END CHATBOT POLICY BLOCKS>>>`, `render_prompt_blocks`) as ONE new
      UNLABELLED `chatbot_semantic_parser` version (max + 1). No label moves. Idempotent on the same body.
      Downgrade deletes only its own row and only while unlabelled.
AC-6  The migration publishes nothing (and logs why) when any AC-1 marker is missing from the file.
AC-7  Single alembic head after the migration.
AC-8  Replay: the owner's 9 Oct prod failure messages + 20 real varied messages (masked) run on v52 and on the
      candidate; PASS/FAIL table per message in the crew report. No real data committed.
AC-9  Final crew report names the version to label (highest with every marker AND the policy blocks), what
      changes for users, rollback (label back to 52) and the owner's confirm SQL.
