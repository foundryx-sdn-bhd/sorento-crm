# WC-SEAT-COMPAT browser evidence (12 Oct 2026)

Cloud sandbox stack: backend on the bootstrapped empty schema plus a sandbox-only seed of
flyer-shaped products (invented SKU codes, no dev or prod data), frontend `npm run dev`,
agent-browser 0.27.0 headless, navigation by sidebar from `/`.

Run:
1. Sidebar Products > All Products > Actions > Seat cover flyer: 35 printed models listed in
   flyer order; SRTWC8383 shows its 5 printed codes, 6 ready SKUs, 2 unresolved (MK finish,
   FT unknown part) with no tick box; SRTWC8066 plain `-S` SKU unresolved (printed only with a
   colour); models with no SKU and missing covers shown, not tickable. (`review-1280.png`)
2. Ticked model SRTWC8383 + SKU SRTWC8122, "Link 7 SKUs": 8 links written, all `flyer`, 8122
   has both 333-UF and 333A-UF, no link on any unresolved SKU (checked in the DB).
3. Retire spec turned enabled after the apply.
4. Product SRTWC8383-RL-MBL > Variants: Compatible (1), SRTSCBD823-MBL with a Flyer badge;
   "Fits these WCs" empty state. (`section-1280.png`)
5. Add compatible: role radio defaulted to Seat cover, picked SRTSCBD407, added with a Manual
   badge; Unlink showed "Unlinking in 3s" with Cancel and then removed it.
6. 375px: section and review page both render without sideways scroll (scrollWidth 360).
   (`section-375.png`, `review-375.png`)
7. Retire spec: "Retiring in 8s" countdown, then the card shows Retired; registry key
   inactive, 0 products hold a value, one audit row `seat_cover_spec.retire` with the removed
   value.
