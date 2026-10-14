'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { ENTITY_DOWNLOADS_QUERY_KEY, MY_DOWNLOADS_QUERY_KEY } from '@/services/myDownloadsService';
import {
  exportStockDebt,
  getStockDebtCell,
  getStockDebtList,
} from '../services/stockDebtService';
import type {
  StockDebtExportParams,
  StockDebtGroupsMode,
  StockDebtListParams,
} from '../services/stockDebtService';
import type { StockDebtBook } from '../types/stockDebt.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

/**
 * The month x product board (AC-S2-6, extended AC-1 to AC-9).
 *
 * `keepPreviousData` so paging or flipping the debt toggle does not blank a wide
 * table back to skeletons - the columns would jump and the reader would lose the
 * month they were looking at.
 */
export function useStockDebtQuery(params: StockDebtListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      'project-sales',
      'stock-debt',
      'list',
      params.pageIndex,
      params.pageSize,
      params.query,
      params.onlyDebt,
      params.book,
      params.supplierIds,
      params.groups ?? [],
      params.groupsMode ?? 'include',
      params.dateFrom,
      params.dateTo,
      Boolean(params.projected),
    ],
    queryFn: () => getStockDebtList(params),
    staleTime: 60_000,
    retry: 1,
  });
}

/**
 * Starts the workbook export through My Downloads (R10/R12, AC-33/AC-34). Same shape as
 * `useExportOrderSheet` (`scm/reorder/hooks/useSummaryOrder.ts`): invalidates the
 * drawer's feed on success and toasts where the file will show up - this mutation never
 * returns or saves a file itself, the worker marks the row ready later.
 */
export function useExportStockDebt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: StockDebtExportParams) => exportStockDebt(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_DOWNLOADS_QUERY_KEY });
      queryClient.invalidateQueries({
        queryKey: [...ENTITY_DOWNLOADS_QUERY_KEY, 'stock_debt_xlsx'],
      });
      toast.success('Export queued - find it in My Downloads.');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to start the stock debt export'),
  });
}

/**
 * One cell's demand and supply (AC-S2-7, extended AC-11/AC-11b). Fires only while its
 * lightbox is open: a board is 4,000 rows x 15 columns, so nothing here is fetched up
 * front.
 *
 * `dateFrom`, `dateTo` and `book` are part of the KEY, not just of the request: the same
 * product and month answer differently under a narrowed board, so a shared cache entry
 * would hand the narrowed board the whole book's drill. `groups` (Location, owner 14 Oct
 * 2026) is part of the key for the same reason.
 */
export function useStockDebtCellQuery(
  productId: string | null,
  month: string | null,
  dateFrom?: string,
  dateTo?: string,
  book?: StockDebtBook,
  projected?: boolean,
  groups?: string[],
  groupsMode?: StockDebtGroupsMode,
) {
  return useQuery({
    queryKey: [
      'project-sales', 'stock-debt', 'cell', productId, month, dateFrom ?? '', dateTo ?? '',
      book ?? 'all', Boolean(projected), groups ?? [],
      groupsMode ?? 'include',
    ],
    // The projection rides only when the switch is on, so the month view's request is
    // exactly what it was before the switch existed.
    queryFn: () => {
      const id = productId as string;
      const key = month as string;
      if (groups?.length) {
        return getStockDebtCell(
          id, key, dateFrom, dateTo, book, projected, groups,
          groupsMode === 'exclude' ? 'exclude' : undefined,
        );
      }
      return projected
        ? getStockDebtCell(id, key, dateFrom, dateTo, book, true)
        : getStockDebtCell(id, key, dateFrom, dateTo, book);
    },
    enabled: Boolean(productId && month),
    staleTime: 60_000,
    retry: 1,
  });
}
