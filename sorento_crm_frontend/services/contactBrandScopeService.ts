/**
 * Per-contact accessible brands (CONTACT-BRAND-SCOPE).
 *
 * Layering: UI (ContactBrandScopeSection) -> hook (useContactBrandScope) -> THIS service ->
 * lib/api -> backend.
 *
 *   GET /api/v1/user-management/contacts/{id}/brands
 *     -> { brand_ids: string[], brands: [{ id, brand_name }] }
 *   PUT /api/v1/user-management/contacts/{id}/brands   body { brand_ids: string[] }
 *     -> the same shape. Replaces the list; `[]` stores NULL = every brand. An unknown
 *        brand id is a 422.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface ContactBrandRef {
  id: string;
  brand_name: string;
}

export interface ContactBrandScope {
  /** Empty = every brand. */
  brand_ids: string[];
  brands: ContactBrandRef[];
}

export const contactBrandScopeKey = (contactId: string) => ['contact-brand-scope', contactId];

const path = (contactId: string) =>
  `/api/v1/user-management/contacts/${encodeURIComponent(contactId)}/brands`;

export async function getContactBrandScope(contactId: string): Promise<ContactBrandScope> {
  const response = await apiFetch(path(contactId));
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load the contact brands'));
  }
  return response.json();
}

export async function saveContactBrandScope(
  contactId: string,
  brandIds: string[],
): Promise<ContactBrandScope> {
  const response = await apiFetch(path(contactId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ brand_ids: brandIds }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save the contact brands'));
  }
  return response.json();
}
