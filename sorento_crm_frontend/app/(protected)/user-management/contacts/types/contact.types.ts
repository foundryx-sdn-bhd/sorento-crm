export interface RespondContactAccessType {
  code: string;
  name: string;
  sort_order?: number | null;
}

export interface RespondContact {
  id: string;
  phone_number: string;
  name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  respond_io_id?: string | null;
  workspace_id?: string | null;
  workspace_name?: string | null;
  workspace_space_id?: string | null;
  access_type_codes?: string[];
  /** AC-F4: their sponsorship form demands a registered project. */
  requires_registered_project?: boolean;
  access_types?: RespondContactAccessType[];
  /** Outbound WhatsApp kill switch. Flipped via the shared outbound endpoints, not a contact update. */
  outbound_enabled?: boolean;
  /**
   * Chatbot turn re-architecture (AC-1503, AC-1515): tier/default_ledgers. `language`
   * and `always_full_report` are gone (chatbot memory lane A, contract section 5) -
   * language is now a profile FACT (`chatbot_profile.facts.language`, see
   * `services/contactChatbotService.ts`), and `always_full_report` was dead.
   */
  chatbot_profile?: {
    tier?: string | null;
    default_ledgers?: string[] | null;
  } | null;
  /**
   * Per-contact context level (chatbot memory lane A, contract section 2): `off` |
   * `conversation` | `episodes` | `full`, or null to follow the system default.
   * `chatbot_recall_enabled` is DROPPED (round 3, AC-MEM054) - column and field both.
   */
  chatbot_memory_level?: 'off' | 'conversation' | 'episodes' | 'full' | null;
  /** S6: may this contact ask the chatbot for stock. A CRM fact, default on. */
  chatbot_stock_allowed?: boolean;
  /** Linked customer codes, sorted (list rows). */
  customer_codes?: string[];
  /** CONTACT-BULK-ACCESS: `chatbot_profile.tier`, flattened for the list (list rows). */
  chatbot_tier?: string | null;
  /** CONTACT-BULK-ACCESS: holds the `purchase_orders.cost` field reveal (list rows). */
  cost_visible?: boolean;
  /** Chatbot card switches the list shows as columns. */
  notify_salesman?: boolean;
  packing_list_allowed?: boolean;
  chatbot_eta_offset_applied?: boolean;
  escalation_allowed?: boolean;
  /** Identity S3: the user this contact is linked to, if any (list rows). Null
   *  without `user_management.users.view`. */
  linked_user_id?: string | null;
  linked_user_name?: string | null;
  /** Identity S3: which role the Add user form should suggest for this contact
   *  (S3 contract 1.1) - a market-segment or sales-agent salesperson, else a
   *  portal user. */
  is_salesperson?: boolean;
  suggested_role_slug?: 'salesperson' | 'portal_user';
  /** Identity S3: the full linked user (contact detail only), or null when
   *  unlinked or the caller lacks `user_management.users.view`. */
  linked_user?: {
    id: string;
    name: string | null;
    email: string | null;
    status: string;
    has_password: boolean;
    roles: { id: string; name: string }[];
    /** True when the user's phone is not the contact's (or is empty), so the
     *  WhatsApp code cannot reach that user (fix round 2, S3). */
    phone_differs_from_contact?: boolean;
  } | null;
  created_at: Date;
  updated_at: Date;
  created_by?: string | null;
}

export interface RespondContactFormData {
  phone_number: string;
  name?: string;
  workspace_id?: string | null;
  access_type_codes?: string[];
  /** AC-F4: their sponsorship form demands a registered project. */
  requires_registered_project?: boolean;
}
