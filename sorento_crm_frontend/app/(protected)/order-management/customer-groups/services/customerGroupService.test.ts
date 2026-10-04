import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch: api.apiFetch }));

import { getCustomerGroups } from './customerGroupService';

const base = { pageIndex: 0, pageSize: 50 };

beforeEach(() => {
  api.apiFetch.mockReset();
  api.apiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ data: [], pagination: { total: 0, page: 1, limit: 50 } }),
  });
});

describe('getCustomerGroups agent_mixed', () => {
  it('puts agent_mixed=true on the request URL', async () => {
    await getCustomerGroups({ ...base, agent_mixed: true });
    expect(api.apiFetch.mock.calls[0][0]).toContain('agent_mixed=true');
  });

  it('leaves it off when the filter is not set', async () => {
    await getCustomerGroups({ ...base, agent_mixed: false });
    expect(api.apiFetch.mock.calls[0][0]).not.toContain('agent_mixed');
  });
});
