# UAC: promotion asks filter by subject; unresolved subject asks back; tier menu always stamped (PROMO-SUBJECT-FILTER)

Plan: `PLAN-promo-subject-filter-07oct.md`. Card: `promo-subject-filter-behaviour-card.md`
(rulings: Q1 (a), Q2 (a), Q3 (a); Q4 and Q5 held, so R5 format words and R6 brand echo are
NOT in this round). Tests in `sorento_crm_backend/tests/` (chatbot ones under `tests/chatbot/`).
Seeded data only (CI DB is empty): seed brands, categories, products with a
`product_specifications.values.class` value, promotions with `access_levels`, dates and
`promotion_products` links. Never real rows.

## R1: subject filter by linked products (Q2 (a))

AC-1  `GET /api/v1/marketing/promotions` accepts a subject filter (the grounded class labels /
      scope terms the chatbot already sends the resolver). Only promotions with at least one
      `promotion_products` row whose product is in that class are returned. A promotion with no
      linked products never matches a subject. No match on promotion description or file name.
AC-2  Subject filter matching nothing returns an empty list: the route's zero-match fallback to
      inactive/expired promotions (promotions.py:88-93) does NOT apply when a subject filter is set.
AC-3  Subject filter ANDs with `access_levels`, brand and the active/date window.
AC-4  The MCP promotions tool (`sorento_crm_mcp/catalog.py` promotions entry) exposes the same
      param; the chatbot's promotions fetch (`lanes/business/fetch.py` TYPE_TO_PARAM path) sends
      it for a grounded `product_type` / `category` entity, both on the tier probe and on the
      main fetch.
AC-5  Example: seeded "Bathroom Furniture" promo A (linked to a cabinet product, levels office +
      dealer) and "Kitchen Sink" promo B (linked to a sink, level office). Ask "any bathroom
      furniture promo", pick Office: reply carries promo A only, never B.
AC-6  The subject survives the tier pick: after the menu, the "1"/"2" turn fetches with the same
      subject filter as the asking turn.

## R3: unresolved subject asks back (Q1 (a))

AC-7  A promotion ask whose subject grounds to an unknown product type (e.g. "any basin cabinet
      promo" where "basin" and "cabinet" name two classes, grounding.py:670-678) gets NO tier menu
      and NO files. The reply says what did not match and asks, one fact per line, no dot
      separators, e.g.:
      `I could not match "basin cabinet" to a product type.`
      `Which product or brand do you mean?`
      This beats the tier-ask bridge (engine.py:5539-5557).
AC-8  The promotions tool is not called on that turn.

## R4: the tier menu is always stamped (Q3 (a))

AC-9  Every path that shows the "Which access level..." menu has run the tier probe with the same
      subject filter; each level line ends "- has promotion" or "- no promotion". The bridge path
      (not_found / access_ask with tier_ask, answer_bridge.py:636-676) included.
AC-10 Levels: a level with zero matching promotions stamps "- no promotion". If every held level
      has zero, no menu: reply `No current promotion for <subject as typed>.`
AC-11 Probe fails (tool error / timeout): no menu, no files; reply
      `I could not check promotions right now.` plus a retry hint line.

## Must not regress

AC-12 Ask with no subject ("any promo") behaves as today (menu stamped per AC-9, all promos at
      the level). Product-code asks (product_ids path) unchanged.
AC-13 `test_water_tap_basin_is_one_unknown_product_type`, round4 `test_r5_*`,
      `test_replay_the_owners_eight_exchanges`, `test_bound_spec_words_never_reach_the_described_sets_scope_term`,
      `test_exact_classes_runs_once_per_distinct_free_term`, test_spec_grounding_prompt stay green.

## Live console eval (after green)

AC-14 Turn 1 "any basin cabinet promo" -> AC-7 reply (no menu, no kitchen sink file).
AC-15 Turn 2 "sroento a3 flyer" -> menu stamped (AC-9); pick Dealer -> Sorento dealer promos only
      (format word and typo echo unchanged this round, held for Q4/Q5).
