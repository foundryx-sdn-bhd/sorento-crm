export interface Warehouse {
  id: string;
  warehouse_code: string;
  warehouse_name: string | null;
  location: string | null;
  manager_id?: string | null;
  is_active: boolean;
  /** Set on a `company_scope=grants` read. */
  company_name?: string | null;
  created_at: Date;
  updated_at: Date | null;
  zones_count?: number;
  /** Whether stock here may cover demand in the reorder plan. */
  counts_as_available?: boolean;
  /** The shared pool this location draws on; null means it is its own pool. */
  pool_warehouse_id?: string | null;
  /** Resolved for display, so no UUID is ever rendered. */
  pool_warehouse_code?: string | null;
  /** Who this location sells to: dealer or project. Splits cost, price and history. */
  segment?: string | null;
  /**
   * Whether this bin takes part in fulfilment planning at all. A bin that is off is
   * invisible to the ladder, the board and the Stock Debt view - its stock, its incoming
   * and its sales-order lines are all outside the plan.
   *
   * Normalised in the service: the backend only carries the column from migration 443, so
   * a response without it reads as `false` rather than `undefined`.
   */
  fulfilment_planning: boolean;
}

export interface WarehouseFormData {
  warehouse_code: string;
  warehouse_name?: string | null;
  location?: string | null;
  manager_id?: string | null;
  is_active: boolean;
  counts_as_available?: boolean;
  pool_warehouse_id?: string | null;
  segment?: string | null;
  fulfilment_planning?: boolean;
}
