# PLAN: embedding scope for frames, deleted sources and shared certificates (SCOPE-FRAMES)

Status: Plan (small fix track candidate; carries a data-backfill migration, so security-reviewer runs)

Lane: crew SCOPE-FRAMES. Follow-up to the #1607 (SCOPE-SEEDS) security review.

## Findings to verify and fix

1. `conversation_frame` embeddings are written company-less (`embedding_worker.py` company map,
   `frame_service.py` enqueue), so a company-scoped RAG search with
   `source_type=conversation_frame` can return other companies' contact summaries.
   Fix: stamp `company_id` from the frame contact's company; backfill existing rows; the RAG
   filter treats a company-less frame as not shared.
2. Embeddings of hard-deleted sources are never removed (delete event dead-letters), so an
   owned-type row with NULL company and no source row stays shared.
   Fix: handle the delete event (deactivate / remove the embedding); add an orphan cleanup step
   with a dry-run count.
3. A shared (NULL-company) certificate embeds covered product codes from every company.
   Fix: restrict embedded codes to the certificate's company (or company-less products only for
   a shared certificate).

Each fix gets a two-company red test first.

## Reachability question

Is (1) reachable today with real prod callers? Answered in the PR body.
