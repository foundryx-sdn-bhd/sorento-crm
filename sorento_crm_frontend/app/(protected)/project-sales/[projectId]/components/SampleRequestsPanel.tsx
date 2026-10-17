'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import type { ColumnDef } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { DELIVERY_MODE_LABELS, type SampleRequestRow } from '@/types/sampleRequest';
import { useProjectSampleRequestsQuery } from '../../sample-requests/hooks/useSampleRequests';
import { deliveryText } from '../../sample-requests/components/SampleRequestsList';
import type { Project } from '../../_shared/types/project.types';

/**
 * This project's sample requests, every status. The row opens the request page; Create opens
 * the full New Sample Request page with this project already picked.
 */
export function SampleRequestsPanel({ project }: { project: Project }) {
  const router = useRouter();
  const canCreate = useHasPermission('projects.sample_requests.create');
  const requests = useProjectSampleRequestsQuery(project.id);
  const rows = React.useMemo(() => requests.data?.data ?? [], [requests.data]);

  const columns = React.useMemo<ColumnDef<SampleRequestRow>[]>(
    () => [
      {
        id: 'request_no',
        accessorFn: (row) => row.request_no,
        header: ({ column }) => <DataGridColumnHeader title="Number" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm font-medium tabular-nums">{row.original.request_no}</span>
        ),
        size: 130,
        meta: { headerTitle: 'Number' },
      },
      {
        id: 'submitted_at',
        accessorFn: (row) => row.submitted_at ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Submitted" column={column} />,
        cell: ({ row }) =>
          row.original.submitted_at ? formatDateInMalaysia(row.original.submitted_at) : '-',
        size: 120,
        meta: { headerTitle: 'Submitted' },
      },
      {
        id: 'status',
        accessorFn: (row) => row.status,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <span className={`${STATUS_PILL_BASE} ${statusPillClass(row.original.status)}`}>
            {row.original.status_label}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'delivery',
        accessorFn: (row) => deliveryText(row),
        header: ({ column }) => <DataGridColumnHeader title="Delivery" column={column} />,
        cell: ({ row }) => deliveryText(row.original),
        size: 120,
        meta: { headerTitle: 'Delivery' },
      },
      {
        id: 'delivery_mode',
        accessorFn: (row) => (row.delivery_mode ? DELIVERY_MODE_LABELS[row.delivery_mode] : ''),
        header: ({ column }) => <DataGridColumnHeader title="Mode" column={column} />,
        cell: ({ row }) =>
          row.original.delivery_mode ? DELIVERY_MODE_LABELS[row.original.delivery_mode] : '-',
        size: 150,
        meta: { headerTitle: 'Mode' },
      },
      {
        id: 'line_count',
        accessorFn: (row) => row.line_count,
        header: ({ column }) => <DataGridColumnHeader title="Lines" column={column} />,
        cell: ({ row }) => row.original.line_count,
        size: 80,
        meta: { headerTitle: 'Lines' },
      },
      {
        id: 'ship_to_name',
        accessorFn: (row) => row.ship_to_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Ship to" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.ship_to_name ?? undefined}>
            {row.original.ship_to_name || '-'}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'Ship to' },
      },
      {
        id: 'salesperson_name',
        accessorFn: (row) => row.salesperson_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Salesperson" column={column} />,
        cell: ({ row }) => (
          <span className="truncate text-sm" title={row.original.salesperson_name ?? undefined}>
            {row.original.salesperson_name || '-'}
          </span>
        ),
        size: 160,
        meta: { headerTitle: 'Salesperson' },
      },
    ],
    [],
  );

  return (
    <PanelDataGrid
      title="Sample requests"
      toolbar={
        canCreate ? (
          <Button
            size="sm"
            onClick={() =>
              router.push(`/project-sales/sample-requests/new?project_id=${project.id}`)
            }
          >
            <Plus />
            Create
          </Button>
        ) : undefined
      }
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      listingKey="projects.projects.view::project-sample-requests"
      isLoading={requests.isLoading}
      error={requests.isError ? requests.error : undefined}
      emptyTitle="No sample requests on this project"
      onRowClick={(row) => router.push(`/project-sales/sample-requests/${row.id}`)}
    />
  );
}
