# PLAN - brand-aware escalation routing, CRM half

> Status: IMPLEMENTED 2026-08-17 revision 2 (member-level evidence run below)
> Prior status line, kept for history: REVISED 2026-08-17 - **sections 1 to 8 and the evidence run below describe
> REVISION 1 (brand as a column on the tier row) and are SUPERSEDED.** What shipped is
> revision 2, described in the final section of this file: the brand is a tag on the
> team MEMBER, `agent_teams` is untouched, and the migration is `371_brand_member_routing`.
> Revision 1 is kept verbatim for history - it is what the row-level ACs, the row-level
> tests and the 2026-08-17 evidence run were written against, and reading the revision-2
> section without it loses why the shape changed.
> UAC: `brand-aware-routing-acceptance-criteria.md` (the contract; its "Revision 2 -
> member-level brand tags" section supersedes the row-level ACs). Journey at the top of it.
> Out of scope, both revisions: any n8n change (separate task); a rules engine; per-brand
> ladders beyond what the admin configures by hand.

---

## Revision 1 (SUPERSEDED - brand on the tier row)

> Decisions (captain, 2026-08-16): Option B (brand column, not the set-name convention); one team
> set per function; T2/T3 shared unless the admin adds a brand row; no brand -> all-brands row;
> `marketing_promotion` all-brands T1 = today's `_sorento` T1; n8n additionally sends `company_id`.
> None of the schema below shipped: no `agent_teams.brand_code`, no coalesce indexes, no
> per-tier Brand select. The migration number 368 was never deployed and was renumbered.

## 1. Journey recap (see UAC)

Admin tags brand on the tier row -> n8n sends `brand_code` + `company_id` with the base set key
-> resolver picks the brand row, else the all-brands row, inside the routing company -> the
tracker stores the brand -> tier 2/3 escalation resolves with it.

## 2. Data model (designed backwards from the journey)

- `agent_teams.brand_code TEXT NULL` - lower-case `brands.brand_code`, NULL = all brands. Not an
  FK: `brands` is company-scoped with per-company ids while the CODE string is the cross-company
  handle (report §3.3), and a deleted brand must not cascade a routing row away.
- Unique keys become (see `app/models/access.py` `AgentTeam.__table_args__`, which MUST mirror
  the migration because scratch-schema fixtures build indexes from the model):
 - `uq_agent_teams_agent_code_company_tier_null`: `(agent_id, code, company_id,
    coalesce(brand_code, ''))` where tier IS NULL
 - `uq_agent_teams_agent_code_company_tier`: `(agent_id, code, tier, company_id,
    coalesce(brand_code, ''))` where tier IS NOT NULL
  `coalesce` because Postgres treats NULLs as distinct in a unique index and we need exactly one
  all-brands row per (agent, code, tier, company).
- `conversation_sla_tracking.brand_code TEXT NULL` - the brand the assignment resolved with,
  read back by every escalation (same pattern as `company_id` from 320).
- Round-robin cursors are per (agent, team) and need no change: a brand row points at its own
  team.

## 3. Migration

One revision (id <= 32 chars), chained onto main's `366_merge_363_365` - the empty merge of
`363_merge_flyer_promo_um` and `365_merge_scm_plan_feedback` that landed in #191, so the branch
has one head:

1. `368_brand_aware_routing` - schema + data:
   1. `ALTER TABLE agent_teams ADD COLUMN brand_code TEXT` / same on
      `conversation_sla_tracking` (both `IF NOT EXISTS`, re-runnable like 320).
   2. Drop the two old partial unique indexes, create the two coalesce ones above.
   3. Collapse, per (agent_id, company_id) that has rows with code in
      `('marketing_promotion_sorento','marketing_promotion_mocha','marketing_promotion_cabana')`:
    - `policy = the _sorento set's policy_id` (else first non-null among the three) - one policy
        per code is an invariant (`resolve_policy_id_for` 409s otherwise).
    - Tier-1 rows (and tier-NULL rows): `code = 'marketing_promotion'`, `brand_code = suffix`.
        **A tier-NULL row is treated exactly like tier 1, all-brands copy included, so the
        collapsed set never has brand rows at a tier with no fallback (which the new
        save-time guard would then reject).**
    - Tier 2 / 3, per tier: distinct team_ids among the suffixed rows. If a base
        `marketing_promotion` row already exists at that tier it stays the all-brands row; else the
        `_sorento` row (else the first) becomes `brand_code = NULL`. Every other suffixed row at
        that tier: same team_id as the all-brands row -> DELETE; different team -> becomes a
        brand row for its suffix (AC-M3).
    - All-brands T1: if no `marketing_promotion` T1 with brand NULL exists, INSERT a copy of
        the `_sorento` T1 row (team, notify_on_extension) with brand NULL. **No `_sorento` T1 ->
        seed it from the next brand in priority order (mocha, then cabana) and warn which one
        was copied (review fix).** Leaving the tier with brand rows and no fallback is worse:
        an unknown brand 404s AND the save-time guard then rejects the whole agent on the
        admin's next save.
    - After the collapse, every brand code written is checked against `brands`
        (`lower(brand_code)`); a code that exists nowhere gets a warning naming the code and
        the company. A guard, not a fix - the prod copy has SORENTO / MOCHA / CABANA for both
        companies.
    - Set `policy_id = policy` on every `marketing_promotion` row of that (agent, company).
    - `UPDATE conversation_sla_tracking SET team_set_code='marketing_promotion',
        brand_code=<suffix> WHERE team_set_code = <suffixed>` (all rows; resolved rows have it
        cleared anyway).
   4. Downgrade: restore the old indexes, drop both columns. The collapse is not reversed
      (docstring says so, as 320's does for its own irreversible parts). It refuses up front
      with a `RuntimeError` naming the count while any brand row survives, since after the
      column is dropped those rows collide on the pre-brand unique key.
   Test: `tests/test_migration_368_brand_routing.py` in the style of
   `test_migration_320_company_routing.py` (`blank_session`, load module by path, run
   `upgrade()` via `Operations`), fixture = today's three sets exactly as in the local DB
   (T1 distinct teams; T2 = one shared team; T3 = one shared team; one policy on `_sorento`),
   plus a control set (`marketing_product`) that must be byte-identical after upgrade, plus the
   AC-M3 disagreement case and the "base row already exists" case, plus re-run.

## 4. Backend changes

`app/services/user_service.py` (`AccessAgentService`):

- New module-level helpers:
 - `normalise_brand_code(value) -> str | None` (strip, lower, '' -> None).
 - `split_legacy_team_set_code(code) -> tuple[str, str | None]`: dict
    `{"marketing_promotion_sorento": ("marketing_promotion","sorento"), ...mocha, ...cabana}`;
    anything else -> `(code, None)`; matched case-insensitively. One-release compat; comment
    says so.
 - `_pick_brand_rows(rows, brand_code)`: rows are `(…, brand_code)` tuples; return the rows whose
    brand equals the wanted one if any, else the rows whose brand is NULL. Single place for the
    preference rule; every resolver calls it.
- `get_team_id_by_tier(agent_id, tier, team_set_code=None, *, company_id, brand_code=None)`:
  select `(team_id, brand_code, code)`, raise the existing "which team set?" 409 on more than one
  DISTINCT code BEFORE `_pick_brand_rows` runs (review fix: a brand can only disambiguate within
  one set, and narrowing first could answer a set-less caller with another set's team), then
  `_pick_brand_rows` and the existing zero / one / many handling and messages. Add `get_team_by_tier(...) -> tuple[team_id, row_brand] | None` with the
  same body and make `get_team_id_by_tier` its thin wrapper (the external route needs the row's
  brand for `brand_matched`).
- `list_team_ids_for_agent_code(..., brand_code=None)`: same preference. **(Built as
  `list_team_rows_for_agent_code(...) -> [(team_id, row_brand)]` with
  `list_team_ids_for_agent_code` as its thin wrapper, mirroring the tier pair: the
  tier-less path needs the picked row's brand for `brand_matched`, and ids alone cannot
  carry it.)**
- `get_tier_team_and_notify(..., brand_code=None)`: same preference; the caller in
  `sla_service.py:2735` (extension notify) passes the tracker's brand.
- `set_agent_teams`: read `brand_code` per assignment through `normalise_brand_code`; dedupe key
  `(code, tier|'__null_tier__', brand|'')`; new validation BEFORE the delete: for each
  (code, tier) with a brand row and no NULL-brand row raise
  `handle_unprocessable(f"Team set '{code}' tier {tier} has brand rows but no 'All brands'
  row. Add one so an unknown brand can still be routed.")`; write `brand_code` on the row.
  **(`handle_unprocessable`, not `handle_validation_error`: AC-R7 and the FE contract both
  say 422, and `handle_validation_error` is 400.)**
- `list_agent_teams` / `list_agent_teams_with_round_robin_state`: select and emit `brand_code`.
- `resolve_team_with_tier_fallback`, `get_user_tier_in_team_set`: gain `brand_code=None` and pass
  it through (default keeps behaviour).

`app/schemas/user.py` `AgentTeamAssignment`: `brand_code: Optional[str] = None`.
`app/api/v1/user_management/access_agents.py` PUT: include `brand_code` in the payload dict.

`app/services/company_routing_service.py` `resolve_routing_company(..., company_id=None)`:
precedence company_id (must exist in `companies`) > company_code > contact > default; unknown id
logs and falls through. `source="body"` for either explicit form.

`app/api/v1/external/next_assignee.py`:
- `_routing_company_for_body` passes `company_id=body.get("company_id")`.
- `_resolve_round_robin_team_id(service, agent_id, body, *, company_id) -> ResolvedTeam`
  (small `NamedTuple(team_id, team_set_code, brand_code, brand_matched)`): base code + brand via
  `split_legacy_team_set_code`, explicit `body["brand_code"]` wins; tier path uses
  `get_team_by_tier`; tier-less path uses `list_team_ids_for_agent_code(brand_code=...)`
  (`brand_matched` = the picked row's brand is not None). **The tier-less fallback is NOT
  brand-narrowed once a tier was supplied and missed (review fix): that list spans every tier,
  so narrowing it could collapse a genuinely ambiguous list onto one row of the wrong tier
  instead of the pre-brand "send tier" 400.** Both callers (`post_next_assignee`,
  `team_members.py`) unpack `.team_id`.
- Response: `_enrich_n8n_response(..., resolved_team=...)` adds `team_set_code`, `brand_code`,
  `brand_matched`.

`app/api/v1/external/team_members.py`: `brand_code` and `company_id` query params -> the same
resolver body / `resolve_routing_company(company_id=...)`.

`app/schemas/sla.py` `ConversationSLATrackingCreate`: `brand_code: Optional[str] = None`,
`company_id: Optional[str] = None`. Response schema gains `brand_code`.
`app/models/sla.py`: `brand_code = Column(Text, nullable=True)` on the tracker.
`app/services/sla_service.py`:
- `create_tracking`: `company_id` = body company when it exists in `companies` else
  `company_for_contact`; `team_set_code, suffix_brand = split_legacy_team_set_code(...)`;
  `brand_code = normalise_brand_code(body brand) or suffix_brand`; store both; RR branch passes
  `brand_code=` to `get_escalation_assignee_for_tier`.
- `get_escalation_assignee_for_tier(..., brand_code=None)` -> `get_team_id_by_tier(brand_code=)`.
- `apply_assignee_team_derivation` (review fix, closes BL-016): `derive_team_for_assignee` also
  returns the resolved row's brand, and "did the team change" compares the resolved `team_id`,
  not just agent + set code - since 368 the brand rows of one tier are different teams under one
  code, so a cross-brand reassign at the same tier was a silent no-op. On a team change the
  tracker's `brand_code` is re-stamped from the row it landed on and the reassignment event log
  names the brand move. The current team is re-resolved through `get_team_by_tier`; when that
  cannot answer (ambiguous / misconfigured ladder) nothing is treated as changed.
- `create_tracking` idempotent hit: an incoming brand that differs from the open tracker's is
  logged at INFO (the open tracker's brand still wins).
- Escalation callers in `app/api/v1/sla/sla_tracking.py` (integration escalate ~:968, manual
  ~:1159) pass `brand_code=getattr(tracking, "brand_code", None)`.

Nothing else. No new service module, no settings knob.

## 5. Frontend changes (`sorento_crm_frontend/app/(protected)/user-management/access-agents/`)

- `types/accessAgent.types.ts` `AgentTeamAssignment`: `brand_code: string | null`.
- `services/accessAgentService.ts`: contract comment for the new field; `setAgentTeams` passes it
  through. Phase 1: a `__mocks__/agentTeamsBrand.ts` fixture (one set with sorento / mocha /
  all-brands T1 rows + shared T2 / T3) behind a temporary flag in the service so the page renders
  the new control without the backend; removed in Phase 2 when the real response carries the field.
- Brand options: reuse `app/(protected)/master-data-management/shared/hooks/use-brand-select-query.ts`
  (already hits `/api/master-data/brands/select`, company-scoped). Value = `brand_code.toLowerCase()`,
  label = `brand_name`.
- `AccessAgentForm.tsx`: `AssignmentRow.brand_code: string | null`; a `SearchableSelect`
  (`clearable`, placeholder "All brands") per row after the Team select; the duplicate-row guard
  keys on (code, tier, brand); payload includes `brand_code`.
- **`AccessAgentFormModal.tsx` gets the SAME control (Phase 2 correction).** `AccessAgentForm.tsx`
  is not mounted anywhere - `/new` redirects to the list and the detail page's Edit opens the
  modal - so the component Phase 1 changed is dead UI. Without the modal the admin never sees the
  Brand select (AC-F1 / AC-V1 unreachable) AND every save through it would blank the brand on
  every row, since the PUT replaces the company's rows wholesale.
- `AccessAgentDetail.tsx`: brand badge per row (`Badge` variant like the tier badge; text = brand
  name or "All brands"); `assignmentKey` includes the brand.
- Vitest (Phase 2, tester): `AccessAgentForm.test.tsx` gets a case for the select + payload;
  a new `AccessAgentDetail.test.tsx` for the badges (mock `useListingColumnPreferences` if a
  DataGrid is involved; the row list is a plain list, so likely not needed).

## 6. Tests (pytest, Postgres only, seed everything, `ZZT` prefix)

- `tests/test_brand_routing_resolver.py`: AC-R1..R7 on `AccessAgentService` via
  `blank_session()` seeds (agent, teams, members, brand rows).
- `tests/test_next_assignee_brand.py`: AC-X1..X3, AC-H1..H4 through the TestClient with the
  external API key (pattern: `test_next_assignee_external.py` / `test_company_routing_echo.py`),
  plus team-members parity (AC-X2).
- `tests/test_tracker_brand_escalation.py`: AC-T1..T3.
- `tests/test_migration_368_brand_routing.py`: AC-M1..M5.
- Existing suites that touch these files must stay green:
  `test_agent_teams_company_isolation.py`, `test_company_routing_*.py`,
  `test_next_assignee_*.py`, `test_team_members_external.py`, `test_market_segment_routing.py`,
  `test_migration_320_company_routing.py`, `test_form_sla_*`, `test_cs_*routing*`.

## 7. Order of work

1. Phase 1 (coder): FE against the mock; browser check (tester) at 1280 / 375.
2. Phase 2 (coder, test-first): migration + model -> resolver -> external endpoints -> tracker;
   FE mock swap. Tester: vitest, full pytest subset above, agent-browser evidence run (AC-V1).
3. Phase 3: reviewer agent + `/code-review`, then codex cross-model review; findings fixed by
   the coder; then `/no-mistakes`.

## 8. Not doing (and why)

- No brand FK, no `company_brands` table: the code string is the handle n8n has, and the report
  found no company<->brand table to lean on.
- No per-brand policy: policy stays per (agent, code); a brand row inherits the set's policy.
- No auto-creation of Mocha-company rows: same provisioning stance as 320.
- No change to `AgentTeamRoundRobinCursor`, market segments, coverage redirect, form SLA.

## Evidence run 2026-08-17 (AC-V1 / AC-F4)

Tester (agent-browser, headless, `--session brand-routing-crm` throughout). FE at
`http://localhost:3091` (dev), BE at `http://localhost:8091`. Servers were already running per
the launch brief; not booted or killed by this run. Screenshots below are filenames under the
run's scratchpad dir; paths are absolute in the raw tool transcript.

### Environment hiccup before the run (not a product bug)

The shared agent-browser daemon was unresponsive for ~15 minutes at the start of this run
(`get url` / `open` repeatedly failed with `Resource temporarily unavailable (os error 35)`).
Root cause: tab `t1` in the shared browser was pinned at 100-160% CPU for 20+ minutes, most
likely a JS busy-loop from before the coordinator's mid-run fix (FE was pointed at the wrong
backend port with CORS failing, producing the "Failed to fetch" toasts called out in the launch
brief). Opening a fresh tab (`tab new`) responded immediately; closing the stuck tab (`tab close
t1`) and re-opening `http://localhost:3091/` on the new tab cleared it. Login persisted from a
prior session (cookies), so no fresh login was needed. Noting this so a future run recognises the
symptom instead of assuming the daemon itself is broken.

### Step 1 - open Access Agent detail (AC-V1)

- `open http://localhost:3091/` -> logged in already (session cookie persisted). `console`
  clean, `errors` empty.
- Sidebar: clicked `User Management` (top-level group, not nested under "System Management" as
  the launch brief guessed) -> expanded to Administrative Users / Roles / Permissions / **AI
  Agents** / Teams / Internal Users / Contact Access Types / Market Segments / Sales Agents. The
  UAC's "Access Agents" entity is labelled **"AI Agents"** in this build's sidebar (route is
  still `/user-management/access-agents`, page header still says "Access Agent" /
  "Create Access Agent" - confirmed by screenshot and URL, not just label guesswork).
- Clicked "AI Agents" -> `/user-management/access-agents`. Searched `general_enquiries` in the
  grid search box -> one row. Clicked it -> URL
  `http://localhost:3091/user-management/access-agents/b988a7c3-348e-48e9-a691-9e4079586b99?...`.
- Screenshot: `04-agent-detail.png` (full page), `05-team-assignments-top.png` (header + top of
  Team Assignments). `console`/`errors` clean throughout.
- **PASS.**

### Step 2 - read-view rows all show the empty-state brand badge (AC-V1)

- Screenshot `06-team-assignments-marketing-product.png`: `marketing_product` Tier 1
  ("Marketing - Product"), Tier 2 ("Marketing Managers"), Tier 3 ("Retail Director") each carry
  an **"All brands"** badge next to the tier/team name. Full-page shot `04-agent-detail.png`
  confirms the same on every other set on the page (`purchasing`, `marketing_promotion_sorento`,
  `warehouse`, `marketing_promotion_mocha`, `marketing_promotion_cabana`) - i.e. every row on
  every set, matching "migration not run on this DB, so all read 'All brands' - correct empty
  state."
- **PASS.**

### Step 3 - Edit modal: Brand select per row, add a Mocha tier-1 row, save (AC-V1, AC-F1, AC-F3)

- Clicked `Edit` -> modal `Edit Access Agent` opens as a fixed-height dialog with its own
  **internal scroll region** for Team Assignments (screenshot `10-modal-before-addtier.png`).
  Gotcha for future runs: `agent-browser scroll` scrolls the outer page, not this inner region;
  `scrollintoview @ref` on the target element is what actually moves the inner scrollbar. Two
  early clicks landed on the dialog backdrop while the target was scrolled out of the inner
  viewport and silently closed the dialog (no error, no console warning) - `08-after-add-tier-
  click.png` / `09-check-state.png` show the read view reappearing after one such stray click.
  State is NOT lost when this happens - re-opening `Edit` restored the in-progress row (React
  form state outlives the dialog's visibility), which is how the second attempt recovered.
- Snapshot confirmed the contract per row: `combobox: Tier` -> `combobox: Team` -> `combobox:
  Brand` reading `"All brands Clear selection"` with a `Clear selection` button - i.e. **Brand
  select immediately after Team, defaulting to All brands, and clearable**, on every existing
  row (`marketing_product` T1/T2/T3, `purchasing` T1/T2/T3, etc). **AC-F1 PASS.**
- On `marketing_product`: clicked `Add tier` (scrolled into view first) -> new row appended
  (Tier `-`, Team `Complaint` default, Brand `All brands`, clearable). Opened the Brand dropdown
  (screenshot `14-brand-click2.png`): options are `All brands` (checked), then the active
  company's brands - `MOCHA`, `BRAVAT`, `CABANA`, `ELLECI`, `IBORN`, `INFINITY`, `CONTACT AA
  SUISSE`, `NO LOGO`, ... - confirming AC-F1's "brands of the active company + All brands".
  Selected `MOCHA` (`15-mocha-selected.png`).
- Opened the Tier dropdown while Brand was still `All brands`: options `1`/`2`/`3` were all
  **disabled** (only `-` selectable) - the existing tier-1 All-brands row blocks another
  all-brands row at tier 1, as expected. After setting Brand to `MOCHA` first, re-opening Tier
  showed `1`/`2`/`3` all enabled (`16-tier-dropdown.png`) - confirms the client duplicate guard
  is keyed on (code, tier, brand), not (code, tier) alone, matching AC-F3's "two rows in the same
  set + tier are allowed when their brands differ." Selected Tier `1`.
- Set Team to `Marketing - Promotion Mocha` (the team holding Kia Yee per the read view) via the
  dropdown option list (`21-team-dropdown.png` -> `22-team-mocha-set.png`). Confirmed via
  snapshot that the row's Code textbox context is `marketing_product` (not a different set) at
  `/tmp/snapG.txt` lines 625-647.
- Final row before save: `Tier 1 | Team "Marketing - Promotion Mocha" | Brand MOCHA`.
- **Note on click reliability**: `find text "1" click --exact` to pick the Tier-1 option matched
  an unrelated `"1"` on the page (most likely the `1 / 1` record-nav text behind the dialog) and
  closed the modal without setting anything - re-confirms the CLAUDE.md guidance to prefer
  `snapshot` + `@ref` clicks over broad text search inside a dialog with background content
  still in the DOM.
- Clicked `Update`. **First attempt** (before the index fix below): `network requests --filter
  access-agents` captured `PUT .../access-agents/{id}` -> `200`, then
  `PUT .../access-agents/{id}/teams` -> **`400`** with body
  `{"message":"cannot have duplicate code in different groups","detail":null,
  "code":"VALIDATION_ERROR"}`. `network request <id> --json` on that call showed the request
  payload already carried the correct contract - `marketing_product` rows
  `{tier:1,brand_code:null}`, `{tier:2,brand_code:null}`, `{tier:3,brand_code:null}`,
  `{tier:1,brand_code:"mocha"}`, every other set's rows `brand_code:null` - i.e. **AC-F3's
  frontend payload construction was already correct**; the 400 was a backend rejection. Traced
  it to `app/services/user_service.py:2381` (the generic `IntegrityError` fallback in
  `set_agent_teams`) and confirmed via `psql \d agent_teams` that this dev DB's
  `uq_agent_teams_agent_code_company_tier[_null]` indexes were still migration 320's pre-brand
  form (no `brand_code` in the key), even though the `brand_code` *column* existed - `alembic
  current` on this DB failed with `Can't locate revision identified by '354_projects_schema_move'`,
  a revision absent from this branch entirely, confirming the DB's migration history didn't
  match this branch. `app/models/access.py` `AgentTeam.__table_args__` and
  `368_brand_aware_routing.py` were already correct in source; this was purely a live-DB
  migration-state gap. Reported it; the orchestrator applied migration 368's index DDL
  (`DROP`/`CREATE UNIQUE INDEX ... coalesce(brand_code, '')`) directly to this dev DB. Re-verified
  via `psql \d agent_teams` before the re-run: both indexes now read
  `... coalesce(brand_code, ''::text) ...`.
- **Re-run** (fresh `open` + login, same `--session brand-routing-crm`; the sidebar/search/detail
  navigation of steps 1-2 was repeated identically to reach the same agent, then Edit -> Add tier
  -> Brand `MOCHA` -> Tier `1` -> Team `Marketing - Promotion Mocha`, mirroring the sequence
  above screenshot-for-screenshot: `37-redo-newrow.png` -> `38-redo-row-complete.png`). Clicked
  `Update`: `PUT .../access-agents/{id}/teams` -> **`200`**. `network request <id> --json`
  response body includes the new row in full:
  `{"code":"marketing_product","team_id":"d62e815d-...","tier":1,"policy_id":null,
  "notify_on_extension":true,"brand_code":"mocha","team_name":"Marketing - Promotion Mocha",
  "members":[{"name":"Kia Yee",...}],"last_assigned":{"name":"Kia Yee",...},
  "next_in_line":{"name":"Kia Yee",...}}` - confirming AC-R8 (the teams list response carries
  `brand_code` per row). Toast "Access agent updated successfully" (`39-redo-saved.png`).
  Read view now shows a second Tier-1 row - `Marketing - Promotion Mocha` with a **`MOCHA`**
  badge, 1 member, Last assigned/Next in line `Kia Yee` - directly under the original all-brands
  Tier-1 row (`40-redo-mocha-badge-closeup.png`). Did a full `reload` (not just re-render) and
  re-screenshotted: the `MOCHA` row persists (`41-redo-after-reload.png`).
- **AC-V1 step 3 (save + reload shows Mocha badge): PASS.**

### Step 4 - negative save: brand row with no All-brands row at that tier (AC-V1, AC-R7)

- Re-opened `Edit`. On `marketing_product`, deleted the **Tier-1 All-brands row** (trash icon),
  leaving only the Tier-1 `MOCHA` row plus the untouched Tier-2/Tier-3 all-brands rows
  (`42-negtest-deleted-allbrands.png`; snapshot confirmed via ref inspection that the remaining
  rows under the `marketing_product` code textbox were exactly Tier 2, Tier 3, and Tier 1/MOCHA
 - no all-brands row left at tier 1).
- Clicked `Update`. `network requests --filter teams` -> `PUT .../teams` -> **`422`**.
  `network request <id> --json` response body:
  `{"message":"Team set 'marketing_product' tier 1 has brand rows but no 'All brands' row. Add
  one so an unknown brand can still be routed.","detail":null,"code":"VALIDATION_ERROR"}` -
  **exact match** to AC-R7's specified message, naming both the set (`marketing_product`) and the
  tier (`1`). Toast screenshot: `43-negtest-422-toast.png`.
 - **AC-R7 (save-time guard, 422 naming set+tier): PASS.**
- Clicked `Cancel`. Read view re-checked: `marketing_product` still shows the Tier-1 All-brands
  row, the Tier-1 `MOCHA` row, and Tier-2/Tier-3 - i.e. exactly the post-step-3 state, with **no**
  partial application of the rejected edit (`44-negtest-nodatachange.png`). The failed PUT left
  no data change, matching the transactional rollback in `set_agent_teams`.
 - **AC-V1 step 4 (negative save, no data change): PASS.**

### Step 5 - cleanup: restore the original three rows (AC-V1)

- Note: `Cancel` does not reset the modal's in-memory form state (confirmed both in the first
  attempt and here - re-opening `Edit` after `Cancel` restored the row list as last edited, not
  as last saved). A `reload` between `Cancel` and the next `Edit` is what guarantees the form
  opens from the server's current state; used that pattern here before touching cleanup.
- Reloaded, re-opened `Edit` (fresh snapshot confirmed the true DB state: Tier-1 All-brands,
  Tier-1 `MOCHA`, Tier-2, Tier-3 - matching post-step-3). Deleted only the Tier-1 `MOCHA` row
  (`46-cleanup-mocha-removed.png`; snapshot confirmed exactly 3 rows remained under
  `marketing_product`, all "All brands").
- Clicked `Update` -> `PUT .../teams` -> **`200`**. Reloaded again and re-screenshotted:
  `marketing_product` shows exactly the original 3 all-brands rows, byte-identical to the
  pre-run baseline screenshot (`47-cleanup-final-confirmed.png` vs `06-team-assignments-
  marketing-product.png`).
 - **AC-V1 step 5 (cleanup/restore): PASS.** The agent's data is back to its original state;
    this evidence run leaves no residue on the shared dev DB.

### Step 6 - 375x812 viewport check (AC-F4)

- `set viewport 375 812`, scrolled to top, re-opened the detail page fresh.
- Read view: `28-mobile-header.png` shows a **pre-existing, out-of-scope** overflow in the page
  header row (prev/next record nav + Edit button run off the right edge, `Edit` reads "Ed...").
  This is `AccessAgentDetail.tsx`'s top action bar (`flex items-center justify-between`, line
  201), not touched by the brand feature (which only changes Team Assignment rows and the edit
  modal per PLAN section 5) - the same header-wrap issue CLAUDE.md's lessons record as already
  fixed on PR/SF, complaint and stock-inquiry detail pages but evidently not on this one. Logged
  as a finding, not a brand-feature regression.
- Team Assignments card rows themselves reflow cleanly at 375px - `29-mobile-team-assignments.png`:
  Tier badge, team name, then the **All-brands badge on its own line directly under the team
  name** (same relative position as desktop), then member count / last-assigned / next-in-line
  stacked below. No clipping, no overlap.
- Edit modal at 375px (`30-mobile-edit-modal.png`, `31-mobile-modal-rows.png`,
  `32-mobile-row-visible.png`): dialog goes full-width, each tier row reflows to `Tier` + `Team`
  on one line and **`Brand` on its own line directly below** with the clear (x) and delete icons
  inline - same relative position as desktop (immediately after Team), no clipping/overlap.
  `console`/`errors` clean (only the pre-existing `Missing Description for {DialogContent}` a11y
  warning, unrelated to this feature).
- **AC-F4: PASS** for the Team Assignments / Brand-select row structure at 1280 and 375px. The
  header overflow above is a separate, pre-existing issue outside AC-F4's row-structure scope.
- Reset `set viewport 1280 800`, confirmed URL still on the agent detail page.

### Step 7 - console/errors

Checked after every interaction (open, sidebar nav, search, detail open, Edit open, each combobox
interaction, save, cancel, viewport changes). Zero uncaught page errors (`errors` always empty)
for the entire run. Console warnings seen: `Missing 'Description' or 'aria-describedby={undefined}'
for {DialogContent}` (pre-existing Radix a11y warning on the Edit dialog, unrelated to brand),
plus routine `[debug] JWT token extracted successfully` / i18next init noise. No "Failed to
fetch" toasts persisted once the browser was on a fresh tab post the coordinator's server fix.

### Step 8 - close session

`close` (session-scoped, not `--all`) run after the first attempt (before the index fix) and
again at the end of the redo covering steps 3-5. A fresh `open` + login (session cookie had
expired between the two halves of this run) started the redo; the same `--session
brand-routing-crm` was used throughout, never `--all`.

### Screenshots (scratchpad, this run)

First attempt (pre index-fix) - steps 1-2, AC-F4 mobile check, and the setup/contract portion of
step 3: `00-home.png`, `01-user-mgmt-expanded.png`, `02-ai-agents-list.png`,
`03-search-general-enquiries.png`, `04-agent-detail.png`, `05-team-assignments-top.png`,
`06-team-assignments-marketing-product.png`, `07-edit-modal-open.png`,
`08-after-add-tier-click.png`, `09-check-state.png`, `10-modal-before-addtier.png`,
`11-scrolled-into-view.png`, `12-after-addtier-clicked.png`, `13-brand-click.png`,
`14-brand-click2.png`, `15-mocha-selected.png`, `16-tier-dropdown.png`, `17-tier1-set.png`,
`18-addtier-redo.png`, `19-after-delete-extra.png`, `20-tier1-set-ok.png`,
`21-team-dropdown.png`, `22-team-mocha-set.png`, `23-verify-set-code.png`, `24-after-save.png`
(the pre-fix 400 toast), `25-after-cancel.png`, `26-confirm-no-data-change.png`,
`27-mobile-detail.png`, `28-mobile-header.png`, `29-mobile-team-assignments.png`,
`30-mobile-edit-modal.png`, `31-mobile-modal-rows.png`, `32-mobile-row-visible.png`,
`33-final-state-1280.png`.

Redo (post index-fix) - steps 3-5: `34-redo-start.png` (signed-out, session had expired),
`35-um-expanded.png`, `36-baseline-marketing-product.png` (baseline re-confirmed: 3 all-brands
rows), `37-redo-newrow.png`, `38-redo-row-complete.png`, `39-redo-saved.png` (200 + success
toast), `40-redo-mocha-badge-closeup.png` (MOCHA badge), `41-redo-after-reload.png` (persists
post-reload), `42-negtest-deleted-allbrands.png`, `43-negtest-422-toast.png` (the exact AC-R7
message), `44-negtest-nodatachange.png`, `45-cleanup-baseline-check.png`,
`46-cleanup-mocha-removed.png`, `47-cleanup-final-confirmed.png` (restored to the original 3
rows, post-reload).

### Summary

| AC | Result |
|----|--------|
| AC-V1 step 1 (open detail) | PASS |
| AC-V1 step 2 (all-brands empty state) | PASS |
| AC-V1 step 3, Brand select contract (AC-F1) | PASS |
| AC-V1 step 3, PUT payload brand_code (AC-F3) | PASS |
| AC-V1 step 3, save + reload badge | **PASS** (after the dev DB's indexes were brought to migration 368's `coalesce(brand_code,'')` form - see step 3 note; the first attempt hit the stale pre-368 indexes and 400'd, which was a live-DB migration-state gap, not a code defect) |
| AC-V1 step 4, negative save (AC-R7 422, no data change) | PASS |
| AC-V1 step 5, cleanup/restore | PASS |
| AC-F4 (1280px row structure) | PASS |
| AC-F4 (375px row structure, no clipping) | PASS |
| Console/errors | Clean throughout |

All UAC items in scope for this evidence run (AC-V1, AC-F1, AC-F3, AC-F4, AC-R7's save-time
guard as observed from the UI) pass against the live stack. The one non-brand finding is a
pre-existing, out-of-scope header overflow at 375px on `AccessAgentDetail.tsx`'s action bar (see
step 6) - logged for a separate fix, not this feature.


---

## Revision 2 (member-level) - WHAT SHIPPED

> Decision (captain, 2026-08-17, on the live stack): brand tags live on the TEAM MEMBER,
> multi-select, exactly like market segments. The per-tier-row Brand select, its badges and
> `agent_teams.brand_code` are gone. Untagged member serves every brand; when nobody in the
> team carries the resolved brand, the whole team round-robins.
> UAC: the "Revision 2 - member-level brand tags" section (AC2-M1, AC2-M2, AC2-R1..R3,
> AC2-X1, AC2-F1, AC2-V1).

### R2.1 Why the shape changed

Revision 1 made the brand a property of a ROUTING ROW, which forced three consequences the
captain rejected on sight: a tier could hold several rows (so "one team per tier" stopped
being true), a tier with brand rows needed an "All brands" row or an unknown brand
dead-ended (hence a 422 save-time guard the admin had to learn), and the admin configured
brands in a place that has nothing to do with the person who actually handles the brand.

Market segments had already solved the identical problem one level down: the tag is on the
membership, an untagged member serves everybody, and an empty filtered pool falls back to the
whole team. Brand is the same kind of thing, so it now uses the same mechanism, and there is
one less concept in the routing model rather than one more.

### R2.2 Data model

- `team_member_brands` (new) - `team_member_id` uuid FK `team_members(id)` ON DELETE CASCADE,
  `brand_code` text, the pair the primary key, plus an index on each column and a `created_at`.
  A byte-for-byte mirror of `team_member_market_segments`, minus the FK on the code:
  `brands` is company-scoped and deleting a brand must not untag its specialists (which would
  silently widen their queue to every brand). Unknown codes are refused at save time instead.
- `conversation_sla_tracking.brand_code TEXT NULL` - unchanged from revision 1. The brand the
  conversation is ABOUT, stamped once at creation and read back by every escalation, because a
  scheduler tick has no request context to re-derive it from.
- `agent_teams` - **no change at all.** The 320-era partial unique indexes stay exactly as
  they are; the model's `__table_args__` is back to its pre-branch form.

### R2.3 The rule, in one place

`member_serves_brand(tags, brand)` in `app/services/user_service.py`: true when the member
carries that brand, or carries none. Applied in exactly two readers, both of which already
applied the identical rule for market segments:

- `AccessAgentService.get_next_assignee(agent_id, team_id, contact_segments=None, *, brand_code=None)`
 - pool = RR-eligible members passing the segment filter AND the brand filter;
 - empty pool -> the whole team on the legacy cursor (never nobody);
 - the returned dict carries `brand_matched`, true only when the member DRAWN is tagged with the
    brand (per assignee, not per pool: an untagged serve-all member drawn from the same pool
    reports false);
 - cursor key = `segment_key` + `brand_pool_key(brand)` (`~b:<code>`), and the brand part is
    appended ONLY when a tagged member matched. A team nobody has tagged therefore keeps its
    single `''` cursor when n8n starts sending `brand_code`, instead of silently splitting one
    rotation into one queue per brand.
- `AccessAgentService.list_active_team_members_detail(team_id, contact_segments=None, *, brand_code=None)`
 - the same filter over the active roster, so `GET /external/team-members` returns exactly the
    pool `next-assignee` draws from.

`_brand_codes_by_member` is one query and is only issued when a brand was actually requested,
so the no-brand path never touches the new table.

### R2.4 Call sites

- `ConversationSLATrackingService.get_escalation_assignee_for_tier(..., brand_code=None)` keeps
  its revision-1 signature but now passes the brand to the POOL: the tier team is resolved
  brand-blind (`get_team_id_by_tier`, pre-branch signature restored), then the brand picks who
  inside it. Called with `tracking.brand_code` by both escalation routes and by the RR branch of
  `create_tracking`.
- `POST /external/next-assignee` reads `brand_code` (normalised, blank -> null) and the legacy
  suffixed set code, resolves the team without the brand, and passes the brand to the RR draw.
  It echoes `team_set_code`, `brand_code` and `brand_matched` (the latter straight off the draw,
  so it cannot disagree with what happened).
- `GET /external/team-members` takes the same `brand_code` + `company_id` query params.
- Extension-notify is untouched: `get_tier_team_and_notify` is back to its pre-brand signature
  and `_peek_next_assignee` stays filter-blind, which is exactly how it already treats segments.
- `GET|PUT /api/v1/user-management/teams/{team}/members/{user}/brands` - sibling of the
  market-segments member endpoints in the same route file, backed by
  `app/services/team_member_brand_service.py` (`{"codes": [...]}` both ways; the PUT replaces
  the whole set, empty clears it, an unknown code is a 422 naming it).

### R2.5 Re-stamping: the brand belongs to the ENQUIRY

Revision 1 re-stamped `tracker.brand_code` from the `AgentTeam` row a takeover or reassign
landed on. With no brand on the row there is nothing to re-stamp from, and the captain's rule is
simpler and better: **the brand is what the customer asked about, not who is handling it.**
Takeover, manual reassign and `apply_assignee_team_derivation` therefore leave `brand_code`
exactly as it is, and `apply_assignee_team_derivation` is back to its pre-branch
"agent or tier changed" comparison. BL-016 is updated to say so.

### R2.6 Migration `371_brand_member_routing`

Renumbered off 368 (never deployed; 368/369/370 are claimed by other lanes), `down_revision =
368_merge_tickets_main`, the single head of `origin/main` after its intervention-tickets merge
(it was `367_promote_flyer_provenance` when this section was first written; rebasing onto the
newer head is what keeps the chain at one head).

- creates `team_member_brands`, adds `conversation_sla_tracking.brand_code`, both IF NOT EXISTS;
- collapses `marketing_promotion_{sorento,mocha,cabana}` per (agent, company) into one
  `marketing_promotion` set. Per tier ONE team survives - the `_sorento` one (or an existing base
  row, or the next brand in `BRAND_PRIORITY` with a warning) - and every other suffixed row's
  team MEMBERS are moved into it tagged with that row's brand, preserving
  `include_in_round_robin` and landing at the BACK of the sort order. The suffixed row is then
  deleted. Kia Yee lands tagged `mocha`, Aqi tagged `cabana`, Am stays untagged;
- a person already in the surviving team is left alone and named in a warning, because tagging
  them would narrow somebody who currently serves every brand there;
- the same routine runs at tiers 2 and 3, so today's shared tier-2 team simply loses its
  duplicate rows, and a disagreeing tier-2 team's people are folded in tagged instead of losing
  their ladder;
- one policy is cast over the collapsed set (the `_sorento` binding, else the first non-null);
- suffixed OPEN trackers are rewritten to the base code + brand;
- a tag naming no row in `brands` is warned about, not rejected;
- re-runnable (no suffixed rows left -> no-op). Downgrade drops the table and the column; the
  collapse and the membership moves are NOT reversed and the docstring says so.

### R2.7 Frontend

- `MemberBrandEditor.tsx` - mirror of `MemberMarketSegmentEditor.tsx` in the same folder:
  popover + checkbox multi-select, options from `useBrandSelectQuery`, chips on the member row,
  "All brands" when untagged, lower-case codes in the payload. Mounted next to the segment
  editor on every member row of `AccessAgentDetail`.
- `services/memberBrandService.ts` + `hooks/useMemberBrands.ts` - the standard
  UI -> hook -> service -> `api-client` chain, `extractApiError` on both calls.
- Removed: the per-tier Brand select in `AccessAgentFormModal.tsx`, the brand badge and
  `useBrandSelectQuery` in `AccessAgentDetail.tsx`, `brand_code` on `AgentTeamAssignment`, and
  the row-level contract block in `accessAgentService.ts`. Those three files are byte-identical
  to their pre-branch state again.

### R2.8 Tests

| Suite | What it pins |
|-------|--------------|
| `test_brand_routing_resolver.py` (19) | AC2-R1 - the pool rule, untagged serves all, empty pool -> whole team, segment AND brand, per-brand cursors, the untagged team keeping its `''` cursor |
| `test_next_assignee_brand.py` (19) | AC2-X1 wire contract (echo, `brand_matched`, legacy suffix, `company_id`) + AC2-R3 headline cases and team-members parity against a seeded DB |
| `test_tracker_brand_escalation.py` (19) | the stamp at creation, the pool at creation and at tier 2, the routes passing `tracking.brand_code`, and the new rule that a reassign leaves the brand alone |
| `test_migration_371_brand_routing.py` (25) | AC2-M1 + AC2-M2 - the join table, `agent_teams` untouched, the collapse, the membership moves and tags, sort order, the control set, policy cases, re-runnable, downgrade |
| `test_team_member_brands.py` (8) | the save path - lower-case, replace-whole-set, unknown code refused, cascade |
| `MemberBrandEditor.test.tsx` (6) | AC2-F1 - chips, "All brands", lower-case payload, clearing, the no-brands empty state |
| `AccessAgentDetail.test.tsx` (2) | AC2-F1 - both editors mounted per member row, keyed by (team, user); empty-state roster |

Deleted with the row-level design: `test_migration_368_brand_routing.py`,
`AccessAgentFormModal.test.tsx` (every case was a Brand-select case), and the row-level halves of
the three brand suites.

### R2.9 Still open

- AC2-V1, the member-level evidence run, is now DONE - see "Evidence run 2026-08-17 (AC2-V1,
  member-level)" below: tag a member, `PUT 200`, reload, chips persist, the roster probe reflects
  the pool rule, untag, restore, 1280 + 375 px all PASS. The first pass of that run surfaced two
  real bugs (case-sensitive brand validation; a 375px member-row clipping regression), both fixed
  and committed before the redo that produced the PASS result above. The revision-1 evidence run
  further below exercised a UI that no longer exists and does not stand in for either pass.
- BL-014 (no committed browser regression guard) and BL-018 (the Brand select needs
  `master_data.brands.view`) still apply, BL-018 now to `MemberBrandEditor`'s catalogue call.

## Evidence run 2026-08-17 (AC2-V1, member-level)

Tester (agent-browser 0.27.0, headless, `--session brand-routing-member` on every command). FE
at `http://localhost:3091` (dev), BE at `http://localhost:8091` - both already running per the
launch brief; neither booted nor killed by this run. Local DB has `team_member_brands` hand-applied
(the promotion-set collapse migration, `371_brand_member_routing`, has NOT run here - expected per
the brief, so all three `marketing_promotion_{sorento,mocha,cabana}` sets are still visible as
separate rows on the page, unrelated to this evidence run's scope).

### Step 1 - open agent detail, expand `marketing_product` Tier 1, confirm member-level controls

- `open http://localhost:3091/` - session cookie persisted, already logged in; a login was primed
  (`REQUEST_BATCH_E2E_EMAIL` / `REQUEST_BATCH_E2E_PASSWORD` from `.env.local`) but not needed. One
  transient daemon hiccup (`Resource temporarily unavailable (os error 35)`) on a single `wait --url`
  call, self-resolved on the next command - noted per the shared-daemon caution, not a product issue.
- Sidebar: `User Management` (top-level group) -> expanded -> clicked **"AI Agents"** (this build's
  sidebar label for the Access Agents page; route/page header are still `access-agents` /
  "Access Agent", matching the prior revision-1 run's note).
- List page search box "Search access agents..." filtered to the one `general_enquiries` row
  (row click by ref/text was unreliable pre-filter - three attempts navigated nowhere until the
  list was narrowed to a single row, then `find text "general_enquiries" click --exact` worked).
  Landed on `http://localhost:3091/user-management/access-agents/b988a7c3-348e-48e9-a691-9e4079586b99?...`.
- Screenshot `03-agent-detail.png` (full page): every team-set card (`marketing_product`,
  `purchasing`, `marketing_promotion_sorento`, `warehouse`, `marketing_promotion_mocha`,
  `marketing_promotion_cabana`) shows **tier rows with no Brand select and no Brand badge anywhere**
 - confirms the row-level UI from revision 1 is gone.
- Expanded the `marketing_product` Tier 1 row ("Marketing - Product") by clicking it (a collapsible
  disclosure, not a plain heading - the first click attempt from off-screen silently no-op'd until
  the row was scrolled into view first). Screenshot `07-step1-member-brands-editor.png`:

  ```
  Team Members (in round-robin order):
  1. Tay Zhi Yang (person41@example.com) [icon]   Serves all   All brands   [Next in line]
  2. NOOR HASNI HUSIN (person15@example.com) [icon]     Serves all   All brands   [Last assigned]
  ```

  Each member row carries an **"Edit market segments"** control ("Serves all" chip) immediately
  followed by an **"Edit brands"** control ("All brands" chip) - the two editors sit side by side,
  same shape, on every member row. No tier-row Brand select or badge exists anywhere on the page
  (re-confirmed against the full-page screenshot above, across all six team sets).
- **AC2-F1 (member-row controls, no tier-row brand UI): PASS.**

### Redo note (both bugs fixed and committed before the redo)

The first pass of this run (steps 1-6 as originally executed) surfaced two real, reproducible bugs
while exercising step 2 and step 5: (1) `TeamMemberBrandService._validate`
(`app/services/team_member_brand_service.py`) compared lower-cased input against
`brands.brand_code` case-sensitively, so every real (upper-case) brand code was rejected with
"Unknown brand code(s)", blocking every tag save in this or any environment where brand codes are
upper-case; and (2) the member row collapsed the name/email block to 0px width at 375px once the
new Brands chip joined the market-segment chip and the position badge on one non-wrapping flex
line. Both were fixed and committed by the coordinator (case-insensitive brand validation; member
row stacks at 375px), and the backend was restarted on `:8091` with `--reload` to pick up the fix
(it had been serving stale code during the first pass). Steps 2, 3, 4 and 5 below are the **redo**
against the fixed code, in a fresh `--session brand-routing-member` browser session (cookie had
expired between passes; logged in again with the same `REQUEST_BATCH_E2E_EMAIL` /
`REQUEST_BATCH_E2E_PASSWORD` pair, never echoed). Step 1's findings (member-row controls, no
tier-row brand UI) were re-confirmed unchanged on the way back to the same agent detail page and are
not repeated below. Redo screenshots are prefixed `16-` through `26-`.

### Step 2 (redo) - tag a member via the Brands editor: PASS

- Re-navigated: sidebar `User Management` -> `AI Agents` -> searched `general_enquiries` -> opened
  the detail -> expanded `marketing_product` Tier 1 (`16-redo-tier1-expanded.png`). Baseline
  reconfirmed: both `Tay Zhi Yang` and `NOOR HASNI HUSIN` read "All brands" (unchanged from the
  first pass - the earlier 400 never wrote anything, as already established).
- Clicked "Edit brands" on Tay Zhi Yang's row -> popover opened with all boxes unchecked
  (`17-redo-brand-editor-open.png`, confirming no residue). Checked `MOCHA` and `CABANA`
  (`18-redo-checked.png`). Clicked `Save`.
- `network requests --filter /brands`: `PUT
  http://localhost:8091/api/v1/user-management/teams/7ebcd57e-22d6-49a0-821b-80253004f281/members/37cb4e13-ef86-4171-a7bf-77ac631c8fc3/brands`
  request body `{"codes":["mocha","cabana"]}` -> **`200`**, response body
  `{"codes":["cabana","mocha"]}` (sorted, lower-case). Read view immediately updated: Tay Zhi
  Yang's row now shows **`CABANA`** and **`MOCHA`** chips in place of "All brands"
  (`19-redo-saved-200.png`); NOOR HASNI HUSIN unchanged ("All brands").
- `reload` (full page reload, not just re-render), re-expanded `marketing_product` Tier 1:
  the `CABANA` / `MOCHA` chips **persist** after reload (`20-redo-after-reload-persists.png`).
- **AC2-F1 (PUT payload contract, lower-case codes): PASS.**
- **AC2-V1 step 2 (tag saves, PUT 200, chips persist on reload): PASS.**

### Step 3 (redo) - roster probe (`GET /external/team-members`), against the now-tagged state

`X-API-Key` read from `sorento_crm_backend/.env` `EXTERNAL_API_KEY` into a shell var, never
echoed. `GET /api/v1/external/team-members?agent_code=general_enquiries&team_code=marketing_product&tier=1`
with each of `brand_code=mocha`, `brand_code=cabana`, and no `brand_code` param, against the real
state (Tay Zhi Yang tagged `mocha` + `cabana`; NOOR HASNI HUSIN untagged):

| `brand_code` | Roster returned (names only) |
|---|---|
| `mocha` | Tay Zhi Yang, NOOR HASNI HUSIN |
| `cabana` | Tay Zhi Yang, NOOR HASNI HUSIN |
| (none) | Tay Zhi Yang, NOOR HASNI HUSIN |

All three returned the same two-member roster - expected in this instance because Tay Zhi Yang is
tagged with BOTH brands used in the probe, so he legitimately qualifies for either, and NOOR HASNI
HUSIN is untagged (qualifies for everything). This does not by itself prove the pool actually
*restricts* anyone, so a fourth, discriminating probe was added: `brand_code=bravat` (a brand
neither member is tagged with).

```
GET .../team-members?agent_code=general_enquiries&team_code=marketing_product&tier=1&brand_code=bravat
-> [ { "name": "NOOR HASNI HUSIN", ... } ]   (Tay Zhi Yang correctly excluded)
```

With `bravat`, the roster narrows to **only** the untagged member (NOOR HASNI HUSIN) - Tay Zhi
Yang, who is tagged `mocha`/`cabana` but not `bravat`, is correctly excluded. This is the
discriminating evidence the original step 3 needed: tagged members are filtered IN only for their
own brand(s), untagged members pass every filter, and the pool genuinely restricts rather than
always returning the whole team. `next-assignee` was not called, per the brief.
- **AC2-R1 (pool rule: tagged-for-brand OR untagged, tagged member excluded from a
  non-matching brand): PASS - verified discriminating case, not just the trivial one.**

### Step 4 (redo) - untag / restore original state: PASS

- Re-opened "Edit brands" on Tay Zhi Yang's row: both `MOCHA` and `CABANA` read checked
  (`checked=true`), matching the saved state. Unchecked both (`21-redo-unchecked.png`). Clicked
  `Save`.
- `PUT .../brands` body `{"codes":[]}` -> **`200`**, response body `{"codes":[]}`.
- `reload`, re-expanded `marketing_product` Tier 1: both members read "All brands" again
  (`22-redo-restored.png`) - byte-for-byte the pre-run baseline. This evidence run leaves no
  residue on the shared dev DB.
- **AC2-V1 step 4 (untag, restore exactly): PASS.**

### Step 5 (redo) - 375x812 viewport check: PASS

- `set viewport 375 812` (member-row disclosure state carried over from 1280px - still expanded).
  Scrolled it into view. Screenshot `23-redo-mobile-untagged.png`: the member row now **stacks**
 - "Tay Zhi Yang" on its own line (fully readable, not clipped), "(staff@example...)"
  truncated with an ellipsis (not collapsed to 0px) on the line below, respond-status icon to the
  right, then "Serves all" / "All brands" chips on their own line, then the "Next in line" /
  "Last assigned" position badge on its own line below that. No horizontal clipping, no cut-off
  text, name and email both legible.
- Re-tagged Tay Zhi Yang at this same 375px width to see actual (non-placeholder) chips wrap:
  opened "Edit brands" (`24-redo-mobile-editor-open.png` - popover itself renders cleanly at
  375px, no clipping), checked `MOCHA` + `CABANA`, `Save` -> `PUT .../brands` **`200`**.
  Screenshot `25-redo-mobile-chips-wrapped.png`: the `CABANA` and `MOCHA` chips render side by
  side on their own line beneath "Serves all", fully visible, no overflow - "chips wrapped"
  confirmed, not just the empty "All brands" placeholder.
- DOM check: `document.documentElement.scrollWidth` (494) vs `window.innerWidth` (375) still shows
  the SAME pre-existing, out-of-scope page-header overflow logged in the revision-1 run (the
  `flex items-center justify-between` title/Edit/Delete row) - re-confirmed the `Team Assignments`
  card itself is fully contained (`clientWidth === scrollWidth === 341`, no overflow), i.e. the
  member-row fix introduced no new page-level overflow.
- Untagged again (unchecked `MOCHA` + `CABANA`, `Save` -> `PUT .../brands` `{"codes":[]}` ->
  **`200`**) while still at 375px, then reset `set viewport 1280 800`, `reload`, and re-confirmed
  the final state: both members read "All brands" (`26-redo-final-1280-restored.png`) - fully
  restored, no residue, no stray toasts this time.
- **AC2-V1 step 5 (375px, name block readable, chips wrapped, no clipping): PASS.**

### Step 6 - console / errors (first pass + redo)

Checked after every interaction across both passes (open, sidebar clicks, search, detail open,
disclosure expand, brand-editor open/check/save/cancel x2 (tag + untag) x2 (1280px + 375px),
reloads, viewport changes). Zero uncaught page errors (`errors` always empty) in either pass.
Console noise, none of it a brand-feature regression:
- Routine `[debug] JWT token extracted successfully` / i18next init lines throughout both passes.
- First pass only: a run of `[warning] No auth token available; falling back to cookies`
  accumulated later in the session, correlating with a `Failed to fetch upload activity (401)`
  toast and a `Failed to fetch unread count` toast - looked like a background-polling
  session/token refresh timing artifact over a long-running headless session. The redo (fresh
  login, shorter session) showed none of this noise and no stray toasts in its final screenshot -
  consistent with that read, not a brand-feature bug either time.

### Step 7 - close session

`close` (session-scoped, `--session brand-routing-member`) run once at the end of each pass. Never
`close --all`.

### Screenshots (scratchpad, this run)

First pass (steps 1, 2, 3, 5 as originally attempted, before the fixes): `00-home.png`,
`01-list-check.png`, `02-search-general-enquiries.png`, `03-agent-detail.png` (full-page, all six
team sets, no tier-row brand UI), `04-after-expand-click.png`, `05-scrolled-tier1.png`,
`06-expand-attempt2.png` / `07-step1-member-brands-editor.png` (member rows with both editors
visible - AC2-F1 evidence), `08-brand-editor-open.png`, `09-mocha-cabana-checked.png`,
`10-after-failed-save.png` (the pre-fix 400), `11-cancelled-popover.png`, `12-mobile-top.png`,
`13-mobile-tier1-expanded.png` (the pre-fix 375px clipping finding), `14-mobile-fullpage.png`,
`15-final-1280-restored-state.png`.

Redo (steps 2, 3, 4, 5, after both fixes landed): `16-redo-tier1-expanded.png`,
`17-redo-brand-editor-open.png`, `18-redo-checked.png`, `19-redo-saved-200.png` (200 + chips
appear), `20-redo-after-reload-persists.png` (chips survive a full reload),
`21-redo-unchecked.png`, `22-redo-restored.png` (back to "All brands"),
`23-redo-mobile-untagged.png` (name/email readable, stacked, no clipping),
`24-redo-mobile-editor-open.png` (popover clean at 375px), `25-redo-mobile-chips-wrapped.png`
(CABANA/MOCHA chips wrapped on their own line), `26-redo-final-1280-restored.png` (final restored
state, clean).

### Summary

| AC | Result |
|----|--------|
| AC2-F1, member rows carry Brands editor next to market-segment editor, no tier-row brand UI | **PASS** |
| AC2-F1, PUT payload lower-case `codes` array | **PASS** |
| AC2-V1 step 2, tag saves + chips persist on reload | **PASS (redo)** - `PUT .../brands {"codes":["mocha","cabana"]}` -> `200 {"codes":["cabana","mocha"]}`; chips survive a full reload. First-pass FAIL (case-sensitivity bug) fixed and committed by the coordinator before this redo. |
| AC2-R1, roster probe (`team-members`, brand_code=mocha/cabana/none/bravat) | **PASS (redo)** - the trivial mocha/cabana/none probes returned the same roster (correct, since Tay Zhi Yang is tagged both brands); the added discriminating `brand_code=bravat` probe returned only the untagged member, proving the pool genuinely restricts tagged members to their own brand(s). |
| AC2-V1 step 4, restore original state | **PASS** - untagged via the UI (`PUT {"codes":[]}` -> `200`), reload confirms "All brands" restored exactly. |
| AC2-V1 step 5, 1280px member-row structure | **PASS** |
| AC2-V1 step 5, 375px member-row - no clipping, chips wrapped | **PASS (redo)** - name and email fully readable on their own stacked line; brand chips (MOCHA/CABANA) render wrapped on their own line with no overflow. First-pass FAIL (0px-width collapse) fixed and committed by the coordinator before this redo. |
| Console / errors | Clean of uncaught errors in both passes; first-pass late-session auth-warning/401-toast noise (session-refresh timing, not a brand-feature bug) did not recur in the redo. |

**Net: both bugs found on the first pass - the backend case-sensitive brand-code validation and the
375px member-row 0-width collapse - are confirmed fixed by this redo. AC2-F1 and AC2-V1 (the member-
level round trip: tag -> `PUT 200` -> reload -> chips persist -> roster probe reflects the pool
rule -> untag -> restore -> 375px readable/wrapped) all PASS against the live stack. The dev DB was
left in its original state (no residual tags) at the end of the run.**
