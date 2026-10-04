import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface ContactAccessTypeOption {
  code: string;
  name: string;
  description: string | null;
  sort_order: number | null;
  keywords: string[];
}

/**
 * Full type for admin CRUD (includes is_active, timestamps).
 *
 * `portal_form_types` moved to market segments (PLAN-portal-forms-market-
 * segment D1) - this type deliberately carries no such field any more, and
 * the create/update routes ignore the key if a stale caller still sends it.
 */
export interface ContactAccessTypeAdmin {
  code: string;
  name: string;
  description: string | null;
  is_active: boolean;
  sort_order: number | null;
  keywords: string[];
  created_at: string;
  updated_at: string;
}

const base = '/api/user-management/contact-access-types';

/**
 * Mirrors `MANAGE` in `app/api/v1/user_management/contact_access_types.py`: every write on
 * the catalog. The active-type list (`getContactAccessTypes`) is open to any signed-in user.
 */
export const CONTACT_ACCESS_TYPE_PERMS = { manage: 'user_management.reference_data.manage' } as const;

export async function getContactAccessTypes(): Promise<ContactAccessTypeOption[]> {
  const response = await apiFetch(base);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch contact access types'));
  const data = await response.json();
  return Array.isArray(data) ? data : [];
}

export async function getAllContactAccessTypes(): Promise<ContactAccessTypeAdmin[]> {
  const response = await apiFetch(`${base}/all`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch contact access types'));
  const data = await response.json();
  return Array.isArray(data) ? data : [];
}

export async function getContactAccessType(code: string): Promise<ContactAccessTypeAdmin> {
  const response = await apiFetch(`${base}/${encodeURIComponent(code)}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch contact access type'));
  return response.json();
}

export async function createContactAccessType(
  body: Omit<ContactAccessTypeAdmin, 'created_at' | 'updated_at'>
): Promise<ContactAccessTypeAdmin> {
  const response = await apiFetch(base, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to create contact access type'));
  return response.json();
}

export async function updateContactAccessType(
  code: string,
  body: Partial<
    Pick<ContactAccessTypeAdmin, 'name' | 'description' | 'is_active' | 'sort_order' | 'keywords'>
  >
): Promise<ContactAccessTypeAdmin> {
  const response = await apiFetch(`${base}/${encodeURIComponent(code)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to update contact access type'));
  return response.json();
}

export async function deleteContactAccessType(code: string): Promise<void> {
  const response = await apiFetch(`${base}/${encodeURIComponent(code)}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete contact access type'));
}
