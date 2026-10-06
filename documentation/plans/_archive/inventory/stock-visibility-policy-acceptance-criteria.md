# UAC: Stock visibility policy

Plan: `PLAN-stock-visibility-policy.md`. Every criterion is testable; the test that proves it is
named in the right column and lands in Phase 2 (never deferred).

## Journey

**Actor 1: admin (CS lead).** Arrives at a contact's detail page from User Management > Contacts
because a user asked for the compact stock format. The page already knows the contact's access
types and companies. The Stock visibility section shows the policy the contact gets today and
where it comes from (Default / Access type / Contact override). One decision: pick a mode
(Detailed / Compact / Availability only). Optionally narrow the locations (or press "Dealer pool",
which fills BRW, MWH, DC1 from the warehouse `segment`). Save. Nothing else to configure; the
chatbot picks the policy up on the next message. Removing the override asks for confirmation and
the section falls back to the inherited policy. The same card, with the same two fields, sits on
each access type (to set the dealer default once) and in system settings (the global default).

**Actor 2: internal user on WhatsApp.** Types "check stock SRTBF11201-NEW". Gets either the
numbered per-location list (today) or, after the admin flipped them to Compact, the per-product
`Total / BRW / BRW-BB` block. Nothing to ask, nothing to choose.

**Actor 3: dealer on WhatsApp.** Types "do you have stock for SRTBF11201-NEW?". Bot: "How many
units do you need?". Dealer: "50". Bot: "Yes, we have stock." or "Sorry, we do not have enough
stock for that quantity." The dealer never sees a number and never sees a location.

**Told automatically:** nobody. A policy change is silent; it shows in the next reply. Audit
listeners record the policy row change like any other master-data edit.

## A. Policy resolution (backend, pytest `tests/test_stock_visibility_policy.py`)

| # | Given | When | Then | Test |
|---|---|---|---|---|
| A1 | Fresh DB after migration | resolve any contact | default row exists: `mode=detailed`, `warehouse_ids=NULL`, source `default` | `test_default_row_seeded_inert` |
| A2 | Contact override row `compact` AND access-type row `availability` for a type the contact holds | resolve | contact override wins (`compact`, source `contact`) | `test_contact_override_beats_access_type` |
| A3 | Contact holds `dealer` (availability, [BRW,MWH,DC1]) and `end_user` (detailed, NULL) | resolve | `availability`, warehouses = [BRW,MWH,DC1] (most restrictive mode, intersection of warehouses, NULL treated as "all") , source `access_type` | `test_multiple_access_types_most_restrictive` |
| A4 | No override, no access-type row | resolve | default row, source `default` | `test_falls_back_to_default` |
| A5 | `contact_id` given but not found (either id form, wrong `space_id`), OR given with NO `space_id` at all | `GET /inventory/stock/balance` | zero rows, `stock_visibility` absent (fail-closed, matches company scope today). `space_id` is mandatory alongside `contact_id`: request-entry company scope needs BOTH params, so `contact_id` alone would apply the policy over EVERY company's stock | `test_unresolvable_contact_fails_closed`, `test_contact_without_a_space_id_fails_closed` |
| A6 | `contact_id` accepted as `respond_contacts.id` OR `respond_io_id` | resolve | same policy either way, AND the same company scope: both resolvers share `field_access.resolve_contact_id`, so the two cannot name different contacts (an internal id used to resolve a policy over an empty scope) | `test_contact_id_both_forms`, `test_company_scope_resolves_the_contact_the_policy_resolved`, `test_company_scope_still_fails_closed_on_a_stranger` |
| A7 | Request has no `contact_id` (staff web UI) | `GET /inventory/stock/balance` | policy NOT applied even if the default row is `compact`: the same rows and the same values as before this change. The response gains four keys, all null (`stock_visibility`, `stock_summary`, `stock_availability`, `last_updated_at`) - `StockBalanceListResponse` declares them, and `response_model_exclude_none` is NOT used because it would drop legitimately-null keys on the rows | `test_staff_path_ignores_policy` |

## B. Enforcement shapes (backend, same file)

| # | Given | When | Then | Test |
|---|---|---|---|---|
| B1 | Policy `detailed`, `warehouse_ids=[BRW, BRW-BB]`; stock exists in BRW, BRW-BB, DC1 | list | rows for BRW and BRW-BB only; `stock_visibility={mode:'detailed', warehouse_codes:['BRW','BRW-BB'], source}` | `test_detailed_filters_warehouses` |
| B2 | Policy `detailed`, `warehouse_ids=NULL` | list | all active warehouses (today's behaviour byte-for-byte) | `test_detailed_null_is_all` |
| B3 | Policy `compact`, product P with BRW=500, BRW-BB=200, BRW-IB=300, DC1=999 (DC1 not allowed) | list | `data=[]`, `total=1` (the PRODUCT answered for - see B15), `stock_summary=[{product_code:P, total_on_hand:1000, locations:[{BRW,500},{BRW-BB,200},{BRW-IB,300}]}]`; DC1 absent; locations ordered by code | `test_compact_groups_and_sums_on_hand` |
| B4 | Policy `compact`, two products | list | two summary entries, one per product, each with its own total | `test_compact_multi_product_blocks` |
| B5 | `compact`, product has `quantity_reserved` > 0 | list | total uses `quantity_on_hand`, reserved ignored | `test_compact_uses_on_hand_not_available` |
| B6 | Policy `availability`, no `requested_qty` | list | `data=[]`, `stock_availability=[{product_code, needs_quantity:true, requested_qty:null, available:null}]`; **no key on the whole response body contains a quantity for that product** (assert by walking the JSON) | `test_availability_needs_quantity_no_leak` |
| B7 | `availability`, `requested_qty=50`, allowed on-hand sum = 60 | list | `available:true`, `needs_quantity:false`; still no quantity key anywhere | `test_availability_yes` |
| B8 | `availability`, `requested_qty=50`, allowed sum = 40, but a non-allowed warehouse holds 500 | list | `available:false` (non-allowed stock never counted) | `test_availability_no_ignores_disallowed_warehouses` |
| B9 | `availability`, `requested_qty=0` or negative | list | 200, and the value is read as NOT PROVIDED: `needs_quantity:true`, `requested_qty:null`, `available:null`. The number is parsed out of a sentence by the LLM, so a 0 is a parse artefact rather than a demand, and a 422 would lose the question ("how many units do you need?") along with the number | `test_requested_qty_below_one_is_read_as_no_quantity` |
| B10 | Any mode | list | `last_updated_at` present and equals the BULK_IMPORT ledger timestamp, same as today | `test_last_updated_carried_on_every_mode` |
| B11 | `response_model` for the list route | schema | declares `stock_visibility`, `stock_summary`, `stock_availability` (lesson: undeclared fields vanish) | `test_response_model_declares_blocks` |
| B12 | Company scope: contact belongs to company C1; stock in C2 warehouses | list, any mode | C2 stock never in rows, summaries or availability sums | `test_policy_composes_with_company_scope` |
| B14 | Policy applied, result empty (a data miss) | list | `availability` carries NO `alternatives` and NO `relaxed_axis`: the data-miss probe names OTHER products that do have stock, which is what the dealer mode exists to withhold. `detailed` and `compact` still get them (amended by WA-MSG-TRIM, owner 3 Oct 2026: every contact moved to compact and keeps the suggestions), and their has-stock gate counts ONLY the policy's warehouses - a suggestion judged on hidden stock is a promise the next question cannot keep | `test_availability_never_offers_alternatives`, `test_compact_offers_alternatives`, `test_compact_alternatives_only_count_stock_the_policy_allows`, `test_detailed_still_offers_alternatives`, `test_detailed_alternatives_only_count_stock_the_policy_allows` |
| B15 | `compact` / `availability` with NO product named ("what stock do you have?") | list | the summary blocks are PAGED over distinct products in `product_code` order using the request's `page`/`limit`, and `pagination.total` is the distinct product count (not 0). `empty` is False whenever a block carries an entry, or the MCP escalation hint staples "We don't have that information" onto a real answer | `test_compact_pages_over_products`, `test_availability_pages_over_products`, `test_summary_modes_are_not_empty_when_they_carry_an_answer`, `test_a_summary_with_no_block_at_all_is_still_empty`, MCP `test_a_summary_answer_is_not_treated_as_nothing_found` |
| B16 | Any mode | list | `stock_visibility.warehouse_codes` names only ACTIVE warehouses, and is OMITTED entirely under `availability` - the echo would name the exact locations that mode exists to keep out of the reply | `test_availability_block_never_names_a_location`, `test_warehouse_codes_name_only_active_locations` |
| B17 | Policy carries `hide_zero_locations = true` | list | `detailed`: rows with `quantity_on_hand == 0` are absent, and a NEGATIVE row is still there - it is a count that cannot be true, not "none left", and the reader is the one who can fix it. Every row filtering out takes the existing empty path. `compact`: the zero LOCATION LINES are gone, `total_on_hand` is unchanged, and a product whose every location is zero keeps its entry (`total_on_hand: 0`, `locations: []`) rather than vanishing. `availability` is unaffected - it has no line to withhold. The flag rides the winning policy row, and across several access types it is OR-ed (hiding is the restrictive reading, like the mode rank and the warehouse intersection). `stock_visibility.hide_zero_locations` echoes it in every mode. PUT round-trips it on all three tiers; a body omitting it reads as false | `test_detailed_hides_zero_rows_but_keeps_the_negative_ones`, `test_detailed_without_the_flag_still_shows_the_zero_rows`, `test_detailed_hide_zero_takes_the_existing_empty_path`, `test_compact_drops_the_zero_locations_and_keeps_the_total`, `test_compact_keeps_a_product_whose_every_location_is_zero`, `test_availability_ignores_hide_zero_locations`, `test_access_types_merge_hide_zero_with_or`, `test_a_contact_override_carries_its_own_hide_zero`, `test_hide_zero_locations_round_trips_through_the_put`, `test_a_put_that_omits_hide_zero_locations_reads_as_false`, `test_the_other_two_tiers_carry_the_flag_too`, `test_the_response_model_declares_hide_zero_locations` |
| B13 | `compact` / `availability`, a product named in `product_ids` has NO stock row in any allowed warehouse (out of stock, or its stock sits only in a location the policy hides) | list | the product STILL gets its block: `total_on_hand: 0` with `locations: []`, or `needs_quantity: true` / `available: false`. Silence is unreadable - "none left" and "I never found your product" arrive as the same empty reply. An id that names no product at all is still dropped | `test_compact_names_a_product_with_no_stock`, `test_availability_says_no_for_a_product_with_no_stock`, `test_availability_still_asks_for_a_product_with_no_stock` |

## C. CRUD API (backend)

| # | Criterion | Test |
|---|---|---|
| C1 | `PUT /inventory/stock-visibility/contacts/{id}` upserts `{mode, warehouse_ids}`; second PUT replaces `warehouse_ids` wholesale | `test_contact_policy_upsert` |
| C2 | `DELETE` removes the override; effective falls back to next tier | `test_contact_policy_delete_falls_back` |
| C3 | `PUT .../access-types/{code}` with unknown code -> 404; `mode` outside the three -> 422; unknown warehouse id -> 422 | `test_policy_validation` |
| C4 | `GET .../effective?contact_id&space_id` returns `{policy, source}`; usable without a staff JWT via integration API key | `test_effective_external` |
| C5 | Writes need `inventory.stock.edit`; reads `inventory.stock.view`; unauthenticated -> 401 | `test_policy_rbac` |
| C6 | Only one default row can exist (second insert fails on the partial unique) | `test_single_default_row` |
| C7 | A policy naming warehouses in TWO companies reads back whole under a single-company admin, and a PUT naming another company's warehouse validates. `stock_visibility_policies` is not company data; `warehouses` is scoped, so the scoped lookup showed half the list and the next wholesale Save deleted the rest | `test_a_policy_spanning_two_companies_reads_back_whole`, `test_a_warehouse_from_another_company_can_be_saved`, `test_stock_enforcement_stays_company_scoped` |
| C8 | A `warehouse_ids` entry that is not a UUID -> 422 with the same "Unknown warehouse" sentence as an unknown id (it reached Postgres as a uuid and 500'd); a PUT body OMITTING `warehouse_ids` -> 422 (a PUT replaces the row, so an omitted key silently widened the policy to every location); the contact-tier GET/PUT/DELETE accept `space_id` to disambiguate an ambiguous Respond.io id; a policy upsert and delete each write an `audit_logs` row | `test_a_malformed_warehouse_id_is_rejected_not_a_500`, `test_the_upsert_body_must_state_its_warehouses`, `test_contact_tier_routes_disambiguate_with_a_space_id`, `test_policy_writes_and_deletes_are_audited` |

## D. MCP (pytest in `sorento_crm_mcp/tests/test_presenters_stock.py`)

| # | Criterion | Test |
|---|---|---|
| D1 | `crm_inventory_stock_balance_list` catalog lists `requested_qty` and forwards it | `test_catalog_requested_qty` |
| D2 | Raw response with `stock_visibility.mode=detailed` -> `result_type=stock`, envelope identical to before this change (golden fixture) | `test_render_detailed_unchanged` |
| D3 | `compact` -> `result_type=stock_compact`; item per product, `title=product_code`, fields `Product Code` first (keyed `product_code`, as on the detailed row - n8n prints `fields`, never `title`), then `Total`, then locations in backend order, values plain ints | `test_render_compact` |
| D4 | `availability` needs_quantity -> `result_type=stock_availability`, intro "How many units do you need?", item `flags.needs_quantity=true`, no field with a numeric value | `test_render_availability_ask` |
| D5 | `availability` available true/false -> intro "Yes, we have stock." / "Sorry, we do not have enough stock for that quantity." | `test_render_availability_answer` |
| D6 | `stock_visibility` block passes through on every envelope | `test_envelope_passthrough` |
| D7 | `availability`, several products that disagree -> intro is the ask if ANY still needs a quantity, else "Here is the stock availability for the requested products." and the items carry their own flags (one yes/no would be a lie about one of them) | `test_render_availability_several_products_that_disagree`, `test_render_availability_ask_wins_over_a_mixed_answer` |
| D8 | The three policy blocks reach the presenter as the backend declared them: `available` survives `_STOCK_HIDDEN_FIELDS` (where it means quantity_available on a ROW) and `warehouse_code` survives the Sage relabel. Stock ROWS keep every sanitizer they have today | `test_sanitizer_keeps_the_availability_answer`, `test_sanitizer_keeps_the_compact_location_codes`, `test_sanitizer_still_relabels_the_detailed_rows` |
| D9 | `last_updated_at` on the summary modes is Malaysia time, like every row `updated_at` (the footer would otherwise read 8 hours early for exactly the contacts on the new formats) | `test_sanitizer_puts_last_updated_at_in_malaysia_time` |

## E. Frontend (vitest + agent-browser evidence)

| # | Criterion | Proof |
|---|---|---|
| E1 | Contact detail page shows "Stock visibility" section with effective mode, warehouses (as `CODE - name`, never UUID) and a source badge | vitest `StockVisibilitySection.test.tsx`; browser run |
| E2 | Mode select is `SearchableSelect`, server-searched warehouses are a `SearchableMultiSelect` whose chips clear individually. Mode itself is NOT clearable: a policy row always has one, and the three options are the whole vocabulary | vitest |
| E3 | "Dealer pool" preset fills the `segment=dealer` warehouses | vitest |
| E4 | Save calls PUT, invalidates, toasts; error path uses `extractApiError` message | vitest |
| E5 | Remove override opens `ConfirmDeleteDialog` (never `confirm()`), then DELETE, then falls back to inherited policy in the UI | vitest |
| E6 | Same section (one component, `scope` prop) on the access-type page - via the row action dialog - and on the Settings > Stock Visibility tab; usable at 375px and 1280px, nothing clipped | browser run at both widths |
| E8 | Locations has three readings and the card keeps them apart: a list; `null` (placeholder "All locations") saved as `null`; `[]` (placeholder "No locations") saved as `[]`. Removing the last chip yields `[]` - the picker can only hand back a list - and the "All locations" button is the way back to `null`. A "Dealer pool" preset that returns NO warehouses toasts an error and leaves the selection untouched, because writing `[]` there would mean "no stock at all" | vitest |
| E7 | Navigation via sidebar from `/`, no deep URL | browser evidence |
| E9 | A "Hide zero-quantity locations" switch sits under Locations on all three tiers. It renders the EFFECTIVE policy's value (inherited included), toggling it makes the card dirty, and it rides the same wholesale Save - sent as `hide_zero_locations` on every PUT, `false` included, because a PUT replaces the row. After the write the switch shows the STORED value, not the clicked one. No explanatory copy beside it | vitest `StockVisibilitySection.test.tsx` (E9 block) + `stockVisibilityService.test.ts`; browser run |

## F. n8n (live-envelope harness + a real chat, evidence attached to the n8n PR)

| # | Criterion |
|---|---|
| F1 | `entity-ids-transformer` forwards `demand_qty` as `requested_qty`; absent -> not sent |
| F2 | `compact` reply reads exactly: product code line, `Total: N`, one `CODE: N` line per location, blank line between products, existing `_Data last updated_` footer |
| F3 | Dealer contact, "do you have stock for X" -> "How many units do you need?"; `session_vars.pending.intent == check_stock` written |
| F4 | Next message "50" -> yes/no reply, `pending` cleared, no quantity anywhere in the outbound text |
| F5 | Contact with `detailed` policy: outbound text byte-identical to before (regression via harness capture) |

## G. Roll-out gates

| # | Criterion |
|---|---|
| G1 | Deploy changes no outbound chatbot text for any contact (F5 on three live contacts) |
| G2 | One contact override flipped to `compact` changes only that contact |
| G3 | Default flipped to `compact` + overrides deleted = every non-dealer contact compact, staff web grid unchanged |
