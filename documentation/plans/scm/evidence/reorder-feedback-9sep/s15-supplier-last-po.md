# S15 (Supplier column = last PO supplier) verification evidence

Lane: `feat/order-sheet-paper-s14` branch on the worktree (`feat/reorder-feedback-9sep`
lineage), stack :3083/:8083. S15 code is UNCOMMITTED in this worktree
(`app/services/scm/summary_order_service.py`, `tests/scm/test_supplier_last_po_s15.py`,
`scripts/backfill_product_supplier_from_last_po.py`) - PR #792 does not yet include it.

DB: `sorento_ai_automation_0907` (prod copy, per lane `.env`).

## 0. Environment note - frontend node_modules had to be reinstalled

This worktree's `sorento_crm_frontend/node_modules` was a symlink to the primary
checkout's `node_modules` (`.../sorento_crm/sorento_crm_frontend/node_modules`).
`next dev --turbopack` refused to start: `TurbopackInternalError: Symlink node_modules is
invalid, it points out of the filesystem root`. Other worktrees on the machine
(`supplier-docs-pi-first`) have a REAL `node_modules` directory, not a symlink, and start
fine - this lane's symlink setup was the outlier. Fixed by `rm node_modules && npm install
--force` in this worktree's `sorento_crm_frontend/` (1220 packages, 13s); `next dev
--turbopack -p 3083` then started clean ("Ready in 2.5s"). This is an environment/tooling
fix, not a code change - nothing under version control touched.

## 1. Backend pytest - PASS

`venv/bin/python -m pytest tests/scm/test_supplier_last_po_s15.py -q` -> **9 passed**
(Postgres, `sorento_ai_automation_0907`). Covers AC-S15.1 through AC-S15.5 against a
seeded chain, and AC-S15.6 (backfill script) against an empty scratch schema per the UAC's
own instruction (AC-S15.7).

`venv/bin/python -m pytest tests/scm/test_order_summary_sheet.py -q` -> **28 passed**, no
regression from the S15 supplier-map swap.

`venv/bin/alembic heads` -> single head `504_order_summary_pool_cols` (S15 added no
migration - service-level change only, matches the plan).

## 2. Stack booted

- Backend: `venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8083` (existing
  `.env`, DB `sorento_ai_automation_0907`). Healthy: `GET /health` -> `{"status":"healthy"}`.
- Frontend: `npm run dev -- -p 3083` in `sorento_crm_frontend/` (after the node_modules
  fix above). `Ready in 2.5s`, `GET /` -> 200.
- Worker not needed - the order-summary export is synchronous (confirmed in S14 evidence
  and reconfirmed here: both xlsx/pdf exports return 200 directly from the request, no RQ
  queue involved).

## 3. Fresh plan run (write_rows freezes Supplier at run time - needed CURRENT code)

The newest existing completed run, `c2251631-...` (used by S14 round 2), was frozen
**before** this S15 code existed - confirmed by querying its `scm.order_summary_row`
directly: **374/374 rows read `supplier_name = 'DEFAULT'`**, the pre-S15 bug. Verifying
S15 required a plan started AFTER the current (uncommitted) service code was loaded by
the running backend, per the brief's own instruction ("regenerate / refresh the sheet").

Navigated via sidebar from `/`: `/` -> Procurement -> nested "Supply Chain" -> "Reorder
Planning" -> `/scm/reorder` -> "Start Plan" button -> defaults (all warehouses, all
products, no dates) -> "Start Plan" -> landed on
`/scm/reorder/1170bcc5-4f8b-4a87-85b2-d87d437f96ba`. Polled the API - `status: "completed"`
within seconds. `write_rows` ran as part of run completion and froze
`scm.order_summary_row` with the current S15 `_last_po_supplier_map` logic.

## 4. AC-S15.1 - no row reads "DEFAULT" - PASS

DB query on the fresh run's frozen rows:

```
 total_rows | default_rows | blank_rows | distinct_suppliers
------------+--------------+------------+--------------------
        374 |            0 |         48 |                 45
```

**0 of 374 rows read DEFAULT** (before S15, this was 374/374). First 10 rows
(alphabetic by product code), from the downloaded xlsx (`Order sheet Excel`, same values
as the frozen DB row):

| Item code | Supplier |
| --- | --- |
| \*\*REPLACE | (blank - no PO history) |
| \*\*SPARE PART | (blank - no PO history) |
| ACC-CB9001 | ZHONGSHAN HONOR HARDWARE PRODUCT CO., LTD |
| ACC-SRT1015 | (blank - no PO history) |
| ACC-SRT1021 | KAILU SANITARY WARE MANAGEMENT DEPARTMENT |
| ACC-SRT2001 | KAIPING KAIXIN SANITARY CO., LTD. |
| ACC-SRT2006 | AFANNI FAUCET WARE |
| BRBC22332W-ENG | (blank - no PO history) |
| BRC21120XUW-3BA-ENG | BRAVAT (CHINA) GMBH - USD |
| BRC21172UW-ENG | (blank - no PO history) |

The only DEFAULT in the whole export is CB2154-DIY, which is the buyer's explicit chosen
override set in step 6 below - not the engine's reading.

## 5. AC-S15.2 - SRTWT8241-GY + 2 cross-checks against SQL - PASS

SQL (`purchase_order_lines` join `purchase_orders` join `suppliers`, `issue_date DESC NULLS
LAST, created_at DESC`):

| Product | SQL last-PO supplier | issue_date | Sheet Supplier (fresh run) |
| --- | --- | --- | --- |
| SRTWT8241-GY | KAILU SANITARY WARE MANAGEMENT DEPARTMENT | 2024-01-23 | KAILU SANITARY WARE MANAGEMENT DEPARTMENT - MATCH |
| ACC-SRT2001 | KAIPING KAIXIN SANITARY CO., LTD. | 2024-04-08 | KAIPING KAIXIN SANITARY CO., LTD. - MATCH |
| BRC21120XUW-3BA-ENG | BRAVAT (CHINA) GMBH - USD | 2023-07-24 | BRAVAT (CHINA) GMBH - USD - MATCH |

SRTWT8241-GY has 2 PO lines, from two different suppliers (KAILU SANITARY WARE
MANAGEMENT DEPARTMENT 2024-01-23, and KAILU HARDWARE FACTORY 2020-10-29) - the sheet
correctly reads the NEWER one, confirming the "last PO" ordering, not just "the only PO".

## 6. AC-S15.3 - explicit chosen supplier wins - PASS

Recorded a decision via `POST /api/v1/scm/order-summary/CB2154-DIY/decision` with
`supplier_code: "DEFAULT"` on the fresh run (CB2154-DIY's own last-PO supplier is XIAMEN
TAIYANG TECHNOLOGY CO.,LTD, confirmed via SQL and the pre-decision sheet reading).
Deliberately chose DEFAULT to prove the override beats the engine's reading even when the
override happens to be the placeholder the whole slice exists to stop defaulting to.

Re-exported the xlsx after recording the decision:

| Product | Supplier column (after decision) |
| --- | --- |
| CB2154-DIY | **DEFAULT** (the buyer's chosen override, not XIAMEN TAIYANG) |

Confirmed in code: `_serialise_row` (`summary_order_service.py:1456-1459`) reads
`(supplier.supplier_name if supplier else None) or row.supplier_name` - `supplier` is the
`chosen_supplier_id` resolved record, `row.supplier_name` is the frozen last-PO reading.
`export_report` -> `report()` -> `_serialise_row` is the same function for both the API and
the xlsx/pdf export, so there is one code path, not two that could drift.

## 7. AC-S15.4 - NULL issue_date sorts last - NOT LIVE-VERIFIABLE (covered by pytest only)

Queried the prod-copy DB for any `purchase_orders.issue_date IS NULL` row with a line -
**0 rows**. No real data exists on this DB to exercise the NULL-sorts-last path live.
This AC is verified by `tests/scm/test_supplier_last_po_s15.py` (part of the 9 passing
tests, seeded chain includes a NULL-`issue_date` PO per the UAC's AC-S15.7 instruction) -
not independently re-verified here beyond confirming that pytest run is green.

## 8. AC-S15.5 - Remarks MOQ reads the SAME last-PO supplier's link - PASS

Found on the fresh run: SRTSS8710 is the only row with a non-null MOQ.

SQL - SRTSS8710 has TWO `product_suppliers` links:

| Supplier | MOQ |
| --- | --- |
| DEFAULT | (null) |
| KAIPING HANSHUN SANITARY WARE INDUSTRIAL CO., LTD. | 100 |

SRTSS8710's last PO (by `issue_date DESC`) is also KAIPING HANSHUN (2026-05-18, three
lines). Exported xlsx row:

```
SRTSS8710 | KAIPING HANSHUN SANITARY WARE INDUSTRIAL CO., LTD. | MOQ 100
```

Supplier column and Remarks MOQ describe the SAME supplier (KAIPING HANSHUN), not the
DEFAULT link that also exists on this product and carries no MOQ - PASS, exactly the
"one choice" the AC asks for.

## 9. AC-S15.6 (backfill script) - covered by pytest only, not run against real data

`scripts/backfill_product_supplier_from_last_po.py` exists; its dry-run/--apply/
--drop-default-all behaviour is exercised by `test_supplier_last_po_s15.py` against an
empty scratch schema (per AC-S15.7's own instruction - "an exact count needs a blank
slate"). Per the plan's "Out" section, running it against real data is explicitly the
owner's call after review - NOT run here.

## 10. Browser verification - PASS, no console errors

`npx -y agent-browser@0.27.0 --session-name s15tester`, logged in as
`person38@example.com`. Navigated via sidebar only: `/` -> Procurement -> nested "Supply
Chain" -> "Reorder Planning" -> `/scm/reorder` -> "Start Plan" (defaults) ->
`/scm/reorder/1170bcc5-...`. Actions menu -> "Order sheet Excel" fired
`GET /api/v1/scm/order-summary/export?run_id=1170bcc5-...&format=xlsx` -> 200 (browser
network log). `console`/`errors` clean (only benign `[debug] JWT token extracted
successfully` lines, same as S14). Screenshots:
`documentation/plans/scm/evidence/reorder-feedback-9sep/s15-actions-menu.png` (Actions
dropdown showing "Order sheet PDF" / "Order sheet Excel"),
`documentation/plans/scm/evidence/reorder-feedback-9sep/s15-plan-grid.png` (plan Lines tab
searched to CB2154-DIY, the row used for the chosen-supplier-override test in step 6).
Session closed by name (`close`, not `close --all`).

PDF export re-confirmed via curl: `HTTP/1.1 200 OK`, `content-type: application/pdf`,
148314 bytes, `%PDF-1.7` magic - no regression from the S14 baseline.

## Summary

| AC | Result |
| --- | --- |
| AC-S15.1 no DEFAULT anywhere the engine chose | PASS (0/374 on the fresh run; 374/374 on the stale pre-S15 run, confirming the fix is real) |
| AC-S15.2 last-PO supplier, cross-checked 3 codes against SQL | PASS (SRTWT8241-GY + 2 others, exact match) |
| AC-S15.3 chosen supplier wins over last-PO reading | PASS (CB2154-DIY forced to DEFAULT via decision, sheet followed) |
| AC-S15.4 NULL issue_date sorts last | NOT LIVE-VERIFIABLE (no NULL-issue_date PO exists on this DB); covered by pytest (9/9 passed) |
| AC-S15.5 Remarks MOQ = same last-PO supplier's link | PASS (SRTSS8710: KAIPING HANSHUN in both Supplier and MOQ, not the product's other DEFAULT link) |
| AC-S15.6 backfill script | Covered by pytest against scratch schema only; not run against real data (owner's call, per plan) |
| AC-S15.7 pytest | PASS - 9/9 in `test_supplier_last_po_s15.py`, 28/28 in `test_order_summary_sheet.py` (no regression) |
| Browser / no console errors / export downloads | PASS |

Stack left RUNNING for the owner: backend :8083 (pid from this session), frontend :3083
(pid from this session, after the node_modules reinstall). No worker needed for this
slice.
