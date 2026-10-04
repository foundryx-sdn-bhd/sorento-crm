# PLAN: one "refer to your salesman" wording, and every such reply is a Customer ask (REFER-SALESMAN)

Status: Built, in review on PR #1386 (small fix track: no auth/RBAC change, no new ingest surface; one additive migration on `stock_asks`)

**Amended 1 Oct 2026 (`PLAN-customer-asks-refer-only-1oct.md`, owner ruling):** rows are written for refer replies ONLY (B3 `incoming` no longer), and whether a reply refers is no longer read off its text (`refer_asks.refers` is gone): the presenter stamps `refers_to_salesman` on each stock ask line, and every backend composer prints the line through `turn/refer.py`, which marks the turn. The rule now covers every contact, so ESCALATION-CONTROL's barred replies (#1406) are rows too.
Owner ruling: 30 Sep 2026, WhatsApp transcript review.
UAC: `refer-salesman-30sep-acceptance-criteria.md` alongside.

## The rule (owner, verbatim in substance)

1. Every variant of the refer line becomes exactly the sentence `Please refer to your salesman.`
   No "to proceed", no "salesperson", no name appended. The content before it stays and is
   closed as its own sentence.
2. EVERY bot reply that refers the customer to their salesman creates a row in the sales
   portal "Customer asks" view (and so on the CRM Asks tab and the to-do, which read the same
   `stock_asks` rows). The row shows what was asked and the answer.

## Every emitter today (measured, file:line on main @ 950785de)

| # | Wording the dealer reads today | Emitter | Ask row today |
|---|---|---|---|
| 1 | `<code> x <Q>: yes, we have stock, please refer to your salesman to proceed.` | `sorento_crm_mcp/sorento_crm_mcp/presenters.py:1471` (`_AVAILABILITY_TAILS["in_stock"]`) | yes (`in_stock`) |
| 2 | `<code> x <Q>: the quantity is more than what I can confirm here, please refer to your salesman.` | `presenters.py:1467-1470` (`too_big`) | yes (`too_big`) |
| 3 | `<code> x <Q>: no stock and no incoming at the moment, please refer to your salesman.` | `presenters.py:1472-1474` (`no_incoming`) | yes (`no_incoming`) |
| 4 | `<code>` / `ETA: <dates>` then `Please refer to your salesperson, <name>.` (or `... salesperson.`) | `presenters.py:932-937` (`_refer_to_salesperson`), set as `closing` at `presenters.py:1809`, printed by `app/services/chatbot/lanes/business/fetch.py:3037` | **no** |
| 5 | `Here's what you want: ... But no incoming matched these.` + `Please refer to your salesman.` (the purchasing offer stripped) | `app/services/chatbot/dealer_stock.py:38-56` (`without_escalation`) via `engine.py:5027-5032` (`_dealer_refers_to_salesman`, dealer stock OR incoming ask) | **no** |
| 6 | `I couldn't find <typed>.` + `Please refer to your salesman.` (stock miss, offer stripped) | same as 5 | **no** |
| 7 | `Please refer to your salesman.` alone (a dealer's "no" to a did-you-mean) | `app/services/chatbot/turn/apply.py:698-706` (`trace.task_question = REFER_TO_SALESMAN`), answered at `engine.py:4296-4298` | **no** |

The constant `REFER_TO_SALESMAN` lives at `app/services/chatbot/turn/task.py:42` and already reads
`Please refer to your salesman.`. Rows 1-3 are the only wordings that vary from it; row 4 is
the "salesperson, <name>" variant. No LLM prompt emits a refer line (grep over
`app/services/chatbot*`, `app/data`, `chatbot_reply_copy.py`: the only salesperson copy is the
commercial HANDOVER line "X looks after your account", which is not a refer and is out of scope).

Rows are created in ONE place: `app/services/stock_ask_service.py:105-209` (`after_answered_turn`),
called from `engine.py:5489-5504` (`_after_stock_ask_turn`) after the turn row is closed, fed
by `engine.py:7074-7091` (`_stock_ask_answered_entries`: the fetch envelopes'
`stock_availability` entries with a `branch` and a `requested_qty`). Only rows 1-3 (and the
non-refer `incoming` B3 line) reach it.

## Design (simplest thing that works)

### 1. Wording

- `presenters._AVAILABILITY_TAILS`: `"yes, we have stock. Please refer to your salesman."`,
  `"the quantity is more than what I can confirm here. Please refer to your salesman."`,
  `"no stock and no incoming at the moment. Please refer to your salesman."`. Still one line per
  product starting `<code> x <Q>:` (R14 stays; `stock_ask_service.answer_line` finds the line by
  that prefix).
- `presenters._refer_to_salesperson` becomes the constant `Please refer to your salesman.`; the
  name is no longer printed. `eta_policy.dealer_view` stops carrying `salesperson_name` and
  `eta_policy.salesperson_name` goes (its one reader was that closing).
- Nothing else changes wording: rows 5-7 already print the constant.

### 2. Every refer reply is an ask row

A dealer's turn whose final reply text carries the refer sentence and whose fetch produced no
`stock_availability` entry for a product gets one `stock_asks` row per product the reply is
about, or one row for the ask as a whole when no product can be named. Entries are built by a
pure function in a new module `app/services/chatbot/refer_asks.py` (no I/O, reads the answer
text, the fetch envelopes, the plan's resolved entities and the pre-turn pending) and flow into
the existing `stock_ask_entries` list, so `after_answered_turn` stays the one writer.

New `branch` values (the CHECK constraint and `STOCK_ASK_BRANCHES` grow):

- `incoming_eta`: a dealer incoming ask answered with ETAs (row 4). One row per dealer line;
  `product_code` from the line, `answer_summary` = `ETA: <dates>. Please refer to your salesman.`
  (the code is the row's own Product column, as on every other row).
- `referred`: every other refer reply (rows 5-7): an incoming or stock miss, a not-found code, a
  declined did-you-mean. One row per resolved product entity when the plan has any; else one row
  whose `product_code` is what the dealer typed (the pending's `typed` for row 7, else the
  message's product words, else the message text, capped at 100). `answer_summary` = the reply
  text as sent (capped).

`quantity` becomes nullable (an incoming ask and a miss carry none; a declined did-you-mean
carries the pending's `stock_qty`). `product_id` comes from the plan's resolved entity when it
has one, else the writer resolves it by code within the ask's company
(`stock_ask_service._resolve_product_ids`), so the row names the product. Neither new branch notifies the salesman on WhatsApp
(`NOTIFIED_BRANCHES` unchanged; the row records `not_notified_branch`): R6 B3's "incoming never
notifies" extends to them, and the owner rule speaks of the Customer asks view only.

### 3. Readers

- `StockAskResponse.quantity: Optional[int]`; FE `StockAsk.quantity: number | null`;
  `BRANCH_LABEL` / `BRANCH_VARIANT` gain `incoming_eta` ("Incoming ETA", info) and `referred`
  ("Referred", secondary); the "Asked" text (`lib/stock-asks-todo.ts`, the CRM tab's Quantity
  cell, the portal list) prints `<code>` alone when quantity is null.
- `SKIP_REASON_LABEL.not_notified_branch` reads "This kind of answer does not notify the salesman".

### 4. Migration

`rs_0001_stock_ask_referred`: `ALTER TABLE stock_asks ALTER COLUMN quantity DROP NOT NULL`;
replace `ck_stock_asks_branch` with the six values. Idempotent (drop-if-exists, add), re-parented
onto main's head before the PR is labelled.

## Ambiguity raised (crew-ask on PR #1386)

A refer reply with no identifiable product (a described set like "gunmetal water closets" that
matched nothing, or a typed code the resolver could not map). Options: (a) still write one row
carrying what the dealer typed as `product_code` (recommended: the rule says EVERY reply, and the
salesman still needs to see the ask); (b) skip the row when no product resolves. Built as (a)
(`refer_asks.referred_entries` step 5); (b) is the deletion of that step.

## Out of scope

- The salesman WhatsApp notification (S4) for the new branches.
- The commercial handover copy (`chatbot_reply_copy.CHATBOT_REPLY_HANDOVER_SALESPERSON`).
- Documentation mockups / evidence transcripts that quote the old wording.
