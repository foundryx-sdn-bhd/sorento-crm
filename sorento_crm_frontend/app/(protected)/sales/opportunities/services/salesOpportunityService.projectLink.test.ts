import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import {
  createSalesOpportunity,
  getSalesOpportunities,
  updateSalesOpportunity,
} from './salesOpportunityService';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => apiFetch.mockReset());

describe('sales opportunity project link', () => {
  it('list sends project_id', async () => {
    apiFetch.mockResolvedValue(json({ data: [], pagination: { total: 0, page: 1, limit: 50 }, empty: true }));
    await getSalesOpportunities({ pageIndex: 0, pageSize: 50, projectId: 'p-1' });
    expect(apiFetch.mock.calls[0][0]).toContain('project_id=p-1');
  });
  it('create (POST) and update (PATCH) carry project_id', async () => {
    apiFetch.mockImplementation(async () => json({ id: 'o1' }));
    await createSalesOpportunity({ title: 'T', project_id: 'p-1' });
    expect(apiFetch.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(apiFetch.mock.calls[0][1].body).project_id).toBe('p-1');
    await updateSalesOpportunity('o1', { project_id: null });
    expect(apiFetch.mock.calls[1][1].method).toBe('PATCH');
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ project_id: null });
  });
  it('a refused project throws the coded error', async () => {
    apiFetch.mockResolvedValue(
      json({ code: 'PROJECT_LINK_NOT_FOUND', message: 'That project was not found.' }, 422),
    );
    const err = await createSalesOpportunity({ project_id: 'x' }).catch((e) => e);
    expect(err.code).toBe('PROJECT_LINK_NOT_FOUND');
  });
});
