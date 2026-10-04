/**
 * NS-SHARED-LOOKUPS (never-stuck L10): the people picker every module uses reads the open
 * `/users/lookup` route, not the admin `/users/select` one, and surfaces a refusal instead of
 * an empty list.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api';
import { getUserLookup } from './userSelectService';

const mockedFetch = vi.mocked(apiFetch);

function calledUrl(): URL {
  return new URL(mockedFetch.mock.calls[0][0] as string, 'http://localhost');
}

describe('getUserLookup', () => {
  beforeEach(() => mockedFetch.mockReset());

  it('reads the open lookup route with no params by default', async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [{ id: 'u1', name: 'Aida' }] } as Response);

    const rows = await getUserLookup();

    expect(calledUrl().pathname).toBe('/api/user-management/users/lookup');
    expect(calledUrl().search).toBe('');
    expect(rows).toEqual([{ id: 'u1', name: 'Aida' }]);
  });

  it('passes the name query and the Respond.io opt-in', async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] } as Response);

    await getUserLookup({ query: 'ai', respond_synced: true });

    expect(calledUrl().searchParams.get('query')).toBe('ai');
    expect(calledUrl().searchParams.get('respond_synced')).toBe('true');
  });

  it('asks for deactivated people only when a filter over past records says so', async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] } as Response);

    await getUserLookup({ include_inactive: true });

    expect(calledUrl().searchParams.get('include_inactive')).toBe('true');
  });

  it('throws the backend message instead of returning an empty list', async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      headers: new Headers({ 'content-type': 'application/json' }),
      status: 503,
      json: async () => ({ detail: 'Service unavailable' }),
    } as Response);

    await expect(getUserLookup()).rejects.toThrow('Service unavailable');
  });
});
