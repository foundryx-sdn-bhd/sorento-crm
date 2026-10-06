'use client';

import { useSession } from 'next-auth/react';
import { useImpersonationSession } from '@/lib/impersonation-store';
import { isSuperadminUser } from '@/lib/is-superadmin';

/**
 * The acting user's superadmin / admin flag, impersonation-aware (the counterpart of
 * `useEffectiveUserId`).
 *
 * Impersonation swaps the BACKEND principal via the `X-Impersonate-User-Id` header
 * (lib/api.ts) and `/users/me/permissions` already answers for the target, but the
 * NextAuth session keeps the admin's `roleName`. Any superadmin-only gate that reads the
 * session (the sidebar's `superadminOnly` entries, `RequireAccess superadmin`) therefore
 * stayed open for the whole view-as. This reads the impersonation target's role instead:
 * `targetUser.role_name` is the target's first-assigned role slug
 * (`impersonation.py::_serialize_target`), matched the same way the session's `roleName`
 * is. The backend refuses to impersonate an admin or superadmin, so in practice the answer
 * while viewing as is `false`; the hook stays correct if that rule ever changes.
 *
 * The store is localStorage, so the target's role can only NARROW the session's answer,
 * never widen it: a forged entry on a non-admin's browser still reads `false` (the backend
 * ignores the view-as header for a non-admin anyway, so nothing beyond the menu was ever at
 * stake, but the gate should not depend on that).
 *
 * Returns `null` while the session is still loading, so a gate can keep showing instead of
 * flickering the denied state at a real superadmin. Not impersonating: exactly
 * `isSuperadminUser(session.user)`, as before.
 */
export function useEffectiveIsSuperadmin(): boolean | null {
  const { data: session, status } = useSession();
  const impersonation = useImpersonationSession();
  if (status === 'loading') return null;
  const own = isSuperadminUser(session?.user);
  if (!impersonation) return own;
  return (
    own && isSuperadminUser({ roleName: impersonation.targetUser.role_name })
  );
}
