# Loading Plan merged-cells + un-fold - end-of-lane browser verification

Branch: fix/loading-plan-merged-cells-unfold
Worktree: `.claude/worktrees/loading-plan-merged-cells`
Stack under test: FE http://localhost:3080 (next dev), BE http://localhost:8080, DB `sorento_ai_automation_0907`
Tool: `npx -y agent-browser@0.27.0`, session `lpmc`
Login: env names `E2E_EMAIL` / `E2E_PASSWORD` from the worktree's `sorento_crm_frontend/.env.local` (values not printed)

Navigation: logged in at `/`, sidebar click path Procurement > Supply Chain (nested submenu under
Procurement, not a separate top-level item in this build) > Loading Plan. No deep URL used for
first arrival.

## Section A - un-fold + qty split on the existing sent plan (CHAOZHOU JINBAICHUAN, 4 Sep,
id `3294616c-40e8-4e24-9201-9263adcdcc80`)

1. **PASS** - Lines tab renders ONE grid. `document.body.innerText.includes('products held with
   no open demand')` => `false` on every page including the last page. Screenshot
   `A0-lines-tab-initial.png`.
2. **PASS** - Column header order: Rank, Product, Suggested qty, Requested qty, Remarks, Need, ...
   Visible in `A0-lines-tab-initial.png`.
3. **PASS** - Last page (3 of 3, rows 51-53 of 53) shows the no-demand rows after every ranked row:
   blank rank (`-`), muted text, Need = "No open d...". Screenshot
   `A3-last-page-no-demand-rows.png`.
4. **PASS** - Row `SRTWC8152-SH-300` has a real override (Suggested 0, Requested 50). Its
   Suggested-qty cell title reads
   `"55 need - 249 on hand - 50 incoming SPO - 184 incoming PL (not yet on an SPO) = 0"` (ends in
   `= <engine_qty>`); the Requested-qty input has no title attribute. Screenshot
   `A4-override-row-tooltip.png`.
5. **PASS, with a caveat** - Plan status is "sent" and the grid is editable (inputs enabled, not
   read-only text), so this plan is on the editable branch, not the AC-Q4 read-only branch.
   Typed 60 into the same row's Requested qty: Suggested qty stayed `0` (unchanged), the "To
   request" stat card moved from 12,161 to 12,171 (+10, matching the delta). Reverted the input
   back to 50: "To request" returned to 12,161 and the Save counter returned to its pre-edit
   baseline. **Caveat**: the page loaded with `Save (1)` already showing on first load AND after a
   full hard reload (not a client nav), with zero edits made by me. This survived a full page
   reload, so it is not a stale-React-state artifact of one session - something about this
   specific plan's initial data makes the dirty-tracker count 1 change that isn't there, or a real
   unresolved server-side draft exists for this plan. I did not click Save (would risk writing
   whatever data caused the count, into a plan I don't understand the origin of). Recommend the
   coder/reviewer check what `Save (1)` refers to on this plan before it is next opened for real
   editing - clicking Save blind on a "sent" plan could silently commit an unintended change.
6. **PASS** - Sorting by Suggested qty (desc) vs Requested qty (desc) reorders: row
   `SRTWC8152-SH-300` (suggested=0, requested=50) sits at index 20 under Suggested-desc and index
   14 under Requested-desc, i.e. clearly reordered between the two sorts. Screenshots
   `A6-sort-suggested-qty-desc.png`, `A6-sort-requested-qty-desc.png`,
   `A6-sort-requested-qty-asc.png`.

## Section B - merged cells through a real upload (new plan)

Uploaded `/Users/tehjayson/Desktop/Stock list (1).xlsx` via Loading Plan list > Upload > selected
supplier "CHAOZHOU JINBAICHUAN SANITARY WARE CO., LTD" (two identical-looking options exist in the
supplier picker for this name; picked the first) > Stock list radio (default) > uploaded file >
Confirm and start plan. Processed synchronously (no worker involved), landed directly on the new
plan detail page.

**New plan id: `da3809c3-93c7-4dac-aeef-d227eb0ca3f6`** (left in place per instruction - not
cancelled or deleted).

1. **PASS** - Upload succeeded in-request (no polling/worker wait needed); new plan opened
   automatically at `/scm/loading-plan/da3809c3-93c7-4dac-aeef-d227eb0ca3f6`. Save(0), clean state
   (no leftover dirty flag, unlike the existing plan in Section A). Screenshot
   `B1-new-plan-lines.png`.
2. **Supplier codes checks:**
   - MWB247 -> "盆小孔 · M" **PASS** (confirmed via search filter).
   - CWB247 -> "盆大孔 · C" **PASS** (visible in the unfiltered Needs-a-decision list).
   - CWC605-RL-180 -> "分体座头 · C · 180横排" **PASS** (unchanged-code case, visible unfiltered).
   - CGB247 -> expected "盆小孔 · C": **FAIL to locate in the Supplier codes tab.** Searching
     "CGB247" and even the "CGB" prefix in the tab's search box returns 0 of 62 in "Needs a
     decision" and 0 of 21 in "Remembered" - the code is absent from both buckets in the UI.
     Screenshot `B2-supplier-codes-cgb247.png` (shows the empty-state after search).
     - Cross-checked in Postgres (see SQL below): `scm.supplier_inventory` for this plan HAS a
       correctly-parsed row for `CGB247` with `product_name='盆小孔'`, `brand='C'`,
       `cbm_per_unit=0.03`, `qty_packed=256`, `qty_unfinished=0` - i.e. the merge parsing itself
       got CGB247 right.
     - Cross-checked the actual backend response for
       `GET /api/v1/scm/supplier-code-aliases/unmatched?plan_id=da3809c3-93c7-4dac-aeef-d227eb0ca3f6`
       (captured via the browser's own network log, then replayed with the session's own bearer
       token): the JSON payload contains exactly 62 items (matches the "Needs a decision (62)"
       badge) and **no CGB-prefixed item_code appears anywhere in it** - not CGB247, not CGB248,
       not CGB762, not CGB9032B, even though all four exist in `supplier_inventory` for this plan
       and none of them has any row in `scm.supplier_product_code_alias` (checked with no
       supplier_id filter - zero rows for `supplier_code ilike 'CGB%'` system-wide, so they are
       not matched, not dismissed, not remembered anywhere).
     - This looks like a real gap: the merged-cell parser wrote a correct `supplier_inventory` row
       for CGB247, but the `/scm/supplier-code-aliases/unmatched` listing endpoint silently drops
       every CGB-prefixed code from its result set (backend-side filter/join issue, not a frontend
       search bug - confirmed by replaying the exact API call outside the UI). I did not
       investigate the endpoint's source since I am read-only on code for this task; flagging for
       the coder to check whether this is in-scope for this lane or a pre-existing gap in the
       supplier-code-alias listing service.
3. **Counts on this new plan**: Lines grid = **53 rows** (1-25, 26-50, 51-53 across 3 pages).
   Supplier codes = **62 "Needs a decision" + 21 "Remembered" = 83** shown in the tab UI, but the
   underlying `scm.supplier_inventory` table has **118 distinct item_codes** for this plan
   (`select count(distinct item_code) ... where loading_plan_id = 'da3809c3-...'` => 118). 118 is
   inside the "114-120" range the brief expected, but the 118-83=35 gap between total DB codes and
   what the two tab buckets show (of which at least 4 CGB-prefixed codes are confirmed missing,
   see above) suggests the 35 "missing" rows are a mix of: already-bound/matched codes (correctly
   excluded from both un-resolved buckets) and the CGB-prefix gap above (incorrectly excluded).
   I did not enumerate all 35 individually - time-boxed to the four CGB codes flagged above.
4. **SQL cross-check** (read-only, `psql -U sorento_crm -h localhost -d sorento_ai_automation_0907`):

   ```
   select item_code, product_name, brand, spec, cbm_per_unit, qty_unfinished
   from scm.supplier_inventory
   where loading_plan_id = 'da3809c3-93c7-4dac-aeef-d227eb0ca3f6'
     and item_code in ('SRTWB247','MWB247','CGB247','CWB247')
   order by item_code;

    item_code | product_name | brand | spec | cbm_per_unit | qty_unfinished
   -----------+--------------+-------+------+--------------+----------------
    CGB247    | 盆小孔       | C     |      |         0.03 |            0.0
    CWB247    | 盆大孔       | C     |      |         0.03 |            0.0
    MWB247    | 盆小孔       | M     |      |         0.03 |            0.0
    SRTWB247  | 盆小孔       | S     |      |         0.03 |         3551.0
   ```

   - cbm 0.03 on all four **PASS** (matches brief).
   - qty_unfinished 0 on the three covered rows, nonzero on SRTWB247 **PASS** in shape (anchor
     carries the real count, covered rows read 0) - though the SRTWB247 value is 3551 on this
     data, not the owner's 14 Sep "2" (expected: this DB's data differs from the 14 Sep fixture,
     per the brief's own caveat).
   - `spec` column is empty/NULL for all four here (the brief's SQL check didn't ask about spec,
     only product_name/brand/cbm/qty_unfinished - the product_name+brand values match the expected
     "盆小孔"/"盆大孔" + M/C/S split exactly).

## Section C - console

No console errors at any point in the run (`agent-browser errors` returned empty every time it was
checked - after login, after Section A edits/sorts, after the Section B upload). Console log noise
was limited to expected dev-mode chatter: JWT-extracted debug lines, i18next init, React DevTools
download reminder, Next.js Fast Refresh rebuild lines (HMR from the coder's ongoing edits), and two
FormData-related debug lines during the file upload. Nothing that looks like an application error.

## Summary

- Section A (un-fold + qty split on the existing sent plan): **all 6 checks pass**, with one
  pre-existing-state caveat (`Save (1)` baseline dirty flag on plan
  `3294616c-40e8-4e24-9201-9263adcdcc80` that predates and survives a hard reload; not created by
  this run, not saved by this run, but worth a look before someone next opens that plan for real
  edits).
- Section B (merged cells through a real upload): parsing into `supplier_inventory` is correct for
  all four probe codes (MWB247, CGB247, CWB247, CWC605-RL-180, SRTWB247) including the CGB247 case
  the brief specifically wanted checked. The **defect is downstream of parsing**: the Supplier
  codes tab / `/scm/supplier-code-aliases/unmatched` endpoint drops CGB-prefixed codes from both
  its "Needs a decision" and "Remembered" buckets even though they are correctly parsed and have
  no alias record anywhere - a real find-and-triage item, not a nitpick, since a purchaser working
  this tab would never see CGB247 to bind it.
- Section C: clean console throughout.
- New plan `da3809c3-93c7-4dac-aeef-d227eb0ca3f6` left in place per instruction, not cancelled or
  deleted.

## Screenshots (this directory)

- `A0-lines-tab-initial.png`
- `A3-last-page-no-demand-rows.png`
- `A4-override-row-tooltip.png`
- `A5-qty-edit-in-progress.png`
- `A6-sort-suggested-qty.png`, `A6-sort-suggested-qty-desc.png`
- `A6-sort-requested-qty-asc.png`, `A6-sort-requested-qty-desc.png`
- `B1-new-plan-lines.png`
- `B2-supplier-codes-cgb247.png`
- `B3-supplier-codes-full.png`
- `C1-lines-375px.png`

## Captain's resolution of the two open items (15 Sep)

- **CGB247 absent from the Supplier codes queue: not a defect.** On plan da3809c3 the row is bound
  to our product CGB247 by direct code match (`supplier_inventory.product_id` set, product code
  CGB247), so it is a Lines row, not an unmatched code. The queue lists rows bound to neither a
  product nor a set (R19). The parse itself is correct (product_name 盆小孔, brand C, cbm 0.03).
- **`Save (1)` on first load of the sent plan 3294616c: pre-existing behaviour, not this lane.**
  The plan carries two saved line edits (one qty 50, one qty 0); Save (N) counts rows whose
  requested figure differs from the engine figure, saved or not. `LoadingPlanView.tsx` has zero
  diff on this branch. Left as-is; if the owner wants Save (N) to count only unsaved edits that is
  a separate ticket.
