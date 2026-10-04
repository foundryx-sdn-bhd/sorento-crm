# UAC - far-dated landed pins become lendable to nearer sales orders (STOCK-DEBT-LENDABLE)

Plan: `PLAN-stock-debt-lendable.md`. Status: in progress, 30 Sep 2026.

## Journey

**Actor:** purchasing or CS planner on Supply Chain > Project Demand > Stock Debt, opening the
drawer for one product.

**Where they arrive from:** SRTSS8710, book Project. Sep 26 reads -76, Oct -29, Dec -207 while
SO381065 (due 29/03/2027) holds 88 on hand at BRW-BB because its own SPO was received.

**Steps and the single decision each:**

1. Planner opens the Sep 26 cell. SO396071 reads covered, Covered by "On hand BRW-BB (from
   SO381065)". The cell reads -44.
2. Planner opens the Mar 27 cell. SO381065 reads "order back 88", Covered by "Lent to
   SO396071 (32) · SO402118 (29) · SO404890 (27)". The cell reads -88, amber.
3. Planner who wants the lend made real opens SO396071 on the fulfilment board: the far line
   is offered as a Borrow donor there and that Confirm raises the order-back. This page
   writes nothing (owner: "this is a dashboard view only").

**What they hold at the end:** near months that state what can actually ship, a far order
whose re-buy is visible, and no stock moved by a filter.

## Acceptance criteria

- **AC-1 `[T]` A lendable pin lends to nearer lines first.** Given 88 on hand at BRW-BB landed
  for a line due on or after `today + lead + 14`, and lines of the same group short 32 (Sep),
  29 (Oct), 207 (Dec), when the view walks, then Sep and Oct read `covered`, Dec `short 180`,
  the far line `order_back` with `lent_qty 88`, `lent` naming the three receivers with 32 / 29
  / 27, and the months read Sep 0, Oct 0, Dec -180, Mar -88.
- **AC-2 `[T]` A partly lent pin stays pinned for the rest.** Only Sep short 32: the far line
  reads `order_back`, `lent_qty 32`, one pinned landed take of 56, uncovered 32.
- **AC-3 `[T]` A non-lendable pin is unchanged.** A landed pin whose line is due inside the
  window, undated, TBA or unlocated behaves exactly as on main (existing tests green).
- **AC-4 `[T]` Free stock first.** 100 on hand, 88 landed: a nearer line of 32 takes 12 free
  then 20 lent (two Covered by entries); a nearer line of 10 lends nothing and the far line
  reads plain `pinned`.
- **AC-5 `[T]` Short outranks.** A far line open 100 with 88 landed and all 88 lent reads
  `short` with `lent_qty 88`.
- **AC-6 `[T]` Chronology holds.** A line due AFTER the far line gets none of its landed goods.
- **AC-7 `[T]` Two claims on one floor.** The earlier-due far line takes its claim first; the
  later one bears the lend; claims are capped by the floor in hold order.
- **AC-8 `[T]` A lend never crosses an ownership group.**
- **AC-R1 `[T]` The window is the board's.** `required_date >= reserve_window_end(as_of,
  lead)` and `< tba_from`, off the product's stated lead (30 -> due in 60 days lends; default
  90 -> it does not); the day on the window itself lends.
- **AC-R2 `[T]` View independence.** The nearer month's balance and the nearer line's status,
  assigned and short quantities are the same with and without `date_to`; `date_from` never
  frees a pin that cannot wait.
- **AC-R3 `[T]` Board parity.** `assignments_for` (board, ladder) reads the far line `pinned`
  and the nearer line `short`; nothing is lent there.
- **AC-R4 `[T]` The wire.** Demand line carries `status: "order_back"`, `lent_qty`; an on-hand
  `assigned_from` entry carries `lent_from_so_number` and the ref "On hand <bin> (from
  SOxxxx)"; a `lent` entry carries `so_number`, `sales_order_id`, `qty`, ref "Lent to SOxxxx
  (N)". Supply tab unchanged.
- **AC-V1 `[V]` Pill.** Status `order_back` renders "order back N" in the violet pill, N being
  `min(lent_qty, short_qty)` (what is still owed for the lend); `short` with a lend renders
  "short N · order back M" with the two halves adding up to the line's shortfall ("short 12 ·
  order back 88" for a line short 100 that lent 88).
- **AC-9 `[T]` Free stock at any bin of the group is drawn before a lend** (reviewer round):
  the lend is the same whatever the bins are called.
- **AC-10 `[T]` The lend does not depend on the order the holds arrive in**; two lenders are
  charged in walk order (the later-due one first).
- **AC-11 `[T]` A lendable hold on a pin-only event pins as any other.**
- **AC-R6 `[T]` The real book's shape lends** (owner hand test 1): a far line whose landed
  goods reach the assignment through the auto placement on its received SPO reads
  `order_back`, the nearer line covered off it; inside the window it stays `pinned`; a
  confirmed Reserve on a far line is never lent.
- **AC-V2 `[V]` Covered by.** Lent entries and the "(from SO...)" on-hand wording render as
  muted text, the lent entry linking the receiving order.
- **AC-V3 `[B]` 1280 and 375** per the mockup (sections 2, 3 and 6; the Rebalance sections are
  superseded by the owner's ruling).

`[T]` pytest, `[V]` vitest, `[B]` browser evidence.
