import { useQuery } from '@tanstack/react-query';
import { getStockStatus, getStockStatusSummaries } from './stockStatusService';

export const STOCK_STATUS_KEY = 'stock-status';
export const STOCK_STATUS_SUMMARY_KEY = 'stock-status-summary';

/** The panel's read. Off until a product is known (`enabled`), e.g. a popover not yet opened. */
export function useStockStatus(productId: string | null, soDueBy: string | null, enabled = true) {
  return useQuery({
    queryKey: [STOCK_STATUS_KEY, productId, soDueBy],
    queryFn: () => getStockStatus(productId as string, soDueBy),
    enabled: enabled && Boolean(productId),
    retry: 1,
  });
}

/**
 * On hand and SO for every item on one page of a list, in one request. Keyed by the sorted
 * id set, so the same page asks once however its rows re-render.
 */
export function useStockStatusSummaries(productIds: string[]) {
  const ids = Array.from(new Set(productIds.filter(Boolean))).sort().slice(0, 200);
  return useQuery({
    queryKey: [STOCK_STATUS_SUMMARY_KEY, ids.join(',')],
    queryFn: () => getStockStatusSummaries(ids),
    enabled: ids.length > 0,
    staleTime: 60_000,
    retry: 1,
    select: (rows) => new Map(rows.map((row) => [row.product_id, row])),
  });
}
