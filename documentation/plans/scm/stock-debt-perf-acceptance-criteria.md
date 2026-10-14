# UAC: Stock Debt first load reads landed pins in one batch (STOCK-DEBT-PERF)

Plan: `PLAN-stock-debt-perf.md`. Tests: `sorento_crm_backend/tests/scm/test_stock_debt_landed_batch.py`.

- AC-1: the landed-pin read sends the same statements for one product as for four, at most two
  (`test_the_landed_read_does_not_grow_with_the_product_count`; main sends two per product).
- AC-2: every landed hold equals what the single-key read says landed for its product: receipts
  across two SPOs summed (30 + 15, less 5 the line already holds, reads 40), a landing capped at
  the line's open quantity (25 of 10 reads 10), a `source_ref` shared by two products' lines
  credits each product only its own purchase (P1 40, P2 7), an SPO row of another product naming
  P1's PO line counts for nobody, an order-inquiry placement lands min(link 25, received 20) = 20,
  and a line with nothing landed pins nothing
  (`test_landed_holds_match_the_single_key_read_for_every_product`).
- AC-3: on the shared dev DB, `StockDebtService.list` output hashes the same on main and on the
  branch for the default request, `only_debt=false&page=3&limit=50`, `projected=true`,
  `book=project` and `book=retail`.
- AC-4: on the shared dev DB the default first-load request runs under 5 s (4.45 s cold, 3.80 s
  warm, from 12.07 s / 11.91 s).
