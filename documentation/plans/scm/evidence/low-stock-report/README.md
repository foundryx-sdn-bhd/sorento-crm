# Evidence - low stock report

## S1 (AC-17) - an unscoped run before and after the second admission leg

Local 0907 prod copy (`sorento_ai_automation_0907`), 14 Sep 2026, engine run in-process
(`create_run(db, None, "warehouse", enqueue=False)` then `run_reorder`), not through the worker.
Global `scm.reorder_policy.dead_stock_days` = 180.

| Run | Recommendations | Products planned | Buys | needs_level | Wall |
| --- | --- | --- | --- | --- | --- |
| Before - 10/09 02:10, the newest completed unscoped run on the committed-demand leg alone | 950 | 950 | 374 | 1 | 4.1 s |
| Before - 10/09 01:55 | 950 | 950 | 374 | 1 | 4.1 s |
| Before - 10/09 01:54 | 950 | 950 | 374 | 1 | 4.5 s |
| **After - 14/09 09:01, both legs** | **2,017** | **2,017** | **1,151** | 1 | **8.5 s** |

What the second leg admits on its own, measured with the same predicate the engine now runs:

- 2,697 products are below their resolved level (a person's product-wide `scm.reorder_level`
  row with `source in (manual, accepted_suggestion)`, else `products.reorder_level`, 0 is not a
  level) against site-pool on hand at `counts_as_available` warehouses.
- 1,433 of those still moved inside 180 days - the dead guard keeps the other 1,264 out.
- Union with the 950 the committed-demand leg already admitted = the 2,017 planned above, so
  366 products satisfy both legs.

Against the plan's estimate: products landed at 2,017 rather than the forecast ~1,600 (the
estimate came off the "7,778 below level, 582 in the last run" figures rather than from running
the predicate), and wall time is 8.5 s against a 15 s ceiling - roughly double the 4.1 s
baseline, for roughly double the products, which is the same ~4 ms a product the plan measured.
777 new buy rows reach the buyer; that is the report's whole point (CB100-BL-DIY's shape).

## S4 (Phase 1) - agent-browser run, 14 Sep 2026

Lane dev server `npm run dev -- -p 3084` from the lane worktree (:3080/:3081/:3082 were held by
other lanes), `FASTAPI_INTERNAL_URL=http://localhost:8000` (the primary backend, on the 0907 prod
copy). agent-browser 0.27.0, isolated session `lowstock-3084`.

Navigation, sidebar clicks only from `/`:
Dashboards -> Procurement -> Supply Chain -> Reorder Planning -> plan `10/09/2026 10:10`
(`/scm/reorder/00788044-...`, 377 lines).

| Step | Read |
| --- | --- |
| Actions menu at 1280x900 | `Order sheet PDF`, `Order sheet Excel`, `Low stock report (Excel)`, `Plan exceptions`, `PO worklist`, `Reset planning` - the new item third, directly under Order sheet Excel (AC-1). |
| Click `Low stock report (Excel)` | `POST /api/v1/scm/order-summary/export`, body `{"run_id":"00788044-...","format":"low_stock_xlsx"}` -> `422` (AC-2's request shape). The backend has no such format until S3, so the toast reads the extracted server message, `format must be pdf or xlsx.` - `extractApiError` is doing its job; nothing is hand-rolled. |
| Actions menu at 375x812 | All six items render, none clipped, `Low stock report (Excel)` fully readable in a 216px menu (AC-5). |
| Plans list at 1280 | Unchanged: `daily` and `superseded` badges render in the Plan column at its existing `size: 190`. No `via chat` row exists yet - `requested_via` is emitted by the backend in S5, and the FE never infers it (AC-4 is covered by `ReorderRunsGrid.test.tsx` until then). |
| Settings > Chatbot Media | `Low stock report chat wait (seconds)` sits in the Pacing card beside `Synchronous wait seconds`, seeded 40 from the service fallback (the column lands in S5); typing `120` refuses inline with "Enter a whole number between 5 and 90." and disables Save (AC-7). Nothing was saved. |

Console: one error, `Each child in a list should have a unique "key" prop` in `Demo1Layout` -
present on the home page before any of this lane's code loads, unrelated.

Not exercised in Phase 1, by design: the workbook itself, the My Downloads row label
(`low_stock_xlsx` rows only exist once S3 writes them), and the `via chat` badge against real data.

## Phase 3 - browser verification, 14 Sep 2026 (attested)

Driven end to end by the tester in one agent-browser 0.27.0 session (`--session lowstock-p3`,
headless, closed at the end). Lane stack: FE `npm run dev -p 3085` (frontend files identical to
lane head 8f1acc3ee - `git diff 5c3850a2f 8f1acc3ee` touches no `sorento_crm_frontend/` source),
BE `:8085` and the RQ worker both from the lane worktree at 8f1acc3ee, runtime DB
`sorento_ai_automation_pospo` (prod copy). Navigation was sidebar clicks from `/` at both widths
(1280x900 and 375x812); `get url` was re-read before each assertion block.

Run under test: `dc562d2a-7be8-46f6-ac80-85c4c2f280bf`, `status = completed`,
`requested_via = chat`, created 14/09/2026 21:21 MYT, 1,546 recommendations (535
`hidden_by_default`).

| AC | Verdict | What was read in the browser |
| --- | --- | --- |
| AC-1 | PASS | Plan view Actions menu (1280 and 375) lists `Order sheet PDF`, `Order sheet Excel`, `Low stock report (Excel)` in that order, the new item third and directly under Order sheet Excel, then `Plan exceptions`, `PO worklist`, `Reset planning`. `p3-02-actions-menu-1280.png` (the 375 shot was pruned, see Artifacts). The "disabled while an export is pending" half was NOT observed - the export was ready in 3.1 s, faster than a snapshot round-trip; it stays covered by `ReorderPlanView.lowStock.test.tsx`. |
| AC-2 | PASS | Clicking it fired `POST http://localhost:3085/api/v1/scm/order-summary/export` -> 200, immediately followed by `GET /api/v1/downloads?limit=50` (the My Downloads key invalidation). Toast text read verbatim from the accessibility tree: `Preparing the low stock report - it will appear in My Downloads.` Shots pruned, see Artifacts. |
| AC-3 | PASS | My Downloads drawer row: `low-stock-14092026.xlsx` / `Low stock report · 14/09/2026, 9:23 pm` / `Ready` - the label is the friendly one, never the raw `low_stock_xlsx` kind, and the filename is `low-stock-<ddmmyyyy>.xlsx` for as-of 14/09/2026. API row: `{"kind":"low_stock_xlsx","status":"ready","filename":"low-stock-14092026.xlsx","source_entity_type":"reorder_run","created_at":"2026-09-14T13:23:15.752245","ready_at":"2026-09-14T13:23:18.854688"}` - 3.1 s end to end. The intermediate "preparing" frame was not caught (it lapsed inside the 3 s poll gap); the row was read as `Ready` after it. `p3-04-my-downloads-1280.png` (the 375 shot was pruned, see Artifacts). |
| AC-4 | PASS | Reorder Planning list, Plan column: the nine newest rows (14/09/2026 21:21 down to 19:27) each render `<span data-slot="badge" class="... bg-secondary dark:bg-secondary/50 text-secondary-foreground">via chat</span>` - the secondary Badge variant, no new column. The manual run `14/09/2026 18:13` and the older `10/09/2026 19:39` / `10/09/2026 10:10` rows render the timestamp alone, cell HTML `<div class="flex min-w-0 items-center gap-2"><span class="truncate ...">14/09/2026 18:13</span></div>` with no badge node. Backed by the DB: run `dc562d2a` has `requested_via = 'chat'`. Shots pruned, see Artifacts. |
| AC-5 | PASS with a finding | 375: all six Actions items measure left 9 -> right 210 inside a 375 viewport, `scrollWidth === clientWidth` on every one, so `Low stock report (Excel)` is fully readable and nothing clips. 1280 and 375: the Plan column keeps its explicit `size` - measured `clientWidth = 190` on both the badged and the unbadged row, so the badge does not widen the column. FINDING (below): inside that fixed 190px the badge pushes the timestamp into its `truncate`, and the span carries no `title`. |
| AC-7 | PASS | User Management > Settings > Chatbot Media (sidebar clicks; `/user-management/settings/chatbot-media`): `Low stock report chat wait (seconds)` sits beside the media pacing fields showing `40`. Typing `4` -> inline `Enter a whole number between 5 and 90.` and `Save Settings` `disabled === true`; typing `91` -> same message, Save still disabled; `60` -> Save enabled, click fired `POST /api/v1/user-management/settings/general` 200 and `system_settings.low_stock_sync_wait_seconds` read `60` in Postgres; page reloaded -> field reads `60`; set back to `40` and saved -> DB reads `40`. At 375 the field renders at x=37 width 301 inside the 375 viewport, unclipped. Shots pruned, see Artifacts. |
| AC-22 | PASS | Container line shape, straight out of the two downloaded workbooks (values quoted below). |
| AC-31 | PASS | Workbook read with `openpyxl`: `wb.sheetnames == ['Low stock', 'All']`; both sheets' header row is exactly `['Item code', 'Description', 'Category', 'BRW on hand', 'Reorder level', 'Reorder qty', 'Suggested qty', 'Suggestion', 'Order qty', 'Dealer o/s', 'Supplier', 'BRW PO qty', 'BRW incoming qty', 'Last in qty', 'Last in date', 'Remarks']` (16 columns, same order on both); `freeze_panes == 'A2'`; header cell bold with fill `FF404040`; quantity cells are `int` (`BRW on hand`, `Reorder level`, `Reorder qty`, `Suggested qty`, `Dealer o/s`, `BRW PO qty`, `Last in qty` all typed `int` on row 2). |
| AC-32 | PASS | "Low stock" = 1,178 data rows; a row-by-row check found **0** rows where `BRW on hand >= Reorder level` or either is blank. Re-deriving the membership from the "All" sheet with the AC's own predicate (`on hand` and `level` both non-null and `on hand < level`) yields exactly the same 1,178 item codes, so hidden-by-default rows are included. Both sheets are sorted by (Category, Item code) - verified by comparing the key list against its own `sorted()`. |
| AC-33 | PASS | "All" = 1,546 data rows; the DB has `count(*) = 1546` (and `count(distinct product_id) = 1546`) recommendations for this run, of which 535 are `hidden_by_default` - so All carries the covered rows too. The order sheet exported from the same run in the same session has 1,011 rows = 1,546 - 535, i.e. it still drops the hidden ones (unchanged). |
| AC-34 | PASS | Description is `products.description`, not the code-repeating `product_name`: e.g. `SRTBT1855-15 -> SORENTO FREE STANDING BATHTUB 1500x750x750MM SRTBT1855-15`, and for `SRTWT7408` the DB holds `description = SORENTO CONCEALED SHOWER COLD TAP SRTWT7408` while `product_name = SRTWT7408`. Only 5 of 1,546 rows have Description equal to Item code, and all five are non-stock service items whose description really is the code (`LABOUR CHARGE`, `**SPARE PART`, `MISC`, `PALLET`, `PVC 5 SECTION FLUSH PIPE`). Category is a category code, not a name or a UUID (`BATHTUB`, `BRT-BA`, `BRT-FT`, `BRT-SH`, `BRT-WB`, `BRT-WC`, `CB-ACC`, `CB-BA`, ...). Reorder qty is numeric or blank: 481 blanks across All, 0 non-numeric non-blank values. |

Container line (AC-22), quoted verbatim from the cells (`\n` = the in-cell newline, first line is
the total):

- low stock workbook, "Low stock" sheet: `4\nSPO-2026/08-0118 - TIIU7904597 - 4`
- low stock workbook, no-container shape: `15\nSPO-2026/08-0066 - 15`
- low stock workbook, large allocation: `2442\nSPO-2026/09-0033 - TCNU2951576 - 2442`
- order sheet workbook, same run: `30\nSPO-2026/09-0006 - NYKU0779055 - 30` and the mixed
  multi-doc cell `878\nSPO-2026/08-0087 - 206\nSPO-2026/08-0088 - 49\nSPO-2026/08-0089 - 75\nSPO-2026/08-0092 - 384\nSPO-202608-0094 - WHSU5693590 - 164`

Both workbooks were fetched through the app's own path - the drawer's `GET
/api/v1/downloads/{id}/url` called in-page with the session's own bearer token, then the returned
R2 URL to disk (`low-stock-14092026.xlsx` 245,097 bytes from download
`f91ba8c9-5c28-4769-b5a2-96681229249b`; `order-sheet-14092026.xlsx` 77,161 bytes from
`433bbc78-d3e0-4006-ab37-ec5768373bba`). Neither workbook is committed - evidence carries no
.xlsx binaries; the figures quoted above are what they were read for.

### Console

`errors` (uncaught page errors) was empty for the whole session. `console` carried exactly two
distinct lines:

- `[error] Each child in a list should have a unique "key" prop.` - the known pre-existing
  `Demo1Layout` warning, present on the home page before any of this lane's code loads.
- `[warning] Warning: Missing 'Description' or 'aria-describedby={undefined}' for {DialogContent}.`
  (x6) - raised by the Sheet shell behind the My Downloads drawer
  (`components/my-downloads/MyDownloadsDrawer.tsx`), which this lane does not touch; the lane's
  only file there is `DownloadRow.tsx`. Pre-existing, not introduced here.

### Finding - the "via chat" badge truncates the plan timestamp, and there is no title fallback

Not a blocker, and it does not fail an AC as written (AC-5 asks that the column not widen, and it
does not), but it is a readability regression on exactly the rows this lane creates:

At 1280 the badged rows render `14/09/2026 ...` while the unbadged `14/09/2026 18:13` renders in
full. Measured on the newest row: the timestamp span has `scrollWidth 127` against
`clientWidth 100` (`truncated = true`), badge width 50px inside the 190px cell, and
`span.getAttribute('title') === null`. `ReorderRunsGrid.tsx` renders
`<span className="truncate text-sm font-medium tabular-nums">{runStartedLabel(...)}</span>` with
no `title`, so the clock time of a chat-created plan cannot be read at all - not even on hover.
CLAUDE.md's DataGrid rule is "long text uses `truncate` + `title`". One-line fix:
`title={runStartedLabel(row.original.started_at)}` on that span. The same span already backed the
older `daily` / `superseded` badges, so the pattern predates this lane - the new badge is what
makes it bite on the common row.

FIXED in reviewer round 3 (item 8): the span now carries that `title`, asserted in
`ReorderRunsGrid.test.tsx`.

### Not covered by this pass

- The chat half (S5/S6/S7): verified separately by the console check (2/2 pass), not re-walked here.
- AC-1's "disabled while an export is pending" - the export completed in 3.1 s, too fast to catch a
  disabled frame; covered by vitest.
- The "preparing" state of the My Downloads row, for the same reason.

### Artifacts, all from this attested run (14 Sep 2026, 21:22-21:35 MYT)

Pruned to the two screenshots `documentation/agents/browser-verification.md` allows per lane. The
other eleven Phase 3 shots, the two Phase 1 ones (`s4-actions-1280.png` / `s4-actions-375.png`)
and the two workbooks were all read for the verdicts recorded above and then dropped rather than
committed, so the lane tracks exactly the two files below. Both are under the 200 KB pre-push
`png-size` limit.

| File | What it shows |
| --- | --- |
| `p3-02-actions-menu-1280.png` | Actions menu at 1280, the three export items in order |
| `p3-04-my-downloads-1280.png` | My Downloads drawer, `Low stock report` row ready |

`p3-02-actions-menu-1280.png` PREDATES the reviewer round 3 rename: the item in that shot reads
"Low stock report (Excel)" and now reads "Low stock report Excel". Its position, order and the
neighbouring items are unchanged, which is what the shot is here for.
