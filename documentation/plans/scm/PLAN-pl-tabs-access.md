# PLAN: Packing-list tabs for users without SCM permission (PL-TABS-ACCESS)

Status: In review, PR #1413 (small fix track: frontend only, no migration, no backend RBAC change)

## Journey

Owner, prod, 1 Oct 2026: viewing a packing list as Kah Xin, the Proforma invoices, Documents and
SPO planner tabs "hang"; Shipment lines works. Granting every SCM permission fixed it, so it is
permission-driven.

## Scope

1. Find the real hang cause (file:line).
2. Hide the tabs a user cannot use; `RequireAccess` / `AccessDenied` on those routes for a deep link.
3. The layout provider's source-invoices read is silent on 403 (no toast, no retry) so the tabs the
   user CAN open do not show an error that belongs to a tab they cannot.
4. Fix the hang cause.

Not in scope: changing which permission the backend endpoints require.

## Findings (repro, 1 Oct 2026, sandbox stack, Playwright + agent-browser)

User with only `procurement.packing_lists.view/edit`, direct login and admin view-as, dev build:

| Tab | Request | Result before the fix |
|---|---|---|
| (all, layout provider) | `GET /scm/inbound-shipments/{id}/source-proforma-invoices` | 403 `scm.dashboard.view`, retried once, permission toast; re-fired on every tab mount |
| Details | `GET /scm/container-sizes` | 403 + retry + toast |
| Proforma invoices | source-proforma-invoices | renders the EMPTY state "Read from a packing list, not drafted from a proforma invoice." (`SourceProformaInvoicesCard.tsx` branched on `isLoading` only) |
| Shipment lines | `/scm/.../packing-list`, `/scm/.../line-photos` | 403 + retry + toast; grid itself works |
| Documents | none of its own; reads the provider | "No proforma invoice behind this container." (false) |
| SPO planner | `/scm/.../spo-suggestion` | raw "Permission required: scm.dashboard.view / Try again" card |

Backend gates: `require_permission("scm.dashboard.view")` (`app/api/v1/scm/proforma_invoices.py:45`,
`app/api/v1/scm/fulfilment.py:61`). `/me/permissions` returns every slug for superadmin/admin.

## Fix

- `[id]/components/packing-list-context.tsx`: `SCM_READ_PERMISSION`, `canReadScm`; source-invoices
  read only with it.
- `hooks/usePackingLists.ts`: that read is `retry: false` + `meta.silent`; the card reports the
  failure in place (`SourceProformaInvoicesCard.tsx`).
- `[id]/layout.tsx`: Proforma invoices + SPO planner tabs only with the permission.
- `[id]/proforma-invoices/page.tsx`, `[id]/spo/page.tsx`: `RequireAccess` for a deep link.
- Lines / Details / Documents: their SCM reads are skipped without the permission.

Review round (reviewer): the shared read still retries a non-403 failure once, and Documents
says "Could not load the proforma invoices." rather than "none"; without SCM read the Lines
grid drops From PI + Photos, the gear drops "Download packing list" (an SCM export), and the
container size stays a value in edit mode.

## The "hang" (root cause, found after the owner's hand test)

Reproduced on the sandbox with a container that HAS lines (40), as Kah Xin: on Shipment lines the
renderer pegged a core, agent-browser's `Runtime.evaluate` timed out, and a click on Documents or
Timeline never committed (the tab highlights, the page stays on Shipment lines). A full load of
`/lines` hangs the same way. My first repro used an empty container, where the grid never mounts,
which is why it did not show.

Cause: a client render loop.
- `components/PackingListLinesTab.tsx:192` (before the fix): `invoicesByLine =
  sourceInvoices?.by_shipment_line ?? {}`. Without SCM read `sourceInvoices` is undefined (a 403
  before this PR, a disabled query after it), so this was a NEW `{}` every render.
- It is a dependency of `viewRows` (`useMemo`), so the grid got new `data` every render.
- TanStack Table resets the page index whenever the row model recomputes
  (`@tanstack/table-core` `getRowModel` memo `onChange` -> `_autoResetPageIndex` ->
  `resetPageIndex` -> `setPagination` with a new object), which is a React state update, which
  re-renders with new rows again. Endless.
- Admin has the invoices, so the reference is stable and nothing loops: granting SCM "fixed" it.
  Signing in again "fixed" it for the same reason (the view-as ended). Not a session problem.

Fix: `invoicesByLine` memoised with a shared empty map. Test: "settles on Shipment lines instead
of re-rendering forever" (`[id]/packing-list-record.test.tsx`) times out on the old code.

## Verification

agent-browser, 1280 and 375, sidebar navigation from `/`: restricted user sees Details, Shipment
lines, Documents, Timeline with no SCM request and no 4xx; deep links to `/proforma-invoices` and
`/spo` show AccessDenied; admin still sees all six tabs, SPO suggestion 200, the workbook download.

## Follow-ups (not in this lane)

- View-as restored by `hydrate()` on a fresh sign-in (impersonation already active server-side)
  keeps the admin's cached `['my-permissions']` until a reload: the tabs show and 403 as before.
  Starting view-as from Users reloads the page, so the owner's normal path is unaffected.
- `next build` with type checking fails on main at `app/(auth)/signin/page.tsx`
  (`isSafeCallbackUrl` is not a valid Page export). Docker skips the type check.
- `RequireAccess` renders a full-screen `ScreenLoader` on a cold deep link, and `AccessDenied`
  carries its own h1 + CTA inside the tab. Reused as briefed; an in-tab variant would read better.
