'use client';

import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { ListLoadFailure } from '@/components/common/ListLoadFailure';
import { usePermissions } from '@/hooks/usePermissions';
import { formatDateInMalaysia } from '@/lib/helpers';
import { useProjectForms } from '../../_shared/hooks/useProjectForms';
import {
  visibleProjectFormSections,
  type ProjectFormRow,
  type ProjectFormSectionDef,
} from '../../_shared/lib/projectForms';
import type { Project } from '../../_shared/types/project.types';

function FormSectionGrid({
  section,
  projectId,
}: {
  section: ProjectFormSectionDef;
  projectId: string;
}) {
  const query = useProjectForms(section.kind, projectId);
  const rows = React.useMemo(() => query.data?.rows ?? [], [query.data]);

  const columns = React.useMemo<ColumnDef<ProjectFormRow>[]>(
    () => [
      {
        id: 'number',
        accessorFn: (row) => row.number,
        header: ({ column }) => <DataGridColumnHeader title="Number" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm font-medium tabular-nums" title={row.original.number}>
            {row.original.number}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'Number' },
      },
      {
        id: 'date',
        accessorFn: (row) => row.date ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Date" column={column} />,
        cell: ({ row }) => (row.original.date ? formatDateInMalaysia(row.original.date) : '-'),
        size: 120,
        meta: { headerTitle: 'Date' },
      },
      {
        id: 'status',
        accessorFn: (row) => row.statusLabel,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) =>
          row.original.statusLabel ? (
            <Badge status={row.original.status} appearance="light">
              {row.original.statusLabel}
            </Badge>
          ) : (
            '-'
          ),
        size: 170,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'summary',
        accessorFn: (row) => row.summary,
        header: ({ column }) => <DataGridColumnHeader title="Details" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.summary || undefined}>
            {row.original.summary || '-'}
          </span>
        ),
        size: 280,
        meta: { headerTitle: 'Details' },
      },
    ],
    [],
  );

  return (
    <PanelDataGrid
      title={section.title}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      listingKey={`${section.permission}::project-forms-${section.kind}`}
      isLoading={query.isLoading}
      error={query.isError ? query.error : undefined}
      onRetry={() => void query.refetch()}
      emptyTitle={section.emptyTitle}
      emptyBody={section.emptyHint}
      rowHref={(row) => row.href}
    />
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
      <SectionSkeleton rows={4} />
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
        <FormSectionGrid key={section.kind} section={section} projectId={project.id} />
      ))}
    </div>
  );
}
