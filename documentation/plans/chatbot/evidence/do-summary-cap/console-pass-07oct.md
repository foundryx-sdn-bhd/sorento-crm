# DO-SUMMARY-CAP console pass (cloud sandbox, 7 Oct 2026)

The prod journey replayed against the REAL backend (engine, lane, MCP hop, outstanding report
route, the changed presenter) and the REAL parser (the sandbox proxy carries the OpenAI
credential; `harness/run_backend.py` stubs storage only). Blank bootstrapped database
(`sorento_browse`, a copy of the `cloud-env-setup.sh` schema) seeded by
`harness/seed_do_summary_cap.py`:

- contact 900000009 "Cap Dealer", chatbot allowed, every suggested agent granted, the
  `sales_orders.outstanding` reveal granted
- customer CAP HARDWARE SDN BHD: 26 products SRTWC9001..SRTWC9026 on 9 outstanding DOs over
  BRW-IB / BRW-KL (51 units; 12, 8, 5, 3, 2 and twenty-one 1-unit codes), one DELIVERED DO as
  the control
- MCP server on :8765 from the backend venv (the editable install of this checkout's
  `sorento_crm_mcp`, so the capped presenter is the one serving), integration key issued by the
  seed and carried by both sides
- `tenant_modules` enabled for every catalogue key so the frontend sidebar shows System
  (empty on a bootstrapped DB, the same note as #1515's pass)

## Graded run: `scripts/chatbot_console_check.py` over `tests/chatbot/console_cases/2026-10-07-do-summary-cap.yaml`

Run `console-check-1791331134`, every turn a dry run, parser prompt = the `production` label
(v3). Both turns' `reply_contains` / `reply_not_contains` graded green; the two `pending_kind`
lines graded None because the runner on main reads `variables.pending` while the tail writes
`variables.open_question` (#1515 fixes the runner; not duplicated here, and the lines were
dropped from the case). Replies, verbatim from the stored turn rows:

Turn A, `CAP HARDWARE delivery order outstanding`:

```
Product: all
Customer: CAP HARDWARE SDN BHD
Location: all
Order date: all

*Delivery order outstanding*
Delivery orders: 9
DO qty: 51
Delivered: 0
Outstanding: 51
DO date range: 10/03/2026 to 06/06/2026
*_By location_*
BRW-IB: 34 (O/S: 34)
BRW-KL: 17 (O/S: 17)
*_By product_*
SRTWC9001: 12 (O/S: 12)
SRTWC9002: 8 (O/S: 8)
SRTWC9003: 5 (O/S: 5)
SRTWC9004: 3 (O/S: 3)
SRTWC9005: 2 (O/S: 2)
SRTWC9006: 1 (O/S: 1)
SRTWC9007: 1 (O/S: 1)
SRTWC9008: 1 (O/S: 1)
SRTWC9009: 1 (O/S: 1)
SRTWC9010: 1 (O/S: 1)
SRTWC9011: 1 (O/S: 1)
SRTWC9012: 1 (O/S: 1)
SRTWC9013: 1 (O/S: 1)
SRTWC9014: 1 (O/S: 1)
SRTWC9015: 1 (O/S: 1)
SRTWC9016: 1 (O/S: 1)
SRTWC9017: 1 (O/S: 1)
SRTWC9018: 1 (O/S: 1)
SRTWC9019: 1 (O/S: 1)
SRTWC9020: 1 (O/S: 1)
... and 6 more products (reply 1 for the full list)

Reply 1 for the delivery order list.
```

Twenty product rows, the six one-unit codes SRTWC9021..9026 left off, the count lines and
By location whole, the closing offer line unchanged.

Turn B, `1`: the header plus the nine DO rows (`1. *DO Number:* DO-26-0606` ... `9. *DO Number:*
DO-26-0310`), every product named on its DO row (SRTWC9021..9026 among them), no `By product`,
no trailer. The delivered control DO-26-0105-DLV appears nowhere.

## Browser pass (frontend Chatbot Console, `next dev` on :3000, agent-browser 0.27.0, the preinstalled Chromium)

Navigation by sidebar clicks from `/`: sign in as the seeded admin, System > Messaging >
Chatbot Console, contact "Cap Dealer" picked in the console's own picker (the console pins
parser v4). The console sends a tapped chip as its label TEXT, as respond.io does.

| Step | Action | Expected | Result |
|---|---|---|---|
| 1 | type `CAP HARDWARE delivery order outstanding` | the DO summary + offer | PASS with two detours outside this lane: the first send hit the parser's 20 s timeout ("Sorry, I am busy right now"); the re-send was read as a bare outstanding ask and the bot asked "Outstanding for which document?" with chips Sales orders / Delivery orders / Both (turn A's parse, the same detour #1515's pass recorded) |
| 2 | tap the chip `Delivery orders` | the DO summary: counts, By location, By product with 20 rows, the trailer, `Reply 1 for the delivery order list.`, chip `Delivery order list` | PASS: 20 product rows ending `SRTWC9020: 1 (O/S: 1)`, then `... and 6 more products (reply 1 for the full list)`, then the offer line and the chip; SRTWC9021 absent |
| 3 | type `1` | the delivery order LIST, every DO, no cap, no trailer | PASS: nine rows `1. DO Number: DO-26-0606` to `9. DO Number: DO-26-0310`, no `By product`, no `more products`, no offer line repeated |
| 4 | 375 x 812 viewport | nothing clipped, no horizontal scroll | PASS: `document.documentElement.scrollWidth` 360 at `innerWidth` 375; the trailer wraps to two lines inside the bubble (`... and 6 more products (reply 1 for` / `the full list)`), the `Delivery order list` chip sits under it, nothing cut |

## After the merge of origin/main (#1515 and #1516 in, head 3f589ced)

The merge touches no presenter file (MCP suite 730 green, `test_do_list_reply_label.py` and
the two lane tests green on the merged head). Four more live graded runs were attempted on the
merged stack to re-grade the case with `pending_kind` (the runner now reads
`variables.open_question`, #1515's fix): none reached the capped summary, every miss on a turn
outside this lane's seam. Run 1791333032: turn A parsed as a plain order lookup (one DO listed).
Runs 1791333086 and 1791333125: turn A parsed as a bare outstanding ask (`document: []`), the
scope question armed (`pending_kind` graded `outstanding_scope`, so the runner fix is live), and
"1" then picked Sales orders. Run 1791333187 (a three-turn variant: bare ask, "2", "1"): the "2"
turn's stored row carries no parse output and no reply text (the backend log shows no error
for it), and "1" re-asked the scope question. Same class of instability #1515 measured (2 of 4 turn-A parses there). The
graded green run (1791331134) and the browser pass above are the lane's evidence; the YAML
keeps the direct two-turn path and says what to do on a detour.

## Final head 742f8d9e (origin/main e4158ab5 merged: #1515, #1516, #1502)

Backend, MCP server and `next dev` restarted on the merged checkout; same seed, same contact.

Graded run `console-check-1791333810`, real parser, every expectation green INCLUDING the two
`pending_kind: outstanding_detail` lines (the runner reads `variables.open_question` since
#1515): turn A the capped summary (20 rows, `... and 6 more products (reply 1 for the full
list)`, `Reply 1 for the delivery order list.`), turn B the nine-row DO list.

Browser pass, same navigation (sidebar from `/`, System > Messaging > Chatbot Console, contact
"Cap Dealer", Reset):

| Step | Action | Expected | Result |
|---|---|---|---|
| 1 | type `CAP HARDWARE delivery order outstanding` | the DO summary + offer | PASS via the detour: the live parser read a bare outstanding ask and the bot asked "Outstanding for which document?" with chips Sales orders / Delivery orders / Both |
| 2 | tap the chip `Delivery orders` | the capped DO summary + offer + chip | PASS: `Delivery orders: 9`, By product `SRTWC9001: 12 (O/S: 12)` ... `SRTWC9020: 1 (O/S: 1)`, `... and 6 more products (reply 1 for the full list)`, `Reply 1 for the delivery order list.`, chip `Delivery order list`; SRTWC9021 absent |
| 3 | type `1` | the full DO list, no cap, no trailer | PASS: nine `DO Number` rows, no `By product`, no `more products`, no offer line repeated |
| 4 | 375 x 812 viewport | nothing clipped, no horizontal scroll | PASS: `scrollWidth` 360 at `innerWidth` 375, trailer box left 28 / right 260 inside the bubble, wraps to two lines |

## Both blocks capped, live (crew audit item 3), head de16c309

`harness/seed_so_both.py` adds 22 open sales orders for the same dealer (SRTWC9001..9022, 5
units each), so the SO block's By product group has 22 rows and the DO block keeps its 26.
Three live `--say` turns with the real parser (contact 900000009, `seed_so_both.py` after
`seed_do_summary_cap.py`; the first attempt's parse of the ask went to the scope question,
the second parsed it directly, `order_status: outstanding_both`):

Turn 1, `CAP HARDWARE outstanding both`, the summary's decisive lines:

```
*Sales order outstanding*
Sales orders: 22
...
*_By product_*
SRTWC9001: 5 (O/S: 5)
... (20 rows)
SRTWC9020: 5 (O/S: 5)
... and 2 more products (reply 1 for the full list)

*Delivery order outstanding*
Delivery orders: 9
...
*_By product_*
SRTWC9001: 12 (O/S: 12)
... (20 rows)
SRTWC9020: 1 (O/S: 1)
... and 6 more products (reply 2 for the full list)

Reply with a number for detail:
1. Sales order list
2. Delivery order list
3. Both lists
```

Turn 3, `2` (parser `reference_positions [2]`, `open_question_answer {pick, [2]}`): the nine-row
DO list (`1. *DO Number:* DO-26-0606` ... `9. *DO Number:* DO-26-0310`), no summary, no trailer.
So `reply 2` under the DO block lands on the DO list.

Turn 2, `3` (Both lists), is NOT this lane's: the parser returned `reference_positions [3]`,
`open_question_answer {pick, [3]}` AND `order_status: outstanding_both` (a status carried from
context, which the prompt says it must not), and the merged engine re-ran the two-block summary
instead of printing both lists. The offer roster is the same three rows with or without the
trailer (the `2` pick on the same roster worked, and `test_do_summary_cap_offer.py` pins it), so
the cap neither causes nor hides it. Reported on the PR as a finding for the engine.

## Sandbox notes

- The parser's own 20 s timeout hit the first browser send (one of 5 live calls in this
  pass); the second send parsed. Same rate #1515 recorded (2 of 60).
- Turn A's parse is not stable (also #1515): the live parser read the customer-subject ask
  as a bare outstanding ask once in the browser pass, so the scope question armed first and
  the chip `Delivery orders` reached the summary. The graded runner's run parsed it directly.
  Outside this lane's seam either way; the presenter renders the same summary from both paths.
- `api_call_log` does not exist on the bootstrapped schema; the MCP hop's call log insert
  fails and is swallowed (same note as #1515). Not this lane's.
- `agent-browser` in the sandbox needs `AGENT_BROWSER_EXECUTABLE_PATH` pointed at the
  preinstalled Chromium and `AGENT_BROWSER_PROXY_BYPASS=localhost,127.0.0.1`, or every page is
  routed through the egress proxy and reads as `about:blank`.
