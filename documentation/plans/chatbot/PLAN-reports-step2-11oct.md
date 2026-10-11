# PLAN: REPORTS-STEP2 (one crm_report tool, one report frame)

Status: card filed, awaiting owner rulings Q1-Q5 (11 Oct 2026). Track: L, lead pattern. Card:
`CARD-reports-step2-11oct.md` / `.html`. Architecture: `ARCH-scalable-chat-11oct.html` step 2. Prior: PROMPT-DIET
stopped at DP1 (PR #1568 measurement record).

## Shape (pending rulings)

1. `crm_report` = the report_ask engine extended (REPORT-ENGINE plan section 7 fold path): measure / dimension
   registry rows in code, seeded from `reports/ask.py` CATALOGUE; rows for DO pending (outstanding service),
   delivered-by-DO-date (delivery_order_lines dataset) and sales value (sales_yearly). Grants, dealer keys,
   `enforce_customer_scope`, rate limits and presenters reused unchanged. MCP ToolSpec + chatbot allow-list.
2. Low stock and the sales-analysis Excel stay as they are.
3. One report frame in focus, owned by the report intent (INTENT-FRAMES); refine = patch, switch = new frame;
   replaces sales_ranking_frame, Focus.top_selling and the outstanding detail-offer carry.
4. Provenance header lines for carried / defaulted values (CLARIFY-POLICY P1 where allowed).
5. Parser: one static report section replaces the report addenda; contract F (semantic frame) vs T (tool
   params) prototyped and decided on the report eval slice, tie -> F.

## Slices (after rulings)

S1 registry + crm_report over the existing ask engine (sales rows) behind the allow-list, red tests first.
S2 frame + refine/switch + provenance. S3 F and T prompt sections + DEV/report-slice runs. S4 remaining rows
(outstanding SO/DO, delivered by DO date, sales value) + `so_count` timed add for DP2. Security review (scope,
dealer keys) and reviewer per slice set.

## DP2

Report-topic pass +10 pt on the report slice, p95 <= 8 s, 30-case smoke no topic worse, new measure <= 1 day.

## Not doing

No grant / scope / presenter change, no UI, no registry table, no prompt diet.
