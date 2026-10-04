'use client';

import { RotateCw, ShieldAlert } from 'lucide-react';
import { isAccessDenied } from '@/lib/api-client';

/**
 * What a picker shows in its menu when its options could not be read (NEVER-STUCK-UI S3,
 * lever L5). Before, a failed options read rendered "No results found.", which says the
 * thing the user is looking for does not exist.
 *
 * A refusal says "no access" with no Retry (asking again cannot help); anything else
 * says "could not load" with a Retry.
 */
export function SelectLoadFailure({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const denied = isAccessDenied(error);
  return (
    <div
      data-testid="select-load-failure"
      role="alert"
      className="flex flex-col items-start gap-2 px-3 py-3 text-sm"
    >
      {denied ? (
        <span className="flex items-start gap-2 text-muted-foreground">
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          {"You don't have access to this list."}
        </span>
      ) : (
        <span className="text-muted-foreground">These options could not be loaded.</span>
      )}
      {!denied && onRetry ? (
        <button
          type="button"
          data-slot="select-load-retry"
          onClick={() => onRetry()}
          className="flex items-center gap-1.5 rounded-sm px-2 py-1 text-sm font-medium hover:bg-accent"
        >
          <RotateCw className="size-3.5" aria-hidden /> Retry
        </button>
      ) : null}
    </div>
  );
}

/** The closed control's placeholder while its options failed to load. */
export function selectLoadFailurePlaceholder(error: unknown): string {
  return isAccessDenied(error) ? 'No access' : 'Could not load';
}

/** True for an `error` value a query hands over (null / undefined / false mean none). */
export function hasLoadError(error: unknown): boolean {
  return error != null && error !== false;
}
