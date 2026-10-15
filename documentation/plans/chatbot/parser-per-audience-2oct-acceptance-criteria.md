# UAC: parser prompt rendered per contact audience

Plan: `PLAN-parser-per-audience-2oct.md`. Status: final, owner answers 2 Oct 2026.
Tests run on Postgres only (`tests/_pg_fixture.py`).

The four gated grants are:
- C = `purchase_orders.cost`
- P = `purchase_orders.placed`
- S = `sales_orders.sales_report`
- L = `scm.low_stock_report`

An "audience" is the subset of {C, P, S, L} a contact holds. There are 16 subsets, plus the
unidentified contact, which holds `[]`.

## Single source and UI

- **AC-PA-1 One table.** `prompt_gates.PROMPT_GATES` is keyed by unique `(domain, part)` pairs.
  There are five rows: purchase_cost, purchase_order, spo_allocation, sales, and
  (inventory, low_stock_report). Several rows may share a grant (placed covers purchase_order
  and spo_allocation). Each row carries:
  - `tag`;
  - `blocks`, the UI titles;
  - `forbidden_terms`;
  - `refused_domains`, `refused_intents` and `refused_order_statuses`.

  The refusal seams import this table; no second copy of a gated domain, intent or status exists
  in the refusal code.
- **AC-PA-2 API.**
  - Request: `GET /api/v1/system/chatbot/field-reveal-keys`.
  - Response: every key carries `prompt_blocks`. For example, `sales_orders.sales_report` gives
    `["SALES REPORT", "SALES ANALYSIS", "TOP SELLING"]`, and `purchase_orders.placed` gives
    `["PURCHASE ORDERS", "SPO LAST RECEIPT"]`. A key with no row gives `[]`.
- **AC-PA-3 UI.**
  - Location: the contact Access tab, Field reveals card.
  - A key with blocks shows "Also removes from the chatbot prompt: " followed by its blocks,
    joined with ", ".
  - A key without blocks shows no such line.
  - Usable at 375px and 1280px.
- **AC-PA-4 Relabel.** The `purchase_orders.placed` label reads "Purchase orders (PO asks, and
  PO on stock answers)".

## Render

- **AC-PA-5 Full render unchanged.**
  - The tagged prod text rendered for {C, P, S, L} is byte-identical to
    `chatbot_semantic_parser.prod-20261001.txt`.
  - The same holds for the editor preview.
- **AC-PA-6 Leak matrix, prompt half.**
  - For each of the 16 audiences plus the unidentified contact, the render contains NO
    `forbidden_terms` of any grant the audience lacks. Matching is case-insensitive and
    word-bounded.
  - Every block of each grant it holds is still present (by block title).
  - Forbidden terms per grant:
    - **C:** `purchase_cost`, `check_po_cost`, `purchase cost`, `last purchase cost`,
      `LAST PURCHASE COST`, `buying price`, `cost price`.
    - **P:** `purchase_order`, `check_po`, `purchase order`, `PO`, `PURCHASE ORDERS`,
      `PO placed`, `supplier order`, `spo_allocation`, `check_spo`, `SPO LAST RECEIPT`,
      `last received`.
    - **S:** `sales_report`, `sales_analysis`, `top_selling`, `SALES REPORT`, `SALES ANALYSIS`,
      `TOP SELLING`, `sales_channel`, `rank_by`, `sales_basis`.
    - **L:** `low_stock_report`, `LOW STOCK REPORT`, `reorder report`.
- **AC-PA-7 Sizes** on the prod text (o200k_base): the minimal audience is at most 27,000
  tokens, and {P} is at most 27,600.
- **AC-PA-8 Malformed tags.** A nested, unbalanced or unknown tag keeps its block, removes its
  markers and logs a warning. It never raises.
- **AC-PA-9 Wiring.**
  - The system prompt `parser.parse` receives on a real `engine.run_turn` is the render for that
    contact's `ctx.access.attributes`.
  - It is the same `production` version: no new version or label is created per audience.
- **AC-PA-10 Same version stream.** Moving the `production` label changes every audience's render
  after the cache TTL.

## Backend refusal (the control)

- **AC-PA-11 Leak matrix, refusal half.** This is an end-to-end `engine.run_turn` with the parser
  output FORCED. For each of the 16 audiences plus the unidentified contact, and each ask below
  whose grant the audience lacks:
  - the reply is the refusal;
  - no restricted tool is called (the captured MCP calls are checked);
  - no restricted field reaches the reply.

  | Ask (forced parser output) | Grant | Expected refusal |
  | --- | --- | --- |
  | domain purchase_cost, product named | C | "Sorry, you are not allowed to access purchase cost" |
  | domain purchase_order, product named | P | refused (Q2), no `crm_procurement_po_placed_list` call |
  | order_status sales_report | S | "Sales report is not enabled for your account." |
  | order_status sales_analysis | S | same |
  | order_status top_selling | S | same |
  | intent low_stock_report | L | "Low stock report is not enabled for your account." |
  | domain spo_allocation (last in), product named | P | refused (G2), no `crm_procurement_spo_allocations_last_receipt_list` call |
  | stock ask that climbs to the PO rung | P | rung not probed |

  An audience that holds the grant gets the tool called; that is the positive control in the
  same matrix.
- **AC-PA-12 Supplier.** For a contact without `purchase_orders.supplier`, a granted cost or PO
  answer carries no supplier value.
- **AC-PA-13 Other customers.** A contact linked to customer A, asking for customer B's orders,
  outstanding or sales figures, is refused: "Sorry, that isn't under your account. ..." and no
  tool is called with B's id.
- **AC-PA-14 G1: unlinked non-office contact fails closed.**
  - Setup: a contact with no office access type and no linked customer makes a customer-named
    or all-customer order ask.
  - Expected: the reply is "Sorry, I can't find an account linked to you yet." and no order tool
    is called.
  - An office-type contact with no links is answered as today.
  - Ships only after the owner data step. Its tests are in
    `tests/chatbot/test_customer_scope_unlinked_fail_closed.py`, in their own commit.
