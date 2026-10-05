# UAC: AutoCount GRN header SPO number (GRN-HEADER-SPO)

Plan: `PLAN-grn-header-spo-04oct.md`.

- AC-GHS01: The AutoCount GRN header is never read for an SPO (owner, 4 Oct: the API has no
  header SPO field); `Ref`, `RefDocNo` and `Description` never become the SPO Number.
- AC-GHS02: A pushed GRN whose lines are received against one SPO shows that SPO as its header
  "SPO Number".
- AC-GHS03: Lines received against several SPOs show them all, joined with ", ", in line order.
- AC-GHS04: A GRN with no line on an SPO keeps a NULL header SPO.
- AC-GHS05: Re-pushing an existing GRN fills a missing header SPO and reports the record as
  `updated`.
- AC-GHS06: A push that finds no SPO never clears a header SPO already stored.
