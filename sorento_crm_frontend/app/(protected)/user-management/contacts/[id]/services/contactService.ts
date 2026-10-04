import { apiFetch } from '@/lib/api';
import {
  apiError,
  buildDataGridParams,
  extractApiError,
  type DataGridParamsInput,
} from '@/lib/api-client';
import type { RespondContact } from '../../types/contact.types';

/**
 * Contact record reads used by the contact detail shell.
 *
 * GET /api/v1/user-management/contacts/{id}           -> RespondContact
 * GET /api/v1/user-management/contacts?page&limit&... -> { data: RespondContact[], pagination }
 * GET /api/v1/user-management/contacts/{id}/companies -> { id, name }[]
 */

export interface RespondContactListResponse {
  data: RespondContact[];
  pagination?: { total: number };
}

export async function getContact(contactId: string): Promise<RespondContact> {
  const response = await apiFetch(`/api/user-management/contacts/${contactId}`);
  if (!response.ok) {
    // Status-carrying, so the layout can tell a refusal or a missing contact from a fault.
    throw await apiError(response, 'Failed to load contact');
  }
  return (await response.json()) as RespondContact;
}

export async function getContacts(
  params: DataGridParamsInput,
  filters: Record<string, string> = {},
): Promise<RespondContactListResponse> {
  const query = buildDataGridParams(params, filters);
  const response = await apiFetch(`/api/user-management/contacts?${query.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load contacts'));
  }
  return (await response.json()) as RespondContactListResponse;
}

/** Superadmin-only; a 403 for anyone else resolves to an empty list rather than
 *  an error, since the caller only uses this to prefill a form. */
export async function getContactCompanies(
  contactId: string,
): Promise<{ id: string; name: string }[]> {
  const response = await apiFetch(`/api/user-management/contacts/${contactId}/companies`);
  if (!response.ok) return [];
  return (await response.json()) as { id: string; name: string }[];
}
