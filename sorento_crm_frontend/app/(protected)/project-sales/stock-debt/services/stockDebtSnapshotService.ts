/**
 * Stock Debt snapshots: the board frozen at a moment, with a decision per product.
 * UI -> hooks (`useStockDebtSnapshots`) -> THIS service -> lib/api-client -> backend.
 *
 * All routes sit under `/api/v1/project-sales/stock-debt/snapshots` and need
 * `projects.stock_debt.view` (UAC AC-1 to AC-18). There is no delete function: Delete is the
 * deferred action `stock_debt_snapshot.delete` (AC-15), not a service call.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { MyDownload } from '@/services/myDownloadsService';
import { stockDebtFilterBody } from './stockDebtService';
import type { StockDebtExportParams } from './stockDebtService';
import type { StockDebtExportSplit } from '../types/stockDebt.types';
import type {
  Snapshot,
  SnapshotDecisionFilter,
  SnapshotDecisionInput,
  SnapshotLinesResponse,
  SnapshotListResponse,
  SnapshotRow,
  SnapshotRowsResponse,
} from '../types/stockDebtSnapshot.types';

const BASE = '/api/v1/project-sales/stock-debt/snapshots';

export interface SnapshotListParams {
  pageIndex: number;
  pageSize: number;
  /** Matches the snapshot name. */
  query: string;
}

export interface SnapshotRowsParams extends SnapshotListParams {
  decision: SnapshotDecisionFilter;
}

/** Save the board as it is now. `filters` is the object Export sends, minus `split`. */
export async function createSnapshot(input: {
  name: string;
  filters: Omit<StockDebtExportParams, 'split'>;
}): Promise<Snapshot> {
  const res = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: input.name, filters: stockDebtFilterBody(input.filters) }),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to save the snapshot'));
  return (await res.json()) as Snapshot;
}

export async function listSnapshots(params: SnapshotListParams): Promise<SnapshotListResponse> {
  const search = buildDataGridParams({
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    searchQuery: params.query,
  });
  const res = await apiFetch(`${BASE}?${search}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load snapshots'));
  return (await res.json()) as SnapshotListResponse;
}

export async function getSnapshot(id: string): Promise<Snapshot> {
  const res = await apiFetch(`${BASE}/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load the snapshot'));
  return (await res.json()) as Snapshot;
}

export async function listSnapshotRows(
  id: string,
  params: SnapshotRowsParams,
): Promise<SnapshotRowsResponse> {
  const search = buildDataGridParams(
    { pageIndex: params.pageIndex, pageSize: params.pageSize, searchQuery: params.query },
    { decision: params.decision },
  );
  const res = await apiFetch(`${BASE}/${encodeURIComponent(id)}/rows?${search}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load the snapshot rows'));
  return (await res.json()) as SnapshotRowsResponse;
}

/** The stored lines of one row: one bucket when `month` is given, every bucket otherwise. */
export async function getSnapshotRowLines(
  id: string,
  rowId: string,
  month?: string,
): Promise<SnapshotLinesResponse> {
  const search = new URLSearchParams();
  if (month) search.set('month', month);
  const query = search.toString();
  const res = await apiFetch(
    `${BASE}/${encodeURIComponent(id)}/rows/${encodeURIComponent(rowId)}/lines${query ? `?${query}` : ''}`,
  );
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load the snapshot lines'));
  const body = (await res.json()) as { data?: SnapshotLinesResponse['lines'] };
  return { lines: body.data ?? [] };
}

export async function setSnapshotDecision(
  id: string,
  rowId: string,
  input: SnapshotDecisionInput,
): Promise<SnapshotRow> {
  const res = await apiFetch(
    `${BASE}/${encodeURIComponent(id)}/rows/${encodeURIComponent(rowId)}/decision`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to save the decision'));
  return (await res.json()) as SnapshotRow;
}

/** Queues the stored snapshot's workbook through My Downloads. */
export async function exportSnapshot(
  id: string,
  input: { split: StockDebtExportSplit; show_aux: boolean },
): Promise<MyDownload> {
  const res = await apiFetch(`${BASE}/${encodeURIComponent(id)}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to start the snapshot export'));
  return (await res.json()) as MyDownload;
}
