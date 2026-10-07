# PLAN - DO-SUMMARY-CAP: the outstanding summary's By product / By customer block stops at the top N rows

Status: small fix track (one presenter function and its goldens, no migration, no auth change, no new ingest surface, no screen change). Lane started 7 Oct 2026 from origin/main e9aa88b2. Behaviour card answered: owner ruled N = 20, trailer wording (a), singular for K = 1, real offer position, same cap on SO and DO blocks. Red-first, reviewer PASS and security-reviewer PASS on the final head 742f8d9e (origin/main e4158ab5 merged, #1515 + #1502 in), console pass PASS with the real parser on that head. R2 (7 Oct 2026, owner hand test on the test copy): the trailer named the list the offer line names (test b3e04d96, fix ccc4983d). R3 (7 Oct 2026, owner ruling on ask a69045557): the trailer counts only and the offer line alone points, reply 1 = the detail list as on main (see "R3: the trailer counts only"; red test 8cd3f35c4 before the fix). Same PR #1518.
Issue: crew lane DO-SUMMARY-CAP (owner ruling 7 Oct 2026 07:10 MYT, Option 1 from the #1515 plan's "Owner question"). Related: DO-LIST-REPLY-LABEL #1515 (the quick-reply label answers the pending detail offer).
UAC: `do-summary-cap-acceptance-criteria.md`
Classification: MCP presenter, `sorento_crm_mcp/sorento_crm_mcp/presenters.py` (pure string rendering). No prompt change, no engine change, no route change.

## Symptom (prod)

Contact 551644114, turn 11 (6 Oct 2026): the dealer asked for their delivery order outstanding.
The "Delivery order outstanding" summary's `*_By product_*` block printed 300+ lines in one
WhatsApp message, most of them 1-unit rows (`<code>: 1 (O/S: 1)`). The counts above it and
the `Reply 1 for the delivery order list.` line below it were correct; the message was simply
unreadable on a phone.

## Cause (file:line)

`presenters.py:2067` `_outstanding_group` prints one line per row, "in the order given", for
all three groups (By location, By customer, By product). The route
(`outstanding_report_service.py:71` `_rank`) already orders every group outstanding desc, total
desc, name asc (R10), so the top of the list is the part worth reading, but nothing stops the
tail. AC-1103 of the 13 Sep lane pinned "no line is ever elided" when the largest group the
owner had seen was 14 customers; a customer-subject report over a dealer's whole range has
hundreds of products.

## Measured on the dev copy (crew, read-only, 7 Oct 2026; labels masked, never real names)

The sandbox cannot reach the dev database (cloud-lanes.md), so the crew ran the query below
on the shared dev copy. Population: the route's own (`_fill_do`, outstanding_report_service.py:
476-487: `orders` inner-joined to `order_lines` and `products`, `deleted_at IS NULL`, and
`_outstanding_clause`, order_service.py:95-108, over the statuses `_delivered_status_ids`
names: `delivered` AND `completed`, order_service.py:68).

| Group | Rows per subject | p50 | p90 | max | subjects over 20 |
|---|---|---|---|---|---|
| DO By product (customer subject) | 829 customers | 4 | 30 | 365 | 135 (16%) |
| SO By product (customer subject) | 538 customers | 5 | 37 | 224 | 107 (20%) |
| DO By customer (product subject) | 2,758 products | 2 | 9 | 197 | 48 |
| SO By customer (product subject) | 1,719 products | 2 | 10 | 202 | 75 |

Whole range: 5,446 outstanding DOs over 2,758 products and 829 customers; 2,367 open SOs
over 1,719 products and 538 customers. Largest customer-subject DO groups (label / DOs /
distinct products): C41795c 545 / 281, C4ffc6a 411 / 249, C4db13e 88 / 218, C1e1dbd 72 / 365,
C08af3e 77 / 106. Largest SO: C94d80e 85 / 190, Cce0c1b 30 / 224, C895c6f 23 / 129.

So N = 20 caps about one customer-subject ask in six and leaves the median (4 to 5 rows)
untouched. C1e1dbd's DO summary goes from 365 product lines to 20 plus
`... and 345 more products`.

```sql
-- products per customer on outstanding DOs, the route's population
SELECT c.customer_name, COUNT(DISTINCT l.product_id) AS products_in_by_product
FROM orders o
JOIN order_lines l ON l.order_id = o.id
JOIN products p ON p.id = l.product_id
LEFT JOIN customers c ON c.id = o.customer_id
WHERE o.deleted_at IS NULL
  AND (o.order_status_id IS NULL
       OR o.order_status_id NOT IN (SELECT id FROM order_statuses WHERE lower(status_code) IN ('delivered', 'completed'))
       OR o.actual_delivery_date IS NULL)
GROUP BY c.customer_name
ORDER BY products_in_by_product DESC;
```

## Fix (presenter only, owner ruling Option 1)

- `*_By product_*` and `*_By customer_*` (the SECOND group of each block, SO block and DO block
  alike) print at most N rows (N = 20 unless the card's answer says otherwise), in the order the
  route gave them, then ONE trailer line.
- The counts (Delivery orders / DO qty / Delivered / Outstanding / date range, and the SO
  equivalents) and the `*_By location_*` group print whole, as today.
- Trailer wording (exact bytes, the owner's):
  - By product: `... and K more products`
  - By customer: `... and K more customers`
  - R3 (7 Oct 2026): a count only. The first cut said `(reply P for the full list)`, R2
    `(reply P for the delivery order list)`; see "R3: the trailer counts only".
  - K = rows beyond N. P = the position that block's list holds in the detail offer printed at
    the end of the same reply: SO block = 1; DO block = 1 when only the DO list is offered, 2 when
    both lists are offered (the offer is `1. Sales order list / 2. Delivery order list / 3. Both
    lists`, R14). A trailer that said "reply 1" under a DO block of a both-scope reply would run
    the SO list.
- N rows or fewer: the block prints exactly as today, byte for byte. The existing goldens
  (3 customers, 14 customers, 3 products) do not move.
- The detail list reached by that reply (`_outstanding_detail`, presenters.py:2444) is
  unchanged: it is the DO (or SO) row list, every row, as before.
- "reply P" lands on the EXISTING pending offer. The chatbot lane reads the offer off the
  rendered text with two anchored patterns (`lanes/business/fetch.py:2062-2067`:
  `^\d+\.\s*Delivery order list\s*$` and `^Reply \d+ for the delivery order list\.$`, SO alike).
  The trailer starts with `...`, says `reply` in lower case inside brackets and never stands as
  one of those lines, so the stored roster is identical with or without the cap, "1" (or the
  label, once #1515 merges) resolves exactly as today, and no offer code is added or copied.
  Nothing in #1515's diff (`turn/decide.py`) is touched here.

## R2: one target, one name (owner hand test, 7 Oct 2026)

The owner's hand test on the test copy (`chatbot.turns` e4895b28, a DO-only customer-subject
summary over HANLIM TRADING, 40 products, Order date 01/09/2026 to 30/09/2026) printed

```
... and 20 more products (reply 1 for the full list)

Reply 1 for the delivery order list.
```

and the owner asked why "reply 1" is the full list and also the delivery order list. The two
lines point at ONE reply (the offer roster has one entry, position 1; `presenters.py`
`_outstanding_report` prints the single sentence only when `len(offer) == 1`, and positions
can never collide: SO 1, DO `len(offer) + 1`). They named that reply with two different
phrases, and two names read as two offers.

Ruling (crew-relayed, option (a)): the trailer names its block's list with the offer line's
own words, lower-cased. `(reply P for the delivery order list)` under a DO block, `(reply P
for the sales order list)` under an SO block; `the full list` is never printed. Positions,
the offer lines, the roster the lane reads (`fetch._outstanding_offer_from_text`, anchored
on `^Reply \d+ for the ...\.$` / `^\d+\. ... list$`, which the bracketed lower-case trailer
never matches) and the detail list are unchanged. The chat-language catalog carries eight
forms (noun, singular, list) instead of four; a `{list}` token was not used because the
localizer copies token values verbatim (`label_catalog.py` `Localizer._text`), which would
leave the English list name inside a Malay or Chinese line.

Options not taken: (b) dropping the bracket (loses the card's "real offer position" ruling);
(c) a separate "full By product list" offer at its own position (a new pending kind in the
lane's roster and the engine seam, against this plan's "the trailer is a pointer at that
offer, not a second one").

## R3: the trailer counts only (owner ruling, 7 Oct 2026, ask a69045557)

The R2 copy printed a product-subject DO summary ending `... and 50 more customers (reply 1
for the delivery order list)` then `Reply 1 for the delivery order list.`. The owner read the
bracket as a second offer ("reply 1 to see more and reply 2 for DO list?"). The closed card
had no "see more" option, so one ask went up with a "Next 20" paging proposal (plan section
and red tests written, parked, never merged). The owner ruled against paging, verbatim:
"okay i agree with your DO summary, just ends with ...and 50 more customers, for #1518 Q1,
the reply 1 will see the detailed list just like in main".

So: the trailer is `... and K more products` / `... and K more customers` (singular for
K = 1), no bracket, no pointer. The closing offer line is unchanged from before the cap and
is the one pointer; `1` is the detail list exactly as on main. The block renderers no longer
carry an offer position. The catalog carries the four count-only forms. Rendered, the
owner's two cases:

```
SRTWT2634-BL: 8 (O/S: 8)
... and 20 more products

Reply 1 for the delivery order list.
```

```
<20 customer rows>
... and 50 more customers

Reply 1 for the delivery order list.
```

Both blocks: each capped group ends with its count line; the numbered offer
(`1. Sales order list / 2. Delivery order list / 3. Both lists`) is unchanged.

## Co-ordination with #1515 (DO-LIST-REPLY-LABEL)

#1515 is open (head 796158ae) and changes `decide.py` only. The two diffs do not overlap. If
#1515 merges first this lane merges origin/main before crew-done as usual; otherwise the trailer
is wired to the offer seam that already exists on main (the regex roster above), and #1515's
label fix applies to it unchanged when it lands. Noted in the PR body.

## Edge cases (pinned by tests)

| Case | Behaviour |
|---|---|
| exactly N rows | N rows, no trailer |
| N + 1 rows | N rows, `... and 1 more product` (singular, pending the card's answer) |
| 0 rows in the second group | heading with no rows, as today (the route never sends this for a non-miss block) |
| a customer with one product | one row, no trailer |
| By location with more than N rows | whole, never capped |
| both scopes offered, DO block capped | `reply 2` |
| DO only offered, DO block capped | `reply 1` |
| SO block capped (either offer shape) | `reply 1` |
| miss block (count 0) | title + miss line, nothing to cap |
| "1" after a capped summary | the full DO list, same as today (offer roster unchanged) |

## Tests (red first)

`sorento_crm_mcp/tests/test_presenters_outstanding_cap.py` (new file, red-first); goldens
`outstanding-report-do-capped.txt` (customer subject, 312 products: 20 volume rows then 292 small
ones, the prod shape) and `outstanding-report-both-capped.txt` (both scopes, SO trailer `reply 1`,
DO trailer `reply 2`) under `tests/fixtures/outstanding/` with their
`documentation/plans/chatbot/samples/` mirrors. `test_fourteen_customers_render_without_elision`
stays as is (14 < N). Backend side: `tests/chatbot/test_do_summary_cap_offer.py` locks the offer
roster read off a capped reply, and the chat-language catalog test pins the trailer's ms / zh
forms (reviewer should-fix: a fixed sentence the report prints must be in `label_catalog.py` or a
Malay or Chinese dealer reads one English line inside a translated summary).

## Not built here

A cap on the detail list itself. A 300-product dealer's "full list" is one row per outstanding
DO (8 lines each), so that reply can be long too; the owner's ruling kept the list unchanged and
the card names this as a risk, not a question.
