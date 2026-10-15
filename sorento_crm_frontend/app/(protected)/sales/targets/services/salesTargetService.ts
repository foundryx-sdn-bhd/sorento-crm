/**
 * Sales targets - feature service (plan 3.1, 3.8, 16.3).
 *
 * Layering: components -> hooks (useSalesTargets) -> THIS service -> lib/api.
 *
 * Backend contract (module `sales`, mounted at /api/v1/sales behind its module guard):
 *   GET    /sales/targets?on&subject&sales_team_id&query -> SalesTargetList          sales.targets.view
 *          (one row per target period containing `on`, plus "No target" rows; with `all=true`
 *          every target of the kind once, whole-range figures, no date)
 *   GET    /sales/targets/options                        -> SalesTargetOptions       sales.targets.view
 *   GET    /sales/targets/{id}?on                        -> SalesTargetDetail        sales.targets.view
 *   POST   /sales/targets                 body SalesTargetCreatePayload -> detail    sales.targets.add
 *   PATCH  /sales/targets/{id}            body SalesTargetUpdatePayload -> detail    sales.targets.edit
 *   PATCH  /sales/targets/{id}/periods/{period_id}  body {target_value} -> detail   sales.targets.edit
 *   POST   /sales/targets/{id}/duplicate                 -> the new target's detail  sales.targets.edit
 *   POST   /sales/targets/{id}/children   body {sales_agent_id, target_value} -> the team target
 *   DELETE /sales/targets/{id}                                                      sales.targets.delete
 *   GET    /sales/targets/point-ratios                   -> {rows: PointRatio[]}    sales.targets.view
 *   POST   /sales/targets/point-ratios   body {effective_from, rm_per_point}        sales.targets.edit
 *   POST   /sales/targets/import?dry_run&year  multipart file -> DealerImportResult sales.targets.edit
 * The screens park `sales_target.delete` instead of calling DELETE (D7), through
 * useDeferredAction. Products for the scope picker come from master data's own
 * `/master-data/products/select`, paged on the server (about 22,000 rows).
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  DealerImportResult,
  PointRatio,
  SalesTargetCreatePayload,
  SalesTargetDetail,
  SalesTargetList,
  SalesTargetListParams,
  SalesTargetOptions,
  SalesTargetUpdatePayload,
} from '../types/salesTarget.types';
import { codeWithName } from '@/lib/product-display';

const BASE = '/api/v1/sales/targets';

async function read<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export async function getSalesTargets(params: SalesTargetListParams): Promise<SalesTargetList> {
  const search = new URLSearchParams();
  if (params.all) search.set('all', 'true');
  else if (params.on) search.set('on', params.on);
  search.set('subject', params.subject);
  if (params.salesTeamId) search.set('sales_team_id', params.salesTeamId);
  if (params.query?.trim()) search.set('query', params.query.trim());
  return read(await apiFetch(`${BASE}?${search.toString()}`), 'Failed to load targets');
}

export async function getSalesTarget(id: string, on?: string): Promise<SalesTargetDetail> {
  const url = on ? `${BASE}/${id}?${new URLSearchParams({ on }).toString()}` : `${BASE}/${id}`;
  return read(await apiFetch(url), 'Failed to load target');
}

export async function getSalesTargetOptions(): Promise<SalesTargetOptions> {
  return read(await apiFetch(`${BASE}/options`), 'Failed to load target options');
}

export async function createSalesTarget(payload: SalesTargetCreatePayload): Promise<SalesTargetDetail> {
  return read(await apiFetch(BASE, jsonInit('POST', payload)), 'Failed to set target');
}

export async function patchSalesTarget(
  id: string,
  payload: SalesTargetUpdatePayload,
): Promise<SalesTargetDetail> {
  return read(await apiFetch(`${BASE}/${id}`, jsonInit('PATCH', payload)), 'Failed to save target');
}

export async function patchSalesTargetPeriod(
  id: string,
  periodId: string,
  payload: { target_value: number },
): Promise<SalesTargetDetail> {
  return read(
    await apiFetch(`${BASE}/${id}/periods/${periodId}`, jsonInit('PATCH', payload)),
    'Failed to save the figure',
  );
}

export async function createTargetChild(
  id: string,
  payload: { sales_agent_id: string; target_value: number },
): Promise<SalesTargetDetail> {
  return read(
    await apiFetch(`${BASE}/${id}/children`, jsonInit('POST', payload)),
    'Failed to add the figure',
  );
}

export async function duplicateSalesTarget(id: string): Promise<SalesTargetDetail> {
  return read(await apiFetch(`${BASE}/${id}/duplicate`, { method: 'POST' }), 'Failed to duplicate target');
}

export async function deleteSalesTarget(id: string): Promise<void> {
  const response = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete target'));
}

export const PRODUCT_PAGE_SIZE = 50;

/** One page of products for the scope picker, searched on the server. */
export async function searchTargetProducts(
  query: string,
  pageIndex = 0,
): Promise<{ value: string; label: string }[]> {
  const search = new URLSearchParams({
    limit: String(PRODUCT_PAGE_SIZE),
    offset: String(pageIndex * PRODUCT_PAGE_SIZE),
  });
  if (query.trim()) search.set('query', query.trim());
  const body = await read<
    { data?: { id: string; product_code: string; product_name: string }[] } | { id: string; product_code: string; product_name: string }[]
  >(await apiFetch(`/api/v1/master-data/products/select?${search.toString()}`), 'Could not load products');
  const rows = Array.isArray(body) ? body : (body.data ?? []);
  return rows.map((row) => ({ value: row.id, label: codeWithName(row.product_code, row.product_name) }));
}

/** One page of customers for a dealer target's accounts, searched on the server. */
export async function searchTargetCustomers(query: string): Promise<{ value: string; label: string }[]> {
  const search = new URLSearchParams({ limit: '50', offset: '0' });
  if (query.trim()) search.set('query', query.trim());
  const body = await read<{ data?: { id: string; customer_code: string; customer_name: string }[] }>(
    await apiFetch(`/api/v1/order-management/customers/select?${search.toString()}`),
    'Could not load customers',
  );
  return (body.data ?? []).map((row) => ({ value: row.id, label: `${row.customer_code} - ${row.customer_name}` }));
}

export async function getPointRatios(): Promise<{ rows: PointRatio[] }> {
  return read(await apiFetch(`${BASE}/point-ratios`), 'Failed to load point ratios');
}

export async function savePointRatio(payload: { effective_from: string; rm_per_point: number }): Promise<PointRatio> {
  return read(await apiFetch(`${BASE}/point-ratios`, jsonInit('POST', payload)), 'Failed to save the ratio');
}

export async function importDealerTargets(file: File, dryRun: boolean, year: number): Promise<DealerImportResult> {
  const body = new FormData();
  body.append('file', file);
  const search = new URLSearchParams({ dry_run: String(dryRun), year: String(year) });
  return read(
    await apiFetch(`${BASE}/import?${search.toString()}`, { method: 'POST', body }),
    dryRun ? 'Failed to test the file' : 'Failed to import the file',
  );
}
