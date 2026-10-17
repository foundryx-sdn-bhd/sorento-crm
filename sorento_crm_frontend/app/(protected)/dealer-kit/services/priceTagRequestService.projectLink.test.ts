import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import {
  listPriceTagRequests,
  updatePriceTagPrintBy,
  updatePriceTagRequestFields,
} from './priceTagRequestService';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => apiFetch.mockReset());

describe('price tag request project link', () => {
  it('list sends project_id', async () => {
    apiFetch.mockResolvedValue(json({ data: [], pagination: { total: 0, page: 1, limit: 50 } }));
    await listPriceTagRequests({ page: 1, limit: 50, project_id: 'p-1' });
    expect(apiFetch.mock.calls[0][0]).toContain('project_id=p-1');
  });
  it('the office PATCH carries only the fields named', async () => {
    apiFetch.mockImplementation(async () => json({}));
    await updatePriceTagRequestFields('r1', { project_id: 'p-1' });
    expect(apiFetch.mock.calls[0][1].method).toBe('PATCH');
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ project_id: 'p-1' });
    await updatePriceTagRequestFields('r1', { project_id: null });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ project_id: null });
  });
  it('print_by still saves as before', async () => {
    apiFetch.mockResolvedValue(json({}));
    await updatePriceTagPrintBy('r1', 'office');
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ print_by: 'office' });
  });
  it('a refused project throws the coded error', async () => {
    apiFetch.mockResolvedValue(
      json({ code: 'PROJECT_LINK_NOT_FOUND', message: 'That project was not found.' }, 422),
    );
    const err = await updatePriceTagRequestFields('r1', { project_id: 'x' }).catch((e) => e);
    expect(err.code).toBe('PROJECT_LINK_NOT_FOUND');
  });
});
