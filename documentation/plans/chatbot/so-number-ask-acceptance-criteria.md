# UAC: SO-number ask (SO-NUMBER-ASK)

Plan: `PLAN-so-number-ask.md`. Every case runs as a chat turn (Chatbot Console, never WhatsApp).

## Miss (no data from anyone else)

- AC-SO-01: A linked dealer asks `status of SO999999` (no such SO). Reply is one miss line naming
  `SO999999`. No DO rows, no customer names, no Customer/Product/Dates header.
- AC-SO-02: An order ask naming nothing (`my orders`) from a linked dealer still runs on the links
  (AC-CS-30 unchanged).

## Found, in scope (contact holds `sales_orders.outstanding`)

- AC-SO-03: `status of SO421624` (open, partly delivered, own link) replies exactly:
  `*SO421624* - <debtor name>` / `Ordered 15 Sep 2026 - Open` / `Delivery: partly delivered`.
- AC-SO-04: an open SO with nothing delivered reads `Delivery: not delivered yet`.
- AC-SO-05: a closed SO with every line delivered reads `- Closed` / `Delivery: fully delivered`.
- AC-SO-06: a closed SO with less delivered than ordered reads `- Closed` / `Delivery: partly delivered`.
- AC-SO-07: a cancelled SO reads `- ❗ Cancelled` and has no delivery line.
- AC-SO-08: no DO list is printed (the SO->DO link is empty in the data).
- AC-SO-09: no escalate offer and no "I could not find" under a found card.

## Out of scope / several / no key

- AC-SO-10: an SO that exists on a customer the dealer is not linked to replies with the scope
  refusal `Sorry, that isn't under your account. I can only check on <links>.` and nothing about
  that SO (no status, no customer name). `<links>` names each group by its group name
  only: `HANLIM TRADING SDN BHD`, several groups `A, B and C`; never a `(6 accounts)` count,
  never `and N more` (owner rule, 2 Oct 2026).
- AC-SO-11: an SO of another company is not found (AC-SO-01's miss line).
- AC-SO-12: `SO421624 SO999999`: one card for SO421624, then `I could not find SO999999.`
- AC-SO-13: a contact without `sales_orders.outstanding` gets `Sales order figures are not
  enabled for your account.` and no SO data.
- AC-SO-14: a contact with no enforced scope (staff) sees any SO of its own company.

## SO list ("all my sales orders" / "my SOs")

- AC-SO-20: no period -> `Which period for <group>?` with This month / Last month; nothing listed.
- AC-SO-21: a typed period answering that question runs the list for it.
- AC-SO-22: `Sales orders for <group>, <d Mon yyyy> to <d Mon yyyy>:` then one line per SO,
  newest first, every status: `SO422095 - 21 Sep 2026 - Closed - fully delivered`.
- AC-SO-23: a cancelled row reads `SO418652 - 27 Aug 2026 - ❗ Cancelled` (no delivery word).
- AC-SO-24: customers spanning groups: every group named once in the header, each row carries
  its group after the date.
- AC-SO-25: more than 31 days is refused with the period's last and first months suggested.
- AC-SO-26: an empty period reads `No sales orders for <group> from <d> to <d>.`
- AC-SO-27: without `sales_orders.outstanding`: `Sales order figures are not enabled for your account.`
- AC-SO-28: another customer's SOs never appear; "outstanding" still runs the outstanding report.
- AC-SO-29: after an SO card, "okay how about all my sales order?" never names that SO.
