# PLAN: embedding scope for frames, deleted sources and shared certificates (SCOPE-FRAMES)

Status: Built, in review (small fix track: ~300 lines of code plus tests; carries a data-backfill migration and touches multi-company scoping, so security-reviewer runs)

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

## What shipped

- `embedding_worker._frame_company_id`: a frame's company is its contact's company when the
  contact is in exactly one (same resolver as the request-entry scope); otherwise NULL.
- `embedding_service._embedding_company_filter`: under a company scope, NO
  `conversation_frame` row is returned (crew ruling (a), 12 Oct: a frame is one contact's private
  conversation; recall stays on `/external/memory/frames/search`). Scope None still sees them.
- `process_embedding_queue_item`: a `.deleted` event whose source row is gone deletes the
  document and chunks (status `removed`), plus the `product_spec` document on `product.deleted`;
  a stale delete for a live row removes nothing.
- `purge_orphan_embeddings` + `scripts/purge_orphan_embeddings.py`: dry-run count by default,
  `--apply` deletes. `debtor:` seeds and row-less types are never touched.
- Certificate codes: own company only; a shared certificate embeds company-less products only.
- Migration `sframes_0001_frame_company`: additive company backfill on frame embeddings, and
  a rebuild queued for every certificate whose embedding carries another company's codes.

The frame company stamp has no reader today: a scoped search drops every frame and an
unscoped one ignores `company_id`. It is kept on purpose (crew ruling (a), 12 Oct) as defence
in depth, so the frame rows obey the same company rule as every other embedding if the
blanket exclusion is ever relaxed.

## Release steps (owner)

1. `python scripts/purge_orphan_embeddings.py` (dry run: counts), then `--apply`.
2. Confirm `ENABLE_SCHEDULER` is on, so the queued certificate rebuilds drain.

## Follow-ups (outside this lane, pre-existing)

- `/external/memory/frames/search` trusts the body's `contact_id`/`space_id`; any key holding
  `integration.conversation_context.edit` can read any contact's frames by naming them.
- `Attachment` and `ConversationFrame` raise no delete event; their orphans need the sweep.

## Reachability (finding 1)

No caller in this repo sends `source_type=conversation_frame` to `/external/rag/search`:
the in-process `fuzzy_resolver` passes customer / product / transporter, the MCP server has no
RAG tool, and the frontend never calls it. The endpoint needs an integration key holding
`integration.semantic_search.use`. The company scope is applied only when the call carries
`?contact_id=&space_id=`. Without them the scope is None, which already sees every company
by design. So the leak needs an external n8n workflow calling with a contact identity and
either `source_type=conversation_frame` or no `source_type` at all (frames rank alongside
everything else). The n8n flows are not in the repo, so this cannot be ruled out from code.

## Trigger for more

- A scheduled orphan sweep: only once the dry-run count shows bulk deletes keep producing orphans.
- Per-company certificate documents: only if a shared certificate must match by product code.
