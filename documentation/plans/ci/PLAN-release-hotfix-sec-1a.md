# PLAN: release blocker test_sec_1a (RELEASE-HOTFIX-1001)

Status: REVIEWED 2026-10-01 (reviewer READY, awaiting CI + owner merge), small fix track (test-only diff, no migration, no auth/RBAC, no ingest surface change).

## Journey

The owner dispatches a production release of main 60c57c7a3 (#1409 #1411 #1420 #1421). Release run
36839883352 fails in "Backend test suite (Postgres, xdist) (3)" on
`tests/test_ingest_parity_security_fixes.py::TestSec1AdoptionPathReceivedGuard::test_sec_1a_first_push_supersede_carries_the_received_quantity_forward`.
PR CI skips the full suite, so #1411 merged green. The release must go out without weakening the
guarantee that a supersede never loses a received quantity.

## Root cause

#1411 (SPO-XLSX-SUPERSEDE) added rule D33 / D33a
(`documentation/plans/autocount/PLAN-spo-xlsx-product-fallback.md:37,40`): a superseded xlsx row
that cannot be deleted is RETIRED. After `assert_supersede_conserved` proves the carry, the row's
receipt is frozen into `stated_received` and `quantity_received` is zeroed
(`sorento_crm_backend/app/services/shipping_order_ingest_service.py:1218-1281`). Leaving it would
double count the receipt (PL "SPO allocated", R2 in that plan).

test_sec_1a asserts every row on the spo_number keeps received >= 5, including the retired row. The
live AutoCount line carries 5 (asserted and passing); only the retired row reads 0. #1411 updated the
sibling `tests/test_spo_xlsx_supersede.py:1323-1330` for the same zeroing and missed this file.

Verdict: the test's expectation is stale against a deliberate, ruled rule. The receipt is not
lowered: it moves to the live line and stays recorded on the retired row's `stated_received`.

## Fix

Test-only. In test_sec_1a:
- the per-row receipt floor applies to LIVE rows (`retired_at IS NULL`);
- the live rows hold exactly 5 (conserved, not doubled);
- the retired row is closed, ref-less, received 0, `stated_received >= 5`.

## Grill (to crew, 2026-10-01)

- G1: test-only fix (a) vs revert D33 zeroing (b). Recommendation (a). Answer (crew, 1 Oct): (a) approved - live-row floor, live total == 5, retired row closed, qty 0, stated_received >= 5; also grep every test for the same stale per-row floor and fix in this PR (none found: test_spo_xlsx_supersede.py:1824 reads live ref lines only).
- G2: browser check on a test-only lane exercises nothing changed; propose skip, stated in PR. Answer (crew, 1 Oct): skip the browser check, say so in the PR.

## PR #1424

#1424 changes `rules/shipping_order_rules.py` (planner) and the dedupe scripts, not this test or the
ingest service. On its head 6fbc345ac the old test also fails (1 failed / 4 passed) and the fixed
test passes (5 passed), so #1424 needs only a merge of main once this lands; no change to #1424 itself.
