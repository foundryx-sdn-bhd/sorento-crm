# UAC: split SO lines into delivery splits (OI-LINE-SPLIT)

Owner OK on mock v2, 16 Oct 2026 23:47; the split screen redone to mock v4 after the 17 Oct hand test
(owner OK 14:10): `documentation/mockups/OI-LINE-SPLIT/index.html`. Sections B to E are unchanged.
Plan: `PLAN-oi-line-split-16oct.md`. Words: a **split** is one part of an SO line (qty, delivery
date or Cancel, remark, the line it sits on). Never "tranche" in UI or copy.

## A. The split sheet (Fulfilment planning), mock v4

Owner hand test of #1749 FAILED the v2 grid (17 Oct ~12:40); owner OK on mock v4 17 Oct 14:10 ("Ok,
make sure the sort handler is at the leftmost, go"), Q1 drag yes, Q2-Q4 (a), Q5 (a). Mock:
`documentation/mockups/OI-LINE-SPLIT/index.html` (v4). This section replaces the v2 grid/zoom AC.

- AC-A1 Entries unchanged: "Split delivery…" in a board line's action menu and right-click menu, the
  AutoCount change lightbox button, the SO header button. Each opens ONE full-screen page for that
  line's SO, scrolled to that line, the line flashed.
- AC-A2 One table with every open line of the SO, sorted by item code, location, then base delivery
  date (then line no). Columns in order: grip (LEFTMOST), LINE (AutoCount line no), SO DATE, S/O NO,
  ITEM CODE, LOCATION, QTY (base), DELIVERY DATE (base) | CHANGE QUANTITY, CHANGE DATE, AUTOCOUNT
  LINE, REMARK, BALANCE, remove. The first line of each item has a heavier top border.
- AC-A3 A split line shows one row per split, stacked downward; LINE to DELIVERY DATE and BALANCE are
  merged across its split rows (rowSpan). A split row: grip, qty, date (typed DD/MM/YYYY or calendar)
  with its change pill, AutoCount line, remark, remove (x). Inside Confirm, a line with a pending
  change also shows "AutoCount now <qty> · <date>" under its delivery date; a line AutoCount added
  (base 0) shows QTY 0 and "Added in AutoCount: <qty> · <date>" and cannot be split or dragged. A line
  other lines' splits sit on shows "+N from line M" for each.
  A line whose saved splits no longer act (`split_state` stale) shows an "AutoCount changed" pill
  under its delivery date; an applied line shows none (as built).
- AC-A4 A line that is not split reads REMAIN in CHANGE QUANTITY and CHANGE DATE, its remark editable.
  Clicking a REMAIN cell, or typing a digit (either cell) or "/" (date cell) on it, turns the line
  into one split row (base qty, base date, this line) with focus in that cell (the typed key in it).
  When focus leaves a line whose only split equals its base on its own line and date, the line reads
  REMAIN again, its remark kept. A zero-qty split is dropped when focus leaves its line and on Save.
- AC-A5 "+ Split" in BALANCE, or Ctrl/Cmd+Enter in a split row, adds a split row under the line:
  qty = what is left on the line (0 when nothing is), the line's base date, AutoCount line "New
  line"; its qty gets focus, selected. The x button, or Ctrl/Cmd+Backspace in the row, removes a
  split row at once (staged until Save; no confirm).
- AC-A6 BALANCE of a split line: "0" with a tick when its splits add up; "N left" as a draggable chip
  when short; "N over" in red when over. A line that is not split shows only "+ Split".
- AC-A7 AUTOCOUNT LINE (SearchableSelect) options: "Line <no> (this line)", every other line of the
  same item and location ("Line <no> · <its date>"), "New line", "Cancel". Picking another line also
  sets the split's date to that line's date (AutoCount's current date, else its base date). Cancel:
  the date cell reads CANCEL. Leaving Cancel takes the line's base date.
- AC-A8 Drag (Q1): the grip of a split row, the grip of a REMAIN line (the whole line) and the "N left"
  chip drag (mouse after 5 px, touch long-press, keyboard). While dragging: lines of other items or
  locations fade and are not targets; the dragged line's own block is not a target; a "New date" and
  a "Cancel this qty" drop row open under the item's last line; the overlay shows the qty, item,
  "from line N" and the target ("→ 20/11/2026 · line 608", "→ New date", "→ Cancel this qty"); the
  hovered line block is highlighted; the sheet autoscrolls slowly from a thin edge band.
- AC-A9 A drop moves ALL of it at once, with no "how many" box (Q5 a). A split row: sits on the target
  line and takes that line's date (as AC-A7); if a split of the same own line already sits there on
  that date the two merge (qty added, remarks joined). The "N left" chip: a new split of N at the
  target (merging the same way). A REMAIN line: the whole line moves. "New date": sits on "New line"
  with an empty date, focused, and Save waits until a date is typed. "Cancel this qty": a cancel
  split. A toast "Moved N to <target>" offers Undo. To move part, type the qty first, then drag what
  is left (SO417636 CSK14B 480: type 14, drag "403 left" onto line 608 -> 403 @ 20/11/2026 on 608).
- AC-A10 Keyboard: Tab / Shift+Tab move between cells; Enter or Down goes to the same column one row
  down, Shift+Enter or Up one row up; on a grip or chip: Space picks up, Down / Up next / previous
  target (the item's other lines in order, then New date, Cancel), Space or Enter drops, Escape puts
  it back. Cmd/Ctrl+Z undoes the last change.
- AC-A11 Change pill per split against its own line's base date: later DELAY, earlier ADVANCE, same
  REMAIN, cancel CANCEL.
- AC-A12 Header: "Split delivery", "<SO no> · <SO date> · <customer>", search by item code
  (ListSearchInput), a "Split lines only" switch, ONE secondary button that reads "Close" with no
  unsaved changes and "Cancel" (discards them and closes) with unsaved changes, and Save (the one
  CTA). Escape and the dialog X with unsaved changes keep the page open and say "Save or Cancel". Under it a status line:
  "<n> lines · <n> products · <n> split", then for each line that does not add up or has a split
  with no date: "<item> line <no>: N left | N over | date to fill" and "Show" (clears search and
  filter, scrolls to the line, flashes it, focuses its first cell).

## B. Exact sums and Save

- AC-B1 Every split line's splits must add up exactly to its base qty (Q2 b). A gap or over shows
  live in BALANCE and in the status line (AC-A12); Save is disabled while any line does not add up
  or a non-cancel split has no date. A zero-qty split is removed as AC-A4 says.
- AC-B2 Base qty and date of a line = the qty and date before its pending AutoCount change when one
  is pending, else its current qty and date; a partly delivered line splits only its open qty. A
  saved split keeps the base it was made against.
- AC-B3 Save writes every split of the SO in one call; the server refuses (422, message names the
  line) when a line does not add up, a split sits on a line of another SO, item or location, a qty
  is not a positive number, or a non-cancel split has no date. Nothing is saved on a refusal.
- AC-B4 A line put back to one split equal to its base on its own line and date with no remark is
  "not split" (its split rows are deleted on Save).
- AC-B5 Never stuck: a failed load shows an error with Retry; a failed Save keeps the view open with
  every value and shows the server's message as a toast; Cancel discards unsaved changes.

## C. Links are explicit (Q3 a, Q4 b)

- AC-C1 A split records its own (origin) line and the line it sits on. Sits-on is only ever set by
  staff (a drop, the AUTOCOUNT LINE select). Nothing auto-links by qty, date or name.
- AC-C2 If the line a split sits on is cancelled or deleted in AutoCount, the split falls back to
  "new line (not in AutoCount yet)"; nothing else changes.

## D. What purchasing gets (Q5 a)

- AC-D1 The OI email (Confirm and Send to purchasing): a split line prints one row per split, each
  row reading alone: LOCATION, ITEM CODE, QTY = base qty, NEW QTY = split qty, DELIVERY = base
  date, NEW DATE = split date (blank for cancel), REMARK = change word + remark. Inside Confirm a
  line's "left to tell" adds a row ("ORDER (new qty)" or "CANCEL BALANCE"). Same template as today.
- AC-D2 The OI Excel (one sheet, #1748) gets a REMARK column appended after the existing columns.
  An OI row whose line has splits sitting on it prints one row per such split (QTY = split qty,
  DELIVERY DATE = split date, REMARK = change word, "from line N" when it is another line's, and the
  remark) when they add up to the row's QTY; otherwise it prints as today with the split plan in
  REMARK. Unsplit rows print as today with REMARK empty. TOTAL QTY stays the sum of the rows.
- AC-D3 Order inquiries Lines tab: a split line shows "Split N"; expanding the row lists its splits
  (Qty, Date, Change, Sits on, Remark). Read-only.

## E. Confirm (engine)

- AC-E1 When a line's AutoCount qty went down and staff linked part of it (a split sitting on
  another line of the same SO whose AutoCount qty went up), Confirm moves that qty with its
  purchasing links and acknowledgement to the target line's OI row (a moved row whose delivery date
  changes goes back to To confirm, the 17 Sep handshake rule): no CANCEL BALANCE for the
  linked qty on the origin, no new ORDER for it on the target. Only what is left is told (SO402757:
  line 2 51 -> 2 with 49 linked to line 18; line 18 82 -> 121 is told as CANCEL BALANCE 10 only).
- AC-E2 With no stored splits Confirm behaves exactly as today (existing pure-split and netting
  tests stay green).
- AC-E3 Nothing is written to AutoCount.

## F. Phone 375

- AC-F1 At 375px the same sheet: LINE, SO DATE, S/O NO and LOCATION are hidden (the line no shows under
  the item code, the SO date and location in the title), ITEM CODE stays pinned left while the table
  scrolls sideways. Grip and chip drag by touch long-press; the AUTOCOUNT LINE select does the same
  as a drop.
- AC-F2 Usable and not clipped at 375px and 1280px.
