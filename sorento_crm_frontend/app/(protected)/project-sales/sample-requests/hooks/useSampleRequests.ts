import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import type { ListPagerPage, ListPagerParams } from '@/hooks/useListPager';
import type { SampleRequestWrite } from '@/types/sampleRequest';
import {
  createSampleRequest,
  getProjectSampleRequests,
  getSampleRequest,
  getSampleRequests,
  getSampleRequestSwitches,
  moveSampleRequest,
  saveSampleRequestInternal,
  submitSampleRequest,
  updateSampleRequest,
  type SampleRequestListParams,
  type SampleRequestMoveBody,
} from '../services/sampleRequestService';

/** The list's React Query key; the detail pager rebuilds the SAME key from the URL. */
export function sampleRequestsListQueryKey(params: SampleRequestListParams): QueryKey {
  return [
    'sample-requests',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.status,
    params.projectId,
    params.salespersonId,
  ];
}

function listParamsFromUrl(params: ListPagerParams): SampleRequestListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    status: params.filters.status,
    projectId: params.filters.project_id,
    salespersonId: params.filters.salesperson_id,
  };
}

/** The pager's two hooks into the sample requests list. */
export const sampleRequestsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    sampleRequestsListQueryKey(listParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getSampleRequests(listParamsFromUrl(params)),
};

export function useSampleRequestsQuery(params: SampleRequestListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: sampleRequestsListQueryKey(params),
    queryFn: () => getSampleRequests(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useProjectSampleRequestsQuery(projectId: string) {
  return useQuery({
    queryKey: ['sample-requests', 'project', projectId],
    queryFn: () => getProjectSampleRequests(projectId),
    retry: 1,
  });
}

export function useSampleRequestQuery(id: string | null) {
  return useQuery({
    queryKey: ['sample-request', id],
    queryFn: () => {
      if (!id) throw new Error('Sample request ID is required');
      return getSampleRequest(id);
    },
    enabled: !!id,
    retry: 1,
  });
}

/** The two System Settings switches, read through the open projection any form user may read. */
export function useSampleRequestSwitches() {
  return useQuery({
    queryKey: ['system-app-config', 'sample-request-switches'],
    queryFn: getSampleRequestSwitches,
    staleTime: Infinity,
  });
}

export function useSampleRequestMutations() {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['sample-requests'] });
    void queryClient.invalidateQueries({ queryKey: ['sample-request'] });
  };
  const fail = (fallback: string) => (error: Error) => toast.error(error.message || fallback);

  const create = useMutation({
    mutationFn: (data: SampleRequestWrite) => createSampleRequest(data),
    onSuccess: refresh,
    onError: fail('Failed to save the sample request'),
  });
  const update = useMutation({
    mutationFn: ({ id, data }: { id: string; data: SampleRequestWrite }) =>
      updateSampleRequest(id, data),
    onSuccess: refresh,
    onError: fail('Failed to save the sample request'),
  });
  const submit = useMutation({
    mutationFn: (id: string) => submitSampleRequest(id),
    onSuccess: () => {
      refresh();
      toast.success('Sample request submitted');
    },
    onError: fail('Failed to submit the sample request'),
  });
  const move = useMutation({
    mutationFn: ({ id, body }: { id: string; body: SampleRequestMoveBody }) =>
      moveSampleRequest(id, body),
    onSuccess: refresh,
    onError: fail('Failed to move the sample request'),
  });
  const saveInternal = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { consignment_no: string | null; po_no: string | null };
    }) => saveSampleRequestInternal(id, body),
    onSuccess: () => {
      refresh();
      toast.success('Saved');
    },
    onError: fail('Failed to save the consignment details'),
  });

  return { create, update, submit, move, saveInternal };
}
