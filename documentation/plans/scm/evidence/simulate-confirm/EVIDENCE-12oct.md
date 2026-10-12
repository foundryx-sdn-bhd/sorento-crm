# Evidence: board Simulate confirm (agent-browser run, 12 Oct 2026)

Lane crew/simulate-confirm, cloud sandbox. Backend `SORENTO_ENV_FILE=.env.ci-tests uvicorn app.main:app --port 8000`
(db sorento_test), FE `npm run dev` on :3000 with an untracked, gitignored `.env.local`
(NEXTAUTH_SECRET = backend JWT_SECRET, FASTAPI_INTERNAL_URL=http://localhost:8000, NEXT_PUBLIC_API_URL unset).
Browser: agent-browser 0.27.0, own sessions `simconfirm` / `simplanner`, Chromium from /opt/pw-browsers.

## Seed (script outside the repo, committed rows)
admin user (role admin), planner user (custom role sim_planner with projects.projects.view + edit only),
project, 1 project SO adopted (2 lines, 50 each), pool stock 1000, two Approved drafts saved through
PUT .../lines/{key}/draft. All tenant_modules rows inserted so the sidebar shows modules.
Needed fixups: company_id stamped on products/warehouses/stock (helpers leave it NULL; board read empty otherwise).

## Admin, 1280px
Nav: sidebar Supply Chain > Project Demand > Fulfilment Planning (the Project Sales group in this layout has no
Fulfilment Planning entry), tick ZZT-CORE-HXGF, Plan together. Board shows "Confirm (2)".
Board actions menu: Undo all, **Simulate confirm**, Back to sales orders.
Click Simulate confirm: POST /api/v1/project-sales/fulfilment-planning/simulate-confirm 200. Results block:
"Simulation, nothing was saved: 1 of 1 orders would confirm / ZZT-CORE-HXGF: would confirm as revision 1 /
Would write / line 10: pool 50.0000 SIM-BRW-HXGE -> SIM-OWN-HXGD / line 20: ..."
DB counts (decisions, drafts, inquiry_rows, transfers, audit_logs) around a press, measured idle:
before `0 / 2 / 0 / 0 / 55`, after `0 / 2 / 0 / 0 / 55`; audit_logs grouped by entity/action identical.
(An earlier 31 -> 55 audit jump was login + board reads, not the press.)
Reload: "Confirm (2)" still shown, drafts 2, decisions 0, no revision. `errors` empty.
Screenshot: admin-results-1280.png

## Planner
Gear menu: Undo all, Back to sales orders (no Simulate confirm). Screenshot: planner-gear-menu-1280.png
`curl -X POST /api/v1/project-sales/fulfilment-planning/simulate-confirm` with planner token:
403 `{"code":"simulate_admin_only","message":"Simulate confirm is for admins only."}` (twice).

## 375px (admin)
Simulate run again: block rect 16..344 of 375, no descendant with scrollWidth > clientWidth, document
scrollWidth 360 (no horizontal scroll); lines wrap. Screenshot: admin-results-375.png
Counts unchanged after this press too (0 / 2 / 0 / 0 / 55).

## Not covered
Failing-order path (simulated_errors lines, `line N ITEM: error`) and batch path: the seeded order confirms cleanly,
so those are covered only by the backend/vitest tests.
