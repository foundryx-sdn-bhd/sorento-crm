# Evidence round 3: board Simulate confirm

Code head: 34a5032c7 (docs-only 61c40ed01 on top). agent-browser 0.27.0 (sessions simr3, simr3b, simr3p2), backend restarted on the
merged tree (`SORENTO_ENV_FILE=.env.ci-tests uvicorn app.main:app --port 8000`), FE `npm run dev`, db sorento_test.
Postgres/Redis had to be restarted (`pg_ctlcluster 16 main start`, `redis-server --daemonize yes`).

| # | Item | Result |
|---|------|--------|
| 1 | Whole quantities + "Would confirm: line ..." | PASS: "pool 50 SIM-BRW-HXGE -> SIM-OWN-HXGD", "Would confirm: line 10 ZZT-HXGC · line 20 ZZT-HXGC" |
| 2 | Stock-short | PASS: "would confirm as revision 1", "Line 10, ZZT-EWXC: Reserve 50 at SIM-BRW-HXGE confirmed as decided; only 30 free at SIM-BRW-HXGE when confirmed, 20 short". Counts unchanged |
| 3 | Cancel-only order | PASS: "ZZT-CORE-EWXI: would transfer 1 line to Cancel SO", "Would transfer to Cancel SO: line 10 ZZT-EWXH", and NO "Would write" heading for it (the other orders still have theirs) |
| 4 | Already-fulfilled line | PASS, but only reachable with a stale board: see below. "1 line already fulfilled, decision would be cleared"; the fulfilled line is absent from Would write / Would confirm; saved drafts still 6 afterwards |
| 5 | Error-line format | PASS via seed data, no code edits (see below) |
| 6 | Planner | PASS: gear menu only "Undo all", "Back to sales orders"; direct POST: 403 `simulate_admin_only` |
| 7 | 375px stock-short | PASS: block 16..344 of 375, no wider descendant, document scrollWidth 360 |

## Item 4 detail
The board hides a line whose plan quantity is 0 (`is_undecided_demand`), so a freshly loaded board never posts a fulfilled line.
Reached it as the real race: board open with 2 approved lines (order ZZT-CORE-AARD), then `update sales_order_lines set qty_required=0`
on line 10's core line (the book moved while the page was open), then Simulate on the SAME page (simulate does not refetch).
The server skipped line 10 as fulfilled; the block read "1 line already fulfilled, decision would be cleared"
(no ": line 10" name list was appended). Draft rows unchanged (drafts 6).

## Item 5 detail (seed data only)
Inserted a pending batch (id 11111111-...) with 2 `delayed` rows on order ZZT-CORE-HXGF, decision `confirm`; line 10's row has
`held_json = "malformed"` (a JSON string), line 20's `{}`; `facts_json {}`. Reached via sidebar Planning changes > clicked the batch row.
That row link opened `?orders=ZZT-PSO-HXGG&batch=...` (the order's PROVISIONAL REF instead of the SO number) and the board said
"No lines to plan on these sales orders"; I then opened `?orders=ZZT-CORE-HXGF&batch=<id>` directly (the one deep URL in this round) and pressed Simulate:
```
Simulation, nothing was saved: 0 of 1 orders would confirm
ZZT-CORE-HXGF: AttributeError: 'str' object has no attribute 'get'
line 10 ZZT-HXGC: AttributeError: 'str' object has no attribute 'get'
line 20 ZZT-HXGC: The components add up to 50 and the line is open for 0.
```
Format `line N ITEM: <Class>: <message>` confirmed. Batch rows stayed `pending` (2), counts unchanged (0/6/0/0/60).
Two observations for the reviewer: (a) the batch row link used the provisional ref, which the board does not resolve (may be my
seed: PSO has so_id but the list builds the link from something my rows lack, `board_link` is empty); (b) line 20 is also listed
as failing, with a named refusal. Probably my `facts_json {}` (open qty reads 0), not a product defect, but the probe's
"names only the failing line" claim was not demonstrated cleanly here; test_the_probe_names_only_the_line_that_fails_alone covers it.

## Counts
decisions / drafts / inquiry_rows / transfers / audit_logs: 0/6/0/0/60 before and after every press this round.

Screenshots: admin-error-line-1280-r3.png, admin-stock-short-375-r3.png, planner-menu-1280-r3.png
