# UAC: report engine, slice 1 (top sales agent by product / brand)

Status: FINAL for slice 1 (owner answers, `report-engine-behaviour-card.md` revision 2); hand-test round 8 Oct adds AC-HT8-1 to 5.
Plan: `PLAN-report-engine.md` (section 0 records the answers).
Part 1a = route + catalogue + presenter (AC-RE-1..17, 20); part 1b = lane wiring with the shared
required-field helper of #1445 (AC-RE-18, 19), built once crew relays the helper's API.

## Journey

A contact holding the Sales report access asks on WhatsApp "top 3 salesman for Sorento brand
last month" and gets a ranked list of sales agents by delivered sales value, the header naming
the basis. A dealer gets only its own sales, by product, brand, category or month. The next
angle ("top customers for a brand") needs no new tool.

## Route and figures (part 1a)

- **AC-RE-1** A grant holder asks rank sales agents for a product code prefix, a period, top N:
  rows are sales agents by delivered amount (DO lines, DO date, Malaysia), highest first, only
  DO lines of products matching the prefix, in the period, of the contact's companies.
- **AC-RE-2** Same with a brand: only DO lines whose product's brand is that brand count.
- **AC-RE-3** Same with a category.
- **AC-RE-4** `top_n=3` returns 3 rows, `more` = the number of other ranked rows, and the total
  of the WHOLE set (not only the 3).
- **AC-RE-5** `sort=asc` ranks lowest first; an agent with no sale in the period is not listed.
- **AC-RE-6** `measure=qty` ranks by quantity (ties by amount, then name); default amount
  (ties by qty, then name).
- **AC-RE-7** DO lines with no sales order, or a sales order with no agent, are one row
  "(no agent)"; ranked rows plus `more` rows sum to the total.
- **AC-RE-8** `basis=ordered` ranks by ordered value of SO lines filed by SO date (cancelled
  orders and lines excluded, the Yearly comparison's ordered figure). No basis = delivered.
  The body names the basis, the period and every filter by name; the reply header reads
  "by delivered sales" / "by ordered sales".
- **AC-RE-9** Group by customer, product, brand, category, location, channel or month, and
  filter by customer, product, brand, category, sales agent, location or channel, all through
  the same route: "top 5 customers for brand Mocha in Q3", "which location sold most of X in
  September", "sales by month for agent JOHN this year".
- **AC-RE-10** No period -> 422 `period_required`; nothing is computed.
- **AC-RE-11** One `group_by` and no `top_n` -> 422 `top_n_required`. No `group_by` = the total
  alone (number shape), no `top_n` needed.
- **AC-RE-12** A contact without the reveal grant `sales_orders.sales_report` -> 403
  `sales_report_not_enabled`; an unknown contact the same.
- **AC-RE-13** A customer-linked contact (dealer) may group and filter by product, brand,
  category, month only; anything else (sales agent, customer, location, channel) -> 403
  `report_dimension_not_allowed`, wording "That breakdown is not available for your account.".
- **AC-RE-14** A dealer's figures are only its linked customers'; naming another customer ->
  403 `customer_not_permitted` (existing wording).
- **AC-RE-15** A grant holder that is not customer-linked (staff, or a contact with no links)
  may use every dimension, sales agent included (owner Q1).
- **AC-RE-16** Figures come only from the contact's companies.
- **AC-RE-17** An unknown measure / dimension / filter / basis -> 422 with the allowed list;
  an empty resolved filter list -> 422 (never read as "no filter").
- **AC-RE-20** Parity: on the same seed, delivered amounts per agent / customer / product equal
  `GET /order-management/sales-report?group_by=...` to the sen. API key only; 10 asks per
  contact per 10 minutes.

## Lane (part 1b)

- **AC-RE-18** An ask with no period gets the shared helper's period question; the answer runs
  the same ask. An ask to rank with no number gets "how many?" through the same helper.
- **AC-RE-19** The parser maps "salesman / sales agent / SA / rep", "brand", "category" onto
  `group_by`, "ordered" / "delivered" onto the basis, and a word outside the catalogue ("by
  colour") gets the list of what sales can be ranked by.

## Hand-test round, 8 Oct 2026 (crew ruling (b) on the owner's Chinese and dealer turns)

The owner's ask: the salesman angle gives the salesman ranking, and the dealer angle gives the dealer ranking, in English AND Chinese.

- AC-HT8-1. A ranking asked while an earlier sales summary's drill-down offer is still open is a NEW ask. The parser names
  `order_status: "sales_ranking"`, and `decide._subject_reading` reads that key and returns NEW_ASK. It never refines
  the summary's subject or product. Any other order_status under the same offer keeps today's reading.
  Example: "cwc7606今年卖了多少个？" then "我们哪一位业务员卖得最多srtwc286-SH" ranks sales agents for SRTWC286-SH.
- AC-HT8-2. A dealer or customer named as the ranked noun ranks by customer, with no channel filter:
  - "which dealer sold the most X";
  - "哪一位经销商卖得最多X";
  - "多少个经销商卖这个产品" (on the standing product).
- AC-HT8-3. A salesman named as the ranked noun ranks by sales agent: "哪一位业务员卖得最多X", "which salesman sold the most X".
- AC-HT8-4. A sales_ranking verdict sets `domain_in_message: true`. The DOMAIN IN MESSAGE words include "sold the most",
  "卖得最多" and "排名". The bare word "top" is NOT a domain word: a bare "top 20" or "top 5" refines a held top selling list or
  order list and must not start a new ask (review round 2, apply.py:1382/1494 and :1211/:1165).
- AC-HT8-1 also covers the anaphora row: a ranking verdict with a backward reference under the open offer is still a new ask.
- AC-HT8-5. The prompt change ships as a new unlabelled version, picked in the console. The `production` label does not move.
