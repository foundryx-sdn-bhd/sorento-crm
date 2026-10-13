export interface ProductAlternativeRow {
  id: string;
  alternative_product_id: string;
  product_code: string;
  product_name: string;
  source: 'auto_new' | 'manual';
  is_discontinued: boolean;
}

export interface ProductAlternativesResult {
  alternatives_manual: boolean;
  alternatives: ProductAlternativeRow[];
}
