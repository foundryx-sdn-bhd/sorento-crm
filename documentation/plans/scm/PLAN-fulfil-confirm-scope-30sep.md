# PLAN: Fulfilment planning Confirm posts what the planner ticked, and says exactly what it wrote

Status: implemented (v5: no preview step, dry run inside Confirm), PR #1395, hand test pending. Track: feature lane (FE + BE, no migration, no RBAC).
Domain: scm (fulfilment planning board)
UAC: `fulfil-confirm-scope-30sep-acceptance-criteria.md`
Lane: `crew/fulfil-confirm-scope` (crew lane FULFIL-CONFIRM-SCOPE), base `origin/main` at e26410c20.
Mockup: `documentation/mockups/fulfil-confirm-scope/index.html`
Alembic: none.

## Complaints (owner, 30 Sep 2026)

A. `?orders=SO402757`: Cyndi ticked two SRTWT6808 rows and pressed Confirm; SRTSH1040 line 8
   was confirmed too, with borrow transfer TR-000056 (BRW-BB -> BRW-IB, 100, order-back from
   SO415810 line 48). Revision 2, 6 lines.
B. `?orders=SO423409`: a planner "clicked all" and confirmed; only 6 items went through.

## Evidence (prod dump 29 Sep 19:00 UTC, restored to scratch DB `sorento_dump_0929`)

Measured, not guessed:

- SO402757 (`projects.sales_orders` 688bdd83) active revision 1, Jayson Foundryx, 22 Sep
  03:12 UTC, covers lines 5, 10, 11, 12 only.
- `projects.so_supply_decision_drafts` holds ONE draft on that order: line 8 SRTSH1040,
  verdict `approved`, saved_by Jayson Foundryx, 22 Sep 03:09 UTC (three minutes before rev 1,
  never promoted because rev 1 did not name it). Its stored borrow rows name LEENA's donors
  (SO417636, SO383872 lines 20/50).
- Board built in-process on the dump (`FulfilmentBoardService.build(["SO402757"])`): line 8 is
  `covered=False`, `draft=approved / Jayson Foundryx`, live suggestion `borrow 43 + 57 from
  BRW-BB` naming JEREMY's donors (SO396347, SO396350). Lines 6 and 9 SRTWT6808 are the two
  rows Cyndi could tick (covered by inquiry rows, no decision).
- Simulated press (`ProjectSupplyService.confirm_many`, never committed): posting lines 6 + 9
  plus line 8 writes revision 2 with 7 lines and raises transfer **TR-000056**, 100, BRW-BB ->
  BRW-IB on line 8's core line. Posting lines 6 + 9 alone writes 6 lines and no transfer.
  The transfer number, quantity and route match the owner's complaint exactly.
- SO423409 (d50faffc) revision 1, Nurain, 28 Sep 09:14:42 UTC, 8 seconds after adoption
  (the Confirm press adopts first): 8 of 16 open lines, every one `buy` with amend reason
  "D. DATE : DEC'26"; 6 of the 8 got an order-inquiry row. Draft audit rows begin 29 Sep
  00:58 UTC, so the 28 Sep press cannot be replayed row by row. On the dump all 8 remaining
  lines confirm cleanly today (simulated). Hold-back (#1362) merged 29 Sep 18:20 MYT, after
  that press.

## Root causes

1. **Confirm's scope is "every saved draft on the board", not "what I ticked"**
   (`FulfilmentBoardPanel.tsx` `runConfirmAll` -> `confirmLinesFor(contributions, so, draftWithoutPreMark)`;
   `fulfilmentBoard.ts` `confirmLinesFor`/`lineFor`). The draft map is seeded from the server on
   every board read (`contribution.draft`, panel seeding effect), so a draft saved by anybody, on
   any day, rides along with the next person's press. The tick boxes feed Decide only, and
   Decide unticks a row once it is saved (R9), so at press time the selection is empty and the
   only signal is the number in "Confirm (N)" and the dialog title "Confirm N lines across M
   orders?". That is complaint A: Cyndi's press carried Jayson's 8-day-old draft on line 8.
2. **An approved draft is re-derived from the LIVE suggestion at press time**
   (`lineFor` tail: `borrowComponents(contribution, decision.borrow)` reads
   `contribution.sources`; the saved composition contributes reasons only). Line 8 was saved
   with LEENA's donors and posted with whatever the ladder proposed at press time (JEREMY's
   donors on the dump, SO415810 in prod). Nobody approved the composition that was written.
3. **The result never names the lines it wrote.** `ConfirmResult`/`ConfirmManyOrderResult`
   carry counts only; the toast's "N lines confirmed" is derived client-side
   (`orders[].lines.length - fulfilled - heldBack`), and the results block prints "confirmed as
   revision N (K purchase rows handed over)". A scope mismatch is silent by construction.
4. Ways a press falls SHORT of what was ticked (complaint B is one or several of these; the
   28 Sep press cannot be replayed):
   - header tick box selects the current PAGE only (`components/ui/data-grid-select-column.tsx:48`,
     `toggleAllPageRowsSelected`; board list `pageSize={25}`), so "click all" on a board with
     more than 25 rows ticks 25;
   - Decide "As suggested" skips rows already saved or already confirmed (`BoardDecideControl.tsx`
     `canQuickSave`), the other ways skip rows the pick cannot cover in full (`decideComposition`);
   - server hold-back (#1362, `_write_holding_back`) drops refused lines out of the press and
     confirms the rest;
   - a draft whose line facts moved (`draft.stale`) is left out; unpostable lines (`no_mirror`,
     `no_reserve_warehouse`, `buy_reason_missing`) are left out; fulfilled lines are skipped.
   All of these already say something (toast, banner, results block), none of them names the
   lines in the same place as what WAS written.

## Design (simplest thing that works)

No new state, no new endpoint. Three changes, each on an existing surface:

S1. **Backend echoes the lines it wrote.** `ProjectSupplyService.confirm()` returns
    `lines_confirmed: [{project_line_id, line_no, item_code}]` (the `checked` lines the new
    revision froze from this payload) and `lines_carried: int` (covered lines carried forward
    untouched). `confirm_many` copies both per order. `ConfirmResult` and
    `ConfirmManyOrderResult` declare them (undeclared fields are dropped by `response_model`).
    A withdrawal-only press echoes `lines_confirmed: []`.
S2. **The pre-confirm dialog lists the lines it will post, and each has a tick box.** The
    existing AlertDialog ("Confirm N lines across M orders?") gains a list grouped per order:
    line no, item code, verdict word (Approved / Amended / Rejected), composition summary
    (e.g. "Borrow 43 + 57 from BRW-BB", "Buy 239"), and "saved by <name> <relative time>".
    A line saved by someone other than the current user, or saved before this board was
    opened, shows that fact in amber. Every box starts ticked; an unticked line is left out of
    the payload and keeps its draft (nothing else changes). The action button reads
    "Confirm N lines" and follows the ticks. Lines the press cannot post (stale, left out,
    held back by a batch) are listed under "Not posted" with their existing reasons.
S3. **The toast and the results block read the server echo.** "N lines confirmed" = the sum
    of `lines_confirmed.length` over ok orders. Under each order's result line the block lists
    the confirmed lines ("line 6 SRTWT6808 · line 9 SRTWT6808"), then "K carried forward",
    then held back / fulfilled / notices as today. When the lines posted for an order differ
    from the lines echoed (server held some back, or an older server sent no echo), the block
    says so in amber: "Posted 3, server confirmed 2: line 8 SRTSH1040 was held back".
S4. **Board list: the header tick box ticks every row, not the page.** `buildSelectColumn`
    gains an opt-in `selectAllRows` (uses `toggleAllRowsSelected`, label "Select all rows");
    only the fulfilment board list passes it. Other listings are untouched.

Not changed: which lines are POSTED for an approved draft (root cause 2). The dialog now shows
the composition that will be written, so the swap is visible before the press; reversing the
"stale is judged on facts, never the proposal" ruling is out of scope and named for the owner.

## Design v2 (owner Lavish feedback, 30 Sep 2026)

Owner, verbatim: "i don't need a popup showing what's to be confirmed, cause the list will be
exhausting, i need to know is there any autosave button, or, we can have a preview CTA before
confirm, so they can preview and finalize what will be sent to OI before confirming, so they need
to click preview first, then only can confirm, in preview, they supposed to see whatever will be
sent to the OI, and, again, I don't want any auto save and please check if there is any autosave
that causes the complaints".

Supersedes S2 of the Design section above (the dialog list is removed). S1 (server echo), S3
(results read the echo) and S4 (header tick = all rows) stand.

P1. **No confirm popup.** The AlertDialog "Confirm N lines across M orders?" and its line list
    go. Confirm writes directly once Preview has been opened.
P2. **Preview is a required step.** Header gets "Preview (N)" beside Confirm. Confirm is disabled
    ("Preview first") until a preview for the CURRENT board state has loaded; any draft save,
    undo, tick change or board refetch that changes the postable population disables Confirm
    again until the next Preview.
P3. **Preview is the server's answer, not a client list.** Preview posts the same body Confirm
    would post to `POST /fulfilment-planning/confirm-all` with `preview: true`. The server runs
    `confirm_many` exactly as for a real press and rolls every order back instead of committing,
    answering per order with `lines_confirmed`, `lines_carried`, `lines_held_back` (with
    reasons), `lines_fulfilled_skipped`, `inquiry_rows` (verb, line_no, item_code, qty,
    delivery_date, stock_location, note) and `transfers` (kind, qty, from, to, line_no). Nothing
    is derived on the client.
P4. **Preview panel is inline under the header** (same place as the results block), grouped per
    order: one row per OI row the press would raise, with a tick box (default ticked), the
    decision that produced it, and who saved that decision and when (amber when another planner
    or before this board was opened). Held-back lines and not-sent lines (stale, unpostable) are
    listed with their reason, untickable. Unticking a row removes its line from the press and
    keeps its saved decision; the Confirm label follows the ticks ("Confirm N lines").
P5. **Confirm** posts the ticked population; toast and results read the echo (S3 unchanged).

## Design v3 (owner hand test on b51a4a40c, 30 Sep 2026)

Owner, verbatim: "for the preview, actually my idea is to use the same datagrid table at the
bottom, don't need this extra section at the top, and it is supposed to be CTA (Preview)
button instead of the Confirm, Confirm should appear after preview, and in preview, the table
of the list of items should be filtered to whatever will be sent to purchasing after clicking
Confirm, and what will become stock transfer after confirm, so it needs to be a clear and
straightforward view on what will be distributed to which flow after clicking confirm".

Supersedes P4 (the inline panel). P1 (no popup), P2 (Preview required), P3 (server dry run)
and P5 (echo) stand.

Q1. Header CTA is "Preview (N)" alone. After the preview loads it becomes "Confirm N lines"
    with "Exit preview" beside it; the count follows the ticks. A population change drops
    Confirm and brings "Preview (N)" back (same fingerprint as v2).
Q2 (v3.1, owner refinement, verbatim: "i think you put 2 sections: order inquiry and stock
    transfer, read only sections, then i can always escape the preview to come back to the
    fulfilment planning page existingly to make any adjustment"). Preview is a READ-ONLY view
    replacing the board content, with two sections built from the dry run using the board's
    datagrid components: "Order Inquiry" (every row sent to purchasing: line, sales order,
    product, verb, qty, delivery date, location, decision, saved by whom and when, amber when
    another planner or before this board opened) and "Stock transfer" (every transfer created:
    product, from, to, qty, kind, for which line, the order-back note). A short third note lists
    held-back and not-sent lines with reasons. No ticks, no editing. "Back to planning" returns
    to the unchanged board for adjustments (undo, re-save, decide); "Confirm N lines" lives on
    the Preview view only. Retired with this: the Flow column and the filtered grid of Q2 v3,
    and per-row ticks (leaving a line out = Undo its decision on the board, then Preview again).
    Q2 v3 text, superseded: Preview mode filters the existing "Every contributing line" grid to the rows the server
    says the press will post, keeps the tick column (untick = leave out, decision kept) and
    adds a first column "Flow": one tag per thing the server would write for that line:
    "Purchasing · <verb> <qty>" per order-inquiry row, "Stock transfer · <from> to <to> <qty>"
    per transfer. A banner above the grid sums it: "N to Purchasing · T stock transfers · H
    held back · C carried forward". Held-back rows (reason, no tick) and not-sent rows sit in
    their own groups under the ticked rows, never mixed in. Decide, Save all suggested and Undo
    all are disabled in preview. Exit preview restores the full grid.
Q3 (v3.1). The board's Stock transfers grid is untouched; the Preview view's own Stock
    transfer section is the list of transfers the press would create.
Q4. The v2 inline preview panel is removed. Results block after Confirm unchanged.

## Autosave audit (owner question, 30 Sep 2026)

There is no autosave. Every server draft write goes through one route, reached only by a click:

- Only backend writer: `PUT /fulfilment-planning/lines/{key}/draft`
  (`app/api/v1/projects/fulfilment_planning.py:393` -> `project_line_draft_service.save_draft`,
  `app/services/project_line_draft_service.py:233`, an upsert keyed by core line). Grep of
  `app/` finds no other `SOSupplyDecisionDraft(` / `save_draft(` writer; the undo reconstruct
  states it never writes one (`project_supply_undo_reconstruct_service.py:631`).
- Only frontend caller: `FulfilmentBoardPanel.tsx:854` inside `decide()`, fired by: the row
  editor's "Save decision" button (`BoardLineDecisionPanel.tsx:604`), the verdict chips
  (`BoardVerdictActions.tsx`), the Decide strip (`BoardDecideControl.tsx`, one PUT per ticked
  row), the cell dialog's bulk verbs (`BoardCellBreakdownDialog.tsx`), and the header's
  "Save all suggested" (`FulfilmentBoardPanel.tsx:1990`, `decideMany`). No timer, blur,
  navigation or mount effect calls it.
- Not writers: planning-change pre-marks (`FulfilmentBoardPanel.tsx:684`, `preMarked: true`)
  are local only and never posted (S5); the board-read seeding effect
  (`FulfilmentBoardPanel.tsx:738`) copies server drafts into the screen and writes nothing.
- What SO402757 hit: an explicit Approved save by Jayson Foundryx on line 8 (22 Sep 03:09 UTC)
  that revision 1 three minutes later did not name. Saved drafts are shared and persistent by
  design (R-F), and Confirm posts every saved draft on the board
  (`FulfilmentBoardPanel.tsx:1319`). That persistence, not an autosave, rode along with
  Cyndi's press. Preview (P3/P4) makes it visible and untickable before anything is sent.
- Nothing to remove. Owner option, not built unless asked: require a draft saved by another
  planner to be re-saved before it can be sent.

## Slices v2

6. BE: `preview: true` on confirm-all (run + rollback per order, echo OI rows and transfers) +
   pytest.
7. FE: remove the dialog list; Preview button + inline preview panel with ticks; Confirm gated
   on a fresh preview; vitest.
8. Review + browser pass + hand test script v2.

## Slices (v1, built)

1. BE: echo (`lines_confirmed`, `lines_carried`) + pytest.
2. FE lib: `confirmLinesFor` / `confirmSummaryFor` take an `excludeKeys` set; dialog rows
   built by a pure helper `confirmDialogRowsFor(contributions, draft, ...)`; vitest.
3. FE panel: dialog list + ticks, toast/results from echo, mismatch notice; vitest.
4. FE list: `selectAllRows` on the board's select column; vitest.
5. Review + browser pass + hand-test script (`laneboard/scripts/<PR>.md`) reproducing A and B
   on the crew test copy.

## Design v4 (owner decision (b), 30 Sep): Preview is a filter mode on the board grid

The separate Preview view is replaced by a filter mode on the board itself. The server contract
(dry run, echo, `only_line_ids`) is unchanged.

1. "Preview (N)" in the board header runs the dry run (adopt-if-needed, the shared
   `buildConfirmOrders`, `previewConfirmMany`) and switches the board into preview mode. No new
   page: the cards and the Stock transfers grid stay on screen.
2. On the list a chip "Will be sent (N)" (pressed) shows only the previewed population
   (confirmed, withdrawn, every line with a previewed inquiry row, held-back lines) in the
   read-only column set (no select column, expansion, Decide, chips or Undo). The OI cell reads
   the raised row, "Withdrawn", or "Held back" with the reason; held-back rows are greyed. Save
   all suggested and Undo all are disabled. The board subtitle shows the preview summary. The
   grid view (matrix) is left untouched: preview mode always renders the list.
3. The Stock transfers grid lists the previewed transfers at the top ("on Confirm" or "kept",
   State Proposed) beside its real rows, read-only.
4. The header shows "Confirm N lines" (disabled when stale, when an order is refused, or at 0)
   and "Exit preview". The chip toggles the filter off too. A refused order and a stale preview
   ("Preview again") are named in the notes under the header.
5. Confirm posts the stored previewed body with `only_line_ids`, exits preview mode, and the
   results block and toast read the echo.

## Design v5 (owner, 30 Sep hand test): no preview step; the dry run lives inside Confirm

The owner did not want an extra step ("very troublesome"). The Preview button, the filter mode and
its read-only columns are removed. The header CTA is "Confirm (N)" again, with no popup. Confirm
now runs the server dry run first (`previewConfirmMany` with the built body), then posts the same
stored body narrowed to `only_line_ids` per order (confirmed plus withdrawn ids; batched orders
whole), so hold-back and the recheck still protect and the toast and results block read the echo.
An order the dry run refuses is not posted; the others are, and the refusal appears in the results
block as a refused entry. Also from the hand test: the "Every contributing line" list gets the
standard Columns menu (Rank hidden by default) and a Status filter over the Verdict states, and the
Line column prints the plain position with a "not synced" title when AutoCount gave no line number.

## Design v6 (owner hand tests, 30 Sep): the list toolbar, the grid strip and Saved | Others

- The "Every contributing line" list uses the app's own `DataGridListToolbar`: the board search
  (`searchSlot`), the Saved | Others toggle, Filters (Status, with active chips), Columns
  (`showColumns`, Rank hidden by default, listing key `-list-v2`), Expand all and Collapse all as
  `leftActions`, Decide as `primaryAction`. The page header keeps only the title, Rows / By date,
  Grid | List and Confirm.
- The grid has no table, so it gets a filter strip under the summary cards inside the same
  Card > CardHeader shell: the same search box, the same toggle, the same Status control.
- Saved | Others: Saved is exactly what Confirm posts (`pressPostsContribution`, the one predicate
  `confirmLinesFor`, `rejectedCoveredLineIdsFor` and `plannedLineCount` read); Others is every other
  line. Default Others, kept across a Confirm press, counts from the whole selection under the
  product search (never the Status filter), Status combines as AND, the segment resets the page and
  drops ticks on hidden rows. The active segment is the system primary variant, like Grid | List.
- Empty texts: Saved empty "Nothing to confirm yet"; Saved with a Status matching nothing "No saved
  decisions match the filter"; Others empty "No other lines".
- `?scope=all` (no segment pressed, every line shown) is internal: the left-out banner link uses it
  to reach a line Confirm will not post, and the panel tests use it for mixed fixtures.

## Design v7 (owner hand test 1 Oct): Saved | All, default All

The Others segment hid a line the moment it was saved, which read as the line disappearing. The
toggle is now Saved | All: All lists every line (saved included), default All, `?scope=saved` still
read once from the URL. Saved stays exactly Confirm's posting set (`pressPostsContribution`,
unchanged). Counts read "Saved (N) | All (M)", M = every line under the product search, never the
Status filter. The pressed segment stays the primary variant. "No other lines" is gone; the Saved
empty texts stay. The grid "N of M" fraction shows only when Saved or another filter narrows. The
left-out banner link sets All. `BoardScope` is `'saved' | 'all'`; the list view's `scope` prop carries
`allCount` in place of `othersCount`.

## DoD gate (PRINCIPLES.md "Definition of Done gate", checked 30 Sep 2026 on PR #1395)

Track: full track (diff far above 300 lines across FE and BE), no migration, no auth/RBAC change,
no new external ingest surface; security-reviewer not run, diff outside its surface (owner
ruling on the skip rule).

1. Mock swapped to real: no in-memory service; Confirm and its dry run hit `confirm-all` on the
   real backend; verified on the crew test copy against the shared dev DB.
2. Backfill: no new column, nothing to backfill.
3. New permission: none (existing `EDIT` on the route).
4. New DB column reaching the FE: none (response fields only, declared on `ConfirmResult` /
   `ConfirmManyOrderResult`, asserted on the HTTP body in pytest).
5. User-perspective verification at 375px and 1280px on the crew copy (`npm run dev`, HMR, one
   dev server): evidence run in `evidence/fulfil-confirm-scope/EVIDENCE-30sep.md` (list, grid,
   toggle, Confirm), two screenshots under 200 KB beside it. Earlier browser passes v3 to v8
   are logged in the crew reports.

Red-first: backend contract slices (echo, preview, notification guard, only_line_ids) and the
FE preview slices were tester-first; the toolbar and toggle polish rounds (8 to 12) were
test-with-code, which is a process deviation named here. Kill tests: reviewer rounds 1 to 6
(round 6 mutates the Saved/Others filter, the Confirm-only-saved rule in `lineFor`, the
preview commit and notification guards). Round 6 kill results: filter a(i)(ii)(iv) red, only-saved
b(i)-(v) red, backend c(i)-(iii) red; a(iii) and the left-out link gap closed in round 13.
Reviewer: seven rounds, all findings fixed on the branch.

## Grill (feature skill step, run 30 Sep 2026 after the owner's process ruling; answers recorded as crew relays them)

| # | Decision or assumption | Recommendation | Owner answer |
|---|---|---|---|
| G1 | Confirm scope = every SAVED line on the board (all orders in the selection, any saver, any age); unsaved Suggested lines never post | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G2 | Confirm runs the server dry run first, then posts only what passed (two round trips) | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G3 | A line the dry run holds back stays Saved and is named in the results with its reason | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G4 | Confirm on a never-planned order adopts it first (committed write before the dry run) | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G5 | An order on a pending planning change is applied whole (no per-line scope), previewed with notifications off | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G6 | One refused order does not stop the others; reported in the results block | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G7 | Saved / Others default Others (Panel@cc:310-316); `?scope=all` and `?scope=saved` are read once from the URL and never written back (URL effect writes granularity, rows, product, view only, Panel@cc:363-378); the left-out banner link switches to all with no segment pressed (Panel@aa:348) | keep; `?scope=all` internal-only | corrected after fact-check; crew: internal-only |
| G8 | Rank hidden by default through a bumped listing key (saved column maps reset once) | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G9 | Status filter offers the four owner values plus Rejected, Suggestion changed, Cancelled, Needs a location | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G10 | Expand all / Collapse all are icon buttons in the toolbar LEFT cluster (`leftActions`, ListView@aa:1034-1062), per the toolbar contract (data-grid-list-toolbar.tsx:183-189); the earlier Actions-menu placement (cc) was against that contract and was moved in round 13 | keep | corrected after fact-check |
| G11 | While rows are ticked the shared toolbar shows N selected + Clear and hides Filters, Columns, Expand all and Collapse all (data-grid-list-toolbar.tsx@cc:431-460, :483-537); the search slot with the toggle stays (`keepSearchWhileSelected`, ListView@cc:960) | keep (design-system behaviour) | corrected after fact-check |
| G12 | A saved Approved decision is re-derived from the live suggestion at press time; the results name what was written | leave this lane; separate ruling for "post what was saved" | with the owner; current behaviour stays until he answers |
| G13 | Line column: AutoCount number when synced, else the positional number with a hover note; ingest gap filed separately | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G14 | Only the last mock (v3.2, retired) stays at `documentation/mockups/fulfil-confirm-scope/index.html`; v1 to v3.1 are recoverable from git history only (c7f55f7f9, 08c3f94b1, 9b85e391e, ad7ac3d97); the shipped v5/v6 has no mock (owner: reuse existing components) | keep | corrected after fact-check |
| G15 | No security-reviewer: no auth / RBAC / permission / webhook surface in the diff | keep | as recommended (crew, from the owner rulings, 30 Sep) |
| G16 | Saved segment = Confirm's own posting set through one predicate `pressPostsContribution` (lib@aa:363-371; Panel@aa:1811-1830; ListView@aa:246-247): a staged reject on a confirmed line is Saved (pill still Rejected) unless a pending batch holds it back; a saved line with no mirror on an adopted order, stale, or unplannable is Others. Before round 13 the segment read the pill verdict (Panel@cc:1811-1820) and both edges were wrong | one source of truth | crew decision 30 Sep, built in round 13 |

Fact-check corrections (30 Sep, read-only pass with `git show` citations; cc = cc4fcf77e, aa = aa0923a4c, e26 = e26410c20):
- G1 caveat: a draft is written to local state before its PUT returns (Panel@cc:842-873), so a press during an in-flight save can carry it. G1 "all orders": `buildConfirmOrders` reads `board.data.contributions`, unwindowed (Panel@cc:617-620, :1236; fbs@cc:858-868).
- G2/G6 gap: an order whose dry run answers ok with no confirmed or withdrawn line and no batch was dropped from the press silently (Panel@cc:1526-1527), e.g. every named line already fulfilled (pss@cc:5248-5252); fixed in round 14.
- G13: the ingest that wrote SO422076's lines is UNVERIFIED (integration_log has no row for DocKey 45823051); the shape matches document_ingest_service.py@cc:1316, :1333-1334 (line_no only when the payload carries line_number). Scale on the dev copy: 611,505 of 611,532 AED_SORENTO SO lines have NULL line_no.
- G15: preview relies on rollback plus two guards (notify flag, `confirm_preview`); side effects outside the DB on that path beyond those two were not audited.
- Root causes: RC2 the `borrowComponents` tail (lib@e26:496) is the UNCOVERED branch; a covered approval goes through `suggestionWithReasons` reading the live walk (`_suggest_live_for_covered`, fbs@cc:2913-2915). RC3 should read "the result named no confirmed line" (it did carry lines_held_back, failing_lines, landed_buy_notices). RC4 `buy_reason_missing` is not a frontend left-out reason (lib@e26:350 has only no_mirror and no_reserve_warehouse). "8-day-old draft": the draft date (22 Sep 03:09 UTC) is in the dump; the press date (30 Sep 10:47) comes from the owner's brief, not the dump. RC2 "SO415810 in prod" comes from the owner's brief; "SO396347/SO396350 on the dump" from the in-process board build in this session, not re-run by the fact-check.
- Autosave audit line numbers at e26: `saveLineDraft` call is Panel:838 (not :854); pre-mark effect :652 with the write at :668 (not :684); seeding effect starts :722 (not :738); "Save all suggested" `decideMany(quickSaveKeys)` is :1872-1874 (not :1990).

