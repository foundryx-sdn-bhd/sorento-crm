/**
 * The ONE way a dead staff session leaves the app (SESSION-NEVER-STUCK).
 *
 * Owner rule (1 Oct 2026): nothing may hang. A session that cannot be used any more
 * (FastAPI said session_expired/revoked/invalid, or `/api/auth/token` said there is no
 * session) ends in exactly one hard navigation to sign-in, carrying the return URL,
 * with view-as cleared. Every caller - apiFetch, the protected layout - goes through
 * `endSessionAndRedirect`, which latches, so concurrent failures cannot stack
 * redirects and later calls can short-circuit instead of storming the server.
 */
import { impersonationStore } from '@/lib/impersonation-store';
import { toast } from '@/lib/toast';

/** Longest we wait for NextAuth to clear its cookie before navigating anyway. */
export const SIGN_OUT_CAP_MS = 3000;

/** Indirection so tests can observe the navigation (jsdom's location is unforgeable). */
export const sessionNavigation = {
  assign: (url: string) => window.location.assign(url),
};

/**
 * After this long with the page still alive, the navigation was cancelled (a page's
 * "leave site?" guard answered Stay) or is very slow: the next call retries it. The
 * latch itself never drops, so requests stay short-circuited for the page's life.
 */
export const REDIRECT_RETRY_MS = 5000;

let _ending = false;
let _navigatedAt = 0;
let _signedInShell = false;

export function isSessionEnding(): boolean {
  return _ending;
}

/**
 * The protected layout says when the signed-in shell is mounted. Only there does a
 * missing session mean "this session died"; a public page using apiFetch (the
 * unsubscribe link) simply has none.
 */
export function setSignedInShell(active: boolean): void {
  _signedInShell = active;
}

export function isSignedInShell(): boolean {
  return _signedInShell;
}

function _basePath(): string {
  return process.env.NEXT_PUBLIC_BASE_PATH || '';
}

/** `/signin?callbackUrl=<where the user was>`, or plain `/signin` when already there. */
export function signInUrl(): string {
  const base = _basePath();
  const loc = window.location;
  let path = loc.pathname;
  // The sign-in page's router.push adds the base path itself, so the return URL is app-relative.
  if (base && (path === base || path.startsWith(`${base}/`))) path = path.slice(base.length) || '/';
  if (path === '/signin' || path.startsWith('/signin/')) return `${base}/signin`;
  const target = `${path}${loc.search}${loc.hash}`;
  return `${base}/signin?callbackUrl=${encodeURIComponent(target)}`;
}

async function _signOutQuietly(): Promise<void> {
  try {
    const { signOut } = await import('next-auth/react');
    await signOut({ redirect: false });
  } catch {
    /* the navigation below is what matters */
  }
}

export function endSessionAndRedirect(): void {
  if (typeof window === 'undefined') return;
  if (_ending) {
    // Already on the way out: only re-try a navigation that has not happened.
    if (_navigatedAt && Date.now() - _navigatedAt >= REDIRECT_RETRY_MS) {
      _navigatedAt = Date.now();
      sessionNavigation.assign(signInUrl());
    }
    return;
  }
  _ending = true;
  const target = signInUrl();
  // The view-as entry belongs to the session that just died; leaving it in storage
  // would re-attach the header to whatever signs in next on this browser.
  impersonationStore.setSession(null);
  const cap = new Promise<void>((resolve) => setTimeout(resolve, SIGN_OUT_CAP_MS));
  void Promise.race([_signOutQuietly(), cap]).finally(() => {
    _navigatedAt = Date.now();
    sessionNavigation.assign(target);
  });
}

// ---------------------------------------------------------------------------
// Stale view-as. The backend ignores an X-Impersonate-User-Id it cannot honour
// (view-as stopped elsewhere, admin role removed, target deactivated) and says so
// with X-Impersonation-Ended. The admin's own session is fine, so this is not a
// sign-out: drop the banner, say once what happened, and refetch so nothing on
// screen is still the target's data.
// ---------------------------------------------------------------------------
export const IMPERSONATION_ENDED_HEADER = 'X-Impersonation-Ended';
export const VIEW_AS_ENDED_MESSAGE = 'View-as ended - you are seeing your own data';

let _viewAsEndedHandler: (() => void) | null = null;

/** The query provider registers a blanket invalidation here. */
export function registerViewAsEndedHandler(fn: (() => void) | null): void {
  _viewAsEndedHandler = fn;
}

/**
 * Called with the target id a request was sent with. Acts only if that view-as is
 * still the current one, so a late response to a request sent before a deliberate
 * stop (or before a new view-as started) changes nothing.
 */
export function endViewAsLocally(sentTargetUserId: string): void {
  const current = impersonationStore.getState();
  if (!current || current.targetUser.id !== sentTargetUserId) return;
  impersonationStore.setSession(null);
  toast.info(VIEW_AS_ENDED_MESSAGE, { id: 'view-as-ended' });
  try {
    _viewAsEndedHandler?.();
  } catch {
    /* a refetch failure is reported by the queries themselves */
  }
}
