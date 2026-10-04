import { NextRequest } from 'next/server';

import { impersonationStore } from '@/lib/impersonation-store';
import { REQUEST_TIMED_OUT_MESSAGE } from '@/lib/api-client';
import {
  IMPERSONATION_ENDED_HEADER,
  endSessionAndRedirect,
  endViewAsLocally,
  isSessionEnding,
  isSignedInShell,
} from '@/lib/session-end';
import {
  REVISION_HEADER,
  clearRememberedRevisions,
  fencedWriteEntityId,
  handleRevisionConflict,
  harvestRevisions,
  isFencedReadPath,
  rememberedRevision,
} from '@/lib/revision-fence';

/**
 * Add the X-Impersonate-User-Id header to outgoing /api/v1/* requests when an
 * admin has an active impersonation session. The backend ignores the header
 * unless it can match an active row in `impersonation_sessions`.
 */
function _attachImpersonationHeader(url: unknown, init: RequestInit | undefined): RequestInit | undefined {
  if (typeof url !== 'string') return init;
  if (!url.includes('/api/v1/')) return init;
  const session = impersonationStore.getState();
  if (!session) return init;
  const next = init ? { ...init } : {};
  const isFormData = next.body instanceof FormData;
  if (next.headers instanceof Headers) {
    const cloned = new Headers(next.headers);
    cloned.set('X-Impersonate-User-Id', session.targetUser.id);
    next.headers = cloned;
  } else if (Array.isArray(next.headers)) {
    next.headers = [...next.headers.filter(([k]) => k.toLowerCase() !== 'x-impersonate-user-id'),
      ['X-Impersonate-User-Id', session.targetUser.id]];
  } else {
    next.headers = {
      ...(next.headers as Record<string, string> | undefined),
      'X-Impersonate-User-Id': session.targetUser.id,
    };
  }
  void isFormData;
  return next;
}

/**
 * Set one header on a RequestInit, whichever of the three shapes it is using.
 * The FormData branch below builds a `Headers`; everything else builds a plain
 * object; callers may hand us an array of pairs.
 */
function _withHeader(init: RequestInit | undefined, name: string, value: string): RequestInit {
  const next: RequestInit = init ? { ...init } : {};
  if (next.headers instanceof Headers) {
    const cloned = new Headers(next.headers);
    cloned.set(name, value);
    next.headers = cloned;
  } else if (Array.isArray(next.headers)) {
    next.headers = [
      ...next.headers.filter(([k]) => k.toLowerCase() !== name.toLowerCase()),
      [name, value],
    ];
  } else {
    next.headers = { ...(next.headers as Record<string, string> | undefined), [name]: value };
  }
  return next;
}

/**
 * The fence is a BROWSER concern. Its registry is module-level state, and on the
 * server that module is shared by every concurrent request in the process - so a
 * revision harvested for one user's render could be stamped onto another's
 * write. "What the user was viewing" only means anything in a browser tab
 * anyway, so keep it there.
 */
function _revisionFenceActive(): boolean {
  return typeof window !== 'undefined';
}

/**
 * Stamp the revision the user was viewing onto an office write of a revisable
 * form (UAC C-bis). See `lib/revision-fence.ts` for why this is one interceptor
 * rather than an argument threaded through 34 service functions.
 *
 * Returns the entity id when the header was actually sent, so a later 409 can be
 * attributed to the fence rather than to any other conflict the route may raise.
 */
function _attachRevisionHeader(
  url: unknown,
  init: RequestInit | undefined,
): { init: RequestInit | undefined; fencedEntityId: string | null } {
  if (!_revisionFenceActive()) return { init, fencedEntityId: null };
  const entityId = fencedWriteEntityId(url, init?.method);
  if (!entityId) return { init, fencedEntityId: null };
  const revision = rememberedRevision(entityId);
  // Never read = never rendered. An absent header is "unfenced", which is the
  // truthful answer here and matches every non-UI principal.
  if (revision === null) return { init, fencedEntityId: null };
  return {
    init: _withHeader(init, REVISION_HEADER, String(revision)),
    fencedEntityId: entityId,
  };
}

// ---------------------------------------------------------------------------
// Request deadlines (NEVER-STUCK-UI S2.1).
//
// A bare `fetch` waits as long as the browser lets it, which for a stalled proxy or
// keep-alive connection is forever: the query stays `pending` and the screen is a
// skeleton that never ends. Every apiFetch therefore gets a budget for the server to
// ANSWER (response headers). Reading the body is not on the clock, so an export that has
// started downloading or an event stream that has connected is never cut off.
// ---------------------------------------------------------------------------
export const API_READ_TIMEOUT_MS = 30_000;
/** Writes and the AI chat: the server may do real work before it answers. */
export const API_WRITE_TIMEOUT_MS = 120_000;
/** A FormData body: the upload itself happens before the server can answer. */
export const API_UPLOAD_TIMEOUT_MS = 600_000;

/** `RequestInit` plus our own per-call budget. `timeoutMs` is never forwarded to fetch. */
export type ApiFetchInit = RequestInit & {
  /** Milliseconds the server has to answer. Defaults by method and body, see above. */
  timeoutMs?: number;
};

/**
 * A GET that builds a file before it answers (an Excel or PDF export) gets the write
 * budget: the server does the whole build before the first byte, so 30s is too short for
 * a large one. Matched by path so every export, present and future, is covered in one
 * place rather than by each service remembering a `timeoutMs`.
 */
const _FILE_BUILD_PATH = /\/(export|download|pdf)(\b|[/?.])|\.(xlsx|pdf|csv)(\?|$)/i;

function _defaultTimeoutMs(url: unknown, init: RequestInit | undefined): number {
  if (init?.body instanceof FormData) return API_UPLOAD_TIMEOUT_MS;
  const request = typeof Request !== 'undefined' && url instanceof Request ? url : null;
  const method = (init?.method || request?.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') return API_WRITE_TIMEOUT_MS;
  const path = request ? request.url : url;
  if (typeof path === 'string' && _FILE_BUILD_PATH.test(path)) return API_WRITE_TIMEOUT_MS;
  return API_READ_TIMEOUT_MS;
}

/**
 * `fetch` with a deadline on the answer. The caller's own signal still works and still
 * rejects with its own AbortError; only our deadline becomes the readable timeout error.
 */
async function _fetchWithDeadline(
  url: RequestInfo,
  init: RequestInit | undefined,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const callerSignal =
    init?.signal ?? (typeof Request !== 'undefined' && url instanceof Request ? url.signal : undefined);
  let timedOut = false;
  const onCallerAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort(callerSignal.reason);
    else callerSignal.addEventListener('abort', onCallerAbort, { once: true });
  }
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException(REQUEST_TIMED_OUT_MESSAGE, 'TimeoutError'));
  }, timeoutMs);
  try {
    // The caller-abort listener stays after the answer arrives, so a caller can still
    // cancel the body (an event stream, a download); only the deadline stops here.
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    callerSignal?.removeEventListener('abort', onCallerAbort);
    if (timedOut && !callerSignal?.aborted) {
      const timeoutError = new Error(REQUEST_TIMED_OUT_MESSAGE);
      timeoutError.name = 'TimeoutError';
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Cached client-side auth token.
//
// Every apiFetch needs a Bearer JWT from /api/auth/token. That route is pure
// CPU (decode the NextAuth JWE, re-sign as HS256) but minting one PER API call
// meant a page firing N queries triggered N parallel token fetches, swamping
// the single Next.js server process (observed 12-17s each under load). The
// token is valid 24h, so cache it and dedupe concurrent fetches: N callers
// share ONE in-flight request and reuse the result until it nears expiry.
// ---------------------------------------------------------------------------
let _cachedToken: string | null = null;
let _cachedTokenExp = 0; // epoch seconds; 0 = unknown
let _tokenInFlight: Promise<string | null> | null = null;
const _TOKEN_REFRESH_MARGIN_S = 60; // refetch this many seconds before exp
/**
 * A token read that never answers used to hold `_tokenInFlight` forever, and every
 * apiFetch awaited that same promise: the whole app spun with nothing failing.
 */
export const TOKEN_FETCH_TIMEOUT_MS = 10_000;
export { SIGN_OUT_CAP_MS } from '@/lib/session-end';

function _decodeJwtExp(token: string): number {
  try {
    const payload = token.split('.')[1];
    if (!payload) return 0;
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const exp = JSON.parse(json)?.exp;
    return typeof exp === 'number' ? exp : 0;
  } catch {
    return 0;
  }
}

async function getCachedAuthToken(basePath: string): Promise<string | null> {
  const now = Math.floor(Date.now() / 1000);
  if (_cachedToken && _cachedTokenExp - _TOKEN_REFRESH_MARGIN_S > now) {
    return _cachedToken;
  }
  // Coalesce concurrent callers onto a single fetch.
  if (_tokenInFlight) return _tokenInFlight;

  _tokenInFlight = (async () => {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), TOKEN_FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(`${basePath}/api/auth/token`, {
        credentials: 'include',
        signal: abort.signal,
      });
      // 401 = the NextAuth cookie holds no usable session (gone, undecodable, or
      // overwritten by another localhost copy). Inside the signed-in shell, sending
      // the call without a bearer only earns a code-less 401 that nothing acts on,
      // so end the session here. A public page (unsubscribe link) has no session
      // to end and its call needs none. Anything else (500, timeout) is transient.
      if (res.status === 401) {
        if (isSignedInShell()) endSessionAndRedirect();
        return null;
      }
      if (!res.ok) return null;
      const data = await res.json().catch(() => null);
      const token: string | null = data?.token ?? null;
      if (token) {
        _cachedToken = token;
        // Opaque FastAPI session tokens carry no JWT exp; cache for a short soft
        // window so a page firing N queries doesn't hit /api/auth/token N times.
        // FastAPI slides the real expiry server-side, so a stale-but-valid cache
        // is fine; a revoked token surfaces as a 401 and triggers sign-out.
        const decoded = _decodeJwtExp(token);
        _cachedTokenExp = decoded > 0 ? decoded : Math.floor(Date.now() / 1000) + 300;
      }
      return token;
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
      _tokenInFlight = null;
    }
  })();

  return _tokenInFlight;
}

/** Clear the cached auth token. Call on logout / session switch. */
export function clearCachedAuthToken(): void {
  _cachedToken = null;
  _cachedTokenExp = 0;
  _tokenInFlight = null;
  // Revisions remembered for the outgoing session must not follow the next one
  // into a fence decision.
  clearRememberedRevisions();
}

/**
 * Revoke the current FastAPI session (this device) before NextAuth signOut.
 * Best-effort: a failure must not block logout. Call BEFORE clearCachedAuthToken
 * so the request still carries the session token.
 */
export async function revokeCurrentSession(): Promise<void> {
  try {
    await apiFetch('/api/v1/auth/logout', { method: 'POST' });
  } catch {
    /* logout should never be blocked by a failed revoke */
  }
}

// ---------------------------------------------------------------------------
// Session-invalidation interceptor.
//
// NextAuth is just a cookie-holder now; FastAPI owns session validity. When a
// session is revoked (logout-all, password change, admin force-logout) or
// expires (30 days with no activity), the NextAuth cookie is still "valid" but FastAPI
// returns 401 with a specific reason code. We gate strictly on the reason code
// so an RBAC 403 or an incidental 401 from one endpoint never logs everyone out.
//
// Before giving up, re-read the cookie once: the tab may hold a cached token
// that died while the user signed in again elsewhere. A newer token replays a
// read; a write is NOT replayed, because the newer token may be another user's
// and the user never meant that write to run as them - it returns the 401 and
// the next submit carries the new token. The same (dead) token ends the session
// through `endSessionAndRedirect`.
// ---------------------------------------------------------------------------
const _SESSION_DEAD_CODES = new Set(['session_revoked', 'session_expired', 'session_invalid']);

async function _isSessionDead(clonedResponse: Response): Promise<boolean> {
  const data = await clonedResponse.json().catch(() => null);
  const code: string | undefined = data?.detail?.code ?? data?.code;
  return !!code && _SESSION_DEAD_CODES.has(code);
}

/** Read the cookie again, skipping a cache that still holds the token that just failed. */
async function _refreshAuthToken(basePath: string, failedToken: string): Promise<string | null> {
  if (_cachedToken === failedToken) {
    _cachedToken = null;
    _cachedTokenExp = 0;
  }
  return getCachedAuthToken(basePath);
}

function _bearerOf(init: RequestInit | undefined): string | null {
  const value = init?.headers ? new Headers(init.headers as HeadersInit).get('Authorization') : null;
  return value?.startsWith('Bearer ') ? value.slice('Bearer '.length) : null;
}

/** What apiFetch answers, without touching the network, once the session is ending. */
function _sessionEndingResponse(): Response {
  return new Response(
    JSON.stringify({ detail: { code: 'session_ending', message: 'Your session has ended. Please sign in again.' } }),
    { status: 401, headers: { 'Content-Type': 'application/json' } },
  );
}

/**
 * apiFetch - universal fetch for dev/prod that prefixes API calls with the correct base URL
 * Routes business logic APIs to FastAPI backend, keeps auth routes in Next.js
 *
 * Usage:
 *   apiFetch('/api/v1/master-data/products', { method: 'GET' })
 *   apiFetch('/api/auth/login', { method: 'POST' }) // stays in Next.js
 */
export async function apiFetch(
  input: string | Request,
  apiInit?: ApiFetchInit,
): Promise<Response> {
  const { timeoutMs: callerTimeoutMs, ...rest } = apiInit ?? {};
  let init: RequestInit | undefined = apiInit ? rest : undefined;
  const timeoutMs = callerTimeoutMs ?? _defaultTimeoutMs(input, init);
  let url = input;
  // Use empty string for relative paths (nginx will proxy), or explicit URL for direct backend access
  let apiUrl = process.env.NEXT_PUBLIC_API_URL || '';
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';

  // Always check and sanitize apiUrl to prevent HTTP URLs in production
  // If apiUrl is HTTP and we're not in localhost development, clear it
  if (apiUrl && apiUrl.startsWith('http://')) {
    // Only allow HTTP URLs on localhost for development
    // In production (any HTTPS or non-localhost), force relative paths
    if (typeof window !== 'undefined') {
      const isLocalhost = 
        window.location.hostname === 'localhost' || 
        window.location.hostname === '127.0.0.1' ||
        window.location.hostname.startsWith('192.168.') ||
        window.location.hostname.startsWith('10.') ||
        window.location.hostname.startsWith('172.');
      
      const isProduction = 
        window.location.protocol === 'https:' || 
        (!isLocalhost && window.location.hostname.includes('.'));
      
      // If HTTP URL but we're in production, clear it to force relative paths
      if (isProduction || !isLocalhost) {
        apiUrl = '';
      }
    } else {
      // Server-side: if it's an HTTP URL, clear it (will use relative paths)
      // Server-side should use relative paths and let nginx proxy handle it
      apiUrl = '';
    }
  }

  // In browser (client-side), always use relative paths to avoid mixed content issues
  // The nginx reverse proxy will handle routing to the backend
  // Only use explicit API URL for local development when explicitly set
  if (typeof window !== 'undefined') {
    // Check if we're accessing via localhost (development)
    const isLocalhost = 
      window.location.hostname === 'localhost' || 
      window.location.hostname === '127.0.0.1' ||
      window.location.hostname.startsWith('192.168.') ||
      window.location.hostname.startsWith('10.') ||
      window.location.hostname.startsWith('172.');
    
    // Check if we're in production (HTTPS or non-localhost domain)
    const isProduction = 
      window.location.protocol === 'https:' || 
      (!isLocalhost && window.location.hostname.includes('.'));
    
    // Check if we're in development mode (HTTP on localhost)
    const isDevelopment = isLocalhost && window.location.protocol === 'http:';
    
    // In production, ALWAYS use relative paths regardless of NEXT_PUBLIC_API_URL
    // This ensures HTTPS pages always use HTTPS requests (no mixed content)
    // Nginx reverse proxy will handle routing to the backend
    if (isProduction) {
      apiUrl = '';
    } else if (isDevelopment && !apiUrl) {
      // Development without explicit API URL: use relative paths (Next.js rewrites will handle it)
      apiUrl = '';
    }
    // If apiUrl is explicitly set in development and we're on localhost HTTP, use it (e.g., http://localhost:8000)
  }

  // If input is a string and is a relative API path
  if (typeof input === 'string') {
      if (input.startsWith('/api/')) {
        // Routes that should stay in Next.js (not routed to FastAPI backend).
        // Keep this list explicit to avoid catching similarly named FastAPI routes.
        const nextJsOnlyPrefixes = [
          '/api/user-management/contact-access-agents',
          // Next.js BFF: proxies GET to /users/me, POST profile, logs, etc. - not a FastAPI path
          '/api/user-management/account',
        ];
        const isNextJsRoute =
          nextJsOnlyPrefixes.some((prefix) => input.startsWith(prefix)) ||
          /^\/api\/user-management\/access-agents\/[^/]+\/contact-access(?:\/|$)/.test(input);

        // Route business logic APIs to FastAPI backend
        const businessApiRoutes = [
          '/api/v1/',
          '/api/master-data/',
          '/api/order-management/',
          '/api/inventory/',
          '/api/procurement/',
          '/api/marketing/',
          '/api/forms-management/',
          '/api/complaint-management/',
          '/api/sla-management/',
          '/api/resource-management/',
          '/api/user-management/',
          '/api/system/',
        ];

        const isBusinessApi = !isNextJsRoute && businessApiRoutes.some(route => input.startsWith(route));

      if (isBusinessApi) {
        // Route to FastAPI backend using relative paths
        // All URLs will be relative to avoid mixed content issues
        if (input.startsWith('/api/master-data/')) {
          url = `/api/v1/master-data${input.replace('/api/master-data', '')}`;
        } else if (input.startsWith('/api/order-management/')) {
          url = `/api/v1/order-management${input.replace('/api/order-management', '')}`;
        } else if (input.startsWith('/api/inventory/')) {
          url = `/api/v1/inventory${input.replace('/api/inventory', '')}`;
        } else if (input.startsWith('/api/procurement/')) {
          url = `/api/v1/procurement${input.replace('/api/procurement', '')}`;
        } else if (input.startsWith('/api/marketing/')) {
          url = `/api/v1/marketing${input.replace('/api/marketing', '')}`;
        } else if (input.startsWith('/api/forms-management/')) {
          url = `/api/v1/forms-management${input.replace('/api/forms-management', '')}`;
        } else if (input.startsWith('/api/complaint-management/')) {
          url = `/api/v1/complaint-management${input.replace('/api/complaint-management', '')}`;
        } else if (input.startsWith('/api/sla-management/')) {
          // Route all SLA management (including tiers) to FastAPI backend
          url = `/api/v1/sla-management${input.replace('/api/sla-management', '')}`;
        } else if (input.startsWith('/api/resource-management/')) {
          url = `/api/v1/resource-management${input.replace('/api/resource-management', '')}`;
        } else if (input.startsWith('/api/user-management/')) {
          url = `/api/v1/user-management${input.replace('/api/user-management', '')}`;
        } else if (input.startsWith('/api/system/')) {
          url = `/api/v1/system${input.replace('/api/system', '')}`;
        } else if (input.startsWith('/api/v1/')) {
          // Already using v1 path - use as-is (relative)
          url = input;
        }

        // Prepend apiUrl to business API routes if set (for local development ONLY)
        // NEVER prepend HTTP URLs in production - always use relative paths
        // Prepend apiUrl ONLY in local development
        // In production with HTTPS, ALWAYS use relative paths (nginx handles proxying)
        if (apiUrl && typeof url === 'string' && url.startsWith('/api/v1/')) {
          const isHttpUrl = apiUrl.startsWith('http://');
          const isHttpsUrl = apiUrl.startsWith('https://');
          const isLocalhost = typeof window !== 'undefined' && (
            window.location.hostname === 'localhost' || 
            window.location.hostname === '127.0.0.1'
          );
          const isProductionHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';
          
          // NEVER prepend URLs in production HTTPS - always use relative paths
          if (isProductionHttps) {
            // Force relative path in production to avoid mixed content
            url = url; // Keep as-is (relative path like /api/v1/...)
          } else if (isHttpUrl && !isLocalhost) {
            // HTTP URL but not localhost = production HTTP, use relative path
            url = url;
          } else if (apiUrl && (isLocalhost || isHttpsUrl)) {
            // Safe to prepend: either localhost or HTTPS URL
            const baseUrl = apiUrl.replace(/\/$/, '');
            url = `${baseUrl}${url}`;
          }
          // else: leave url as relative path
        }

        // Extract JWT token from NextAuth and send in Authorization header
        // NextAuth stores JWT encrypted in cookies, so we need to get the raw token
        if (typeof window !== 'undefined') {
          // The session is on its way to /signin: answer at once, no request storm
          // (and re-try the navigation if it was cancelled).
          if (isSessionEnding()) {
            endSessionAndRedirect();
            return _sessionEndingResponse();
          }
          try {
            // For client-side: get a cached (deduped) JWT from the Next.js API.
            const token = await getCachedAuthToken(basePath);
            // The token read itself may have found the session gone.
            if (!token && isSessionEnding()) return _sessionEndingResponse();

            {
              if (token) {
                console.debug('JWT token extracted successfully');
                // Don't set Content-Type for FormData - browser needs to set it with boundary
                const isFormData = init?.body instanceof FormData;
                
                if (isFormData) {
                  // For FormData, we MUST NOT set Content-Type header
                  // Browser will automatically set it with boundary when it sees FormData body
                  // However, we can still add other headers like Authorization
                  console.debug('FormData detected - preserving browser Content-Type handling');
                  
                  const currentInit = init || {};
                  
                  // Create a new Headers object (don't copy Content-Type if it exists)
                  const headers = new Headers();
                  
                  // Only copy non-Content-Type headers from existing init
                  if (currentInit.headers) {
                    if (currentInit.headers instanceof Headers) {
                      currentInit.headers.forEach((value, key) => {
                        // Explicitly skip Content-Type - browser must set it
                        if (key.toLowerCase() !== 'content-type') {
                          headers.set(key, value);
                        }
                      });
                    } else if (Array.isArray(currentInit.headers)) {
                      currentInit.headers.forEach(([key, value]) => {
                        if (key.toLowerCase() !== 'content-type') {
                          headers.set(key, value);
                        }
                      });
                    } else {
                      // Plain object
                      Object.entries(currentInit.headers as Record<string, string>).forEach(([key, value]) => {
                        if (key.toLowerCase() !== 'content-type') {
                          headers.set(key, value);
                        }
                      });
                    }
                  }
                  
                  // Add Authorization header
                  headers.set('Authorization', `Bearer ${token}`);
                  
                  // Important: Don't set Content-Type - let browser handle it
                  // When fetch sees FormData body, it will automatically set:
                  // Content-Type: multipart/form-data; boundary=...
                  
                  init = {
                    ...currentInit,
                    credentials: 'include' as RequestCredentials,
                    headers: headers, // Headers object without Content-Type
                  };
                  
                  console.debug('FormData request prepared - Content-Type will be set by browser');
                } else {
                  // For non-FormData, convert headers to plain object
                  const existingHeaders: Record<string, string> = {};
                  if (init?.headers) {
                    if (init.headers instanceof Headers) {
                      init.headers.forEach((value, key) => {
                        existingHeaders[key] = value;
                      });
                    } else if (Array.isArray(init.headers)) {
                      init.headers.forEach(([key, value]) => {
                        existingHeaders[key] = value;
                      });
                    } else {
                      Object.assign(existingHeaders, init.headers);
                    }
                  }
                  
                  const headers: Record<string, string> = {
                    ...existingHeaders,
                    'Authorization': `Bearer ${token}`,
                  };
                  if (!headers['Content-Type'] && !headers['content-type']) {
                    headers['Content-Type'] = 'application/json';
                  }
                  init = {
                    ...init,
                    credentials: 'include' as RequestCredentials,
                    headers,
                  };
                }
              } else {
                // No token available - fall back to cookie-based auth.
                console.warn('No auth token available; falling back to cookies');
                const isFormData = init?.body instanceof FormData;
                const headers: Record<string, string> = {
                  ...(init?.headers as Record<string, string>),
                };
                if (!isFormData && !headers['Content-Type']) {
                  headers['Content-Type'] = 'application/json';
                }
                init = {
                  ...init,
                  credentials: 'include' as RequestCredentials,
                  headers,
                };
              }
            }
          } catch (e) {
            console.error('Failed to get token for API call', e);
            // Fallback: send cookies
            const isFormData = init?.body instanceof FormData;
            const headers: Record<string, string> = {
              ...(init?.headers as Record<string, string>),
            };
            if (!isFormData && !headers['Content-Type']) {
              headers['Content-Type'] = 'application/json';
            }
            init = {
              ...init,
              credentials: 'include' as RequestCredentials,
              headers,
            };
          }
        } else {
          // Server-side: cookies will be forwarded automatically
          const isFormData = init?.body instanceof FormData;
          const headers: Record<string, string> = {
            ...(init?.headers as Record<string, string>),
          };
          if (!isFormData && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
          }
          init = {
            ...init,
            credentials: 'include' as RequestCredentials,
            headers,
          };
        }
      } else {
        // Keep auth and account routes in Next.js
        url = basePath + (input.startsWith('/') ? input : '/' + input);
      }
    }
  }

  const isBrowserApiCall =
    typeof window !== 'undefined' && typeof url === 'string' && url.includes('/api/v1/');

  init = _attachImpersonationHeader(url, init);
  const fenced = _attachRevisionHeader(url, init);
  init = fenced.init;
  const sentViewAs = impersonationStore.getState()?.targetUser.id ?? null;
  let response = await _fetchWithDeadline(url as RequestInfo, init, timeoutMs);
  // Browser-side: a 401 with a session-dead reason code means our session was
  // revoked/expired server-side → refresh once, else sign out and bounce to /signin.
  if (isBrowserApiCall && response.status === 401 && (await _isSessionDead(response.clone()))) {
    const failedToken = _bearerOf(init);
    const fresh = failedToken ? await _refreshAuthToken(basePath, failedToken) : null;
    const method = (init?.method ?? 'GET').toUpperCase();
    if (fresh && fresh !== failedToken && !isSessionEnding()) {
      if (method === 'GET' || method === 'HEAD') {
        init = _withHeader(init, 'Authorization', `Bearer ${fresh}`);
        response = await _fetchWithDeadline(url as RequestInfo, init, timeoutMs);
        if (response.status === 401 && (await _isSessionDead(response.clone()))) {
          endSessionAndRedirect();
        }
      }
    } else {
      endSessionAndRedirect();
    }
  }
  if (isBrowserApiCall && sentViewAs && response.headers.get(IMPERSONATION_ENDED_HEADER) === '1') {
    endViewAsLocally(sentViewAs);
  }
  // The revision fence, both directions (UAC C-bis). A read of a revisable list
  // or record records what the screen is about to show; a refusal against a
  // superseded version refreshes the record and normalizes the sentence.
  if (fenced.fencedEntityId && response.status === 409) {
    return handleRevisionConflict(fenced.fencedEntityId, response);
  }
  if (response.ok && _revisionFenceActive() && isFencedReadPath(url, init?.method)) {
    try {
      harvestRevisions(await response.clone().json());
    } catch {
      /* a body we cannot read is simply a revision we do not know */
    }
  }
  return response;
}

export function getClientIP(request: NextRequest): string {
  return (
    request.headers.get('x-forwarded-for') ||
    request.headers.get('x-real-ip') ||
    //|| request.socket.remoteAddress
    'unknown'
  );
}
