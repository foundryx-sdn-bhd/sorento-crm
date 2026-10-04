/**
 * Shared user select service for dropdowns (teams, access agents, approvers, etc.).
 * See docs/ADR-PRODUCT-STANDARDS.md.
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface UserSelectItem {
  id: string;
  name: string | null;
  /** Null for a phone-only user (identity S0/S3: email is optional). */
  email: string | null;
  respond_user_id?: string | null;
  respond_synced?: string | null;
}

const USERS_SELECT = '/api/user-management/users/select';

export async function getUsersSelect(params?: {
  query?: string;
  respond_synced?: string;
  status?: string;
  /** Only users granted this company. Team membership requires the grant. */
  company_id?: string;
  /** The user holding this phone (identity S3, "Link this contact to <name> instead"). */
  phone?: string;
  /** The user linked to this WhatsApp contact (identity S3, "Open user"). */
  respond_contact_id?: string;
  /** Only users with no linked WhatsApp contact (identity S3, "Link existing user"). */
  unlinked?: boolean;
}): Promise<UserSelectItem[]> {
  const sp = new URLSearchParams();
  if (params?.query) sp.set('query', params.query);
  if (params?.respond_synced) sp.set('respond_synced', params.respond_synced);
  if (params?.status) sp.set('status', params.status);
  if (params?.company_id) sp.set('company_id', params.company_id);
  if (params?.phone) sp.set('phone', params.phone);
  if (params?.respond_contact_id) sp.set('respond_contact_id', params.respond_contact_id);
  if (params?.unlinked) sp.set('unlinked', 'true');
  const url = USERS_SELECT + (sp.toString() ? `?${sp.toString()}` : '');
  const response = await apiFetch(url);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch users'));
  return response.json();
}

/**
 * A row of the shared people picker. Id and name only: no email, phone or account state.
 * `respond_user_id` is present only when the caller asked for `respond_synced`.
 */
export interface UserLookupItem {
  id: string;
  name: string | null;
  respond_user_id?: string | null;
}

const USERS_LOOKUP = '/api/user-management/users/lookup';

/**
 * Active people for owner / assignee / watcher pickers in any module. Open to every
 * signed-in user (owner ruling 1 Oct 2026, never-stuck L10), unlike {@link getUsersSelect},
 * which needs `user_management.users.view` and serves the user-admin screens.
 */
export async function getUserLookup(params?: {
  /** Matches the name only. */
  query?: string;
  /** Only users linked to a Respond.io agent, each with its `respond_user_id`. */
  respond_synced?: boolean;
  /** Also deactivated staff: for filters over past records, never for assigning. */
  include_inactive?: boolean;
}): Promise<UserLookupItem[]> {
  const sp = new URLSearchParams();
  if (params?.query) sp.set('query', params.query);
  if (params?.respond_synced) sp.set('respond_synced', 'true');
  if (params?.include_inactive) sp.set('include_inactive', 'true');
  const url = USERS_LOOKUP + (sp.toString() ? `?${sp.toString()}` : '');
  const response = await apiFetch(url);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch people'));
  return response.json();
}
