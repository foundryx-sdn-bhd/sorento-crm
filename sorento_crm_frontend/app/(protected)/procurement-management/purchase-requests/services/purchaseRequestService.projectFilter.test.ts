import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { getPurchaseRequests } from './purchaseRequestService';
import { complaintListExtraParams } from '@/app/(protected)/complaint-management/complaints/services/complaintService';

beforeEach(() => apiFetch.mockReset());

describe('project_id list filter', () => {
  it('purchase requests send project_id with request_type', async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ data: [] }), { status: 200 }));
    await getPurchaseRequests({
      pageIndex: 0, pageSize: 50, sorting: [], searchQuery: '',
      requestType: 'sponsorship_form', projectId: 'p-1',
    });
    const url = apiFetch.mock.calls[0][0] as string;
    expect(url).toContain('project_id=p-1');
    expect(url).toContain('request_type=sponsorship_form');
  });
  it('complaint list params carry project_id', () => {
    const p = complaintListExtraParams({ pageIndex: 0, pageSize: 50, project_id: 'p-1' });
    expect(p.project_id).toBe('p-1');
  });
});
