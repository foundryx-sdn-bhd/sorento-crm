import { useQuery } from '@tanstack/react-query';
import {
  getScopeBrandOptions,
  type ScopeBrandOption,
} from '../services/productDiscontinuedScopeService';

/**
 * Brands of one company, for a single row of the product-discontinued scope
 * editor. Disabled for the "All companies" row, which has no brand choice.
 *
 * Callers must read ``isError``: this endpoint 404s a company the caller cannot
 * reach, and an editor that showed the failure as an empty list would read as "this company has no brands" - which is the one
 * reading that quietly widens the scope being saved.
 */
export const useProductDiscontinuedScopeBrands = (companyId: string | null) =>
  useQuery<ScopeBrandOption[]>({
    queryKey: ['product-discontinued-scope-brands', companyId],
    queryFn: () => getScopeBrandOptions(companyId as string),
    enabled: Boolean(companyId),
    staleTime: 1000 * 60 * 5,
    retry: 1,
  });
