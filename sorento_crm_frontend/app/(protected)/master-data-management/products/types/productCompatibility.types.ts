/** Accessory <-> host compatibility: seat cover/WC, mirror/cabinet, shelf/cabinet. */

export type CompatibilitySource = 'flyer' | 'manual';
export type CompatibilityKind = 'seat_cover' | 'mirror' | 'shelf';
/** What the OTHER product is: the accessory (seat cover, mirror, shelf) or the host (WC, cabinet). */
export type CompatibilityRole = 'accessory' | 'host';

export interface ProductCompatibilityRow {
  id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  source: CompatibilitySource;
  is_discontinued: boolean;
  kind: CompatibilityKind;
}

export interface ProductCompatibilityResult {
  /** The accessories that fit this product (as the host), every kind. */
  covers: ProductCompatibilityRow[];
  /** The hosts this product fits (as an accessory), every kind. */
  fits: ProductCompatibilityRow[];
}
