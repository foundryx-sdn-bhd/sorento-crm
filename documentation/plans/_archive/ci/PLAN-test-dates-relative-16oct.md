# PLAN: test dates relative to today (TEST-DATES-RELATIVE)

Status: implemented (PR #1724). Small fix track: tests only, no product code.

## Problem

Release run 38825237025 (main e19bc513f) shard 6 is red on
`tests/test_incoming_region_filter.py::test_ac_rpl_13_stock_ask_still_answers_from_a_west_plus_east_container`
(`assert 'no_incoming' == 'incoming'`). The test seeds a shipment with `eta=date(2026, 10, 15)`;
the dealer stock read only takes shipments whose ETA is today or later in Malaysia time
(`app/services/inventory_service.py` `_shipments_from_today`, `not_before=incoming.today_myt()`;
filter in `app/services/incoming_stock_service.py` `earliest_packing_list_shipment`). Today (MYT)
is 16 Oct 2026, so the seeded ETA is in the past and the shipment is dropped.

## Fix

Seed every such date relative to today and build the expected value from the same seeded value,
keeping the relative order of dates inside each test. A seeded "earlier" shipment that the test
means to be filtered by REGION (not by date) also moves to the future, so the test keeps proving
the region rule rather than passing on the date filter.

## Sweep

The 186 test files that seed a literal date between 2026-10-16 and 2026-12-31 ran with the
process clock moved to 2026-12-31 (scratch pytest plugin on `time-machine`, not committed;
Postgres `now()` does not move). 32 failed there; 7 of those also fail on the real clock
(`tests/chatbot/test_s6c_answer_lane.py` replay cases reading fixtures from the local sibling
`sorento_crm_n8n` checkout, outside this repo; not date related).
The other 25:

Fixed (12 tests, all expire on the real calendar before 2027):

| File | Tests | First red day | Today source in the app |
| --- | --- | --- | --- |
| `tests/test_incoming_region_filter.py` | 2 | 16 Oct (red now), 21 Oct | `today_myt()` |
| `tests/chatbot/test_stock_ask_packing_list_attachment.py` | 1 | 20 Oct | `today_myt()` |
| `tests/chatbot/test_product_alternatives_stock.py` | 2 | 21 Oct | `today_myt()` |
| `tests/test_incoming_contact_rules.py` | 4 | 29 Oct | `today_myt()` |
| `tests/test_order_inquiry_place_on_po.py` | 1 | 14 Nov | cascade lead-time window (90 days) |
| `tests/scm/test_stock_debt_routes.py` | 6 | Nov to 1 Dec | month axis starts at `date.today()` |
| `tests/scm/test_reorder_plan_oi_need_in_full.py` | 1 | 18 Dec | fixed "Plan until" vs `_today() + 14` |

Also corrected: the East-only seeds of two AC-RPL-13 region tests (1 Oct) were already past,
so those tests passed on the date filter, not the region rule they claim to prove.

Not fixed (fake-clock artifacts, never red on the real calendar):

- `tests/scm/test_stock_debt_export.py::test_export_route_refuses_in_flight_409`: the row's
  `created_at` is Postgres `now()` (`app/models/download.py`), the stale sweep compares Python's
  clock (`app/services/download_service.py` `fail_stale`).
- `tests/test_oi_handover_resend.py` (9): `automation_runs.started_at` is Postgres `now()`,
  `finished_at` Python's; 76 days of skew overflows the int32 duration column.
- `tests/chatbot/test_escalation_round6_n8n_behaviour.py` (3): company-order flake (no ORDER BY
  in the product lookup; the file documents it), not date related; passes 43/43 alone at
  2026-12-31.

## Proof

Throwaway DB `sorento_tdr_ci` built by `scripts.bootstrap_env` and a private Redis on :6391.

- After: the 7 files, 228 passed on the real clock and at 2026-10-29, 11-14, 12-01, 12-18,
  12-31.
- Kill: the 12 tests from origin/main fail 4 by 29 Oct, 7 by 14 Nov, 11 by 1 Dec, all 12 by
  18 Dec and at 31 Dec.
