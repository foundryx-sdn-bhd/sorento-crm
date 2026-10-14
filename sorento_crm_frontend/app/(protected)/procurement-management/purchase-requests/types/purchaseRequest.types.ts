export interface PurchaseRequestLine {
  id: string;
  purchase_request_id: string;
  item_code?: string | null;
  quantity?: number | null;
  remark?: string | null;
  unit_price?: number | null;
  total?: number | null;
  sort_order?: number | null;
  created_at?: string | null;
}

export interface PurchaseRequestAttachment {
  id: string;
  purchase_request_id: string;
  attachment_id?: string | null;
  file_name?: string | null;
  original_filename?: string | null;
  file_url?: string | null;
  file_size_bytes?: number | null;
  uploaded_at?: string | null;
  link_type?: string | null;
  /** Uploader attribution (S1) - names only, never a UUID. */
  uploader_kind?: 'user' | 'contact' | 'system' | null;
  uploaded_by_name?: string | null;
  uploaded_by_role?: 'contact' | 'staff' | null;
}

export interface PurchaseRequest {
  id: string;
  request_type: string;
  request_number?: string | null;
  request_date?: string | null;
  customer_name?: string | null;
  // The customer picked from the customer list; null = free text.
  customer_id?: string | null;
  /** Site contact, free text ("name and contact number"). Optional. */
  pic?: string | null;
  project_title?: string | null;
  /** AC-L3: reportable link to a registered project. `project_code` is the display value. */
  project_id?: string | null;
  project_code?: string | null;
  purpose?: string | null;
  delivery_address?: string | null;
  total_project_value?: number | null;
  total_project_value_text?: string | null;
  /** R2: sales type (project / cash_sales), lookup-bound; routes CS assignment. */
  sales_type?: string | null;
  sponsor_subject?: string | null;
  sponsor_subject_other?: string | null;
  expected_delivery_date?: string | null;
  expected_po_date?: string | null;
  expected_po_date_text?: string | null;
  requested_by?: string | null;
  /** Contact FK routing key (CS pin lookup). `requested_by` stays the display
   *  label, derived server-side when this FK is set. */
  requested_by_contact_id?: string | null;
  /** Resolved live contact name when the FK is set; falls back to `requested_by`. */
  requested_by_contact_name?: string | null;
  requested_at?: string | null;
  /** Top document "Date" - auto-stamped on submit (read-only). */
  submitted_at?: string | null;
  assigned_to_id?: string | null;
  assigned_to_name?: string | null;
  handled_by_name?: string | null;
  status?: string | null;
  source?: string | null;
  external_reference?: string | null;
  contact_id?: string | null;
  space_id?: string | null;
  respond_inbox_url?: string | null;
  approver_user_id?: string | null;
  approver_email?: string | null;
  approver_display_name?: string | null;
  approval_status?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
  approval_signature_ref?: string | null;
  approval_comments?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** R3 void audit fields (populated when status === 'voided'). */
  voided_by?: string | null;
  voided_by_name?: string | null;
  voided_at?: string | null;
  void_reason?: string | null;
  /** Contact-initiated portal revisions, denormalized so the list needs no
   *  per-row query (UAC H4). 0 = the original submission. The `-R{n}` document
   *  number suffix is DERIVED from this, never stored (UAC N2). */
  revision_no?: number | null;
  /** When the latest revision landed (naive UTC). */
  last_revised_at?: string | null;
  lines?: PurchaseRequestLine[];
  attachments?: PurchaseRequestAttachment[];
  grand_total?: number | null;
}

export interface SendApprovalLinkRequest {
  approver_email?: string | null;
  approver_user_id?: string | null;
  expires_hours?: number;
  send_email?: boolean;
  base_url?: string | null;
}

export interface SendApprovalLinkResponse {
  approval_url: string;
  expires_at: string;
  token_id: string;
  email_sent?: boolean | null;
  email_error?: string | null;
}

export interface PurchaseRequestLineFormData {
  item_code?: string;
  quantity?: number;
  remark?: string;
  unit_price?: number;
  total?: number;
}

export interface PurchaseRequestFormData {
  request_type: string;
  request_number?: string | null;
  request_date?: string;
  customer_name?: string;
  // `null` unlinks the customer; `undefined` leaves it alone (PUT reads `exclude_unset`).
  customer_id?: string | null;
  pic?: string | null;
  project_title?: string;
  project_id?: string | null;
  purpose?: string;
  delivery_address?: string;
  total_project_value?: number;
  total_project_value_text?: string;
  sales_type?: string;
  sponsor_subject?: string;
  sponsor_subject_other?: string;
  expected_delivery_date?: string;
  expected_po_date?: string;
  expected_po_date_text?: string;
  requested_by?: string;
  requested_by_contact_id?: string | null;
  requested_at?: string;
  contact_id?: string | null;
  space_id?: string | null;
  products?: PurchaseRequestLineFormData[];
}

export interface PurchaseRequestDetail extends PurchaseRequest {}

/** One row of the customer picker; `sales_type` is the server's mapping of the debtor type. */
export interface CustomerLookupItem {
  customer_id: string;
  debtor_name: string;
  customer_code: string;
  debtor_type: string | null;
  sales_type: string | null;
}
