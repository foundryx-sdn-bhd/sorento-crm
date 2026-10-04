'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { MoveLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const CHUNK_FAILURE_PATTERNS = [
  /Loading (CSS )?chunk [\w-]+ failed/i, // webpack
  /Failed to load chunk/i, // turbopack
  /Failed to fetch dynamically imported module/i, // Chromium native import()
  /Importing a module script failed/i, // Safari native import()
  /error loading dynamically imported module/i, // Firefox native import()
];

/**
 * A chunk the browser asked for no longer exists on the server: the tab still runs
 * the previous build and a deploy replaced its chunks. Retrying the render cannot
 * fix that; only a full page load, which fetches the new build, can.
 */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === 'ChunkLoadError') return true;
  return CHUNK_FAILURE_PATTERNS.some((p) => p.test(error.message));
}

export function reloadPage(): void {
  window.location.reload();
}

/**
 * The body of every route group's `error.tsx` (NEVER-STUCK-UI S5): a thrown error
 * ends on this screen, never a blank page or Next's bare "Application error".
 *
 * Copy is fixed, never `error.message` (see `(protected)/error.tsx`): a production
 * render error is developer boilerplate and a rethrown API error can carry a
 * record id. `digest` is the one token that correlates with the server log.
 */
export default function RouteErrorScreen({
  error,
  reset,
  exit,
  onReload = reloadPage,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  /** The way out this route group offers, if any. Customer-facing groups pass none. */
  exit?: { href: string; label: string };
  onReload?: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const chunk = isChunkLoadError(error);

  return (
    <div className="flex w-full justify-center px-4">
      <Card className="mt-10 w-full max-w-lg">
        <CardHeader>
          <CardTitle>
            {chunk ? 'A new version is available' : 'Something went wrong'}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {chunk
              ? 'This page could not load because the app was updated. Reload to get the latest version.'
              : 'Something went wrong on this page.'}
          </p>
          {error.digest ? (
            <p className="font-mono text-xs text-muted-foreground">
              Reference: {error.digest}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {chunk ? (
              <Button onClick={onReload}>Reload</Button>
            ) : (
              <Button onClick={reset}>Try again</Button>
            )}
            {exit ? (
              <Button asChild variant="outline">
                <Link href={exit.href}>
                  <MoveLeft /> {exit.label}
                </Link>
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
