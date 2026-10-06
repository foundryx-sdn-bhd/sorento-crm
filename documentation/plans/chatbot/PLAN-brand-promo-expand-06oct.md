# PLAN: a brand word never becomes a stored promotion (BRAND-PROMO-EXPAND)

Status: in progress (track: small fix, backend only, no migration, no auth/RBAC change, no new
ingest surface, no UI). UAC: `brand-promo-expand-acceptance-criteria.md`.

## Owner trace (6 Oct 2026, prompt v47, turn 120 then 121)

Turn 120 read a photo (SRTWC8383-RL-GY + SORENTO) and settled the product on `focus.products`.
The brand word was left on `focus.extra["promotion"]` as
`{raw "SORENTO", hint "promotion", canonical_code "SORENTO", confident true}`. Turn 121 ("1",
`entity_op reuse`) handed that row to the resolver again (PR #1502 fixes the fill turn's side;
this lane fixes the source of the stale row). Owner rule: "Sorento is always a brand."

## Evidence (crew scout, dev DBs, 8,532 turns, read-only)

- The parser emitted `hint: promotion` 28 times; none was a brand word. The parser is not the
  source.
- Seven promotion rows on INVENTORY turns ("sorento" x3, "sorento wc" x4) all trace back to a
  10 Sep turn where the parser said `hint: brand` / `category` and the stored row came back
  typed `promotion`.

## Root cause (measured on origin/main 9373f6c9)

Paths under `sorento_crm_backend/app/services/`.

1. `entity_resolver.py:118-121` `_ENTITY_TYPE_EXPANSIONS`: a `brand` or `category` allowed
   type fans out to the `promotion` probe. It exists so "Sorento promotions" lists that brand's
   promotions. The resolver therefore answers a brand token with matches whose `entity_type` is
   `promotion`.
2. `chatbot/turn_runtime.py:2092-2102` `resolve_kinds` counts those matches into
   `by_token[token][entity_type]`, so a brand word that hit only promotions reads as
   `{"promotion": n}`.
3. `chatbot/turn/reconcile.py:83-90` `apply_reconciliation`: one kind hitting that differs from
   the parser's hint REWRITES the hint. Brand -> promotion. The row is then stored under
   `focus.extra["promotion"]` by `turn/apply.py::_set_kind_field` (`:126-139`).
4. `chatbot/turn_runtime.py:3700-3706` `with_carried_entities` re-emits every `focus.extra`
   row with `hint = <bucket kind>`, so the next turn (reuse or replace_combine) sends a
   `promotion`-hinted "sorento" to the resolver under whatever domain is active.

## Rule

- The parser decides hints. A resolver hit reached ONLY through the brand/category ->
  promotion expansion confirms the hint; it never re-types it. The carried row keeps
  `brand` / `category`.
- The expansion itself stays where it is intended: the promotion domain's own fetch still
  lists a brand's promotions.
- A brand row carried into a non-promotion domain behaves as that domain's narrowing says
  (inventory narrowing = product list_all): a brand filter where the tool takes one, otherwise
  ignored. Never a promotion.
- No typo, word or fuzzy matching in code. No parser prompt change.

## Change (code only)

- `turn/reconcile.py`: a hit under a kind the hinted kind EXPANDS to (brand/category ->
  promotion) is not "another kind"; the hint stands and no `reconciled` tuple is traced.
- Where the brand/category -> promotion fan-out would produce a stored `promotion` row with no
  promotion asked, derive the promotion candidate at fetch time instead of persisting it.

## Tests (red first)

`tests/chatbot/test_brand_promo_expand.py`:

1. Reconciliation unit: `entity("sorento", hint="brand")` with `resolved {"sorento": {"promotion": 2}}`
   keeps `hint: brand`, lands on `focus.brands`, `trace.reconciled == []`; same for `category`.
2. Engine replay, two turns: A "sorento wc promotion?" (brand + category, domain promotion),
   resolver answers both tokens with promotion rows; B "check stock" (reuse). After A the focus
   holds no `extra["promotion"]` row for either word; the turn B inventory fetch carries no
   promotion token and no `promotion_ids`.
3. Promotion domain with a brand entity still lists that brand's promotions (the expansion
   still works where intended).
4. The prod photo shape: product (uuid) + brand SORENTO under inventory; after the turn the
   focus has the brand, not a promotion; the fill turn "1" renders the availability line.
