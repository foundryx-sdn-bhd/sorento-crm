# S14 (AC-S14.10) browser verification evidence

Lane: `feat/reorder-feedback-9sep`, stack :3083/:8083. Tool: `npx -y agent-browser@0.27.0`,
isolated `--session reorder-s14` (default daemon session shared with other agents).
Logged in as `person38@example.com` via the sign-in form (E2E creds from `.env.local`).

## 1. Navigate to the plan (sidebar clicks from `/`) - PASS

`/` -> sidebar "Procurement" -> nested "Supply Chain" sub-group -> "Reorder Planning" link
-> `http://localhost:3083/scm/reorder`. Note: "Reorder Planning" lives under the
**Procurement** top-level sidebar group's own "Supply Chain" sub-group (`config/menu.config.tsx`
line ~305), not under the top-level "Supply Chain" group (which only holds
Dashboard/Planning/Project Demand).

List showed 75 plans across 3 pages, all displaying "Planning" in the grid's Status column
even for runs the API returns as `status: "completed"` (confirmed via
`GET /api/v1/scm/reorder-runs`, API key auth) - the grid's "Status" column is a decision-workflow
label, not the backend run status, so it cannot be used to spot a completed run visually.
Picked the newest completed run from the API list, `run_id=9070da13-0ee2-48cc-9311-e25c4652b1d0`
(`status: "completed"`, `started_at: 2026-09-09T16:13:59Z` = displayed row
"10/09/2026 00:13" in MYT, top row on page 1), clicked it. Landed on
`/scm/reorder/9070da13-0ee2-48cc-9311-e25c4652b1d0`, matching run_id confirms correct plan.

## 2. Order sheet Excel - PASS

Actions menu (`Actions` -> "Order sheet Excel") fired
`GET /api/v1/scm/order-summary/export?run_id=9070da13-...&format=xlsx` -> 200 in the browser
network log. Fetched the identical URL with `curl -H "X-API-Key: test" -H "X-Acting-User-Id: <act-as-user>"`
-> HTTP 200, 32272 bytes, `file` reports "Microsoft Excel 2007+". Saved to
`/private/tmp/.../scratchpad/s14.xlsx`.

## 3. Workbook structure - PARTIAL PASS (one real defect found)

Opened with backend venv's openpyxl 3.1.5:

- Headers (row 1), exact match to spec: `Item code, BRW on hand, Reorder level, Project qty,
  Dealer o/s, Order qty, Delivery, Project / customer, Supplier, BRW PO qty, BRW incoming qty,
  Last in qty, Last in date, Remarks` - PASS.
- Header font: bold=True, color=FFFFFFFF (white) - PASS.
- Header fill: patternType=solid, fgColor=FF404040 - PASS ("solid dark fill", matches "bold +
  white + solid fill").
- `ws.freeze_panes == "A2"` - PASS.
- Data-cell border: `Delivery` cell for both test rows has `border.top.style == "thin"` - PASS.
- Data-cell wrap_text: `Delivery` cell `alignment.wrap_text == True` for both rows - PASS.
- SRT1000-CR row: `Delivery` and `Project / customer` both `"\n"`-joined multi-entry strings -
  PASS. `Project qty` = 8949, and summing the "Project / customer" quantities by hand
  (52+3538+75+598+1820+408+72+42+454+36+36+838+414+202+364) = 8949 - PASS, matches exactly.
- SRTSS8710 row: same pattern, `Project qty` = 3724; customer-quantity sum
  (732+621+394+88+111+138+500+1140) = 3724 - PASS.
- **DEFECT**: `BRW on hand`, `Reorder level` and `Order qty` are `None` for **every** data row
  in the workbook (checked all 374 rows, 0 non-null in any of the three columns), including
  SRT1000-CR and SRTSS8710. The header exists and is styled correctly but the column is never
  populated. This blocks the grid-vs-sheet on-hand comparison in step 5 below - there is no
  sheet value to compare against.

## 4. Order sheet PDF - PASS

Actions menu -> "Order sheet PDF" fired the network request (browser log showed it without a
resolved status due to a download-triggered navigation, so confirmed directly):
`curl -D- "http://localhost:8083/api/v1/scm/order-summary/export?run_id=9070da13-...&format=pdf" -H "X-API-Key: test" -H "X-Acting-User-Id: <act-as-user>"`
-> `HTTP/1.1 200 OK`, `content-type: application/pdf`, `content-disposition: attachment;
filename="order-summary-2026-09-10.pdf"`, `content-length: 165911`. First bytes `%PDF-1.7` -
valid PDF magic. No 503 `pdf_rendering_unavailable` seen - weasyprint is working on this lane.

## 5. Grid On hand vs sheet BRW on hand for SRT1000-CR - FAIL (blocked by the step-3 defect)

Grid Lines tab, SRT1000-CR row (`#145`): the "On hand BRW" column cell reads **75** (via
`get text` on the cell's inner button, not the flattened row string which concatenates
ambiguously). This is already BRW-scoped (column header is literally "On hand BRW", not a
company-wide total) so there is no separate ~4061 company-wide figure surfaced on this grid to
contrast against - the "4061 vs 75" comparison anticipated in the brief did not materialize as
described; the grid only ever shows the BRW-scoped figure (75). The sheet's "BRW on hand" cell
for the same item is `None` (see defect above), so grid (75) cannot be reconciled against sheet
(blank) - reported as FAIL because the sheet side of the comparison is empty, not because the
numbers disagree.

## 6. No regression on the plan page - PASS

After both exports: `GET /api/v1/notifications/unread-count` polling continued normally, no new
console errors (`errors` empty, `console` tail only showed benign `[debug] JWT token extracted
successfully` lines), Lines grid re-rendered 25 rows on reload, and the Actions menu still lists
exactly `Order sheet PDF`, `Order sheet Excel`, `Plan exceptions`, `PO worklist`, `Reset
planning` - no "Order summary" entry present.

## Summary

| Step | Result |
| --- | --- |
| 1. Navigate via sidebar to completed plan | PASS |
| 2. Order sheet Excel download (200, .xlsx) | PASS |
| 3. Workbook headers/formatting/freeze/border/wrap/Project-qty-sum | PASS, except `BRW on hand` / `Reorder level` / `Order qty` are blank on every row (DEFECT) |
| 4. Order sheet PDF download (200, %PDF) | PASS |
| 5. Grid On hand vs sheet BRW on hand | FAIL - sheet column is blank, cannot compare (same defect as step 3) |
| 6. No regression (grid renders, Actions menu unchanged) | PASS |

Browser session `reorder-s14` closed by name (not `close --all`) at the end of the run.

## Round 2 (fresh plan)

Captain correction: run `9070da13` was frozen at 00:14 on 10 Sep, before the S14 service commit
(01:18), on a worker still running pre-S14 code - the blanks above are not a defect, they are a
pre-504 row printing blank by design. Fix round landed at `0c63ebe30..590fc1d25`; lane worker
restarted on current code (Redis DB 9, queues `imports` + `catalogue_render`). Re-verified with a
freshly-started plan on the restarted worker. New `--session reorder-s14b`, logged in the same way,
closed by name at the end.

**1. Start a fresh plan - PASS.** `/` -> sidebar Procurement -> nested "Supply Chain" -> "Reorder
Planning" -> `/scm/reorder` -> "Start Plan" button opened the modal with defaults (All warehouses,
All products, no dates) -> clicked "Start Plan" -> navigated to
`/scm/reorder/c2251631-849a-4bdd-9b7f-6857fc0c5de4`. Polled
`GET /api/v1/scm/reorder-runs/c2251631-...` (API key auth): `status: "completed"` on the **first**
poll, ~11 seconds after start (`started_at 17:36:19` -> checked at `17:36:30`). Well under the
3-minute stay-queued threshold - no in-process run needed.

**2. Order sheet Excel + PDF on the new run - PASS.** Actions menu -> "Order sheet Excel" fired
`GET /api/v1/scm/order-summary/export?run_id=c2251631-...&format=xlsx` -> 200 in the browser
network log. Actions menu -> "Order sheet PDF" fired the `format=pdf` variant -> 200 in the same
log. Re-fetched the xlsx via `curl -H "X-API-Key: test" -H "X-Acting-User-Id: <act-as-user>"` -> HTTP
200, 25561 bytes, saved to `.../scratchpad/s14b.xlsx`.

**3. Workbook content for SRT1000-CR / SRTSS8710 - PASS, defect from round 1 is resolved.**

| Field | SRT1000-CR | SRTSS8710 |
| --- | --- | --- |
| BRW on hand | 75 | 160 |
| Reorder level | 50 | 50 |
| Project qty | 1096 | 732 |
| Delivery | `May - 364\nSep - 732` | `Sep - 732` |
| Project / customer | `ASASJAYA HARDWARE ENTERPRISE (PROJECT) - 732\nEMB EMPRESS (MALAYSIA) SDN BHD (PROJECT) - 364` | `ASASJAYA HARDWARE ENTERPRISE (PROJECT) - 732` |
| BRW PO qty / BRW incoming qty / Last in qty | 0 / 0 / 0 | 0 / 0 / 0 |
| Last in date | 22/06/2026 | 03/08/2026 |
| Remarks | None | None |

Project qty = customer-line sum for both (732+364=1096; 732=732) - PASS. Multi-line `\n`-joined
Delivery + Project/customer cells confirmed - PASS.

Row counts (374 total data rows): **374/374 rows have a non-None `BRW on hand`** (expected "all" -
PASS, matches expectation exactly, fixes round-1's 0/374). **2/374 rows have a non-empty
`Delivery`** (expected "a few dozen at most" - PASS, well within bound; the inquiry book behind
this run is small, consistent with the brief).

**4. Grid vs sheet On hand for SRT1000-CR - PASS.** Same-run Lines grid, row `#145` SRT1000-CR,
"On hand in the site pool" cell -> `get text` on the cell's button -> **75**. Sheet's `BRW on
hand` for the same item (from step 3) -> **75**. Grid and sheet agree exactly.

### Round 2 summary

| Step | Result |
| --- | --- |
| 1. Fresh plan started + completed within 3 min (11s actual) | PASS |
| 2. Order sheet Excel + PDF on new run (both 200) | PASS |
| 3. BRW on hand / Reorder level / Project qty / Delivery / customer cells populated correctly | PASS (round-1 blank-column defect resolved by the S14 fix commits) |
| 4. Grid On hand (75) vs sheet BRW on hand (75) | PASS - match exactly |

Browser session `reorder-s14b` closed by name (not `close --all`) at the end of the run.
