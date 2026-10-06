# DO-LIST-REPLY-LABEL console replay (cloud sandbox, 6 Oct 2026)

The prod journey replayed against the REAL backend (engine, lane, MCP hop, outstanding
report route, presenter) and the REAL parser: the 6 Oct sandbox routes `api.openai.com`
through a proxy that carries the credential, so nothing was stubbed but storage
(`harness/run_backend.py`). Blank bootstrapped database seeded by `harness/seed_do_list.py`:

- contact 900000008 "DO List Dealer", chatbot allowed, every suggested agent granted, the
  `sales_orders.outstanding` reveal granted
- SRTWT7445: five delivery orders still outstanding over BRW-IB / BRW-KL, two customers,
  dated 2026 (18 units); SRTWT7446 on two of them; one DELIVERED DO as the control
- MCP server on :8765 with an issued integration key, written into `.env.browse` as
  `EXTERNAL_API_KEY` (company scope accepts only that key; see the AVAIL-ONHAND-BASIS notes)

Runner: `scripts/chatbot_console_check.py` over the lane's YAML
(`tests/chatbot/console_cases/2026-10-06-do-list-reply-label.yaml`); every turn a dry run.
Parser prompt: the `production` label of the bootstrapped DB (v3); five runs pinned to the
newest version (v4) read the same.

## The three turns, as the engine answered them (head 2bf60372 + the fix of 63b3f2fe)

| Turn | Text | Live parser (B and C measured) | decide() | Fetch | Reply |
|---|---|---|---|---|---|
| A | `SRTWT7445 delivery order outstanding` | `order_status outstanding`, `document [DO]`, product entity | new_ask (the report) | `scope=do` | the "Delivery order outstanding" summary (5 DOs, 18 units, By location, By customer) + "Reply 1 for the delivery order list." |
| B | `Delivery order list` (the tapped quick reply) | see the tally below | `answer / picked_scope` whenever a pick is declared | `scope=do&detail=do` | the DO LIST, byte for byte the reply of C |
| C | `1` | `reference_positions [1]`, `open_question_answer {pick, [1]}` | `answer / picked_scope` | `scope=do&detail=do` | the DO LIST |

Run 1 (console-check-1791310546) is the clean A/B/C pass of the whole journey live:
B `reference_positions [1]`, `open_question_answer {pick, [1]}`, `document ["DO"]`; rules
`open_question_answer_pick, answer_outstanding, reuse_alive, pick_in_roster_domain` on B and
C alike. Before the fix (origin/main) the same B read `new_ask / named_document` and re-ran
the summary (red paste on the PR).

## What the live parser returns for B over an armed offer (turn A pinned to its own run-1 emission, `harness/do-list-reply-label-pinned-A.yaml`, B and C live)

| Reading of "Delivery order list" | Runs | Engine before this PR | Engine at the final head |
|---|---|---|---|
| `reference_positions [1]` + `open_question_answer {pick,[1]}` + `document [DO]`, no entity | 1 (run 1) | summary re-run (`named_document`) | the DO list |
| `open_question_answer {pick,[1]}` only (`reference_positions []`), `document [DO]`, `order_status do_outstanding`, product SRTWT7445 re-stated with `entity_op reuse`, `current_message true` | 6 (runs 3, 5, 7, 12, 14, 16) | summary re-run (`new_ask / domain_in_message`, D17 point 3) | the DO list |
| no pick anywhere (`open_question_answer` mode null, `reference_positions []`), `document [DO]`, the same reused entity | 7 (runs 6, 8, 9, 10, 11, 13, 15) | summary re-run (`answer_pending_not_an_answer`, planned as a fresh ask for the same report) | summary re-run: the parser declared nothing to act on |

14 B turns over an armed offer: 7 declared the pick, 7 did not. Prompt v3 (`production`)
9 turns: 4 declared; prompt v4 (newest, pinned) 5 turns: 3 declared. One C turn (run 6) and
one (run 20) hit the parser's 20 s timeout and are not counted.

Restating the rule beside the options in the parser's context (one sentence after
`Open question options:` in `head/parser.py`, "a message that is one of these labels ... is
a tap of that button: answer with its position") was measured over 10 more runs: 2 declared
the pick, 8 did not (runs 17 to 26). Not kept.

Turn A on its own is not stable either: in 2 of 4 fully live runs the parser read
`SRTWT7445 delivery order outstanding` as a plain order lookup (run 2: "Here are the orders
I found. 1. DO-26-0921") or as a bare outstanding ask (the frontend console pass: "Outstanding
for which document?"), so no detail offer was armed. That is the opening ask's parse, outside
this lane's seam; the pinned-A copy exists so B can be measured over an armed offer.

## Browser pass (frontend Chatbot Console, `next dev` on :3000, agent-browser, Chromium)

Navigation by sidebar clicks from `/`: Sign in as the seeded admin, System > Messaging >
Chatbot Console, contact "DO List Dealer" picked in the console's own picker (the console
pins parser v4). The console sends a tapped chip as its label TEXT, as respond.io does.

| Step | Action | Expected | Result |
|---|---|---|---|
| 1 | type `SRTWT7445 delivery order outstanding` | the DO summary + offer | PASS with a detour: the live parser read a bare outstanding ask, the bot asked "Outstanding for which document?" with chips Sales orders / Delivery orders / Both (turn A's parse, see above) |
| 2 | tap the chip `Delivery orders` | the DO summary + "Reply 1 for the delivery order list." + chip `Delivery order list` | PASS (parser `picked [2]`, `document [DO]`, `entity_op reuse`; decide `picked_scope`) |
| 3 | tap the chip `Delivery order list` | the delivery order LIST, not the summary | PASS (parser `picked [1]`, `reference_positions []`, `entity_op reuse`; decide `picked_scope`; reply "1. DO Number: DO-26-0921 ..." five rows, no By location, no By product, no offer line). Before this PR that reading re-ran the summary. |
| 4 | 375 x 812 viewport | nothing clipped, no horizontal scroll | PASS (`document.documentElement.scrollWidth` 360 at `innerWidth` 375, the `Delivery order list` chip 128 px wide ending at x 156) |

## Sandbox notes

- `scripts/chatbot_console_check.py` graded `pending_kind` off `variables.pending`; the tail
  writes `variables.open_question` (`turn/tail.py:33`), so every `pending_kind` line in every
  case file graded None. Fixed in this lane (reads `open_question`, then `pending`).
- `_base_envelope` borrows the contact's LATEST `chatbot.turns` row, dry-run rows included,
  so from the second run on the envelope comes from the previous run's last turn. Harmless
  here (text and ids are swapped, `cold: true` blanks the state), noted for whoever reads
  "the envelope is a real one, borrowed".
- `tenant_modules` is empty on a bootstrapped DB, so the frontend sidebar hides every module
  (the backend guard short-circuits, the frontend filter does not). The pass enabled all 24
  catalogue modules for `__default__` by SQL.
- `api_call_log` does not exist on the bootstrapped schema (migration `293_api_call_log`
  present, table absent); the MCP hop's call log insert fails and is swallowed. Not this
  lane's.
- The backend's parser call has a 20 s timeout; two of 60 live calls hit it.
