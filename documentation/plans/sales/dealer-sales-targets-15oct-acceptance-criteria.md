# UAC: dealer sales targets on the one target engine (lane SALES-TARGET-DEALER)

Plan: `PLAN-dealer-sales-targets-15oct.md` (card v2 + owner rulings 16 Oct 00:10 and his OK of
00:19: "make sure we can configure all the sales targets in the excel using this
architecture"). Mock: `mockups/dealer-targets-15oct.html`. Import layout:
`dealer-targets-import-layout.md`.

Each criterion names the test that proves it (BE = pytest, FE = vitest).

## D1. Engine (`commission_service.payout_for`, pure)

- D1-1 E1 shared threshold, rate on A/C I only: 5,400.00. BE `test_payout_e1_shared_threshold`.
- D1-2 E2 two additional lines stack, each rounded: 11,405.28 (sen), 11,406 (ringgit).
  BE `test_payout_e2_additional_stack_*`.
- D1-3 E3 rate per account: 6,900.00. BE `test_payout_e3_rate_per_account`.
- D1-4 E4 below the first threshold pays 0; another target pays on its own. BE `test_payout_e4_*`.
- D1-5 E5 points thresholds x ratio on the period's last day: 5,200.00 then 0.
  BE `test_payout_e5_points_ratio`.
- D1-6 E6 payout account is not a counted account; amount unchanged. BE `test_payout_e6_pay_to`.
- D1-7 E7 agent target, fixed RM tiers, highest reached: 9,180.00. BE `test_payout_e7_agent`.
- D1-8 Today's `%`-of-target tiers (marginal and retroactive, bonuses) give exactly today's
  figures. BE: the existing `commission_for` tests, unchanged and green.
- D1-9 Points threshold with no ratio: no tier reachable, `ratio_missing` true, 0 paid.
  BE `test_payout_points_without_ratio`.

## D2. Model and API

- D2-1 A target can be for a dealer: `subject_kind = dealer` with 1+ counted accounts
  (`customer_ids`) and an optional `payout_customer_id` (defaults to the first account).
  BE `test_create_dealer_target`.
- D2-2 Threshold unit `pct` / `amount` / `points`, rounding `sen` / `ringgit`, tiers per period on
  for any subject; an agent target accepts all of them. BE `test_agent_target_fixed_tiers_per_period`.
- D2-3 Tier rates per account only name counted accounts; account rates and per-period tiers
  validated (unknown account, period start not a period, duplicate threshold) with a 422 in
  plain words. BE `test_tier_validation_*`.
- D2-4 Dealer achievement counts orders of the counted accounts only, any agent, cancelled
  excluded; the payout account's own orders count only if it is also counted.
  BE `test_dealer_achievement_*`.
- D2-5 Detail returns accounts with code, name, label and per-account achievement, the payout
  account, per-period commission lines (main, each additional), the ratio used.
  BE `test_dealer_detail_shape`.
- D2-6 `GET /sales/targets?subject=dealer&all=true` lists dealer targets with accounts, pays to
  and the current commission. BE `test_list_dealer_targets`.
- D2-7 Point ratios: dated history per company; `GET/POST /sales/targets/point-ratios`; one row
  per date. BE `test_point_ratios_*`.
- D2-8 Existing agent/team targets unchanged after the migration (defaults `pct`, `sen`, same
  tiers every period). BE `test_existing_targets_unchanged`.
- D2-9 RBAC: view/add/edit as today; import and ratio writes need `sales.targets.edit`; 403
  otherwise. BE `test_dealer_routes_403`.
- D2-10 Company scope: another company's customer cannot be counted or paid to.
  BE `test_dealer_accounts_company_scoped`.

## D3. Import

- D3-1 `POST /sales/targets/import?dry_run=true` writes nothing and returns one report row per
  sheet row: `mapped` or `not_representable` with a reason, plus the matched customer.
  BE `test_import_dry_run_writes_nothing`, `test_import_report_per_row`.
- D3-2 The every-pattern fixture (merged multi-account thresholds, per-account rates incl.
  Deluxe/Mastile/W-Ply, Q1 + Additional, monthly, Dec'25 to Nov'26, Mocha points 4% quarterly +
  2 and 3 tier yearly, mixed number/text cells) maps every row; applying it creates the targets
  and each computes the expected commission. BE `test_import_every_pattern_*`.
- D3-3 Code not found: `not_representable` "no customer with code X"; name differs: `mapped`
  with a warning. BE `test_import_code_rules`.
- D3-4 Re-import of the same accounts and dates updates the target, never a second one.
  BE `test_import_reimport_updates`.
- D3-5 The real Excel never enters the repo; the fixture is generated in the test.

## D4. Frontend

- D4-1 Targets has a Dealers tab (`?tab=dealer`) with Counts and Pays to columns.
  FE `SalesTargetsView.test.tsx`.
- D4-2 "Target for" offers Dealer; a dealer target shows an Accounts tab; the Commission tab has
  Threshold in, Round to, Tiers per period, Additional rows and per-account rates, for every
  subject. FE `TargetRecord.test.tsx`.
- D4-3 Import dialog: Test shows the per-row report; Import enabled only after a Test.
  FE `DealerTargetImportDialog.test.tsx`.
- D4-4 Point ratio dialog lists the dated history and adds a row. FE `PointRatioDialog.test.tsx`.
- D4-5 Usable at 375 and 1280; no UUIDs shown. Browser pass.
