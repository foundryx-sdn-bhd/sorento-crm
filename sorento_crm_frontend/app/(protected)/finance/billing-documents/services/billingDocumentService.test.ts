/**
 * billingDocumentService: the contract with /api/v1/finance (plan 3.5; UAC S2-4, S2-8, S3-1,
 * S5-8). Paths and query params are asserted directly: a key typo is a silently dropped filter
 * on the backend, not an error.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
const searchCustomersSelect = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));
vi.mock('@/app/(protected)/order-management/customers/services/customerService', () => ({
  searchCustomersSelect,
}));

import {
  getBillingAgentOptions,
  getBillingDocument,
  getBillingDocuments,
  getCustomerOutstanding,
  searchBillingCustomerOptions,
} from './billingDocumentService';

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function failed(status: number, body: unknown) {
  return { ok: false, status, json: async () => body } as Response;
}

const BASE_PARAMS = { pageIndex: 0, pageSize: 20, sorting: [], searchQuery: '' };

beforeEach(() => {
  apiFetch.mockReset();
  searchCustomersSelect.mockReset();
});

describe('getBillingDocuments', () => {
  it('lists through buildDataGridParams, with every filter under its backend key', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0 }, empty: true }));

    await getBillingDocuments({
      ...BASE_PARAMS,
      sorting: [{ id: 'doc_date', desc: true }],
      searchQuery: 'IV-88',
      documentTypes: ['invoice', 'credit_note'],
      status: 'posted',
      customerId: 'cust-1',
      agentCode: 'AG-X',
      dateFrom: '2026-09-01',
      dateTo: '2026-09-30',
      openOnly: true,
      salesOrderId: 'so-1',
    });

    const [url] = apiFetch.mock.calls[0];
    expect(url).toContain('/api/v1/finance/billing-documents?');
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('page')).toBe('1');
    expect(qs.get('limit')).toBe('20');
    expect(qs.get('sort')).toBe('doc_date');
    expect(qs.get('dir')).toBe('desc');
    expect(qs.get('query')).toBe('IV-88');
    expect(qs.get('document_type')).toBe('invoice,credit_note');
    expect(qs.get('status')).toBe('posted');
    expect(qs.get('customer_id')).toBe('cust-1');
    expect(qs.get('agent_code')).toBe('AG-X');
    expect(qs.get('date_from')).toBe('2026-09-01');
    expect(qs.get('date_to')).toBe('2026-09-30');
    expect(qs.get('open_only')).toBe('true');
    expect(qs.get('sales_order_id')).toBe('so-1');
  });

  it('leaves unset filters off the query string, and open_only off when false', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0 }, empty: true }));

    await getBillingDocuments({ ...BASE_PARAMS, documentTypes: [], status: '', openOnly: false });

    const qs = new URLSearchParams(apiFetch.mock.calls[0][0].split('?')[1]);
    for (const key of [
      'document_type',
      'status',
      'customer_id',
      'agent_code',
      'date_from',
      'date_to',
      'open_only',
      'sales_order_id',
    ]) {
      expect(qs.has(key)).toBe(false);
    }
  });

  it('returns the response body as is', async () => {
    const body = { data: [{ id: 'd1', outstanding: null }], pagination: { total: 1 } };
    apiFetch.mockResolvedValue(ok(body));
    await expect(getBillingDocuments(BASE_PARAMS)).resolves.toEqual(body);
  });

  it('throws the extracted server message on a failure', async () => {
    apiFetch.mockResolvedValue(failed(403, { detail: { message: 'Permission required' } }));
    await expect(getBillingDocuments(BASE_PARAMS)).rejects.toThrow();
  });
});

describe('getBillingDocument', () => {
  it('reads one record by id', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'd1' }));
    await expect(getBillingDocument('d1')).resolves.toEqual({ id: 'd1' });
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/finance/billing-documents/d1');
  });

  it('throws on a 404', async () => {
    apiFetch.mockResolvedValue(failed(404, { detail: { message: 'Billing document not found.' } }));
    await expect(getBillingDocument('nope')).rejects.toThrow();
  });
});

describe('getCustomerOutstanding', () => {
  it('reads the customer total, null when never synced', async () => {
    const body = { customer_id: 'c1', ar_outstanding: null, ar_as_of: null };
    apiFetch.mockResolvedValue(ok(body));
    await expect(getCustomerOutstanding('c1')).resolves.toEqual(body);
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/finance/customers/c1/outstanding');
  });
});

describe('options', () => {
  it('reads agent options from the backend and unwraps data', async () => {
    apiFetch.mockResolvedValue(ok({ data: [{ value: 'AG-X', label: 'AG-X - Ali' }] }));
    await expect(getBillingAgentOptions()).resolves.toEqual([{ value: 'AG-X', label: 'AG-X - Ali' }]);
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/finance/billing-documents/agent-options');
  });

  it('reuses the customer select and keeps only value and label', async () => {
    searchCustomersSelect.mockResolvedValue([{ value: 'c1', label: 'Kedai Mine', extra: 'x' }]);
    await expect(searchBillingCustomerOptions('ked')).resolves.toEqual([
      { value: 'c1', label: 'Kedai Mine' },
    ]);
    expect(searchCustomersSelect).toHaveBeenCalledWith('ked');
  });
});
