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

## S2: the chatbot asks crm_report; one report entry in Focus; refine / switch; provenance

Scope: sales rankings only (the six S1 measures). Top selling, sales report, outstanding, sales analysis and the
low stock read move in S4. No parser prompt change (S3); the lane maps today's parser keys (order_status
sales_ranking, basis, measure/rank_by, group_by, top_n, filters, ranking_refine, ranking_drill) as today.

Contract (pinned for tester and coder):
- `Focus.report` replaces `Focus.sales_ranking_frame`: `{"measure": <registry key>, "args": {<crm_report params
  without contact/space>}, "shown": <drill rows or absent>}`. Reading a stored session that still has
  `sales_ranking_frame` maps it to `report` (measure = f"{basis}_{measure}") so a refine after deploy works.
- The lane calls `crm_report` with `measure=<registry key>` wherever it called `crm_report_ask`; the reply text is
  the presenter's, as today.
- `/report` echoes `measure_key` (the registry key) next to the existing `measure` figure; the presenter is unchanged
  except for provenance below.
- Provenance: the call carries `carried` (list of param names whose value came from the Focus entry, not from this
  message); `/report` accepts and echoes it; the presenter appends " (from earlier)" to each fact line whose param is
  in `carried` (Period, and each filter label). Not sent on a fresh ask.

Acceptance:
- AC-11 A fresh sales ranking ask calls `crm_report` (not `crm_report_ask`) with the registry key; for the same
  inputs the reply text equals what the crm_report_ask path printed (no "(from earlier)").
- AC-12 After a ranking, `Focus.report` holds the measure key, the args and `shown`; `Focus.sales_ranking_frame` is
  no longer written.
- AC-13 A stored session with only `sales_ranking_frame` (old shape) still refines: "top 5" re-runs the held args
  with top_n 5 through `crm_report`.
- AC-14 Refine patches only what the message changes: "top 5", "amount instead", "ordered instead" (basis),
  "only dealer" (channel), "last month" (period), a brand / category word; every other held arg is unchanged.
- AC-15 Provenance: on a refine, each fact line whose value was carried ends with " (from earlier)"; a value said in
  this message has no mark; a fresh ask has no marks. One fact per line, "Label: value", no " · " joins.
- AC-16 Switch: a report ask that names a different group_by without a refine or drill reading starts a fresh
  entry (no held filters leak); drill behaviour (`ranking_drill` scoped / unsure / global) is unchanged.
- AC-17 Dealer: a held entry is re-checked on refine exactly as today (report_ask.py:647-657); a dealer refine that
  asks for a staff-only dimension is refused, never served.
- AC-18 Same lifetime as today's ranking entry (engine.py:6600-6604): a turn that is not a report ask clears
  `Focus.report`, so a later "top 5" after a stock question is a fresh ask, never a stale refine.
