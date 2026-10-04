'use client';

import RouteErrorScreen from '@/components/common/RouteErrorScreen';

/**
 * Error boundary for the unsubscribe links in notification emails
 * (NEVER-STUCK-UI S5.2, audit row 40). The reader arrives from an email, signed in
 * or not, so it offers no link into the app.
 */
export default function UnsubscribeError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteErrorScreen error={error} reset={reset} />;
}
