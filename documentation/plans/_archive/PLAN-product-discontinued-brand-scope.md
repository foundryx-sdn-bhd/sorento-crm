# PLAN - Product-discontinued notification: per-(company, brand) recipient scoping

**Status:** Implemented 2026-08-18 (branch `fm/product-discontinued-brand-scope`, awaiting PR).
S1-S4 done; reviewer findings applied (dialog reopen no longer resurrects a cleared scope set,
scopes sent only when edited, `integration_api_key` principal matched, migration DDL exercised
by its test). Local prod-copy DB migrated to 375; captain hands-on tested on :3000.
**Classification:** CORE, `public` schema (extends the existing core notification feature;
nothing tenant-installable).
**UAC:** `product-discontinued-brand-scope-acceptance-criteria.md` (the contract).
**Parent:** `PLAN-product-discontinued-notification.md` (stamp-first batch mechanics, toggles,
deep link - all preserved).

Process note: autonomous crewmate run. Grill / lavish / to-tickets steps have no human in the
loop; the firstmate brief carries the requirements and acceptance criteria and stands in as the
grilled contract. Deviation to be recorded in the PR description.

## Design

### Semantics (locked)

- A **scope** = (company_id | NULL, brand_id | NULL) per user. NULL company = all companies
  (and forces NULL brand). NULL brand = all brands in that company.
- A product **matches** a scope iff
  `(scope.company_id IS NULL OR scope.company_id = product.company_id) AND
   (scope.brand_id IS NULL OR scope.brand_id = product.brand_id)`.
  A product with `brand_id IS NULL` therefore matches only all-brands scopes (AC-6).
- Channel toggles keep their meaning (HOW); scopes decide WHAT. Recipient of a company batch =
  user with either toggle on, not trashed, whose scopes yield a non-empty subset of that
  batch's products. Zero scopes = zero notices (AC-4).
- Per-recipient content: count/wording/link built from the recipient's subset. If the subset is
  the full batch (an all-brands scope matched), the link is the existing plain
  `?discontinued_batch_id=` link - byte-compatible with today for migrated all/all users
  (AC-1). Otherwise the link appends `&brand_id=<comma-joined sorted distinct brand ids>` from
  the user's matching specific-brand scopes for that company; the filtered list equals the
  subset because NULL-brand products only ever appear in the full-batch case.

### Schema

New table `user_product_discontinued_scopes` in `app/models/user.py` (or a sibling module the
coder judges cleaner - it is a user-preference table):

- `id` UUID pk (str uuid4 default, matching house style)
- `user_id` UUID FK `users.id` ON DELETE CASCADE, not null, indexed
- `company_id` UUID FK `companies.id` ON DELETE CASCADE, nullable
- `brand_id` UUID FK `brands.id` ON DELETE CASCADE, nullable
- `created_at` naive-UTC default now
- Dedupe guard: unique index on
  `(user_id, coalesce(company_id, '00000000-0000-0000-0000-000000000000'),
    coalesce(brand_id,  '00000000-0000-0000-0000-000000000000'))`
  (expression index; avoids depending on PG15 NULLS NOT DISTINCT).
- **Plain `Base` - NOT CompanyScopedMixin, no `__company_shared__`.** The scheduled task
  applies a session-level company scope filter to every CompanyScopedMixin table; a scoped
  preference table would silently hide recipients during scoped runs (AC-11, AC-9).

### Migration (single Alembic revision)

1. Create table + indexes. `down_revision` chains onto the CURRENT committed single head -
   run `alembic heads` first; if multiple heads exist, stop and report (do not merge heads in
   this branch unless they are this repo's own committed state; per PRINCIPLES.md rejoin with
   an empty merge revision only if `alembic heads` already shows >1 on main).
2. Backfill (same revision, after create): insert one `(user_id, NULL, NULL)` row for every
   user with `notify_email_on_product_discontinued OR notify_whatsapp_on_product_discontinued`
   true, guarded by NOT EXISTS on (user_id, both-null) so re-runs and partial prior runs are
   idempotent (AC-2). Trashed users included (toggle state is what we preserve; fan-out still
   filters trashed).
3. Downgrade drops the table.

### Backend service - `product_discontinued_notify_service.py`

Keep `run_product_discontinued_check` structure (per-company grouping, stamp-first commit,
aggregate return shape). Rework `_run_for_company` fan-out:

- Load candidate users once per run (either toggle on, not trashed) with their scope rows
  (single query + `selectinload` or a second IN query; do NOT query the Brand table here -
  match on `product.brand_id` values already in hand, avoiding the session scope filter).
- Per user, compute subset per the matching rule. Empty subset -> skip silently.
- Full-batch subset -> today's exact title/body/wa_text/link. Partial subset -> same wording
  templates with the subset count and the brand-filtered link. `context_vars` /
  `data` payload keys unchanged in shape; `discontinued_count`, `discontinued_link`,
  `whatsapp_text` etc. reflect the recipient's subset.
- `subscribers` in the per-company return dict = number of recipients with a non-empty subset;
  `notified_users` counts successful sends as today. Best-effort try/except per recipient
  unchanged (AC-8). `create_with_channel_preferences` call signature unchanged (same
  email/whatsapp pref attrs - constraint: no new channels).

### Products list filter - multi-brand

`app/api/v1/master_data/products.py` (both list endpoints that accept `brand_id`) +
`product_service`: accept `brand_id` as comma-separated ids; split/strip, single id keeps
current code path (`==`), multiple use `IN` (AC-10).

### User API - scopes CRUD rides the existing user endpoints (AC-12, AC-13)

- Pydantic: `ProductDiscontinuedScopeIn {company_id: Optional[str], brand_id: Optional[str]}`,
  `ProductDiscontinuedScopeOut {id, company_id, company_name, brand_id, brand_code,
  brand_name}`. `UserUpdate.product_discontinued_scopes: Optional[list[ScopeIn]]` -
  replace-all when provided, untouched when omitted. Validation via AppException: brand must
  exist and belong to the given company; `company_id NULL` forces `brand_id NULL`; dedupe
  before insert.
- `UserResponse.product_discontinued_scopes: list[ScopeOut] = []`, populated in ALL THREE
  manual `UserResponse(**user_dict)` builders (`users.py:392,532,832` today) - the known
  manual-dict-drops-fields trap.
- Permission: whatever dependency guards the existing profile/toggle update on that route
  guards scopes too (no new permission).

### Frontend - one screen only

1. **Scope editor** in `user-profile-edit-dialog.tsx` notification section, directly under the
   two discontinued toggles (AC-14, AC-15): rows of company `SearchableSelect`
   ("All companies" sentinel + companies from the existing companies service) + brand
   `SearchableMultiSelect` ("All brands" option + brands of the selected company - reuse
   `use-brand-select-query` if it can filter by company, else filter client-side on the
   brands' company_id). All-companies locks brand to All brands. Add/remove row. Toggle-on
   with zero rows pre-populates the all/all row; zero rows shows a short inline hint (one
   line, per no-explanations rule). FE model `user.ts` gains the camelCase scope list; the
   edit dialog submits snake_case `product_discontinued_scopes` on the existing profile
   update path.
2. **Read view parity** (AC-16): render saved scopes read-only in the same position on the
   profile view (company/brand names, never UUIDs).
3. **Deep link** (AC-17): `ProductsList.tsx` param-normalization effect (currently keeps only
   `discontinued_batch_id`, line ~124) must preserve `brand_id` alongside it and pass it
   through to the API query. Multi-id `brand_id` need not be reflected in the single-select
   brand dropdown; the dropdown may stay on "all" while the param filters the grid.

### Tests (tester seat, Postgres only, seed-own-chain per CLAUDE.md)

- pytest `tests/test_product_discontinued_notify_brand_scope.py`: AC-1..AC-9 including the
  Kia Yee scenario (AC-3) asserting per-recipient counts, link params, and absence for the
  unmatched slice; follow the seeding patterns of the existing
  `test_product_discontinued_notify*.py` files (marker-prefixed rows, blank_session or
  rolled-back SessionLocal).
- pytest for products `brand_id` comma filter (AC-10) and for the user update scope
  validation + response round-trip (AC-12, AC-13 happy + denial + validation).
- Migration backfill test: run the revision's upgrade() against seeded pre-migration users in
  a rolled-back transaction (repo idiom), assert idempotency.
- vitest: scope editor states (AC-18); ProductsList deep-link param preservation.

### Slices

- S1 [FE, Phase 1]: scope editor + read view + deep-link preservation against mocked service.
  **Done** (code only; no browser verification run yet). The expected API contract is
  documented at the top of
  `sorento_crm_frontend/app/(protected)/user-management/users/services/productDiscontinuedScopeService.ts`,
  whose `USE_SCOPE_BRAND_MOCKS` flag is the single Phase-1 mock S2 flips. One addition to the
  plan: the editor needs brands OF A GIVEN COMPANY, so the contract asks S2 for a
  `company_id` filter on `GET /api/v1/master-data/brands/select` (brands are
  `CompanyScopedMixin`, and an admin edits scopes for a company they are not switched into).
  Company options come from the existing `useCompany()` grants rather than the superadmin-only
  `/companies/select`, so a non-superadmin admin can use the editor.
- S2 [BE, Phase 2]: model + migration + backfill, service fan-out rework, products
  multi-brand filter, user API scopes; swap FE mock to real. **Done** (migration
  `375_user_discontinued_scopes`, chained onto `374_merge_proj_media_flyer`; still one
  head). Three notes on top of the plan:
 - `GET /brands/select?company_id=` runs the read under `company_scope(db, {company})`
    but first checks the caller can REACH that company (superadmin/admin, or holds the
    grant; the API-key principal is unscoped by design) and 404s the company otherwise,
    mirroring `system/companies.get_company`. The param widens WHICH company is read,
    never WHO may read it - the precedent set by `lookup.resolve`, which refuses to let
    a JWT user re-scope themselves out of a body value.
 - Scope validation raises `handle_unprocessable` (422) for an unknown company, an
    unknown brand, or a brand outside the named company - matching the FE contract.
 - The three existing discontinued suites seed their subscribers with the all/all scope
    the migration backfills, since a user with no scope now hears nothing by design.
- S3 [T, Phase 2]: pytest + vitest suites above. **Done** (5 pytest files, 4 vitest specs).
- S4 [Review, Phase 3]: reviewer agent + evidence run (AC-19). **Done**; fix-first findings
  applied in the follow-up commit (see Status). Write-side company gating on `replace_scopes`
  is a recorded follow-up in the UAC.

### Out of scope

New notification channels; any admin surface beyond the existing profile dialog; reflecting
multi-brand deep links in the brand dropdown UI; per-scope channel overrides.

## Evidence run (AC-19)

Recorded agent-browser evidence run (headless `npx -y agent-browser@0.27.0`), tester seat, no new
Playwright spec. Stack was already running (FE prod build :3000, BE :8000) - neither booted nor
killed. Own tab via `tab new`; `get url` re-checked before every trusted read; session closed with
`close` (never `close --all`). Screenshots and raw snapshots saved under
`/private/tmp/claude-501/-Users-tehjayson--treehouse-sorento-crm-732336-12-sorento-crm/bd96cd9d-b1d8-4914-b0fa-a89a317085a8/scratchpad/ac19-evidence/`
(local scratchpad, not committed).

### Walk

1. **Login + navigate by sidebar** (never deep URL except step 4). Home -> sidebar "User
   Management" -> "Administrative Users" -> `/user-management/users` -> row click into detail.
   First subject: `f6885bcd-f2ce-4cf4-a698-eb4b88c3fd31` (test user, no role assigned - see
   defect note below). Second subject (used for the actual save/persist steps once the first hit
   a pre-existing role-required validation unrelated to this feature): `User B`
   (`efda32ff-c9a7-4b47-9c30-0e019c05c8df`, has a role, "b-5b278f@test.local").

2. **Read view (AC-16).** Both subjects' detail page renders a `Discontinued product scope:`
   description-list row, positioned directly after `Tier:` and before the `Edit user details`
   button - i.e. at the bottom of the same DescriptionList the edit dialog's fields come from.
   Baseline for both: `No scope set. No discontinued product notices.`
   Screenshots: `step1-read-view.png`, `step1-read-view-userB.png`.
   Note: the two channel toggles (`notify_email_on_product_discontinued` /
   `notify_whatsapp_on_product_discontinued`) are **not** rendered read-only anywhere on this
   page (they only exist inside the edit dialog) - consistent with the parent
   product-discontinued-notification feature, which never surfaced those toggles read-only
   either. AC-16 only requires the *scope* row to sit at edit-dialog-equivalent position, which
   it does; not a defect.

3. **Edit dialog layout (AC-14).** Opened via "Edit user details". Snapshot confirms, in DOM
   order: `Email on products discontinued` checkbox -> `WhatsApp on products discontinued`
   checkbox -> `Discontinued product scope` heading + `Add scope` button -> (rows) ->
   `WhatsApp daily SLA summary` (next unrelated field). The scope editor sits directly under the
   two toggles as specified.

4. **Toggle-on pre-population (AC-15).** Checked `Email on products discontinued` from
   unchecked/zero-rows: one row appeared immediately with company select = "All companies" and a
   disabled brand select = "All brands", plus a "Remove scope" trash icon. Reproduced on both
   subjects. Screenshots: `step2a-toggle-on.png`, `step2b-prepop-check.png`,
   `step2c-prepop-row.png` (subject 1); pre-population confirmed again via snapshot text for
   User B before the add-row step.

5. **Add row + company/brand scoping (AC-14, S1 company_id contract).** Clicked "Add scope":
   second row appeared defaulting to the first available company ("Mocha") + "All brands".
   Changed company to "Sorento" via the select; `network requests --filter brands/select`
   showed:
   `GET /api/v1/master-data/brands/select?company_id=5e2c68f5-1b35-4f1d-a6e0-e904c0d8260f` (Mocha
   company, on initial row default) then
   `GET /api/v1/master-data/brands/select?company_id=00000000-0000-0000-0000-000000000001`
   (Sorento company id, after switching) - both 200, confirming the brand list is fetched scoped
   to the selected company as documented in the S1 contract note. The multi-select then listed
   13 Sorento brands (BRAVAT, CABANA, ELLECI, IBORN, INFINITY, CONTACT AA SUISSE, MOCHA, NO LOGO,
   OTHERS, SORENTO, TP ENTERPRISE, WDI, and the initial "All brands"). Selected `SORENTO` and
   `MOCHA`. Screenshot: `step2d-row-added.png`, `step2e-row-added-userB.png`.

6. **Save + persistence (AC-12, AC-14).** On User B (has an existing role, so the pre-existing
   unrelated "at least one role is required" dialog validation didn't block save - see defect
   note), clicked "Save Changes". `network request` on the resulting
   `PUT /api/v1/user-management/users/efda32ff-.../` showed request body
   `"product_discontinued_scopes":[{"company_id":null,"brand_id":null},{"company_id":"00000000-0000-0000-0000-000000000001","brand_id":"c90ce49e-...MOCHA"},{"company_id":"00000000-0000-0000-0000-000000000001","brand_id":"438cab0b-...SORENTO"}]`,
   response 200 with each row round-tripped back with `company_name":"Sorento"`,
   `"brand_code"/"brand_name":"MOCHA"` and `"SORENTO"` respectively - no UUIDs in any rendered
   text. Reloading the detail page (fresh `open`, not client nav) showed the read view badges
   `All companies: All brands` and `Sorento: MOCHA, SORENTO` - persisted, named, correctly
   grouped by company. Screenshot: `step2f-read-view-persisted.png`.

7. **Delete-all inline hint (AC-15).** Reopened the edit dialog (rows loaded from server:
   All/All + Sorento/MOCHA+SORENTO). Removed both rows via their trash icons. The paragraph
   `No scope set. This user will not be notified about any discontinued product.` appeared in
   place of the removed rows, next to the still-enabled "Add scope" button. Screenshot:
   `step3-zero-rows-hint.png`. Clicked "Cancel" - reload confirmed the two rows were **not**
   deleted server-side (cancel correctly discarded the in-progress edit).

8. **Cleanup / DB left sane.** Reopened the dialog, removed the Sorento/brand row, unchecked
   `Email on products discontinued`, removed the remaining All/All row, and saved - restoring
   User B to its original zero-scope / toggle-off state. Confirmed via reload:
   `No scope set. No discontinued product notices.` Subject 1
   (`f6885bcd-...`) was never actually saved (blocked by the pre-existing role-required
   validation before the fix), and a final reload confirmed it is still at its original baseline
 - no cleanup needed there.

9. **Products deep link (AC-17).** Direct URL (the one exception to sidebar-only navigation, per
   task instructions, since the deep link itself is the surface under test):
   `http://localhost:3000/master-data-management/products?discontinued_batch_id=00000000-0000-0000-0000-000000000000&brand_id=c90ce49e-86ee-4f1f-9a6c-46247aadeee2`
   (brand id captured from the `brands/select` network call in step 5, MOCHA under Sorento).
   After load, `get url` showed both query params intact (the param-stripping effect preserved
   `brand_id` alongside `discontinued_batch_id`, per the S1 fix to `ProductsList.tsx`).
   `network requests --filter master-data/products` showed
   `GET /api/v1/master-data/products?...&brand_id=c90ce49e-...&discontinued_batch_id=00000000-...`
   200. UI showed the expected banner "Showing only the products from a recent 'products
   discontinued' notification" with a "Filters 1" badge and an empty grid (`1 - 0 of 0`, correct
   since the batch id is fake). Screenshot: `step4-deeplink-products.png`.

10. **375x812 layout sanity (AC-14, AC-15, AC-16).** Set viewport to 375x812, reloaded User B's
    detail page (baseline, already restored). Read view: full DescriptionList renders without
    horizontal overflow, `Discontinued product scope:` row wraps its label/value onto separate
    lines cleanly (`step5a-mobile-read.png`). Edit dialog: toggles and the "Discontinued product
    scope" card stack full-width immediately below the two toggles with no clipping
    (`step5b-mobile-toggles.png`); toggling email-on pre-populated the All/All row exactly as at
    desktop width, company/brand selects stacked full-width with no overlap
    (`step5c-mobile-prepop.png`). Cancelled without saving; confirmed baseline unchanged.
    Reset viewport to 1280x800 before closing the tab.

11. **Console/errors.** Checked after every interaction. Only recurring output across the whole
    run: `Warning: Missing 'Description' or aria-describedby={undefined} for {DialogContent}` -
    a pre-existing Radix a11y warning on the edit-profile dialog, unrelated to this feature (not
    newly introduced; the dialog existed pre-feature). No other console warnings, no page errors,
    at either viewport.

### Result

AC-14, AC-15, AC-16, AC-17 all confirmed working end-to-end against the live stack with real
network calls (brands/select scoped by company_id, PUT carrying `product_discontinued_scopes`,
GET products carrying both deep-link params). No implementation defects found in the feature
itself.

### Notes / non-blocking observations (not implementation defects, recorded for completeness)

- **agent-browser tooling quirk, not a product bug:** the CLI's default `click`/`find role option
  click` on cmdk-style multi-select options (the brand `SearchableMultiSelect`) intermittently
  no-opped or closed the popover without registering the selection (Radix Popover content
  re-renders between mousedown/mouseup faster than the CDP-simulated click completes). Worked
  around with a raw `eval` DOM dispatch (`pointerdown`/`mousedown`/`mouseup`/`click` on the
  matched `[role="option"]` element). The underlying app behavior, once the click actually
  landed, was correct both times (state updated, chip appeared, dropdown stayed open for further
  selection). Also: pressing `Escape` while the multi-select popover was open but had already
  lost its own outside-click target closed the entire "Edit User Details" dialog rather than
  just the popover - lost in-progress edits once, recovered by reopening and redoing the steps.
  Neither is scoped to this feature's code.
- **Pre-existing unrelated validation blocked save on subject 1:** `f6885bcd-...` (a test user
  with zero roles assigned) rejected `Save Changes` with "At least one role is required." This
  is the existing user-profile-edit-dialog role requirement, not part of this feature; it simply
  meant AC-12's round-trip (steps 6-8) had to be demonstrated on a second subject that already
  held a role. No code change needed or made.
- Search box on `/user-management/users` (`Search users`) did not visibly filter the grid in
  this run (grid rows unchanged after typing an email into it) - pre-existing, unrelated to this
  feature, not investigated further; worked around by scanning/paging the unfiltered list for a
  usable row instead.

### Browser verification

Reachable and used throughout (daemon-shared browser, own tab confirmed via `get url` before
every trusted snapshot). Not a vitest-only fallback.
