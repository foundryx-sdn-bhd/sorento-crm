/**
 * getSeriesPricedRows reads every priced row of a series in pages of 100 and stops at the
 * server total, or at an empty page.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const { apiFetch } = await import('@/lib/api');
const mockFetch = apiFetch as unknown as ReturnType<typeof vi.fn>;

import { getSeriesPricedRows } from './projectService';

const rows = (n: number, from = 0) =>
  Array.from({ length: n }, (_, i) => ({ product_id: `p${from + i}`, product_code: `C${from + i}` }));
const ok = (data: unknown[], total: number) =>
  ({ ok: true, json: async () => ({ data, pagination: { total, page: 1, limit: 100 } }) }) as Response;
const urlOf = (call: number) => new URL(mockFetch.mock.calls[call][0] as string, 'http://t.local');

beforeEach(() => mockFetch.mockReset());

describe('getSeriesPricedRows', () => {
  it('fetches page 1 and page 2 at limit 100 with priced=true, and stops at the total', async () => {
    mockFetch.mockResolvedValueOnce(ok(rows(100), 130)).mockResolvedValueOnce(ok(rows(30, 100), 130));
    const out = await getSeriesPricedRows('s1');
    expect(out).toHaveLength(130);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    for (const call of [0, 1]) {
      expect(urlOf(call).searchParams.get('priced')).toBe('true');
      expect(urlOf(call).searchParams.get('limit')).toBe('100');
    }
    expect(urlOf(0).searchParams.get('page')).toBe('1');
    expect(urlOf(1).searchParams.get('page')).toBe('2');
  });

  it('stops on an empty page even when the total promises more', async () => {
    mockFetch.mockResolvedValueOnce(ok(rows(100), 500)).mockResolvedValueOnce(ok([], 500));
    const out = await getSeriesPricedRows('s1');
    expect(out).toHaveLength(100);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});
