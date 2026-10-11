# PLAN: REPORTS-STEP2 (one crm_report tool, one report entry in Focus)

Status: S1 building (owner answered Q1-Q5 on 11 Oct: Q1 low-stock split, Q2 extend the engine, Q3 crew labels ~40 report cases, Q4 tie -> meaning output, Q5 carried values in one header line, rankings still ask). Track: L, lead pattern. Card:
`CARD-reports-step2-11oct.md` / `.html`. Architecture: `ARCH-scalable-chat-11oct.html` step 2. Prior: PROMPT-DIET
stopped at DP1 (PR #1568 measurement record).

## Shape (pending rulings)

1. `crm_report` = the report_ask engine extended (REPORT-ENGINE plan section 7 fold path): measure / dimension
   registry rows in code, seeded from `reports/ask.py` CATALOGUE; rows for DO pending (outstanding service),
   delivered-by-DO-date (delivery_order_lines dataset) and sales value (sales_yearly). Grants, dealer keys,
   `enforce_customer_scope`, rate limits and presenters reused unchanged. MCP ToolSpec + chatbot allow-list.
2. Low stock SPLIT (owner Q1): the read side (`build_low_stock_view`, `scm/low_stock_report_service.py:271`, over
   `reorder_run_service.today_or_latest_run` :466) becomes a `low_stock` measure in crm_report; generating a reorder
   run + Excel (`low_stock_report.py` create_run :483, download :507, enqueue :519-536) stays the action tool. The
   sales-analysis Excel stays.
3. One report entry in Focus (no new state system): Focus.sales_ranking_frame extended to every report kind,
   owned by the report intent (INTENT-FRAMES); refine = patch, switch = fresh entry; absorbs Focus.top_selling and
   the outstanding detail-offer carry.
4. Provenance header lines for carried / defaulted values (CLARIFY-POLICY P1 where allowed).
5. Parser: one static report section replaces the report addenda; contract F (meaning output) vs T (tool
   params) prototyped and decided on the report eval slice, tie -> F.

## Slices (after rulings)

S1 registry + crm_report over the existing ask engine (sales rows) behind the allow-list, red tests first.
S2 Focus report entry + refine/switch + provenance. S3 F and T prompt sections + DEV/report-slice runs. S4 remaining rows
(outstanding SO/DO, delivered by DO date, sales value) + `so_count` timed add for DP2. Security review (scope,
dealer keys) and reviewer per slice set.

## DP2

Report-topic pass +10 pt on the report slice, p95 <= 8 s, 30-case smoke no topic worse, new measure <= 1 day.

## Not doing

No grant / scope / presenter change, no UI, no registry table, no prompt diet.
