# UAC: CA-3 chat dataset in the bot (10 Oct 2026)

Plan: `PLAN-chat-analytics-ca3-chat-dataset-10oct.md`. Design: `DESIGN-chat-analytics-09oct.md`
section 4.2 to 4.6 and 5. Answers Q1 to Q5 of the behaviour card are marked PENDING until the
owner rules; the recommended option is written in so the tests can be red now.

Every example is synthetic (public repo): fixtures seed their own topics, contacts and asks.

## Asking

- **UAC-1** A staff contact holding the `chatbot.analytics` reveal texts "top questions this
  week" (en), "这个星期最多人问什么" (zh), "soalan paling banyak minggu ni" (ms) or "top
  questions minggu ni" (mixed). The parser reads each as a chat report ranked by topic, measure
  asks, period this week; the bot replies with the ranking, never a clarify question.
- **UAC-2** Ranking axes the bot accepts: topic, subject, product, customer, outcome, failure
  reason, week, month, day, language. Measures: asks, failed, failure rate, contacts. Anything
  else gets the catalogue line, never a guess.
- **UAC-3** (Q1 PENDING, rec a) No period named: last 30 days ending today (Malaysia), printed on
  the Period line.
- **UAC-4** (Q2 PENDING, rec a) No count named: top 10, head line says "Top 10".
- **UAC-5** Filters by name: topic ("most asked products about stock availability"), product,
  customer, outcome ("questions the bot failed on"). A name that resolves to nothing ends in
  "I couldn't find ..." , never an empty ranking that looks like data.

## Counting (same numbers as the Chat overview / Top asks screens)

- **UAC-6** Asks count every ask; failed and failure rate count turns (ask 1 of a message).
- **UAC-7** Noise is never counted. Office contacts are excluded (Q3 PENDING, rec a).
- **UAC-8** Contacts = distinct contacts in the row (count-distinct, never a sum of rows). The
  Total line is the distinct count over the whole set, not the sum of the printed rows.
- **UAC-9** Failure rate per row = failed turns / non-noise turns in that row, one decimal,
  percent. A row with 0 turns is not printed. Total line = the same ratio over the whole set.
- **UAC-10** For the same period and filters, the bot's numbers equal the screen's
  (`chat_asks` dataset run via `/reports/{key}/run`).

## Reply shape

- **UAC-11** One fact per line `Label: value`; no " · " anywhere. Head line names the unit
  ("Top 10 topics by asks"), then `Period: <from> to <to>`, then `1. <name>: <n> asks` lines,
  `and N more`, `Total: <n> asks`, coverage line last.
- **UAC-12** Coverage line = `Topics labelled: <p>% of asks (taxonomy v<n>). From bot traces,
  5 Sep 2026 on.` When p < 80 it is the FIRST line instead of the last.
- **UAC-13** Unlabelled asks rank as the row "(not labelled yet)".
- **UAC-14** No message text, contact name or phone appears in any reply; customer appears
  only as the customer account name, only for staff.

## Access

- **UAC-15** Without the `chatbot.analytics` reveal: the bot refuses in one line and no route
  call is made (lane gate); the route also answers 403 `chat_analytics_not_enabled` when
  called directly (route gate). Holding the Sales report reveal alone is not enough.
- **UAC-16** Dealer (customer-scoped contact): only asks of its own customers; may rank by
  topic, subject, product, month only; anything else gets "That breakdown is not available for
  your account."
- **UAC-17** Salesman (contact linked from `sales_agents.contact_id`): only asks of customers
  whose card agent is that agent. Staff contact with the grant and no agent link: all
  (Q5 PENDING, rec a).
- **UAC-18** (Q4 PENDING, rec a) No contact holds the grant after the migration; the owner
  grants it on the contact reveal screen ("Chat analytics").

## Failure paths (never stuck)

- **UAC-19** Period with no asks: "No asks in that period. Chat data starts 5 Sep 2026."
- **UAC-20** Period starting before 5 Sep 2026: answered from 5 Sep; Period line shows 5 Sep.
- **UAC-21** Route 422 / 5xx / timeout: one clear line (the existing technical reply), turn
  closes.
- **UAC-22** Rate limit shared with the sales report: "Too many reports in the last N minutes."

## Regression

- **UAC-23** Every existing sales `report_ask` test stays green; a call without `dataset` is
  sales, byte-identical.
- **UAC-24** Golden set: at least 10 varied real-shaped messages (en / zh / ms / mixed,
  different periods and groupings), each asserting the parser route; live-checked 3 times
  each against the provider before hand test. Parser parity script shows no regression on the
  existing sales/stock set.
