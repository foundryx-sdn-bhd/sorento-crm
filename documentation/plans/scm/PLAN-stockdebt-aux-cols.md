# PLAN: Stock Debt auxiliary columns + toggle + Excel (STOCKDEBT-AUX-COLS)

Status: building; size M, crew lane (LEAD pattern); owner answered the card 4 Oct 2026 (see UAC header).
UAC: `stockdebt-aux-cols-acceptance-criteria.md`.

## Ask (owner, 4 Oct 2026)

Four columns between Product and the months: BRW On Hand Qty, BRW PO Qty, BRW Incoming Qty,
Dealer O/standing Qty. A show/hide switch. Also in the Excel export. Reuse existing
calculations. "BRW" = all site pool. Dealer outstanding only up to the page's end date.

## Design (simplest thing that works)

No new arithmetic. `StockDebtService.list()` (`app/services/scm/stock_debt_service.py:317`)
already has every product id of the filtered set; after the rows are built it calls, once
each over that id list:

| Field | Existing function |
|---|---|
| `pool_on_hand` | `summary_order_service._pool_on_hand_map` (`summary_order_service.py:885`) |
| `pool_po_qty` | `site_pool_supply.open_po_by_product` (`site_pool_supply.py:69`) |
| `pool_incoming_qty` | `site_pool_supply.open_spo_by_product` (`site_pool_supply.py:104`) |
| `dealer_outstanding` | MINUS `summary_order_service._demand_aggregates(horizon_start=date_from, horizon=date_to)["dealer_qty"]` (`summary_order_service.py:1075`); negated once in `list()` so screen, totals and Excel share the sign (Q6) |

Called over the FILTERED rows only (after `only_debt` + supplier filtering), so the cost is
four batch queries per request. `_totals` (`stock_debt_service.py:1994`) sums the four.
Schema: four `float = 0.0` fields on `StockDebtRow` and `StockDebtTotals`
(`app/schemas/stock_debt.py:52,84`); `show_aux: bool = False` on `StockDebtExportIn`
(`stock_debt.py:295`), threaded through the route (`api/v1/projects/stock_debt.py:168`),
the task (`tasks/export_tasks.py` `generate_stock_debt_xlsx`) and `export()` /
`_render_workbook` / `_export_row` / `_export_total_row` (`stock_debt_service.py:2049-2181`).

The two private `summary_order_service` helpers are imported as-is (no move, no rename):
moving them is churn the ask does not need.

FE (`app/(protected)/project-sales/stock-debt/`): types gain the four fields; a
`showAux` state (default true, localStorage `stock-debt.show-aux`, try/catch) drives four
columns in `StockDebtClient.tsx` (inserted after Product, before months, `size` 112,
`text-end`) and a Switch under "Only products in debt"; footer totals render the four
sums; `StockDebtExportPopover` passes `showAux` to `exportStockDebt` as `show_aux`.

## Tests (tester writes red first)

BE `tests/test_stock_debt_aux_columns.py` (Postgres fixture, seeds own chain): AC-2..AC-7,
AC-10..AC-12 (pool vs project bin vs counts_as_available=false stock; open PO at pool vs
project bin; SPO open vs received; retail lines before start / inside / after end / no
date, project line ignored; totals over whole set with limit=1; workbook header + Total row
with show_aux true/false).
FE vitest: columns present/absent by switch, persisted value restored, export sends
`show_aux`, footer sums.

## Out of scope

Cell drill for aux values; per-user server-side preference (named trigger: owner asks for it
across devices).
