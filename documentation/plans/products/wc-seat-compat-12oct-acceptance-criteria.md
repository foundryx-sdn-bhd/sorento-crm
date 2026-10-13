# UAC: WC to seat cover compatibility (WC-SEAT-COMPAT)

Plan: `PLAN-wc-seat-compat-12oct.md`. Owner rulings 12 Oct are binding.

## A. Links

- A-1 A link joins one seat cover SKU to one WC SKU in the same company. No model-wide link.
- A-2 A WC SKU shows a cover only when a link names it. A sibling SKU of the same model with no
  link of its own shows nothing.
- A-3 A product cannot be linked to itself; a pair cannot be linked twice (either direction).
- A-4 A `-SC` SKU can be linked as a seat cover and never as a WC.
- A-5 Links never cross companies; another company's product or link answers 404.

## B. One-off flyer script (`scripts/oneoff/link_seat_cover_flyer.py`)

- B-1 It carries the 49 printed pairs as data; the 7 intelligent-seat WCs are not in it.
- B-2 Dry run (the default) prints every pair it would link (row, printed code, cover, SKU),
  every UNRESOLVED SKU with its reason, and every MISSING pair, and writes nothing.
- B-3 A colour SKU links only through the printed code carrying its colour
  (`SRTWC8383-RL-MBL` -> `SRTSCBD823-MBL`); MBL never matches BL. SRTWC8122 gets both covers.
  A finish printed as a row note (rows 22, 23) counts as printed for that row.
- B-4 A SKU that is not unambiguous (finish not printed, sub-model not printed, unknown
  segment, ambiguous, grouped by hand) is UNRESOLVED and never linked.
- B-5 `-SC` SKUs are never linked as WCs and never listed.
- B-6 `--apply` writes source `flyer` links in each WC's own company; a second run writes
  nothing; an existing manual link is kept as it is.

## C. Old Seat Cover spec

- C-1 `--retire-spec` runs only with `--apply`. It removes every `seat_cover` value, sets the
  key inactive, clears its rules and writes one audit row with the removed values.
- C-2 It is refused, writing nothing, while any company holding a value has no flyer link.

## D. Product page

- D-1 The product page's Variants tab shows a Compatible section directly under Alternatives, on every product. It is the only UI; there is no flyer page.
- D-2 It lists "Seat covers that fit" (this product as a WC) and "Fits these WCs" (this product
  as a seat cover), each row linking to the product, with a Flyer / Manual source badge.
- D-3 With no links it shows an empty state (heading + hint, no button).
- D-4 Staff with products edit add a link: pick the other product and whether it is a seat cover
  or a WC. Errors (duplicate, -SC as WC) show as a toast.
- D-5 Unlink is the reversible countdown (no confirm dialog); Escape does not cancel it.
- D-6 Usable at 375px and 1280px.
