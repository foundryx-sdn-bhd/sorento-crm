# PLAN: a linked contact's sales asks run the customer sales report

Status: in progress on PR #1401, FULL track (mock v4 approved 2 Oct 2026; v4 built, review fix round 1 done, hand test filed) (crew relabel, 30 Sep 2026: an additive grant
migration on an RBAC role plus a diff well over 300 lines; the earlier "small fix" label was
wrong). The engine/resolver half of the lane is PARKED on PR #1403 (owner decision, 30 Sep
2026: the parser prompt is fixed in production first).
Lane: CHATBOT-SELFREF-SCOPE. Parent: `PLAN-chatbot-customer-scope-29sep.md` (PR #1365).
UAC: `selfref-scope-acceptance-criteria.md`.

## Symptom (production, 30 Sep 2026)

A contact linked to six HANLIM TRADING customers asked "I want to check my sales for this
month" and got the customer-scope refusal line at 0 ms (trace one). After "clear all" the
same question ran as `order_status="sales_analysis"` and came back "Could not run the sales
report right now." with `has_result=true` and `error=null` on the trace (trace two).

## Trace one (parked, PR #1403)

Replayed in the engine harness with the real resolver against the owner's focus verbatim:
the carried category token `water tap` reached the resolver, which re-types a category
token as a customer under `order` (`entity_resolver._DOMAIN_HINT_EXPANSIONS`, introduced by
`2aa63ce8a` for a TYPED customer name the n8n agent had hinted brand/category) and
fuzzy-matched two accounts nobody named; the picker tripped `_screen_resolver_for_scope`'s
offer rule. The parser had emitted `domain_in_message=false` beside `order_status=sales_report`
("sales" is not in the prompt's DOMAIN IN MESSAGE word list), so the engine carried the
standing subject. Owner's call: fix the parser prompt in production first; the engine and
resolver work (carried context keeps its kind, typed-word-only refusal) waits on #1403.

## Trace two (this PR)

- B1 `/api/v1/sales/analysis` is gated on `sales.reports.view` for the API key's act-as
  principal; `sales_s1_reports_module` granted it to admin and superadmin only, so the n8n
  key's role `integration_n8n` was answered 403.
  `alembic/versions/selfref_0001_n8n_sales_view.py` grants it (idempotent);
  `scripts/bootstrap_env.py` replays it for a create_all database.
- B2 Even once granted, the analysis route refuses any contact linked to a customer
  (AC-S1-23): a company's totals are not a linked contact's figures. `run_fetch` now
  answers a customer-scoped contact's `sales_analysis` ask with `crm_sales_report` over the
  linked customer ids (parent plan Q6a, Q8a). Links win whatever the tier (owner hand
  test "Mr Loo", an office contact also linked to A/C II): a linked staff contact is not
  forced by the engine, so the lane hands its links over as the subject itself; only a
  contact with NO links keeps `crm_sales_analysis`.
- B3 `sorento_crm_mcp.http_client` returned the 403 body verbatim and
  `presenters._sales_analysis_envelope` rendered any non-status body as "Could not run the
  sales report right now." with `has_result: true`. `http_error_body` now stamps
  `status_code`, `error` and `http_error` on every non-2xx body and `present_response`
  answers an error envelope (`has_result: false`, `error`, `status_code`, `detail`) for every
  presenter tool, which the lane records as a tool error on the trace.
- The reply is TEXT. `crm_sales_report` renders the presenter's month blocks
  (`sorento_crm_mcp/presenters.py::_sales_report_envelope`) and carries no attachment
  (`fetch._sales_report_output`); the Excel belongs to `crm_sales_analysis` alone (owner
  ruling 26 Sep 2026, "always text + file"), which a linked contact no longer reaches.
- R4 Every scope decision writes a `customer_scope` trace event (the gate's and the
  screen's refusal with the ids dropped, the scoped-to-links pass, the fetch and tier-probe
  refusals, the B2 re-route). `trace_detail.compose_trace_detail` projects them and the chat
  history turn drawer prints a "Customer scope" panel: the event existed before but no panel
  read it, which is why the owner's trace showed nothing for it.

## Hand test follow-ups (owner, 30 Sep 2026, crew test copy)

- "my sales" means ALL the asker's linked accounts (parent plan Q6a): a customer id the
  conversation carried from an earlier report never narrows a self-reference ask on either
  report (`fetch._self_reference`, both report arms); traced as
  `customer_scope` `decision: self_reference_uses_own_ids_not_the_carry`.
- A rendered sales report with no month on the asker's OWN accounts is a final answer,
  "No sales found.", and arms nothing (`fetch._sales_report_output`); a miss on a named
  product or customer keeps AC-1658's escalate offer.
- The header line "Delivery date" is now "Required date (or order date)", the bucket the
  report filters on (`sales_report_service._bucket_expr`): fixtures, doc samples, the
  console case and the sales report UAC follow. The top selling header is untouched.
- The failed hand-test run had used a copy contact linked to A/C II only; the script now
  keys its SQL on the console contact's Respond.io id and lists the six accounts.

## Grill (feature skill step 2, run late on crew's instruction, 30 Sep 2026)

Twenty decisions the reshape makes were sent as one crew-ask on PR #1401 (comment
"crew-ask: GRILL of the sales report reshape"), each with a recommendation. Answers are
recorded here as they land; until then the recommendation is what is built.

| # | Decision | Recommendation | Owner |
| --- | --- | --- | --- |
| 1 | Grain: 1 to 7 days by day, 8 to 31 by week, longer or no window by month | as is | pending |
| 2 | Week = Monday to Sunday, clipped to the window, `dd/mm to dd/mm` | as is | pending |
| 3 | Only buckets with a delivery print | yes | pending |
| 4 | Channel from the linked accounts' demand classes; one class = fixed, no line | as is | pending |
| 5 | Location capped to the stock visibility policy; line only when named | as is | pending |
| 6 | Named location outside the policy: report over the allowed ones | report | pending |
| 7 | Policy `[]` does not cap the sales report | no cap | pending |
| 8 | SO list no longer offered; DO list replaces it | as is | pending |
| 9 | Caps of 10 with "and N more"; no "reply all" | as is | pending |
| 10 | DO rows name the account only when the scope holds several | as is | pending |
| 11 | A drill prints a one-line title, not the header | as is | pending |
| 12 | Typed picks via roster aliases plus parser reference_positions | as is | pending |
| 13 | Out-of-range re-ask wording; aside keeps, new question drops | as is | pending |
| 14 | One-day reply prints Total and the day line | keep both | pending |
| 15 | Unpriced DO lines: silent in the reply | silent | pending |
| 16 | Amount: ex-tax, else total, else qty x unit price (DO, else SO) | ex-tax first | pending |
| 17 | Cancelled, deleted, undated DOs never count | as is | pending |
| 18 | A refinement re-runs the default view and re-offers | as is | pending |
| 19 | Staff and n8n get the same delivered shape (the triple leaves the reply) | one shape | pending |
| 20 | One account hides By customer; one product hides By product | as is | pending |

## Design note: one dimension/measure model (owner, 2 Oct 2026)

Owner, on approving mock v4: do not design for one case; a new perspective such as "top
sales agent" must be a new dimension, not new code, so the parser can later map intent onto
it. Q1 to Q5 of mock v4 answered (a) (`documentation/mockups/sales-report/index.html`).

**The model already exists; this lane adds a dataset to it, not a second engine.**
`app/services/reports/registry.py` declares a report as a `Dataset` of `Column`s tagged
`dimension` or `measure`, `SelectParam` filters (each one a predicate), a `PeriodParam`
and a pivot view (`rows` dimension x `cols` dimension x `measures`);
`app/services/reports/engine.py::run` executes any view in grouped SQL, company scope
fail-closed (`_predicates`). The chatbot's `crm_sales_analysis` already runs on it
(`app/api/v1/sales/analysis.py:224`, a rows/cols/measures view over
`datasets/sales_order_lines.py`).

New: `app/services/reports/datasets/delivery_order_lines.py`, one row per DO line:

- Rows: `orders` + `order_lines`, DO date (`orders.order_date`), not cancelled, not
  deleted, replacement DOs (number `REP...`) left out (Q2 a).
- Measures: `amount` (Q1 a: the DOC total once for a legacy DO, which writes it on every
  line; the line total for an AutoCount `db1` line; a line's share of its DOC total is
  weighted by qty x unit price x (1 - discount), computed once in the dataset's base
  subquery so every grouping sums to the same Total), `qty`.
- Dimensions: `customer`, `product`, `sales_agent` (the DO's SO's agent), `location`
  (`order_lines.warehouse_id`), `channel` (the account's `market_segment_code`, project /
  contract = Project, any other = Retail, `demand_class.class_of`'s rule in SQL; no segment
  = in every channel), `day`, `week` (Monday to Sunday), `month`, `delivery_order`, and a
  constant `all` (the one-column side of a one-dimension view).
- Filters (`SelectParam`): `customer` (the contact's links), `product`, `location`,
  `channel`. Company: the companies of the scoped accounts as the run's grant.

The chatbot customer sales report = `engine.run` with one view per reply:

| Ask | rows | cols | filters |
| --- | --- | --- | --- |
| default (Total + per-period lines) | `day` / `week` / `month` from the window (today and a week by day, a month by week, longer by month) | `all` | links, policy locations |
| By customer / By product | `customer` / `product` | `all` | same |
| Delivery orders | `delivery_order` | `all` | same |
| "project only", "at WH-A" | (as above) | `all` | + `channel` / `location` |

The route (`GET /order-management/sales-report`) gains `group_by` validated against the
dataset's catalog dimensions; the presenter ranks by `amount`, caps at 10 with "and N
more", and prints the v4 lines. The drill options are the catalog dimensions the reply
offers (By customer only when the scope holds more than one account).

**"Top sales agent" under this model:** `group_by=sales_agent`, ranked by `amount`, top N.
No new code: the dimension is in the dataset now; what is missing is the parser mapping
"top sales agent" onto `group_by` and the decision who may ask it (staff), both later.

**Where access is enforced:** the route, when it carries a contact, resolves that contact's
stock visibility policy (`stock_visibility.resolve_policy`): a named location outside it is
refused (Q3 a, "Sorry, <location> isn't one of the locations you can check."), an empty
include list answers no rows (Q4 a). An empty multi-select means "no filter" in the engine
(`engine._predicates`), so the empty-policy case is answered before the engine runs, never
passed through as `[]`.

**NOT built now** (each with the trigger that builds it):

- A DO count measure: the engine sums every measure (`_pivot`, `func.sum`); a distinct
  count needs an aggregate kind on `Column`. Trigger: the first reply that prints a count.
- The new dataset on the Reports screen: the definition is run directly, not
  `reg.register`ed, so the screen's list does not change. Trigger: the owner wants it there.
- Parser intent to dimension mapping (free "group by X" asks): the drill options and
  `order_status=sales_report` cover today's asks. Trigger: the parser prompt work
  (PROMPT-DYNAMIC) adds the sales domain.
- Nested drills and a second measure in one reply (v3 annotation 8 withdrew nesting).

## Report shape (owner ruling 30 Sep 2026, mock v2)

`documentation/mockups/sales-report-drilldown.html` @ 044bc23ea is the agreed reply, pending
"mock ok". Built as: `sales_report_service._delivered_block` (the DO documents by delivery
date, bucketed by `delivered_bucket_for_window`, the grouped views and the DO list, and the
numbered `options` roster: ONE writer for the numbering), `SalesReportDelivered` on the route
body, `presenters._sales_report` (the reply) and `_sales_report_envelope` (`options` for the
lane), `fetch._sales_report_output` (the roster becomes the `sales_report_detail` open
question, with typed aliases), `fetch.entity_ids_transformer` (a pick maps to `group_by` /
`detail=do`; the channel and location rules of grill items 4 to 7), and
`_outstanding_detail_reoffer` (the re-ask wording).

## Tests

- `tests/chatbot/test_customer_scope_lane.py`: B2 (linked, unlinked, staff), the trace
  records (scoped, gate refusal, resolver refusal with dropped ids).
- `tests/chatbot/test_customer_scope_fetch.py`: `ScopeViolation.dropped`, the fetch and
  tier-probe refusal traces.
- `tests/chatbot/test_sales_analysis_lane.py`: B3 through `run_fetch` with the real presenter.
- `tests/chatbot/test_rearch_s3_trace.py`: the `customer_scope` projection.
- `tests/test_migration_selfref_0001_n8n_sales_view.py`: B1.
- `sorento_crm_mcp/tests/test_http_error_envelope.py`: B3 at the MCP.
- `TurnDetailDrawer.test.tsx`: the panel.
