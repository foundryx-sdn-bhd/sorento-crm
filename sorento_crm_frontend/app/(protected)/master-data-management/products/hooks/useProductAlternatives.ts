'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  addProductAlternative,
  getProductAlternatives,
  resetProductAlternativesAuto,
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

export function useResetProductAlternativesAuto(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resetProductAlternativesAuto(productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: alternativesKey(productId) });
      toast.success('Alternatives reset to auto');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to reset alternatives'),
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
