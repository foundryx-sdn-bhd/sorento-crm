# UAC: Stock Debt projected balance (STOCK-DEBT-PROJECTION)

Plan: `PLAN-stock-debt-projection.md`. Owner answers 12 Oct 2026.

- **AC-1** The Filters popover on Stock Debt has a "Projected balance" switch, off by default.
  With it off, the board, the drill and the export are what they were before this lane.
- **AC-2** With it on, every month cell shows the projected balance
  `projected(M) = project(M) + carried_in(M) + reserve_supply(M)` (owner, 12 Oct 2026, option
  D): the project lines' own month figure, plus a project reserve's supply carried from
  earlier months, plus a project reserve's unlinked INCOMING supply dated in M (open POs on
  Delivery date, open not-landed SPOs on ETA). A project reserve is a pool with `segment =
  project`, not flagged for planning (#1597).
- **AC-3** Pool supply (BRW and every other pool) counts for a project line only when linked
  to it: an OI placement, a confirmed planning decision, or the S/O ref on the PO/SPO line as
  the walk reads it (R40: within its own group). Incoming or landed alike: a placed SPO reads
  the same before and after it is received (SRTWT2215's BRW SPO 333). Unlinked pool on hand
  and unlinked pool POs/SPOs never count. Pool orders are not project lines.
- **AC-4** The projection agrees with Covered by line for line: under the switch a month's
  `month_balance` is what that book's Covered by leaves owed (less the project bins' spare).
  No quantity is counted twice: the reserve term is the same in Book=All and Book=Project,
  and reserve supply any link took (from an order at any pool or project bin) is not offered
  again.
- **AC-5** Nothing is allocated or written (R45): line statuses and "Covered by" are
  unchanged.
- **AC-6** "Only products in debt" and the row sort read the projected months while the
  switch is on. The row total is the sum over the axis of project figure plus reserve
  supply, plus TBA.
- **AC-7** A chip "Projected balance" shows while the switch is on and clears it; the
  export follows the switch.
- **AC-8** A month drill opened while the switch is on states the month plainly, e.g.
  "Project bins -377 · Projected -377", or with a reserve "Project bins -38 · Reserve supply
  5,000 (HQ reserve PO 5,000) applied to this month · Projected +4,962 · Carried forward
  +4,962" (carried in, reserve supply and carried forward when not zero), and lists the
  out-of-span reserve documents dated in that month in Supply.
- **AC-9** With `date_from` set, demand before it still takes its share of reserve stock
  before the stock carries into the first visible month.
- **AC-10** Usable at 375px and 1280px.
