/**
 * SESSION-NEVER-STUCK: a dead staff session ends in exactly ONE redirect to sign-in.
 *
 * The defect these pin: when `/api/auth/token` answered 401 (the NextAuth cookie gone,
 * undecodable, or overwritten by another localhost copy), apiFetch sent every
 * `/api/v1` call with no bearer, FastAPI answered a code-less 401, nothing redirected,
 * and the shell sat there with every widget failing (2 requests per call, forever).
 *
 * Each test imports fresh modules: the token cache and the "ending" latch are
 * module state, exactly as in a browser tab.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

vi.mock('next-auth/react', () => ({
  signOut: vi.fn(async () => undefined),
}));
const toastInfo = vi.hoisted(() => vi.fn());
vi.mock('@/lib/toast', () => ({ toast: { info: toastInfo } }));

const IMP_KEY = 'impersonation-session-v1';
const VIEW_AS = {
  sessionId: 's-1',
  startedAt: '2026-10-01T00:00:00',
  targetUser: { id: 'u-kx', name: 'Kah Xin', email: null, avatar: null, role_name: null },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const dead = (code: string) => json({ detail: { code, message: code } }, 401);

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;

async function load() {
  const api = await import('./api');
  const end = await import('./session-end');
  const imp = await import('./impersonation-store');
  assign = vi.fn();
  end.sessionNavigation.assign = assign as unknown as (url: string) => void;
  // The protected layout marks the signed-in shell; these run "inside" it unless a
  // test says otherwise.
  end.setSignedInShell(true);
  return { ...api, ...end, ...imp };
}

const tokenCalls = () =>
  fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/auth/token')).length;
const v1Calls = () =>
  fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/v1/')) as [
    RequestInfo,
    RequestInit | undefined,
  ][];
const bearerOf = (call: [RequestInfo, RequestInit | undefined]) =>
  new Headers(call[1]?.headers as HeadersInit).get('Authorization');

/** Let the fire-and-forget sign-out settle (it is capped, never awaited by callers). */
async function settle() {
  await vi.waitFor(() => expect(assign).toHaveBeenCalled());
}

beforeEach(async () => {
  const nextAuth = await import('next-auth/react');
  vi.mocked(nextAuth.signOut).mockClear();
  vi.mocked(nextAuth.signOut).mockImplementation(async () => undefined);
  vi.resetModules();
  toastInfo.mockClear();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/procurement-management/packing-lists/pl-1/lines?x=1');
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete process.env.NEXT_PUBLIC_BASE_PATH;
});

describe('token route says the session is gone (401)', () => {
  it('redirects to sign-in once with the return URL and sends no bearer-less API call', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'No valid session' }, 401);
      return json({ detail: 'Authentication required' }, 401);
    });
    const { apiFetch } = await load();

    const responses = await Promise.all([
      apiFetch('/api/v1/procurement/packing-lists/pl-1'),
      apiFetch('/api/v1/user-management/users/me/permissions'),
      apiFetch('/api/v1/notifications/unread-count'),
    ]);
    await settle();

    expect(responses.every((r) => r.status === 401)).toBe(true);
    expect(v1Calls()).toHaveLength(0);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(
      `/signin?callbackUrl=${encodeURIComponent('/procurement-management/packing-lists/pl-1/lines?x=1')}`,
    );
  });

  it('stops the request storm: later calls make no network request at all', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'No valid session' }, 401);
      return json({}, 200);
    });
    const { apiFetch } = await load();

    await apiFetch('/api/v1/a');
    await settle();
    const before = fetchMock.mock.calls.length;
    for (let i = 0; i < 10; i++) await apiFetch('/api/v1/b');

    expect(fetchMock.mock.calls.length).toBe(before);
    expect(tokenCalls()).toBe(1);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it('ends view-as cleanly: the persisted impersonation is cleared', async () => {
    window.localStorage.setItem(IMP_KEY, JSON.stringify(VIEW_AS));
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'No valid session' }, 401);
      return json({}, 200);
    });
    const { apiFetch, impersonationStore } = await load();
    expect(impersonationStore.getState()?.targetUser.id).toBe('u-kx');

    await apiFetch('/api/v1/a');
    await settle();

    expect(impersonationStore.getState()).toBeNull();
    expect(window.localStorage.getItem(IMP_KEY)).toBeNull();
  });

  it('outside the signed-in shell (a public page) it neither redirects nor blocks the call', async () => {
    // The daily-SLA unsubscribe link calls a no-login route through apiFetch.
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'No valid session' }, 401);
      return json({ unsubscribed: true }, 200);
    });
    const { apiFetch, setSignedInShell, isSessionEnding } = await load();
    setSignedInShell(false);

    const res = await apiFetch('/api/v1/notifications/daily-sla-summary/unsubscribe?token=t');

    expect(res.status).toBe(200);
    expect(v1Calls()).toHaveLength(1);
    expect(isSessionEnding()).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });

  it('a transient token failure (500) does NOT sign the user out', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'boom' }, 500);
      return json({ ok: true }, 200);
    });
    const { apiFetch, isSessionEnding } = await load();

    const res = await apiFetch('/api/v1/a');

    expect(res.status).toBe(200);
    expect(isSessionEnding()).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});

describe('FastAPI says the session is dead (401 with a session_* code)', () => {
  it('session_expired with an unchanged token: one redirect, view-as cleared', async () => {
    window.localStorage.setItem(IMP_KEY, JSON.stringify(VIEW_AS));
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok-old' });
      return dead('session_expired');
    });
    const { apiFetch, impersonationStore } = await load();

    await Promise.all(
      Array.from({ length: 10 }, (_, i) => apiFetch(`/api/v1/parallel-${i}`)),
    );
    await settle();

    // The latch is taken before any await: ten parallel 401s, ONE sign-out, ONE navigation.
    const nextAuth = await import('next-auth/react');
    expect(vi.mocked(nextAuth.signOut)).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(impersonationStore.getState()).toBeNull();
    // The refresh is ONE shared token read, not one per failed call.
    expect(tokenCalls()).toBeLessThanOrEqual(2);
  });

  it('a failed refresh (the cookie still holds the dead token) signs out, without a replay', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok-old' });
      return dead('session_revoked');
    });
    const { apiFetch } = await load();

    await apiFetch('/api/v1/a');
    await settle();

    expect(v1Calls()).toHaveLength(1);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it('replays with the fresh token when the cookie moved on (signed in again elsewhere)', async () => {
    let cookieToken = 'tok-old';
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return json({ token: cookieToken });
      const bearer = new Headers(init?.headers as HeadersInit).get('Authorization');
      if (bearer === 'Bearer tok-old') return dead('session_revoked');
      return json({ ok: true });
    });
    const { apiFetch } = await load();
    // Prime the cache with a token that still works for a first call.
    fetchMock.mockImplementationOnce(async () => json({ token: 'tok-old' }));
    fetchMock.mockImplementationOnce(async () => json({ ok: true }));
    await apiFetch('/api/v1/prime');

    cookieToken = 'tok-new';
    const res = await apiFetch('/api/v1/procurement/packing-lists/pl-1', { method: 'GET' });

    expect(res.status).toBe(200);
    const calls = v1Calls().filter((c) => String(c[0]).includes('pl-1'));
    expect(calls.map(bearerOf)).toEqual(['Bearer tok-old', 'Bearer tok-new']);
    expect(assign).not.toHaveBeenCalled();
  });

  it('a write is NOT replayed under a newer token (it may be another user), and no sign-out', async () => {
    let cookieToken = 'tok-old';
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return json({ token: cookieToken });
      const bearer = new Headers(init?.headers as HeadersInit).get('Authorization');
      if (bearer === 'Bearer tok-old') return dead('session_revoked');
      return json({ ok: true });
    });
    const { apiFetch, isSessionEnding } = await load();
    fetchMock.mockImplementationOnce(async () => json({ token: 'tok-old' }));
    fetchMock.mockImplementationOnce(async () => json({ ok: true }));
    await apiFetch('/api/v1/prime');

    cookieToken = 'tok-user-b';
    const res = await apiFetch('/api/v1/procurement/packing-lists/pl-1', {
      method: 'PUT',
      body: JSON.stringify({ notes: 'x' }),
    });

    expect(res.status).toBe(401);
    const writes = v1Calls().filter((c) => String(c[0]).includes('pl-1'));
    expect(writes.map(bearerOf)).toEqual(['Bearer tok-old']);
    expect(isSessionEnding()).toBe(false);
    expect(assign).not.toHaveBeenCalled();
    // The next submit carries the cookie's token.
    await apiFetch('/api/v1/procurement/packing-lists/pl-1', { method: 'PUT', body: '{}' });
    expect(bearerOf(v1Calls().at(-1)!)).toBe('Bearer tok-user-b');
  });

  it('HEAD is a read: replayed with the newer token', async () => {
    let cookieToken = 'tok-old';
    fetchMock.mockImplementation(async (input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) return json({ token: cookieToken });
      const bearer = new Headers(init?.headers as HeadersInit).get('Authorization');
      if (bearer === 'Bearer tok-old') return dead('session_revoked');
      return new Response(null, { status: 200 });
    });
    const { apiFetch } = await load();
    fetchMock.mockImplementationOnce(async () => json({ token: 'tok-old' }));
    fetchMock.mockImplementationOnce(async () => json({ ok: true }));
    await apiFetch('/api/v1/prime');

    cookieToken = 'tok-new';
    const res = await apiFetch('/api/v1/attachments/a-1', { method: 'HEAD' });

    expect(res.status).toBe(200);
    expect(assign).not.toHaveBeenCalled();
  });

  it('a replayed read that is still dead ends the session', async () => {
    let cookieToken = 'tok-old';
    let alive = true;
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: cookieToken });
      return alive ? json({ ok: true }) : dead('session_revoked');
    });
    const { apiFetch } = await load();
    await apiFetch('/api/v1/prime'); // caches tok-old

    alive = false;
    cookieToken = 'tok-newer-but-also-dead';
    await apiFetch('/api/v1/a');
    await settle();

    const calls = v1Calls().filter((c) => String(c[0]).endsWith('/a'));
    expect(calls.map(bearerOf)).toEqual(['Bearer tok-old', 'Bearer tok-newer-but-also-dead']);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it('no replay once another call has started ending the session', async () => {
    let reads = 0;
    let end: (() => void) | null = null;
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) {
        reads += 1;
        if (reads === 2) end?.(); // a parallel call trips the latch mid-refresh
        return json({ token: reads === 1 ? 'tok-old' : 'tok-new' });
      }
      return dead('session_expired');
    });
    const mods = await load();
    end = mods.endSessionAndRedirect;

    await mods.apiFetch('/api/v1/a');
    await settle();

    expect(v1Calls()).toHaveLength(1);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it('an RBAC 403 or a code-less 401 from one endpoint never signs out', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
      if (String(input).includes('/forbidden')) return json({ detail: 'Permission required: x' }, 403);
      return json({ detail: 'Invalid credentials.' }, 401);
    });
    const { apiFetch, isSessionEnding } = await load();

    await apiFetch('/api/v1/forbidden');
    await apiFetch('/api/v1/auth/change-password', { method: 'POST' });

    expect(isSessionEnding()).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});

describe('never hang', () => {
  it('a token request that never answers is abandoned, so apiFetch still settles', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((input: RequestInfo, init?: RequestInit) => {
      if (String(input).includes('/api/auth/token')) {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        });
      }
      return Promise.resolve(json({ ok: true }));
    });
    const { apiFetch, TOKEN_FETCH_TIMEOUT_MS } = await load();

    const pending = apiFetch('/api/v1/a');
    await vi.advanceTimersByTimeAsync(TOKEN_FETCH_TIMEOUT_MS + 10);
    const res = await pending;

    expect(res.status).toBe(200);
  });

  it('a sign-out that never answers still redirects (capped)', async () => {
    vi.useFakeTimers();
    const nextAuth = await import('next-auth/react');
    vi.mocked(nextAuth.signOut).mockImplementation(() => new Promise(() => undefined));
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ error: 'No valid session' }, 401);
      return json({}, 200);
    });
    const { apiFetch, SIGN_OUT_CAP_MS } = await load();

    await apiFetch('/api/v1/a');
    await vi.advanceTimersByTimeAsync(SIGN_OUT_CAP_MS + 10);

    expect(assign).toHaveBeenCalledTimes(1);
  });
});

describe('endSessionAndRedirect', () => {
  it('keeps the return URL app-relative under a base path, with a real path boundary', async () => {
    process.env.NEXT_PUBLIC_BASE_PATH = '/crm';
    const { signInUrl } = await load();

    window.history.replaceState(null, '', '/crm/procurement-management/packing-lists?x=1#h');
    expect(signInUrl()).toBe(
      `/crm/signin?callbackUrl=${encodeURIComponent('/procurement-management/packing-lists?x=1#h')}`,
    );
    window.history.replaceState(null, '', '/crmfoo/bar');
    expect(signInUrl()).toBe(`/crm/signin?callbackUrl=${encodeURIComponent('/crmfoo/bar')}`);
    window.history.replaceState(null, '', '/crm/signin');
    expect(signInUrl()).toBe('/crm/signin');
  });

  it('a cancelled navigation (a "leave site?" guard answered Stay) is retried, the latch never drops', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async () => json({ ok: true }));
    const { apiFetch, endSessionAndRedirect, isSessionEnding, REDIRECT_RETRY_MS } = await load();

    endSessionAndRedirect();
    await vi.advanceTimersByTimeAsync(0);
    expect(assign).toHaveBeenCalledTimes(1);

    // Too soon: a slow /signin compile must not restart anything.
    await apiFetch('/api/v1/a');
    expect(assign).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(REDIRECT_RETRY_MS + 10);
    const res = await apiFetch('/api/v1/b');

    expect(res.status).toBe(401);
    expect(isSessionEnding()).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalledTimes(2);
    const nextAuth = await import('next-auth/react');
    expect(vi.mocked(nextAuth.signOut)).toHaveBeenCalledTimes(1);
  });

  it('is idempotent and skips the return URL when already on sign-in', async () => {
    window.history.replaceState(null, '', '/signin');
    const { endSessionAndRedirect } = await load();

    endSessionAndRedirect();
    endSessionAndRedirect();
    await settle();

    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/signin');
  });
});

describe('stale view-as: the backend ignored X-Impersonate-User-Id', () => {
  const ended = (body: unknown = { ok: true }) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'X-Impersonation-Ended': '1' },
    });

  it('ends view-as locally, says so once, refetches, and does not sign out', async () => {
    window.localStorage.setItem(IMP_KEY, JSON.stringify(VIEW_AS));
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
      return ended();
    });
    const { apiFetch, impersonationStore, registerViewAsEndedHandler, isSessionEnding } = await load();
    const refetch = vi.fn();
    registerViewAsEndedHandler(refetch);

    await Promise.all([apiFetch('/api/v1/a'), apiFetch('/api/v1/b')]);

    const sent = v1Calls().map((c) => new Headers(c[1]?.headers as HeadersInit).get('X-Impersonate-User-Id'));
    expect(sent).toEqual(['u-kx', 'u-kx']);
    expect(impersonationStore.getState()).toBeNull();
    expect(window.localStorage.getItem(IMP_KEY)).toBeNull();
    expect(toastInfo).toHaveBeenCalledTimes(1);
    expect(toastInfo).toHaveBeenCalledWith('View-as ended - you are seeing your own data', {
      id: 'view-as-ended',
    });
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(isSessionEnding()).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });

  it('a late answer for an older view-as leaves the current one alone', async () => {
    window.localStorage.setItem(IMP_KEY, JSON.stringify(VIEW_AS));
    let release: (r: Response) => void = () => undefined;
    fetchMock.mockImplementation((input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return Promise.resolve(json({ token: 'tok' }));
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    const { apiFetch, impersonationStore } = await load();

    const pending = apiFetch('/api/v1/a');
    await vi.waitFor(() => expect(v1Calls()).toHaveLength(1));
    const next = { ...VIEW_AS, targetUser: { ...VIEW_AS.targetUser, id: 'u-other' } };
    impersonationStore.setSession(next);
    release(ended());
    await pending;

    expect(impersonationStore.getState()?.targetUser.id).toBe('u-other');
    expect(toastInfo).not.toHaveBeenCalled();
  });

  it('no signal, no change', async () => {
    window.localStorage.setItem(IMP_KEY, JSON.stringify(VIEW_AS));
    fetchMock.mockImplementation(async (input: RequestInfo) => {
      if (String(input).includes('/api/auth/token')) return json({ token: 'tok' });
      return json({ ok: true });
    });
    const { apiFetch, impersonationStore } = await load();

    await apiFetch('/api/v1/a');

    expect(impersonationStore.getState()?.targetUser.id).toBe('u-kx');
    expect(toastInfo).not.toHaveBeenCalled();
  });
});
