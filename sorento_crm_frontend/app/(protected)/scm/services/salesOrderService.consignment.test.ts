/** AC-CONSIGN-CRM UAC-18/20: getSalesOrders asks for consignments only when told to. */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { getSalesOrders } from './salesOrderService';

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => apiFetch.mockReset());

describe('getSalesOrders doc_type', () => {
  it('docType consignment sends doc_type=consignment', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], empty: true, pagination: { total: 0, page: 1 } }));
    await getSalesOrders({ pageIndex: 0, pageSize: 25, docType: 'consignment' } as Parameters<
      typeof getSalesOrders
    >[0]);
    const u = new URL(String(apiFetch.mock.calls[0][0]), 'http://x');
    expect(u.pathname).toBe('/api/v1/scm/sales-orders');
    expect(u.searchParams.get('doc_type')).toBe('consignment');
  });

  it('without docType no doc_type is sent', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], empty: true, pagination: { total: 0, page: 1 } }));
    await getSalesOrders({ pageIndex: 0, pageSize: 25 });
    const u = new URL(String(apiFetch.mock.calls[0][0]), 'http://x');
    expect(u.searchParams.has('doc_type')).toBe(false);
  });
});
