/**
 * Translation memory admin list (AC-G4, purchasing consolidation batch, lane C).
 *
 * ---------------------------------------------------------------------------
 * API CONTRACT (as implemented) - app/api/v1/system/translations.py
 * ---------------------------------------------------------------------------
 * GET    /api/v1/system/translations?page&limit&query&sort&dir&target_lang
 *   -> { data: Translation[], pagination: {total,page,limit}, empty }
 *   Needs `system.translations.view`.
 *
 * PUT    /api/v1/system/translations/{id}   body { target_text } -> Translation.
 *   Always writes `source: 'manual'` from here on (a correction outranks any AI
 *   guess). Needs `system.translations.edit`.
 *
 * No create route: a row is written by the upload preview (a manual edit) or the AI
 * fill, never by hand here - this page only reads, corrects or removes one. Delete
 * runs through the deferred-action pending-actions route
 * (`translation_memory.delete`, `useDeferredRowAction`), not a plain DELETE call from
 * this file.
 * ---------------------------------------------------------------------------
 */

import type { SortingState } from '@tanstack/react-table';
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { Translation, TranslationUpdateBody } from '../types/translation.types';

const BASE = '/api/v1/system/translations';

export interface TranslationListQuery {
  pageIndex: number;
  pageSize: number;
  searchQuery?: string;
  sorting?: SortingState;
  /** The language a row translates INTO: `en` supplier memory, `ms` / `zh` chatbot wording. */
  targetLang?: string;
}

export interface TranslationPage {
  data: Translation[];
  pagination: { total: number; page: number; limit: number };
  empty?: boolean;
}

export async function listTranslations(q: TranslationListQuery): Promise<TranslationPage> {
  const params = buildDataGridParams({
    pageIndex: q.pageIndex,
    pageSize: q.pageSize,
    sorting: q.sorting ?? [],
    searchQuery: q.searchQuery ?? '',
  }, { target_lang: q.targetLang || undefined });
  const response = await apiFetch(`${BASE}?${params.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load translations'));
  }
  return response.json();
}

export async function updateTranslation(
  id: string,
  body: TranslationUpdateBody,
): Promise<Translation> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update the translation'));
  }
  return response.json();
}
