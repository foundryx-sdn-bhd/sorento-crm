# PLAN: ended promotions keep pricing tags and tiles within their type's grace (PRICE-TAG-A3-EXPIRED)

Status: Build (small fix track: no migration, no auth/RBAC change, no new ingest surface). PR #1559.

UAC: `price-tag-a3-expired-acceptance-criteria.md` (alongside).

## Journey

A salesperson prints price tags for a dealer. Four Sorento A3 flyers ended 30 Sep 2026; the
hourly `promotion_active_window` task switched them off; ~787 SRT products whose only special
price (SP) lived on them started printing list price (e.g. SRT1000-CR list 84, SP 69 -> 84).
The owner ruling (10 Oct 2026): price tags keep the SP from an ended A3 flyer within the A3
type's existing 180-day rule.

## Root cause (base e0512282)

- `is_active=f` is set by the scheduled task `promotion_active_window`
  (`alembic/versions/139_seed_promotion_active_window_task.py`, handler
  `app/scheduler/task_scheduler.py:240`, registered `:728`), which runs
  `PromotionService.sync_promotion_active_by_calendar_window`
  (`app/services/marketing_service.py:1069`) and switches a promotion off once today is past
  its `end_date`. So `is_active` cannot be the test for an ended promotion: the type decides.
- Pricing (`app/services/dealer_kit/pricing.py`) kept only `is_active AND start<=today<=end`
  in `_offer_prices` and `_covering_promotions`; the type rule in
  `app/services/promotion_serving.py` (`_expired_row_allowed`, `evaluate`) was never called.
- The tag sheet export guard (`tag_sheet_export_service._check_promotion_expired`) returned
  409 `PROMOTION_EXPIRED` / `PROMOTION_INACTIVE` for any ended promotion.

## Owner answers (behaviour card, PR #1559)

1. Unified: catalogue/brochure tiles (every `resolve_prices` caller) follow the same rule as
   price tags.
2. A saved line keeps printing the promotion it was saved with while that promotion is inside
   its grace, even when a newer one appears.
3. Label: `<description> (expired 30 Sep 2026)`.
4. Export: allowed within grace, 409 only after.
5. Every type follows its own setting (A3 180 days; Standard/PP/Focus until 31 Dec of the
   year it ended; Special never). No per-type code.

## Design

- `pricing.promotion_may_price(db, promotion, today)`: live (`promotion_window.is_live`) or
  served by `promotion_serving.evaluate([promotion], ...)` as expired-but-usable. One
  money-only difference: an ended promotion with no type at all (no types configured, so no
  default) fails closed, where the chatbot serves it.
- `_offer_prices` (so `resolve_prices`, so every tile and tag render) judges the promotion
  through it instead of SQL window filters.
- `_covering_promotions`: audience filter (`_may_see_offer`) FIRST, then
  `promotion_serving.evaluate` on that set (type bound, latest end per type, a live row of a
  type suppresses that type's ended rows), then live beats ended per product across types (an
  ended promotion is offered only if it covers a product no live promotion covers). The
  line's saved promotion (`keep_id`) is kept while `promotion_may_price` says so.
- `line_pricing`: options carry `expired_on`; auto-pick sorts live before ended, then the
  existing (total, description, id).
- Export guard: an ended promotion passes when `promotion_may_price`; otherwise the existing
  409s.
- FE: `promotionOptionName` in `lib/dealer-kit/line-pricing-types.ts`, used by the CRM detail
  dropdown + read-only line text and the portal form dropdown.

## Not changed

- `tag_data_service._promotion_is_live` (the "Promotion ended" note in the tag-changes
  dialog): it only fires when the live offer is gone, which now happens after the grace, so
  the wording stays right.
- The chatbot / references serving paths: already on `promotion_serving`.

## Trigger for more

If an owner wants price tags and the chatbot to diverge per type, that is a column on
`promotion_types`, not code here.
