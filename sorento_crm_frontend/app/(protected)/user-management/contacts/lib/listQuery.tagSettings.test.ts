/**
 * CONTACT-TAGS S2, UAC T4.2 / T5.3: the Tag settings select (Any / Matches / Differs) as a query param.
 * Assumed from ./listQuery: contactsListFilters({ chatbotMemoryLevel, tagSettings?: 'matches'|'differs'|null })
 * and contactTagSettingsFilterFromUrl(filters) -> 'matches' | 'differs' | null. Any = param absent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { contactsListFilters, contactTagSettingsFilterFromUrl, fetchContactsPage } from './listQuery';

const base = { pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '' };

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ data: [], pagination: { total: 0 } }) });
});

describe('contacts list tag_settings filter', () => {
  it('goes out as tag_settings=<value> and combines with tag', async () => {
    await fetchContactsPage({
      ...base,
      filters: contactsListFilters({ chatbotMemoryLevel: null, tagIds: ['t1'], tagSettings: 'differs' }),
    } as never);
    const u = new URL(apiFetch.mock.calls[0][0], 'http://x');
    expect(u.searchParams.get('tag_settings')).toBe('differs');
    expect(u.searchParams.getAll('tag')).toEqual(['t1']);
  });

  it('Any sends no tag_settings param', async () => {
    await fetchContactsPage({ ...base, filters: contactsListFilters({ chatbotMemoryLevel: null, tagSettings: null }) } as never);
    expect(new URL(apiFetch.mock.calls[0][0], 'http://x').searchParams.has('tag_settings')).toBe(false);
  });

  it('parses back from the URL the list writes', () => {
    const filters = contactsListFilters({ chatbotMemoryLevel: null, tagSettings: 'matches' });
    const state = parseDetailSearch(new URLSearchParams(buildDetailSearch(base, filters)));
    expect(contactTagSettingsFilterFromUrl(state.filters)).toBe('matches');
    expect(contactTagSettingsFilterFromUrl({})).toBeNull();
  });
});
