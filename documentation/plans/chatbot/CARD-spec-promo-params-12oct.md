# CARD: spec + active-promotion params in the area lanes (SPEC-PROMO-PARAMS)

Status: Plan. Behaviour card posted as one crew ask; no code until it is answered. Track: full
(parser schema + prompt version change, backend route params, MCP catalog), size M.

Chatbot architecture option D (owner yes, 11 Oct, question 4 on
`ARCH-scalable-chat-11oct.html`, PR #1566; the doc numbers this work "step 3"). Add MCP params
`spec_constraints` and `has_active_promotion` inside the existing product lookup / stock / list
lanes, so "250mm floor grating got stock?" and "which basins are on promotion?" filter by spec
and by live promotion instead of failing. Owner spot-check (11 Oct): "250mm is a spec and we
should have captured that".

## 1. What exists today (read, not assumed)

### Spec vocabulary and values

- Vocabulary: `product_spec_registry` (`app/models/product_spec.py:41`), seeded in code by
  `SPEC_REGISTRY_SEED` (`app/services/product_spec_registry.py:452`). 50 keys. Per key:
  `data_type` enum | numeric | boolean (`product_spec.py:51`), `unit` (`:52`),
  `allowed_values` + staff `user_values` minus `suppressed_values`, `synonyms`,
  `match_tolerance` / `match_decay` (`:88-89`, mm default 5 / 150 at
  `product_spec_registry.py:59-62`), `is_active`.
- Numeric mm keys: `diameter`, `dim_length`, `dim_width`, `dim_height`, `depth`, `thickness`,
  `hose_length`, `trap_length` (`product_spec_registry.py:480-531, 931, 1084`). Others:
  `capacity_litre` (L), `power_hp` (hp), `capacity_oz`.
- Enum keys the asks use: `class`, `product_type` (holds `floor_grating`, `floor_trap`,
  `product_spec_registry.py:681-740`), `finish` (colour, `:596-614`), `material`, `mounting`,
  `trap_type`, `steel_grade`, `flush_type`, ...
- Per-product values: `product_specifications.values` JSONB
  (`product_spec.py:218, 232`), `{"dim_length": {"value": 250, "unit": "mm"}, "finish":
  {"value": "black"}}`, GIN-indexed. Derived offline from the description
  ("SORENTO BASIN (600MM)") by the registry's derivation rules; never read from text at query
  time (`product_spec_search.py:15-18`).
- The products table also has `dimensions_length/width/height` (`app/models/product.py:285-287`),
  already filterable on the MCP products tool as `length_min/max` etc.
  (`sorento_crm_mcp/catalog.py:64-67`).

### Spec matching code that already exists

- `membership_clause` (`app/services/product_spec_search.py:886`): ANDs keys, ORs values within
  a key; enum = case-insensitive equality or list containment; numeric = the stored JSON number
  equal to the value (`:913-921`); `brand` reads `Product.brand_id`.
- `filter_specs` (`:943`) and `search_specs` (`:1144`): the did-you-mean ranker. By design
  "every spec is a boost, never a WHERE" (`:4-13`).
- `resolve_product_set` (`app/services/product_predicate_service.py:849`): the described set
  intersected with `REQUIRE_LEGS` (`:495`), including `promotion` (`_leg_promotion`, `:383`)
  and `stock` (`_leg_stock`, `:416`). Reached through `POST /system/references/resolve`
  (`app/api/v1/system/references.py:3158, 3408`) and in-process `turn_runtime.page_the_set`
  (`app/services/chatbot/turn_runtime.py:4142`). That route is NOT an MCP tool.

### Active promotion

- `promotions` (`app/models/marketing.py:92`): `is_active` (`:111`), nullable `start_date` /
  `end_date` (`:109-110`), `access_levels` JSONB (`:112`). No status enum. Products link
  through `promotion_products` (`:174`).
- One rule: `promotion_window.is_live` / `live_clause` (`app/services/promotion_window.py:38,
  60`): flag on, start blank or <= today, end blank or >= today. `_leg_promotion` applies the
  same rule plus the caller's tier (`product_predicate_service.py:400-412`).
- Serving policy (`app/services/promotion_serving.py:96`) additionally serves the latest
  EXPIRED row per type when the type allows it; the MCP promotion tools force it on
  (`sorento_crm_mcp/server.py:165-173`).
- Known drift, not this lane's: `marketing_service._build_promotions_ordered_query`
  (`:435-447`) and `_has_active_promo` (`:844-868`) still carry the old "both dates or none"
  clause, and several paths take `today` from UTC (`marketing_service.py:435, 739, 1514`).

### Parser and lanes

- The parser already extracts specs: `entities[].spec_key` / `spec_value`
  (`app/services/chatbot/head/parser.py:178-179`), prompt `SPECIFICATION_ADDENDUM`
  (`app/services/chatbot_parser_prompt.py:486`), vocabulary rendered from the registry
  (`specification_lines`, `:1170`; a numeric key is "a number in <unit>"). Grounded against the
  registry by `head/grounding.py:671`.
- There is no parser key for "the products must be on promotion"; promotion today is
  `intent_hint check_promotion` / `domain_hint promotion` -> the `promotion` leg
  (`lanes/business/predicate.py:29, 143`) or the promotions list tool.
- Lane to tool: `turn/policy_rows.py` (`master_products` -> `crm_master_products_list` `:56-75`;
  `inventory` -> `crm_inventory_stock_balance_list` `:132-152`). Params are built in
  `fetch.entity_ids_transformer` (`lanes/business/fetch.py:650-1293`) from
  `_fetch_semantic_input` (`lanes/business/__init__.py:925-1027`).
- MCP: `crm_master_products_list` (`sorento_crm_mcp/catalog.py:58`, route
  `GET /master-data/products`, `ProductService._build_list_query`
  `app/services/product_service.py:303`) and `crm_inventory_stock_balance_list` (`:462`, route
  `GET /inventory/stock/balance`, `StockService.list_stock`
  `app/services/inventory_service.py:640`). Neither takes a spec or promotion filter. A param
  not declared in a ToolSpec's tuple never reaches the backend (`server.py:1633`); object
  params go in `TOOL_OBJECT_QUERY_PARAMS` (`server.py:137`).

## 2. Proposed behaviour

### MCP params (on `crm_master_products_list` and `crm_inventory_stock_balance_list`)

- `spec_constraints`: JSON list of `{key, op, value}`.
  - `key` must be an active registry key; an unknown key is a 422 naming it (never silently
    ignored).
  - `op`: `eq` (default) | `between` (`value` = `[lo, hi]`) | `gte` | `lte`. Numeric `eq` is
    exact; the parser emits `between` only for a hedged size ("around 250"), as the products
    tool already instructs for dimensions (`catalog.py:68-69`).
  - `value`: an enum value (matched case-insensitively against stored values, synonyms
    resolved through the registry), a number in the KEY'S OWN unit (the registry `unit`; the
    parser is told the unit per key already, so code does no text-side unit parsing), or a
    boolean.
  - Constraints AND together; this is a WHERE on the route, unlike the ranker.
- `has_active_promotion`: `true` keeps only products in a live promotion visible to the asking
  contact's tier (same clause as `_leg_promotion`, made public and reused, not copied).
- Zero rows with `spec_constraints` set: the response carries `spec_alternatives`, the values
  of the most specific numeric/enum key that DO exist in the set without that constraint (the
  same idea as `_near_miss`, `product_predicate_service.py:694`), so the reply is never a bare
  "none".

### Chatbot mapping (code maps parser params; no text matching)

- Grounded `specification` entities (spec_key + non-null spec_value) of a described ask (no
  product code resolved) become `spec_constraints` on the two tools; the category / product
  type entity becomes `{key: class|product_type, op: eq, value}`.
- A specification entity with spec_value null keeps today's clarify ("I don't know 'f trap';
  I know P trap and S trap", `lanes/business/answer.py:2999`).
- `has_active_promotion` comes from a new parser key (question 4).
- MCP stays the executor: the lane calls the tool; it does not pre-resolve ids for these asks.

## 3. Example asks (seeded fixtures in tests; real-data replies come from the crew gate)

| Ask | Parser (mocked) | MCP call | Expected reply |
| --- | --- | --- | --- |
| "250mm floor grating got stock?" | product_type floor_grating; spec size 250 mm, axis not said | data holds 250 only on `dim_length` among floor gratings: `crm_inventory_stock_balance_list(spec_constraints=[{product_type eq floor_grating}, {dim_length eq 250}])` | stock rows for the 250mm gratings only, today's stock presenter |
| same ask, 250 on both `dim_length` and `dim_width` of different gratings | same | none yet | "Do you mean 250mm length or 250mm width?" (two quick replies) |
| "300mm floor grating got stock?", no grating carries 300 | same, 300 | `spec_constraints` as above -> 0 rows + `spec_alternatives` | "No floor grating is 300mm. They come in 100mm, 150mm and 250mm. Which one?" |
| "which basins are on promotion?" | class basin; on_promotion true | `crm_master_products_list(spec_constraints=[{class eq basin}], has_active_promotion=true)` | the basin list (counted, paged as today) |
| "black basin on promo got stock?" | class basin, finish black, on_promotion true, domain inventory | stock tool with both params | stock rows of black basins in a live promotion |

## 4. Edge cases

- A size with no axis word on a class whose products carry no numeric spec at all: clarify by
  naming what the class is described by ("Floor gratings are listed by length; which length?"),
  never a guess.
- A size in another unit ("25cm"): the parser emits 250 for an mm key (its prompt says "a number
  in mm"); a test pins the prompt line. No unit parsing in code.
- A registry key that is `is_active=false` or a suppressed value: 422 from the route / dropped
  by grounding before the call; the reply is the existing unknown-value clarify.
- A product with no `product_specifications` row: excluded by a spec filter (not recorded is
  not a match); the zero-row reply says what does exist.
- Promotion live today but tier-restricted from this dealer: excluded (SEC-S1 rule kept).
- Promotion ended yesterday but "expired but usable" under its type: excluded by
  `has_active_promotion` (question 3).
- `has_active_promotion` with no products in any live promotion: "No basins are on promotion
  right now." plus the existing promotion list offer.
- Scoped contact (`contact_id` + `space_id`): the filters run inside today's company and stock
  visibility scope, never around it.

## 5. Questions (crew ask)

1. Filter, not boost, on the MCP tools: `spec_constraints` is a hard WHERE, and a miss returns
   the values that exist. The resolver's did-you-mean ranker stays a boost. Recommendation: yes.
   The tool answers "which ones" questions, and the ranker still serves the picker.
2. A size with no axis ("250mm"): the parser emits a virtual key `size` (a number in mm) and
   code asks the DATA which mm keys of that class hold the value: one key -> use it, several ->
   ask which, none -> list the sizes that exist. Alternative: let the parser pick the axis.
   Recommendation: virtual `size` resolved by data, because the parser cannot know which axis
   the catalogue records.
3. "Active promotion" = live (flag on, inside the date window) and visible to the contact's
   tier; expired-but-usable rows of the serving policy do NOT count. Recommendation: yes. "On
   promotion" to a customer means now.
4. New parser key `on_promotion: true|null` (set only when the products asked about must be on
   promotion), so "which basins are on promotion" lists basins via the products tool, while
   "basin promotion" / "any promo?" stays on the promotions list tool. Recommendation: yes. It
   keeps the decision in the parser and needs one new prompt version.
5. Scope: this lane adds the params and maps them in the product and stock lanes only; the
   in-process `resolve_product_set` path stays as is until the eval shows the MCP path matches
   it. Recommendation: yes. Removing it now would mix a migration of working code into a
   param add.

## 6. Data (crew, 12 Oct, dev DB, counts only)

- 155 `product_specifications` rows with `product_type = floor_grating`. mm keys present:
  `dim_length` 17 rows (11, 100, 120, 130, 145, 150, 900), `dim_width` 16 (11, 68, 76, 100,
  120, 130, 145, 150), `dim_height` 1 (76); `diameter` / `depth` / `thickness` 0.
- No grating carries 250 on any key; 138 of 155 carry no size at all.
- So the spot-check ask fails on spec COVERAGE before it fails on matching. A filter alone turns
  it into an honest "no 250mm floor grating", which is wrong if the catalogue does sell one.
  The size derivation (`product_spec_registry.py:303-329`: the product column, the size block,
  a lone "NNNMM" not below 10) evidently does not read how gratings state their size. The
  value 11 is a likely misread.
- Follow-up request: the description SHAPES of gratings with no size (masked), to see whether a
  derivation rule (offline, product data, not user text) closes the gap, and whether that is
  this lane or a separate spec-coverage lane.
- Reply rule while coverage is thin: when the class has products with NO value for the asked
  key, the zero-match reply must not claim "none exists". It says the size is not recorded for
  most of them and offers the class's list (or its stock), never a bare no.

## 7. Data requests (first round)

`crew-data-request:` for (a) the 11 Oct spot-check turn ("250mm floor grating") trace: parser
output and tool calls, and (b) per numeric mm key, how many `floor_grating` products carry a
value and the distinct values (counts only). Feeds question 2 and the example replies.
