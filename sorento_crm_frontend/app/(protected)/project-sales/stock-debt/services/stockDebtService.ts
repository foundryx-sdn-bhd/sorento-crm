/**
 * ============================================================================
 * Stock Debt - feature service (S2, AC-S2-6 / AC-S2-7; extended 24 Sep 2026,
 * PLAN-stock-debt-filters-totals-export-24sep.md, Phase 2)
 * ============================================================================
 * Layering: UI -> hooks (`useStockDebtQuery`) -> THIS service -> lib/api-client
 * -> backend.
 *
 * ── PHASE 2 (24 Sep 2026 lane) ───────────────────────────────────────────────
 * All three functions call the real backend below - `USE_STOCK_DEBT_FILTER_MOCKS`
 * stays declared and `false` rather than deleted outright, matching
 * `reorderRunService.ts`'s own `USE_M4_MOCKS` convention: a later regression that
 * flips it back to `true` is a one-line, reviewable diff rather than a
 * from-scratch mock rewrite.
 *
 * The export popover's "212 rows, 14 sheets" preview (AC-33) reads
 * `pagination.total` for rows and the envelope's `sheet_counts` (AC-7b) for
 * sheets - both real, whole-filtered-set fields from the backend below, so
 * `previewStockDebtExport()` needed no change at all once Phase 2 landed.
 *
 * ── PHASE-2 BACKEND CONTRACT ────────────────────────────────────────────────
 * All three routes live under the `projects` domain router and all require
 * `projects.stock_debt.view` (AC-S2-8, R22; permission + grant sweep shipped in
 * migration 443 with S1).
 *
 * 1) The month x product board (AC-S2-6, extended AC-1 to AC-9; R14/R15/R16 owner round)
 *
 *      GET /api/v1/project-sales/stock-debt
 *          ?page=<1-based>      standard `buildDataGridParams` paging
 *          &limit=<n>
 *          &query=<text>        product code or name
 *          &only_debt=<bool>    drop rows with no negative month (default true on
 *                               the screen)
 *          &date_from=<YYYY-MM-DD>  drop demand due before this date; the axis starts
 *                               at `max(current month, this date's month)` (R14, AC-1b).
 *                               Replaces `cutoff`, not an alias.
 *          &date_to=<YYYY-MM-DD>    drop demand due after this date; ends the axis at
 *                               its month (R14, A2, AC-1/AC-2/AC-3). Replaces `cutoff`.
 *          &supplier_ids=<uuid>|none  REPEATABLE (R15, AC-4): keep only products whose
 *                               LAST supplier (newest PO line, else the primary flag) is
 *                               ANY of the values passed; `none` is one more value among
 *                               the others. Replaces `supplier_id`, not an alias.
 *          &book=<all|project|retail>  `all` (default) = flagged project bins
 *                               PLUS the site pools, in one span; `project` is
 *                               today's view; `retail` is pools only (R1, AC-8)
 *
 *      No `group` param - the Ownership group filter left the screen entirely (R16). The
 *      backend still accepts one; this service just never sends it.
 *
 *      -> 200 {
 *           data: [{
 *             product_id, product_code, product_name,
 *             months: [{ key: 'YYYY-MM', balance, tone: 'red'|'amber'|'green' }],
 *             tba, undated, unlocated,
 *             supplier_id, supplier_name, category_code, total
 *           }],
 *           pagination: { total, page, limit },
 *           months:    ['YYYY-MM', ...],   the column axis
 *           tba_month: 'YYYY-MM',          the policy's `tba_date_from`, by month
 *           groups:    ['BB', ...]         what the flag admits, for the select
 *           totals:    { months: { 'YYYY-MM': number, ... }, tba, undated,
 *                        unlocated, total }   over the WHOLE filtered set (AC-6)
 *           suppliers: [{ id, name }]      distinct last suppliers of the
 *                                          filtered set, sorted by name (AC-7)
 *           sheet_counts: { supplier, category, supplier_category }
 *                                          exact export sheet counts for the current
 *                                          filtered set, none-buckets included (AC-7b)
 *         }
 *
 *      `data[].months` carries one entry per axis key, in axis order. A month states its
 *      OWN month (R37, 30 Aug 2026): the supply dated in it that is still free once the
 *      assignment walk is over, less what the lines due in it went short of on their own
 *      dates. Nothing carries, so a month with nothing due and nothing arriving reads 0.
 *      Rows are sorted by EARLIEST RED MONTH, then product code; a row with no red
 *      month sorts after every row that has one.
 *      `tba`, `undated` and `unlocated` are plain signed totals - the demand dated on
 *      or after `tba_date_from`, the demand with no date, and the demand booked at no
 *      warehouse at all. None of the three draws supply (R14), so they carry no tone and
 *      the screen renders them as informational.
 *      `total` (AC-5, R17) sums every month's balance plus `tba` ONLY - `undated` and
 *      `unlocated` are NOT folded in any more (the "No date"/"No location" columns left
 *      the screen and the workbook both); the row still carries `undated`/`unlocated`
 *      themselves, unchanged.
 *      `product_name` is `null` when it equals `product_code`, case-sensitive and
 *      trimmed, so the board and the export agree without each re-deriving it (AC-9).
 *
 *      The axis fields, `totals`, `suppliers` and `sheet_counts` are envelope-level and
 *      NOT per row, because each is a property of the whole filtered set: derived per
 *      page, the columns (or the footer, or the select, or the export preview) would
 *      change under the reader as they page (AC-7b: page 2 states the same
 *      `sheet_counts` as page 1).
 *
 * 2) The cell drill (AC-S2-7, R28; extended AC-11; R14 owner round)
 *
 *      GET /api/v1/project-sales/stock-debt/{product_id}/cell
 *          ?month=<YYYY-MM | tba | undated | unlocated>
 *          &date_from=<YYYY-MM-DD>  the board's own `date_from`, echoed so the drill
 *                               foots with the cell that opened it. Replaces `cutoff`.
 *          &date_to=<YYYY-MM-DD>    the board's own `date_to`, same reason.
 *          &book=<all|project|retail>  the board's own book, same reason.
 *
 *      No `group` - the toolbar no longer has an Ownership group control to echo (R16);
 *      the backend param itself is untouched.
 *
 *      -> 200 {
 *           demand: [{ so_number, agent_code, warehouse_code, required_date, open_qty,
 *                      assigned_qty, assigned_source, short_qty,
 *                      status: 'covered'|'late'|'short'|'pinned' }],
 *           supply: [{ kind: 'on_hand'|'spo'|'po', ref, warehouse_code, date,
 *                      bought_for, qty, free_qty, overdue,
 *                      assigned_to: [{ so_number, qty }] }]
 *         }
 *
 *      `demand` = the lines whose required date falls in that month, or every TBA /
 *      undated line for those two keys. `supply` = the events dated in that month:
 *      on hand by bin for the current month, an SPO at its arrival, a PO line at its
 *      Delivery date (`expected_date`, R42), else `issue + lead` (R29). A PO row and a
 *      PO entry in a demand line's `assigned_from` carry `po_number`, `po_line_number`,
 *      `po_id` and `po_line_id` (R42). An event whose arrival has passed past the
 *      overdue policy's dead days is listed with `overdue: true` and counted as nothing
 *      (R31 as R-O leaves it), so its `free_qty` is 0.
 *
 *      The drill FOOTS with the cell that opened it (R37): `sum(free_qty)` less
 *      `sum(short_qty)` over these rows IS that month's balance, which is what the two
 *      tab footers print. `short_qty` is what a line went short of on its own date, so a
 *      `late` line ends covered and still carries one.
 *
 * 3) Export (AC-12 to AC-18; R14/R15/R16 owner round)
 *
 *      POST /api/v1/project-sales/stock-debt/export
 *          { query?, only_debt?, date_from?, date_to?, supplier_ids?, book?, split }
 *          (never `group`, `cutoff` or `supplier_id` - all three retired, not aliased)
 *
 *      -> 201 MyDownload (`status: 'pending'`, `kind: 'stock_debt_xlsx'`) - a
 *         `user_downloads` row; `generate_stock_debt_xlsx` runs on the `imports`
 *         queue and the workbook is fetched later from My Downloads, once the
 *         worker marks the row ready. Same pipeline as the low stock report
 *         (`exportLowStockReport` in `scm/low-stock-report/services/
 *         lowStockReportService.ts`), a different route because this screen has
 *         its own filters and its own `split`, not a
 *         `run_id` off a reorder run.
 *      -> 422 above `MAX_LOW_STOCK_ROWS` rows, same reason and same cap as the
 *         low stock report; nothing is written.
 *
 * The shapes are typed field for field in `types/stockDebt.types.ts`; they are not
 * restated here, so the two cannot drift.
 *
 * ── PERMISSION ──────────────────────────────────────────────────────────────
 * `projects.stock_debt.view` PRESUMES `projects.projects.view`. The page sits under
 * `app/(protected)/project-sales/layout.tsx`, which gates every child of the section
 * on `projects.projects.view`, and `RequireAccess` takes ONE permission - it has no
 * any-of mode, and building one for a single caller is machinery nobody has asked for
 * yet. Migration 443's sweep grants `stock_debt.view` to exactly the roles that hold
 * `projects.view`, so the presumption holds for every role that can reach the entry;
 * recorded in the UAC as AC-S2-8. The trigger for an any-of gate is the first role that
 * needs Stock Debt WITHOUT the section it lives in.
 *
 * ── ERROR SHAPE ─────────────────────────────────────────────────────────────
 * The standard `AppException` envelope the global handler in `app/main.py`
 * serialises, read with `extractApiError(res, fallback)` and surfaced as an `Error`
 * message - which is what the page's error state renders beside its Retry.
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { MyDownload } from '@/services/myDownloadsService';
import type {
  StockDebtBook,
  StockDebtCell,
  StockDebtExportPreview,
  StockDebtExportSplit,
  StockDebtListResponse,
} from '../types/stockDebt.types';

/** Phase 1 -> Phase 2 switch (see the header). `false` since the backend in AC-1 to
 *  AC-18 shipped; kept declared, not deleted, so a regression is a one-line diff. */
export const USE_STOCK_DEBT_FILTER_MOCKS = false;

/**
 * What the board asks for: a page, a needle, the book, the suppliers, a due date range
 * and the debt-only switch.
 *
 * Owner's hand-test round (R14-R16): the single `cutoff` became a `dateFrom`/`dateTo`
 * range, the single `supplierId` became a repeatable `supplierIds`, and the Ownership
 * group filter left the screen entirely - there is no `group` field here any more, and
 * this service never sends one (the backend's own `group` param and its tests stay,
 * untouched and simply FE-unreachable).
 */
export interface StockDebtListParams {
  pageIndex: number;
  pageSize: number;
  query: string;
  onlyDebt: boolean;
  /** `all` (default) / `project` / `retail` (R1, AC-8). */
  book: StockDebtBook;
  /** Repeatable; `'none'` is one more value among the others, not a sentinel that
   *  excludes them (R15, AC-4). Empty = every supplier. */
  supplierIds: string[];
  /** `YYYY-MM-DD`, or '' for no lower bound (R14, AC-1b). */
  dateFrom: string;
  /** `YYYY-MM-DD`, or '' for no upper bound (R14, AC-1). */
  dateTo: string;
  /** STOCK-DEBT-PROJECTION: month cells read the running projected balance. */
  projected?: boolean;
}

/** The list params minus paging, plus the workbook split - what `exportStockDebt` sends. */
export interface StockDebtExportParams {
  query: string;
  onlyDebt: boolean;
  book: StockDebtBook;
  supplierIds: string[];
  dateFrom: string;
  dateTo: string;
  /** Whether the workbook carries the four stock and PO columns (default off on the wire). */
  showAux: boolean;
  /** STOCK-DEBT-PROJECTION: the workbook follows the "Projected balance" switch. */
  projected?: boolean;
  split: StockDebtExportSplit;
}

/** The month x product board (AC-S2-6, AC-1 to AC-9). */
export async function getStockDebtList(
  params: StockDebtListParams,
): Promise<StockDebtListResponse> {
  const search = buildDataGridParams(
    {
      pageIndex: params.pageIndex,
      pageSize: params.pageSize,
      searchQuery: params.query,
    },
    {
      only_debt: params.onlyDebt,
      book: params.book === 'all' ? '' : params.book,
      date_from: params.dateFrom || '',
      date_to: params.dateTo || '',
      projected: params.projected ? 'true' : '',
    },
  );
  // Repeatable, so `buildDataGridParams`'s scalar `extra` cannot carry it - appended
  // directly (R15).
  for (const id of params.supplierIds ?? []) {
    if (id) search.append('supplier_ids', id);
  }
  const res = await apiFetch(`/api/v1/project-sales/stock-debt?${search}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load stock debt'));
  return (await res.json()) as StockDebtListResponse;
}

/**
 * The demand and supply behind one cell (AC-S2-7, extended AC-11). `month` is `YYYY-MM`,
 * `tba`, `undated` or `unlocated`; `dateFrom`/`dateTo` (R14) and `book` are the board's
 * own narrowing, passed through so the drill is recomputed over the same span the cell
 * was. Never sends `group` or `cutoff` (R14/R16 - both retired, not aliased).
 */
export async function getStockDebtCell(
  productId: string,
  month: string,
  dateFrom?: string,
  dateTo?: string,
  book?: StockDebtBook,
  projected?: boolean,
): Promise<StockDebtCell> {
  const search = new URLSearchParams({ month });
  if (dateFrom) search.set('date_from', dateFrom);
  if (dateTo) search.set('date_to', dateTo);
  if (book && book !== 'all') search.set('book', book);
  if (projected) search.set('projected', 'true');
  const res = await apiFetch(
    `/api/v1/project-sales/stock-debt/${encodeURIComponent(productId)}/cell?${search}`,
  );
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load the cell'));
  return (await res.json()) as StockDebtCell;
}

/**
 * Starts the workbook export through My Downloads (R10/R12, AC-12 to AC-18, AC-33/AC-34).
 * Returns a `MyDownload` row shaped exactly like the low stock report's
 * (`exportLowStockReport` in `scm/low-stock-report/services/lowStockReportService.ts`) so the same drawer / toast plumbing
 * serves both.
 */
export async function exportStockDebt(params: StockDebtExportParams): Promise<MyDownload> {
  const res = await apiFetch('/api/v1/project-sales/stock-debt/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query || undefined,
      only_debt: params.onlyDebt,
      book: params.book,
      supplier_ids: params.supplierIds ?? [],
      date_from: params.dateFrom || undefined,
      date_to: params.dateTo || undefined,
      split: params.split,
      show_aux: params.showAux,
      // Only while the switch is on: the month view's body is what it was before.
      ...(params.projected ? { projected: true } : {}),
    }),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to start the stock debt export'));
  return (await res.json()) as MyDownload;
}

/**
 * The export popover's preview (AC-33) - never a network call of its own: both fields are
 * already whole-filtered-set, envelope-level values (`pagination.total`, AC-6; `sheet_counts`,
 * AC-7b), so reading them here is exact, not a guess - `split=none` is the only case not
 * carried on the envelope, because it is always exactly one sheet.
 */
export function previewStockDebtExport(
  envelope: StockDebtListResponse | undefined,
  split: StockDebtExportSplit,
): StockDebtExportPreview {
  const rows = envelope?.pagination.total ?? 0;
  if (!envelope || rows === 0) return { rows: 0, sheets: 0 };

  switch (split) {
    case 'none':
      return { rows, sheets: 1 };
    case 'supplier':
      return { rows, sheets: envelope.sheet_counts.supplier };
    case 'category':
      return { rows, sheets: envelope.sheet_counts.category };
    case 'supplier_category':
      return { rows, sheets: envelope.sheet_counts.supplier_category };
    default:
      return { rows, sheets: 1 };
  }
}
