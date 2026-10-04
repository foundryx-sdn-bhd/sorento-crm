/**
 * DEV-LOGIN-BYPASS: the `dev-login` NextAuth provider (AC-09). Registered only when configured;
 * authorize() refuses a non-local Host before any backend call and sends only the email.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Authorize = (c: Record<string, string> | undefined, req: unknown) => Promise<unknown>;

async function loadOptions(env: Record<string, string | undefined>) {
  vi.resetModules();
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v as string);
  return (await import('./auth-options')).default;
}

// next-auth v4 keeps a provider's own id on `options` until the handler merges it.
type ProviderShape = { id: string; options?: { id?: string; authorize?: Authorize } };
function devProvider(options: { providers: unknown[] }) {
  return (options.providers as ProviderShape[]).find((p) => (p.options?.id ?? p.id) === 'dev-login');
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const BASE = {
  FASTAPI_INTERNAL_URL: 'http://localhost:8101',
  DEV_AUTO_LOGIN_SECRET: 'fe-server-secret-0123456789',
  __NEXT_PRIVATE_ORIGIN: 'http://127.0.0.1:3101',
};

describe('dev-login provider registration', () => {
  it('is absent by default', async () => {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: undefined, NODE_ENV: 'development' });
    expect(devProvider(options)).toBeUndefined();
  });
  it('kill: absent in a production build even with the flag on', async () => {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: 'true', NODE_ENV: 'production' });
    expect(devProvider(options)).toBeUndefined();
  });
  it('kill: absent without the shared secret', async () => {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: 'true', NODE_ENV: 'development', DEV_AUTO_LOGIN_SECRET: undefined });
    expect(devProvider(options)).toBeUndefined();
  });
  it('is present with the flag on in dev', async () => {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: 'true', NODE_ENV: 'development' });
    expect(devProvider(options)).toBeDefined();
  });
});

describe('dev-login authorize', () => {
  async function authorize() {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: 'true', NODE_ENV: 'development' });
    return devProvider(options)!.options!.authorize!;
  }

  it('posts only the email and returns the backend session as apiToken', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: 'u1', email: 'a@example.com', name: 'A', status: 'ACTIVE', role_id: 'r1', role_ids: ['r1'], token: 'opaque-token', home_path: '/' }), { status: 200 }),
    );
    const user = (await (await authorize())({ email: 'a@example.com' }, { headers: { host: 'lane-a.localhost:3101' } })) as Record<string, unknown>;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://localhost:8101/api/v1/auth/dev-login');
    expect(JSON.parse(init.body)).toEqual({ email: 'a@example.com' });
    expect(init.headers['X-Dev-Login-Secret']).toBe(BASE.DEV_AUTO_LOGIN_SECRET);
    expect(user.apiToken).toBe('opaque-token');
    expect(user.email).toBe('a@example.com');
  });

  it.each([['sorento.example.com'], ['tehs-mac-mini:3000'], [undefined]])(
    'kill: Host %s refuses without calling the backend',
    async (host) => {
      const fn = await authorize();
      await expect(fn({ email: 'a@example.com' }, { headers: host ? { host } : {} })).rejects.toThrow();
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it('kill: a dev server bound to every interface refuses a spoofed localhost Host', async () => {
    const options = await loadOptions({ ...BASE, DEV_AUTO_LOGIN: 'true', NODE_ENV: 'development', __NEXT_PRIVATE_ORIGIN: 'http://localhost:3101' });
    const fn = devProvider(options)!.options!.authorize!;
    await expect(fn({ email: 'a@example.com' }, { headers: { host: 'localhost:3101' } })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('a backend refusal becomes a sign-in error, never a user', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 }));
    const fn = await authorize();
    await expect(fn({ email: 'a@example.com' }, { headers: { host: 'localhost:3000' } })).rejects.toThrow(/not available/);
  });
});
