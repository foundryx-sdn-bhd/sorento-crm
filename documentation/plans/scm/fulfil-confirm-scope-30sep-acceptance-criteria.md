# UAC: Fulfilment planning Confirm posts what the planner ticked, and says exactly what it wrote

Plan: `PLAN-fulfil-confirm-scope-30sep.md`

## Server, `POST /api/v1/project-sales/sales-orders/{pso_id}/confirm` and `.../fulfilment-planning/confirm-all`

- AC-S1 A press naming lines X and Y on an order whose active revision covers lines P and Q
  answers with `lines_confirmed` = exactly `[X, Y]` (each `{project_line_id, line_no,
  item_code}`, payload order) and `lines_carried` = 2. Both fields survive `response_model`
  on both routes (the per-order entry of `confirm-all` carries the same two fields).
- AC-S2 A held-back line (#1362) is absent from `lines_confirmed` and present in
  `lines_held_back`; `lines_confirmed.length + lines_held_back.length` = lines named.
- AC-S3 A press whose named lines were all already fulfilled (`revision_no: null`) answers
  `lines_confirmed: []`; a withdrawal-only press answers `lines_confirmed: []`.
- AC-S4 `lines_decided` still equals `lines_confirmed.length + lines_carried`.

## Server, preview (`POST .../fulfilment-planning/confirm-all` with `preview: true`)

- AC-P1 A preview press writes nothing: after it, `so_supply_decisions`, `order_inquiry_rows`,
  `stock_transfers` and `so_supply_decision_drafts` are unchanged (row counts and max ids).
- AC-P2 The preview answer per order carries the same `lines_confirmed`, `lines_carried`,
  `lines_held_back` and `lines_fulfilled_skipped` a real press would, plus `inquiry_rows`
  (verb, line_no, item_code, qty, delivery_date, stock_location, note) and `transfers`
  (kind, qty, from_location, to_location, line_no) the press would raise.
- AC-P3 A real press right after the preview, same body, writes exactly the previewed
  `lines_confirmed` and raises exactly the previewed inquiry rows and transfers.

## Board, Preview view (v3.1, owner refinement 30 Sep 2026; supersedes AC-V1..V5 below, retired)

v4 note (owner decision (b), 30 Sep 2026): the Preview view is now a FILTER MODE on the board.
AC-W2 and AC-W3 below read accordingly: the list shows only what will be sent (chip "Will be sent (N)"),
the cards and transfers stay on screen, and "Exit preview" or the chip returns the full grid.

- AC-W1 The board header's only press CTA is "Preview (N)" (N = saved lines the press would
  send). There is no Confirm button on the board and no popup anywhere.
- AC-W2 (v3.2) Pressing Preview posts the Confirm body with `preview: true` and opens a
  READ-ONLY view in place of the board content, titled "Preview: what Confirm will send", with
  the summary in the subtitle ("N lines · R Order Inquiry rows · T stock transfers · H held
  back · C carried forward unchanged") and two sections. "Order Inquiry" is the board's
  contributing-lines DataGrid with the SAME columns in the same order (Line, Sales order,
  Agent, Customer, Product, OI, Required date, Outstanding qty, Suggested, Decided, Rank,
  Verdict) and the same date format (dd/mm/yyyy), filtered to the lines the press will send;
  the OI column shows the row that will be raised ("<verb> <qty> · <delivery> · <location>",
  "already placed" when `is_new` is false); held-back lines stay in the same table greyed
  with "Held back · <reason>" in the OI column; the Verdict cell reads "Saved · you" or
  "Saved · <name>, <ago>" (amber when another planner or more than a minute before the board
  opened). No tick box, no row expansion, no Decide, no chips, no Undo, no explanation text.
  "Stock transfer" is the board's transfers grid (Transfer no, Product, From, To, Qty, State,
  For), read-only, listing the transfers the press would create ("on Confirm", State
  "Proposed"; "kept" when `is_new` is false).
- AC-W3 "Back to planning" returns to the board exactly as it was (drafts, ticks, page). The
  planner adjusts there (undo, re-save, Decide) and presses Preview again.
- AC-W4 "Confirm N lines" lives on the Preview view (N = lines_confirmed + lines_withdrawn
  echoed by the preview, never held-back lines). It posts the same body with
  `only_line_ids` = the previewed confirmed and withdrawn ids per order, so the press can never
  act on a line the preview did not show. If the board changed while the view was open (a
  refetch brought a new draft), Confirm is disabled and the view says "Preview again".
- AC-W5 After Confirm the board returns with the results block and toast reading the server
  echo (AC-R1..R4); an order the preview refused shows its error in the view and Confirm is
  disabled for the whole press.
- AC-W6 An order on a pending planning change previews like any other (server applies with
  notifications off and rolls back); the view marks it "applies pending change <name>".
- AC-W7 375px: both sections readable in the grid's card layout, no horizontal page scroll.

## Board, Preview then Confirm (v2, RETIRED by owner feedback 30 Sep 2026)

- AC-V1 The header shows "Preview (N)" (N = saved lines the press would send) and Confirm,
  disabled with the hint "Preview first", until a preview for the current board state has
  loaded. There is no confirm popup.
- AC-V2 Preview renders inline under the header, grouped per order: one row per previewed
  inquiry row (line, item, verb, qty, delivery date, location, the decision behind it, saved by
  whom and when). A decision saved by another planner or before this board was opened carries
  that fact in amber. Held-back and not-sent lines are listed with their reason, untickable.
- AC-V3 Every row starts ticked. Unticking removes that line from the press (its draft is left
  alone) and the Confirm label follows: "Confirm N lines". Unticking every row disables Confirm.
- AC-V4 Any change to the postable population after a preview (save, undo, board refetch that
  changes drafts) disables Confirm until Preview is pressed again; the stale preview says so.
- AC-V5 Confirm posts exactly the ticked previewed lines; results block and toast read the
  server echo (AC-R1..R4 unchanged).

## Board, pre-confirm dialog (v1, RETIRED by owner feedback 30 Sep 2026)

- AC-D1 Pressing "Confirm (N)" opens the dialog listing N rows grouped by sales order:
  "line <no> <item> · <Approved|Amended|Rejected> · <composition> · saved by <name>
  <relative time>". N in the title, the rows and the button count agree.
- AC-D2 A row saved by another user, or saved before this board was opened, carries an
  amber note "saved by <name>, <relative time>" (SO402757 line 8: "saved by Jayson
  Foundryx, 8 days ago").
- AC-D3 Every row starts ticked. Unticking a row lowers the button count and removes that
  line from the request body; its draft is untouched after the press (still Saved on the
  board). Unticking every row disables the button.
- AC-D4 Lines the press cannot post are listed under "Not posted" with their existing
  reason (suggestion changed / left out / staged rejection held by pending change), not
  counted, not tickable.
- AC-D5 An `approved` draft row shows the composition that WILL be posted (the live
  suggestion), e.g. "Borrow 43 + 57 from BRW-BB", so a changed suggestion is visible.

## Board, after the press

- AC-R1 The success toast's "N lines confirmed" equals the sum of the server's
  `lines_confirmed.length` over ok orders (a server answer without the field falls back to
  the posted count, and the results block says the server did not name its lines).
- AC-R2 The results block lists, under each ok order, the confirmed lines as
  "line <no> <item>" and "K carried forward"; held back / fulfilled / landed notices as today.
- AC-R3 When an order's posted lines differ from its echoed lines, the block shows an amber
  line "Posted P, server confirmed C: <missing lines>".
- AC-R4 Drafts are cleared locally only for lines the server echoed as confirmed.

## Board toolbar and Saved | All (v6 30 Sep 2026, v7 owner hand test 1 Oct 2026)

- AC-T1 The list has no title row. Its toolbar is `DataGridListToolbar`: board search, Saved | All,
  Filters (Status, chips), Columns (Rank hidden by default), Expand all / Collapse all, Decide; one
  row at 1280. The page header carries no search.
- AC-T2 The grid shows the same search, Saved | All and Status in a strip under the summary cards,
  inside the same Card > CardHeader shell, with no Columns, Expand/Collapse or Decide.
- AC-T3 Saved = the lines Confirm posts plus the covered rejections it withdraws (one predicate);
  All = every line (saved included), so a line does not vanish when it is saved. Default All
  (`?scope=saved` still opens Saved); the choice survives a Confirm press; counts read "Saved (N) |
  All (M)", M = every line under the product search, never the Status filter; Status combines with the segment; a segment change returns to page 1 and drops
  ticks on hidden rows.
- AC-T4 The pressed segment uses the system primary variant, like the Grid | List switch.
- AC-T5 Empty texts: "Nothing to confirm yet" (Saved), "No saved decisions match the filter" (Saved
  with a Status matching nothing). There is no "No other lines" text.
- AC-T6 The left-out banner link sets All and still reaches its line.

## Board list, selection

- AC-L1 The board list's header tick box ticks every row across every page ("Select all
  rows"); the Decide chip reads "<total> selected". Other listings keep the page-only box.

## Hand test (owner, crew test copy)

- HT-A `?orders=SO402757`: with Jayson's 22 Sep draft still on line 8, tick lines 6 and 9,
  Decide -> Buy, Confirm: the dialog lists line 8 SRTSH1040 with "saved by Jayson Foundryx"
  in amber; untick it; the button reads "Confirm 2 lines"; after the press the toast reads
  "2 lines confirmed", the results block lists line 6 and line 9 and "4 carried forward", no
  transfer is proposed, and line 8 still shows Saved.
- HT-B `?orders=SO423409`: header tick box ticks all 8 remaining rows, Decide -> Buy, Confirm:
  the dialog lists 8 rows; the results block lists the 8 lines the server confirmed and
  "8 carried forward"; any line the server held back is named beside the posted count.
