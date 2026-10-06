# PLAN: a brand word never becomes a stored promotion (BRAND-PROMO-EXPAND)

Status: built on PR #1504, reviewer round 1 applied (the two crew additions reverted on
measured regressions, raised as a crew-ask), lane file and related chatbot suites green
(track: small fix, backend only, no migration, no auth/RBAC change, no new ingest surface,
no UI). UAC: `brand-promo-expand-acceptance-criteria.md`.

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

## Crew additions (prod trace of the photo turn, 6 Oct 2026)

- (A) The resolver gate under inventory answered SORENTO with 20 `promotion` matches and zero
  brand matches although the product's `brand_code` is SORENTO. Cause: the shared resolver has
  no brand probe; the chatbot's brands-table lookup (`turn_runtime._brand_hinted_entities_
  matching_live`) ran for the order and sales domains only (`turn_runtime.py:2021-2028`).
- (B) A candidate whose kind the gate rejected for the domain (`gate_debug.incompatible_only`)
  must never be written into focus state.

## Change (code only, one seam: `chatbot/turn_runtime.py::resolve_kinds`)

- `by_token` (what reconciliation reads): a match under a kind the hint fans out to
  (`entity_resolver._ENTITY_TYPE_EXPANSIONS`, imported, not copied) is counted under the hint,
  so brand stays brand and category stays category. The match itself still reaches the fetch
  through the gate's `compatible_entities` (the promotion fetch is derived there, nothing is
  persisted); under inventory the gate already rejects it as incompatible. The domain overrides
  (brand -> customer under order) are not folded: that re-type is the intended reading there.
- A BRAND hint outside order/sales goes one step further (ROGUE-CONVO-REVIEW shape, prod 6 Oct
  18:50 MYT: "SORENTO S-TRAP 250MM ONE PIECE WC-ZZWC8318-RL-250" armed "Which one do you mean?
  1. SORENTO (promotion) 2. SORENTO (customer) 3. SORENTO (form) 4. SORENTO (transporter)"
  under inventory): every hit the resolver answers a brand word with counts as the brand. The
  resolver has no brand probe, so none of those kinds can contradict the parser's confident
  hint; no kind pick is armed, no row of those kinds reaches the focus, the product is the
  subject. Under order/sales the resolver's own override (brand -> customer / transporter) is
  the intended reading and is left alone; category hints keep today's reading beyond the
  promotion fold.
- `turn/reconcile.py` untouched: the pure core keeps its contract and one seam decides what the
  resolver's answer means.
- Sessions already holding a stale `focus.extra["promotion"]` row are not repaired here; PR
  #1502 keeps a task fill turn from handing that carry to the resolver, and the row stops being
  written from this lane on.

## Tried and reverted (reviewer round 1, measured in a throwaway worktree)

- Crew (A) as "brands table first in every domain but promotion": a brand-only inventory ask
  ("SORENTO stock") then skipped the resolver AND the gate (the entity-less early return) and
  answered the generic "I need at least one filter" line where main answers "Here's what you
  want: Sorento products with stock / But none matched"; and
  `brand_rows_for_word`'s spelling-distance tier (`turn_runtime.py` ~590-608) started deciding
  brands in nine more domains ("Sorrento" became Sorento under master_products), against the
  owner's no-fuzzy rule. It also meant rewriting the Security B1/SF3 pin
  `test_brand_strip_is_order_domain_only`. No UAC needs it: with it reverted the photo shape
  still passes for the live brand. The cause crew asked about stands as found: the shared
  resolver has no brand probe (`entity_resolver.py:113-121`); fixing THAT is a resolver change
  with n8n/MCP callers and is not this lane's.
- Crew (B) as "drop every gate-rejected kind from `resolved_kinds`": broke the owner-ruled D2
  kind-pick control (`test_rearch_r11_catalogue.py`, "cabana stock" hinted product hitting a
  promotion and an attachment must still ask "Which one do you mean?") and dropped the typed
  word in silence (not in `unresolved_tokens`, so never said back). For the traced shape the
  fold already keeps the incompatible promotion candidate off the focus; the generic shape (a
  product-hinted code re-typed to a single incompatible kind) has no prod evidence and needs an
  owner ruling against D2 before it is built.

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
