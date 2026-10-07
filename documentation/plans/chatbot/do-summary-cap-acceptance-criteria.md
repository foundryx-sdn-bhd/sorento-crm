# UAC - DO-SUMMARY-CAP

Plan: `PLAN-do-summary-cap.md`. Amends AC-1103 of `chatbot-outstanding-report-acceptance-criteria.md`
("No line is ever elided; no '+N more' anywhere in the reply") for the By customer / By product
group only, by owner ruling of 7 Oct 2026. By location and every count line keep AC-1103 whole.
N = 20 unless the behaviour card's answer changes it.

- **AC-1601 [T]** The `*_By product_*` and `*_By customer_*` group of each block (SO and DO) prints
  at most N rows, in the order the route gave them, with no re-sorting. The first N rows are
  byte-identical to today's rendering of the same rows.
- **AC-1602 [T]** When the group holds more than N rows, exactly one trailer line follows the Nth
  row: `... and K more products (reply P for the <list>)` under By product,
  `... and K more customers (reply P for the <list>)` under By customer, K = rows beyond N.
  K = 1 prints the singular noun (`1 more product`, `1 more customer`). `<list>` is the
  offer line's own label, lower-cased: `delivery order list` under the DO block, `sales order
  list` under the SO block (R2, 7 Oct 2026; the first cut's `the full list` is never printed).
- **AC-1603 [T]** P is the position that block's list holds in the reply's own detail offer:
  SO block 1; DO block 1 when only the delivery order list is offered, 2 when both lists are
  offered. R2: the trailer's `<list>` and its P are the same words and number as the offer
  entry below it (`Reply 1 for the delivery order list.` / `2. Delivery order list`), so one
  target has one name.
- **AC-1604 [T]** A group of N rows or fewer prints exactly as before, no trailer. The existing
  goldens (`outstanding-report-both.txt`, `-so.txt`, `-do.txt`, `-customer.txt`, `-miss.txt`) do
  not change by one byte.
- **AC-1605 [T]** `*_By location_*` is never capped, whatever its length. The count lines of
  both blocks are unchanged.
- **AC-1606 [T]** The detail list (`_outstanding_detail`) is unchanged: every row, no trailer.
- **AC-1607 [T]** The chatbot lane's offer roster read off a capped reply
  (`_outstanding_offer_from_text`) equals the roster read off the same reply uncapped; the trailer
  never matches the offer patterns. The "1" turn after a capped summary runs the full list.
- **AC-1608 [T]** The trailer carries no em dash, en dash, arrow or `+N more`; the dash guard over
  the new goldens passes.
- **AC-1609 [B]** Cloud browser pass on seeded data mirroring the prod case (one dealer contact,
  one customer with more than N products on outstanding DOs): the console reply shows N product
  rows and the trailer; tapping or typing the offer returns the full DO list; nothing clipped at
  375px.
