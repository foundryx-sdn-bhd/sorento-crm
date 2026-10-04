import { useQuery, useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  getPackingLists,
  getPackingList,
  createPackingList,
  updatePackingList,
  deletePackingList,
  bulkDeletePackingLists,
  getClearanceCheckpoints,
  getPackingListSourceInvoices,
  type PackingListsListParams,
} from '../services/packingListService';
import { getAuditLogs } from '@/app/(protected)/system-management/audit-logs/services/auditLogService';
import type { PackingListFormData } from '../types/packingList.types';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

/** `audit_logs.entity_type` for a container - `InboundShipment.__tablename__`. */
const PACKING_LIST_ENTITY_TYPE = 'inbound_shipments';

/**
 * What has happened to this container, newest first (R17).
 *
 * The audit trail IS the timeline: `InboundShipment` is `__audit_track__`, so every save
 * already writes a row, and the conversion's over-capacity reason is written there as one
 * described entry rather than appended to the operator's own Notes field.
 */
export function usePackingListHistory(packingListId: string | null) {
  return useQuery({
    queryKey: ['packing-lists', 'history', packingListId],
    queryFn: () =>
      getAuditLogs({
        entity_type: PACKING_LIST_ENTITY_TYPE,
        entity_id: packingListId as string,
        pageIndex: 0,
        pageSize: 50,
      }),
    enabled: !!packingListId,
  });
}

/**
 * The proforma invoices this container's lines were charged on (F10).
 *
 * Read once for the whole page: the Details card, the Lines column, the Timeline entry and
 * the Documents list are four readings of the same link rows, and four fetches of it would
 * be four chances for them to disagree.
 */
export function usePackingListSourceInvoices(
  packingListId: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: ['packing-lists', 'source-proforma-invoices', packingListId],
    queryFn: () => getPackingListSourceInvoices(packingListId as string),
    // False for a user without SCM read, for whom the endpoint 403s (PL-TABS-ACCESS).
    enabled: !!packingListId && enabled,
    // A 403 is an answer, so retrying it only doubles the wait; anything else gets the
    // app's one retry. Silent because it is read for every tab: the failure is reported
    // where the invoices would be (the card, Documents), not as a toast on Lines.
    retry: (failureCount, error) =>
      failureCount < 1 && !error.message.startsWith('Permission required:'),
    meta: { silent: true },
  });
}


/**
 * The list's React Query key. The detail page's pager rebuilds the SAME key from
 * the URL, so it reads the page the list already fetched.
 */
export function packingListsListQueryKey(params: PackingListsListParams): QueryKey {
  return [
    'packing-lists',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.supplier_id,
    params.shipment_status,
    params.region,
  ];
}

/** The list query a detail URL describes, in the shape the list passes. */
export function packingListsListParamsFromUrl(
  params: ListPagerParams,
): PackingListsListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    supplier_id: params.filters.supplier_id,
    shipment_status: params.filters.shipment_status,
    region: params.filters.region,
  };
}

/** The pager's two hooks into the packing lists list. */
export const packingListsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    packingListsListQueryKey(packingListsListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getPackingLists(packingListsListParamsFromUrl(params)),
};

export function usePackingLists(params: PackingListsListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: packingListsListQueryKey(params),
    queryFn: () => getPackingLists(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function usePackingList(id: string | null) {
  return useQuery({
    queryKey: ['packing-list', id],
    queryFn: () => {
      if (!id) throw new Error('Packing list ID is required');
      return getPackingList(id);
    },
    enabled: !!id,
    retry: 1,
  });
}

export function useCreatePackingList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: PackingListFormData) => createPackingList(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packing-lists'] });
      toast.success('Packing list created successfully');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to create packing list'),
  });
}

export function useUpdatePackingList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Partial<PackingListFormData>;
    }) => updatePackingList(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packing-lists'] });
      queryClient.invalidateQueries({ queryKey: ['packing-list'] });
      toast.success('Packing list updated successfully');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to update packing list'),
  });
}

export function useDeletePackingList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deletePackingList(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packing-lists'] });
      toast.success('Packing list deleted successfully');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to delete packing list'),
  });
}

export function useBulkDeletePackingLists() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => bulkDeletePackingLists(ids),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['packing-lists'] });
      toast.success(
        result?.message ?? `${result?.deleted_count ?? 0} packing list(s) deleted`,
      );
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to bulk delete packing lists'),
  });
}

/**
 * Checkpoint definitions for the clearance timeline.
 *
 * Configuration that changes when an admin edits it, not per-record data, so it
 * is cached hard and shared by every packing list detail page in the session.
 */
export function useClearanceCheckpoints() {
  return useQuery({
    queryKey: ['clearance-checkpoints'],
    queryFn: getClearanceCheckpoints,
    staleTime: 1000 * 60 * 30,
    retry: 1,
  });
}
