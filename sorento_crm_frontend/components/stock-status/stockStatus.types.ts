/**
 * `GET /api/v1/inventory/stock-status` - AutoCount's Available Stock Status for one item.
 * Shapes mirror `sorento_crm_backend/app/services/stock_status_service.py`.
 */
export interface StockStatusLocation {
  warehouse_id: string;
  warehouse_code: string;
  warehouse_name: string | null;
  /** False: inactive in the CRM, still listed and counted (AutoCount lists it). */
  is_active: boolean;
  on_hand: number;
  so_qty: number;
  po_qty: number;
  spo_qty: number;
  /** On hand - SO + PO + SPO, signed: AutoCount's Forecast. */
  forecast: number;
}

export interface StockStatusTotals {
  on_hand: number;
  so_qty: number;
  po_qty: number;
  spo_qty: number;
  forecast: number;
  incoming_pl: number;
  forecast_incl_pl: number;
}

export interface StockStatusIncomingPl {
  shipment_id: string;
  shipment_number: string;
  container_number: string | null;
  eta: string | null;
  still_to_land: number;
  on_spo: number;
  counted: number;
}

export interface StockStatusSalesOrder {
  line_id: string;
  sales_order_id: string;
  so_number: string;
  debtor_code: string | null;
  debtor_name: string | null;
  project_label: string | null;
  salesperson: string | null;
  doc_date: string | null;
  /** Null when the line has no date, or AutoCount's 1 Jan 2030 placeholder. */
  due_date: string | null;
  qty: number;
  covered_in_crm: boolean;
  warehouse_id: string;
}

export interface StockStatusPurchaseOrder {
  line_id: string;
  purchase_order_id: string;
  po_number: string;
  supplier_code: string | null;
  supplier_name: string | null;
  issue_date: string | null;
  expected_date: string | null;
  qty: number;
  warehouse_id: string;
}

export interface StockStatusSpo {
  allocation_id: string;
  spo_number: string;
  spo_line_number: number | null;
  supplier_code: string | null;
  supplier_name: string | null;
  issue_date: string | null;
  arrival_date: string | null;
  container_number: string | null;
  qty: number;
  warehouse_id: string;
}

export interface StockStatus {
  product_id: string;
  item_code: string;
  description: string | null;
  as_of: string | null;
  so_due_by: string | null;
  locations: StockStatusLocation[];
  totals: StockStatusTotals;
  incoming_pl: StockStatusIncomingPl[];
  sales_orders: StockStatusSalesOrder[];
  purchase_orders: StockStatusPurchaseOrder[];
  spos: StockStatusSpo[];
}

/** `GET /api/v1/inventory/stock-status/summary` - one entry per item, for list chips. */
export interface StockStatusSummary {
  product_id: string;
  on_hand: number;
  so_qty: number;
}
