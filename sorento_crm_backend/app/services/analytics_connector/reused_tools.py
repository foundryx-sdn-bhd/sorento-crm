"""The 34 reused read tools the connector manifest offers (CLAUDE-CONNECTOR card 4.2).

GENERATED from `sorento_crm_mcp` (catalog + merged module catalogs), then hand-edited where dropping
params left broken sentences (incoming-stock list, the two attachment tools): name, description, method and
params as the n8n catalog has them (dashes become hyphens); `path` is the backend's REAL route path
(trailing slash included). The chatbot's contact-scoping params and the signed-URL switch (presigned
URLs are credentials) are dropped, with every description sentence that names them. The backend image
carries no `sorento_crm_mcp`, so this is
a static copy; `tests/analytics_connector/test_manifest.py` cross-checks it against the catalog.
"""
from __future__ import annotations

REUSED_TOOLS: tuple[dict, ...] = (
    {
        "name": 'crm_certificates_list',
        "description": (
            'List product certificates from the certificate register (scheme such as PPS / '
            'SPAN, certifying body such as IKRAM / JBC, certificate number, validity window, '
            "covered products). Use this for 'is our PPS certificate still valid', 'when "
            "does WCM PC 000321 expire', 'which certificate covers WC8038', and to deliver "
            'the certificate PDF.\n\nVALIDITY IS DERIVED, NEVER GUESSED. Every row carries '
            '`validity_state` (valid | expiring_soon | expired | not_yet_valid | unknown), '
            '`is_expired`, `days_until_expiry`, `valid_from`, `valid_until` and '
            '`covered_product_count`. When `is_expired` is true, tell the user the '
            'certificate was FOUND but is EXPIRED; never present an is_expired row as live, '
            'and never answer a validity question from a file name. `validity_state=unknown` '
            'means the register holds no expiry date: say the expiry is not recorded, never '
            'that the certificate does not expire.\n\nNARROWING FILTERS (all optional; csv / '
            'JSON list / repeated where they take ids):\n - `certificate_ids` (canonical '
            'certificate UUIDs)\n - `certificate_number` (raw text, normalized server-side: '
            "'PPS 0119', 'pps-0119' and 'PPS0119' all match the same certificate)\n - "
            '`product_ids` (canonical product UUIDs: certificates covering any of them)\n - '
            '`scheme` (e.g. PPS, SPAN), `status` (active | archived)\n - `validity_state`, '
            '`expiring_within_days` (integer), `valid_on` (YYYY-MM-DD)\n - `needs_review` '
            '(true = the register flagged the extracted data for a human)\n\nTHE SAME NUMBER '
            'CAN EXIST UNDER TWO SCHEMES (04124FC is approved under both PPS and SPAN, with '
            'different expiries). When a number filter returns more than one row, ask which '
            'scheme rather than answering from the first row.\n\nSuperseded revisions are '
            'never resolved, so no path can hand out a replaced document.'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/certificates/',
        "path_params": (),
        "query_params": ('page', 'limit', 'sort', 'dir', 'certificate_ids', 'certificate_number', 'product_ids', 'scheme', 'status', 'validity_state', 'expiring_within_days', 'valid_on', 'needs_review'),
        "body_params": (),
    },
    {
        "name": 'crm_complaint_analytics',
        "description": (
            'AGGREGATE / ANALYTICAL tool for complaints - returns COUNTS (a single number or '
            'ranked buckets), NOT a row list. Use this (NOT crm_complaints_list) whenever '
            "the user asks 'how many complaints', 'which product has the most complaints', "
            "'complaints by product / status / month', or 'how many complaints were resolved "
            "in <period>'.\n\nmetric: count (only supported value).\ngroup_by (optional): "
            "status | product | month | none (default none = one overall count). For 'which "
            "product has the most complaints' pass group_by=product - rows are ranked by "
            'count descending, so the first group is the answer.\ndate_field (optional): '
            'complaint_date (when raised, default) or resolved_at (when resolved). For '
            "'complaints resolved last month / in <period>' pass date_field=resolved_at with "
            '`date_from`/`date_to` - do NOT pass status=resolved (there is no such status; '
            'resolved_at being set already means resolved).\n`status` filters an exact '
            'complaint status (approved, processed_by_cs, closed, ...). `date_from` / '
            "`date_to` accept YYYY-MM-DD, DD/MM/YYYY, or 'YYYY-MM'. Returns ranked `groups` "
            '[{group_key, group_label, metric, value}] plus an overall `total`.'
        ),
        "method": 'GET',
        "path": '/api/v1/complaints-management/complaints/analytics',
        "path_params": (),
        "query_params": ('metric', 'group_by', 'status', 'date_field', 'date_from', 'date_to', 'limit'),
        "body_params": (),
    },
    {
        "name": 'crm_forms_management_forms_list',
        "description": (
            'List forms (newest-first by updated_at when sort omitted). Caller-supplied '
            '`limit` is respected verbatim (server max 100). OPTIONAL narrowing filter:\n  • '
            '`form_ids` (canonical form UUIDs csv / JSON / repeated)\n\nWithout `form_ids`, '
            'returns the page as requested - use page + limit to paginate.'
        ),
        "method": 'GET',
        "path": '/api/v1/forms-management/forms/',
        "path_params": (),
        "query_params": ('page', 'limit', 'form_ids', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_incoming_stock_by_product',
        "description": (
            "ONE-SHOT tool for any 'incoming for product X' / 'is SKU X arriving?' / 'how "
            "much is pending?' / 'where will this product be stocked?' question. Returns: "
            'total_remaining_incoming_quantity; warehouse_allocation_summary (per warehouse: '
            'warehouse_code, warehouse_name, allocated_quantity); per-shipment breakdown '
            '(shipment_number, container, ETA, batch_number, remaining qty, packing-list '
            'attachment, warehouse_allocations, unallocated_quantity); '
            'nearest_estimated_arrival_date.\n\nALLOCATION SIGNAL: `unallocated_quantity` is '
            'how much of that shipment line no salesperson has claimed to a warehouse yet. '
            'Empty `warehouse_allocations` = nothing allocated at all (pending allocation); '
            'a positive `unallocated_quantity` alongside allocations = partly allocated; '
            'null = fully allocated, nothing to flag. In `view=render` the same signal '
            'arrives as the per-item booleans `flags.unallocated` / '
            '`flags.partially_allocated`. Does NOT expose received quantities, SPO numbers, '
            'or internal IDs. FILTER BY UUID: `product_ids` (canonical product UUIDs).\n\n'
            'OPTIONAL ETA WINDOW: `eta_from` / `eta_to` (YYYY-MM-DD, inclusive) narrow to '
            "shipments arriving within the window - e.g. 'is SKU X arriving before month "
            "end?'. Applied on top of the product filter (a product hint is still required); "
            'never a standalone filter.'
        ),
        "method": 'GET',
        "path": '/api/v1/incoming-stock/by-product',
        "path_params": (),
        "query_params": ('product_ids', 'eta_from', 'eta_to', 'limit'),
        "body_params": (),
    },
    {
        "name": 'crm_incoming_stock_list',
        "description": (
            'UNIFIED incoming-stock list - shipment-rooted with nested product lines. Covers '
            "BOTH 'any incoming for product X / SKU X?' (pass `product_ids`) AND 'what is "
            "arriving this month / from supplier Y / shipment Z?' (pass `eta_from`/`eta_to`, "
            '`supplier_ids`, `shipment_ids`). Returns one row per still-incoming shipment: '
            'shipment_number, shipping_container_number, estimated_arrival_date, '
            'packing-list attachment, and a `lines[]` array - each line carries '
            'product_code, product_name, batch_number, remaining_incoming_quantity, '
            'warehouse_allocations (warehouse_code, warehouse_name, allocated_quantity), and '
            'unallocated_quantity.\n\nALLOCATION SIGNAL: `unallocated_quantity` is how much of '
            'that line no salesperson has claimed to a warehouse yet. Empty '
            '`warehouse_allocations` = nothing allocated at all (pending allocation); a '
            'positive `unallocated_quantity` alongside allocations = partly allocated; null '
            '= fully allocated, nothing to flag. In `view=render` the same signal arrives as '
            'the per-item booleans `flags.unallocated` / `flags.partially_allocated`.\n\nWhen '
            '`product_ids` is given, lines are filtered to those products. NO aggregate '
            'totals are returned - sum the line quantities yourself for a product total or '
            'per-warehouse summary. Never exposes received/rejected quantities, SPO numbers, '
            'or internal IDs. REQUIRED: at least ONE narrowing filter (product_ids / '
            'shipment_ids / supplier_ids / eta_from / eta_to) or the tool returns an empty '
            'page.\n\nCLEARANCE DATES (eta_delay_date, inspection_date, approval_date, '
            'gatepass_date, loading/etc/etd, liner_code, forwarders, consignee, '
            'free_days_available, coa_permit_no) are ENTITLEMENT-GATED server-side, FIELD BY '
            'FIELD. When not permitted the keys '
            "are ABSENT from the response - absent means 'not permitted', it does NOT mean "
            "'not reached yet'. Never tell a user a date is unknown or pending because a key "
            'is missing; say you cannot share it. A `field_access.denied` block lists what '
            'was withheld and why. The container number (`shipping_container_number`) and '
            'the quantities (`remaining_incoming_quantity`, `unallocated_quantity`, '
            '`allocated_quantity`) are gated the same way.'
        ),
        "method": 'GET',
        "path": '/api/v1/incoming-stock/list',
        "path_params": (),
        "query_params": ('product_ids', 'shipment_ids', 'supplier_ids', 'eta_from', 'eta_to', 'page', 'limit'),
        "body_params": (),
    },
    {
        "name": 'crm_incoming_stock_shipments',
        "description": (
            "SHIPMENT-CENTRIC incoming list: 'any incoming shipments this month?' / 'what is "
            "arriving with ETA on date X?' / 'open shipments from supplier Y'. Returns "
            'shipment headers (shipment_number, container, ETA, '
            'total_remaining_incoming_quantity, distinct_products_incoming, packing-list '
            'attachment).\n\nREQUIRED - at least ONE narrowing filter or the tool returns an '
            'empty page:\n  • `shipment_ids` (canonical inbound-shipment UUIDs)\n  • '
            '`supplier_ids` (canonical supplier UUIDs)\n  • `eta_from` / `eta_to` (ETA '
            "window, YYYY-MM-DD)\n\nFor 'incoming for product X' use "
            'crm_incoming_stock_by_product instead - do NOT call this tool without a '
            'narrower just to enumerate every open shipment.'
        ),
        "method": 'GET',
        "path": '/api/v1/incoming-stock/shipments',
        "path_params": (),
        "query_params": ('shipment_ids', 'supplier_ids', 'eta_from', 'eta_to', 'page', 'limit'),
        "body_params": (),
    },
    {
        "name": 'crm_inventory_stock_balance_list',
        "description": (
            'Paged active-warehouse stock balances. Exposes quantity_on_hand and '
            'Malaysia-time updated_at only. Response uses Sage-aligned warehouse vocabulary: '
            '`system_location` (was warehouse_code), `system_location_description` (was '
            'warehouse_name), `warehouse` (was location).\n\nALL FILTERS OPTIONAL - call with '
            'none to span every product + active warehouse.\nFILTER BY UUID: `product_ids` '
            '(canonical product UUIDs csv / JSON / repeated); `warehouse_ids` (canonical '
            'warehouse UUIDs csv / JSON / repeated). quantity_operator / quantity_value '
            'filter on-hand; status = critical|low|normal|overstock.\nRows whose on-hand is 0 '
            "ONLY because the latest stock movement was a SYSTEM_ADJUSTMENT (e.g. 'missing "
            "from full stock take') are always hidden; a genuine 0 is still returned.\n\n"
            "QUANTITY ASKED FOR: if the user said how many units they need ('do you have "
            "50?', or just '50' after being asked how many), pass that number as "
            '`requested_qty`. Some contacts are answered yes/no against it instead of with '
            'quantities, and without it the reply can only ask how many units they need.\n\n'
            'QUANTITY PER PRODUCT: when the user named a DIFFERENT quantity for MORE THAN '
            "ONE product in the same ask ('MWT5727SS-CR 5, MHS1028 60'), pass "
            "`requested_quantities` - a JSON object mapping each product's UUID to ITS OWN "
            'quantity - instead of, or alongside, `requested_qty`. Per product the map wins; '
            '`requested_qty` only fills a product the map does not name.\n\nOUTSTANDING SO: '
            "pass `include_sellable=true` to add `Outstanding` (that warehouse row's own "
            'open sales-order quantity, not yet a delivery order) to every stock row, and an '
            "`(O/S: n)` suffix on the compact block's Total and warehouse lines. Default "
            'false. Field-reveal gated downstream (`inventory.sellable`), not by this tool.'
        ),
        "method": 'GET',
        "path": '/api/v1/inventory/stock/balance',
        "path_params": (),
        "query_params": ('page', 'limit', 'product_ids', 'sort', 'dir', 'warehouse_ids', 'quantity_operator', 'quantity_value', 'status', 'requested_qty', 'requested_quantities', 'include_sellable'),
        "body_params": (),
    },
    {
        "name": 'crm_inventory_warehouses_list',
        "description": (
            'List warehouses. Response uses Sage-aligned vocabulary: `system_location` (was '
            'warehouse_code), `system_location_description` (was warehouse_name), '
            '`warehouse` (was location). FILTER BY UUID: `warehouse_ids` (canonical '
            'warehouse UUIDs csv / JSON / repeated). is_active=true for active-only.'
        ),
        "method": 'GET',
        "path": '/api/v1/inventory/warehouses/',
        "path_params": (),
        "query_params": ('page', 'limit', 'warehouse_ids', 'is_active'),
        "body_params": (),
    },
    {
        "name": 'crm_lookup_resolve',
        "description": (
            'Resolve a raw user keyword into the canonical option value for a CRM dropdown '
            'set. Body: {set_key, raw, locale?}. Returns '
            '{value,label,matched_keyword,match_type,score} or 404. Use whenever a user '
            'gives a free-text value for a CRM dropdown field - translate first, then send '
            'the canonical value to the matching write API.'
        ),
        "method": 'POST',
        "path": '/api/v1/lookup/resolve',
        "path_params": (),
        "query_params": (),
        "body_params": ('set_key', 'raw', 'locale'),
    },
    {
        "name": 'crm_marketing_promotion_attachments_list',
        "description": (
            'List / filter promotion-attachment links (BROCHURE / FLYER documents). Each row '
            'carries `promotion_type_name`, `is_expired` - true when the parent promotion is '
            'NOT currently live (is_active off OR today outside its start/end window) - and '
            '`expired_but_usable`. When `expired_but_usable` is true, say the promotion HAS '
            'EXPIRED (give the end date) BUT STILL APPLIES. When is_expired is true without '
            'it, the document was FOUND but its promotion is EXPIRED; never present an '
            'is_expired row as a live promotion.\n\nREQUIRED - at least ONE narrowing filter '
            'or the tool returns an empty page:\n  • `promotion_ids` (canonical promotion '
            'UUIDs)\n  • `attachment_ids` (canonical attachment UUIDs)\n\nACCESS LEVELS: '
            '`access_levels` filters by parent promotion access overlap. Pass a single name '
            '(e.g. "Sorento Dealer") or a JSON array. Phrases like `sorento dealer`, `mocha '
            'office`, `end user` are `access_levels` ONLY - never `*_ids` values.\n\n'
            'PARENT-PROMOTION ACTIVITY: documents come back only for the promotions Sorento '
            'currently serves - live promotions, plus per type config at most the latest '
            'ended one that is still honoured, and never an expired special. Documents of an '
            'inactive, superseded or no-longer-honoured promotion cannot be retrieved here '
            'at all, so never offer a result as the full set of past promotion flyers. DO '
            'NOT PASS `active`: the backend discards it, and `active=false` returns the same '
            'served set rather than the archive.'
        ),
        "method": 'GET',
        "path": '/api/v1/marketing/promotion-attachments/',
        "path_params": (),
        "query_params": ('page', 'limit', 'sort', 'dir', 'promotion_ids', 'attachment_ids', 'access_levels', 'active'),
        "body_params": (),
    },
    {
        "name": 'crm_marketing_promotion_products_list',
        "description": (
            "Promotion product lines (paginated). Each row carries the parent promotion's "
            '`promotion_attachments` inline, plus `promotion_type_name`, `is_expired` - true '
            'when the parent promotion is NOT currently live (is_active off OR today outside '
            'its start/end window) - and `expired_but_usable`. When `expired_but_usable` is '
            'true, say the promotion HAS EXPIRED (give the end date) BUT STILL APPLIES. When '
            'is_expired is true without it, the line was FOUND but its promotion is EXPIRED; '
            'never present an is_expired row as a live promotion.\n\nREQUIRED - at least ONE '
            'narrowing filter or the tool returns an empty page:\n  • `promotion_ids` '
            '(canonical promotion UUIDs)\n  • `product_ids` (canonical product UUIDs)\n\n'
            'STRUCTURED FILTERS: PRICE price_min/max (MYR); DIMENSIONS (mm) length_min/max, '
            'width_min/max, height_min/max; axis-agnostic any_dimension_min/max; item_type; '
            'status=active|inactive|all. EXACT vs FUZZY: bare number is EXACT (min == max == '
            'value); hedge words apply ±5 mm. ACCESS LEVELS: `access_levels` filters by '
            'parent promotion access overlap.\n\nPARENT-PROMOTION ACTIVITY (NOT product '
            'status): lines come back only for the promotions Sorento currently serves - '
            'live promotions, plus per type config at most the latest ended one that is '
            'still honoured, and never an expired special. Lines under an inactive, '
            'superseded or no-longer-honoured promotion cannot be retrieved here at all, so '
            "never present a result as the full history of a product's promotions. DO NOT "
            'PASS `active`: the backend discards it, and `active=false` returns the same '
            'served set rather than the archive.'
        ),
        "method": 'GET',
        "path": '/api/v1/marketing/promotion-products/',
        "path_params": (),
        "query_params": ('page', 'limit', 'sort', 'dir', 'promotion_ids', 'product_ids', 'item_type', 'status', 'active', 'price_min', 'price_max', 'length_min', 'length_max', 'width_min', 'width_max', 'height_min', 'height_max', 'any_dimension_min', 'any_dimension_max', 'access_levels'),
        "body_params": (),
    },
    {
        "name": 'crm_marketing_promotions_list',
        "description": (
            'List promotions (summary fields + linked attachments inline; no product lines). '
            'Each row carries its `attachments` array.\n\nWHICH PROMOTIONS COME BACK is '
            'decided by the backend, per promotion TYPE - a live promotion always wins, and '
            'a type with no live promotion may still contribute the one that just ended. '
            'Every row carries `promotion_type_name`, `is_expired` and '
            '`expired_but_usable`:\n  • `is_expired=false` - live. Present it normally.\n  • '
            '`is_expired=true` AND `expired_but_usable=true` - say the promotion HAS EXPIRED '
            '(give its end_date) BUT STILL APPLIES, e.g. "the July promotion ended on 31/07 '
            'and can still be used". Never hide it and never present it as live.\n  • '
            '`is_expired=true` and `expired_but_usable=false` - FOUND but EXPIRED; never '
            'present it as usable.\nA promotion type that cannot be honoured after its end '
            'date (a special) is not returned at all once it ends, so there is nothing to '
            'explain away.\n\nNO HISTORY THROUGH THIS TOOL. It only ever returns the '
            'promotions Sorento currently serves - the live ones, plus per type config at '
            'most the latest ended one that is still honoured, and never an expired special. '
            'Inactive, superseded and no-longer-honoured promotions cannot be retrieved here '
            'at all. If the user asks for past promotions ("every promo that ended in July", '
            '"show me the expired ones"), say that list is not available from this tool '
            'rather than presenting the served set as if it were the history.\n  • DO NOT '
            'PASS `active`. The backend discards it; `active=false` does NOT return inactive '
            'promotions, it returns the same served set.\n  • `period_from` / `period_to` '
            '(YYYY-MM-DD) and `date_mode` only NARROW the served set - they never reach past '
            'it. `date_mode` picks which date the window tests: `overlap` (default, promo '
            'running during the window), `started` (start_date in window), `ended` (end_date '
            'in window). `ended` is therefore not an archive query: it can only return '
            'served promotions that happen to have ended in the window, so never answer '
            '"what ended in July" from it.\n\nOPTIONAL narrowing filters (call without any to '
            'get the latest 10 currently-served promotions):\n  • `promotion_ids` (canonical '
            'promotion UUIDs, csv / JSON / repeated)\n  • `product_ids` (canonical product '
            'UUIDs - promotions containing any)\n  When BOTH `promotion_ids` and '
            '`product_ids` are supplied, they combine\n  via OR: a promotion is returned if '
            'it is in `promotion_ids` OR contains\n  any product in `product_ids` (no AND '
            'option).\n\nUnfiltered MCP calls are auto-capped to limit=10 newest-first so open '
            'questions like "what is sorento\'s latest promo" return a bounded page instead '
            'of the full catalog.\n\nACCESS LEVELS: `access_levels` filters promotions whose '
            '`access_levels` JSONB overlaps the supplied names (case-insensitive). Phrases '
            'like `sorento dealer`, `mocha office`, `end user` are `access_levels` ONLY - '
            'never `*_ids` values.'
        ),
        "method": 'GET',
        "path": '/api/v1/marketing/promotions/',
        "path_params": (),
        "query_params": ('page', 'limit', 'promotion_ids', 'product_ids', 'active', 'period_from', 'period_to', 'date_mode', 'sort', 'dir', 'access_levels'),
        "body_params": (),
    },
    {
        "name": 'crm_master_brands_list',
        "description": (
            'List brands with pagination. FILTER BY UUID: `brand_ids` (canonical brand '
            'UUIDs) and / or `product_ids` (returns the brands of those products). Both '
            'accept csv / JSON list / repeated.'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/brands/',
        "path_params": (),
        "query_params": ('page', 'limit', 'brand_ids', 'product_ids'),
        "body_params": (),
    },
    {
        "name": 'crm_master_customers_list',
        "description": (
            'List / search / RANK distinct customers, deduplicated by debtor_name aggregated '
            'from the orders table (the customers master table is not used by the business - '
            'real customer identity lives on orders.debtor_name / debtor_code). Each row '
            'returns debtor_name, debtor_code AND order_count (the number of orders that '
            'customer placed).\n\nTHIS TOOL AGGREGATES AND RANKS CUSTOMERS BY ORDER COUNT - '
            "use it (NOT the orders list) for 'top customers', 'top 5 / top N customers by "
            "order count', 'busiest customers', 'which customers have the most orders', "
            "'rank customers by orders', 'customer order counts', 'who orders the most'. "
            'Pass `sort=order_count` with `dir=desc` and `limit=N` to get the top N. For '
            "plain discovery ('list customers', 'find customer ABC', 'do we already have "
            "this developer') sort by debtor_name.\n\nFILTER BY UUID: `customer_ids` "
            '(canonical customer UUIDs, csv / JSON / repeated) filters the source orders by '
            'Order.customer_id before aggregation. External AI/MCP callers are HARD-CAPPED '
            'at limit=20 server-side. Sort: debtor_name | debtor_code | order_count.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/orders/debtors',
        "path_params": (),
        "query_params": ('page', 'limit', 'customer_ids', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_master_product_attachments_list',
        "description": (
            'List per-SKU product↔attachment links (BROCHURE / SPEC SHEET / DATASHEET / '
            'MANUAL / INSTALLATION GUIDE / CERTIFICATE for a specific product). FILTER BY '
            'UUID: `product_ids` (canonical product UUIDs), `attachment_ids` (canonical '
            'attachment UUIDs), and / or `attachment_type_ids` (canonical AttachmentType '
            'UUIDs - narrows to a doc class such as brochure or spec sheet), and / or '
            '`certificate_ids` (canonical Certificate UUIDs - the product↔file rows whose '
            'file is a filed revision of those certificates; get the UUID from '
            'crm_certificates_list first). All accept csv / JSON list / repeated query '
            'params.\n\nCERTIFICATE VALIDITY: a row whose file is a filed certificate carries '
            '`certificate` with `validity_state` (valid / expiring_soon / expired / '
            'not_yet_valid / unknown), `valid_until` and `is_current_revision`. NEVER '
            'compare dates yourself and never call a certificate valid when `certificate` is '
            'absent - absent means the file is not a certificate at all (a brochure, a spec '
            'sheet). Do not hand over a file whose `validity_state` is `expired`; say it has '
            'lapsed and use crm_certificates_list to find whether a renewal has been filed.'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/product-attachments/',
        "path_params": (),
        "query_params": ('page', 'limit', 'sort', 'dir', 'product_ids', 'attachment_ids', 'attachment_type_ids', 'certificate_ids'),
        "body_params": (),
    },
    {
        "name": 'crm_master_product_categories_list',
        "description": (
            'List product categories with pagination. FILTER BY UUID: `category_ids` '
            '(canonical category UUIDs) and / or `product_ids` (returns the categories of '
            'those products). csv / JSON / repeated.'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/product-categories/',
        "path_params": (),
        "query_params": ('page', 'limit', 'category_ids', 'product_ids'),
        "body_params": (),
    },
    {
        "name": 'crm_master_products_list',
        "description": (
            'List structured product master rows (SKU / code / name / description / brand / '
            'category / price / dimensions) with filters and pagination. FILTER BY UUID: '
            'pass `product_ids` as csv / JSON list / repeated of canonical product UUIDs. '
            '`category_id` and `brand_id` are canonical UUIDs. PRICE: price_min, price_max '
            '(MYR). DIMENSIONS (millimetres): per-axis length_min/max, width_min/max, '
            'height_min/max; axis-agnostic any_dimension_min / any_dimension_max (match when '
            'ANY of L/W/H is in range). EXACT vs FUZZY: bare numbers are EXACT (min == max '
            "== value); apply ±5 mm only when the user hedges ('around', 'about', '~', "
            "'approx'). SORT KEYS: created_at, updated_at, product_code, product_name, "
            'list_price (alias price), cost_price, invoice_price, is_active, '
            'dimensions_length (alias length), dimensions_width (alias width), '
            'dimensions_height (alias height), largest_dimension, smallest_dimension; '
            'combine with dir=asc|desc. Each row returns `currency` (default MYR) - render '
            'prices with that code, never $. Rows may include `field_attachments` (map of '
            'product field → linked docs). ATTACHMENTS: pass `attachment_type_ids` '
            "(canonical AttachmentType UUIDs, csv / JSON / repeated) to nest each product's "
            'files of those types under `attachments[]` (e.g. Product Photos, Technical '
            'Specifications). Omit it for a plain price/spec listing with NO attachments.\n\n'
            'DERIVED SPECIFICATIONS: pass `include_specifications=true` to add '
            '`specifications` to every row - `values` (the spec block as key -> value, e.g. '
            'thickness 1.2), `rendered_text` (the spec sentence) and `sources` (per key: '
            'derived | human | category, i.e. where the value came from). Null on a product '
            "with no derived specs, which means 'not recorded', never 'does not have it'. "
            'Default false; omit it for a plain price/dimension listing.\n\nSPEC LIST (A1, '
            'chatbot-growth-r1): pass `include_specs=true` to add `specs: [{key, label, '
            'value, unit, rank_weight}]` - only populated keys, ordered by rank_weight desc '
            'then label. Default false; omit it for a plain price/dimension listing. '
            'Distinct from `include_specifications` above (this is the ranked-list shape the '
            'render presenter appends as fields; that one is the sentence/values block for a '
            'catalogue editor).'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/products/',
        "path_params": (),
        "query_params": ('page', 'limit', 'product_ids', 'status', 'price_min', 'price_max', 'item_type', 'length_min', 'length_max', 'width_min', 'width_max', 'height_min', 'height_max', 'any_dimension_min', 'any_dimension_max', 'attachment_type_ids', 'include_specifications', 'include_specs', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_master_units_of_measure_list',
        "description": (
            'List units of measure with pagination. FILTER BY UUID: `uom_ids` (canonical UOM '
            'UUIDs) and / or `product_ids` (returns the base UOMs of those products). csv / '
            'JSON / repeated.'
        ),
        "method": 'GET',
        "path": '/api/v1/master-data/units-of-measure/',
        "path_params": (),
        "query_params": ('page', 'limit', 'uom_ids', 'product_ids'),
        "body_params": (),
    },
    {
        "name": 'crm_order_management_orders_by_product_list',
        "description": (
            'List distinct CUSTOMER SALES orders containing a specific product (outgoing / '
            'sold, NOT incoming stock). A product narrower is REQUIRED - pass `product_ids` '
            '(canonical product UUIDs, csv / JSON / repeated) or the tool returns an empty '
            'page.\n\nExternal/AI callers are HARD-CAPPED at limit=20 server-side - narrow via '
            'UUID + date filters and paginate via `page` when more results are needed.\n\n'
            'OPTIONAL UUID FILTERS: `customer_ids`, `transporter_ids` (canonical UUIDs). '
            "`brand_ids` - narrows to that brand's own products (Product.brand_id); counts "
            'as the product narrower this endpoint requires, alone or intersected with '
            '`product_ids`. Date window: actual_delivery_date_from / actual_delivery_date_to '
            "(YYYY-MM-DD). For 'any incoming for product X' use "
            'crm_incoming_stock_by_product instead.\n\nQUANTITY ASK: pass '
            '`include_summary=true` when the user asks HOW MANY / how much a customer took '
            'of a product - the response then carries `summary` (per product and per '
            'customer x product delivered/pending quantity, counts, date spans). Add '
            '`include_pipeline=true` alongside it to fold `so_outstanding_qty` (open SO '
            'lines not yet a DO, same customer_ids/product_ids scope) into EVERY '
            '`summary.products` and `summary.groups` row; default false, independent of '
            '`include_summary`. Omit both for a plain DO list.\n\n`customer_query` - partial '
            'customer/debtor name or code match (case-insensitive). `warehouse_codes` - '
            'exact warehouse codes (csv/JSON/repeated), filtered on the SAME line that '
            'matched the product. The only date filter here is '
            '`actual_delivery_date_from`/`actual_delivery_date_to` above; for a '
            'sales-order-placement-date window over open/pending figures use '
            'crm_outstanding_report instead.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/orders/by-product',
        "path_params": (),
        "query_params": ('page', 'limit', 'product_ids', 'brand_ids', 'customer_ids', 'transporter_ids', 'actual_delivery_date_from', 'actual_delivery_date_to', 'order_status', 'include_summary', 'include_pipeline', 'sort', 'dir', 'customer_query', 'warehouse_codes'),
        "body_params": (),
    },
    {
        "name": 'crm_order_management_orders_list',
        "description": (
            'List orders. Response uses `pickup_time` (was delivery_time); `order_status` is '
            'a plain string. Rows carry NO UUIDs - identify orders by `order_number`. '
            'External/AI callers are HARD-CAPPED at limit=20 server-side - narrow via UUID + '
            'date filters and paginate via `page` when more results are needed.\n\nFILTER BY '
            'UUID (typed canonical UUIDs, csv / JSON / repeated):\n  • `order_ids` - specific '
            'orders\n  • `customer_ids` - customers (Order.customer_id, falls back to '
            'debtor_name for legacy rows)\n  • `product_ids` - orders containing any of these '
            'products\n  • `brand_ids` - orders containing a product of this brand '
            '(Product.brand_id via order lines); intersects with `product_ids` when both are '
            'given\n  • `transporter_ids` - transporters (Order.transporter_id, text fallback '
            'for legacy rows)\nDate window: actual_delivery_date_from / '
            "actual_delivery_date_to.\nDELIVERY BUCKET: `order_status` = 'outstanding' | "
            "'delivered' | 'so_outstanding' (omit for all, over `orders`). 'so_outstanding' "
            'is a DIFFERENT bucket over `sales_order_lines` (qty_ordered - qty_delivered > '
            '0, still open, no DO created yet AT ALL) - rows carry '
            'so_number/product_code/outstanding_qty/order_date/customer/requested_delivery_date '
            "instead of the usual order fields. Use for 'SO outstanding', 'ordered but no "
            "DO', 'belum DO', '还没出DO'.\nQUANTITY ASK: pass `include_summary=true` when the "
            'user asks HOW MANY / how much a customer took of a product - the response then '
            'carries `summary` (filter-wide delivered/pending quantity per product, counts, '
            'customers, delivered date span, the span of DO dates over every DO in the row '
            'delivered or not). Add `include_pipeline=true` alongside it for the three-line '
            'SO outstanding / DO open / delivered pipeline '
            '(so_outstanding_qty/so_outstanding_count over open SO lines for the same '
            'customer_ids/product_ids scope, folded into `summary`); default false, '
            "independent of `include_summary`. Omit both for a plain DO list. 'outstanding' "
            '= NOT yet delivered (New Order, Processing, In Transit, Cancelled, or a '
            "delivery date under a non-delivered status); 'delivered' = status "
            'delivered/completed AND actual_delivery_date set (or, with no date, assumed '
            'delivered by the DO history backfill). Use for '
            "'outstanding/pending/undelivered orders', 'belum hantar', 'not delivered yet'. "
            "AND'd with the other filters.\n\nGROUPING: `group_by` = customer | transporter | "
            'date | product renders headed sections instead of (alongside) the flat list - '
            "use for 'DO by transporter this week', 'open DO by customer'. Applies to every "
            'bucket. `sort`/`dir`/`limit` narrow and order the rows before grouping.\n\n'
            '`customer_query` - partial customer/debtor name or code match '
            '(case-insensitive), an alternative to resolving `customer_ids` upstream. '
            '`warehouse_codes` - exact warehouse codes (csv/JSON/repeated); an order '
            'qualifies when ANY of its lines sits at one of these warehouses. The only date '
            'filter here is `actual_delivery_date_from`/`actual_delivery_date_to` above; for '
            'a sales-order-placement-date window over open/pending figures use '
            'crm_outstanding_report instead.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/orders/',
        "path_params": (),
        "query_params": ('page', 'limit', 'order_ids', 'customer_ids', 'product_ids', 'brand_ids', 'transporter_ids', 'actual_delivery_date_from', 'actual_delivery_date_to', 'order_status', 'include_summary', 'include_pipeline', 'group_by', 'sort', 'dir', 'customer_query', 'warehouse_codes'),
        "body_params": (),
    },
    {
        "name": 'crm_outstanding_report',
        "description": (
            'SO backlog + DO pending for ONE product - the report to use for any '
            "'outstanding', 'o/s', 'backlog', 'sales order outstanding', 'DO pending', 'not "
            "yet delivered', 'not yet transferred to DO' question. Returns TWO named blocks, "
            "never the bare word 'outstanding' alone:\n  • `so` - Sales order outstanding: "
            '`ordered_qty` (SUM qty_ordered), `transferred_qty` (SUM qty_delivered), '
            '`outstanding_qty` (the difference - booked, not yet turned into a DO), '
            '`so_count`, `order_date_min`/`order_date_max`.\n  • `do` - Delivery order '
            'pending: `pending_qty` (DO raised, not yet delivered), `do_count`, '
            '`do_date_min`/`do_date_max`. PENDING DOs ONLY - a delivered DO is not in this '
            'population at all, so there is no delivered or total DO quantity here.\n`scope` '
            '= so | do | both (default both) picks which block(s) come back - the OTHER key '
            'is absent from the body entirely, not null. Every total is also broken down as '
            '`so_by_location`/`so_by_customer`/`do_by_location`/`do_by_customer` '
            "(code/customer_name + that block's own quantities - the DO side carries "
            '`pending_qty` only), and as `so_rows`/`do_rows` (one row per SO / per PENDING '
            'DO, lines rolled up).\n\nSUBJECT: a product, a customer, a brand, or any '
            'combination - at least one of `product_code` / `customer_ids` / '
            '`customer_query` / `brand_ids` is REQUIRED (422 otherwise). The breakdown '
            'groups follow the subject: a product subject returns `so_by_location` + '
            '`so_by_customer` (and the DO pair), a CUSTOMER subject returns `so_by_location` '
            '+ `so_by_product` (what that customer is waiting for, per product), and naming '
            'both returns `so_by_location` only; a group the subject does not want is ABSENT '
            'from the body. Rows always carry both `customer_name` and `product_code`.\n\n'
            'FILTERS: `product_code` (exact, case-insensitive - no sibling-code expansion). '
            '`product_codes` - the SEVERAL-code form of it (csv/JSON/repeated), for one '
            'question about a whole family; the rendered header then names every code. '
            '`customer_query` - partial match on customer NAME only (never debtor/customer '
            'code). `customer_ids` - canonical customer UUIDs (csv/JSON/repeated); '
            'intersects with `customer_query` when both are given. `brand_ids` - canonical '
            'brand UUIDs (csv/JSON/repeated), a FILTER never a breakdown axis '
            '(Product.brand_id); the response echoes `brand_name` so the header can say '
            "'Brand: Sorento'. `warehouse_codes` - exact warehouse codes "
            "(csv/JSON/repeated); resolve a location TOKEN (e.g. an 'IB' suffix matching "
            'several codes) to exact codes yourself before calling - this tool does not do '
            'suffix matching. `order_date_from`/`order_date_to` filter SO rows on '
            'sales_orders.order_date and DO rows on orders.order_date (never '
            'actual_delivery_date - a pending DO by definition has none); omit both for all '
            'dates. `detail` = so | do - render the numbered detail list for that scope '
            "instead of the two-block report (`view=render`); the report's own computation "
            'is unchanged by it; `detail=both` renders the sales order list and then the '
            'delivery order list in one reply. `location_token` (max 32 chars) - the raw '
            "location word you resolved into `warehouse_codes` (e.g. 'IB'), echoed back so "
            "the rendered header reads 'IB (BRW-IB, MWH-IB)'. `so_refused=true` - echoed "
            'back so the rendered reply names the withheld SO half; it changes no filter, '
            'send `scope=do` alongside it.\n\nUse crm_order_management_orders_list / '
            'crm_order_management_orders_by_product_list instead for a plain row list or a '
            'delivered/actual-delivery-date question.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/outstanding-report',
        "path_params": (),
        "query_params": ('product_code', 'product_codes', 'scope', 'customer_query', 'customer_ids', 'warehouse_codes', 'brand_ids', 'order_date_from', 'order_date_to', 'detail', 'location_token', 'so_refused'),
        "body_params": (),
    },
    {
        "name": 'crm_procurement_po_last_cost_list',
        "description": (
            "The last PURCHASE ORDER line PER PRODUCT PER LOCATION and its cost - 'last "
            "purchase cost for X', 'what did we pay for X', 'last cost', 'buying price'. "
            'Cancelled lines and cancelled POs are excluded; a line with no unit_cost is '
            'never picked.\n\nEach row reads in this order: PO Number, Product Code, PO '
            'Quantity, PO Date, Cost / unit, Discount / unit, Cost after discount / unit, '
            'Warehouse, Supplier. The three money figures are PER UNIT, DERIVED by the '
            "backend from the LINE's own discount / line_total amounts (never a unit figure "
            'the source data carries directly): Discount / unit is ALWAYS present, 0.00 when '
            'the line carries no discount, Cost after discount / unit falls back to the '
            'plain Cost / unit when the line has no line_total, Warehouse is present ONLY '
            'when the line names one, and Supplier is present ONLY when the purchase order '
            'names one - a line bought with no warehouse stated, or a PO with no supplier, '
            'answers with that one field absent.\n\nFILTER BY UUID: `product_ids`, '
            '`warehouse_ids` (canonical UUIDs, csv / JSON / repeated), both optional; '
            '`warehouse_ids` narrows BEFORE the pick. `top_n` (default 1) = lines per '
            '(product, warehouse) when `product_ids` is given, else lines overall - a '
            'resolved product FAMILY returns up to `top_n` lines for EACH member at EACH of '
            'its locations, not `top_n` rows overall; with no product named, `top_n` is a '
            'plain cap over every line, newest first, across every product.\n\nRESTRICTED: '
            'unit_cost, discount_per_unit and unit_cost_after_discount are gated on the '
            'contact holding purchase_orders.cost; Supplier is gated on '
            'purchase_orders.supplier, independent of the cost grant - a dealer without the '
            'matching grant never sees that one field.'
        ),
        "method": 'GET',
        "path": '/api/v1/procurement/purchase-orders/last-cost',
        "path_params": (),
        "query_params": ('product_ids', 'warehouse_ids', 'top_n'),
        "body_params": (),
    },
    {
        "name": 'crm_procurement_po_placed_list',
        "description": (
            'List PURCHASE ORDER lines PLACED but not yet fully received (qty_ordered - '
            "qty_received > 0, line open) - 'PO for X', 'PO placed, not yet shipped', 'what "
            "did we order from the supplier'. Each row carries po_number, product_code, "
            'ordered_qty, outstanding_qty, po_date, location, and supplier (RESTRICTED - a '
            'dealer never sees it, only a contact holding purchase_orders.supplier).\n\nNEVER '
            'NETTED against incoming: `spo_allocations.po_line_id` is NULL on every row, so '
            'this tool NEVER subtracts what has already arrived - for that use '
            "crm_procurement_spo_allocations_last_receipt_list, and for Foundre's rule ('no "
            "stock, no incoming, but a PO is placed') the CRM probes this tool itself.\n\n"
            'FILTER BY UUID: `product_ids`, `warehouse_ids` (canonical UUIDs, csv / JSON / '
            "repeated; the line's warehouse - an SPO allocation also matches by its book "
            'location code). Date window: expected_date_from / expected_date_to (filters '
            'only; the expected date is not rendered in the reply).\n\nSORT: `sort` = '
            'expected_date | product | supplier | outstanding_qty | po_date | ordered_qty, '
            '`dir` = asc | desc; an unknown value is a 422.\n\nGROUPING: `group_by` = product '
            '| supplier | date renders headed sections. `include_summary=true` adds '
            '`summary` (po_placed_qty/po_placed_count over the filtered lines).'
        ),
        "method": 'GET',
        "path": '/api/v1/procurement/purchase-orders/placed',
        "path_params": (),
        "query_params": ('limit', 'product_ids', 'warehouse_ids', 'expected_date_from', 'expected_date_to', 'group_by', 'include_summary', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_procurement_spo_allocations_last_receipt_list',
        "description": (
            "The last SPO line PER PRODUCT, by the SPO's expected date - 'last in for X', "
            "'last incoming qty', '上次进货', 'last 3 in'. GR never decides WHICH line answers: "
            "the ordering is purely the SPO's own delivery date, and a product whose latest "
            'line is still open (nothing received) still answers. GR is REPORTED on the line '
            'that answered, when there is any.\n\nEach row reads in this order: spo_number; '
            'container_number (present ONLY when the line was ingested from a shipping order '
            'that named its container - an older line has no such field); product_code; '
            'spo_quantity (the ordered quantity); gr_quantity (the received quantity, '
            'present ONLY when something has actually been received - an open line has no '
            'such field); spo_date (the SPO date that answered) with spo_date_source naming '
            "which column it came from ('expected' = expected_date, 'issued' = issue_date "
            "when expected_date is empty, 'recorded' = neither is set, so created_at "
            "answered and the field is labelled 'SPO Date (recorded)' so it is not read as a "
            'promised delivery); gr_date (the goods-received date, present ONLY when an '
            'APPROVED GRN header points at the line - it comes from picking_lines -> '
            "picking_headers.picking_date with picking_status 'approved'; a line received "
            'through the ESB-stated path has a gr_quantity and no gr_date, which is normal); '
            'and warehouse.\n\nFILTER BY UUID: `product_ids`, `warehouse_ids` (canonical '
            'UUIDs, csv / JSON / repeated), both optional; `warehouse_ids` narrows BEFORE '
            'the pick. `top_n` (default 1) = lines per product when `product_ids` is given, '
            "else lines overall - 'last 3 in' for a resolved product FAMILY returns up to "
            "`top_n` lines for EACH member, not `top_n` rows overall; 'last 3 in' with NO "
            'product named returns the 3 newest lines across every product, NOT 3 per '
            'product.\n\nSORT: `sort` = spo_date (default) | spo_quantity | gr_date | '
            'gr_quantity, `dir` = asc | desc (default desc); nulls last; `top_n` still means '
            'lines per product / lines overall.'
        ),
        "method": 'GET',
        "path": '/api/v1/procurement/spo-allocations/last-receipt',
        "path_params": (),
        "query_params": ('product_ids', 'warehouse_ids', 'top_n', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_project_detail',
        "description": (
            'Full detail for ONE project sales pursuit: registration facts (developer, '
            'registered company / SPV, location, architect, main contractor, brands, '
            'estimated value, launch date, expected delivery window), the funnel stage and '
            'derived outcome, the owner, the critical flag with its management notes, '
            'activity recency and the derived next action, plus the staleness rung. USE '
            "after crm_projects_list when the user asks about one named project ('what is "
            "the status of Residensi Damai', 'who owns PRJ-000142', 'when does <project> "
            "launch'). INVOCATION: `project_id` is the canonical project UUID -- resolve the "
            'project code or title first. For what we quoted on it, call '
            'crm_project_quotations_list.'
        ),
        "method": 'GET',
        "path": '/api/v1/project-sales/projects/{project_id}',
        "path_params": ('project_id',),
        "query_params": (),
        "body_params": (),
    },
    {
        "name": 'crm_project_forecast',
        "description": (
            'AGGREGATE project-sales forecast: THREE NUMBERS THAT MUST NEVER BE ADDED '
            'TOGETHER.\n  * committed -- purchase orders on record. The only banked figure.\n  '
            '* pipeline -- open quotations at their current version, or the registration '
            'estimate where nothing is priced yet. SPECULATIVE.\n  * weighted -- pipeline '
            "times the probability configured on each project's stage. SPECULATIVE.\nThere is "
            'deliberately NO total: a single figure mixing a signed PO with a '
            '10%-probability rumour is the number this module exists to stop producing. When '
            'answering, report the three separately and say which are speculative.\n\nAlso '
            'returns `by_year` (the same three per delivery year, derived from launch date '
            'plus the configured lag unless a project states its own window) and `undated` '
            'for projects with no derivable year -- report `undated` rather than dropping '
            'it, or the years will not reconcile with the totals. `project_count` counts '
            'live pursuits only; lost projects contribute nothing. No filters: it is the '
            "whole company's forecast."
        ),
        "method": 'GET',
        "path": '/api/v1/project-sales/reports/forecast',
        "path_params": (),
        "query_params": (),
        "body_params": (),
    },
    {
        "name": 'crm_project_quotations_list',
        "description": (
            'List the QUOTATION SCOPES on one project (e.g. House Units, Common Area) with '
            "each scope's current version number, current total, outcome (open / won / lost) "
            "and loss reason. USE for 'what did we quote on <project>', 'how much is "
            "<project> worth', 'did we win the common area', 'why did we lose <project>'. A "
            'project has MANY quotations, one per scope, and each quotation has versions -- '
            "the total returned is the CURRENT version's, which is the only figure that "
            'should be quoted back. INVOCATION: `project_id` is the canonical project UUID.'
        ),
        "method": 'GET',
        "path": '/api/v1/project-sales/projects/{project_id}/quotations',
        "path_params": ('project_id',),
        "query_params": (),
        "body_params": (),
    },
    {
        "name": 'crm_projects_list',
        "description": (
            'List PROJECT SALES pursuits (property developments, hotels, fitouts we are '
            'trying to win) with UUID filters, stage filter and pagination. PRIMARY TOOL for '
            "'which projects are we chasing', 'my pipeline', 'projects for <developer>', "
            "'projects at tendering stage', 'critical projects', 'what is <salesperson> "
            "working on'. NOT for delivery orders or purchase orders -- a project is a "
            'pursuit, not a transaction.\n\nEach row returns project_code, title, '
            'developer_name, type_name, status_label, outcome (open / won / lost / partial), '
            'owner_name, estimated_sales_value, launch_date, days_since_last_activity, '
            'next_action_date, next_action_overdue, stale_level (0 fine, 1 nudged, 2 warned, '
            '3 unattended) and is_unattended.\n\nALL FILTERS OPTIONAL. FILTER BY UUID: '
            '`project_ids`, `owner_user_ids`, `developer_party_ids` each accept csv / JSON '
            'list / repeated canonical UUIDs -- resolve a project code, salesperson or '
            'developer NAME to its UUID first; a non-UUID value is rejected with 400 rather '
            "than silently ignored.\n  * `owner_user_ids` IS 'my pipeline'. Rows carry "
            "`owner_name`, never an owner UUID, so the salesperson's FULL NAME or work email "
            'has to be resolved to a user UUID first (the CRM assistant does that '
            'automatically for this param); an unresolved name is rejected rather than '
            'answered about the wrong person.\n  * `status_key` -- funnel rung by stable key: '
            'identified, registered, specified, quoted, tendering, po_received, lost, '
            'dormant. An unknown key returns 422 naming the valid ones.\n  * `outcome` -- '
            'open | won | lost | partial. Commercial result, DERIVED from the quotations; a '
            "project can sit at 'tendering' with outcome 'partial' when one scope is won.\n  "
            '* `only_critical=true` -- the Final Negotiation flag, not a stage.\nSORT KEYS: '
            'created_at, updated_at, title, project_code, estimated_sales_value, '
            'last_meaningful_activity_at; combine with dir=asc|desc.'
        ),
        "method": 'GET',
        "path": '/api/v1/project-sales/projects/',
        "path_params": (),
        "query_params": ('page', 'limit', 'project_ids', 'owner_user_ids', 'developer_party_ids', 'status_key', 'outcome', 'only_critical', 'sort', 'dir'),
        "body_params": (),
    },
    {
        "name": 'crm_resource_attachments_catalogue',
        "description": (
            'DOMAIN: catalogue. Attachment lookup for Sorento product catalogue PDFs / '
            'catalog documents. ALWAYS pass `attachment_type_code=catalogue`: that filter '
            'is what scopes the answer to catalogue documents. Pass `attachment_ids` '
            '(canonical attachment UUIDs, csv / JSON / repeated) to read the metadata of '
            'known catalogue files.'
        ),
        "method": 'GET',
        "path": '/api/v1/resource-management/attachments/',
        "path_params": (),
        "query_params": ('page', 'limit', 'attachment_ids', 'attachment_type_code', 'sort', 'dir', 'uploaded_at_from', 'uploaded_at_to'),
        "body_params": (),
    },
    {
        "name": 'crm_resource_attachments_list',
        "description": (
            'List file attachments in the global document library. PRIMARY TOOL for PRODUCT '
            'CATALOGUE / CATALOG / MASTER CATALOGUE PDF / COMPANY BROCHURE / PRICE LIST '
            'DOCUMENT requests; for per-SKU brochures use '
            'crm_master_product_attachments_list; for the standing Stock List PDF use '
            'crm_resource_attachments_current_stock_list.\n\nWHICH FILTER TO PASS depends on '
            'what the person named.\n  • They NAMED a kind of document ("the container status '
            'list", "the catalogue") -> pass `attachment_type_code` with that name, e.g. '
            '"Container Status", case-insensitive. This is how you return THE document they '
            'asked for instead of a directory listing, so always pass it when a document '
            'class is identifiable. `attachment_type_id` takes the same thing as a UUID if '
            'you happen to hold one, but the name is preferred: no lookup, nothing to get '
            'stale. They named SEVERAL kinds of document -> pass `attachment_type_codes` '
            '(csv / JSON / repeated names) or `attachment_type_ids` (csv / JSON / repeated '
            'UUIDs); each unions with its singular twin, so a row matching ANY of the named '
            'types is returned.\n  • They named a SPECIFIC FILE you already hold the UUID for '
            '-> `attachment_ids`. Also available: `directory_id`, `uploaded_by` (canonical '
            'UUIDs).\n  • They named NO document at all -> ask which document they want '
            'instead of guessing.\nDOCUMENT TYPES: the answer holds only the document types '
            'the signed-in user may see.'
        ),
        "method": 'GET',
        "path": '/api/v1/resource-management/attachments/',
        "path_params": (),
        "query_params": ('page', 'limit', 'attachment_ids', 'sort', 'dir', 'directory_id', 'attachment_type_id', 'attachment_type_ids', 'uploaded_by', 'attachment_type_code', 'attachment_type_codes', 'uploaded_at_from', 'uploaded_at_to', 'is_deleted'),
        "body_params": (),
    },
    {
        "name": 'crm_sales_report',
        "description": (
            'DELIVERED sales (delivery orders, by DO date) for a customer, a product, or '
            'both. Returns `total` {qty, amount}, `periods[]` latest first (`grain` day when '
            'the window is at most 7 days, week Monday to Sunday when at most 31, else '
            'month; only periods with a delivery), and `options[]`, the drill-downs the '
            'reply offers. A legacy DO counts its document total once, an AutoCount DO its '
            'line totals; cancelled, deleted and replacement (REP...) DOs never count.\n\n'
            'SUBJECT: a product, a customer, or both - at least one of `product_code` / '
            '`customer_ids` / `customer_query` is REQUIRED (422 `subject_required` '
            'otherwise). `product_code` is a PREFIX, case-insensitive (at least 3 '
            'characters). `customer_query` - partial match on customer NAME only. '
            '`customer_ids` - canonical customer UUIDs (csv/JSON/repeated).\n\nFILTERS: '
            "`channel` - dealer | project (the account's market segment class; an account "
            'with no segment is in every channel; ignored when the accounts span one class). '
            '`warehouse_codes` - exact warehouse codes (csv/JSON/repeated); resolve a '
            'location TOKEN yourself first, this tool does no suffix matching. '
            '`location_token` (max 32 chars) - the raw location word, echoed back so the '
            "header reads 'IB (BRW-IB, MWH-IB)'. `date_from`/`date_to` filter on the DO "
            'date; omit both for every month. `group_by` = customer | product | '
            'delivery_order | sales_agent - adds `rows[]` ranked by amount (delivery orders '
            'latest first), top 10 with `more` the rest (`view=render` prints the drill).\n\n'
            'Use crm_outstanding_report instead for the SO-backlog / DO-pending question; '
            "this tool is for a SALES REPORT / sales performance / 'how much did X buy' "
            'question.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/sales-report',
        "path_params": (),
        "query_params": ('product_code', 'customer_query', 'customer_ids', 'channel', 'warehouse_codes', 'location_token', 'date_from', 'date_to', 'group_by'),
        "body_params": (),
    },
    {
        "name": 'crm_sla_conversation_event_logs_list',
        "description": (
            'SLA conversation event logs. FILTER BY UUID: `tracking_id`, `assigned_to_id` '
            '(canonical UUIDs). event_type, assigned_to, date_from / date_to scope the logs.'
        ),
        "method": 'GET',
        "path": '/api/v1/sla-management/conversation-sla-tracking/event-logs',
        "path_params": (),
        "query_params": ('page', 'limit', 'sort', 'dir', 'tracking_id', 'event_type', 'assigned_to', 'assigned_to_id', 'date_from', 'date_to'),
        "body_params": (),
    },
    {
        "name": 'crm_sla_conversation_tracking_dashboard',
        "description": (
            'SLA conversation-tracking dashboard metrics.'
        ),
        "method": 'GET',
        "path": '/api/v1/sla-management/conversation-sla-tracking/dashboard',
        "path_params": (),
        "query_params": (),
        "body_params": (),
    },
    {
        "name": 'crm_top_selling_report',
        "description": (
            'Top selling ITEMS (or CATEGORIES) over a date window, ranked by quantity or by '
            'amount - the same sales_order_lines source, exclusions and bucket date as '
            'crm_sales_report. Returns `rows[]` (rank, code, quantity, amount - code alone, '
            'no name, owner ruling 26 Sep), `total_count` (every ranked row, always the full '
            'count), `totals` over the whole set, the resolved `date_from`/`date_to`, '
            '`basis`, the applied `filters`, and `sales_agent_fill_rate` when a sales agent '
            'filter was used.\n\n`rank_by` - quantity | amount, REQUIRED (422 '
            '`rank_by_required`): never guess, ask the customer which one. `basis` - '
            'delivered (default: transferred to DO, capped at ordered) | ordered. `group` - '
            'item (default) | category (rank categories). `n` - how many rows, 1 to 1000 (a '
            'safety ceiling, not a product limit: 200 is fine), exactly the number the '
            "customer named (no paging, no 'more'). When the customer named no number, send "
            '`count_only=true`: it returns the full `total_count` and NO rows (a single '
            'ranked row is still returned), so you can say the count and ask how many; never '
            'omit `n` to pull every row.\n\nFILTERS (all optional, ANDed): `customer_ids` '
            '(csv/JSON/repeated UUIDs), `customer_query` (partial customer NAME, min 3 '
            'chars), `category_ids`, `sales_agent_ids` (sales_orders.sales_agent_id), '
            '`brand_ids` (products.brand_id), `channel` dealer | project, '
            '`date_from`/`date_to` on the bucket date (required_date, else order_date); both '
            'omitted = the current calendar year.\n\nDIRECTION: `direction` top (default, most '
            "sold first) | bottom (least sold first, for 'cold selling', 'least sold'). Only "
            'items with a sale in the window are ranked either way.\n\nDETAIL: `detail_code` = '
            "one row's code (a product code, or a category code under group=category) "
            'answers the detail offer: `rows`/`totals` narrow to that code and `detail` '
            'carries its `by_customer` and `by_month`, same filters and basis.\n\nThe contact '
            'needs the Sales report reveal (403 `sales_report_not_enabled`). A contact '
            'holding any active office access type is staff and sees every customer, '
            'whatever else it holds. Otherwise a contact linked to customers is forced to '
            'them and naming another is 403 `customer_not_permitted`, the same 403 any other '
            'contact gets.'
        ),
        "method": 'GET',
        "path": '/api/v1/order-management/top-selling',
        "path_params": (),
        "query_params": ('rank_by', 'basis', 'group', 'n', 'customer_ids', 'customer_query', 'category_ids', 'sales_agent_ids', 'brand_ids', 'channel', 'date_from', 'date_to', 'detail_code', 'count_only', 'direction'),
        "body_params": (),
    },
)
