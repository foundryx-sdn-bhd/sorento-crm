'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  RowSelectionState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useScheduledTasks } from '../hooks/useScheduledTasks';
import type { ScheduledTask } from '../types/scheduledTask.types';
import { ChevronRight } from 'lucide-react';

const INTERVAL_LABELS: Record<string, string> = {
  seconds: 'sec',
  minutes: 'min',
  hours: 'hr',
  days: 'day',
};

function formatFrequency(task: ScheduledTask): string {
  const unit = INTERVAL_LABELS[task.interval_unit] ?? task.interval_unit;
  return `Every ${task.interval_value} ${unit}${task.interval_value !== 1 ? 's' : ''}`;
}

export default function ScheduledTasksList() {
  const router = useRouter();
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 50 });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, refetch, isFetching, error } = useScheduledTasks();
  const tasks = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<ScheduledTask>[]>(
    () => [
      buildSelectColumn<ScheduledTask>(),
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium">{row.original.name}</span>
            {row.original.description && (
              <span className="text-xs text-muted-foreground truncate max-w-[200px]">
                {row.original.description}
              </span>
            )}
          </div>
        ),
        size: 220,
        minSize: 140,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-8 w-40" /> },
      },
      {
        accessorKey: 'key',
        header: ({ column }) => <DataGridColumnHeader title="Key" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.key}
          </Badge>
        ),
        size: 180,
        minSize: 100,
        meta: { headerTitle: 'Key' },
      },
      {
        accessorKey: 'enabled',
        header: ({ column }) => <DataGridColumnHeader title="Enabled" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.enabled ? 'success' : 'secondary'}>
            {row.original.enabled ? 'Yes' : 'No'}
          </Badge>
        ),
        size: 90,
        meta: { headerTitle: 'Enabled' },
      },
      {
        accessorKey: 'interval',
        header: ({ column }) => <DataGridColumnHeader title="Frequency" column={column} />,
        cell: ({ row }) => formatFrequency(row.original),
        size: 120,
        meta: { headerTitle: 'Frequency' },
      },
      {
        accessorKey: 'next_run_at',
        header: ({ column }) => <DataGridColumnHeader title="Next Run" column={column} />,
        cell: ({ row }) =>
          row.original.next_run_at
            ? formatDateTimeInMalaysia(row.original.next_run_at)
            : '-',
        size: 160,
        meta: { headerTitle: 'Next Run' },
      },
      {
        accessorKey: 'last_run_at',
        header: ({ column }) => <DataGridColumnHeader title="Last Run" column={column} />,
        cell: ({ row }) =>
          row.original.last_run_at
            ? formatDateTimeInMalaysia(row.original.last_run_at)
            : '-',
        size: 160,
        meta: { headerTitle: 'Last Run' },
      },
      {
        accessorKey: 'last_status',
        header: ({ column }) => <DataGridColumnHeader title="Last Status" column={column} />,
        cell: ({ row }) => {
          const status = row.original.last_status || '-';
          return (
            <Badge status={status}>
              {status}
            </Badge>
          );
        },
        size: 100,
        meta: { headerTitle: 'Last Status' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => router.push(`/system-management/scheduled-tasks/${row.original.id}`)}
          >
            <ChevronRight className="size-4" />
          </Button>
        ),
        size: 60,
        enableHiding: false,
      },
    ],
    [router],
  );

  const table = useReactTable({
    data: tasks,
    columns,
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: (updater) => {
      const next = typeof updater === 'function' ? updater(pagination) : updater;
      setPagination(next);
    },
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: false,
    pageCount: Math.ceil(total / pagination.pageSize) || 1,
  });

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      onRowClick={(task) => task?.id && router.push(`/system-management/scheduled-tasks/${task.id}`)}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            exportConfig={{ filename: 'scheduled_tasks_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter className="flex justify-between border-t px-4 py-3">
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
