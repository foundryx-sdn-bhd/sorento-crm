# PLAN - Chatbot sales report: confirmed vs outstanding sales, by month, one shape

Status: BUILT, Phase 3 fix round in progress 19 Sep 2026; DRAFT PR #1034. S19
(product-code prefix rule, live-testing fix) landed 19 Sep 2026, small fix track. S20
(a family-covering product ask also breaks out By product, small fix track) landed 19 Sep
2026 too.
UAC: `chatbot-sales-report-acceptance-criteria.md` (AC-16xx, rulings S1 to S20).
Base: origin/main. The turn re-architecture (#952, #863) is on hold by owner ruling and this
lane does not wait for it. Branch `feat/chatbot-sales-report`, one lane, one PR.
Precedent: `PLAN-chatbot-outstanding-report.md` (#862). This plan copies that lane's seams,
each with the justification that earned it, and adds no new mechanism.

## Journey

See the UAC's Journey section (seven steps). Decisions asked of the user: none beyond the
existing customer picker when a name is ambiguous. Everything else is derived: no date = all
dates, no channel word = all, breakdown axis follows the subject.

## Why a new route and not `order_analytics`

`GET /order-management/orders/analytics` exists, but it sums the DO table, whose line money
is unusable (UAC "Measured": August 2026 sums to 136.3M against 23.2M), it groups on one axis
only, and it knows nothing of confirmed vs outstanding. Repairing the DO import is a separate
problem with no reader today (UAC backlog). The SO lines already hold everything this report
needs, so the report reads them and nothing else.

## The reply (contract for Phase 1)

```
Customer: HANLIM TRADING SDN BHD, HANLIM TRADING SDN BHD [A/C I]
Product: all
Channel: Dealer
Location: all
Required date (or order date): all

*_Sep 2026_*
Sales orders: 31
Ordered: RM 182,450.00 (Qty: 3,120)
Confirmed (DO): RM 96,300.50 (Qty: 1,540)
Outstanding: RM 86,149.50 (Qty: 1,580)
*_By product_*
SRTWT7445: RM 50,000.00 (Qty: 900) (Confirmed: RM 40,000.00, Qty: 720)
SRTKT39SS: RM 31,200.00 (Qty: 400) (Confirmed: RM 0.00, Qty: 0)

*_Aug 2026_*
...

Reply 1 for the sales order list.
```

Detail (`detail=so`), one item per SO, latest first:

```
1. *SO Number:* SO421287
*Customer:* HANLIM TRADING SDN BHD
*Location:* BRW-IB, MWH-IB
*Order Date:* 12/09/2026
*Ordered:* RM 12,400.00 (Qty: 210)
*Confirmed (DO):* RM 6,200.00 (Qty: 105)
*Outstanding:* RM 6,200.00 (Qty: 105)
```

Miss: header, blank line, `No sales found.`, no offer. Denied: `Sales report is not enabled
for your account.` and nothing else.

`Location:` prints exactly as the outstanding header does, through the same
`_outstanding_location_header` rule: `IB (BRW-IB, MWH-IB)` when a token was typed, the bare code when the token IS the one code
(`BRW-IB`), `all` when no token. The lane always sends the token with the codes (captain ruling 19 Sep, parity with S9).

`Product:` is the COMMA-SEPARATED list of every code the typed stem covers (S19 second fix
round, owner ruling from live testing, 19 Sep 2026 - the earlier bracket form
`SRT5674 (SRT5674, SRT5674-N)` read "weird"): `SRT5674, SRT5674-N` when the family has 2+
codes, the bare code when the family is just one, `SRT56 (37 products)` past 10, `all` with
no product. `product_codes` (the field this renders) is every code the prefix COVERS, not
only codes with a row in this filtered report - a covered code with zero sales still counts.

S20 (owner ruling from live testing, 19 Sep 2026: with S19, the header read `Product:
SRT5674, SRT5674-BL, SRT5674-N, SRT5674-NL` but each month still only printed `*_By
customer_*` - "how I know the report is for which product"): a product ask that COVERS 2+
codes also prints `*_By product_*`, By product first when both lists print:

```
Product: SRT5674, SRT5674-N
Channel: all
Location: all
Required date (or order date): all

*_Sep 2026_*
Sales orders: 12
Ordered: RM 50,000.00 (Qty: 900)
Confirmed (DO): RM 40,000.00 (Qty: 720)
Outstanding: RM 10,000.00 (Qty: 180)
*_By product_*
SRT5674: RM 35,000.00 (Qty: 630) (Confirmed: RM 28,000.00, Qty: 504)
SRT5674-N: RM 15,000.00 (Qty: 270) (Confirmed: RM 12,000.00, Qty: 216)
*_By customer_*
HANLIM TRADING SDN BHD: RM 30,000.00 (Qty: 540) (Confirmed: RM 24,000.00, Qty: 432)
KIM HUAT HARDWARE SDN BHD: RM 20,000.00 (Qty: 360) (Confirmed: RM 16,000.00, Qty: 288)

Reply 1 for the sales order list.
```

A customer named alongside a family-covering product prints `*_By product_*` only (the
`*_By customer_*` list drops, same as any customer+product ask). A single-code product ask
is unchanged: `*_By customer_*` only, no `*_By product_*`.

## Backend contract

`GET /api/v1/order-management/sales-report` on the no-prefix router beside
`outstanding-report` (`app/api/v1/order_management/orders.py`), same auth dependency and
view permission as that route.

| param | type | rule |
|---|---|---|
| `product_code` | str | prefix, case-insensitive, min 3 chars (S19) |
| `customer_ids` | csv uuid | the lane's resolved ledgers |
| `customer_query` | str | `customer_name ILIKE %q%`, for n8n / the in-app assistant |
| `channel` | `dealer` / `project` | absent = all; else 422 |
| `warehouse_codes` | csv | exact codes; token resolution stays in the lane |
| `location_token` | str | echo only, the word the customer typed (`IB`); never filters. Same as the outstanding route |
| `date_from`, `date_to` | flex date | on the bucket date, `_parse_flex_date` |
| `detail` | `so` | adds `so_rows[]` |

At least one of `product_code` / `customer_ids` / `customer_query`, else 422
`subject_required`.

Response (`SalesReportResponse`, every field declared):

```
{
  "customer_name": str | null, "product_code": str | null, "product_codes": [str],
  "channel": "dealer"|"project"|null,
  "location_token": str | null, "warehouse_codes": [str], "date_from": date|null, "date_to": date|null,
  "months": [ { "month": "2026-09", "so_count": int,
                "ordered_value", "ordered_qty", "confirmed_value", "confirmed_qty",
                "outstanding_value", "outstanding_qty",
                "by_product":  [ { "product_code",  ...same six figures } ],   # customer subject,
                                                                                # or a product filter
                                                                                # covering 2+ codes (S20)
                "by_customer": [ { "customer_name", ...same six figures } ] } ],# product subject
                                                                                 # (both keys can be
                                                                                 # present at once, S20)
  "so_rows": [ { "so_number", "customer_name", "location", "order_date", ...six figures } ]
}
```

One service function `sales_report(db, filters) -> dict` in a new
`app/services/sales_report_service.py`. ONE base query over `sales_order_lines` joined to
`sales_orders`, `customers`, `products`, `warehouses`, filtered `sales_orders.status <>
'cancelled' AND line_status <> 'cancelled'`, selecting per line:

- `bucket = COALESCE(l.required_date, s.order_date)`
- `confirmed_qty = LEAST(l.qty_delivered, l.qty_ordered)`
- `confirmed_value = l.line_total * confirmed_qty / NULLIF(l.qty_ordered, 0)` (null to 0)
- `outstanding_qty = l.qty_ordered - confirmed_qty` when `s.status = 'open' AND
  l.line_status = 'open'`, else 0; `outstanding_value = l.line_total - confirmed_value` under
  the same condition, else 0
- `ordered = confirmed + outstanding` (value and quantity), so the identity is arithmetic

Months, breakdowns and `so_rows` are rolled up in Python from those rows, the way
`outstanding_report_service.py` does, so a breakdown can never drift from its total. Decimal
throughout, quantised to 2 places at the response edge. `_customer_echo` is imported from the
outstanding service, not copied. No registry, no list-query resource: the chatbot is the only
reader and the shape is a report, not a grid.

No migration for data. One migration publishes the parser prompt text (S4 below).

## Slices

| slice | phase | what | files |
|---|---|---|---|
| S1 | 1 | Presenter over mock JSON; goldens for customer / product / both / miss / detail; dash guard | `sorento_crm_mcp/sorento_crm_mcp/presenters.py` (`_sales_report`, `_sales_report_detail`, shared money formatter), `documentation/plans/chatbot/samples/sales-report-*.txt` + `.json`, `sorento_crm_mcp/tests/test_presenters_sales_report.py` |
| S2 | 2 | Route + service + response model | `app/api/v1/order_management/orders.py`, `app/services/sales_report_service.py`, `app/schemas/order_management.py`, `tests/test_sales_report.py` |
| S3 | 2 | MCP tool + reveal key (NO assistant bootstrap - struck 19 Sep, security B1: the in-app assistant must never carry `crm_sales_report`, same reason as `crm_low_stock_report`, N4) | `sorento_crm_mcp/sorento_crm_mcp/catalog.py` (`crm_sales_report`), `app/services/contact_field_reveal_service.py` (`FIELD_REVEAL_KEYS`), `app/services/mcp_tool_domains.py`, `sorento_crm_mcp/tests/test_catalog_sales_report.py` |
| S4 | 2 | Lane wiring, see below | `chatbot_parser_prompt.py` + one alembic migration publishing it, `contracts.py`, `head/parser.py`, `lanes/business/fetch.py`, `lanes/business/__init__.py`, `lanes/business/answer.py`, `lanes/business/resolve_gate.py`, `tail/pending.py`, `tail/compile_state.py`, `head/output_exchange.py`, `tests/chatbot/test_sales_report_lane.py`, `tests/chatbot/console_cases/2026-09-19-sales-report.yaml` |
| S5 | 3 | reviewer + security-reviewer (new route, per-contact key, company scope) + console check + one browser pass on Contacts > Access, in parallel; then guide-writer | `documentation/plans/chatbot/evidence/sales-report/`, `documentation/user-guides/system-management/chatbot-sales-report.md` |

Phase 1 for a chatbot lane is the presenter against a mock: the "UI" is the WhatsApp text and
the goldens are its screenshots. The owner reviews the goldens before S2 starts.

## S4 wiring, smallest shape that fits each seam

1. **Parser** (`chatbot_parser_prompt.py`, published by one migration the way 514 was):
   `order_status` gains `sales_report` (sales report / sales performance / how much did X
   buy); a new nullable output field `sales_channel` = `dealer` / `project`, emitted only on
   a sales report ask. Allowlisted in `contracts.py`. The prompt says in words that "dealer"
   inside an outstanding ask is NOT a channel. No word table anywhere in Python (S12).
2. **Carry**: `sales_channel` is persisted and restored beside `order_status` /
   `date_filter_*` in the SAME reuse arm R16 built (`tail/compile_state.py` write,
   `head/output_exchange.py` reuse branch), so a customer-picker answer ("1" or "all")
   continues the ask with its channel. Same rule, same justification, no picker branch.
3. **Tool pick** (`fetch.py`): domain `order` AND (resolved product OR resolved customer) AND
   `order_status == "sales_report"` picks `crm_sales_report`. One more branch beside the
   outstanding one.
4. **Gate** (S10): before any fetch, read `ctx["access"]["attributes"]` for
   `sales_orders.sales_report` the way the outstanding lane reads its key. Absent = the
   denial line, no fetch, no offer. There is no fallback scope here, so this is simpler than
   D13.
5. **Params**: `DATE_PARAMS["crm_sales_report"] = ("date_from", "date_to")`; customer to
   `customer_ids`; location through the existing warehouse resolver to `warehouse_codes`;
   product through `outstanding_product_code` (typed code wins); `sales_channel` to `channel`.
6. **No scope question.** The report always prints confirmed AND outstanding, so nothing like
   `outstanding_scope` exists here.
7. **Detail offer** (S11): a hit arms `selection_context = "sales_report_detail"` with one row
   (`Sales order list`, `so`) and stores the filter set plus `tool: "crm_sales_report"`. The
   arms that today test `kind == "outstanding_detail"` (`tail/pending.py::derive`,
   `compile_state._offer_carry`, `output_exchange._apply_outstanding_pending`, the parser
   option-label feed in `head/parser.py::build_user_block`) test membership in ONE shared
   constant `DETAIL_OFFER_KINDS = {"outstanding_detail", "sales_report_detail"}`, and the
   re-run reads the tool name from the stored filter set (absent = `crm_outstanding_report`,
   so sessions open at deploy keep working). Sticky, decline, reprint-once, refinement and
   new-ask behaviour arrive for free and are pinned for both kinds by one parametrized test
   family (AC-1655, AC-1656). This honours the standing note "no NEW open-question kind arm
   is added to the head": a kind value is added, an arm is not.
8. **Picker hint off** (`resolve_gate.py`): R20's rule extended to `sales_report`, same
   reason (the has-DO probe measures a different population).
9. **Header skip** (`compile_state._search_scope_header`): skipped for `crm_sales_report`.
10. **Miss**: no months returns the presenter's miss text through `not_found_error_message`,
    so the escalate offer follows unchanged.

## Tester's list

S1: AC-1601 `test_customer_subject_golden`; AC-1602 `test_month_block_line_order`; AC-1603
`test_absent_axis_prints_all`; AC-1604 `test_breakdown_heading_follows_subject` (3 cases);
AC-1605 `test_hit_ends_with_offer_sentence`; AC-1606 `test_detail_golden`; AC-1607
`test_miss_prints_no_offer`; AC-1608 `test_presenter_keeps_input_order`; AC-1609
`test_money_format_and_no_dashes`.

S2: AC-1620 `test_ordered_equals_confirmed_plus_outstanding`; AC-1621
`test_over_delivered_capped_and_zero_qty_safe`; AC-1622 `test_bucket_required_date_else_order_date`
and `test_so_counts_in_each_month_it_has_lines`; AC-1623 `test_date_window_on_bucket_date`
and `test_bad_date_422`; AC-1624 `test_months_latest_first_and_breakdown_ranked_and_tallies`;
AC-1625 `test_channel_filter` (dealer / project / none / null-class / bad value); AC-1626
`test_subject_required`; AC-1627 `test_product_exact_and_warehouse_filter`; AC-1628
`test_breakdown_key_follows_subject`; AC-1629 `test_detail_rows_roll_up_sorted_and_tally`;
AC-1630 `test_customer_echo_shared`; AC-1631 `test_response_model_keeps_every_field`; AC-1632
`test_auth_401_403_apikey_and_company_scope`.

S3: AC-1640 `test_catalog_lists_sales_report_tool`; AC-1641 `test_reveal_key_declared_and_pinned`;
AC-1642 `test_bootstrap_idempotent`.

S4: AC-1650 `test_tool_pick_sales_vs_outstanding`; AC-1651 `test_no_key_denies_before_fetch`;
AC-1652 `test_channel_from_parser_only`; AC-1653 `test_param_mapping`; AC-1654
`test_hit_arms_sales_report_detail`; AC-1655 `test_detail_offer_lifecycle[kind]`; AC-1656
`test_refinement_and_new_ask[kind]`; AC-1657 `test_picker_no_do_hint_and_pick_continues`;
AC-1658 `test_miss_takes_not_found_path`; AC-1659 `test_header_skipped`; AC-1660
`test_parser_prompt_live_sha`.

All backend tests on Postgres via `tests/_pg_fixture.py`, seeding their own company,
customer, product, warehouse, SO and lines (CI's database is empty).

## Design brief

Surface: WhatsApp text, read a few times a day by management and sales. Nothing animates. No
emoji. Bold italic on month titles and breakdown sub-headings, bold on detail-row labels (the
existing convention). Contacts > Access gains one row through its existing list; no new UI.

## Definition of done notes

- No new column, so no dict-builder sweep. No new permission slug: the route reuses the
  outstanding report's view permission; the per-contact key is a reveal key, not RBAC, so
  there is no role grant sweep. The owner ticks the key per contact after deploy.
- After deploy: prompt republish (the migration does it), MCP restart so `sync_catalog`
  seeds `crm_sales_report`, then the console check against production.

## Risks

- Length. HANLIM over all dates is 37 months at about 80 products each, about 3,000 lines.
  Ruled acceptable (S5); n8n chunks. Backlog trigger recorded in the UAC.
- `required_date` is a REQUESTED date: 10,542 lines dated 2026 sit in future months, out to
  2032. Printed as they are (S3).
- 22,717 priced lines dated 2026 carry `line_total = 0`. They count quantity at RM 0.00. If
  the owner sees a month whose value looks low, this is the first place to look.
- `output_exchange.py` is the file every chatbot lane collides in. The change there is a
  membership test and a tool-name read, kept that small on purpose.
