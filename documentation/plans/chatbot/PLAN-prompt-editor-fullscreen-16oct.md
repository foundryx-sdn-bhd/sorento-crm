# PLAN: Full-screen prompt editor and preview (PROMPT-EDITOR-FULLSCREEN)

Status: in review (small fix track: FE only, no migration, no auth/RBAC change, expected diff < 300 lines)

## Journey

Owner, 16 Oct: the prompt editor box on System > AI Assistant > Prompts > <prompt> is too small to read
long prompts. They want to toggle the Editor card to full screen, read and edit there, and come back
without losing anything.

## Design

- Reuse `FocusShell` + `FocusToggle` from `app/(protected)/dealer-kit/components/FocusMode.tsx`
  (fixed overlay, Esc exits, body scroll lock, on-screen "Press Esc" hint). No new design-system piece.
- `PromptDetail.tsx` wraps the Editor `Card` in `FocusShell`; the toggle sits next to Diff.
- In full screen the Card fills the viewport as a flex column; the chip editor / textarea / preview
  grow to fill the height (`max-h-[70dvh]` cap is lifted). Save + commit message stay in the card, so
  they stay reachable. Both tabs, Insert variable, Find and Diff are inside the card already.
- `FocusShell` rendered `<>{children}</>` when inactive and `<div>{children}</div>` when active, so
  React remounted the subtree on every toggle: the draft (lifted state) survived but caret, scroll
  and Find state did not. Fix: keep one stable wrapper `div` (`display: contents` when inactive) so
  toggling never remounts. Benefits every dealer-kit host too; `[data-dk-focus-mode]` still appears
  only when active, so their tests keep their meaning.
- `FocusShell` also ignores an Escape something inside already handled (`event.defaultPrevented`):
  closing Find, a Radix popover or a dialog no longer also drops you out of full screen. This
  applies to the dealer-kit hosts too (Esc with a popover open, or while editing a text layer,
  now takes two presses to leave full screen: one to close, one to exit).
- Fill-height chain: every flex wrapper between the overlay and the editor carries `min-h-0`
  (reviewer finding 1: without it the chip editor grew to the prompt's full length); the scroll
  boxes keep their 320px floor.
- Long lines already wrap (`whitespace-pre-wrap`); section headers (markdown `#`, `== BANNER ==`
  lines as the chatbot parser uses, or short ALL-CAPS lines) are bold in Preview for scanning.
- Publish / Stage live in Version history, which full screen covers, so the loaded version's
  Stage vN / Publish vN come into the editor header in full screen; disabled while the draft is
  unsaved (they publish the SAVED version).
- Closing Find scrolled the chip editor back to the top (pre-existing: `root.focus()` on a
  contentEditable). Now `focus({ preventScroll: true })`, so you stay at the match.

## UAC

1. A "Full screen" button next to Diff puts the Editor card over the whole viewport; "Exit full
   screen" and Esc both leave.
2. Draft text, scroll position and caret survive entering and leaving (no remount).
3. Edit / Preview tabs, Insert variable, Find (highlight + jump) and Diff work in full screen;
   Esc with Find or a popover open closes that only.
4. Commit message + Save as new version reachable in full screen; Stage / Publish for the loaded
   version too, disabled while there are unsaved edits.
5. Usable at 1280 and 375; Preview headers bold; closing Find keeps the scroll at the match.

## Tests (red first)

`PromptDetail.test.tsx`: toggle keeps text and the editor DOM node (same element, so scroll/caret
survive), Esc exits, Find opens and finds a match in full screen.

## Evidence run (agent-browser, sandbox stack, bootstrap seed, 16 Oct)

Stack: `scripts/cloud-env-setup.sh` DB (`sorento_ci`, bootstrap prompts), admin from
`scripts.seed_never_stuck_smoke`, uvicorn :8000, `npm run dev` :3000. Navigated by sidebar:
System > AI Assistant > Prompts > semantic_parser, then chatbot_semantic_parser (v4, ~150K chars).

| Check | 1280x800 | 375x812 |
|---|---|---|
| Overlay covers viewport, no horizontal scroll | PASS (1280x800) | PASS (375x812, scrollWidth 375) |
| Editor fills height, scrolls inside | PASS (chip editor 449px, scrollHeight 44747) | PASS (390px) |
| Save + commit visible without scrolling | PASS (Save y 701-735) | PASS (Save y 713-747) |
| Textarea key: Esc exits, text + caret (200) + focus + scrollTop (25) kept | PASS | n/a |
| Chip editor: scrollTop 20000 in, 30000 out, same DOM node | PASS | PASS (20072 carried in) |
| Find highlights + jump in full screen (38/52, scrolled to 32825) | PASS | n/a |
| Esc with Find open closes Find only; scroll kept after close (20072) | PASS after preventScroll fix | n/a |
| Insert variable menu opens; Esc closes menu only | PASS | n/a |
| Diff opens inside the overlay | PASS | n/a |
| Preview fills height; banner headings bold (101 found) | PASS (493px) | PASS (434px) |
| Stage v4 disabled while dirty; save v5 from full screen, DB row has the edit | PASS | n/a |
| Console errors | none | none |
