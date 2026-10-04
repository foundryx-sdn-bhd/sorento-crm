'use client';

import AccessDenied from '@/app/components/common/AccessDenied';
import { Button } from '@/components/ui/button';
import { isAccessDenied } from '@/lib/api-client';

/**
 * A failed list read, in place of the empty state (NEVER-STUCK-UI S3, lever L2), shared by
 * `DataGrid` and `PanelDataGrid`: "No data" after a 403 or a 500 tells the user something
 * false about their records. A refusal never shows the raw slug and offers no Retry.
 */
export function ListLoadFailure({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (isAccessDenied(error)) {
    return (
      // Capped to the viewport: inside a wide grid's sticky `w-fit` cell the line would
      // otherwise run past the scrollport at 375px and clip.
      <div data-testid="data-grid-no-access" className="max-w-[min(32rem,calc(100vw-4rem))]">
        <AccessDenied inline title="You don't have access to this list" />
      </div>
    );
  }
  const message =
    (error instanceof Error && error.message) || 'This list could not be loaded.';
  return (
    <div
      data-testid="data-grid-error"
      className="flex max-w-[min(32rem,calc(100vw-4rem))] flex-col items-start gap-2"
    >
      <span role="alert" className="text-foreground">
        {message}
      </span>
      {onRetry ? (
        <Button type="button" variant="outline" size="sm" onClick={() => onRetry()}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

