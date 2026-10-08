# UAC: promotion asks filter by subject; unresolved subject asks back; tier menu always stamped (PROMO-SUBJECT-FILTER)

Plan: `PLAN-promo-subject-filter-07oct.md`. Card: `promo-subject-filter-behaviour-card.md`
(rulings: Q1 (a), Q2 (a), Q3 (a), Q5 (a); Q4a (a) and Q4b (a) by the owner 7 Oct: an A3 flyer is a promotion KIND, promotion_types). Tests in `sorento_crm_backend/tests/` (chatbot ones under `tests/chatbot/`).
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

## R6: the menu names the real brand (Q5 (a), owner 7 Oct)

AC-16 When the parser resolved a brand (canonical brand entity, or the tier gate's brand narrowing,
      tier_gate.py:76-118, 208-223), the access-level question names the brand's real name, e.g.
      "sroento" -> `Which access level do you need for Sorento?`. The typed word is echoed only
      for an entity that did not resolve to a brand (D16 stays for products: never name a guessed
      product code, answer.py:303-312).

## R5: promotion kinds (Q4a (a), Q4b (a), owner 7 Oct)

AC-19 The parser reads a named promotion kind against the promotion_types list (type names from the
      DB: Standard Promo, Special Promo, A3 Flyer, Focus Item, PP Promo) into a structured field
      (new dev prompt version, never labelled production). A bare "flyer" / "promo" / "brochure"
      names NO kind (Q4b (a)): every kind is in play.
AC-20 `GET /api/v1/marketing/promotions` accepts `promotion_types` (type codes) and `brand_ids`;
      brand means a brand of a LINKED product (promotion_products -> products.brand_id). Both AND
      with scope_terms, access_levels and the window. MCP promotions tool exposes both.
AC-21 Kind + brand with no LIVE promotion (is_active and inside its window) at the customer's levels:
      no tier menu, no files. Reply, one fact per line:
      `There is no Sorento A3 flyer right now.`
      then the live promotions of the same kind from other brands the customer may see, by name:
      `Other A3 flyers:` / `MOCHA A3 FLYER 02102026` / `MOCHA A3 Flyer 03082026`
      (one line per distinct promotion name; two rows with one name are one line). No other brand
      has a live one: the first line only. Brand name is the canonical name (Q5).
AC-22 Kind + brand WITH live promotions: normal stamped tier menu narrowed to that kind and brand;
      the pick sends only that kind and brand.

## Must not regress

AC-12 Ask with no subject ("any promo") behaves as today (menu stamped per AC-9, all promos at
      the level). Product-code asks (product_ids path) unchanged.
AC-13 `test_water_tap_basin_is_one_unknown_product_type`, round4 `test_r5_*`,
      `test_replay_the_owners_eight_exchanges`, `test_bound_spec_words_never_reach_the_described_sets_scope_term`,
      `test_exact_classes_runs_once_per_distinct_free_term`, test_spec_grounding_prompt stay green.

## Live console eval (after green)

AC-14 Turn 1 "any basin cabinet promo" -> AC-7 reply (no menu, no kitchen sink file).
AC-15 Turn 2 "sroento a3 flyer" -> AC-21 reply: `There is no Sorento A3 flyer right now.` then the
      two live Mocha A3 flyers by name; no menu, no Sorento standard promo.
AC-17 (owner acceptance check) A promo ask for a product code (a kitchen sink code linked to a live
      promotion) still returns that product's promotion for the picked level (product_ids path).
AC-18 (owner acceptance check) "kitchen sink promo" returns only promotions linked to kitchen sink
      products for the picked level; no bathroom furniture, WC or tap promotion.

## Owner review round 2 (8 Oct 2026, copy, prompt v72)

AC-22 (supersedes the live-only part of AC-21, owner: "even the flyer need to show even though it
      is inactive, same principle with promotion"). A kind ask (and the "Other <kind>s" list) is
      decided by the same serving rule as every promotion (promotion_serving.py: live rows win; a
      type that allows it serves its latest expired rows, e.g. a3_flyer show_expired, 180 days),
      expired rows marked "(EXPIRED)" as today. "There is no <Brand> <kind> right now." only when
      the serving rule serves nothing for that brand and kind.
AC-23 (supersedes AC-10's all-zero line for a subject that grounds to nothing): a promotion subject
      that does not ground to any class, product type or spec value ("kitchen cabinet", "seat
      cover" before AC-26) asks back as AC-7, never "No current promotion for <subject>.". "No
      current promotion for X." stays only for a subject that grounds but has no promotion.
AC-24 A subject grounds against spec attribute values too (spec registry, e.g. furniture_type
      basin_cabinet with its synonyms), exactly as the stock path does; "basin cabinet promo"
      filters to promotions linked to products with furniture_type basin_cabinet. Data-driven, no
      word rules in code.
AC-25 A subject that maps to several distinct product types / spec values ("cabinet": basin
      cabinet, mirror cabinet, side cabinet, ...) asks which, listing the options, never a
      confident mixed list.
AC-26 "seat cover promo" grounds to the toilet seat type through the parser prompt (wording, not
      code) and returns toilet seat promotions (dev data: Office 3, Dealer 2, End user 2).
AC-27 One consolidated new dev parser version (never labelled production) carries every current
      addendum including promotion kind and the seat-cover grounding; the migration publishes it.
AC-28 Live console check covers the owner phrases: "kitchen cabinet promo", "sorento cabinet
      promo", "seat cover promo", "basin cabinet promo", "any sorento a3 flyer?".

