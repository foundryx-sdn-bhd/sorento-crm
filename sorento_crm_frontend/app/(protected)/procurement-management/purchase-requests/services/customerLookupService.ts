import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { CustomerLookupItem } from '../types/purchaseRequest.types';

/** Customer picker for the PR / SF form. */
export async function lookupCustomersForRequest(query: string): Promise<CustomerLookupItem[]> {
  const params = new URLSearchParams();
  if (query.trim()) params.set('q', query.trim());
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/lookups/customers?${params.toString()}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customers'));
  }
  return response.json();
}
