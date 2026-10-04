'use client';

import type { ReactNode } from 'react';
import { useSession } from 'next-auth/react';
import { ScreenLoader } from '@/components/common/screen-loader';
import { isSuperadminUser } from '@/lib/is-superadmin';
import { usePermissions } from '@/hooks/usePermissions';
import AccessDenied from '@/app/components/common/AccessDenied';
import LoadErrorState from '@/components/common/LoadErrorState';

type RequireAccessProps = {
  children: ReactNode;
} & (
  | { superadmin: true; permission?: never }
  | { permission: string; superadmin?: never }
);

/**
 * Page-level access guard. Renders <AccessDenied/> (NOT a redirect) when the
 * current user is denied, a loader while access is still resolving, an error
 * with Retry when the permission check itself failed, else the page body. Mirrors the sidebar's fail-open-while-loading behaviour so the
 * denied state never flashes before permissions resolve.
 *
 * Two modes:
 * - <RequireAccess superadmin> - allowed only for superadmin/admin roles.
 * - <RequireAccess permission="slug"> - allowed only when the user's
 *    permission set includes `slug`.
 */
export default function RequireAccess(props: RequireAccessProps) {
  if (props.superadmin) {
    return <SuperadminGate>{props.children}</SuperadminGate>;
  }
  return <PermissionGate permission={props.permission}>{props.children}</PermissionGate>;
}

function SuperadminGate({ children }: { children: ReactNode }) {
  const { data: session, status } = useSession();
  if (status === 'loading') return <ScreenLoader />;
  if (!isSuperadminUser(session?.user)) return <AccessDenied />;
  return <>{children}</>;
}

function PermissionGate({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { permissionSet, isLoading, isError, isFetching, refetch } = usePermissions();
  if (isLoading) return <ScreenLoader />;
  // The check itself failed: say so, with Retry. An empty set from a failed read
  // is not "denied" (NEVER-STUCK-UI S3, lever L6).
  if (isError) return <PermissionsLoadError onRetry={() => void refetch()} retrying={isFetching} />;
  if (!permissionSet.has(permission)) return <AccessDenied />;
  return <>{children}</>;
}

export function PermissionsLoadError({
  onRetry,
  retrying,
}: {
  onRetry: () => void;
  retrying?: boolean;
}) {
  return (
    <LoadErrorState
      className="min-h-[60dvh]"
      title="Could not check your access"
      message="Your permissions did not load, so this page cannot tell what you are allowed to see. Retry, and if it keeps failing, reload the page."
      onRetry={onRetry}
      retrying={retrying}
    />
  );
}
