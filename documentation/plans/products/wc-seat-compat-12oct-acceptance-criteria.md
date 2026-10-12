# UAC: WC to seat cover compatibility (WC-SEAT-COMPAT)

Plan: `PLAN-wc-seat-compat-12oct.md`. Owner rulings 12 Oct are binding.

## A. Links

- A-1 A link joins one seat cover SKU to one WC SKU in the same company. No model-wide link.
- A-2 A WC SKU shows a cover only when a link names it. A sibling SKU of the same model with no
  link of its own shows nothing.
- A-3 A product cannot be linked to itself; a pair cannot be linked twice (either direction).
- A-4 A `-SC` SKU can be linked as a seat cover and never as a WC.
- A-5 Links never cross companies; another company's product or link answers 404.

## B. Flyer review (owner only applies)

- B-1 The review lists every printed WC model once, with its printed codes in flyer order and
  each printed cover marked found or missing. 49 printed pairs in total.
- B-2 Each SKU of a printed model shows the printed code it maps to, its cover(s) and its extra
  code segments. `-SC` SKUs are not listed.
- B-3 A colour SKU maps only to the printed code carrying its colour (`SRTWC8383-RL-MBL` ->
  `SRTWC8383-MBL` -> `SRTSCBD823-MBL`); MBL never matches BL.
- B-4 A SKU that is not unambiguous is UNRESOLVED with its reason (finish not printed, sub-model
  not printed, unknown segment, ambiguous) and cannot be ticked or applied.
- B-5 SRTWC202 (Pink) and SRTWC203 (Blue) resolve through the row's printed note.
- B-6 A SKU whose printed cover is missing from the catalogue cannot be ticked.
- B-7 Apply writes links (source `flyer`) for the ticked SKUs only; SRTWC8122 gets both covers.
  Re-applying writes nothing new. An id that is not a printed model's ready SKU is skipped with
  its reason.
- B-8 Reading needs `master_data.products.view`; applying needs `master_data.products.edit`.

## C. Old Seat Cover spec

- C-1 The review shows the old spec: active or not, and how many products hold a value.
- C-2 Retire is disabled until at least one flyer link exists; the server refuses it (409) too.
- C-3 Retire is a server-deferred destructive countdown, gated on
  `master_data.spec_registry.edit`. When it lapses, every `seat_cover` value is removed, the key
  is set inactive, and one audit row records the removed values.

## D. Product page

- D-1 The Variants tab shows a Compatible section under Alternatives on every product.
- D-2 It lists "Seat covers that fit" (this product as a WC) and "Fits these WCs" (this product
  as a seat cover), each row linking to the product, with a Flyer / Manual source badge.
- D-3 With no links it shows an empty state (heading + hint, no button).
- D-4 Staff with products edit add a link: pick the other product and whether it is a seat cover
  or a WC. Errors (duplicate, -SC as WC) show as a toast.
- D-5 Unlink is the reversible countdown (no confirm dialog); Escape does not cancel it.
- D-6 Usable at 375px and 1280px.
