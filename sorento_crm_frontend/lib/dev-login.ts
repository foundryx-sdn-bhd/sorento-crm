/**
 * DEV-LOGIN-BYPASS frontend guard (documentation/plans/identity/PLAN-dev-login-bypass-03oct.md).
 *
 * Server-only: reads `DEV_AUTO_LOGIN` and `DEV_AUTO_LOGIN_SECRET`, which are deliberately NOT
 * `NEXT_PUBLIC_*` values so they are never inlined into the browser bundle. Every check must
 * hold (fail closed):
 *   1. `DEV_AUTO_LOGIN === 'true'`
 *   2. `NODE_ENV !== 'production'` - a production build cannot carry the bypass
 *   3. `DEV_AUTO_LOGIN_SECRET` is set (16+ chars); the server sends it to the backend, which
 *      refuses without it, so a browser request relayed by the `/api/v1` rewrite cannot sign in
 *   4. the dev server is bound to 127.0.0.1 only (`next dev -H 127.0.0.1`). The Host header is
 *      whatever the caller says, so only the bind keeps LAN / Tailscale peers out
 *      (security review B2). Next records the bind in `__NEXT_PRIVATE_ORIGIN`, and it reads
 *      `localhost` when bound to every interface, so only `127.0.0.1` counts.
 *   5. the browser's Host is `localhost`, `*.localhost` or `127.0.0.1` (stops DNS rebinding)
 * The backend repeats its own guards; this is the outer layer, not the only one.
 */

export const DEV_LOGIN_SECRET_HEADER = 'X-Dev-Login-Secret';
const MIN_SECRET_LENGTH = 16;

type Env = Record<string, string | undefined>;

/** Flag, build mode and secret (no request). Decides whether the provider is registered at all. */
export function devLoginConfigured(env: Env = process.env): boolean {
  return (
    env.DEV_AUTO_LOGIN === 'true' &&
    env.NODE_ENV !== 'production' &&
    (env.DEV_AUTO_LOGIN_SECRET ?? '').trim().length >= MIN_SECRET_LENGTH
  );
}

/** True only when the dev server listens on 127.0.0.1 alone (`next dev -H 127.0.0.1`). */
export function boundToLoopback(env: Env = process.env): boolean {
  const origin = env.__NEXT_PRIVATE_ORIGIN;
  if (!origin) return false;
  try {
    return new URL(origin).hostname === '127.0.0.1';
  } catch {
    return false;
  }
}

/** The header the frontend SERVER sends the backend; never sent to or from the browser. */
export function devLoginBackendHeaders(env: Env = process.env): Record<string, string> {
  return { [DEV_LOGIN_SECRET_HEADER]: (env.DEV_AUTO_LOGIN_SECRET ?? '').trim() };
}

/** `localhost`, `*.localhost` or `127.0.0.1`, with an optional numeric port. Mirrors the backend. */
export function isLocalHost(hostHeader: string | null | undefined): boolean {
  if (!hostHeader) return false;
  let host = hostHeader.trim().toLowerCase();
  if (host.startsWith('[')) return false;
  const colon = host.lastIndexOf(':');
  if (colon !== -1) {
    const port = host.slice(colon + 1);
    if (!/^\d+$/.test(port)) return false;
    host = host.slice(0, colon);
  }
  if (host === 'localhost' || host === '127.0.0.1') return true;
  if (host.endsWith('.localhost')) {
    const label = host.slice(0, -'.localhost'.length);
    return label.length > 0 && label.split('.').every((part) => /^[a-z0-9-]+$/.test(part));
  }
  return false;
}

/** Every frontend guard, for one request. */
export function devLoginAllowed(hostHeader: string | null | undefined, env: Env = process.env): boolean {
  return devLoginConfigured(env) && boundToLoopback(env) && isLocalHost(hostHeader);
}

/** Read the Host header from either a Fetch `Headers` or NextAuth's plain header record. */
export function hostFromHeaders(headers: unknown): string | null {
  if (!headers) return null;
  if (typeof (headers as Headers).get === 'function') {
    return (headers as Headers).get('host');
  }
  const value = (headers as Record<string, string | string[] | undefined>).host;
  if (Array.isArray(value)) return value[0] ?? null;
  return value ?? null;
}
