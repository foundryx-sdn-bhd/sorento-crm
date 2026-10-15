# PLAN: dealer sales targets and incentive schemes (lane SALES-TARGET-DEALER)

Status: **card and mock with the owner** (15 Oct). Track: full (new subject, tier model, Excel
import, migration). No code before the owner OKs the card and the mock.

Parent plan: `PLAN-sales-targets-opportunities-26sep.md` (slice S7, dealer targets, line 1412).
Mock: `mockups/dealer-targets-15oct.html`. UAC: written after the owner answers the card's
questions, as `dealer-sales-targets-15oct-acceptance-criteria.md`.

Slices: 1 dealer subject with its accounts; 2 fixed-RM tiers (rate per account, additional x%,
points with a dated ratio); 3 Excel import by customer code with a Test preview. Slice 4
(invoiced basis) waits on the AutoCount billing sync and is not in this lane.

## 1. What is on main today (measured)

- A target's subject is an agent or a team only:
  `ck_sales_targets_subject_kind` at `sorento_crm_backend/app/models/sales.py:389`.
- Tiers are a % of the target (`from_pct`), one set per target, not per period:
  `SalesTargetCommissionTier`, `sales.py:482-510`. Paid `marginal` or `retroactive` (highest
  tier reached on everything) in `app/services/sales/commission_service.py:31-69`, rounded once
  per period after summing tiers (`:68`). Ruling 3 needs separate rounding for the additional
  line, so that last step does not fit as is.
- A dealer is several ledgers: `customer_groups` (`app/models/order.py:51`) and
  `customers.account_level` (`order.py:183`). Mocha's A/C II/III ledgers have no level set.
- Achievement counts sales order lines, by SO date or DO date
  (`app/services/sales/achievement_service.py:1-31`); the amount is `line_total`, the file's
  "Total (Inc)" (`order.py:783-786`), so tax is in it and no tax split exists on SO lines.
  Credit notes are not netted.
- Invoices and credit notes have tables with a net-of-tax figure
  (`app/models/finance.py:97-103`, `local_net_total`), empty on dev until the sync is live.
- Sorento and Mocha are separate companies (company-scoped tables), so each Excel imports into
  its own company and each company has its own point ratio.
- Frontend: `app/(protected)/sales/targets/` (list `components/SalesTargetsView.tsx`, record
  `components/TargetRecord.tsx` with tabs Details, Periods, Commission at `:546-551`).
  Import precedent: `order-management/customers/components/CustomerImportDialog.tsx` with
  `components/common/ImportFeedbackSections.tsx`.

## 2. Behaviour card

### Rules

1. **A dealer target counts a list of accounts.** Sales of every listed account count toward the
   threshold. A dealer with two separate thresholds (different merged cells) gets two targets.
   Credit is by the order's customer account, whatever agent sold it.
2. **Tiers are fixed RM thresholds, set per period.** Q1 to Q4 each carry their own thresholds
   (2 to 5 tiers). A yearly scheme is its own target with one period; a monthly one is its own
   target split by month.
3. **A rate belongs to a tier and an account.** A shared threshold whose cell says "A/C I - 3%"
   pays 3% on A/C I's sales only; the other accounts helped reach the threshold but earn nothing
   on that tier (ruling 2). Deluxe / Mastile / W-Ply each carry their own rate on the same tier.
4. **Which tier pays:** the highest tier reached pays its rate on the period's sales of the
   accounts it names (Q2 below asks to confirm). Below the first threshold: 0.
5. **"+ Additional x%"** has its own threshold. When met, compute the main tier amount and round
   it, compute the additional amount on the same sales and round it, then add (ruling 3). Never
   one combined rate. If a period has two additional lines, only the highest one reached pays,
   like the main tiers (card assumption, say if wrong).
6. **Rounding:** each account's amount on each line is rounded half up to the unit in Q1, then
   added.
7. **Each scheme pays on its own** (ruling 4): a missed Q4 does not stop the yearly payout, and a
   met yearly does not change any quarter. Monthly works the same way.
8. **Points (Mocha):** a points target computes its incentive in points the same way, then shows
   the RM value = points x the RM-per-point ratio. The ratio is a dated history per company; a
   new ratio never rewrites an earlier period (ruling 5).
9. **Sales basis:** until the AutoCount invoices sync is live, achievement is the sales orders'
   figure and every figure carries a "provisional" label (ruling 1). Slice 4 switches to
   invoiced minus credit notes, excluding tax.
10. **Import:** keys on customer CODE. Every upload is a Test first (writes nothing); the preview
    lists per row: matched customer, name differs (warning, still imported), code not found
    (skipped), new target or update of an existing one. Re-importing the same dealer and dates
    updates that target. The real Excel never enters the repo.

### Worked examples (illustrative figures; swapped for real masked rows when crew sends them)

| # | Case | Inputs | Result |
|---|------|--------|--------|
| E1 | Sorento, shared threshold, "A/C I - 3%" | Q2 tiers: 200,000 at 2% (A/C I), 250,000 at 3% (A/C I). Sales A/C I 180,000.00, A/C III 70,000.00 | Threshold total 250,000.00, tier 2. Pays 3% x 180,000.00 = **RM 5,400.00** (A/C III earns 0) |
| E2 | Sorento Q1 with "+ Additional 1%" | Tiers 150,000 at 2%, 200,000 at 3%, additional 1% from 220,000. Sales 223,450.50 | 3% = 6,703.515 -> 6,703.52; 1% = 2,234.505 -> 2,234.51; **RM 8,938.03** (a combined 4% would give 8,938.02; whole RM would give 6,704 + 2,235 = 8,939) |
| E3 | Sorento, rate per account | One threshold 300,000; Deluxe 2%, Mastile 3%, W-Ply 1.5%. Sales 150,000 / 100,000 / 60,000 | 310,000 meets it. 3,000.00 + 3,000.00 + 900.00 = **RM 6,900.00** |
| E4 | Quarterly and yearly separate | Q4 threshold 200,000, sales 190,000. Yearly 850,000 at 1%, year sales 900,000 | Q4 pays **0**; yearly pays **RM 9,000.00** |
| E5 | Mocha points, ratio changes | Q3 figure 120,000 at 4%, sales 130,000; Q4 figure 120,000, sales 125,000; ratio RM 1.00 until 30 Sep, RM 0.80 from 1 Oct | Q3: 5,200 pts = **RM 5,200.00**; Q4: 5,000 pts = **RM 4,000.00** |

### Edge cases

- An order on an account not in the target's list never counts, even for the same dealer.
- A cancelled order or line drops out on the next read (achievement is live, never stored).
- An order whose customer account is blank (not matched at sync) counts for no dealer; the
  target's Accounts tab shows only matched accounts.
- The same account can sit in a quarterly and a yearly target at once (rule 7).
- An Excel row whose code is not in the CRM is skipped and listed; the rest still imports.
- Excel name differs from the CRM name (35 of 109 Sorento rows, 98 of 135 Mocha): imported on the
  code, flagged in the preview.
- Mocha A/C II/III ledgers have no `account_level`: the label falls back to the customer name.
- A points target with no ratio on its date shows points and "no ratio set" for RM.
- The Dec'25 to Nov'26 dealers: the target's dates come from the Excel, so its quarters run
  Dec-Feb, Mar-May, and so on (only if the Excel states quarters for them).

### Questions (at most 5, each with a recommendation)

- **Q1 Rounding unit.** (a) RM 2 decimals, half up; (b) whole RM. Recommend (a) unless the Excel
  shows whole RM, because the books are kept to the sen and (a) never hides a 50 sen difference.
- **Q2 Which tier pays.** (a) the highest tier reached pays its rate on all the period's sales;
  (b) each rate only on the slice above its threshold. Recommend (a): "threshold paying x%" in
  the Excel reads as (a), and it is the existing "Highest rate on everything" method.
- **Q3 Provisional figure and tax.** SO lines hold only a tax-inclusive total. (a) show the SO
  figure as is, labelled "provisional, incl. tax"; (b) strip a fixed SST rate. Recommend (a):
  (b) guesses per line, and slice 4 replaces it with the true net invoiced figure.
- **Q4 Points.** Are the Mocha thresholds RM sales with the incentive paid in points (as the card
  assumes), or are the thresholds themselves in points? And which ratio applies: (a) the one in
  force on the period's last day; (b) the one in force on the day it is paid. Recommend RM
  thresholds and (a), so a figure for a closed period never changes.
- **Q5 Two "+ Additional" lines in one period.** (a) only the highest one reached pays;
  (b) every one reached pays. Recommend (a), the same rule as the main tiers, unless the Excel
  has a row meant to stack.

## 3. Design (after the owner OKs the card)

Schema (one migration, `sales` schema, additive):
- `sales.targets.subject_kind` widened to `dealer`; dealer targets carry
  `payout_unit` (`rm` | `points`, default `rm`).
- `sales.target_accounts (target_id, customer_id)`: the accounts counted.
- `sales.target_tiers (id, target_period_id, threshold, is_additional)` and
  `sales.target_tier_rates (tier_id, customer_id, rate)`: per-period fixed-RM tiers and the rate
  per account. Kept apart from `target_commission_tiers` (a % of target, per target) because the
  shapes differ; agent and team targets do not change.
- `sales.point_ratios (company_id, effective_from, rm_per_point)`, unique per company and date.

Services: dealer credit in `achievement_service` (customer_id in the target's accounts, both
bases unchanged); `dealer_incentive_service.incentive_for(...)` next to `commission_service`,
pure and unit-tested; `dealer_target_import_service` with `test` and `apply`.

Frontend: Dealers tab in `SalesTargetsView`, Dealer in `KIND_OPTIONS`, an Accounts tab and an
Incentive tab in `TargetRecord`, an import dialog on the `CustomerImportDialog` pattern, a Point
ratio dialog. Component map: in the mock's notes.

## 4. Tests (red first, after the OK)

E1 to E5 as `incentive_for` unit tests; dealer credit golden set (listed accounts only, any
agent, cancelled excluded); additional rounding separate; ratio by date; import Test writes
nothing, code match, name-differs warning, code-not-found skip, re-import updates; 403 per route;
company scope; vitest for the Dealers tab, the Accounts and Incentive tabs, the import preview.
