import { useQuery, useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useCompany } from '@/app/providers/CompanyProvider';
import { toast } from '@/lib/toast';

import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { getAccessAgents, getAccessAgent, createAccessAgent, updateAccessAgent, getContactAccessAgents, createContactAgentAccess, updateContactAgentAccess, deleteContactAgentAccess, getAgentTeams, setAgentTeams, getTeams } from '../services/accessAgentService';
import type { AccessAgentFormData, ContactAgentAccessFormData } from '../types/accessAgent.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';


export type AccessAgentsListParams = DataGridApiFetchParams & { status?: string };

/**
 * The list's React Query key. The detail page's pager rebuilds the SAME key from
 * the URL, so it reads the page the list already fetched.
 */
export function accessAgentsListQueryKey(params: AccessAgentsListParams): QueryKey {
  return [
    'access-agents',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.status,
  ];
}

/** The list query a detail URL describes, in the shape the list passes. */
export function accessAgentsListParamsFromUrl(
  params: ListPagerParams,
): AccessAgentsListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    status: params.filters.status,
  };
}

/** The pager's two hooks into the access agents list. */
export const accessAgentsPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    accessAgentsListQueryKey(accessAgentsListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getAccessAgents(accessAgentsListParamsFromUrl(params)),
};

export function useAccessAgents(params: AccessAgentsListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: accessAgentsListQueryKey(params),
    queryFn: () => getAccessAgents(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useAccessAgent(id: string | null) {
  return useQuery({
    queryKey: ['access-agent', id],
    queryFn: () => {
      if (!id) throw new Error('Access agent ID is required');
      return getAccessAgent(id);
    },
    enabled: !!id,
    // After delete, a refetch would 404 - treat as empty data, not a thrown error (no error toast).
    retry: false,
  });
}

export function useCreateAccessAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: AccessAgentFormData) => createAccessAgent(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['access-agents'] });
      toast.success('Access agent created successfully');
    },
  });
}

export function useUpdateAccessAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<AccessAgentFormData> }) => updateAccessAgent(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['access-agents'] });
      queryClient.invalidateQueries({ queryKey: ['access-agent'] });
    },
  });
}

// Contact Agent Access hooks
export function useContactAccessAgents(agentId: string | null) {
  return useQuery({
    queryKey: ['contact-access-agents', agentId],
    queryFn: () => {
      if (!agentId) throw new Error('Agent ID is required');
      return getContactAccessAgents(agentId);
    },
    enabled: !!agentId,
    retry: 1,
  });
}

export function useCreateContactAgentAccess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, data }: { agentId: string; data: ContactAgentAccessFormData }) => createContactAgentAccess(agentId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents', variables.agentId] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents'] }); // Invalidate all for grouped list and contact-specific queries
      queryClient.invalidateQueries({ queryKey: ['access-agent', variables.agentId] });
      toast.success('Contact access agent created successfully');
    },
    onError: (error: Error) => {
      // Show user-friendly error message
      const errorMessage = error.message || 'Failed to create contact access agent';
      toast.error(errorMessage);
    },
  });
}

export function useUpdateContactAgentAccess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, contactId, data }: { agentId: string; contactId: string; data: Partial<ContactAgentAccessFormData> }) => updateContactAgentAccess(agentId, contactId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents', variables.agentId] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents'] }); // Invalidate all for grouped list
      queryClient.invalidateQueries({ queryKey: ['access-agent', variables.agentId] });
      toast.success('Contact access agent updated successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to update contact access agent'),
  });
}

export function useDeleteContactAgentAccess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, contactId }: { agentId: string; contactId: string }) => deleteContactAgentAccess(agentId, contactId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents', variables.agentId] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents'] }); // Invalidate all for grouped list
      queryClient.invalidateQueries({ queryKey: ['access-agent', variables.agentId] });
      toast.success('Contact access agent deleted successfully');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete contact access agent'),
  });
}

export function useAgentTeams(agentId: string | null) {
  // The active company is part of the KEY, not just an invalidation trigger.
  // invalidateQueries() only refetches MOUNTED queries, so an agent detail that
  // was not open during a company switch keeps its old payload and serves it on
  // the next visit - the other company's team sets under this company's label.
  // A distinct key per company makes that impossible rather than unlikely.
  const { activeCompany } = useCompany();
  return useQuery({
    queryKey: ['agent-teams', agentId, activeCompany?.id ?? null],
    queryFn: () => {
      if (!agentId) throw new Error('Agent ID is required');
      return getAgentTeams(agentId);
    },
    enabled: !!agentId,
    retry: 1,
  });
}

export function useSetAgentTeams() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      agentId,
      assignments,
    }: {
      agentId: string;
      assignments: { code: string; team_id: string }[];
    }) => setAgentTeams(agentId, assignments),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['agent-teams', variables.agentId] });
      toast.success('Teams updated');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to set teams'),
  });
}

export function useTeams() {
  // Teams are company-scoped, so the picker must not serve another company's list.
  const { activeCompany } = useCompany();
  return useQuery({
    queryKey: ['teams-list', activeCompany?.id ?? null],
    queryFn: () => getTeams(),
    staleTime: 1000 * 60 * 2,
    retry: 1,
  });
}
