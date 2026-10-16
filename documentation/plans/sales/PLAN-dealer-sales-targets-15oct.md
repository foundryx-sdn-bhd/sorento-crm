# PLAN: dealer sales targets and incentive schemes (lane SALES-TARGET-DEALER)

Status: **built, review READY, waiting on the owner's hand test** (16 Oct; L track). Code review
fix round done: a rate typed on some rows of a merged target is those accounts' rate (ruling 2),
accounts group per scheme kind, codes match loosely incl. PAY TO, kill tests for the ratio day and
the default pay-to; agent and team targets keep the plain achievement statement. Owner OK on v2 (00:19: "make sure we can configure all
the sales targets in the excel using this architecture"), with the gate that every row of both
Excel files is representable. Slices 1 to 3 built on PR #1715: the one engine
(`commission_service.payout_for`), migration `sales_0006_dealer_targets`, dealer credit in
`achievement_service`, the import with its per-row dry-run report and the local
`scripts/dealer_targets_dry_run.py` for the real files, and the frontend. Security review done
and fixed; code review and kill test running. Track: full (new subject, tier model, Excel import,
migration). Open: crew's dry run on the real files (only failing patterns come back, masked).

Parent plan: `PLAN-sales-targets-opportunities-26sep.md` (slice S7, dealer targets, line 1412).
Mock: `mockups/dealer-targets-15oct.html`. UAC: written after the owner OKs v2, as `dealer-sales-targets-15oct-acceptance-criteria.md`.

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

## 2. Behaviour card (v2, 16 Oct)

### Owner rulings, final (16 Oct 00:10)

- **One target engine.** A dealer is a third subject on the existing `sales.targets` engine,
  beside agent and team. Agent and dealer targets get the same flexibility; nothing is a
  dealer-only engine. Keep the UI simple: reuse the existing Targets list, record and
  Commission tab, and the existing commission logic.
- **Q1 rounding:** configurable, default 2 decimals (sen).
- **Q2:** the highest tier reached pays its rate; the tiers below and above it play no part.
- **Q3 (crew, 15 Oct):** until the billing sync, the SO figure as is, labelled
  "provisional, incl. tax".
- **Q4 points:** Mocha thresholds are in POINTS, converted to RM with the dated ratio. Each
  scheme says its threshold unit, points or RM. The ratio is the one in force on the period's
  last day.
- **Q5:** two "+ Additional" lines in one period both pay (they stack), each rounded on its own.
- **New:** the accounts a target counts and the account it pays to are separate. A target on
  A/C III may pay its rebate to A/C I.

### Rules

1. **One engine, three subjects.** A target is for an agent, a team or a dealer. Everything
   below about tiers, thresholds, additional lines, points and rounding works the same for an
   agent target as for a dealer target. Only the Accounts list and Pay to account are
   dealer-only, because an agent has no accounts.
2. **A dealer target counts a list of accounts.** Sales of every listed account count toward the
   threshold, whoever the agent was. A dealer with two separate thresholds (two merged cells)
   gets two targets.
3. **Pay to account.** Each dealer target names the one account its incentive is credited to.
   It defaults to the first counted account and may be an account the target does not count
   (target on A/C III, paid to A/C I).
4. **Tiers can differ per period.** A target either uses the same tiers every period (today's
   behaviour, unchanged for existing targets) or its own tiers per period (Q1 to Q4 each with
   their own thresholds). A yearly scheme is its own target with one period; a monthly one is its
   own target split by month. Each pays on its own (ruling 4).
5. **Threshold unit per target:** % of the target figure (today's tiers), RM, or points. A points
   threshold becomes RM as points x the ratio in force on the period's last day; the
   comparison is then in RM.
6. **Which tier pays:** the highest tier reached pays its rate on the period's sales. Below the
   first threshold: 0. Today's other method ("Higher rate above each threshold only") stays
   available for agent targets that use it; dealer targets and the import use the highest
   tier.
7. **Rate per account (dealer).** A tier has one rate, or a rate per counted account. With rates
   per account, only the accounts given a rate earn on that tier: a shared threshold whose cell
   says "A/C I - 3%" pays 3% on A/C I's sales only (ruling 2), and Deluxe / Mastile / W-Ply
   each get their own rate on the same tier.
8. **"+ Additional x%"** lines have their own thresholds. Every additional line whose threshold
   is met pays, on the same sales as the main tier (Q5, they stack).
9. **Rounding:** each line (main tier, each additional, each account's amount on a line) is
   rounded on its own, then the lines are added; never one combined rate (ruling 3). The unit is
   set per target, sen (default) or whole ringgit, and the import reads it from a column
   (A2 answered (a) by crew, 16 Oct).
10. **Payout is in RM.** Points only set thresholds (A1 answered (a) by crew, 16 Oct).
11. **Sales basis:** until the AutoCount invoices sync is live, the SO figure as is, labelled
    "provisional, incl. tax" (ruling 1, Q3). Slice 4 switches to invoiced minus credit notes,
    excluding tax.
12. **Import:** keys on customer CODE. Every upload is a Test first and writes nothing. The
    preview lists per row: matched customer, name differs (warning, still imported), code not
    found (skipped), new target or update of an existing one. Re-importing the same dealer and
    dates updates that target. The real Excel never enters the repo.

### Worked examples (illustrative figures; swapped for real masked rows when crew sends them)

| # | Case | Inputs | Result |
|---|------|--------|--------|
| E1 | Sorento, shared threshold, "A/C I - 3%" | Counts A/C I + III. Q2 tiers 200,000 at 2% (A/C I), 250,000 at 3% (A/C I). Sales A/C I 180,000.00, A/C III 70,000.00 | 250,000.00 reaches tier 2. 3% x 180,000.00 = **RM 5,400.00** to A/C I; A/C III earns 0 |
| E2 | Sorento Q1, two "+ Additional" lines | Tiers 150,000 at 2%, 200,000 at 3%; + 1% from 220,000; + 0.5% from 250,000. Sales 253,450.50 | 7,603.515 -> 7,603.52; 2,534.505 -> 2,534.51; 1,267.2525 -> 1,267.25; **RM 11,405.28** (one 4.5% rate would give 11,405.27; whole ringgit gives 7,604 + 2,535 + 1,267 = 11,406) |
| E3 | Sorento, rate per account | One threshold 300,000; Deluxe 2%, Mastile 3%, W-Ply 1.5%. Sales 150,000 / 100,000 / 60,000 | 310,000 reaches it. 3,000.00 + 3,000.00 + 900.00 = **RM 6,900.00** |
| E4 | Quarterly and yearly separate | Q4 threshold 200,000, sales 190,000. Yearly 850,000 at 1%, year sales 900,000 | Q4 pays **0**; yearly pays **RM 9,000.00** |
| E5 | Mocha, thresholds in points | Q3 and Q4 threshold 100,000 pts at 4%. Ratio RM 1.20/pt from 1 Jan, RM 1.30/pt from 1 Oct. Sales Q3 130,000, Q4 125,000 | Q3: 100,000 x 1.20 = RM 120,000.00, reached, 4% = **RM 5,200.00**. Q4: 100,000 x 1.30 = RM 130,000.00, not reached, **0** |
| E6 | Target on A/C III, paid to A/C I | Counts A/C III only; Q2 threshold 80,000 at 2%; pay to A/C I. Sales A/C III 90,000 | 2% x 90,000 = **RM 1,800.00**, credited to A/C I |
| E7 | Agent target, same engine | Agent, quarterly, RM thresholds 500,000 at 1% and 600,000 at 1.5%. Q1 sales 612,000 | Highest reached 1.5%: **RM 9,180.00** |

### Edge cases

- An order on an account the target does not count never counts, even for the same dealer; the
  pay to account's own sales do not count unless it is also in the counted list.
- A cancelled order or line drops out on the next read (achievement is live, never stored).
- An order whose customer account is blank (not matched at sync) counts for no dealer.
- The same account can sit in a quarterly and a yearly target at once (rule 4).
- A points target with no ratio in force on the period's last day cannot test its thresholds:
  the period shows "no ratio set" and pays nothing until a ratio is added.
- A new ratio changes only periods whose last day is on or after its date; closed periods keep
  their figure.
- Existing agent and team targets read exactly as today (same tiers every period, % of target,
  same method); a migration test asserts it.
- An Excel row whose code is not in the CRM is skipped and listed; the rest still imports.
- Excel name differs from the CRM name (35 of 109 Sorento rows, 98 of 135 Mocha): imported on the
  code, flagged in the preview.
- Mocha A/C II/III ledgers have no `account_level`: the label falls back to the customer name.

### Assumptions (both answered (a) by crew, 16 Oct)

- **A1 Payout unit on a points target. ANSWERED (a), shown in RM.** (a) RM: rate x RM sales, points only set the threshold;
  (b) points: the incentive is also counted in points and then converted. Recommend (a): with
  one dated ratio both give the same RM, and (a) shows one figure instead of two.
- **A2 Where rounding is set. ANSWERED (a), per target, import column, default sen.** (a) per target, default sen, the import sets it per file;
  (b) one company-wide setting. Recommend (a): one column on the target the engine already
  reads, no new settings screen, and a Sorento and a Mocha scheme can differ.

## 3. Design (one engine)

Schema (one migration, `sales` schema, additive; existing rows keep today's meaning):
- `sales.targets`: `subject_kind` widened to `dealer`; `payout_customer_id` (dealer only);
  `threshold_unit` (`pct` default, `amount`, `points`); `rounding` (`sen` default, `ringgit`);
  `tiers_per_period` (bool, default false).
- `sales.target_accounts (target_id, customer_id)`: the accounts a dealer target counts.
- `sales.target_commission_tiers` (the existing table) gains `target_period_id` (null = every
  period), `threshold` (RM or points, when the unit is not `pct`; `from_pct` becomes nullable,
  one of the two required by a check) and `is_additional`. The unique index moves to
  (target, period, threshold kind value, is_additional).
- `sales.target_tier_rates (tier_id, customer_id, rate)`: rate per account; no rows = the tier's
  own `rate` for every counted account.
- `sales.point_ratios (company_id, effective_from, rm_per_point)`, unique per company and date.

Services: `achievement_service` gets the dealer credit (customer in the target's accounts) and
returns achievement per (period, account) for dealer targets so per-account rates can pay;
`commission_service.commission_for` grows to: threshold in RM (from pct, RM or points x ratio),
highest-tier or marginal main line, every reached additional line, per-account rates, rounding
per line in the target's unit. One function for every subject. `dealer_target_import_service`
with `test` and `apply`.

Frontend (reuse, no new screens beyond the import and ratio dialogs): Dealers tab in
`SalesTargetsView`; Dealer in `KIND_OPTIONS`; an Accounts tab in `TargetRecord` (like the team's
Agents tab); the existing Commission tab gains Threshold in, Round to, Same tiers every period,
the Additional row type, per-account rate columns and Pay to account, for every subject; the
import dialog on the `CustomerImportDialog` pattern; a Point ratio dialog. Component map: the
mock's notes.

## 4. Tests (red first, after the OK)

E1 to E7 as `commission_for` unit tests; existing commission tests unchanged and green (one
engine); a migrated agent target reads as before; dealer credit golden set (listed accounts
only, any agent, cancelled excluded, pay to account not counted); additional lines stack and
round separately; rounding per target; ratio on the period's last day; import Test writes
nothing, code match, name-differs warning, code-not-found skip, re-import updates; 403 per
route; company scope; vitest for the Dealers tab, the Accounts tab, the Commission tab's new
fields on an agent and a dealer target, and the import preview.
