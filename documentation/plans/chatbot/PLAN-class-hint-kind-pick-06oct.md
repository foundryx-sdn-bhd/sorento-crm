# PLAN - a confident class hint is never re-typed by name match (CLASS-HINT-KIND-PICK)

Status: BUILT, in review (6 Oct 2026). Owner Q1 ruled "cabana is a brand": reach is
category / product_type / product / brand, plus any word the catalogue knows as a brand
exactly; D2 superseded. Fix green on the sandbox (31 lane tests + rewritten r11 control),
kill test 24 red / 7 controls green on origin/main, before/after replay table posted on
PR #1509. Track: small fix (backend only, no migration, no auth/RBAC change, no new ingest
surface, no UI). Card: `CARD-class-hint-kind-pick-06oct.md`. UAC:
`class-hint-kind-pick-acceptance-criteria.md`. Hand test: `laneboard/scripts/1509.md`.

Built beyond the first draft below, per the owner's Q1 ruling: (1) `turn_runtime.
exact_brand_rows` (the first tier of `brand_rows_for_word`, extracted) and
`entities_naming_a_live_brand` over every hint; `resolve_kinds` locks those tokens and
counts them as the brand so reconcile keeps or rewrites the hint to brand; (2) the lock
covers confident `product` and `brand` hints too (`resolve_gate.HINT_LOCKED_KINDS`); (3)
`references.py` strips the five name-matched kinds for a locked token in the primary pass
as well (brand -> promotion fan-out), keeping the request domain's own kind; (4) a brand entity is
RESOLVED as the brand with no hits needed (owner ruling 6 Oct 2026, replacing the extra
rule (b) "brand word beside a matched code closes the spec fallback", REJECTED as "too
deterministic" and removed with its three tests): the body sends `brand_tokens`
(`{token: brand name}`), `references._resolve_brand_tokens` answers the token with one exact
`brand` match, so it is never in `unresolved_tokens` and the spec fallback does not open for
it beside a code ("SORENTO SRTWC8318" answers the code alone; the HAS branch for a brand-only
ask is untouched). Owner rule recorded: only unresolved tokens go to spec search; if on real
data the fallback still opens for a brand word, that is a follow-up for ROGUE class C / the
whole-sentence spec change, never a code gate. (5) Brand identity keys on the parser's
`canonical_code` (owner clarification "what if typo, what if SRT people type"): the Known
brands name the prompt's KNOWN BRANDS rule maps the typed word to, raw only when empty,
never a spelling tier in code, never a non-confident entity (`turn_runtime.
brand_identity_word`). A shorthand the prompt does not map is a prompt addendum, not code.

Crew rulings on the card (6 Oct 2026): Q2 = S3 (one optional resolver request field; the
two `references.py` lines are this lane's), Q3 = (a) non-confident class hints keep
today's rerun, Q4 = (a) the order/sales primary-pass expansion of `category` to
customer/transporter is out of scope and is this plan's named next trigger, Q5 = (a)
existing wording only. Q1 pending.

## The defect in one line

`references.py:2267-2343` reruns a zero-hit token against every kind, so "basin" (hinted
`category`, confident) came back as 31 promotions and 1 customer by `ILIKE '%basin%'`;
the engine then asked a kind pick, and because the token now counted as resolved the
spec/class search that knows "basin" as Wash Basin never ran (`references.py:3090`).

## Change (S3)

1. `app/services/chatbot/lanes/business/resolve_gate.py::resolve_entity_body` (lines
   600-640 region): beside `allowed_entity_types`, add `hint_locked_tokens`: the
   `_token_of` value of every entity whose hint is in the locked set and whose
   `confident` is not False. Key present only when the list is non-empty, so every body
   with no class hint is byte-identical to today. The locked set is
   `turn_runtime.CLASS_HINTS` (`product_type`, `category`), imported, never a second copy;
   under Q1 reading (b) it also includes `product`.
2. `app/api/v1/system/references.py`: `ResolveReferenceRequest.hint_locked_tokens:
   list[str] | None = None` and, at the one place the per-token rerun collects its tokens
   (`:2300-2304`), leave those tokens out of `unresolved`. They stay in
   `unresolved_tokens`, so the spec fallback at `:3090` runs as it does today for a class
   word that matches no promotion name. `_resolve_input` gains the parameter; the GET
   route does not expose it (n8n/MCP shapes unchanged).
3. No change to `turn/reconcile.py`, the parser prompt, or any wording. `turn_runtime.py`
   changes only as the "Built beyond the first draft" paragraph above says (the exact brand
   read and the lock; #1504's `by_token` loop is untouched). No word matching anywhere: the
   field is built from the parser's own `hint` and `confident` and the catalogue's brand
   rows. Domain exceptions to the strip, stated once: the request domain's own kind
   (promotion under promotion, attachment under resource_attachment) and, per crew ruling
   Q4, customer and transporter under order/sales (the resolver's own expansion there, and
   #1504's control). The owner's literal rule would remove the order/sales exception; that
   is the Q4 trigger below, not this lane's call.

## Tests (red first, `tests/chatbot/test_class_hint_kind_pick.py`)

Red on origin/main in the sandbox (real engine, real resolver, blank schema seeded in the
dev shape: two promotion PDFs and one customer named with "basin", one Wash Basin class
with two products):
- the recorded turn "any basin with thickness 1.0mm" answers the owner's exact text
  "Which one do you mean? 1. basin (promotion) 2. basin (customer)" today; after, never;
- "basin" alone, the same;
- the resolver's answer carries no promotion/customer match under "basin", only
  `spec_search` rows, and the products tool receives exactly the two basin ids;
- the body carries `hint_locked_tokens: ["basin"]` for `category` and `product_type`,
  not for a non-confident class hint, and not at all with no class hint;
- the route leaves a locked token out of the rerun, keeps a mis-hinted customer code's
  own rerun beside it (per token, not per request), and answers an absent field and an
  empty list alike.
Controls green on both: the seed reproduces dev (the rerun re-types "basin" without the
field), D2 `test_rearch_r11_catalogue.py::TestGenuinelyAmbiguousWordStillAsks` unchanged
under reading (a).

Then: kill test (fix reverted, the targets red again), opus reviewer, console replay
table on the sandbox for the five card examples, crew-handtest.

## Owner evidence turn (replay case 13, "which tap has technical drawing" -> "sorentooooo?")

On the sandbox after the fix: no kind pick (before: "Which one do you mean? 1. Sorento
(customer) 2. (promotion) 3. (form) 4. (transporter)" on the owner's console), the brand
entity is Sorento via canonical_code and lands on `focus.brands`. The carried set did NOT
narrow to Sorento taps here: the second turn re-fetched the carried set by its ids (three
taps, one of them Cabana's). Narrowing a carried set by a brand named on a later turn is
the set-carry / narrow seam (`turn_runtime.set_page_carry`, `turn/narrow.py`), not the
resolver lock; follow-up to raise with ROGUE class C (#1506), where the ~200-code dump
above the question also belongs.

## Seen on the replay, not this lane's (identical before and after)

"water tap" (category, master_products): the resolver answers three spec-tier taps, the
engine fetches `crm_master_products_list` with no ids and the miss composer prints
"Here's what you want: / • product: X (+2 more) / But no master products matched these."
ROGUE-CONVO-REVIEW #1506 classes A (zero-scope fetch) and C (set members under a miss
header) own it.

## Next trigger (Q4, out of scope)

Under `order` / `sales` the PRIMARY pass expands a `brand` or `category` hint to customer,
customer_order and transporter (`entity_resolver.py:131-134`, shared with n8n/MCP), so such
a word there meets customer rows in the first pass, not the rerun. This lane keeps that
reading (`references._name_kinds_dropped_for`, the `customer_order` exception, pinned by
`test_order_keeps_the_resolvers_customer_reading_for_a_locked_word`). Build the change the
day the owner rules on it or a recorded order-domain turn re-types a class word.
