# PLAN: chatbot resolver company-scope leak fix (SCOPE-LEAK-FIX)

Status: in progress (small fix track: no migration, no auth/RBAC change, diff under 300 lines;
security-reviewer joins because the diff touches multi-company scoping).

## Problem

Lane CHAT-SPEED (#1602) reported two pre-existing company-scope gaps in the chatbot resolve path:

1. `entity_resolver._tier3_embedding_lookup` runs a raw `text()` vector search with no
   `company_id` filter. The `do_orm_execute` scope listener never sees raw SQL.
2. `db.query(exists().where(Product...))` is not filtered by the scope listener
   (`with_loader_criteria` does not reach a bare `exists()`, see
   `product_predicate_service.py`).

## Fix (no new mechanism)

- Tier 3: apply the same scope the rest of the resolver uses (`_company_scope_sql`), allowing
  NULL-company (shared) embedding rows exactly as `embedding_service._embedding_company_filter`
  does.
- `exists()`: add the explicit company predicate (`build_company_predicate`) where a bare
  `exists()` over a company-scoped model sits on the chatbot resolve / MCP read path.

## Tests (red first)

Two companies, same product name. A contact scoped to company A must never resolve to, or see,
company B's row on either path.

## Audit list

Every other raw `text()` / bare `exists()` on the chatbot resolve + MCP read paths, with
file:line and verdict, goes in the PR body.
