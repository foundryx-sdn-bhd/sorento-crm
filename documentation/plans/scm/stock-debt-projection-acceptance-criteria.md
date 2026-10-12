# UAC: Stock Debt projected balance (STOCK-DEBT-PROJECTION)

Plan: `PLAN-stock-debt-projection.md`. Owner answers 12 Oct 2026.

- **AC-1** The Filters popover on Stock Debt has a "Projected balance" switch, off by default.
  With it off, the board, the drill and the export are what they were before this lane.
- **AC-2** With it on, every month cell shows the projected balance
  `projected(M) = project(M) + carried_in(M) + incoming(M) - dealer_left(M)`: the project
  bins' own month figure, plus pool stock carried from earlier months, plus INCOMING pool
  supply dated in M (open POs on Delivery date, open not-landed SPOs on ETA, every active pool:
  segment not `project`, not flagged for planning), less the open orders at pool locations due
  in M that pool on hand does not meet.
- **AC-3** "Pool a": in each month pool supply covers that month's dealer / retail orders
  first, then project shortfalls. Pool ON HAND meets dealer orders only and never a project
  shortfall. Only a surplus carries; project spare and debt never carry.
- **AC-4** No pool quantity is counted twice: Book=All and Book=Project give the same
  projection, and supply a project line's hold took is not offered again.
- **AC-5** Nothing is allocated or written (R45): line statuses and "Covered by" are
  unchanged.
- **AC-6** "Only products in debt" and the row sort read the projected months while the
  switch is on. The row total is the sum over the axis of project figure plus incoming pool
  supply less pool orders not met by pool on hand, plus TBA.
- **AC-7** A chip "Projected balance" shows while the switch is on and clears it; the
  export follows the switch.
- **AC-8** A month drill opened while the switch is on states the month plainly, e.g.
  "Project bins -377 · Pool supply 15 (BRW PO 15) applied to this month · Projected
  -362" (carried in, dealer orders and carried forward when not zero), and lists the
  out-of-span pool documents dated in that month in Supply.
- **AC-9** With `date_from` set, demand before it still takes its share of pool stock before
  the stock carries into the first visible month.
- **AC-10** Usable at 375px and 1280px.
