# Never-stuck UI standard

Status: binding for new frontend work from 1 Oct 2026 (lane PERM-UI-AUDIT, scope extended by the
owner the same day: "I don't want anything stuck in the system for this kind of error").
Audit of the current gaps, ranked, with file:line: `documentation/reports/AUDIT-never-stuck-2026-10-01.md`.

## The rule

**Every screen, tab, panel, picker and job reaches a final, honest state within a bounded time.**
The final states are exactly:

| State | What the user sees |
|---|---|
| data | the thing they asked for |
| empty | "no X yet", only when the server said so with a 2xx |
| no access | `AccessDenied` (page) or its inline variant (tab, panel, picker) |
| error | a short message and a **Retry** that actually refetches |
| signed out | one redirect to `/signin?callbackUrl=<where they were>` |
| job finished | done / failed / timed out, with what happened |

A skeleton or spinner is never a final state. Neither is an empty state reached because a request
failed, nor a "not found" reached because a request was refused.

Why this exists: PR #1413 found packing-list tabs reading SCM endpoints that need
`scm.dashboard.view`, never checked by the UI, whose bodies branched only on `isLoading`, so a 403
read as "No proforma invoice". The owner then hit the same packing-list screen hanging on a spinner
until they signed in again. The audit found the same shapes on every module.

The backend is the enforcement. The frontend's job is to never *show* something the backend will
refuse, to say "no access" honestly when it does refuse, and to never leave the user waiting on
something that is not going to arrive.

## Facts the rules rely on

- Backend gates: `require_permission(slug)` / `require_any_permission([...])` and their
  `_with_api_key` twins, `sorento_crm_backend/app/dependencies.py:384-560`, plus a module guard
  per router in `app/api/v1/__init__.py`. Their 403 `detail` strings start with
  `Permission required:`, `One of these permissions required` (with or without
  ` (module may be disabled)`) or `Module not enabled:` (`dependencies.py:396,456,462`,
  `modules/runtime/guards.py`).
- Superadmin and admin pass every backend gate (`services/user_service.py:1501`), and
  `GET /user-management/users/me/permissions` returns them every slug that exists as a row in
  the permissions table (`user_service.py:1468-1470`).
- FE errors are plain `Error(message)` built by `extractApiError` (`lib/api-client.ts`); the HTTP
  status is not carried. Today the only way to recognise a 403 is the message prefix.
- Two sign-out exits exist: `apiFetch` sends a `/api/v1/` 401 carrying a session-dead `code` to
  `_maybeForceSignOut` (`lib/api.ts:198-219,540-550`), and the protected layout redirects when
  NextAuth reports `unauthenticated` (`app/(protected)/layout.tsx:35-54`). There is no
  `middleware.ts`. NextAuth never validates the FastAPI token after login.
- `apiFetch` and the shared token fetch have no timeout (`lib/api.ts:138,540`). React Query
  default is `retry: 1`, `staleTime: 30s` (`providers/query-provider.tsx:37-43`), version 5.
- `DataGrid` has no error state: a failed load renders `emptyMessage` / "No data available"
  (`components/ui/data-grid.tsx:105-131`, `components/ui/data-grid-table.tsx:1085`).
- Helpers that already exist: `usePermissions` / `useHasPermission` / `useHasAnyPermission`
  (`hooks/usePermissions.ts:9-35`), `RequireAccess` + `AccessDenied`
  (`app/components/common/`), menu gating via `permission` / `permissionsAny`
  (`config/types.ts:17-20`), the per-query toast opt-out `meta: { silent: true }`
  (`query-provider.tsx:54`), the route error boundary `app/(protected)/error.tsx`, and the
  stall guard in `scm/reorder/hooks/useReorderRun.ts:98` + `RunProgressCard.tsx:28-37`.

---

## S1. Session: one 401 path, one redirect

Today there are two exits and neither is safe under load: `_maybeForceSignOut`
(`lib/api.ts:198-219`) checks its `_signingOut` latch before an `await` and sets it after, so all
~10 parallel 401s of a detail page each run an untimed `await signOut()` before navigating; and
the protected layout (`app/(protected)/layout.tsx:35-54`) shows `ScreenLoader` behind one soft
`router.push` with no timeout. A failed token fetch sends the request with no Bearer, which the
backend answers with an uncoded 401 (`dependencies.py:289-296`) that neither exit handles.

1. **Every authentication 401 carries a session code.** The backend's `get_current_user` 401s
   all get a `detail.code` (`session_expired`, `session_revoked`, `session_invalid`, and a new
   `session_missing` for "no token"). The FE redirects on any of them. The code gate stays
   (`lib/api.ts:186-193` explains why: a 401 from an unrelated integration must not log everyone
   out), and an uncoded 401 renders as S3's error state, never a hang. A non-auth exception during
   authentication is a 503, not an uncoded 401 (`dependencies.py:318-327`).
2. **A failed or timed-out token fetch is a dead session.** When `/api/auth/token` returns non-OK
   or no token, `apiFetch` does not send an unauthenticated request; it takes the redirect.
3. **One latch, set synchronously, then navigate at once.** The latch is set before any `await`.
   The redirect is `window.location.replace('/signin?callbackUrl=...')`, issued immediately;
   `signOut()` runs fire-and-forget with a short timeout, or `/signin` clears the cookie itself.
   The protected layout's `unauthenticated` branch uses the same function, so N 401s plus the
   session broadcast still produce exactly one navigation.
4. **Every exit clears client state**: the token cache (with a generation counter so an in-flight
   token fetch cannot repopulate it), the React Query cache, and the view-as store.
5. **`callbackUrl` is path + query, basePath stripped, never a `/signin...` URL.**
6. **401 is never retried** by React Query (see S2 `retry`).
7. **View-as (impersonation):** a stale view-as header is not silently ignored (today
   `dependencies.py:192-213` serves the admin's own data while the banner says "viewing as X").
   The backend answers it with a coded refusal (`impersonation_ended`); the FE clears the view-as
   store and reloads the current page as the real user, without signing them out. `/current`
   applies the same ACTIVE-target check as the header path (`impersonation.py:194-212`).
8. **NextAuth `status === 'loading'` has a ceiling** (10 s): past it, re-check the session once,
   then take the redirect. No endless `ScreenLoader`.

```ts
// lib/api.ts (target shape)
let _exiting = false;
export function exitToSignIn(): void {
  if (_exiting) return;
  _exiting = true;                                    // before any await
  clearClientState();                                 // token cache, query cache, view-as store
  void withTimeout(signOutQuietly(), 3000);           // fire and forget
  window.location.replace(signInUrl(currentPathWithoutBasePath()));
}
```

## S2. Fetch: a timeout on every request, bounded retry, no endless polling

1. **`apiFetch` applies a timeout** via `AbortSignal.timeout`, merged with any caller signal
   with `AbortSignal.any`: 10 s for the token fetch, 30 s for reads, 120 s for writes and the AI
   chat; uploads and exports pass an explicit longer value. An abort becomes an error whose
   message is "The server took too long to answer." so it renders as S3's error state.
2. **Shared retry rule** (one function, in the query provider):

```ts
retry: (failureCount, error) =>
  failureCount < 1 && !isAccessDenied(error) && !isSignedOut(error) && !isNotFound(error),
```

3. **Every `refetchInterval` is a function that returns `false`** when the status is terminal,
   when the query is erroring, or when a ceiling is passed. A constant number is allowed only for
   ambient polls (notifications, inbox) and those set `refetchIntervalInBackground: false`.

```ts
refetchInterval: (q) => {
  if (q.state.status === 'error' || q.state.fetchFailureCount >= 3) return false;
  if (isTerminal(q.state.data?.status)) return false;
  if (Date.now() - startedAt > JOB_CEILING_MS) return false;   // UI then shows "timed out"
  return 2000;
},
```

4. **Hand-rolled loops** (`setInterval`, recursive `setTimeout`) are replaced by the same query
   pattern. If one must stay, it has an attempt cap, stops after 3 consecutive errors, and clears
   on unmount. `catch { /* ignore */ }` inside a poll is a defect.
5. **Streams (SSE)** have an idle watchdog (no frame, keepalive included, for ~45 s: abort,
   reconnect, report `connected: false`) and a capped reconnect delay; after N failures the UI
   says it is offline.

## S3. Every data consumer renders five states

Loading, no access, error with Retry, empty, data. Branching on `isLoading` alone is a defect, and
so is `if (isLoading || !data) return <Skeleton/>` (an error leaves `data` undefined: skeleton
forever), and so is `!data ? <NotFound/>` (a refusal or a 500 is not "not found").

```tsx
const { data, isLoading, error, refetch } = useProformaInvoice(id, { enabled: canSeePi });

if (isLoading) return <TabSkeleton />;
if (error) {
  if (isAccessDenied(error)) return <AccessDenied variant="inline" />;
  if (isNotFound(error)) return <NotFoundState />;
  return <InlineError message={error.message} onRetry={() => refetch()} />;
}
if (!data) return <EmptyState title="No proforma invoice" />;
return <ProformaInvoiceView pi={data} />;
```

- **Lists:** `DataGrid` gets `error` and `onRetry` props and renders the denied / error row itself
  (inline `AccessDenied`, or message + Retry), so every list passes `error={query.error}` and the
  per-list `isError` branches go away. This one change covers most list rows in the audit.
- **Pickers / selects:** a select hook never turns a failure into `[]` (no `.catch(() => [])`, no
  `if (!res.ok) return []`, no toast-then-`res.json()`). It throws; the select shows "No access"
  or "Could not load, retry" in its menu, and a form that needs the options does not submit empty
  values it could not read.
- **Defaults are not data:** `useQuery({ ... })` with `data = <default object>` must still branch
  on `error` before rendering a form from it. A form that renders defaults after a failed read can
  save the defaults over real config (the settings layout does today).
- **Permissions query failure is not "no access":** if `/me/permissions` fails, `RequireAccess`
  and `useHasPermission` consumers show "Could not check access" + Retry, not `AccessDenied`.

The helpers the snippets use go in `lib/api-client.ts` next to `extractApiError`. They do not
exist yet; the first fix lane adds them:

```ts
const ACCESS_DENIED_PREFIXES = [
  'Permission required:',
  'One of these permissions required',   // covers the "(module may be disabled)" variant
  'Module not enabled:',
];
export function isAccessDenied(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : '';
  return ACCESS_DENIED_PREFIXES.some((p) => msg.startsWith(p));
}
```

If a status-carrying error is added later (recommended: `extractApiError` callers throw an `Error`
with `status` set, the way `codedError` sets `code`), only these helpers change; call sites stay.
`providers/query-provider.tsx:83-85` must use `isAccessDenied` too: it matches only
`One of these permissions required:` with the colon, so it misses the strict-mode variant and
`Module not enabled:`, which then toast the raw backend string.

## S4. Permissions: gate what the backend gates, and say "no access" in place

1. **One permission constant per route group, shared by FE and BE.** Backend routers declare
   module-level constants (`SCM_READ = "scm.dashboard.view"`, `_READ = require_permission(SCM_READ)`)
   and every route uses them. The FE service that owns the URL exports a `*_PERMS` object with a
   comment naming the backend constant; menu, tabs, buttons and `RequireAccess` import it. A slug
   string literal in a component is a review defect.

```ts
// app/(protected)/scm/services/proformaInvoiceService.ts
/** Mirrors SCM_READ / SCM_EDIT in app/api/v1/scm/proforma_invoices.py. */
export const PROFORMA_PERMS = { read: 'scm.dashboard.view', edit: 'scm.dashboard.edit' } as const;
```

2. **Tabs, menu items and buttons are gated by the slug the route requires** (the read slug for a
   tab or page, the write slug for a button). A hidden tab is removed from the list; its query is
   `enabled` only when allowed. A button that always 403s is not shown.

```tsx
const tabs = [
  { value: 'lines', label: 'Lines' },
  canSeePi && { value: 'proforma', label: 'Proforma invoice' },   // cross-module, see 5
].filter(Boolean) as TabDef[];
```

3. **A 403 renders `AccessDenied` in place: no toast, no retry.** Page: full `AccessDenied`. Tab,
   panel, picker: the inline variant (an `inline` prop, added by the first fix lane: smaller, no
   "Back to dashboard"). Never a raw red card with `Permission required: x.y.z`. A component that
   renders all five states sets `meta: { silent: true }` on its query; the global toast stays as
   the fallback until the audit rows are closed.
4. **Every deep-linkable page is under a `RequireAccess`** with the slug of its primary read, in
   the `page.tsx` or the segment `layout.tsx` (only when every child needs the same slug: a
   layout that over-gates hides pages from roles the backend allows, as `scm/layout.tsx` does for
   `scm.reorder.run` / `scm.policy.manage` holders).
5. **Cross-module reads are declared.** A screen in module A reading module B imports B's
   `*_PERMS`, gates on it, enables B's query only when allowed, and renders B's 403 inline so the
   rest of A stays usable. B's module guard applies too (`Module not enabled:`).
6. **Shared lookups are not admin reads.** A picker that many modules need (users select, status
   graph, roles select, reference data) must not sit behind the admin slug of the module that
   owns the table. Either the backend exposes a lookup route gated by authentication + module
   guard (precedent: `/project-sales/quotation-approval-graph`, `quotation_documents.py:395`,
   which exists precisely so salespeople do not need `system.statuses.view`), or the consumer is
   gated per rule 5. Which one, per lookup, is an owner call recorded in the audit.
7. **Record-level `can_edit` is not a slug.** Where an action's route needs
   `projects.projects.delete`, the button checks that slug as well as `can_edit`.

## S5. Boundaries: a thrown error never leaves a blank or frozen screen

1. `app/global-error.tsx` exists (own `<html>`/`<body>`, Reload button). It catches what escapes
   the root layout, including a failed `ssr:false` chunk load of the client providers
   (`components/DynamicClientProviders.tsx`).
2. Every route group has an `error.tsx`: `(protected)` has one; `(auth)` (sign-in is the entry
   point for every user), `(public)` and `unsubscribe` need one.
3. A detail segment whose layout fetches the record (`users/[id]`, `contacts/[id]`,
   `packing-lists/[id]`, ...) handles its own error in the layout (S3) because a stuck layout
   takes every child tab with it. `loading.tsx` is optional for client pages; the root
   `<Suspense>` gets a fallback.
4. `error.tsx` copy stays fixed (never `error.message`, see `(protected)/error.tsx:38-46`).

## S6. Navigation never loops

1. Redirects in effects use `router.replace`, never `push` (no Back-button trap).
2. No effect redirects on `!useHasPermission(...)`: it is `false` while permissions load, so the
   redirect fires for users who do have access. Gate with `RequireAccess` instead.
3. URL-sync effects return early when the URL already matches (the pattern
   `SpecVerificationList.tsx:310-335` already documents).
4. No redirect inside a `queryFn` (it runs once per retry). Redirect from an effect on the
   settled error.
5. `callbackUrl` is same-origin, starts with `/`, and does not start with `/signin`.

## S7. Long jobs show progress and reach a terminal state

1. **The job row has a terminal state the worker cannot forget.** Exceptions write `failed` in
   the task's `except`; a SIGKILLed or never-started job is failed by a sweeper (age past the
   job's own timeout, or no live RQ job). Precedents: `download_service.fail_stale` (20 min),
   `_reconcile_orphan_import_jobs`, `project_extraction_recovery_service`,
   `chatbot_turn_sweep`. A `processing` row must never double as a dedupe lock without an expiry.
2. **The task publishes progress from its first second** (total known, then counts), not only at
   the end (PR #1414: a DO apply job read STARTED 0/0/0 for its whole run).
3. **The UI has a ceiling derived from the job's own timeout.** Past "expected", it says "Taking
   longer than expected"; past the timeout, it shows "Timed out" with Retry and stops polling.
   `useReorderRun.ts:98` + `RunProgressCard.tsx` is the template.
4. **Queued with no worker is visible.** A job queued for more than N minutes says the worker may
   be down; the backend fails it once no worker has picked it up within its timeout.
5. **Uploads are cancellable and time out**: the `AbortController` signal reaches the uploader.

---

## Review checklist (the short form lives in `PR-CHECKLIST.md`, "Never stuck")

- [ ] Every query consumer renders loading / no access / error + Retry / empty / data; no
      `isLoading || !data` skeleton, no `!data` "not found", no `.catch(() => [])`.
- [ ] Lists pass `error` to `DataGrid`; pickers surface failure instead of `[]`.
- [ ] Every new endpoint call goes through a service exporting `*_PERMS` naming the backend
      constant; tabs / menu leaves / buttons gate on it; no slug literals.
- [ ] Cross-module reads import the other module's `*_PERMS`, gate on it, render 403 inline.
- [ ] 403 renders `AccessDenied` in place, query is `meta: { silent: true }`, no retry.
- [ ] Every new `page.tsx` sits under a `RequireAccess`.
- [ ] Every `refetchInterval` is a function with an error stop and a ceiling.
- [ ] A new background job has a terminal failed state reachable without the worker, publishes
      progress, and its UI has a "timed out" state.
- [ ] Redirects use `replace`; none fire on permission-loading `false`; none inside `queryFn`.
- [ ] A new backend slug is seeded as a permissions row (else admins are hidden from it in the UI).

## Automated guards

Proposed in the audit report (section 7, "Guards"): a route-permission manifest test (backend) and
service-to-manifest test (frontend), a five-state / polling ratchet test, and a stuck-screen
Playwright smoke that opens every route as admin, as a restricted user and with an expired
session, and fails if anything is still loading after N seconds.
