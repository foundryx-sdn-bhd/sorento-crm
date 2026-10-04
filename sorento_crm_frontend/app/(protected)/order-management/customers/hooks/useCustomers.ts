import { useQuery, useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import {
  getCustomers,
  getCustomer,
  createCustomer,
  updateCustomer,
  deleteCustomer,
  getCustomerLinkedContacts,
} from '../services/customerService';
import type { CustomerFormData } from '../types/customer.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';


export type CustomersListParams = DataGridApiFetchParams & {
  status?: string;
  customer_group_id?: string;
};

/**
 * The list's React Query key. The detail page's pager rebuilds the SAME key from
 * the URL, so it reads the page the list already fetched.
 */
export function customersListQueryKey(params: CustomersListParams): QueryKey {
  return [
    'customers',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.status,
    params.customer_group_id,
  ];
}

/** The list query a detail URL describes, in the shape the list passes. */
export function customersListParamsFromUrl(
  params: ListPagerParams,
): CustomersListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    status: params.filters.status,
    customer_group_id: params.filters.customer_group_id,
  };
}

/** The pager's two hooks into the customers list. */
export const customersPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    customersListQueryKey(customersListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getCustomers(customersListParamsFromUrl(params)),
};

export function useCustomers(params: CustomersListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: customersListQueryKey(params),
    queryFn: () => getCustomers(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useCustomer(id: string | null) {
  return useQuery({
    queryKey: ['customer', id],
    queryFn: () => {
      if (!id) throw new Error('Customer ID is required');
      return getCustomer(id);
    },
    enabled: !!id,
    retry: 1,
  });
}

export function useCreateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CustomerFormData) => createCustomer(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      toast.success('Customer created successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create customer'),
  });
}

export function useUpdateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<CustomerFormData> }) => updateCustomer(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      queryClient.invalidateQueries({ queryKey: ['customer'] });
      toast.success('Customer updated successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to update customer'),
  });
}

export function useDeleteCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCustomer(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      toast.success('Customer deleted successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete customer'),
  });
}

export function useCustomerLinkedContacts(customerId: string) {
  return useQuery({
    queryKey: ['customer-linked-contacts', customerId],
    queryFn: () => getCustomerLinkedContacts(customerId),
    enabled: !!customerId,
    retry: 1,
  });
}
