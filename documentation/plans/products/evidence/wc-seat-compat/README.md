# WC-SEAT-COMPAT browser evidence

Scope after the owner's decision of 12 Oct: the flyer is linked once by
`sorento_crm_backend/scripts/oneoff/link_seat_cover_flyer.py`, and the only UI is the
Compatible section on the product page (Variants tab, under Alternatives). Screenshots of the
removed flyer review page were deleted with it.

Cloud sandbox stack: backend on the bootstrapped empty schema plus a sandbox-only seed of
flyer-shaped products (invented SKU codes, no dev or prod data), frontend `npm run dev`,
agent-browser 0.27.0 headless, navigation by sidebar from `/`.

- `section-1280.png`, `section-375.png`: the Compatible section (first pass, 12 Oct).
- `c75a3ee80/r2-section-375.png`, `c75a3ee80/r2-sc-refused.png`: section at 375px and the
  `-SC` refusal toast (re-run on c75a3ee80).
- `867b61475/`: final-head pass (section at 375px, `-SC` refusal); the PASS/FAIL table is in the PR's crew-note.
