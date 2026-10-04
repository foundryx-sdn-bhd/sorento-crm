# UAC - Attribute-first asks ("which products have X")

Plan: `PLAN-attribute-first-asks.md`. Numbering: AC-13xx. Each criterion names its evidence
(pytest / console turn on the restored prod copy). "Contact" = a Respond.io contact through
`/api/v1/external/chat/turn`. "Prod copy" = local `sorento_ai_automation` restored from the
2026-09-10 dump (outbound credentials scrubbed). Numbers quoted below were measured on it and
are the acceptance values unless the copy is refreshed, in which case re-measure and update here.

Decisions this UAC encodes (taken by the owner on the lavish review, 10 Sep 2026): "has cert"
means the certificate register; brand scopes the set; all five predicates in one lane; a set
answer shows 5 products WITH their files and a header carrying the full count, "more" pages on;
default validity is any active certificate with expired rows flagged as the render does today;
"watermark" is not mapped to a scheme and must clarify. No parser prompt change. No new MCP
tool. No new reply format.

## A. Honest zero in the described set [BE]

- AC-1301 `filter_specs(free_terms=["water tap"])` returns `clause=None` AND
  `unrecognized_terms=["water tap"]`. Today it returns `clause=None` with an empty list. Same
  for "water basin" and any term whose content words are all in the vocabulary but none names a
  class, a product_type or a brand. Evidence: pytest `tests/test_product_spec_search.py`.
- AC-1302 `resolve_product_set(require={"certificate": True}, free_terms=["water tap"])`
  returns `qualifying_total=0` and `unrecognized_terms=["water tap"]`, never a silent zero.
  Evidence: pytest `tests/test_product_predicate_service.py`.

## B. Predicate derived from what the parser already emits [BE]

- AC-1303 A pure function `derive_require(parser_output) -> dict | None` in the lane maps, with
  no message-text matching beyond the existing `_CERT_RE` on the attachment_type entity raw:
  `check_stock` → `{"stock": true}`; `check_incoming` → `{"incoming": true}`;
  `check_promotion` → `{"promotion": true}`; `check_product_attachment` with an
  `attachment_type` entity whose raw matches `_CERT_RE` → `{"certificate": true}` when the raw
  is only the cert word ("cert", "certificate", "sijil"), else `{"certificate": {"scheme":
  <raw minus the cert word>}}` ("pps cert" → scheme "pps", "watermark certificate" → scheme
  "watermark"); the function stays pure, the resolver normalises the scheme word through the
  `certificate_scheme` lookup set (AC-1313); `check_product_attachment` with any other
  attachment_type raw → `{"attachment_type": <raw>}`; every other intent → `None`.
  Evidence: pytest, one case per row, `tests/test_chatbot_lane_require.py` (new).
- AC-1304 `resolve_entity_body` includes `require` exactly when `derive_require` is not None,
  and includes `predicate_words`: the raw of every attachment_type entity plus the intent's
  own word, so the resolver can subtract them from the described set. Every other key of the
  body is byte-identical to today (existing body snapshot tests stay green). Evidence: pytest
  on the body builder, before/after dict diff.

## C. The resolver runs HAS only when LOOKUP found no exact code [BE]

- AC-1305 With `require` present and a CODE-SHAPED product token that resolved to any product
  match (the customer typed a code or a code prefix such as "srtwc286"), the response is byte-identical to the same
  request without `require`. With only WORD tokens ("bidet" → 3 name matches, "sorento" → an exact
  brand-name hit, "tap" as a `category` entity) HAS runs whatever tier they resolved at. Evidence: pytest `tests/test_resolve_predicate.py`,
  deep-equal on the head_code case, predicate block present on the substring case.
- AC-1306 With `require` present and no exact code, the resolver builds the described set from
  the union of: product ids LOOKUP matched by name or code prefix for the caller's product
  tokens; `class` bindings; `product_type` bindings; `brand` bindings derived from `query` by
  `derive_search_inputs` after removing `predicate_words`. "which sorento bidet has cert" yields
  a set of exactly the 3 BIDET products scoped to brand Sorento. Evidence: pytest on the prod
  copy seed shape (3 products named BIDET, one with brand Sorento).
- AC-1307 `filter_specs` membership accepts `product_type` and `brand` entries beside `class`
  (today class only). `resolve_product_set(require={"promotion": True},
  specs=[{"key":"product_type","value":"shower_set"}])` counts only shower sets. Evidence:
  pytest.
- AC-1308 `resolve_product_set` accepts `product_ids` (the LOOKUP matches) and intersects them
  with the legs; when both `product_ids` and class/type/brand bindings are present the set is
  the UNION of the two descriptions, then intersected with the legs. Evidence: pytest, three
  cases (ids only, bindings only, both).
- AC-1309 Qualifying ids land in `resolutions[].matches` with `entity_type="product"` and
  `match_tier="spec_search"`, plus `result["predicate"]` = `{require, qualifying_total,
  truncated, unrecognized_terms}`. Evidence: pytest asserting the shape the fetch step reads.
- AC-1310 Company scope holds per leg, INSIDE the leg: with the product in the caller's company
  and the child row (stock, attachment link, shipment line, promotion link) in company B, the
  product does not qualify; with the child in the caller's company on a company-B product, it does
  not qualify either. Evidence: pytest, both directions for every leg including `incoming`; a leg
  rewritten as unscoped SQL must turn these red (reviewer kill test).
- AC-1337 **Amended (owner ruling R39, 26 Sep 2026 01:55Z (no paging)):** the `is_more_reply` half is RETIRED with paging (the function is deleted; `test_the_paging_vocabulary_is_gone` pins its absence). The `set_noun_for` and cert-regex halves stand; `answer.SET_ID_CAP` (renamed from `SET_PAGE_ID_CAP`, reviewer N4) is still pinned equal to the resolver's copy. Original text: `is_more_reply` accepts only the fixed paging phrases (more, next, lagi, more please,
  show more, next 5, next five, lagi 5, "more <number>"); "no more" and "next week?" are not pages.
  `set_noun_for` pluralises "Bathroom Accessory" as "bathroom accessories" and the header's
  singular is the class label itself. `answer.SET_PAGE_ID_CAP` and the resolver's copy are pinned
  equal by a test, as are the two cert regexes. Evidence: pytest.

## D. Legs and aliases [BE]

- AC-1311 New `incoming` leg: EXISTS `inbound_shipment_lines` L JOIN `inbound_shipments` S
  (scoped) WHERE L.product_id = P.id AND COALESCE(L.quantity_shipped,0) minus
  COALESCE(L.quantity_received,0) > 0 AND S.actual_arrival_date IS NULL. ORM only. On the prod
  copy `require={"incoming": true}` with no terms counts 624 distinct products (853 lines).
  Evidence: pytest with a seeded shipment (one open line, one fully received line, one arrived
  shipment) asserting exactly one product qualifies.
- AC-1312 `attachment_type` label resolution tries, in order: exact code or type_name (today),
  then the `attachment_type_alias` lookup set through `LookupResolverService.resolve`. "photo",
  "picture", "gambar" resolve to Product Photos; an unknown word still feeds
  `unrecognized_terms`. Evidence: pytest with the set seeded in the test, since CI has no data.
- AC-1313 `certificate.scheme` is normalised through the `certificate_scheme` lookup set before
  equality: "pps", "PPS", "span" resolve; "watermark" does NOT resolve (no option carries it)
  and the leg returns `qualifying_total=0` with `unrecognized_terms=["watermark"]` and
  `predicate.schemes_on_file` listing the register's distinct active schemes (company-scoped,
  sorted), so the reply can name them; `resolve_product_set` returns the same list as
  `schemes_on_file` on a scheme miss. Evidence: pytest.
- AC-1314 Data: a migration creates the two lookup sets `certificate_scheme` and
  `attachment_type_alias` with NO options (the owner enters options and keywords on System >
  Lookup Sets). With an empty or absent set, a scheme word or alias word is reported in
  `unrecognized_terms` and the request never fails. Evidence: alembic upgrade on an empty
  scratch DB creates the two sets; pytest for the empty-set and missing-set paths.
- AC-1327 (the shipped header for "which sorento bidet has cert" reads "1 tap has certificates.":
  the class label wins over the product_type binding, accepted 11 Sep) When HAS ran, `resolutions` and `intersection` carry no product matches for WORD tokens
  other than the single spec_search resolution; non-product resolutions (attachment_type, brand,
  customer) are untouched. The lane's reply never prints a "Found:" enumeration of the qualifying
  codes; the set header stands in its place. Evidence: pytest on the resolver response shape and a
  lane run with two forward substring product matches on the word token.
- AC-1328 `derive_require` returns `{"certificate": {"scheme": "PPS"}}` for intent
  check_product_attachment with a single attachment_type entity raw "PPS" when `user_goal` or the
  message text matches the certificate regex; `_leg_certificate` matches a scheme word by
  case-insensitive equality against the register's active schemes before consulting the lookup
  set. Evidence: pytest, both halves.
- AC-1329 An unrecognised attachment label clarifies as a DOCUMENT type: "I don't know 'photo' as a
  document type. Types I know: <product-facing AttachmentType names>."; an unrecognised scheme
  uses the AC-1321 sentence; only an unrecognised set word uses the product-type sentence, and its
  fallback list of common product types is never empty. Evidence: pytest, three cases.
- AC-1330 For a word token (not code-shaped) the raw always defines the described set, whatever it
  resolved to: "sink" resolving to customers still scopes to class Kitchen Sink. The code-shaped
  gate of AC-1305 reads both `intersection` (the lane's AND mode) and `resolutions`. `shown` in the
  header counts distinct products. Evidence: pytest on the resolver in AND mode and a stock lane
  run with multi-row products.
- AC-1331 When `predicate.qualifying_total > 0`, an unresolved word token (e.g. the parser's single
  entity "Sorento bidet") is never listed under "Couldn't find"; the set answer renders. Evidence:
  lane run with a real resolver, the folded token resolving to nothing.
- AC-1332 The HAS branch derives bindings deterministically: the model phrase reader is never
  invoked while `require` is present, and predicate_words are stripped word by word ("PPS cert"
  removes both words). Evidence: pytest with the model reader patched to fail if called, plus the
  phrase-stripping case.
- AC-1333 [security] **Amended (owner ruling R39, 26 Sep 2026 01:55Z (no paging)):** there is no "more" page. What survives is the recount behind the answer to "how many should I show?" (AC-1317a): it carries the entitlement the first answer counted under (the carry's `access_levels`, never the parser's empty list), and the carry is armed only when a set was withheld; a tier-ask turn arms nothing. Evidence: `test_counted_set_no_paging.py`, `test_counted_set_review_fixes.py::test_the_recount_after_how_many_keeps_the_dealers_stock_visibility`. Original text: A "more" page carries the contact's recomposed `access_levels` into the tool
  args (never the parser's empty list), and the set_page carry is armed only after a set answer
  actually rendered; a tier-ask turn arms nothing. Evidence: pytest, two-turn promotion set with a
  contact holding one tier, asserting the page turn's `access_levels` equal the fresh turn's; a
  tier-ask turn followed by "more" is not paged.
- AC-1334 [security] The promotion leg respects `access_levels`: a promotion the contact's tier
  cannot see never counts toward `qualifying_total` nor names its product. Evidence: pytest, two
  promotions on two products, one tier-restricted.
- AC-1335 [security] `_common_class_labels` and the nearest-label suggestions are company-scoped:
  a class label that exists only in company B never reaches a company A reply. Evidence: pytest on
  a two-company scratch schema.
- AC-1336 The set_page carry clears on any business answer that is not a page; a fresh set answer
  re-arms it. Evidence: pytest, three turns.
- AC-1338 A bare `{"certificate": true}` require whose remainder holds a word equal
  (case-insensitive) to a register scheme spelling or a `certificate_scheme` lookup keyword is
  promoted to `{"certificate": {"scheme": <register spelling>}}`, the word never reaches
  `unrecognized_terms`, and the header names the scheme ("N products have PPS certificates.").
  Evidence: pytest on the resolver with query "which item has PPS cert", predicate_words
  ["certificate"], a register cert with scheme "PPS"; a remainder word that is neither stays a set
  word (regression on AC-1320).
- AC-1339 `filter_specs` class membership includes rows whose class provenance is `category`: a
  product filed under Bathroom Accessory by its category is a member of the described set for
  "bathroom accessory". Evidence: pytest, one product with a category-sourced class row and one
  with a derived one, both members; console "which bathroom accessory has stock" answers a count
  and five products.
- AC-1340 The zero-qualifying miss copy never reads "a a match": the subject is brand + product +
  category / product_type raws, then the predicate's class labels, else the sentence is "Couldn't
  find any product with <predicate>." Evidence: pytest on the rendered text for a category-only
  entity turn.
- AC-1341 [security] The certificate leg is company-scoped in both directions: a certificate
  stamped to company B and linked to company A's product does not count under A's scope, and a
  certificate with NULL company (shared) still counts. Evidence: pytest, both directions plus the
  shared arm, mirroring the four REV-B1 tests.
- AC-1342 [security] `_access_level_codes` accepts a `contact_access_types.code` and a bare tier
  token: `["dealer"]` selects every code that is `dealer` or ends with `_dealer`; a name still
  translates as before; an unknown value still yields the empty set. Evidence: pytest on the
  helper and on `resolve_product_set` with a tier-restricted promotion and `access_levels=["dealer"]`.
- AC-1343 The set_page carry survives only a page continuation: any later turn that is not a page
  (a same-domain non-page answer, a same-domain zero-qualifying clarify, a casual turn) clears it, so a bare "more" afterwards is NOT routed
  as a set page (`is_set_page_more_reply` is false and the turn takes the ordinary low-signal
  route, exactly as a "more" with no prior set does today); a rendered set answer re-arms it.
  Evidence: pytest, two three-turn sequences in the same domain, with the low_signal lane
  registered in the harness so turn 3 completes.
- AC-1344 No em-dash or en-dash in any file the lane adds or touches. Evidence:
  `scripts/git-hooks/pre-push` dash guard green on the lane's added lines.
- AC-1345 `derive_require` treats every inflection of the cert word as the bare leg: an
  attachment_type raw of "Certification", "certificates", "certs" or "certifications" (any case)
  yields `{"certificate": true}`, never a scheme; "PPS certification" still yields scheme "PPS".
  Evidence: pytest parametrize on `derive_require`.
- AC-1346 The stock leg counts only stock in active warehouses: a product whose only on-hand row
  is in a warehouse with `is_active = false` does not qualify; the same row in an active warehouse
  does. Evidence: pytest on `resolve_product_set` with two warehouses; console "which bathroom
  accessory has stock" shows five products for five ids.
- AC-1347 **Amended (owner ruling R39, 26 Sep 2026 01:55Z (no paging)):** the stopword half stands (a remainder of only these words scopes nothing and is never reported unrecognized); the console evidence "... -> 'more' answers a set header" is RETIRED, since "more" now pages nothing (AC-1317b). Original text: The paging words (more, next, lagi, please, show) are phrase stopwords: a HAS turn whose
  remainder is only such words scopes nothing and never reports them as unrecognized. Evidence:
  pytest on `filter_specs` with free term "more"; console "which tap has cert" -> "which water tap
  has cert" -> "more" answers a set header, not "I don't know 'more'".
- AC-1348 [security] `_access_level_codes` treats `%`, `_` and `\` in a value literally:
  `["%"]` and `["%dealer"]` select nothing; `["dealer"]` still selects every `_dealer` code.
  Evidence: pytest on the helper.
- AC-1349 [security] When the tier gate ran, the resolve body's `access_levels` are the recomposed
  names, not the parser tokens: a contact entitled to exactly one tier (no tier ask) gets a
  promotion count restricted to that tier; a contact with no entitlement and no stated tier keeps
  the empty list. Evidence: pytest on `resolve_entity_body` with a tier gate output, and on
  `resolve_product_set` with `["Sorento Dealer"]` counting only the dealer promotion.
- AC-1350 **Amended (owner ruling R39, 26 Sep 2026 01:55Z (no paging)):** the page-turn offset half is RETIRED (there is no offset); the single-head migration half stands. Original text: A page turn whose fetch never reached the tool leaves the carry's offset unchanged, and
  the migration test asserts the lane migration is the single head whose parent exists in the
  versions directory, not a spelled parent id. Evidence: pytest.
- AC-1351 Words the spec reader bound (`Understanding.bound_phrases`) never reach the described
  set's scope term: "check stock water closet with s trap 250mm" scopes to class Water Closet with
  `trap_type = s_trap` and `trap_length = 250` bound, and `unrecognized_terms` is empty. Evidence:
  pytest on the resolver; console turn.
- AC-1352 A spec binding with a string value is a membership filter of the described set: with
  two water closets in stock, one `trap_type = s_trap` and one `p_trap`, "which water closet with
  s trap has stock" counts 1; a numeric binding (trap_length) does not filter but ranks the
  matching product first. Evidence: pytest on `filter_specs` and `resolve_product_set`; console
  "check stock water closet with s trap 250mm" answers a count and five S-trap products, and
  "any incoming for water closet with p trap" answers a count, never the clarify.
- AC-1353 `derive_require` with intent `check_product_attachment` and no attachment_type entity
  yields `{"certificate": true}` when the user_goal or the message text carries a cert word
  ("trying to find which item has PPS cert"), and None when neither does; the resolver then
  recovers the scheme from the remainder (AC-1338), so "which item has PPS cert" answers "N
  products have PPS certificates" on every parser variant seen. Evidence: pytest parametrize;
  console case.
- AC-1354 **Amended (owner ruling R39, 26 Sep 2026 01:55Z (no paging)):** "and on 'more'" is RETIRED; the certificate ids ride the first answer and the recount after "how many". Original text: A scheme-narrowed certificate leg lists only that scheme's files: the predicate block
  carries `certificate_ids` (active certificates of the scheme linked to the qualifying products),
  the fetch passes them with the page's product ids on the first answer and on "more", and a
  product holding a PPS and a WCM certificate renders its PPS file only. A bare certificate leg
  passes no certificate ids. Evidence: pytest on the resolver block and on the fetch args (two
  turns); console "any tap has PPS cert" shows PPS files only.
- AC-1355 **RETIRED (main's turn engine re-architecture, #952, merged into this lane 26 Sep 2026):** main removed the Match line renderer entirely, so no answer carries it; the lane's four Match-line tests are retired with it and the resolver's `spec_asked` payload is no longer written (reviewer N6). Customers were seeing "_Matched on: ..._" on main's own Sep 4 and Sep 10 prod replays, so this is a product change on main, recorded here. Original text: A set answer whose products were described with spec words carries the forward path's
  Match line, rendered by the same renderer with the same wording ("Match: trap type: S-trap,
  trap length: 250 mm, class: Water Closet" or the renderer's existing format), when every shown
  product matches the bindings; a set answer with no spec words carries no Match line. Evidence:
  pytest on the rendered text for "check stock water closet with s trap 250mm"; console case.
- AC-1356 **RETIRED (same ruling as AC-1355: main removed the Match line).** Original text: The Match line renders on a set answer: the renderer accepts the set_page carry dict
  (the tail's `last_result_set` on a set answer) as an answered set, and its whole-answer check
  counts product rows only, so a set answer whose word tokens also matched promotions still carries
  the line when every shown PRODUCT matched the bindings. Evidence: pytest with the carry-shaped
  `last_result_set` and with 15 promotion matches on the category token; console "check stock
  sorento water closet with s trap 250mm" shows "_Matched on: Water Closet, trap type: ..._".
- AC-1357 The require-only arm orders candidates by the number of matched bindings, descending,
  then code: with string binding s_trap and numeric binding 250, the products matching both come
  before those matching s_trap only. Evidence: pytest on `resolve_product_set`; console turn shows
  a 250 mm S-trap product first.
- AC-1358 The R28 fallback matches whole cert words only: with no attachment raw, "certainly, send
  me the drawing for the basin" and "concert hall basin photo" stay forward (None) while "is this
  certified?" and "which item has PPS cert" yield the bare leg; the predicate word taken from the
  message is the bare word ("cert", not "cert,"). A negated cert word ("no cert needed, just the
  photo") is out of scope here (still the bare leg; the parser normally carries the photo entity on
  such a turn). Evidence: pytest parametrize.
- AC-1359 A turn whose predicate require carries the certificate leg never answers the
  attachment-type ask ("Please provide the attachment type ..."), whatever entities survived the
  head: qualifying above zero renders the set answer, zero takes the AC-1319 miss copy. Evidence:
  pytest on the lane with derived entities [] and a scheme-narrowed predicate qualifying 1; console
  "any tap has PPS cert".
- AC-1326 A resolver result carrying a `predicate` block (any `qualifying_total`, zero included)
  never enters the gate's `REQUIRE_SPECIFIC_DOMAINS` ambiguity block nor the product_attachment "subject
  product did not resolve" block: the qualifying matches pass on as entities, `gate_passed`
  is True and no "needs to be more specific" picker is built; with `qualifying_total` 0 the
  turn proceeds to the miss flow of AC-1319, never to the picker. Evidence: pytest on the gate
  with a 3-match predicate result (product raw "water tap" unresolved) asserting
  `require_specific` False, `gate_passed` True, three entities reach the fetch; the AC-1319 lane
  test covers the zero case.

## E. Reply shape: existing fetch, existing render, one header line [BE]

- AC-1315 A HAS turn feeds the qualifying ids to the SAME domain tool the forward turn uses
  (`crm_master_product_attachments_list` for cert and photo, `crm_inventory_stock_balance_list`
  for stock, `crm_incoming_stock_list` for incoming, `crm_marketing_promotion_products_list`
  for promotion) through the existing `TYPE_TO_PARAM` product → `product_ids` path. No new tool
  name appears in any trace. Evidence: pytest asserting the picked tool name per predicate.
- AC-1316 **Superseded by AC-1316a/b below (owner ruling R39, 26 Sep 2026 01:55Z (no paging)).** Original text: A HAS turn fetches the first 5 qualifying PRODUCTS (all their rows or files; the tool's
  row limit stays at its default); the render is the existing block (for
  cert: Product Code / Attachment Type / File Name / Certificate Number / Valid Until / Validity,
  files attached) preceded by one header line "<qualifying_total> <set noun> have <predicate
  noun>. Showing <n>." e.g. "1,256 taps have certificates. Showing 5." When
  `qualifying_total` is 5 or fewer the header omits "Showing". Evidence: pytest on the rendered
  text; console turn "which tap has cert" on the prod copy.
- AC-1317 **RETIRED, superseded by AC-1317a/b below (owner ruling R39, 26 Sep 2026 01:55Z (no paging)).** Original text: Replying "more" to a HAS answer returns the next 5 of the same qualifying set with
  the same header and "Showing 6 to 10"; a "more" past the end says "That was all
  <qualifying_total>." Evidence: pytest on two consecutive turns through the existing
  offer-carry state; console turn.
- AC-1316/AC-1317 amended by owner ruling R39 (26 Sep 2026, PLAN "Revive round"): no paging.
  - AC-1316a Up to 50 qualifying products (`answer.SET_LIST_MAX`): every one is listed under
    "<qualifying_total> <set noun> have <predicate noun>." with no "Showing". Evidence:
    `tests/chatbot/test_counted_set_no_paging.py`.
  - AC-1316b More than 50, no count named: the header adds "That is too many to list in one
    message. How many should I show (up to 50)? Or ask again naming a brand or size." (worded
    per AC-1317d)
    and no rows (and no files) are sent. Every leg (certificate, stock, attachment_type,
    promotion, incoming). Evidence: same file.
  - AC-1317a The answer to that question (the parser's `top_n`, no new subject) lists that many
    from the start of the SAME set, recounted under the same tier and stock visibility policy,
    headed "... Here are the first <n>."; a count above 50 lists 50; a count in the ask itself
    works the same way. Evidence: same file.
  - AC-1317b "more" / "next" / "lagi" pages nothing, and no carry survives the turn after the
    question. A recount that finds nothing never calls the tool without a product filter.
    Evidence: same file; `tests/chatbot/test_rearch_invariants.py`.
  - AC-1316c (reviewer B3, 26 Sep 2026) The header counts what the rows show: the fetch asks
    each leg's tool for its maximum row `limit` (`fetch.SET_ROW_LIMIT`), and when the tool still
    cuts rows short the header says "Here are the first <n>." over the products actually
    listed, never a bare "<total> ... have X." over fewer. Evidence:
    `tests/chatbot/test_counted_set_review_fixes.py` (B3 cases).
  - AC-1317c (reviewer S1, 26 Sep 2026) While "how many should I show?" is open, a message that
    is only a count ("10", "show 10", "the first 10", "10 please") is that count whatever the
    parser made of it (a null `top_n`, or the number read as a `reference_positions` pick);
    anything else is left to the parser. Evidence: same file (S1 cases); console case "a long
    set asks how many" in `2026-09-11-attribute-first-asks.yaml` (live parser, local pass).
  - AC-1317d (reviewer S2, 26 Sep 2026) The question offers only what is wired: "How many
    should I show (up to 50)? Or ask again naming a brand or size." A full re-ask names its
    own narrower set (AC-1327); "which brand or size should I narrow it to?" is dropped until
    a bare narrowing reply is carried against the same set. Evidence:
    `test_counted_set_no_paging.py` header tests.
- AC-1318 Validity: "has cert" counts any active register certificate; expired rows keep the
  existing "Validity: Expired" flag in the block. Evidence: pytest on a product whose only
  certificate is expired (counted, flagged).

## F. Miss: the existing did-you-mean flow, with the set named [BE]

- AC-1319 **Amended (reviewer B2, 26 Sep 2026):** the did-you-mean list over the class word's
  own forward code matches is dropped (those codes are the ones named as checked; a class word
  is not a typo), and the zero set never fetches. Evidence: whole turn
  `test_counted_set_review_fixes.py::test_an_honest_zero_names_the_set_and_fetches_nothing`.
  Original text: `qualifying_total=0` with an empty `unrecognized_terms` enters the existing miss flow
  (`DOMAIN_PROBE` for the domain) and the "Couldn't find" sentence names the described set and
  predicate: "Couldn't find a Sorento bidet with a certificate (checked ACC- BIDET, CABANA
  BIDET, SRT-BIDET)." followed by today's did-you-mean list and escalate offer. Evidence:
  pytest on the rendered text; console turn "which sorento bidet has cert".
- AC-1320 `qualifying_total=0` with a non-empty `unrecognized_terms` clarifies the term and
  never says "none": "I don't know 'water tap' as a product type. Did you mean tap, basin
  tap, shower tap?" where the suggestions come from the existing class / product_type
  vocabulary nearest-match. Evidence: pytest; console turn "which water tap has cert".
- AC-1321 **Amended (reviewer B1/B2, 26 Sep 2026):** holds as a whole turn (no fetch, no
  did-you-mean over it), and a cert PROPERTY word ("valid", "validity", "expiry", "no",
  "number") is never read as a scheme: "valid cert" is the bare leg. Evidence:
  `test_counted_set_review_fixes.py` (B1, B2 cases). Original text: A scheme miss names the schemes: "The register has no watermark certificates.
  Schemes on file: SPAN, PPS, WCM, IKRAM ..." Evidence: pytest.

## G. Invariants [BE]

- AC-1322 Every existing lane test, world replay and resolver snapshot stays green: a turn whose
  intent has no leg, or whose token resolved to an exact code, produces the same bytes as
  before this lane. Evidence: full `pytest` on the lane and resolver test files, before/after.
- AC-1323 Field-reveal gating is unchanged: a dealer without `inventory.sellable` sees the same
  stock block on a HAS turn as on a forward turn. Evidence: pytest, two contacts, same seed.
- AC-1324 Nothing in this lane changes the parser prompt file, the prompt registry, the MCP
  catalogue or `n8n-changes.md`. Evidence: `git diff --stat` on the PR shows no change under
  `chatbot_parser_prompt.py`, `ai_prompt_registry.py`, `sorento_crm_mcp/`.

## H. Verification on the prod copy [E2E]

- AC-1325 Console turns, each recorded in the test report with the trace: "which water tap has
  cert" (clarify), "which tap has cert" (1,256, 5 shown with files), "which sorento bidet has
  cert" (miss with the 3 named), "which basin got stock" (586), "which item has PPS cert"
  (940), "which basin has photo" (alias hit), "which sink has incoming" (count from the new
  leg), "any shower set on promo" (product_type binding), "more" after the tap answer (6 to
  10). Evidence: `documentation/agents/chatbot-verification.md` procedure.

## I. Owner hand test round 2 (26 Sep 2026, contact 900000039) [BE + FE]

Evidence for each: `tests/chatbot/test_attribute_asks_round2.py` (whole turns, real resolver,
MCP stubbed), plus the console cases in `console_cases/2026-09-11-attribute-first-asks.yaml`.

- AC-1360 (W1) A brand word is a brand: "sorento wash basin" (the brand as its own entity or
  inside the class word) answers Sorento wash basins only, never every Sorento product;
  "wall hung basin" keeps the Wash Basin class and the wall hung mounting. The brand is read
  off the brands table (`Product.brand_id`), and a brand word that scoped the set is never
  reported as "I could not find <brand>".
- AC-1361 (W2) The header names the resolved spec in plain words, labels from the spec
  registry, no snake_case: "Brand: Sorento, Product type: Wash basin, Mounting: Wall hung. 2
  wash basins have stock." (AMENDED by AC-1372, round 4: one filter per line, labels bold.) The count noun is the set's class. A word that was not understood
  is said ("I did not understand "zzqx", so it is not part of this search.").
- AC-1362 (W3) Each set row is one line: product name (key spec) | code | the tool's fields.
  AMENDED by AC-1366 (round 3): the row is a vertical block, never one line.
- AC-1363 (W4) A bare count after the count question pages the set just asked, brand
  included; "another N" after a list continues from where it stopped with the header
  repeated ("Here are 3 to 4."); a bare number after a listed page is not a page (AMENDED
  by AC-1367, round 3: it is); the count
  does not change between ask and page, and when stock moved the page says so. The bot never
  offers "more"/"next".
- AC-1364 (W5) SUPERSEDED by AC-1371 (round 4 R1: per-brand weights replace the switch).
  One chatbot default brand per company, seeded to Sorento, edited on Master Data >
  Brands. No brand named: "Brand: Sorento (default), ... Other brands: Mocha 3, name one to
  see them." (AMENDED by AC-1369, round 3: no "(default)", the other brands close the reply); a named brand answers that brand only; a page keeps the default; a default the
  set does not reach leaves the set whole. Evidence also `tests/test_brand_chatbot_default_route.py`,
  `tests/test_migration_bcd_0001_brand_chatbot_default.py`, and vitest
  `brands/[id]/page.chatbotDefault.test.tsx`, `brands/components/BrandFormDialog.chatbotDefault.test.tsx`.
- AC-1365 (W6) "water basin" is Wash Basin even where the category lacks the synonym; a
  section header never lists more than five codes in one line.

## J. Owner hand test of round 2 (26 Sep 2026 13:07Z to 13:11Z, contact 900000039) [BE]

Evidence for each: `tests/chatbot/test_attribute_asks_round3.py` (whole turns through
`engine.run_turn`, real resolver, class vocabulary and brands table, MCP stubbed), plus the
round 3 console cases in `console_cases/2026-09-11-attribute-first-asks.yaml`.

- AC-1366 (W1) AMENDED by AC-1373 (round 4 R3: a row is at most two lines). Every set list
  reads top to bottom. A row is a block: line 1 "N. <product
  name>" (the description when the name is only the code, never spec values), then one
  "*Label:* value" line per field: Product Code, the key specs the header does not already
  say, then the tool's fields (Total, each location; attachment type, file, certificate; ETA).
  A blank line between products, no "|" anywhere in a set reply, the header one line, the
  tool's own intro not repeated under it. Stock, certificate and incoming lists alike.
- AC-1367 (W2) After a listed page, a bare count ("10") continues the SAME set in the same
  layout ("Here are 31 to 40." over rows 31 to 40), whatever the parser read the number as
  (null, `top_n`, a position). After the last page, a count answers "<header>. That is all
  62." with no tool call. The bot never offers "more"/"next".
- AC-1368 (W3) A class word the message names starts a new set: the earlier turn's class,
  spec, brand and product entities the parser hands back (`current_message: false`) are
  dropped. "which basin has cert" after a water closet set answers the wash basin
  certificate set; it never blends the classes and never names the old codes.
- AC-1369 (W4) No "(default)". No brand named and the company default applies: the header
  reads "Brand: Sorento, Product type: Wash basin." and the reply's last line reads "Other
  brands with stock: Bravat 79, Cabana 57. Name one to see them." ("with certificates" /
  "with incoming" for those asks). A named brand answers that brand only.
- AC-1370 (W5) Every listed product belongs to the header's brand, read off
  `Product.brand_id` through the brands table, on the first answer and on a page. The GB
  glass basin line is Sorento's in the catalogue (`brand_name` SORENTO, category `SRT-WB`,
  e.g. GB3011B "SORENTO GLASS BASIN ONLY GB3011B").

## K. Owner console test of round 3 (27 Sep 2026 00:03 to 00:07 MYT, contact 900000039) [BE + FE]

Owner rulings R1 to R7, 27 Sep 2026 (PR #833 comment "Owner console test of round 3").
Evidence for each: `tests/chatbot/test_attribute_asks_round4.py` (whole turns through
`engine.run_turn`, real resolver, class vocabulary, spec registry and brands table, spec
fallback on; parser and MCP stubbed), which also replays the owner's eight exchanges from
`tests/chatbot/fixtures/owner_console_2026_09_27.json` and scans every reply for snake_case.

- AC-1371 (R1, owner ruling 27 Sep 2026) Brand preference is a weight per brand
  (`brands.chatbot_weight`, 0 = none, Sorento seeded 1.5 by migration
  `bcw_0001_brand_chatbot_weight`, which drops `is_chatbot_default`). No brand named: the
  highest weighted brand the set reaches heads the header, and "Other brands with stock:
  Mocha 1, Cabana 2. Name one to see them." lists the rest by weight, then count; raising
  another brand above Sorento makes it the header brand; no weighted brand in the set
  answers every brand with no brand line. Edited on Master Data > Brands: list column
  "Chatbot weight", record page and dialog field "Chatbot brand weight" (0 or more).
  Evidence also `tests/test_brand_chatbot_weight_route.py`,
  `tests/test_migration_bcw_0001_brand_chatbot_weight.py`, vitest
  `brands/[id]/page.chatbotWeight.test.tsx`,
  `brands/components/BrandFormDialog.chatbotWeight.test.tsx`,
  `brands/components/BrandTable.chatbotWeight.test.tsx`.
- AC-1372 (R2, owner ruling 27 Sep 2026) The set header is line by line, one filter per
  line, labels bold ("*Brand:* Sorento", "*Product type:* Water closet", "*Trap:* P trap"),
  then the count sentence on its own line. A class word with its spec after it ("water closet
  p trap") keeps its Product type line.
- AC-1373 (R3, owner ruling 27 Sep 2026) A counted-set row is at most two lines: "N. <name>
  (<code>)", then the one or two facts the ask was about (stock: Total; certificate:
  Certificate Number and Valid Until; incoming: quantity and arrival date). No spec lines, no
  per-location lines. Fifty rows fit one WhatsApp message. AMENDED 27 Sep 2026 (owner hand test
  of rounds 4 to 6, items 1 and 7): the two-line row holds for a stock (and promotion) set only.
  An incoming, certificate or attachment set renders each row as the normal ETA or attachment
  ask does (AC-1378, AC-1383).
- AC-1374 (R4, owner ruling 27 Sep 2026) A set that qualifies nothing, described by a class and
  one value, says what it searched and the count in the other values before the escalation
  offer: "No gunmetal wash basins with incoming stock (I looked for Finish or colour: Gunmetal
  among wash basins). 1 wash basin has incoming stock in another finish or colour: White 1.
  Would you like me to escalate to purchasing team?"; with none in any value, "No bathtubs have
  a certificate in any finish or colour." AMENDED 26 Sep 2026 (reviewer pass at d6fa2b31, S1): a
  product carrying two values (a dual finish) is listed under each and counted once in the total;
  an acronym value keeps its capitals ("No PVC wash basins", N2). AMENDED 26 Sep 2026 (reviewer
  pass at 34cb4697, N-r5-2): word by word, so "PVC pipe" reads "No PVC pipe wash basins".
- AC-1375 (R5, owner ruling 27 Sep 2026) The answer to "I don't know 'water tap basin' as a
  product type. Did you mean tap or wash basin?" that is one of the options re-runs the original
  ask with that word: "tap" answers "2 taps have stock." with the list, never a code search. A
  reply that is not an option is its own question, and the clarify is spent after one turn.
- AC-1376 (R6, owner ruling 27 Sep 2026) An attribute value the registry does not know is said
  back with the values it does, on a product ask and a set ask alike: "I don't know 't trap' as a
  trap. I know P trap and S trap." Nothing is listed; answering "p trap" re-runs the ask (AC-1375).
  Found from the registry's own synonyms (a key's head word), no word list in code; "water closet
  p trap", "water closet trap 250mm" and "floor waste wc" are not unknown. AMENDED 26 Sep 2026
  (reviewer pass at d6fa2b31, B1): the word must sit in a value position (a single letter beside
  single-letter values, or a one-edit slip of a known value word) and the phrase must be no
  product's name or description; a plain product word before a head word ("deck mounted bath
  mixer", "long spout basin tap", "ceiling mounted shower", "rain shower ceiling mount", "grease
  trap", "click clack waste") is searched, never said back. AMENDED 26 Sep 2026 (reviewer pass
  at 34cb4697, B1-r5): a product names the phrase only as whole words; a product called "Floor
  Waste With Basket Trap" does not name "t trap", and exchange 8 still answers exactly "I don't
  know 't trap' as a trap. I know P trap and S trap." beside it.
- AC-1377 (R7, owner ruling 27 Sep 2026) Every value a reply shows is plain words: the registry's
  `value_labels`, else the stored slug in sentence case ("cold_only" -> "Cold only", "s_trap" ->
  "S trap", "pp" -> "PP"). Every enum value in the registry seed reads without "_". The product
  list carries `display_value` per spec; the chatbot's Specs line reads any slug that still
  arrives. No reply in the round 4 replays, and no console case expectation, carries a
  snake_case token (the miss copy's domain key included).

## L. Owner hand test of rounds 4 to 6 (27 Sep 2026 10:37 to 10:45 MYT, console :3084, contact Mr Loo) [BE]

The asks come from a salesperson (Leena) who types a product code, a descriptor and a field.
Every criterion below is tested with her exact messages.

- AC-1378 (F1, item 1) An incoming or ETA ask never says it did not understand "eta" or any word
  that selected the incoming domain ("65502 eta"), and never closes a row with "not recorded yet"
  for the ask's own word. A multi-product incoming answer renders each row in the same field
  structure as the single-product ETA reply (Product Code, Product Name, Shipment Container,
  Estimated Arrival Date, the other incoming fields, Incoming Quantity), then the packing list
  files.
- AC-1379 (F2, items 2 and 3) When the message carries a product code, descriptor words ("s
  trap", "p trap", "bowl only", "seat cover") pick among that code's variants and never open a
  category set: "I need srtwc8840 s trap bowl only price" answers SRTWCX8840-S, "Srtwc8840 bowl
  only price" answers SRTWCX8840-P and SRTWCX8840-S, "Srtwc8840 seat cover price" answers
  SRTWC8840-SC, each in the normal price structure. A stock ask of the same shape lists the
  variant's stock, never a counted set.
- AC-1380 (F3, item 4) A code followed by a descriptor resolves as code, then descriptor:
  "Srtwc7604 p trap price" answers SRTWCX7604-P-RL-NEW (the X-prefixed sibling), "Srtwc8840 p
  Trap" answers SRTWCX8840-P. When no variant carries every word, the variants carrying the most
  are offered; words every variant carries, or none does, leave the code as typed.
- AC-1381 (F4, items 5 and 7) A described stock set keeps the header shape of rounds 4 to 6: one
  bold label per attribute the user named, then the count ("*Brand:* Sorento", "*Product type:*
  Close coupled water closet", "*Trap:* P trap", "4 water closets have stock."), with no second
  "*Type:*" line. "I could not find X" is never said for a phrase that resolved into the header
  ("close couple wc", "WALL MOUNTED KITCHEN TAP").
- AC-1382 (F5, item 6) A bare brand name answering "Other brands with stock: Mocha 2, Cabana 3.
  Name one to see them." narrows the same set to that brand: "*Brand:* Cabana", the same Product
  type and Trap lines, "3 water closets have stock.", the three Cabana rows. The count in the
  offer and the narrowed answer agree; a brand the offer did not name is a new question; "50"
  and "10" after it stay on the same three products.
- AC-1383 (F6, item 7) A certificate ask lists certificate files only, never Product Photos,
  each row in the normal product attachment structure (Product Code, Product Name, Attachment
  Type, File Name, Certificate Number, Valid Until, Validity, the expiry flag when lapsed), then
  the files.
- AC-1384 (F7) Replies are plain, human sentences: no internal phrasing ("described set",
  "qualifies nothing", "predicate", "unrecognized", "scope term", "None", "null") reaches any
  reply.

## M. Owner retest of round 7 (27 Sep 2026 12:59 to 13:04 MYT, console :3084 at b056885e, contact Mr Loo) [BE]

Rulings assumed (the owner confirms): a set answer's intro names the described set and its count,
then exactly the product-code rows in the contact's detailed or compact mode, then the same footer;
the same rule per domain. AC-1381's header lines and AC-1373's two-line rows are superseded.

- AC-1385 (F1, grounding) The parser is bounded by the specification registry. Its prompt carries a
  `specification` entity kind and one Specification line per registry key (key, label, every choice
  with its words, the unit of a numeric key, the words of a yes-or-no key), rendered from the
  registry at publish time; the entity carries `spec_key` and `spec_value`. After every parse a
  deterministic step grounds each descriptor against the registry, whatever kind the parser gave
  it: the category token keeps only what the product IS ("gunmetal basin" -> category "basin" plus
  Finish or colour: Gunmetal; "undermount basin" -> Mounting: Under counter); a number beside a
  numeric key's word is that measurement ("thicnkess 1.2 mm" -> Thickness: 1.2 mm); a word beside a
  key's own word that is none of its choices is an unknown value of that key, said back with the
  known choices ("I don't know 'pink' as a finish or colour. I know ...", "I don't know 'f trap' as a
  trap. I know P trap and S trap."); `attachment_type` stays only for a word on the attachment-type
  list, never for a descriptor; a document domain chosen only for a misfiled descriptor becomes the
  product domain. Tested on the owner's messages and on phrases generated from the registry.
- AC-1386 (F1, follow-up) A one-word follow-up ("cert?", "stock?", "incoming?") after a described
  answer switches the domain and keeps the grounded set.
- AC-1387 (F2) A set answer renders its rows through the product-code answer's renderer, from the
  same tool in the same mode, with the same fields, flags ("PRODUCT DISCONTINUED"), warehouse lines
  and "Data last updated" footer. Only the intro differs: "Stock summary for Sorento wash basins with
  stock (276, showing 1 to 10)." The same for incoming, certificates and attachments, and for price
  ("Prices for Sorento gunmetal wash basins (3)."). No separate set row format and no bold attribute
  header lines.
- AC-1388 (F1, zero set) A described set that qualifies nothing says what it looked for and what the
  set holds in the key's other values (AC-1374), never "Could not find incoming for category ...".

## N. Owner hand test of 28 Sep 2026 (11:27 to 11:32 MYT, console :3083, contact 900000039) [BE]

Every attribute ask answers through the ONE reply the product-code ask already has. AC-1374,
AC-1388 and the "I don't know ... I know ..." sentence of AC-1385 are superseded.

- AC-1389 A described set that qualifies nothing reads "Here's what you want: gunmetal wash basins
  (2)", the described products one per line, then "But no incoming matched these. Would you like me
  to escalate to purchasing team?". No "I looked for", no other-finish counts.
- AC-1390 A value the registry does not know reads "Here's what you want: pink water closets", the
  subject broken down by that key one line each ("• White water closets: 7"), then "Couldn't find:
  pink (finish or colour). Would you like me to escalate to ... team?". No list of known choices, no
  typo mention. A word the set reader could not use is a "Couldn't find" line, never "did you mean".
- AC-1391 No brand named means every brand: no default brand in the intro, no "Other brands" line.
- AC-1392 A set longer than one reply gives the full count and a breakdown by the next attribute
  (brand when none was named, then class, then the registry's keys), one line each; never "how many
  should I show".
- AC-1393 A domain word alone ("cert?") after any attribute answer, hit or miss, keeps the subject
  and the specifications of that ask.
- AC-1394 A product ask naming a property ("kitchen sink 1.2mm thickness") lists only the products
  with it, every brand, under one intro line ("Here are kitchen sinks with thickness 1.2 mm (2).").
- AC-1395 A category word alone never opens the "Which kind of file do you need?" menu.

## Fix round 12 (owner retest of round 10, 28 Sep 2026: attribute-first by structure)

- AC-1396 An ask that describes a product set (a product type, a category or a specification, no
  code typed) is the attribute-first lane's whatever verb carries the leg. The leg is read from the
  parser's intent, the routed domain, and any word of a requested attribute that the registry knows
  (`_LEG_BY_ATTRIBUTE_WORD` plus each leg domain's `switch_words`). No phrase has an entry of its
  own. The leg sets the turn's domain before APPLY, so the rows show the leg's figures and the offer
  names the leg's team.
- AC-1397 Every described-set answer, hit or miss, opens with "Here's what you want:", the set with
  its leg and its count: "Here's what you want: gunmetal wash basins with stock (2)", then the
  product-code rows. No set answer opens with a tool sentence ("Stock summary for ...", "Incoming
  stock found for ...", "Certificates found for ...", "Here are ..."). Those stay only on a
  product-code answer, which is not a set.
- AC-1398 A generated test builds the phrasings from the registry and the owner's list (has stock,
  have stock, got stock, in stock, still have, any left, incoming, arriving, on order, the bare
  form) over several parser readings each, and asserts AC-1396 and AC-1397 for every one.
