/**
 * New product application shapes (NEW-PRODUCT-APP), shared by the staff feature and the portal.
 * Names are the backend contract in
 * documentation/plans/procurement/PLAN-new-product-application-17oct.md ("API contract (as built)").
 */

export type NewProductApplicationStatus =
  | 'draft'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'completed'
  | 'cancelled';

/** One line as written (create and draft update replace all lines); null clears a field. */
export interface NewProductApplicationLineWrite {
  proposed_code?: string | null;
  description?: string | null;
  further_description?: string | null;
  category_id?: string | null;
  base_uom_id?: string | null;
  brand_id?: string | null;
  item_type_id?: string | null;
  supplier_id?: string | null;
  supplier_item_code?: string | null;
  /** Numbers travel as strings or numbers; null clears, never 0. */
  cost_price?: string | number | null;
  selling_price?: string | number | null;
  expected_qty?: string | number | null;
  remark?: string | null;
}

/** The write payload, staff and portal alike. `needed_by` is YYYY-MM-DD. */
export interface NewProductApplicationWrite {
  stock_inquiry_id?: string | null;
  customer_id?: string | null;
  customer_name?: string | null;
  project_name?: string | null;
  needed_by?: string | null;
  remark?: string | null;
  lines?: NewProductApplicationLineWrite[];
}

/** GET /prefill: a Write-shaped body copied as text, plus the inquiry's number. */
export interface NewProductApplicationPrefill extends NewProductApplicationWrite {
  stock_inquiry_number: string | null;
}

export interface NewProductApplicationSummary {
  id: string;
  application_no: string;
  status: NewProductApplicationStatus;
  status_label: string;
  source: string;
  applicant_id: string | null;
  applicant_name: string | null;
  stock_inquiry_id: string | null;
  stock_inquiry_number: string | null;
  customer_name: string | null;
  project_name: string | null;
  first_line: string | null;
  lines_total: number;
  lines_done: number;
  opened_codes: string[];
  needed_by: string | null;
  submitted_at: string | null;
  created_at: string | null;
}

export interface NewProductApplicationLine extends NewProductApplicationLineWrite {
  id: string;
  line_no: number;
  category_code: string | null;
  category_name: string | null;
  base_uom_code: string | null;
  base_uom_name: string | null;
  brand_name: string | null;
  item_type_name: string | null;
  supplier_code: string | null;
  supplier_name: string | null;
  opened_product_id: string | null;
  opened_product_code: string | null;
  opened_product_name: string | null;
  opened_product_active: boolean | null;
  closed_reason: string | null;
  resolved_by: string | null;
  resolved_by_name: string | null;
  resolved_at: string | null;
}

export interface NewProductApplicationDetail extends NewProductApplicationSummary {
  stock_inquiry_product_code: string | null;
  customer_id: string | null;
  customer_code: string | null;
  remark: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decided_by_name: string | null;
  reject_reason: string | null;
  revise_reason: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  completed_at: string | null;
  updated_at: string | null;
  lines: NewProductApplicationLine[];
}

export interface NewProductApplicationPage {
  items: NewProductApplicationSummary[];
  total: number;
  page: number;
  limit: number;
}

/** GET /item-types and GET /products rows (the only fields the screens read). */
export interface NewProductApplicationItemType {
  id: string;
  item_type_code: string;
  item_type_name: string;
}

export interface NewProductApplicationProductHit {
  id: string;
  product_code: string;
  product_name: string;
  brand_name: string | null;
}

/** A refusal that names a field arrives as `{message, code, details: {field}}`. */
export class NewProductApplicationError extends Error {
  readonly code: string | null;
  readonly field: string | null;

  constructor(message: string, code: string | null, field: string | null) {
    super(message);
    this.name = 'NewProductApplicationError';
    this.code = code;
    this.field = field;
  }
}
