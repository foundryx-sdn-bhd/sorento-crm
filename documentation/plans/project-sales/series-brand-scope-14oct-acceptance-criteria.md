# UAC - Series brand scope (SERIES-BRAND-SCOPE)

**Status:** card answered 14 Oct 2026 (Q1-Q5 all (a)); (a)(b)(c) in build. Scope (d) sets/families pending its own card addendum.
**Plan:** `PLAN-series-brand-scope-14oct.md`

## Journey

The Project Sales admin opens Project Sales > Configuration > Series > a series (e.g. BRAVAT,
brand BRAVAT, categories BRT-WB/WC/JC/BA/SH/FT). They pick the brand, then the categories that
brand actually sells in, and the Products table below fills with every active product of that
brand in those categories, prices blank, ready to type. Products of other brands that happen to
sit in the same categories are not part of the standard range any more.

## Acceptance criteria

### A - Categories picker
- **A1** With a brand chosen, the Categories picker lists only categories holding at least one
  ACTIVE product of that brand, plus every parent of such a category. Nothing else.
- **A2** A category whose only products of the brand are inactive is not listed.
- **A3** No 100 cap: every qualifying category is listed (with or without brand).
- **A4** With no brand chosen, the picker lists every active category (today's list, uncapped).
- **A5** A category already selected on the series that no longer qualifies stays visible as a
  selected chip with its name (never a UUID, never a blank chip).
- **A6** Changing the brand in the form refetches the picker for the new brand.

### B - Brand restricts "standard"
- **B1** Series with a brand: a product counts as standard if it has its own row in the series
  Products table (any brand), OR it sits in a series category (incl. sub-categories) AND its
  brand equals the series brand.
- **B2** A product of another brand, or with no brand, sitting in a series category and with no
  row of its own is NON-standard (quotation line badge + live check endpoint).
- **B3** Series with no brand: category membership ignores brand (today's behaviour).
- **B4** A named (row) product of another brand stays standard (Q2 a).
- **B5** Floors unchanged: a series floor applies only to a product row with both price and
  discount; otherwise Price Floors apply. A preloaded (blank) row behaves exactly like no row.
- **B6** Off-catalog lines stay non-standard whenever a series is chosen (unchanged).
- **B7** Existing quotation lines keep their stored badge until the line/version is next saved
  (Q5 a): no backfill.

### C - Products table preloaded
- **C1** Series with brand + categories: the Products table lists every ACTIVE product of the
  brand in those categories (incl. sub-categories) with blank Selling price, Max discount and
  Floor, plus every stored row (sheet / hand-added / other brands) with its own numbers. Each
  product appears once; ordered by product code.
- **C2** Inactive products are never preloaded; a stored row for an inactive product still shows.
- **C3** Typing a price and/or discount on a preloaded row saves it (the row becomes stored) and
  the Floor shows when both are set.
- **C4** Removing a category (or changing/clearing the brand) and saving: the untouched preloaded
  rows of products that left disappear; stored rows with a price or discount stay (Q1 a). No
  confirm dialog.
- **C5** A product of the brand added to the catalogue later (active, in a series category)
  appears in the table on the next load, no button (Q4 a).
- **C6** Rows of the brand inside the series categories show no Remove (bin) action (Q3 a);
  rows outside that scope (other brand, other category) keep Remove.
- **C7** Series with no brand, or a brand but no categories: the table shows stored rows only
  (today's behaviour).
- **C8** Sheet import (Add / Replace) and hand-added rows work as today on top of the preload.
  After Replace, in-scope products not in the sheet show again as blank preloaded rows.
- **C9** On create: after "Create series" with brand + categories, the page lands on the series
  and the table already shows the preloaded rows. On edit: after "Save changes", the table
  refreshes to the new scope without a page reload.
- **C10** The table stays usable at the BRAVAT size (~926 rows): scrolling, search and typing a
  price without visible lag at 1280 and 375. If not, it is paged or virtualised in this PR.

### D - Sheet import resolves sets and families (addendum, crew answers 14 Oct: Q1 a, Q3 a; Q2 (c) built here, set-as-one-quotation-line is its own L lane)
- **D1** Each sheet code resolves in a fixed order, first hit wins, never fuzzy: (1) exact product
  code, ignoring spaces and dashes (today's rule); (2) product SET code (`product_sets.set_code`,
  active, same normalisation) -> the set's member products; (3) FAMILY: every active product
  whose code is the sheet code followed by `-` and more (`SRTWC286` -> `SRTWC286-SH`,
  `SRTWC286-SH-150`, ...; never `SRTWC2861`), plus every active set whose code is the sheet code
  followed by `-` and more, each expanded to its members.
- **D2** Family members (variant products) each get their own series row and the sheet row's
  price/discount fills each of them (Q1 a). Re-import updates them all.
- **D3** Set members get series rows (standard) but NO price or discount is written on them, even
  when the sheet row states one (Q2 c): the price belongs to the set.
- **D4** The load report lists, per code that did not match exactly, what it resolved to: "set"
  with its parts, or "family" with every product/set it fanned out to (Q3 a). Codes that resolve
  to nothing stay in the "not in the catalogue" list.
- **D5** A code with an exact product match never fans out (exact wins), and a set code never
  fans out to a family.
- **D6** Rows written by the import are named rows: standard regardless of brand (B1/B4).
- **D7** Company scope: another company's products/sets never resolve.
