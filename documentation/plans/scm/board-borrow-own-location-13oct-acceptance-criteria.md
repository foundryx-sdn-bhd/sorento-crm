# UAC: Fulfilment Planning board, borrow from the line's own location (BORROW-OWN-LOC)

Plan: `PLAN-board-borrow-own-location-13oct.md`. Owner rule, 13 Oct 2026.

## Offer

- AC-1. On a board line with a fulfilment location, "Add a borrow" always lists that own
  location, in both "Borrow from another order" states, whatever holds the stock there and even
  with nothing there. Test: `test_the_board_offers_the_lines_own_location_under_borrow[False/True]`,
  `test_the_own_location_is_offered_even_with_nothing_there_and_nowhere_else`.
- AC-2. The own-location row is a plain location row (names no donor order or project), comes
  last, and is never the pre-selected (recommended) row while any ranked donor exists. Same tests,
  plus `test_borrow_donor_ranking.py`.
- AC-3. The own-location row offers 0 free: the line's "borrow available" figure does not count
  the free units Reserve already shows. Test: `test_the_board_offers_the_lines_own_location_under_borrow`.
- AC-4. The Buy sentence never says borrowing is possible from a row with nothing free.
  Test: `test_a_buy_the_board_cannot_avoid_still_says_borrowing_is_possible`.
- AC-5. The bulk Decide "Borrow" picker does not offer the own location's 0-free row.
  Test: `BoardDecideControl.test.tsx` (BORROW-OWN-LOC S5).

## Confirm

- AC-6. A borrow at the own location confirms; the reason stays mandatory.
  Tests: `test_confirm_an_own_location_borrow_with_free_stock_holds_it`,
  `test_an_own_location_borrow_still_needs_a_reason`.
- AC-7. Reserve and Borrow at the same own location never hold more than is free there: the
  Borrow is held only from what this line's Reserve leaves; the rest confirms as decided and is
  recorded short ("Borrow 2 at X confirmed as decided; only 0 free at X when confirmed, 2 short").
  Test: `test_confirm_reserve_plus_an_over_asked_own_location_borrow_passes_short[False/True]`.
- AC-8. Re-confirming the same composition never holds the units twice.
  Test: `test_re_confirming_the_same_reserve_and_own_borrow_never_holds_more_than_on_hand`.
- AC-9. With no stock at all, Confirm still passes, recorded short.
  Test: `test_confirm_an_own_location_borrow_with_no_stock_at_all_still_passes`.
- AC-10. With the switch off, an own-location borrow raises the same order-back on the borrowing
  line as every other location borrow (crew ruling (a), 13 Oct).
- AC-11. A plain borrow at the line's site pool is still refused ("inside this line's Reserve
  pool"). Test: `test_a_plain_borrow_at_the_lines_site_pool_is_still_refused`.

## Dialog

- AC-12. In a merged bin (switch off), a typed quantity beyond the bin's free rides on the plain
  location row, never on a donor line's row. Test: `BorrowAddDialog.test.tsx` (review B2).
- AC-13. The panel reads "Own N X · Borrow M X"; the summary cards count Own and Borrow apart.
  Test: `boardAmend.test.ts`; browser pass 1280 and 375.
