# PLAN: rogue dealer conversation, 6 Oct (ROGUE-CONVO-REVIEW)

Status: BUILDING PR-2 (classes B, C, G, F) on this branch; PR-1 (class A) waits on the
owner's Q2 wording. Owner rulings (6 Oct 2026, via crew): Q1 = (a) PLUS the contact's
brand scope: the chatbot list applies `is_searchable` (product and category), `is_active`
and the contact's brand bound. Q2: the owner asked for the trigger turns (posted as a
crew-note on #1506) and decides the wording. Q3: the owner REJECTS "Couldn't find 8066":
a bare number typed as a product must match its code family (SRTWC8066-*) and offer the
same picker compact mode gives (located: the resolver's substring probe already places the
family; only `engine._exact_codes_only`'s `_code_like` gate discards it on availability
profiles; crew-note on #1506 has the chain). Q4 = (a): the miss header lists only what the
customer named and the resolver placed, and a miss carries no set_page. Q5 = (a): two PRs.
Track per PR: small fix track (backend only, no migration, no auth/RBAC change, no new
ingest surface, no UI). Cloud sandbox.
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

General rule B: **a typed product token the resolver or the dealer filter could not place
is reported as "Couldn't find X", with did-you-mean when the resolver has candidates,
never as the scope sentence; the scope sentence is for a message that named nothing.**

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
   "named nothing readable" branch (`answer.py:3884-3913` into `what_you_want_reply`
   `:2711-2728`) or the R6 unknown-values arm (`:4206-4232`); either way the parser gave
   "srt" (no code derivation exists: `resolve_gate._token_of` only folds). The parser side
   is class F; the composer side is the same rule as below.

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
  it is one product entity whose raw is the code; the words become category /
  specification; a Known brand glued to a code is two entities.
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

| shape | message | base v60 | patched |
|---|---|---|---|
| F1-description-with-code | SORENTO ONE PIECE WASHDOWN TOILET (S-TRAP250MM)-SRTWC8354-SH | ?/5 | ?/5 |
| F1-price-description-with-code | price SORENTO ONE PIECE ... SRTWC8354-SH | ?/5 | ?/5 |
| F1-photo-brand-glued-to-code | SORENTO PSJLSRTSC07 x50 / SORENTO | ?/5 | ?/5 |
| F2-X-50-unit | X 50 unit (after a photo named a product) | ?/5 | ?/5 |
| F2-x46-pcs | x46 pcs | ?/5 | ?/5 |
| C-turn6-sentence (control) | SORENTO S-TRAP 250MM ONE PIECE WC-SRTWC8318-RL-250 | ?/5 | ?/5 |
| C-8066-stock (control) | 8066 stock ? | ?/5 | ?/5 |
| C-Srts07 (control) | Srts07 | ?/5 | ?/5 |
| C-Srtsc07-50-unit (control) | Srtsc07 50 unit | ?/5 | ?/5 |
| C-50-fill (control) | 50 (open stock question) | ?/5 | ?/5 |

Pass bar: every target 5/5 on patched; every control 5/5 on both. The owner edits prod by
hand once the table is green.

## Plan (A, B, C, G)

### PR-1 "zero-scope list asks for a filter" (class A), size S without the visibility
rule, M with it

Change:

* `turn_runtime.py:3016`: the no-subject probe runs for every domain whose
  `gate.ALLOWS_EMPTY` row is False (read the dict, not a second list), still excluding
  `would_be_unfiltered` and still exempt for `INTENTS_ALLOWING_EMPTY`.
* `turn/apply.py:2142`: `_REFUSES_EMPTY_SUBJECT` becomes the same read of `ALLOWS_EMPTY`
  (one source), and the stale comment goes.
* `_SCOPE_WORD` already has `master_products: "product"` (`answer.py:2305-2314`), so the
  sentence reads "That would search every product we have - I need at least one filter
  ... Give me a product code, category, or brand, and I can look it up." with no new
  wording.
* Visibility (owner decision Q1): `ProductService._build_list_query` takes a
  `chat_visible: bool` argument that applies `chat_searchable_products()` AND
  `is_active`; the chatbot's `crm_master_products_list` call sets it (one arg on the
  tool call, read by the route as a query flag the admin UI never sends). No new column:
  `is_searchable` is the flag that already means "the chatbot may answer with this".
  Marking AC-EXP-* and masked rows `is_searchable=false` is a data task for the owner.

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

Change C:

* `answer.py:3553-3600`: `found_lines` is built only from `compatible_entities` the
  customer NAMED (a typed token, or a carried focus row), never from spec_search set
  members when the message's own product token is in `unplaced`; with none, the miss is
  "Couldn't find X" + did-you-mean + the salesman line, no header.
* `engine.py:5981-6018` / `turn_runtime.py:3306`: no `set_page` is carried when this
  message's product token is unplaced (the set described nothing the customer asked).
* `resolve_gate.py:716` / `gate._CODE_SHAPED`: NOT widened (no word-matching in code,
  owner rule); the parse is class F's job and rule C holds regardless.

Change G: `engine.py:4049` as above.

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
  the token unplaced: no "Here's what you want" header, no "(+34 more)", no `set_page`
  on `focus`, "Couldn't find" names the token, did-you-mean rows are offered;
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
