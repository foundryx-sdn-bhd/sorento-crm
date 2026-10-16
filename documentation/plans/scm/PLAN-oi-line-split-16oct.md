# PLAN: split one SO line into delivery tranches in Order Inquiries (OI-LINE-SPLIT)

Status: Plan. Discovery done; behaviour card filed to crew (ask); Lavish mock next; build only after the owner OKs the mock. Size L (new interactive flow on the OI, likely a
migration for tranche rows). Track: standard (L), lead pattern (tester red tests, coder, reviewer,
security-reviewer only if auth/RBAC is touched, browser pass 1280/375).

Card: `oi-line-split-behaviour-card.md` (alongside, after discovery). UAC:
`oi-line-split-16oct-acceptance-criteria.md` (after the card is answered). Mock:
`documentation/mockups/OI-LINE-SPLIT/index.html`.

## Journey

Owner 16 Oct ~21:20: staff Excel shows one SO line split into several delivery tranches (SO417636
CSK14B: 417 @ 20/10/2026 split 14 @ 20/9/2026 + 236 @ 20/11/2026 + 167 @ 20/11/2029; 504 @
20/12/2026 split 37 @ 20/11/2026 + 467 @ 2/1/2027). AutoCount users reduce the line, change its
date and add new lines, so the CRM OI only sees net numbers and the split is lost. Staff need a
very interactive, flexible, drag-and-drop way to allocate one line's qty to several tranches (qty +
delivery date + remark), before Confirm or inside Confirm.

## Steps

1. Discovery with file:line (OI line mapping, AutoCount pull, Confirm, OI email/Excel, grid
   components, OI Lines page from #1727).
2. Behaviour card as ONE crew ask.
3. Lavish mock on SO417636 (business data only).
4. After owner OK: UAC, tickets, tester red tests, coder, reviewer, browser pass, hand-test script.

## Discovery (summary; full citations in the card)

- One OI row per SO line (owner rule, `project_so.py:1138-1145`); tranches sit under the row.
- AutoCount edits arrive per DtlKey; reduce + add new lines = unrelated qty_down / added / qty_up
  change rows (`planning_change_service.py:800-810`); pure split only on same date
  (`project_order_inquiry_service.py:5308`).
- Confirm parts must sum to open qty (`project_supply_service.py:6404`); no date per part.
- Email one row per saved line (`project_order_inquiry_service.py:4280-4295`); Excel one row per OI
  row, month sheets, no remark (`order_inquiry_worklist_service.py:195-222, 3353-3376`).
- FE reuse: Sheet, `SupplyBar` (`_shared/components/SupplyBar.tsx:42`), `DataGridTableDndRows`
  (`components/ui/data-grid-table-dnd-rows.tsx:183`), Kanban/Sortable (`components/ui/sortable.tsx`),
  `ContextMenu`, `DatePicker`, `Badge`, `Kbd`; quotation lines grid pattern
  (`[projectId]/components/QuotationLinesGrid.tsx`).

## Design draft (pending owner answers on Q1, Q2, Q4, Q5; BE = sorento_crm_backend/)

Storage (migration, company-scoped, UUID ids):
- `projects.so_line_splits`: id, company_id, so_line_id (mirror `projects.sales_order_lines.id`, UNIQUE,
  FK CASCADE), base_qty (the qty the split was made against), updated_by/at.
  Keyed on the mirror line: OI rows, `buy_lines["line"]`, `_split_row` and planning-change
  `project_line_id` all use it (`app/models/project_so.py:1011-1014`); it survives AutoCount re-sync
  (in-place update by DtlKey, `app/services/document_ingest_service.py:1527-1594`). Not in the board
  draft: drafts are deleted at Confirm (`app/services/project_supply_service.py:7425`).
- `projects.so_line_split_tranches`: id, split_id (FK CASCADE), qty > 0, delivery_date, remark,
  target_so_line_id (FK mirror, ON DELETE SET NULL; NULL = not in AutoCount yet; = origin = kept on
  its own line). Save validates same SO, product and location.
- Cancelled target line: hook `flag_rows_for_cancelled_lines` (`project_order_inquiry_service.py:738`)
  to null the link.

Engine (the move, explicit links only):
- New pre-pass `_apply_tranche_links` before `_apply_pure_splits` in `refresh_for_decision`
  (`project_order_inquiry_service.py:1323`): moves each linked tranche's qty + placement links + ack
  from the origin row to the target line's row via `_split_row` (no date rule inside it, :5428), with a
  merge branch when the target already has a row, tranche qty instead of the target's Buy, previous_*
  + changed ack when the date moves (as `_settle_row_in_place` :2522-2547), latest tranche first.
  Keeps the pure split's single-live-ORDER / not-redirected / no-reserve-link guards; no date or
  exact-sum rule. The normal netting then tells only what is left (the mock's "left to tell").
- Planning-change apply: `_oi_demand_rows` drop = from - to - linked_out
  (`planning_change_service.py:3889-3892`), else a false CANCEL BALANCE stays.
- Both origin and target must be named in the same Confirm (pure split has the same limit, :5326-5332);
  the panel's Save names both lines.

Purchasing view (tranches as a read-only child view; `row.delivery_date` stays the line date so
cascade windows and filters are unchanged):
- Email: `_queue_saved_line` (:4530-4560), `handover_item` (:4229), `_build_handover_context`
  (:12419), resend (`oi_handover_resend.py:86,109,194`); template sub-rows need migration oihr_0009.
- Excel: `_all_rows` (`order_inquiry_worklist_service.py:3387-3409`) expands tranches, month sheets
  follow; REMARK appended at the END of EXPORT_HEADINGS (:195-222; `tests/scm/test_oi_worksheet_writer.py`
  pins the first 10).
- Lines tab: `serialize_rows` (:6372/:6433) adds `tranches`.

Tests that pin today (must stay green): `tests/test_oi_cancel_bugs_so422492.py` (test_review_2_5_6
:467 "a date move declines the split" stays true without stored links), `tests/scm/test_oi_email_so_line_changes.py`,
`tests/test_oi_saved_line_email.py`, `tests/scm/test_oi_date_move_settle.py`, `tests/test_planning_changes.py`,
`tests/test_oi_handover_excel.py`. Global guards: `tests/test_schema_uuid_id_principle.py`,
`tests/test_list_pagination_limit.py`.

Risks: origin and target not in the same Confirm; ack mix when merging into a row with another
handshake; an unlinked late tranche is placed against the line date (cascade reads the row date).

## Open

- Owner answers 16 Oct ~22:05: Q1 (a), Q2 (b) splits add up EXACTLY to the line qty (no balance,
  gap shown live, Save blocked), Q3 (a) staff-picked link, Q4 yes = (b) drag qty between SO lines of
  the same SO (each split keeps its own line and records the line it sits on), Q5 (a). New owner
  rule: the OI Excel is never split by month (separate PR #1748, crew ruled TOTAL QTY (a)).
- Owner Lavish notes on v1 (22:30): split at header level on planning (all SO lines, drag and drop
  across lines); never say "tranche", say "split"; real drag and drop (reuse @dnd-kit).
- Mock v2 filed: whole-SO split view (rows = SO lines grouped by item + location, columns = dates,
  New date, Cancel; a New line row), zoom pane = the single-line view. Design draft above still
  holds; the balance mode column is dropped (Q2 b) and a split may be a cancel.
