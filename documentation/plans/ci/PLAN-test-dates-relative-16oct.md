# PLAN: test dates relative to today (TEST-DATES-RELATIVE)

Status: in progress (small fix track, tests only, no product code)

## Problem

Release run 38825237025 (main e19bc513f) shard 6 is red on
`tests/test_incoming_region_filter.py::test_ac_rpl_13_stock_ask_still_answers_from_a_west_plus_east_container`
(`assert 'no_incoming' == 'incoming'`). The test seeds a shipment with `eta=date(2026, 10, 15)`;
the dealer stock read only takes shipments whose ETA is today or later in Malaysia time
(`app/services/inventory_service.py` `_shipments_from_today`, `not_before=incoming.today_myt()`;
filter in `app/services/incoming_stock_service.py` `earliest_packing_list_shipment`). Today (MYT)
is 16 Oct 2026, so the seeded ETA is in the past and the shipment is dropped.

Four more tests on the same path expire between 19 and 21 Oct 2026:

- `tests/test_incoming_region_filter.py::test_ac_rpl_13_stock_ask_for_a_west_contact_ignores_an_earlier_east_only_shipment` (20 Oct)
- `tests/chatbot/test_stock_ask_packing_list_attachment.py::test_b3_toggle_off_produces_no_send_attachments_action` (19 Oct)
- `tests/chatbot/test_product_alternatives_stock.py::test_alternative_incoming_carries_its_own_eta` (20 Oct)
- `tests/chatbot/test_product_alternatives_stock.py::test_nested_alternative_never_carries_a_packing_list` (20 Oct)

## Fix

Seed every such ETA as `today_myt() + N days` and build the expected `DD/MM/YYYY` string from the
same value, keeping the relative order of dates inside each test. A seeded "earlier" shipment that
the test means to be filtered by REGION (not by date) also moves to the future, so the test keeps
proving the region rule rather than passing on the date filter.

Sweep: run the test files that seed near-future literal dates (2026-10-16 to 2026-12-31) with the
process clock moved to 31 Dec 2026 (scratch pytest plugin on `time-machine`, not committed), fix
every test that fails only because a literal date went past, and list them in the PR.

## Proof

- Each changed test passes on the real clock and with the clock at 2026-12-31.
- Kill proof: the old literal dates fail with the clock at 2026-12-31.
- Runs on a throwaway DB built by `scripts.bootstrap_env` and a private Redis, never the shared dev DB.
