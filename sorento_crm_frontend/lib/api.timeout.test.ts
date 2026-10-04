/**
 * L4 (NEVER-STUCK-UI S2.1): every `apiFetch` request has a deadline.
 *
 * Before this, `apiFetch` and the shared `/api/auth/token` fetch called a bare `fetch`
 * with no signal, so a hung backend or proxy left every query `pending` (a skeleton that
 * never ends) and one hung token fetch froze every call in the app, because they all
 * await the same in-flight promise.
 *
 * The deadline is on the server ANSWERING (response headers), not on reading the body:
 * a large export or an event stream that has started answering is not cut off.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  apiFetch,
  clearCachedAuthToken,
  API_READ_TIMEOUT_MS,
  API_WRITE_TIMEOUT_MS,
  API_UPLOAD_TIMEOUT_MS,
  TOKEN_FETCH_TIMEOUT_MS,
} from './api';
import { REQUEST_TIMED_OUT_MESSAGE } from './api-client';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** A fetch that never answers until its signal aborts, like a hung proxy. */
function hangingUntilAborted(_input: RequestInfo, init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const signal = init?.signal;
    if (!signal) return; // no signal: hangs forever, which is the defect
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
      return;
    }
    signal.addEventListener('abort', () =>
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError')),
    );
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  clearCachedAuthToken();
  fetchMock = vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
    return hangingUntilAborted(input, init);
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearCachedAuthToken();
});

/** Settle a promise into a tagged result so a pending one can be observed. */
function track<T>(p: Promise<T>) {
  const state: { done: boolean; value?: T; error?: unknown } = { done: false };
  p.then(
    (value) => Object.assign(state, { done: true, value }),
    (error) => Object.assign(state, { done: true, error }),
  );
  return state;
}

describe('apiFetch deadline', () => {
  it('the budgets are the standard ones', () => {
    expect(API_READ_TIMEOUT_MS).toBe(30_000);
    expect(API_WRITE_TIMEOUT_MS).toBe(120_000);
    expect(TOKEN_FETCH_TIMEOUT_MS).toBe(10_000);
    expect(API_UPLOAD_TIMEOUT_MS).toBeGreaterThanOrEqual(600_000);
  });

  it('a GET that never answers fails after 30s with a readable message', async () => {
    const s = track(apiFetch('/api/v1/master-data/products'));
    await vi.advanceTimersByTimeAsync(API_READ_TIMEOUT_MS - 1);
    expect(s.done).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    expect(s.done).toBe(true);
    expect(s.error).toBeInstanceOf(Error);
    expect((s.error as Error).message).toBe(REQUEST_TIMED_OUT_MESSAGE);
  });

  it('a write gets 120s, not 30s', async () => {
    const s = track(apiFetch('/api/v1/orders', { method: 'POST', body: '{}' }));
    await vi.advanceTimersByTimeAsync(API_READ_TIMEOUT_MS + 1);
    expect(s.done).toBe(false);
    await vi.advanceTimersByTimeAsync(API_WRITE_TIMEOUT_MS);
    expect(s.done).toBe(true);
    expect((s.error as Error).message).toBe(REQUEST_TIMED_OUT_MESSAGE);
  });

  it('an upload (FormData body) gets the long upload budget', async () => {
    const fd = new FormData();
    fd.append('file', new Blob(['x']), 'x.xlsx');
    const s = track(apiFetch('/api/v1/imports', { method: 'POST', body: fd }));
    await vi.advanceTimersByTimeAsync(API_WRITE_TIMEOUT_MS + 1);
    expect(s.done).toBe(false);
    await vi.advanceTimersByTimeAsync(API_UPLOAD_TIMEOUT_MS);
    expect(s.done).toBe(true);
  });

  it.each([
    '/api/v1/scm/proforma-invoices/abc/export',
    '/api/v1/system/jobs/abc/rows/export?status=failed',
    '/api/v1/autocount/pulls/abc/download.xlsx',
    '/api/v1/sales-orders/abc/pdf',
  ])('a GET that builds a file (%s) gets the write budget', async (url) => {
    const s = track(apiFetch(url));
    await vi.advanceTimersByTimeAsync(API_READ_TIMEOUT_MS + 1);
    expect(s.done).toBe(false);
    await vi.advanceTimersByTimeAsync(API_WRITE_TIMEOUT_MS);
    expect(s.done).toBe(true);
  });

  it('an ordinary list read whose name only contains "export" text is still a read', async () => {
    const s = track(apiFetch('/api/v1/procurement/exporters'));
    await vi.advanceTimersByTimeAsync(API_READ_TIMEOUT_MS + 1);
    expect(s.done).toBe(true);
  });

  it('a caller can name its own budget', async () => {
    const s = track(apiFetch('/api/v1/reports/export', { timeoutMs: 5_000 }));
    await vi.advanceTimersByTimeAsync(5_001);
    expect(s.done).toBe(true);
    expect((s.error as Error).message).toBe(REQUEST_TIMED_OUT_MESSAGE);
    // `timeoutMs` is ours, never forwarded to fetch.
    const sent = fetchMock.mock.calls.find((c) => String(c[0]).includes('/reports/export'));
    expect(sent?.[1]).not.toHaveProperty('timeoutMs');
  });

  it("a caller's own abort still reaches it as an AbortError, not a timeout", async () => {
    const controller = new AbortController();
    const s = track(apiFetch('/api/v1/master-data/products', { signal: controller.signal }));
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(s.done).toBe(true);
    expect((s.error as Error).name).toBe('AbortError');
    expect((s.error as Error).message).not.toBe(REQUEST_TIMED_OUT_MESSAGE);
  });

  it('an answer that arrives in time is returned untouched and the deadline is cleared', async () => {
    let sentSignal: AbortSignal | undefined;
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
      sentSignal = init?.signal ?? undefined;
      return json({ ok: 1 });
    });
    const res = await apiFetch('/api/v1/master-data/products');
    expect(res.status).toBe(200);
    // Reading the body long after the read budget still works: the deadline covers
    // the answer arriving, not the body being consumed. A fired deadline would abort the
    // signal the body (a download, an event stream) is read under.
    await vi.advanceTimersByTimeAsync(API_READ_TIMEOUT_MS * 2);
    expect(sentSignal).toBeDefined();
    expect(sentSignal!.aborted).toBe(false);
    expect(await res.json()).toEqual({ ok: 1 });
  });

  it("after the answer arrives, the caller's own abort still reaches the body", async () => {
    let sentSignal: AbortSignal | undefined;
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
      sentSignal = init?.signal ?? undefined;
      return json({ ok: 1 });
    });
    const controller = new AbortController();
    await apiFetch('/api/v1/sla/conversation-events', { signal: controller.signal });
    controller.abort();
    expect(sentSignal!.aborted).toBe(true);
  });

  it("a Request's own signal is honoured", async () => {
    const controller = new AbortController();
    const s = track(
      apiFetch(new Request('http://localhost/api/v1/x', { signal: controller.signal })),
    );
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(s.done).toBe(true);
    expect((s.error as Error).name).toBe('AbortError');
  });
});

describe('auth token fetch deadline', () => {
  it('a hung /api/auth/token gives up after 10s instead of freezing every call', async () => {
    let apiSeen = false;
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return hangingUntilAborted(input, init);
      apiSeen = true;
      return json({});
    });
    const s = track(apiFetch('/api/v1/master-data/products'));
    await vi.advanceTimersByTimeAsync(TOKEN_FETCH_TIMEOUT_MS - 1);
    expect(apiSeen).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    // The token fetch settled (as "no token"); what happens to a token-less request is the
    // session lane's call (L1). What matters here is that nothing waits forever.
    expect(apiSeen || s.done).toBe(true);
  });
});
