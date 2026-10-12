# PLAN: staff stock reply, one PO block line per PO document, location and date

Status: built, in review (small fix track: under 300 changed lines, no migration, no auth/RBAC change). Rescoped 13 Oct 2026.

## Problem

Crew live check on main (d01ca05cb): a staff (office) contact asking "stock SRTWC286" read the
SRTWC286-SH-PP purchase-order block twice.

Investigation (posted on PR #1612): this is real data, not a render bug. One PO has two
distinct open lines for the product (36 + 36, same location and PO date, each from a
different SO line). The PO-placed read returns one row per open line
(`purchase_order_service.purchase_orders_placed_rows`), and the stock reply's PO block
(`lanes/business/answer.py::_crossdomain_rung_text`) prints one block per row without the
document number (owner ruling D3, 12 Sep 2026). So the two lines read as a repeat.

The past-ETA half of the original lane is dropped (owner ruling 13 Oct 2026: past ETAs on
the staff reply are a data issue and staff need to see them). Staff ETA display is unchanged.

## Fix (owner ruling 13 Oct 2026)

In the stock reply's PO block, rows of the same product, same document (`kind` + company + PO/SPO
number), same location and same PO date are consolidated into ONE row before rendering,
with ordered and outstanding summed. Rows that differ in document, location or date stay
separate. The existing formatter `_crossdomain_rung_text` is reused unchanged; the grouping
is `answer._consolidate_rung_rows`, called on the rows just before it.

- "Date" is the PO date: it is the only date the block prints, and the only date the rung
  row carries. The MCP presenter drops the line's expected date.
- A row merges only when both of its quantities are numbers and it has a document number.
- The merged row keeps the first line's position.

## Other readers

- `_crossdomain_rung_text` has one caller, `_apply_crossdomain_rung`, which serves both the
  stock-origin and the incoming-origin ladder. Both are covered.
- Dealer and customer replies do not reach this block. `crossdomain_zeroset` switches the
  ladder off for an availability (dealer) stock reply and for a dealer incoming reply, and the
  dealer incoming presenter prints ETAs only.
- The direct PO ask and the `turn/fetch.py::_climb` PO rung render through the MCP presenter
  (`sorento_crm_mcp/presenters.py::_purchase_orders_placed`). That is a different formatter
  in a different package, and it prints the PO number. Not changed in this lane; reported to
  the owner.

## Tests

- `sorento_crm_backend/tests/chatbot/test_po_rung_consolidate.py` (red first): two lines of
  the same PO, location and date print as one line, 72/72. Three lines sum, including a
  decimal. A different document, location, PO date or kind (PO vs SPO) stays separate. No
  document number, or a non-numeric quantity, never merges. The merged line keeps the first
  position.
- `tests/chatbot/test_crossdomain_ladder.py::TestD11LinesFollowOneAnotherInToolOrder` is
  updated to the ruling: its three same-PO lines now print as one row, 57.
- Review round 1 (READY with two should-fixes, both applied): ordered and outstanding are
  pinned apart (36/20 twice prints 72/40). Company is part of the document key, because PO
  numbers are unique per company only. A blank PO number never merges. The summed number
  prints plain (`36.0` + `36` prints `72`).
- Kill test: removing the grouping call turns 4 tests red, and dropping location and date
  from the key turns 3 red. Each of these turns 1 red: summing outstanding from ordered,
  printing the raw sum, and dropping company from the key.
