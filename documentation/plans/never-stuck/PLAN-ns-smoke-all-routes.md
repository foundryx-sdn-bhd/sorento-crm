# PLAN: NS-SMOKE-ALL-ROUTES (Never-stuck guard G4)

Status: Review, ready for merge (small fix track: test + CI + three marker attributes, no migration, no auth/RBAC
change, no product behaviour change). UAC: `ns-smoke-all-routes-acceptance-criteria.md`.

Standard: `documentation/reference/NEVER-STUCK-UI.md`. Source: audit
`documentation/reports/AUDIT-never-stuck-2026-10-01.md` section 7, G4. Owner approved this ONE new
Playwright spec on 1 Oct 2026 as an exception to the no-new-specs order.

## Journey

The owner opens a page and it spins forever, or a restricted user sees "No proforma invoice"
where the truth is "you cannot see that". Nightly, a robot opens every page as three people and
names each page that is not in a final, honest state, so a regression is caught the morning after
it merges instead of when a user hits it.

## What ships

| Piece | File |
|---|---|
| Seed: admin, restricted (procurement `.view` slugs only), expired-session user, all on the default company; localhost DBs only | `sorento_crm_backend/scripts/seed_never_stuck_smoke.py` |
| Route walk (every `page.tsx` under `app/(protected)`, route groups stripped, Metronic demo dirs excluded), persona fixtures | `sorento_crm_frontend/e2e/never-stuck/routes.ts` |
| Global setup: NextAuth credentials sign-in per persona, storage state saved; expired persona's FastAPI session revoked through `POST /api/v1/auth/logout` after sign-in | `e2e/never-stuck/global-setup.ts` |
| The spec | `e2e/never-stuck.smoke.spec.ts` |
| Known failures (audit row id per entry, run as expected failures) | `e2e/never-stuck/known-failures.json` |
| Markdown summary of the JSON report | `e2e/never-stuck/summarize.mjs` |
| Config (parallel, JSON + HTML reporters) | `playwright.never-stuck.config.ts` |
| Loading marker `data-loading` | `components/ui/skeleton.tsx`, `components/common/screen-loader.tsx` |
| Refusal marker `data-access-denied` | `app/components/common/AccessDenied.tsx` |
| `/api/v1` rewrite on a production build when `NEVER_STUCK_API_PROXY=1` at build time | `next.config.mjs` |
| One-command runner (bootstrap, seed, BE, FE build + start, smoke, summary) | `scripts/never-stuck-smoke.sh` |
| Nightly job (cron + dispatch, skips when main has not moved since the last green run) | `.github/workflows/never-stuck-nightly.yml` |

## Decisions

- **Marker attribute is `data-loading`, not `data-testid`.** The audit's G4 names
  `[data-loading]`; `Skeleton` spreads caller props, so a `data-testid` there would be overwritten
  by any caller that sets its own (and would overwrite theirs if placed after). `Skeleton` covers
  `SectionSkeleton`, `ListPageSkeleton` and `LayoutLoadingFallback`, which compose it. Hand-rolled
  spinners (`animate-spin` outside a button) are also counted as loading.
- **Expired = revoked.** The audit's G4 recipe: sign in, revoke the `user_sessions` row. The
  NextAuth cookie stays valid, so this is the owner's reported state (FastAPI 401
  `session_revoked`), not a plain signed-out visit.
- **Dynamic routes** use the seeded record where the seed has one (user detail), else a
  missing-record id. A detail page on a missing record must still settle (not found / error);
  that is the audit's "skeleton forever on any error" class.
- **Restricted role = procurement viewer**, the role in the audit's top rows (packing-list tabs
  reading SCM endpoints, PR #1413).
- **Refusal rendered as empty** = a 403 on `/api/v1/` during the visit, no `[data-access-denied]`
  on screen, and empty / not-found copy on screen.
- **Known failures are reported, not failed.** Each entry carries an audit row id and the
  problem kinds it tolerates (`route: "*"` covers a persona's every route); a different kind
  of problem on the same route still fails, so a known row cannot hide a new regression. The route still runs; its problems go into
  the summary's folded "still failing" table and the night stays green for it. One that
  passes is listed under "passed tonight" so the entry gets retired. Not `test.fail()`
  (a ratchet that fails the night a known row passes): measured, the expired-session
  redirect is timing-bound and flaps run to run, so a strict ratchet would page on noise.
- **2 Playwright workers.** Measured on a 4-core sandbox: at 4, the backend saturates
  (`/me/permissions` 3-7 s) and the dashboard's loaders flap past 15 s for reasons that are
  load, not product; at 2, the packing-list slice was 24/24 twice.
- **Settled** means: the document reached `load`, then no loader on two reads 500 ms apart
  inside the 15 s budget (a blank pre-render page never counts as settled).
- **Runner safety.** Local DB named `*_smoke` / `*_ci` only (seed and runner both refuse
  anything else), backend reads no `.env` (`SORENTO_ENV_FILE` = empty file), its own
  `NEXT_DIST_DIR=.next-never-stuck`, refuses busy ports.
- **Cost**: one job, nightly + dispatch only, never on PR or push; skips itself when main's head
  already passed. No labels touched.
- **Production build in CI**, not `next dev`: dev compiles each route on first hit, which would
  read as "stuck" on a 15 s budget. `NEXT_SKIP_TYPECHECK=1` as in the Docker build.

- **Modules enabled in the seed.** A fresh bootstrap installs no tenant module, and
  `ModuleRouteGuard` then sends every module route to `/`: the first baseline "passed" by
  checking the dashboard. The seed enables every catalog module, and landing on `/` unasked
  is now a failure, so that cannot recur silently.

## Baseline (sandbox, 1 Oct 2026)

1101 visits: 997 pass, 104 fail, 8 redirected (all to a sibling route, none to `/`). Re-run on
the final code: 997 pass, 102 known, 1 new (`restricted /forms-management/forms/new`, a picker
refusal that flaps with render timing; added as known, 104 entries).
Expired persona: every route reached `/signin` within 5 s at 2 workers. 103 failures are in
`known-failures.json` (the 104th was a regex false positive, fixed): rows 11, 12, 15, 16, 17,
18, 35, 36 where a top-40 row matches, else L2 (24), L3 / S4.3 (17), S3 + S4.4 (31), S3
skeleton forever (10).

## Not in scope

Fixing any screen the smoke flags (fix lanes NS-SESSION / NS-GRID / NS-ACCESS / NS-BOUNDARIES
own those). Seeding a record per detail route (trigger to add one: a fix lane that wants its
detail page smoked with real data adds its row to the seed's `records`).
