# REF: AutoCount billing documents (IV, CS, CN, DN) and AR outstanding, schema reference

Source: crew research, 15 Oct 2026 (online AutoCount sources plus sibling tables read live on 13 Oct).
Nothing here was run against the billing tables themselves; AutoCount has not granted access. Every
column carries a status. Database names and anything not needed for the mapping are left out.

**Status legend**
- **[C-off]** official AutoCount source (wiki.autocountsoft.com, the autocountsoft.com help file, AOTG API pages).
- **[C-3p]** a third-party source that reads or mirrors real AutoCount tables (open-source ETL code, the
  vendor Web API Postman collection in the shared-service repo).
- **[C-live]** read live on the owner's AutoCount, but on a SIBLING table of the same 2.x family
  (SO, PO, Cancel SO `XS`, Cancel PO `XP`, Consignment), not on the billing table.
- **[I]** inferred from the SO/DO/XS pattern. **[?]** unknown.

## 1. Shape

- Master/detail pairs `IV`/`IVDTL`, `CS`/`CSDTL`, `CN`/`CNDTL`, `DN`/`DNDTL` (IV [C-off], others [C-3p]).
- Header PK `DocKey` bigint; detail PK `DtlKey` bigint, FK `DocKey`, `Seq`.
- Header: `DocNo` (editable, never an identity), `DocDate`, `DebtorCode` (-> `Debtor.AccNo`), `DebtorName`,
  `SalesAgent`, `CurrencyCode`, `CurrencyRate`, `Cancelled` 'T'/'F' (cancel = VOID, row stays), `DocStatus`
  [C-live XS], `LastModified` (datetime, server local time, not indexed on the siblings).
- Delete in AutoCount is a hard delete [C-off].
- Transfer matrix [C-3p]: IV from QT/SO/DO; CS from QT/SO/DO; CN only from IV; DN has no transfer source.
  CN and DN name their invoice in header text `OurInvoiceNo` (may be blank, hand-typed, or a comma list).

## 2. Header columns -> CRM `finance.billing_documents` (contract 2.6)

| CRM field | AutoCount | Status |
|---|---|---|
| `source_ref` | `{db}:{IV\|CS\|CN\|DN}:{DocKey}` | identity rule |
| `doc_no` | `DocNo` | [C-off] |
| `doc_date` | `DocDate` | [C-off] |
| `customer_ref` / `customer_code` / `customer_name` | `Debtor.AutoKey` via `AccNo`, `DebtorCode`, `DebtorName` | [C-off] |
| `agent_code` | `SalesAgent` | [C-off] |
| `currency_code`, `currency_rate` | `CurrencyCode`, `CurrencyRate` (doc -> MYR) | [C-off] |
| `net_total` | `TotalExTax` | [C-off] column, meaning [I] |
| `tax_total` | `Tax` | [C-live XS] [I] |
| `total` | `NetTotal` (incl. tax, before cash rounding; NOT `FinalTotal` = `NetTotal + RoundAdj`) | [C-off] O14 |
| `local_net_total` | `LocalNetTotal - LocalTax` (`LocalNetTotal` almost certainly includes tax) | [?] |
| `status` | `Cancelled = 'T'` -> `cancelled`, else `posted`; drafts excluded at source | [C-off] |
| `source_modified_at` | `LastModified` | [C-off] |
| `ref`, `description` | `Ref`, `Description` | [C-live XS], [C-off] |
| `against_doc_no` (CN, DN) | `OurInvoiceNo` (first entry when a comma list) | [C-off] |
| (not mapped) | `CNType`, `DNType`, `Reason`, `GoodsReturn` (line), `PaymentMode` (CS: 1 cash, 2 card, 3 multi, 4 credit sales), footer charges `Footer1..3*`, rounding, e-Invoice fields, `UDF_*` | mixed |

## 3. Line columns -> CRM `finance.billing_document_lines`

| CRM field | AutoCount | Status |
|---|---|---|
| `source_ref` | `{db}:{type}:{DocKey}:{DtlKey}` | identity rule |
| `line_no` | `Seq` | [C-live XSDTL] |
| `product_ref` / `item_code` | `Item.AutoKey` via `ItemCode` (NULL on charge, discount, subtotal and text lines) | [C-off] |
| `description`, `uom`, `quantity`, `unit_price` | `Description`, `UOM`, `Qty`, `UnitPrice` | [C-off] |
| `discount_amount` | `DiscountAmt` (`Discount` is text like "10%+5") | [C-off] |
| `net_amount` | `SubTotalExTax` | [C-off] O14 |
| `tax_code`, `tax_rate`, `tax_amount` | `TaxCode` (1.9 name `TaxType`), `TaxRate`, `Tax` | [C-live XSDTL] |
| `line_total` | `SubTotal` (INCLUDES tax: 85.50 + 8.55 = 94.05) | [C-off] O14 |
| `from_doc_type`, `from_doc_no`, `from_line_ref` | `FromDocType` ('SO'/'DO'/'QT'/'IV'), `FromDocNo`, `FromDocDtlKey` resolved to its own header | [C-3p] |

Billing line cut (differs from SO): keep any line whose `SubTotal` is non-zero; drop pure text lines
(`SubTotal` NULL/0 and `Qty` NULL); package parent/child (`DtlType`, `ParentDtlKey`) must not double count.

## 4. Outstanding lives in the AR module, not on IV/CS/CN/DN

| Table | Key columns | Status |
|---|---|---|
| `ARInvoice` (one per IV and per CS, plus hand-keyed AR invoices) | `DocKey`, `DocNo`, `DebtorCode`, `DocDate`, `DueDate`, `Outstanding`, `Cancelled`, `LastModified`, `NetTotal`, `CurrencyCode`; `SourceType`/`SourceKey` back to the IV/CS | [C-3p]; Source* as API fields [C-off], as columns [I] |
| `ARDN` | `DocKey`, `DocNo`, `DebtorCode`, `DocDate`, `DueDate`, `NetTotal`, `Outstanding`, `LastModified` | [C-off] O4 |
| `ARCN` | `DocKey`, `DocNo`, `DebtorCode`, `DocDate`, `NetTotal`, `KnockOffAmt`, `LastModified`; unapplied = `NetTotal - KnockOffAmt` | [C-off] O3 |
| `ARPaymentKnockOff`, `ARCNKnockOff` | `DocKey` (payment / CN) -> `KnockOffDocKey`, `KnockOffDocType` ('RI' invoice, 'RD' debit note), `Amount`; no `LastModified` | [C-3p] |
| `Debtor` | `IsCashSaleDebtor` (the shared walk-in account) | [C-3p] |

- Invoice / cash sale outstanding = `ARInvoice.Outstanding`, matched by `SourceType`/`SourceKey` [I], else
  `DocNo` + `DebtorCode` [I]. Debit note = `ARDN.Outstanding`. Credit note unapplied = `NetTotal - KnockOffAmt`.
- "Post To GL" unticked = no AR row = blank outstanding (O21). A paid-in-full cash sale shows 0 at once;
  `PaymentMode = 4` (credit sales) leaves it fully outstanding.
- Whether a knock-off bumps `ARInvoice.LastModified` is **unknown**; `IV.LastModified` almost certainly does
  not move on payment. Outstanding needs its own sweep, not the billing-document watermark.

## 5. Gotchas that shape the CRM side

1. Charge, discount and rounding lines carry money with no `ItemCode`; a charge line may have NULL `Qty`.
2. Cash sales always carry a debtor, often a shared cash account: customer-level views must treat it as walk-in.
3. Multi-currency: use AutoCount's own `Local*` amounts, never recompute.
4. Send `NetTotal`, not `FinalTotal`, or the CRM's `total = net_total + tax_total` (0.01) check fails.
5. Footer charges are header-only money: header totals need not equal the sum of lines.
6. DocNo is not an identity (an IV can take its DO's number); key on `{db}:{type}:{DocKey}`.
7. `FromDocDtlKey` may be NULL while `FromDocNo` is filled (on XSDTL always NULL since Dec 2024): link only exactly.
8. A CN cannot be transferred from a CS; a CN against a CS has only `OurInvoiceNo` text.
9. Line-only edits do not bump the header `LastModified` (proven on SO): keep the line-fingerprint sweep.
10. Drafts (2.2) have no AR entry; exclude them and send a deletion if a pushed draft disappears.
11. IV line -> DO line -> SO line needs `DODTL`, which the read-only login cannot read: a DO-sourced IV line
    carries `from_doc_type = 'DO'` + `from_doc_no` and no SO line ref.

## 6. First checks once access is granted (read-only)

1. `INFORMATION_SCHEMA.COLUMNS` for the 8 billing tables plus `ARInvoice`, `ARCN`, `ARDN`, `ARPayment`,
   `ARPaymentKnockOff`, `ARCNKnockOff` on every company DB; flip each [I] to confirmed or wrong.
2. `DocStatus` x `Cancelled` counts: find the draft letter.
3. `FromDocType` distribution and `FromDocDtlKey` NULL rate by year.
4. Totals identities on a sample: `NetTotal = TotalExTax + Tax`, `FinalTotal = NetTotal + RoundAdj`,
   `LocalNetTotal - LocalTax` vs `LocalAnalysisNetTotal`.
5. Line shapes: `ItemCode IS NULL AND SubTotal <> 0`, `Qty IS NULL`, `Qty < 0`, `DtlType` values.
6. AR linkage: `ARInvoice.SourceType` values, `SourceKey` match rate to IV/CS `DocKey`, `DocNo` equality rate.
7. Watermark: on a recent IV with a CN and a payment, compare `IV.LastModified`, `ARInvoice.LastModified`
   and the knock-off `GainLossDate`.
8. Cash debtor share of CS; non-MYR count; `OurInvoiceNo` blank / comma-list counts.

Full source list (O1 to O22 official, T1 to T4 third party) is in the crew research report of 15 Oct.
