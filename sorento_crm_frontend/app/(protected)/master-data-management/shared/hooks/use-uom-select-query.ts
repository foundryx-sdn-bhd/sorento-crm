import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { UnitOfMeasure } from '@/app/(protected)/master-data-management/products/types/product.types';

export const useUOMSelectQuery = () => {
  const fetchUOMList = async (): Promise<UnitOfMeasure[]> => {
    const response = await apiFetch('/api/v1/master-data/units-of-measure/select');

    // Throw, never return [] or the error body (NEVER-STUCK-UI S3, lever L5): the
    // picker then says "no access" or "could not load, retry" instead of looking
    // empty, and the shared query toast speaks once.
    if (!response.ok) throw new Error(await extractApiError(response, 'Could not load units of measure.'));

    return response.json();
  };

  return useQuery({
    queryKey: ['uom-select'],
    queryFn: fetchUOMList,
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });
};
