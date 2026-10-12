# PLAN: Fulfilment Planning board, admin "Simulate confirm" (dry run)

Status: Plan (behaviour card posted as crew-ask, awaiting owner answers). Track: standard (Size M,
LEAD pattern; touches an admin gate, so security-reviewer runs).
Lane: SIMULATE-CONFIRM, branch `crew/simulate-confirm`, PR #1585.
UAC: `board-simulate-confirm-12oct-acceptance-criteria.md` (written after the card is answered).

## Owner ask (12 Oct 2026)

On the Fulfilment Planning board, add a "Simulate confirm" action under the gear button, admins
only. It runs the full Confirm for the current selection (incl. the planning-change batch /
revision path) as a dry run: one DB transaction, always rolled back; no emails, no notifications,
no AutoCount writes, no outbox rows. It shows per order and per line whether it would confirm, or
the exact error and the line it hits, plus what would be written (holds, borrows, order-backs /
stock-short warnings). Purpose: before telling a user "this will work", the admin sees any error
first. Prod case: SO422492 failed on a date JSON error (fixed in #1584).

## What already exists (measured, not designed)

- **The dry run itself ships already.** `POST /project-sales/fulfilment-planning/confirm-all`
  with `preview: true` (`ConfirmManyBody.preview`, `app/schemas/project_supply.py:603`) runs every
  order's real write and rolls it back (`ProjectSupplyService.confirm_many`,
  `app/services/project_supply_service.py:10981`, rollback at `:11087`), batch path included
  (`_confirm_a_planning_change(..., preview=...)` -> `planning_change_service.apply(notify=not
  preview)`, `app/api/v1/projects/fulfilment_planning.py:1183-1191`). The board already calls it
  silently before every real Confirm (`FulfilmentBoardPanel.tsx:1580`, FULFIL-CONFIRM-SCOPE v5).
- It already answers, per ok order, the order inquiry rows and stock transfers the press would
  raise (`_preview_of`, `project_supply_service.py:11167`; `PreviewInquiryRow` /
  `PreviewTransfer`, `project_supply.py:606-624`), but no screen renders them.
- Gap 1: a non-`AppException` error (SO422492's date JSON error) comes back as one sentence for
  the whole order with `failing_lines=None` (`_error_detail`, `project_supply_service.py:586`;
  batch path `_exc_message`, `planning_change_service.py:4752`). Nothing says which line.
- Gap 2: the board's Confirm adopts never-planned orders BEFORE the dry run
  (`buildConfirmOrders`, `FulfilmentBoardPanel.tsx:1308-1321`), a committed write.

So this lane is: an admin-gated route that wraps the existing preview, pins a failing order's
error to its line, adds a commit/enqueue tripwire, and a gear entry that renders the answer in the
existing results block. Not a second simulator.

## Behaviour card

### 1. Who counts as admin

Role slug `superadmin` or `admin` via `UserPermissionService.get_user_role_slugs`, the check this
same domain already uses for the admin-only undo reconstruction
(`app/services/project_supply_undo_service.py:1327-1329`; also `app/api/v1/system/pending_actions.py:258`,
`app/services/record_actions.py:2142`). There is no permission slug for "admin" (the FE helper says
so: `sorento_crm_frontend/lib/is-superadmin.ts:3-15`). Route gate: `require_permission(EDIT)`
(`projects.projects.edit`, like Confirm) AND the role-slug check, 403 otherwise. Per-project
`_assert_can_act_on` still runs per order, so an order the admin could not confirm reports that
403 as its own result. FE: the gear entry shows when `isSuperadminUser(session.user)`
(`lib/is-superadmin.ts:19`); best-effort UX gate, the server is the enforcement.

### 2. How the dry run guarantees no side effects

Every side effect in `confirm_supply` / `_confirm_a_planning_change` / `planning_change_service.apply`
/ `ProjectSupplyService.confirm`, and how Simulate suppresses it:

| # | Side effect | Where | Suppressed by |
|---|---|---|---|
| 1 | All DB writes (revision, decision lines, inquiry rows, transfers, cancels, draft deletes, batch row states, `applied_at`, undo journal, audit rows, embedding queue rows) | flush, same transaction | `confirm_many(preview=True)` rolls back every order (`project_supply_service.py:11087`, `:11127`); audit (`audit_service.py:968-973`), undo journal (`project_supply_undo_service.py:268-285`) and embedding queue (`embedding_change_listener.py:147`) write on the same connection, so they roll back with it |
| 2 | Purchasing notification + web push enqueue for a planning change (`NotificationService.create_with_channel_preferences` commits, `notification_service.py:355`, enqueues `:358-364`) | `planning_change_service.py:4782` | `notify=not preview` (`fulfilment_planning.py:1190`) |
| 3 | `order_inquiry_changed_with_links` automation (outbox rows, on a fresh `SessionLocal`) | `after_commit` drain, `project_order_inquiry_service.py:11115` | `if session.info.get("confirm_preview"): return` (`:11119`), root rollback drops the queue |
| 4 | In-app purchasing notification for a raised inquiry (fresh session, commits) | `after_commit` drain, `:11155` | same guard (`:11164`) |
| 5 | `order_inquiry_handover` / `order_inquiry_undone` emails | `after_transaction_end`, `:11239`, `:11365` | fire only when the ROOT transaction committed (`:11276-11298`); a rollback discards the queue |
| 6 | AutoCount writes, n8n, HTTP, file storage | none in the graph | nothing to suppress (audit found no `requests`/`httpx`/storage call) |
| 7 | Number allocation (revision no, `TR-` transfer no, inquiry no) | MAX+1 reads (`stock_transfer.py:153-183`, `project_supply_service.py:6935`) | no `nextval`, so a rollback leaves no gap |
| 8 | Logging | `planning_change_service.py:4753` etc. | not suppressed, harmless |

New in this lane, defence in depth so a FUTURE side effect cannot slip past:
- **Commit tripwire**: while `session.info["confirm_simulate"]` is set, a `before_commit` listener
  raises (`SimulationWouldCommit`). Any inner root commit (a future notify call, an unguarded
  drain) becomes that order's reported error instead of a write.
- **Enqueue tripwire**: a contextvar set for the request makes `queue_service.enqueue_job` refuse
  while simulating, so no RQ job (email, push, WhatsApp) can leave.
- **Never adopts**: an order with no planning record is reported "not being planned yet; Confirm
  would adopt it first" and not simulated (the real Confirm's adopt is a committed write).
- A pytest proves nothing persists: row counts of every table the confirm writes, plus the
  draft, batch row state and `applied_at`, are equal before and after, and `enqueue_job` and the
  notification service were never called.

### 3. Per-line errors (the SO422492 case)

When an order fails and the failure names no line (`failing_lines` empty), Simulate re-runs that
order once per posted line, each alone (`only_line_ids=[line]`, preview, rolled back), and reports
every line that fails alone with its exact error text (exception class + message, admin-only, so
not the generic "internal error" sentence). A recheck refusal that already names lines
(`SupplyLinesRefused`) is shown as is. Bounded: at most the order's posted lines, only for failed
orders.

### 4. Result layout (reuses existing components, no new dialog, so no Lavish mock)

Renders in the board's existing results block (`data-testid="board-confirm-results"`,
`FulfilmentBoardPanel.tsx:2375-2660`), the block Confirm already writes into, with:
- heading "Simulation, nothing was saved: X of Y orders would confirm";
- per order, the existing line: "SO… would confirm as revision N", the existing Confirmed /
  Cancel SO / held-back / landed-buy notices (`confirmNoticeLines`), or the refusal with
  `failing_lines` named per line (existing rendering);
- NEW sub-list per ok order, "Would write": inquiry rows (`line 10 ITEM: ORDER 50 at BRW-IB,
  new` / `settles existing`) and transfers (`line 10: borrow 100 BRW-BB -> BRW-IB`, holds/pool/own
  group by `kind`), from the preview fields the server already returns;
- for a probed order, each failing line: "line 8 SRTSH1040: TypeError: Object of type date is not
  JSON serializable".
Gear entry: "Simulate confirm", under "Undo all" in the existing gear menu
(`FulfilmentBoardPanel.tsx:2174-2285`). Same population as Confirm (`buildConfirmOrders(new Set())`
minus the adopt step). Usable at 375px and 1280px like the block today.

### 5. Real examples from dev data

Requested from crew (crew-data-request on PR #1585): SO422492 (batch path, the date JSON error),
one order whose saved drafts raise a borrow transfer, one whose revision raised an order-back
inquiry row. Filled in here when the rows arrive; they become the hand-test fixtures.

### 6. Questions (with recommendations)

Q1. Admin = role slug `superadmin`/`admin` (existing precedent), not a new permission slug.
    Recommend: role slug, because it is the existing check and needs no migration or seed.
Q2. Error text for an admin shows the raw exception (class + message, capped at 1,000 chars),
    which can include SQL fragments. Recommend: show it raw, admin-only, because "the exact error"
    is the point and the real Confirm still shows the generic sentence.
Q3. Never-planned orders are reported and skipped, not adopted-then-simulated. Recommend: skip,
    because adopt is a committed write and simulating it means a second, rarely used adopt path.
Q4. Per-line probing for an order that fails with no named line (one rolled-back re-run per line).
    Recommend: yes, capped at 60 lines per order, because that is the only way to name the line for
    an error like SO422492's.
Q5. Simulate runs as the admin (their own per-project rights), not impersonating the planner.
    Recommend: as the admin, because impersonation is a new auth surface for no stated need.

## Slices (after answers)

1. Tester: red pytest (admin gate 403/200, nothing persists incl. tripwires, per-line probe on an
   injected non-AppException, batch path) + red vitest (gear entry admin-only, results block
   simulated wording and Would write list).
2. Coder: route `POST /fulfilment-planning/simulate-confirm` (body = `ConfirmManyBody`), tripwires,
   probe; FE service + hook + gear entry + results block mode.
3. Reviewer + security-reviewer + cloud browser pass; hand test script.
4. Rebase after PR #1580 (touches `confirm_supply`) merges. #1580 adds `stock_short_notices` to
   the Confirm results (undelivered shortfall warnings, never a block); once on main, the
   simulation result block renders them beside held-back lines (they are the owner's
   "stock-short warnings"). Until then the block shows `lines_held_back` and
   `landed_buy_notices`, which main already returns.
