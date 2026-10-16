import { invalidateContactTagSettings } from '@/app/(protected)/user-management/contacts/[id]/hooks/useContactTagSettings';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  createMarketSegment,
  deleteMarketSegment,
  getContactMarketSegments,
  getMemberMarketSegments,
  listMarketSegments,
  setContactMarketSegments,
  setMemberMarketSegments,
  updateMarketSegment,
  type MarketSegmentUpdate,
} from '../services/marketSegmentService';

/** Catalog query. `activeOnly` = only active segments (used by pickers). */
export function useMarketSegments(activeOnly = false) {
  return useQuery({
    queryKey: ['market-segments', { activeOnly }],
    queryFn: () => listMarketSegments(activeOnly),
    staleTime: 5 * 60 * 1000,
  });
}

/** Catalog create/update/delete mutations - invalidate + toast, mirrors the CRUD standard. */
export function useMarketSegmentMutations() {
  const queryClient = useQueryClient();

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['market-segments'] });
  };

  const create = useMutation({
    mutationFn: createMarketSegment,
    onSuccess: () => {
      invalidate();
      toast.success('Market segment created');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const update = useMutation({
    mutationFn: ({ code, body }: { code: string; body: MarketSegmentUpdate }) =>
      updateMarketSegment(code, body),
    onSuccess: () => {
      invalidate();
      toast.success('Market segment updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: deleteMarketSegment,
    onSuccess: () => {
      invalidate();
      toast.success('Market segment deleted');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return { create, update, remove };
}

/** A contact's assigned segment codes. */
export function useContactMarketSegments(contactId: string) {
  return useQuery({
    queryKey: ['contact-market-segments', contactId],
    queryFn: () => getContactMarketSegments(contactId),
    enabled: !!contactId,
  });
}

export function useSetContactMarketSegments(contactId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (codes: string[]) => setContactMarketSegments(contactId, codes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-market-segments', contactId] });
      invalidateContactTagSettings(queryClient, contactId);
      toast.success('Market segment updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

/** A team-member's assigned segment codes (per (team, user) membership). */
export function useMemberMarketSegments(teamId: string, userId: string) {
  return useQuery({
    queryKey: ['member-market-segments', teamId, userId],
    queryFn: () => getMemberMarketSegments(teamId, userId),
    enabled: !!teamId && !!userId,
  });
}

export function useSetMemberMarketSegments(teamId: string, userId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (codes: string[]) => setMemberMarketSegments(teamId, userId, codes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['member-market-segments', teamId, userId] });
      toast.success('Segments updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}
