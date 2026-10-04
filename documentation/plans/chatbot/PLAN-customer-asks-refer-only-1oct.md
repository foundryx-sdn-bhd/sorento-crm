# PLAN: Customer asks logs only refer-to-salesman replies; one Open to-do list (CUSTOMER-ASKS-REFER-ONLY)

Status: Built, in review on PR #1420 (small fix track: no migration, no auth/RBAC change, no new ingest surface). Existing non-refer rows are left as they are (no data rewrite).
Owner rulings: 1 Oct 2026. Crew decision on the design: 1 Oct 2026 (PR #1420 thread).
UAC: `customer-asks-refer-only-1oct-acceptance-criteria.md` alongside.
Amends: `PLAN-chatbot-stock-ask-v2-24sep.md` (S5 write rule), `PLAN-refer-salesman-30sep.md` (how "refers" is decided), `../sales/PLAN-sales-asks-todo-29sep.md` (grouping).

## The rulings

1. Customer asks logs EVERY reply where the bot told the customer to refer to their salesman, and ONLY those. Derived from one source of truth per reply kind, not by matching the reply text.
2. The to-do loses its `Needs attention` / `Today` split: one `Open` section, oldest first by default, each card showing its date; `Done today` stays. The Agent select's count is the open count.

## Every reply that carries the refer line (measured on main @ b466afe3)

| # | Reply | Printed by | Row before | Row now |
|---|---|---|---|---|
| 1 | `<code> x <Q>: the quantity is more than what I can confirm here. Please refer to your salesman.` (B1) | `sorento_crm_mcp/sorento_crm_mcp/presenters.py` `_AVAILABILITY_TAILS["too_big"]` | yes | yes |
| 2 | `<code> x <Q>: yes, we have stock. Please refer to your salesman.` (B2) | `_AVAILABILITY_TAILS["in_stock"]` | yes | yes |
| 3 | `<code> x <Q>: no stock and no incoming at the moment. Please refer to your salesman.` (B4) | `_AVAILABILITY_TAILS["no_incoming"]` | yes | yes |
| - | `<code> x <Q>: no stock at the moment, ETA dd/mm/yyyy.` (B3, NOT a refer reply) | `presenters._availability_line`, `incoming` branch | yes | **no** |
| 4 | dealer incoming ETA lines, then the refer line | presenter `closing`, printed at `lanes/business/fetch.py` (dealer incoming) | yes (`incoming_eta`) | yes |
| 5 | dealer stock / incoming reply whose escalation offer became the refer line | `dealer_stock.without_escalation` | yes (`referred`) | yes |
| 6 | dealer "no" to a did-you-mean: the line alone | `turn/apply.py` (`stock_pick_declined`) | yes (`referred`) | yes |
| 7 | barred contact (#1406): a miss in the composer, line where the offer was | `turn/compose.py` | **no** | yes |
| 8 | barred contact: business lane misses (`what_you_want_reply`, `not_found_error_message._esc_offer`, the `all dates` hint, `build_suggest_offer._cont`) | `lanes/business/answer.py` | **no** | yes |
| 9 | barred contact asking for a person / answering an old offer: the line alone | `escalation_control.barred_reply` via `engine._run_stages` | **no** | yes |
| 10 | barred contact: any composed reply whose offer the backstop stripped (`_run_answer`, casual lane, `/complete` tail) | `escalation_control.strip_text` | **no** | yes |

## Design

### The stock ask's own lines: the presenter's flag

The backend cannot import `sorento_crm_mcp` at runtime (`lanes/business/fetch.py` notes it), so the presenter stamps each answered `stock_availability` entry it passes through with `refers_to_salesman`, read off the tail it printed for that entry (`_availability_tail(entry).endswith(REFER_TO_SALESMAN)`). No hand-kept branch list: rewording a tail moves the branch in or out of Customer asks, and `test_pin_only_b3_incoming_answers_without_the_refer_line` fails so that is a decision. An entry still owing a quantity is not stamped (no tail was printed). `stock_ask_service.refer_entries` keeps only flagged entries; an entry without the flag (an older MCP) is not guessed at.

### Every other refer reply: one helper and a turn mark

`app/services/chatbot/turn/refer.py`: `sentence()` and `after(text, sep=...)` print `REFER_TO_SALESMAN` and mark the turn; `tracking()` opens one mark per turn on a context variable (`engine.run_turn` and `engine.complete_turn` are wrapped; a `complete_turn` inside `run_turn` shares the mark); `consume()` reads and clears it so a second tail cannot log twice. `SALESMAN_TEAM` is the business lane's barred "team" sentinel. Every backend site in the table above prints through the helper. Guards: an AST test allows the constant outside `refer.py` / `task.py` only in imports, comparisons, the tail's `MARKERS` tuple and the sentinel; a second AST test allows the words in no other string literal (docstrings aside).

### The writer

`engine._record_customer_asks`, run by both tails once the turn row is closed (`_run_answer`, `complete_turn`): the turn referred when the mark is set or a stock entry is flagged; then `refer_asks.referred_entries(referred=...)` names the rows (ETA lines, misses, a declined did-you-mean, the plan's products, else what the customer typed) and `stock_ask_service.after_answered_turn` writes the flagged ones. Dry runs still write nothing except the chat console (`chat_console` now also reaches the casual lane's `complete_turn`).

### To-do

`lib/stock-asks-todo.ts::bucketTodo` returns one `open` section (`Open`) ordered by the toolbar sort, default `created_at` ascending; `AskTodoList` / `AskTodoGrid` render `Open` and `Done today` (no red heading). `AskCard` already shows `formatDateTimeInMalaysia(created_at)`. `stock_ask_service.agent_counts` and `StockAskAgentCount` drop `needs_attention`; the Agent select reads `CODE · N open`. `today_start` stays on the payload: it is the Done today window.

## Review round 1 (reviewer, 1 Oct 2026)

- The mark is set when a composer BUILDS the line, so a line built and then discarded would log a row: `answer.py`'s promotion entitlement miss was built eagerly and is now built only inside the reply that sends it (`build_breakdown_msg`). Every other site's output is the reply.
- `refer_asks` step 2 reads `miss` / `unresolved` only from `inventory` / `incoming` envelopes: another domain's miss is a customer name or an order number, so a barred order miss is named by what the customer typed.
- `engine._record_customer_asks` is best-effort (logs, never fails a turn whose reply is already recorded).

## Known limits

- A business-lane or entities-only console run of a barred reply reaches `complete_turn` / `_run_answer` without `chat_console`, so a console hand test of those writes no row (a live turn does). The casual lane and the head's own arms do pass it.
- An MCP older than this PR stamps no flag, so during a split deploy stock ask lines write no row until both images are on this commit (compose deploys them together).

## Out of scope

- Rewriting or deleting existing `incoming` rows (owner: leave them).
- The salesman WhatsApp notification rules (unchanged: B1, B2, B4 only).
