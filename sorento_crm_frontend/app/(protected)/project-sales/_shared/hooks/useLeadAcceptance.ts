'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { getUserLookup } from '@/services/userSelectService';
import {
  acceptLead,
  assignLead,
  declineLead,
  listAwaitingAcceptance,
  nudgeLeadAssignee,
} from '../services/leadAcceptanceService';
import type { AwaitingAcceptanceParams } from '../types/leadAcceptance.types';
// Read-only: one definition of the lead cache key, owned by useProjects.
import { LEADS_KEY, leadKey } from './useProjects';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export const AWAITING_ACCEPTANCE_KEY = [LEADS_KEY, 'awaiting-acceptance'];
export const awaitingAcceptanceKey = (params: AwaitingAcceptanceParams) => [
  ...AWAITING_ACCEPTANCE_KEY,
  params,
];

export function useAwaitingAcceptance(params: AwaitingAcceptanceParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: awaitingAcceptanceKey(params),
    queryFn: () => listAwaitingAcceptance(params),
  });
}

/**
 * Who a lead can be handed to. The shared people lookup, open to every signed-in user.
 */
export function useAssignableUsers() {
  return useQuery({
    queryKey: ['user-lookup'],
    queryFn: () => getUserLookup(),
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Assign, accept, decline, nudge.
 *
 * Every one of them changes both the lead and the worklist, so all four invalidate the
 * whole lead key rather than a single list: a lead that leaves "awaiting acceptance"
 * has to disappear from that screen in the same beat it changes on its own page.
 */
export function useLeadAcceptanceMutations() {
  const queryClient = useQueryClient();

  const invalidate = (leadId?: string) => {
    queryClient.invalidateQueries({ queryKey: [LEADS_KEY] });
    if (leadId) queryClient.invalidateQueries({ queryKey: leadKey(leadId) });
  };

  const assign = useMutation({
    mutationFn: ({
      id,
      ownerUserId,
      note,
    }: {
      id: string;
      ownerUserId: string;
      note?: string | null;
    }) => assignLead(id, { owner_user_id: ownerUserId, note: note ?? null }),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(
        lead.owner_name
          ? `${lead.lead_code} is awaiting acceptance by ${lead.owner_name}`
          : `${lead.lead_code} assigned`,
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const accept = useMutation({
    mutationFn: (id: string) => acceptLead(id),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} accepted`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const decline = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      declineLead(id, reason),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(`${lead.lead_code} declined and back with marketing`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const nudge = useMutation({
    mutationFn: ({
      id,
      ownerUserId,
      note,
    }: {
      id: string;
      ownerUserId: string;
      note?: string | null;
    }) => nudgeLeadAssignee(id, ownerUserId, note),
    onSuccess: (lead) => {
      invalidate(lead.id);
      toast.success(
        lead.owner_name ? `${lead.owner_name} notified again` : 'Assignee notified again',
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { assign, accept, decline, nudge };
}
