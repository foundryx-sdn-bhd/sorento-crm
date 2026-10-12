'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  addProductCompatibility,
  applyFlyerReview,
  getFlyerReview,
  getProductCompatibility,
} from '../services/productCompatibilityService';
import type { CompatibilityRole } from '../types/productCompatibility.types';

const compatibilityKey = (productId: string | undefined) => [
  'product-compatibility',
  productId,
];
const flyerReviewKey = ['seat-cover-flyer-review'] as const;

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

export function useFlyerReview() {
  return useQuery({ queryKey: flyerReviewKey, queryFn: getFlyerReview });
}

export function useApplyFlyerReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (wcProductIds: string[]) => applyFlyerReview(wcProductIds),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: flyerReviewKey });
      queryClient.invalidateQueries({ queryKey: ['product-compatibility'] });
      const skipped = result.skipped.length;
      toast.success(
        `${result.linked} link${result.linked === 1 ? '' : 's'} added` +
          (skipped
            ? `, ${skipped} SKU${skipped === 1 ? '' : 's'} skipped`
            : ''),
      );
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to link the SKUs'),
  });
}

/** Retiring the old Seat Cover spec is destructive: the long countdown, on the registry row. */
export function useRetireSeatCoverSpec() {
  return useDeferredRowAction({
    actionKey: 'seat_cover_spec.retire',
    entityType: 'seat_cover_spec',
    verb: 'Retiring',
    successMessage: 'Seat Cover spec retired',
    invalidateKeys: [flyerReviewKey],
  });
}
