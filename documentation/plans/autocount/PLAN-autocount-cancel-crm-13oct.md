# PLAN: AutoCount Cancel SO / Cancel PO in the CRM (AC-CANCEL-CRM)

Status: Merge-ready (owner hand test PASS 14 Oct on the crew copy after the re-decide; reviewer and security reviewer READY). Track: full (migration, RBAC slugs).
Lane: AC-CANCEL-CRM. Sibling: AC-CANCEL-PULL (the shared service that reads the cancel docs; payload agreed via crew).
Follow-up lane CANCEL-STOP-AT-SO (14 Oct, owner ruling, small fix track, no migration): R5 revised below; UAC
`cancel-stop-at-so-acceptance-criteria.md`; owner repair SQL `SQL-cancel-so-retired-oi-rows-14oct.sql` (read
only) and `SQL-cancel-so-restore-oi-rows-14oct.sql`. Status: PR open, in review.

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

Facts from crew's SORENTO schema read (13 Oct): XS 15,201 rows, XP 77 (all-time). 1,134 XS and 93 XP keys
point at SO/PO lines that no longer exist. Owner's 2026-only figure: 854 of 5,365 XS lines ambiguous.
`Cancelled` T = DocStatus V.

XSDTL `FromDocDtlKey` by year (crew, FXView read-only, 13 Oct 21:20; this replaces an earlier "~23% / ~25%
empty, all-time" figure, which was wrong): 2023 7,898 of 7,898 keyed; 2024 5,420 of 6,544; 2025 0 of 7,553;
2026 0 of 5,510. Every line was keyed until Oct 2024, Nov 2024 208 of 658, and from Dec 2024 the key is
ALWAYS NULL (`FromDocNo` is still filled). AutoCount API DO lines carry `FromDocDtlKey` 0 always. FXView is
denied SELECT on DO / DODTL (and GRN) on Sorento and Mocha (error 229); readable: Consignment (+ DTL /
Return), Creditor, Debtor, Item*, Location, PO / PODTL, SO / SODTL, XP / XPDTL, XS / XSDTL. Mocha needs
pymssql `tds_version='7.0'`.

## Owner rule (13 Oct, supersedes Q2 a and Q3)

Cancel attribution must be 100% correct: exact links only. No lowest-Seq fallback, no tie-break, no guess,
no flag-for-CS. No DO / DODTL SQL ever: DO data comes only through hapi `/deliveryorderbydocdate`, whose
lines carry `FromDocDtlKey` 0, so a repeated item's DO line never links to an SO line. Attribution is exact
arithmetic per (SO, item, location) group, built as R2 step 4 below. Status rule (b): "Partly cancelled"
only while the line is open; a done line reads Completed and the Qty cancelled column carries the cancel.

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
    4. No key: decided with its group, one document + item + location (strict, blank equals blank), by
       `autocount_cancel_matching.resolve_group`. T_i = a group line's TransferedQty (SO `qty_delivered`,
       PO `qty_received`, SPO `stated_received`) less the live line-key cancels on it; D = the group's DO
       qty (DO lines linked to a group line, or naming the SO, item and location as sent; GRN qty on the
       group's lines for a PO / SPO); C = the group's key-less cancel qty.
       - No line: unmatched (`item_not_on_order`).
       - One line: matched to it (`item`).
       - C = 0: matched, nothing cancelled (`group_none`).
       - D = 0 and C = sum(T_i): matched (`group_whole`); the cancel lines name the group (no line id,
         `candidate_line_ids` = its lines), and each line's cancelled qty is its own TransferedQty.
       - Anything else: unmatched (`undetermined`), kept at document + item level, named on no line,
         never counted as cancelled, counted in the sync log. Owner (b), 13 Oct: nothing on screen,
         no extra list and no line change; it lives only in the sync log and the stored documents.
       Every decision re-decides the groups its lines are in now AND the groups they were last decided
       in (through the target or `candidate_line_ids`); a removed line queues its group's other lines.
       **Amended 14 Oct (owner, DO-HISTORY Q2 (a)):** a Cancel SO group is one SO + item, whatever
       the location: its lines are every line of the item on the SO, its cancels every key-less
       cancel line naming the SO and item, and D every live DO line linked to a group line or
       naming the SO and item, at any location. A cancel or DO line's location can differ from
       its SO line's (dev: 570 of 9,828 single-match DO lines), so it proves nothing. Cancel PO
       and SPO groups keep the location. Built on the DO-HISTORY follow-up PR (branch
       `claude/do-history-group-links`). After its deploy, lines stored under the per-location
       rule are re-decided with `scripts/autocount_cancel_redecide.py --company SRT` (dry run,
       then `--apply` on the owner's go), then the same for MCH; until then a group is re-keyed
       only when one of its documents is pushed again.
       Locations are compared in Python only (`_loc`), never against a SQL upper / trim.
       The SO / PO Lines tooltip lists a `group_whole` document once per line with no qty.
R3. `qty_cancelled` = sum of matched lines of live docs (not void, not vanished), or the line's own
    TransferedQty when a live `group_whole` cancel holds its group; capped at ordered (SPO: allocated),
    recomputed for every touched line (a group's lines included) at the end of every batch, deletion sweep
    and order push.
R4. Delivered shown = TransferedQty - cancelled. Planned = coalesce(required, ordered) - cancelled.
    Outstanding / incoming unchanged (TransferedQty already counts the cancel).
R5. Fully cancelled (cancelled >= ordered): SO / PO line `cancelled`. **Effects stop at the SO** (owner
    ruling 14 Oct, lane CANCEL-STOP-AT-SO, reversing the original R5): no OI row is retired, no
    handover is queued, no purchasing email goes out. Open OI rows / pins on a cancelled line stay as they
    are and CS handles them in Fulfilment Planning, where such a line stays on the board read-only (pill
    Cancelled, "by AutoCount", the XS numbers and the open OI row count) while a live OI row remains.
    Superseded original text: "SO OI rows retired by the
    #1530 path ("Transferred to Cancel SO <XS no>") and flagged". Partial: a `qty_down` planning change for
    CS (Q4 a).
R6. Cancel lowers: a line this lane cancelled returns to the status its quantities give; board-cancelled
    lines and cancelled orders stay cancelled. OI rows already retired stay retired (since R5's revision a
    Cancel SO retires none).
R7. Every cancel line ends matched, waiting or unmatched with a reason; each batch logs the counts per
    reason (`ingest.cancel_match ...`). No screen, nothing spins.

## Decisions after the card (13 Oct)

- Crew: AC-CANCEL-PULL mirrors the DO / GRN ingest shape for XS / XP and for Consignment /
  ConsignmentReturn. `consignments` / `consignment_returns` are accepted and stored only (same tables,
  `kind`, lines never matched: `unmatched:stored_only`, no quantity moves).
- Permissions: `tests/test_external_permission_coverage.py` requires a distinct slug per entity per door,
  so each entity has `scm.<entity>.{view,edit,delete}`, seeded by `ac_cancel_0001_docs` to admin /
  superadmin and to every role holding `scm.sales_orders.edit` (`scm.purchase_orders.edit` for Cancel PO).
- Crew audit (13 Oct): a database where the old matcher ran keeps its guesses (re-pushing the same
  document is `unchanged`). `scripts/autocount_cancel_redecide.py --company <CODE> [--apply]`
  re-decides every stored Cancel SO / PO line with the ingest's own code (dry run by default,
  idempotent, runs the ingest hooks on apply). Production never ran the old matcher, so there it
  moves nothing; crew runs it on the copy. A version stamp was not added: one copy, one run.
- Review round 1: `cancelled_by_cancel_doc` (SO / PO lines) records which lines this rule cancelled, so
  only those reopen and the SO / PO push keeps them cancelled without retiring OI rows twice.

## 2026 Sorento XS match counts under the old rules (crew, 13 Oct, report script at 117d2aed, since removed)

SORENTO AutoCount extract of 13 Oct, counts only. 5,423 XS lines dated 2026 in scope (64 more on
cancelled XS docs excluded).

| Outcome | Lines | Share |
| --- | ---: | ---: |
| matched: line key | 0 | 0% |
| matched: item (one candidate after location) | 3,348 | 61.7% |
| matched: tie-break (Q2 a) | 533 | 9.8% |
| matched: fallback (Q3, history row) | 1,540 | 28.4% |
| unmatched: no fit | 2 | 0.04% |
| unmatched: dangling line key / item not on order / waiting | 0 | 0% |

Per month (item / tie-break / fallback / no fit): Jan 342/51/164/0, Feb 314/40/219/0, Mar 370/27/139/0,
Apr 318/96/435/1, May 302/74/93/0, Jun 322/44/82/0, Jul 525/92/132/0, Aug 427/54/159/1,
Sep 334/33/76/0, Oct (to 13th) 94/22/41/0.

Caveats:
1. No DO quantity was subtracted. The AutoCount SQL login has no SELECT on DO / DODTL, and the dev
   DB's DO lines (Sep 2026 only) all carry `from_dtl_key` 0, so none link to an SO line. Unexplained
   transfer was therefore TransferedQty minus line-key cancels only, so the tie-break and fallback
   figures are optimistic: with real DO qty some tie-break lines would move to fallback and some
   fallback lines to no fit. In the CRM the same is true until DO lines link to SO lines.
2. No 2026 XS line carries `FromDocDtlKey` (0 of 5,423), while 46,828 XS lines from earlier years do.
   Line-key matching will not help 2026 at all.

## 2026 Sorento XS buckets under the exact rule (crew, 13 Oct, `scripts/autocount_cancel_bucket_report.py`
at d52cbfb1, `--do-from 2026-08-20`)

5,446 XS lines in 4,230 (SO, item, location) groups. The clone's DO data covers 2026-08-20 to 2026-10-14.

| Bucket | Lines | Groups |
| --- | ---: | ---: |
| exact: single line | 3,361 | 3,286 |
| exact: D = 0 and C = sum(T) | 23 | 11 |
| undetermined (D > 0) | 43 | 21 |
| D = 0, C != sum(T) | 1 | 1 |
| no SO line at that item and location | 56 | 18 |
| SO older than the DO data (D unknown) | 1,962 | 893 |

Exact today: 3,384 lines (62.1%), 3,297 groups (77.9%). The 893 "D unknown" groups are still decided
safely: TransferedQty counts every DO, so C = sum(T) alone proves nothing was delivered, and the matcher
only calls such a group whole on that exact equality; every other one is undetermined.

## Deferred (named triggers, not built)

- SPO received display net of `qty_cancelled`: no SPO screen shows a cancelled SPO line wrongly yet
  measured; build when one does (incoming is already right through TransferedQty).
- The AutoCount pull's DO / GRN apply path does not run the cancel relink (the push route does); the
  next cancel or order sync re-decides. Build when the pull becomes the DO / GRN writer.
- Consignment / Consignment Return live in `autocount_cancel_docs` / `_lines` (`kind`) while they are
  stored only; move them to their own tables in the lane that gives consignment behaviour.
- The DO / GRN `/deletions` sweep does not re-decide key-less cancels (a vanished DO stays counted as
  explaining a transfer until the next order or cancel sync re-decides).
- A GIN index on `autocount_cancel_lines.candidate_line_ids` (the `?|` lookups in the SO / PO detail
  read and the recompute scan the company's cancel lines): build when that table passes about 100k rows
  or an SO detail read shows it in a slow-query log.

## Test list (red first)

`tests/test_ingest_autocount_cancel_docs.py`: ingest / unchanged / dry run / book required / bad record;
partial, full, sum + cap, line removed, void and unvoid, deletion sweep, board cancel kept; key match,
dangling, item, group by location, item not on order, waiting then linked, other company never matched;
`tests/test_autocount_cancel_matching.py` (pure): one line, no line, C = 0, D = 0 and C = sum(T) per line,
D > 0 undetermined, C != sum(T) undetermined, the old tie-break and fallback cases undetermined, no line
ever named in a 2+ line group, order independence over permutations; DB: whole group per line + tooltip,
whole group lowered reopens, a DO landing turns whole into undetermined, XP over two PO lines, no
auto-match history, order independence (both push orders);
XP to PO line (cancelled), XP to SPO line; contract 2.8 + slugs; SO push keeps a fully cancelled line
cancelled and relinks waiting lines; OI retire on full cancel; planning change on partial; readers
(plan_qty_of, demand.plan_qty, board delivered).
