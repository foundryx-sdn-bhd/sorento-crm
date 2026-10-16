/**
 * CONTACT-TAGS S1 contract: the contact-tags service paths and verbs.
 * Assumed: services/contactTagService.ts exports getContactTags(params, extra?), getContactTag(id),
 * createContactTag(body), updateContactTag(id, body), deleteContactTag(id),
 * bulkTagContacts({contactIds, addTagIds, removeTagIds, dryRun}); built on apiFetch from '@/lib/api'
 * (paths carry the FE-side '/api/user-management/...' prefix, like every sibling service).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import {
  getContactTags,
  getContactTag,
  createContactTag,
  updateContactTag,
  deleteContactTag,
  bulkTagContacts,
} from './contactTagService';

function ok(body: unknown = {}, status = 200) {
  return { ok: true, status, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue(ok());
});

describe('contactTagService', () => {
  it('lists with buildDataGridParams page/limit/sort/dir/query and extra filters', async () => {
    await getContactTags(
      { pageIndex: 1, pageSize: 25, sorting: [{ id: 'name', desc: true }], searchQuery: 'dea' },
      { is_active: 'false' },
    );
    const [url] = apiFetch.mock.calls[0];
    const u = new URL(url, 'http://x');
    expect(u.pathname).toBe('/api/user-management/contact-tags');
    expect(u.searchParams.get('page')).toBe('2');
    expect(u.searchParams.get('limit')).toBe('25');
    expect(u.searchParams.get('sort')).toBe('name');
    expect(u.searchParams.get('dir')).toBe('desc');
    expect(u.searchParams.get('query')).toBe('dea');
    expect(u.searchParams.get('is_active')).toBe('false');
  });

  it('get / create / update / delete hit the contract paths and verbs', async () => {
    await getContactTag('t1');
    expect(apiFetch.mock.calls[0][0]).toBe('/api/user-management/contact-tags/t1');

    await createContactTag({ name: 'Dealer' });
    expect(apiFetch.mock.calls[1][0]).toBe('/api/user-management/contact-tags');
    expect(apiFetch.mock.calls[1][1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ name: 'Dealer' });

    await updateContactTag('t1', { name: 'Dealers' });
    expect(apiFetch.mock.calls[2][0]).toBe('/api/user-management/contact-tags/t1');
    expect(apiFetch.mock.calls[2][1]).toMatchObject({ method: 'PATCH' });

    await deleteContactTag('t1');
    expect(apiFetch.mock.calls[3][0]).toBe('/api/user-management/contact-tags/t1');
    expect(apiFetch.mock.calls[3][1]).toMatchObject({ method: 'DELETE' });
  });

  it('bulk-tags posts snake_case ids and the dry_run flag', async () => {
    await bulkTagContacts({ contactIds: ['c1'], addTagIds: ['a'], removeTagIds: [], dryRun: true });
    expect(apiFetch.mock.calls[0][0]).toBe('/api/user-management/contacts/bulk-tags');
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({
      contact_ids: ['c1'],
      add_tag_ids: ['a'],
      remove_tag_ids: [],
      dry_run: true,
    });
  });

  it('a failed response throws the extracted API message', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'A tag named "Dealer" already exists.' }),
      text: async () => '',
    } as unknown as Response);
    await expect(createContactTag({ name: 'dealer' })).rejects.toThrow(/Dealer/);
  });
});
