# PLAN: low stock report asks for its filters before it runs (LOWSTOCK-FILTER-ASK)

Status: in review, PR #1445 ready (2 Oct 2026). Reviewer + security reviewer rounds fixed; full tests/chatbot 5240 passed on 147009ab; owner hand test posted. Track: feature (no migration).
Card: `lowstock-filter-ask-behaviour-card.md` (holds the Step 1 trace with file:line).

## Owner report (2 Oct 2026)

"Sorento water tap low stock list" goes straight to a reorder run and returns the low stock
report with NO filters. We never collect the filters (category, supplier) nor ask whether the
user wants it grouped by supplier or by category.

Rule: never trigger a reorder run before the filters are settled; every failure ends in a clear
message (never-stuck rule).

## Design (as built)

1. **Route** `app/api/v1/scm/low_stock_report.py`: new query params `categories` (codes,
   csv or repeated), `suppliers` (names, repeated only, never split on commas), `split`
   (none / supplier / category / supplier_category, 422 otherwise). Forwarded to
   `generate_low_stock_report`. A supplier filter or supplier split for a contact without
   `purchase_orders.supplier` answers `error` before any run. MCP tool declares the three.
2. **Helper** `app/services/chatbot/required_fields.py`: the shared required-field
   collector (owner Q2). API in its docstring and in the PR body.
3. **Low stock config** `app/services/chatbot/lanes/business/low_stock_ask.py`: category
   required (brand-narrowed through `brand_hint`), supplier optional, grouping words.
4. **Engine** `engine.py`: `required_fields.reply_verdict` + `low_stock_ask.take_words`
   before every other seam; after the lane, the envelope's `required_ask` is recorded on
   `focus.required_ask` (`turn/state.py`, `contracts.SessionVars` focus schema).
5. **Lane** `lanes/business/__init__.py` low stock override: `settle` before any fetch;
   `fetch.py` sends the filters; the filter line is said under the report's first line.

## Tester finding (3 Oct 2026): a console ask pushed the real workbook

Pre-existing on main (`lanes/business/__init__.py` discarded `dry_run`; the route claimed
delivery unconditionally; `export_tasks._push_low_stock_to_chat` has no test check). Fixed
here: `run_fetch` passes `dry_run` to `crm_low_stock_report`; on a dry run the route never
claims delivery (the run and the file are still built and land in My Downloads), and the
presenter's pending line says `test turn: it will be in My Downloads, nothing is sent to
WhatsApp.` Tests: route (claim never set / live still set), lane (arg sent / not sent),
presenter, console full turn.

## Not done (named triggers)

- The parser addendum still says a bare token under inventory "usually is" a product;
  the lane reads a digit-free product word as the category instead. Trigger to teach the
  parser (a new unlabelled prompt version): a live turn where a type word still misses.
- A supplier named only by a word the parser places as something else (a customer) is
  read from the leftover message words; trigger to add a `supplier` entity hint: a live
  miss.
