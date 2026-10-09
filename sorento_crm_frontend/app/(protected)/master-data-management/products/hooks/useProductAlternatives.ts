'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  addProductAlternative,
  getProductAlternatives,
} from '../services/productAlternativeService';

const alternativesKey = (productId: string | undefined) => ['product-alternatives', productId];

export function useProductAlternatives(productId: string | undefined) {
  return useQuery({
    queryKey: alternativesKey(productId),
    queryFn: () => getProductAlternatives(productId as string),
    enabled: !!productId,
  });
}

export function useAddProductAlternative(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alternativeProductId: string) =>
      addProductAlternative(productId, alternativeProductId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: alternativesKey(productId) });
      toast.success('Alternative added');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to add the alternative'),
  });
}

/**
 * Reset to auto is a server-deferred action on the product (hand-added links go with it),
 * with the same Cancel window as unlink.
 */
export function useResetProductAlternativesAuto(productId: string) {
  return useDeferredRowAction({
    actionKey: 'product_alternatives.reset_auto',
    entityType: 'product_alternatives',
    verb: 'Resetting',
    successMessage: 'Alternatives reset to auto',
    invalidateKeys: [alternativesKey(productId)],
  });
}

/** Unlink is a server-deferred action with a Cancel window, like the companion rule delete. */
export function useProductAlternativeUnlink(productId: string) {
  return useDeferredRowAction({
    actionKey: 'product_alternative.unlink',
    entityType: 'product_alternative',
    verb: 'Unlinking',
    successMessage: 'Alternative unlinked',
    invalidateKeys: [alternativesKey(productId)],
  });
}
