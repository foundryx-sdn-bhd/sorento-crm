/**
 * useProducts Hook
 * 
 * Main CRUD hook for products using React Query
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';

import type { DataGridApiFetchParams } from '@/components/ui/data-grid';

import {
  getProducts,
  getProduct,
  createProduct,
  updateProduct,
  deleteProduct,
  duplicateProduct,
  bulkUpdateProducts,
  getPriceHistory,
  getProductPurchaseHistory,
  setVariantParent,
  unlinkVariant,
  resetVariantAuto,
  setFamilyCode,
  resetFamilyCode,
  type GetProductsParams,
  type ProductBulkUpdates,
} from '../services/productService';
import type { ProductFormData } from '../types/product.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

/**
 * Hook for fetching products list with pagination, sorting, and filtering
 */
export function useProducts(params: GetProductsParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      'products',
      params.pageIndex,
      params.pageSize,
      params.sorting,
      params.searchQuery,
      params.category_id,
      params.brand_id,
      params.status,
      params.price_min,
      params.price_max,
      params.item_type,
      params.variant_filter,
      params.discontinued_batch_id,
    ],
    queryFn: () => getProducts(params),
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });
}

/** List query the products detail nav forwards to the neighbours endpoint. */
export type ProductNeighboursListParams = DataGridApiFetchParams & {
  category_id?: string;
  brand_id?: string;
  status?: string;
  discontinued_batch_id?: string;
};


/**
 * Hook for fetching single product by ID
 */
export function useProduct(id: string | null) {
  return useQuery({
    queryKey: ['product', id],
    queryFn: () => {
      if (!id) throw new Error('Product ID is required');
      return getProduct(id);
    },
    enabled: !!id,
    staleTime: 1000 * 60 * 5, // 5 minutes
    retry: 1,
  });
}

/**
 * Every purchase order that bought this product, plus the cost summary the Overview
 * leads with. Loaded on the detail page rather than on the tab, because the Overview
 * shows the cost too and both must agree.
 */
export function useProductPurchaseHistory(id: string | null) {
  return useQuery({
    queryKey: ['product-purchase-history', id],
    queryFn: () => {
      if (!id) throw new Error('Product ID is required');
      return getProductPurchaseHistory(id);
    },
    enabled: !!id,
    staleTime: 1000 * 60 * 5,
    retry: 1,
  });
}

/**
 * Hook for fetching product price history
 */
export function useProductPriceHistory(id: string | null) {
  return useQuery({
    queryKey: ['product-price-history', id],
    queryFn: () => {
      if (!id) throw new Error('Product ID is required');
      return getPriceHistory(id);
    },
    enabled: !!id,
    staleTime: 1000 * 60 * 5, // 5 minutes
    retry: 1,
  });
}

/**
 * Hook for creating a new product
 */
export function useCreateProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ProductFormData) => createProduct(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      toast.success('Product created successfully', {
        position: 'top-center',
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to create product', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for updating a product
 */
export function useUpdateProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ProductFormData> }) =>
      updateProduct(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.id] });
      toast.success('Product updated successfully', {
        position: 'top-center',
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to update product', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for setting / changing a product's variant parent (manual curation).
 *
 * Reused for "Add variant" (attach a child): call with the CHILD's id as
 * `productId` and the current product's id as `parentId` - both ids are
 * invalidated so the parent detail's Variants list refreshes.
 */
export function useSetVariantParent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ productId, parentId }: { productId: string; parentId: string }) =>
      setVariantParent(productId, parentId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.productId] });
      if (variables.parentId) {
        queryClient.invalidateQueries({ queryKey: ['product', variables.parentId] });
      }
      toast.success('Variant parent updated', { position: 'top-center' });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to set variant parent', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for unlinking a product from its variant parent (manual curation).
 *
 * Reused for "Remove variant" (detach a child): call with the CHILD's id as
 * `productId` and the current product's id as `parentId` so the parent detail's
 * Variants list refreshes.
 */
export function useUnlinkVariant() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ productId }: { productId: string; parentId?: string }) =>
      unlinkVariant(productId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.productId] });
      if (variables.parentId) {
        queryClient.invalidateQueries({ queryKey: ['product', variables.parentId] });
      }
      toast.success('Variant unlinked', { position: 'top-center' });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to unlink variant', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for resetting a manually-curated product back to automatic derivation.
 */
export function useResetVariantAuto() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ productId }: { productId: string }) => resetVariantAuto(productId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.productId] });
      toast.success('Reset to auto-linking', { position: 'top-center' });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to reset variant link', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for hand-setting a product's model family.
 */
export function useSetFamilyCode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ productId, familyCode }: { productId: string; familyCode: string | null }) =>
      setFamilyCode(productId, familyCode),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.productId] });
      toast.success('Family updated', { position: 'top-center' });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to set family', { position: 'top-center' });
    },
  });
}

/**
 * Hook for resetting a hand-set family back to the code-derived one.
 */
export function useResetFamilyCode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ productId }: { productId: string }) => resetFamilyCode(productId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product', variables.productId] });
      toast.success('Family reset to auto', { position: 'top-center' });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to reset family', { position: 'top-center' });
    },
  });
}

/**
 * Hook for deleting a product
 */
export function useDeleteProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => deleteProduct(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      toast.success('Product deleted successfully', {
        position: 'top-center',
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to delete product', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for duplicating a product
 */
export function useDuplicateProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, newProductCode }: { id: string; newProductCode: string }) =>
      duplicateProduct(id, newProductCode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      toast.success('Product duplicated successfully', {
        position: 'top-center',
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to duplicate product', {
        position: 'top-center',
      });
    },
  });
}

/**
 * Hook for bulk updating products
 */
export function useBulkUpdateProducts() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      ids,
      updates,
    }: {
      ids: string[];
      updates: ProductBulkUpdates;
    }) => bulkUpdateProducts(ids, updates),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      queryClient.invalidateQueries({ queryKey: ['product'] });
      toast.success(`${result.updated_count} product${result.updated_count === 1 ? '' : 's'} updated`, {
        position: 'top-center',
      });
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to update products', {
        position: 'top-center',
      });
    },
  });
}

// Bulk delete has no mutation hook: the list parks one `product.delete` per selected
// row behind one countdown (`useDeferredBulkAction`, D7), so there is nothing left for a
// mutation to do and nothing for it to toast.
