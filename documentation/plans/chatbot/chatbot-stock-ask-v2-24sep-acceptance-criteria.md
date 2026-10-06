# UAC - Chatbot stock ask v2: four-branch answer, X cap, Y ETA offset, contact toggles, salesman notification, asks record

Status: grilled 24 Sep 2026, ready for tickets. S4 (AC-SA401 to AC-SA409) and S5 + S6 (AC-SA501 to AC-SA512, AC-SA601 to AC-SA607) built 28 Sep 2026 in PR #1333; Console fix round 28 Sep 2026 (owner ruling "it should work using chat console also"): AC-SA401 and AC-SA501 rewritten so a chat console turn notifies and records (`stock_asks.source = console`, migration `sa2_0005_stock_ask_source`); AC-SA410 and AC-SA511 stand and are hand tested from the console. Fix round 2 28 Sep 2026: AC-SA411 (allowed-to-send gate) and AC-SA412 (hand test as another contact, salesperson = owner). Plan: `PLAN-chatbot-stock-ask-v2-24sep.md`. Issue #1168. Rulings R1 to R11 (quoted in the plan) are binding.
Numbering: AC-SA<slice><nn>, slices S0 to S6. Tags: [BE] pytest (Postgres), [FE] vitest, [E2E] recorded agent-browser or console evidence, [T] a test that exists only to pin a rule. The `tester` writes every [BE]/[FE] test red before the `coder` starts the slice.

## Journey

Actor: a dealer contact on WhatsApp whose stock visibility policy mode is "Availability only" (R1). The system knows the contact's policy locations (R3), the customer they act for, that customer's sales agent (#1177), each product's X and Y (S1), and the contact's two toggles (S2).

1. **Ask.** The dealer names one or more products. #1118's stock task collects a quantity per product (unchanged, R1).
   Owner ruling 26 Sep 2026 (R1 re-ruled after the owner's hand test on PR #1247, verbatim "for R1 - okay can"): R1's "keep #1118's quantity collection unchanged" is lifted; the collection is changed in PR #1247 by AC-SA319 to AC-SA326 below.
2. **Decide.** Per product with quantity Q: X and Y resolved (product, else own category, else 0); available = on hand in the policy locations minus open SO (R4); the earliest non-draft still-incoming shipment with a packing list, any location (R5).
3. **Answer.** One R6 sentence per product in the order named. No number of ours except Q and the ETA date.
4. **Attach.** B3 only, and only when "Packing list allowed" is on: the shipment's packing list is sent.
5. **Notify.** B1, B2, B4 only, and only when "Notify salesman" is on: the customer's sales agent gets one WhatsApp message (template outside the window, text inside), logged in `integration_log`.
6. **Record.** Every answered ask is a `stock_asks` row, state open.
7. **Work it.** The office sets state and note on the customer's Asks tab; the sales agent does the same on the portal's Customer asks page.
8. **Configure (once).** A holder of `master_data.chatbot_stock_limits.edit` sets X and Y on a category (and overrides on a product); a contacts editor flips the two toggles; an admin maps a template to `stock_ask_salesman`.

## S0 - Rebase and merge #1118

- **AC-SA001 [BE]** `feat/chatbot-dealer-stock-verdict` carries main, `alembic heads` shows one head with `dsv_0001` re-parented, and #1118's suites pass on the rebased head in CI.
- **AC-SA002 [BE]** #1118 is merged to main before any S1 to S6 commit lands on the v2 lane.

## S1 - X and Y columns, permission, forms

- **AC-SA101 [BE][T]** `effective()`: product X = 200, category X = 50 -> 200; product NULL, category 50 -> 50; both NULL -> 0. Same for Y. Zero set explicitly is 0 and wins over the category.
- **AC-SA102 [BE][T]** Product NULL, own category NULL, parent category X = 30 -> 0 (no parent walk, R2).
- **AC-SA103 [BE]** After the migration the four columns exist, nullable, and a negative value is rejected by the DB CHECK.
- **AC-SA104 [BE]** `user_permissions` holds `master_data.chatbot_stock_limits.{view,add,edit,delete}`; every role holding `master_data.products.edit` and `admin` hold `.view` and `.edit`; an `integration_*` role holds neither.
- **AC-SA105 [BE]** With `.edit`, PUT category with X = 200, Y = 7 -> 200 and the response carries both.
- **AC-SA106 [BE]** Without `.edit`, PUT category changing X -> 403 (`AppException`, message names the permission); carrying the unchanged X -> 200.
- **AC-SA107 [BE]** AC-SA105 and AC-SA106 hold for PUT product.
- **AC-SA108 [BE]** A negative X or Y at the API -> 422. GET category and GET product carry `chatbot_max_qty` and `chatbot_eta_offset_days` (asserted field by field).
- **AC-SA109 [FE]** `CategoryForm` shows "Max quantity (assistant)" and "ETA offset (days)" only with `.view`; the inputs are disabled without `.edit`. No helper text.
- **AC-SA110 [FE]** `ProductForm` Basic Information shows the two inputs beside the reorder fields, placeholder = the category value when empty; the product view shows the same fields in the same place and order, `-` when unset. (Nit, reviewer pass PR #1221 85c2e9e7: landed on the Specifications tab instead, beside Reorder Level / Reorder Quantity - see the plan's dated note, PLAN-chatbot-stock-ask-v2-24sep.md line 166, "the reorder fields actually live in Specifications per `product-schema.ts`'s own tab comment"; both placements stand for Phase 2.)
- **AC-SA111 [E2E]** Sidebar to Master Data > Product Categories, open a category, set X and Y, save, reopen: persisted; at 375px the modal reaches Save; the product form shows the category placeholder.

## S2 - Contact toggles

- **AC-SA201 [BE]** PUT `/contacts/{id}/chatbot` `{"notify_salesman": true}` persists and returns it; `packing_list_allowed` unchanged (absent = leave alone).
- **AC-SA202 [BE]** A migrated contact reads both false; GET contact lists both keys (dict builder).
- **AC-SA203 [BE]** Without `user_management.contacts.edit` the PUT is 403.
- **AC-SA204 [BE]** `load_profile` returns `notify_salesman` and `packing_list_allowed` for a workspace row and for the NULL-workspace fallback; an unknown contact yields false for both.
- **AC-SA205 [FE]** `ContactChatbotSection` renders "Notify salesman" and "Packing list allowed" switches; toggling one saves the whole profile with the others unchanged. **[E2E]** Sidebar to User Management > Contacts, open a contact, flip "Notify salesman", reload: still on.

## S3 - Four-branch verdict and the R5 ETA read

Truth table for `branch()` (x and y already resolved; unset = 0):

| # | Q | X | available | shipment (date) | Y | branch | told |
|---|---|---|---|---|---|---|---|
| 1 | 250 | 200 | 1000 | 2026-10-12 | 7 | too_big | - |
| 2 | 5 | 0 (unset) | 1000 | none | 0 | too_big | - (cap_unset) |
| 3 | 200 | 200 | 200 | none | 0 | in_stock | - |
| 4 | 150 | 200 | 149 | 2026-10-12 | 7 | incoming | 19/10/2026 |
| 5 | 150 | 200 | 149 | 2026-10-28 | 5 | incoming | 02/11/2026 |
| 6 | 150 | 200 | 0 | none | 7 | no_incoming | - |
| 7 | 150 | 200 | 0 | 2026-10-12 | 0 (unset) | incoming | 12/10/2026 |
| 8 | 10 | 100 | 6 (on hand; 13 on open SO, ignored) | none | 0 | in_stock | "Got stock, 6 available." (AVAIL-ONHAND-BASIS, the MWC7632-SH-P prod case) |
| 9 | 5 | 100 | 6 (on hand; 13 on open SO, ignored) | none | 0 | in_stock | "Got stock." |
| 10 | 5 | 100 | 0 (on hand; no open SO) | none | 0 | no_incoming | "No stock and no incoming." |

`available` is ON HAND in the contact's allowed locations (AVAIL-ONHAND-BASIS, owner ruling 6 Oct 2026: "our basis should be the on hand, without considering the outstanding"). It is never negative: open sales orders are not subtracted, so the old row 8 ("-3, SO over on hand -> no_incoming") no longer exists. Wording unchanged.

- **AC-SA301 [BE][T]** `branch()` returns every row above.
- **AC-SA302 [BE]** Available counts only the contact's policy locations: 0 in the policy set and 500 elsewhere, Q = 10, X = 100 -> not `in_stock`.
- **AC-SA303 [BE]** Open sales order lines are IGNORED (re-ruled 6 Oct 2026, AVAIL-ONHAND-BASIS; was "subtracted"): on hand 100, open SO 60, Q = 50 -> `in_stock` with no count; Q = 150 (X = 200) -> `in_stock`, "100 available". On hand 6, open SO 13, Q = 10 -> `in_stock`, "6 available"; Q = 5 -> `in_stock`. The staff stock summary's O/S and sellable are unchanged (`tests/test_avail_onhand_basis.py`).
- **AC-SA304 [BE]** ETA shipment: of two qualifying shipments (non-draft, line not received, remaining > 0, `attachment_id` set) with `estimated_arrival_date` 2026-11-01 and 2026-10-12, the 2026-10-12 one is used.
- **AC-SA305 [BE]** An earlier shipment WITHOUT `attachment_id` is ignored; a draft shipment is ignored; a line with `line_status = received` is ignored; a line with `quantity_received = quantity_shipped` is ignored. With no other shipment the branch is `no_incoming`.
- **AC-SA306 [BE]** `eta_delay_date` is never read: a shipment with `estimated_arrival_date` 2026-10-12 and `eta_delay_date` 2026-10-20, Y = 0 -> told 12/10/2026.
- **AC-SA307 [BE]** A qualifying shipment whose line is allocated to a warehouse OUTSIDE the policy set still yields `incoming` (ANY location, R5).
- **AC-SA308 [BE]** Open `spo_allocations` and open purchase order lines alone produce `no_incoming` (never a source, R5).
- **AC-SA309 [BE]** A shipment lacking `estimated_arrival_date` never qualifies for R5; with no other qualifying shipment the answer is B4 (lavish review R10, 24 Sep 2026).
- **AC-SA310 [BE]** Entry shape: `branch`, `cap_unset`, `category_name`, `eta` (incoming only, dd/mm/yyyy), `packing_list` (see AC-SA311); `verdict`, `running_low`, `disclaimer`, `available` are gone; `needs_quantity` and `requested_qty` unchanged so `StockQtyTask` still opens, fills and closes (#1118 task suites green).
- **AC-SA311 [BE]** `packing_list` (`filename`, `file_path`, `mime_type`) is present on an `incoming` entry only when the asking contact's `packing_list_allowed` is true; absent otherwise and on every other branch.
- **AC-SA312 [BE][T]** No entry field and no presenter line carries a quantity of ours: a regex guard over the rendered reply finds no digits except Q and the date.
- **AC-SA313 [BE]** MCP presenter renders, per entry in asked order, every line starting `<code> x <Q>:` (lavish review R14, wording proposed): B1 "<code> x <Q>: the quantity is more than what I can confirm here, please refer to your salesman."; B2 "<code> x <Q>: yes, we have stock, please refer to your salesman to proceed."; B3 "<code> x <Q>: no stock at the moment, ETA <dd/mm/yyyy>."; B4 "<code> x <Q>: no stock and no incoming at the moment, please refer to your salesman." No "running low", no "purchase", no UUID.
- **AC-SA314 [BE]** Engine: B3 with `packing_list` present emits one `send_attachments` action with that file; without it, no attachment action.
- **AC-SA315 [BE]** `detailed` and `compact` payloads are unchanged (R10); the `stock_denied` / `demand_qty` suites on `chatbot_stock_denial_enabled` pass unchanged (R1).
- **AC-SA316 [BE]** `stock_verdict.py` and `tests/test_stock_verdict.py` are gone; nothing imports `stock_verdict`.
- **AC-SA317 [BE]** Owner ruling 25 Sep 2026: the threshold column is dropped, #1118 never merges - it superseded lavish review R11 (24 Sep 2026), which had kept the column and card unchanged from #1118. The migration, model column, settings dict entries, schema field and the test that only pinned it are removed from this PR; there is no reader and no card. **[FE]** N/A - no card is added.
- **AC-SA318 [E2E]** Console check per `documentation/agents/chatbot-verification.md` against an "Availability only" dealer contact: one turn per branch (B1 via Q > X, B1 via unset X, B2, B3 with toggle on and off, B4); replies quoted in `documentation/plans/chatbot/evidence/`.

### Owner hand test 26 Sep 2026 (PR #1247 fix round 3)

Owner ruling 26 Sep: R1 re-ruled ("for R1 - okay can"): slices 2 to 6 of the scout's hand-test comment are built in PR #1247.
Owner ruling 26 Sep: F1 (verbatim) "we should offer the ELP3756 and yes, yes should still go to warehouse, oh wait, btw dealer ask cannot have escalation, cannot have direct escalation to warehouse, their contact point is sales person". Reading implemented: a one-candidate did-you-mean is offered as a pick carrying the typed quantity; "yes" or the code answers that product with it; "no" refers the dealer to their salesman; a dealer (availability-only contact) is never offered or given an escalation anywhere in the stock ask. Staff contacts keep today's behaviour. This is an owner exception to AC-1691's two-option minimum, for the dealer's stock did-you-mean only.
Owner ruling 26 Sep: F2 "okay": one bare number after a question about several products (a real multi-product ask, not a family) applies to each of them.

- **AC-SA319 [BE]** Every availability reply, the quantity question included, carries no `_Data last updated_` line; compact and detailed replies keep it (R10). (T1, T3, T8, T13, T16)
- **AC-SA320 [BE]** One typed token that places a product family with no exact code opens no task: the reply is "<TOKEN> matches N products. Which one?" (or "<TOKEN> x Q: which one?") then one code per line, at most 10 then "and N others, reply with the full code."; a `product_pick` carrying Q is the open question; a bare number under it re-asks it carrying the number; the code or position answered is fetched with Q and the pick is spent. A typed token that is an exact code keeps only that product. A one-product question reads "How many units of <code>?". (T1 to T6)
- **AC-SA321 [BE]** A resume naming some of an open task's products narrows the task to them. (T5)
- **AC-SA322 [BE]** An open task never claims a turn naming a product it does not hold; the ordinary fetch runs for every product named, each with its own quantity. (T14)
- **AC-SA323 [BE]** An answered stock check is kept as status `answered` until the next new ask; the parser block states "Last answered: <code> x <Q>."; a bare number over a one-product answered check re-fetches it at the new quantity; `STOCK_TASK_ADDENDUM` teaches "how about N" as entities [], demand_qty N, correction true; `_normalise_demand_qty` drops a not-confident entity whose raw is only the digits of demand_qty. (T7, T8, T12, T13)
- **AC-SA324 [BE]** A retry naming exactly one product with no quantity, over a focus still holding a missed product row with a quantity, carries that quantity. (T15, T16)
- **AC-SA325 [BE]** F1: a dealer's did-you-mean is "Couldn't find <TYPED>. Did you mean <CODE>?" (or a list) with a `product_pick` carrying the typed quantity; "yes" or the code answers it with that quantity; "no" replies "Please refer to your salesman." with nothing open; any other escalation offer in a dealer's stock reply becomes "Please refer to your salesman." with no escalation pending. A detailed or compact contact is unaffected.
- **AC-SA326 [BE]** F2: a bare number over an open task with several products still owed, not a family, fills each and fetches them all at that quantity.

### Owner hand test of round 3, 26 Sep 2026 (:3087 console, PR #1247 fix round 4)

- **AC-SA327 [BE]** A which-one pick list (the family pick and a dealer's multi-code did-you-mean) prints one numbered code per line, "1. <CODE>", the format every other picker prints; "and N others, reply with the full code." is unchanged.
- **AC-SA328 [BE]** Once the which-one pick is spent and the stock check is about one product (still asked or just answered), a bare number is that product's quantity: after an answered quantity it re-fetches that product at the new quantity and answers straight away ("2" after SRTWC286-SH x 10 -> SRTWC286-SH x 2). It is never a pick from the old list and never a re-ask, whether the parser emits it as demand_qty or as a lone reference position.
- ~~**AC-SA329 [BE]**~~ Superseded by AC-SA330 (owner ruling 26 Sep ~08:25Z). A real new pick still works after an answered quantity: a typed code is a new stock ask for that code, and "no, <position>" (a lone position with is_affirmative false) picks that position off the list the product was picked from, carrying the quantity already given.

### Owner ruling 26 Sep ~08:25Z, "make the picker not sticky" (PR #1247 fix round 5)

- **AC-SA330 [BE]** Once one product is picked off a stock which-one list, the list is closed and forgotten: no open question, nothing kept on the stock task, nothing offered to the parser. A later bare number (also "no, 2") revises that product's quantity and never picks from the old list. A typed code or a new "check stock <code>" starts a new ask, and a new family ask prints a new numbered pick. The owner's five turns followed by "1, 10, 2, 3" answer SRTWC286-SH x 10, 2, 3, 1, 10, 2, 3.
- **AC-SA331 [BE]** "all" over a stock which-one list (every position, or `broaden_axis` "all") opens one quantity question for every product on it, and nothing stays open.
- **AC-SA332 [BE]** A stock question about two or more products is point form: "How many units for each?" then one "N. CODE - " line per product in a fixed order, a noted quantity filled in after its " - ". No em or en dash. One product still asks "How many units of CODE?".
- **AC-SA333 [BE]** Over that question, one number (the parser's `demand_qty` or a lone position) applies to every product still owed; numbered lines ("1. 10, 2. 5", "1) 10", "1. CODE - 10") fill those lines and re-ask the rest; "CODE 10" from the parser fills that line. The owner's transcript (check stock srtwc286, all, 1. 10, 2. 5, 10) answers all ten products.
- **AC-SA334 [BE]** Every chatbot model call (parser, recall re-parse, clarifier) goes through `chatbot/llm_call.py`. A 429 is retried with a growing backoff that honours the server's "try again in Ns" (at most 15 s a wait, 30 s in all, no wait on `insufficient_quota`). If it never clears the dealer reads "Sorry, I am busy right now, please send that again in a minute.", never the provider's text; the turn is `failed` with the real reason on the row.
- **AC-SA335 [BE]** (SUPERSEDED by AC-SA336, owner correction 26 Sep ~08:33Z) Small talk ("tia", "thanks", "tq", "terima kasih", "ok thanks") is answered "You're welcome." and an acknowledgement ("ok", "noted") "Noted.", with no parser and no clarifier call. An acknowledgement under an open question is left to the parser (it may be a yes). A quantity for the open stock question (one number, or numbered lines) needs no parser call either, and never under an open which-one list.
- **AC-SA336 [BE]** Every message goes through the parser: no small-talk reply and no quantity read from the message's shape without it. "tia" (a typo of "tiga") under "How many units of SRTWC286-SH?" is sent to the parser, and its reading (quantity 3) is the quantity the stock check is fetched at; the turn never answers "You're welcome." by rule. Numbered lines ("1. 10, 2. 5") fill those lines from the parser's reading: a product code, the line number kept as the entity's text, or the line numbers as positions beside the quantities. A line number off the list fills nothing.
- **AC-SA337 [BE]** (PR #1247 round 8) The parser reads the open stock question as a structured object on its own line, `Open question: {"kind", "items": [{position, code, qty}], "owed"}` (kind `stock_quantities` while asked, `last_answer` once answered), and the last three completed exchanges, oldest first, the newest reply not repeated. It answers in the declared `open_question_answer` object `{mode: fill | all | done | cancel | null, items: [{position, code, qty}], qty_for_all}`, which the apply layer reads before any shape rule; the shape rules run only when the object is absent or mode null.
- **AC-SA338 [BE]** Semantics of the answer: numbers one per line are positions in order and fill those lines, the rest re-asked; the list pasted back with blanks fills the filled lines and answers now, blanks skipped (even a line an earlier turn had noted) and named not checked; "that's it" / "done" / "itu saja" answers what is filled; "N for all of them" gives every line of the list N, over an open or a just-answered check; cancel drops the question. An item that places on no line, or two on one line, leaves the object unapplied.
- **AC-SA339 [BE]** A bare number after a check answered for two or more products never fetches and never reopens a bigger list: it asks "Is N for all K products, or for one of them?" over the same numbered products, only when the message names nothing else and no other domain. The asked number rides on the check for the one next turn: "all" gives every product that number, a line number ("2") gives that line that number, never a new question. A one-product check keeps AC-SA330. A parked check is not offered to the parser and is not answered by a sign-off.
- **AC-SA340 [BE]** Every product the dealer named with a quantity is answered: a code is never dropped as a family sibling of another code the same message named (SRTWC286-SH-150 beside SRTWC286-SH). The owner's 26 Sep 10:11Z to 10:14Z session replays with every row as ruled.
- **AC-SA341 [BE]** The n8n JS literal after "Companies OFFERED" is cut from the parser prompt. "READING THE CURRENT MESSAGE IN CONTEXT" stays until a live-parser replay shows the corpus green without it.
- **AC-SA342 [BE]** (PR #1247 round 9, issue #1293) Every question the bot asks reaches the parser as one `Open question:` object with a kind (`pick_one`, `choose_brand`, `confirm`, `quantities`, `last_answer`, `free`), its options with positions and codes, and what is still owed. An open pick or offer is the object; the stock task is the object only when nothing else is open.
- **AC-SA343 [BE]** The parser answers in one declared `open_question_answer` (mode pick / yes / no / fill / all / done / cancel / null, `picked`, `items`, `qty_for_all`), strict-schema safe and tolerated absent on a recorded emission. The apply layer applies it first; a declared answer that does not fit the question is not applied and the shape rules run as the fallback.
- **AC-SA344 [BE]** Under a did-you-mean or family pick, a message that picks AND states a quantity ("the first one, I need 2", "1, I need 2", "yang pertama, 2 unit", "第一个, 要两个") answers the picked product at that quantity in the same turn. A pick with no quantity asks "How many units of CODE?". "both", "all", "1 and 3" pick every named option; "none of them" refers the dealer to their salesman.
- **AC-SA345 [BE]** Without the object (a recorded emission, mode null), a position on the pick beside a stated quantity is still the pick plus the quantity; a lone number stays the quantity (round 5).
- **AC-SA346 [BE]** A reply header never repeats a code the resolver did not recognise: the did-you-mean re-ask with a quantity reads "Which one do you need N of?"; once the pick resolves the recognised code leads.
- **AC-SA347 [BE]** The owner's 26 Sep console sessions (07:18Z, 08:18Z, 10:11Z, 14:06Z) replay through `engine.run_turn` with the parser stubbed as it should answer, and ten natural phrasings per question kind (English, Malay, Chinese ordinals and numbers) are answered as ruled.

## S4 - Agent notification + integration_log

- **AC-SA401 [BE]** `notify_salesman` on: B1, B2, B4 each enqueue exactly one `notify_salesman` job on `respond_io`; B3 enqueues none; toggle off enqueues none. A live turn and a CHAT CONSOLE turn enqueue the same real job (owner ruling 28 Sep 2026, "for stock ask, yeah i know chat console is dry run, but it should work using chat console also"); every other dry run (`is_test`, `test_run_id`, the Prompts screen's "Run a turn", which is `ingress: console` without the marker) enqueues none. A chat console turn is `engine._chat_console(envelope)`: `Envelope.dry_run` AND `ingress == "console"` AND the `console_origin` envelope extra (not a declared field, so the pinned external contract is unchanged), the last set only by `console_service`'s two envelope builders. The console turn's own reply stays a dry run: every action it hands back carries `dry_run: true` and nothing sends it to the dealer's WhatsApp.
- **AC-SA402 [BE]** The job is enqueued only after the turn row is written: a turn that fails before the write enqueues nothing.
- **AC-SA403 [BE][T]** Outcome phrase: B2 `in stock`, B1 with X set `too big`, B1 with `cap_unset` `no cap set for <category name>`, B4 `no stock no incoming`. Context vars carry `customer_name`, `contact_name`, `product` (code - name), `quantity`, `asked_at`.
- **AC-SA404 [BE]** Recipient chain, one test per missing link (no customer, customer without `sales_agent_id`, agent without `contact_id`, agent contact without `respond_io_id`): no send, warning log, skip reason recorded, no raise.
- **AC-SA405 [BE]** Window open: `send_text_or_template` called with `use_case="stock_ask_salesman"`, `sent_as == "text"`; one `integration_log` row, `respond_io` / outbound / success, payload = the text.
- **AC-SA406 [BE]** Window closed + mapped template: `sent_as == "template"`, log row carries the template payload.
- **AC-SA407 [BE]** Window closed + no mapped template: `TemplateSendSkipped`, log row failed with the message.
- **AC-SA408 [BE]** Respond 401: log row failed with status and body; the dealer's reply is unaffected.
- **AC-SA409 [BE]** `stock_ask_salesman` is in `TEMPLATE_DEFAULT_USE_CASES` and accepted by `set_default`. **[FE]** The Set Default Template dialog lists it with its label.
- **AC-SA410 [E2E]** With a template mapped, a B2 console turn on a live contact with a linked agent delivers one message to the agent's Respond contact; the `integration_log` row is visible in the Respond outbox.
- **AC-SA411 [BE]** Allowed-to-send gate (owner note on PR #1333, 28 Sep 2026, verbatim "it needs to respect the allowed to send boolean in respond contact"): the `notify_salesman` job is enqueued only when the salesperson's Respond contact has `respond_contacts.outbound_enabled` true, checked with the send path's own `assert_outbound_enabled`, on live and chat console turns alike. When it is false no job is enqueued, the `stock_asks` rows are still written (`notified_agent` false, `notify_skip_reason = contact_not_allowed_to_send`, so the Asks tab still shows them), and each B1 / B2 / B4 row gets one `integration_log` row, `status = skipped`, `error_message = "not sent: contact not allowed to send"`, `external_reference` the salesperson's Respond id.
- **AC-SA412 [E2E] (closing hand test, fix round 2)** From the chat console, as ANOTHER dealer contact (not the owner's own), whose customer's sales agent is set to the owner (Jayson), whose Respond contact has allowed to send ON: a B2 ask delivers one WhatsApp line to the owner's phone and the Asks tab shows the row notified. Flip the owner's contact to allowed to send OFF, ask again: no WhatsApp, the Asks tab shows the new row not notified, and the Respond outbox shows the skipped line "not sent: contact not allowed to send".

## S5 - Asks table + CRM Asks tab

- **AC-SA501 [BE]** A live turn answering N products writes N `stock_asks` rows with `customer_id`, `contact_id`, `product_id`, `product_code`, `quantity`, `branch`, `answer_summary` (the exact line sent), `state = open`; a chat console turn writes the same rows with `source = console` (a live turn `source = live`), shown as a "Console" badge on the CRM Asks tab and on the portal Customer asks page (owner ruling 28 Sep 2026); every other dry run writes none.
- **AC-SA502 [BE]** At write: B3 `notified_agent = false`, `notify_skip_reason = not_notified_branch`; toggle off `toggle_off`. The S4 task sets `notified_agent = true` on success, or the skip / failure reason.
- **AC-SA503 [BE]** A contact with no resolvable customer still gets a row with `customer_id` NULL and reason `no_customer`.
- **AC-SA504 [BE]** The S4 `integration_log` row has `business_table = stock_asks`, `business_id = ask.id`.
- **AC-SA505 [BE]** GET `/order-management/customers/{id}/asks`: newest first, paged, `contact_name` and `product_code`, no bare ids; 403 without `order_management.customers.view`; a user scoped to another company sees none.
- **AC-SA506 [BE]** PATCH `/order-management/customers/{id}/asks/{ask_id}` `{state: done, note}` persists; `done -> open` allowed; 403 without `order_management.customers.edit`; ask of another customer 404; state outside open / done 422.
- **AC-SA507 [BE][T]** `test_schema_uuid_id_principle.py` passes with no exemption; the `list_query_registry` entry serializes a row.
- **AC-SA508 [FE]** `CustomerDetail` has line tabs "Details" (the existing two cards, unchanged) and "Asks".
- **AC-SA509 [FE]** Asks tab: DataGrid fixed layout, resizable, explicit sizes, truncate + title; columns Asked at, Contact, Product, Qty, Branch (Badge), Answer, Notified (Badge, reason in title), State, Note; empty state when no rows.
- **AC-SA510 [FE]** With `.edit`, State (required `SearchableSelect`) and Note are editable in place and save through the mutation hook (invalidate + toast); without `.edit` the same cells render read-only values.
- **AC-SA511 [E2E]** Sidebar to Order Management > Customers, open the console-check customer, Asks tab: the S3 console turns appear with the right branches; set one to done with a note, reload: persisted. 375px and 1280px: no page clipping.
- **AC-SA512 [BE]** Deleting a customer removes its asks (CASCADE).

## S6 - Portal "Customer asks" page

- **AC-SA601 [BE]** `sales_agent_for_contact` is the single agent resolution used by both `lookup_debtors_for_agent` and the new routes; the existing debtor lookup tests pass unchanged.
- **AC-SA602 [BE]** GET `/public/portal/customer-asks` as an agent's linked contact lists asks whose customer's `sales_agent_id` is that agent, newest first, paged; asks of other agents' customers and of customer-less rows are absent.
- **AC-SA603 [BE]** A portal contact with no linked agent gets 403 `NOT_A_SALES_AGENT` on GET and PATCH.
- **AC-SA604 [BE]** PATCH `/public/portal/customer-asks/{ask_id}` `{state, note}` persists for an in-scope ask; an out-of-scope ask is 404; a bad state is 422.
- **AC-SA605 [FE]** `CustomerAsksList` renders the portal DataGrid (PortalLanding list pattern) with Asked at, Customer, Contact, Product, Qty, Branch, Answer, Notified, State, Note; empty state; State and Note editable in place.
- **AC-SA606 [FE]** The portal nav shows "Customer asks" only for a linked agent contact.
  Fix round 5 (owner, 29 Sep): it is one kind in the landing selector, not a separate card,
  and it is also a per-contact switch (Contact page, Portal forms, default off); a linked
  agent whose switch is off is not offered it and the API answers 403 FORM_TYPE_NOT_VISIBLE.
- **AC-SA607 [E2E]** Log into the portal as the agent's contact, open Customer asks, mark the S5 row done: the CRM Asks tab shows the same state and note; 375px usable.

## Definition of Done for the lane

- Every AC green, or re-ruled by the owner in this file.
- All three new migrations (`sa2_0001`, `sa2_0002`, `sa2_0004`; no `sa2_0003`, R11) chain onto main's single head (`./scripts/alembic-reparent.sh`), `alembic heads` shows one.
- New columns on every manual dict builder (AC-SA108, AC-SA202); dropped column off both settings builders (AC-SA317).
- Reviewer + security-reviewer (RBAC, outbound send, per-contact attachment release, portal scope) + browser pass at 375px and 1280px, once per lane.
- PR body names the track (full), this file and the plan.
