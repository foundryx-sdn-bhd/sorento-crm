/**
 * DEV-LOGIN-BYPASS: the allowlisted dev users for the sign-in page's "Sign in as" picker
 * (PLAN-dev-login-bypass-03oct.md). Unauthenticated, so plain `fetch` against the Next route
 * (never `apiFetch`, which needs a session nobody has yet).
 *
 * GET /api/auth/dev-login/users
 *   200: { users: [{ email, name, role_name }] }  first entry = default dev user
 *   404: dev sign-in is off for this copy (the normal case everywhere else)
 */

export interface DevLoginUser {
  email: string;
  name: string | null;
  role_name: string | null;
}

/** The dev users, or null when dev sign-in is off (any failure reads as off). */
export async function getDevLoginUsers(): Promise<DevLoginUser[] | null> {
  try {
    const response = await fetch('/api/auth/dev-login/users', { cache: 'no-store' });
    if (!response.ok) return null;
    const data = (await response.json()) as { users?: DevLoginUser[] };
    return Array.isArray(data.users) && data.users.length > 0 ? data.users : null;
  } catch {
    return null;
  }
}
