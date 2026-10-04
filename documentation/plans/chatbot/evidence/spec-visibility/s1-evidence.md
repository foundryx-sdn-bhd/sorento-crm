# S1 browser evidence - spec visibility policy (Phase 1, mocked FE)

Verified with `agent-browser@0.27.0`, session `spec-vis-s1`, against http://localhost:3082
(FE) / :8082 (BE, real - see AC-4 note). Logged in as `person38@example.com`. Contact used:
"~ Zilin" (`0ca0f43d-514d-4e9a-92ef-8487d93c4215`).

**Nav note:** `/user-management/contacts` is NOT reachable from the left sidebar - its only
nav path is the topbar grid icon (`aria-label="Switch layout"`, misleading label; it renders
`AppsDropdownMenu`, item labelled "Internal Users" / "Respond contacts") per the code comment
in `apps-dropdown-menu.tsx:29-31`. This is pre-existing, not part of this lane. Used that path
throughout instead of a deep URL. Settings > Spec Visibility and Market Segments ARE on the
regular sidebar (Users & Access group), used normally.

| AC | Result | Evidence | Notes |
|---|---|---|---|
| AC-2 (badge/rule/picker/Hidden today, no slugs/UUIDs) | PASS | `s1-1-1280.png` | Badge "Default", "Hide these" selected, chips Drainer board + Thickness, "Hidden today: Drainer board / countertop thickness, Thickness". Label order is alphabetical, not the Thickness-first order used in the walkthrough prose - AC-2 does not pin an order, not treated as a defect. |
| AC-3 (placeholders) | PASS | `s1-4-1280.png` | Show only + [] -> "No specs"; Hide these + [] -> "All specs"; flipping that empty Hide-these to Show only -> "All specs" (null), not "No specs". All three transitions reproduced live. |
| AC-4 (save body / dirty tracking / Remove gating) | PASS w/ 1 blocked sub-case | `s1-2-1280.png`, `s1-2-reload-1280.png`, `s1-3-1280.png` | Show-only Material-only save -> badge "Contact override", "Hidden today" lists all 10 other keys, state survives an SPA (client-side) re-navigation away and back with Save then correctly disabled (clean). Read the component (`SpecVisibilitySection.tsx:159-171`): Save is intentionally enabled with no dirty check while the tier has no own row yet (`!hasOwnRow`) - matches the documented "Save creates the row" behaviour, not a bug. **Remove override is blocked**: clicking it fires the real backend pending-actions endpoint (`useDeferredAction` -> `services/pendingActionService.ts` -> `/api/v1/system/pending-actions`, not a Phase-1 mock) and the backend returns `Unknown action: 'spec_visibility_policy.remove'.` (`app/api/v1/system/pending_actions.py:91-93`) because that action key isn't registered in `app/services/record_actions.py` yet. No countdown ever renders. This is expected at S1 (the action registration is S2/backend work) - flagging so the captain can confirm S2's brief includes registering this action key, and re-verify the full countdown-and-lapse flow once S2 lands. |
| AC-5 (states + 375/1280) | PASS | `s1-8-375.png`, `s1-8-picker-375.png` | Card and picker dropdown both usable, no clipping, no horizontal scroll at 375px; chips wrap, buttons stack. Loading/error states not exercised (no way to force them against the mock in this pass; code review of `SpecVisibilitySection.tsx:173-191` shows a skeleton and an extracted-error branch consistent with AC-5's wording). |
| AC-6 (placement) | PASS | `s1-1-1280.png`, `s1-5-project-1280.png`, `s1-5-retail-1280.png`, `s1-6-1280.png` | Contact page: directly under Stock visibility, same card width. Market segments: per-row "Spec visibility" action opens a modal (Project: badge "Market segment: Project", "Nothing hidden"; Retail: badge "Default", two hidden keys, no Remove button since Retail has no own row). Settings: "Spec Visibility" tab immediately right of "Stock Visibility" (tab strip is horizontally scrollable at 1280px - only reachable after "Scroll tabs right", same as Stock Visibility itself, not new). |
| AC-7 (no motion beyond shared primitives, no explanatory copy) | PASS | all screenshots | No inline help text, no info icons, no extra copy anywhere in the card across all three placements. |
| No key slugs / no UUIDs in the card DOM | PASS | verified via `eval` | Card `outerHTML` scoped to the "Hidden today" container has zero UUID matches; only human labels (e.g. "Drainer board / countertop thickness") appear, never `board_thickness`/`thickness` as a bare slug. (Two UUIDs are present elsewheer on the page - the contact's own id in unrelated DOM data - unrelated to the spec card.) |

## Other observations (not AC failures)

- The "Spec visibility" dialog on the Market Segments page (`MarketSegmentsAdmin.tsx:423-433`)
  has no `DialogDescription`, producing a Radix console warning ("Missing `Description` or
  `aria-describedby`"). The sibling "Add/Edit market segment" dialog in the same file has the
  same gap, so this is a pre-existing file-wide pattern, not new to this feature - noting only
  in case a lane-wide a11y pass wants it.
- `console`/`errors` were clean of anything feature-specific across the whole walk; the only
  errors seen (`Each child in a list should have a unique "key" prop` in `Demo1Layout`) are a
  pre-existing layout-chrome issue unrelated to spec visibility.

## Steps walked (UAC S1 checklist)

1. Contact > Spec visibility card under Stock visibility: badge Default, Hide these, chips
   Thickness + Drainer board, "Hidden today" line. PASS.
2. Show only carries chips over; ticked Material, unticked both thickness chips, Saved:
   badge -> Contact override, "Hidden today" lists every other key. Verified persistence via
   an SPA nav-away-and-back (client-side, not a hard reload - the mock is in-memory and a hard
   browser reload legitimately resets it, confirmed by design, not a bug). PASS.
3. Remove override: BLOCKED - see AC-4 note above; the backend pending-action isn't registered
   yet, so no countdown ever appears, only an error toast. Did not let anything lapse since
   nothing started.
4. Placeholder matrix (Show only + null/[] , Hide these + []) reproduced exactly as specified,
   including the empty-Hide -> Show-only "All specs" (never "No specs") case. PASS.
5. Market segments: Project shows "Market segment: Project" / "Nothing hidden"; Retail shows
   "Default" with the two seeded hidden keys. PASS.
6. Settings > Spec Visibility tab beside Stock Visibility shows the default card. PASS.
7. No key slugs or UUIDs visible in the card DOM. PASS.
8. 375px: contact page card and picker both usable, non-clipped. PASS.
