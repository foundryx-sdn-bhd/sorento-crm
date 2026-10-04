# PLAN: session never stuck (SESSION-NEVER-STUCK)

Status: in review, DoD gate passed pending CI (Track: full - staff auth and impersonation change, so not
the small-fix track). PR #1417. UAC: `session-never-stuck-acceptance-criteria.md`.

## Journey

Owner, 1 Oct 2026, on a crew copy and on prod: while viewing as Kah Xin, the packing-list page
load spun and tab clicks hung (the tab highlights, the page never changes) until they went to
sign-in and signed in again. The server returned the page in 0.2 s, so the hang is client side.
Owner rule: nothing may hang; every failure ends in a clear, truthful state.

## Findings (sandbox reproduction, file:line on main 236b71ca9)

1. **The freeze.** `hooks/usePermissions.ts:11` defaulted `data: permissions = []`, a new array
   every render while permissions were missing (loading, or failed because the session died). The
   universal search dialog (`app/components/partials/dialogs/search/search-dialog.tsx:72-85`,
   mounted in the header on every page) has an effect on `permissions` that always sets state, so
   it re-ran every render. After a click React flushes that update in a microtask; the loop never
   yields and the main thread freezes. CDP `Debugger.pause` mid-freeze stopped in
   `collectMenuItems` < `runUniversalSearch` < `SearchDialog.useEffect`. With a dead session the
   permissions never load, so any click freezes the page until a fresh sign-in.
2. **The silent dead session.** When `/api/auth/token` answered 401, `getCachedAuthToken` returned
   null (`lib/api.ts:139`), apiFetch sent the call with no bearer (`lib/api.ts:482-497`), FastAPI
   answered a code-less `401 Authentication required` (`app/dependencies.py:292-296`), and
   `_maybeForceSignOut` ignored it (`lib/api.ts:203`). Nothing redirected; 2 requests per call.
3. **Two redirects, N sign-outs.** The latch was set after an await (`lib/api.ts:199-204`), so
   parallel 401s each ran an untimed `signOut`; the protected layout did a soft `router.push`
   while apiFetch did a hard redirect. View-as stayed in localStorage.
4. **No cap on the token read** (`lib/api.ts:138`): one stuck request held `_tokenInFlight` and
   every apiFetch awaited it.
5. **Stale view-as was silent** (`app/dependencies.py:209-213`): banner "viewing as X" over the
   admin's own data.

## Design

- `lib/session-end.ts`: one latched `endSessionAndRedirect` (latch before any await, view-as
  cleared, NextAuth sign-out capped 3 s, one hard navigation with the return URL, latch released
  after 5 s if the navigation was cancelled). The protected layout uses it and marks the
  signed-in shell, which is the only place a token-route 401 means "this session died".
- `lib/api.ts`: token read capped 10 s; token-route 401 ends the session (inside the shell);
  dead-code 401 re-reads the cookie once, replays a read with a newer token, never a write;
  calls made while ending answer locally.
- Backend: `_maybe_apply_impersonation` marks `request.state` when it ignores the header;
  `ImpersonationEndedMiddleware` stamps `X-Impersonation-Ended: 1`; CORS exposes it. The FE ends
  view-as locally with one notice and refetches.
- `hooks/usePermissions.ts`: one shared empty list, memoised set.

## Follow-ups (not this lane)

- `app/services/company_scope_resolver.py:134-164` does not check the target's status, unlike
  `dependencies.py`; an inactive target with an open row gets the admin's identity but the
  target's company scope for one request.
- Impersonation rows have no TTL and outlive logout; a re-sign-in restores view-as through
  `hydrate()` without a new `impersonation_start` audit row.
- Other hooks may default query data to a fresh `[]` that feeds an effect; the same freeze shape.
