# PLAN: AI Extract did-you-mean for misread product codes (AI-EXTRACT-DYM)

Status: In review on PR #1558, awaiting the owner's answers to the behaviour card (crew-ask);
built on its recommendations, reviewer + kill test + browser pass done. Track: small fix (expected diff under ~300 lines, no migration, no auth/RBAC
change, no new ingest surface).

UAC: `ai-extract-dym-acceptance-criteria.md` alongside.

## Problem

AI Extract on the Price tag request portal matches product codes exact-only
(`sorento_crm_backend/app/services/ai_extract/extract_service.py` `_resolve_product_codes`).
A code the model misread lands as "Not found" and the salesman retypes it. Real case (9 Oct):
SRTK1634SS-BL (real SRTKT1634SS-BL), SRTK1636SS-GM (SRTKT1636SS-GM), SRTK1632SS (SRTKT1632SS),
SRTKT1868-BL (SRTKT1868SS-BL).

## Rule (owner, binding)

Suggest, never auto-apply. The user confirms each suggestion. Every failure ends in a clear state.

## What already exists (the reason this is small)

The same `resolve_references` call AI Extract already makes computes trigram "did you mean"
neighbours for every miss (`entity_resolver.py`, `tr.alternatives`): company-scoped in SQL and
again by `_apply_company_scope`, placeholders dropped (`_chat_visible_product_ids`), floor
`ENTITY_MISS_SUGGEST_FLOOR` (0.30), cap 5, prefix-extensions first then similarity. AI Extract
throws them away. No new matcher and no new query: keep them.

## Decisions (card recommendations; replaced by owner answers when they arrive)

- D1 Candidates only for a line with no exact match. Matched lines unchanged.
- D2 Candidates = `tr.alternatives` with entity_type `product`, filtered to `is_active` products
  (same filter as the manual Item picker, `tag_data_service.search_products`), top 3.
- D3 Products only. Trigger to add sets: the first real misread set code.
- D4 Ambiguous exact (2+ scoped hits): unchanged (no candidates).
- D5 Floor: the resolver's shared 0.30.
- D6 UI: in the existing dialog's Match column, a not-found row with candidates shows a clearable
  `SearchableSelect` ("Did you mean?") with nothing pre-selected. Picking one marks the row
  "Matched (picked)" and Apply adds that product (merging into an existing line like an exact
  match). Clearing returns it to Not found. An unpicked row is skipped and named in the
  "Not found" toast, exactly as today.
- D7 Never stuck: zero candidates or a failed probe = plain "Not found". Candidates ride the extract
  response, so no extra request and no loading state.

## Contract

`ExtractedProductLine.suggestions: list[ExtractedProductSuggestion]` (default `[]`), where a
suggestion is `{product_id: str, product_code: str, product_name: str | None}`. Present only when
`match` is null.

## Slices

1. BE: `_resolve_product_codes` returns suggestions for misses; `_extract_products` sets them.
   Tests: `tests/test_ai_extract_dym.py`.
2. FE: `AIExtractedProductLine.suggestions`; `PriceTagRequestForm` Match cell picker; apply reads
   the picked product. Tests: `PriceTagRequestForm.aiExtractDym.test.tsx`.
