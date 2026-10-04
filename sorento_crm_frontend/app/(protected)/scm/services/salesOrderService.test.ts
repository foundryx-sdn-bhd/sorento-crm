import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { createSalesOrder, getSalesOrders } from './salesOrderService';
import type { SalesOrderFormData } from '../types/scm.types';

function ok(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response;
}

beforeEach(() => apiFetch.mockReset());

describe('salesOrderService', () => {
  it('list forwards status + priority filters', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], empty: true, pagination: { total: 0, page: 1 } }));
    await getSalesOrders({
      pageIndex: 0,
      pageSize: 25,
      status: 'open',
      priority: 'high',
    });
    const u = new URL(String(apiFetch.mock.calls[0][0]), 'http://x');
    expect(u.pathname).toBe('/api/v1/scm/sales-orders');
    expect(u.searchParams.get('status')).toBe('open');
    expect(u.searchParams.get('priority')).toBe('high');
  });

  it('list forwards the customer filter as customer_code, the route\'s own param (was customer_id, a no-op since 8 Aug 2026)', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], empty: true, pagination: { total: 0, page: 1 } }));
    await getSalesOrders({
      pageIndex: 0,
      pageSize: 25,
      customerCode: 'C001',
    });
    const u = new URL(String(apiFetch.mock.calls[0][0]), 'http://x');
    expect(u.searchParams.get('customer_code')).toBe('C001');
    expect(u.searchParams.has('customer_id')).toBe(false);
  });

  it('list forwards the transferable filter (SO-TRANSFERABLE), and nothing when it is unset', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], empty: true, pagination: { total: 0, page: 1 } }));
    await getSalesOrders({ pageIndex: 0, pageSize: 25, transferable: 'no' });
    await getSalesOrders({ pageIndex: 0, pageSize: 25, transferable: null });
    const narrowed = new URL(String(apiFetch.mock.calls[0][0]), 'http://x');
    const all = new URL(String(apiFetch.mock.calls[1][0]), 'http://x');
    expect(narrowed.searchParams.get('transferable')).toBe('no');
    expect(all.searchParams.has('transferable')).toBe(false);
  });

  it('create forwards a line uom the caller set (9a730b5dc: uom is a real, editable field)', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'so-1' }, 201));
    const form: SalesOrderFormData = {
      order_type: 'dealer',
      customer_code: 'C-1',
      priority: 'normal',
      requested_delivery_date: '2026-08-01',
      lines: [{ sku: 'WESERP10B', qty_ordered: 5, uom: 'BAG' }],
    };
    await createSalesOrder(form);
    const [, init] = apiFetch.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.requested_delivery_date).toBe('2026-08-01');
    expect(body.lines).toEqual([{ sku: 'WESERP10B', qty_ordered: 5, uom: 'BAG' }]);
  });

  it('create omits uom for a line the caller never set, rather than sending it as null', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'so-1' }, 201));
    const form: SalesOrderFormData = {
      order_type: 'dealer',
      customer_code: 'C-1',
      priority: 'normal',
      requested_delivery_date: '2026-08-01',
      lines: [{ sku: 'WESERP10B', qty_ordered: 5 }],
    };
    await createSalesOrder(form);
    const [, init] = apiFetch.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.lines).toEqual([{ sku: 'WESERP10B', qty_ordered: 5 }]);
    expect(body.lines[0]).not.toHaveProperty('uom');
  });
});
