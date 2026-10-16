'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { ENTITY_DOWNLOADS_QUERY_KEY, MY_DOWNLOADS_QUERY_KEY } from '@/services/myDownloadsService';
import {
  createSnapshot,
  exportSnapshot,
  getSnapshot,
  getSnapshotRowLines,
  listSnapshotRows,
  listSnapshots,
  setSnapshotDecision,
} from '../services/stockDebtSnapshotService';
import type { SnapshotListParams, SnapshotRowsParams } from '../services/stockDebtSnapshotService';
import type { StockDebtExportParams } from '../services/stockDebtService';
import type { StockDebtExportSplit } from '../types/stockDebt.types';
import type { SnapshotDecisionInput } from '../types/stockDebtSnapshot.types';

export const SNAPSHOTS_KEY = ['project-sales', 'stock-debt', 'snapshots'] as const;

export function useSnapshotsQuery(params: SnapshotListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [...SNAPSHOTS_KEY, 'list', params.pageIndex, params.pageSize, params.query],
    queryFn: () => listSnapshots(params),
    retry: 1,
  });
}

export function useSnapshotQuery(id: string) {
  return useQuery({
    queryKey: [...SNAPSHOTS_KEY, 'one', id],
    queryFn: () => getSnapshot(id),
    retry: 1,
  });
}

export function useSnapshotRowsQuery(id: string, params: SnapshotRowsParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      ...SNAPSHOTS_KEY, 'rows', id, params.pageIndex, params.pageSize, params.query, params.decision,
    ],
    queryFn: () => listSnapshotRows(id, params),
    retry: 1,
  });
}

/** The stored lines behind one cell (`month`) or one product (no month). Fires only while the
 *  drill is open. */
export function useSnapshotLinesQuery(id: string, rowId: string | null, month: string | null) {
  return useQuery({
    queryKey: [...SNAPSHOTS_KEY, 'lines', id, rowId, month ?? 'all'],
    queryFn: () => getSnapshotRowLines(id, rowId as string, month ?? undefined),
    enabled: Boolean(rowId),
    retry: 1,
  });
}

/** Save the board. Success toasts "Snapshot saved" with an Open action (the caller supplies
 *  where Open goes, because the hook has no router). */
export function useCreateSnapshot(onOpen: (id: string) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; filters: Omit<StockDebtExportParams, 'split'> }) =>
      createSnapshot(input),
    onSuccess: (snapshot) => {
      queryClient.invalidateQueries({ queryKey: [...SNAPSHOTS_KEY, 'list'] });
      toast.success('Snapshot saved', {
        action: { label: 'Open', onClick: () => onOpen(snapshot.id) },
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useSetSnapshotDecision(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { rowId: string } & SnapshotDecisionInput) => {
      const { rowId, ...decision } = input;
      return setSnapshotDecision(id, rowId, decision);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...SNAPSHOTS_KEY, 'rows', id] });
      queryClient.invalidateQueries({ queryKey: [...SNAPSHOTS_KEY, 'one', id] });
      queryClient.invalidateQueries({ queryKey: [...SNAPSHOTS_KEY, 'lines', id] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/** Starts the stored snapshot's workbook through My Downloads, as the board's Export does. */
export function useExportSnapshot(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { split: StockDebtExportSplit; show_aux: boolean }) =>
      exportSnapshot(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_DOWNLOADS_QUERY_KEY });
      queryClient.invalidateQueries({
        queryKey: [...ENTITY_DOWNLOADS_QUERY_KEY, 'stock_debt_snapshot_xlsx'],
      });
      toast.success('Export queued - find it in My Downloads.');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to start the snapshot export'),
  });
}
