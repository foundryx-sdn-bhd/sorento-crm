# UAC: OI line follows the SO line's product change (OI-PRODUCT-FOLLOW)

Plan: `PLAN-oi-product-follow-2oct.md` (owner rulings R1-R6, 2 Oct 2026). PR #1442.

## Journey

AutoCount changes the product on an SO line (same line ref). CS opens fulfilment planning,
sees the product change for that line, and clicks Confirm. The order inquiry line now names
the new product with the old one shown as "was X", purchasing gets the handover email
saying "CHANGE ITEM CODE TO <new> (WAS <old>)", and a line purchasing had confirmed comes
back to To confirm. A PO/SPO already linked to the line stays linked.

## Acceptance criteria

- **AC-PF-1 (S1)** An ESB re-push that swaps a line's product moves the board's mirror line
  to the new product; date and qty untouched. An identical re-push leaves it alone.
  Test: `tests/test_oi_product_follow.py`.
- **AC-PF-2 (R1)** The OI row is restated on Confirm, in place (same row id): `item_code` =
  new, `previous_item_code` = old, note says "Was item <old>". Nothing changes on the OI
  before Confirm.
- **AC-PF-3 (R2)** A linked row keeps every link (same link id, same qty). An acknowledged
  row goes to `changed` with `changed_at` set, as a qty/date change does.
- **AC-PF-4 (R4)** The handover email line prints the NEW code in ITEM CODE and
  "CHANGE ITEM CODE TO <new> (WAS <old>)" in REMARK, after any qty/date phrase
  ("ORDER 5, CHANGE ITEM CODE TO ...").
- **AC-PF-5** A Confirm where the product did not move writes no `previous_item_code`, and
  a product-only change writes no `previous_qty` / `previous_delivery_date` (no false
  "Was 10 -> Now 10"). A row whose code is not a catalogue code is never rewritten.
- **AC-PF-5b (R1, tester round)** A line whose old row is set aside because its
  document is already received, or a line carried along by a Confirm of another line,
  gets a fresh row on the new code with "was <old>", "Was item <old>" in its note, and a
  handover line with "CHANGE ITEM CODE TO <new> (WAS <old>)". The set-aside row keeps
  its old code and its link.
- **AC-PF-6 (S3)** The worklist and the OI detail Lines tab show the new code with
  "was <old>" muted under it; no "was" when the product never moved.
  Test: `orderInquiryWorklistColumns.productFollow.test.tsx`.
- **AC-PF-7 (R3)** `oi-product-follow-correction-PROPOSED.sql` fixes ALL live mismatched
  rows (links untouched), runs a dry-run count first, ends in ROLLBACK, and refuses to run
  before the migration. The owner runs it; crew never touches prod.
- **AC-PF-8 (R5/R6)** New SO lines and lines set to qty 0 behave exactly as before.
