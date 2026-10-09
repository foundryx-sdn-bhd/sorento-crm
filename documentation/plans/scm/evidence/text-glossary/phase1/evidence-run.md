# Text glossary S2 (Phase 1) - agent-browser evidence run, 10 Sep 2026

Lane `feat/text-glossary`, FE dev server `PORT=3081`, backend the shared primary at `:8000`
(DB `sorento_ai_automation_0907`). Logged in as `person38@example.com` (role `admin`).

## Golden path: Lines tab inline translate (AC-E2, AC-E3)

1. Sidebar: Dashboards -> Procurement (group) -> Supply Chain (nested group) -> Proforma
   Invoices -> opened `PI-2609-005` (CHAOZHOU JINBAICHUAN, real supplier data - the exact
   Jinbaichuan wording the plan's journey names).
2. Lines tab: `Description (EN)` column renders right after `Description`, both in view
   mode. Four lines share `description = 连体马桶`; each showed a muted dash button
   "Add English for 连体马桶" (glossary empty, R6 - no seed rows).
3. Clicked the first row's dash -> swapped to an `<Input>` pre-focused, aria-label
   "English for 连体马桶".
4. Typed "One-piece toilet", pressed Enter -> ALL FOUR rows sharing that description
   updated to "One-piece toilet" in the same render, with no page reload (AC-E2's
   same-tab, same-description requirement). Network: no new request fired for the OTHER
   three rows - one client-side mock write, one shared re-render.
5. Re-opened the cell (pre-filled "One-piece toilet"), typed "Wrong text", pressed
   Escape -> reverted to "One-piece toilet" with no request sent (confirmed via
   `network requests` - no new PUT/POST after Escape).
6. `console` / `errors`: no uncaught errors from this feature. One PRE-EXISTING warning
   unrelated to this lane: "Each child in a list should have a unique key prop... Check
   the render method of `Demo1Layout`" (the app shell layout, not touched by this lane).

Screenshots: `lines-tab-1280.png` (1280x800, post-edit, four rows reading "One-piece
toilet"), `lines-tab-375.png` (375x812, same page - header/tabs/table readable, table
scrolls horizontally under the fixed `tableLayout`, the same pattern every other PI grid
on this page already uses).

## Packing tab (AC-E1)

Checked every PI in this DB copy (`sorento_ai_automation_0907`) via a read-only query
(`proforma_invoice_packing_line` has zero rows across every invoice) - this dev snapshot
has NO packing rows on ANY proforma invoice, so the Packing tab's non-empty grid could
not be exercised live. What WAS verified: the tab's empty state ("No packing list
attached yet" + Attach button) renders correctly and unaffected, and the new
`description_en` column is defined identically to the Lines tab's (same
`DescriptionEnCell`, same props), which the Lines tab run above exercised end to end.
Flagged to the captain as a dataset gap, not a code gap.

## System Management > Text Glossary (AC-E4)

Menu entries added beside Import Column Mappings / Translations in both nav configs
(`config/menu.config.tsx`). Live verification is BLOCKED: `system.text_glossary.view`
does not exist in `user_permissions` yet (S1's migration adds it), so
`RequireAccess permission="system.text_glossary.view"` correctly denies even the `admin`
role today (`get_user_permission_slugs` grants admin every permission that EXISTS, not a
blanket bypass). A direct-URL sanity check (accepted deviation from "always navigate via
sidebar", used only to catch a render crash, not to prove nav/permission wiring) showed
title "Text Glossary | Sorento" and the correct AccessDenied screen, no console errors -
the component itself mounts cleanly. Full sidebar-click + populated-grid verification is
deferred to Phase 3 once S1 grants the permission for real.

## R11 cleanup re-check (same day, after the plan revision)

After deleting `app/(protected)/system-management/text-glossary/**` and both menu
entries, and retargeting `proformaInvoiceTranslationService.ts`'s contract doc + mock
body/response shape to `{source_text, target_text}` / `{source_text, target_text,
source, rebound}`:

- Sidebar: Dashboards -> System -> Configuration now lists Automation, Work Calendar,
  Running Numbers, Import Column Mappings, Status Graphs, **Translations**, Lookup Sets -
  no "Text Glossary" entry, in either nav config.
- Re-opened `PI-2609-005` Lines tab (fresh page load, mock reset as expected): four
  `连体马桶` rows still show "Add English for 连体马桶", column still in the same
  position after Description. Packing tab still renders its empty state
  ("Attach packing list"), no console errors.
- `npx eslint` and `npx tsc --noEmit` on every touched file: 0 new errors/warnings (same
  21 pre-existing, unrelated `tsc` errors as before the refactor).

## Bug found and fixed during this run

React Query's default structural sharing returns the SAME `data` reference from
`useProformaInvoice` after a refetch whenever the backend JSON is unchanged - true on
every mock translate, since the mock lives entirely on the client. The Lines tab's
`description_en` decoration lived in a plain `useMemo(() => ..., [data])`, so a save
wrote the mock map correctly but the tab never repainted. Fixed with a tiny
subscribe/version pair in `proformaInvoiceTranslationService.ts`
(`subscribeMockGlossary` / `getMockGlossaryVersion`, bumped on every write) and a
`useMockGlossaryVersion` hook added to the `useMemo`'s dependency list. Phase-1-only code,
deleted with the rest of the mock once S1 lands. The Packing tab did not need this fix -
its decoration runs inside `getProformaInvoicePacking`'s own `queryFn`, which TanStack
always re-invokes on invalidate regardless of structural sharing.
