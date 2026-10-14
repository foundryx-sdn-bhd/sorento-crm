/**
 * Product sets: the flyer code that names an assembly.
 *
 * `SRTWC8608-RL` is printed on a flyer and asked for on WhatsApp, but the
 * catalogue holds only its parts. A set is that missing code.
 *
 * UAC: `documentation/plans/master-data/product-sets-acceptance-criteria.md`.
 */

/** One real SKU inside a set. These are what a stock answer counts. */
export interface ProductSetMember {
  id: string;
  /** Used only to build the link back to the product's own page - never shown. */
  product_id: string;
  /** Human-readable. No UUID reaches the UI. */
  product_code: string;
  product_name: string;
  description: string | null;
  list_price: number | null;
  is_discontinued: boolean;
  quantity: number;
  /** Ticking members IS the price formula. No expression language. */
  contributes_to_price: boolean;
  sort_order: number;
  /** Available across every warehouse. Null when stock is not loaded. */
  available: number | null;
}

/**
 * Both figures always travel.
 *
 * `computed` is null with a `reason` when there is no basis yet: a set
 * mid-authoring must not claim RM 0.00, because a price of zero and a missing
 * price are different facts.
 */
export interface ProductSetPrice {
  computed: number | null;
  override: number | null;
  resolved: number | null;
  is_overridden: boolean;
  reason: 'no_members' | 'no_member_contributes' | null;
  override_set_by_name?: string | null;
  override_set_at?: string | null;
}

export interface ProductSet {
  id: string;
  set_code: string;
  name: string;
  is_active: boolean;
  company_name: string | null;
  price: ProductSetPrice;
  member_count: number;
  /** `min(floor(available / quantity))`. Null when stock is not loaded. */
  complete_sets: number | null;
  /** The member that produced the minimum, named so a zero is explicable. */
  limiting_member_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProductSetDetail extends ProductSet {
  members: ProductSetMember[];
}

export interface ProductSetMemberPayload {
  product_code: string;
  quantity: number;
  contributes_to_price: boolean;
  sort_order: number;
}

export interface ProductSetPayload {
  set_code: string;
  name: string;
  is_active?: boolean;
  /** Null clears the override and returns the set to its computed price. */
  list_price_override?: number | null;
  members?: ProductSetMemberPayload[];
}

/**
 * A set as a line picker needs it (`GET /product-sets/select`): no cost, no UUID beyond the id the
 * pick is stored under. Readable by every role that can quote, which `master_data.product_sets.view`
 * is not.
 */
export interface ProductSetLineRef {
  id: string;
  set_code: string;
  name: string;
  is_active: boolean;
  member_count: number;
  list_price: string | number | null;
  /** Null for a caller who may not read stock. */
  complete_sets: number | null;
  /** The driver part's brand, or null. */
  brand_name?: string | null;
  parts: { product_id?: string; product_code: string; quantity: string | number }[];
}

/** A set this product belongs to, for the Sets section on product detail (S4). */
export interface ProductSetRef {
  id: string;
  set_code: string;
  name: string;
}
