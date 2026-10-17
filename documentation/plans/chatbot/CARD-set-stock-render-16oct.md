# Behaviour card: a stock answer for a product SET reads like the product-code answer (SET-STOCK-RENDER)

Status: v2 FILED for the owner's OK (16 Oct 2026). v1 (compact block for every contact, brand
lines, grouping) is SUPERSEDED by the owner's direction below. Size M, LEAD pattern. Plan:
`PLAN-set-stock-render-16oct.md` (same folder). Answer/presenter layer: the parser and grounding
stay with CHAT-ADVISOR-SPIKE (#1720, #1737); the misspelt-word binding stays with
CHAT-GROUNDING-TYPO.

Owner, 16 Oct ~14:00: "i am just saying the answer of the matching spec looks wrong".

Owner direction on v1, 16 Oct ~17:40 (verbatim): "btw you know in some of our answer is incorrect
ah, for example, when we ask like any undermount wash basin has stock, it gives nonsense like
sorento wash basin how much, cabana how much, then when i say sorento, it gives nonsense like
100mm how much, 120mm how much, that is nonsense, our answer should be similar to how we normally
answer when the user provide product code, it should be unified".

Crew reading of it (binding): v1 Q1 = (a) in the SAME format as the product-code stock answer,
same composer; v1 Q3 = NO brand-count or size-bucket lines; v1 Q2 (a) (drop on-hand 0 locations
in a "with stock" set), Q4 (a) (no cap rule kept), Q5 (a) (same code in two companies shown in
full) as recommended.

Bindings from before: empty locations are NOT hidden by default (owner 16 Oct); availability is
on-hand; one fact per line `Label: value`, no " · "; no IDs; truthful; the parser decides
meaning; staff see numbers, dealers see got-stock.

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


Also measured for v2 (main c11f4dcd1):

10. The product-code answer and the set answer already share one row loop (`_item_line`,
    fetch.py:3382-3429, loop :3487-3496) over the presenter of the contact's stock mode. A set
    differs only in: the set header replaces the intro "Stock details found for the requested
    products." (presenters.py:81, cleared at fetch.py:3333-3337); over 50 with a breakdown the
    rows are withheld (fetch.py:3625-3628); `product_ids` are one per family (item 6) and cut to
    50 (fetch.py:1234-1241).
11. Breakdown lines in a set reply come from: (a) over 50, any leg (stock, incoming, promotion,
    certificate, product): `_set_breakdown` (product_predicate_service.py:1248-1252, key order
    brand, then class, then the registry keys :789-791) printed by `build_set_header`
    (answer.py:2986-2992); (b) a misspelt or unknown word: `_unknown_value_members` always breaks
    down (references.py:1797-1840, :3331-3343) printed by `unknown_values_reply`
    (answer.py:2895); (c) nothing qualifies but more than 50 described products
    (answer.py:2842); (d) no described product at all, `near_miss_reply` (answer.py:2859).
12. The owner's case on the dev copy (staff contact, 16 Oct): 08:21Z "stock for sorento
    undermoun wash basin" -> "Here's what you want: Sorento wash basins with stock (276)" then 36
    lines "• 140 mm wash basins: 25" ... and "Couldn't find: "undermoun"." (path a; the
    misspelt "undermoun" dropped the spec, which is CHAT-GROUNDING-TYPO's fix). 04:03Z "i mean
    any stock for sorento undrmount basin" -> "Here's what you want: Sorento undrmount wash
    basins" then 46 lines "• 140 mm wash basins: 42" ... (path b).
13. A brand word after a set ("sorento") narrows the carried set: `with_brand_from_offer`
    (turn_runtime.py:4373, called at engine.py:4722) -> `set_brand_chosen` (turn/apply.py:3677-3707)
    -> `page_the_set` (turn_runtime.py:4422) recounts with the brand and no breakdown and lists
    through the same header path. UNVERIFIED that it fires after a set that was listed in full
    (the tester pins it).

## The rule (v2)

- R1. A set stock answer prints exactly what the product-code answer prints for the same
  products, from the same row loop and the contact's own stock mode. Detailed: one block per
  location (Company only when the answer spans two companies, as today). Compact: one block per
  product (Code, Total, locations). Availability: the numbered codes, then "How many units do you
  need?", or the "✅ Got stock." lines once a quantity is given. The ONLY difference from the
  code answer is the first line: the set header with the product count, in place of "Stock
  details found for the requested products.".
- R2. No breakdown lines in a set answer (no "• Sorento wash basins: 9", no "• 140 mm wash
  basins: 42"). Over 50 products: the first 50 are listed and the header says so with the
  existing wording, "(276, showing 1 to 50)" (answer.py:2977). The customer narrows by saying
  more ("sorento", "white", "under counter").
- R3. A set asked "with stock" drops locations with on hand 0. Every other answer, the code
  answer included, keeps the contact's policy as today.
- R4. The same code in two companies is listed for both companies, as the code answer does
  (gate.py:677-698): one product in the count, both companies' rows.
- R5. A brand word after a set ("sorento") answers the narrowed set the same way (R1 to R4).
- Unchanged: the `Company:` line (the owning company, printed by the code answer too), the
  footer, the row order (product code, then warehouse: inventory_service.py:1051-1056).

## Real examples, unified form (dev copy, read-only)

Example 1, staff (Mr Loo; his policy: detailed, hides empty locations), 16 Oct 04:02Z, "any stock
for undermount basin". Today: header "(20)" and 33 location blocks; the Sorento-company Mocha
twins are missing. Unified: the same header and blocks as today, plus the two twin blocks (R4):

```
Here's what you want: under counter wash basins with stock (20)

1. *Company:* Sorento
*Product Code:* BRBC22102W
*Warehouse:* BUKIT RAJA
*System Location:* BRW
*Quantity On Hand:* 2
*Outstanding:* 0

2. *Company:* Sorento
*Product Code:* BRBC22102W
*Warehouse:* WH3 (PSM)
*System Location:* WH3
*Quantity On Hand:* 1
*Outstanding:* 0
...
*Company:* Mocha / *Product Code:* MWB8051 / MOCHA-WH / *Quantity On Hand:* 10
*Company:* Sorento / *Product Code:* MWB8051 / BUKIT RAJA / BRW-BB / *Quantity On Hand:* 2   (new)
...
*Company:* Sorento / *Product Code:* MWB8054 / BUKIT RAJA / BRW / *Quantity On Hand:* 1     (new)
...
_Data last updated: 21/09/2026 11:24:43_
```
(The "/" lines are abbreviated HERE only; the reply prints each field on its own line.)

Example 1, follow-up "sorento" (R5). The narrowed set, as the dev copy already answered the direct
ask "stock for sorento undermount waqsah bsin" (08:20Z):

```
Here's what you want: Sorento under counter wash basins with stock (6)

1. *Product Code:* SRTWB1610
*Warehouse:* BUKIT RAJA
*System Location:* BRW
*Quantity On Hand:* 41
*Outstanding:* 0

2. *Product Code:* SRTWB1610
*Warehouse:* BUKIT RAJA
*System Location:* BRW-BB
*Quantity On Hand:* 5
*Outstanding:* 5
...
```
No "• 140 mm wash basins" lines, ever.

Example 2, staff, the owner's 08:21Z "stock for sorento undermoun wash basin" (spec lost to the
typo). Today: "(276)" + 36 size lines. Unified (R2):

```
Here's what you want: Sorento wash basins with stock (276, showing 1 to 50)

1. *Product Code:* ...
(the location blocks of the first 50 products: 65 blocks with on hand > 0 on the dev copy,
about 390 lines)

Couldn't find: "undermoun".

_Data last updated: ..._
```
Once CHAT-GROUNDING-TYPO binds "undermoun" to under counter, the same ask is example 1's
follow-up (6 products).

Example 3, the owner's 04:03Z "i mean any stock for sorento undrmount basin". Today: "Sorento
undrmount wash basins" + 46 size lines. Built (Q2 (b2), see Q2): the miss without the lines:

```
Here's what you want: Sorento undrmount wash basins

Couldn't find: "undrmount".
```
(plus the escalate offer where today's reply has one; a staff profile gets none).

Example 4, Zilin (default policy: detailed, empty locations shown), 04:01Z "any undermunt basin
has stock?". Today: "(10)" over 34 blocks, 15 of them on hand 0. Unified (R3): "(10)" over the
19 blocks with stock (SRTWB7518 keeps 1 of 7: MWH-AM 1; SRTWB7109 keeps 3 of 9).

Example 5, dealer on "Availability only". Corrected 16 Oct after the tester's engine run: the
dealer does not see the set header at all. The engine's own quantity ask (STOCK-QTY-PARTIAL-FILL,
`turn/task.py` after_reply) answers the set exactly as it answers the same codes asked by code:
"How many units for each?" then the numbered codes, no stock number. The SSR-3 engine test pins
"dealer set reply == dealer code reply" and is GREEN on main, so R1 already holds for dealers and
nothing changes for them. After a quantity, the code answer's verdict lines follow
(`_availability_line`, presenters.py:1545).

## Edge cases

- A dealer limited to a site pool sees only products with on hand at its allowed locations
  (product_predicate_service.py:448), so the count can be smaller than staff's.
- "show me 5": the named count picks 5 (fetch.py:3636-3637), as today.
- "more" after "(276, showing 1 to 50)": the next page, "showing 51 to 100" (the W4 page,
  fetch.py:3621-3623), as today.
- A set with no stock leg ("any undermount basin?"): product rows, not touched by R3.
- WhatsApp length: example 2 is about 8,000 characters. Whether Respond.io splits long texts is
  UNVERIFIED.

## Questions for the owner's OK (3)

Q1. Over 50 products. (a) list the first 50 (the existing limit), header "(276, showing 1 to
50)"; (b) list the first 10, header "(276, showing 1 to 10)". Recommendation: (a), it keeps your
28 Sep "no cap" and the existing limit; pick (b) if about 8,000 characters in one reply is too
long.

Q2. Which replies lose the breakdown lines. (a) the over-50 set answer, every leg (stock,
incoming, promotion, certificate, product), at the one shared place (fetch.py:3625-3628,
references.py:3456); (b) (a) plus the misspelt-word reply (example 3: the set without the word,
listed, then "Couldn't find"); (c) (b) plus the two "nothing matched" replies (answer.py:2842,
:2859). Recommendation: (b), the two replies you hit; (c) changes "nothing matched" messages,
which deserve their own look.
BUILT as (b2), captain 16 Oct, on the coder's conflict report: the misspelt-word reply loses its
breakdown lines but keeps its miss shape (example 3), with no rows. Listing the set without the
word would answer up to 50 of 276 basins nobody asked for and drop the escalate offer the miss
carries; the misspelt word itself is CHAT-GROUNDING-TYPO's fix. Say so if you want the listing.

Q3. Numbering in detailed mode. The header counts products (20) while a detailed answer numbers
each location block (1 to 35), exactly as the code answer does. (a) keep it (unified with the
code answer); (b) say "(20 products)" in the header. Recommendation: (a), the code answer
numbers the same way and nothing else changes.
