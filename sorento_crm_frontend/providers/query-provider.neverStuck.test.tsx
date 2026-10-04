/**
 * L3 (NEVER-STUCK-UI S2.2, S4.3): a refused request is final.
 *
 * - 401 / 403 / 404 and a timed-out request are never retried, whatever `retry` the hook
 *   names. 250 hooks set their own `retry: N`, which used to replace the provider default
 *   wholesale, so the rule has to hold for them too, not only for the default.
 * - Any other failure keeps the hook's own retry budget.
 * - The shared toast never shows a raw backend permission string: every 403 shape
 *   (including `Module not enabled:` and the strict-mode variant) collapses into the one
 *   friendly, deduped permission toast.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { toastCustom } = vi.hoisted(() => ({ toastCustom: vi.fn() }));
vi.mock('@/lib/toast', () => ({
  toast: { custom: toastCustom, dismiss: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

import { createAppQueryClient } from './query-provider';
import { REQUEST_TIMED_OUT_MESSAGE } from '@/lib/api-client';

beforeEach(() => {
  toastCustom.mockReset();
});

async function callsFor(message: string, retry?: number | boolean) {
  const client = createAppQueryClient();
  const queryFn = vi.fn(async () => {
    throw new Error(message);
  });
  await client
    .fetchQuery({
      queryKey: ['t', message, String(retry)],
      queryFn,
      ...(retry === undefined ? {} : { retry }),
      retryDelay: 0,
    })
    .catch(() => undefined);
  return queryFn.mock.calls.length;
}

describe('retry rule', () => {
  it.each([
    'Permission required: scm.dashboard.view',
    'One of these permissions required (module may be disabled): a.b',
    'Module not enabled: scm',
    'Session expired',
    'Order not found',
    REQUEST_TIMED_OUT_MESSAGE,
  ])('"%s" is fetched once even when the hook asks for retry: 3', async (m) => {
    expect(await callsFor(m, 3)).toBe(1);
  });

  it('a 5xx keeps the hook\'s own retry budget', async () => {
    expect(await callsFor('Server error. Try again or contact support.', 2)).toBe(3);
  });

  it('retry: false stays false', async () => {
    expect(await callsFor('Server error. Try again or contact support.', false)).toBe(1);
  });

  it('the provider default retries a 5xx once', async () => {
    const client = createAppQueryClient();
    const queryFn = vi.fn(async () => {
      throw new Error('Server error. Try again or contact support.');
    });
    // fetchQuery defaults retry to false; an observer-style query uses the default, so
    // read the defaulted options the cache would build.
    const opts = client.defaultQueryOptions({ queryKey: ['d'], queryFn });
    const retry = opts.retry as (n: number, e: Error) => boolean;
    expect(typeof retry).toBe('function');
    expect(retry(0, new Error('Server error.'))).toBe(true);
    expect(retry(1, new Error('Server error.'))).toBe(false);
    expect(retry(0, new Error('Permission required: a.b'))).toBe(false);
  });
});

describe('permission toast', () => {
  it.each([
    'Permission required: scm.dashboard.view',
    'One of these permissions required (module may be disabled): a.b',
    'Module not enabled: scm',
  ])('"%s" shows the one friendly toast, never the raw string', async (m) => {
    const client = createAppQueryClient();
    await client
      .fetchQuery({ queryKey: ['p', m], queryFn: async () => { throw new Error(m); } })
      .catch(() => undefined);
    expect(toastCustom).toHaveBeenCalledTimes(1);
    expect(toastCustom.mock.calls[0][1]).toMatchObject({ id: 'permission-denied' });
  });
});
