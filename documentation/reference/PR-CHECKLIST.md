# PR Checklist: Product Standards

Before merging, verify compliance with [ADR-PRODUCT-STANDARDS.md](./ADR-PRODUCT-STANDARDS.md).

## PR body (owner ruling, 24 Sep 2026)
- [ ] Body carries `Track: small-fix` or `Track: full` near the top, matching the plan's
      Status line (`PRINCIPLES.md` "Small fix track")
- [ ] Body carries `Plan created: <ISO timestamp>` (the plan file's first git commit
      timestamp, or its Status-line date if not yet committed), so lane duration is
      measurable alongside CI's own PR-open-to-merge time
- [ ] If `security-reviewer` did not run, the body says so ("security-reviewer: not run,
      diff outside its surface") - it runs only when the diff touches auth, RBAC, external
      ingest, uploads or multi-company scoping
- [ ] `guide-writer` is not expected per lane (retired from the per-lane pipeline, 24 Sep
      2026 ruling) - Outline updates are on-request or a weekly batch instead

## CRUD structure
- [ ] List page has search/filter and "Add/Create" button
- [ ] Create/edit uses modal by default (dedicated page only when ADR-exempt)
- [ ] Dedicated detail page for view
- [ ] Delete is a deferred (grace-window) action, not a confirmation dialog (D7 - see
      "Apple Alignment" below); a bare `confirm()` or a new `ConfirmDeleteDialog` /
      destructive `AlertDialog` importer is a defect, not a style choice

## Delete / Archive
- [ ] Delete is hard delete (permanent)
- [ ] If retention needed, separate Archive endpoint exists
- [ ] The record/list row shows the grace-window countdown with Cancel (10s hard delete,
      5s reversible) and no dialog opens; Escape does not cancel the pending action

## Detail pages
- [ ] All sections render even when empty (never hide entire section)
- [ ] Empty states are explicit and actionable ("No X yet. Do Y to add.")

## Shared usage
- [ ] Uses `extractApiError`, `buildDataGridParams` from `lib/api-client` (no duplication)
- [ ] Uses shared scaffolds: `ListPageToolbar`, `DetailActions`, `FormDialogScaffold` where
      applicable (`ConfirmDeleteDialog` is retired for anything on the deferred model)
- [ ] User/team selects: `services/userSelectService` (no feature-local duplication)

## Apple Alignment (design-system, every screen)
- [ ] Status renders as a pill via `<Badge status>` (`getStatusBadgeVariant`), not a
      hand-rolled coloured span
- [ ] DataGrid rows use `rowHref` (or `onRowClick` for a lightbox-edited record); log and
      sub-tables carry no pointer cursor and no row action
- [ ] No confirm dialogs on a destructive or detach action - the grace-window/deferred-action
      model (D7) is used instead; see "Delete / Archive" above. The only exceptions are the
      carve-outs `ADR-PRODUCT-STANDARDS.md` section 2 names (bulk selection, `PeopleGrid`, the
      portal ticket-draft confirm, `ReportViewsMenu`, the 22 `project-sales` files awaiting the
      `FormAction` auth-callback follow-up) - a confirm dialog anywhere else is a defect
- [ ] The page renders exactly one `PageHeader` (no hand-rolled `<h1>`, no `ToolbarTitle`)
- [ ] Tab strips use `variant="line"` (the default) unless they are a two/three-option
      segmented switch, which pins `variant="default"` explicitly
- [ ] Every icon-only button (`mode="icon"` / `size="icon"`) has an `aria-label` or an
      `sr-only` label - a bare icon with no accessible name is a defect
- [ ] Every list is a `DataGrid` with `headerSticky`, `columnsResizable` and `columnsMovable`
      on by default (M5-05); a list that opts a default off, or a raw `<Table>` importer
      outside `components/ui`, names why (`components/ui/raw-table.inventory.test.ts`'s
      allowlist)
- [ ] Row click carries `from=<row id>` to the detail href, and Back restores that exact row,
      scrolled into view and highlighted (M5-07) - true for free once the list uses `rowHref`
      and `useHrefWithListState`/`BackToList`, so this only needs checking on a list that
      built its own navigation instead

## Design
- [ ] `documentation/reference/DESIGN-LANGUAGE.md` hard-fails absent (`transition-all`,
      `scale(0)` entrance, `ease-in` entrance, raw `cubic-bezier` outside `config.reui.css`,
      motion on a keyboard-initiated action)
- [ ] Primitives are from the roster (`DESIGN-LANGUAGE.md` section 4), not hand-rolled
- [ ] No feature-explanation prose in the UI
- [ ] 375px and 1280px screenshots attached
- [ ] Any new motion honours `prefers-reduced-motion` (`useReducedMotion` from `lib/motion.ts`)

## Who did this (audit actor, `PLAN-unified-identity-26sep.md` section 8.3)
- [ ] A new "who did this" column is `<verb>_by_user_id`, String FK to `users.id` ON DELETE SET
      NULL; never a Respond.io agent id, a name or an email
- [ ] A column written from a portal route also gets `<verb>_by_contact_id` beside it, filled
      whenever the actor is a contact with no user (such contacts keep using the portal)
- [ ] An action a contact takes without a user (chatbot turn, WhatsApp ingest) is recorded against
      the contact and, when the contact has a user, that user too
- [ ] New request, job or tick entry points stamp the actor through `app.audit_context.stamp_actor`
      (never a bare contextvar set inside a sync dependency: it is lost at flush); audit screens show
      `actor_label`, never an id

## Never stuck (`NEVER-STUCK-UI.md`, owner ruling 1 Oct 2026)
- [ ] Every query consumer renders loading / no access / error + Retry / empty / data; no
      `isLoading || !data` skeleton, no `!data` "not found", no `.catch(() => [])` in a hook
- [ ] Tabs, menu leaves and buttons are gated by the slug the backend route requires, imported
      from the service's `*_PERMS`; cross-module reads gate on the other module's slug
- [ ] A 403 renders `AccessDenied` in place (inline for tabs and panels): no toast, no retry,
      never a raw `Permission required:` string; every new `page.tsx` sits under `RequireAccess`
- [ ] Every `refetchInterval` stops on terminal status, on error and at a ceiling; a new
      background job has a failed state reachable without the worker and a "timed out" UI
- [ ] Redirects use `replace`; none fire while permissions load; none inside a `queryFn`

## Test cost
- [ ] New backend tests do not add whole-suite-running slow tests without cause; check the `--durations=30` block in the backend CI logs for the PR ("Backend test suite (Postgres)" and "Backend test suite - SCM (Postgres)") and justify any new entry over ~2s
- [ ] A test that only asserts against production-copy data goes in `tests/ci_excluded.txt` with a reason, not into the gated set
