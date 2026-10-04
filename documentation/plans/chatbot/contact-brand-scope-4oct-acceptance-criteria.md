# UAC - Per-contact accessible brands (CONTACT-BRAND-SCOPE)

Card: `CARD-contact-brand-scope-4oct.md` (owner answers 4 Oct 2026, Q1-Q5 all (a)).
Plan: `PLAN-contact-brand-scope-4oct.md`.

"Scoped contact" = a contact whose `brand_ids` is a non-empty list. "Unscoped" = NULL or `{}`.
Test fixture contact: MOCHA-only. Products: one MOCHA (in scope), one SORENTO (out), one with
`brand_id` NULL (out, Q1).

## Data + settings

- AC-1 `respond_contacts.brand_ids uuid[] NULL` exists; migration is additive, no backfill; every
  existing contact is unscoped.
- AC-2 `GET /api/v1/user-management/contacts/{id}/brands` returns `{brand_ids, brands: [{id,
  brand_name}]}`; `PUT` same path with `{brand_ids: [...]}` replaces the list (same shape as the
  market-segments pair, `contacts.py:694,711`); `[]` stores NULL (all brands); an unknown brand id
  is a 422; needs `user_management.contacts.edit`. Audited like other contact access edits.
- AC-3 The contacts list response carries `brands` per row; the FE list has a hideable "Brands"
  column showing the brand names, "All" when unscoped.
- AC-4 Contact Profile tab shows a "Brands" card directly under the Locations card: a clearable
  SearchableMultiSelect of brands; empty shows "All brands"; save persists; reload shows the
  saved brands. Usable at 375px and 1280px. No UUIDs on screen.

## Central scope

- AC-5 One function `contact_brand_scope(db, contact_id)` returns the brand ids or None; every
  enforcement point calls it (no second reader of `brand_ids`).
- AC-6 Unscoped contact: every chatbot / MCP path returns byte-identical results to today.
- AC-7 Scoped contact: a product whose brand is not in the list, OR whose brand is NULL, is never
  returned by any chatbot tool, MCP tool or in-process resolver (applies to office-staff
  contacts too, Q2).

## Per path (scoped MOCHA-only contact)

- AC-8 Stock: MOCHA code answered as today; SORENTO code and unbranded code get the exact reply
  an unknown code gets (Q4), no stock, no ETA, no did-you-mean naming them.
- AC-9 Did-you-mean / miss suggestions (every tier: exact, trigram, embedding) offer MOCHA
  products only.
- AC-10 Pickers and numbered picks offer MOCHA only; a stale pick or follow-up carrying an
  out-of-scope product id from an earlier turn returns nothing for it.
- AC-11 Incoming / ETA / shipment products / packing list rows: MOCHA only.
- AC-12 Orders / DO / SO lines (orders, orders-by-product, outstanding): only MOCHA lines; an order
  with no MOCHA line is not returned; amounts are sums of the returned lines (Q3).
- AC-13 Reports (top selling, low stock, sales report, sales analysis): MOCHA rows only; totals
  computed from MOCHA lines only.
- AC-14 Spec / price / attachment / current stock list answers: MOCHA products only.
- AC-15 Escalation product suggestions / focus product: MOCHA only.

## Regression guard

- AC-16 A contract test enumerates every tool in `CHATBOT_READ_ONLY_TOOLS` and every MCP `CATALOG`
  tool, and fails if any tool is missing from `BRAND_SCOPE_TREATMENT`
  (`filtered` | `no_products`). Adding a new tool without a treatment goes red.
- AC-17 The fetch output guard drops any result row naming an out-of-scope product (by code or
  id) before the answer is built, for every tool marked `filtered`.
- AC-18 Kill tests: removing the session criterion, the raw-SQL filter, or the fetch guard each
  turns at least one per-path test red (proof notes in the PR).
- AC-19 Console-case replays: the card's examples 1, 2, 3 replayed through the turn runtime with
  the scoped contact give the expected replies.
