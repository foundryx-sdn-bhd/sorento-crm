# PLAN: SO-number ask answers about that SO only (SO-NUMBER-ASK)

Status: in progress, size M (owner chose option (b) on PR #1435: fix the miss AND add the SO
lookup). No migration, no new route, no new MCP tool, no new reveal key.
UAC: `so-number-ask-acceptance-criteria.md` (alongside).

## Bug

A linked dealer asks `status of SO422056` (a real HANLIM SO, closed, [A/C III]). The bot
correctly skips the period question, then sends a 20-row DO dump across all 12 linked
customers and closes with "I could not find SO422056." (tester note on PR #1433, "E3 SO
number").

## Root cause

1. Nothing in the chatbot can place an SO number. The resolver's order probe matches
   `orders.order_number` only (the DO book, `app/services/entity_resolver.py:976`
   `_probe_customer_order`), so `SO422056` comes back in `unresolved_tokens`.
2. For a customer-scoped contact the engine then replaces the resolver's not-found exit with the
   contact's linked customers: `engine.py:4197` calls `_scoped_compatible` (`engine.py:1534`),
   which appends one `scope: True` row per link, and `_pass_scope_gate` (`engine.py:1564`)
   turns `_exit_kind: not_found` into `continue`.
3. `turn_runtime._answered_unfiltered` (`turn_runtime.py:3696`) is the guard that rewrites "every
   subject was an unplaced token" into a miss. It saw the uuid'd scope rows as placed subjects,
   so the orders list (`crm_order_management_orders_list`, hard-capped at 20 rows) ran on the
   links alone and its rows reached the reply, followed by compose's unplaced line
   (`turn/compose.py:516`).

## Fix, part 1: the miss (done, `528f41da`)

- `turn_runtime._answered_unfiltered`: scope rows are not typed subjects. A dealer's own customer
  word never reaches this as unplaced (answered from the links, never sent to the resolver,
  AC-CS-12), so the rule cannot turn a real own-customer ask into a miss.
- `answer_bridge.answer_for`: on a miss that has unplaced words, the scope rows leave the miss
  gate, so no Customer/Product/Dates header or "customer: <link> (+11 more)" bullet is printed
  above the miss line.
- Reply on a typo / unknown SO: `Couldn't find: "SO422056" (order). Would you like me to escalate
  to customer service team?` (production's own miss sentence and offer).

## Fix, part 2: the SO lookup

Owner answers to behaviour card v2 (PR #1435, 2 Oct 2026):

| Q | Ruling |
|---|--------|
| 1 | Delivery line is words only: `not delivered yet` / `partly delivered` / `fully delivered`. No unit or line counts. |
| 2 | An SO that exists but is not under the contact's account gets the scope refusal (`Sorry, that isn't under your account. I can only check on ...`), not the miss line. |
| 3 | Cancelled: header + `❗ Cancelled`, no delivery line. Closed with less delivered than ordered: `Closed`, delivery `partly delivered` (same words-only rule). |
| 4 | Several SO numbers: one card per found SO, plus one miss line for the rest. |
| 5 | Gated by the existing `sales_orders.outstanding` reveal key. |
| 6 | (2 Oct, after the tester pass) The "isn't under your account" sentence names the linked customers the way #1433's DO header does: `HANLIM TRADING SDN BHD (6 accounts)`, further families `and N more`. Same rule, not a second one: `ledger_family.family_words`, copied byte for byte from #1433's branch so the two merge cleanly, used by `contact_customer_scope._refusal_for` (every scoped refusal, not only the SO one). |
| 7 | (2 Oct, later) A customer company is named by its GROUP NAME ONLY: `HANLIM TRADING SDN BHD`, no `(6 accounts)` count, no `and N more`; several groups are each named, `A, B and C`. Applied in `ledger_family.family_words` (the only place this branch prints grouped customers; main's roster `turn/narrow.py` and header `turn/compose.py` already print `ledger_family_label` with no count). Supersedes the count half of row 6. |

Data facts (crew, dev DB read-only): the SO->DO link is empty (0 of 37,996 `orders.sales_order_id`,
0 of 99,335 `order_lines.sales_order_line_id`), so no DO numbers are listed. Delivery comes from
`sales_order_lines.qty_ordered` / `qty_delivered`. Status mix: closed 147,011 / cancelled 3,401 /
open 2,372.

Card:

```
*SO421624* - HANLIM TRADING SDN BHD [A/C I]
Ordered 15 Sep 2026 - Open
Delivery: partly delivered
```

Design (simplest thing that works):

- `turn_runtime` (tool runner, order domain): SO-shaped words (`SO` + digits) that the resolver
  could not place, on a turn that placed no other typed subject, ride to `run_fetch` as
  `so_numbers` on the lane input. Those words are answered by the lane, so they leave `unplaced`
  and compose does not add its own "I could not find" line for them.
- `lanes/business/run_fetch`: an `so_numbers` arm, ahead of the generic tool pick. It checks the
  `sales_orders.outstanding` grant first (no key: `Sales order figures are not enabled for your
  account.`), then reads through `app/services/chatbot/so_status.py` on the engine's own
  per-contact scoped session (the precedent `resolve_warehouse_token` / `outstanding_customer_echo`
  set: company scope applies, so another company's SO is simply not found).
- In scope = the SO's `customer_id` is one of the contact's links, or it has no `customer_id` and
  its `debtor_code` is a linked customer's code. A contact with no enforced scope (staff, unlinked)
  sees any SO of its company.
- Delivery over lines that are not `cancelled`: none delivered -> `not delivered yet`; every line
  delivered in full -> `fully delivered`; otherwise `partly delivered`. No such lines -> no
  delivery line.
- Reply = cards (in the order typed), then the refusal (if any SO was outside the account), then
  `I could not find X.` for the rest. A fixed reply: no escalate offer over a found card.

Trigger to widen: SO resolution as a real resolver kind (picker, fuzzy did-you-mean, DO list per
SO) once `orders.sales_order_id` is actually populated by the ingest.

## Fix, part 3: the SO list ("all my sales orders", owner option (2), 2 Oct 2026)

Owner: "when I ask for SO, I genuinely want to see SO"; the outstanding report is triggered
only by the word "outstanding". Behaviour card rulings:

| Q | Ruling |
|---|--------|
| 1 | A period, the same as the DO ask: no period asks "Which period?" (This month / Last month, words to type); at most 31 days, rolling. |
| 2 | Newest order date first, every status; a cancelled row carries the marker (`SO418652 - 27 Aug 2026 - ❗ Cancelled`). |
| 3 | The company named once in the header, group name only; when the customers span groups each row carries its group name after the date. |
| 4 | Gated by `sales_orders.outstanding` (before the period question). |

Design: `turn_runtime._asks_for_so_list` marks an order ask whose document is SO (typed, or
carried on `focus.document` so a typed period answers the question), with no status word and
no subject of its own; `run_fetch`'s `so_list` arm takes the customers in scope off the gate
(the links on a scoped or `self_reference` turn), checks the key, applies
`so_status.period_reply` (the DO ask's rule from `do_ask.py`, copied byte for byte from #1433,
in a sales order list's words), and renders `so_status.list_text`. No row cap beyond the
31-day window (not ruled). Hand-test bug fixed on the way (`99f7edb3`): an SO the card answered
is dropped from the focus, so "okay how about all my sales order?" no longer names it as a miss.

## Fix, part 4: the live parser's readings (cloud pass, 3 Oct 2026)

The a4 cloud pass ran the hand test with the live parser (`gpt-5.4-mini`) and found readings the
stubbed tests did not model. The words decide in each case, at the engine's verdict seam beside
`so_list_verdict` (all in `app/services/chatbot/so_status.py`):

- `typed_so_numbers_verdict`: "status of SO421624" after the SO422056 card was parsed as
  `{raw: SO422056, current_message: true}` (copied off "Previous response") in 2 of 4 runs. The
  SO numbers the message types are its SO entities, in typed order.
- `carried_status_verdict`: "1" under the outstanding summary came back `document: ["SO"]` +
  `so_outstanding` and `turn/decide` read the document as a named one (the summary again, 3 of 3);
  "september" after the SO list's "Which period?" came back `so_outstanding` and the list never
  ran. A pick under the offer, or a period on the SO list, drops a report status and document
  its words do not hold ("outstanding", "report" or a document typed keeps them).
- `typed_words_verdict`: an SO-document ask keeps the one month ("september", "Sep 2026", this /
  last month; "may" is left to the parser) and the "outstanding" it types when the parser drops
  them (2 of 3 and 2 of 9 runs).

Open for the owner: "find all my sales order" after a September summary asked "Which period?"
in 1 of 3 runs, because the parser's `broaden_axis: all` clears the carried window.

## Tests

`sorento_crm_backend/tests/chatbot/test_so_number_ask.py`, red first, through one real
`engine.run_turn` (harness of `test_customer_scope_lane.py`).
