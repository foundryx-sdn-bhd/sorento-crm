# PLAN: Report engine drill-down follow-up (any axis to any axis, filters kept)

Status: Review. Card approved 8 Oct 2026 (`report-drilldown-behaviour-card.md` revision 3);
reviewer round 1 fixed; waiting on CI and the owner's hand test.
Track: FULL (parser prompt change as a data-only migration publishing a new dev version; MCP
tool param added). Lane: REPORT-DRILLDOWN, PR #1531.

## Owner's words (8 Oct 2026)

Turn 1 "top sales agents for product X", turn 2 "who are the customers for these sales agents" /
"top 10 customers for these agents" must carry the agent set AND every turn-1 filter. Turn 2 may
also be a fresh ask: the parser decides; unsure asks. Customers of the agents = the customer
card link. Generic: any axis to any axis on a declared link per pair.

## Design (simplest thing that answers it)

1. **Route rows carry keys** (`reports/ask.py`, `schemas/report_ask.py`): each ranked row gets
   `keys`, the ids (codes for a location) the row's name stands for inside the contact's
   companies; `"none"` for the "(no agent)" row. Channel and month rows carry none (not a drill
   source). Looked up AFTER the cut, only for the rows shown.
2. **One new filter, the card link** (`sales_order_lines_ask.py`, route, MCP catalog):
   `customer_agent_ids`, `Customer.sales_agent_id IN (...)` (`"none"` = no card agent). The
   route's `sales_agent_ids` also takes `"none"` (orders with no agent). A dealer may not use
   either. Echoed as "Customers assigned to: <agents>".
3. **Declared links** (`reports/ask.py::drill_param`): `(sales_agent, customer)` ->
   `customer_agent_ids`; every other source's own filter param; channel and month none.
4. **Frame keeps the shown rows** (`lanes/business/fetch.py::_report_ask_output`): the MCP
   envelope passes `drill_rows` (name + keys per row, plus group_by); the frame stores them
   under `shown`. A refine never sends `shown` to the route.
5. **Parser key `ranking_drill`**: `scoped | global | unsure | null` on a sales_ranking verdict
   naming its own axis after a ranking. New dev prompt version (data-only migration,
   production label unmoved). The frame rides on a scoped or unsure verdict
   (`turn_runtime.py`), as it does on a refine.
6. **Lane `_drill`** (`lanes/business/report_ask.py`): scoped -> held args minus period, count,
   axis and shown rows; the new axis; the link param = union of the shown rows' keys; the
   message's own basis, measure, sort, channel, brand or category word, period replace held
   ones; count from the message else "How many?". unsure -> one question naming the count and
   the rows above; nothing runs. No frame -> one line, never a guess. Same axis -> the refine.
   More than 50 keys -> one line (the route's per-filter cap).
7. **Reply header one fact per line** (MCP presenter): `Top 5 customers by delivered sales`,
   then `Label: value` per filter, `Period: from to to`.

## Test list (red first)

Route (`tests/test_report_drilldown_route.py`): rows carry keys for agent/customer/product/
brand/category/location, none for channel/month, "none" for (no agent); `customer_agent_ids`
ranks customers by their card agent across ANY SO agent with the other filters kept; `"none"`
in both params; echo "Customers assigned to"; dealer refused with `customer_agent_ids`.
Lane (`tests/chatbot/test_report_drilldown_lane.py`): scoped drill carries product, period,
basis, measure and sends `customer_agent_ids` = turn 1's shown agents; scoped with no count
asks "How many?" then runs; scoped other pair uses the source's own param; message overrides
win; global ignores the frame; unsure asks and calls nothing; no frame says the line; channel
or month source says the line; refine never sends `shown`.
MCP (`sorento_crm_mcp/tests/test_report_ask_presenter.py`): header lines; `drill_rows` in the
envelope. Prompt: addendum carries `ranking_drill`; the migration publishes a version with
the label unmoved.
