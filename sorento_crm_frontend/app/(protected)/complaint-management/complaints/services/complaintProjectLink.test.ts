/**
 * S6b - the complaint project picker's option mapping (AC-L3).
 *
 * What matters here is what reaches the SCREEN. The value has to be the UUID (it is what the
 * API stores) while every visible string is a project code and title, because the no-UUIDs-in-
 * the-UI rule is absolute and this mapper is the only place it can be broken.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();

vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  extractApiError: async () => 'Failed to load projects',
}));

import { searchProjectsForLink } from './complaintService';

function ok(rows: unknown[]) {
  return { ok: true, json: async () => ({ data: rows }) };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('searchProjectsForLink', () => {
  it('labels an option with the code and title, never the id', async () => {
    apiFetch.mockResolvedValue(
      ok([
        {
          id: '11111111-1111-1111-1111-111111111111',
          project_code: 'PRJ-000142',
          title: 'Residensi Damai Phase 1',
          developer_name: 'Damai Land Sdn Bhd',
          status_label: 'Quoted',
        },
      ]),
    );

    const options = await searchProjectsForLink('damai');

    expect(options).toHaveLength(1);
    expect(options[0].value).toBe('11111111-1111-1111-1111-111111111111');
    expect(options[0].label).toBe('PRJ-000142 - Residensi Damai Phase 1');
    expect(options[0].label).not.toContain('1111');
    expect(options[0].description).toBe('Damai Land Sdn Bhd - Quoted');
  });

  it('omits the description rather than printing empty separators', async () => {
    apiFetch.mockResolvedValue(
      ok([
        {
          id: '22222222-2222-2222-2222-222222222222',
          project_code: 'PRJ-000200',
          title: 'Unnamed developer project',
          developer_name: null,
          status_label: null,
        },
      ]),
    );

    const options = await searchProjectsForLink('');
    expect(options[0].description).toBeUndefined();
  });

  it('server-searches instead of filtering a pre-loaded list', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await searchProjectsForLink('  mutiara  ', 2);

    const url = String(apiFetch.mock.calls[0][0]);
    expect(url).toContain('query=mutiara');
    expect(url).toContain('page=2');
    // Sorted newest-touched first: the project somebody is complaining about today is
    // almost always one that was worked on recently.
    expect(url).toContain('sort=updated_at');
  });

  it('sends no query param at all for an empty search', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await searchProjectsForLink('   ');
    expect(String(apiFetch.mock.calls[0][0])).not.toContain('query=');
  });

  it('surfaces a failure instead of returning an empty list', async () => {
    apiFetch.mockResolvedValue({ ok: false, json: async () => ({}) });
    await expect(searchProjectsForLink('x')).rejects.toThrow(/Failed to load projects/);
  });
});
