# CARD - product alternatives (-NEW) + availability Alternative line (PRODUCT-ALTERNATIVES)

Status: CARD POSTED (8 Oct 2026), awaiting owner answers on Q1-Q5 before code. Track: L
(migration, chatbot reply change, product screen section). Lane PR #1533 carries
`crew-lane: PRODUCT-ALTERNATIVES`. Real-data counts requested from crew (PR comment
`crew-data-request`); they fill the "Measured" table below when they arrive.

## The journey (owner, 8 Oct 2026)

A dealer (availability mode) asks for stock of `SRTBF11821`. That code is discontinued with
nothing on hand; the live product is `SRTBF11821-NEW` (16 in BRW). Today the dealer reads
"no stock, discontinued" and never hears about `-NEW`. Same for `SRTWC286-SH-200`, replaced by
`SRTWC286-SH-NEW-200` (the `NEW` sits mid-code, not as a suffix). The owner wants:

1. a stored "alternative" link old code -> new code that the system always sets for `-NEW`,
   on product sync, product creation and a one-off backfill;
2. when the asked code has an alternative, the reply carries an `Alternative:` line with the
   alternative's own availability, in the same unified shape;
3. the staff stock summary and the dealer availability reply read the same lookup.

## What exists today (measured on origin/main 57fd71c4)

| Fact | Where |
| --- | --- |
| `Product` is company-scoped (`CompanyScopedMixin`, not shared); code unique per company | `app/models/product.py:220-231`, `:345` |
| Discontinued is the boolean `products.is_discontinued` (AutoCount-derived) | `app/models/product.py:294`; derived in `app/services/master_ingest_service.py:1346`, `:1398-1402` |
| No product -> product "alternative" link exists. The one product self-link is the variant graph (`variant_of_id` + `variant_link_manual`), derived from codes by `reconcile_variant_links` on API create/update/delete | `app/models/product.py:251-256`; `app/services/variant_link_service.py:153`; `app/services/product_service.py:1112`, `:1183` |
| AutoCount sync: per-row upsert by code, each row in a savepoint; `_post_write_product_hooks` runs after every create AND update. It never runs variant reconciliation | `app/services/master_ingest_service.py:813`, `:1105`, `:1146-1153`, `:1443` |
| Other creation paths: API create, Excel bulk import (new rows built at `:1905`, batched commits) | `app/services/product_service.py:1067`, `:1551`, `:1905` |
| Stock tool for both modes: `StockService._apply_stock_visibility` builds `stock_summary` (compact, staff) and `stock_availability` (dealer) from the same `products_by_id` | `app/services/inventory_service.py:1472`, `:1489-1522`, `:1682-1704` |
| Discontinued marker for BOTH modes is one line in the renderer | `app/services/chatbot/lanes/business/fetch.py:3147-3149` (`_item_line`) |
| Dealer line shape `SRTBF11821 x 5: <verdict>`, verdicts `✅ Got stock.` / `✅ Only N got stock.` / `❌ No stock, ETA dd/mm/yyyy.` / `❌ No stock and no incoming. Please refer to your salesman.` / `🚫 the quantity is more than ...` | `sorento_crm_mcp/sorento_crm_mcp/presenters.py:1508-1518`, `:1521-1555` |
| Availability presenter builds its item flags by hand (a new flag needs a presenter line); compact passes `flags` through as-is | `presenters.py:1600-1609`; `presenters.py:1482` |
| The existing `payload["alternatives"]` is a DIFFERENT thing: a "neighbour products with stock" probe on an empty answer, deliberately switched OFF for dealers (AC-B14: "naming other in-stock products is the one thing the dealer mode exists to withhold") | `app/services/inventory_service.py:1131-1157` |
| Dealer turns answer exact codes only; a typed prefix keeps its family for the "matches N products. Which one?" picker | `app/services/chatbot/engine.py:511` (`_exact_codes_only`); `app/services/chatbot/turn/task.py:865-898` |
| Product detail page tabs (Overview, Stock, ..., Variants, ...) with a Variants tab + picker dialog to reuse | `sorento_crm_frontend/app/(protected)/master-data-management/products/[id]/components/ProductDetail.tsx:296-333`, `ProductVariantsTab.tsx`, `ProductVariantPickerDialog.tsx` |

## Proposed behaviour (crew reading, pending Q1-Q5)

**Data.** New additive table `product_alternatives` (company-scoped):
`product_id` -> `alternative_product_id`, `source` (`auto_new` | `manual`), created by/at,
unique on the pair, both FKs `ON DELETE CASCADE`, same-company only. One boolean on
`products`, `alternatives_manual` (the `variant_link_manual` precedent): once a person edits a
product's alternatives by hand, the rule never touches that product's links again until
"Reset to auto".

**The -NEW rule (a data rule on product codes, never on user text).** Product Y is the
alternative of product X, same company, when removing exactly ONE `NEW` segment (split on
`-`, case-insensitive) from Y's code gives X's code exactly. So `SRTBF11821-NEW` ->
alternative of `SRTBF11821`; `SRTWC286-SH-NEW-200` -> alternative of `SRTWC286-SH-200`;
`SRTWC286-SH-NEW-P` -> alternative of `SRTWC286-SH-P`; `X-NEW-NEW` -> alternative of `X-NEW`
(chains are one hop each). `XNEW` / `NEWTON-1` never match (whole segment only). One idempotent
function `reconcile_new_alternatives(db, product)`: called for the written product from the
AutoCount ingest hook, API create/update, Excel import, and a one-off backfill script (dry run
by default). It only ADDS `auto_new` links (and removes an `auto_new` link whose code no longer
matches after a rename); it never touches `manual` links or an `alternatives_manual` product.

**Lookup.** One function `alternatives_for(db, product_ids) -> {pid: [alt product]}` (one hop,
active + searchable alternatives only, ordered by code). Called once inside
`_apply_stock_visibility` right after `products_by_id` (`inventory_service.py:1472`), so
compact (staff) and availability (dealer) read the same map.

**Dealer reply (availability mode).** The asked line is unchanged. Under it (after the
discontinued marker when there is one), one `Alternative:` line per alternative, judged with
the SAME asked quantity, cap (X) and on-hand basis as any other line - so it can only ever say
one of the existing verdict sentences, never a figure of ours beyond what the mode already
allows. When the quantity is still owed, the question is unchanged ("How many units of
SRTBF11821?"); the Alternative line appears with the answer.

**Staff summary (compact).** Under the discontinued marker, `*Alternative:* <code>`, and the
alternative's own block is listed if the ask did not already list it (Q4).

## Card questions (owner decides; each has crew's recommendation)

**Q1. The -NEW rule vs "no auto matching, explicit links only".** The rule above is an exact,
deterministic transform on product codes that WRITES explicit stored links the owner can see
on the product page and remove; nothing matches user text. Removing or adding a link by hand
marks that product `alternatives_manual`, so the next sync never re-adds what was removed.
Options: (a) as described, links visible + removable + sticky manual edits; (b) the rule only
proposes, a person confirms each link. **Recommend (a)**: the owner asked for "always set
-NEW", and (b) leaves every dealer ask unanswered until someone clicks through every pair.

**Q2. When does the Alternative line show?** (a) only when the asked code is discontinued;
(b) when discontinued OR its verdict is not a full `✅ Got stock.` (no stock, incoming,
partial, over cap); (c) always when a link exists. **Recommend (b)**: the owner's words are
"discontinued, no stock already"; a live old code with full stock needs no detour, but a dealer
told "no stock" while the replacement sits in BRW is the exact miss being fixed. Note this
reverses AC-B14 ("never name other in-stock products to a dealer") for STORED alternatives only;
the neighbour probe stays off for dealers.

**Q3. The "SRTWC286 matches N products. Which one?" picker.** (a) keep every code, unchanged;
(b) hide an old code from the picker when it is discontinued AND has an alternative in the same
list (dealer mode only). **Recommend (b)**: in the screenshot `-SH-200` and `-SH-NEW-200` both
appear and the dealer cannot tell which is live; a discontinued code with its replacement right
there is noise. A live (not discontinued) old code stays listed.

**Q4. Wording (customer-facing).** Dealer, "SRTBF11821" then "5":

(a) one line:
```
SRTBF11821 x 5: ❌ No stock and no incoming. Please refer to your salesman.
⚠️  *(PRODUCT DISCONTINUED)*
Alternative: SRTBF11821-NEW x 5: ✅ Got stock.
```
(b) label line + verdict line (strict "one fact per line, `Label: value`"):
```
SRTBF11821 x 5: ❌ No stock and no incoming. Please refer to your salesman.
⚠️  *(PRODUCT DISCONTINUED)*
Alternative: SRTBF11821-NEW
SRTBF11821-NEW x 5: ✅ Got stock.
```
Also with (a): `Alternative: SRTWC286-SH-NEW-200 x 2: ❌ No stock, ETA 20/10/2026.` and, for a
discontinued code whose replacement is partial, `Alternative: SRTBF11416-NEW x 10: ✅ Only 5 got
stock.` Should the refer sentence drop from the old line when an alternative has stock?
**Recommend (a)** and keep the old line exactly as today (unchanged wording rule). Staff
summary gets `*Alternative:* SRTBF11821-NEW` under the marker plus the `-NEW` block appended
when it was not already listed.

**Q5. Admin screen now or later?** (a) now: an "Alternatives" section on the product detail
page's existing Variants tab (list, add via the existing product picker, remove = deferred
Unlink, "Reset to auto"); no new screen, so no mock; (b) later: data + rule + chatbot only, links
editable only by SQL. **Recommend (a)**: Q1's "owner can see and remove" is only true with it,
and it reuses `ProductVariantsTab` / `ProductVariantPickerDialog`.

## Measured on real data

Pending crew's run of the `crew-data-request` SQL on `sorento_cagent_stack` and
`sorento_ai_automation_20261006`: -NEW codes with a base (suffix / mid-code), orphans (no
base), chains, bases discontinued / zero on hand / live with stock.

## Out of scope

Detailed mode (internal staff) output beyond what compact gets; MCP tool for n8n (the payload
key rides along; no new tool); the neighbour-suggestion probe (unchanged, still off for
dealers); any user-text matching.
