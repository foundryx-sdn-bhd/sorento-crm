# UAC: Customer asks logs only refer-to-salesman replies; one Open to-do list (CUSTOMER-ASKS-REFER-ONLY)

Plan: `PLAN-customer-asks-refer-only-1oct.md`. Tests: `sorento_crm_backend/tests/chatbot/test_customer_asks_refer_only.py` unless named.

## Logging

- **AC-RO01** A dealer stock ask answered B1 / B2 / B4 writes one open row per product with the exact line sent (`test_a_live_stock_ask_logs_the_three_refer_lines_and_not_b3_incoming`, `test_stock_ask_record.py`).
- **AC-RO02** A B3 `incoming` answer ("no stock at the moment, ETA dd/mm/yyyy.") writes no row, alone or beside refer lines (same tests).
- **AC-RO03** The refer branches are derived from the presenter tails, and the set is pinned: too_big, in_stock, no_incoming (`test_the_presenter_stamps_each_answered_entry_from_the_tail_it_printed`, `test_pin_only_b3_incoming_answers_without_the_refer_line`).
- **AC-RO04** A contact whose escalation switch is off (#1406) gets a row for: an incoming miss (product named), asking for a person (what they typed), a stale yes to an old offer, small talk whose offer was stripped (`test_a_barred_*`).
- **AC-RO05** The same turns for an allowed contact (offered a team) write no row (`test_the_same_miss_for_an_allowed_contact_*`, `test_an_unbarred_contacts_small_talk_logs_nothing`).
- **AC-RO06** Dealer ETA replies, misses and a declined did-you-mean still write their `incoming_eta` / `referred` rows (`test_refer_salesman.py`).
- **AC-RO07** No backend composer prints the refer line except through `turn/refer.py`; the words appear in no other string literal (the two guard tests).
- **AC-RO08** A dry run that is not the chat console writes nothing; a chat console turn writes `source = console` rows (`test_a_dry_run_still_writes_nothing`, `test_stock_ask_record.py`).
- **AC-RO09** Existing rows are not rewritten: no migration, no backfill in this lane.

## To-do

- **AC-RO10** The portal Customer asks and CRM Sales > Customer asks to-do shows one `Open` heading, then `Done today`; no `Needs attention`, no `Today` (`AskTodoList.test.tsx`, `AskTodoGrid.test.tsx`, `CustomerAsksList.test.tsx`).
- **AC-RO11** Open is oldest first by default across days; the toolbar sort reorders the whole list (`lib/stock-asks-todo.test.ts`).
- **AC-RO12** Each card shows the date and time it was asked (`AskTodoList.test.tsx`).
- **AC-RO13** The Agent select line reads `CODE · N open`; `GET /api/v1/sales/customer-asks/agents` carries no `needs_attention` (`MyCustomerAsksClient.test.tsx`, `tests/test_sales_customer_asks_api.py`).
- **AC-RO14** Usable at 1280px and 375px (agent-browser evidence on the PR).
