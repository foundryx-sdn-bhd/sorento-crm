# UAC - WA-CONCISE: WhatsApp replies, one fact per line (card v4)

Plan: `PLAN-wa-concise.md`. Card v4: `documentation/mockups/wa-concise/index.html` (owner "ok can",
3 Oct 2026). Every string below is exact (WhatsApp `*bold*`, `_italic_`); `\n` = newline.
Placeholder data only (public repo).

## Block form (all ACs)

A block is one fact per line, `*Label:* value`, every label bold, today's labels in today's order
(orders `presenters.py:424-436`, incoming `presenters.py:849-873`, stock `presenters.py:1348-1352`
and `1403-1447`). Values are unchanged (`24 (O/S: 10)`, `BRW (35)`). No blank line inside a
block, one blank line between blocks. More than one block: `1. ` before the first line of each
block. Exactly one block: no number. Flag lines (`⚠️  *(PRODUCT DISCONTINUED)*` etc.) stay, once
per block, after its last fact line. Applies to stock, incoming, order and product list replies;
every other tool's rows are unchanged.

## S1 Stock (crm_inventory_stock_balance_list)

- AC-1 Compact, one location: `*Product Code:* SRTWT5844-GM\n*BRW:* 24 (O/S: 10)` (no Total
  line). Ungranted O/S: `*BRW:* 24`.
- AC-2 Compact, more than one location keeps Total:
  `3. *Product Code:* SRTSWT3001\n*Total:* 136\n*BRW:* 28\n*MWH:* 108`.
- AC-3 Zero locations print as today: `4. *Product Code:* SRTSWT3001-GM\n*BRW:* 0`. A compact
  entry with no location line keeps `*Total:* 0`.
- AC-4 Detailed rows merge into one block per (company, product code), first-seen order. Lines:
  `*Company:* X` only when the reply's rows span more than one company; `*Product Code:*`;
  `*Product Name:*` when today's row has it; then one line per row `*{System Location}:* {Quantity
  On Hand}` with ` (O/S: {Outstanding})` appended when the row has Outstanding; Warehouse name
  dropped. `*Total:* {sum}` (plus ` (O/S: {sum})` when every row has Outstanding) only when the
  block has more than one location, placed before the location lines. Example (two companies):
  `1. *Company:* Sorento\n*Product Code:* SRT6542-DIY\n*BRW:* 0 (O/S: 233)\n\n2. *Company:* Mocha\n*Product Code:* SRT6542-DIY\n*MOCHA-WH:* 1`.
- AC-5 Openers removed when at least one block prints: "Stock details found for the requested
  products.", "Stock summary for the requested products.", "Here are the orders I found.",
  "Here are the delivered orders I found.", "Here are the matching products.". With zero blocks
  the reply is unchanged.
- AC-6 Footer `_Updated 11/09/2026 17:26_` (was `_Data last updated: 11/09/2026 17:26:05_`)
  wherever the old one printed. `turn/compose.py:451` (extras slot above the footer) and
  `answer.py:1564` (promo re-intro keeps it) work with the new footer.
- AC-7 Dealer availability replies (`stock_availability`) are byte-identical to main.

## S2 Cross-domain (incoming ask, nothing incoming)

- AC-8 Stock exists, whole reply:
  `*Product Code:* SRTWT5844-GM\n*Incoming:* none\n*BRW:* 24 (O/S: 10)\n\nWould you like me to escalate to purchasing team?`
  None of: "Here's what you want", "But no incoming matched these.", "No incoming for",
  "But here are the stock details for the requested products:".
- AC-9 All zero, nothing on order:
  `*Product Code:* SRTWT5844-BL\n*Incoming:* none\n*Stock:* 0\n*PO:* none\n\nWould you like me to escalate to purchasing team?`
  A zero row with non-zero O/S keeps its location line (`*BRW:* 0 (O/S: 233)`) instead of
  `*Stock:* 0`, and `*PO:* none` still follows when nothing is on order.
- AC-10 No stock row at all, nothing on order: `*Product Code:* X\n*Incoming:* none\n*Stock:* none\n*PO:* none`.
- AC-11 PO placed: `*PO:* placed` then today's PO/SPO lines for that code, in its block.
- AC-12 Mixed (one code incoming, one not), incoming block(s) first, numbering continues:
  `1. *Product Code:* SRTWB1543\n*Container:* IAAU1907074\n*ETA:* 2026-09-09\n*Incoming Quantity:* 49\n*Warehouse Allocations:* BRW (35), BRW-BB (4), BRW-SMC (10)\n\n2. *Product Code:* SRTWB1543-BL\n*Incoming:* none\n*BRW:* 20 (O/S: 0)\n\nWould you like me to escalate to purchasing team?`
- AC-13 Mirror (stock ask, no stock, incoming exists): `*Product Code:* X\n*Stock:* 0` (or
  `*Stock:* none` with no stock row) followed by that code's incoming lines in today's order;
  no "But there is INCOMING stock (ETA) for the requested products:" lead.
- AC-14 The escalation phrase and quick replies are unchanged (LOCKED, `tail/compose.py:96`);
  staff (no offer) get the same blocks without it.
- AC-15 A company searched but empty keeps `*Mocha:* no stock records for X.`

## S3 Incoming and order rows

- AC-16 Incoming rows: today's labels, order and values; single block unnumbered (AC block form).
- AC-17 Order rows: today's labels, order and values; single order:
  `*Order Number:* SMC202609-0055\n*Customer:* CUSTOMER A (PROJECT-CASH)\n*Order Date:* 08/09/2026\n*Actual Delivery Date:* 09/09/2026\n*Status:* Picked Up / In Transit\n*Pickup Time:* 19:46:00\n*Transporter:* GLORY MOTION\n*Driver:* DRIVER B\n*Lorry Plate:* PLATE-1\n*Warehouse:* BRW\n*Products:* SRTKS7547-NEW (1), TPE-9201 (1), SRTKT71SS (1), TRANSPORT (1)\n\n_Updated 10/09/2026 17:36_`
- AC-18 One named order whose number prints in the reply: no `Customer: all customers` /
  `Product: all products` / `Order: X` / `Dates: all dates` header. Broad searches and misses keep it.

## S4 Product list

- AC-19 `*List Price:*` and `*Dimensions:*` print only when the ask names price or dimensions
  (`requested_attributes`, matched the way `_names_a_base_property` matches); asked and empty
  still reads `Not defined`. Not asked: neither line.

## Constraints

- AC-20 Values never translated or reformatted; the footer drops seconds only.
- AC-21 No `·`, no en/em dash in changed reply text.
