'use client';

import { useEffect } from 'react';
import GlobalErrorView from '@/components/common/GlobalErrorView';

/**
 * Catches what escapes the root layout (NEVER-STUCK-UI S5.1, audit row 33): above
 * all a failed `ssr:false` chunk load of the client providers
 * (`components/DynamicClientProviders.tsx`) when a deploy has replaced the build
 * the tab is running. Without this file that is a blank page. It replaces the root
 * layout, so it renders its own `<html>` and `<body>`, and imports nothing that
 * shares a chunk with the UI it is standing in for.
 */
export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ margin: 0 }}>
        <GlobalErrorView
          digest={error.digest}
          onReload={() => window.location.reload()}
        />
      </body>
    </html>
  );
}
