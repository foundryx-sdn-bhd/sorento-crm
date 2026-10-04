# UAC: NS-SMOKE-ALL-ROUTES (never-stuck guard G4)

Plan: `PLAN-ns-smoke-all-routes.md`.

1. The spec visits every `page.tsx` route under `app/(protected)` except the Metronic demo dirs
   (`/public-profile`, `/network`, `/dark-sidebar`, `/i18n-test`), once per persona: admin,
   restricted, expired. A new `page.tsx` joins with no registration.
2. Admin / restricted: a visible `[data-loading]` element or a spinner outside a button 15 s after
   navigation fails the route with "still loading after 15s".
3. Expired: a route not on `/signin` within 5 s of navigation start fails with "expired session
   not on /signin within 5s" and the measured time.
4. Restricted: a 403 from `/api/v1/` plus empty / not-found copy (a line opening with "No" /
   "Nothing", or "not found" / "does not exist") and no `AccessDenied` on screen fails with
   "refusal rendered as empty" naming the refused endpoint(s). Detail routes other than user
   detail open a missing record, so tabs that only load for a real record are not covered yet.
5. Any persona: `Permission required:`, `One of these permissions required` or
   `Module not enabled:` on screen fails with "raw permission text on screen".
6. Admin / restricted landing on `/signin` fails with "live session bounced to /signin".
7. Each entry in `known-failures.json` carries an audit row id and the problem kinds it
   tolerates (`route: "*"` = every route of that persona). Its route still runs; a problem of a
   listed kind is reported in the summary's folded "known failures still failing" table without
   failing the night, a problem of any other kind fails, and a pass is listed under "known
   failures that passed tonight".
8. The nightly job runs on cron and on dispatch only, never on a PR or push, and skips itself
   when main's head already has a green run. Its summary lists counts, new failures, known
   failures that passed, the known failures still failing and the redirects (both folded), and
   any run error; screenshots are in the `never-stuck-report` artifact.
9. The seed script and the runner refuse any `DATABASE_URL` that is not a local database named
   `*_smoke` / `*_ci`.
10. `npm run test:e2e` (the default config) does not pick up the smoke spec.
11. `Skeleton` / `ScreenLoader` render `data-loading` and `AccessDenied` renders
   `data-access-denied` (vitest `components/never-stuck-markers.test.tsx`).
12. The seed installs and enables every catalog module for the default tenant (a fresh bootstrap
   has none, and `ModuleRouteGuard` would then send every module route to `/`). A live persona
   redirected off the requested route is listed in the summary; one that lands on `/` without
   asking for it fails with "redirected to /".
