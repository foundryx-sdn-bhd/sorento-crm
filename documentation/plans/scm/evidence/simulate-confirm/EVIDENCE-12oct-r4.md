# Evidence round 4: board Simulate confirm v2 (adopts never-planned orders, rolled back)

Code head: 69852cd64. agent-browser 0.27.0 (sessions simr4, simr4p), backend restarted on this tree
(`SORENTO_ENV_FILE=.env.ci-tests uvicorn app.main:app --port 8000`), FE `npm run dev`, db sorento_test. Redis had stopped and was restarted.

| # | Item | Result |
|---|------|--------|
| 1 | Owner case: never-planned order | PASS. ZZT-CORE-IMQD (core SO, no projects.sales_orders row, 2 saved Approved drafts) read "would confirm as revision 1" with "Would write line 1 / line 2: pool 50 ..." and "Would confirm: line 2 ZZT-IMQC · line 1 ZZT-IMQC"; no "not being planned yet" text. Only POST /simulate-confirm fired (no /adopt). After: planning records for that SO 0, projects.sales_orders rows 6 -> 6, decisions 0, inquiry rows 0, drafts 10 -> 10. After reload the board still lists it with no planning record (API: project_sales_order_id null) |
| 2 | Two orders, one pool (50) | PASS. ZZT-CORE-IMQH (A, reserve 50): clean. ZZT-CORE-IMQJ (B, reserve 50): "would confirm" + "Line 10, ZZT-IMQG: Reserve 50 at SIM-BRW-HXGE confirmed as decided; only 0 free at SIM-BRW-HXGE when confirmed, 50 short" (B sees A's hold) |
| 3 | Regression r2-r3 | PASS. Whole quantities ("pool 50"), "Would confirm: line ...", stock-short on ZZT-EWXC (30 free, 20 short), cancel-only ZZT-CORE-EWXI "would transfer 1 line to Cancel SO" / "Would transfer to Cancel SO: line 10 ZZT-EWXH" with no "Would write" under it; planner gear menu "Undo all", "Back to sales orders" only, direct POST 403 `simulate_admin_only`; 375px block 16..344 of 375, no overflowing descendant, document width 360 |
| 4 | "has nothing to confirm" | NOT RUN. The wording appears only for an ok order whose answer has no lines_confirmed / lines_withdrawn / lines_cancelled / fulfilled-skipped and no cancel or batch (`pressHasSomething`); I found no way to make the server answer ok with every posted line absent from `lines_confirmed` from seed data in the time box, and app code may not be edited |
| 5 | Nothing escapes | PASS. Before and after all presses (1280 and 375, plus planner/403 attempt): email_outbox 0/0, automation_runs 0/0, notifications 0/0, notification_deliveries 0/0, audit_logs 64/64 |

## Method
Seed (scratchpad `seed4.py`, committed rows): NEVER = core SO + 2 core lines, no project SO; COMPA/COMPB = two adopted orders, 1 line of 50 each, one product, pool stock 50.
Drafts through PUT `/api/v1/project-sales/fulfilment-planning/lines/{key}/draft`: Approved on NEVER lines; amended `reserve` 50 from the pool (with warehouse_id) on A and B.
Walk: sign in, sidebar Supply Chain > Project Demand > Fulfilment Planning, select all rows, Plan together ("Confirm (9)"), Board actions > Simulate confirm. 7 of 7 orders would confirm. `errors` empty.
Counts query: scratchpad `counts4.sh` (planning-record count for IMQD, projects.sales_orders, decisions, drafts, inquiry rows, transfers, email_outbox, automation_runs, notifications, notification_deliveries, audit_logs); identical before and after.

Screenshots: admin-never-planned-1280-r4.png (palette-reduced to fit 200 KB), admin-stock-short-375-r4.png, planner-menu-1280-r4.png
