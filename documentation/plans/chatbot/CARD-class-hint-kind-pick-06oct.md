# CARD - a confident category hint is never re-typed by name match (CLASS-HINT-KIND-PICK)

Status: DRAFT, posted as the lane's one crew-ask on its PR (6 Oct 2026). No code until the
owner answers Q1 and Q2. Track: small fix (backend only, no migration, no auth/RBAC change,
no new ingest surface, no UI). Lane PR carries `crew-lane: CLASS-HINT-KIND-PICK`.

Owner (6 Oct 20:55, "unacceptable"): real-parser dry run on dev, `chatbot.turns`
4d18caff-d2f1-4990-9ae8-b78a5213b70b, contact 437264483. "any basin with thickness 1.0mm"
-> parser entities `[{raw "basin", hint "category", confident true}, {raw "1.0mm", hint
"specification", spec_key thickness, spec_value 1}]`, domain master_products, intent
check_product -> reply "Which one do you mean? 1. basin (promotion) 2. basin (customer)".
Rule the owner stated: a category word must never be re-typed to a promotion or a customer
by name matching.

## What exists today (origin/main 9fc96e29a, every line re-read for this card)

Paths under `sorento_crm_backend/app/`.

- The resolve body sends the parser's hints as a whitelist, the domain, and
  `fallback_to_all_types: True`: `services/chatbot/lanes/business/resolve_gate.py:609`
  (`allowed_entity_types`), `:619` (`domain`), `:620` (`fallback_to_all_types`).
- The grounded `specification` entity ("1.0mm") is removed from the token list before the
  body is built and travels as `extracted_specs` instead (`resolve_gate.py:579-582`,
  `:637-643`). So the resolver saw ONE token, "basin", hinted `category`.
- Primary pass: under domain `master_products` a `category` hint probes PRODUCT rows only
  (`services/entity_resolver.py:147-150`, `_DOMAIN_HINT_EXPANSIONS["product"]
  ["category"] = {"product"}`). "basin" is no product code, so zero matches.
- Fallback rerun: `api/v1/system/references.py:2267` continues because
  `fallback_to_all_types` is on; `:2300-2304` collects the zero-match tokens; `:2326-2343`
  reruns them with EVERY resolver kind except `attachment` and `cross_type_expand=True`
  (`entity_resolver.py:5170-5212` probes each remaining kind, tier 1 exact then tier 2
  prefix/substring, `PREFIX_LIMIT = 20` at `:1868`).
- Promotion rows match by `ILIKE '%basin%'` on `promotions.description`
  (`entity_resolver.py:2230-2253`, via `_norm_contains` `:793-805`). Customer rows match
  by `ILIKE '%basin%'` on `customers.customer_name` (`:1117-1134`). The crew scout counted
  31 promotion rows (PDF filenames such as "6. Wash Basin.pdf", "CABANA BASIN TAP
  PROMO_...pdf") and 1 customer row on dev; this sandbox has no path to dev, so those two
  counts are the scout's and the SQL to re-check them is in "Examples" below.
- The engine counts hits per kind per token (`services/chatbot/turn_runtime.py:2092-2102`,
  `by_token`); `turn/reconcile.py:65-116` builds `kind_pick_options` when two or more
  kinds hit; `turn/apply.py:1766-1782` arms `pending_ask("kind_pick")` reading neither
  `hint` nor `confident`; `turn/compose.py:958` prints "Which one do you mean?".
- No guard exists: `grep hint_confident|kind_hint|pin_kind` over the resolver and turn
  files returns nothing (re-run for this card).
- The SPEC search never ran on that turn. `references.py:3090-3098` runs the spec
  fallback only when `not _every_caller_token_resolved(result)`; in OR mode that helper is
  "every token's own resolution carries at least one match" (`:1898-1901`). "basin"
  carried 21 promotion/customer matches, so the one path that knows "basin" as a product
  class (`services/product_spec_search.py:1102-1120`, `resolve_classes_for_term`) was
  skipped. The re-type did two things at once: it armed the question AND it hid the
  answer.
- Side fact, same lines: `_no_product_row_answered` (`references.py:1953-1982`, "a
  promotion is not stock", written for "any water closet s trap 250mm" matching eleven
  promotions) sits behind that same `_every_caller_token_resolved` guard, so for a
  single-token product question whose only matches are promotions it can never fire.
  Not this lane's to fix; it is why the fix has to happen BEFORE the resolver answers.
- Owner-ruled D2 control, green today and must stay green without a ruling:
  `tests/chatbot/test_rearch_r11_catalogue.py:486-504`
  `test_control_two_real_kinds_under_a_non_scope_hint_mint_the_kind_pick` ("cabana
  stock", CONFIDENT `product` hint, one promotion hit and one attachment hit, no product)
  still asks "Which one do you mean?".
- Neighbour lane #1504 (BRAND-PROMO-EXPAND, fold only, done): a `brand` hit under a kind
  the hint fans out to is counted under the hint at the `by_token` seam in
  `turn_runtime.resolve_kinds`. It leaves `category` on today's reading ("no evidence")
  and does not touch the resolver request. This lane is that evidence, for the class
  hints only; it touches neither `turn_runtime.py` nor #1504's tests.
- The class hints have one name already: `turn_runtime.CLASS_HINTS = ("product_type",
  "category")` (`turn_runtime.py:3196`), the same pair `resolve_gate.py:691` forwards as
  `scope_terms`.

## Rules (proposed; R1 and R2 are the owner's words, R3 to R5 are how they land)

R1. A confident parser class hint (`product_type` or `category`) keeps its kind. The
    resolver's fallback rerun may not re-type that word to promotion, customer,
    transporter, form or any other kind by name match.
R2. No word matching is added anywhere. The parser's `hint` and `confident` decide which
    tokens the rule covers; the engine never reads the message text for it.
R3. With its kind kept and zero code hits, the class word stays an unresolved token, so
    the resolver's existing spec/class search runs over the sentence
    (`references.py:3090`): "basin" + thickness 1.0 answers with the basins at 1.0mm, or
    the existing zero-set wording when none qualify (near miss on the other thickness
    values, `answer.py` "Couldn't find: ..."), or the existing "I don't know 'X' as a
    product type" when the registry has no such class. No new wording.
R4. A RECORD hint (`product`, `customer`, `order`, ...) is untouched: a mishinted code is
    still re-typed to the one kind that hit (AC-1527, "7445" hinted order -> product) and
    two kinds hitting still ask (D2, "cabana stock"). `brand` stays #1504's.
R5. Never a stuck state: the turn either answers from the spec tier, says the miss in the
    existing wording, or asks the existing spec/class question; it never asks a kind pick
    over a class word and never fetches an unscoped catalogue.

## Seam options

S1. `resolve_gate.py:620`: send `fallback_to_all_types: False` when the message carries a
    confident class hint. Per REQUEST, not per token: `references.py:2267` returns before
    the rerun for EVERY token, so "basin srtwc286-x" (class word beside a mishinted code)
    loses the AC-1527 re-type for the code. Coarse; covers the recorded turn only.
S2. `turn/reconcile.py:65-116`: a confident class-hinted entity is never rewritten and
    never arms a kind pick, whatever kinds hit. Stops the question, but the resolver still
    reports "basin" as resolved (to promotions), `_every_caller_token_resolved` stays True
    and the spec search still never runs; the gate then carries 20 promotion rows the
    domain cannot use and the fetch has no product scope. Not sufficient alone.
S3 (recommended). One optional field on the resolver request, built at `resolve_gate.py`
    beside `allowed_entity_types`: the tokens whose entity is a confident class hint,
    e.g. `keep_kind_tokens: ["basin"]`. `references.py:2300` leaves those tokens out of the
    per-token rerun (one `if` in the `unresolved` list), so they stay in
    `unresolved_tokens` and the spec fallback runs exactly as it does today for a class
    word that happens to match no promotion name. Absent field = byte-identical response
    for n8n, MCP and every other caller. Per token, so a code typed beside the class word
    keeps its own fallback. It touches `references.py` (two lines, additive), which is
    outside the lane's named ownership of `turn/reconcile.py` and
    `resolve_gate.py:600-640`: Q2 asks for that.
    Belt: S2 can be added as a second guard so no later producer of promotion hits under
    a class token can mint the question. Recommendation: S3 alone (one mechanism, one
    fact); S2 only if the owner wants the composer side pinned too.

## Examples (dev dry runs not runnable from this sandbox; stated as read from the code)

1. "any basin with thickness 1.0mm" (owner's dev turn 4d18caff). Today: "Which one do
   you mean? 1. basin (promotion) 2. basin (customer)". After: "basin" stays unresolved
   in the code tiers, the spec fallback reads the sentence with `extracted_specs`
   thickness 1, and the reply is the existing spec-set answer (basins at 1.0mm) or the
   existing zero-set line naming the thickness values basins do come in.
2. "basin" alone (category, confident, master_products). Today: the same two-way kind
   pick (the same 31 + 1 rows). After: the spec fallback with no spec bound; the existing
   class listing/ask for Wash Basin. Never a kind pick.
3. "cabana basin tap". Parser decides the split (brand "cabana" + class "basin tap", or
   one class phrase). The brand word is #1504's (folded to brand); the class words are
   this lane's (kept, spec search reads "basin tap" as a tap class per
   `product_spec_search.py:1102-1110`). Today on dev the class words would meet the
   "CABANA BASIN TAP PROMO" filenames in the rerun and ask a kind pick.
4. "SORENTO ZZWC8318" (ROGUE note, class D). Brand + product code, no class hint. This
   lane changes nothing on it; #1504 answers it. Control.
5. "cabana stock" (D2 control, `test_rearch_r11_catalogue.py:487`). Product hint,
   promotion + attachment hits, no product. Still asks "Which one do you mean?". Control,
   pinned by the existing test; under S3 the product-hinted token is not in
   `keep_kind_tokens`, so its rerun is unchanged.

SQL for crew to re-check the scout's dev counts (read only):
`SELECT count(*) FROM promotions WHERE description ILIKE '%basin%';` and
`SELECT count(*) FROM customers WHERE customer_name ILIKE '%basin%';`.

## Edge cases

- Class word beside a code ("basin SRTWC286"): the code resolves, the class word stays
  unresolved, the spec fallback runs as it does today for any class word with no name
  hit. Unchanged shape.
- Class hint with `confident: false`: not covered; today's rerun applies (the parser was
  unsure, the resolver may say what the word is). Owner may widen (Q3).
- ORDER / sales domain: the PRIMARY pass itself expands a `category` hint to customer,
  customer_order and transporter (`entity_resolver.py:131-134`), so "basin orders" meets
  the customer row in the first pass, not the rerun. S3 does not reach it. Out of scope
  here unless ruled (Q4); #1504 kept the same expansion for `brand` on purpose.
- A class word that is literally a product code: the primary pass pins category to
  product in product domains and matches it by code. Unchanged.
- A kind pick already open from an earlier turn: not touched; `_answer_pending` runs
  before any of this.
- The rerun keeps its `attachment` exclusion (`references.py:2326-2331`) and its
  trigram neighbours merge for a token that resolved nowhere (AC-1703, `:2368-2389`):
  a kept class word still receives alternatives; it only receives no MATCH of another
  kind.

## Questions (max 5; crew-ask on the lane PR)

Q1. Confirm the rule's reach: a CONFIDENT `category` / `product_type` hint only. Should the
    same apply to a confident `product` hint ("cabana stock"), which would change the
    owner-ruled D2 control? (a) class hints only, D2 stands (recommended: a product hint
    names a RECORD and the record can be of another kind, which is what AC-1527 and D2
    exist for; a class hint names a set and a set is never a promotion row); (b) product
    too, D2 superseded.
Q2. Seam: (a) S3, one optional request field read at `references.py:2300`, two additive
    lines outside this lane's named files (recommended: it is the only seam that both
    stops the question and lets the spec search run, and an absent field is
    byte-identical for every other caller); (b) S1 per-request flag inside
    `resolve_gate.py:620` only, accepting that a code beside a class word loses its
    re-type; (c) S3 plus S2 as a second guard in `reconcile.py`.
Q3. `confident: false` class hints: (a) leave today's rerun (recommended: the parser said
    it was unsure, and the rule as you stated it is about a confident category); (b)
    cover every class hint regardless.
Q4. ORDER/sales primary-pass expansion of `category` to customer/transporter
    (`entity_resolver.py:131-134`, shared with n8n/MCP): (a) out of scope, note it in the
    plan as the next trigger (recommended: no incident yet, #1504 kept the brand twin for
    the "super ceramic" customer case); (b) in scope now.
Q5. Reply when the spec search knows no such class (registry has no "basin" synonym): (a)
    existing "I don't know 'basin' as a product type" wording, no change (recommended: it
    exists and is tested); (b) new wording.
