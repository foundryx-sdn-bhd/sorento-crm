# UAC: product ref collision (code only, owner rulings 1 + 2, 3 Oct)

Ruling 1: "when we match product, it is by product code, we don't really care about the source ref."
Ruling 2: "we shouldn't backfill the source ref for products from orders; source ref on products
doesn't really matter because the item code IS the ref ... why can't I just clear the source refs on
products?"

Product identity = company + normalised product code, everywhere. Product rows in
`integration_references` are never read for matching and never written by ingest.

## Product feed
AC-1 (prod case) Mocha holds product `2001`; product `MKT4524SS-DIY` holds ref `AED_V2_MOCHA:2001`.
Push ItemCode `2001`: product `2001` updated; `MKT4524SS-DIY` unchanged (code and every column); no
failure. Same in dry run.

AC-2 (silent rename) No product `2004`; product X holds `AED_V2_MOCHA:2004`. Push ItemCode `2004`
(with a valid category): product `2004` created; X unchanged; no failure.

AC-3 The feed writes NO product row to `integration_references` on create, on update, or on adopt
(count of entity_type='products' refs unchanged by any push).

AC-4 An existing product found by code is updated whatever ref it holds (or none); its stored ref
row is left as it is (not deleted by ingest, not rewritten).

## Document lines (SO, PO, SPO, billing; DO/GRN already code only)
AC-5 Line code owned by product B binds B, whatever the line's product_ref names (including a ref
held by another product A). A untouched. No failure.

AC-6 Line code owned by no product: the existing unknown-product path of that document type, never
the ref: SO/PO line dropped (counted in `lines.dropped`, D9), SPO record retryable with
`lines.N.product_code`, billing line `product_unresolved` warning + NULL product.

AC-7 Line with no product code: same unknown-product path as AC-6 (the ref is not used).

AC-8 Line ingest writes NO product row to `integration_references` (count unchanged after SO, PO,
SPO and billing pushes, including when the bound product held no ref before).

## Both
AC-9 Code comparison is case- and edge-whitespace-insensitive (existing code-lookup normalisation).

AC-10 Same batch: two feed records whose refs point at other products both obey AC-1/AC-2.

AC-11 With every product ref deleted (the post-deploy cleanup), feed and line pushes behave
identically to AC-1..AC-10.

## Product deletions (`POST /external/ingest/products/deletions`)
AC-12 A product deletion resolves by the record's code (the `codes` map shared service sends for
products, `codes_from_refs`), never by the ref. Prod shape: deleting ItemCode `2001` in Mocha removes
or deactivates product `2001`, never `MKT4524SS-DIY` (which holds ref `AED_V2_MOCHA:2001`). A product
deletion with no code is `not_found`.
