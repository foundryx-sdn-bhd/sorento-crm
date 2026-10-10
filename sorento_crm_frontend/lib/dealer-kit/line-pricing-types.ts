/**
 * Line-level pricing - shared wire types (PLAN-price-tag-line-promo-combo-subject.md D1-D4).
 *
 * A promotion (or a hand-typed price) lives on the LINE, not the header (D1);
 * one pricing call answers every line on a request at once (D4):
 * `POST /api/v1/public/portal/lookups/line-pricing` and
 * `POST /api/v1/dealer-kit/price-tag-requests/line-pricing` (S7, S12) both
 * answer `LinePricingResult[]` in this shape, one per input `key`. Both
 * service files (`app/(auth)/portal/lib/price-tag-request-service.ts`,
 * `app/(protected)/dealer-kit/services/priceTagRequestService.ts`) import
 * these types for their own `lookupLinePricing` return type - the ONLY thing
 * that still lived in `lib/dealer-kit/mock-line-pricing.ts` for a production
 * reader after S12 swapped that file's `computeLinePricing` for a real fetch
 * call. That function is now test-only (moved to
 * `app/(auth)/portal/components/__fixtures__/line-pricing.ts`); this file is
 * what is left for production to import.
 */

export type SellPriceBasis = 'manual' | 'promotion' | 'list';

export interface LinePricingPromotionOption {
  id: string;
  description: string;
  sell_price: number;
  /** `YYYY-MM-DD` when the promotion has ended but still prices inside its
   * type's grace (PRICE-TAG-A3-EXPIRED); null while it is live. */
  expired_on?: string | null;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * The promotion's name as the dropdown and the line text show it:
 * `<description> (expired 30 Sep 2026)` for an ended one still in its grace.
 * Parsed by hand, not through `Date`, so a browser west of UTC cannot show
 * the day before.
 */
export function promotionOptionName(option: LinePricingPromotionOption): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(option.expired_on ?? '');
  if (!match) return option.description;
  const [, year, month, day] = match;
  return `${option.description} (expired ${Number(day)} ${MONTHS[Number(month) - 1]} ${year})`;
}

export interface LinePricingCandidate {
  product_id: string;
  list_price: number;
  sell_price: number;
}

export interface LinePricingResult {
  key: string;
  list_price: number;
  promotion_options: LinePricingPromotionOption[];
  auto_promotion_id: string | null;
  sell_price: number | null;
  sell_price_basis: SellPriceBasis;
  parts_at_list: string[];
  candidates: LinePricingCandidate[];
}

export interface LinePricingLineInput {
  key: string;
  product_id: string | null;
  part_product_ids: string[];
  candidate_product_ids: string[];
  promotion_id?: string | null;
}
