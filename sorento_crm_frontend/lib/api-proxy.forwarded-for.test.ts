/**
 * SORENTO-CLIENT-IP: Next route handlers that proxy to FastAPI (signup, reset-password, ...)
 * pass the browser's `X-Forwarded-For` on, so the backend's per-IP limits see the real client.
 */
import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { proxyToFastAPI } from './api-proxy';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  vi.stubEnv('FASTAPI_INTERNAL_URL', 'http://localhost:8101');
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('proxyToFastAPI', () => {
  it('forwards X-Forwarded-For', async () => {
    const req = new NextRequest('http://localhost:3000/api/auth/signup', {
      method: 'POST',
      headers: { 'x-forwarded-for': '6.6.6.6, 203.0.113.7' },
      body: '{}',
    });
    await proxyToFastAPI(req, '/api/v1/auth/signup', { requireAuth: false });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ 'X-Forwarded-For': '6.6.6.6, 203.0.113.7' });
  });

  it('adds none when the browser request had none', async () => {
    const req = new NextRequest('http://localhost:3000/api/auth/signup', { method: 'POST', body: '{}' });
    await proxyToFastAPI(req, '/api/v1/auth/signup', { requireAuth: false });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).not.toHaveProperty('X-Forwarded-For');
  });
});
