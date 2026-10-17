import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { StockStatus, StockStatusSummary } from './stockStatus.types';

const BASE = '/api/v1/inventory/stock-status';

/** One item, every location, with its documents. `soDueBy` (YYYY-MM-DD) cuts SO only. */
export async function getStockStatus(productId: string, soDueBy: string | null): Promise<StockStatus> {
  const search = new URLSearchParams({ product_id: productId });
  if (soDueBy) search.set('so_due_by', soDueBy);
  const response = await apiFetch(`${BASE}?${search.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load the stock status'));
  return response.json();
}

/** On hand and SO for up to 200 items at once: one page of a list's item chips. */
export async function getStockStatusSummaries(productIds: string[]): Promise<StockStatusSummary[]> {
  if (productIds.length === 0) return [];
  const search = new URLSearchParams({ product_ids: productIds.join(',') });
  const response = await apiFetch(`${BASE}/summary?${search.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load stock'));
  return response.json();
}
