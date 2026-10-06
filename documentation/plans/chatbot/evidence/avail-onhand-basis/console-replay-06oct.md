# AVAIL-ONHAND-BASIS console replay (cloud sandbox, 6 Oct 2026)

The prod case replayed against the real backend (engine, lane, MCP hop, stock route,
presenter) with ONLY the parser's LLM call stubbed (`harness/run_backend_stubparser.py`, this
sandbox holds no LLM key), on a blank bootstrapped database seeded by `harness/seed_onhand.py`:

- contact 900000012 "Onhand Buyer", Availability stock policy over warehouse MOCHA-WH
- MWC7632-SH-P: 6 on hand in MOCHA-WH, 13 on an open sales order line to MOCHA-WH, category max 100
- OHB0000: 0 on hand, no open SO, no shipment (control)

Runner: `scripts/chatbot_console_check.py --say ... --contact 900000012` (every turn a dry run).

## After (branch `crew/avail-onhand-basis`, PR #1510)

```
> MWC7632-SH-P
  reply.text: How many units of MWC7632-SH-P?
> 10
  reply.text: MWC7632-SH-P x 10: ✅ Got stock, 6 available.

> MWC7632-SH-P
  reply.text: How many units of MWC7632-SH-P?
> 5
  reply.text: MWC7632-SH-P x 5: ✅ Got stock.

> OHB0000 x 5
  reply.text: OHB0000 x 5: ❌ No stock and no incoming. Please refer to your salesman.
```

Staff route at the same data (`GET /inventory/stock/balance?include_sellable=true`, no contact),
unchanged: `quantity_on_hand 6, open_so_qty 13, sellable -7`.

## Before (main e3ea227e, same seed, same harness)

See the section appended below once replayed; the prod reply was:

```
MWC7632-SH-P x 10: ❌ No stock and no incoming. Please refer to your salesman.
```

## Sandbox notes

- `respond_contacts.chatbot_allowed` must be true for the seeded contact (migration
  `aig_0001_chatbot_allowed`), else every turn is `ai_not_allowed`.
- The company scope resolver accepts ONLY the legacy `EXTERNAL_API_KEY` from settings
  (`company_scope_resolver._api_key_valid`); an integration key issued by the seed
  authenticates but resolves an UNSET (fail-closed) scope and every read returns 0 rows.
  The harness therefore writes the issued key into `.env.browse` as `EXTERNAL_API_KEY`
  (the env file loads with override=True, so a shell variable does not win).
