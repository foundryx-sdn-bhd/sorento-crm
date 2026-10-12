# PLAN: release blocker, 30 red backend tests (RELEASE-FIX 12 Oct)

Status: BUILT 2026-10-12, awaiting review + CI label. Small fix track, with one exception: a
data-only migration (`rkind_0003_reference_kind_rows`, no schema change). No auth/RBAC, no
ingest surface change.

## Journey

The owner dispatches a production release of main 4ad65522f. Release run 38256456682 fails: 30
backend tests red in 6 jobs (1 + 2 + 9 + 1 + 3 + 14). Release runs had the backend suites skipped
since about 11 Oct, so these were already red on prod aa1df029c. The release must go out with
every test green, none skipped, xfailed or deleted, and the one real user-facing defect fixed in
code.

## Items (verified, file:line in the PR body)

1. #1569 REF-PROBES, 16 tests.
   - Entity kinds x11: `ENTITY_HINTS` gained five kinds with no `chatbot_entity_kinds` row
     (AC-1502: one row per hint). Data migration + blank-schema fallback + bootstrap seed; the
     prompt-blocks golden gains the five lines. The v56 parser text carries no policy blocks, so
     no published prompt drifts.
   - SO label (real bug, code fix): the hit-path scope header printed "Delivery order: SO12345".
     A generic `order` row now prints "Order:"; a resolver-placed `customer_order` row keeps
     "Delivery order:".
   - Brand scope x2: `crm_purchase_request_by_number` = `filtered` (already the runtime default).
   - Warehouse matrix + staff-check pins: deliberate #1569 changes, restated with their reasons.
2. #1580 OI-CLOSED-LINE, 5 tests: 06dab0171 cherry-picked; three restated to R2 (a shortfall is a
   warning, one bad line never refuses the order). Checked against the #1580 PR body, none
   contradicts it. Held-back coverage kept via an unbalanced line.
3. #1577 CI-COST, 6 tests: secret hygiene follows gitleaks to the `PR gate` step and
   `scripts/ci_gitleaks.sh`. Kill-tested.
4. #1565 RESPOND-MULTI-KEY, 1 test: the code is right (keys under 24 chars ignored); the test key
   was 18 chars.
5. #1573 CODE-VARIANTS, 1 test: own family first in did-you-mean is by design; test restated.
6. r5 attachment miss, 1 test: not order dependent. A digitless `unique_code` stem plus an
   all-letter random suffix (about 1 in 2,600) is no code to `_code_matched`. Stems carry a digit.

## Left for the owner

- The lane carries a migration: merge one migration PR at a time.
- On prod, `rkind_0003` runs with the release.
