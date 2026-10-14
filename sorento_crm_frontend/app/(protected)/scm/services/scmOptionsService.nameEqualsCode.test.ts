import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { searchProductOptions } from './scmOptionsService';

function respond(data: { product_code: string; product_name: string }[]) {
  apiFetch.mockResolvedValue({ ok: true, json: async () => ({ data }) });
}

beforeEach(() => apiFetch.mockReset());

describe('searchProductOptions label (AC-3)', () => {
  it('is the code alone when the name equals the code', async () => {
    respond([{ product_code: '1861', product_name: '1861' }]);
    const [opt] = await searchProductOptions('');
    expect(opt.label).toBe('1861');
    expect(opt.value).toBe('1861');
  });

  it('keeps the joined text when the name differs', async () => {
    respond([{ product_code: 'SRT6641', product_name: 'FLOOR TRAP' }]);
    const [opt] = await searchProductOptions('');
    expect(opt.label).toBe('SRT6641 · FLOOR TRAP');
  });
});
