# PLAN: never-stuck safety nets (levers L6, L8, L9)

Status: in review, PR #1419 (small fix track: FE only, no migration, no RBAC change; the permission
gate's *failure* path changes, not who is allowed). Lane NS-SAFETY-NETS.

Standard: `documentation/reference/NEVER-STUCK-UI.md` (S3, S5). Audit:
`documentation/reports/AUDIT-never-stuck-2026-10-01.md` (PR #1416). Owner rule, 1 Oct 2026:
nothing may hang; every failure ends in a clear, honest state.

Out of scope: session / sign-out (L1, lane SESSION-NEVER-STUCK), shared-lookup permissions
(L10, owner call pending), DataGrid error state (L2), pickers (L5), polling (L7).

## Shared fixes

### L8: boundaries (rows 33, 40)

- `components/common/RouteErrorScreen.tsx`: one fixed-copy error screen (never
  `error.message`; `digest` shown as a reference). Detects a chunk-load failure (deploy skew:
  the browser holds an old build whose chunks are gone) and offers **Reload** (full
  `window.location.reload()`, the only thing that fetches the new build) instead of `reset()`.
- `app/global-error.tsx` + `components/common/GlobalErrorView.tsx`: own `<html>`/`<body>`,
  plain markup and inline styles, no UI-primitive imports (the root layout's providers, CSS or a
  shared chunk may be what failed), Reload button. Catches a failed `ssr:false` chunk load of
  `DynamicClientProviders`.
- `error.tsx` in `app/(auth)`, `app/(public)`, `app/unsubscribe`, all rendering
  `RouteErrorScreen`, none with a staff link: `(auth)` also holds customer pages (portal,
  quotation-sign, view, approval, forms). `(protected)/error.tsx` keeps its copy and gains the chunk-load Reload.

### L6: permission-load failure is not "no access" (row 34)

- `usePermissions()` exposes `isError` = the permissions query failed and holds no data
  (a failed background refetch over a good cached set is still "data").
- `RequireAccess` (permission mode): `isError` renders `PermissionsLoadError` ("Could not
  check your access" + Retry calling `refetch`), never `AccessDenied`.
- `useHasPermission` / `useHasAnyPermission` consumers (452 call sites) cannot each render an
  error, so the protected layout shows one banner with Retry while the permissions query is
  failed. Buttons stay hidden (fail closed) but the user is told why and can retry.

### L9: detail layouts branch on error before drawing tabs (rows 11, 12, 15)

- `components/common/LoadErrorState.tsx`: "Could not load X" + message + Retry, and
  `QueryErrorState` (403 -> `AccessDenied`, else `LoadErrorState`). `lib/api-client.ts` gains
  `apiError` (status-carrying), `isAccessDenied`, `isNotFound` (S3 snippet) and
  `retryUnlessRefused` (one retry for a fault, none for a 403 / 404). The three layout queries
  and the permissions query set `meta: { silent: true }` (S4.3): the failure is shown in place,
  not toasted on top. `query-provider.tsx` uses `isAccessDenied` for its permission toast.
- A failed background refetch over data already shown keeps the data in all three layouts.
- `user-management/users/[id]/layout.tsx` (row 11): error -> AccessDenied / not-found /
  LoadErrorState before the tabs render; the 404 redirect moves out of `queryFn` into an
  effect on the settled error, using `router.replace` (S6.4).
- `user-management/settings/layout.tsx` (row 12): a failed settings read renders
  LoadErrorState instead of the tab bodies, so no tab can draw (and Save) blank defaults.
- `user-management/contacts/[id]/layout.tsx` (row 15): same, no endless skeleton, no "not
  found" for a refused or failed read. `page.tsx` needs no change: the layout no longer renders
  `children` until the record is there.

## Tests (red first, vitest)

1. L8: `RouteErrorScreen` shows fixed copy (not the message), the digest, Retry calls
   `reset`; a ChunkLoadError shows Reload. `global-error` renders html/body + Reload.
   Each route group has an `error.tsx` (file presence test).
2. L6: `RequireAccess` with a failed permissions query shows "Could not check your access" and
   Retry refetches; denied still shows AccessDenied; banner shows on failure only.
3. L9: each of the three layouts with a 500 renders the error + Retry and no tab body; a 403
   renders AccessDenied; settings layout with a failed read renders no Save button.

Kill test: revert each fix, the matching test goes red.

Found in the browser pass and fixed here (L6): with `/me/permissions` failed, `usePermissions`
returned a new `[]` and `Set` on every render. `SearchDialog`
(`app/components/partials/dialogs/search/search-dialog.tsx:71-87`, always mounted in the demo1
header) has `permissions` in an effect's deps and sets state there, so it re-ran forever and the
renderer sat at ~95% CPU (pre-existing on main, reproduced there). The hook now returns a stable
empty array and a memoised set. Left open, not this lane: `sidebar-menu.tsx` keys root groups by
index and shows the unfiltered menu while `isLoading`, so a group can flip between two menus
while a failed permissions query refetches.

## Evidence run (agent-browser, cloud sandbox, empty bootstrapped DB, all modules installed)

Users: an admin and a salesperson seeded into the throwaway DB. Failures induced with
`network route <url> --abort`.

1. Admin, Settings (sidebar Users & Access > Settings) with
   `/api/v1/user-management/settings*` aborted: two attempts (one retry), then "Could not load
   settings" + Retry, no tab strip, no "Save Settings". 1280 and 375, no horizontal scroll
   (scrollWidth 375). Unroute + Retry: tabs and real values render.
2. Admin, user detail (Administrative Users > row) with the user's record read aborted: "User"
   header + "Could not load this user" + Retry, no tabs, no skeleton. 1280 and 375 (scrollWidth
   375). Unroute + Retry: hero, Profile and Activity Logs tabs render.
3. Salesperson, `/me/permissions` aborted at sign-in: the shell banner "Could not check your
   access, so some menus and actions are hidden." + Retry on every page; a sidebar group opens
   without freezing (renderer ~7-10% CPU after the fix, ~95% before); `/project-sales/pipeline`
   (under `RequireAccess`) shows "Could not check your access" + Retry, not AccessDenied, at 1280
   and 375. Unroute + Retry: the pipeline page shows AccessDenied, the honest answer for this
   role. `/user-management/settings` as the salesperson (backend 403
   `Permission required: user_management.settings.view`) shows AccessDenied through the L9 path.

L8 (crash screens) is covered by unit tests only: a chunk-load failure of the client providers
cannot be induced on a dev server without breaking every script.

