# PROMPT-MERGE-1531: hand-merge the drill-down block into the prod parser prompt

Lane REPORT-DRILLDOWN, PR #1531. The prod `chatbot_semantic_parser` prompt was edited by hand by
the owner, so this PR never touches prod: `report_drill_0001_prompt` publishes the branch's whole
prompt as a NEW version with the `production` label unmoved. Prod gets the drill-down reading by
these three edits, made by hand in the admin prompt editor on a COPY of the current production
version, published as a new version, then the label moved onto it.

Ship it in the same release as the code (PR body "Release condition"): without it the parser never
emits `ranking_drill`, so every ranking on a new axis right after a ranking gets the scope
question.

All three edits sit in the SALES RANKING block (the one that starts
`== SALES RANKING: order_status "sales_ranking", domain_hint "order" ==`). Copy the text exactly,
line breaks included. The source of truth is `REPORT_ASK_ADDENDUM` in
`sorento_crm_backend/app/services/chatbot_parser_prompt.py` on this branch.

## Edit 1: a different ranked noun is never a refine

Find this sentence (it ends a line and the refine list follows it):

```
(reference_positions []) and the ask stays "sales_ranking". A message that ONLY changes:
```

Replace it with:

```
(reference_positions []) and the ask stays "sales_ranking". First the RANKED noun: one that is
not the previous ranking's (customers after sales agents, products after customers) is
never a refine, whatever else it says: ranking_refine false, group_by that noun, ranking_drill below.
"top 10 customers for these sales agents" -> group_by "customer" (the noun after "top 10"),
never "sales_agent": "these sales agents" names the rows, not the axis. A message that ONLY changes:
```

## Edit 2: the `ranking_drill` reading

Find these two lines:

```
A singular "top salesman", "top customer" or "best sales agent" names NO count: top_n null
(not 1), also after a ranking.
```

Insert directly AFTER them (before `A ranking of PEOPLE or ACCOUNTS ...`):

```
"ranking_drill": "scoped|global|unsure|null": set only on a sales_ranking naming its OWN group_by
right after a SALES RANKING answer, else null:
  - "scoped": it asks about the rows that ranking listed ("these", "their", "those", "them",
    "这些", "above"): after top sales agents, "who are the customers for these sales agent" ->
    group_by "customer", top_n null; "top 10 customers of these agents" -> top_n 10; dates null;
    entities only for a filter the message itself names (the listed rows and earlier filters
    carry by themselves). Products or categories too: "which products do these agents sell" ->
    order_status "sales_ranking", group_by "product", "scoped" (never top_selling)
  - "global": ONLY when it says so ("overall", "all customers", "whole company") or names its own
    subject ("top 10 customers for Cabana this year")
  - "unsure": either reading fits: "top 10 customers", "customers", "top customers" with nothing
    tying it to the rows or saying overall. In doubt, "unsure", never "global"
  - a reply to "... above, or across all customers?": "above", "those", "them" -> that
    question's ranking, "scoped"; "all", "overall" -> "global"; group_by AND top_n come from
    that question (the one exception to the top_n rule)
```

## Edit 3: products and categories drill, never top selling

Under `NOT a sales ranking:`, find:

```
  - ranking PRODUCTS or CATEGORIES ("top 10 products", "hot selling", "which category
    sells most") stays "top_selling";
```

Replace it with:

```
  - ranking PRODUCTS or CATEGORIES ("top 10 products", "hot selling", "which category
    sells most") stays "top_selling", except a "ranking_drill" ask right after a SALES RANKING;
```

## Check after publishing

The structured-output schema already declares `ranking_drill` (code, not prompt), so nothing else
changes. In the console, on the new version:

1. `top 3 salesman for SSFH14 this year, delivered, by amount` -> 3 agents.
2. `top 10 customers for these sales agents` -> runs at once, header line
   `Customers assigned to: <the 3 agents>` and `Product: SSFH14`.
3. Start again with step 1, then `top 10 customers` -> `Top 10 customers of the 3 sales agents
   above, or across all customers?`.

Rollback: move the `production` label back to the previous version.
