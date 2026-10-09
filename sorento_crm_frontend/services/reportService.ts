/* -------------------------------------------------------------------------------------
 * reportService - PLAN-reporting-foundation.
 *
 * One service for EVERY report. The report key is a parameter, never a code path: the
 * sponsorship report is report #1 and report #2 must reach this file unchanged.
 *
 * ===================================================================================
 * API CONTRACT (live since S3; the S1 mock is deleted)
 * ===================================================================================
 *
 * Every route is gated on the report's own permission slug (sponsorship:
 * `procurement.sponsorship_forms.report`; the sales reports: `sales.reports.view`) AND
 * on the module the definition names (403 when that module is disabled). Publishing a
 * view additionally needs `reports.views.publish`. A company-scoped report (the sales
 * reports) reads the one company its Company filter names, inside the caller's grant.
 *
 *   GET  /api/v1/reports
 *        -> { "reports": [{ "key", "title", "permission" }] }   catalog, permission-filtered
 *
 *   GET  /api/v1/reports/{key}
 *        -> ReportMeta (below). 403 without the permission, 404 for an unknown key.
 *
 *   POST /api/v1/reports/{key}/run
 *        body { "params": ReportParamValues, "view": ReportViewConfig | null }
 *        -> ReportResult (below)
 *        `view` is the view as it stands on screen, saved or not, so an unsaved change
 *        runs without being saved first. null = the report default view.
 *        `view.detail.columns` empty means THE WHOLE CATALOG. The screen always asks for
 *        the whole catalog and hides client-side, so ticking a column is instant and a
 *        hidden column is still offerable in the Columns panel (AC-B7). The export asks
 *        for exactly the visible columns in the visible order (AC-C5), which is where
 *        "requested columns in the requested order" (AC-A5) is exercised.
 *        422 { "message": "...", "code": "REPORT_CAPPED", "capped": true } when the sync
 *            caps (5,000 detail rows / 5,000 pivot cells) are exceeded. The export path
 *            has no cap, so the message tells the user to export instead. The envelope is
 *            FLAT because that is what the backend's AppException handler serialises
 *            (app/main.py) - `extractApiError` already reads `message`.
 *        422 { "message": "Unknown param 'foo'", "code": "REPORT_INVALID_PARAMS" } for an
 *            unknown param, an unknown date basis or an unknown column.
 *
 *   POST /api/v1/reports/{key}/export
 *        body { "params": ReportParamValues, "view": ReportViewConfig | null }
 *        -> { "download_id": "<uuid>", "filename": "Sponsorship report-2026.xlsx" }
 *        Creates a `downloads` row (kind `report_xlsx`) and enqueues the render on the
 *        `imports` queue. The file surfaces through the existing My Downloads drawer,
 *        so there is no new download plumbing.
 *
 *   GET    /api/v1/reports/{key}/views          -> { "mine": ReportView[], "shared": ReportView[] }
 *   POST   /api/v1/reports/{key}/views          body { "name", "view": ReportViewConfig } -> ReportView
 *   DELETE /api/v1/reports/{key}/views/{id}     -> 204
 *   POST   /api/v1/reports/{key}/views/{id}/publish     body { "is_shared": bool }  -> ReportView
 *   POST   /api/v1/reports/{key}/views/{id}/set-default                             -> ReportView
 *
 *   `mine` = the views the caller OWNS, published ones included (the menu badges those);
 *   `shared` = OTHER users' published views. A personal view is visible to its owner only;
 *   a published one to anyone holding the report permission. At most one `is_default` per
 *   report key, enforced by a partial unique index.
 *
 * --- Money -------------------------------------------------------------------------
 *
 * Every measure travels as a DECIMAL STRING with two places ("1166830.70"), or is
 * ABSENT when the report has no value for it. Absent is not zero: a sponsorship form
 * with no lines has no sample price, and the workbook prints "-" there. The frontend
 * never adds two numbers together - every total in this contract is computed by the
 * engine, so the screen and the exported workbook cannot disagree.
 *
 * --- Deviation from the review artifact ---------------------------------------------
 *
 * `.lavish/reporting-foundation.html` shows `col_dim.values` as bare strings
 * ("2026-01"). The screen needs "Jan'26" in the header and must not invent a
 * formatting rule per dimension, so the contract carries an OPTIONAL
 * `col_dim.value_labels` map supplied by the engine; the frontend falls back to the
 * raw value when it is absent. Recorded in PLAN-reporting-foundation.md.
 *
 * ----------------------------------------------------------------------------------- */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/** A column as the dataset catalog describes it, and as a layout returns it. */
export type ReportColumnType = 'text' | 'money' | 'integer' | 'date' | 'bool';
export type ReportColumnTag = 'dimension' | 'measure' | 'date' | 'text';

export interface ReportColumn {
  key: string;
  label: string;
  type: ReportColumnType;
  /** Suggested DataGrid width; the user's saved sizing wins once they resize it. */
  size?: number;
}

export interface ReportCatalogColumn extends ReportColumn {
  tag: ReportColumnTag;
  /** Values of the report's dataset param whose dataset lacks this column. */
  unavailable_for?: string[];
}

/**
 * A merged header cell over a run of detail columns (Expected year of delivery).
 *
 * `source` is the catalog dimension the group was derived from. A saved view names the
 * SOURCE among its detail columns, because the member keys are data-dependent (one tick
 * column per year present in the result) and a view written in 2026 must still mean
 * "show the delivery years" in 2027.
 */
export interface ReportColumnGroup {
  label: string;
  source: string;
  keys: string[];
}

export type ReportRow = Record<string, string | number | boolean | null>;

export interface ReportDetailLayout {
  key: string;
  title: string;
  columns: ReportColumn[];
  column_groups: ReportColumnGroup[];
  rows: ReportRow[];
  /** measure key -> decimal string. A measure with nothing to total is absent. */
  totals: Record<string, string>;
  /** True when the run had more lines than the sync cap and the report truncates rather
   *  than refuses: `rows` are the newest ones, `totals` are still the whole set's. */
  truncated?: boolean;
}

export interface ReportPivotDimension {
  key: string;
  label: string;
}

export interface ReportPivotColumnDimension extends ReportPivotDimension {
  /** Ordered naturally (months chronological). */
  values: string[];
  /** Optional display label per value; falls back to the raw value. */
  value_labels?: Record<string, string>;
}

/** row value -> column value -> measure key -> decimal string. Sparse: a missing cell is absent. */
export type ReportPivotCells = Record<string, Record<string, Record<string, string>>>;

export interface ReportPivotLayout {
  key: string;
  title: string;
  row_dim: ReportPivotDimension;
  col_dim: ReportPivotColumnDimension;
  measures: ReportColumn[];
  /** Ordered row values, so the screen does not have to sort a dimension it cannot rank. */
  row_values: string[];
  cells: ReportPivotCells;
  row_totals: Record<string, Record<string, string>>;
  col_totals: Record<string, Record<string, string>>;
  grand_total: Record<string, string>;
  /** The VARIANCE row (the last row minus the one before, per column), sparse like
   *  `cells`; absent when the report declares none. Computed by the engine. */
  variance_row?: Record<string, Record<string, string>> | null;
  variance_total?: Record<string, string> | null;
  variance_label?: string | null;
  /** Display label per ROW value when the row axis names its values ('01' -> 'JAN'). */
  row_value_labels?: Record<string, string> | null;
  /** 'line': the screen draws this pivot as a line chart under the table. */
  chart?: 'line' | null;
  /** The screen prints whole ringgit (the workbook keeps the sen). */
  whole_units?: boolean;
  /** False when the column-totals row means nothing (years added to years). */
  show_column_totals?: boolean;
}

/** One block of a split summary: the pivot for one value of the report's split filter
 *  (the Yearly comparison's DEALER and PROJECT TEAM). */
export interface ReportBlock {
  key: string;
  title: string;
  summary: ReportPivotLayout;
}

/**
 * A run that came back. A run that did NOT is the 422 with `capped: true` on it
 * (`ReportCappedError`), so there is no flag here: a result in hand was never capped.
 */
export interface ReportResult {
  key: string;
  period_label: string;
  row_count: number;
  layouts: {
    detail: ReportDetailLayout;
    summary: ReportPivotLayout;
    /** One per chosen value of the report's split filter; absent when it does not split. */
    blocks?: ReportBlock[] | null;
  };
  /** A line printed under the filter bar (the basis of a sales report). */
  note?: string | null;
}

export type ReportPeriod =
  | { kind: 'year'; year: number }
  | { kind: 'month_range'; year: number; from_month: number; to_month: number }
  | { kind: 'custom'; from: string; to: string };

export type ReportParamValue = string | string[] | ReportPeriod;
export type ReportParamValues = Record<string, ReportParamValue>;

export interface ReportSelectOption {
  value: string;
  label: string;
}

export type ReportParamMeta =
  | {
      kind: 'date_basis';
      key: string;
      label: string;
      default: string;
      options: ReportSelectOption[];
    }
  | {
      kind: 'period';
      key: string;
      label: string;
      default: ReportPeriod;
      /** Years the dataset actually holds rows for, newest first. */
      years: number[];
    }
  | {
      kind: 'select';
      key: string;
      label: string;
      multi: boolean;
      clearable: boolean;
      default: string[];
      options: ReportSelectOption[];
    };

export interface ReportViewConfig {
  params: ReportParamValues;
  detail: { columns: string[]; order: string[] };
  pivot: { rows: string; cols: string; measures: string[] };
}

export interface ReportView {
  id: string;
  name: string;
  is_shared: boolean;
  is_default: boolean;
  /** Display name of the owner, never the user id (no UUID reaches the UI). */
  owner_name: string | null;
  view: ReportViewConfig;
}

export interface ReportViews {
  mine: ReportView[];
  shared: ReportView[];
}

export interface ReportMeta {
  key: string;
  title: string;
  permission: string;
  params: ReportParamMeta[];
  catalog: ReportCatalogColumn[];
  default_view: ReportViewConfig;
  /** True when the caller holds `reports.views.publish`. Publish + Set as default are
   *  ABSENT without it, never disabled. */
  can_publish: boolean;
  /** The tab the screen opens on. */
  opens_on?: 'detail' | 'summary';
  /** The select param that picks the row set; null/absent when the report reads one. */
  dataset_param?: string | null;
}

export interface ReportExportResult {
  download_id: string;
  filename: string;
}

/**
 * The sync caps are a real answer, not a failure: the run is refused and the user is
 * pointed at the uncapped export. Thrown so a hook can tell it apart from a 500.
 */
export class ReportCappedError extends Error {
  readonly capped = true;
  constructor(message: string) {
    super(message);
    this.name = 'ReportCappedError';
  }
}

/** react-query keys, declared here so the hooks and any invalidator agree. */
export const REPORT_META_KEY = 'report-meta';
export const REPORT_RUN_KEY = 'report-run';
export const REPORT_VIEWS_KEY = 'report-views';

/**
 * The listing key the detail DataGrid persists show / hide / order under (AC-C1).
 * Permission-slug scoped like every other listing key, e.g.
 * `procurement.sponsorship_forms.report::detail`.
 */
export function reportLayoutListingKey(permission: string, layoutKey: string): string {
  return `${permission}::${layoutKey}`;
}

const base = (key: string) => `/api/v1/reports/${encodeURIComponent(key)}`;

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

async function failed(response: Response, fallback: string): Promise<Error> {
  return new Error(await extractApiError(response, fallback));
}

export async function fetchReportMeta(key: string): Promise<ReportMeta> {
  const response = await apiFetch(base(key));
  if (!response.ok) throw await failed(response, 'Failed to load the report');
  return response.json();
}

export async function runReport(
  key: string,
  params: ReportParamValues,
  view: ReportViewConfig,
): Promise<ReportResult> {
  const response = await apiFetch(`${base(key)}/run`, jsonInit('POST', { params, view }));
  if (!response.ok) {
    // The capped 422 is an answer, not a failure, and it is FLAT: the backend's
    // AppException handler serialises `exc.detail` as the whole body, so `capped` sits
    // beside `message`. The MESSAGE still comes from extractApiError; the clone is read
    // only for that one flag, because extractApiError consumes the body.
    let body: { capped?: boolean; code?: string } | null = null;
    try {
      body = await response.clone().json();
    } catch {
      body = null;
    }
    const message = await extractApiError(response, 'Failed to run the report');
    if (body?.capped || body?.code === 'REPORT_CAPPED') throw new ReportCappedError(message);
    throw new Error(message);
  }
  return response.json();
}

export async function fetchReportViews(key: string): Promise<ReportViews> {
  const response = await apiFetch(`${base(key)}/views`);
  if (!response.ok) throw await failed(response, 'Failed to load the saved views');
  return response.json();
}

export async function createReportView(
  key: string,
  body: { name: string; view: ReportViewConfig },
): Promise<ReportView> {
  const response = await apiFetch(`${base(key)}/views`, jsonInit('POST', body));
  if (!response.ok) throw await failed(response, 'Failed to save the view');
  return response.json();
}

export async function deleteReportView(key: string, id: string): Promise<void> {
  const response = await apiFetch(`${base(key)}/views/${id}`, jsonInit('DELETE'));
  if (!response.ok) throw await failed(response, 'Failed to delete the view');
}

export async function publishReportView(
  key: string,
  id: string,
  isShared: boolean,
): Promise<ReportView> {
  const response = await apiFetch(
    `${base(key)}/views/${id}/publish`,
    jsonInit('POST', { is_shared: isShared }),
  );
  if (!response.ok) throw await failed(response, 'Failed to publish the view');
  return response.json();
}

export async function setDefaultReportView(key: string, id: string): Promise<ReportView> {
  const response = await apiFetch(`${base(key)}/views/${id}/set-default`, jsonInit('POST'));
  if (!response.ok) throw await failed(response, 'Failed to set the default view');
  return response.json();
}

export async function exportReport(
  key: string,
  params: ReportParamValues,
  view: ReportViewConfig,
): Promise<ReportExportResult> {
  const response = await apiFetch(`${base(key)}/export`, jsonInit('POST', { params, view }));
  if (!response.ok) throw await failed(response, 'Failed to queue the export');
  return response.json();
}
