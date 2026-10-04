import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ProductCategory } from '@/app/(protected)/master-data-management/products/types/product.types';

export const useProductCategorySelectQuery = () => {
  const fetchCategoryList = async (): Promise<ProductCategory[]> => {
    const response = await apiFetch('/api/v1/master-data/product-categories/select');

    // Throw, never return [] or the error body (NEVER-STUCK-UI S3, lever L5): the
    // picker then says "no access" or "could not load, retry" instead of looking
    // empty, and the shared query toast speaks once.
    if (!response.ok) throw new Error(await extractApiError(response, 'Could not load product categories.'));

    return response.json();
  };

  return useQuery({
    queryKey: ['product-category-select'],
    queryFn: fetchCategoryList,
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });
};
