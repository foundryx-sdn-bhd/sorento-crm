# PLAN: Centre the full-page Sorento loader (LOADER-CENTER)

Status: in review (Track: small fix). PR #1540. Plan created: 2026-10-09.

## Journey

Owner, 9 Oct 2026: the full-page loading screen (white page, "Sorento" wordmark, small grey
spinner below) looks misaligned. The spinner sits right of the wordmark's centre and the pair
reads as below the page's vertical centre. Wanted: "more aligned and centred".

## Measured cause

- The only loader that draws the wordmark plus a spinner is `ScreenLoader`
  (`sorento_crm_frontend/components/common/screen-loader.tsx`), used by the protected layout
  while the session resolves and by `RequireAccess` while permissions resolve.
  `LayoutLoadingFallback` is a different design (shell skeleton, no spinner) and is untouched.
- `ScreenLoader` renders `public/media/app/sorento-logo.svg`, whose `<text>` starts at `x="0"` in
  a 120-unit-wide viewBox. The glyphs fill roughly the left two thirds, so the image box's centre
  (where flex centres it, and where the spinner lines up) is well right of the visible word.
- The SVG is shared with the sidebar and header logos, which are left-aligned and depend on the
  left anchor, so the asset itself stays as it is.

## Change

- `ScreenLoader` draws the wordmark as text (same face, size, weight and navy as the SVG, with the
  dark-logo colour under `dark:`), so its box is exactly the ink and flex centring lines the word
  and the spinner up on one axis. The group stays `fixed inset-0` flex-centred on both axes.
- No behaviour change: same `data-slot`, `data-loading`, `role="status"` + `aria-label` on the
  spinner, same call sites.

## Verification

- vitest: `components/never-stuck-markers.test.tsx` plus a new `screen-loader.test.tsx` asserting
  the wordmark is text, centred in a single column with the spinner.
- Browser at 1280 and 375: measure wordmark centre x, spinner centre x and the group's vertical
  centre against the viewport, before and after.
