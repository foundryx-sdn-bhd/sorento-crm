# UAC: PRICE-TAG-A3-EXPIRED

Plan: `PLAN-price-tag-a3-expired.md`. Tests: `sorento_crm_backend/tests/test_price_tag_expired_promotion_grace.py`,
`sorento_crm_frontend/lib/dealer-kit/line-pricing-types.test.ts`.

- AC-1 An A3 flyer that ended N days ago (N <= 180), switched off by the window task, still
  prices a line: it is offered, auto-picked when nothing live covers the product, and the
  line's sell price is its SP.
- AC-2 On day 181 the same flyer is not offered and the line prices at list.
- AC-3 A Standard (or unclassified, via the default type) promotion that ended this calendar
  year still prices; one that ended last year does not.
- AC-4 A Special promotion never prices after its end date.
- AC-5 A promotion switched off inside its window does not price.
- AC-6 A live promotion beats an ended one for the same product, even if the ended SP is lower.
- AC-7 On a multi-product line where a live promotion and an ended one each cover a different
  product, both are offered, and the live one is auto-picked even when the ended one totals lower.
- AC-8 Among ended promotions of one type, only the latest end date is offered.
- AC-9 Audience is filtered before the latest-wins ranking: a dealer keeps the DEALER flyer
  even when an audience-hidden flyer ended later.
- AC-10 A saved line keeps its ended promotion while it is inside its grace, even when a newer
  one exists; past the grace it prices at list.
- AC-11 A catalogue tile (`resolve_prices`) shows the same SP as the line, and drops the offer
  after the grace.
- AC-12 The tag render (`resolve_tags_live`) prints the ended flyer's SP with
  `show_promo_price` true.
- AC-13 The line-pricing route serialises `expired_on` (`YYYY-MM-DD`, null when live).
- AC-14 The tag sheet export allows an ended promotion inside its grace and returns 409
  `PROMOTION_EXPIRED` after it.
- AC-15 The dropdown and the read-only line text read `<description> (expired 30 Sep 2026)`
  for an ended option, the description alone for a live one.
