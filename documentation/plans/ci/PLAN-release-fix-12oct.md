# PLAN: release blocker, 30 red backend tests (RELEASE-FIX 12 Oct)

Status: IN PROGRESS 2026-10-12, small fix track (test restatements + one label bug, no migration,
no auth/RBAC, no ingest surface change).

## Journey

The owner dispatches a production release of main 4ad65522f. Release run 38256456682 fails: 30
backend tests red. Release runs had the backend suites skipped since about 11 Oct, so these were
already red on prod aa1df029c. The release must go out with every test green, none skipped,
xfailed or deleted, and the one real user-facing defect fixed in code.

## Items

1. #1569 REF-PROBES: entity-kind seed rows for the five new kinds, the
   `crm_purchase_request_by_number` matrix treatment, the staff-check offer sites, and the
   real bug: an SO number scoped ask prints "Delivery order: SO12345" (code fix).
2. #1580 OI-CLOSED-LINE: cherry-pick 06dab0171 (PR #1594) plus restate three tests to the owner
   rule (one bad line never refuses the order, a shortfall is a warning).
3. #1577 CI-COST: test_secret_hygiene follows gitleaks to the PR gate, still fails if it vanishes.
4. #1565 RESPOND-MULTI-KEY: test_tp2_webhook 503 webhook not configured.
5. test_stock_ask_ht26_r9 did-you-mean order.
6. test_rearch_r5 attachment rich miss, order-dependent leak.

Per-item findings and file:line land in the PR body as they are verified.
