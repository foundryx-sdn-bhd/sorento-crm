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

export type FlyerSkuStatus =
  | 'ready'
  | 'linked'
  | 'unresolved'
  | 'cover_missing';
export type FlyerUnresolvedReason =
  | 'not_printed'
  | 'ambiguous'
  | 'finish_not_printed'
  | 'sub_model_not_printed'
  | 'unknown_segment';

export interface FlyerSku {
  product_id: string;
  product_code: string;
  product_name: string;
  company_name: string | null;
  is_active: boolean;
  status: FlyerSkuStatus;
  reason: FlyerUnresolvedReason | null;
  printed_code: string | null;
  flyer_row: number | null;
  cover_codes: string[];
  extra_segments: string[];
}

export interface FlyerPrinted {
  flyer_row: number;
  wc_code: string;
  covers: { code: string; found: boolean }[];
}

export interface FlyerModel {
  family_code: string;
  printed: FlyerPrinted[];
  skus: FlyerSku[];
}

export interface SeatCoverSpecState {
  registry_id: string;
  is_active: boolean;
  value_count: number;
  can_retire: boolean;
}

export interface FlyerReview {
  models: FlyerModel[];
  counts: {
    models: number;
    models_missing: number;
    covers_missing: number;
    ready: number;
    linked: number;
    unresolved: number;
    cover_missing: number;
  };
  seat_cover_spec: SeatCoverSpecState | null;
}

export interface FlyerApplyResult {
  linked: number;
  already_linked: number;
  skipped: { product_id: string; reason: string }[];
}
