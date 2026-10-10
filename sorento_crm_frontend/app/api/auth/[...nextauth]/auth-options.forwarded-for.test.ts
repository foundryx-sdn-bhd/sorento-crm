/**
 * SORENTO-CLIENT-IP: the sign-in providers pass the browser's `X-Forwarded-For` on to the backend,
 * so its per-IP throttles see the real client instead of this server's own address.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Authorize = (c: Record<string, string> | undefined, req: unknown) => Promise<unknown>;
type ProviderShape = { id: string; options?: { id?: string; authorize?: Authorize } };

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'no' }), { status: 401 }));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

async function authorizeFor(id: string): Promise<Authorize> {
  vi.resetModules();
  vi.stubEnv('FASTAPI_INTERNAL_URL', 'http://localhost:8101');
  const options = (await import('./auth-options')).default;
  const provider = (options.providers as ProviderShape[]).find((p) => (p.options?.id ?? p.id) === id);
  return provider!.options!.authorize!;
}

describe.each([
  ['credentials', { email: 'a@example.com', password: 'pw' }, '/api/v1/auth/login'],
  ['phone-otp', { phone: '+60123456789', code: '123456' }, '/api/v1/auth/phone/verify'],
])('%s authorize', (id, credentials, path) => {
  it('forwards the browser X-Forwarded-For to the backend', async () => {
    const authorize = await authorizeFor(id);
    await authorize(credentials, { headers: { 'x-forwarded-for': '6.6.6.6, 203.0.113.7' } }).catch(() => null);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`http://localhost:8101${path}`);
    expect((init as RequestInit).headers).toMatchObject({ 'X-Forwarded-For': '6.6.6.6, 203.0.113.7' });
  });

  it('sends no X-Forwarded-For when the browser request had none', async () => {
    const authorize = await authorizeFor(id);
    await authorize(credentials, { headers: {} }).catch(() => null);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).not.toHaveProperty('X-Forwarded-For');
  });
});
