# PLAN: generic find (one MCP read tool for products + filter registry)

Status: Review (lane CHAT-FIND, PR #1601; round 3 from the crew audit + eval gate of 13 Oct built red-first,
see section 2.7; reviewer READY on rounds 1-2, security READY). Open: crew re-gate; filters 2 and 3 wait on #1582.
Track: standard (new MCP tool surface, so security review runs; no migration, no FE change).

Architecture: option D of `documentation/plans/chatbot/ARCH-scalable-chat-11oct.html` (PR #1566),
milestone "generic find", rule "reads generic, actions specific". Card:
`CARD-chat-generic-find-12oct.md`. Data: `product_compatibility` (PR #1590, on main as be8835c1).

## 1. Journey

A dealer or staff member asks "what seat cover fits SRTWC8383-RL-MBL" or "which WC fits
SRTSCBD823-MBL", in English, Malay, Chinese or a mix. The bot answers with the linked codes, each
with its stock, one fact per line, from the links on record only.

The point of the lane is the shape, not the seat cover: ONE generic read tool, `crm_find`, whose
filters live in a registry. A new question type = one registry row (key, type, source query,
parser vocabulary line, presenter lines, translations) + eval cases. No prompt surgery (the
vocabulary reaches the parser through a live `{{find_filters}}` variable), no new tool, no new
domain, no new parser key.

## 2. Contract

### 2.1 Registry (`app/services/product_find.py`)

```
FindFilter(key, type, vocabulary, labels, run)
  key        "fits"                  # what the parser emits and the route takes
  type       "product_ref"           # value = resolved product ids (the resolver fills them)
  vocabulary one line for the parser policy block (what the filter means, when to emit it)
  labels     English presenter lines -> {"ms", "zh"} (merged into label_catalog.LABELS)
  run        (db, product_ids) -> list[group]
FILTERS: dict[str, FindFilter]       # {"fits": ...}
```

`run_find(db, filters, *, contact_id, space_id) -> dict` validates and runs the filters, then
attaches stock. `parser_vocabulary(db=None) -> str` renders the rule plus one line per filter.

`fits` (card Q1 a): for each named product (route input order, de-duplicated), its links in
`product_compatibility` read through `product_compatibility_service.covers_for` / `wcs_for`
(company + brand scope apply through the ORM listener). Covers on record -> head
`Seat cover for {code}:`; else WCs on record -> head `{code} fits these WCs:`; else one line
`No seat cover is on record for {code}.`, or `No WC is on record for {code}.` when the data calls the
product a seat cover (round 3; card Q3 a; a product with no link has no known
role, so the line names neither as fact). A named product the caller cannot see is skipped.

### 2.2 Route `GET /api/v1/master-data/products/find`

- Auth: `require_permission_with_api_key("master_data.products.view")`; module guard `product`
  (the master_data router's own).
- Query: `filters` = JSON list of `{key, product_ids?: [uuid], value?}`; `contact_id`,
  `space_id` (both or neither; contact without space = empty answer, as stock does).
- 422 `unknown_filter` (key not in FILTERS), 422 `filters_required` (none / not a list),
  422 `product_ids_required` (product_ref filter without ids), 400 bad uuid, caps: 8 filters,
  10 product ids per filter, 4096 chars of `filters` (422 `too_many`).
- 200 body:
```
{"groups": [{"filter": "fits", "anchor": {"product_id", "product_code"},
             "relation": "covers" | "wcs" | null, "head": "Seat cover for X:",
             "rows": [{"product_id", "product_code", "product_name", "is_discontinued",
                       "stock": {"mode": "detailed"|"compact"|"availability"|"none",
                                 "on_hand": int|null, "verdict": {...}|null}}],
             "more": int}],
 "stock_visibility": {"mode": ...} | null}
```
  Rows per group capped at 20, `more` = the rest (presenter says `Showing 20 of N.`).
- Stock (card Q2 a): ONE `StockService.list_stock(product_ids=<all row ids>, contact_id,
  space_id, requested_qty=1, limit=5000)` call. `detailed`: sum of `quantity_on_hand` of the
  returned rows per product; `compact`: `stock_summary[].total_on_hand`; `availability`: the
  product's `stock_availability` entry (its `branch`, `eta`, `available_qty`), never a number of
  ours; no contact (staff/n8n): no policy, summed rows as detailed. Unresolvable contact: the
  route answers no groups (fail closed, like stock).

### 2.3 MCP

- `ToolSpec("crm_find", path "/api/v1/master-data/products/find", query_params ("filters",
  "contact_id", "space_id"), domain "products", escalation_team "sales")`.
- `filters` is an object query param (`list[dict] | str`), JSON-encoded on the wire (the same
  `_OBJECT_UNIONS` / `_normalize_query_value` change as PR #1582, identical hunks).
- Presenter `_find_envelope` (PRESENTER_TOOLS): `{"response": text, "has_result": bool,
  "result_type": "find"}`. Text per group: head line; per row `Code: <code>`, then
  `Stock: <value>`, then the product replies' marker `⚠️  *(PRODUCT DISCONTINUED)*` when
  discontinued (card Q4 a, round 3); blank line between
  rows and groups; `Showing 20 of N.` when `more`. Stock value: detailed/compact/none -> the
  number; availability -> `_verdict_tail(entry)` (the stock tool's own 1-unit verdict wording).
  Never " · ".

### 2.4 Parser

- New strict top-level key `find`: `{"filters": [{"key": string, "value": string|null}]}` or
  null. In `required`, in `TOLERATED_ABSENT` (no recorded emission carries it).
- New prompt variable `find_filters` (`chatbot_prompt_vars.VARIABLES`, code list, no table):
  the FIND rule + one vocabulary line per registry row. Rule text (code constant): emit `find`
  only when the message asks which products stand in a listed relation to a named product (or
  match a listed attribute), never for facts about the named product itself; key from the list
  only; the named product stays an ordinary product entity; domain_hint master_products; when
  no product is named the entity list is empty (the lane asks).
- `publish_find_block(session, *, from_version)` (`app/services/chatbot_find_prompt.py`) +
  `scripts/publish_parser_find_block.py`: copies the source version word for word and adds
  `{{find_filters}}` inside the policy markers (before the end marker), or appends a marker pair
  when the text has none; new UNLABELLED version; idempotent; never moves a label.

### 2.5 Lane

- `run_fetch` override (`lanes/business/__init__.py`, beside the low-stock override): when the
  parse carries `find.filters` with a key in the registry -> tool `crm_find`. Product refs = the
  turn's resolved product entities (uuid). None resolved and none unresolved -> fixed reply
  `Which product code? For example SRTWC8383-RL-MBL.` (card rule 6). Unknown keys dropped.
- `output_structurer` -> `_find_output`: the presenter's `response` verbatim, localized with
  `Localizer.lines`, `own_header` so the generic search-scope header does not print.
- Allow-list: `crm_find` in `contracts.UNDOMAINED_CHATBOT_TOOLS` (reached by override, like the
  helpers), `BRAND_SCOPE_TREATMENT["crm_find"] = "filtered"`, `mcp_tool_domains`.
- `label_catalog.LABELS` gains the registry's labels (`Code`, `Stock`, `Status`, `Discontinued`,
  heads with `{code}`, the ask line) in ms and zh.

## 2.6 Review round 1 decisions

- Brand scope rests on the route (ORM brand predicate on every Product read, anchors and links
  alike), pinned by `tests/test_product_find_scope.py` through the real resolver. The chatbot's
  `brand_guard` cannot prune a text reply, the same as `crm_report_ask` and the outstanding
  report (security L2, accepted).
- Stock is printed only when the caller may use the `inventory` module
  (`guards.module_allowed_with_api_key`, security L1); `contact_id` / `space_id` are stripped as
  the scope resolver strips them (L3); a repeated filter key is read once.
- The lane's subject: the products this message named (codes compared by letters and digits
  only), else every resolved product; a product entity the parser carried back ("fits it?") is
  the subject; no product entity at all asks for the code.
- No group (nothing visible, or the contact resolved to nobody): `I have nothing on record for
  that product.`, localized; never "try again".

## 2.7 Round 3 (crew audit + eval gate, 13 Oct)

Crew live check of 3df87cee on v88 (v52 + find block), 19 turns, and eval gate v65 (no touched
topic worse, compat 7/8). Each fix red-first:

- Typed filter values: a registry row declares `products` and/or `value=ValueSpec(code | number
  + units | enum values)`, plus its own `ask`; the route coerces and refuses (`bad_value`,
  `value_required`), the runner gets one `FilterArg(product_ids, value, unit)`, the parser
  emits `value` + `unit`. #1582's spec / promotion rows move in with no code branch
  (`tests/test_product_find_values.py`).
- chatfind-07 (dealer "seat cover for SRTWC8088" answered about SRTWC8088-SC):
  `narrow_code_tokens` swapped the typed code for its -SC sibling from the words "seat cover".
  A find turn's resolve body carries `keep_typed_codes` and the route keeps typed codes as
  typed; every other ask keeps PR #833's word narrowing. `named_this_turn` takes the exact code
  first (`tests/chatbot/test_find_exact_code.py`).
- The stray "找不到 马桶盖。": on a find turn only an unplaced product code is a miss line; a
  type word the parser also emitted is dropped from the envelope's unplaced words
  (`turn_runtime`, beside the PO/SO word drops).
- No-link wording: `No seat cover is on record for <code>.`; `No WC is on record for <code>.` when
  the data calls it a seat cover (an `-SC` code, or derived `product_type` toilet_seat).
- Discontinued: the existing marker, never a `Status:` line.
- Past ETA (shared with the dealer stock reply; crew answer (b) then (a), 13 Oct): the date read
  is the shipment's promised date, its revised `eta_delay_date` when it has one, else its ETA
  (`eta_policy._told`'s rule; R5 amended: a delayed shipment still coming is never "no
  incoming"), and `earliest_packing_list_shipment(not_before=)` drops a promised date before
  today (Malaysia time) on the dealer read, so a past date is never told; the next dated
  shipment answers, else the existing no-incoming line (R10 kept: no "to be confirmed").
- File-ask misreads ("seat cover SRTWC8122", "SRTWC8383-RL-MBL 配什么马桶盖"): the `fits` row's
  vocabulary names the shape (the other side beside a code is a fit ask, never a file ask without a
  file word). Live parse on the prod-shaped snapshot: 19 cases x 3 runs = 57/57, incl. two file
  asks that must stay file asks.
- p95 (gate 14.2 s vs 11.8 s): measured parser time with and without the block on 8 non-find and
  find messages (gpt-5.4-mini, prod-shaped snapshot): +339 uncached prompt tokens, p50 3.00 s ->
  3.15 s; the block sits after the 33,024-token cached prefix. Not a 2.4 s shift: the gap is
  elsewhere (crew: per-case latency on identical non-find cases, both runs).

## 3. Filters 2 and 3 (card Q5 a)

After PR #1582 merges: `spec_constraints` (type `spec`) and `has_active_promotion` (type
`flag`) register in FILTERS, delegating to #1582's `product_spec_filter.product_filter_clause`
/ `promoted_clause`; the old tools keep their params until `crm_find` reaches eval parity
(ARCH exit ramps). Coordinated with lane SPEC-PROMO-PARAMS on PR #1582.

## 4. Test list (red first)

Backend (`tests/test_product_find.py`, Postgres scratch schema):
- F1 fits on a WC lists its covers, head `Seat cover for <code>:`; on a cover lists its WCs,
  head `<code> fits these WCs:`; no link -> the none line, no rows.
- F2 two named products -> two groups in input order; duplicate id once.
- F3 company scope: a link/product of another company never appears; anchor of another company
  skipped.
- F4 validation: unknown key 422 `unknown_filter`; missing filters 422; product_ref without ids
  422; bad uuid 400; caps 422.
- F5 stock per mode: detailed sums visible rows; compact total; availability entry with branch
  and no on-hand number in the body; contact without space -> empty.
- F6 discontinued row carries `is_discontinued: true`; 21 links -> 20 rows + `more: 1`.
- F7 permission: no products.view -> 403.
- F8 registry: every filter has key/type/vocabulary/labels with ms+zh for every English label;
  `parser_vocabulary` holds every filter's line.

Parser / prompt (`tests/chatbot/test_find_parser_contract.py`):
- P1 schema declares `find` (required, strict object), tolerated absent.
- P2 `find_filters` variable renders the rule + `fits` line.
- P3 `publish_find_block` from a version with markers inserts `{{find_filters}}` before the end
  marker, every other byte kept; without markers appends a pair; unlabelled; idempotent; never
  touches labels; unknown version -> None.

Lane (`tests/chatbot/test_find_lane.py`):
- L1 parse with `find.filters=[fits]` + resolved product -> `crm_find` called with
  `filters=[{"key":"fits","product_ids":[id]}]` + contact params, reply = presenter text.
- L2 no product entity -> the ask line, no tool call.
- L3 unknown filter key only -> today's path (no `crm_find`).
- L4 `crm_find` is in `CHATBOT_READ_ONLY_TOOLS`, read-only pool test green, brand treatment set.
- L5 localized: ms localizer turns `Stock:` / head into Malay, codes untouched.

MCP (`sorento_crm_mcp/tests/test_find_tool.py`):
- M1 catalog entry: GET, path, query params; `filters` list-of-dict JSON-encoded on the wire.
- M2 presenter: head, `Code:` / `Status:` / `Stock:` lines, availability verdict wording, none
  line, `Showing 20 of N.`, never " · ".

Live parse (opt-in `live_external`, `tests/chatbot/test_find_live_parser.py`): the card's 5
examples + "which seat cover fits my WC?" on the published line -> `find.filters[0].key ==
"fits"` and the code as a product entity; a plain "SRTWC8383-RL-MBL stock?" -> no `find`.

## 5. Release post-steps

1. After deploy: `venv/bin/python -m scripts.publish_parser_find_block --from-version <N>`
   where N is the production line (v52, or the version #1582's publisher printed if that ran
   first). Prints the new unlabelled version.
2. Owner promotes it on the Prompts page. Until then `find` is never emitted and nothing changes.
