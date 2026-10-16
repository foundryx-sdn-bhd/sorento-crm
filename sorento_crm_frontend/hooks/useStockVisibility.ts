import { invalidateContactTagSettings } from '@/app/(protected)/user-management/contacts/[id]/hooks/useContactTagSettings';
import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import {
  deleteStockVisibility,
  getDealerPoolWarehouses,
  getStockVisibility,
  saveStockVisibility,
  searchStockVisibilityWarehouses,
  stockVisibilityScopeKey,
  type StockVisibilityInput,
  type StockVisibilityPolicyResponse,
  type StockVisibilityScope,
  type StockVisibilityWarehouse,
} from '@/services/stockVisibilityService';

/**
 * Stock visibility policy hooks (PLAN-stock-visibility-policy, S1).
 *
 * One set of hooks for all three tiers - the contact page, the access type admin and
 * the settings default differ only by the `scope` they pass down.
 */

export function useStockVisibilityQuery(scope: StockVisibilityScope, enabled = true) {
  return useQuery<StockVisibilityPolicyResponse, Error>({
    queryKey: stockVisibilityScopeKey(scope),
    queryFn: () => getStockVisibility(scope),
    enabled,
    retry: 1,
  });
}

export function useStockVisibilityMutations(scope: StockVisibilityScope) {
  const queryClient = useQueryClient();
  const key = stockVisibilityScopeKey(scope);

  const save = useMutation<StockVisibilityPolicyResponse, Error, StockVisibilityInput>({
    mutationFn: (input) => saveStockVisibility(scope, input),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      queryClient.invalidateQueries({ queryKey: ['stock-visibility'] });
      if (scope.kind === 'contact') invalidateContactTagSettings(queryClient, scope.contactId);
      toast.success('Stock visibility saved');
    },
    onError: (error) => toast.error(error.message || 'Failed to save stock visibility'),
  });

  // No toast here on purpose: Remove goes through `useDeferredAction`, which owns the
  // success and error toasts. Two toasts for one click is the bug this avoids.
  const remove = useMutation<StockVisibilityPolicyResponse, Error, void>({
    mutationFn: () => deleteStockVisibility(scope),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      queryClient.invalidateQueries({ queryKey: ['stock-visibility'] });
      if (scope.kind === 'contact') invalidateContactTagSettings(queryClient, scope.contactId);
    },
  });

  return { save, remove };
}

/**
 * "Dealer pool" preset. A click-time fetch rather than a standing query: the list is
 * only ever wanted the moment the button is pressed.
 */
export function useDealerPoolWarehouses() {
  return useMutation<StockVisibilityWarehouse[], Error, void>({
    mutationFn: () => getDealerPoolWarehouses(),
    onError: (error) => toast.error(error.message || 'Failed to load the dealer pool'),
  });
}

/** Server-search feed for the Locations picker; never a capped client-side list. */
export function useStockVisibilityWarehouseSearch() {
  return useCallback(
    (query: string) => searchStockVisibilityWarehouses(query),
    [],
  );
}
