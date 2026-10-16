import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { SettingsCatalog } from '../types/contactTag.types';

export const EMPTY_CATALOG: SettingsCatalog = {
  access_types: [],
  brands: [],
  companies: [],
  market_segments: [],
  attachment_types: [],
  warehouses: [],
  agents: [],
  field_reveals: [],
  portal_forms: [],
  spec_keys: [],
};

/** Every pick list the tag settings editor offers, in one read. */
export async function getSettingsCatalog(): Promise<SettingsCatalog> {
  const response = await apiFetch(
    '/api/user-management/contact-tags/settings-catalog',
  );
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to load tag setting options'),
    );
  const data = (await response.json()) as Partial<SettingsCatalog> | unknown[];
  if (!data || Array.isArray(data)) return EMPTY_CATALOG;
  return { ...EMPTY_CATALOG, ...data };
}
