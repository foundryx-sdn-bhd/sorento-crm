# PLAN: rogue dealer conversation, 6 Oct (ROGUE-CONVO-REVIEW)

Status: BUILDING. PR-2 (classes B, C, G, F) is PR #1506 on this branch, size M; PR-1
(class A) builds on its own branch. Owner rulings (6 Oct 2026, via crew): Q1 = (a) PLUS
the contact's brand scope: the chatbot list applies `is_searchable` (product and category),
`is_active` and the contact's brand bound. Q2: a domain-level refuse-empty for
`master_products` with the inventory sentence worded for products ("That would search every
product we have - I need at least one filter to narrow it down. Give me a product code,
category, or brand, and I can look it up."). Q3: the owner REJECTS "Couldn't find 8066":
a bare number typed as a product must match its code family (SRTWC8066-*) and offer the
same picker compact mode gives (located: the resolver's substring probe already places the
family; only `engine._exact_codes_only`'s `_code_like` gate discards it on availability
profiles; crew-note on #1506 has the chain); the ladder stays in PR-2 as built (owner
ruling on #1511's card, Q4 = b). Class B addendum (owner, 6 Oct, second round): the miss
line prints the customer's TYPED text, never a folded token or a word the set reader
derived from it. Q4 = (a): the miss header lists only what the customer named and the
resolver placed, and a miss carries no set_page. Q5 = (a): two PRs. Class F(i) wording
aligned to #1511 (card Q1 = a): `raw` stays the typed text, `canonical_code` is the bare
code. Track per PR: small fix track (backend only, no migration, no auth/RBAC change, no
new ingest surface, no UI). Cloud sandbox.
UAC: `rogue-convo-review-acceptance-criteria.md`.
Related lanes: #1502 STOCK-FILL-PREDICATE (class E, handed over), #1504 BRAND-PROMO-EXPAND
(class D, handed over), #1501 merged (no stock claim without a lookup).

## Scope

Owner, 6 Oct 2026 19:15 MYT: "the most rogue conversation we have had today with a dealer;
look into it to improve WITHOUT overfitting to this scenario."

One dealer contact (General Enquiries access, an availability-only profile), 16 turns,
prompt v47, pre-19:05-release code. Seven failure classes A to G. Each is stated below as
a general rule; the turns are only the red tests. Product codes in this document are
catalogue codes; no contact ids, turn ids or names appear.

Code read on origin/main `2c4b6824c`. The crew scout's citations (sent 6 Oct 19:25) were
verified by three independent read-throughs; every line below was re-read in this lane.
Paths are under `sorento_crm_backend/app/services/chatbot/` unless stated.

## Findings

### Class A: zero-scope catalogue dump (turns 2 and 7)

Mechanism, confirmed:

1. `lanes/business/gate.py:143-155` `ALLOWS_EMPTY` already says `master_products: False`,
   and `gate.py:462-475` would refuse with "no entities and 'master_products' requires a
   scoping entity". But the gate runs only inside the resolver, and the resolver is
   skipped when the parser returns zero entities (`turn_runtime.py:2057-2058`).
2. The engine's own zero-entity refusal is inventory-only in both places:
   `turn/apply.py:2142` `_REFUSES_EMPTY_SUBJECT = {"inventory"}` (and only fires on a
   multi-domain ask or `refuse_empty_subject`, `apply.py:2386-2391`), and the no-subject
   probe `turn_runtime.py:3014-3027` is `domain == "inventory"`. The comment at
   `apply.py:2137-2141` argues master_products "narrows on a must_narrow_one policy that
   already asks"; that premise is false: the master_products domain row has
   `narrowing={}` (`turn/policy_rows.py:56-75`) so the product kind's
   `default_narrowing="list_all"` applies (`policy_rows.py:404-414`, seed
   `alembic/_chatbot_policy_seed.py:224`), and `turn/narrow.py:390-391` makes `list_all`
   a pass-through with no ask.
3. `turn_runtime.py:2973-2977` `would_be_unfiltered` covers inventory/incoming only;
   `policy_rows.py:549-551` `ENTITY_FILTER_REQUIRED_TOOLS` lacks
   `crm_master_products_list`; the MCP server adds no default narrowing for it. So the
   tool runs with no filter and the route (`app/api/v1/master_data/products.py:108-111`)
   returns its default `limit=50` page, sorted by `created_at`. The intro "Here are the
   matching products." is the unconditional per-tool intro
   (`sorento_crm_mcp/sorento_crm_mcp/presenters.py:75`).
4. Visibility: `app/services/product_service.py:369-370` filters `is_active` only when a
   `status` argument is passed; the chatbot never passes one (`lanes/business/fetch.py:
   724-726` copies `is_active` only if the parser set it). The model already has
   `Product.is_searchable` ("Whether the chatbot may answer with this product",
   `app/models/product.py:293`) and `chat_searchable_products()` (`product.py:375-401`,
   product OR category `is_searchable` false), honoured by the resolver
   (`app/services/entity_resolver.py:846, 1891, 1900, 2771, 3547`) and by spec search,
   but NOT by `ProductService._build_list_query` (`product_service.py:302-496`). So a
   typed code is protected, a zero-entity list is not: "FOR ACCOUNT DEPARTMENT ONLY
   (DISBURSEMENT)", a "****" description and "(PRODUCT DISCONTINUED)" rows reach the
   dealer.

General rule A: **a domain fetch with zero resolved scoping entities and no explicit
broaden asks for a filter, in every domain whose `ALLOWS_EMPTY` row is False**; the
inventory-only lists are the defect, not the design. And **the chatbot's product list
honours the same visibility predicate the resolver already honours.**

### Class B: unresolved typed product token answered with the scope sentence (turns 3, 8, 10)

Mechanism, confirmed for "8066":

1. The dealer profile is `stock_availability_only`, so `engine.py:4980-4986` runs
   `_exact_codes_only` (`engine.py:511-600`). Per typed product token it keeps exact or
   family candidates; a token that is not `code_like` (`turn/task.py:778-783`: letters
   AND digits) goes to `ignored` (`engine.py:578-579`), and `engine.py:599` DROPS an
   ignored token from `unplaced`. "8066" is all digits, so it vanishes from `unplaced`
   (the trace's `unresolved []`).
2. With `unplaced` empty, `would_be_unfiltered` is False (`turn_runtime.py:2973-2977`),
   no entity has a uuid, so the no-subject probe fires (`turn_runtime.py:3014-3027`),
   `scope_gate` is stamped (`:3072`), `answer_bridge.py:1821` prefers it, and
   `answer.py:3104-3111` `needs_scope` is True because `has_unresolved` (`:3099-3100`)
   reads the RESOLVER's `unresolved_tokens`, which never saw the filter's verdict. The
   scope sentence is `answer.py:3210-3213`.
3. Did-you-mean needs the token in `unplaced` (`engine.py:6686`,
   `turn_runtime.py:4135-4137, 4223`; `lanes/business/miss_suggest.py:149-178`), so a
   dropped token can never get suggestions. "Srts07" is code-like, stays unplaced, and
   gets its trigram alternatives (turn 13, the good path).
4. The prompt itself says a bare number IS a product token (prod text line 408-416,
   "7445" in the BARE FAMILY CODE rule), so the code's `ignored` bucket contradicts the
   parser it is supposed to defer to.

"SORENTO PSJLSRTSC07" (turns 8, 10): the token is code-like and should stay in `unplaced`
as a miss. The remaining way it reaches the scope sentence is the same seam read from the
other side: the resolver placed describe/semantic candidates for the two-word phrase (so
its `unresolved_tokens` is empty and the resolver's own gate reason is the
"requires a scoping entity" string once the filter emptied `compatible_entities`), and
`answer.py:3099` reads that resolver list. Not reproduced in this lane (no trace access);
the red test for this turn settles the exact path and the fix below covers both.

`tests/chatbot/test_rearch_r13_stock_no_subject.py:439-520` (T4) claims the guard but
seeds a non-availability contact (`_seed_contact_and_get`, `variables={}`), so
`_exact_codes_only` never runs there, and its token "Srtks8060-BL" is code-like. It
cannot fire for either shape.

"Srt-hp" (turn 11): the token has no digit, so `gate._is_a_described_word` reads it as a
DESCRIPTION, `resolve_gate.resolve_entity_body:716` keeps the `require` leg (no typed code
to null it), the set reader splits the query into content words
(`product_spec_search._content_words`: "srt", "hp"; "hp" is under `_MIN_REPORTABLE_WORD`),
"srt" binds nothing and lands in `predicate.unrecognized_terms`, and the zero-set branch
`answer.py` F2 (`what_you_want_reply(asked, [], missing=...)`) prints that first term as
both the header subject (`asked = ... or terms[0]`) and the miss. The dealer's own token
never reached the reply. Reproduced on the real engine console in this lane: on main the
reply is `Here's what you want: srt / Couldn't find: "srt".`; the pasted turn-4 line gives
the same shape over its class words (`Here's what you want: sorento one piece washdown
toilet / ...`).

General rule B: **a typed product token the resolver or the dealer filter could not place
is reported as "Couldn't find X", with did-you-mean when the resolver has candidates,
never as the scope sentence; the scope sentence is for a message that named nothing. X is
the customer's typed text, never a folded token and never a word the set reader derived
from it.**

### Class C: a miss renders "Here's what you want: • product: X (+N more)" (turns 4, 5, 11)

Mechanism, confirmed:

1. The header is printed whenever `found_lines` is non-empty (`answer.py:3753-3754`,
   `use_breakdown = len(found_lines) > 0` at `:3600`). `found_lines` is built from the
   gate's `compatible_entities` (`:3289-3291`, `by_type` at `:3392-3421`), naming the
   typed codes or else the first row plus "(+N more)" (`:3567-3596`). It never checks
   that the dealer's own token resolved.
2. The 35 and 145 rows are spec_search SET MEMBERS, not did-you-mean rows: the C4 bypass
   (`gate.py:679-695`) appends every product of a token group whose matches are all
   `spec_search` to `exact_entities`; `_without_guesses` (`turn_runtime.py:1845-1898`)
   keeps them when the predicate's `qualifying_total > 0`. The did-you-mean rows came
   from the separate `miss_suggest` path and never showed because the breakdown won.
3. Why a miss became a set at all: `resolve_gate.py:716` nulls the set predicate when the
   message `_names_a_typed_code`, but `gate._CODE_SHAPED` (`gate.py:197-199`) allows no
   space or parenthesis, so a whole description line with the code inside is read as a
   DESCRIPTION word (`gate.py:207-224`), `require` stays, `resolve_product_set` reads the
   description words ("toilet", "washdown", "s-trap", "sorento") and counts 145 Sorento
   water closets, and `engine.py:5981-6018` carries a `set_page` because
   `set_page_carry` (`turn_runtime.py:3306-3307`) only asks whether a description exists,
   not whether the typed token was placed.
4. "Here's what you want: srt / Couldn't find: "srt"" (turn 11) is the described-set
   "named nothing readable" branch (`answer.py` F2 into `what_you_want_reply`); "srt" is
   the set reader's first unrecognised content word of "Srt-hp", not a parser output (see
   class B above). The composer side is the same rule as below.
5. The join that decides "this message's own product token missed" has to fold both
   sides: the resolver lists an unresolved token the way it was SENT, separators stripped
   (`resolve_gate._token_of`, `_PRODUCT_FOLD`), so "Srt-hp" comes back as "srthp" and the
   pasted line as one run. A raw-to-raw comparison (`_nf_norm_raw`) never met a hyphenated
   or spaced token, which is every token of this conversation; `_type_norm` (the key the
   composer already uses for `raw_of_token`) is the join.

General rule C: **"Here's what you want" lists only what the customer named and the
resolver placed. A miss on the customer's own token never lists set members, did-you-mean
candidates or alternatives under that header, and never carries a set_page.** (The
code-inside-a-description parse is class F; this rule holds even when the parser gets it
wrong.)

### Class G: media caption "null" (turns 8, 10)

Confirmed, one line: `engine.py:4049`
`caption = jsc.js_string(jsc.get(attachment, "description")) or None`. `jsc.js_string(None)`
returns the string `"null"` (`jsc.py:45-48`), which is truthy, so `or None` never fires;
an absent key gives `"undefined"` the same way. `media_extract/service.py:297` then keeps
it as a real caption and `:339-345` renders it as the first line, so the parser read
"null\nSORENTO PSJLSRTSC07 x50\nSORENTO". Fix shape (same pattern as `:4042` and
`media_intake.py:206`): `d = jsc.get(attachment, "description"); caption = jsc.js_string(d)
if jsc.truthy(d) else None`.

### Class D (turn 6) and class E (turns 15, 16): handed over

* D posted to PR #1504 as a crew-note (confident parser brand beside a code armed a
  kind_pick of promotion/customer/form/transporter). That lane's fix A (brand probe for
  every domain but promotion) and B (drop kinds the gate rejects for the domain) should
  close it; the note names the asserts.
  Second D example (crew, real parser dry run on the Mac against dev, 6 Oct): "any basin
  with thickness 1.0mm" parsed perfectly (category `basin`, specification `thickness` =
  1) and the engine still routed `clarify_menu`: "Which one do you mean? 1. basin
  (promotion) 2. basin (customer)". Same mechanism one kind over: the resolver's
  category/brand fan-out answers a CLASS word with promotion and customer rows, the
  reconciliation cannot confirm the `category` hint, and a kind pick is armed instead of
  the product spec search the words describe. The general rule for #1504: a confident
  parser hint of kind `brand` or `category` is confirmed or dropped, never re-typed and
  never asked as a pick over kinds the domain's gate does not accept. Forwarded to #1504.
* E posted to PR #1502 as a crew-note: the stale extra here is a `category` word (a
  described set), so a fix keyed on "pop the predicate unless it describes a set" keeps
  this predicate. The owner's rule must win over the described-set exception when the
  description came from a carried uuid-less extra, not from this message.

### Class F: parser prompt (semantic, prompt-only)

Two addenda, anchored inside the sections they extend (applier
`tests/chatbot/_rogue_convo_prompt.py`):

* `alembic/data/chatbot_semantic_parser.rogue-convo.code-in-description.addendum.txt`
  after the BARE FAMILY CODE bullet (prod text line 416): a description with a code inside
  it is one product entity whose `raw` is the typed text and whose `canonical_code` is the
  bare code (owner ruling on #1511, card Q1 = a; the chatbot reads `canonical_code` first
  once #1511 merges, so the two addenda state one rule); the words become category /
  specification; a Known brand glued to a code is two entities. The live eval grades
  `canonical_code` (folded).
* `alembic/data/chatbot_semantic_parser.rogue-convo.bare-count.addendum.txt` after the
  QUANTITY section (prod text line 1373): "x 50 unit" / "X 50 unit" / "x46 pcs" with no
  code is a quantity for the standing product or the open question, never a product
  named X.

Eval: `tests/chatbot/test_rogue_convo_live_parser.py` (`live_external`, 5 runs per shape,
base vs patched, 5 targets + 5 controls, prints `ROGUE-CONVO-EVAL shape=... pass=k/n`).
`ROGUE_CONVO_PROMPT_FILE` points it at the exported dev v60 text. **This sandbox cannot
reach api.openai.com (egress proxy 403), so the table below is filled by crew running:**

```
cd sorento_crm_backend && CHATBOT_LIVE_LLM=1 ROGUE_CONVO_PROMPT_FILE=/path/to/dev-v60.txt \
  venv/bin/pytest tests/chatbot/test_rogue_convo_live_parser.py -q -s 2>&1 | grep ROGUE-CONVO-EVAL
```

Run 6 Oct 2026 from the cloud sandbox (api.openai.com reachable through its proxy; the dev
v60 text is not, so the eval ran on the shipped `SEMANTIC_PARSER_PROMPT` and on the
committed `alembic/data/chatbot_semantic_parser.prod-20261001.txt`; both anchors occur once
in each). Model `gpt-5.4-mini`, 5 runs per shape:

| shape | message | shipped base | shipped patched | prod-20261001 base | prod-20261001 patched |
|---|---|---|---|---|---|
| F1-description-with-code | SORENTO ONE PIECE WASHDOWN TOILET (S-TRAP250MM)-SRTWC8354-SH | 0/5 | 5/5 | 0/5 | 5/5 |
| F1-price-description-with-code | price SORENTO ONE PIECE ... SRTWC8354-SH | 0/5 | 5/5 | 0/5 | 5/5 |
| F1-photo-brand-glued-to-code | SORENTO PSJLSRTSC07 x50 / SORENTO | 0/5 | 5/5 | 0/5 | 5/5 |
| F2-X-50-unit | X 50 unit (after a photo named a product) | 0/5 | 5/5 | 0/5 | 5/5 |
| F2-x46-pcs | x46 pcs | 0/5 | 5/5 | 0/5 | 5/5 |
| C-turn6-sentence (control) | SORENTO S-TRAP 250MM ONE PIECE WC-SRTWC8318-RL-250 | 0/5 (xfail, see below) | 5/5 | 0/5 (xfail) | 5/5 |
| C-8066-stock (control) | 8066 stock ? | 5/5 | 5/5 | 5/5 | 5/5 |
| C-Srts07 (control) | Srts07 | 5/5 | 5/5 | 5/5 | 5/5 |
| C-Srtsc07-50-unit (control) | Srtsc07 50 unit | 5/5 | 5/5 | 5/5 | 5/5 |
| C-50-fill (control) | 50 (open stock question) | 5/5 (one echo ignored) | 5/5 | 5/5 | 5/5 |

The F(i) targets and the turn-6 control grade the product entity's `canonical_code`
(#1511 card Q1 = a); the base texts carry no canonical_code rule, so that control's base
column measures nothing and is marked xfail (not strict). A carried echo
(`current_message: false`, the standing product said back) is not a typed token and is
ignored by the grader. Pass bar met: every target 5/5 patched; every control 5/5 patched
and, where gradable, on base. The owner edits prod by hand (the v60 text) from the two
addendum files; the anchors are the BARE FAMILY CODE bullet's last line and the QUANTITY
section's closing sentence.

## Plan (A, B, C, G)

### PR-1 "zero-scope list asks for a filter" (class A), size M, branch
`crew/rogue-convo-review-pr1` (from main e9aa88b2)

Change (as built):

* `gate.REFUSES_EMPTY_FETCH = {inventory, master_products}`, one constant beside
  `ALLOWS_EMPTY`, two readers. `turn_runtime.make_tool_runner`: the no-subject probe
  runs for every domain in it, still excluding `would_be_unfiltered` and still exempt for
  `INTENTS_ALLOWING_EMPTY`. The R6 `would_be_unfiltered` guard stays inventory/incoming:
  a first cut widened it too, and the reviewer's kill showed no shape where that changed
  a reply (turn 7's "srtwc-8318" lands in the probe, the fragment carries `scope_gate`,
  and `not_found_error_message`'s own `needs_scope ... and not has_unresolved` keeps the
  ordinary "Couldn't find" wording), so it was dropped. Turn 7 is refused with its own
  miss wording and no fetch; turn 2's "product" gets the scope sentence.
* Why not every `ALLOWS_EMPTY` False row (the first cut, and AC-RC-A2's first wording):
  the full chatbot suite measured eight `sales`, `order` and `promotion` pins broken by
  the dict-wide read (the sales report for an unlinked or staff contact, top selling, the
  order broaden "all products" re-run, a promotion read off the focus brand). Those
  entity-less asks are whole-book or focus-driven asks with their own flows, so refusing
  them is a regression, not a fix. A third domain joins the set on its own live turn.
* `turn/apply._REFUSES_EMPTY_SUBJECT` becomes `_refuses_empty_subject(domain)`, the same
  read of `REFUSES_EMPTY_FETCH` (one source; the "master_products already asks" premise
  in its old comment was false).
* Two pins relied on the unfiltered zero-entity `master_products` read and were adjusted
  to a scoped fetch, intent kept: `test_engine_failure_paths` (an MCP raise on a fetch
  whose resolver now places the typed code) and `test_escalation_brand_from_focus` (a
  "hit over a settled product" whose verdict now echoes the carried product the way the
  real parser does, `current_message: False` under `entity_op: reuse`; measured on main,
  the tool call carried no `product_ids` at all).
* `_SCOPE_WORD` already has `master_products: "product"`, so the sentence reads "That
  would search every product we have - I need at least one filter to narrow it down. Give
  me a product code, category, or brand, and I can look it up." with no new wording.
* Visibility (owner ruling Q1 = a + brand): `ProductService._build_list_query` /
  `list_products` take `chat_visible: bool` (applies `chat_searchable_products()` AND
  `is_active`), the products route exposes it as a query flag, the MCP catalog declares it
  on `crm_master_products_list` (a param missing from `query_params` never reaches the
  backend), and the chatbot fetch builder sets it on every call of that tool. The admin
  list never sends it and is byte-identical. No new column: `is_searchable` already means
  "the chatbot may answer with this". Marking AC-EXP-* and masked rows
  `is_searchable=false` is a data task for the owner.
* **The brand bound needed no build** (checked before designing it):
  `company_scope_resolver.apply_company_scope`, the router-level dependency, already
  stamps `set_brand_scope(contact_brand_scope(contact_id, space_id))` on every X-API-Key
  request that carries both ids (#1512), and `fetch.py` forwards `contact_id` +
  `space_id` on every chatbot tool call. PR-1 proves it with a route test (a SORENTO-scoped
  contact with `chat_visible=true` sees the searchable SORENTO product alone) instead of
  adding a second mechanism.
* `is_active` bounds the UNSCOPED list only (reviewer finding 4 on #1517): a code the
  customer named resolves whether or not the product is active (the resolver returns it
  with `display.is_active`) and the answer says so; hiding the row would turn
  "<discontinued code> price" into a miss. Owner to confirm that reading of Q1.
* Follow-up recorded, not built (reviewer finding 1 on #1517): a follow-up about a
  SETTLED focus product in `master_products` with a verdict of `entities: []` under
  `entity_op: reuse` carries nothing into the fetch (`with_carried_entities(unsettled_only
  =True)` skips rows that already have a uuid), so it now gets the scope sentence where
  main listed 50 unrelated products. The real parser echoes the standing product in
  `entities` with `current_message: False` (measured on live verdicts in this lane), which
  is the shape that answers; measure how often a live `master_products` + `reuse` verdict
  comes with `entities: []` over a settled product before carrying it in code.

Red tests (`tests/chatbot/test_zero_scope_list.py`, 9; 8 red on main, the bare-PO control
green on both): bare "product" never calls the tool and gets the product scope sentence;
"srtwc-8318" (missed) never sweeps the catalogue and keeps its own miss wording; control
"<code> price" fetches once with the id, `chat_visible: True`, `contact_id` + `space_id`;
control bare "PO" still reaches a procurement tool; service `chat_visible` drops a flagged
product, a product in a flagged category and an inactive one while the admin list keeps
all four; the route end to end for a brand-scoped contact; the MCP catalog declares the
param. `test_rearch_r13` T3 (low stock with no entities) stays green.

Red tests (new file `tests/chatbot/test_zero_scope_list.py`):

* bare "product" on a fresh contact (parser: master_products, entities [], reuse) never
  calls `crm_master_products_list`; reply is the scope sentence with "product";
* the same message with a kind_pick open (turn 7 shape: `answer_pending_not_an_answer`)
  never dumps the catalogue;
* control: "SRTWC286 price" still fetches with that product's id (one call, ids present);
* control: bare "PO" still lists the open book (`ALLOWS_EMPTY["purchase_order"]` True);
* control: `low_stock_report` with no entities still reaches its tool
  (`test_rearch_r13` T3 stays green);
* visibility: a product with `is_searchable=false`, one whose category is
  `is_searchable=false`, and one `is_active=false` are absent from the chatbot's list
  call for a category-scoped ask; control: the admin products list still returns them.

### PR-2 "a miss says what it could not find" (classes B, C, G), size M

Change B (as built, owner ruling Q3: compact parity, not "Couldn't find 8066"):

* `turn/task.py` gains `code_or_number` (a code-like token OR a bare number), the one
  reader shared by `engine._exact_codes_only`, `turn/task._group` (the picker grouping)
  and `turn_runtime._spec_quantities` (the quantity carry). A bare number the parser
  tagged as a product takes the same ladder as a code: exact, then the prefix family, then
  the substring family the resolver's own probe placed, else an unplaced miss. So "8066
  stock ?" asks "8066 matches 3 products. Which one?" (the picker compact mode gives) and
  "9031 got stock?" over one code answers that code. A WORD ("xx", "new") is still never
  a product (owner, 3 Oct 2026, S47).
* `has_unresolved` needed no change: `answer_bridge._unlisted_words` (`:986-1000`) already
  merges the envelope's post-filter `unresolved` into the resolver's list before the
  composer; the token only has to survive the filter, which the ladder above guarantees.
* Pins rewritten to the ruling: `test_avail_picker_substring.py` ("286" is the family;
  "2" inside one code carries its quantity; `_group` reads a bare number) and S55f ("2
  srtwc286" asks "2"'s own family first, then SRTWC286's; the parser's QUANTITY rule is
  the defence for that mis-parse, flagged to the owner in the Q3 crew-note).

Change C (and the class B miss-line rule, one seam):

* `answer.py` `not_found_error_message`: `product_token_missed` joins this message's
  product entities to the resolver's `unresolved_tokens` through `_type_norm` (separators
  folded on both sides). When it holds: spec_search set members read off the words around
  the token are dropped from `found_lines` (no header, no "(+N more)") AND do not count as
  having placed the token (`resolved_toks`), so a miss with members reaches the
  product-code miss, which quotes the token as typed (`label_token` -> `raw_of_token`):
  `Couldn't find: "<line>" (product).` The described-set "named nothing readable" branch
  (F2, fix round 9/10 on PR #833: "I want that structure to stay") KEEPS its structure;
  the words it prints are the typed token(s) instead of the content words the reader
  split off them: `Here's what you want: Srt-hp / Couldn't find: "Srt-hp".` (A first cut
  stood the described-set answers down instead; it broke three #833 pins in
  `test_lane_require`, where a product-hinted phrase that binds nothing, "water tap",
  "zzqx", is its own term in that structure.) A described-set ask with no product token
  keeps every one of its answers (controls: the gunmetal basins set, "flurbish basin").
* `engine.py:5981-6018` / `turn_runtime.py:3306`: no `set_page` is carried when this
  message's product token is unplaced (the set described nothing the customer asked).
* `resolve_gate.py:716` / `gate._CODE_SHAPED`: NOT widened (no word-matching in code,
  owner rule); the parse is class F's job and rule C holds regardless.

* `engine.py` set_page carry (reviewer finding 2, built): the guard fires only when NO
  class word of the customer's own named the set (`class_terms` empty), so "which tap has
  cert ZZQ9917" lists the taps, names the missed code and stays carried for the follow-up
  count; turn 5's shape (one description-line token, no class entity) still carries
  nothing. Pinned on the counted-set harness (`seven_taps`, list limit 5): kill M7 is dead.

Change G: `engine.py:4049` as above.

### Follow-ups recorded, not built (each needs its own live turn as the trigger)

* **Rule C on the HIT side.** The composer change covers the miss composer (the shapes
  that occurred: no stock, no price). When a set read off the words of an unplaced
  product token HAS data, the set-answer path lists it as "Here's what you want: <set>
  (N, showing ...)"; the fix would be to drop the set predicate before the fetch when the
  message's own product token is unplaced and no class word named the set (the same
  condition as the carry guard), one seam over in the runner. Trigger: a live turn that
  shows it.
* **A product-hinted token that disappears from the resolver's verdict.** Measured on the
  counted-set harness: "SORENTO TAP (CHROME)-ZZQNINE" came back with `unresolved_tokens:
  []` and one resolution for the word "TAP" (substring of the seeded codes ZZT-ZZTAP-*).
  The resolver's word synthesis replaced the typed token with a word that happened to sit
  inside codes, so neither the composer nor the carry guard can see a miss. Real codes
  rarely contain a class word; the prod turns listed the whole token unresolved. #1511
  touches the head-code retry on that path; re-measure after it merges.
* **Quoted raw text has no length or formatting cap** (security-reviewer nit): the quote
  reaches only its own sender; a cap is more code than the risk buys today.
* **F(i) before #1511 merges buys nothing in prod**: the addendum puts the code in
  `canonical_code` and `_exact_codes_only` and the resolver read `raw` first until #1511
  lands. Edit prod after #1511, or alongside it.
* **A class word mis-tagged `product`** ("basin" with hint product) now reads `Couldn't
  find: "basin" (product).` instead of a set answer: the parser decides, and the prompt
  says class words are category entities. Said in the PR body.
* **Turn 5 once F(i) is live** (reviewer nit on 8348009e): the addendum splits the pasted
  line into a product entity plus a category entity ("one piece washdown toilet"), so
  `class_terms` is non-empty and the water-closet set is carried again beside "Couldn't
  find" for the code. That follows "the parser decides" (the class word is then the
  customer's own); AC-RC-C2's turn-5 red test holds while the parser emits the line as one
  product entity, which is the shape prod produced.

Red tests (new file `tests/chatbot/test_miss_says_what_it_could_not_find.py`):

* availability-only contact, "8066 stock ?" with a resolver that returns describe
  candidates and no exact: reply starts "Couldn't find 8066", never "That would search
  every stock"; the stock tool is not called unfiltered;
* same contact, "SORENTO PSJLSRTSC07 x50" (one product entity with the brand glued, a
  brand entity that resolves to nothing): "Couldn't find PSJLSRTSC07" (or the full token),
  never the scope sentence;
* control: bare "stock?" still gets the scope sentence (`test_rearch_r13` T2 green);
* control: "Srts07" with trigram alternatives still gets "Couldn't find SRTS07. Did you
  mean: ..." + quick replies;
* whole-description token with 35 spec_search set members in `compatible_entities` and
  the token unplaced (listed by the resolver folded, the way it is sent): no "Here's what
  you want" header, no "(+34 more)", no `set_page` on `focus`, "Couldn't find" names the
  token as typed;
* dealer console, real resolver: "Srt-hp" answers `Here's what you want: Srt-hp /
  Couldn't find: "Srt-hp".` plus the salesman line, never `srt`; the pasted turn-4 line
  the same with the line as typed (its words unread by the blank catalogue), and
  `focus.set_page` stays None; control: bare "stock?" still gets the scope sentence;
* composer, a described-set ask that named nothing readable ("flurbish basin", no product
  token) keeps `Here's what you want: flurbish basin / Couldn't find: "flurbish".`;
* control: a real described-set miss ("which gunmetal basins have stock", zero
  qualifying) keeps its described-members reply (`test_lane_require` stays green);
* control: a resolved product that missed stock keeps "Here's what you want: • product:
  SRTWC8517 / But no inventory matched these." (`test_crossdomain_ladder.py:169`);
* media turn whose attachment has no `description`: `rendered_text` has no "null" or
  "undefined" line and the trace's caption is None; control: a caption "X5" still
  reaches `rendered_text` as its own first line.

Each PR: pytest red on main / green on head / kill proofs pasted as PR comments, opus
reviewer with a VERDICT line, no security-reviewer (no auth, ingest or scoping change),
no browser pass (no UI), console hand-test script posted as `crew-handtest:`.

## Owner decisions (the crew-ask)

1. **A visibility rule.** (a) Recommended: the chatbot list applies the existing
   `is_searchable` predicate (product and category) plus `is_active`, on the chatbot
   path only; the owner marks internal SKUs `is_searchable=false`. (b) `is_active` only
   (misses active internal lines such as AC-EXP-*). (c) a new `chatbot_visible` column
   (duplicates `is_searchable`).
2. **A: bare "product" (or "price", "catalogue") with nothing standing.** (a)
   Recommended: ask for a filter, same sentence as inventory with the word "product".
   (b) list the first 50 with a narrowing hint (today's dump, with better wording).
3. **B: a bare number typed as a product ("8066").** (a) Recommended: "Couldn't find
   8066" plus did-you-mean from the resolver's own substring family when it has one
   (SRTWC8066-*), the same family rule a code-like token already gets. (b) "Couldn't find
   8066" with trigram did-you-mean only (digits rarely clear the 0.30 floor, so usually
   none). (c) keep ignoring it in code (the scope sentence stays; not recommended).
4. **C: miss header rule.** (a) Recommended: "Here's what you want" lists only what the
   customer named and the resolver placed; a miss on the customer's own token never lists
   set members or candidates under it and never carries a set_page. (b) keep listing the
   set members but under their own line ("Similar: ...") - more words, same confusion.
5. **Packaging.** (a) Recommended: two PRs as above (PR-1 class A; PR-2 classes B, C, G),
   both small fix track, class F lands as the two addendum files plus the live eval in
   PR-2 and the owner edits prod by hand after the table is green. (b) one PR for all
   four classes. (c) four PRs, one per class.
