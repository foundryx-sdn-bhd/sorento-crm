/**
 * Stock Debt - the field-for-field payload contract (S2, AC-S2-6 / AC-S2-7).
 *
 * `services/stockDebtService.ts` carries the ROUTE contract (paths, params, auth); this
 * file carries the SHAPES, so neither restates the other. Both the Phase-1 fixture and
 * the Phase-2 backend answer to exactly these types.
 */
import type { ExportSplit } from '@/components/common/export-split';

/** Where a month's balance sits against "can this still be bought in time" (AC-S2-6). */
export type StockDebtTone = 'red' | 'amber' | 'green';

/**
 * One month cell of a product row, stating its OWN month and nothing else (R37).
 *
 * `balance` is the supply dated in the month that is still free once the whole assignment
 * walk is over, less what the lines due in the month went short of on their own dates. It
 * does not carry: a month with nothing due and nothing arriving reads 0.
 */
export interface StockDebtMonth {
  /** `YYYY-MM`. Always one of the axis keys in `StockDebtListResponse.months`. */
  key: string;
  balance: number;
  tone: StockDebtTone;
}

/**
 * One product's debt row.
 *
 * `product_id` exists only to address the cell route - it is NEVER rendered (cursor rule:
 * no UUIDs in the UI). The reader sees `product_code` and `product_name`.
 */
export interface StockDebtRow {
  product_id: string;
  product_code: string;
  product_name: string | null;
  /** One entry per axis month, in axis order. */
  months: StockDebtMonth[];
  /** Total demand dated on or after the policy's `tba_date_from`. Draws no supply (R14). */
  tba: number;
  /** Total demand with no required date. Draws no supply (R14). */
  undated: number;
  /**
   * Total demand booked at NO warehouse. In no ownership group's pile, so it draws
   * nothing - and it is counted rather than dropped, because a screen that lists what is
   * owed and silently omits it answers a narrower question than the one it is asked.
   */
  unlocated: number;
  /**
   * The row's LAST supplier (R3, A1): the supplier on the product's newest purchase-order
   * line, falling back to the manually-flagged primary supplier, else null. Never rendered
   * as an id - `supplier_name` is what the Supplier column and filter chip print.
   */
  supplier_id: string | null;
  supplier_name: string | null;
  /** `products.item_type` reads NULL everywhere today (measured fact); this is the category
   *  code instead (R4). Null prints as "No category" wherever the export splits on it. */
  category_code: string | null;
  /** Sum of every month's balance plus `tba` + `undated` + `unlocated` (R9, AC-5). */
  total: number;
  /** BRW pool on hand, open PO and open SPO quantity, and dealer outstanding (negative). */
  pool_on_hand: number;
  pool_po_qty: number;
  pool_incoming_qty: number;
  dealer_outstanding: number;
}

/**
 * The whole filtered set's own totals (AC-6), never the page's: `totals.months['2026-09']`
 * sums EVERY row's September balance, whether or not that row's page has been fetched yet -
 * so the footer prints the same figures on page 1 and on page 2.
 */
export interface StockDebtTotals {
  months: Record<string, number>;
  tba: number;
  undated: number;
  unlocated: number;
  total: number;
  pool_on_hand: number;
  pool_po_qty: number;
  pool_incoming_qty: number;
  dealer_outstanding: number;
}

/** One entry of the toolbar's supplier select (AC-7): never an id on screen, only `name`. */
export interface StockDebtSupplierOption {
  id: string;
  name: string;
}

/** Which span `book` narrows to (R1). `group` only narrows the `project` half. */
export type StockDebtBook = 'all' | 'project' | 'retail';

/**
 * The exact sheet count an export of the current filtered set would produce, one entry
 * per non-`none` `StockDebtExportSplit` (AC-7b). Computed server-side over the WHOLE
 * filtered set, none-buckets ("No supplier" / "No category") included - so the export
 * popover's preview (AC-33) never has to guess at a bucket it cannot see from a page.
 */
export interface StockDebtSheetCounts {
  supplier: number;
  category: number;
  supplier_category: number;
}

/** The list envelope: the repo's standard `{data, pagination}` plus the column axis. */
export interface StockDebtListResponse {
  data: StockDebtRow[];
  pagination: { total: number; page: number; limit: number };
  /**
   * The month columns, current month -> last dated month carrying demand or supply.
   *
   * Server-declared rather than derived from the page's rows: the axis is a property of
   * the WHOLE filtered set, so deriving it per page would make the columns change under
   * the reader as they page.
   */
  months: string[];
  /** `YYYY-MM` of the policy's `tba_date_from` - the TBA column's own label. */
  tba_month: string;
  /** Ownership groups the flag currently admits, for the toolbar's select. */
  groups: string[];
  /** The whole filtered set's totals (AC-6), for the footer row. */
  totals: StockDebtTotals;
  /** Distinct last suppliers of the filtered set, sorted by name, for the toolbar's select (AC-7). */
  suppliers: StockDebtSupplierOption[];
  /** The export popover's exact sheet counts for the current filtered set (AC-7b). */
  sheet_counts: StockDebtSheetCounts;
}

/**
 * How a demand line ended up in a cell (AC-S2-7). `order_back` (STOCK-DEBT-LENDABLE, owner
 * 30 Sep 2026): the line lent its landed goods to nearer lines and is owed a re-buy for
 * them at its own date; `lent_qty` says how much.
 */
export type StockDebtDemandStatus = 'covered' | 'late' | 'short' | 'pinned' | 'order_back';

/** One sales-order line due in the cell's month (or in its TBA / undated bucket). */
export interface StockDebtDemandLine {
  so_number: string;
  agent_code: string | null;
  /** The bin the line is booked in - the drill's Bin column. Null for an unlocated line. */
  warehouse_code: string | null;
  /** `YYYY-MM-DD`, or null for an undated line. */
  required_date: string | null;
  open_qty: number;
  /**
   * R22: the drill's own Ordered/Delivered columns, beside `open_qty` (Outstanding,
   * unchanged) - `qty_ordered` is `plan_qty()` server-side (CS's own `qty_required` when
   * the Order Inquiry sheet states one, else the sales-order book's `qty_ordered`).
   * Optional on this TYPE only - the real wire always carries both - so a fixture built
   * before this round (`states the short quantity a LATE line still books`, R37) still
   * type-checks without adding fields it never asked to test.
   */
  qty_ordered?: number;
  qty_delivered?: number;
  assigned_qty: number;
  /** Human source of the assignment: `On hand DC1-BB`, `SPO 2026/08-0063`, `PO ... line 3`.
   *  R29 retires this for `assigned_from` below, kept only because it is still a
   *  declared field on the wire. */
  assigned_source: string | null;
  status: StockDebtDemandStatus;
  /**
   * What the line went short of ON ITS OWN DATE - the quantity its month books (R37).
   * A `late` line ends covered and still carries one: it went without on the date it was
   * promised, and that is the fact the month states.
   */
  short_qty: number;
  /** The sales order this line belongs to - the Sales order cell's own link target
   *  (`/scm/sales-orders/<id>`, R29). Optional on this TYPE only so a fixture built
   *  before this round still type-checks; the real wire always carries it. */
  sales_order_id?: string | null;
  /**
   * R29: `assigned_source` (free text) replaced by one LINKED entry per source, each
   * with its own quantity. Optional on this TYPE only, same reason as `sales_order_id`
   * above.
   */
  assigned_from?: StockDebtAssignedFrom[];
  /**
   * STOCK-DEBT-LENDABLE: what nearer lines took of this line's lendable landed goods -
   * the "order back N" the status pill prints. 0 (or absent, on an older fixture) on a
   * line that lent nothing.
   */
  lent_qty?: number;
}

/**
 * One source behind an assigned quantity (R29 + addendum): a document (SPO/PO) or an
 * on-hand bin, each carrying its OWN quantity so the From cell can print one linked
 * entry per source instead of one merged sentence.
 *
 * `oi_number`/`oi_id` name the order inquiry a PINNED placement came through - both
 * `null` on a document source that was WALK-assigned (no placement behind it). An
 * on-hand entry never carries either key at all: it never comes through a placement,
 * so there is nothing to name (`response_model` drops what a kind never declares).
 */
export interface StockDebtAssignedFromOnHand {
  kind: 'on_hand';
  ref: string;
  spo_number: null;
  spo_line_number: null;
  qty: number;
  /** STOCK-DEBT-LENDABLE: the sales order whose landed goods this take was LENT from
   *  ("On hand BRW-BB (from SO381065)"). Null on free stock at the same bin. */
  lent_from_so_number?: string | null;
}

/**
 * STOCK-DEBT-LENDABLE: one receiver of this line's lendable landed goods - "Lent to
 * SO396071 (32)". Not a source (nothing was assigned by it); listed under Covered by so
 * the far line says where its goods went, linking the receiving order.
 */
export interface StockDebtAssignedFromLent {
  kind: 'lent';
  ref: string;
  qty: number;
  so_number: string;
  sales_order_id: string | null;
}

export interface StockDebtAssignedFromDocument {
  kind: 'spo' | 'po';
  ref: string;
  spo_number: string | null;
  spo_line_number: number | null;
  qty: number;
  oi_number: string | null;
  oi_id: string | null;
  /** R42 (28 Sep 2026): a PO's own link target - its number, its line within the
   *  document, and the two ids the document dialog opens a PO on. Null for any other
   *  kind. Optional on the TYPE only so a fixture built before R42 still type-checks. */
  po_number?: string | null;
  po_line_number?: number | null;
  po_id?: string | null;
  po_line_id?: string | null;
}

export type StockDebtAssignedFrom =
  | StockDebtAssignedFromOnHand
  | StockDebtAssignedFromDocument
  | StockDebtAssignedFromLent;

/** What a supply event is: stock already held, a shipment arriving, or a PO on order. */
export type StockDebtSupplyKind = 'on_hand' | 'spo' | 'po';

/** One supply event landing in the cell's month (AC-S2-7). */
export interface StockDebtSupplyEvent {
  kind: StockDebtSupplyKind;
  /** Document reference. Null for on hand, which is a bin rather than a document. */
  ref: string | null;
  /** R29: the SPO's own number/line off `spo_allocations` - the Document cell's link
   *  target. Null for on hand and for the PO kind. */
  spo_number?: string | null;
  spo_line_number?: number | null;
  /** R42 (28 Sep 2026): a PO's own link target - its number, its line within the
   *  document, and the two ids the document dialog opens a PO on. Null for any other
   *  kind. Optional on the TYPE only so a fixture built before R42 still type-checks. */
  po_number?: string | null;
  po_line_number?: number | null;
  po_id?: string | null;
  po_line_id?: string | null;
  warehouse_code: string | null;
  /** Arrival: today for on hand, the SPO's arrival, and for a PO line its Delivery date
   *  (R42), else `issue + lead` (R29). */
  date: string | null;
  /** PO only: the SO delivery date the line was typed against. Display only (R30). */
  bought_for: string | null;
  qty: number;
  /**
   * R26: an SPO's own Received/Outstanding - `qty` above is the RAW ordered quantity for
   * an SPO row, `outstanding_qty` the walk's own netted balance. R42: a PO row states the
   * same three. Both `null` for on hand (on hand has no received/outstanding history to state), so the drill
   * prints those two columns blank rather than a fabricated 0.
   */
  received_qty?: number | null;
  outstanding_qty?: number | null;
  /** What nobody took by the end of the walk - the quantity its month credits (R37). */
  free_qty: number;
  /** Arrival passed with nothing received: listed, but counted as nothing (R31). */
  overdue: boolean;
  /** R29: `line_no` is the SO LINE's own number (the project mirror's `line_no`), beside
   *  `so_number` - "SO382618 line 2 (100)". Optional/nullable on this TYPE only so a
   *  fixture built before this round still type-checks; null when the core line has no
   *  project-line number of its own. */
  assigned_to: { so_number: string; line_no?: number | null; qty: number }[];
}

/** One location and kind of pool supply in a month (`BRW on hand`, `HQ PO`). */
export interface StockDebtPoolSource {
  label: string;
  qty: number;
}

/** STOCK-DEBT-PROJECTION ("pool a"): how one month's projected balance is made up
 *  (`projected = month_balance + carried_in + pool_supply - dealer_demand`);
 *  `carried_out` is the pool surplus the next month starts with. */
export interface StockDebtProjection {
  carried_in: number;
  month_balance: number;
  pool_supply: number;
  pool_sources: StockDebtPoolSource[];
  dealer_demand: number;
  projected: number;
  carried_out: number;
}

/**
 * The cell drill (R28): the two tables behind one product x month.
 *
 * `sum(supply.free_qty) - sum(demand.short_qty)` is the balance of the cell that opened it
 * (R37), which is what the two tab footers print.
 */
export interface StockDebtCell {
  demand: StockDebtDemandLine[];
  supply: StockDebtSupplyEvent[];
  /**
   * R25: the tab labels' own quantity totals, over the WHOLE tab - never recomputed by
   * the FE from a page of rows the server has already reduced. Optional on this TYPE
   * only (the real wire always carries both) so a fixture built before this round still
   * type-checks; the dialog falls back to summing its own rows when either is missing.
   */
  demand_total_qty?: number;
  supply_total_qty?: number;
  /** Set only when the drill was opened with the "Projected balance" switch on. */
  projection?: StockDebtProjection | null;
}

/**
 * How the export workbook is split into sheets (R5). One sheet for `none`. Re-exported off
 * the shared `ExportSplit` (`components/common/export-split.ts`, PLAN-low-stock-export-
 * split-25sep) so both Stock Debt and the low stock report speak the same four values -
 * kept under this name so nothing else here changes.
 */
export type StockDebtExportSplit = ExportSplit;

/** A rough count for the export popover's "212 rows, 14 sheets" line (AC-33). Best-effort:
 *  built from what the board already has loaded (`pagination.total` + `suppliers`), not a
 *  server round trip of its own - the real, exact counts are what the workbook itself carries. */
export interface StockDebtExportPreview {
  rows: number;
  sheets: number;
}
