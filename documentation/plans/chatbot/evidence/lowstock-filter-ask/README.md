# LOWSTOCK-FILTER-ASK cloud browser pass (3 Oct 2026)

Code under test: `d381248e` (branch `claude/lowstock-filter-chatbot-j8ad0t`). The commit that
adds this folder is docs only.

## Stack (all inside the cloud sandbox)

- Postgres 16 `sorento_browse`, built by `scripts.bootstrap_env`, then the synthetic seed in
  `harness/` (no customer data): admin login, contact 437264483 with `scm.low_stock_report` +
  `purchase_orders.supplier`, categories SRT-WC / CB-WC / M-WC / BRT-WC / IDC-WC / SRT-FT /
  CB-FT, suppliers XIAMEN TAIYANG TECHNOLOGY CO.,LTD (400-X006, 400-X008), JINBAICHUAN
  TRADING, JINBAICHUAN HARDWARE, six products below their reorder level at a pooled BRW,
  each with one received past PO naming its supplier.
- Backend :8000 (`harness/run_backend_stubparser.py`), MCP :8765, RQ worker
  (`harness/run_worker_local.py`), frontend `npm run dev` :3000, agent-browser 0.27.0 on the
  sandbox Chromium.
- Modules installed through `POST /api/v1/system/modules/install` (a fresh DB has none).

What is NOT real here, and why:

- **The parser's LLM call is stubbed** (no LLM key in the sandbox). Each message returns the
  parser output the owner's dev copy recorded for it (crew trace of turns 3dec9b68 / 4a90dd1d:
  "taiyang" hinted brand, `{raw: "supplier", hint: "supplier"}`), else what the parser prompt
  teaches. Everything after the parser is real: engine, lane, MCP hop, route, reorder run,
  worker, export.
- **File storage is local disk** (no S3/R2 credentials): `harness/local_storage.py`.
- Respond.io is never reached: every console turn is a dry run, and the pass confirms no
  download is ever claimed for delivery.

Navigation: sidebar from `/` > System > Messaging > Chatbot Console, contact "Cloud Buyer".

## Results

| # | Case (exact message) | Width | Expected | Seen | Result |
|---|---|---|---|---|---|
| O1 | "low stock report water closet taiyang" (owner, turn 3dec9b68) | 1280, 375 | supplier Taiyang taken; header names it | `Low stock report (water closet, supplier XIAMEN TAIYANG TECHNOLOGY CO.,LTD, no grouping) - as of 03/10/2026` / `Low: 2 of 2 planned products` + xlsx | PASS |
| O2 | "low stock report by supplier, water closet only" (owner, turn 4a90dd1d) | 1280, 375 | runs by supplier; no "Could not find" | `Low stock report (water closet, all suppliers, by supplier) - as of 03/10/2026` / `Low: 4 of 4` + xlsx | PASS |
| O3 | owner msg 1 with the worker stopped (pending, test turn) | 1280 | header on the pending line, nothing sent | `Low stock report (water closet, supplier XIAMEN TAIYANG TECHNOLOGY CO.,LTD, no grouping)` / `Preparing the low stock report - test turn: it will be in My Downloads, nothing is sent to WhatsApp.` | PASS |
| 1 | "Sorento water tap low stock list" | 1280 | no question, SRT-FT only | `Low stock report (Sorento water tap, all suppliers, no grouping) - as of 03/10/2026` / `Low: 1 of 1` | PASS |
| 2 | "low stock report" | 1280, 375 | the question; no run | `Which product category? Reply with a category (e.g. water tap) or "all".`; chat runs 10 -> 10 | PASS |
| 3 | "low stock report" -> "water closet" | 1280 | runs WC | `Low stock report (water closet, all suppliers, no grouping) - as of 03/10/2026` / `Low: 4 of 4` | PASS |
| 4 | "low stock report" -> "all" | 1280 | whole book | `Low stock report (all categories, all suppliers, no grouping) - as of 03/10/2026` / `Low: 5 of 5` | PASS |
| 5 | "low stock report" -> "spaceship" -> "rocket" | 1280 | miss, then give up; no run | `I don't know 'spaceship' as a category.` + question; `I still can't place 'rocket'. Ask for the low stock report again with a category or "all".`; runs 10 -> 10 | PASS |
| 6 | "low stock water closet by supplier" | 1280 | no question, by supplier | `Low stock report (water closet, all suppliers, by supplier) - as of 03/10/2026`; runs 10 -> 11 | PASS |
| 7 | "low stock report" -> "cancel" | 1280 | cancelled; no run | `Low stock report cancelled.`; runs 10 -> 10 | PASS |
| 8 | workbook of step 6 + My Downloads | 1280 | one Low/All pair per supplier, WC rows only; in My Downloads | sheets `JINBAICHUAN HARDWARE - Low`/`JINBAICHUAN HARDWARE` (MWC3001), `JINBAICHUAN TRADING - Low`/... (SRTWC1002), `XIAMEN TAIYANG TECHNOLOGY - Low`/... (CBWC2001, SRTWC1001); tap SRTFT4001 excluded; listed Ready in My downloads | PASS |
| D | no console turn pushes to WhatsApp | - | `deliver_to_contact_id` never set | 0 of 11 downloads claimed | PASS |
| R | 375 layout | 375 | no page overflow | `scrollWidth 360 == clientWidth 360`; header wraps inside the bubble | PASS |

Observation, not this lane: at 375 the console's attachment row (the full MIME type string)
scrolls sideways inside the thread (`m1-owner-taiyang-375.png`). The console component is
untouched by this branch.

## Needs the owner's dev copy

- The real parser's reading of the owner's messages (the LLM call is stubbed here). The lane
  is built to be robust to the readings crew recorded, and those are what the stub returns.
- Real plan volumes and real supplier names on real POs (here: six synthetic products).
