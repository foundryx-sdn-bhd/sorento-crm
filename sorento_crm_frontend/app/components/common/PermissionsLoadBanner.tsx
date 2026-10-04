'use client';

import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePermissions } from '@/hooks/usePermissions';
import { cn } from '@/lib/utils';
import { Container } from '@/components/common/container';

/**
 * One banner in the protected shell while the permission check is failed
 * (NEVER-STUCK-UI S3, lever L6, audit row 34).
 *
 * `useHasPermission` answers false while it cannot know, so menu entries, tabs
 * and buttons stay hidden (fail closed). Without this the user just sees a
 * thinner app and assumes their role changed; with it they are told why, and
 * one Retry brings everything back once the check succeeds.
 */
export default function PermissionsLoadBanner() {
  const { isError, isFetching, refetch } = usePermissions();
  if (!isError) return null;
  return (
    <Container>
      <div
        role="alert"
        className="mb-5 flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm"
      >
        <AlertCircle className="size-4 shrink-0 text-destructive" />
        <span className="min-w-0 flex-1 basis-48 text-foreground">
          Could not check your access, so some menus and actions are hidden.
        </span>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          <RefreshCw className={cn(isFetching && 'animate-spin')} />
          Retry
        </Button>
      </div>
    </Container>
  );
}
