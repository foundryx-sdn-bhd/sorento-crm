# UAC: REPORTS-STEP2

Plan: `PLAN-reports-step2-11oct.md`. Card: `CARD-reports-step2-11oct.md`. Tests under
`sorento_crm_backend/tests/reports_step2/` (backend) and `sorento_crm_mcp/tests/` (MCP).

## S1: measure registry + crm_report over the ask engine (sales rows only)

Contract (pinned for tester and coder):

- Module `app/services/reports/measures.py`:
  `@dataclass(frozen=True) Measure(key, label, synonyms: tuple[str, ...], basis, figure, dimensions: tuple[str, ...],
  grant: str, dealer_dimensions: tuple[str, ...], run: Callable)`; `MEASURES: dict[str, Measure]`;
  `register(measure)` (duplicate key raises ValueError); `get(key)` (unknown raises KeyError);
  `keys() -> tuple[str, ...]` in registration order.
- S1 rows (all over `reports/ask.py` `run_ask`, basis x figure): `ordered_amount`, `ordered_qty`,
  `delivered_amount`, `delivered_qty`, `outstanding_amount`, `outstanding_qty`. dimensions = `ask.DIMENSIONS`,
  dealer_dimensions = `ask.DEALER_KEYS`, grant `sales_orders.sales_report`.
- Route `GET /api/v1/order-management/report` in `app/api/v1/order_management/report_ask.py`, same guards as
  `/report-ask`: params `measure` (required, a registry key), `group_by`, `top_n`, `sort`, `date_from`,
  `date_to`, the same filter ids (`product_ids`, `brand_ids`, `category_ids`, `sales_agent_ids`,
  `customer_ids`, `customer_agent_ids`, `warehouse_codes`, `channel`, `product_code`), `contact_id`, `space_id`.
- MCP: ToolSpec `crm_report` in `sorento_crm_mcp/catalog.py`, read_only, restricted_fields like
  `crm_report_ask`, `measure` described with the registry keys; presenter = the report_ask presenter. Chatbot
  allow-list gains `crm_report`; `crm_report_ask` and the other report tools stay.

Acceptance:

- AC-1 For each of the 6 S1 measures, `/report` returns the same rows, totals and keys as `/report-ask` with
  the matching basis and measure for the same inputs (equivalence on seeded data).
- AC-2 Unknown `measure` -> 422 whose detail lists the valid keys; missing `measure` -> 422.
- AC-3 Period required, group_by needs top_n, empty filter -> the same 422 codes as `/report-ask`.
- AC-4 Grant missing (`sales_orders.sales_report`) -> the same refusal as `/report-ask`.
- AC-5 Customer-scoped (dealer) contact: rows limited to its own customers; a group_by outside the measure's
  dealer_dimensions (e.g. sales_agent, customer, location, channel) -> 403 `report_dimension_not_allowed`.
- AC-6 A group_by outside the measure's dimensions -> 422.
- AC-7 Adding a measure is one `register(Measure(...))` plus its function: a test registers a fake measure and
  `/report` serves it with no other code change; `keys()` and the MCP description include it.
- AC-8 Duplicate key -> ValueError; `get` of an unknown key -> KeyError.
- AC-9 The MCP `crm_report` ToolSpec is read_only, carries contact_id/space_id, and its `measure` description
  names every registry key; the chatbot read-only allow-list includes `crm_report` and still includes the old
  six tools.
- AC-7 note: the MCP description lists the keys as a literal (no import of the backend registry from the MCP
  package); the sync is checked by a backend test in S4.
- AC-10 Rate limit and brand scope behave as `/report-ask` (same settings, same stamping).
