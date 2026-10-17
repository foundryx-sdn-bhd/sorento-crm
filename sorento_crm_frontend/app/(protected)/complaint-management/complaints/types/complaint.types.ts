export interface ComplaintProductLine {
  product_code: string;
  quantity?: string | null;
  product_type?: string | null;
}

export interface ComplaintAttachment {
  id: string;
  complaint_id: string;
  attachment_id?: string | null;
  /** Original filename (from attachment). Prefer this for display. */
  file_name?: string | null;
  original_filename?: string | null;
  file_url?: string | null;
  file_size_bytes?: number | null;
  uploaded_at: Date;
  created_at?: Date;
  /** From complaint_attachments table. 'response_attachment' rows come from the
   *  technical team response popup and unlink through a dedicated endpoint. */
  link_type?: 'complaint_attachment' | 'response_attachment' | null;
  /** Uploader attribution (S1) - names only, never a UUID. */
  uploader_kind?: 'user' | 'contact' | 'system' | null;
  uploaded_by_name?: string | null;
  uploaded_by_role?: 'contact' | 'staff' | null;
}

export interface Complaint {
  id: string;
  complaint_number?: string | null;
  delivery_order_number?: string | null;
  complaint_date?: Date | null;
  customer_type?: string | null;
  customer_type_others?: string | null;
  within_warranty?: string | null;
  product_type?: string | null;
  defects_discovered?: string | null;
  complaint_type?: string | null;
  defect_description?: string | null;
  product_code?: string | null;
  quantity?: string | null;
  salesperson?: string | null;
  customer_name?: string | null;
  contact_person?: string | null;
  contact_number?: string | null;
  customer_address?: string | null;
  project_title?: string | null;
  /** AC-L3: the reportable link to a registered project. `project_code` / `project_name` are
   *  the resolved display values -- never render the UUID. */
  project_id?: string | null;
  project_code?: string | null;
  project_name?: string | null;
  /** `CODE - Title`, ready to display. */
  project_label?: string | null;
  contact_id?: string | null;
  space_id?: string | null;
  respond_inbox_url?: string | null;
  technical_team_response?: string | null;
  status?: string | null;
  rejection_reason?: string | null;
  /** R3 void audit fields (populated when status === 'voided'). */
  voided_by?: string | null;
  voided_by_name?: string | null;
  voided_at?: string | null;
  void_reason?: string | null;
  last_responded_by?: string | null;
  last_responded_by_name?: string | null;
  last_responded_at?: string | null;
  created_at?: string | null;
  assigned_to?: string | null;
  assigned_to_name?: string | null;
  handled_by_name?: string | null;
  root_cause_id?: string | null;
  resolution_id?: string | null;
  root_cause_name?: string | null;
  resolution_name?: string | null;
  root_cause_notified_at?: string | null;
  resolution_notified_at?: string | null;
  required_on_site_support?: boolean | null;
  print_count?: number | null;
  /**
   * Whether the backend will accept a `technical_team_response` write at this
   * record's current status (UAC O1). Server-owned: the allowed statuses live in
   * `app/services/response_gate.py` and are deliberately NOT mirrored on this
   * side, so the affordance can never disagree with the endpoint that 422s.
   *
   * Absent means not gated, matching the backend's own fail-open for a type it
   * has no rule for.
   */
  response_write_allowed?: boolean | null;
  product_lines?: ComplaintProductLine[];
  attachments: ComplaintAttachment[];
}

export interface ComplaintFormData {
  delivery_order_number?: string;
  complaint_date?: Date;
  customer_type?: string;
  customer_type_others?: string;
  within_warranty?: string;
  product_type?: string;
  defects_discovered?: string;
  complaint_type?: string;
  defect_description?: string;
  product_code?: string;
  quantity?: string;
  product_lines?: ComplaintProductLine[];
  salesperson?: string;
  customer_name?: string;
  contact_person?: string;
  contact_number?: string;
  customer_address?: string;
  project_title?: string;
  project_id?: string | null;
  contact_id?: string | null;
  space_id?: string | null;
  technical_team_response?: string;
  status?: string;
  last_responded_by?: string;
  last_responded_at?: string;
  root_cause_id?: string | null;
  resolution_id?: string | null;
  required_on_site_support?: boolean | null;
  attachments?: ComplaintAttachment[];
}

export interface ComplaintDetail extends Complaint {
  attachments: ComplaintAttachment[];
}
