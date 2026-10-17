import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { getStockInquiries, createStockInquiry, updateStockInquiry } from './stockInquiryService';
import { isProjectLinkError } from '@/lib/project-link';

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

beforeEach(() => apiFetch.mockReset());

describe('stock inquiry project link', () => {
  it('list sends project_id', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1 }, empty: true }));
    await getStockInquiries({ pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '', project_id: 'p-1' });
    expect(apiFetch.mock.calls[0][0]).toContain('project_id=p-1');
  });
  it('list omits project_id when not given', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1 }, empty: true }));
    await getStockInquiries({ pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '' });
    expect(apiFetch.mock.calls[0][0]).not.toContain('project_id');
  });
  it('create and update carry project_id in the body, null included', async () => {
    apiFetch.mockImplementation(async () => ok({ id: 's1' }));
    await createStockInquiry({ project_id: 'p-1' });
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ project_id: 'p-1' });
    await updateStockInquiry('s1', { project_id: null });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ project_id: null });
  });
  it('a refused project surfaces as a recognisable error', async () => {
    apiFetch.mockResolvedValue(
      new Response(
        JSON.stringify({ code: 'PROJECT_LINK_NOT_FOUND', message: 'That project was not found.' }),
        { status: 422, headers: { 'content-type': 'application/json' } },
      ),
    );
    const err = await updateStockInquiry('s1', { project_id: 'bad' }).catch((e) => e);
    expect(isProjectLinkError(err)).toBe(true);
    expect(err.message).toBe('That project was not found.');
  });
});
