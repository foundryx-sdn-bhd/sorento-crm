# PLAN - Chatbot stock ask v2: four-branch answer on #1118's verdict step, X cap, Y ETA offset, salesman notification, asks record

Status: approved by the owner 24 Sep 2026 (lavish review, "ok cool, good to go"); tickets S0 #1193, S1 #1194, S2 #1195, S3 #1196, S4 #1197, S5+S6 #1192. S1 and S2 Phase 2 backend built (24 Sep 2026, PR #1221) - migrations, permission slugs, resolver, contact toggles, both Phase 1 FE overlays deleted. S0, S3 to S6 not built. S3 in PR #1247: built 25 Sep, hand-test fix round 3 (slices 1 to 6, F1, F2) built 26 Sep 2026. Round 4 (the owner's round 3 hand test: numbered which-one list, a bare number after an answered quantity revises it) built 26 Sep 2026. Round 5 (the which-one picker is not sticky) built 26 Sep 2026. Round 6 (the owner's console test of round 4: "all", point form, 429 retry, small talk fast path, parser tokens) built 26 Sep 2026. Round 7 (ruling 4 dropped: the small-talk and quantity fast path removed, every message goes through the parser) built 26 Sep 2026. Round 8 (the owner's console test of round 7: the open question object and the last three exchanges to the parser, the declared open_question_answer driving the apply layer, the row 12 clarify, the dropped sibling code, the n8n JS literal cut) built 26 Sep 2026; the prompt edit needs a republish of chatbot_semantic_parser. Round 9 (issue #1293, the owner's console test of round 8: "the first one, I need 2" after a did-you-mean; every question the bot asks is one open-question object the parser answers) built 26 Sep 2026, full track (one prompt-publish migration, `sa2_r9_open_question`), folded into PR #1247 by owner ruling 26 Sep 22:30 MYT; the prompt goes live when `production` is moved onto the version that migration publishes. Round 10 (fix lane, 26 Sep 2026): main dc10a1afb merged in, `sa2_r9_open_question` re-parented onto main's head `sales_0002_team_leader`, single alembic head again. S4, S5 and S6: build lane started 28 Sep 2026 from origin/main 77d083a2 (owner go 28 Sep, "for stock ask yeah we need S4 and S5"), one PR, full track. S4 built 28 Sep 2026 (salesman notification on the `stock_ask_salesman` use case, `app/services/stock_ask_service.py`, `app/tasks/stock_ask_tasks.py`). S5 built 28 Sep 2026 (`stock_asks`, migration `sa2_0004_stock_asks` on main's head `merge_28sep_batch2`, CRM customer Asks tab). S6 built 28 Sep 2026 (`sales_agent_for_contact` extracted, `GET/PATCH /public/portal/customer-asks`, portal Customer asks page and landing link). Lane review round (28 Sep): a reply still owing a quantity records nothing; the notify job reads the ask's own customer and company. Console fix round (28 Sep, owner ruling "it should work using chat console also"): a chat console turn (`engine._chat_console`: dry run, ingress console, `console_origin` extra) writes the asks rows with `source = console` and enqueues the real salesman job; its own reply stays a dry run; migration `sa2_0005_stock_ask_source`. Hand-test fix round 5 (29 Sep, owner: "customer asks should be own tab like price tag request, and can be controlled per contact"): Customer asks is a `customer_asks` kind in the portal landing selector (open count badge, cards or list, State filter, no New) instead of the card above the search box, and a per-contact switch in the contact's Portal forms (the `contact_portal_form_overrides` row Price Tag Request uses, default off, no migration); the route answers 403 FORM_TYPE_NOT_VISIBLE when it is off and the selector offers it only to a linked sales agent. main 54c3b404 merged, `sa2_0004_stock_asks` re-chained onto `cpc4_cost_packaging_method`. S4 to S6 built in PR #1333, awaiting CI and the owner's hand test; not merged.
Issue: #1168. Grill: owner rulings R1 to R11 of 24 Sep 2026 (quoted below, binding; they override the pre-grill draft).
UAC: `chatbot-stock-ask-v2-24sep-acceptance-criteria.md`
Depends on: PR #1118 (`feat/chatbot-dealer-stock-verdict`, open) rebased onto main and merged (slice S0); PR #1177 (`feat/customer-sales-agent-assignment`, open, #1170 slice 1) merged before S4.
Classification: the X / Y columns, the contact toggles and the `stock_asks` table are CORE in `public` (they extend `product_categories`, `products`, `respond_contacts`, `customers`); the branch decision lives in the stock balance read #1118 already extends (`app/services/inventory_service.py`), the notification and the ask write live in the `chatbot` module package (`app/services/chatbot/`), the Respond send in `app/tasks/`.

## Rulings (owner, grill of 24 Sep 2026, quoted verbatim)

> R1 Scope: contacts whose stock visibility policy mode is "Availability only". v2 is a change to PR #1118's verdict step (branch feat/chatbot-dealer-stock-verdict, feat: dealer stock verdict): keep #1118's per-product quantity collection (Focus.tasks, StockQtyTask) and replace its answers with the four branches below. The old stock_denied / demand_qty path on the chatbot_stock_denial_enabled flag stays exactly as is for stock-denied contacts. #1118 must be rebased onto main and merged before v2 slices start; the plan states that as slice 0.
>
> R2 X and Y: plain nullable integer columns on product_categories and products (X = max quantity the AI may answer for; Y = days added to the shipment ETA). Product value overrides the category value when set; only the product's own category, never a parent. Unset means 0: no X means the AI cannot answer any quantity for that product (opt-in per category), no Y means 0 days. Editing X and Y is gated by a new permission slug from the _crud pattern; shown on the category form and the product form, view = edit layout.
>
> R3 Location scope: the asking contact's stock visibility policy locations (site pool), the same set #1118 uses. No new column on customers.
>
> R4 "Got stock" = #1118's available (on hand in the policy locations minus what sales orders reserve) covers Q.
>
> R5 ETA source: the earliest still-incoming inbound_shipments row (shipment_status != draft, a line for the product with line_status != received and quantity_shipped - quantity_received > 0) that has a packing list (attachment_id IS NOT NULL), ordered by estimated_arrival_date asc nulls last, ANY location. Never eta_delay_date, never spo_allocations, never purchase orders. Answer date = estimated_arrival_date + Y, formatted dd/mm/yyyy. #1118's incoming and purchase reads and its "purchase, ETA in N days" wording are removed from this path.
>
> R6 Branches after quantity Q for product P is known:
> B1 Q > X, or X unset: "The quantity is more than what I can confirm here, please refer to your salesman" (polite, professional). Never reveal stock. Notify the agent; when X is unset the agent notification reason reads "no cap set for <category>".
> B2 Q <= X and available covers Q: "Yes, we have stock for <P> x Q, please refer to your salesman to proceed". Notify the agent.
> B3 Q <= X, not covered, a shipment per R5 exists: "No stock at the moment, ETA dd/mm/yyyy" (date + Y). No notification. Attach the shipment's packing list only when the contact's packing_list_allowed toggle is on; otherwise no attachment and no quantities.
> B4 Q <= X, not covered, no shipment per R5: "No stock and no incoming at the moment, please refer to your salesman". Notify the agent.

(R14 below, lavish review, prefixes each of these four sentences with the product and quantity; the wording quoted above is the original grill text.)

> R7 Per-contact toggles on respond_contacts: notify_salesman (default off) and packing_list_allowed (default off), on the contact form.
>
> R8 Notification: customer -> customers.sales_agent_id -> sales_agents.contact_id -> Respond.io. One use case stock_ask_salesman, one template with an outcome phrase slot (in stock / too big / no stock no incoming / no cap set) and the ask facts (customer, contact, product, quantity, time). Sent through send_text_or_template (template outside the 24h window, plain text following the template wording inside it). Each send writes its integration_log row. No agent or no agent contact: record the ask, skip the send, log the skip reason.
>
> R9 Asks record ("Asks"): a new table, one row per stock ask answered by v2: customer, contact, product, quantity, branch, answer summary, notified_agent (bool + reason when skipped), state open / done, note (free text), timestamps. Surfaces: CRM customer detail page "Asks" tab worked by the office (state + note editable, RBAC via the customers edit permission); portal page "Customer asks" for the sales agent's linked contact, listing asks for customers assigned to that agent, same state + note, using the portal DataGrid pattern (PortalLanding) and the debtors-for-agent resolution. Portal page is the LAST slice. Opportunity link comes with #1170 later (one nullable column then).
>
> R10 Detailed and Compact mode contacts are unchanged by v2.
>
> R11 Dependencies: PR #1177 (customer sales agent picker, slice 1 of #1170) gives the customer -> agent link; #1118 gives quantity collection and the availability read.

### Lavish review, 24 Sep 2026

> R10 "a shipment shouldn't have null eta". Measured on the prod copy sorento_ai_automation_0921: 0 of 275 inbound_shipments have a NULL estimated_arrival_date (119 in_transit, all dated). Plan change: an R5 candidate shipment must have estimated_arrival_date NOT NULL; one without it does not qualify, so the dealer gets B4. Remove the "ETA to be confirmed" line everywhere (S3 entry shape, Risks item about NULL ETA, and UAC AC-SA309: replace that AC with "a shipment lacking estimated_arrival_date never qualifies for R5; with no other qualifying shipment the answer is B4"). Record the measurement in the plan.
>
> R11 "can we keep the column?" (stock-low-threshold). Yes: keep system_settings.chatbot_stock_low_threshold_pct and its Settings card exactly as #1118 lands them. Delete migration sa2_0003_drop_stock_low_threshold from S3, delete the "Settings > Chatbot loses the threshold card" frontend seam line, delete the Risks item that offers dropping dsv_0001 in S0, and adjust any UAC line that asserted the column or card is gone (state instead: unchanged, still unread by v2).
>
> Owner ruling 25 Sep 2026: the threshold column is dropped, #1118 never merges.

Owner ruling 26 Sep: R1 re-ruled after the owner's hand test on PR #1247, verbatim "for R1 - okay can": R1's "keep #1118's per-product quantity collection (Focus.tasks, StockQtyTask) ... unchanged" is lifted, and the scout's slices 2 to 6 (family pick, resume narrows, a task never swallows new products, revise the last answer, did-you-mean keeps the quantity) are built in PR #1247. UAC AC-SA319 to AC-SA324.

Owner ruling 26 Sep: F1, verbatim "we should offer the ELP3756 and yes, yes should still go to warehouse, oh wait, btw dealer ask cannot have escalation, cannot have direct escalation to warehouse, their contact point is sales person". Implemented reading: a one-candidate did-you-mean is a pick carrying the typed quantity ("Couldn't find ELP3753. Did you mean ELP3754?"); "yes" or the code answers that product with the carried quantity; "no" refers the dealer to their salesman. A dealer (an availability-only contact, `Profile.stock_availability_only`, read off `stock_visibility.resolve_policy`) is never offered or given an escalation anywhere in the stock ask: every such offer or stored team pick becomes "Please refer to your salesman." with no pending question. Staff (detailed / compact) keep today's behaviour. UAC AC-SA325.

Owner ruling 26 Sep: F2, "okay": one bare number after a question about several products (a real multi-product ask, not a family) applies to each of them, and the reply names each product with that quantity. UAC AC-SA326.

Owner rulings 26 Sep (round 3 hand test on the :3087 console, head 40cb26022, prompt v32): (1) a which-one pick list is numbered ("1. SRTWC286-SH, 2. SRTWC286-SH-150, ..."), like the other pickers; (2) after a quantity has been answered, a bare number is a revised quantity for that same product ("2" -> SRTWC286-SH x 2 answered straight away), never a pick from the old list and never a re-ask; (3) the fix round explains why "2" re-asked. Implemented reading: the spent pick's options ride on the one-product stock task (`Task.picked_from`) so "no, the 2nd one" can still pick again. UAC AC-SA327 to AC-SA329.

Owner ruling 26 Sep ~08:25Z, verbatim: "for stock ask, the gist is let's make the picker not sticky". Supersedes round 4's `Task.picked_from` reading: once one product is picked off a which-one list, the list is closed and forgotten for position resolution (nothing kept on the task, nothing offered to the parser), so a later bare number, with or without a "no", is that product's quantity and never a pick. A new "check stock <code>" starts a new pick. Scoped to the stock ask pick; the customer picker and the top X list do not share this path. UAC AC-SA330 (replaces AC-SA329).

Owner rulings 26 Sep (console test of round 4, :3087, 08:18Z to 08:21Z, head 9f20c24c1), from the owner's words "why i can't do all, and when i do all, why so complicated, can it be point form, we can do like numbered list, then " - " and let them put in (no emdash), well of course they can just say one number like 10 to apply to all, but why rate limit is reached? why when i say tia also rate limit reached?": (1) "all" over the which-one list opens one quantity question for every product; (2) that question is point form, "How many units for each?" then one "N. CODE - " line per product, and the dealer replies per line ("1. 10, 2. 5", "SRTWC286-SH 10", the lines pasted back filled in) or with one number that applies to all; (3) a 429 is retried with backoff inside the turn through one wrapper for every chatbot model call (parser, recall re-parse, clarifier), and on final failure the reply is "Sorry, I am busy right now, please send that again in a minute."; (4) small talk ("tia", "thanks", "ok") takes a fast path with no parser and no clarifier call; (5) the parser's tokens per turn are measured and cut where safe. Built as `chatbot/llm_call.py` (the wrapper) and `chatbot/head/fast_path.py` (small talk, one number or numbered lines over the open stock question, read from the message's shape with no model call). UAC AC-SA331 to AC-SA335.

Owner correction 26 Sep ~08:33Z, verbatim: "wait for the small talk skip ... the tia is a typo of tiga so it supposed to pass through parser, we shouldn't be so rigid to say small talk bypass the parser". Ruling 4 above is DROPPED: there is no fast path, and every message goes through the parser. `head/fast_path.py` is deleted in round 7, both halves (the small-talk replies and the quantity-shape reader). A numbered-lines reply reaches the open question's lines through the parser's own reading (`apply._numbered_lines_are_the_products`: entities keyed by the line number, or `reference_positions` beside the quantities), and the open task's hint prints the lines so the parser can name the product by code. UAC AC-SA336 (AC-SA335 superseded).
>
> R12 "the product inherit from category, overridable, we don't need parent category -> category relationship for now". This already matches the resolution rule (product value, else its own category value, else 0); add the quote under Rulings and leave the rule as is.
>
> R13 owner asked "the notification to salesperson is in this plan?" Answer is yes, S4. Add one line at the top of S4 saying so in plain words.
>
> R14 on the two-product sample (g): "need to specify the product code, else nobody knows what does 2nd line mean, for every answer, we must include product code". Effect: every one of the four fixed sentences (B1 to B4) starts with the product and quantity, so a multi-product reply reads line by line. Proposed shape (wording proposed, owner proof-reads it on the page): B1 "<P> x Q: the quantity is more than what I can confirm here, please refer to your salesman." B2 "<P> x Q: yes, we have stock, please refer to your salesman to proceed." B3 "<P> x Q: no stock at the moment, ETA dd/mm/yyyy." B4 "<P> x Q: no stock and no incoming at the moment, please refer to your salesman."

What these rulings retire from the pre-grill draft: `customers.stock_warehouse_id` (R3), any change to the `stock_denied` / `demand_qty` arm (R1), `COALESCE(eta_delay_date, ...)` and location-scoped incoming (R5), "NULL X = no cap" (R2), the fifth `unassigned` answer (R3 removes the missing-location case; a contact with no customer still gets its branch answer, only the send is skipped per R8), and a "Stock asks" card in place of a tab (R9).

## What exists today (measured 24 Sep 2026: main at 855ccf0c, #1118 head at c5249888)

### #1118, the path v2 changes (not on main)

- Quantity collection: `app/services/chatbot/turn/task.py` (`Slot` :34, `Task` :45, `StockQtyTask` :178, `TASK_KINDS = {"stock_qty": StockQtyTask(), "ideation": IdeationTask()}` :382, `tasks_after_reply` :773, which rebuilds the task from the stock tool's `stock_availability` block); `Focus.tasks: tuple[Task, ...]` on `turn/state.py:120`. The engine calls `turn_task.tasks_after_reply(...)` in `engine.py` (:2319 on the #1118 head). v2 does not touch any of this.
- The read: `GET /api/v1/inventory/stock/balance` (`app/api/v1/inventory/stock.py:203`) with `contact_id` + `space_id` (the contact the ask is on behalf of) and `requested_quantities` (JSON map, `parse_requested_quantities`). For a policy with `mode == "availability"` (UI label "Availability only", `services/stockVisibilityService.ts:114`; `stock_visibility_policies.mode` CHECK in `app/models/access.py:846`) `StockService._apply_stock_visibility` (`inventory_service.py:1246` on #1118) returns no rows and a `stock_availability` list, one entry per product code.
- Inside that block (#1118 `inventory_service.py:1458-1709`): `_supply_scope(column)` = `warehouse_criterion(policy, column)` + active warehouse + caller's `warehouse_ids` (:1482); open SO per product in that scope (:1497-1515); on hand minus open SO = `net_available` (:1665-1670). These two stay: they ARE R3 + R4.
- Also inside it, and removed by v2 (R5): the incoming read over `spo_allocations` (:1517-1566), the PO read over `purchase_order_lines` (:1568-1593), the threshold + lead-time read (:1595-1605), the `stock_verdict.verdict()` call and the `disclaimer` / `running_low` fields (:1680-1706). `app/services/stock_verdict.py` (pure, `Verdict(answer, running_low, sources, limited)`) has no other caller.
- Wording: MCP presenter `sorento_crm_mcp/sorento_crm_mcp/presenters.py` `_availability_line` (:1447 on #1118): "Yes, available." / "Yes, available, but running low." / "Not available, but there is ... incoming, ETA <date> ... purchase, ETA in <N> days." (:1454-1472). v2 replaces that function's body.
- #1118 also adds `system_settings.chatbot_stock_low_threshold_pct` (migration `dsv_0001`) and `StockLowThresholdCard.tsx` on Settings > Chatbot. After R6 nothing reads the threshold (no "running low" answer exists in B1 to B4).

### Incoming (main)

- `inbound_shipments` (`app/models/procurement.py:126`): `estimated_arrival_date Date` (:138), `shipment_status String(50)` (:143), `attachment_id` FK `attachments.id` (:150, the packing list), `eta_delay_date` (:177, NOT used, R5).
- `inbound_shipment_lines` (:343): `shipment_id` (:347), `product_id` (:348), `quantity_shipped` (:355), `quantity_received` (:408), `line_status` (:409).
- `app/services/incoming_stock_service.py`: `_still_incoming_filter()` (:73, `line_status NOT IN ('received')` AND `GREATEST(quantity_shipped - COALESCE(quantity_received,0), 0) > 0`) and `_not_draft_shipment_filter()` (:82, `shipment_status != _DRAFT_SHIPMENT_STATUS`) are exactly R5's line and shipment predicates; `_attachment_payload(attachment)` (:159) returns `{filename, file_path, mime_type}`.

### Contacts, customers, agents, messaging (main)

- `respond_contacts` (`app/models/access.py:230`): `chatbot_recall_enabled` (:266), `chatbot_stock_allowed` (:269). Surface: `PUT /api/v1/user-management/contacts/{id}/chatbot` (`ContactChatbotUpdate`, `app/api/v1/user_management/contacts.py:220`), manual dict builder `contact_to_response_dict` (`app/services/contact_service.py:317`), `turn_runtime._PROFILE_COLUMNS` (`turn_runtime.py:146`), FE `user-management/contacts/[id]/components/ContactChatbotSection.tsx` (Switch rows).
- `customers.sales_agent_id` FK `sales_agents.id` (`app/models/order.py:142`); settable from the CRM once #1177 merges. `sales_agents.contact_id` FK `respond_contacts.id` (`app/models/sales_agent.py:89`). Contact to customer: `contact_customer_service.resolve_customer(db, contact_id)` (`app/services/contact_customer_service.py:44`, `None` when unlinked or ambiguous).
- `send_text_or_template(...)` (`app/services/respond_messaging_service.py:545`), in-window text via `render_in_window_text` (:485), `TemplateSendSkipped` (:99). `integration_log` write pattern: `app/tasks/notification_tasks.py:178-231` (success and failure rows). Use cases: `TEMPLATE_DEFAULT_USE_CASES` (`app/models/respond_template.py:37`); FE labels `USE_CASES` (`services/whatsappTemplateService.ts:177`) read by `SetDefaultTemplateDialog.tsx`.
- Chatbot attachments: the engine emits a `send_attachments` action with `attachments_src` (`engine.py:3074`, `:3722`).

### Master data and RBAC (main)

- `_crud(module, resource, name_prefix)` (`app/rbac/permission_registry.py:11`) yields `.view/.add/.edit/.delete`; `_crud("master_data", "products", ...)` :193, `_crud("master_data", "product_categories", ...)` :205, `_crud("order_management", "customers", ...)` :98. Grant sweep precedent: `alembic/versions/522_autocount_pull_perms.py`.
- FE: `master-data-management/product-categories/components/CategoryForm.tsx` (modal), `master-data-management/products/components/ProductForm.tsx` (page, Basic Information card). `CustomerDetail.tsx` (`order-management/customers/components/`, 144 lines) is two Cards today, no tabs.

### Portal (main)

- `PriceTagRequestService.lookup_debtors_for_agent(db, contact_id)` (`app/services/price_tag_request_service.py:2367`): resolves the one `SalesAgent` whose `contact_id` is the portal contact (ordered, warns on two), then customers with `sales_agent_id == agent.id` (plus 24 months of order debtors). Route `GET /api/v1/public/portal/lookups/debtors-for-agent` (`app/api/v1/public/portal_price_tag.py:662`, `get_portal_token`).
- `app/(auth)/portal/components/PortalLanding.tsx` renders its list view with the repo `DataGrid` + `DataGridTable` (:1057-1074); `price_tag_request/page.tsx` is the per-type page shape.

## Journey (the UAC carries it in full)

A dealer contact on an "Availability only" policy names products; #1118's task collects a quantity per product. For each product the bot answers one of four fixed sentences and never a number of ours except Q and a date. For B1, B2, B4 the customer's sales agent gets one WhatsApp line when the contact's "Notify salesman" is on. Every answered ask whose reply refers the customer to their salesman (B1, B2, B4; not B3 `incoming`, amended 1 Oct 2026 by `PLAN-customer-asks-refer-only-1oct.md`) is a row the office works on the customer's Asks tab and the agent works on the portal's Customer asks page.

## Sample conversations (for the owner to proof-read)

Dealer contact: Ah Seng (Hock Lee Trading). Every bot sentence below is R14's shape (wording proposed, owner proof-reads it on the page) with only the product code, quantity and date substituted: each line starts "<code> x <quantity>:" so a multi-product reply reads line by line. Every agent notification line renders the S4 context vars (R8/S4) through a plain, one-line layout that is not yet the final template copy - marked "(wording proposed)" where the plan leaves the exact words open. Dates are dd/mm/yyyy.

**(a) B2 - in stock, Q <= X**

Dealer: Do you have SRT5674, need 50 units?
Bot: SRT5674 x 50: yes, we have stock, please refer to your salesman to proceed.
Agent (WhatsApp): Stock ask - Hock Lee Trading (contact: Ah Seng) asked about SRT5674, qty 50, outcome: in stock. Asked at 24/09/2026 14:32. (wording proposed; sent as plain text, inside the 24h window)

**(b) B1 - quantity above X**

Dealer: I need 300 units of CWCX604.
Bot: CWCX604 x 300: the quantity is more than what I can confirm here, please refer to your salesman.
Agent (WhatsApp): Stock ask - Hock Lee Trading (contact: Ah Seng) asked about CWCX604, qty 300, outcome: too big. Asked at 24/09/2026 14:35. (wording proposed; sent as template, outside the 24h window)

**(c) B1 - X unset for that category**

Dealer: Can I get 20 of SRT446?
Bot: SRT446 x 20: the quantity is more than what I can confirm here, please refer to your salesman.
Agent (WhatsApp): Stock ask - Hock Lee Trading (contact: Ah Seng) asked about SRT446, qty 20, outcome: no cap set for Wiper Blades. Asked at 24/09/2026 14:40. (wording proposed; sent as plain text, inside the 24h window)

**(d) B3 - incoming with ETA, packing list toggle OFF**

Dealer: Do you have SRTW2000, need 150?
Bot: SRTW2000 x 150: no stock at the moment, ETA 19/10/2026.
No agent notification (B3).

**(e) B3 - incoming with ETA, packing list toggle ON**

Dealer: Need 150 units of SRT6536.
Bot: SRT6536 x 150: no stock at the moment, ETA 02/11/2026.
[the shipment's packing list is attached to this reply]
No agent notification (B3).

**(f) B4 - no stock, no incoming**

Dealer: Any stock for SRT5674, need 150?
Bot: SRT5674 x 150: no stock and no incoming at the moment, please refer to your salesman.
Agent (WhatsApp): Stock ask - Hock Lee Trading (contact: Ah Seng) asked about SRT5674, qty 150, outcome: no stock no incoming. Asked at 24/09/2026 15:05. (wording proposed; sent as template, outside the 24h window)

**(g) Multi-product turn - one message, two lines**

Dealer: Need SRT5674 x 50 and CWCX604 x 300.
Bot: SRT5674 x 50: yes, we have stock, please refer to your salesman to proceed.
CWCX604 x 300: the quantity is more than what I can confirm here, please refer to your salesman.

## Slices

Every slice: migration, backend seam, frontend seam, tests (the `tester` agent writes them first, red, from this plan and the UAC; the `coder` makes them green), UAC ids. pytest on Postgres only (`tests/_pg_fixture.py`); CI has no data, every test seeds its own chain.

Tickets: S0 #1193, S1 #1194, S2 #1195, S3 #1196, S4 #1197, S5 and S6 share one issue, #1192.

### S0 - Rebase and merge #1118

**Migration:** none new. `dsv_0001_chatbot_stock_low_threshold` re-parented onto main's head with `./scripts/alembic-reparent.sh`; `alembic heads` shows one.
**Backend / frontend seam:** none beyond conflict resolution. `git fetch origin main`, rebase or merge main into `feat/chatbot-dealer-stock-verdict`, resolve, CI green, merge.
**Tests:** #1118's own suites stay green on the rebased head (`tests/chatbot`, `tests/test_stock_availability_block.py`, `tests/test_stock_verdict.py`, MCP `tests/`). No new test.
**UAC:** AC-SA001, AC-SA002.
**Gate:** no v2 slice starts until #1118 is on main (R1).

**Owner ruling, 24 Sep 2026:** PR #1118 is never merged (it carries an old requirement). S1 and S2 start now from `origin/main` rather than waiting on this gate; the gate above stands for S3 onward, whose branch logic still depends on #1118's quantity collection and availability read landing first.

### S1 - X and Y columns, permission, category and product forms

**Migration** `sa2_0001_xy_columns` (re-parented at PR time): `product_categories.chatbot_max_qty INTEGER NULL`, `product_categories.chatbot_eta_offset_days INTEGER NULL`, `products.chatbot_max_qty INTEGER NULL`, `products.chatbot_eta_offset_days INTEGER NULL`, CHECK `>= 0` on all four. No backfill: NULL is the shipped value and means 0 (R2: nothing answers until a category opts in). Inserts the four `_crud` slugs `master_data.chatbot_stock_limits.{view,add,edit,delete}` into `user_permissions` and grants `.view` + `.edit` to every role holding `master_data.products.edit` and to `admin`, integration roles excluded (the 522 shape).

**Permission:** `PERMISSION_REGISTRY.extend(_crud("master_data", "chatbot_stock_limits", "Chatbot Stock Limits"))` next to `product_categories` (R2: "from the _crud pattern"). Only `.view` (see the two values) and `.edit` (change them) are read by code; `.add` / `.delete` are the pattern's siblings and gate nothing (there is no row to add or delete).

**Resolution rule** (pure, `app/services/stock_ask_limits.py`, core): `effective(product, category) -> (max_qty: int, eta_offset_days: int)` = product value if not NULL, else the product's own category value if not NULL, else 0. No parent walk (R2).

**Backend seam:** the two fields on `ProductCategoryBase/Update/Response` and `ProductBase/Update/Response` (`app/schemas/product.py`), `>= 0` validated (422). The category PUT (`app/api/v1/master_data/categories.py`) and product PUT (`products.py`) raise `AppException(403)` when the body changes either field and the caller lacks `master_data.chatbot_stock_limits.edit`; the unchanged value passes so the ordinary form keeps saving. Responses always carry both fields: the values are not secret, and `.view` gates only whether the FE shows them (one rule, one place). Assert both fields in a response test (`response_model` drops undeclared fields).

**Frontend seam:** `CategoryForm.tsx` gains "Max quantity (assistant)" and "ETA offset (days)" number inputs, rendered when `useHasPermission("master_data.chatbot_stock_limits.view")`, disabled unless `.edit`. `ProductForm.tsx` Basic Information card gains the same two inputs beside the reorder fields, placeholder = the category value when the product value is empty. The product view renders the same card with the same fields in the same order (view = edit layout, R2); `-` when unset. No helper text.

**Dated note (24 Sep 2026, Phase 1 mock lane, measured against the code):** the category detail page's inline edit (`product-categories/[id]/page.tsx`), not `CategoryForm.tsx`'s edit branch, is the live "view = edit" surface for an existing category - `CategoriesList.tsx` never opens `CategoryForm` in edit mode, the tree row opens this page instead - so X/Y were added there too; without it S1 would only ever let someone set X/Y at category creation. Separately, `ProductForm.tsx`'s two fields landed on the Specifications tab beside Reorder Level/Reorder Quantity, not the Basic Information tab named above: the reorder fields actually live in Specifications per `product-schema.ts`'s own tab comment. Both placements stand for Phase 2.

**Tests (tester first):** `tests/test_stock_ask_limits.py`: `effective()` table (product set, category set, both, neither -> 0, zero is a value, parent category ignored). `tests/test_stock_ask_limits_api.py`: PUT category/product with `.edit` persists; without `.edit` and a changed value 403; without `.edit` and the unchanged value 200; negative 422; GET carries both fields; migration seeds the four slugs, sweeps `.view` + `.edit` onto a `master_data.products.edit` role and `admin`, not onto an `integration_*` role. Vitest: `CategoryForm` hides the fields without `.view`, disables without `.edit`; `ProductForm` shows the category placeholder.
**UAC:** AC-SA101 to AC-SA111.

### S2 - Contact toggles

**Migration** `sa2_0002_contact_toggles`: `respond_contacts.notify_salesman BOOLEAN NOT NULL DEFAULT false`, `respond_contacts.packing_list_allowed BOOLEAN NOT NULL DEFAULT false` (R7 names, R7 defaults).

**Backend seam:** `ContactChatbotUpdate` gains both as `bool | None` (absent = leave alone, the route's existing rule); `contact_to_response_dict` lists both; `turn_runtime._PROFILE_COLUMNS` selects both and `Profile` (`turn/state.py`) carries `notify_salesman: bool = False`, `packing_list_allowed: bool = False`, so S3/S4 read them off the profile the engine already loads.

**Frontend seam:** `ContactChatbotSection.tsx` gains two Switch rows, "Notify salesman" and "Packing list allowed"; `contactChatbotService.ts` + `useContactChatbot.ts` map and save them with the rest of the profile.

**Tests (tester first):** `tests/chatbot/test_stock_ask_contact_toggles.py`: PUT one toggle persists and returns it, the other untouched; GET lists both, default false; without `user_management.contacts.edit` 403; `load_profile` returns both (workspace row and NULL-workspace fallback), unknown contact false. Vitest: `ContactChatbotSection` renders both switches and saves the full profile.
**UAC:** AC-SA201 to AC-SA205.

### S3 - Four-branch verdict and the R5 ETA read

**Migration:** none (R11: `system_settings.chatbot_stock_low_threshold_pct`, added by #1118's `dsv_0001`, stays exactly as #1118 lands it; v2 simply never reads it, since B1 to B4 have no "running low" answer).

**Backend seam** (`StockService._apply_stock_visibility`, availability branch only; `detailed` / `compact` return earlier and are untouched, R10):
1. Keep: `_supply_scope`, the open SO read, `net_available` (R3, R4), `_resolve_ask`, the per-code merge, `needs_quantity` (so `StockQtyTask` keeps working unchanged, R1).
2. Delete: the `spo_allocations` read, the `purchase_order_lines` read, the threshold / lead-time read, the `verdict()` call, `stock_verdict.py`, `tests/test_stock_verdict.py`.
3. Add one read per page: X and Y via `stock_ask_limits.effective` for the page's products (products joined to their category, one query).
4. Add the R5 ETA read (one query per page, `app/services/incoming_stock_service.py` gains `earliest_packing_list_shipment(db, product_ids) -> {product_id: (shipment_id, estimated_arrival_date, attachment_id)}`): `inbound_shipments` join `inbound_shipment_lines` on `shipment_id`, `_not_draft_shipment_filter()`, `_still_incoming_filter()`, `inbound_shipments.attachment_id IS NOT NULL`, `inbound_shipments.estimated_arrival_date IS NOT NULL` (R10: a shipment without a date never qualifies), `line.product_id IN (...)`, ordered by `estimated_arrival_date ASC`, first per product (`DISTINCT ON (product_id)`). No warehouse filter (ANY location, R5). No `eta_delay_date`. Measured on the prod copy `sorento_ai_automation_0921` (24 Sep 2026): 0 of 275 `inbound_shipments` have a NULL `estimated_arrival_date` (119 `in_transit`, all dated), so this filter drops nothing observed in practice; it is there for correctness, not to fix a live gap.
5. Decision (pure, `app/services/stock_ask_branch.py`, replaces `stock_verdict.py`): `branch(q, x, available, shipment_date) -> "too_big" | "in_stock" | "incoming" | "no_incoming"`: `q > x` (x = 0 when unset, so every q >= 1 is `too_big`) -> `too_big`; `available >= q` -> `in_stock`; a shipment row exists -> `incoming`; else `no_incoming`. Because the R5 read requires `estimated_arrival_date NOT NULL` (R10), a shipment row that reaches `branch()` always carries a date: there is no undated `incoming` case, and "ETA to be confirmed" is not a real answer.
6. Entry shape (replaces `available` / `verdict` / `running_low` / `disclaimer`): `branch`, `cap_unset: bool` (X resolved from NULLs), `category_name` (for the B1 reason), `eta` (`dd/mm/yyyy` of `estimated_arrival_date + Y`, `incoming` only), `packing_list` (`_attachment_payload` of the shipment's attachment, `incoming` only AND only when the asking contact's `respond_contacts.packing_list_allowed` is true; the route already resolves that contact, so the raw GET never carries the file for a contact who may not have it). No quantity of ours on the wire.
7. Presenter `_availability_line` (MCP) renders R14's shape per entry, one line per product in asked order, every line starting with `<P> x <Q>` (R14: "for every answer, we must include product code", wording proposed, owner proof-reads it on the page):
   - `too_big`: "<P> x <Q>: the quantity is more than what I can confirm here, please refer to your salesman."
   - `in_stock`: "<P> x <Q>: yes, we have stock, please refer to your salesman to proceed."
   - `incoming`: "<P> x <Q>: no stock at the moment, ETA <dd/mm/yyyy>."
   - `no_incoming`: "<P> x <Q>: no stock and no incoming at the moment, please refer to your salesman."
   `<P>` is `product_code` (fallback `product_name`, never the id).
8. Engine: at the `tasks_after_reply` call site, when an entry is `incoming` with `packing_list` present, emit a `send_attachments` action with that file (the existing action shape). Nothing else changes in the engine in S3.

**Frontend seam:** none (R11: Settings > Chatbot keeps the threshold card exactly as #1118 landed it).

**Tests (tester first):** `tests/test_stock_ask_branch.py`: the UAC truth table. `tests/test_stock_availability_block.py` (rewritten where it pinned `verdict` / `disclaimer`, the old assertions deleted, not skipped): location scope (stock outside the policy set not counted); open SO subtracted; ETA picks the earliest `estimated_arrival_date` among shipments with a packing list; a shipment without `attachment_id` ignored even if earlier; draft shipment ignored; received line ignored; fully received quantity ignored; `eta_delay_date` ignored; a shipment into a warehouse outside the policy set still counts; `spo_allocations` and open PO lines produce no ETA; Y added across a month end; X unset -> `too_big` + `cap_unset`; product X overrides category X; `packing_list` present only with the toggle on; no digit of ours in any entry field; `detailed` and `compact` payloads byte-identical to before. MCP `tests/test_presenters_availability_lines.py` rewritten: four sentences, asked order, no UUID, no "running low", no "purchase". `tests/chatbot` engine test: B3 with toggle on emits `send_attachments`, off does not. The `demand_qty` / `stock_denied` suites untouched and green (R1).
**UAC:** AC-SA301 to AC-SA318.

### S3 round 9 - one open-question object for every question the bot asks (issue #1293)

**Design rule (owner question, 26 Sep ~14:15Z: "I'm also curious in your methodology ... not too much hard coding, hard routing"): the LLM parser READS, the code APPLIES.**

- Every place the bot asks the customer something is recorded as ONE open question object, built by `turn/question.py::open_question(pending, tasks)` from what the turn already stores (the pending pick or offer, else the stock task). No new state: the object is a view.
- Kinds: `pick_one` (a numbered list: the did-you-mean, the family which-one, a customer or product roster, a team or member list), `choose_brand` (a `brand_pick` roster), `confirm` (a yes/no: a one-option did-you-mean, a yes/no escalation offer), `quantities` (the stock quantity question, one line per product), `last_answer` (a stock check just answered, open to revision), `free` (a question with no options). `how_many_to_show` is declared in the contract but never written: no question of that kind exists (the no-paging, full-counts ruling), so nothing asks it; the first asker is the trigger to write it.
- The object carries its options with positions and codes, what is still owed (`pick`, `quantity`, `yes_no`, or the owed line positions) and a quantity already given. It is sent to the parser on the `Open question:` line with the last three exchanges.
- The parser returns ONE declared answer, `open_question_answer`: `mode` pick / yes / no / fill / all / done / cancel / null, `picked` (positions), `items` (per line or per picked option: position, code, qty), `qty_for_all`. Ordinals, codes, "both" / "all" / "none" and number words in English, Malay and Chinese are the parser's job; the contract states them.
- The apply layer acts on the object: a pick becomes the positions `decide()` already reads, a yes or no becomes `is_affirmative`, a quantity with a stock pick rides on the pick and is stamped onto the product it settles. A declared answer that does not fit the question (a position not offered, a quantity of zero) is not applied, and the shape rules run as the fallback, only then.
- No keyword routing: no rule reads the customer's words. A phrasing that is not understood is a parser contract fix, never an `if "first" in message`.
- Headers never repeat a code the resolver did not recognise: the did-you-mean re-ask with a quantity reads "Which one do you need 2 of?", not "STWC2867 x 2: which one?"; once a pick resolves, the recognised code leads ("SRTWC286-SH x 2: ...").
- The prompt contract is published as a new unlabelled `chatbot_semantic_parser` version by migration `sa2_r9_open_question`; moving `production` is the owner's click.

**UAC:** AC-SA342 to AC-SA347.

### S4 - Agent notification + integration_log

R13: yes, this is where the salesman notification lives.

**Migration:** none (use case registration is code; the template mapping is admin data).

**Backend seam:**
- `stock_ask_salesman` appended to `TEMPLATE_DEFAULT_USE_CASES` and to FE `USE_CASES` with a label.
- Engine, after the turn row is written (never before; the dealer's reply never waits on Respond), for each entry of the reply's `stock_availability` block with branch `too_big` / `in_stock` / `no_incoming`, when `Profile.notify_salesman` is true and the turn is live or a chat console turn (owner ruling 28 Sep 2026; no other dry run): enqueue `app/tasks/stock_ask_tasks.notify_salesman(...)` on the `respond_io` queue with the ask facts. `incoming` never enqueues (R6 B3).
- Task: resolve customer (`resolve_customer`) -> `customers.sales_agent_id` -> `sales_agents.contact_id` -> `respond_contacts.respond_io_id`. A missing link is a skip with reason `no_customer` / `no_sales_agent` / `agent_has_no_contact` / `agent_contact_has_no_respond_id`, a warning log, no raise (R8).
- Context vars (one template, R8): `outcome` (phrase: `in stock` / `too big` / `no stock no incoming` / `no cap set for <category>` when `cap_unset`), `customer_name`, `contact_name` (the dealer contact who asked), `product` (code - name), `quantity`, `asked_at` (dd/mm/yyyy HH:MM, Malaysia wall clock). `send_text_or_template(db, identifier=respond_io_id, text=<default wording>, use_case="stock_ask_salesman", context_vars=..., respond_contact_id=agent_contact.id)`: template outside the window, in-window text following the template wording (R8, the function's own behaviour).
- One `integration_log` row per attempt, success or failure, the `notification_tasks.py:178-231` shape: `integration_channel="respond_io"`, `direction="outbound"`, `external_reference=respond_io_id`, `request_payload` = what was sent. In S4 alone `business_table="chatbot_turns"`, `business_id=turn_id`; S5 moves it to the ask row.

**Frontend seam:** the use case label in the Set Default Template dialog only.

**Tests (tester first):** `tests/chatbot/test_stock_ask_notify.py` (`send_text_or_template` monkeypatched): toggle on + B1 / B2 / B4 enqueue one job each; B3 none; toggle off none; a chat console turn enqueues, every other dry run none; job only after the turn row is written; outcome phrase per branch and `no cap set for <category>` when `cap_unset`; each missing link a skip with its reason and no send; window open `sent_as == "text"`, closed with a mapped template `sent_as == "template"`, closed and unmapped `TemplateSendSkipped` -> failed log row; Respond 401 -> failed row with status and body; every attempt exactly one `integration_log` row. `tests/test_respond_template_use_cases.py` extended: `set_default` accepts `stock_ask_salesman`. Vitest: the dialog lists the label.
**Depends on:** #1177 merged.
**UAC:** AC-SA401 to AC-SA410.

### S5 - Asks table + CRM Asks tab

**Migration** `sa2_0004_stock_asks`:

```
stock_asks
  id                  uuid PK
  company_id          uuid FK companies.id            (CompanyScopedMixin, from the customer)
  customer_id         uuid NULL FK customers.id ON DELETE CASCADE
  contact_id          text FK respond_contacts.id ON DELETE SET NULL
  product_id          uuid FK products.id ON DELETE SET NULL
  product_code        varchar(100) NOT NULL               (snapshot)
  quantity            integer NOT NULL
  branch              varchar(20) NOT NULL CHECK in (too_big, in_stock, incoming, no_incoming)
  answer_summary      text NOT NULL                       (the line the dealer was sent, R14's product-and-quantity prefix included since it is the exact line, not a re-derivation)
  notified_agent      boolean NOT NULL DEFAULT false
  notify_skip_reason  varchar(80) NULL                    (why not, when not)
  state               varchar(10) NOT NULL DEFAULT 'open' CHECK in (open, done)
  note                text NULL
  created_at / updated_at
  index (customer_id, created_at desc)
```

Exactly R9's fields. `customer_id` nullable: an ask from a contact with no resolvable customer is still recorded (R8 "record the ask"); such a row shows on no customer tab and no portal page (nothing to hang it on) and is visible in the DB only; flagged in the UAC. No `sales_agent_id` snapshot (the portal scope reads the customer's current agent, R9). No opportunity column (R9: #1170).

**Backend seam:**
- Engine: at the same post-turn point as S4, one row per entry with a branch (all four), live turns only (**amended 1 Oct 2026, `PLAN-customer-asks-refer-only-1oct.md`:** only an entry whose line refers the dealer to their salesman, the presenter's `refers_to_salesman` flag; B3 `incoming` is answered but not a row); `notified_agent=false` + `notify_skip_reason` (`toggle_off` / `not_notified_branch` for B3) at write; the S4 task flips `notified_agent=true` on success or sets its skip / failure reason. `integration_log.business_table="stock_asks"`, `business_id=ask.id`.
- `GET /api/v1/order-management/customers/{id}/asks` (paged, newest first, `buildDataGridParams` contract, `list_query_registry` entry for column config; gated by `order_management.customers.view`) and `PATCH /api/v1/order-management/customers/{id}/asks/{ask_id}` `{state, note}` (gated by `order_management.customers.edit`, R9). Rows carry `contact_name`, `product_code`, never a bare id. Service `app/services/stock_ask_service.py` (core).

**Frontend seam:** `CustomerDetail.tsx` gains line tabs "Details" (the two existing cards, unchanged) and "Asks". Asks tab: DataGrid (`tableLayout: { width: 'fixed', columnsResizable: true }`, `columnResizeMode: 'onChange'`, explicit sizes, truncate + title): Asked at, Contact, Product, Qty, Branch (Badge), Answer, Notified (Badge + reason in `title`), State, Note. State is a required `SearchableSelect` (open / done; required, so not clearable) and Note an inline text input, editable when the user holds `order_management.customers.edit`, read-only values otherwise (view = edit layout). Empty state names what fills it. Service `services/stockAskService.ts` -> hooks `useCustomerAsksQuery` / `useUpdateAskMutation` (invalidate + toast, `extractApiError`).

**Tests (tester first):** `tests/chatbot/test_stock_ask_record.py`: one row per product per live turn with the right branch, quantity, answer summary; a chat console turn writes them with `source = console`, every other dry run none; B3 `notify_skip_reason = not_notified_branch`; toggle off `toggle_off`; S4 success flips `notified_agent`; skip reason written on each missing link; unresolved customer -> row with NULL `customer_id`; integration_log references the ask. `tests/test_stock_asks_api.py`: GET newest first, names not ids, company scope (another company's user sees none), 403 without `.view`; PATCH state + note persists, `done -> open` allowed, 403 without `.edit`, ask of another customer 404; `test_schema_uuid_id_principle.py` passes. Vitest: Asks tab renders grid + empty state; read-only without `.edit`; state change calls the mutation.
**UAC:** AC-SA501 to AC-SA512.

### S6 - Portal "Customer asks" page (last)

**Migration:** none.

**Backend seam:** extract the agent resolution at the top of `PriceTagRequestService.lookup_debtors_for_agent` (the ordered `SalesAgent.contact_id == contact_id` query + two-link warning) into `sales_agent_for_contact(db, contact_id) -> SalesAgent | None` and call it from both places (reuse, not a copy). New `app/api/v1/public/portal_customer_asks.py` mounted under `/portal` like `portal_price_tag`: `GET /api/v1/public/portal/customer-asks` (paged, `q` on customer / product) and `PATCH /api/v1/public/portal/customer-asks/{ask_id}` `{state, note}`, both on `get_portal_token`. Scope: asks whose `customers.sales_agent_id == sales_agent_for_contact(token.contact_id).id` (customers assigned to that agent only, R9; not the 24-month order debtors). No linked agent -> 403 `NOT_A_SALES_AGENT`; an ask outside the scope -> 404.

**Frontend seam:** `app/(auth)/portal/customer_asks/page.tsx` rendering `CustomerAsksList` in `app/(auth)/portal/components/`, the same `DataGrid` + `DataGridTable` list pattern as `PortalLanding`'s list view: Asked at, Customer, Contact, Product, Qty, Branch, Answer, Notified, State (editable), Note (editable). Portal nav entry "Customer asks" shown only when the contact is a linked agent (the GET's 403 hides it). Usable at 375px.

**Tests (tester first):** `tests/test_portal_customer_asks.py`: agent contact sees asks of customers assigned to their agent only; another agent's customer's ask not listed and PATCH 404; non-agent contact 403; PATCH state + note persists; `lookup_debtors_for_agent` suite still green after the extraction. Vitest: `CustomerAsksList` renders grid, empty state, edits state + note.
**UAC:** AC-SA601 to AC-SA607.

## Simplest thing, not built

- No customer stock location column (R3). No parent-category walk for X / Y (R2; trigger: a category whose X the owner wants inherited). No per-company X / Y.
- No `TaskKind` or task change: #1118's collection is reused as is (R1).
- No new use case per branch: one template, one outcome slot (R8). No digest, no retry beyond the existing `respond_io` log sweeper, no in-app notification.
- No follow-up states beyond open / done, no assignment, no SLA, no export, no archive; no opportunity link (R9, #1170).
- No new portal visibility form type: the page's gate is "this contact is a linked sales agent", the same resolution the debtor lookup uses.

## Risks and rebase points

- #1118 is +18k lines and 44 commits; S0 may take a round of conflict work in `engine.py` and `turn_runtime.py`. Line numbers above cite #1118's head and will move.
- #1177 not merged blocks S4 onwards (the agent link is settable only through it).
- Security review joins S3 (per-contact attachment release), S4 (outbound send) and S6 (portal scope).

## Files (expected)

Backend: `alembic/versions/sa2_0001_*.py`, `sa2_0002_*.py`, `sa2_0004_*.py` (no `sa2_0003`: R11 keeps the threshold column, so S3 needs no migration), `app/models/product.py`, `app/models/access.py`, `app/models/user.py`, `app/models/stock_ask.py` (new), `app/models/respond_template.py`, `app/schemas/product.py`, `app/schemas/stock_ask.py` (new), `app/rbac/permission_registry.py`, `app/api/v1/master_data/categories.py`, `app/api/v1/master_data/products.py`, `app/api/v1/user_management/contacts.py`, `app/api/v1/user_management/settings.py`, `app/api/v1/order_management/customers.py`, `app/api/v1/public/portal_customer_asks.py` (new), `app/api/v1/public/__init__.py`, `app/services/stock_ask_limits.py` (new), `app/services/stock_ask_branch.py` (new), `app/services/stock_verdict.py` (deleted), `app/services/inventory_service.py`, `app/services/incoming_stock_service.py`, `app/services/stock_ask_service.py` (new), `app/services/price_tag_request_service.py`, `app/services/contact_service.py`, `app/services/list_query_registry.py`, `app/services/chatbot/engine.py`, `app/services/chatbot/turn_runtime.py`, `app/services/chatbot/turn/state.py`, `app/tasks/stock_ask_tasks.py` (new).
MCP: `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.
Frontend: `product-categories/components/CategoryForm.tsx`, `products/components/ProductForm.tsx` (+ product view), `user-management/contacts/[id]/components/ContactChatbotSection.tsx` + service + hook, `order-management/customers/components/CustomerDetail.tsx` + new `CustomerAsksTab.tsx`, `services/stockAskService.ts`, `services/whatsappTemplateService.ts`, `app/(auth)/portal/customer_asks/page.tsx`, `app/(auth)/portal/components/CustomerAsksList.tsx`. `user-management/settings/chatbot/` untouched (R11: the threshold card stays).
