/**
 * CONTACT-TAGS S1, UAC T5.3: the contacts list's Tag filter and "No tags" checkbox as query
 * params, and back from the URL. Assumed exports from ./listQuery:
 *   contactsListFilters({ chatbotMemoryLevel, tagIds?: string[], noTags?: boolean })
 *   contactTagFiltersFromUrl(filters) -> { tagIds: string[]; noTags: boolean }
 * The wire contract is pinned at the fetch: repeated `tag=` params, `tags=none`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { contactsListFilters, contactTagFiltersFromUrl, fetchContactsPage } from './listQuery';

const base = { pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '' };

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ data: [], pagination: { total: 0 } }),
  });
});

async function urlFor(filters: Record<string, string>) {
  await fetchContactsPage({ ...base, filters } as never);
  return new URL(apiFetch.mock.calls[0][0], 'http://x');
}

describe('contacts list tag filters', () => {
  it('tag ids go out as repeated tag= params', async () => {
    const u = await urlFor(contactsListFilters({ chatbotMemoryLevel: null, tagIds: ['t1', 't2'] }));
    expect(u.searchParams.getAll('tag')).toEqual(['t1', 't2']);
    expect(u.searchParams.has('tags')).toBe(false);
  });

  it('"No tags" goes out as tags=none', async () => {
    const u = await urlFor(contactsListFilters({ chatbotMemoryLevel: null, noTags: true }));
    expect(u.searchParams.get('tags')).toBe('none');
    expect(u.searchParams.has('tag')).toBe(false);
  });

  it('no tag filter sends neither param', async () => {
    const u = await urlFor(contactsListFilters({ chatbotMemoryLevel: null }));
    expect(u.searchParams.has('tag')).toBe(false);
    expect(u.searchParams.has('tags')).toBe(false);
  });

  it('both parse back from the URL the list writes', () => {
    const filters = contactsListFilters({ chatbotMemoryLevel: null, tagIds: ['t1', 't2'], noTags: true });
    const search = buildDetailSearch(base, filters);
    const state = parseDetailSearch(new URLSearchParams(search));
    expect(contactTagFiltersFromUrl(state.filters)).toEqual({ tagIds: ['t1', 't2'], noTags: true });
  });
});
