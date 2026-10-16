import { apiFetch } from '@/lib/api';
import {
  buildDataGridParams,
  extractApiError,
  type DataGridParamsInput,
} from '@/lib/api-client';
import type {
  ApplyResult,
  BulkTagInput,
  BulkTagResult,
  ContactTag,
  ContactTagInput,
  ContactTagListResponse,
} from '../types/contactTag.types';

const base = '/api/user-management/contact-tags';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

export async function getContactTags(
  params: DataGridParamsInput,
  extra: Record<string, string> = {},
): Promise<ContactTagListResponse> {
  const query = buildDataGridParams(params, extra);
  const response = await apiFetch(`${base}?${query.toString()}`);
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to fetch contact tags'),
    );
  return (await response.json()) as ContactTagListResponse;
}

export async function getContactTag(id: string): Promise<ContactTag> {
  const response = await apiFetch(`${base}/${encodeURIComponent(id)}`);
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to fetch contact tag'),
    );
  return (await response.json()) as ContactTag;
}

export async function createContactTag(
  body: ContactTagInput,
): Promise<ContactTag> {
  const response = await apiFetch(base, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to create contact tag'),
    );
  return (await response.json()) as ContactTag;
}

export async function updateContactTag(
  id: string,
  body: Partial<ContactTagInput>,
): Promise<ContactTag> {
  const response = await apiFetch(`${base}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to update contact tag'),
    );
  return (await response.json()) as ContactTag;
}

export async function deleteContactTag(id: string): Promise<void> {
  const response = await apiFetch(`${base}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to delete contact tag'),
    );
}

export async function bulkTagContacts(
  input: BulkTagInput,
): Promise<BulkTagResult> {
  const response = await apiFetch('/api/user-management/contacts/bulk-tags', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      contact_ids: input.contactIds,
      add_tag_ids: input.addTagIds,
      remove_tag_ids: input.removeTagIds,
      apply_settings: input.applySettings,
      dry_run: input.dryRun,
    }),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to tag contacts'));
  return (await response.json()) as BulkTagResult;
}

/** Replace one contact's tag set (the contact edit dialog). */
export async function setContactTags(
  contactId: string,
  tagIds: string[],
): Promise<{ tags: { id: string; name: string }[] }> {
  const response = await apiFetch(
    `/api/user-management/contacts/${encodeURIComponent(contactId)}/tags`,
    {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify({ tag_ids: tagIds }),
    },
  );
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to save contact tags'),
    );
  return (await response.json()) as { tags: { id: string; name: string }[] };
}

/** Preview (`dryRun`) or apply the settings of every tag each contact holds. */
export async function applyTagSettings(input: { contactIds: string[]; dryRun: boolean }): Promise<ApplyResult> {
  const response = await apiFetch('/api/user-management/contacts/bulk-apply-tag-settings', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ contact_ids: input.contactIds, dry_run: input.dryRun }),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to apply tag settings'));
  return (await response.json()) as ApplyResult;
}

/** Preview or apply to every holder of one tag, only the keys that tag sets. */
export async function applyContactTagSettings(tagId: string, input: { dryRun: boolean }): Promise<ApplyResult> {
  const response = await apiFetch(`${base}/${encodeURIComponent(tagId)}/apply`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ dry_run: input.dryRun }),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to apply tag settings'));
  return (await response.json()) as ApplyResult;
}
