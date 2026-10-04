# UAC: AutoCount GRN header SPO number (GRN-HEADER-SPO)

Plan: `PLAN-grn-header-spo-04oct.md`.

- AC-GHS01: A pushed GRN whose header SPO field (see `_GRN_HEADER_SPO_FIELDS`) holds a known SPO
  number shows that SPO as its header "SPO Number".
- AC-GHS02: A pushed GRN with no SPO in the header but lines received against one SPO shows that
  SPO as its header "SPO Number".
- AC-GHS03: Lines received against several SPOs show them all, joined with ", ", in line order.
- AC-GHS04: A header value that is not a known SPO number is never shown as the SPO; the line
  SPOs are used instead.
- AC-GHS05: Re-pushing an existing GRN fills a missing header SPO and reports the record as
  `updated`.
- AC-GHS06: A push that finds no SPO never clears a header SPO already stored.
