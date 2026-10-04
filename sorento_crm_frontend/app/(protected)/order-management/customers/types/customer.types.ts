export interface Customer {
  id: string;
  customer_code: string;
  customer_name: string;
  email?: string | null;
  phone_number?: string | null;
  is_active: boolean;
  created_at: Date;
  updated_at?: Date | null;
  created_by?: string | null;
  orders_count?: number;
  // Who sells to this customer. `sales_agent_id` addresses a write; the code + name are
  // read-only, resolved server-side off the `sales_agents` master (no UUID in the UI).
  sales_agent_id?: string | null;
  sales_agent_code?: string | null;
  sales_agent_name?: string | null;
  // Which numbered account this ledger is ("account 1" in a chatbot message); null = none.
  account_level?: number | null;
  // The customer group this ledger belongs to; the name is read-only (no UUID in the UI).
  customer_group_id?: string | null;
  customer_group_name?: string | null;
  // Extended profile (added by commercial_core)
  registered_name?: string | null;
  trading_name?: string | null;
  registration_number?: string | null;
  industry?: string | null;
  website?: string | null;
  billing_address?: Record<string, unknown> | null;
  country?: string | null;
  tax_id?: string | null;
  notes?: string | null;
  account_owner_user_id?: string | null;
  customer_type?: string | null;
  salutation?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  mobile_number?: string | null;
}

export interface CustomerFormData {
  customer_code: string;
  customer_name: string;
  email?: string;
  phone_number?: string;
  is_active: boolean;
  // `null` clears the assignment; `undefined` leaves it alone (PUT reads `exclude_unset`).
  sales_agent_id?: string | null;
  // `null` clears the level; `undefined` leaves it alone (PUT reads `exclude_unset`).
  account_level?: number | null;
  // `null` clears the group; `undefined` leaves it alone (PUT reads `exclude_unset`).
  customer_group_id?: string | null;
  // Extended profile fields
  registered_name?: string;
  trading_name?: string;
  registration_number?: string;
  industry?: string;
  website?: string;
  billing_address?: Record<string, unknown> | null;
  country?: string;
  tax_id?: string;
  notes?: string;
  account_owner_user_id?: string;
  customer_type?: string;
  salutation?: string;
  first_name?: string;
  last_name?: string;
  mobile_number?: string;
}

export interface CustomerDetail extends Customer {
  orders_count?: number;
}
