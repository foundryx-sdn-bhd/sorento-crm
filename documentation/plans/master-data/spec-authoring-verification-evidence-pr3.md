# Spec verification (PR 3) - agent-browser evidence run

## What this is

This is the recorded agent-browser evidence run standing in for a Playwright spec, per the
repo's standing order (`CLAUDE.md`, "Persisted Playwright spec" section: no new spec is added;
a flow that would have earned one is covered instead by a reproducible agent-browser evidence
run written into the plan). It documents the walk that was actually driven against the running
stack for AC-D.19 ("Verification workflow and product list", PR 3) of
`documentation/plans/master-data/spec-authoring-verification-acceptance-criteria.md`.

**The run was cut by a session limit** (`resets 4:50am (Asia/Kuala_Lumpur)`) after the browser
walk had finished but before this evidence document was written or the vitest files were
committed. This document was reconstructed after the fact from the run's own artifacts: 27
`e2e-*.png` screenshots, the backend access log (`backend.log`), and the dead agent's own
transcript summaries (grepped, not the raw JSONL). No new browser session was opened to produce
it - every claim below is sourced from something the original run recorded.

- Date of the original run: 2026-08-17 (session timestamps 00:28-00:46 local time inside the
  screenshots' `e2e-*` filenames and the backend log).
- Stack: backend on `:8000`, frontend dev server on `:3050` (later moved to `:3022` mid-run after
  a CORS issue with the first port, per the transcript), local Postgres that is a copy of prod
  (**not** sqlite), holding **8,820 live (non-discontinued) product codes** at the time of the
  walk (`Verified 0 of 8,820 live codes` on the unfiltered worklist).
- Branch: `fm/spec-pr3-verification-list`. Commits under test:
 - `307a9268` - spec verification worklist against fixtures (PR 3 phase 1)
 - `9b770b5e` - spec verification off mocks; verify/unverify on the Specifications tab (PR 3
    phase 2 frontend)
 - `bd4da862` - spec verification ledger, worklist and verify/unverify endpoints (PR 3 phase 2
    backend)

## Vitest (Phase 2 tests, this session)

Four new files, 47 tests, all green (re-run to confirm during this reconstruction):

```
npx vitest run "app/(protected)/master-data-management/spec-verification" \
  "app/(protected)/master-data-management/products/[id]/components/ProductSpecificationsTab.verification.test.tsx"

 Test Files  4 passed (4)
      Tests  47 passed (47)
```

- `sorento_crm_frontend/app/(protected)/master-data-management/spec-verification/services/specVerificationService.test.ts` (16 tests)
- `sorento_crm_frontend/app/(protected)/master-data-management/spec-verification/hooks/useSpecVerification.test.ts` (8 tests)
- `sorento_crm_frontend/app/(protected)/master-data-management/spec-verification/components/SpecVerificationList.test.tsx` (13 tests)
- `sorento_crm_frontend/app/(protected)/master-data-management/products/[id]/components/ProductSpecificationsTab.verification.test.tsx` (10 tests)

Per the dead agent's transcript, these 47 were also run alongside the neighbouring suite
(52 pre-existing tests in the same directories) with all 99 green, and the full
`master-data-management` vitest directory (35 files / 289 tests) was green at the time. Only the
47 new tests were re-run for this reconstruction; the wider-suite claim is carried from the
transcript, not independently re-verified here.

## Steps, in order

All screenshots referenced live at
`/private/tmp/claude-501/-Users-tehjayson--treehouse-sorento-crm-732336-1-sorento-crm/42477364-f03d-4393-b62c-917551000a3a/scratchpad/`.

1. **Sidebar navigation from `/`.** Logged in, landed on `/` (`e2e-01-home.png`). Expanded
   "Product Management" in the sidebar and clicked the "Spec Verification" leaf. Landed on the
   worklist showing `Verified 0 of 8,820 live codes` (`e2e-02-worklist.png`).
   Network: `GET /api/v1/master-data/product-specifications/verification/worklist?page=1&limit=25`
 - 200 (backend.log line 223, 1.664s).

2. **Filter by search + state, URL-persisted params.** Typed `63524` into the search box, then
   opened the Verification filter and picked "Unverified" (`e2e-03-search-63524.png` shows the
   filter popover with "Unverified" checked). Both `query` and `state` params were carried on the
   GET, confirming the filters are wired into the request (and, per the transcript, persisted in
   the URL):
 - `GET .../worklist?page=1&limit=25&query=63524` - 200 (line 263)
 - `GET .../worklist?page=1&limit=25&query=63524&state=unverified` - 200 (line 290)

3. **Single-row verify.** With the filter narrowed to 2 live codes (`63524`, `63524D`), clicked
   the row-level "Verify" button on `63524`. Pill flipped to "Verified", button became "Unverify",
   row order unchanged (`e2e-04-row-verified.png`).
   Network: `POST /api/v1/master-data/product-specifications/verification/verify-bulk` (single
   item) - 200 (line 413, 00:32:35).

4. **Unverify the same row.** Clicked "Unverify" on the same row. It read "Unverified" again
   (`e2e-05-row-unverified.png`).
   Network: `POST .../verification/unverify-bulk` (single item) - 200 (line 495, 00:32:54).

5. **Multi-select bulk verify with count confirmation.** Ticked both rows; the bulk action strip
   appeared ("2 selected", "Verify selected", "Unverify selected", "Clear"). Clicked
   "Verify selected"; a confirmation dialog read **"Verify 2 product code(s)? A code with open
   exceptions, or one whose values moved while you were reviewing, is reported back as skipped."**
   (`e2e-06-confirm-verify-bulk.png`). Confirmed; both rows flipped to "Verified"
   (`e2e-07-bulk-verified.png`).
   Network: `POST .../verification/verify-bulk` - 200 (line 555, 00:33:32).

6. **Exception code skipped with reason (bulk).** Cleared filters back to the full 8,820-row
   view (`e2e-08-exception-code.png`), then searched `MCH906`, a code carrying **3 open
   exceptions** (`e2e-08b-exception-code.png`). Selected it and clicked "Verify selected"; the
   confirmation read **"Verify 1 product code(s)? ..."** (`e2e-09-confirm-verify-exception.png`).
   Confirmed. MCH906 stayed "Unverified" and remained selected after the call
   (`e2e-11-exception-skipped.png`), matching AC-D.11/D.19's "skipped stays selected" clause.
   Network: two `POST .../verification/verify-bulk` calls at 00:37:29 and 00:37:52 (lines 1000,
   1038), both 200 - the transcript records the first click landed on the dialog rather than the
   row and the second was the real bulk-verify attempt against MCH906. **Caveat:** the access log
   only records HTTP status, not response body, so the per-code "skipped - exceptions open"
   reason is evidenced by the screenshot (row stayed Unverified, stayed selected) and the
   transcript's own read of the UI, not by a captured JSON payload.

7. **Row click → Specifications tab, single verify, prev/next.** Clicked the MCH906 row; landed
   on the product detail page's Specifications tab (`e2e-12-product-detail.png` /
   `e2e-13-product-spec-tab.png`), with prev/next showing "5048 / 11677" and the verification pill
   reading "Unverified" with a Verify button - same state as the worklist. Clicked Verify; refused
   inline with **"Still needs a human: Height, Length, Width"** (`e2e-14-verify-exception-tab.png`),
   the pill staying "Unverified".
   Network: `POST .../verification/verify` - **409 Conflict** (line 1178, 00:38:51), matching the
   `exceptions_open` refusal in AC-D.5/D.13.
   Clicked "next"; moved to a different product (MGB5026B, "5049 / 11677") while staying on
   `?tab=specifications` (`e2e-15-next-product.png`). Clicked Verify there; it succeeded and the
   pill read **"Verified by Jayson Personal, 17/08/2026, 12:39 am"** with an Unverify control
   beside it (`e2e-16-single-verified.png`).
   Network: `POST .../verification/verify` - 200 (line 1300, 00:39:37).

8. **Edit a verified code → needs_reverify with diff.** On the now-verified MGB5026B, opened the
   Material field editor and changed it from "Glass" to "Ceramic", saved
   (`e2e-17d-editor-check.png` shows the editor mid-edit). The verification pill flipped to
   **"Needs Re-Verify"** and rendered a "WHAT MOVED SINCE IT WAS VERIFIED" block
   (`e2e-18-needs-reverify.png`).
   Network: `PUT .../by-product/{id}/values/material` - 200 (line 1482, 00:41:28).
   **Defect found (not fixed, per this task's scope):** the diff line rendered
   `Material: was [object Object], now [object Object]` instead of the scalar values (`glass`
   / `ceramic`) - confirmed directly in `e2e-18-needs-reverify.png`. Root cause per the
   transcript: the backend sends `{value: "glass"}` while the frontend's diff renderer expects a
   plain scalar. This is a real bug against AC-D.19's "returns it as needs-re-verify with the
   diff" clause - the state transition itself works, but the diff is unreadable.
   The transcript records the agent then edited Material back to "Glass" and saved again
   (`e2e-19-after-save-glass.png` shows the spec table row for Material reading "Glass" with
   source pill "Set By Hand"), tried to find a "Revert to derived" action in the row's "More
   actions" menu across several attempts (`e2e-20` through `e2e-23`, all showing the same menu
   state - the transcript notes the menu did not open as expected), then clicked Verify again.
   The pill returned to "Verified" (`e2e-24-current-verification-state.png` /
   `e2e-25-reverify.png`, "by Jayson Personal, 17/08/2026, 12:45 am").
   Network: `POST .../verification/verify` - 200 (line 1921, 00:45:41).

9. **Final cleanup - unverify.** Clicked "Unverify" on MGB5026B, confirmed via the AlertDialog
   (product-code copy). Pill read **"Unverified by Jayson Personal, 17/08/2026, 12:45 am" /
   "Withdrawn by Jayson Personal, 17/08/2026, 12:46 am"** (`e2e-26-final-unverified.png`),
   confirming AC-D.20's "preserves the original verified_by/verified_at" clause directly in the
   rendered UI.
   Network: `POST .../verification/unverify` - 200 (line 1966, 00:46:06).
   Then bulk-unverified the two remaining verified codes (`63524`, `63524D`) via
   "Unverify selected".
   Network: `POST .../verification/unverify-bulk` - 200 (line 2007, 00:46:25).
   Re-checked the worklist: **"Verified 0 of 8,820 live codes"** (matches line 2082's
   unfiltered `GET .../worklist?page=1&limit=25` - 200).

10. **375px and 1280px.** The 1280x800 viewport is what every screenshot above was taken at
    (agent-browser's default per the daemon session). Set viewport to 375px width; the worklist
    rendered with the "Verified 0 of 8,820 live codes" header, search box, and columns visible,
    no page-level horizontal scrollbar (`e2e-27-mobile-375.png`). The transcript records an
    explicit `scrollWidth === 375` assertion (no horizontal overflow) and "clean console" at that
    point.

11. **Console.** The transcript records explicit "clean console, no errors" checks after: the
    row-level verify/unverify (step 3-4), the final unverify + cleanup (step 9), and the 375px
    check (step 10). No console check is recorded around the `[object Object]` diff defect itself
    (steps 8) - the defect is a rendering/data-shape bug, not a console error, so a clean console
    at that point would not have caught it either way.

## AC-D.19 clause-by-clause

| Clause | Status | Evidence |
|---|---|---|
| Sidebar navigation from `/` to Spec Verification | EVIDENCED | `e2e-01-home.png` → `e2e-02-worklist.png`; transcript: "Spec Verification leaf renders under Product Management (AC-D.19 first assertion confirmed)" |
| Filter | EVIDENCED | `e2e-03-search-63524.png`; `GET .../worklist?...query=63524&state=unverified` - 200 (backend.log:290) |
| Verify a single row from its own row button | EVIDENCED | `e2e-04-row-verified.png`; `POST .../verify-bulk` - 200 (backend.log:413) |
| Unverify that same row, reads unverified again | EVIDENCED | `e2e-05-row-unverified.png`; `POST .../unverify-bulk` - 200 (backend.log:495) |
| Multi-select bulk verify, confirm count, rows flip | EVIDENCED | `e2e-06-confirm-verify-bulk.png` ("Verify 2 product code(s)?"), `e2e-07-bulk-verified.png`; `POST .../verify-bulk` - 200 (backend.log:555) |
| Selected code with open exception reported as skipped with reason, not failing the batch | EVIDENCED, with a caveat | `e2e-08b`, `e2e-09`, `e2e-11` show MCH906 (3 open exceptions) staying Unverified + selected after the bulk call returns 200; the skip *reason text* is not captured in any artifact (access log has no response body, no screenshot shows the per-code result toast/list) - the "not failing the batch" and "stays selected" parts are directly evidenced, the specific reason string is not |
| Clicking a product lands on Specifications tab, single Verify works, prev/next moves to next product | EVIDENCED | `e2e-12`/`e2e-13` (detail page open), `e2e-14` (409 exceptions_open inline), `e2e-15` (prev/next to a new product, `?tab=specifications` retained), `e2e-16` (single verify 200, pill flips) |
| Edit to a verified code returns it as needs-re-verify with the diff | EVIDENCED, with a real defect | `e2e-18-needs-reverify.png` shows the state transition to "Needs Re-Verify" with a diff block, but the diff values render as literal `[object Object]` rather than `glass` / `ceramic` - see step 8 above. The clause's state-machine behavior is proven; the diff's readability is not, and is a filed defect, not a pass |
| Clean console | EVIDENCED, partially | Transcript records explicit clean-console checks at three points (step 3-4, step 9, step 10); no explicit console check is recorded around the diff-defect screenshot itself |
| 375px and 1280px | EVIDENCED | 1280px is the default session viewport for every screenshot; 375px explicitly checked in `e2e-27-mobile-375.png` with a recorded `scrollWidth === 375` assertion |

## State left in the shared DB

Checked directly against the local Postgres copy of prod used for the run
(`sorento_crm_backend`, `SessionLocal`), not inferred from the transcript alone:

- `product_spec_verifications`: **0 active rows** (`invalidated_at IS NULL`), **5 total history
  rows** - consistent with a fully-cleaned-up run (history rows are expected and correct; only
  the *active* count needed to return to 0). The 5 rows are: two for `63524` (verify → manual
  unverify, twice), one for `63524D` (verify → manual unverify), and two for `MGB5026B` (verify →
  `values_changed` invalidation from the Material edit, then verify → manual unverify). This
  matches the worklist's own "Verified 0 of 8,820 live codes" reading after the run
  (backend.log:2082).
- `product_specifications` for `MGB5026B` (`product_id = 23e36570-f9ee-467b-b53e-4574f810e889`):
  the `material` value is back to its original content (`{"value": "glass"}`), **but its
  provenance now reads `source: human, evidence: "set by person38@example.com"` and `status:
  authored`**, not the presumed original `derived` status. The transcript records the agent
  looking for a "Revert to derived" action in the row's "More actions" menu
  (`e2e-20` through `e2e-23`) and not managing to trigger it before the session ended. This is a
  residual, low-impact state change left in the shared DB: the *value* the row will render is
  unchanged (still "Glass"), but the source pill will show "Set By Hand" instead of the original
  derived source for this one code, until someone either fixes it by hand or a "revert to
  derived" path is used successfully. Nothing else under `product_specifications` was touched by
  this run.

## Resolution after the run

Written after the Phase 3 review round that followed this run. The steps above are the
historical record and are not rewritten; this section records what happened to what
they found.

- **The `[object Object]` diff defect is fixed** (commit `081a4595`). The backend sends
  each side of the diff as the stored spec ENTRY (`{value, unit?}`), and the frontend
  was formatting it as a scalar. `lib/spec-readable.ts` gained `readableEntry`, which
  renders the entry's value with its unit, and both call sites use it: the worklist
  row's verification tooltip (`SpecVerificationList.tsx`) and the "what moved since it
  was verified" block on the Specifications tab (`ProductSpecificationsTab.tsx`). Two
  vitest cases pin it, one per call site, each asserting the rendered text does NOT
  contain `[object Object]` and DOES contain the readable pair.
- **The MGB5026B provenance residue is reverted.** Step 8 left `material` on
  `MGB5026B` reading `source: human / status: authored` after the Material edit was
  undone by hand, because the run ended before a revert-to-derived path was found. It
  was cleared afterwards through the supported path, `apply_spec_values` with
  `op=revert` - before: `source human`, `status authored`; after: `source derived`.
  The value the row renders was "Glass" throughout, so nothing a user sees changed;
  what changed back is the badge and the row status. No other row was touched.
- **Vitest count.** The four files held 47 tests at the time of the run, 49 after the
  `[object Object]` fix added its two, and **54** after the review round (a class-facet
  test replacing three registry-mapping ones on the service, two conflict-message
  tests, and four on the list: plural confirmation copy, the confirmed row-level
  Unverify and its cancel, and page-scoped selection across a page change).

Nothing in this section was re-driven through a browser. The defect fix and the
review-round changes are covered by vitest and pytest; the walk itself was not repeated.

## Notes on this reconstruction

- The transcript was read via targeted `grep`/Python JSON extraction of assistant `text` blocks
  only (never the full JSONL, which is ~11MB / 742 lines with embedded tool payloads).
- Every network call cited above is quoted directly from `backend.log`, not from the transcript's
  paraphrase of it, except where explicitly noted as a transcript-only claim (the wider vitest
  regression count in "Vitest" above, and the skip-reason text in the AC table).
- No new agent-browser session was opened for this reconstruction, per the assigning agent's
  instruction. No servers were started or stopped.

## Re-check after the Phase 3 fixes (84877f3f)

A short, live agent-browser re-check of the Phase 3 review fixes that had not yet been seen in
a browser: the class filter dropdown, the row-level Unverify confirmation, page-scoped selection,
and mobile layout at 375px. Driven on branch `fm/spec-pr3-verification-list` at `84877f3f`.

**Stack.** Backend `uvicorn` on `:8000`, frontend `npm run dev` on `:3050`, this worktree's local
Postgres (copy of prod, 8,820 live product codes at the time of the run). Login via
`E2E_EMAIL`/`E2E_PASSWORD` from `sorento_crm_frontend/.env.local` (values never printed).

**Environment note.** This worktree's `.env.local` / backend `.env` are provisioned for ports
`3031`/`8031` (another lane's assignment), which do not match the `:3050`/`:8000` this task was
scoped to run against. `NEXT_PUBLIC_API_URL` / `NEXTAUTH_URL` were temporarily pointed at
`:8000`/`:3050` and the backend's `CORS_ORIGINS` temporarily gained `http://localhost:3050` (both
files are gitignored, both backed up before editing and restored to their original `3031`/`8031`
values at the end of the run - confirmed by `grep` immediately after restore). No tracked file was
touched for this.

Screenshots referenced below live at
`/private/tmp/claude-501/-Users-tehjayson--treehouse-sorento-crm-732336-1-sorento-crm/42477364-f03d-4393-b62c-917551000a3a/scratchpad/`.

### 1. Class filter dropdown - PASS

Sidebar navigation from `/`: Product Management -> Spec Verification (`e2e2-00-home.png` ->
`e2e2-01-worklist.png`). Opened Filters, opened the Class combobox: all 15 real class labels
listed (Bathroom Accessory, Bathroom Furniture, Bathtub, Bathtub and Jacuzzi, Cloth Hanger,
Flexible Hose, Jacuzzi, Kitchen Sink, Seat Cover, Shower, Squatting Pan, **Tap**, Urinal,
**Wash Basin**, Water Closet) - `e2e2-02-class-dropdown.png`. Picked "Tap":
URL became `?class_label=Tap`, network call
`GET .../verification/worklist?page=1&limit=25&class_label=Tap` - 200, header changed to
"Verified 0 of 2,871 live codes", rows show Class = Tap, Filters badge reads "1"
(`e2e2-03-class-tap-applied.png`). Reopened the Class dropdown with the filter still applied: all
15 classes still listed, "Tap" the current value (confirmed in the interactive snapshot).
Cleared via the field's own "Clear selection" control: URL returned to
`/master-data-management/spec-verification` (no `class_label`), combobox back to "Any class"
(`e2e2-04-filters-cleared.png`). Console clean throughout, no errors.

### 2. Row Unverify confirmation - PASS

Clicked row-level Verify on `11X11` (first row): `POST .../verification/verify-bulk` - 200,
pill flips to "Verified", header "Verified 1 of 8,820 live codes"
(`e2e2-05-row-verified.png`). Clicked that row's Unverify: a confirm dialog appeared, heading
"Confirm unverify", copy **"Withdraw the verification on 1 product code? It reads as unverified
again and the history keeps who vouched."** (contains "1 product code") - `e2e2-06-confirm-unverify.png`.
Clicked Cancel: no `unverify-bulk` (or any `unverify`) network call fired, row still read
"Verified". Reopened the dialog, clicked Unverify (confirm): `POST .../verification/unverify-bulk`
- 200, pill reads "Unverified" again, header back to "Verified 0 of 8,820 live codes"
(`e2e2-07-row-unverified-confirmed.png`) - code left unverified, ledger back to 0 active rows.
Console clean, no errors.

### 3. Page-scoped selection - PASS

Ticked 2 rows on page 1 (`11X11`, `63522-6`): bulk strip appears, "2 selected" / "Verify selected"
/ "Unverify selected" / "Clear" (`e2e2-08-two-selected.png`). Navigated to page 2 (pagination
"Go to next page", confirmed via "Go to previous page" flipping from disabled to enabled and the
row set changing to `26-50 of 8820`): no bulk strip, no "N selected" text anywhere on the page,
all page-2 checkboxes unchecked (`e2e2-12-page2-top2.png`) - selection did not carry across the
page change. Navigated back to page 1 (rows `11X11`... reappear, confirming page 1). Ticked 1 row
(`11X11`): "1 selected" (`e2e2-14-one-selected.png`). Clicked "Verify selected": confirm dialog
heading "Confirm verify", copy **"Verify 1 product code? A code with open exceptions, or one whose
values moved while you were reviewing, is reported back as skipped."** - singular, count-correct
(`e2e2-15-confirm-verify-1.png`). Clicked Cancel: no new `verify-bulk` call fired (network log
unchanged from before the click). Console clean, no errors. Cleared the row selection afterward
(no residual verify/unverify call - the code stayed unverified, matching item 2's cleanup).

### 4. Console/errors and 375px - PASS

Console and `errors` were checked after every step above (items 1-3) and stayed clean throughout -
no unexpected warnings, no uncaught errors, at any point including the empty/no-selection page-2
state and the two confirm-dialog Cancels.

Set viewport to 375x812: `document.documentElement.scrollWidth === 375` (no page-level horizontal
overflow). Full-page screenshot shows the worklist, search box and columns rendering cleanly with
no overlap (`e2e2-16-mobile-375.png`). Opened Filters -> Class dropdown at 375px: the popover and
its option list render fully on-screen, no clipping, no overlap with the trigger, still usable
(`e2e2-17-mobile-class-dropdown.png`). Console/errors re-checked clean at 375px after closing the
popover. Restored viewport to 1280x800 before finishing.

### Cleanup and final state

`--session spec-pr3 close` at the end (own session only, never `close --all`). Killed only the
PIDs started for this re-check (frontend `npm run dev` process tree, backend `uvicorn`); confirmed
`:8000`/`:3050` free afterward. `.env.local` / backend `.env` restored to their pre-run values
(`3031`/`8031`), confirmed by `grep` immediately after restore.

DB check (read-only, `SessionLocal`, `sorento_crm_backend`):
`product_spec_verifications` - **0 active rows** (`invalidated_at IS NULL`), **6 total rows**
(one more than the prior run's 5, from this run's own verify -> cancel-then-confirm-unverify cycle
on `11X11`; the cycle left the code unverified, consistent with "Verified 0 of 8,820 live codes"
shown on screen throughout). No defects found in this re-check; all four items pass.
