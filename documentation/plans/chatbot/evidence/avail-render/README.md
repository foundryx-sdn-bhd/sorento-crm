# AVAIL-RENDER cloud browser pass (5 Oct 2026)

Code under test: branch `crew/avail-render`, PR #1493, head a9b2e9af (the lane merged with
origin/main 3a231689, WA-MSG-TRIM). A first run on a075c4a6 gave the same ten results; the
crew asked for the pass on the merged head because that merge touched `label_catalog.py` and
`inventory_service.py`.

## Stack (all inside the cloud sandbox)

- Postgres 16 `sorento_browse`, built by `scripts.bootstrap_env`, then the synthetic seed in
  `harness/seed_avail.py` (no customer data): admin login, contact 900000011 "Avail Buyer" on
  the Availability stock policy over warehouse AVRBRW, every suggested access agent allowed, and
  four products in one category with chatbot max 100: AVR1001 (discontinued, 100 on hand),
  AVR1002 (live, 30 on hand), AVR1003 (live, nothing, no shipment), AVR1004 (discontinued,
  nothing, inbound shipment with its packing list, ETA 2026-10-19).
- Backend :8000 (`harness/run_backend_stubparser.py`), MCP :8765 (the lane's own integration
  key), frontend `npm run dev` :3000, agent-browser 0.27.0 on the sandbox Chromium, session
  `avr`. Modules installed through `POST /api/v1/system/modules/install` (a fresh DB has none).

What is NOT real here, and why:

- **The parser's LLM call is stubbed** (no LLM key in the sandbox): each hand-test message maps
  to the parser output the prompt teaches for it (a stock ask with the product and quantity as
  entities; an ETA ask with the products). Everything after the parser is real: engine, business
  lane, MCP hop, `/api/v1/inventory/stock/balance` and `/api/v1/incoming-stock/list` routes,
  presenter, renderer, language detection.
- **File storage is local disk** (`harness/local_storage.py`). Respond.io is never reached:
  every console turn is a dry run.

Navigation: sidebar from `/` > System > Messaging > Chatbot Console, contact "Avail Buyer".
Replies read off `[data-testid="chatbot-console-thread"]` (`harness/say.sh`); the console
renders the WhatsApp `*(...)*` bold as bold, so the marker reads `⚠️  (PRODUCT DISCONTINUED)`.

## Results

| # | Message | Expected | Seen | Result |
| --- | --- | --- | --- | --- |
| 1 | `check stock AVR1001 x 5` (discontinued, in stock) | `✅ Got stock.` + marker line, no refer | `AVR1001 x 5: ✅ Got stock.` / `⚠️  (PRODUCT DISCONTINUED)` | PASS |
| 2 | `check stock AVR1001 x 500` (discontinued, over the max: the owner's SRTWT2634-RG trace) | 🚫 line keeps the refer + marker line | `AVR1001 x 500: 🚫 the quantity is more than what I can confirm here. Please refer to your salesman.` / `⚠️  (PRODUCT DISCONTINUED)` | PASS |
| 3 | `check stock AVR1002 x 50` (live, 30 on hand) | `✅ Got stock, 30 available.`, no refer, no marker | `AVR1002 x 50: ✅ Got stock, 30 available.` | PASS |
| 4 | `check stock AVR1003 x 5` (live, nothing, no shipment) | ❌ no stock and no incoming + refer, no marker | `AVR1003 x 5: ❌ No stock and no incoming. Please refer to your salesman.` | PASS |
| 5 | `check stock AVR1004 x 5` (discontinued, shipment due) | `❌ ETA 19/10/2026.` + marker line, no refer | `AVR1004 x 5: ❌ ETA 19/10/2026.` / `⚠️  (PRODUCT DISCONTINUED)` | PASS |
| 6 | `ETA AVR1004 and AVR1003` | ETA line + marker, then `No ETA`, then the refer once | `AVR1004: ✅ ETA 19/10/2026` / `⚠️  (PRODUCT DISCONTINUED)` / `AVR1003: No ETA` / `Please refer to your salesman.` | PASS |
| 7 | `AVR1001 x 5 ada stok tak` (ms) | `✅ Stok ada.` + ms marker | `AVR1001 x 5: ✅ Stok ada.` / `⚠️  (PRODUK DIHENTIKAN)` | PASS |
| 8 | `AVR1002 x 50 ada stok tak` (ms) | `✅ Stok ada, 30 tersedia.` | `AVR1002 x 50: ✅ Stok ada, 30 tersedia.` | PASS |
| 9 | `AVR1001 x 5 有库存吗` (zh) | `✅ 有库存。` + zh marker | `AVR1001 x 5: ✅ 有库存。` / `⚠️  (产品已停产)` | PASS |
| 10 | `AVR1002 x 50 有库存吗` (zh) | `✅ 有库存，可供 30 件。` | `AVR1002 x 50: ✅ 有库存，可供 30 件。` | PASS |

One seed defect found on the first run, not a product one: case 5 answered
`❌ No stock and no incoming.` because the seeded shipment had no packing list, and the stock
ask's R5 read counts only a shipment that carries one (`incoming_stock_service.
earliest_packing_list_shipment`); the ETA ask (case 6) reads every shipment and already showed
the date. The seed attaches one now (`seed_avail.py`).

Screenshots: `console-thread-1280.png`, `console-thread-375.png` (the thread after case 10 on
a9b2e9af).

Raw transcript of the pass on a9b2e9af: `browser-pass.txt`.
