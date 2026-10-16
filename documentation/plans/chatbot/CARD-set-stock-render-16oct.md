# Behaviour card: a stock answer for a product SET reads right (SET-STOCK-RENDER)

Status: FILED for owner rulings (16 Oct 2026). Size M, LEAD pattern. Plan:
`PLAN-set-stock-render-16oct.md` (same folder). Answer/presenter layer only: parser and
grounding stay with CHAT-ADVISOR-SPIKE (#1720, #1737).

Owner, 16 Oct ~14:00: "i am just saying the answer of the matching spec looks wrong".

Bindings already ruled (not asked again): empty locations are NOT hidden by default (owner
16 Oct: "no" to the global hide-zero switch); availability is on-hand; one fact per line
`Label: value`, no " · "; no IDs; truthful; the parser decides meaning; staff see numbers,
dealers see got-stock; and from 28 Sep (PLAN-attribute-first-asks.md:572-573): "no cap, but no
explanations and no repeats", "always break it down", "reuse the functions that we have",
"i don't want new layer".

## What happens today (main c11f4dcd1, every line checked)

1. The header counts PRODUCTS: `qualifying_total` is a count of distinct product families
   (product_predicate_service.py:1061), printed by `answer.build_set_header`
   (answer.py:2944, count at :2975-2979; called from fetch.py:3642-3652).
2. The rows are STOCK ROWS: in detailed mode the stock tool gives one numbered block per
   product x location (sorento_crm_mcp presenters.py:1362-1425), and the backend re-renders
   each one (fetch.py:3487-3497, `_item_line` :3382). So "(10)" sits over 34 blocks.
3. "with stock" qualifies a PRODUCT (any allowed location with on hand > 0,
   product_predicate_service.py:416-454), but the stock call then returns every location of
   those products; zero rows are dropped only when the contact's policy says hide-zero
   (inventory_service.py:870-874; dev default row is detailed, hide-zero off).
4. Per-brand counts are computed (`set_brands`, product_predicate_service.py:1033-1055) and
   never printed (comment at :1051-1052 "never said in the reply"); its inner join on Brand
   (:1037) drops products with no brand, which is every Mocha-company code (6 of 40 in this set).
5. "Company: Sorento" on Bravat codes is the product's OWNING company
   (`stamp_lookup_companies`, company_scope.py:492-600, called at inventory_service.py:1110),
   printed only when the lookup spans more than one company; stock rows carry no brand.
6. The same code in two companies counts and lists ONCE (family = parent code or own code,
   product_predicate_service.py:1001-1002, `distinct(family)` :1100): Sorento-company MOCHA
   MWB8051 (2 at BRW-BB) and MWB8054 (1 at BRW) never reach staff.
7. Long sets: up to `SET_LIST_MAX` = 50 products are listed (answer.py:2760, fetch.py:1235);
   above 50 the reply is the count plus a breakdown by the next attribute (fetch.py:3625-3628).
8. Dealer on "Availability only" with no quantity: numbered bare codes then "How many units do
   you need?" (fetch.py:3474-3486, presenters.py:121). No numbers.
9. The footer "Data last updated: 21/09/2026" is true: it is the last stock import time of the
   dev copy (inventory_service.py:1080-1094, fetch.py:3672-3677). No change proposed.

## Proposed rule (the recommendation in each question below)

- The header count = the number of product blocks under it. One block per product, never one
  per location.
- Staff (detailed or compact policy): each block is today's COMPACT block (presenters.py:1451,
  reused, no new format): `Product Code`, `Total`, then one line per location `*BRW:* 41`.
- A set the customer asked "with stock" lists, inside each block, only locations with on hand
  > 0. Every other stock answer keeps the contact's policy exactly as today.
- More than one brand: one line per brand under the header, today's breakdown line format
  (answer.py:2795, "• Bravat under counter wash basins: 9"), then the blocks grouped under a
  brand line in the same order (brand weight, then count: product_predicate_service.py:1050).
  No brand on the product: its owning company's name (Mocha). The `Company:` line goes.
- Inside a brand, highest total first.
- Dealer (availability): the same brand lines and grouping, codes only, then "How many units
  do you need?". No numbers, no per-line "got stock" (the header already says "with stock").

## Real examples (dev copy, read-only; current vs proposed)

Example 1. Zilin (dual Sorento + Mocha contact, default policy), 16 Oct 04:01Z, "any undermunt
basin has stock?". Today: header "(10)", 34 blocks, 15 with on hand 0, 241 lines, 4,709 chars.
Proposed (shown in full for the first block of each brand):

```
Here's what you want: under counter wash basins with stock (10)
• Sorento under counter wash basins: 6
• Mocha under counter wash basins: 4

*Sorento*

1. *Product Code:* SRTWB1610
*Total:* 88
*BRW:* 41
*BRW-BB:* 5
*BRW-SMC:* 2
*MWH:* 39
*MWH-IB:* 1

2. SRTWB7109 (17: BRW 3, BRW-BB 7, BRW-SMC 7)
3. SRTWB7109-RG (14), 4. SRTWB7109-GY (12), 5. SRTWB7109-BL (10), 6. SRTWB7518 (1: MWH-AM)

*Mocha*

7. *Product Code:* MWB8053
*Total:* 86
*HQ:* 28
*MOCHA-WH:* 58

8. MWB8052 (71), 9. MWB8054 (27), 10. MWB8051 (10: MOCHA-WH only; HQ 0 dropped)

_Data last updated: 21/09/2026 11:24:43_
```
(Blocks 2 to 10 are abbreviated HERE only; the reply prints each as block 1 does. About 60
lines instead of 241.)

Example 2. Mr Loo (staff, his own policy: detailed, hide-zero on), 16 Oct 04:02Z, "any stock for
undermount basin". Today: header "(20)", 33 location blocks, Bravat codes labelled "Company:
Sorento", no brand lines, 4,643 chars. Proposed head (blocks as in example 1):

```
Here's what you want: under counter wash basins with stock (20)
• Sorento under counter wash basins: 6
• Bravat under counter wash basins: 9
• Mocha under counter wash basins: 4
• Others under counter wash basins: 1
```
then *Sorento* SRTWB1610 88 ... SRTWB7518 1; *Bravat* BRBC22353W-ENG 255 (MWH 3, MWH-IB 252),
BRBC22153W 73 (discontinued, flagged as today), BRBC22102W 3 ... BRBC22449W-ENG 1; *Mocha*
MWB8053 86, MWB8052 71, MWB8054 28 (27 Mocha + 1 Sorento BRW, Q5), MWB8051 12 (10 + 2 BRW-BB,
Q5); *Others* VLDWB1610 4.

Example 3. A real contact, 9 Sep, "Can suggest close couple wc p trap with available stock"
(detailed, hide-zero off). Today: 29 location blocks for 8 products, 14 with on hand 0, 3,859
chars. Proposed: one brand (all Sorento) so no brand lines; 8 blocks, e.g.

```
1. *Product Code:* SRTWCX7604-P-RL-NEW
*Total:* 227
*BRW:* 38
*BRW-BB:* 1
*DC1-BB:* 90
*MWH:* 85
*MWH-BB:* 13
```
... 8. SRTWCX7405-RL-P-PJ (1: BRW). SRTWCX8608-P-RL keeps 1 of its 7 locations (BRW-IR 14).

Example 4. Dealer on "Availability only" (shape from fetch.py:3474-3486; no captured dev turn,
UNVERIFIED live), same set:

```
Here's what you want: under counter wash basins with stock (10)
• Sorento under counter wash basins: 6
• Mocha under counter wash basins: 4

*Sorento*
1. SRTWB1610
2. SRTWB7109
...
*Mocha*
7. MWB8053
...

How many units do you need?
```

## Edge cases

- One brand in the set: no brand lines, no brand line above the blocks (example 3).
- A product that qualifies only through an excluded location never appears: the leg already
  reads the contact's allowed locations (product_predicate_service.py:448).
- More than 50 products: unchanged (count + breakdown, fetch.py:3625-3628).
- "show me 5": the named count picks the first 5 in the new order.
- A set with no stock leg ("any undermount basin?"): not touched (product rows, not stock).
- A product-code ask ("check stock mwc76", 42 codes, compact): a different path, not touched.
- The positional pick ("the 2nd one") resolves against the same numbering the reply prints.
- WhatsApp length: today's examples are 4.6k to 4.9k chars; the proposal is about a third.
  Whether Respond.io splits long texts is UNVERIFIED.

## Questions (at most 5)

Q1. Shape of each product in a set stock answer (staff).
(a) today's compact block for every set answer whatever the policy: code, total, one line per
location; (b) one line per product `*SRTWB1610:* 88`, locations only when one code is asked
next; (c) keep per-location blocks, fix only the header ("10 products, 34 locations").
Recommendation: (a), it reuses `_stock_compact` (presenters.py:1451) as is, keeps the locations
staff pick from, and makes the count match the blocks.

Q2. Empty locations inside a "with stock" set answer.
(a) drop locations with on hand 0 inside each block, ONLY when the set was asked "with stock";
every other answer keeps the policy (your 16 Oct "no" stands for them); (b) list every location
the policy allows, zeros included. Recommendation: (a), the header promises stock and 15 of 34
blocks in example 1 say 0; it changes no policy and no other answer.

Q3. Brand summary and grouping.
(a) one brand line per brand under the header (existing breakdown line) and the blocks grouped
under a brand line; a product with no brand shows its company (Mocha); the `Company:` line goes;
(b) the brand lines only, one flat list, `*Brand:* Bravat` inside each block; (c) no brand lines,
just Brand in place of Company. Recommendation: (a), "always break it down", says each brand
once ("no repeats"), and fixes "Company: Sorento" on Bravat.

Q4. Long sets (20+ products).
(a) keep 28 Sep "no cap": every product up to 50 listed, highest total first inside each brand,
above 50 the count + breakdown as today; (b) the top 10 by total and "10 more, say a brand to
narrow". Recommendation: (a), (b) reverses your 28 Sep "no cap" ruling; the compact block plus
dropping zero rows already cuts example 1 from 241 to about 60 lines.

Q5. The same code in two companies (Sorento-company MOCHA MWB8051 2 units, MWB8054 1 unit).
(a) one block per code, the total and locations from both companies (MWB8051: 12 = MOCHA-WH 10 +
BRW-BB 2); (b) two blocks, one per company; (c) leave as today (the Sorento twin is not shown).
Recommendation: (a), one product to the customer, counted once as today, and staff see all 12.
