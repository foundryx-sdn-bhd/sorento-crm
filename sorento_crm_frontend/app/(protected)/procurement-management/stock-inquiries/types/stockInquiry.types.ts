export interface StockInquiry {
  id: string;
  inquiry_number?: string | null;
  salesperson?: string | null;
  /** Contact FK routing key (CS pin lookup). The `salesperson` text stays the
   *  display label, derived server-side when this FK is set. */
  salesperson_contact_id?: string | null;
  /** Resolved live contact name when the FK is set; falls back to `salesperson`. */
  salesperson_contact_name?: string | null;
  product_code?: string | null;
  item_description?: string | null;
  project_customer?: string | null;
  project_name?: string | null;
  /** Registered project link (explicit pick only). `project_name` above is the free text;
   *  the linked project is `project_label` (`CODE - Title`). */
  project_id?: string | null;
  project_code?: string | null;
  project_label?: string | null;
  quantity?: string | null;
  delivery_date?: string | null;
  remark?: string | null;
  additional_remark?: string | null;
  purchasing_response?: string | null;
  contact_id?: string | null;
  space_id?: string | null;
  respond_inbox_url?: string | null;
  status?: string | null;
  last_responded_by?: string | null;
  last_responded_by_name?: string | null;
  last_responded_at?: string | null;
  rejection_reason?: string | null;
  rejected_at?: string | null;
  rejected_by?: string | null;
  rejected_by_name?: string | null;
  /** R3 void audit fields (populated when status === 'voided'). */
  voided_by?: string | null;
  voided_by_name?: string | null;
  voided_at?: string | null;
  void_reason?: string | null;
  rejected_from?: string | null;
  reopen_reason?: string | null;
  reopened_at?: string | null;
  reopened_by?: string | null;
  reopened_by_name?: string | null;
  assigned_to_id?: string | null;
  assigned_to_name?: string | null;
  handled_by_name?: string | null;
  /** PDF exports the CURRENT user has taken of this record (list path only). */
  print_count?: number | null;
  /** Contact-initiated portal revisions, denormalized so the list needs no
   *  per-row query (UAC H4). 0 = the original submission. The `-R{n}` document
   *  number suffix is DERIVED from this, never stored (UAC N2). */
  revision_no?: number | null;
  /** When the latest revision landed (naive UTC). */
  last_revised_at?: string | null;
  /**
   * Whether the backend will accept a `purchasing_response` write at this
   * record's current status (UAC O1). Server-owned: the allowed statuses live in
   * `app/services/response_gate.py` and are deliberately NOT mirrored on this
   * side, so the affordance can never disagree with the endpoint that 422s.
   *
   * Absent means not gated, matching the backend's own fail-open for a type it
   * has no rule for.
   */
  response_write_allowed?: boolean | null;
  attachments?: StockInquiryAttachment[];
  created_at: Date;
  updated_at: Date;
}

export const STOCK_INQUIRY_STATUS_LABELS: Record<string, string> = {
  new: 'New',
  pending_project_sales: 'Pending project sales',
  pending_purchasing: 'Pending purchasing',
  rejected: 'Rejected',
  responded: 'Responded',
  updated: 'Updated', // legacy
};

export interface StockInquiryAttachment {
  id: string;
  inquiry_id: string;
  attachment_id?: string | null;
  file_name?: string | null;
  original_filename?: string | null;
  file_url?: string | null;
  file_size_bytes?: number | null;
  uploaded_at?: string | Date | null;
  /** 'response_attachment' rows come from the purchasing response popup and
   *  unlink through a dedicated endpoint. */
  link_type?: string | null;
  /** Uploader attribution (S1) - names only, never a UUID. */
  uploader_kind?: 'user' | 'contact' | 'system' | null;
  uploaded_by_name?: string | null;
  uploaded_by_role?: 'contact' | 'staff' | null;
}

export interface StockInquiryFormData {
  salesperson?: string;
  salesperson_contact_id?: string | null;
  product_code?: string;
  item_description?: string;
  project_customer?: string;
  project_name?: string;
  /** null clears the link; omitted leaves it unchanged. */
  project_id?: string | null;
  quantity?: string;
  delivery_date?: string;
  remark?: string;
  additional_remark?: string;
  purchasing_response?: string;
  contact_id?: string;
  space_id?: string;
}

export interface StockInquiryDetail extends StockInquiry {}
