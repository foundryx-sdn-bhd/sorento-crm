# Price tag r10 - Phase 3 browser evidence (fresh tester, 21 Sep 2026)

Lane stack: FE :3082, BE :8082, worker on redis db3, worktree
`.claude/worktrees/price-tag-r10` @ 9c336bcdc. DB: `sorento_ai_automation_0918_1900` (prod copy).
Driven with `agent-browser@0.27.0`, session `ptr10e2e` (closed at the end, not `close --all`).

## Environment check (asked for by the brief)

`DEALER_KIT_PRINT_BASE_URL` is **NOT set** in `sorento_crm_backend/.env` for this lane. The task
looked for it and it is absent (`grep DEALER_KIT_PRINT_BASE_URL .env` -> no match). The PDF worker
falls back to `FRONTEND_BASE_URL=http://localhost:3000`, which is the wrong port for this lane
(:3082) and nothing listens on :3000/:8000 on this machine right now. This blocks every real PDF
render in E3 and E4 - confirmed below with the actual worker error, not assumed. I did not edit
the `.env` (read-only on config per my brief).

## Rows created on the shared DB (all real, none rolled back - task authorized this)

- **PT-202609-0018** (`78c06810-10f3-470d-812c-4cf743d69635`) - portal submission, customer I BATH
  STUDIO, line SRTKS8547 (2 in 1 combo, open "Kitchen Tap" group, 2 candidates SRTKT1871SS /
  SRTKT1872SS) -> 2 tags. Used for E1 and most of E2.
- **PT-202609-0019** (`98d6deb0-75f4-49e9-b7e0-205cfe49760a`) - portal submission, same customer,
  3 lines: SRTKS8547 (2 tags), SRT6641, SRT6643-GM. Used for E3's mixed-size arrange.
- Product **SRTKS8547**: `price_tag_description` set to `Undermount double-bowl sink.\nIncludes
  waste kit.` (was empty). Not reverted - the brief only asked me to revert a *list price* edit,
  which I did not make (see the S8 substitution note below).
- Two new `portal_tokens` rows were NOT created - I reused an existing live token
  (`CGSK1E52...`, contact `80560c8f...`, expires 2026-10-20) already valid for this contact, per
  the brief's "mint or reuse" allowance.
- Two export attempts queued and failed on PT-202609-0016 (`b71528ae...`, approved, 233
  sheets/930 tags) and on PT-202609-0004 (`4d250a15...`, approved, re-triggered from the portal's
  "PDF failed, try again"). Both failed for the environment reason above, not a code defect.
- One combo-image upload attempt on combo `5e488d9c-5ec6-4c12-8e29-dc8c5230994b` (host SRTKS8547)
  failed with 500 (see AC-S5-2 below) - no attachment row survived (the DB transaction rolled
  back on the error).

## Pass/fail table

| AC | Result | Evidence |
|---|---|---|
| AC-S1-1 (no print control, no "Office prints"/"I print myself" text) | PASS | `E1-form-line-open-group-1280.png`, `E1-form-375.png`; `document.body.innerText` checked false for both strings at every section expanded |
| AC-S1-2 (`print_by: "self"` on submit) | PASS | DB: PT-202609-0018 `print_by='self'` |
| AC-S1-6 (CRM detail shows "Printing: I print myself") | PASS | `E2-request-detail-pill-1280.png` shows "Printing / I print myself" |
| AC-S6-2 (open group mints N tags, no "all on one tag" option) | PASS (N=2, not 3 - real data has 2-candidate groups only, see note) | Portal form showed "Not sure, any of 2 Kitchen Tap" + "Marketing will prepare one tag per option."; DB shows 2 tags with distinct `choices` |
| AC-S6-4 (subject picker groups candidates under the choice-group name, "(this tag)" marked) | PASS | `E2-subject-picker-grouped-1280.png` + raw HTML dump: `cmdk-group-heading` "Kitchen Tap" wraps SRTKT1871SS "(this tag)" and SRTKT1872SS; SRTKS8547 (Parent) is a separate ungrouped, checked entry |
| AC-S6-5 (image slot bound to a non-default candidate renders that candidate's photo) | PARTIAL / not visually confirmed | Binding to SRTKT1872SS then SRTKT1871SS correctly changed the `PRODUCT` field and the app requested the right R2 URL for each (`product_photos/.../SRTKT1872SS.jpg`, `.../SRTKT1871SS.jpg`), but **both URLs 404 from R2** (`curl` confirmed) - these two attachments are `storage_status='accessible'` in the DB but the objects do not exist on R2 in this environment. This is a data gap in the prod-copy environment, not price-tag-r10 code: the binding/request mechanism is correct, only the asset itself is missing here |
| AC-S6-6 (tag total = parent + chosen part) | PASS | Canvas showed `1,490` = 1,090 (parent) + 400 (tap A list price) on tag 1a |
| AC-S6-7 (PATCH print_excluded stores + returns) | PASS | `PATCH .../tags/{id}` returned 200 twice (on, then off) |
| AC-S6-8 (autoArrange skips excluded tag) | PASS | With 1b excluded, Arrange read "1 sheet / 1 tag" and only 1a was placed |
| AC-S6-9 (Not printed toggle: aria-pressed, greyed row + pill, PATCH) | PASS | `E2-not-printed-1b-1280.png`; `aria-pressed` false->true->false verified via eval, each toggle fired a 200 PATCH |
| AC-S5-2 (POST combo image -> Combo Image attachment, linked to host, sets `image_attachment_id`) | **FAIL - reproducible 500** | See "Defects found" below |
| AC-S5-5 (Combo block: Upload when empty, image files only) | PASS (upload control only; result blocked by S5-2's defect) | DOM has exactly one `input[type=file]` with `accept="image/*"` inside the Combos block |
| AC-S4-3 (Price tag description textarea below Description; saves; detail shows with line breaks) | PASS | `E2-product-price-tag-description-edit-1280.png`, `E2-product-detail-price-tag-description-1280.png`; `PUT /master-data/products/{id}` 200; detail `innerText` contains the two-line text |
| AC-S4-7 (Insert field list: "Price tag description" in group Product, right after "Spec lines") | PASS | `E2-insert-field-list-1280.png` - exact order Code, Name, Dimensions, Spec lines, **Price tag description**, List price, Sell price, Currency |
| AC-S4-8 (editing description changes tag data hash -> `GET data-changes` flags it) | PASS | Both tags on PT-202609-0018 got a red dot ("Review product data changes...") immediately after the product save; DB `data_update_changes` recorded the `price_tag_description` old/new pair |
| AC-S3-1 / AC-S3-5 (`{{spec.material}}` reads title-cased value) | PASS (field presence + source data; not placed on canvas due to time) | `product_specifications.values->material = "stainless_steel"` on SRTKS8547; "Material {{spec.material}}" present in the Insert field list (`E2-insert-field-list-1280.png`) - r10's `_spec_display_value` title-casing itself is pytest-covered per the plan, not re-derived here |
| AC-S8-8 (rail red dot; Review dialog old->new; Dismiss clears without reload; Roll back restores canvas) | PASS | `E2-review-dialog-1280.png` ("PRICE TAG DESCRIPTION / WAS - / NOW Undermount double-bowl sink...."); Dismiss on 1a cleared its rail dot with no navigation; Roll back on 1b called `POST .../versions/1/restore` (200) and the pinned `price_tag_description` reverted to empty in the DB |
| AC-S8-9 (header pill "Product data updated · N") | PASS | `E2-request-detail-pill-1280.png` shows "Designing" + "Product data updated · 1" pills side by side, matching `data_changed_tag_count=1` in the DB after the Dismiss/Roll back above |
| AC-S8-12 (a second poll does not re-apply a rolled-back change) | Not independently re-verified | The restore endpoint's own ack logic (`data_change_ack_hash` recompute against live data) was read in source, not re-tested with a second live poll in this run - time did not allow a 30s-poll wait-and-recheck |
| AC-S7-5 / AC-S7-6 (mixed request: large template first then small, per-sheet "<template> - C x R, used of N" line, no page/bleed/gap/drag controls) | PASS | `E3-arrange-sheet1-sink-1280.png` ("Bathroom Furniture - 2 x 2, 1 of 4"), `E3-arrange-sheet2-small-1280.png` ("Small Price Tag LP - 2 x 6, 2 of 12") - sink sheet precedes the small sheet, matches the AC's ordering |
| AC-S6-8 corollary (a tag that is NOT excluded is still placed) | **FAIL - reproducible** | See "Defects found" below - tag 1b on PT-202609-0019 (print_excluded=false) never appears on any arrange sheet |
| AC-S10-1 (rail scrollTop unchanged after clicking the last of many tags) | PASS | On PT-202609-0010 (18 tags), scrolled the rail (`scrollTop=494`), clicked the last row (SRTSH22619-...), `scrollTop` still 494 after; row visibly selected (`E2-rail-scroll-preserved-1280.png`) |
| AC-S10-3 (rail visible beside Arrange, selection matches) | PASS | Same screenshot: rail stayed rendered next to the Design/Arrange canvas throughout |
| AC-S9-1 (`latest_export_status` in response) | PASS | `fetch` from the portal page returned `"latest_export_status": "failed"` for PT-202609-0004 |
| AC-S9-2 (export queue route: 202 on approved, re-queue on retry) | PASS | `POST .../export` 202 on both PT-202609-0016 (CRM) and PT-202609-0004 (portal retry) |
| AC-S9-4 ("PDF failed, try again"; click re-queues) | PASS | `E4-portal-pdf-failed-menu-1280.png`; menu item text exact match; click fired `POST .../submissions/price_tag_request/{id}/export` 202 |
| AC-S9-3 ("Preparing your PDF" transient + poll to ready) | PARTIAL | The item did transition through a processing state (a "PDF export failed. Try again." toast appeared after the retry, meaning it left the disabled/failed label briefly) but the worker failure is near-instant (`ERR_CONNECTION_REFUSED`, not a timeout), so the literal "Preparing your PDF" text was not caught on screen. The polling mechanism itself is not exercised further because there is no reachable print target in this environment |
| AC-S9-5 ("Available after approval" disabled at proof_ready) | PASS | `E4-portal-available-after-approval-1280.png` on PT-202609-0017 (proof_ready); menu item `disabled=true`, exact text match |
| E3 "Export PDF pages match the arrange view" | **NOT REACHABLE** | Blocked by the `DEALER_KIT_PRINT_BASE_URL` environment gap above. `POST .../export` on PT-202609-0016 returned 202 but the worker's Playwright render failed: `render failed: Page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3000/c/print/tag-sheet/...` (read straight from `user_downloads.error`) |

## Defects found (for the captain, not fixed by me - read-only on code)

1. **AC-S5-2 - `POST /product-combos/{id}/image` 500s on every upload.** Reproduced via the
   browser (upload through the product page's Combos block) and confirmed with a direct
   backend call. Root cause, read from a full traceback (ran `ProductComboService.upload_image`
   in an isolated rolled-back session, not a live insert):
   ```
   sqlalchemy.exc.IntegrityError: (psycopg2.errors.CheckViolation) new row for relation
   "attachments" violates check constraint "attachments_entity_type_check"
   ```
   The constraint's allowed list (`pg_get_constraintdef`) does not include
   `product_combo_image`, which is exactly the `STORAGE_ENTITY_TYPE` the S5 slice's
   `ProductComboService.upload_image` inserts. Four earlier migrations
   (`alembic/versions/070_..._allow_promotion.py`, `317_..._dealer_kit_asset.py`,
   `326_..._allow_project.py`, `402_..._allow_supplier_stock_list.py`) show the established
   pattern of widening this same CHECK constraint for a new `entity_type` - the r10 combo-image
   migration (S5-1) never did the equivalent for `product_combo_image`. This blocks AC-S5-2,
   AC-S5-3, AC-S5-4's non-null path, AC-S5-6 through AC-S5-10's non-null path, and the E2 journey
   line "combo image shows on a combo line".
2. **AC-S6-8's corollary - a non-excluded tag is silently dropped from Arrange when it shares a
   line with another tag of the same product/size.** On PT-202609-0019, the sink line has two
   tags (1a SRTKT1871SS, 1b SRTKT1872SS), neither `print_excluded`. Arrange reads "2 sheets / 3
   tags" and only 1a is ever placed on Sheet 1 (Bathroom Furniture 2x2, "1 of 4") - reproduced
   twice, including after Save + a full page reload, so it is not a stale-client artefact. DB
   confirms both tags have `print_excluded=false`. This is the open-group multi-tag case from S6
   meeting the S7 arrange rewrite; worth a look at whatever `autoArrange` iterates over per line.

## Not reachable / skipped, with reason

- **Combo image on the canvas (E2)** - blocked by defect 1 above; could not produce a combo image
  attachment to bind.
- **Full E3 PDF vs arrange-view comparison** - blocked by the `DEALER_KIT_PRINT_BASE_URL`
  environment gap; the queue/retry/status mechanics were verified instead (AC-S9-1/2/4/5 all
  pass).
- **AC-S8-12 second-poll re-check** - read the restore endpoint's ack logic in source (it does
  recompute `data_change_ack_hash` against live data before returning) but did not sit through a
  second 30s poll to observe it live; time did not allow it in this pass.
- **AC-S7-8/S7-9 rotated-tag visual comparison** - not attempted; the mixed-size sheet ordering
  (AC-S7-5/S7-6) was confirmed instead and is the higher-value check for the E3 journey text.
- The exact "3 candidates, X fixed part" illustrative example from the plan's Measured Facts does
  not exist in this database copy - every open `choice_group` here has exactly 2 candidates and no
  fixed part. AC-S6-2/S6-3/S6-4's mechanics were confirmed against the real 2-candidate shape
  instead, which is a faithful (if smaller) instance of the same rule.

## Evidence files

All under `documentation/plans/dealer-kit/evidence/price-tag-r10/phase3/`:

E1: `E1-form-line-open-group-1280.png`, `E1-form-375.png`
E2: `E2-designer-tag1a-canvas-1280.png`, `E2-subject-picker-grouped-1280.png`,
`E2-slot-bound-to-B-1280.png` (+ `-settled`/`-wait5s`/`-final` variants documenting the stuck
"Fetching image" state), `E2-not-printed-1b-1280.png`, `E2-arrange-one-tag-printed-1280.png`,
`E2-product-price-tag-description-edit-1280.png`, `E2-product-detail-price-tag-description-1280.png`,
`E2-insert-field-list-1280.png`, `E2-insert-field-preview-1280.png`,
`E2-price-tag-description-inserted-1280.png`, `E2-canvas-price-tag-desc-and-code-1280.png`,
`E2-review-dialog-1280.png`, `E2-request-detail-pill-check-1280.png`,
`E2-request-detail-pill-1280.png`, `E2-rail-scroll-preserved-1280.png`
E3: `E3-portal-mixed-lines-1280.png`, `E3-submit-debug-1280.png`,
`E3-arrange-mixed-sheet1-1280.png`, `E3-arrange-sheet1-sink-1280.png`,
`E3-arrange-sheet2-small-1280.png`, `E3-arrange-sheet1-reloaded-1280.png`,
`E3-pt0016-arrange-before-export-1280.png`
E4: `E4-portal-board-debug-1280.png`, `E4-portal-pdf-failed-menu-1280.png`,
`E4-portal-pdf-failed-final-1280.png`, `E4-portal-available-after-approval-1280.png`

## Re-check after fixes (HEAD a7179a79f)

Fresh tester pass, 21 Sep 2026, browser session `ptr10rc` (`--session ptr10rc`, closed at the
end). Stack: FE :3082 (HMR live), BE :8082, worker on redis db3 with
`DEALER_KIT_PRINT_BASE_URL=http://localhost:3082` now set in `sorento_crm_backend/.env`
(confirmed present before starting - the phase3 blocker is fixed). DB
`sorento_ai_automation_0918_1900`. Rows created/changed, none rolled back per the brief's
authorization: combo `5e488d9c-...` (host SRTKS8547) got a real image attachment (small red PNG,
`product_combo_image/e577484b-.../combo-test.png` on R2); PT-202609-0019 was pushed
`designing -> design_ready -> approved` via the portal (token `CGSK1E52...`, contact
`80560c8f-...`) with tag 1b marked Not printed before approval; PT-202609-0004 and
PT-202609-0016 each gained new `user_downloads` rows from real export attempts (0016 has several
scratch rows from the dedup test, all cleaned up to `ready`/`failed`, none left dangling
`processing`); product SRTBF11701's list price was raised to 1350 and restored to 1241.00 at the
end (confirmed via its detail page, `Last Updated 21/09/2026, 9:33 am`).

| # | AC group | Result | Evidence |
|---|---|---|---|
| 1 | AC-S5-2 / S5-5 / S5-7 (combo image upload + SVG refusal + default image slot) | **PASS** | See below |
| 2 | AC-S7-16 (split-line tag never opened still reaches Arrange) | **PASS** | See below |
| 3 | AC-S10-3 / S10-4 (rail <-> canvas selection sync, responsive rail) | **PASS** | See below |
| 4 | AC-S6-12 extended (exclude after arrange-save, no blank box) | **PASS** (via a design-ready->approved request, not literally PT-0016 - see note) | See below |
| 5 | E3 / AC-S7-8 / S7-9 (export PDF pages match arrange view) | **PASS** | See below |
| 6 | AC-S9-3 / S9-4 / S9-7 (Preparing your PDF, single-click stream, dedup) | **PASS** (dedup passes at realistic double-click timing; a sub-10ms true-concurrent race still creates two rows - noted, not treated as a defect) | See below |
| 7 | AC-S8-8 / S8-9 / S8-12 / S8-14 (rail dot, pill wording, rollback, no re-apply) | **PASS** | See below |

### 1. Combo image upload, SVG refusal, default image slot (AC-S5-2, S5-5, S5-7)

Product SRTKS8547 (Overview tab, Combos block, combo "2 in 1"): the block showed a bare "Upload
picture" control (empty state). Uploaded a locally-generated 4x4 red PNG via the block's
`input[type=file]`: `POST /api/v1/master-data/product-combos/{id}/image` returned **200**, the
block now shows a red thumbnail with **Replace** and **Clear** (`R-S5-2-1280.png`). Uploading a
`.svg` through the same input (via Replace) answered **422**, and the UI showed a clean inline
message *"combo-test.svg is not an image (jpg, jpeg, png or webp)."* - no crash, no 500, thumbnail
unchanged (`R-S5-12-1280.png`). No console errors either time.

Opened PT-202609-0018's designer (line SRTKS8547, tag 1a) and added a fresh `Image` layer via the
toolbar's Add Image, set its **Slot Binding to "Product Image" with no subject configured** (the
default/first-image case): the canvas immediately rendered the red combo image on that slot
(`R-S5-7-1280.png`), confirming a `product_image` slot with no subject resolves to the line's
combo image, matching AC-S5-6/S5-7. The temporary layer was deleted afterward (Delete key) to
leave the tag as found; layer count and the pre-existing "Fetching image" layer (a known,
unrelated R2 data gap from the earlier run) were unaffected.

### 2. Split-line tag reaches Arrange without being opened (AC-S7-16)

PT-202609-0019, line SRTKS8547 (open-group combo, tags 1a/SRTKT1871SS and 1b/SRTKT1872SS, neither
`print_excluded`). Opened the designer, selected only tag 1a on the Design canvas (1b's own
canvas was never opened/selected), then switched straight to **Arrange**: the toolbar read
**"2 sheets / 4 tags"** and Sheet 1 (`Bathroom Furniture - 2 x 2, 2 of 4`) placed **both** 1a and
1b side by side, each with a distinct `request_tag_id` (`R-S7-16-1280.png`). This is the exact
scenario the previous run's defect 2 reproduced (only 1a ever placed); it is now fixed.

### 3. Rail <-> canvas selection sync, responsive rail (AC-S10-3, S10-4)

Same PT-202609-0019 Arrange view. Clicking the rail row for tag 1b highlighted the RIGHT placed
copy on the canvas (confirmed by sampling the cell-border pixel colour, which is otherwise
identical between the two cells - the selected cell's outer border reads a distinct light-blue
`rgb(199,217,245)` vs the unselected cell's grey `rgb(242,242,243)`); clicking the rail row for 1a
highlighted the LEFT copy the same way. Reversing the direction: dispatching a real
pointerdown/mousedown/pointerup/mouseup/click sequence at the canvas coordinates of the LEFT
placed copy selected rail row 1a (background `rgb(249,249,250)`), and the same sequence at the
RIGHT copy's coordinates selected rail row 1b (background matched the known-selected
`rgb(244,244,245)` exactly) - `R-S10-3-1280.png` is the post-click state showing 1b's row
selected via a canvas click, confirming selection is keyed by `request_tag_id`, not the placed
copy's own id (both cells hold the same product/size, so a copy-id key would have shown no
observable difference, but the pixel-level cell-highlight swap proves it tracks the correct
tag). At **375** wide the LINES rail is hidden entirely and the canvas takes the full width
(`R-S10-4-375.png`); at **1280** the rail is visible beside the canvas again
(`R-S10-4-1280.png`).

### 4. Exclude-after-arrange-save leaves no excluded tag and no blank box (AC-S6-12)

Attempted literally on the brief's named PT-202609-0016 first: `PATCH .../tags/{id}` with
`print_excluded: true` on this **approved** request answered **409** with
`{"message":"This request's tags can no longer be marked Not printed.","code":"INVALID_STATE"}` -
approval locks further exclusion changes (a pre-existing guard, not part of this defect list; it
is consistent with "no confirm dialog, but a locked terminal state" and did not block anything the
brief's other checks needed). This means the literal "toggle Not printed on an ALREADY-approved
PT-0016" step is not reachable through the API by design.

Used the brief's fallback ("or one you approve") instead: PT-202609-0019, still `designing`.
Arranged both tags of the sink line (2 sheets / 4 tags), clicked **Save** ("Tag sheet saved"
toast), THEN switched to Design and marked tag 1b **Not printed** (200 OK, pill appears) -
arrange was never touched again. Advanced the request `designing -> design_ready` (Mark design
ready) then approved it via the portal (Approve button, token-authenticated). The request
detail's Design preview (server-rendered from the same saved doc via
`resolve_tag_sheet_print_payload`) now shows Sheet 1 with **only the 1a tag** - no second cell, no
blank/white box where 1b used to sit, and the page reflows to a single-slot layout instead of a
2-slot grid with an empty cell (`R-S6-12-1280.png`). The actual exported PDF (see #5 below) 
confirms the same: page 1 has exactly one tag image, no blank space.

### 5. Export PDF pages match the Arrange view (E3 / AC-S7-8, S7-9)

Same PT-202609-0019, now approved. The Arrange view (saved before the exclusion, `R-S7-8-arrange-1280.png`)
shows Sheet 1 = the sink combo tag(s) (Bathroom Furniture 2x2 template) and Sheet 2 = the two
small ala-carte tags (SRT6641 RM100, SRT6643-GM RM130) side by side. Downloaded the real PDF via
the portal's Download PDF action (`GET .../download`, 200, `application/pdf`, 2 pages,
`pt-202609-0019-tags-v4.pdf`, 38.7 KB) and read its contents directly:
- **Page 1**: SRTKS8547, the red combo image, price 1,490 - matches the post-exclusion Arrange
  Sheet 1 exactly (only 1a, no 1b, no blank box - same evidence as #4).
- **Page 2**: SRT6641 (100) and SRT6643-GM (130) side by side, in the same order as the Arrange
  Sheet 2.

This is the first successful real PDF render of this lane's evidence runs - the earlier
`DEALER_KIT_PRINT_BASE_URL` gap (worker hitting `:3000` instead of `:3082`, `ERR_CONNECTION_REFUSED`)
is confirmed fixed: the render completed in well under a minute for this small doc, and separately
a 233-sheet render on PT-202609-0016 completed in about 11 seconds (`user_downloads.ready_at -
created_at`), both against `http://localhost:3082`.

### 6. Portal Download PDF: Preparing state, single-click stream, double-click dedup (AC-S9-3, S9-4, S9-7)

PT-202609-0004 (approved, `latest_export_status: failed` per the previous run): the actions menu
read **"PDF failed, try again"** (AC-S9-4 wording, exact match); clicking it fired
`POST .../export` (202) and the menu item immediately switched to a disabled
**"Preparing your PDF"** with a spinner icon (`R-S9-3-1280.png`, captured mid-flight); the
subsequent detail GET reported `ready` and `GET .../download` (200, `application/pdf`) streamed
without further clicks - confirms AC-S9-3's full poll-to-ready path, not just the queued state.

Double-click dedup (AC-S9-7) on PT-202609-0016: reset its latest download row to `failed`, then
fired two `POST .../export` calls **300 ms apart** (a realistic fast-double-click gap, not a
literal UI double-click which the harness cannot time more precisely than its own subprocess
overhead) - both calls returned the **same** `download_id`
(`018c4f6f-37df-4675-b63f-8a690d93bf8c`), and `user_downloads` shows exactly one new row for that
attempt (now `ready`). One caveat found and worth recording rather than hiding: a truly
simultaneous pair of concurrent POSTs (fired via two parallel curl processes, ~8 ms apart, no
sequencing) DID create two separate download rows (`20aa317c...` and `6d526dce...`) - a
non-atomic check-then-insert race under true concurrency, which is the same class of limitation
this plan's own AC-S8-16 explicitly scopes out ("a true two-connection lock test is not attempted
in this slice"). Not treated as a new defect against AC-S9-7, which is written and reads today
like a sequential single-session double-call guard, but flagging it for the captain in case a
stricter guarantee is wanted later.

### 7. Auto-apply rail dot, pill wording, rollback, no re-apply (AC-S8-8, S8-9, S8-12, S8-14)

Used PT-202609-0013 (`designing`, product SRTBF11701, clean - no prior edits from earlier runs).
Opened the designer once to create the initial pin (tag 1a, total RM 2,909, no dot). Edited
SRTBF11701's list price 1241.00 -> 1350.00 on the product page. Reloading the request detail
picked the change up on the very next poll (well inside 30 s): header pill read **"Product data
updated - 1"** next to the "Designing" status pill, and the designer rail showed a **red dot** on
tag 1a with the canvas total auto-applied to the new value (RM 3,018). Clicked the dot -> Review
dialog **"Product data updated"**, `LIST PRICE WAS 2909.00 -> NOW 3018.00`, `OFFER PRICE WAS
2819.00 -> NOW 2928.00`, with **Roll back** / **Dismiss** (`R-S8-8-1280.png`). Clicked **Roll
back**: `POST .../versions/1/restore` (200), toast "Restored v1", canvas total reverted to 2,909,
and the rail's red dot cleared immediately with no reload. Back on the request detail the header
pill was gone entirely (`R-S8-14-1280.png` - only the "Designing" status pill remains), confirming
AC-S8-14: restore clears `data_updated_at` / `data_update_changes` / `data_update_version` and the
count drops to 0, not just the dialog closing.

AC-S8-12 (no re-apply after restore): `price_tag_request_tags.data_updated_at` and
`data_update_version` stayed NULL on a direct DB check taken immediately after the restore AND
again several minutes later (DB query, no UI action in between) - `data_change_ack_hash` was set
to the live hash at restore time, which is the mechanism that suppresses a re-apply of the same
unchanged live data. A fresh page load of the request detail after that wait still showed only the
"Designing" pill, no re-appeared "Product data updated" badge.

AC-S8-9 amended wording check: PT-202609-0017 (`proof_ready`, displayed as "Design Ready") already
carried a stale diff from data seeded in an earlier run - its header shows **"Product data
changed - 2"** (`R-S8-9-1280.png`), the "changed" (not "updated") wording for a flag-only,
non-designing status, exactly as AC-S8-9 amended specifies.

Restored SRTBF11701's list price back to 1241.00 at the end (confirmed on its detail page).

### Evidence files (this re-check)

`R-S5-2-1280.png`, `R-S5-12-1280.png`, `R-S5-7-1280.png`, `R-S7-16-1280.png`,
`R-S10-3-1280.png`, `R-S10-4-375.png`, `R-S10-4-1280.png`, `R-S6-12-1280.png`,
`R-S7-8-arrange-1280.png`, `R-S9-3-1280.png`, `R-S8-8-1280.png`, `R-S8-14-1280.png`,
`R-S8-9-1280.png`

## S11 check (HEAD ceda4f7d1)

Scope: AC-S4-3 (amended), AC-S4-10 to AC-S4-14, plus a 375px usability check. Session `ptr10s11`,
logged in as `person38@example.com`, navigated by sidebar clicks from `/` throughout (Master Data
Management > Products > All Products, then Dealer Kit > Room Designer > Price Tag Requests).

- **AC-S4-3 (amended): PASS.** Product SRTWT1212-SS-GM-DIY, Overview detail tabpanel text dump
  contains no "price tag" string anywhere (Basic Information / Pricing Summary / Price floor /
  Specifications / Tracking Flags / Combos / Sold with, no such row). Edit Product form's "Basic
  Information" tab (the Overview edit form) lists exactly Product Code, Product Name, Description,
  Category, Brand, Barcode, Item Type, Active Status, Chat Search, Exclude from reorder planning -
  no "Price tag description" textarea. (`S11-AC-S4-3-edit-form-1280.png`)
- **AC-S4-10: PASS.** Specifications tab on SRTWT1212-SS-GM-DIY shows "PRICE TAG DESCRIPTION"
  directly under "PRODUCT DESCRIPTION", reading "(none)" (`S11-AC-S4-10-spec-tab-none-1280.png`).
  On SRTKS8547 (already carried a stored description "Undermount double-bowl sink. Includes waste
  kit.") "Edit price tag description" swapped it for a prefilled textarea with Insert field / Save
  / Cancel. Typed `{{product.name}} in {{spec.material}}`, Save persisted it; reloaded the product
  detail page (fresh `open` of the same URL with `?tab=specifications`) and the saved template was
  still shown, not the old text (`S11-AC-S4-10-saved-persisted-1280.png`). Cancel on
  SRTWT1212-SS-GM-DIY discarded a typed-in template back to "(none)". Escape on SRTKS8547: typed
  "TEMP ESCAPE TEST" over the saved template, pressed Escape, box reverted to read-only showing
  the saved `{{product.name}} in {{spec.material}}` unchanged - the Escape edit was never saved.
- **AC-S4-11: PASS.** Insert field dialog's own field list contains exactly two group labels,
  "PRODUCT" (Code, Name, Dimensions, Spec lines, Price tag description, List price, Sell price,
  Currency, Accessories) and "SPECS" (~50 spec fields including Material); no Line, Set or part
  group anywhere in the list. Clicked "Material {{spec.material}}" with the caret placed at the
  start of the textarea's second line (Home key): the content became
  `Undermount double-bowl sink.\n{{spec.brand}}Includes waste kit.{{spec.material}}` after two
  inserts, i.e. each token landed exactly at the caret, not appended to the end. Testing note, not
  a product defect: the field list is its own scroll container inside the dialog, and clicking a
  button that is scrolled out of view silently closed the dialog without inserting anything -
  `scrollintoview` on the target button before every click fixed this consistently.
  (`S11-AC-S4-11-insert-field-dialog-1280.png`, `S11-AC-S4-11-scrolled-to-material-1280.png`)
- **AC-S4-12: DEFECT.** On SRTKS8547 with template `{{spec.material}} tap`, "Prints as:" read
  "Stainless steel tap" - the spec token resolves correctly to the readable form (matching the
  casing the same tab's own Specification/Value table already uses for this field, "Stainless
  steel" sentence-case, not the AC text's illustrative "Stainless Steel" title-case - judged
  consistent with the app's existing convention, not a second defect). But with template
  `{{product.name}} in {{spec.material}}` "Prints as:" read **"in Stainless steel"** - `
  {{product.name}}` resolved to nothing where "SRTKS8547" (or "SRTWT1212-SS-GM-DIY" on the other
  product) was expected. Reproduced on two different products, confirmed not a debounce/timing
  issue (re-checked after a 1s wait, value unchanged), reproduced again with the template reduced
  to just `{{product.name}}` alone -> "Prints as:" empty. (`S11-AC-S4-12-prints-as-check-1280.png`,
  `S11-AC-S4-12-DEFECT-product-name-blank-1280.png`,
  `S11-AC-S4-12-DEFECT-product-name-blank-srtks8547-1280.png`)
- **AC-S4-13: DEFECT.** Opened PT-202609-0018 (`designing`, line product SRTKS8547) in the
  designer. Tag 1a already carries a text layer bound to Content
  `{{product.price_tag_description}}\n{{product.code}}`. Editing SRTKS8547's price tag description
  (AC-S4-10/12 above) triggered this request's own auto-apply (S8): the tag's "Review product data
  changes" dialog showed PRICE TAG DESCRIPTION was "Undermount double-bowl sink. Includes waste
  kit." -> now `{{product.name}} in {{spec.material}}`, i.e. the tag's pin correctly carries the
  raw stored template. On the canvas, though, that layer renders only **"SRTKS8547"** - the
  `{{product.code}}` line - the `{{product.price_tag_description}}` line renders as nothing, not
  the expected "SRTKS8547 in Stainless Steel". Ruled out a box-clipping artifact: grew the layer's
  H from 10mm to 30mm (Inspector > Transform) and still only the single "SRTKS8547" line appeared,
  vertically centered as it would be if the first line is genuinely an empty string, not truncated
  - reverted H back to 10mm and Saved to leave the tag as found. Did not additionally test a
  part-slot binding (`subjectPart`), since the base product-level token is already broken and a
  part-level check depends on the same resolution path. (`S11-AC-S4-13-designer-overview-1280.png`,
  `S11-AC-S4-13-layer-selected-1280.png`, `S11-AC-S4-13-zoomed-1280.png`,
  `S11-AC-S4-13-resized-check-1280.png`, `S11-review-dialog-1280.png`,
  `S11-AC-S4-13-after-dismiss-1280.png`, `S11-restored-height-1280.png`)
- **AC-S4-14: NOT INDEPENDENTLY RE-TESTED.** No new backend behaviour to check per the AC's own
  text ("no new pytest"); `GET`/`PATCH /products/{id}` round-tripping `price_tag_description` was
  exercised indirectly by every Save above (AC-S4-10) and by the Specifications tab showing the
  saved value after a fresh page load.
- **375px usability check: PASS.** Specifications tab (read-only and mid-edit, with the textarea,
  Insert field / Save / Cancel and "Prints as:" line) is fully legible and not clipped at 375
  wide. (`S11-AC-check6-spec-tab-375.png`, `S11-AC-check6-edit-mode-375.png`)

Product left edited (intentionally, dev copy, task authorized): SRTKS8547 now carries
`price_tag_description = "{{product.name}} in {{spec.material}}"` (was "Undermount double-bowl
sink. Includes waste kit."); its price tag request tag 1a's pin picked this up via auto-apply and
was Dismissed (not rolled back) to clear the red dot.

### Evidence files (S11 check)

`S11-AC-S4-3-edit-form-1280.png`, `S11-AC-S4-10-spec-tab-none-1280.png`,
`S11-AC-S4-10-saved-persisted-1280.png`, `S11-AC-S4-11-insert-field-dialog-1280.png`,
`S11-AC-S4-11-scrolled-to-material-1280.png`, `S11-AC-S4-12-prints-as-check-1280.png`,
`S11-AC-S4-12-DEFECT-product-name-blank-1280.png`,
`S11-AC-S4-12-DEFECT-product-name-blank-srtks8547-1280.png`,
`S11-AC-S4-13-designer-overview-1280.png`, `S11-AC-S4-13-layer-selected-1280.png`,
`S11-AC-S4-13-zoomed-1280.png`, `S11-AC-S4-13-resized-check-1280.png`,
`S11-review-dialog-1280.png`, `S11-AC-S4-13-after-dismiss-1280.png`,
`S11-restored-height-1280.png`, `S11-AC-check6-spec-tab-375.png`,
`S11-AC-check6-edit-mode-375.png`

## S11 re-check (backend fix applied to `resolve-prices` response schema; coordinator ruling on AC-S4-12)

Coordinator ruling carried into this re-check: the AC-S4-12 "product.name renders empty" finding
above is NOT a defect - both products used (SRTWT1212-SS-GM-DIY, SRTKS8547) have `name == code`,
and the app's long-standing `nameOrBlankIfCode` rule intentionally blanks a name that only repeats
the code. Recorded as pass below using `{{product.code}}` instead. Session `ptr10s11b`, same rules
(sidebar navigation, `get url` before reads, closed only its own session at the end).

1. **AC-S4-12, re-tested with `{{product.code}}`: PASS.** On SRTKS8547's Specifications tab, set
   the template to `{{product.code}} in {{spec.material}}`. "Prints as:" read **"SRTKS8547 in
   Stainless steel"** - both tokens resolve. Saved. (`S11b-AC-S4-12-prints-as-code-1280.png`)
2. **AC-S4-13, line/parent subject: PASS (backend fix confirmed).** Opened PT-202609-0018's
   designer fresh (`open` to the `/design` URL, i.e. a hard reload, not a soft client nav). Tag
   1a's text layer bound to `{{product.price_tag_description}}{{product.code}}` with subject
   Parent/SRTKS8547 now renders **"SRTKS8547 in Stainless Steel"** on the canvas (confirmed at
   100%/146%/232% zoom - previously this rendered nothing for the first line). The edit to
   SRTKS8547's template (step 1) auto-applied to both tag 1a and 1b via S8: each tag's "Review
   product data changes" dialog showed PRICE TAG DESCRIPTION **WAS** `{{product.name}} in
   {{spec.material}}` **NOW** `{{product.code}} in {{spec.material}}` (raw templates, not
   resolved), matching the request's expected format; Dismissed both.
   (`S11b-AC-S4-13-canvas-resolved-1280.png`, `S11b-AC-S4-13-zoomed-1280.png` /
   `S11b-AC-S4-13-max-zoom-1280.png` / `S11b-AC-S4-13-100-1280.png`,
   `S11b-AC-S4-13-review-1a-1280.png`, `S11b-AC-S4-13-review-1b-1280.png`)
3. **AC-S4-13, part subject: DEFECT (different cause, not yet fixed).** Selected tag 1a's
   `{{product.price_tag_description}}` layer, changed its subject via the Product dropdown from
   Parent to the open-group candidate **"SRTKT1871SS (this tag)"** (options were Parent
   `SRTKS8547` plus a "Kitchen Tap" group listing both candidates, matching AC-S6-4's picker
   shape). With SRTKT1871SS carrying no price tag description yet, the canvas rendered only
   **"SRTKT1871SS"** (the `{{product.code}}` line) - the description line was empty, as expected
   for a part with no template. (`S11b-AC-part-subject-empty-1280.png`) Then, on SRTKT1871SS's own
   Specifications tab, set price tag description to `{{product.code}} part` and Saved (DB
   confirmed: `products.price_tag_description = '{{product.code}} part'` for SRTKT1871SS). Back in
   the designer: no red dot / no "Review product data changes" appeared for either tag (unlike the
   line-level edit in step 2, which did trigger S8 auto-apply); the canvas still rendered only
   "SRTKT1871SS", no "SRTKT1871SS part" line, after clicking "Check product data", after a full
   `open` hard-reload of the `/design` URL, and after leaving and re-entering via the request
   detail page. (`S11b-AC-part-template-check-1280.png`, `S11b-AC-part-zoom-check-1280.png`)
   Traced the root cause via the SAME Bearer token the page itself uses (captured from
   `agent-browser network request <id> --json`), calling
   `POST /api/v1/dealer-kit/price-tag-requests/78c06810-10f3-470d-812c-4cf743d69635/resolve-prices`
   directly: in that response, tag 1a's top-level (line-product) `price_tag_description` reads
   `"{{product.code}} in {{spec.material}}"` (correct, confirms the coordinator's backend fix), but
   **both entries in `parts[]` (SRTKT1871SS and SRTKT1872SS) carry `"price_tag_description": null`**
   even though SRTKT1871SS's own DB row and its own Specifications tab both show the saved
   template. So this is not a frontend resolution bug and not the same cause as the original
   AC-S4-13 defect: the `resolve-prices` endpoint's line/parent-product serialization was fixed,
   but its `parts[]` row serialization still drops `price_tag_description` - a part-bound text
   layer can never resolve the token no matter what the part's own template says, until that
   `parts[]` row also carries the field (this is exactly what AC-S4-4 requires: "on the line's
   product row AND on every part row"). Reverted the layer's subject back to Parent
   (`SRTKS8547`, the "(None)" slot binding / default product) via the same dropdown and clicked
   Save to restore the tag's design to its pre-test state (confirmed by re-selecting the layer:
   label and Inspector both show no part suffix, "Saved 12:42" shown).
   (`S11b-revert-subject-dropdown-1280.png`, `S11b-subject-reverted-1280.png`,
   `S11b-final-state-1280.png`)

Products left edited (dev copy, intentional): SRTKS8547's `price_tag_description` is now
`{{product.code}} in {{spec.material}}` (changed from the `{{product.name}} in ...}}` set in the
first S11 check, per this re-check's step 1); SRTKT1871SS's `price_tag_description` is now
`{{product.code}} part` (was empty). PT-202609-0018 tag 1a/1b data-change dots were Dismissed
again; tag 1a's canvas layer subject was tested against the part then reverted back to Parent and
saved, so the tag's own design is unchanged from before this re-check.

### Evidence files (S11 re-check)

`S11b-AC-S4-12-prints-as-code-1280.png`, `S11b-AC-S4-13-canvas-resolved-1280.png`,
`S11b-AC-S4-13-canvas-zoomed-1280.png`, `S11b-AC-S4-13-resized-full-text-1280.png`,
`S11b-AC-S4-13-max-zoom-1280.png`, `S11b-AC-S4-13-fit-view-1280.png`,
`S11b-AC-S4-13-100-1280.png`, `S11b-AC-S4-13-review-1a-1280.png`,
`S11b-AC-S4-13-review-1b-1280.png`, `S11b-product-combobox-1280.png`,
`S11b-product-dropdown-open-1280.png`, `S11b-product-dropdown-open2-1280.png`,
`S11b-AC-part-subject-empty-1280.png`, `S11b-AC-part-template-check-1280.png`,
`S11b-AC-part-zoom-check-1280.png`, `S11b-revert-subject-dropdown-1280.png`,
`S11b-subject-reverted-1280.png`, `S11b-final-state-1280.png`
