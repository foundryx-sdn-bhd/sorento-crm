/** DEV-LOGIN-BYPASS: the picker's users route 404s unless every FE guard holds (AC-01, AC-09). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const fetchMock = vi.fn();

async function get(host: string, env: Record<string, string | undefined>) {
  vi.resetModules();
  const base = {
    FASTAPI_INTERNAL_URL: 'http://localhost:8101',
    DEV_AUTO_LOGIN_SECRET: 'fe-server-secret-0123456789',
    __NEXT_PRIVATE_ORIGIN: 'http://127.0.0.1:3101',
  };
  for (const [k, v] of Object.entries({ ...base, ...env })) vi.stubEnv(k, v as string);
  const { GET } = await import('./route');
  return GET(new NextRequest('http://localhost/api/auth/dev-login/users', { headers: { host } }));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const ON = { DEV_AUTO_LOGIN: 'true', NODE_ENV: 'development' };

describe('GET /api/auth/dev-login/users', () => {
  it('relays name, email and role name only', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ users: [{ email: 'a@example.com', name: 'A', role_name: 'Admin', id: 'leak' }] }), { status: 200 }),
    );
    const res = await get('lane-a.localhost:3101', ON);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ users: [{ email: 'a@example.com', name: 'A', role_name: 'Admin' }] });
    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:8101/api/v1/auth/dev-login/users');
    expect(fetchMock.mock.calls[0][1].headers['X-Dev-Login-Secret']).toBe('fe-server-secret-0123456789');
  });

  it.each([
    ['flag off', 'localhost:3000', { DEV_AUTO_LOGIN: undefined, NODE_ENV: 'development' }],
    ['production build', 'localhost:3000', { DEV_AUTO_LOGIN: 'true', NODE_ENV: 'production' }],
    ['non-local host', 'sorento.example.com', ON],
    ['no secret', 'localhost:3000', { ...ON, DEV_AUTO_LOGIN_SECRET: undefined }],
    ['bound to every interface', 'localhost:3000', { ...ON, __NEXT_PRIVATE_ORIGIN: 'http://localhost:3101' }],
  ])('kill: %s -> 404 without calling the backend', async (_n, host, env) => {
    const res = await get(host, env);
    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('a backend 404 stays a 404', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 404 }));
    expect((await get('localhost:3000', ON)).status).toBe(404);
  });
});
