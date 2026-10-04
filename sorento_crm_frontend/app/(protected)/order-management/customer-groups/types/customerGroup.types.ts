export interface CustomerGroup {
  id: string;
  name: string;
  ledger_count: number;
  /** Sorted distinct account levels of the member ledgers. */
  account_levels: number[];
  /** The one person behind the ledgers' agents; null when none is set or they differ. */
  sales_agent_label?: string | null;
  sales_agent_mixed?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

/** A member ledger row (`CustomerResponse`, the fields the Ledgers tab reads). */
export interface GroupLedger {
  id: string;
  customer_code: string;
  customer_name: string;
  account_level?: number | null;
  sales_agent_code?: string | null;
  sales_agent_name?: string | null;
  is_active: boolean;
}

export interface CustomerGroupSelectOption {
  id: string;
  name: string;
  ledger_count: number;
}
