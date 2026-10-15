import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));
const { apiFetch } = await import('@/lib/api');
const mockFetch = apiFetch as unknown as ReturnType<typeof vi.fn>;

import { getReserveNoticeRecipients } from './orderInquiryReserveService';

beforeEach(() => mockFetch.mockReset());

describe('getReserveNoticeRecipients (S2)', () => {
  it('requests the recipients route with the staged row ids as one comma list', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ to: { name: 'Joey' }, cc: [] }),
    } as unknown as Response);

    const result = await getReserveNoticeRecipients('oi-1', ['r1', 'r2']);

    const url = new URL(mockFetch.mock.calls[0][0] as string, 'http://test.local');
    expect(url.pathname).toBe('/api/v1/project-sales/order-inquiries/oi-1/reserve-notices/recipients');
    // URLSearchParams encodes the comma as %2C; either way it decodes to r1,r2.
    expect(url.searchParams.get('row_ids')).toBe('r1,r2');
    expect(result).toEqual({ to: { name: 'Joey' }, cc: [] });
  });
});
