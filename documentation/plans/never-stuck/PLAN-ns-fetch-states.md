# PLAN: never-stuck levers L2-L5 (lane NS-FETCH-STATES)

Status: PR ready for review, 1 Oct 2026 (small fix track: shared frontend code plus mechanical call-site edits; no
migration, no auth/RBAC change, no new ingest surface. The call-site sweep pushes the diff over
~300 lines, but each site is a one-prop edit, so the lane stays on the small track and says so
in the PR.)

Standard: `documentation/reference/NEVER-STUCK-UI.md` (PR #1416). Audit:
`documentation/reports/AUDIT-never-stuck-2026-10-01.md` section 2, levers L2-L5. UAC:
`ns-fetch-states-acceptance-criteria.md` beside this file.

Out of scope: L1 (session / sign-out: lane SESSION-NEVER-STUCK), L10 (shared lookup routes:
owner call pending), L6-L9, per-screen tab / button gating, polling ceilings.

## Journey

A user opens a screen. Whatever happens on the wire, the screen ends in data, an honest empty
state, "no access", or an error with a Retry, within a bounded time. A list that could not be
read never says "No data"; a picker that could not be read never looks like "nothing to
choose"; a refused request is not retried; a hung server is given up on.

## Levers (shared fixes)

| Lever | Change | Where |
|---|---|---|
| L4 | Every `apiFetch` has a deadline on the server answering: 30s read, 120s write / AI chat / file-building GET (`/export`, `/download`, `/pdf`, `.xlsx`, `.pdf`, `.csv`), 10 min FormData upload, caller override `timeoutMs`. `/api/auth/token` gets 10s and settles as "no token" (what that means is L1's). Timeout error message: `The server took too long to answer.` The caller's own abort still reaches it as an `AbortError`. The deadline is cleared when headers arrive, so body reads and event streams are never cut. | `lib/api.ts` (`_fetchWithDeadline`, `ApiFetchInit`) |
| L3 | `isAccessDenied`, `isSignedOut`, `isNotFound`, `isTimedOut`, `isRefused` (message-based; errors carry no status). `AppQueryClient` wraps every query's resolved `retry` so a refusal or timeout is never retried, including the ~250 hooks that set their own `retry: N`. The shared toast uses `isAccessDenied`, so `Module not enabled:` and the strict-mode variant get the friendly toast, never the raw slug. | `lib/api-client.ts`, `providers/query-provider.tsx` |
| L2 | `DataGrid` takes `error` + `onRetry`. With no rows, a refusal renders the inline `AccessDenied` (new `inline` prop) and anything else renders the message + Retry. Rows on screen stay. Every grid body shares `DataGridTableEmpty`, so the table, dnd and drive views all get it. | `components/ui/data-grid.tsx`, `data-grid-table.tsx`, `app/components/common/AccessDenied.tsx` |
| L5 | `SearchableSelect` / `SearchableMultiSelect` take `loadError` + `onRetry`; the menu says "You don't have access to this list." or "These options could not be loaded." + Retry, an empty trigger says "No access" / "Could not load". Async pickers catch their own rejection. The uom / brand / product-category / country / role select hooks throw the backend message instead of returning `[]` (or the error body). | `components/common/SelectLoadFailure.tsx`, the two selects, the five hooks |

## Applying them to audit rows

Mechanical: each list in a cleared row passes `error={q.error} onRetry={() => q.refetch()}` to
its `DataGrid`; each picker passes `loadError={q.error} onRetry={() => q.refetch()}`. Row ids
follow the audit's own numbering (`T<n>` = section 4 top-40, `<appendix><n>` = n-th data row of
that appendix's findings table). The row-to-site map is in the PR body.

L4 and L3 clear rows by construction (no call-site edit): T2 (stalled token fetch half), I1,
I19 (AI chat), I20 (retry doubling the hang), H4, H9 (401 retried).

## Tests (red first, committed before the fixes)

- `lib/api-client.neverStuck.test.ts` (L3 classifiers)
- `lib/api.timeout.test.ts` (L4 deadlines, caller abort, token fetch, file-building GETs)
- `providers/query-provider.neverStuck.test.tsx` (L3 retry rule incl. explicit `retry: N`, toast)
- `components/ui/data-grid-table.errorState.test.tsx` (L2)
- `components/common/SearchableSelect.loadError.test.tsx` (L5 component, static + async)
- `app/(protected)/master-data-management/shared/hooks/selectHooks.neverStuck.test.tsx` (L5 hooks)

## Triggers named, not built

- A status-carrying error (`error.status`) would replace the message prefixes; build it when a
  second refusal shape appears that the prefixes cannot tell apart.
- A `<DataGrid` without `error=` ratchet is G3 in the audit (lane NS-GUARDS).
