import { useQuery } from '@tanstack/react-query';
import { getStockStatus, loadStockStatusSummary } from './stockStatusService';

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
 * One item's On hand and SO for a list chip. Batched under the hood
 * (`loadStockStatusSummary`): a page of chips shares one request.
 */
export function useStockStatusSummary(productId: string | null | undefined) {
  return useQuery({
    queryKey: [STOCK_STATUS_SUMMARY_KEY, productId],
    queryFn: () => loadStockStatusSummary(productId as string),
    enabled: Boolean(productId),
    staleTime: 60_000,
    retry: 1,
  });
}
