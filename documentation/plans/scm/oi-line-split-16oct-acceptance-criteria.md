# UAC: split SO lines into delivery splits (OI-LINE-SPLIT)

Owner OK on mock v2, 16 Oct 2026 23:47 ("Ok proceed"): `documentation/mockups/OI-LINE-SPLIT/index.html`.
Plan: `PLAN-oi-line-split-16oct.md`. Words: a **split** is one part of an SO line (qty, delivery
date or Cancel, remark, the line it sits on). Never "tranche" in UI or copy.

## A. The whole-SO split view (Fulfilment planning)

- AC-A1 "Split delivery…" on a board line (its action menu) opens one full-screen view for that
  line's SO: every open SO line, grouped by item code + location, one row per
  line, columns = every delivery date in the group (line dates and split dates, ascending), then
  "New date" and "Cancel". A last row per group is "New line" (not in AutoCount yet).
- AC-A2 A line's right-click menu on the board has "Split delivery…", and the AutoCount change
  lightbox (WAS / NOW) has a "Split delivery" button; both open the same view with that line zoomed.
- AC-A3 Each split is a card in (row = the line it sits on, column = its date). A card sitting on
  another line than its own shows "from <its own line>". A row header shows: the line (qty on date,
  line number when known), "Split N" when split, Gap N / Over N when it does not add up, what moved
  out ("→ 236 to line 640") and in ("← 236 from line 560"), "Plan X · AutoCount Y".
- AC-A4 Inside Confirm (the line has a pending AutoCount change), each row also shows what is left
  to tell: "Explained" when AutoCount qty = qty planned on that line, "ORDER +N" when AutoCount has
  more, "CANCEL BALANCE N" when less.
- AC-A5 Drag a card (pointer, touch long-press, or keyboard) onto another cell of the SAME group: a
  box asks how many (default all, Enter moves). All = the split moves; part = a new split is made
  for the moved qty; landing on a cell that already holds a split of the same own line and same
  sits-on line merges them. Cells of another item/location are not drop targets.
- AC-A6 Dropping on "New date" asks for the date; on "Cancel" the qty becomes a cancel split (no
  date, sits on its own line); on the "New line" row the split sits on "new line (not in AutoCount
  yet)".
- AC-A7 Keyboard on a focused card: Left/Right previous/next date, Up/Down previous/next line of
  the group (Shift moves 10 instead of all), N new date, S split in two, C cancel, Delete back to
  its own line and original date, Enter zooms and focuses its qty. Escape closes a box/menu.
  Cmd/Ctrl+Z undoes the last change (also an Undo on the toast).
- AC-A8 Clicking a row header (or Enter on it) opens the zoom pane on the right for that line:
  prev/next line, a bar with one segment per split (draggable dividers move qty between
  neighbours; Left/Right 1, Shift 10), and a table: Qty, Date (or Cancel), change pill, Sits on
  (own line / other lines of the group / New line / Cancel), Remark, "⋯" menu; "+ Add split".
- AC-A9 Change pill per split vs the line's original date: later = DELAY, earlier = ADVANCE, same
  = REMAIN, cancel = CANCEL BALANCE.

## B. Exact sums and Save

- AC-B1 Every split line's splits must add up exactly to its base qty (Q2 b). A gap or over shows
  live on the row header, in the zoom ("Gap 20" / "Over 20") and in the footer listing the lines;
  Save is disabled while any line does not add up. A zero-qty split is removed on blur/Save.
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
  staff (drag, menu, select). Nothing auto-links by qty, date or name.
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
  purchasing links and acknowledgement to the target line's OI row: no CANCEL BALANCE for the
  linked qty on the origin, no new ORDER for it on the target. Only what is left is told (SO402757:
  line 2 51 -> 2 with 49 linked to line 18; line 18 82 -> 121 is told as CANCEL BALANCE 10 only).
- AC-E2 With no stored splits Confirm behaves exactly as today (existing pure-split and netting
  tests stay green).
- AC-E3 Nothing is written to AutoCount.

## F. Phone 375

- AC-F1 At 375px the view shows one line at a time (line chips at the top), each split a card with
  qty stepper, date, sits on, remark, menu; Add split; footer with the gap warning and Save.
- AC-F2 Usable and not clipped at 375px and 1280px.
