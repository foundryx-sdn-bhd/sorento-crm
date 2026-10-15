# PLAN: Stock Debt first load reads landed pins in one batch (STOCK-DEBT-PERF)

Status: in review, PR #1696. Track: small fix (speed only, no behaviour change, no migration,
no auth/RBAC, no new ingest, no UI change). UAC: `stock-debt-perf-acceptance-criteria.md`.
Plan created: 2026-10-14T17:30:25Z.

Crew audit of the #1683 test copy saw the Stock Debt page take about 39 s on first load. Measured
on the shared dev DB (read-only) on origin/main a92baeb87, the default request
(`page=1&limit=25&only_debt=true&book=all`) took 12.07 s cold / 11.91 s warm in process and sent
1,288 SQL statements, so the cost is main's own, not #1683's.

Seam: `StockDebtService._landed_holds` (`app/services/scm/stock_debt_service.py`) called
`ProjectSupplyService._po_received_by_so_line_ref` once per product, two reads each (the SPO
receipt join and the order-inquiry placement join): 629 x 2 = 1,258 statements, ~7 s.

Fix: `ProjectSupplyService._po_received_by_product` runs the same two joins once over every
product and keeps each row only for its own product and the refs asked for that product, so each
product's answer equals the single-key read with `company_id=None`. `_landed_holds` collects its
candidates, reads once, then builds the same holds in the same order. The single-key read stays
for its other callers. Nothing else in the SCM read changes (owner rule: SCM fixes minimal).

Result on the dev DB: 4.45 s cold / 3.80 s warm, 32 statements, `list()` output byte-identical to
main for five request shapes (see the UAC).
