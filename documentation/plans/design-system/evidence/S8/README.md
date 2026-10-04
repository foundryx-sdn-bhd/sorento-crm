## Addendum, 31 Aug 2026 (post-merge regression)

The S8-03 finding below (PASS, mid-collapse only) missed the END states: the
user reported the collapsed sidebar with icons squished/distorted and its
content overlapping the page past the collapsed rail. Root cause: once the
toggle-clipping fix (`a5db654aa`) moved `overflow: hidden` from `.sidebar`
onto `.sidebar-rail`, the rail's own clip ran BEFORE the rail's own
counter-scale was applied (clip happens in an element's local, pre-transform
coordinate space), so the counter-scale re-inflated the already-clipped
content back past the visible collapsed width with nothing left to re-clip
it - the inflated menu/header spilled over the page. Separately, the toggle
button (a plain, non-counter-scaled child of `.sidebar`) was itself squished
by `.sidebar`'s own `scaleX`, since only a shape-preserving counter-scale
tracks position without also tracking size, and the button needed the former
without the latter. Given two independent defects in the same mechanism (not
one clear bug), the fix reverted the collapse to the pre-S8 width-based
animation rather than patching the transform trick further; see the S8-03
line in `apple-alignment-acceptance-criteria.md` and
`PLAN-apple-alignment.md` section 3.11 for the recorded deviation.

# S8 Motion - browser verification evidence (agent-browser, 31 Aug 2026)

Session `s8-evidence` against the S8 worktree lane (:3090/:8000). Verified by
measurement (`getComputedStyle`/`getBoundingClientRect` sampled over rAF loops)
per the brief, plus screenshots. Login: `person38@example.com` (E2E_EMAIL).

## Findings summary

- S8-01 Dialog: PASS. `S8-01-dialog-open-spring-samples.json` - opacity 0->1,
  scale 0.96->1 over ~450ms (critically damped spring, matches `SURFACE_SPRING`).
  Interrupt (close then reopen mid-close): PASS, `S8-01-dialog-interrupt-no-jump-samples.json`
  - value continues from ~0.63 opacity, no reset-to-0 jump.
- S8-01 Sheet: PASS. `S8-01-sheet-open-samples.json` (slide-in translateX 500->0),
  `S8-01-sheet-interrupt-no-jump-samples.json` (continuity confirmed).
- S8-01 DropdownMenu: PASS (animates), see `S8-01-dropdown-inner-animation-samples.json`
  - inner `motion.div` opacity/scale do animate. Sampling is noisy (dispatched
    synthetic pointer events double-toggled open/close/open) but the spring
    mechanism is confirmed running.
- S8-01 reduced motion: FIX. `S8-01-dialog-reduced-motion-samples.json` -
  under confirmed `prefers-reduced-motion: reduce` (verified via matchMedia on
  a FRESH page load, before any surface mounted), the dialog's `transform`
  correctly stays static (no scale - `surfaceVariants(true)` correctly drops
  scale), but `opacity` still visibly ramps over ~170ms instead of appearing
  in one frame as S8-01 requires ("opens/closes in one frame"). Suspect: the
  10ms tween in `REDUCED_MOTION_TRANSITION` isn't actually being applied as
  configured, or something else is smoothing the opacity commit. Needs a
  follow-up look at `lib/motion.ts` `REDUCED_MOTION_TRANSITION` / how Framer
  Motion resolves a sub-16ms tween.
- S8-02 transform-origin: **BLOCKER**, code-verified. `popover.tsx`,
  `dropdown-menu.tsx` and `tooltip.tsx` all reference a CSS variable
  `--radix-popper-content-transform-origin` that Radix **never sets**. Each
  primitive exposes its OWN per-primitive variable instead:
  `--radix-popover-content-transform-origin` (Popover),
  `--radix-dropdown-menu-content-transform-origin` (DropdownMenu),
  `--radix-tooltip-content-transform-origin` (Tooltip) - confirmed by reading
  `node_modules/@radix-ui/react-{popover,dropdown-menu,tooltip}/dist/index.mjs`.
  Because the referenced variable doesn't exist, `transform-origin` silently
  falls back to CSS-initial (50% 50%, dead centre) - confirmed empirically,
  `S8-02-dropdown-transform-origin-BUG-samples.json` shows `transformOrigin:
  "81.4141px 47.5px"` against a 162.8x95 content box (exactly 50%/50%), not
  anchored to the trigger. SECOND compounding bug: even with the right name,
  the `origin-*` class sits on the OUTER static `Content` wrapper (which never
  scales - Radix's own positioning transform lives there), while the actual
  scale animation runs on an INNER `motion.div` that carries no `origin-*`
  class at all - so fixing only the variable name would still not visually
  anchor the scale. Both need fixing together.
- S8-03 sidebar collapse: PASS. `S8-03-sidebar-collapse-samples.json` - only
  `.sidebar`'s `transform` (`transitionProperty: "transform"`) animates
  smoothly scale 1 -> 0.2857 over ~300ms; `.wrapper`/`.header` padding/inset
  flip in a single frame (`transition-duration: 0s` confirmed, even though
  their computed `transitionProperty` reads `"all"` - a NIT, that property
  list looks broader than it needs to be even though it never fires at 0s
  duration). `layout-initialized` class confirmed present. Content not
  distorted mid-collapse (`S8-03-sidebar-collapsed.png`).
- S8-04 mobile drawer: PASS. `data-vaul-drawer` confirmed present
  (`S8-04-drawer-open.png`). Drag tracks the finger 1:1
  (`translate3d(-80px,0,0)` mid-drag, `S8-04-drawer-mid-drag.png`); released
  near-start, it DISMISSED (`S8-04-drawer-after-release-dismissed.png`) - a
  valid outcome per spec ("dismisses OR snaps back"). Reopened and clicked a
  nav link successfully, confirming inputs/links are interactive while open
  (`S8-04-drawer-nav-interactive.png`).
- S8-05 AI panel: PASS. `S8-05-ai-panel-materialise-samples.json` - scale
  0.92->1, blur 8px->0px, opacity 0->1 over ~450ms. Resize handle with pointer
  events verified: dragging the left handle grew the panel 384px -> 521px
  (`S8-05-ai-resized.png`).
- S8-06 row drag: PASS. Source-verified `activationConstraint: { distance: 6 }`
  (`data-grid-table-dnd-rows.tsx`/`data-grid-table-dnd.tsx`) and
  `dropAnimation={defaultDropAnimation}`. Empirically verified on
  `SpecRuleEditor` (Product Specifications > a spec key > Edit > rule list,
  `distance: 4`): a 2px move did NOT start a drag (`S8-06-dnd-tiny-2px-no-drag.png`,
  `S8-06-dnd-after-tiny-unchanged.png` - order unchanged), a ~74px move DID
  (`S8-06-dnd-mid-drag-active.png`, `S8-06-dnd-live-reorder.png`), and the
  drop committed the new order (`S8-06-dnd-drop-committed.png`); `useSortable`
  wires `transition` onto the row's own style so the settle is animated.
- Regressions: PASS. Deferred-action countdown toast still renders correctly
  (`regression-S6-delete-countdown-toast.png` - "Deleting in 9s", draining
  bar, Cancel, dimmed row). Page-scoped pager still works after a row click
  from a list ("2 / 50", `regression-S3-page-scoped-pager.png`), URL carries
  page/limit/sort/dir. SearchableSelect's own 300ms debounce still fires
  correctly (`regression-S7-searchable-select-debounce.png`) - NOTE: no
  shared `useDebouncedSearch` hook exists anywhere in this codebase tree
  (grepped for it, zero hits); the Products list's own search box is
  Enter-to-search, not live-debounced, so "S7 search debounce" as described
  in the UAC (S7-02) does not appear to be built in this branch - this
  predates S8 and is not a regression introduced by it.
- Console: zero `error`-level entries and zero `errors` command output across
  the whole session.

## INCIDENT (read before repeating any delete test on this DB)

While exercising the S6 regression (deferred delete + Cancel) on the Brands
list, a `find text "Cancel" click` locator failed right after the countdown
toast appeared. By the time the correct ref was found, the 9s window had
already lapsed and the delete **committed for real** against the shared dev
Postgres (`sorento_ai_automation`), for brand `BRAVAT`
(`351aacba-a61b-4490-ad37-45efac3dafc0`).

Verified directly against Postgres (read-only queries):
- The `brands` row is gone (hard delete, as designed - D7).
- `products.brand_id` has an `ON DELETE SET NULL` FK: **997 products currently
  have `brand_id IS NULL`**, and the Brands list showed `964` products
  attributed to this BRAVAT row just before the delete - almost certainly the
  bulk of that 997 is this incident, not pre-existing nulls (I did not capture
  a "before" count, so this can't be proven exactly).
- A different, pre-existing, unrelated `brands` row also named/coded `BRAVAT`
  (`e2d80295-ddd5-47cf-8820-d0988e1d877e`, company_id
  `5e2c68f5-1b35-4f1d-a6e0-e904c0d8260f`, created 26 Jul 2026, holding 894
  products) is NOT affected and NOT related to this incident - it is simply
  invisible in the UI because it belongs to a different company scope than
  the logged-in user's default tenant (`00000000-0000-0000-0000-000000000001`).
  It is a coincidence of a shared brand code across two tenants, confirmed via
  direct `company_id` inspection.
- No `audit_logs` rows exist for the affected products at the commit
  timestamp - the FK cascade ran at the Postgres level, not through the ORM,
  so there is no application-level "before" value to restore from for the
  ~964 orphaned products.

This is **not an S8 code defect** - the deferred-delete mechanism (S6) behaved
exactly as designed (10s window, toast, Cancel button, hard delete on
commit); the mistake was mine, in test execution (a bad locator cost the
window). Flagging prominently per instructions to report failures with
actual output, and because "Local DB = prod copy" per project lessons -
the captain/team needs to decide whether to restore `brand_id` on the
affected products (best-effort, e.g. by SKU-prefix pattern) or accept the
loss. `regression-S6-delete-countdown-toast.png` (the "Deleting in 9s" toast,
captured 200ms after the click, before the failed Cancel attempt) is the only
S6 evidence taken in this session - it confirms the countdown/toast/dimmed-row
UI still renders correctly, which is what the regression check is actually
verifying (the S8 motion changes did not break the S6 UI). No further delete
tests, successful-cancel or otherwise, were attempted after this incident.
