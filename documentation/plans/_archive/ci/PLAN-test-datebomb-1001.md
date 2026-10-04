# PLAN: date-dependent tests blocking release (TEST-DATEBOMB-1001)

Status: implemented (PR #1412). Small fix track: tests only, no service change.

## Problem

Release run 36798275466 (main 757b41da) failed full backend suites once the date rolled to
2026-10-01. Four tests depended on the wall clock:

1. `tests/scm/test_purchase_trend.py::test_the_endpoint_serves_it_and_rbac_holds`: POs
   hard-coded in Jun/Jul 2026, while the endpoint windows off `date.today()`
   (`purchase_trend_service.py:129`; the route takes no `as_of`).
2. `tests/test_cost_price_apply.py::test_apply_writes_one_cost_row_per_line_with_set_dates_and_source`:
   a "future" `start_date` of 2026-10-01 became today.
3. `tests/scm/test_stock_debt_lendable_routes.py::test_the_cell_value_is_the_same_with_and_without_date_to`:
   `0.0 == 38.0`.
4. `tests/test_oi_monthly_number_and_raises.py::...AC_NO_03`: expected `OI-2609-` from a
   header minted off the clock.

## Stock Debt verdict: test defect, not a service bug

Reproduced with `faketime` on the unchanged test: passes 2026-09-30 and 2026-10-15, fails
2026-10-01, 2026-10-11, 2026-11-01, 2027-01-01, 2027-03-01. The figures are identical on 30 Sep
and 1 Oct; only the column they sit in moves:

```
2026-09-30  whole    [09: 0, 10: 0, ... 03: -50]   narrowed [09: 38, 10: 0]
2026-10-01  whole    [10: 0, ... 03: -50]          narrowed [10: 38]
near line, both days, both views: covered, assigned 50, short 0
```

`date_to` drops demand before the walk (`stock_debt_service.py:1143-1147`), the far line's
landed pin goes with it (`:962`), and on hand is stamped at `as_of` (`:1058`) in the current
month column (`_axis`, `:1537`). So the narrowed page shows the far line's 38 as free in the
current month. The test asserted the near month (`TODAY + 20`), which equals the current month
on the first 8 to 11 days of every month. `date_to` semantics are out of scope
(`PLAN-stock-debt-lendable.md:132`); owner ruling 1 Oct 2026 (a19364058): leave as is, `date_to`
is a demand horizon. The test pins the +38 as the ruled behaviour.

## Fixes

1. Purchase trend: `_world(as_of=...)` dates POs relative to `as_of`; the endpoint test passes
   `date.today()`. The service tests keep `AS_OF = 2026-08-10` (same dates as before).
2. Cost price: `start_date = today + 30`.
3. Stock debt: near line due on the 10th of next month (10 to 40 days out, never this month).
4. OI: expected prefix read from the clock in Malaysia time, before and after the call. The
   sibling burned-slot test now raises its fresh header now, so the guard bites in any month.

## UAC

- The four tests pass on 2026-10-01 and on other days: checked under `faketime` on 2026-10-01,
  10-31, 11-01, 12-15, 2027-01-31, 02-01, 02-28, 03-01.
- No assertion weakened; reviewer kill tests show each still catches its regression.
- Sweep: every test file hard-coding a 2026-10..12 / 2027-01..03 date run under
  `faketime 2026-12-01` (see PR).
