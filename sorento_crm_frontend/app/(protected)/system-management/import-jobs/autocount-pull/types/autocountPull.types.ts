/**
 * AutoCount pull + review - shared FE shapes.
 * See `documentation/plans/autocount/PLAN-autocount-pull-review.md` ("Routes" and the pull job
 * metadata shape) and its UAC for the contract these mirror.
 */

/** The entities a pull can be started for; the values match the backend route param.
 *  `delivery_orders` (PLAN-autocount-do-pull-crm-30sep.md) previews and applies through the
 *  DO ingest and reviews on this same page. */
export type AutocountPullEntity =
  | 'products'
  | 'stock_balances'
  | 'delivery_orders'
  | 'goods_receive_notes';

/** The entities whose snapshot is a set of AutoCount documents read by DocDate
 *  (PLAN-autocount-grn-pull-crm-02oct.md): they take a DocDate scope, run the doc ingest,
 *  and compare against two files, one of lines and one of documents. */
export const DOCUMENT_ENTITIES: readonly AutocountPullEntity[] = ['delivery_orders', 'goods_receive_notes'];

export function isDocumentEntity(entity: AutocountPullEntity): boolean {
  return DOCUMENT_ENTITIES.includes(entity);
}

/** The permission slug that gates each entity's pull (backend `ENTITY_PERMISSIONS`). One
 *  place, so a list cannot wire the shared action with the wrong slug (review blocker 2). */
export const AUTOCOUNT_PULL_PERMISSION: Record<AutocountPullEntity, string> = {
  products: 'master_data.products.autocount_pull',
  stock_balances: 'inventory.stock.autocount_pull',
  delivery_orders: 'order_management.orders.autocount_pull',
  goods_receive_notes: 'procurement.grn.autocount_pull',
};

/** The flat scope a delivery-orders build takes (DO-PULL-SS contract): a day window, or one
 *  document by number; `null` / absent = the gateway's default, the last 31 MYT days. */
export interface AutocountPullScope {
  fromDay?: string;
  toDay?: string;
  docNo?: string;
}

/**
 * Where a pull is in its life. `building` = FoundryX still assembling the snapshot (no
 * Sorento worker job yet); `previewing` = the preview task is running; `review` = ready for
 * Confirm; `confirmed` = the apply job has been created; `failed` / `expired` / `discarded`
 * = dead ends - `discarded` is the owner throwing an open pull away on purpose
 * (PLAN-autocount-pull-discard.md), never a refusal.
 */
export type AutocountPullPhase =
  | 'building'
  | 'previewing'
  | 'review'
  | 'confirmed'
  | 'failed'
  | 'expired'
  | 'discarded';

export interface AutocountPullProgress {
  pagesDone: number;
  pagesTotal: number;
  stage?: string | null;
}

/** B3 (small-fix track): the preview task's own row-by-row progress through
 *  `MasterIngestService.ingest`'s `on_progress` (products) or set once at the end
 *  (stock, which has no per-record hook) - `null` before the task has published a
 *  total yet, and outside `previewing` (nothing left to show once the phase has
 *  moved on). Distinct from `progress` above, which is FoundryX's own page-fetch
 *  progress during `building`. */
export interface AutocountPullPreviewProgress {
  processed: number;
  total: number;
}

/**
 * The FoundryX ready header, as the pull route stores + returns it - camelCase, stripped of
 * `excludedRows` / `negativePairList` (AC-BD-2; both can be large and are not needed on the
 * page). Present once the snapshot has been read (`review` / `confirmed`), `null` before that.
 */
export interface AutocountPullHeader {
  snapshotId: string;
  entity: AutocountPullEntity;
  companyCode: string;
  extractedAt: string;
  expiresAt: string;
  recordCount: number;
  complete: boolean;
  contentHash: string;
  sourcePageSize?: number;
  /** Products only. */
  zeroListPriceCount?: number;
  negativeListPriceCount?: number;
  /** Stock only (SR4). */
  zeroPairs?: number;
  negativePairs?: number;
  fractionalPairs?: number;
  excludedCount?: number;
  excludedNonzeroCount?: number;
}

export interface ProductPullCounts {
  received: number;
  new: number;
  changed: number;
  unchanged: number;
  failed: number;
  left_out: number;
  price_to_zero: number;
}

export interface StockPullCounts {
  received: number;
  fed: number;
  not_applied_inactive: number;
  not_applied_unknown: number;
  qty_changes: number;
  set_to_zero: number;
  skipped_product_not_found: number;
  negative_in_autocount: number;
}

/** Delivery orders (AC-DP-10): the DO ingest's dry-run verdicts, one per document.
 *  `adopted` = an existing tracking-uploaded DO taken over by number (its tracking columns
 *  kept); `lines_to_delete` = old lines adoption cannot match plus lines a document no
 *  longer carries; `retryable` = a product or warehouse not in the CRM yet;
 *  `with_warnings` = documents carrying any warning (unresolved sales order, SO line,
 *  customer, branch, a line without item code) - never a blocker. */
export interface DeliveryOrderPullCounts {
  received: number;
  created: number;
  updated: number;
  adopted: number;
  unchanged: number;
  lines_to_delete: number;
  failed: number;
  retryable: number;
  with_warnings: number;
}

/** `goods_receive_notes` (AC-GP-10/13): the DO counters plus how many GRN lines the preview
 *  linked to a PO or SPO line and how many it could not. */
export interface GoodsReceiveNotePullCounts extends DeliveryOrderPullCounts {
  lines_linked: number;
  lines_unlinked: number;
}

export type AutocountPullCounts =
  | ProductPullCounts
  | StockPullCounts
  | DeliveryOrderPullCounts
  | GoodsReceiveNotePullCounts;

export interface AutocountPullCompareSummary {
  filename: string;
  compared_at: string;
  total: number;
  matched: number;
  different: number;
  only_in_excel: number;
  only_in_pull: number;
  qty_total_excel?: number | null;
  qty_total_pull?: number | null;
}

/** One field-level (or row-level) difference the Compare tab lists and can export - the
 *  REAL backend shape (`app/services/autocount_pull_compare.py`): `excel` / `pull`, not
 *  `your_excel` / `autocount_pull`. `excel`/`pull` are JSON-typed, not always strings - an
 *  `is_active` difference carries a boolean and `on_hand_qty` carries a number (small-fix
 *  track, autocount-compare-tab-detail: booleans rendered blank before this type was widened
 *  to match what the backend actually sends). */
export interface AutocountCompareDifference {
  item_code: string;
  /** Delivery orders compare only - the line's document number. */
  doc_no?: string;
  /** Stock and delivery orders compare - the pair's / line's location. */
  location?: string;
  field: string;
  excel: string | number | boolean | null;
  pull: string | number | boolean | null;
}

/** Delivery orders compare with the two macro files the checker uses today (owner decision
 *  30 Sep): `lines` = Order Listing, sheet Master (one row per DO line); `headers` = Order
 *  Tracking, sheet Master (one row per DO). */
export type AutocountPullCompareSource = 'lines' | 'headers';

/** `POST /api/v1/autocount/pulls/{job_id}/compare` response. `summary` is the STORED
 *  compare summary (same shape `GET /{job_id}` returns as `compare`); `only_in_excel` /
 *  `only_in_pull` here are the item-code LISTS the comparison just computed - distinct
 *  from `summary.only_in_excel` / `summary.only_in_pull`, which are counts. Delivery
 *  orders add `source`, that file's own `source_summary`, the pulled DocDate `window` the
 *  rows were cut to, and how many rows sat outside it. */
export interface AutocountComparePullResult {
  summary: AutocountPullCompareSummary;
  differences: AutocountCompareDifference[];
  only_in_excel: string[];
  only_in_pull: string[];
  source?: AutocountPullCompareSource | null;
  source_summary?: AutocountPullCompareSummary | null;
  confirm_blocked_reason?: string | null;
  window?: { fromDay: string | null; toDay: string | null } | null;
  ignored_outside_window?: number;
  rows_in_window?: number;
}

/** The apply job's own `import_jobs.status` (backend `JobStatus`). */
export type AutocountApplyStatus = 'pending' | 'queued' | 'started' | 'finished' | 'failed' | 'cancelled';

/** `GET /api/v1/autocount/pulls/{job_id}` response - also what `POST /` and `/current` return. */
export interface AutocountPull {
  job_id: string;
  entity: AutocountPullEntity;
  company_code: string;
  /** Delivery orders only: what the snapshot was asked to cover; `null` = the default. */
  scope?: AutocountPullScope | null;
  phase: AutocountPullPhase;
  progress?: AutocountPullProgress | null;
  preview_progress?: AutocountPullPreviewProgress | null;
  /** The FoundryX ready header (camelCase), once the snapshot has been read; `null` before. */
  header?: AutocountPullHeader | null;
  counts?: AutocountPullCounts | null;
  /** Set only when Confirm is blocked (e.g. stock AC-SP-1, or a delivery-orders pull under
   *  the "compare must match" switch); Confirm stays enabled otherwise. */
  confirm_blocked_reason?: string | null;
  /** Delivery orders, owner Q4: true while the switch holds Confirm until both files
   *  compare clean (the reason above says so); false = the compare is advisory. */
  confirm_requires_match?: boolean;
  compare?: AutocountPullCompareSummary | null;
  /** Delivery orders: each file's own last summary; `compare` above is the two added up. */
  compare_sources?: Partial<Record<AutocountPullCompareSource, AutocountPullCompareSummary>> | null;
  /** Delivery orders: the DocDate window the compare cuts the files to (the scope, else the
   *  snapshot's default 31 days); `null` on the other entities. */
  window?: { fromDay: string | null; toDay: string | null } | null;
  /** Set once Confirm has been clicked - the apply job the page links to. */
  apply_job_id?: string | null;
  /** The apply job's own status (fix round 3, item 2); `null` while there is no apply job
   *  yet. `usePull` keeps polling past `phase === 'confirmed'` while this is not yet
   *  `finished`/`failed` - the apply task's own `stock_list_not_archived` warning lands on
   *  the pull's metadata only once the apply task actually runs. */
  apply_status?: AutocountApplyStatus | null;
  /** String warning codes (e.g. `content_hash_mismatch`) - never a refusal, absent or empty
   *  means a clean match. */
  warnings?: string[];
  /** Set on `failed` / `expired`. */
  error?: string | null;
}

/** The Excel-view row shape for `products` - same columns, same order as the manual template. */
export interface ProductExcelRow {
  item_code: string;
  description: string;
  desc_2: string;
  item_group: string;
  item_brand: string;
  price: number;
  is_active: boolean;
}

/** The Excel-view row shape for `stock_balances`. FED rows only. */
export interface StockExcelRow {
  item_code: string;
  item_description: string;
  location: string;
  on_hand_qty: number;
}

/** The Excel-view row shape for `delivery_orders`: one row per DO LINE, in the shape of the
 *  "Import delivery order lines" sheet (AC-DP-30). */
export interface DeliveryOrderExcelRow {
  doc_no: string;
  doc_date: string | null;
  debtor_code: string | null;
  debtor_name: string | null;
  item_code: string;
  description: string | null;
  location: string | null;
  qty: number | null;
  uom: string | null;
  unit_price: number | null;
  sub_total: number | null;
}

/** The Excel-view row shape for `goods_receive_notes`: one row per GRN LINE, in the shape of
 *  the "DETAIL LISTING" sheet (AC-GP-50); `from_doc_no` is the sheet's "Our PO No.". */
export interface GoodsReceiveNoteExcelRow {
  doc_no: string;
  doc_date: string | null;
  creditor_code: string | null;
  creditor_name: string | null;
  item_code: string;
  description: string | null;
  location: string | null;
  qty: number | null;
  uom: string | null;
  from_doc_no: string | null;
}

export type AutocountPullExcelRow =
  | ProductExcelRow
  | StockExcelRow
  | DeliveryOrderExcelRow
  | GoodsReceiveNoteExcelRow;

export interface AutocountPullRowsQuery {
  pageIndex: number;
  pageSize: number;
  query?: string;
}

// ---- DO compare mapping (DO-COMPARE-SIM) ------------------------------------------------

export type CompareMappingKind =
  | 'order_listing'
  | 'order_tracking'
  | 'grn_detail_listing'
  | 'grn_listing';

export interface CompareMappingColumn {
  excel_header: string;
  transform: string;
  field: string;
}

export interface CompareMappingBody {
  sheet_name: string;
  columns: CompareMappingColumn[];
}

export interface CompareMapping extends CompareMappingBody {
  kind: CompareMappingKind;
}

export interface CompareMappingsResponse {
  items: CompareMapping[];
}
