'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  addProductCompatibility,
  getProductCompatibility,
} from '../services/productCompatibilityService';
import type { CompatibilityRole } from '../types/productCompatibility.types';

const compatibilityKey = (productId: string | undefined) => [
  'product-compatibility',
  productId,
];

export function useProductCompatibility(productId: string | undefined) {
  return useQuery({
    queryKey: compatibilityKey(productId),
    queryFn: () => getProductCompatibility(productId as string),
    enabled: !!productId,
  });
}

export function useAddProductCompatibility(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      otherProductId,
      otherRole,
    }: {
      otherProductId: string;
      otherRole: CompatibilityRole;
    }) => addProductCompatibility(productId, otherProductId, otherRole),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['product-compatibility'] });
      toast.success('Link added');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to add the link'),
  });
}

/** Unlink is a server-deferred action with a Cancel window, like the alternatives unlink. */
export function useProductCompatibilityUnlink() {
  return useDeferredRowAction({
    actionKey: 'product_compatibility.unlink',
    entityType: 'product_compatibility',
    verb: 'Unlinking',
    successMessage: 'Link removed',
    invalidateKeys: [['product-compatibility']],
  });
}
