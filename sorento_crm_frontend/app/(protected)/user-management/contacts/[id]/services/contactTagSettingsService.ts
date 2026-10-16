import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface TagSettingsItem {
  section: string;
  key: string;
  label: string;
  current: unknown;
  expected: unknown;
  decided_by: string[];
  conflict: boolean;
  conflict_tags: { name: string; value: unknown }[];
  differs: boolean;
}

export interface ContactTagSettings {
  status: 'matches' | 'differs' | 'none';
  tags: { id: string; name: string }[];
  items: TagSettingsItem[];
}

/** GET /contacts/{id}/tag-settings: expected (merged across the contact's tags) against current. */
export async function getContactTagSettings(
  contactId: string,
): Promise<ContactTagSettings> {
  const response = await apiFetch(
    `/api/user-management/contacts/${encodeURIComponent(contactId)}/tag-settings`,
  );
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to load tag settings'),
    );
  return (await response.json()) as ContactTagSettings;
}
