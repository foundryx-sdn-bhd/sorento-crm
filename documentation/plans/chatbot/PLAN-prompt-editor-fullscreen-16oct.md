# PLAN: Full-screen prompt editor and preview (PROMPT-EDITOR-FULLSCREEN)

Status: in progress (small fix track: FE only, no migration, no auth/RBAC change, expected diff < 300 lines)

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
- Long lines already wrap (`whitespace-pre-wrap`); section headers (lines starting with `#`, or
  ALL-CAPS lines ending in `:`) get a bolder weight in Preview for scanning.

## UAC

1. A "Full screen" button next to Diff puts the Editor card over the whole viewport; "Exit full
   screen" and Esc both leave.
2. Draft text, scroll position and caret survive entering and leaving (no remount).
3. Edit / Preview tabs, Insert variable, Find (highlight + jump) and Diff work in full screen.
4. Commit message + Save as new version reachable in full screen.
5. Usable at 1280 and 375.

## Tests (red first)

`PromptDetail.test.tsx`: toggle keeps text and the editor DOM node (same element, so scroll/caret
survive), Esc exits, Find opens and finds a match in full screen.
