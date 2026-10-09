export type GrantStatus = 'Active' | 'Revoked' | 'Expired';

export interface GrantRow {
  id: string;
  user_id: string;
  user_name: string;
  created_at: string;
  last_used_at: string | null;
  calls_30d: number;
  status: GrantStatus;
}

export interface GrantCall {
  time: string;
  tool: string;
  rows: number | null;
  ms: number | null;
  status: string;
}

export interface GrantDetail {
  id: string;
  user_id: string;
  user_name: string;
  client_name: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  status: GrantStatus;
  recent_calls: GrantCall[];
}

export interface GrantsPage {
  data: GrantRow[];
  pagination: { total: number; page: number; limit: number };
}
