# Multi-company reply clarity (backend half): acceptance criteria

Status: built and reviewed on `fm/mc-label-backend` (2026-08-17); PR pending.
Plan: `documentation/plans/multi-company/PLAN-multi-company-reply-clarity-backend.md`
Source: scout report `multicompany-response-clarity/report.md` (firstmate data dir),
sections 2.2, 3, 4 PR-A, 5.1, 6. Captain decisions 2026-08-16: rollout (c) = every
company-scoped presenter tool; label ONLY when the lookup or the returned rows actually
span more than one company; never key on the caller's access list.

## Journey

**Actor.** A WhatsApp contact who buys from both Mocha and Sorento (contact 900000008 in
the reproduced case), asking the assistant "MWC-SC08B check stock". Behind them: n8n
(`sub-get-results` -> `crm_inventory_stock_balance_list` with `view=render`) and the
FastMCP process that presents the backend list into `items[]`.

**What the system already knows.** The resolver already told n8n that `MWC-SC08B` exists
as two products, one per company. The backend then queried BOTH companies' stock in one
call (one `IN` predicate over the caller's scope). Nothing about the caller's grant is
asked again.

**What happens today.** Every row (and the empty reply) is company-blind: "No matching
results found." with no hint that two companies were searched, and when rows do come
back two rows with the same product code cannot be told apart.

**What happens after.**

1. Contact asks for a code that exists in BOTH companies. Every rendered row leads with
   `Company: Mocha` / `Company: Sorento`, and the envelope carries
   `lookup_companies=[{id,name},...]` naming both. If nothing is stocked anywhere, the
   intro says "No matching results found for Mocha or Sorento." and `lookup_companies`
   still names both, so n8n can say which companies it checked.
2. Contact asks for a code that exists in ONE company only, even though they can see two.
   The reply is byte-identical to today's single-company reply: no `Company` field, no
   `lookup_companies`, same intro.
3. Same for every other company-scoped presenter tool (incoming stock list / by-product /
   shipments, products, product attachments, certificates, promotions, promotion products,
   orders list, orders by product): rows span >1 company -> labelled; otherwise unchanged.

**What they hold at the end.** A reply where, whenever two companies are genuinely in
play, every line names its company and an empty result names the companies searched.

## Criteria

Tag: `[BE]` backend, `[MCP]` presenter, `[T]` test-only. Every AC traces to journey step
1, 2 or 3 above.

### A. Contract (journey 1, 2)

- **AC-A1 [BE]** `ListResponse` (`app/schemas/common.py`) has an optional
  `lookup_companies: Optional[List[Dict[str, Any]]] = None`; each entry is
  `{"id": <company uuid str>, "name": <company name>}`, sorted by name. The same key
  is emitted on the raw-dict payloads of the incoming-stock endpoints (which do not use
  `ListResponse`). ONE name everywhere: `lookup_companies`.
- **AC-A2 [BE]** Every affected row schema declares `company_id: Optional[str] = None`
  and `company_name: Optional[str] = None`. `company_name` is set ONLY when the lookup
  spans >1 company (AC-B1); it is `None` otherwise.
- **AC-A3 [BE]** The company set is computed as: companies of the products the tool was
  asked about (its resolved `product_ids`, read through the SCOPED ORM so a company the
  caller cannot see never appears) UNION companies of the rows returned. Labelling fires
  iff that union has size > 1. The caller's company grant is never read for this
  decision.

### B. Behaviour, per tool (journey 1, 2, 3)

Given a two-company caller scope (Sorento + Mocha), for EACH tool in the plan's tool
table:

- **AC-B1 [BE]** found-in-several: rows exist in both companies -> every row carries
  `company_id` + resolved `company_name`; payload `lookup_companies` names both companies.
- **AC-B2 [BE]** none-in-several: the requested product ids span both companies but no
  row matches -> `data == []`, `empty == True`, `lookup_companies` names both companies.
- **AC-B3 [BE]** found-in-one-of-several: product ids span both companies, rows exist in
  one only -> rows carry `company_name`, `lookup_companies` names BOTH (the other was
  searched and had nothing).
- **AC-B4 [BE]** single-company lookup for a two-company caller: product ids resolve to
  one company and rows (if any) are from that one company -> `company_name is None` on
  every row and `lookup_companies is None`.
- **AC-B5 [BE]** rows returned span two companies even with no `product_ids` given (for
  tools whose lookup is a free-text/other filter) -> labelled as AC-B1.
- **AC-B6 [BE]** a product id from a company OUTSIDE the caller's scope contributes
  nothing: a Sorento-only caller passing a Mocha product id gets a single-company
  answer (AC-B4 shape), never a Mocha label.
- **AC-B7 [BE]** the company-name lookup is one batched `companies` query per response,
  never per row; and it is issued only when the union has size > 1.

Tools covered by B (all in one PR): `crm_inventory_stock_balance_list`,
`crm_incoming_stock_list`, `crm_incoming_stock_by_product`, `crm_incoming_stock_shipments`
(rows-only company set, no product input), `crm_master_products_list`,
`crm_master_product_attachments_list`, `crm_certificates_list`,
`crm_marketing_promotions_list`, `crm_marketing_promotion_products_list`,
`crm_order_management_orders_list`, `crm_order_management_orders_by_product_list`.

### C. Presenter (journey 1, 2)

- **AC-C1 [MCP]** `_PASSTHROUGH_KEYS` includes `lookup_companies`; the render envelope
  carries it when the backend emitted it, and omits it (not null) otherwise.
- **AC-C2 [MCP]** Each affected row presenter emits a leading keyed field
  `{"key": "company_name", "label": "Company", "value": <name>}` when `company_name` is
  present, and no `Company` field at all when it is missing or `None`.
- **AC-C3 [MCP]** When `has_result` is false and `lookup_companies` is present, the intro
  names the companies searched: `No matching results found for Mocha or Sorento.`
  (names joined with " or "; three or more: "A, B or C"). Without `lookup_companies` the
  intro is unchanged: `No matching results found.`
- **AC-C4 [MCP]** Byte-identical single-company output: for every affected tool, the
  envelope for a payload with `company_name: null` on rows and `lookup_companies: null`
  equals, byte for byte, the envelope for the same payload with those keys absent (which
  is today's output).
- **AC-C5 [MCP]** The `_sanitize_tool_response` slimmers for orders / products /
  promotions / promotion-products / incoming keep `company_name` (and top-level
  `lookup_companies`) on their way to the presenter.
- **AC-C6 [MCP]** `_sanitize_tool_response` drops the raw `company_id` UUID from the
  rows (nested rows included) of exactly the eleven tools in B, and from no other tool
  (`crm_resource_attachments_list` keeps its row `company_id`). Top-level
  `lookup_companies` keeps its ids.

### D. Tests (journey 1, 2, 3)

- **AC-D1 [T]** Backend: for every tool in B, tests for AC-B1, AC-B2, AC-B4 (AC-B3 and
  AC-B5 at least on stock and one raw-dict incoming tool; AC-B6 and AC-B7 at least once
  through the shared helper). Modelled on
  `tests/test_attachment_company_stamp_in_list.py`: `blank_session`, seeded Mocha
  company, pinned two-company scope, own seeded data chain, Postgres only.
- **AC-D2 [T]** MCP: `tests/test_presenters.py` covers AC-C1 to AC-C4 (row with and
  without `company_name`, empty intro with and without `lookup_companies`, byte-identical
  proof) for stock and at least a smoke case per other presenter touched;
  `tests/test_multi_company_lookup_sanitizer.py` covers AC-C5 and AC-C6.
- **AC-D3 [T]** Existing suites stay green (`pytest` in `sorento_crm_backend/`,
  `pytest` in `sorento_crm_mcp/`).

### E. Deploy (journey 1)

- **AC-E1** PR body carries the deploy note: the FastMCP process registers presenters at
  startup, restart it after merge (same gotcha as sorento_crm_mcp PR #109). No n8n change
  in this PR.
