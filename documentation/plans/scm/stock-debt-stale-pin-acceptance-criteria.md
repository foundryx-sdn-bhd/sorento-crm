# UAC: Stock Debt stale pins never count as cover (STOCK-DEBT-STALE-PIN)

Plan: `PLAN-stock-debt-stale-pin.md`. Tests: `sorento_crm_backend/tests/scm/test_stock_debt_stale_pin.py`,
`test_stock_debt_routes.py::_assert_pinned_on_the_bin`, `StockDebtCellDialog.test.tsx`.

- AC-1 (CB6633-NL): a line pinned 126 at a bin holding 0 free (0/0, or on hand all reserved)
  reads Short 126, Covered by empty, and its month owes 126.
- AC-2: pinned 126 with 50 free reads covered 50 (On hand <bin> 50), short 76. SRTSH1040
  shape: pinned 150 with 2 free reads covered 2, short 148.
- AC-3: pinned 126 with 200 free still reads Pinned, covered 126, short 0.
- AC-4 (CSH2073): two pins (8 and 2) at one empty bin both read short.
- AC-5: a pin at a bin outside the screen's span covers at most that bin's free stock
  (0 -> nothing, 10 -> 10, 500 -> the whole 40).
- AC-6: two pins at one out-of-span bin holding 5 share it, the one confirmed first served
  first, whatever order the rows were written in.
- AC-7: the Supply tab of a line's column lists the bin it is pinned on, Free 0 there,
  assigned to that line; today's column keeps the free part.
- AC-8: a pin at an out-of-span bin is listed in its line's column for the part it holds.
- AC-9: the Fulfilment Planning board's shared assignment gives the same capped cover.
- AC-10: the Supply empty state never says "No stock is held" beside a demand row showing
  cover; it says "What covers these lines is listed in an earlier column."
- AC-11: pinned 126 at a bin holding 50, with an SPO of 76 whose S/O names the line: covered
  126 (on hand 50 + SPO 76), short 0. At a bin holding 0: SPO 76, short 50.
- AC-12: received-SPO placements on one thin bin outside the span share it, the placement
  made first served first, whatever order the rows were written in.
- AC-13 (round 3): a pin `assign()` does not serve (its line is already covered by an earlier
  pin) leaves the bin to the next pin, in the span and outside it.
