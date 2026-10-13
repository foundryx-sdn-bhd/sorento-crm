# CARD: spec + active-promotion params in the area lanes (SPEC-PROMO-PARAMS)

Status: Review. Step-1 hand test round fixed (section 10b); owner size ruling built (section 10c); owner promotion hand test ruling built (section 10d, supersedes the promotion parts of sections 9 and 10). Track: full (sections 2-5 are the pre-answer proposal; sections 8-10c are what shipped).
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
| "which basins are on promotion?" (SUPERSEDED by section 10) | class basin; on_promotion true | `crm_master_products_list(spec_constraints=[{class eq basin}], has_active_promotion=true)` | the basin list (counted, paged as today) |
| "black basin on promo got stock?" (SUPERSEDED by section 10) | class basin, finish black, on_promotion true, domain inventory | stock tool with both params | stock rows of black basins in a live promotion |

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

## 8. Owner answers (12 Oct)

1. Q1 yes, not restrictive: the spec filter applies whenever the parser gives a spec constraint, in any
   phrasing ("which ones", "got 600mm basin?", "basin 600mm price"). A miss lists the values that exist.
2. Q2 yes: virtual `size` key, axis resolved by data (one key: use it; several: ask which; none: list
   the sizes that exist and, when true, say how many have no size recorded).
3. Q3 yes: active = live now and visible to the contact's tier.
4. Q4 reworked: "which basins are on promotion" and "basin promotion" / "any promo?" must give ONE
   consistent answer from one path (the promotions covering basins together with the basins on them).
   No `on_promotion` split, no new parser key for it.
5. Q5 yes: product + stock lanes only; `resolve_product_set` stays.
6. Owner rule: replies state only what the data holds; no invented lines, no unconfigured fallbacks.
7. `seat_cover` registry key will be turned off (separate WC seat-cover lane); it must not reach the
   parser spec vocabulary.

## 9. Build plan after the answers (12 Oct)

Root cause of the spot-check, from the code: grounding drops a `specification` entity whose key is not
in the registry and whose raw has no key-name word (`head/grounding.py:738-751`), so "250mm" with no
axis never reaches the resolver or the tool. Slices:

1. Virtual `size` key. `specification_lines` (`chatbot_parser_prompt.py:1170`) renders one line for it
   (a number in mm when no axis word is said); grounding keeps `spec_key=size` with a numeric value;
   the route resolves the axis from data: the active numeric mm keys whose stored value equals the
   number among products matching the other constraints. One key: use it (`spec_resolved`). Several:
   no rows plus `spec_ambiguous` (the bot asks which). None: `spec_alternatives` per mm key plus
   `spec_unrecorded` (products with no mm value at all).
2. Product and stock lanes: grounded specs (plus the class / product type, resolved through registry
   data, never text matching) become `spec_constraints` on the MCP call for every described ask (no
   typed code), whatever the phrasing; the MCP call then carries no resolver `product_ids`.
3. SUPERSEDED by section 10d (13 Oct). Promotion: a promotion ask with a described subject calls `crm_marketing_promotion_products_list`
   with `spec_constraints` (the promotions covering the subject together with the products on them);
   "which basins are on promotion" and "basin promotion" give the same call. A promotion ask with no
   subject keeps `crm_marketing_promotions_list`.
4. Miss replies come from the MCP presenters and state only what the response holds.
5. Guard: an inactive registry key (`seat_cover` once turned off) is absent from the parser vocabulary
   and rejected by `spec_constraints`.

## 10. As shipped (12 Oct)

- SUPERSEDED by section 10d (13 Oct). Q4 follow-up, owner chose option (a): `crm_marketing_promotion_products_list` is the ONE promotion path
  for a promotion ask with a described subject (first turn and tier-pick turn). The route takes
  `spec_constraints`, `brand_ids`, `promotion_types` (and `access_levels`); the reply groups product
  lines under their promotion (code, name, promo price; dates when the promotion holds them); the
  stamped tier one-liner ("No <Level> promotion for <subject>.") stays on this tool; no resolver
  "Couldn't find ... escalate" after the tool has answered. "Any promo?" with no subject keeps
  `crm_marketing_promotions_list`.
- Product and stock lanes: the MCP spec filter is sent only when a size is in the ask; every other
  spec is already filtered exactly by the resolver's counted set (membership_clause), in any phrasing.
- `has_active_promotion` + `access_levels` on the products / stock tools: approved MCP params (ARCH
  option D, owner question 4 on PR #1566) for MCP / n8n callers; no chatbot consumer in this lane. A
  dealer call without `access_levels` falls back to the contact's own tier (fail closed).
- Enum values in `spec_constraints` match the stored value case-insensitively; synonyms are NOT
  resolved by the route (the chatbot sends grounded canonical values). Catalog text says so.
- Guards: company scope on every miss-detail / size read; hidden spec keys for a contact never filter
  or print; caps (16 constraints, 50 values per list, 4096 chars raw); non-finite numbers and
  wrong-typed values rejected; an inactive registry key (e.g. `seat_cover` once turned off) is absent
  from the parser vocabulary and rejected by the route.

### 10b. Owner hand test step 1 round (12 Oct)

Hand test step 1 failed on 2c6ec044 (parser v86, crew trace e2a6498a): "600mm basin got stock?"
listed every basin. Three causes, each fixed red-first:

- The parser put the size on the category entity (`spec_key dim_width`, `spec_value 600`) and
  nothing read it. Grounding now splits a spec pair on a category entity into its own
  specification entity; a millimetre number there is the virtual `size` key.
- A millimetre spec whose own words name no axis ("600mm"; the registry's `ground_words` finds no
  axis in it) is `size`, whatever axis the parser guessed. An axis word keeps the parser's key.
- A size ask sends the resolver no `require` leg: its counted set never sees the size, so its
  count, header and "no stock matched these" described a different set.

The v84-v86 parser texts carry no `{{specs}}`, so the size line never reached the parser on that
line. `chatbot_prompt_vars.publish_specs_block` (`scripts/publish_parser_specs_block.py`)
publishes the production text word for word with the block as a new unlabelled version; the owner
promotes it (PR release post-steps).

The axis pick this round raised was closed by the owner ruling in 10c (no axis question).

### 10c. Owner size ruling (12 Oct)

Ruling: never ask Length or Width. A bare millimetre size matches any axis; an axis word narrows
to that axis. Reply with a few matches, then offer more; the parser marks the follow-up.

- `product_spec_filter.resolve_size`: several axes holding the value become one `any_of`
  constraint (OR across the axes); `spec_ambiguous` is gone. One axis rewrites onto its key.
- Grounding keeps the parser's axis key only when an axis word is in the entity's raw or the
  message (`_axis_said`, the registry's `ground_words`); otherwise the number is `size`. A spec
  pair the parser put on a product hint splits off the same way, and a product entity that is
  only a measure is dropped.
- Paging reuses the carried set page (`focus.set_page`, `turn/apply.py`, runner in
  `turn_runtime.make_tool_runner`) under a `spec_page` key: page size 5, the routes state
  `spec_page {page, limit, total}`, the lane adds "Reply to see more." while more remain. The
  next turn continues only when the parser sets `continuation: true` (rule added to the parser
  prompt; no text matching in code), with the same constraints and the next page.
- `publish_specs_block(from_version=...)` handles the three prompt shapes: `{{specs}}` present;
  prod v52 policy-block markers with static Specification lines (replaced by `{{specs}}`);
  no markers (dev v52, block appended). Release post-step: `--from-version 52`.

Live parse on the v52 line (prod shape + publisher, gpt-5.4-mini, 7 calls): "600mm basin got
stock?" lists 5 of 7 with the offer; "more", "show more", "lagi", "ada lagi?" and a Chinese
variant each return page 2 in the customer's language; "600mm length basin" returns the length
basins only.

## 10d. Owner promotion hand test ruling (13 Oct)

Hand test FAIL on "any 600mm basin promo": the reply listed per-product price lines. Ruling: a
promotion ask reuses the promotion-list lane; a size or spec only RESOLVES the products, and the
selling price is never shown in the chatbot.

What ships instead of the promotion parts of sections 9 and 10:

- Every promotion ask calls `crm_marketing_promotions_list`, as on main. The promotion-products
  route, service, schema, MCP ToolSpec and presenter are main's; the grouped price lines, group
  names and the one-line renderer are removed.
- A size in a promotion ask sends `spec_constraints` on that same call. The route keeps only
  promotions with a linked product holding the size on any mm axis the contact may see (hidden
  spec keys are passed through to `resolve_size`); a size no product holds returns no rows, and
  the inactive fallback is off for a spec-filtered ask.
- A class-only or code promotion ask carries no `spec_constraints` and replies exactly as on main,
  including the "No <Level> promotion for <subject>." tier line and its miss handling.
- Parity test (`tests/chatbot/test_promo_spec_parity.py`): the reply body to "any 600mm basin
  promo" equals the reply body to "promotion for <the 600 mm basin code>".
