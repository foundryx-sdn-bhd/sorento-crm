import { invalidateContactTagSettings } from '@/app/(protected)/user-management/contacts/[id]/hooks/useContactTagSettings';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  deleteSpecVisibility,
  getSpecVisibility,
  getSpecVisibilityKeys,
  saveSpecVisibility,
  specVisibilityScopeKey,
  type SpecKeyRef,
  type SpecVisibilityInput,
  type SpecVisibilityPolicyResponse,
  type SpecVisibilityScope,
} from '@/services/specVisibilityService';

/**
 * Spec visibility policy hooks (PLAN-spec-visibility-policy, S1).
 *
 * One set of hooks for all three tiers - the contact page, the market segment admin
 * and the settings default differ only by the `scope` they pass down (same shape as
 * `useStockVisibility`).
 */

export function useSpecVisibilityQuery(scope: SpecVisibilityScope, enabled = true) {
  return useQuery<SpecVisibilityPolicyResponse, Error>({
    queryKey: specVisibilityScopeKey(scope),
    queryFn: () => getSpecVisibility(scope),
    enabled,
    retry: 1,
  });
}

export function useSpecVisibilityMutations(scope: SpecVisibilityScope) {
  const queryClient = useQueryClient();
  const key = specVisibilityScopeKey(scope);

  const save = useMutation<SpecVisibilityPolicyResponse, Error, SpecVisibilityInput>({
    mutationFn: (input) => saveSpecVisibility(scope, input),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      queryClient.invalidateQueries({ queryKey: ['spec-visibility'] });
      if (scope.kind === 'contact') invalidateContactTagSettings(queryClient, scope.contactId);
      toast.success('Spec visibility saved');
    },
    onError: (error) => toast.error(error.message || 'Failed to save spec visibility'),
  });

  // No toast here on purpose: Remove goes through `useDeferredAction`, which owns
  // the success and error toasts. Two toasts for one click is the bug this avoids.
  const remove = useMutation<SpecVisibilityPolicyResponse, Error, void>({
    mutationFn: () => deleteSpecVisibility(scope),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      queryClient.invalidateQueries({ queryKey: ['spec-visibility'] });
      if (scope.kind === 'contact') invalidateContactTagSettings(queryClient, scope.contactId);
    },
  });

  return { save, remove };
}

/** The registry keys the picker offers, sorted by label. A small fixed list, so a
 * standing query (not a per-keystroke search) is all the picker needs. */
export function useSpecVisibilityKeys() {
  return useQuery<SpecKeyRef[], Error>({
    queryKey: ['spec-visibility-keys'],
    queryFn: getSpecVisibilityKeys,
    staleTime: Infinity,
  });
}
