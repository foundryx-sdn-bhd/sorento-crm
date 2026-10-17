import type { SortingState } from '@tanstack/react-table';

export type BillingDocumentType = 'invoice' | 'cash_sale' | 'credit_note' | 'debit_note';
export type BillingDocumentStatus = 'posted' | 'cancelled';

export const BILLING_TYPE_LABEL: Record<BillingDocumentType, string> = {
  invoice: 'Invoice',
  cash_sale: 'Cash sale',
  credit_note: 'Credit note',
  debit_note: 'Debit note',
};

export const BILLING_STATUS_LABEL: Record<BillingDocumentStatus, string> = {
  posted: 'Posted',
  cancelled: 'Cancelled',
};

export interface BillingDocumentListRow {
  id: string;
  document_type: BillingDocumentType;
  doc_no: string;
  /** YYYY-MM-DD */
  doc_date: string;
  /** Null when the document's debtor is not a CRM customer. */
  customer_id: string | null;
  customer_name: string | null;
  debtor_code: string;
  agent_code: string | null;
  agent_name: string | null;
  currency_code: string;
  net_total: number;
  tax_total: number;
  total: number;
  status: BillingDocumentStatus;
  /** Null means not synced or not posted to the ledger. Never shown as 0. */
  outstanding: number | null;
  /** YYYY-MM-DD */
  due_date: string | null;
  /** Naive timestamp from the sync. */
  outstanding_as_of: string | null;
}

export interface BillingDocumentLine {
  line_no: number;
  item_code: string | null;
  product_name: string | null;
  description: string | null;
  uom: string | null;
  quantity: number;
  unit_price: number;
  discount_amount: number;
  net_amount: number;
  tax_code: string | null;
  tax_rate: number | null;
  tax_amount: number;
  line_total: number;
  from_doc_type: string | null;
  from_doc_no: string | null;
  sales_order: { id: string; doc_no: string } | null;
}

export interface BillingRelatedNote {
  id: string;
  document_type: BillingDocumentType;
  doc_no: string;
  doc_date: string;
  currency_code: string;
  total: number;
  outstanding: number | null;
}

export interface BillingDocumentDetail extends BillingDocumentListRow {
  currency_rate: number;
  local_net_total: number;
  ref: string | null;
  description: string | null;
  last_synced_at: string | null;
  source_modified_at: string | null;
  /** The invoice a credit or debit note is against; id is null when it is not in the CRM. */
  against: { id: string | null; doc_no: string } | null;
  lines: BillingDocumentLine[];
  related: {
    notes: BillingRelatedNote[];
    sales_orders: { id: string; doc_no: string }[];
    delivery_order_nos: string[];
  };
}

export interface CustomerOutstanding {
  customer_id: string;
  ar_outstanding: number | null;
  /** Naive timestamp from the sync. */
  ar_as_of: string | null;
}

export interface BillingDocumentListParams {
  pageIndex: number;
  pageSize: number;
  sorting: SortingState;
  searchQuery: string;
  documentTypes?: BillingDocumentType[];
  status?: BillingDocumentStatus | '';
  customerId?: string;
  agentCode?: string;
  dateFrom?: string | null;
  dateTo?: string | null;
  openOnly?: boolean;
  salesOrderId?: string;
}

export interface BillingDocumentListResponse {
  data: BillingDocumentListRow[];
  pagination: { total: number };
}

export interface BillingSelectOption {
  value: string;
  label: string;
}
