# RUNBOOK: billing documents backfill and outstanding, first live run (S4, S5)

Status: DRAFT (lane AC-BILLING, 17 Oct). Runs after 25 Oct, once AutoCount grants read access. UAC S4-1 to
S4-3 and S5-10. Schema reference: `REF-autocount-billing-schema.md`. Contract: cross-repo plan section 12
(2.6 `billing_documents`) and the `ar_outstanding` section (2.10). Nothing here writes to AutoCount.

## 0. Preconditions

1. AutoCount admin grants the read-only SQL login SELECT on `IV, IVDTL, CS, CSDTL, CN, CNDTL, DN, DNDTL,
   ARInvoice, ARDN, ARCN` (Debtor and Item are already readable), on every company DB that is in scope.
2. The REF section 6 checks have run and every [I] that the presets rely on is confirmed; the presets are
   corrected where a check failed. Record the results in section 5 below.
3. The `finance` module is switched on for the tenant (App Store).
4. Permission grant: the feed's act-as role holds `finance.billing_documents.edit`, `.delete` and `.view`.
   fin_0001 granted them to every role holding `scm.sales_orders.edit` (Q16 built as (a)); confirm on the
   Roles screen before the first push, or every record answers 403.

## 1. Start date

Shared-service configuration, not CRM configuration: `from_date = 2023-01-01` on the four billing presets.
The CRM holds no copy and has no date floor.

## 2. Order and size

- Dry run first: one day of each type through `?dry_run=true`, read the verdicts, expect no `failed`.
- Then oldest first, day by day. Within a day: IV and CS before CN and DN (a note links its invoice by
  `against_source_ref` / `against_doc_no` at ingest; a note that lands first is linked when its invoice lands).
- The DO backfill must reach a day before that day's invoices are pushed: an invoice line's DO hop
  (`{book}:DO:{DocKey}:{DtlKey}` to the sales order line) is decided at its push, so an invoice pushed
  before its DO stays unlinked until the invoice is pushed again.
- Batch size 1000 records (the door's cap; 413 above it).
- Each company pushes under its own `companyCode`.
- Outstanding (`ar_outstanding`) runs only after the billing backfill has reached today, so every listed
  document exists; before that the verdicts count `documents_unknown`.

## 3. Re-run rule

Safe. An identical push answers `unchanged` and writes nothing (UAC S0-5); an older `source_modified_at`
answers `unchanged` + `stale_ignored`. Re-run any failed day as a whole.

## 4. Check queries (CRM, read-only)

```sql
-- documents per type per month (compare with AutoCount's own counts, S4-2)
SELECT document_type, date_trunc('month', doc_date) AS month, count(*), sum(net_total)
FROM finance.billing_documents
WHERE company_id = :company_id AND status = 'posted'
GROUP BY 1, 2 ORDER BY 2, 1;

-- unclassified documents per month (the "(blank)" row of the Yearly comparison)
SELECT date_trunc('month', doc_date) AS month, count(*)
FROM finance.billing_documents
WHERE company_id = :company_id AND demand_class IS NULL AND status = 'posted'
GROUP BY 1 ORDER BY 1;

-- unresolved masters (fix the master, then re-push the day)
SELECT count(*) FILTER (WHERE customer_id IS NULL) AS no_customer,
       count(*) FILTER (WHERE sales_agent_id IS NULL AND agent_code IS NOT NULL) AS no_agent
FROM finance.billing_documents WHERE company_id = :company_id;

-- outstanding per customer vs the sum of its documents (S5-10; a gap = AR entries keyed straight in AR)
SELECT c.customer_code, c.ar_outstanding, c.ar_as_of, sum(b.outstanding) AS docs_sum
FROM customers c LEFT JOIN finance.billing_documents b
  ON b.customer_id = c.id AND b.status = 'posted'
WHERE c.company_id = :company_id AND c.ar_outstanding IS NOT NULL
GROUP BY 1, 2, 3 ORDER BY abs(c.ar_outstanding - coalesce(sum(b.outstanding), 0)) DESC LIMIT 50;
```

## 5. Results (fill in on the run)

| Check | Result |
|---|---|
| REF section 6 checks (per item) | |
| Test months (2024 or later) and per-type counts vs AutoCount | |
| Yearly comparison, Basis = Invoiced, vs the AutoCount PDF (S4-3, within RM 1 per cell) | |
| Three debtors: CRM outstanding vs AutoCount Outstanding AR Invoice report (S5-10) | |
