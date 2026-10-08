# CARD - product alternatives (-NEW) + availability Alternative line (PRODUCT-ALTERNATIVES)

Status: Round A accepted by the owner (9 Oct 2026). Round B (Addendum 1 domains) built and
pushed; in review, then its own hand test. Track: L (migration, chatbot reply change,
product screen section). Lane PR #1533 carries `crew-lane: PRODUCT-ALTERNATIVES`. Real-data
counts measured by crew (8 Oct), see "Measured on real data".

## Owner answers (8 Oct 2026, verbatim)

"1 - yeah a, 2 - yeah b, 3 - yeah that one can keep, so a, Q4 - yeah ok, this is simple,
good, Q5 - yeah okay a, corect"

| Q | Ruling | What it means in the build |
| --- | --- | --- |
| Q1 | (a) | The -NEW rule writes `auto_new` links, visible and removable on the product page; any hand edit sets `products.alternatives_manual` and the rule never touches that product again until Reset to auto. Whitespace around each segment is trimmed (crew). |
| Q2 | (b) | Dealer: the Alternative line shows when the asked code is discontinued OR its verdict is not a full `✅ Got stock.` (no stock, incoming, partial, over cap). Staff summary: when discontinued OR the total on hand is 0. |
| Q3 | (a) | The "matches N products. Which one?" picker is UNCHANGED; no code is hidden. |
| Q4 | (a) | One extra line `Alternative: <code> x <N>: <verdict>` under the old line (after its marker). The old line keeps its wording, except that `Please refer to your salesman.` drops from it when an alternative line has stock (crew reads "yeah ok" as accepting that recommendation). Staff summary: `*Alternative:* <code>` under the old block, plus the alternative's own block when the ask did not already list it. |
| Q5 | (a) | Alternatives section now, on the existing product Variants tab: list, Add (existing product picker), Unlink (deferred), Reset to auto. Existing components only, no mock. |

Live-parser checks run on crew's machine (the cloud sandbox has no OpenAI).

## The journey (owner, 8 Oct 2026)

A dealer (availability mode) asks for stock of `SRTBF11821`. That code is discontinued with
nothing on hand in the warehouses a dealer sees; the live product is `SRTBF11821-NEW` (16 in BRW). Today the dealer reads
"no stock, discontinued" and never hears about `-NEW`. Same family for `SRTWC286-SH-200`, whose
replacement is `SRTWC286-SH-NEW-200` (the `NEW` sits mid-code, not as a suffix; the old code
itself is not discontinued, see the measured table). The owner wants:

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
`-`, case-insensitive, whitespace around each segment trimmed: crew ruling 8 Oct, so
`SRT1000-CR - NEW` links to `SRT1000-CR`) from Y's code gives X's code exactly. So `SRTBF11821-NEW` ->
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
Measured case for the alternative WITHOUT stock: `BMC6067` (discontinued, 18 on hand) ->
`BMC6067-NEW` (0 on hand). As ruled (b), discontinued ALONE triggers the line (owner via crew,
8 Oct, confirming the table over this card's earlier wrong example): asking 5 reads
`BMC6067 x 5: ✅ Got stock.`, the marker, then `Alternative: BMC6067-NEW x 5: ❌ No stock and
no incoming. Please refer to your salesman.` An alternative with no stock is still shown (it
is the true answer about the replacement).

**Q3. The "SRTWC286 matches N products. Which one?" picker.** (a) keep every code, unchanged;
(b) hide an old code from the picker when it is discontinued AND has an alternative in the same
list (dealer mode only). **Recommend (b)**: in the screenshot `-SH-200` and `-SH-NEW-200` both
appear and the dealer cannot tell which is live; a discontinued code with its replacement right
there is noise. A live (not discontinued) old code stays listed. Measured: `SRTWC286-SH-200`
is NOT discontinued (1 on hand vs 4 on `-SH-NEW-200`), so (b) keeps it; `SRTWC286-SH-P`
(discontinued, 1 on hand -> 23 on `-SH-NEW-P`) is the one (b) hides.

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

Crew, 8 Oct 2026, read-only. Figures are prod snapshot `sorento_ai_automation_20261006` /
dev `sorento_cagent_stack`.

| Measure | prod / dev |
| --- | --- |
| Codes with a `NEW` segment | 118 / 118, across 2 companies |
| Base exists in the same company | 108 / 108 (105 suffix, 3 mid-code: `SRTWC286-SH-NEW-150`, `-200`, `-P`) |
| Chains (`-NEW-NEW`) | 0 / 0 |
| Orphans (no base) | 10 / 10, e.g. `SRTWHBWP-GB9032B-NEW`, `SRTWC8066-S-150-NEW`; `MWCX7605-RL-S10` has its base only in the OTHER company, so no link |
| Base discontinued | 58 / 57 |
| Base zero on hand | 28 / 30 (both discontinued and zero: 23 / 23) |
| Base live (not discontinued) with stock | 45 / 44 |
| -NEW with stock | 76 / 60 |
| Cross-brand pairs | 0 |
| Spaced codes the plain split missed | `SRT1000-CR - NEW`, `SRTUB6503-BACK INLET - NEW` (real bases) -> rule trims whitespace per segment; `**NEW` has no base, ignored |

Examples (prod; base discontinued?, base on hand -> -NEW on hand):

| Old | Disc. | Old on hand | -NEW on hand |
| --- | --- | --- | --- |
| `SRTBF11821` | yes | 3 (2 BRW-DFCT, 1 SPARE/P; 0 in dealer-visible warehouses) | 77 (16 BRW) |
| `SRTWC286-SH-200` | NO | 1 | 4 |
| `SRTWC286-SH-P` | yes | 1 | 23 |
| `SRTBF11416` | yes | 1 | 98 |
| `BMC6067` | yes | 18 | 0 (alternative with no stock) |

Correction to the journey above: `SRTBF11821` is 0 on hand only in the dealer-visible
warehouses (the 3 units sit in BRW-DFCT and SPARE/P); `SRTWC286-SH-200` is not discontinued.

## Out of scope

Detailed mode (internal staff) output beyond what compact gets; MCP tool for n8n (the payload
key rides along; no new tool); the neighbour-suggestion probe (unchanged, still off for
dealers); any user-text matching.

## Addendum 1: every product domain (owner, 8 Oct 2026, after the answers)

Owner, verbatim: "btw for the alternative, need to apply for all domains ya, incoming,
promotion, delivery, product attachment all those scenairo also need to ahve this caapbility".
Crew: ONE shared resolver, not per-domain lookups; wording per domain goes to the owner as one
ask before those domains are coded. Availability continues as approved.

**Shared resolver.** Data: `product_alternative_service.alternatives_for(db, product_ids)` (the
same one the stock tool uses). Chat seam: `lanes/business/__init__.py::run_fetch`, the only
caller of `fetch.output_structurer` (between `brand_guard.guard_result` `:2223-2225` and
`output_structurer` `:2251`), which holds `db`, the envelope and the asked product entities
(`trigger["entities"]`, `:2109-2111`). For an asked product that is discontinued and has an
alternative, the resolver adds the alternative's id to the SAME tool call's `product_ids`
(`:2137-2201`), so the domain answers for it with its own logic and permissions, then moves the
alternative's items under the old product's item as `item["alternatives"]` (the key PR #1535's
discontinued answer already reads). `fetch._item_line` (`fetch.py:3130-3156`) prints them after
the marker. Non-stock domains show the line only when the asked code is DISCONTINUED (Q2's
"not a full Got stock" half is a stock verdict and has no meaning there).

| Domain | Tool -> service | Presenter / reply | Discontinued reaches reply today? | Proposed Alternative line |
| --- | --- | --- | --- | --- |
| Stock / availability (approved) | `crm_inventory_stock_balance_list` -> `inventory_service._apply_stock_visibility` | `presenters._stock_availability` `:1581`, `_stock_compact` `:1427`; `fetch._item_line` | yes (`:1700-1703`, `:1517`) | dealer: `Alternative: SRTBF11821-NEW x 5: ✅ Got stock.`; staff: `*Alternative:* SRTBF11821-NEW` + its block |
| Incoming / ETA | `crm_incoming_stock_list` -> `IncomingStockService.incoming_list` (`api/v1/incoming_stock.py:426-537`, `incoming_stock_service.py:702`) | dealer `_incoming_dealer` `presenters.py:963-984` (`<code>: ✅ ETA d1, d2` / `No ETA`); staff `_incoming_list` `:868-902` | dealer yes (`incoming_stock.py:147-180`); staff no (`incoming_stock_service.py:888-901`) | dealer: `Alternative: SRTBF11821-NEW: ✅ ETA 20/10/2026` (or `No ETA`); staff: `*Alternative:* SRTBF11821-NEW` + its incoming rows |
| Promotion | `crm_marketing_promotions_list` -> `MarketingService.list_promotions(product_ids)` (`marketing_service.py:510-530`, `:596`) | `_promotions` `presenters.py:1027-1043` (one item per promotion, no product on it); miss: `No promotions found for X.` (`turn/compose.py:603-605`) | no | one line per asked discontinued code after the list: `Alternative: SRTBF11821-NEW: <Promotion> (<start> to <end>)`, or `Alternative: SRTBF11821-NEW: No promotion.` |
| Delivery (order domain) | `crm_order_management_orders_list` -> `OrderService.list_orders` (`orders.py:304`); outstanding goes to `crm_outstanding_report` (`__init__.py:1871-1877`) | `_orders_list` `presenters.py:412-454` (one item per DO) | no (`schemas/order.py:323-327` has it, presenter drops it) | crew recommends NONE: a delivery history of the old code is what was delivered, and the replacement's DOs answer a different question. If wanted: `Alternative: SRTBF11821-NEW: <n> DOs, last delivered dd/mm/yyyy` |
| Product attachment / files | `crm_master_product_attachments_list` -> `ProductAttachmentService.list_product_attachments` (`product_service.py:2675`) | `_product_attachments` `presenters.py:1114-1157` (one item per file, also sends the file) | yes (`presenters.py:1150`) | `Alternative: SRTBF11821-NEW: <File Name>` and the alternative's file of the same type is sent too |
| Master product info | `crm_master_products_list` -> `ProductService.list_products` (`product_service.py:498`) | `_products` `presenters.py:1082-1108`, trimmed by `_project_product_specs` (`fetch.py:2851`) | yes (`presenters.py:1106`) | `*Alternative:* SRTBF11821-NEW` under the marker, plus the alternative's own block with the same asked fields |
| Discontinued ask (PR #1535, not on main) | master products | `_discontinued_status_lines` in #1535's `fetch.py` | yes | already prints `Alternative: <code>` from `item["alternatives"]`; the shared resolver feeds it, no new wording |

Not covered by the seam (stated, not built): `crm_outstanding_report` and the other text-returning
bypasses at `fetch.py:2721-2738`, and the cross-domain block's own render (`answer.py:991-1007`).

## Review round 1 decisions (8 Oct 2026)

- Reset to auto is a server-deferred record action `product_alternatives.reset_auto` on the
  product (entity type `product_alternatives`, destructive window), deleting row by row so
  the audit listeners record each link; the immediate `reset-auto` route is gone.
- The add route is session-only; an inactive product cannot be added (400); a racing
  duplicate add answers 409.
- An `Alternative:` line that refers marks the entry `refers_to_salesman` (Customer asks log).
- A nested alternative carries its ETA but never a packing list file.
- An alternative the dealer also asked for in its own right is not repeated under the old code.
- Compact: an alternative's own block is appended only when the caller named products, never
  on a catalogue page.
- The rule's per-company code probe is an expression index (`ix_products_company_norm_code`).
- The API update path cannot rename a product (`ProductUpdate` has no `product_code`), so
  the rule runs on create, AutoCount ingest (create and update), Excel import and the backfill.

## Round B built (9 Oct 2026)

Owner approved Addendum 1 ("okay can"); round A accepted on the copy (owner retest, 9 Oct).
Round A's hand test exposed a route-schema drop (`StockAvailabilityEntry` lacked
`alternatives`; fixed 813c4fa3, pinned by route-level tests). Round B:

- One shared resolver, `app/services/chatbot/lanes/business/alternatives.py`, called once from
  `lanes/business/__init__.py::run_fetch`: for each asked DISCONTINUED product with a stored
  alternative inside the contact's brand scope, the same tool is called once more per
  alternative (same seam, same brand guard, best-effort, traced) and folded in. Delivery,
  the outstanding report, counted sets and the cross-domain block are out.
- Owner, 9 Oct: an alternative whose search found nothing is still named with what was not
  found: `Alternative: X-NEW: No ETA` (dealer), `Alternative: X-NEW: No incoming stock.`
  (staff), `Alternative: X-NEW: no Product Photos.` (attachment type asked),
  `Alternative: X-NEW: No promotion.`. On a miss for the old code the lines ride the fetch
  (`alternative_lines`) and `answer_bridge.answer_for` prints them before any escalate offer.
- DISCONTINUED-ASK (#1535): "is X discontinued?" names the alternative from the item's
  `alternatives`; a status-only ask makes no extra call.
- Round B endpoints: the resolver reads only fields the incoming, promotion, attachment and
  product endpoints already return; it adds no key to any route payload, so the round A
  schema drop cannot recur there (the e2e tests go through the real endpoints).
