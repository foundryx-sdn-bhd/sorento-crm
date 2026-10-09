# Text glossary - Phase 3 browser evidence run (UAC section E, E1-E6)

Lane `feat/text-glossary`. Stack: frontend `http://localhost:3081`, backend `:8081`, private
DB `sorento_ai_automation_tg` (PI-2609-001/002, Jiexia fixtures, `水箱`->`Water tank` and
`盖板`->`Cover plate` pre-translated). `npx -y agent-browser@0.27.0`, isolated
`--session tg-verify`, headless. Login via `E2E_EMAIL`/`E2E_PASSWORD` from
`sorento_crm_frontend/.env.local`. Navigation by sidebar clicks from `/` throughout (Procurement
> Supply Chain (nested group) > Proforma Invoices; System > Configuration > Translations).

## Route walked

`/` -> sidebar "Procurement" -> nested "Supply Chain" group -> "Proforma Invoices" ->
`/scm/proforma-invoices` -> click "PI-2609-001" -> `/scm/proforma-invoices/{id}` -> tab clicks
for General/Lines/Packing. Same path again for PI-2609-002. Admin page: sidebar "System" ->
"Configuration" -> "Translations" -> `/system-management/translations`.

## E1 - Packing tab column + dash/English states

**PASS.** Packing tab of PI-2609-001 shows a `Description (EN)` column immediately after
`Description`. Of the four packing rows:
- `座斗 S-250出水 对冲` and `座斗 横排` (untranslated at the time) rendered a `-` button,
  `aria-label="Add English for <text>"` (confirmed via `get html`, not just visible text).
- `水箱` -> `Water tank` and `盖板` -> `Cover plate` rendered the pre-seeded English directly.

No already-English source description exists in either seeded PI's fixture data, so the "R7:
an English source also shows a dash" sub-clause could not be exercised live; it is covered by
the backend's `_has_source_script` guard (proven in the pytest suite, `test_a1_...`).

Screenshot: `e1-packing-tab.png`.

## E2 - Inline edit: Enter saves, toast, sibling propagation, Escape cancels

**FAIL - real defect found**, plus one AC sub-clause not directly observable given fixture
data (see below).

Repro (row 1 of PI-2609-001's Packing tab, item 840, description `座斗 S-250出水 对冲`,
undash then saved twice to build clean before/after evidence):

1. Click the `-` button on `座斗 S-250出水 对冲` -> cell becomes a textbox.
2. `fill` "Pedestal S-250 outlet, counter-flush", press Enter.
3. Network: `PUT /api/v1/scm/proforma-invoices/{id}/translations` -> 200,
   `{"source_text":"座斗 S-250出水                对冲","target_text":"Pedestal S-250 outlet, counter-flush","source":"manual","rebound":{"lines":0,"packing_rows":2}}`.
   Immediately followed by `GET /api/v1/scm/proforma-invoices/{id}` -> 200, and that response
   body's `packing_lines[0].description_en` is correctly
   `"Pedestal S-250 outlet, counter-flush"` (confirmed via `network request <id> --json`).
4. **Defect: the cell on screen did NOT update.** It kept rendering
   `Add English for 座斗 S-250出水 对冲` (the dash button) even though the refetch that just
   landed carries the correct value. Screenshot `e2-defect-cell-not-updating.png` is this
   exact state, captured mid-repro on a second row (`座斗 横排` -> `Side-by-side pedestal`,
   same defect, same evidence pattern).
5. A full page reload (`open` the same URL) shows the correct English immediately - the data
   is genuinely persisted; only the already-mounted `DescriptionEnCell` fails to pick up the
   fresh query result while it stays mounted. Exiting the inline-edit context via the Lines
   tab's "Cancel" button (which unmounts/remounts the row in view mode) DOES show the correct
   value without a reload, which narrows this to `DescriptionEnCell` not re-deriving its
   displayed value from new props/query data on an in-place refetch, rather than a cache-key
   or invalidation bug.

   Exact repro for the coder: open any PI's Packing or Lines tab, click a dash cell, type
   text, press Enter. The `PUT .../translations` call succeeds (200, correct `rebound`
   counts) and the following `GET` response already contains the right `description_en`, but
   the cell you just edited keeps showing the `-` / "Add English for ..." placeholder until
   the page is hard-reloaded or the row remounts through some other state change (e.g.
   Cancelling out of Lines edit mode).

Toast: **confirmed correct.** A `role="status"` toast read `"Translation saved, 1 row
updated"` for the `座斗 横排` edit (which only exists on this one PI, so 1 is right).

Sibling-row propagation: **confirmed at the DATA layer, not observable as a live same-screen
update given the fixture set.** `座斗 S-250出水 对冲` happens to appear on BOTH PI-2609-001
and PI-2609-002's Packing tabs (not twice on the SAME tab - neither seeded PI has two packing
rows sharing identical description text within itself). The PUT's own response reported
`rebound.packing_rows: 2`, and after navigating to PI-2609-002 the same word showed the
correct English there too - so the backend rebind reached both rows, matching the already-
green `test_a3_rebind_updates_every_matching_row_across_two_pis` /
`test_d2_success_body_and_every_matching_row_on_file_updates` pytest coverage. What could NOT
be shown live is two rows on ONE open tab updating simultaneously without any navigation,
because no such duplicate-description pair exists in either seeded PI. Given item 4 above
already proves the CURRENT PI's own cell fails to live-update after a save, a second row on
the same tab would almost certainly show the identical symptom (same component, same data
flow) - this is asserted with high confidence but not independently observed.

Escape cancels: **PASS.** Clicked the dash on PI-2609-002 Lines tab's
`座厕 S-250出水 对冲 (天地盖）`, typed "should not save", pressed Escape. Network log showed
no `PUT .../translations` call (only an unrelated notifications poll). The cell reverted to
the `-` placeholder, discarding the typed text.

## E3 - Lines tab: same column, view + edit mode, does not dirty the line form

**PASS.** Lines tab of PI-2609-002 shows `Description (EN)` in the same position (right after
`Description`, before `Qty`) in both view mode and inside the invoice's own Edit mode (opened
via header "More actions" -> "Edit", which shows `Cancel` / `Save proforma invoice` and turns
`Item code`/`Description`/etc. into real form inputs). The `Description (EN)` cell in edit
mode is still the SAME dash/English button, not a plain input tied to the line's own draft
state. Editing it (`座厕 S-250出水 对冲 (天地盖）` -> "Toilet unit, counter-flush (paired
lid)") fired only `PUT .../translations` (not any of the line-form's own save endpoints), and
the surrounding form's `Save proforma invoice` / `Cancel` buttons were unaffected - clicking
`Cancel` afterwards returned to view mode WITHOUT reverting the translation (it is written
immediately via its own path, independent of the line form's dirty/save state), confirming
the edit does not dirty the line form.

## E4 - System Management > Translations: admin edit reaches the PI; no Text Glossary entry

**PASS.** `System` (sidebar) -> `Configuration` -> `Translations` -> `/system-management/
translations`. The admin grid listed all three words typed during E2/E3 (`座厕 S-250出水 对冲
(天地盖）`, `座斗 横排`, `座斗 S-250出水 对冲`), each with `Source kind = manual` and the
correct `By` (the signed-in user).

Edited `座斗 横排`'s English inline (`Side-by-side pedestal` -> `Twin pedestal, side layout`)
-> `PUT /api/v1/system/translations/{id}` -> 200. Navigated back to PI-2609-001's Packing tab
via the sidebar (a fresh page load, not an already-open tab) and its row now read
`Twin pedestal, side layout` - the admin edit reached the PI's cell on next load.

No `Text Glossary` entry anywhere in the sidebar (checked via a full-tree grep across every
expanded group visited) - R8/R11 confirmed: System > Translations is the only glossary
surface.

Screenshot: `e4-admin-edit-reflects-on-pi.png`.

## E5 - 375px and 1280px, Packing and Lines tabs

**PASS**, both tabs, both widths. At 1280px both tabs render with no clipping or overlap
(`e5-packing-1280.png`, `e5-lines-1280.png`). At 375px the DataGrid uses the app's standard
mobile pattern - a horizontally-scrollable table (`overflow-x-auto` on the grid's own
wrapper, confirmed via `getComputedStyle`) rather than a fixed layout that clips; scrolling
the grid (not the page) brings `Description (EN)` fully into view with clean ellipsis
truncation, no cut-off glyphs or overlapping cells
(`e5-packing-375-scrolled.png`, `e5-lines-375-scrolled.png`).

## E6 - No UUIDs, no on-screen explanation text

**PASS.** Ran `document.body.innerText.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi)`
against the rendered text of: PI-2609-001 Packing tab, PI-2609-001 Lines tab, and the System >
Translations admin list - zero matches on all three (the URL bar carries the invoice's UUID,
which is expected and outside the UI surface the rule governs). No feature-explanation copy
(checked for common phrasing patterns) found on any of the three pages; the only inline text
next to the feature is the functional `aria-label="Add English for <word>"` on the dash
button, which names the action rather than explaining the feature.

## Incident: browser session hang mid-run

Partway through (after the System > Translations edit, before returning to verify E4's PI
cell), every `agent-browser` command against `--session tg-verify` started timing out
(120s) and one call finally returned `Resource temporarily unavailable (os error 35) (after 5
retries - daemon may be busy or unresponsive)`. `ps aux` showed this session's own Chrome
renderer process pegged at ~105% CPU continuously (accumulating 8-9 minutes of CPU time),
consistent with the pre-existing (unrelated to this lane) React warning seen earlier in
`console`: `Each child in a list should have a unique "key" prop... Check the render method
of Demo1Layout`. Recovered by closing only this session (`agent-browser --session tg-verify
close`, confirmed all of that session's Chrome processes exited) and reopening + re-logging-in
fresh - no other agent's session was touched (`close`, not `close --all`). Flagging this
because if it recurs across lanes it is worth a captain-level look at `Demo1Layout`; it is not
scoped to the text-glossary feature itself (nothing in this lane touches that layout
component) and did not block the rest of the verification once recovered.

## Summary

| AC | Result |
|----|--------|
| E1 | PASS |
| E2 | **FIXED, see below** - inline-edited cell does not show the new English until a hard reload / remount, despite the PUT + refetch both carrying the correct value; toast and Escape-cancel both correct; cross-PI rebind confirmed at the data layer only |
| E3 | PASS |
| E4 | PASS |
| E5 | PASS |
| E6 | PASS |

E2 was the one defect blocking a clean pass; fixed and re-verified live below. Everything else
in section E holds up under a real, headless-browser, sidebar-navigated pass with network/console
inspection.

## E2 root cause and fix (follow-up round, same day)

Root cause: `useProformaInvoicePacking`'s `queryFn` derived `description_en` synchronously off
the `invoice` argument's CLOSURE (whatever `ProformaInvoicePackingTab`/`ProformaInvoiceDetail`
last rendered with), not the query cache. `useProformaInvoiceTranslation`'s save invalidated the
detail key and the packing key together; the packing key's synchronous derivation could run and
mark itself fresh BEFORE the detail key's real network refetch landed, using the pre-save
closure. Once fresh, react-query does not refetch a query again just because time passes or the
parent re-renders - so the cell was stuck on the dash (or the old English) permanently, until
some UNRELATED invalidate (e.g. Cancelling out of Lines edit mode) forced another refetch that
happened to run after the detail data had already updated.

Fix (`app/(protected)/scm/hooks/useProformaInvoicePacking.ts`,
`app/(protected)/scm/hooks/useProformaInvoiceTranslation.ts`): the packing `queryFn` now reads
the detail query's CACHE via `qc.getQueryData` at call time instead of the closure, and the
translation mutation's `onSuccess` awaits the detail invalidation to full settlement before
invalidating the packing query. Both are necessary: the cache read removes the render-timing
dependency, and the awaited order guarantees the cache is already correct by the time the
packing query is asked to re-derive. Neither change touches `DescriptionEnCell` itself (it
already rendered whatever `descriptionEn` prop it was given) or the Dismiss/Match invalidation
paths, which were not reported broken.

### Live re-verification, :3081/:8081

1. **Packing tab, PI-2609-001, row 1** (`座斗 S-250出水 对冲`, already carrying prior evidence's
   "Pedestal S-250 outlet, counter-flush"): clicked the cell, changed the text to "Retry test
   pedestal", pressed Enter with the backend STOPPED (see the savingRef re-arm check below) -
   failed, then pressed Enter again once the backend was back. `PUT .../translations` -> 200,
   and the cell repainted to the button "Retry test pedestal" IMMEDIATELY, no reload, no
   remount. Screenshot `e2-savingref-retry-success.png`.
2. **Cross-PI propagation on load**: navigating (sidebar/record-nav, not a reload) to
   PI-2609-002's Packing tab, its row 1 (same description `座斗 S-250出水 对冲`) already read
   "Retry test pedestal" too - the rebind reached the sibling invoice.
3. **Lines tab, PI-2609-002**, a genuinely untranslated row (`座厕 S-250出水 对冲 （四方纸箱）`,
   dash button "Add English for..."): clicked, typed "Toilet unit, counter-flush (square
   carton)", Enter. `PUT .../translations` -> 200, and the cell repainted to the new English
   IMMEDIATELY, same tab, no reload. Screenshot `e2-fix-lines-tab-live-update.png`.
4. `console`/`errors`: no warnings or uncaught errors from either interaction.

Two rows sharing an identical description on the SAME open tab still could not be exercised
live (same fixture-set limitation as the original run) - not independently observed, but the fix
removes the exact mechanism (a permanently-stale packing query) that would have blocked it, and
every row on a tab is driven by the SAME query, so a fix that repaints one repaints all of them
on the next render.

### Savingref re-arm re-check (fail then retry succeeds)

Separately re-confirmed the `savingRef`/`onSettled` re-arm fix from `46bfb3520` still holds
after the E2 fix: PI-2609-001 Packing tab, row 1, backend stopped, typed "Retry test pedestal",
Enter -> toast "Internal Server Error", cell stayed in edit mode with the typed text retained
(screenshot `e2-savingref-retry-fail-toast.png`). Backend restarted, same cell (still showing
the textbox, unchanged), Enter again -> `PUT .../translations` -> 200 this time, cell exited
edit mode and repainted with the new English (the E2 fix in action, item 1 above). No dead
cell, no need to reload.

### Vitest added

`DescriptionEnCell.test.tsx`'s "Enter saves" case now also rerenders the SAME tree (not a fresh
mount) with an updated `descriptionEn` prop after the mutation resolves, and asserts the English
shows - pinning that the cell itself was never the bug (it already renders whatever prop it is
handed); the fix lives in the two hooks. 80/80 across the three touched vitest files
(`DescriptionEnCell.test.tsx`, `ProformaInvoicePackingTab.test.tsx`,
`ProformaInvoiceDetail.test.tsx`) - fixing the E2 race also surfaced (and required fixing) a gap
in `ProformaInvoiceDetail.test.tsx`'s own mock of `useProformaInvoices`, which never exported
`proformaInvoiceDetailQueryKey`: harmless while nothing read it, but the new cache-read in
`useProformaInvoicePacking` reads it on every fetch, so the Convert-to-packing-list dialog and
the Packed-cell test both started throwing inside their `queryFn` and rendering zero rows. Added
the real key-builder to that mock.
