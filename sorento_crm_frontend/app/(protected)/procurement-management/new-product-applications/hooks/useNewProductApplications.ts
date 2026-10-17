import { useMemo, useRef } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import type { ListPagerPage, ListPagerParams } from '@/hooks/useListPager';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import type { NewProductApplicationLookups } from '@/components/common/NewProductApplicationLines';
import { getSuppliersSelect } from '@/services/supplierSelectService';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
import { useProductCategorySelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import type { NewProductApplicationWrite } from '@/types/newProductApplication';
import {
  approveNewProductApplication,
  cancelNewProductApplication,
  closeNewProductApplicationLine,
  createNewProductApplication,
  getNewProductApplication,
  getNewProductApplicationPrefill,
  getNewProductApplications,
  getStockInquiryApplications,
  linkNewProductApplicationLine,
  rejectNewProductApplication,
  reviseNewProductApplication,
  searchNewProductApplicationItemTypes,
  submitNewProductApplication,
  undoNewProductApplicationLine,
  updateNewProductApplication,
  type NewProductApplicationListParams,
} from '../services/newProductApplicationService';

/** The list's React Query key; the detail pager rebuilds the SAME key from the URL. */
export function newProductApplicationsListQueryKey(
  params: NewProductApplicationListParams,
): QueryKey {
  return [
    'new-product-applications',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.status,
    params.applicantId,
  ];
}

function listParamsFromUrl(
  params: ListPagerParams,
): NewProductApplicationListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    status: params.filters.status,
    applicantId: params.filters.applicant_id,
  };
}

/** The pager's two hooks into the applications list. */
export const newProductApplicationsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    newProductApplicationsListQueryKey(listParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getNewProductApplications(listParamsFromUrl(params)),
};

export function useNewProductApplicationsQuery(
  params: NewProductApplicationListParams,
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: newProductApplicationsListQueryKey(params),
    queryFn: () => getNewProductApplications(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useNewProductApplicationQuery(id: string | null) {
  return useQuery({
    queryKey: ['new-product-application', id],
    queryFn: () => {
      if (!id) throw new Error('Application ID is required');
      return getNewProductApplication(id);
    },
    enabled: !!id,
    retry: 1,
  });
}

/** The pre-fill copied from a responded stock inquiry; fetched once per inquiry. */
export function useNewProductApplicationPrefillQuery(
  stockInquiryId: string | null,
) {
  return useQuery({
    queryKey: ['new-product-application', 'prefill', stockInquiryId],
    queryFn: () => getNewProductApplicationPrefill(stockInquiryId as string),
    enabled: !!stockInquiryId,
    staleTime: Infinity,
    retry: 1,
  });
}

/** The applications raised from one stock inquiry; `enabled` is false for users with no NPA permission. */
export function useStockInquiryApplicationsQuery(
  stockInquiryId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['new-product-applications', 'stock-inquiry', stockInquiryId],
    queryFn: () => getStockInquiryApplications(stockInquiryId as string),
    enabled: enabled && !!stockInquiryId,
    retry: 1,
  });
}

const matches = (label: string, q: string) =>
  label.toLowerCase().includes(q.trim().toLowerCase());

/**
 * The staff selects of the line table: item group, UOM and brand from the master-data select
 * hooks (read once, filtered here), supplier and item type from their own searches. The
 * portal passes its own lookups to the same table.
 */
export function useNewProductApplicationLookups(): NewProductApplicationLookups {
  const groups = useProductCategorySelectQuery();
  const uoms = useUOMSelectQuery();
  const brands = useBrandSelectQuery();
  // The fetchers stay the same function between renders, so an open dropdown is not
  // refetched each time a query settles; they read what the hooks last returned.
  const latest = useRef<Record<string, SearchableSelectOption[]>>({});
  latest.current = {
    groups: (groups.data ?? []).map((c) => ({
      value: c.id,
      label: c.category_name,
      code: c.category_code,
    })),
    uoms: (uoms.data ?? []).map((u) => ({
      value: u.id,
      label: u.uom_code,
      description: u.uom_name,
    })),
    brands: (brands.data ?? []).map((b) => ({
      value: b.id,
      label: b.brand_name,
      code: b.brand_code,
    })),
  };
  return useMemo(() => {
    const pick = (key: string) => async (q: string) =>
      (latest.current[key] ?? []).filter(
        (o) =>
          !q.trim() ||
          matches(`${o.label} ${o.code ?? ''} ${o.description ?? ''}`, q),
      );
    return {
      itemGroups: pick('groups'),
      uoms: pick('uoms'),
      brands: pick('brands'),
      itemTypes: (q: string) => searchNewProductApplicationItemTypes(q),
      suppliers: (q: string, pageIndex: number) =>
        getSuppliersSelect(q, pageIndex),
    };
  }, []);
}

export function useNewProductApplicationMutations() {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ['new-product-applications'],
    });
    void queryClient.invalidateQueries({
      queryKey: ['new-product-application'],
    });
  };
  const fail = (fallback: string) => (error: Error) =>
    toast.error(error.message || fallback);
  const move = (fallback: string, done?: string) => ({
    onSuccess: () => {
      refresh();
      if (done) toast.success(done);
    },
    onError: fail(fallback),
  });

  const create = useMutation({
    mutationFn: (data: NewProductApplicationWrite) =>
      createNewProductApplication(data),
    ...move('Failed to save the application'),
  });
  const update = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: NewProductApplicationWrite;
    }) => updateNewProductApplication(id, data),
    ...move('Failed to save the application'),
  });
  const submit = useMutation({
    mutationFn: (id: string) => submitNewProductApplication(id),
    ...move('Failed to submit the application', 'Application submitted'),
  });
  const approve = useMutation({
    mutationFn: (id: string) => approveNewProductApplication(id),
    ...move('Failed to approve the application', 'Application approved'),
  });
  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      rejectNewProductApplication(id, reason),
    ...move('Failed to reject the application', 'Application rejected'),
  });
  const revise = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      reviseNewProductApplication(id, reason),
    ...move('Failed to revise the application', 'Back to draft'),
  });
  const cancel = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      cancelNewProductApplication(id, reason),
    ...move('Failed to cancel the application', 'Application cancelled'),
  });
  const link = useMutation({
    mutationFn: ({
      id,
      lineId,
      productId,
    }: {
      id: string;
      lineId: string;
      productId: string;
    }) => linkNewProductApplicationLine(id, lineId, productId),
    ...move('Failed to link the product', 'Product linked'),
    // Two purchasers on one line: the second write is refused (409) and the screen refreshes.
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to link the product');
      refresh();
    },
  });
  const close = useMutation({
    mutationFn: ({
      id,
      lineId,
      reason,
    }: {
      id: string;
      lineId: string;
      reason: string;
    }) => closeNewProductApplicationLine(id, lineId, reason),
    ...move('Failed to close the line'),
  });
  const undo = useMutation({
    mutationFn: ({ id, lineId }: { id: string; lineId: string }) =>
      undoNewProductApplicationLine(id, lineId),
    ...move('Failed to undo the line'),
  });

  return {
    create,
    update,
    submit,
    approve,
    reject,
    revise,
    cancel,
    link,
    close,
    undo,
  };
}
