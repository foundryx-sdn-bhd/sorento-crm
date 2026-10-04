'use client';

import RouteErrorScreen from '@/components/common/RouteErrorScreen';

/**
 * Error boundary for the customer-facing `(public)` pages, the published
 * catalogues at `/c/...` (NEVER-STUCK-UI S5.2, audit row 40). The reader is a
 * customer with no staff account, so it offers no link into the app.
 */
export default function PublicError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteErrorScreen error={error} reset={reset} />;
}
