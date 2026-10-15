/**
 * Stock Debt snapshots, as the backend serves them (`app/schemas/stock_debt_snapshot.py`).
 * A snapshot row is a `StockDebtRow` and a snapshot line a `StockDebtDemandLine`, extended;
 * nothing about those shapes is restated here.
 */
import type {
  StockDebtDemandLine,
  StockDebtRow,
  StockDebtTotals,
} from './stockDebt.types';

export type SnapshotDecision = 'reserve' | 'buy' | 'ignore';
/** The rows listing's Decision filter; '' = no filter. */
export type SnapshotDecisionFilter = '' | 'decided' | 'undecided' | SnapshotDecision;

/** The filters a snapshot was taken under, plus the labels it kept (AC-6). */
export interface SnapshotFilters {
  query?: string | null;
  book?: string;
  location?: string[];
  exclude_location?: string[];
  only_debt?: boolean;
  date_from?: string | null;
  date_to?: string | null;
  supplier_ids?: string[];
  projected?: boolean;
  show_aux?: boolean;
  location_codes?: string[];
  exclude_location_codes?: string[];
  supplier_names?: string[];
}

/** The header fields the list and the page both carry (AC-8, AC-9). */
export interface SnapshotSummary {
  id: string;
  name: string;
  filters: SnapshotFilters;
  filters_label: string;
  row_count: number;
  decided_count: number;
  created_by_name: string | null;
  created_at: string;
  can_delete: boolean;
}

export interface Snapshot extends SnapshotSummary {
  months: string[];
  tba_month: string | null;
  totals: StockDebtTotals;
  prev_id: string | null;
  next_id: string | null;
}

export interface SnapshotPagination {
  total: number;
  page: number;
  limit: number;
}

export interface SnapshotListResponse {
  data: SnapshotSummary[];
  pagination: SnapshotPagination;
}

export interface SnapshotRow extends StockDebtRow {
  row_id: string;
  decision: SnapshotDecision | null;
  decision_warehouse_id: string | null;
  decision_warehouse_code: string | null;
  decision_note: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
}

export interface SnapshotRowsResponse {
  data: SnapshotRow[];
  pagination: SnapshotPagination;
}

export type SnapshotReserveState = 'requested' | 'parked' | 'reserved' | 'cancelled';

/** One order inquiry row on a line: frozen fields plus the live reading (AC-5, AC-12). */
export interface SnapshotOiRow {
  oi_id: string;
  inquiry_no: string;
  row_id: string;
  qty: number;
  delivery_date: string | null;
  stock_location: string | null;
  verb: string;
  state: string;
  now_state?: string | null;
  reserve_state?: SnapshotReserveState | null;
  remaining?: number | null;
  requestable?: boolean;
  after_snapshot?: boolean;
}

export interface SnapshotLine extends StockDebtDemandLine {
  bucket: string;
  sales_order_line_id: string;
  oi_rows: SnapshotOiRow[];
  now: { so_status: string | null; open_qty: number };
}

export interface SnapshotLinesResponse {
  lines: SnapshotLine[];
}

export interface SnapshotDecisionInput {
  decision: SnapshotDecision | null;
  warehouse_id: string | null;
  note: string | null;
}
