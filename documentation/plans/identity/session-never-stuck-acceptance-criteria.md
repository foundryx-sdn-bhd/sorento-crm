# UAC: session never stuck (SESSION-NEVER-STUCK)

Plan: `PLAN-session-never-stuck-01oct.md`. Owner rule (1 Oct 2026): nothing may hang; every
failure ends in a clear, truthful state.

| # | Given | When | Then | Test |
| --- | --- | --- | --- | --- |
| AC-1 | Signed in, inside the app (any page, with or without view-as) | FastAPI answers 401 `session_expired` / `session_revoked` / `session_invalid`, and the cookie still holds the same token | Exactly ONE hard navigation to `/signin?callbackUrl=<path+query+hash>`; NextAuth sign-out attempted at most once, capped at 3 s | `lib/api.sessionEnd.test.ts` (10 parallel 401s, 1 signOut, 1 navigation); e2e test 1 |
| AC-2 | Signed in, inside the app | `/api/auth/token` answers 401 (cookie gone, undecodable, overwritten by another localhost copy) | Same single redirect; no `/api/v1` call is sent without a bearer | `lib/api.sessionEnd.test.ts`; e2e test 2 |
| AC-3 | A redirect to sign-in is under way | More API calls fire | They answer locally (401 `session_ending`) with no network request and no error toast | `lib/api.sessionEnd.test.ts` (storm); `providers/query-provider.test.tsx` |
| AC-4 | Viewing as another user | The session ends (AC-1 or AC-2) | The view-as entry is cleared from the browser before the redirect | `lib/api.sessionEnd.test.ts`; e2e test 1 |
| AC-5 | The tab cached a token that died, and the cookie now holds a newer one | A read (GET/HEAD) gets the dead-session 401 | It is replayed once with the newer token; no sign-out | `lib/api.sessionEnd.test.ts` |
| AC-6 | Same as AC-5 | A write gets the dead-session 401 | It is NOT replayed (the newer token may be another user's); the 401 returns; the next submit uses the newer token; no sign-out | `lib/api.sessionEnd.test.ts` |
| AC-7 | `/api/auth/token` never answers | Any API call | The token read is abandoned after 10 s and the call still settles | `lib/api.sessionEnd.test.ts` |
| AC-8 | NextAuth sign-out never answers | The session ends | The redirect still happens after 3 s | `lib/api.sessionEnd.test.ts` |
| AC-9 | A page's "leave site?" guard cancels the redirect | 5 s pass | The latch releases; the next call tries again (no page that silently answers "ending" forever) | `lib/api.sessionEnd.test.ts` |
| AC-10 | A public page using apiFetch with no session (daily-SLA unsubscribe link) | The token read answers 401 | No redirect; the call goes out and works | `lib/api.sessionEnd.test.ts` |
| AC-11 | An RBAC 403, or a 401 with no session code (wrong current password) | Any call | Never signs out | `lib/api.sessionEnd.test.ts` |
| AC-12 | Viewing as X, and the backend can no longer honour it (stopped in another tab, admin role removed, target deactivated) | Any API call | Response carries `X-Impersonation-Ended: 1` (exposed to CORS); the client drops the banner, shows once "View-as ended - you are seeing your own data", refetches every query; no sign-out | `tests/test_session_never_stuck.py`; `lib/api.sessionEnd.test.ts`; e2e test 3 |
| AC-13 | The admin clicks Stop on the view-as banner | The stop request is in flight | No "View-as ended" notice (view-as is cleared before the request; restored if Stop fails) | `hooks/useImpersonation.stop.test.tsx` |
| AC-14 | Permissions not loaded (loading, or failed because the session died) | The user clicks anything | The page stays responsive (no freeze) | `hooks/usePermissions.stable.test.tsx`; e2e (all three hang at the first click on main) |
| AC-15 | The NextAuth session itself is unauthenticated on a protected page | The protected layout renders | It uses the same latched redirect (no soft `router.push` racing a hard one) | `app/(protected)/layout.sessionEnd.test.tsx` |
