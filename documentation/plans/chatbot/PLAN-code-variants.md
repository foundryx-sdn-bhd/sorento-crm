# PLAN - CODE-VARIANTS (product family relation in did-you-mean)

Status: building (track L: migration). Mock v1 approved by the owner 11 Oct 2026 ("ok"). Card answered 11 Oct 2026. UAC:
`code-variants-acceptance-criteria.md`. Branch `crew/code-variants`.

## Slices (one lane, one PR)

S1 Data (AC-1..3)
- `app/services/product_family.py`: `family_code_for(code) -> str | None` (the one Python rule).
- Migration `family_code_0001` (revision id <= 32 chars, down_revision = current head): two
  columns, index `ix_products_company_family_code`, backfill UPDATE with the same rule in SQL
  (`upper(regexp_replace(split_part(product_code,'-',1),'\s','','g'))`, NULL unless `~ '[0-9]'`).
- Model `Product.family_code`, `Product.family_code_manual` (`app/models/product.py` beside
  `variant_link_manual`).
- Write path: one place per write that already exists, prefer a single ORM `before_insert` /
  `before_update` hook on `Product` (covers AutoCount `_insert`/update at
  `master_ingest_service.py:1446-1490`, `product_service` create/update, Excel import) IF the
  tests show every path goes through the ORM; otherwise set it at each path beside
  `reconcile_new_alternatives_safely`.

S2 API + detail (AC-4, AC-5)
- Routes beside `variant-parent` (`app/api/v1/master_data/products.py:455-520`), service methods
  beside `set_variant_parent` / `reset_variant_auto`.
- `ProductResponse` fields; detail-only populate beside `_populate_variant_graph`
  (`product_service.py:840`). Both manual dict builders checked (lesson: new column must reach FE).

S3 Resolver (AC-6..9)
- `entity_resolver.py` alternatives block (`:5500-5540`): compute the key from `raw_tokens`
  (the chatbot folds dashes away, `resolve_gate.py:511-525`, raw rides at `:672`), add family
  rows (scope via `_company_scope_sql`, `_chat_visible_product_ids`, `_drop_inactive_products`),
  `similarity()` scored like `_trgm_lookup` (`:3029`), floor exemption, family-first stable sort,
  cap 5. The fallback re-run in `references.py:2643` must keep family alternatives (carry the raw
  text by token, not by position, or merge alternatives from the primary pass).

S4 Screen (AC-10)
- `ProductVariantsTab.tsx`: Family section; hooks in `../../hooks/useProducts`, service call via
  the products service + `lib/api-client`. Mock first, owner approves.

## Tests (tester writes first, red)

- `tests/test_product_family_code.py`: AC-1 table; SQL rule parity on Postgres; AC-3 each write
  path (AutoCount ingest create+update, API create/update/code change, Excel import); manual
  never rewritten; AC-4 routes; AC-5 detail fields + `response_model` keeps them.
- `tests/test_resolver_family_alternatives.py`: seeded own chain (CI DB is empty): a
  `SRTWC8357`-style family vs other-model `-SH-150` codes; folded token + raw_tokens; family
  first, floor exemption, cap; AC-8 no-change cases; company scope (other company's family never
  shown); inactive member dropped with `alternatives_active_only`; route-level AC-9 incl.
  fallback re-run.
- `ProductVariantsTab.test.tsx`: Family section render, edit, reset, empty state.

## Gates

Red-first commit `test(red):`, kill test, reviewer + security-reviewer (L), crew eval gate
(touched topics inventory.* + 30 smoke), agent-browser 1280/375 on the crew test copy, hand test
with before/after cases `cbs208-bl`, `SRTUB206-BACK INLET`, `SRTWC8357-SH-150`.

## Slice 2 (DIY / -N / -PP, owner 11 Oct)

Generalise `new_base_codes` / `_new_codes_for` / the backfill filter in
`app/services/product_alternative_service.py` to the markers NEW, DIY, N, PP plus PP-for-NL;
reuse source `auto_new` (the CHECK allows only auto_new/manual; the screen shows non-manual as
"Auto"). Script prints the CB55CR-N note. `answer.py` product_attachment miss maps the raw to the
stored code. Tests: `tests/test_product_alternatives_suffix_rule.py`,
`tests/chatbot/test_attachment_miss_prints_stored_code.py`. Prod backfill is the owner's step
(command in the PR body).
