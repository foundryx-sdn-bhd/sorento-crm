# PLAN: parser prompt rendered per contact audience

Status: Built, review READY, hand test owed. Track: full. Crew ruling (b) 13 Oct 2026: ships the backend refusals, the order did-you-mean customer scope fix and the Access tab hint with the prompt trim INERT: ppa_0001 fails closed on today's parser line (prod v52 / dev v103 lack '== LOW STOCK REPORT'), so it writes nothing. Follow-up lane re-tags ppa_0001 against live anchors once the parser line settles. G1 split to draft PR #1487. UAC: `parser-per-audience-2oct-acceptance-criteria.md`.

crew-lane: PARSER-PER-AUDIENCE

## Journey

A dealer asks the bot about stock on WhatsApp. Today the parser is sent the same 33,207-token
prompt a buyer gets, including purchase cost, purchase orders, the sales report, sales analysis,
top selling and the low stock report, none of which the dealer can be answered on. After this lane
the dealer's parser prompt is the same template with those blocks left out (about 26.7k tokens).
The backend refusal stays the control: hiding a block is a size and focus change, never the gate.

## Scout facts (all against #1405 head 8ce60b41, `sorento_crm_backend/`)

### The prompt

- The prod text is `alembic/data/chatbot_semantic_parser.prod-20261001.txt`: 1,741 lines and
  33,207 tokens (o200k_base, which is what `gpt-4o-mini` uses).
- It is rendered per turn at `engine.py:3425-3430` through `head/parser.py:574-610`, which calls
  `ai_prompt_registry.render` (`ai_prompt_registry.py:1415-1440`). That fills `{{current_date}}`
  and the registry tokens from `chatbot_prompt_vars.render_values_safe`
  (`chatbot_prompt_vars.py:389`).
- The text is resolved by the `production` label (`ai_prompt_registry.py:1364-1387`). Its cache
  is a 60 s TTL keyed `(name, label)`.
- At render time the prompt knows nothing about the contact. `access_levels`
  (`chatbot_prompt_vars.py:124-135`) is the catalogue of ALL active access types, not this
  contact's.

### What actually gates a domain

Contact access types do NOT gate any domain.

- **Access types are display and tier facts only.** They are many-to-many on the contact through
  `respond_contact_access_types` (`models/access.py:88-115`, `:302-306`). They drive:
  - the promotion and attachment tier (`lanes/business/tier_gate.py:42-52`);
  - stock visibility (`models/access.py:803-906`);
  - the office-staff exemption from customer scoping (`contact_customer_scope.py:21-41`).
- **The gates that refuse answers are per-contact reveal grants**
  (`contact_field_reveals` -> `ctx.access.attributes`, `head/access.py:42-63,133`):
  - `purchase_orders.cost`: the whole `purchase_cost` domain is refused before any tool
    (`answer.py:1165-1168`, `lanes/business/__init__.py:1459-1474`). Reply: "Sorry, you are not
    allowed to access purchase cost".
  - `sales_orders.sales_report`: the `sales` domain and every `SALES_FIGURE_STATUSES` ask
    (sales_report, sales_analysis, top_selling) are refused (`engine.py:3936-3951`,
    `__init__.py:1601-1611`). Reply: "Sales report is not enabled for your account."
  - `purchase_orders.placed`: gates ONLY the PO rung of the cross-domain climb
    (`answer.py:1156`). **A direct `purchase_order` ask is NOT refused.** It runs, and only the
    restricted fields are stripped (`turn/fetch.py:2644-2713`). See Q2.
  - `scm.low_stock_report`: the low stock report (`__init__.py:1526-1527`).
  - `sales_orders.outstanding`: the SO arm of an outstanding answer is redirected to DO
    (`__init__.py:1846-1849`). The order domain itself stays open.
- **Every domain row already carries its grant.** `chatbot_domains.reveal_key`
  (`models/chatbot_policy.py:44`; seed at `turn/policy_rows.py:149,207,301,323,345`).
  A dormant per-domain gate reads it (`turn/apply.py:2204-2208`), but `load_profile` sets
  `grants=None` (`turn_runtime.py:432-436`), so that gate never fires today.
- **Staff** have no identity in a WhatsApp turn. The console (`api/v1/system/chatbot.py:638`)
  runs as a real contact and inherits that contact's grants.
- **Prompt caching:** there is no `cache_control` anywhere. OpenAI applies its automatic prefix
  cache (1,024 tokens or more, exact prefix). The provider is set in the DB per label, defaulting
  to openai / gpt-4o-mini (`models/ai_assistant.py:31-32`).

## Design (final, owner answers 2 Oct 2026)

1. **One code table is the single source: `PROMPT_GATES`, keyed by DOMAIN (+ part).**
   - It lives in `app/services/chatbot/prompt_gates.py`.
   - Each row is `(domain, part)`, where `part` is an intent or status inside the domain, or
     None. The row maps to the grant it needs, the prompt tag it strips, the block titles the UI
     shows, and the asks refused without it.
   - It is the first piece of the owner's ACCESS-MODEL lane (contact -> roles -> domains ->
     fields, deny by default). Keyed by domain, it can later read its grant from the role/domain
     tree instead of a reveal key, without the renderer or the refusal seams changing.
   - The refusal seams in `lanes/business/__init__.py` (`run_fetch`) and `engine.py` (sales
     figures) read the same table, so the trim and the refusal cannot drift.

   | (domain, part) | Grant today | Prompt tag | Blocks removed (UI wording) | Refused without it |
   | --- | --- | --- | --- | --- |
   | (purchase_cost, -) | purchase_orders.cost | purchase_cost | LAST PURCHASE COST | domain purchase_cost |
   | (purchase_order, -) | purchase_orders.placed | purchase_order | PURCHASE ORDERS, PO examples and vocabulary | domain purchase_order (Q2, new), plus the PO rung |
   | (spo_allocation, -) | purchase_orders.placed | spo_allocation | SPO LAST RECEIPT | domain spo_allocation (G2, new) |
   | (sales, -) | sales_orders.sales_report | sales | SALES REPORT, SALES ANALYSIS, TOP SELLING | domain sales; order_status sales_report / sales_analysis / top_selling |
   | (inventory, low_stock_report) | scm.low_stock_report | low_stock_report | LOW STOCK REPORT | intent low_stock_report |

2. **The UI shows the map.** `GET /api/v1/system/chatbot/field-reveal-keys` gains
   `prompt_blocks: [str]` per key, read from `PROMPT_GATES`. The Field reveals switch on the
   contact Access tab prints "Also removes from the chatbot prompt: SALES REPORT, SALES
   ANALYSIS, TOP SELLING" under the label, and nothing for a key with no blocks.
3. **Tags in the template.**
   - Block form: `{{#only purchase_cost}}` ... `{{/only}}` on lines of their own.
   - Inline form: the same markers around a span inside a shared line.
   - An unknown or unbalanced tag keeps the block, removes the markers and logs a warning.
4. **Audience = `ctx.access.attributes`.** These are the reveal keys `head/access.py:42-63`
   reads, the same set every refusal reads. An unidentified contact gets `[]`, which is the
   minimal render (Q5 = a, no special case).
5. **Live on merge, with no switch (Q3 = NO).**
   - Render: `head/parser.resolve_config` takes the audience. The engine passes the turn's
     grants (`engine.py:3425`), and the tag strip runs after the `production` label resolves.
     It uses the same version and the same label, so there is no second version stream.
   - Prompt editor preview: renders the full audience.
   - Migration: publishes the tagged copy of the prod text as an UNLABELLED version, like
     `pdyn_0003`. It proves that a full-audience render equals the prod file byte for byte
     before it inserts. The owner promotes it, so the trim is live once that version is
     labelled.
6. **Inline PO text is stripped (Q4 = a)**, and a leak test matrix pins it (see UAC).
7. **The backend is the control, proven per domain** (crew finding, 2 Oct 2026).
   - `turn_runtime.load_profile` passes `grants=None` (`turn_runtime.py:432-436`), so the
     domain-row reveal gate (`turn/apply.py:2204-2208`) never fires. `chatbot_domains.reveal_key`
     is NOT enforced at runtime.
   - Enforcement exists only where code constants check grants.
   - Turning `Profile.grants` on as it stands would deny every domain with no key, because the
     fallback is the bare domain name. It would also deny all of stock and order for anyone
     without `inventory.sellable` / `sales_orders.outstanding`, which are PARTIAL field keys,
     not domain gates.
   - So enforcement goes through `PROMPT_GATES` at `run_fetch`, the seam that already refuses
     purchase cost. The leak matrix proves each row end to end.

### Every domain's actual gate today (#1405 head 5f28b7b9; main 7eb9767a carries the same code)

All business turns first pass the agent gate: `check_access` in `engine.py:3688-3694` needs an
allowed `contact_agent_access` row for the routed agent.

| Domain | Whole-domain refusal | Partial (field / scope) | Gap |
| --- | --- | --- | --- |
| master_products | none | spec keys hidden by stock visibility policy (`head/access.py:66-101`) | none |
| product_attachment, promotion, resource_attachment | none | tier from access type (`tier_gate.py:42-52`) | none |
| forms, portal_link, ideate | none | none | none |
| inventory | none (`chatbot_stock_allowed` -> `stock_denied`, `engine.py:4212`) | `inventory.sellable` field drop (`fetch.py:1094-1097`); low_stock_report intent refused on `scm.low_stock_report` (`lanes/business/__init__.py:1525-1527`) | none |
| order | none | customer scope only when the contact has links AND no office type (`contact_customer_scope.py:49-51`); SO arm on `sales_orders.outstanding` (`__init__.py:1846`); sales figure statuses refused on `sales_orders.sales_report` (`engine.py:3936-3951`, `__init__.py:1601-1611`) | **G1: a non-office contact with NO customer links is unscoped and can read any customer's orders** |
| sales (#1405) | `DOMAIN_GRANT_REQUIRED` (`answer.py:1165-1168`) | none | none |
| incoming | none | per-field `GATED_FIELDS`, deny by default (`field_access.py:56-119`) | none |
| spo_allocation (last in) | none | none (tool returns spo_number, quantities, warehouse) | **G2: SPO numbers reach any contact holding the agent** |
| goods_receive | `supported=False`, so not_supported (`policy_rows.py:256-265`) | none | none |
| purchase_order | none | `purchase_orders.supplier` / `.placed` field drop (`fetch.py:2644-2713`) | **G3: closed by Q2 (whole-domain refusal)** |
| purchase_cost | `DOMAIN_GRANT_REQUIRED` | cost and supplier field drop | none |

Owner rulings 2 Oct 2026:
- **G2 = (a):** `spo_allocation` goes under `purchase_orders.placed`. It is refused, and its
  SPO LAST RECEIPT block is stripped.
- **G1 = (a):** fail closed. A contact with no office access type and no linked customer is
  refused customer order data ("Sorry, I can't find an account linked to you yet.", no tool
  call).
  - It ships ONLY after the owner-gated data step: crew brings the list of the 55 internal dev
    contacts without an office type, and they are fixed first.
  - The G1 commit is kept separate and named in the PR description as blocked on that step.

### Who on dev is affected on merge (Q3: live, crew data, 100 internal contacts)

| Contacts | Grants held | Prompt loses |
| ---: | --- | --- |
| 88 | placed only | cost, sales (report / analysis / top selling), low stock |
| 5 | cost + placed | sales, low stock |
| 5 | none | cost, PO, sales, low stock, and PO asks are now refused (Q2) |
| 1 (CONTACT B) | cost + placed + sales_report | low stock |
| 1 (CONTACT C) | all four | nothing |

Every one of the 99 already gets a refusal for each block it loses, so only the prompt changes.
The exception is PO for the 5 with no grant: their PO asks change from a field-stripped answer to
a refusal.

## Tag map on the owner's prod text (prod-20261001 line numbers)

| Tag | Grant | Whole-line blocks | Tokens | Inline spans |
| --- | --- | --- | ---: | --- |
| purchase_cost | purchase_orders.cost | 81, 727-729, 985-1004 (LAST PURCHASE COST), 1675 | 410 | 82 enum entry, 87 "purchase cost", 928-929, 933 |
| purchase_order | purchase_orders.placed | 414-416, 698, 918-933 (PURCHASE ORDERS), 957-963, 971-973, 1674 | 586 | 46, 87 "PO", 583, 655, 693-694, 777, 786, 792 supplier, 803, 820, 968, 1621 |
| sales | sales_orders.sales_report | 651-652, 1027-1134 (SALES REPORT), 1250-1296 (SALES ANALYSIS), 1397-1560 (TOP SELLING) | 5,267 | 778 `sales_report` |
| low_stock_report | scm.low_stock_report | 1006-1025 (LOW STOCK REPORT) | 278 | 1666 intent `low_stock_report` |

Kept for everyone: the SO outstanding vocabulary (`so_outstanding`, `do_outstanding`), because
the DO rules share it; SPO / last in (`spo_allocation`, no grant); incoming, stock, promotion,
product, attachments, forms, portal and ideate.

## Measured sizes (o200k_base, whole-line blocks only; inline spans take roughly 100 more off)

| Audience | Tokens | Share of full |
| --- | ---: | ---: |
| Full (all four grants: buyer, admin console) | 33,207 | 100% |
| No cost (PO + sales + low stock) | 32,797 | 98.8% |
| Sales only (linked dealer holding the sales report grant) | 31,933 | 96.2% |
| Dealer (none of the four; PO block now includes SPO LAST RECEIPT) | 26,475 | 79.7% |

### Real dev contacts (crew, read-only, sorento_cagent_stack, 100 contacts, 2 Oct 2026)

| Gated grants held | Contacts | Example | Tokens | Share |
| --- | ---: | --- | ---: | ---: |
| purchase_orders.placed only | 88 | CONTACT A, End User | 27,252 | 82.1% |
| cost + placed | 5 | | 27,662 | 83.3% |
| none | 5 | | 26,475 | 79.7% |
| cost + placed + sales_report | 1 | CONTACT B, all 7 types | 32,929 | 99.2% |
| all four | 1 | CONTACT C, type dealer | 33,207 | 100% |

All reveal rows are `granted=true`. 95 of 100 contacts hold `purchase_orders.placed`, an end
user among them. With Q1(a) as asked, nearly every dealer keeps the PO blocks: on dev data the
grant does not separate dealers from office. See the revised Q2 on #1429.

Prompt cache:

- `{{current_date}}` sits at line 14 (126 tokens in), so the cache already turns over daily.
- The first gated span is line 46, at 692 tokens in. That is under the 1,024-token minimum, so
  each audience is its own cache entry and no prefix is shared across audiences.
- With at most a handful of live grant combinations, each audience still gets heavy traffic, so
  the hit rate per audience stays close to today's. The cost is one cold miss per audience per
  day.
- No `cache_control` is used today, so the Anthropic path is uncached either way.

## Owner decisions

- **Q2 = (a), 2 Oct 2026.** Dealers cannot ask about POs: `purchase_order` joins
  `DOMAIN_GRANT_REQUIRED` on `purchase_orders.placed`.
  - The switch is relabelled "Purchase orders (PO asks, and PO on stock answers)".
  - The revocation list for dealer and end-user holders goes to the owner; it is a data change,
    not code.
  - Answers to the owner's follow-ups (i)-(iv) are on #1429:
    - grant screen: Contact > Access > Field reveals;
    - switch home: Settings > Chatbot > Switches;
    - dealer holders: a SQL list, with the recommendation "revoke by list, no auto-deny by type";
    - unidentified contacts: they already hold zero grants, so there is no special case.
- **(iii) = (a), 2 Oct 2026: revoke by list, with no list now.** All current respond contacts
  are internal users; real dealers are not onboarded yet, so there is no data change.
  - A new contact starts with no reveal grant. The only writer is the admin PUT
    (`api/v1/system/chatbot_field_reveals.py:83-100` -> `contact_field_reveal_service.py:86`).
    `granted_keys` returns `[]` for a contact with no rows (`:73-83`).
  - So future dealers get the trimmed prompt and every gated refusal by default.
- **Q1 = (a)**: grants drive the trim. The block-to-grant map is ONE code table
  (`PROMPT_GATES`), documented here and in the PR, and shown on the Field reveals switches.
- **Q3 = NO switch.** Live on merge. The dev contacts affected are listed above.
- **Q4 = (a)**: inline PO text is stripped too, with a leak test matrix over all 16 grant
  combinations plus an unidentified contact.
- **Q5 = (a)**: no special case.
- **Crew finding**: `chatbot_domains.reveal_key` is not enforced at runtime (`grants=None`). Every
  domain's real gate is in the table above. G2 = (a) and G1 = (a) (G1 blocked on the owner data step).

## Slices (build after #1405 merges; tests prepared before)

1. **Red tests, prepared now against the #1405 head:**
   - `tests/chatbot/test_parser_audience_render.py`: the tag strip, `PROMPT_GATES` shape, the
     full render equal to the prod file, 16 combinations plus an unidentified contact free of
     forbidden terms, and edge cases (inline, unbalanced, unknown).
   - `tests/chatbot/test_parser_audience_leak_matrix.py`: an end-to-end `_run_turn` per grant
     combination, with forced parser outputs for cost, PO, sales report / analysis / top
     selling, low stock, supplier and other customers. Each one must be refused, with no
     restricted tool call and no restricted field.
   - `tests/chatbot/test_parser_audience_wiring.py`: the system prompt the parser actually
     receives, per contact.
   - `tests/test_chatbot_field_reveals_prompt_blocks.py`: the API returns `prompt_blocks`.
   - `ContactFieldRevealsSection.test.tsx`: the "Also removes from the chatbot prompt" line.
2. `prompt_gates.py` plus the renderer, wired into `resolve_config`. The refusal seams read
   `PROMPT_GATES`, and `purchase_order` joins them (Q2).
3. Migration: the tagged prod text as an unlabelled version (full render proven byte-identical).
4. API plus UI hint, and the switch relabel.
5. G2 fix in this lane. G1 fix as its own commit, merged only after the owner data step (55 internal contacts without an office type).
6. Kill tests, reviewer, security review, hand-test script, PR ready.

## Red test findings (tester, 2 Oct 2026; tests at 21debae7 and f08ed10b)

- **Low stock refusal is swallowed on a real turn.**
  - Setup: a contact without `scm.low_stock_report` asks for the low stock report.
  - What already holds: no tool runs and no file is sent.
  - The defect: the reply is "Could not find inventory. Would you like me to escalate to
    warehouse team?", not "Low stock report is not enabled for your account.". `run_fetch`
    builds the refusal (`lanes/business/__init__.py:1526-1527`), but the miss composer replaces
    it.
  - The fix is in this lane, at the same seam as the PROMPT_GATES refusal.
- **About 30 `fake_resolve_config` stubs** under `tests/chatbot/` take no `grants` keyword. The
  engine passes `grants` to `resolve_config`, so the coder sweeps the stubs (one line each).
- **The relabel of `purchase_orders.placed`** is also pinned in
  `sorento_crm_mcp/sorento_crm_mcp/catalog.py:1330` and
  `sorento_crm_mcp/tests/test_field_reveal_keys_declared.py:112`, via
  `tests/chatbot/test_field_reveal_keys_pinned_to_catalog.py`. Both sides change together.
- **Matrix today:**
  - Green: cost, the three sales statuses, the PO rung, supplier, other customers.
  - Red: direct PO, SPO last in, the low stock reply text, and G1.

## Red-first proof (owner rule, 2 Oct 2026)

The steps, in order:
1. Every new or changed test is committed ALONE first, with a subject starting `test(red):`.
2. Run those tests and post `crew-note: red-proof <sha> <command>` with the failing output.
3. Commit the code.
4. After green, break or revert the fix, show the same tests fail, restore it, and post
   `crew-note: kill-proof`.
5. The crew-done DoD lists the red-proof shas and the kill-proof.

Already posted: the red-proof for 21debae7 (134 failed / 134 passed) and for f08ed10b (G1, 3
failed / 2 passed). Both commits predate the prefix rule.

## Cloud browser pass (owner rule, 3 Oct 2026)

Before a head is reported hand-test ready:
1. Run the app plus a headless chromium (agent-browser) in this sandbox, against the sandbox
   Postgres with a seed script that mirrors the owner's cases. No customer data.
2. Cover every hand-test script step and the owner's exact failing messages, at 1280 and 375.
3. Post `crew-note: crew-tester (cloud) pass at <head>` with a PASS/FAIL table.
4. Rerun whenever the head moves. Crew brings a local copy only for the owner's hand test.

Planned split for this lane:
- Cloud, seeded: the Access tab hint line per key, the relabel, `GET field-reveal-keys`
  `prompt_blocks`, and the chatbot console turns for each seeded audience (dealer with no
  grants, P only, C+P, all four, an unlinked non-office contact for G1). The refusal replies
  are deterministic backend text, so the seed only needs one product, one PO, one SPO
  receipt, two customers with orders, and the reveal rows.
- Needs the live parser key (not dev data): free-text asks go through the OpenAI parser. If
  the sandbox has no key, those steps run with the parser output forced, as the matrix does,
  and the PR says so.
- Needs real dev data: answers over real customers and products (wording and figures the
  owner recognises) and the G1 data step on the 55 internal contacts. These stay in the
  owner's hand test.

Real dev data (owner rule, 3 Oct 2026): when the cloud pass needs real rows, post a PR
comment, filed as a crew ask (`crew-ask: crew-data-request: ...`), naming the tables, the filters (codes, ids, date range)
and why. Crew sends masked rows by crew message. They load into the sandbox DB only, from a
file kept outside the repo (scratchpad). Real data is never committed and never pasted in a PR
or gist.

## Kill list (tests must fail when these are broken)

- The strip keeps a tagged block for a contact without the grant.
- The audience is read from access types instead of grants.
- A refusal seam stops reading `PROMPT_GATES` (the matrix goes red).
- The full render differs by one byte from today's.
- The backend refusal is removed (the forced-intent test must go red).
