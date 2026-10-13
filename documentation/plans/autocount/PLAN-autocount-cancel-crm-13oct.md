# PLAN: AutoCount Cancel SO / Cancel PO in the CRM (AC-CANCEL-CRM)

Status: Review (fix round 1 in, reviewers verifying; PR #1623). Track: full (migration, RBAC slugs).
Lane: AC-CANCEL-CRM. Sibling: AC-CANCEL-PULL (the shared service that reads the cancel docs; payload agreed via crew).

## Goal

The CRM accepts Cancel SO and Cancel PO documents from the shared service (ingest contract 2.8) and derives
cancelled quantity per SO / PO line on every sync, so cancelled quantity stops reading as delivered, received
or incoming.

## Owner rulings (13 Oct, closed)

- Cancelled qty is DERIVED on every sync: sum of cancel-doc lines for that SO/PO line. No events, no flags.
  Deleting the cancel doc in AutoCount lowers it. OI rows already retired stay retired.
- Partial cancel lowers planned qty; a line is "cancelled" only when fully cancelled.
- SRT first; Mocha later by configuration, same design.
- AutoCount is truth; no name / fuzzy matching. Cancel lines carry no line key since ~Nov 2024: match by
  SO/PO no + item (+ location + qty); ambiguous = manual review, never a guess (2026: 854 of 5,365 XS lines).
- Never stuck UI; every failure ends in a clear state.

## Measured (code on main fe6c08d9)

- SO `qty_delivered` IS AutoCount's SODTL TransferedQty, copied straight from the SO push
  (`document_ingest_service.py:244`, `:1291-1329`, setattr `:1550`). Nothing derives it from DO lines
  (DO ingest writes no SO quantity). PO `qty_received` is the same, from the PO push (`:276`, `:285`).
- Line status at push: header cancelled -> cancelled, delivered >= ordered -> closed, else open
  (`document_ingest_service.py:1828-1850`). A fully Cancel-SO'd line therefore lands `closed`, delivered = ordered.
- Owed / outstanding is `ordered - delivered` (`project_supply_service.py:225-247`, `:487-499`; `demand.py:168-174`;
  `committed_v`). Because TransferedQty = delivered + cancelled, outstanding is ALREADY right (cancelled qty is not
  owed). What is wrong is (1) the delivered figure people read, (2) the planned qty: `plan_qty_of` /
  `demand.plan_qty` = `coalesce(qty_required, qty_ordered)` ignores delivered by the 14 Sep ruling, so a cancelled
  unit is still planned and Confirm refuses the line (SO373923, `PLAN-so-cancel-line.md`), (3) the status: closed
  instead of cancelled.
- Same on PO: incoming = `qty_ordered - qty_received` (`project_supply_service._po_rows:9772`) is already right;
  "received" is overstated.
- SPO (`spo_allocations`, the shipping order): receipt is `max(GRN receipt, stated_received)` where stated_received is
  AutoCount TransferedQty (`procurement_service.py:1189-1213`, `shipping_order_ingest_service.py:989-1014`).
  Whether a Cancel PO can name an SPO is being checked with AC-CANCEL-PULL / dev data.
- Existing cancel plumbing reused: `SalesOrderLine.cancelled_at/_by_id/cancel_reason` (#1524, board cancel),
  `retire_rows_for_cancelled_lines` (`project_order_inquiry_service.py:4498`, #1530: OI rows cancelled, links
  removed, handover email "Transferred to Cancel SO"), `flag_rows_for_cancelled_lines` (`:563`), the planning-change
  diff around an SO push (`document_ingest_service.py:1236-1280` -> `planning_change_service`), #1599's
  undecided-change guard.
- DO / GRN ingest pattern to copy: `autocount_doc_ingest_service.py` (vendor PascalCase record with `Details`,
  envelope `book`, idempotency (company, book, DocKey), LastModified stale guard, `unchanged` verdict, waiting-link
  retry at batch end, `/deletions` with `doc_keys` + DocDate range marks vanished, never deletes).

## Behaviour card: owner answers (13 Oct)

- Q1 (a): separate `qty_cancelled` on `sales_order_lines`, `purchase_order_lines`, `spo_allocations`;
  AutoCount's ordered and TransferedQty are kept exactly as sent.
- Q2 (a): unexplained-transfer tie-break, order-independent (a test pushes the same docs in both orders).
- Q3: NO user involvement, no review screen. Lines still ambiguous after Q2 (a) take a deterministic
  fallback (R2 step 6). Fallbacks are written to the target line's history and counted in the sync log;
  lines that fit no candidate stay unmatched (counted in the log, never counted as cancelled).
- Q4 (a): a partial cancel raises the AutoCount planning change (qty down) for CS on the board (#1599 flow);
  no silent trim.
- Q5 (a): a Cancel PO line can name an SPO (crew: 5 of 534 Sorento XPDTL lines, FromDocType always 'PO');
  SPO sources are handled like any PO.
- R6 correction (crew): a line CS cancelled on the board (#1524, `cancelled_at`) is never reopened.

Facts from crew's SORENTO schema read (13 Oct): XS 15,201 rows, XP 77 (all-time). FromDocDtlKey unset on
~23% of XS and ~10% of XP lines (all-time); 1,134 XS and 93 XP keys point at SO/PO lines that no longer
exist. Owner's 2026-only figure: 854 of 5,365 XS lines ambiguous. `Cancelled` T = DocStatus V.

## Rules

R1. Two push entities, `cancel_sales_orders` (XS) and `cancel_purchase_orders` (XP), contract 2.8, the DO /
    GRN vendor shape (PascalCase record with `Details`, envelope `book`, identity (company, book, DocKey),
    LastModified stale guard, `/deletions` with `doc_keys` + DocDate range marks the doc vanished). Stored in
    `autocount_cancel_docs` / `autocount_cancel_lines` (`kind` = cancel_so / cancel_po).
R2. Matching, per cancel line, from stored state only (never arrival order):
    1. `FromDocDtlKey` set and the line exists: matched (`line_key`).
    2. Key set, line missing, its document known: unmatched (`dangling_line_key`); no item fallback.
    3. Named document (SO / PO / SPO, the number's table decides) not in the CRM: waiting (`order_not_found`),
       retried every batch and whenever that order is pushed.
    4. No key: candidates = lines of that document with the same item, narrowed by Location when that leaves
       any. One candidate: matched (`item`). None: unmatched (`item_not_on_order`).
    5. Several (Q2 a), per group (document + item): a candidate's unexplained transfer = TransferedQty - DO
       (or GRN) qty linked to it - cancel qty matched to it by line key. A candidate qualifies for a cancel
       line when its unexplained transfer >= the cancel qty. Exactly one qualifier = tentative; if the
       tentative picks on one candidate exceed its unexplained transfer, none of those picks stand.
       Standing picks: matched (`tie_break`).
    6. Fallback (Q3) for what is left, in a fixed order (cancel DocDate, DocNo, DtlKey): the earliest candidate
       line (lowest Seq; a PO line by DtlKey) whose remaining unexplained transfer can take the whole qty;
       matched (`fallback`), history row "auto-matched: ambiguous cancel <XS no>". None can: unmatched
       (`no_fit`).
R3. `qty_cancelled` = sum of matched lines of live docs (not void, not vanished), capped at ordered (SPO:
    allocated), recomputed for every touched line at the end of every batch, deletion sweep and order push.
R4. Delivered shown = TransferedQty - cancelled. Planned = coalesce(required, ordered) - cancelled.
    Outstanding / incoming unchanged (TransferedQty already counts the cancel).
R5. Fully cancelled (cancelled >= ordered): SO / PO line `cancelled`; SO OI rows retired by the #1530 path
    ("Transferred to Cancel SO <XS no>") and flagged. Partial: a `qty_down` planning change for CS (Q4 a).
R6. Cancel lowers: a line this lane cancelled returns to the status its quantities give; board-cancelled
    lines and cancelled orders stay cancelled. OI rows already retired stay retired.
R7. Every cancel line ends matched, waiting or unmatched with a reason; each batch logs the counts per
    reason (`ingest.cancel_match ...`). No screen, nothing spins.

## Decisions after the card (13 Oct)

- Crew: AC-CANCEL-PULL mirrors the DO / GRN ingest shape for XS / XP and for Consignment /
  ConsignmentReturn. `consignments` / `consignment_returns` are accepted and stored only (same tables,
  `kind`, lines never matched: `unmatched:stored_only`, no quantity moves).
- Permissions: `tests/test_external_permission_coverage.py` requires a distinct slug per entity per door,
  so each entity has `scm.<entity>.{view,edit,delete}`, seeded by `ac_cancel_0001_docs` to admin /
  superadmin and to every role holding `scm.sales_orders.edit` (`scm.purchase_orders.edit` for Cancel PO).
- Review round 1: `cancelled_by_cancel_doc` (SO / PO lines) records which lines this rule cancelled, so
  only those reopen and the SO / PO push keeps them cancelled without retiring OI rows twice.

## Deferred (named triggers, not built)

- SPO received display net of `qty_cancelled`: no SPO screen shows a cancelled SPO line wrongly yet
  measured; build when one does (incoming is already right through TransferedQty).
- The AutoCount pull's DO / GRN apply path does not run the cancel relink (the push route does); the
  next cancel or order sync re-decides. Build when the pull becomes the DO / GRN writer.
- Consignment / Consignment Return live in `autocount_cancel_docs` / `_lines` (`kind`) while they are
  stored only; move them to their own tables in the lane that gives consignment behaviour.
- The DO / GRN `/deletions` sweep does not re-decide key-less cancels (a vanished DO stays counted as
  explaining a transfer until the next order or cancel sync re-decides).
- A visible "Cancelled" column on the SO / PO lines tab: the API returns `qty_cancelled`; the screen
  change needs a mock first.

## Test list (red first)

`tests/test_ingest_autocount_cancel_docs.py`: ingest / unchanged / dry run / book required / bad record;
partial, full, sum + cap, line removed, void and unvoid, deletion sweep, board cancel kept; key match,
dangling, item, location, item not on order, waiting then linked, other company never matched; tie-break,
oversubscribed, fallback lowest Seq + history row, no fit, order independence (both push orders);
XP to PO line (cancelled), XP to SPO line; contract 2.8 + slugs; SO push keeps a fully cancelled line
cancelled and relinks waiting lines; OI retire on full cancel; planning change on partial; readers
(plan_qty_of, demand.plan_qty, board delivered).
