# Console check, S3 (AC-SA318), 25 Sep 2026

Closes AC-SA318 ("Console check per `documentation/agents/chatbot-verification.md` against
an Availability only dealer contact: one turn per branch - B1 via Q > X, B1 via unset X,
B2, B3 with toggle on and off, B4; replies quoted in
`documentation/plans/chatbot/evidence/`").

## Round 2 update (review round 2 on PR #1247, Should fix 1)

The replies below were re-quoted after review round 2's Blocking 1 fix (the
`_Data last updated: dd/mm/yyyy hh:mm:ss_` footer no longer prints on an answered
`stock_availability` reply, `app/services/chatbot/lanes/business/fetch.py`, gated by the
same `stock_availability_answered` flag that already gates the intro and the numbering).

Round 1's seed database carried no `stock_ledger` row with `transaction_type =
'BULK_IMPORT'`, so `StockService`'s `last_import_at` query returned `NULL` and the footer
never printed regardless of whether the code suppressed it - a pass for the wrong reason.
This round adds one such row (product `SA318C`, warehouse `SA318W1`, `created_at`
2026-08-24 18:00 seed-local) before re-running, so the footer case is genuinely exercised.
Both console-case yaml files now also carry `reply_not_contains: ["Data last updated"]`
on their final (answered) turn, so a regression is caught by the yaml itself, not only by
eyeballing this file.

Everything else about the scenario (contact, categories, products, stock, the incoming
shipment + packing list) is unchanged from the round 1 seed described below. Round 2 also
needed three additional rows round 1's evidence did not call out (the environment this
round ran in was a fresh CI-shaped Postgres with none of them, where round 1's sandbox
apparently already had them):

4. **`respond_workspaces` + the contact's `workspace_id`.** `mcp_access_service.
   evaluate_agent` (the in-process port of `/external/access-agent/check`) resolves the
   contact via `(respond_io_id, workspace_id)`, and the default workspace via
   `RespondWorkspace.is_default`. Without a workspace row the contact resolves to
   `deny_unknown_contact` regardless of any access grant. Seeded one `is_default=true`
   workspace and pointed the contact at it.
5. **An `access_agents` row named `general_enquiries` plus a `contact_agent_access` grant
   for it.** A stock ask's `routing.suggested_agent` defaults to `general_enquiries` when
   the mock parser leaves it null (this file's own case turns do). With no such agent row
   the check fails closed (`deny_unknown_agent`); with the row but no grant it fails
   `deny_no_access`. Both are pre-existing sandbox/DB gaps, not an S3 code defect - the
   same class of gap round 1's evidence section below documents for `api_call_log` and
   `respond_contact_companies`.
6. **`integration_api_keys` carrying the console-check API key's hash.** Authentication
   now resolves the presented `X-API-Key` through `integration_api_keys` exclusively
   (`app.dependencies.get_current_user_or_api_key`, "the env var is no longer consulted at
   runtime") rather than the legacy `settings.external_api_key` comparison round 1's
   evidence describes below. Ran `app.services.integration_seed.seed_integrations(db,
   external_api_key="cloud-lane-test-key")` - the same seed the Group A migration runs in
   a real deploy - which attaches the key's hash to the `n8n` integration and gives it an
   `act_as_user_id` principal with Admin-parity permissions, making the separate
   `EXTERNAL_API_KEY_ACT_AS_USER_ID` env var round 1 needed no longer necessary (kept it
   set anyway, harmless).

None of the round 2 seed additions touched a migration or application code - additive rows
only, on top of round 1's scenario.

## Environment note (round 1, deviation from the brief)

The brief assumed this sandbox's `DATABASE_URL` was already a bootstrapped prod-copy DB.
Measured instead: `scripts/cloud-env-setup.sh` bootstraps a CI-shaped, EMPTY Postgres
(`sorento_ci`) via `scripts.bootstrap_env` (reference data only - roles, permissions, order
statuses; zero `chatbot.turns`, zero `respond_contacts`, zero `products`, zero `users`).
`bash scripts/cloud-env-setup.sh --check` reported "all checks passed" before this session
started, confirming the DB was genuinely at this state rather than mid-setup.

The console-check script's own envelope-borrowing mechanism (`_base_envelope`, see
`scripts/chatbot_console_check.py`'s module docstring) requires an existing
`chatbot.turns` row for the contact, so a scenario contact had to be seeded, not just
reused. All scenario data (contact, categories, products, stock, the incoming shipment +
packing list, the access grant, the integration principal and the seed `chatbot.turns`
row) was seeded directly via SQLAlchemy against `sorento_ci`, matching the shape
`tests/chatbot/test_console_turn_endpoint.py`'s own fixtures (`seeded_contact_with_a_prior_turn`,
`tests/chatbot/test_engine.py::_envelope`/`CONTACT_ID = 900000008`) already establish for this
exact purpose - the SAME `CONTACT_ID` every other console-case file in this repo uses.

Two additional environment gaps were found and worked around (neither is an S3 code
defect, both are pre-existing sandbox/DB gaps):

1. **`api_call_log` table missing.** The `ApiCallLog` ORM model (`app/models/api_call_log.py`)
   exists, but `scripts.bootstrap_env`'s `Base.metadata.create_all(engine)` had not created
   it in this DB (a `create_all`-vs-import-order gap the bootstrap script's own comments
   describe for several other tables, e.g. reference-data rows it explicitly replays).
   Worked around additively: `Base.metadata.create_all(bind=engine, tables=[ApiCallLog.__table__])`
   after importing `app.main` (registers every model). No migration touched, no destructive
   DDL - the same "additive DDL only" rule `sorento_crm_backend/CLAUDE.md`'s shared-DB lesson
   states for schema drift.
2. **Company scope for a chatbot turn is resolved off the CONTACT, not the API key**
   (`app.services.company_scope_resolver.resolve_contact_company_scope`, read via
   `respond_contact_companies` - `engine.py`'s own comment: "the chatbot turn engine... calls
   resolver/stock/promotion code IN PROCESS and so never runs that [request] dependency").
   Without a `respond_contact_companies` row for the seed contact, every company-scoped read
   inside a turn (products, categories, stock, the resolver itself) sees zero rows,
   fail-closed - read as "Couldn't find: X" rather than an auth error, which cost real time
   to diagnose. Seeded one row, company = the DB's single seeded company
   (`00000000-0000-0000-0000-000000000001`, "Sorento").

None of the above are committed - the throwaway seed script stayed in the session's private
scratch directory, per the brief's default.

The `chatbot_semantic_parser` prompt / `ai_assistant_configs` singleton auto-creates on first
use with an empty `api_key_ciphertext`; `resolve_config` (called unconditionally, even on a
`--mock-parser` turn - it is the parser CONFIG resolution, not the parser CALL) raises when
that key is empty. Set `ai_assistant_configs.api_key_ciphertext` to a placeholder string (never
actually sent - the mocked turn never reaches `parser.parse`) to clear it.

The MCP server (`sorento_crm_mcp`, package already installed in the backend venv) was not
running; the engine calls it in-process over HTTP for every domain fetch
(`ai_assistant_service.call_tool` -> `settings.ai_assistant_mcp_url`, default
`http://localhost:8765/mcp`). Booted it:

```
cd sorento_crm_mcp
CRM_BASE_URL=http://localhost:8000 EXTERNAL_API_KEY=cloud-lane-test-key \
    /home/user/sorento-crm/sorento_crm_backend/venv/bin/python -m sorento_crm_mcp
```

The console-check API key used throughout is `.env.ci-tests`'s `EXTERNAL_API_KEY`
(`cloud-lane-test-key`) - deliberately, not a fresh integration key (round 1's reasoning;
round 2 instead seeded that same value's hash into `integration_api_keys`, see above,
since the legacy env-var comparison round 1 relied on has since been removed).

## Scenario data

Contact: `respond_io_id = "900000008"` (the console-case convention contact; matches every
other file under `tests/chatbot/console_cases/`), stock visibility policy `mode=availability`
(a `stock_visibility_policies` row with `contact_id` = the internal id, R1's "Availability
only" scope), `packing_list_allowed` flipped between the two B3 runs (see below), warehouse
`SA318W1` named in the policy's `warehouse_ids`.

Categories:
- `SA318CAP` - `chatbot_max_qty = 200`, `chatbot_eta_offset_days = 7`.
- `SA318UNCAP` - both NULL (R2: unset means 0/no cap).

Products (all inherit their category's X/Y - product-level `chatbot_max_qty` /
`chatbot_eta_offset_days` left NULL, R2's resolution rule):
- `SA318A` (SA318CAP) - too_big via Q(250) > X(200).
- `SA318B` (SA318UNCAP) - too_big via unset X (X resolves to 0).
- `SA318C` (SA318CAP) - in_stock: 200 on hand in the policy warehouse, ask 50.
- `SA318D` (SA318CAP) - incoming: 0 on hand, a qualifying R5 shipment (`SA318-SHIP-1`,
  `shipment_status = in_transit`, `estimated_arrival_date = 2026-10-12`, `attachment_id` set,
  line `quantity_shipped=150`, `quantity_received=0`, `line_status=in_transit`) - told date
  `2026-10-12 + Y(7) = 19/10/2026`.
- `SA318E` (SA318CAP) - no_incoming: 0 on hand, no shipment.

Round 2 addition: one `stock_ledger` row, `product_id = SA318C`, `warehouse_id = SA318W1`,
`transaction_type = 'BULK_IMPORT'` - the system-wide "last import" row `StockService`'s
`last_import_at` query reads across every policy mode.

`branch()` (`app.services.stock_ask_branch.branch`) was confirmed by hand against these
numbers BEFORE writing the case files' `reply_contains`, per the brief's instruction:
`branch(250, 200, 0, None) = too_big`; `branch(20, 0, 0, None) = too_big` (X unset -> 0);
`branch(50, 200, 200, None) = in_stock`; `branch(150, 200, 0, date(2026,10,12)) = incoming`;
`branch(150, 200, 0, None) = no_incoming`.

## Commands run (round 2)

```
cd sorento_crm_backend
SORENTO_ENV_FILE=.env.ci-tests venv/bin/python scripts/chatbot_console_check.py \
    tests/chatbot/console_cases/2026-09-25-stock-ask-v2-s3-branches.yaml \
    --base-url http://127.0.0.1:8000 --api-key cloud-lane-test-key --mock-parser
```

Output:

```
console-check-1790332814  3 cases against http://127.0.0.1:8000  parser prompt: whatever the `production` label points at
lane switches before: enabled=False lanes=[]
lane switches for the run: enabled=True lanes=13
PASS  B1 via Q>X and B1 via unset X, one multi-product turn (R14 sample g) branch=business_query  'SA318A x 250: the quantity is more than what I can confirm here, please refer to your salesman.  SA318B x 20: the quanti'
PASS  B2 - in stock                                branch=business_query  'SA318C x 50: yes, we have stock, please refer to your salesman to proceed. SA318C x 50: yes, we have stock, please refer'
PASS  B4 - no stock and no incoming                branch=business_query  'SA318E x 150: no stock and no incoming at the moment, please refer to your salesman. SA318E x 150: no stock and no incom'
lane switches restored: enabled=False lanes=[]

3 passed, 0 failed  (console-check-1790332814)
```

Then, for B3, run twice with the toggle flipped between runs (`UPDATE respond_contacts SET
packing_list_allowed = <true|false> WHERE respond_io_id = '900000008'`):

```
SORENTO_ENV_FILE=.env.ci-tests venv/bin/python scripts/chatbot_console_check.py \
    tests/chatbot/console_cases/2026-09-25-stock-ask-v2-s3-incoming-packing-list.yaml \
    --base-url http://127.0.0.1:8000 --api-key cloud-lane-test-key --mock-parser
```

Output (toggle ON):

```
console-check-1790332824  1 cases against http://127.0.0.1:8000  parser prompt: whatever the `production` label points at
lane switches before: enabled=False lanes=[]
lane switches for the run: enabled=True lanes=13
PASS  B3 - incoming with ETA (run with packing_list_allowed both true and false) branch=business_query  'SA318D x 150: no stock at the moment, ETA 19/10/2026. SA318D x 150: no stock at the moment, ETA 19/10/2026.'
lane switches restored: enabled=False lanes=[]

1 passed, 0 failed  (console-check-1790332824)
```

Output (toggle OFF):

```
console-check-1790332830  1 cases against http://127.0.0.1:8000  parser prompt: whatever the `production` label points at
lane switches before: enabled=False lanes=[]
lane switches for the run: enabled=True lanes=13
PASS  B3 - incoming with ETA (run with packing_list_allowed both true and false) branch=business_query  'SA318D x 150: no stock at the moment, ETA 19/10/2026. SA318D x 150: no stock at the moment, ETA 19/10/2026.'
lane switches restored: enabled=False lanes=[]

1 passed, 0 failed  (console-check-1790332830)
```

Both runs' `reply_contains` AND `reply_not_contains` graded identically (the reply TEXT
does not carry the attachment, and neither carries the footer) - the attachment finding
below is the second half of this branch's evidence, read directly off
`chatbot.turns.response->'actions'` for each run's own row, as the case file's own header
says to.

## The six branches, replies verbatim (round 2, footer fix + BULK_IMPORT row both in effect)

Read via `SELECT response->'reply'->>'text' FROM chatbot.turns WHERE contact_respond_id =
'900000008' AND is_test = true ORDER BY created_at DESC` immediately after each run (dry
runs still write `chatbot.turns`, per `documentation/agents/chatbot-verification.md`).

**B1 via Q > X** (`SA318A`, asked 250, X=200) and **B1 via unset X** (`SA318B`, asked 20, X
unset) - one multi-product turn, R14 sample (g)'s own shape:

```
SA318A x 250: the quantity is more than what I can confirm here, please refer to your salesman.

SA318B x 20: the quantity is more than what I can confirm here, please refer to your salesman.
```

Both lines read identically to the dealer, confirming R14/AC-SA313: the "no cap set for
`<category>`" wording is S4's own AGENT-notification reason (not built in this slice),
never a sentence the dealer is sent. No intro, no numbering, no footer.

**B2 - in stock** (`SA318C`, asked 50, 200 on hand):

```
SA318C x 50: yes, we have stock, please refer to your salesman to proceed.
```

**B3 - incoming, ETA** (`SA318D`, asked 150, 0 on hand, shipment 2026-10-12 + Y=7 ->
19/10/2026) - identical reply text both toggle runs:

```
SA318D x 150: no stock at the moment, ETA 19/10/2026.
```

**B4 - no stock, no incoming** (`SA318E`, asked 150, 0 on hand, no shipment):

```
SA318E x 150: no stock and no incoming at the moment, please refer to your salesman.
```

**No reply above carries the `_Data last updated: ..._` footer**, confirmed against a DB
that DOES have a `BULK_IMPORT` ledger row - this is what round 1's evidence could not show
(its seed DB had none, so the assertion held for the wrong reason). As a control, the
FIRST turn of each case (before the dealer states a quantity, `needs_quantity` still true
- e.g. `"How many units do you need?"`) DOES carry the footer
(`_Data last updated: 25/08/2026 02:00:00_`), confirming the fix is scoped to the answered
case exactly as designed (`stock_availability_answered`), not a blanket removal that would
also silence it for the still-open ask.

## B3 `send_attachments` finding

Read via `SELECT response->'actions' FROM chatbot.turns WHERE id = '<turn id>'` for each
run's own final (quantity-stating) turn:

**Toggle ON** (`respond_contacts.packing_list_allowed = true`), turn `97c4cdc6-9f2e-4849-b566-675bb2e74f15`:

```json
[
  {"kind": "send_message", "text": "SA318D x 150: no stock at the moment, ETA 19/10/2026.", "dry_run": true, "result_set": [], "quick_replies": null},
  {"kind": "send_attachments", "dry_run": true, "attachments_src": [{"url": "https://example.test/sa318-packing-list.pdf", "filename": "sa318-packing-list.pdf", "mimeType": "application/pdf"}]}
]
```

`send_attachments` IS present, carrying the seeded shipment's packing list attachment.

**Toggle OFF** (`respond_contacts.packing_list_allowed = false`), turn `65e70daf-84d4-48c0-ba5e-6936c75c21db`:

```json
[
  {"kind": "send_message", "text": "SA318D x 150: no stock at the moment, ETA 19/10/2026.", "dry_run": true, "result_set": [], "quick_replies": null}
]
```

`send_attachments` is ABSENT - only `send_message`. Text is byte-identical between the two
runs (as expected: R6/AC-SA311 gate the ATTACHMENT on the toggle, never the wording).

This confirms AC-SA311 ("`packing_list` is present on an `incoming` entry only when the
asking contact's `packing_list_allowed` is true") and AC-SA314 ("Engine: B3 with
`packing_list` present emits one `send_attachments` action with that file; without it, no
attachment action") against a real turn through the real backend, not just the pytest suite.

## Files

- `tests/chatbot/console_cases/2026-09-25-stock-ask-v2-s3-branches.yaml` (B1 via Q>X, B1 via
  unset X, B2, B4; round 2 adds `reply_not_contains: ["Data last updated"]` to each case).
- `tests/chatbot/console_cases/2026-09-25-stock-ask-v2-s3-incoming-packing-list.yaml` (B3,
  run against both toggle states; round 2 adds the same `reply_not_contains`).
- This file.

## Result

All six AC-SA318 branches (B1 via Q > X, B1 via unset X, B2, B3 with the toggle on, B3 with
the toggle off, B4) pass for the right reason: each reply is graded on the exact R14
sentence, not the generic error reply or a `branch_kind`-only assertion (the pitfall
`documentation/agents/chatbot-verification.md` and this repo's other console-case files both
name), AND on the absence of the last-updated footer against a DB that actually has a
`BULK_IMPORT` row to trigger it. No case was weakened to pass. No S3 code defect was found
this round; the three additional environment gaps above (workspace/access-agent seeding,
the `integration_api_keys` auth cutover) are pre-existing sandbox/DB gaps, not part of this
round's diff, and were worked around additively (rows seeded, no migration or application
code touched) exactly as round 1's own two gaps were.
