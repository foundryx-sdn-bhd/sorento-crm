# UAC - CODE-VARIANTS (product family relation in did-you-mean)

Card: `CARD-code-variants-11oct.md`. Owner answers 11 Oct 2026: Q1 (a) `products.family_code` +
manual override; Q2 use the current resolver, family relation inside its ranking, no LLM;
Q3/Q4/Q5 current behaviour, minimal change (same roster size, same flows, same order apart from
keeping the user's own model family in the list). Fuzzy/similarity on PRODUCT CODES inside the
resolver is allowed; fuzzy matching of the input message to find mentions is not.

## Rule

AC-1 `family_code_for(code)`: the text before the first `-`, all whitespace removed, uppercased;
`None` when that text is empty or holds no digit.

| code | family_code |
| --- | --- |
| `SRTWC8357-RL-P` | `SRTWC8357` |
| `SRTWC8066` | `SRTWC8066` |
| `SRTUB206-BACK INLET` | `SRTUB206` |
| `MWC7630 -S10` | `MWC7630` |
| `cbs208-bl` | `CBS208` |
| `TPE-9201`, `SRT-BB7001`, `MWC-SC604` | None |
| `CWC INLET`, `SORENTO` | None |
| `` / None | None |

## Data

AC-2 Migration adds `products.family_code VARCHAR(100) NULL`, `products.family_code_manual
BOOLEAN NOT NULL DEFAULT false`, index on `(company_id, family_code)`, and backfills
`family_code` for every row with `family_code_manual = false` by the SAME rule (a test asserts
SQL and Python agree on the AC-1 table).

AC-3 Every product write sets `family_code` from `product_code` unless `family_code_manual`:
AutoCount product sync (create and update), API create, API update (incl. a code change), Excel
bulk import (create and update). A manual row is never rewritten by any of these.

AC-4 `PUT /api/v1/products/{id}/family-code` body `{"family_code": "<text>" | null}`: stores the
value with whitespace removed and uppercased (empty or null stores NULL), sets
`family_code_manual = true`, returns the product. `POST /api/v1/products/{id}/family-code-reset`
clears the flag and re-derives by AC-1. Same auth/permission as the existing
`variant-parent` routes (`app/api/v1/master_data/products.py:460-520`). Company scoping as any
product write.

AC-5 Product detail response carries `family_code`, `family_code_manual` and `family_members`:
`[{id, product_code, product_name}]` for active products in the same company with the same
non-null `family_code`, excluding itself, ordered by `product_code`. List rows do not carry
`family_members`.

## Resolver

AC-6 For a product-typed token that ends with NO match (the existing alternatives block,
`entity_resolver.py:5500-5540`), its family key is `family_code_for(raw text)` when the caller
passed `raw_tokens` for it, else `family_code_for(token)`. When the key is non-null, products
with `family_code = key` (same company scope, chat-visible, and active-only when
`alternatives_active_only`) are added to the candidate pool with `display["same_family"] = True`
and their real `similarity()` to the token.

AC-7 Ordering: same-family candidates come first (by similarity desc among themselves, then
code); every other candidate keeps today's order after them. The `ENTITY_MISS_SUGGEST_FLOOR`
does not drop a same-family candidate. The cap stays `_ALTERNATIVES_CAP` (5); the roster still
prints its current count.

AC-8 No change when the key is null, when no product carries that family code (a family of ONE
product still leads, see `cbs208-bl`), for non-product
tokens, for matched tokens, and for the data-miss neighbour probe
(`find_entity_neighbours_with_data`): alternatives are identical to today.

AC-9 Chatbot shape: the resolve route the chatbot uses (`app/api/v1/system/references.py`,
folded token + `raw_tokens`, product type) returns the family-first alternatives, including when
the route's fallback re-run (`tokens_override`, no `raw_tokens`) handles the token.

Examples on the prod copy (`sorento_eval_base`), roster top 3:

| typed | today | after |
| --- | --- | --- |
| `SRTWC8357-SH-150` | SRTWC8354-SH-150, SRTWC8036-SH-150, SRTWC8152-SH-150 | three of SRTWC8357-RL / -RL-P / -200-RL / -300-RL |
| `SRTUB206-BACK INLET` | SRTUB906/2020/668-BACK INLET | SRTUB206, SRTUB206-BI, SRTUB206-NZ (by similarity) |
| `cbs208-bl` | CBS201-BL, CBS203-BL, CBS205-BL | CBS208-DB first, then today's list |
| `SRTKS8152` (no dash, typo of the number) | unchanged | unchanged |

## Screen

AC-10 Product detail, Variants tab: a Family section (approved mock,
`documentation/mockups/code-variants/index.html`) showing the family code or an empty state,
a Manual badge when manual, Edit (inline input, Save / Cancel), Reset to auto when manual, and
the same-family products as links to their detail pages. Usable at 375px and 1280px.

## Slice 2 (owner, 11 Oct 2026): DIY / -N / -PP alternatives, like -NEW

Crew rulings 11 Oct: Q1 (a) PP-for-NL rule; Q2 (a) base -> suffixed for all, CB55CR-N named for a
hand unlink after the prod run; Q3 (b) no attachment fallback in this lane.

AC-11 The existing code rule (`product_alternative_service.py`, source `auto_new`) also links Y as
the alternative of X (same company, both exist) when removing exactly one whole `DIY`, `N` or `PP`
segment from Y gives X, or replacing exactly one whole `PP` segment of Y with `NL` gives X. No
description parsing, no fuzzy, no new source value, no migration.
AC-12 `scripts/backfill_product_alternatives.py`: dry run by default (counts + every pair, writes
nothing), `--apply` writes, idempotent; output names CB55CR-N as the known reverse case.
AC-13 The product_attachment not-found reply prints the resolved product's stored code
(SRT207-DIY) when it equals the parser's raw text under the token fold; otherwise the raw.
