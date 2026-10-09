import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import type { ConversationSLATrackingDetail } from '../types/conversationSLATracking.types';
import {
  getConversationSLATracking,
  getConversationSLATrackingDetail,
  getSLATrackingDashboardMetrics,
  deleteConversationSLAEventLog,
  getConversationSLAEventLogs,
  syncAssigneeFromRespond,
  postConversationSLATestOverrides,
  fetchSlaTrackingMedia,
  getSlaTrackingConversation,
  getSlaTrackingConversationPage,
  searchSlaTrackingConversation,
  postSlaTrackingConversationReply,
  type ConversationSLATestOverridesBody,
  type ConversationSLATrackingListParams,
} from '../services/conversationSLATrackingService';
import type { ConversationSLAEventLogsParams } from '../services/conversationSLATrackingService';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';


/**
 * The list's React Query key. The detail page's pager rebuilds the SAME key from
 * the URL, so it reads the page the list already fetched.
 */
export function conversationSlaListQueryKey(
  params: ConversationSLATrackingListParams,
): QueryKey {
  return [
    'conversation-sla-tracking',
    params.pageIndex,
    params.pageSize,
    params.sorting,
    params.searchQuery,
    params.policy_id,
    params.status,
    params.assigned_to,
    // AC-M2 deep-link filters: part of the key, or a "View history" landing
    // renders the previously cached unfiltered page.
    params.contact,
    params.is_resolved,
    params.resolved_by,
  ];
}

/** The list query a detail URL describes, in the shape the list passes. */
export function conversationSlaListParamsFromUrl(
  params: ListPagerParams,
): ConversationSLATrackingListParams {
  const f = params.filters;
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    policy_id: f.policy_id,
    status: f.status,
    assigned_to: f.assigned_to,
    contact: f.contact,
    is_resolved: f.is_resolved === 'true' ? true : f.is_resolved === 'false' ? false : undefined,
    resolved_by: f.resolved_by,
  };
}

/** The pager's two hooks into the conversation SLA list. */
export const conversationSlaPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    conversationSlaListQueryKey(conversationSlaListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getConversationSLATracking(conversationSlaListParamsFromUrl(params)),
};

export function useConversationSLATracking(params: ConversationSLATrackingListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: conversationSlaListQueryKey(params),
    queryFn: () => getConversationSLATracking(params),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useConversationSLATrackingDetail(id: string | null) {
  return useQuery<ConversationSLATrackingDetail>({
    queryKey: ['conversation-sla-tracking-detail', id],
    queryFn: () => {
      if (!id) throw new Error('Conversation SLA tracking ID is required');
      return getConversationSLATrackingDetail(id);
    },
    enabled: !!id,
    retry: 1,
  });
}

export function useConversationSLAEventLogs(trackingId: string | null, params: Omit<ConversationSLAEventLogsParams, 'tracking_id'>) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['conversation-sla-event-logs', trackingId, params.page, params.limit, params.sort, params.dir, params.event_type, params.date_from, params.date_to, params.assigned_to_id],
    queryFn: () => getConversationSLAEventLogs({ ...params, tracking_id: trackingId! }),
    enabled: !!trackingId,
    retry: 1,
  });
}

export function useSLATrackingDashboardMetrics() {
  return useQuery({
    queryKey: ['sla-tracking-dashboard-metrics'],
    queryFn: () => getSLATrackingDashboardMetrics(),
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });
}

export function useDeleteConversationSLAEventLog() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteConversationSLAEventLog,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking-detail'] });
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-event-logs'] });
    },
  });
}

export function useSyncAssigneeFromRespond() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: syncAssigneeFromRespond,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking'] });
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking-detail'] });
      const message = data?.message ?? (data?.updated ? 'Assignee synced from Respond.io.' : 'Sync successful.');
      toast.success(message);
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to sync assignee from Respond.io.');
    },
  });
}

export function useConversationSLATestOverrides(trackingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ConversationSLATestOverridesBody) =>
      postConversationSLATestOverrides(trackingId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking'] });
      queryClient.invalidateQueries({ queryKey: ['conversation-sla-tracking-detail', trackingId] });
      toast.success('Tracking updated.');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Update failed.');
    },
  });
}

/**
 * The shared contact thread. `refetchIntervalMs` opts a surface into polling so
 * an open, idle chat shows the contact's next message without a manual refresh;
 * it is opt-in so surfaces that merely display the thread do not poll. A poll
 * reads `chat_histories`; at most one throttled background delta read per
 * contact per 30 s reaches Respond.io. Polling stops while
 * the tab is in the background and whenever the query is disabled.
 */
export function useSlaTrackingConversation(
  trackingId: string | null,
  options?: { limit?: number; cursor?: string; enabled?: boolean; refetchIntervalMs?: number },
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['sla-tracking-conversation', trackingId, options?.limit, options?.cursor],
    queryFn: () =>
      getSlaTrackingConversation(trackingId!, {
        limit: options?.limit ?? 50,
        cursor: options?.cursor,
      }),
    enabled: !!trackingId && (options?.enabled !== false),
    staleTime: 30 * 1000,
    refetchInterval: options?.refetchIntervalMs ?? false,
    refetchIntervalInBackground: false,
  });
}

/**
 * The two loaders `useConversationThread` needs for scroll-back and in-thread
 * search (AC-L7 / AC-L8), memoised on the ticket id.
 *
 * A hook rather than two service imports in the component: the layering rule is
 * UI -> hook -> service, and the thread hook re-runs its effects whenever a
 * loader identity changes, so where the `useCallback` lives is behaviour, not
 * tidiness.
 */
export function useSlaTrackingThreadLoaders(trackingId: string | null) {
  const loadPage = useCallback(
    (params: { before?: string; after?: string; around?: string; limit?: number }) =>
      getSlaTrackingConversationPage(trackingId ?? '', params),
    [trackingId],
  );
  const searchMessages = useCallback(
    (query: string) => searchSlaTrackingConversation(trackingId ?? '', query),
    [trackingId],
  );
  return { loadPage, searchMessages };
}

/**
 * The chat-media byte loader for a ticket-scoped thread (UAC AC-N4), memoised
 * on the ticket id so `RespondChatList` does not see a new callback each render.
 */
export function useSlaTrackingMediaProxy(trackingId: string | null) {
  return useCallback(
    (url: string) => fetchSlaTrackingMedia(trackingId ?? '', url),
    [trackingId],
  );
}

export function useSlaTrackingConversationReply(trackingId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => postSlaTrackingConversationReply(trackingId, message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sla-tracking-conversation', trackingId] });
      toast.success('Message sent');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to send'),
  });
}
