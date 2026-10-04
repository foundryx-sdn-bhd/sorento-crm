import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import type { SortingState } from '@tanstack/react-table';
import {
  createDoFromSalesOrder,
  createSalesOrder,
  deleteSalesOrder,
  getSalesOrder,
  getSalesOrderAgents,
  getSalesOrders,
  resetSalesOrderPlanning,
  updateSalesOrder,
} from '../services/salesOrderService';
import type { SalesOrderFormData } from '../types/scm.types';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export interface UseSalesOrdersParams {
  pageIndex: number;
  pageSize: number;
  sorting: SortingState;
  searchQuery: string;
  status: string | null;
  priority: string | null;
  /** Where the order came from: 'inquiry' | 'upload' | 'manual'. Null for all. */
  source?: string | null;
  /** Order date, inclusive of both ends. ISO `yyyy-mm-dd`. */
  dateFrom?: string | null;
  dateTo?: string | null;
  customerCode?: string | null;
  /** Keep only orders with quantity still owed. `false` narrows nothing. */
  outstanding?: boolean;
  salesAgentId?: string | null;
  /** The planning class: 'project' | 'retail' | 'unclassified'. Null for all. */
  demandClass?: string | null;
  /** AutoCount's Transferable flag: 'yes' | 'no' | 'unknown'. Null for all. */
  transferable?: string | null;
}

/**
 * The list's React Query key. The detail page's pager rebuilds the SAME key from
 * the URL, so it reads the page the list already fetched.
 */
export function salesOrdersListQueryKey(params: UseSalesOrdersParams): QueryKey {
  return ['scm', 'sales-orders', params];
}

/** The list query a detail URL describes, in the shape the list passes. */
export function salesOrdersListParamsFromUrl(
  params: ListPagerParams,
): UseSalesOrdersParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    status: params.filters.status || null,
    priority: params.filters.priority || null,
    source: params.filters.source || null,
    dateFrom: params.filters.date_from || null,
    dateTo: params.filters.date_to || null,
    // The detail URL carries `customer_code` - `buildDetailSearch`'s own key on this page
    // (`SalesOrdersGrid.tsx`'s `detailSearch`) - never `customer_id`.
    customerCode: params.filters.customer_code || null,
    outstanding: params.filters.outstanding === 'true',
    salesAgentId: params.filters.sales_agent_id || null,
    demandClass: params.filters.demand_class || null,
    transferable: params.filters.transferable || null,
  };
}

/** The pager's two hooks into the sales orders list. */
export const salesOrdersPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    salesOrdersListQueryKey(salesOrdersListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> => {
    const p = salesOrdersListParamsFromUrl(params);
    return getSalesOrders({
      pageIndex: p.pageIndex,
      pageSize: p.pageSize,
      sortField: p.sorting?.[0]?.id,
      sortDir: p.sorting?.[0]?.desc ? 'desc' : 'asc',
      searchQuery: p.searchQuery,
      status: p.status,
      priority: p.priority,
      source: p.source ?? null,
      dateFrom: p.dateFrom ?? null,
      dateTo: p.dateTo ?? null,
      customerCode: p.customerCode ?? null,
      outstanding: p.outstanding ?? false,
      salesAgentId: p.salesAgentId ?? null,
      demandClass: p.demandClass ?? null,
      transferable: p.transferable ?? null,
    });
  },
};

/**
 * `enabled` lets the list hold the fetch until the user's remembered sort and filter have
 * resolved, so the grid is fetched ONCE with the view already applied instead of fetching the
 * defaults and immediately refetching (PLAN-listing-view-memory, AC-B3). Kept OUT of the query
 * key - it is a fetch gate, not part of what was fetched.
 */
export function useSalesOrders(params: UseSalesOrdersParams & { enabled?: boolean }) {
  const { enabled = true, ...listParams } = params;
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: salesOrdersListQueryKey(listParams),
    queryFn: () =>
      getSalesOrders({
        pageIndex: listParams.pageIndex,
        pageSize: listParams.pageSize,
        sortField: listParams.sorting?.[0]?.id,
        sortDir: listParams.sorting?.[0]?.desc ? 'desc' : 'asc',
        searchQuery: listParams.searchQuery,
        status: listParams.status,
        priority: listParams.priority,
        source: listParams.source ?? null,
        dateFrom: listParams.dateFrom ?? null,
        dateTo: listParams.dateTo ?? null,
        customerCode: listParams.customerCode ?? null,
        outstanding: listParams.outstanding ?? false,
        salesAgentId: listParams.salesAgentId ?? null,
        demandClass: listParams.demandClass ?? null,
        transferable: listParams.transferable ?? null,
      }),
    enabled,
    staleTime: 10_000,
    retry: 1,
  });
}

/** One sales order, for the detail page. Mirrors `usePurchaseOrder`. */
export function useSalesOrder(id: string | null) {
  return useQuery({
    queryKey: ['scm', 'sales-orders', 'detail', id],
    queryFn: () => getSalesOrder(id as string),
    enabled: !!id,
    staleTime: 5_000,
    retry: 1,
  });
}

/** Active sales agents, for the Agent filter and the detail page's Agent select. */
export function useSalesOrderAgents() {
  return useQuery({
    queryKey: ['scm', 'sales-order-agents'],
    queryFn: getSalesOrderAgents,
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

export function useCreateSalesOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: SalesOrderFormData) => createSalesOrder(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
      qc.invalidateQueries({ queryKey: ['scm', 'net-position'] });
      toast.success('Sales order created');
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to create sales order'),
  });
}

export function useUpdateSalesOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: SalesOrderFormData }) =>
      updateSalesOrder(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
      qc.invalidateQueries({ queryKey: ['scm', 'net-position'] });
      toast.success('Sales order updated');
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to update sales order'),
  });
}

export function useDeleteSalesOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSalesOrder(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
      qc.invalidateQueries({ queryKey: ['scm', 'net-position'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to delete sales order'),
  });
}

export function useCreateDoFromSalesOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => createDoFromSalesOrder(id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
      qc.invalidateQueries({ queryKey: ['scm', 'net-position'] });
      qc.invalidateQueries({ queryKey: ['scm', 'rollups'] });
      toast.success(`Delivery order ${res.do_number} created`);
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to create delivery order'),
  });
}

export function useResetSalesOrderPlanning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, rewindBook }: { id: string; rewindBook: boolean }) =>
      resetSalesOrderPlanning(id, rewindBook),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scm', 'sales-orders'] });
      qc.invalidateQueries({ queryKey: ['scm', 'net-position'] });
      qc.invalidateQueries({ queryKey: ['project-sales'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to reset planning'),
  });
}
