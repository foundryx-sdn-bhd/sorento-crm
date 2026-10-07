# UAC: DBR debtor cleanup

Owner answers 7 Oct 2026 (crew relay a85186219): Q2 (a), Q3 yes, Q4 (a), Q5 (a). Q1 (chat links, DELUXE HOME CENTRE (KTN)
contact) still with the owner; the data script's chat-link handling waits on it.

## Stop creating DBR customers

- AC-1 Creating a DO (manual create, DO sheet import) with a blank or missing debtor code and debtor name "SAMPLE"
  stores the DO with debtor_name "SAMPLE", customer_id NULL, and creates NO customer row.
- AC-2 Updating a DO with a blank debtor code that today points at a `DBR-` customer clears its customer_id to NULL;
  debtor_name kept. No customer row created.
- AC-3 A DO with a non-blank debtor code still links to (or creates) the customer by that code, exactly as today.
- AC-4 AutoCount-owned DOs are untouched by the create/update sync (as today).

## Customer filter and dealer scope match by customer only (Q4)

- AC-5 Filtering the DO list (and every list path that takes customer ids: list, analytics, the `/debtors`
  endpoint's callers via the same filter) by a customer named "SAMPLE" returns only DOs whose customer_id is that
  customer. A customer-less DO whose debtor_name is "SAMPLE" is NOT returned.
- AC-6 A dealer contact scoped to that customer does not see the customer-less "SAMPLE" DO.

## Debtor name shown / searched when there is no customer (Q5)

- AC-7 Outstanding report, DO side: a customer-less DO shows its debtor name as the customer name and buckets under
  it; a `customer_query` search for its debtor name finds it. A customer id filter still excludes it (Q4).
- AC-8 Delivery-order-lines report dataset: the Customer column shows the DO's debtor name when there is no customer.
- AC-9 DO export quick search finds a customer-less DO by its debtor name.
- Display/search only: no row is linked to a customer by name anywhere.

## Data script (dry run by default; never run from the lane)

- AC-10 `scripts/cleanup_dbr_customers.py` without `--apply` prints counts and rows (DBR customers, DOs, stock asks,
  chat links) and changes nothing.
- AC-11 With `--apply`, in one transaction: DOs on DBR customers get customer_id NULL (debtor_name untouched);
  stock asks on DBR customers get customer_id NULL and survive (Q2); DBR customers are deleted.
- AC-12 DBR = customer_code matching `^DBR-[0-9a-f]{10}$` with no integration_references row. A real customer and a
  DBR-looking code that has an integration reference are left alone.
- AC-13 Chat links (respond_contact_customers) on DBR customers: behaviour set by owner Q1 (pending). Until then the
  script refuses `--apply` while any chat link exists unless an explicit `--drop-chat-links` flag is passed.

No UI change, no migration.
