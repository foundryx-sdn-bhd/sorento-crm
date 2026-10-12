# UAC: Stock Debt projected balance (STOCK-DEBT-PROJECTION)

Plan: `PLAN-stock-debt-projection.md`. Owner answers 12 Oct 2026.

- **AC-1** The Filters popover on Stock Debt has a "Projected balance" switch, off by default.
  With it off, the board, the drill and the export are what they were before this lane.
- **AC-2** With it on, every month cell shows the projected balance:
  `projected(M) = month(M) + added(M) + carried_in(M)`, where `month(M)` is the month's own
  figure, `added(M)` is the month figure of the active site pools outside the page's span
  (segment not `project`, not flagged for planning), and `carried_in(M)` is pool spare
  carried from earlier months, capped at the pool stock that could have held it.
- **AC-3** Only pool stock carries. Spare at a project bin stays in its own month, and a
  month that ends short starts the next one at 0 carried.
- **AC-4** No pool quantity is counted twice: the same pool stock and orders give the same
  projection whether the span holds that pool or not (pool orders netted either way), and a
  hold the page's walk placed on out-of-span supply is taken off before it carries.
- **AC-5** Nothing is allocated or written (R45): line statuses and "Covered by" are
  unchanged.
- **AC-6** "Only products in debt" and the row sort read the projected months while the
  switch is on. The row total is today's total plus the out-of-span pools' month figures on
  the axis.
- **AC-7** A chip "Projected balance" shows while the switch is on and clears it; the
  export follows the switch.
- **AC-8** A month drill opened while the switch is on states
  "Carried in x · This month y · Pool added z · Projected w" and lists the out-of-span pool
  documents dated in that month in Supply.
- **AC-9** With `date_from` set, demand before it still takes its share of pool stock before
  the stock carries into the first visible month.
- **AC-10** Usable at 375px and 1280px.
