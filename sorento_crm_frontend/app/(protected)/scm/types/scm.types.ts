/**
 * SCM M1 - shared types (net-position dashboard + sales orders).
 *
 * M1 "real" fields carry live numbers. M1 "deferred" fields are typed `| null`
 * and MUST render as "-" in the UI (never a fabricated number) - they light up
 * in M2/M3 (avg-daily-demand, days-of-cover, ROP, low/overstock composition).
 */

/** Health-state vocabulary. Backend-real: stockout (rendered "Out of stock"), dead,
 *  low (rendered "Low stock" - stocked but net <= demand-aware reorder point, M8-B),
 *  healthy, incoming, overstock (days-of-cover over the ceiling, server-computed). */
export type HealthState =
  | 'stockout'
  | 'low'
  | 'healthy'
  | 'overstock'
  | 'dead'
  | 'incoming';

export type Perspective = 'product' | 'warehouse' | 'supplier';

/** ABC = consumption-value class; XYZ = demand-variability class (M2). Both are
 *  `| null` on a row when unknown (e.g. a null-cost SKU can't be ABC-classed). */
export type AbcClass = 'A' | 'B' | 'C';
export type XyzClass = 'X' | 'Y' | 'Z';

/** Filter-bar selections. `unknown` targets rows the analytics job couldn't
 *  classify (null class), so they stay reachable rather than silently hidden. */
export type AbcFilterValue = AbcClass | 'unknown';
export type XyzFilterValue = XyzClass | 'unknown';

/** Product-lifecycle scope. Defaults to the FOCUSED view (`active` + `ongoing`)
 *  so inactive/discontinued SKUs don't inflate the headline counts; `all` widens
 *  the scope back to every SKU. → products.is_active / products.is_discontinued. */
export type ActiveStatusFilter = 'active' | 'inactive' | 'all';
export type LifecycleFilter = 'ongoing' | 'discontinued' | 'all';

/** Supplier performance scorecard (M2 - supplier×product rolled to supplier for
 *  display). `confidence` gates how loudly the score is shown; `low` = thin
 *  sample, render visually distinct and never oversell it. */
export interface SupplierPerformance {
  /** Share of receipts on/before expected + grace, 0-1. null = not derivable. */
  on_time_rate: number | null;
  avg_lead_time_days: number | null;
  /** Σ rejected ÷ Σ received, 0-1. */
  reject_rate: number | null;
  /** Σ received ÷ Σ ordered, 0-1. */
  fill_rate: number | null;
  /** Composite 0-100. */
  composite_score: number | null;
  /** PO→GR completed lines behind the score. */
  sample_size: number;
  confidence: 'high' | 'medium' | 'low';
}

/** Roll-up stat tiles. */
export interface ScmRollups {
  total_stock_valuation: number;
  dead_stock_valuation: number;
  stockout_count: number;
  incoming_po_count: number;
  /** ISO date of the nearest incoming PO ETA, or null if none open. */
  incoming_po_next_eta: string | null;
  /** SKUs missing cost_price - valuation excludes them; surfaced as a coverage note. */
  valuation_missing_cost_count: number;
  // M2 - real: Σ stock_valuation / count where days_of_cover > overstock_days.
  overstock_valuation: number | null;
  overstock_count: number | null;
  // Deferred (M3) - render "-": needs a real reorder point.
  below_rop_count: number | null;
}

/** One monthly bucket of DO outflow for the expandable product row's trend viz. */
export interface DemandSeriesPoint {
  /** ``YYYY-MM`` calendar month. */
  month: string;
  /** Summed outflow qty for the month (≥ 0). */
  qty: number;
}

/** One delivery order behind a SKU's avg-daily-demand, shown in the explain drill
 *  (M8-B9 / A2). Navigable to the order; `order_id` feeds the href, never displayed. */
export interface DemandExplainDO {
  order_id: string;
  do_number: string;
  order_date: string | null;
  qty_out: number;
}

/** `GET /analytics/explain/demand` - the demand working behind a SKU's avg daily
 *  demand: the delivery orders that drove outflow, the rate, and the variability
 *  (Coefficient of variation). Proves the number is fact-based, not fabricated. */
export interface DemandExplain {
  avg_daily_demand: number;
  demand_cv: number | null;
  demand_dos: DemandExplainDO[];
}

/** ~12 monthly buckets of DO outflow for one SKU (M2 demand trend). */
export interface DemandSeries {
  sku: string;
  product_name: string;
  warehouse_code: string | null;
  /** Demand-variability class → drives the plain-language caption. */
  xyz_class: XyzClass | null;
  /** Oldest → newest monthly buckets, zero-filled. */
  points: DemandSeriesPoint[];
  total_qty: number;
  peak_qty: number;
}

/** One warehouse tile in the Health Grid. */
export interface WarehouseHealth {
  warehouse_code: string;
  warehouse_name: string;
  /** The single worst health state in the warehouse - drives the accent bar. */
  worst_state: HealthState;
  /** on_hand × cost_price summed; null when no SKU in the warehouse has a cost. */
  stock_valuation: number | null;
  stockout_count: number;
  dead_count: number;
  incoming_po_count: number;
  sku_count: number;
  /** Composition mini-bar counts. low/overstock are deferred (null) at M1. */
  composition: {
    stockout: number;
    dead: number;
    healthy: number;
    incoming: number;
    low: number | null;
    overstock: number | null;
  };
}

/** Per-warehouse breakdown of an aggregated product row (expand target). */
export interface WarehouseBreakdown {
  warehouse_code: string;
  warehouse_name: string;
  on_hand: number;
  on_order: number;
  committed: number;
  net_position: number;
  stock_valuation: number | null;
  status: HealthState;
}

/** Product perspective: SKU aggregated across the selected warehouse set. */
export interface NetPositionRow {
  /** Human-readable SKU code - shown in the UI (never a UUID). */
  sku: string;
  product_name: string;
  product_class: string | null;
  variant: string | null;
  supplier_name: string | null;
  on_hand: number;
  on_order: number;
  committed: number;
  /** net_position = on_hand + on_order − committed. */
  net_position: number;
  stock_valuation: number | null;
  last_movement_at: string | null;
  status: HealthState;
  /** True when one warehouse is stocked out while another has surplus. */
  imbalance: boolean;
  /** Attention badge: stockout WITH open committed demand. */
  stockout_with_committed: boolean;
  /** Server-side attention sort key (lower = more urgent). */
  attention_rank: number;
  warehouses: WarehouseBreakdown[];
  // M2 - real analytics (demand_stat + item_classification), server-fed.
  avg_daily_demand: number | null;
  days_of_cover: number | null;
  abc_class: AbcClass | null;
  xyz_class: XyzClass | null;
  // Deferred (M3) - render "-".
  reorder_point: number | null;
}

/** Product line used by the drill-down popups (stat tiles, warehouse counts,
 *  "view products"). Read-only, human code + name + warehouse + the same metric
 *  columns as the Product-perspective grid. Never a UUID. */
export interface ProductSummary {
  sku: string;
  product_name: string;
  /** UUIDs carried ONLY for the avg-daily-demand explain fetch (M8-B9); never
   *  displayed - the drill resolves them to human-readable DO numbers. */
  product_id?: string | null;
  warehouse_id?: string | null;
  warehouse_code: string;
  warehouse_name: string;
  on_hand: number;
  on_order: number;
  committed: number;
  /** net_position = on_hand + on_order − committed. */
  net_position: number;
  /** on_hand × cost_price; null when the SKU has no cost on file. */
  stock_valuation: number | null;
  status: HealthState;
  /** Stocked out WITH open committed demand - the attention badge. */
  stockout_with_committed: boolean;
  // M2 - real analytics (demand_stat + item_classification), server-fed.
  avg_daily_demand: number | null;
  days_of_cover: number | null;
  abc_class: AbcClass | null;
  xyz_class: XyzClass | null;
  // M8-B - engine reorder point (latest completed run); null when the SKU was never
  // planned. Surfaced as a column in the "Low stock" drill so net <= ROP is visible.
  reorder_point?: number | null;
  // M8-F10 - the ROP inputs from the SAME latest-run rec that sourced reorder_point
  // (rec.inputs JSONB): safety_stock + supplier lead-time days. Null when un-planned.
  // Shown with a one-line plain definition in the Low-stock reorder-point (i).
  safety_stock?: number | null;
  lead_time_days?: number | null;
}

/** Paginated drill-down response (server-side search/sort/pagination). */
export interface ProductSummaryPage {
  data: ProductSummary[];
  total: number;
  page: number;
}

/** Supplier perspective: one SKU line under a supplier group. */
export interface SupplierSkuRow {
  sku: string;
  product_name: string;
  on_hand: number;
  on_order: number;
  net_position: number;
  status: HealthState;
  incoming_po_eta: string | null;
}

export interface SupplierGroup {
  supplier_code: string;
  supplier_name: string;
  /** Declared (contractual) lead time in days; null if not on file. */
  declared_lead_time_days: number | null;
  incoming_po_count: number;
  incoming_po_next_eta: string | null;
  skus: SupplierSkuRow[];
  /** M2 scorecard (real supplier-level analytics). null = not yet scored. */
  performance: SupplierPerformance | null;
}

// ---------------------------------------------------------------------------
// Sales orders (M1-D14)
// ---------------------------------------------------------------------------

// `medium` is what the seed / back-end emits; `normal` is the FE-form default -
// both are supported. Any unknown value still renders via a neutral fallback.
export type SalesOrderPriority = 'low' | 'medium' | 'normal' | 'high' | 'urgent';
export type SalesOrderStatus =
  | 'open'
  | 'partially_delivered'
  | 'fulfilled'
  // 11,006 orders absorbed from the AutoCount export land here: read as delivered off a
  // spreadsheet rather than delivered by this system. The status exists in the data, so it
  // belongs in the type; `statusBadge` title-cases anything it does not recognise anyway.
  | 'closed'
  | 'cancelled';

export interface SalesOrderLine {
  id: string;
  /**
   * AutoCount's own line number (`Seq`), kept as it arrives - the address the Lines tab
   * sorts and labels by (PLAN-so-lines-autocount-order.md). `null` for a line AutoCount
   * never numbered: an order-inquiry, upload, absorbed-history or manual line, or an
   * AutoCount line pushed before this column existed.
   */
  line_no?: number | null;
  sku: string;
  product_name: string;
  qty_ordered: number;
  /** Stamped by create-DO-from-SO (soft link, no hard FK). */
  qty_delivered: number;
  /** What is still to go out on this line, off the SERVER: `ordered - delivered` floored at
   *  0, and **0 on a closed line** whatever those two say. A book re-upload closes a line by
   *  absence without knowing what shipped, so the subtraction alone reads as the whole
   *  quantity still being owed on an order that is done. */
  outstanding_qty?: number;
  uom: string;
  /** The line's money, as decimal STRINGS - the backend sends `Decimal` and Pydantic
   *  serialises it as a string, which is what keeps RM 985.00 from arriving as
   *  984.9999999. `null` when nobody priced the line, which is not the same as 0.
   *  `line_total` is what the source document charged; the other two are its parts. */
  unit_price?: string | null;
  discount?: string | null;
  line_total?: string | null;
  /** Where this line ships from. Per line: one order can land in two locations. */
  warehouse_code?: string;
  /** `open` or `closed`. A closed line is not a commitment however much it still shows. */
  line_status?: string;
  /**
   * Where THIS LINE came from - never inherited from the header (R2): a line with no
   * provenance of its own reads Manual even under an AutoCount or uploaded header, since
   * the line itself is what carries no source system. Same vocabulary as the header's own
   * `source`, plus `autocount`.
   */
  source?: string;
  /** When this line's quantity is due. Per line, for the same reason as the location. */
  required_date?: string | null;
  /**
   * The order inquiry covering this line, reached through the planning record's mirror
   * line, and the state purchasing left that instruction in. `null` when nobody has
   * raised one for the line - which is the honest answer, not an empty object.
   *
   * Sent on the single order read only: the list has no column for it and would pay two
   * more queries a page for a fact nothing there prints.
   */
  order_inquiry?: SalesOrderLineInquiry | null;
  /**
   * Which confirmed revision decided supply for this line: the ACTIVE decision's
   * `revision_no` when its frozen snapshots cover the line, `null` otherwise. A line
   * inside an order that has an active decision but was left out of it is not decided,
   * and reads `null` exactly like a line on an order nobody has planned.
   */
  decision_revision?: number | null;
  /**
   * What was DECIDED for this line and what the engine had SUGGESTED, as components, in the
   * planning board's vocabulary (`PLAN-scm-cs-planning-uat.md` section 2).
   *
   * COMPONENTS, never a sentence: the words are written once, by
   * `project-sales/_shared/lib/supplyVocabulary.describe`, and a sentence composed on the
   * backend would be a second implementation of the same vocabulary free to drift.
   *
   * `supply_decided` is null on a line no active revision covers. `supply_proposed` is null
   * for that AND for a revision frozen before the proposal was recorded, which the column
   * reads as "Not recorded" rather than as "the engine suggested nothing".
   */
  supply_decided?: SalesOrderLineSupplyComponent[] | null;
  supply_proposed?: SalesOrderLineSupplyComponent[] | null;
  /**
   * A SAVED (unconfirmed) decision on this line - the planning board's own draft, not yet
   * promoted by Confirm (D10, captain 3 Sep). `null` when no draft covers the line, OR the
   * line already carries `supply_decided`: Confirm deletes the draft it promotes in the
   * same write, so the two never both answer for one line at once.
   */
  supply_saved?: SalesOrderLineSupplyComponent[] | null;
  /** Who saved it, by name - never an id - and when, ISO. Both `null` exactly when
   *  `supply_saved` is `null`. */
  saved_by?: string | null;
  saved_at?: string | null;
  /** The engine has re-suggested this line since it was saved, judged on the line's own
   *  outstanding quantity and required date - never true when `supply_saved` is `null`. */
  saved_stale?: boolean;
  /**
   * Where this line's Buy actually sits (AC-I9): every link on the order inquiry row that
   * covers the line, PO or SPO, with the quantity each holds. Read off the SAME child
   * table (`projects.order_inquiry_links`) the worklist's "Linked to" column and the PO
   * occupancy panel read, so the three cannot disagree.
   *
   * `[]` when the row exists and nothing has been linked yet; absent when no inquiry row
   * covers the line at all - the two are different answers and the column says so.
   */
  linked_to?: SalesOrderLineLink[] | null;
  /**
   * The planning-change BATCH ROW behind this line, when one exists. Enough for this screen
   * to draw the same "what changed" reading the fulfilment board's own dialog gives it
   * (`BoardChangeTable`, `board-change-icon-<id>` / `board-change-dialog`) - never the whole
   * batch row, which carries fields (`from`/`to`, `facts`, `suggestion`, `inquiry_rows`...)
   * this screen has no question for.
   *
   * Slice D: once Apply has run on a CANCELLED line, the line leaves the fulfilment board
   * entirely (a closed line has no cell), so that dialog's "Where it went" list - where the
   * line's held composition actually landed, e.g. `Reallocate 202607-S0080 3 to pool` - is
   * unreachable there. This is the same fact, read here instead.
   */
  planning_change?: {
    /** The batch row's own id - the `board-change-icon-<id>` / dialog title key. */
    id: string;
    kind: string;
    applied_state: string;
    /** `null` on a row Apply has not written yet. */
    result?: { executed_reallocations: string[]; released_documents: string[] } | null;
  } | null;
}

/** One link on the order inquiry row covering a sales order line. Never an id on screen. */
export interface SalesOrderLineLink {
  /** Which book the document lives in. */
  kind: 'po' | 'spo';
  /** `202607-S0105`, `SPO-2026/08-0061`. */
  document: string;
  /** `L3` when the book numbered the line. Absent when it did not - never invented. */
  line_label?: string | null;
  /**
   * The PO/SPO header this link points at (L4, review round). Never rendered as text - it
   * makes `document` a link to `/scm/purchase-orders/{id}`. Absent when the caller building
   * this row does not have the id at hand, in which case `document` stays plain text.
   */
  purchase_order_id?: string | null;
  qty: string;
  location?: string | null;
  expected_date?: string | null;
  /**
   * The document arrives AFTER the row's own required date (AC-P3-7). Stated, never acted
   * on: purchasing decides whether a late document is still the answer, and unlinking it
   * would leave the line with nothing rather than with something late.
   */
  late?: boolean;
  /** BY HOW MUCH, in days (AC-D17). Null when the document is not late. */
  late_days?: number | null;
  /**
   * A derived SPO is an OPEN SPO allocation the row's linked PO was raised from (S5 of
   * `PLAN-scm-oi-excel-parity.md`) - never a stored link, so the SO detail marks it "via PO"
   * rather than treating it as a link this system made independently.
   */
  derived?: boolean;
}

/**
 * One piece of a line's supply. The same three facts the board reads a source by: what kind,
 * how much, and from where - plus the RUNG, which is what decides the words. Never the
 * warehouse code, which reads `BRW-BB` (the agent's own location) and `BRW` (the shared pool)
 * as the same site when they are not the same kind of supply at all.
 */
export interface SalesOrderLineSupplyComponent {
  kind: string;
  qty: string;
  source_location?: string | null;
  rung?: string | null;
  /** The sales order a borrow was taken FROM, when one was named. */
  donor_so_number?: string | null;
}

/** The inquiry an order line belongs to, in the two words a person reads it by. */
export interface SalesOrderLineInquiry {
  /** `OI-000123`. Null only on a row raised before inquiries were numbered. */
  inquiry_no?: string | null;
  /** The ROW's own state (`raised` / `partly_linked` / `placed` / `actioned` /
   *  `cancelled`), not the header's: "has purchasing linked this line" is the question
   *  the column answers. `placed` reads "Linked". */
  state: string;
  /**
   * S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-2/AC-B6-8): the order inquiry
   * HEADER's own id and this LINE's OI row id, addressing the "Order inquiry" cell's own
   * link (`/project-sales/order-inquiries/<inquiry_id>?row=<row_id>`). Optional because a
   * row raised before inquiries were numbered carries neither - absent means the cell
   * stays plain text.
   */
  inquiry_id?: string | null;
  row_id?: string | null;
}

export interface SalesOrder {
  id: string;
  /** Human-readable SO number - shown in the UI (never a UUID). */
  so_number: string;
  order_type: string;
  order_type_label: string;
  customer_code: string;
  customer_name: string;
  /** Drives demand_nature in M2 channel-tagging. */
  market_segment: string | null;
  priority: SalesOrderPriority;
  status: SalesOrderStatus;
  order_date: string;
  requested_delivery_date: string | null;
  /** Every DISTINCT date this order's lines are due on (`sales_order_lines.required_date`),
   *  earliest first. Empty when no line names one - an order is not due "today" because
   *  nobody dated it. This is what the list's "Delivery date" column shows; the header's own
   *  `requested_delivery_date` is a different figure and stays on the detail page.
   *
   *  A list, not the `delivery_date_from`/`_to` span it replaced: an order due on 12 January
   *  and 10 March is due on two days, and a range claims the eight weeks between them. */
  delivery_dates?: string[];
  /** Who sold it - the `sales_agents` master. The id rides along only so an edit select
   *  can pre-select the current agent; a person reads `sales_agent_code` / `_label`,
   *  never the id. Absent (all three null) when the order names no agent. */
  sales_agent_id?: string | null;
  sales_agent_code?: string | null;
  /** `sales_agents.person_label` - the human the code belongs to, when set. */
  sales_agent_label?: string | null;
  total_qty: number;
  /** Undelivered qty = committed demand contributed to the dashboard. */
  committed_qty: number;
  /** What the order is worth, summed from its lines. A decimal STRING for the same reason
   *  the line figures are; `null` when not one line carries money, which is not 0. */
  total_amount?: string | null;
  /** What the order says, and how much of it is still open. */
  line_count?: number;
  open_line_count?: number;
  lines: SalesOrderLine[];
  /** Where the order came from - it decides who may edit its figures. `inquiry` = the Order
   *  Inquiry sheet created it, `upload` = CS's outstanding extract, `manual` = keyed in. */
  source?: SalesOrderSource;
  /** The project the Order Inquiry sheet named when no customer of that name existed. */
  internal_note?: string | null;
  /** The human-readable project name, resolved from whichever source ranked highest -
   *  the Order Inquiry sheet, the AutoCount note, its `Ref`, or a delivery address. Never
   *  a UUID. `null` when nothing has ever named a project for this order. */
  project_label?: string | null;
  /** Which of the four rules wrote `project_label` - decides the muted word shown next to
   *  it (`Inquiry sheet` / `Note` / `AutoCount ref` / `Delivery address`). */
  project_label_source?: 'inquiry' | 'note' | 'ref' | 'delivery' | null;
  /** Every distinct location its lines ship from. Plural: one order can land in two. */
  stock_locations?: string[];
  /** The planning class this order was classified into, or `null` when nobody has ever
   *  said. Distinct from `order_type_label` - see `lib/demandClass.ts`. */
  demand_class?: 'project' | 'retail' | null;
  /** AutoCount's `Transferable` flag, read-only (AutoCount owns it). `false` = not confirmed
   *  for the queue yet, so Stock Debt and the fulfilment ladder skip the order; `null` = the
   *  source never said (counted). */
  is_transferable?: boolean | null;
  /** The purchase orders its lines wait on. Present on the LIST, absent on a single read. */
  linked_purchase_orders?: LinkedPurchaseOrder[];
  awaiting_purchase_orders?: number;
  /** The order inquiries raised against this order - on the list AND the single read.
   *  Empty for an order nobody has planned: the business sees sales orders and order
   *  inquiries and nothing between them, so this is where an order says what has been
   *  done about it. */
  order_inquiries?: SalesOrderInquiry[];
  /**
   * How much of this order anybody has DECIDED, in two counts, on the LIST and on the single
   * read (a field missing from either dict builder never reaches the screen).
   *
   * `plannable_lines` is how many of its lines the fulfilment board would admit - not
   * cancelled, not marked no purchase needed, and asking for something. `planned_lines` is
   * how many of those are already settled, by an active supply decision or by a live order
   * inquiry row. The two are counted by the SAME predicates the board itself uses, so the
   * list and the board cannot disagree about one order.
   *
   * Not the delivery question. A completed order whose stock shipped without anybody sourcing
   * it reads 0 of 3, which is the whole point: it is the order nobody planned.
   */
  planned_lines?: number;
  plannable_lines?: number;
  /**
   * The PENDING planning-change batch this order is in, when a re-uploaded book moved one of
   * its planned lines and nobody has applied the change yet (AC-P3-1). Present on the LIST,
   * which is where the Changed badge is; `null` on every order with nothing outstanding, which
   * is nearly all of them.
   */
  planning_change_batch_id?: string | null;
  created_at: string;
}

/** One order inquiry raised against a sales order, by NUMBER - never by id. */
export interface SalesOrderInquiry {
  /** The order inquiry HEADER's own id (`PLAN-oi-header-list-detail.md`, S3, AC-LK-01) -
   *  addresses `/project-sales/order-inquiries/<id>`, never shown. Absent on a payload
   *  from before that column existed; the link then falls back to the old filtered-list
   *  search. */
  id?: string | null;
  inquiry_no: string | null;
  state: string;
  /** ISO datetime, or null on a record that predates the column. */
  raised_at: string | null;
  raised_by_name: string | null;
  /** How far purchasing has got: `rows_placed` of `rows_total` instructions placed. */
  rows_total: number;
  rows_placed: number;
}

export type SalesOrderSource = 'autocount' | 'inquiry' | 'upload' | 'history' | 'manual';

/** A pairing this order's lines claim, and whether both sides are present. */
export interface LinkedPurchaseOrder {
  po_number: string;
  item_code: string | null;
  resolved: boolean;
}

export interface SalesOrderFormData {
  /**
   * The ERP document type. The CREATE modal still sends it; the detail page's edit does
   * not - it edits the planning class under its own name (`demand_class` below), because
   * that is what the screen renders and `order_type` is NULL on 96% of the book. An
   * omitted key leaves the stored value alone.
   */
  order_type?: string;
  /** The planning class the detail page shows and now writes: `project`, `retail`, or
   *  `''`/`null` meaning "unclassified - leave the stored classification alone". */
  demand_class?: string | null;
  customer_code: string;
  priority: SalesOrderPriority;
  /** When the order was raised. ISO `yyyy-mm-dd`; omitted leaves it alone. */
  order_date?: string | null;
  requested_delivery_date?: string | null;
  /**
   * `undefined` leaves the stored agent alone (the field was never sent); `null` or `''`
   * explicitly CLEARS it; an id sets it. Always sent on the detail page's save, so the
   * "leave alone" case in practice only applies to a payload that never sets this key -
   * see `toWritePayload`.
   */
  sales_agent_id?: string | null;
  /**
   * Omitted entirely on an update where the person never touched a line - a header-only
   * edit (order type, customer, dates) must not resend lines: the BE upserts by `id` (or
   * SKU when no `id` is given), and a KEY left off a sent line (`warehouse_code` /
   * `required_date` / `uom`) leaves that line's stored value alone rather than clearing
   * it. Always present (non-empty) on create.
   */
  lines?: {
    /** The existing line's id, carried on an edit so the BE matches by id rather than
     *  falling back to SKU. Absent on create, where the line does not exist yet. */
    id?: string;
    sku: string;
    qty_ordered: number;
    /** Omitted leaves the line's stored UoM alone; `null`/`''` clears it (falls back to
     *  the product's base UoM); a value sets an override. */
    uom?: string | null;
    /** Warehouse CODE, never the UUID. Omitted leaves the line's warehouse alone; `null`/
     *  `''` clears it; a code sets it. */
    warehouse_code?: string | null;
    /** ISO `yyyy-mm-dd`. Same omitted/clear/set semantics as `warehouse_code`. Shown on
     *  the detail page as "Delivery date". */
    required_date?: string | null;
    /** What the customer pays, and what came off it. Decimal STRINGS (never floats - see
     *  `SalesOrderLine`), with the same omitted/clear/set semantics as `uom`. The line
     *  TOTAL is deliberately not writable: it is what the source document charged. */
    unit_price?: string | null;
    discount?: string | null;
  }[];
}

// ---------------------------------------------------------------------------
// Purchase orders (read-only at M1 - create/confirm lands in M4)
// ---------------------------------------------------------------------------

// `active` is the seed / back-end value for a confirmed-but-not-fully-received
// PO. Any unknown value still renders via a neutral fallback.
//
// `draft_recommendation` (M4-D4) is a PO drafted from an accepted reorder
// recommendation. It is deliberately OUTSIDE the on-order set - `scm.on_order_v`
// counts only status IN ('active','received','partial','closed'), so a draft is
// NOT counted as incoming supply until it is confirmed (M4-D5). Confirming a
// draft flips it to `active` and it then counts as on-order (M4-D6).
export type PurchaseOrderStatus =
  | 'draft'
  | 'draft_recommendation'
  | 'active'
  | 'confirmed'
  | 'partially_received'
  | 'received'
  // Imported purchase history is written closed and fully received (it is history, not
  // incoming supply). The union omitted it, so every historical order was typed as
  // something it is not and only rendered by the title-case fallback.
  | 'closed'
  | 'cancelled';

export interface PurchaseOrderLine {
  id: string;
  sku: string;
  product_name: string;
  qty_ordered: number;
  qty_received: number;
  /** What is still to ARRIVE on this line, off the SERVER: `ordered - received` floored at
   *  0, and **0 on a closed line** whatever those two say. Same rule and same reason as the
   *  sales book's own `outstanding_qty`. */
  outstanding_qty?: number;
  uom: string;
  /** The line's own destination - location is a line fact, never a header one. */
  warehouse_code?: string | null;
  /** `open` or anything else. A line that has left the book is not a commitment however
   *  much quantity it still shows. */
  line_status?: string;
  /** When this line's goods are due. Per line, for the same reason as the location. */
  expected_date?: string | null;
  /**
   * What we pay for this line, and what came off it. Decimal STRINGS, never floats: the
   * backend sends `Decimal`, Pydantic serialises it as a string, and a float sum of 200
   * line totals drifts by a cent that then reads as a data problem.
   *
   * `unit_price` is the `purchase_order_lines.unit_cost` column under the name the sales
   * screen uses for the same fact, so the two grids one click apart do not label one figure
   * two ways. All three are null when the source document priced nothing - a 0 discount
   * claims a discount of nothing was given, and a 0 total claims free goods.
   */
  unit_price?: string | null;
  discount?: string | null;
  line_total?: string | null;
  /** The currency the figures above are IN. Blank predates the book having more than one. */
  currency?: string | null;
  /**
   * The AutoCount book's own sales-order linkage for this LINE, read off the line's own
   * `from_so_line_ref` - ONE sales order, never a list, because the book records a
   * purchase line as raised for a single sales-order line. Distinct from
   * `PurchaseOrderLineAllocation.dedicated_to` below, which is who RESERVED the line
   * through our own order-inquiry flow. A line can carry one, the other, both or neither.
   *
   * `null` both when the book named no sales order at all and when it named one this CRM
   * does not hold; `book_so_unresolved` is what tells those two apart
   * (`PLAN-scm-book-linkage-on-document-lines.md`).
   */
  book_so_number?: string | null;
  /** True when the book named a sales order this CRM does not hold. Three display states,
   *  not two - see `BookSoCell`. */
  book_so_unresolved?: boolean | null;
}

/**
 * One order-inquiry placement sitting on a purchase-order line (section 3.G, AC-G1).
 *
 * The captain, 25 August 2026: when an order inquiry occupies quantity on a PO, the PO must
 * show how much of its outstanding is occupied, by which inquiry and sales order, and at
 * which location - and it must live BESIDE the line, never as columns in it, because the
 * buyer re-keys the split in AutoCount and re-uploads, and an upload that overwrote our
 * split would lose it.
 *
 * Everything here is a NAME, never an id: the inquiry by its number, the sales order by its
 * document number, the customer and the agent by the labels the order-inquiry worklist
 * already prints for them.
 */
export interface PurchaseOrderPlacement {
  /**
   * Which kind of placement this is (F7).
   *
   * `inquiry` is the original panel - somebody's order inquiry linked to this line.
   * `spo` is a CRM SPO that PULLED from this line: the goods are already on a container,
   * and the fields below that describe a customer are null because there is no inquiry
   * behind it.
   */
  kind?: 'inquiry' | 'spo';
  /** The SPO that took this quantity - `spo` rows only. */
  spo_number?: string | null;
  /**
   * The SPO's own header id (L2, review round). Never rendered as text - makes
   * `spo_number` a link to `/scm/purchase-orders/{id}`. `spo` rows only.
   */
  purchase_order_id?: string | null;
  /** The container it is on, by container number or shipment number - `spo` rows only. */
  packing_list?: string | null;
  /** Where it is landing, and how much at each - `spo` rows only. */
  warehouses?: { warehouse_code: string; qty: number }[];
  /** When the container is due - `spo` rows only. */
  arrival_date?: string | null;
  /** `OI-000006`. Null only on a row raised before the numbering stamp existed. */
  inquiry_no: string | null;
  /** The sales order this quantity is owed to - the AutoCount number where there is one. */
  so_number: string | null;
  /** `YOTU BUILDER / LOT 2752` - the same label the worklist's PROJECT/CUSTOMER prints. */
  customer: string | null;
  /** Who sold it, by person label or agent code. Null on an order with no agent. */
  agent: string | null;
  /** How much of the PO line this placement occupies. */
  qty: number;
  /** Where the demand needs it - `order_inquiry_rows.stock_location`. */
  needed_at: string | null;
  /**
   * True when `needed_at` is not the PO line's own location: the PO line says DC1 and the
   * demand is at BRW-BB. That IS the split instruction for AutoCount, which is why it is a
   * mark on the row rather than a filter that hides it.
   */
  location_differs: boolean;
}

/** One purchase-order line's occupancy: the three figures, then who is on it (AC-G1). */
export interface PurchaseOrderLineAllocation {
  /** Which line this belongs to - matched against `PurchaseOrderLine.id`. */
  line_id: string;
  sku: string;
  /** The PO LINE's own location, so the panel can say what it differs from. */
  warehouse_code: string | null;
  /** `qty_ordered - qty_received`, floored at 0 and 0 on a closed line. */
  outstanding: number;
  /** The sum of every link on this line. */
  allocated: number;
  /**
   * `outstanding - allocated - what other sales orders' CLAIMS still reserve`, floored at
   * 0. A claim reserves the claiming order line's live outstanding (G7), so a line the
   * AutoCount book dedicates to somebody else is not free to buy against even where
   * nothing has been linked to it yet.
   */
  free: number;
  /** WHO the book (or a person, or the buy that created the line) dedicated it to, in
   *  sales-order date order. Empty on an ordinary undedicated line. */
  dedicated_to: PurchaseOrderDedication[];
  placements: PurchaseOrderPlacement[];
}

/** One sales order's claim on a purchase order line (G7 dedication). */
export interface PurchaseOrderDedication {
  /** The sales order document number - never an id. */
  so_number: string;
  /** What the claim reserves: that order line's LIVE outstanding. */
  reserved: number;
  /** How much of `reserved` the order has not placed on this line yet - what is actually
   *  still taken out of `free`. */
  unplaced: number;
  /** Which feed stated it: the purchase book (`po_history` / `po_upload`), the buy that
   *  created the line (`crm_supply`), a person in the Link dialog (`manual`), or the
   *  placement's own audit row (`order_inquiry`). */
  source: string;
}

/** One SOURCE purchase-order line a CRM SPO line pulled from (R1, AC-H5). */
export interface SpoPlanPull {
  /** Null on an older payload that predated this field - the FE renders the number as plain
   *  text rather than a link when it is absent. */
  purchase_order_id: string | null;
  po_number: string | null;
  po_line_label: string | null;
  qty: number;
}

/** One retail sales-order line a CRM SPO line covers (R1, AC-H5). */
export interface SpoPlanCover {
  so_number: string | null;
  customer: string | null;
  qty: number;
  warehouse: string | null;
}

/** The PO detail's Plan card (R1) - a `crm_spo` order's own pulls/covers, read off its
 *  lines' `source_ref`. `null`/absent on every other order. */
export interface SpoPlan {
  pulls: SpoPlanPull[];
  covers: SpoPlanCover[];
}

export interface PurchaseOrder {
  id: string;
  /** Human-readable PO number - shown in the UI (never a UUID). */
  po_number: string;
  supplier_code: string;
  supplier_name: string;
  warehouse_code: string | null;
  warehouse_name: string | null;
  status: PurchaseOrderStatus;
  order_date: string;
  /** Expected delivery / ETA; null when not committed yet. */
  expected_date: string | null;
  /** What the ORDER says: every line of it. Not what is still coming - see `open_qty`. */
  total_qty: number;
  line_count: number;
  /** What the PO still contributes as incoming supply. Zero on a received or historical
   *  order, which is why it is a separate figure rather than a narrowing of `total_qty`. */
  open_qty?: number;
  open_line_count?: number;
  lines: PurchaseOrderLine[];
  /**
   * What order inquiries have OCCUPIED on this order, one entry per line that carries at
   * least one placement (section 3.G). Absent on the list, present on the single read; an
   * empty array is a purchase order nothing is linked to yet, and the panel says so.
   */
  allocations?: PurchaseOrderLineAllocation[];
  /**
   * The sum of every placement across the whole order, on the LIST as well as the single
   * read (AC-G4). 0 is "nothing is linked to it", which is a different answer from a book
   * row that predates the links table - and both read as 0 here, deliberately: an order
   * nobody has linked to is exactly what the filter's "No" means.
   */
  allocated_qty?: number;
  created_at: string;
  /** True when this PO counts as incoming supply (on-order) - false for a draft
   *  or cancelled PO. Mirrors `scm.on_order_v`'s status filter (M4-D5/D6). */
  is_on_order?: boolean;
  /** How the PO originated - `recommendation` = drafted from an accepted reorder
   *  recommendation (Slice B); `import` = arrived through the purchase-history upload;
   *  `crm` = created by "Create SPO" off an inbound shipment (PLAN-scm-proforma-to-spo.md);
   *  `manual` = created directly. */
  source?: 'recommendation' | 'import' | 'crm' | 'manual';
  /** Goods-receipt reference once a GR has been created from this PO (M4-D6). */
  gr_reference?: string | null;
  /** What the order is WORTH, summed on the backend from the same line figures the Lines
   *  tab prints: each line's stated total where it has one, otherwise price x qty less
   *  discount. `null` when not one line carries money, which is not the same as 0. */
  total_amount?: string | null;
  /** The currency the order is written in. Blank means ringgit. */
  currency?: string | null;
  /** A `crm_spo` order's own pulls/covers (R1, AC-H7). `null`/absent on every other order. */
  spo_plan?: SpoPlan | null;
}

/**
 * What the detail page's Edit session writes. Mirrors `SalesOrderFormData`'s own
 * omitted/clear/set rules, which the backend reads through `model_fields_set`.
 */
export interface PurchaseOrderUpdateData {
  /** When the order was raised (`purchase_orders.issue_date`). ISO `yyyy-mm-dd`. */
  order_date?: string | null;
  /** When the goods are expected. ISO `yyyy-mm-dd`. */
  expected_date?: string | null;
  /** The supplier CODE, never the UUID. */
  supplier_code?: string | null;
  /**
   * Omitted entirely on a header-only edit, so the backend does not re-upsert every line
   * for nothing. A KEY left off a sent line leaves that line's stored value alone; an
   * explicit `null` clears it.
   */
  lines?: {
    /** The existing line's id, so the backend matches by id rather than falling back to
     *  SKU - which is what keeps `qty_received` and any goods-receipt link attached. */
    id?: string;
    sku: string;
    qty_ordered: number;
    uom?: string | null;
    warehouse_code?: string | null;
    expected_date?: string | null;
    /** Decimal STRINGS. The line TOTAL is deliberately not writable: it is what the
     *  supplier's document charged. */
    unit_price?: string | null;
    discount?: string | null;
  }[];
}
