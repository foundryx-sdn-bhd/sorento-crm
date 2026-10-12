/** Seat cover <-> WC compatibility (WC-SEAT-COMPAT). */

export type CompatibilitySource = 'flyer' | 'manual';
export type CompatibilityRole = 'seat_cover' | 'wc';

export interface ProductCompatibilityRow {
  id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  source: CompatibilitySource;
  is_discontinued: boolean;
}

export interface ProductCompatibilityResult {
  /** The seat covers that fit this product (as a WC). */
  covers: ProductCompatibilityRow[];
  /** The WCs this product fits (as a seat cover). */
  fits: ProductCompatibilityRow[];
}
