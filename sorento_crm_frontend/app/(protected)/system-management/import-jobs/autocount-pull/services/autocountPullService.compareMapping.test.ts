/** autocountPullService - compare mapping calls (DO-COMPARE-SIM, AC-CMM-1/2). */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { getCompareMappings, saveCompareMapping } from './autocountPullService';

beforeEach(() => apiFetch.mockReset());

describe('compare mapping service', () => {
  it('getCompareMappings GETs the list', async () => {
    const payload = { items: [{ kind: 'order_listing', sheet_name: 'Master', columns: [] }] };
    apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => payload } as unknown as Response);
    await expect(getCompareMappings()).resolves.toEqual(payload);
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/autocount/pulls/compare-mappings');
  });

  it('saveCompareMapping PUTs the body to the kind', async () => {
    const body = { sheet_name: 'Master', columns: [{ excel_header: 'Doc No', transform: 'text', field: 'doc_no' }] };
    apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ kind: 'order_listing', ...body }) } as unknown as Response);
    await saveCompareMapping('order_listing', body);
    const [url, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/autocount/pulls/compare-mappings/order_listing');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual(body);
  });

  it('saveCompareMapping throws the extracted 422 message', async () => {
    const err = { message: 'Map doc_no.', code: 'INVALID_MAPPING' };
    apiFetch.mockResolvedValue({
      ok: false, status: 422, headers: { get: () => 'application/json' },
      json: async () => err, text: async () => JSON.stringify(err),
      clone() { return this; },
    } as unknown as Response);
    await expect(
      saveCompareMapping('order_listing', { sheet_name: 'Master', columns: [] }),
    ).rejects.toThrow('Map doc_no.');
  });
});
