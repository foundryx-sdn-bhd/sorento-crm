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

export interface StockStatusProductOption {
  value: string;
  label: string;
  description?: string;
  searchText?: string;
}

/**
 * The stand-alone page's item picker: products searched on the server, one page at a time
 * (never a capped static list). Keyed by id, the code as the label and the name beside it as
 * its own field (no joined "code · name" label).
 */
export async function searchStockStatusProducts(query: string, pageIndex = 0): Promise<StockStatusProductOption[]> {
  const size = 50;
  const search = new URLSearchParams({ limit: String(size), offset: String(pageIndex * size) });
  if (query.trim()) search.set('query', query.trim());
  const response = await apiFetch(`/api/v1/master-data/products/select?${search.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load products'));
  const body = (await response.json()) as { data: { id: string; product_code: string; product_name: string | null }[] };
  return body.data.map((p) => ({
    value: p.id,
    label: p.product_code,
    description: p.product_name ?? undefined,
    searchText: `${p.product_code} ${p.product_name ?? ''}`,
  }));
}

type Waiter = { resolve: (row: StockStatusSummary | null) => void; reject: (error: unknown) => void };
let queued = new Map<string, Waiter[]>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

async function flushSummaries() {
  const batch = queued;
  queued = new Map();
  flushTimer = null;
  const ids = Array.from(batch.keys());
  for (let start = 0; start < ids.length; start += 200) {
    const chunk = ids.slice(start, start + 200);
    try {
      const rows = await getStockStatusSummaries(chunk);
      const byId = new Map(rows.map((row) => [row.product_id, row]));
      for (const id of chunk) for (const w of batch.get(id) ?? []) w.resolve(byId.get(id) ?? null);
    } catch (error) {
      for (const id of chunk) for (const w of batch.get(id) ?? []) w.reject(error);
    }
  }
}

/**
 * One item's summary, batched: every chip that asks within the same tick shares ONE request
 * (200 ids per call), so a page of a list costs one read however many rows it shows.
 */
export function loadStockStatusSummary(productId: string): Promise<StockStatusSummary | null> {
  return new Promise((resolve, reject) => {
    const waiters = queued.get(productId) ?? [];
    waiters.push({ resolve, reject });
    queued.set(productId, waiters);
    if (!flushTimer) flushTimer = setTimeout(flushSummaries, 0);
  });
}
