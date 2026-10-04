import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import {
  addCustomerGroupCustomers,
  createCustomerGroup,
  getCustomerGroup,
  getCustomerGroupCustomers,
  getCustomerGroups,
  updateCustomerGroup,
  type CustomerGroupsListParams,
} from '../services/customerGroupService';

/** The list's React Query key; the detail pager rebuilds the SAME key from the URL. */
export function customerGroupsListQueryKey(params: CustomerGroupsListParams): QueryKey {
  return [
    'customer-groups',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    !!params.agent_mixed,
  ];
}

function paramsFromUrl(params: ListPagerParams): CustomerGroupsListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    agent_mixed: params.filters.agent_mixed === 'true',
  };
}

/** The pager's two hooks into the customer groups list. */
export const customerGroupsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    customerGroupsListQueryKey(paramsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getCustomerGroups(paramsFromUrl(params)),
};

export function useCustomerGroups(params: CustomerGroupsListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: customerGroupsListQueryKey(params),
    queryFn: () => getCustomerGroups(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useCustomerGroup(id: string | null) {
  return useQuery({
    queryKey: ['customer-group', id],
    queryFn: () => getCustomerGroup(id as string),
    enabled: !!id,
    retry: 1,
  });
}

/** The ledgers key. The remove countdown invalidates it after the server commits. */
export const CUSTOMER_GROUP_CUSTOMERS_PREFIX = ['customer-group-customers'] as const;

export function useCustomerGroupCustomers(groupId: string | null, params: DataGridApiFetchParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      ...CUSTOMER_GROUP_CUSTOMERS_PREFIX,
      groupId,
      params.pageIndex,
      params.pageSize,
      params.sorting,
      params.searchQuery,
    ],
    queryFn: () => getCustomerGroupCustomers(groupId as string, params),
    enabled: !!groupId,
    retry: 1,
  });
}

export function useCreateCustomerGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string }) => createCustomerGroup(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customer-groups'] });
      toast.success('Customer group created');
    },
    // No error toast: the Add group dialog shows the reason inline and stays open.
  });
}

export function useUpdateCustomerGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name: string } }) =>
      updateCustomerGroup(id, data),
    onSuccess: (_res, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['customer-groups'] });
      queryClient.invalidateQueries({ queryKey: ['customer-group', id] });
      queryClient.invalidateQueries({ queryKey: ['customer'] });
      toast.success('Customer group updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to save customer group'),
  });
}

export function useAddCustomerGroupCustomers(groupId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (customerIds: string[]) => addCustomerGroupCustomers(groupId, customerIds),
    onSuccess: (customers) => {
      // The whole prefix: a ledger left another group's tab too.
      queryClient.invalidateQueries({ queryKey: CUSTOMER_GROUP_CUSTOMERS_PREFIX });
      queryClient.invalidateQueries({ queryKey: ['customer-groups'] });
      queryClient.invalidateQueries({ queryKey: ['customer-group'] });
      queryClient.invalidateQueries({ queryKey: ['customer'] });
      toast.success(customers.length === 1 ? 'Ledger added' : `${customers.length} ledgers added`);
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to add ledgers'),
  });
}
