# UAC: mirror / shelf -> cabinet links (MIRROR-LINKS)

Plan: `PLAN-mirror-shelf-links-14oct.md`.

1. After migration every existing `product_compatibility` row has `kind = 'seat_cover'`; a row
   with any other kind than seat_cover / mirror / shelf is refused by the database.
2. The same accessory, host and kind cannot be linked twice; the same pair under a different
   kind can.
3. `link_mirror_shelf_flyer.py` with no flag prints every row as LINK or MISSING and writes
   nothing. SRTMR11417 and SRTSV11417 are reported MISSING, not a failure.
4. `--apply` writes each found row once, source `flyer`, with the row's kind; a second run
   writes 0 and reports them as already linked.
5. On SRTBF11404's product page (Variants tab, Compatible section): "Mirrors that fit" lists
   SRTMR11404-BL, "Shelves that fit" lists SRTSV11404-BL, both badged Flyer.
6. On SRTMR11404-BL's page: "Fits these cabinets" lists the 7 cabinets SRTBF11404, SRTBF11405,
   SRTBF11412, SRTBF11413-MBL, SRTBF11501, SRTBF11705, SRTBF11707.
7. A seat cover / WC product page reads as before ("Seat covers that fit", "Fits these WCs").
8. Add compatible on a cabinet: pick link type Mirror, side Mirror, a product, Add: it appears
   under "Mirrors that fit" badged Manual. Unlink still runs as the deferred countdown.
9. The chatbot's seat cover answer for a WC never includes a mirror or shelf link.
10. Usable at 375px and 1280px.
