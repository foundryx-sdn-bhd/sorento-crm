/**
 * Sales opportunities as `/api/v1/sales/opportunities` serialises them (plan 3.4, 3.5;
 * section 16, slice S2). Dates are `YYYY-MM-DD` strings, datetimes naive UTC.
 */
export interface SalesOpportunityLine {
  id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  qty: string | number;
  /** Prefilled from the product's list price (the dealer flyer's price) when omitted on
   *  create/update and that price is above zero; null otherwise (plan section 16, F7). */
  unit_price: string | null;
  /** `qty x unit_price`, 2dp; null when there is no unit price. */
  line_amount: string | null;
}

export interface SalesOpportunityTransition {
  to_status_id: string;
  key: string;
  label: string;
}

/** The shape both the list row and the detail read share (section 16's one detail shape). */
export interface SalesOpportunityListItem {
  id: string;
  opportunity_no: string;
  title: string;
  customer_id: string | null;
  customer_code?: string | null;
  customer_name: string | null;
  prospect_name: string | null;
  sales_agent_id?: string | null;
  sales_agent_label?: string | null;
  status_id?: string | null;
  stage_key: string;
  stage_label: string;
  win_probability?: string | number | null;
  outcome?: string;
  expected_amount: string;
  expected_close_date: string;
  lost_reason?: string | null;
  lost_reason_label?: string | null;
  sales_order_id?: string | null;
  sales_order_no?: string | null;
  /** Registered project link; null when unlinked. */
  project_id?: string | null;
  project_code?: string | null;
  /** `CODE - Title`, ready to display. */
  project_label?: string | null;
  source: 'portal' | 'crm';
  created_by_label?: string | null;
  created_by_contact_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  stage_changed_at?: string | null;
  lines?: SalesOpportunityLine[];
  available_transitions?: SalesOpportunityTransition[];
}

export type SalesOpportunityDetail = SalesOpportunityListItem;

export interface SalesOpportunityLineInput {
  product_id: string;
  qty: number;
  /** Omitted or null - the server fills the product's list price when it is above zero. */
  unit_price?: string | number | null;
}

/** CRM create/update payload - the same field names the backend reads (plan section 16). */
export interface SalesOpportunitySavePayload {
  title?: string;
  expected_amount?: string | number;
  expected_close_date?: string;
  customer_id?: string | null;
  prospect_name?: string | null;
  sales_agent_id?: string | null;
  status_id?: string;
  lost_reason?: string | null;
  sales_order_id?: string | null;
  /** null clears the link; omitted leaves it unchanged. */
  project_id?: string | null;
  lines?: SalesOpportunityLineInput[];
}

export interface SalesOpportunityCustomerOptionItem {
  customer_id: string;
  customer_code: string;
  customer_name: string;
}

export interface SalesOpportunityCustomerOptionsResponse {
  items: SalesOpportunityCustomerOptionItem[];
  prospect: { name: string } | null;
  blocked: { name: string; message: string } | null;
}

export interface SalesOpportunityStageOption {
  id: string;
  key: string;
  label: string;
  win_probability: string | number | null;
  is_active: boolean;
  is_terminal: boolean;
}

export interface SalesOpportunityLostReasonOption {
  value: string;
  label: string;
}

export interface SalesOpportunityMeta {
  stages: SalesOpportunityStageOption[];
  lost_reasons: SalesOpportunityLostReasonOption[];
}

export interface SalesOpportunitySalesOrderOption {
  id: string;
  so_number: string;
  customer_id: string | null;
  customer_name: string | null;
  order_date: string | null;
}

export interface SalesOpportunityListParams {
  pageIndex: number;
  pageSize: number;
  sorting?: { id: string; desc: boolean }[];
  searchQuery?: string;
  statusId?: string;
  salesAgentId?: string;
  customerId?: string;
  projectId?: string;
  closeFrom?: string;
  closeTo?: string;
  outcome?: string;
}

export interface SalesOpportunityListResponse {
  data: SalesOpportunityListItem[];
  pagination: { total: number; page: number; limit: number };
  empty: boolean;
}
