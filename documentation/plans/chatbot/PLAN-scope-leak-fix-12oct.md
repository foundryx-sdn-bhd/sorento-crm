# PLAN: chatbot resolver company-scope leak fix (SCOPE-LEAK-FIX)

Status: implemented, in review (small fix track: no migration, no auth/RBAC change, diff under
300 lines; security-reviewer joins because the diff touches multi-company scoping).

## Problem

Lane CHAT-SPEED (#1602) reported two pre-existing company-scope gaps in the chatbot resolve path:

1. `entity_resolver._tier3_embedding_lookup` runs a raw `text()` vector search with no
   `company_id` filter. The `do_orm_execute` scope listener never sees raw SQL.
2. `db.query(exists().where(Product...))` is not filtered by the scope listener
   (`with_loader_criteria` does not reach a bare `exists()`, see
   `product_predicate_service.py`).

The audit for this lane found a third, and the owner added a fourth:

3. `_rag_resolve_phrase` (the `resolve_entities_to_filters` fallback behind the MCP orders /
   stock / products tools) runs the same unscoped vector search, and its hits carry no uuid, so
   the `_attach_company_info` backstop cannot drop them: another company's codes were echoed.
4. The per-request resolve memo and the cross-request vector cache from #1602 were keyed without
   the caller's scope.

## Fix (no new mechanism)

- Tier 3 and RAG phrase: `company_sql_predicate(db, "ec.company_id", shared=True)`, the existing
  raw-SQL helper. NULL-company chunks are shared knowledge (forms), as in
  `embedding_service._embedding_company_filter`.
- Owner decision (b): `debtor:<code>` seeds are company-less (migration 304) although their
  orders are not, so any company scope excludes them from tier 3, RAG phrase (one fragment,
  `_embedding_scope_sql`) and `EmbeddingReadService.search_current` (`/external/rag/search`).
  Staff (scope None) still see them. Follow-up lane (a): stamp debtor seeds per company, then
  drop the exclusion.
- `_scope_key(db)` = (company scope, brand scope) joins `_memo_key`, the per-request embedding
  memo key and `_vector_key`. Brand scope is in the key because the listener narrows Product
  reads by it, so tier-1/2 answers depend on it. Company in `_vector_key` is the owner's rule
  (one company never reuses another's entry), although the vector itself carries no company data.
- Bare `exists()`: no fix. No app code on the chatbot resolve or MCP read paths issues the
  uncorrelated shape over a company-scoped model (audit in the PR body). The listener gap is
  pinned by a strict xfail so a listener change that closes it is noticed. Changing the listener
  itself would be a new mechanism on every request; trigger for doing it: the first call site
  that needs the shape.

## Tests (red first)

`sorento_crm_backend/tests/test_resolver_tier3_company_scope.py`: two companies, the same product
code / customer name in each. A contact scoped to company A never resolves to, or sees, company
B's row on any path above; staff (scope None) behaviour is unchanged.
