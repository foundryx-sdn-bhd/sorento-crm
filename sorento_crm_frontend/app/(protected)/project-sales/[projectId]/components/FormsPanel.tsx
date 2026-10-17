'use client';

import * as React from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ListLoadFailure } from '@/components/common/ListLoadFailure';
import { usePermissions } from '@/hooks/usePermissions';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { useProjectForms } from '../../_shared/hooks/useProjectForms';
import {
  visibleProjectFormSections,
  type ProjectFormSectionDef,
} from '../../_shared/lib/projectForms';
import type { Project } from '../../_shared/types/project.types';

function FormSectionCard({
  section,
  projectId,
}: {
  section: ProjectFormSectionDef;
  projectId: string;
}) {
  const query = useProjectForms(section.kind, projectId);
  const rows = query.data?.rows ?? [];
  const total = query.data?.total ?? rows.length;

  return (
    <Card aria-label={section.title}>
      <CardHeader className="py-3">
        <CardTitle className="text-base">
          {section.title}
          {query.data ? (
            <span className="ms-2 text-sm font-normal text-muted-foreground tabular-nums">
              {total}
            </span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {query.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : query.isError ? (
          <ListLoadFailure error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <div>
            <p className="text-sm font-medium">{section.emptyTitle}</p>
            <p className="text-sm text-muted-foreground">{section.emptyHint}</p>
          </div>
        ) : (
          <>
            <ul className="divide-y rounded-lg border">
              {rows.map((row) => (
                <li key={row.id}>
                  <Link
                    href={row.href}
                    className="flex flex-col gap-1 px-3 py-2 text-sm hover:bg-muted/50 sm:flex-row sm:items-center sm:gap-3"
                  >
                    <span className="shrink-0 font-medium tabular-nums sm:w-36">{row.number}</span>
                    <span className="shrink-0 text-muted-foreground sm:w-28">
                      {row.date ? formatDateInMalaysia(row.date) : '-'}
                    </span>
                    <span className="shrink-0">
                      <span className={`${STATUS_PILL_BASE} ${row.statusClass}`}>
                        {row.statusLabel || '-'}
                      </span>
                    </span>
                    <span className="min-w-0 flex-1 truncate" title={row.summary || undefined}>
                      {row.summary || '-'}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {total > rows.length ? (
              <p className="mt-2 text-xs text-muted-foreground tabular-nums">
                Showing {rows.length} of {total}
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Every form type linked to this project, one section each. A section appears only for a
 * user who may view that form type, and its rows come from the form's own list, so that
 * list's own row rules apply.
 */
export function FormsPanel({ project }: { project: Project }) {
  const { permissionSet, isLoading, isError, error, refetch } = usePermissions();
  const sections = React.useMemo(
    () => visibleProjectFormSections((slug) => permissionSet.has(slug)),
    [permissionSet],
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (isError) {
    return <ListLoadFailure error={error} onRetry={() => void refetch()} />;
  }
  if (sections.length === 0) {
    return (
      <Card>
        <CardContent className="p-4">
          <p className="text-sm font-medium">No forms to show</p>
          <p className="text-sm text-muted-foreground">
            Forms linked to this project appear here for the form types you can view.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      {sections.map((section) => (
        <FormSectionCard key={section.kind} section={section} projectId={project.id} />
      ))}
    </div>
  );
}
