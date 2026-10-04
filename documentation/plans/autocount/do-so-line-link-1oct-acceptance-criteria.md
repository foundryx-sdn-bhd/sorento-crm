# UAC: AutoCount DO ingest, FromDocDtlKey 0 means "no link" (DO-SO-LINE-LINK)

Plan: `PLAN-do-so-line-link-1oct.md`.

- **AC-DSL001** A DO line with `FromDocType "SO"`, `FromDocNo` naming an SO that holds exactly one
  line of the DO line's product, and `FromDocDtlKey 0` is linked to that SO line, stores
  `from_dtl_key` NULL, and the record carries no `so_line_unresolved`.
- **AC-DSL002** The same with `FromDocDtlKey` missing (key absent or null): linked the same way.
- **AC-DSL003** `FromDocDtlKey 0`, `FromDocNo` naming an SO with no line of that product (or no
  such SO): no link, `so_line_unresolved`.
- **AC-DSL004** `FromDocDtlKey 0`, the SO holds two lines of the product: no link,
  `so_line_unresolved` (no guess).
- **AC-DSL005** A DO landed with `FromDocDtlKey 0` before its SO; a later non-dry DO batch links
  it by the natural key. A row stored by the old code with `from_dtl_key = 0` heals the same way.
- **AC-DSL006** A GRN line with `FromDocDtlKey 0` and an `OurPONo` links the purchase order by
  number, stores `from_dtl_key` NULL and carries no `po_line_unresolved`. A GRN line stored with
  `from_dtl_key = 0` by the old code links its purchase order on a later batch.
- **AC-DSL007** An SO with the same number and product in another company is never a match.
- Unchanged: an exact `FromDocDtlKey` link (AC-AG031/032), `RefDocNo` links no line (AC-AG034).
