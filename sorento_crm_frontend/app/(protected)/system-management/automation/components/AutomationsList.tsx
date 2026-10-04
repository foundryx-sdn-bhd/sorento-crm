'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  RowSelectionState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { ChevronRight, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
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
import { Switch } from '@/components/ui/switch';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  useAutomations,
  useRunAutomationNow,
  useToggleAutomation,
} from '../hooks/useAutomations';
import type { Automation } from '../types/automation.types';
import AutomationForm from './AutomationForm';

function ToggleCell({ row }: { row: { original: Automation } }) {
  const mut = useToggleAutomation(row.original.id);
  return (
    <Switch
      checked={row.original.enabled}
      onCheckedChange={(c) => mut.mutate(c)}
      aria-label="Toggle enabled"
    />
  );
}

function RunNowCell({ row }: { row: { original: Automation } }) {
  const mut = useRunAutomationNow(row.original.id);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={mut.isPending}
      onClick={async (e) => {
        e.stopPropagation();
        try {
          const r = await mut.mutateAsync();
          toast.success(`Run queued: ${r.recipients_attempted} recipient(s) attempted`);
        } catch (err) {
          toast.error((err as Error).message || 'Run failed');
        }
      }}
    >
      <Play className="size-3 mr-1" /> Run
    </Button>
  );
}

export default function AutomationsList() {
  const router = useRouter();
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 50 });
  const {
    value: queryInput,
    setValue: setQueryInput,
    debouncedValue: query,
    isSettling: querySettling,
  } = useDebouncedSearch();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Automation | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [query]);

  const params = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      query: query.trim() || undefined,
    }),
    [pagination, query],
  );

  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useAutomations(params);
  const rows = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'automation.delete',
    entityType: 'automation',
    successMessage: 'Automation deleted',
    invalidateKeys: [['automations']],
  });
  const rowPending = useRowPending<Automation>('automation');

  const columns = useMemo<ColumnDef<Automation>[]>(
    () => [
      buildSelectColumn<Automation>(),
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium" title={row.original.name}>
              {row.original.name}
            </span>
            {row.original.description && (
              <span className="text-xs text-muted-foreground truncate max-w-[260px]" title={row.original.description}>
                {row.original.description}
              </span>
            )}
          </div>
        ),
        size: 240,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-8 w-32" /> },
      },
      {
        accessorKey: 'trigger_type',
        header: ({ column }) => <DataGridColumnHeader title="Trigger" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.trigger_type}
          </Badge>
        ),
        size: 180,
        meta: { headerTitle: 'Trigger' },
      },
      {
        accessorKey: 'schedule',
        header: ({ column }) => <DataGridColumnHeader title="Schedule" column={column} />,
        cell: ({ row }) => {
          const a = row.original;
          if (a.schedule_type === 'daily') {
            return <span>Daily {a.run_time?.slice(0, 5) ?? ''} {a.timezone}</span>;
          }
          return <span className="text-muted-foreground">Manual</span>;
        },
        size: 220,
        meta: { headerTitle: 'Schedule' },
      },
      {
        accessorKey: 'email_template_name',
        header: ({ column }) => <DataGridColumnHeader title="Template" column={column} />,
        cell: ({ row }) => (
          <span className="truncate max-w-[180px] block" title={row.original.email_template_name ?? ''}>
            {row.original.email_template_name ?? '-'}
          </span>
        ),
        size: 200,
        meta: { headerTitle: 'Template' },
      },
      {
        accessorKey: 'enabled',
        header: ({ column }) => <DataGridColumnHeader title="Enabled" column={column} />,
        cell: ToggleCell,
        size: 80,
        meta: { headerTitle: 'Enabled' },
      },
      {
        accessorKey: 'last_status',
        header: ({ column }) => <DataGridColumnHeader title="Last status" column={column} />,
        cell: ({ row }) => row.original.last_status || '-',
        size: 110,
        meta: { headerTitle: 'Last status' },
      },
      {
        accessorKey: 'next_run_at',
        header: ({ column }) => <DataGridColumnHeader title="Next run" column={column} />,
        cell: ({ row }) => (row.original.next_run_at ? formatDateTimeInMalaysia(row.original.next_run_at) : '-'),
        size: 160,
        meta: { headerTitle: 'Next run' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1">
            <RunNowCell row={row} />
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(row.original);
                setShowForm(true);
              }}
              aria-label="Edit"
            >
              <Pencil className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0 text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                deletion.run({ id: row.original.id, subject: row.original.name });
              }}
              aria-label="Delete"
            >
              <Trash2 className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={(e) => {
                e.stopPropagation();
                router.push(`/system-management/automation/${row.original.id}`);
              }}
              aria-label="View"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        ),
        size: 220,
        enableHiding: false,
      },
    ],
    [router, deletion],
  );

  const table = useReactTable({
    data: rows,
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
    manualPagination: true,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setEditing(null);
        setShowForm(true);
      }}
    >
      <Plus className="mr-1 size-4" /> Add automation
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      onRowClick={(t) => t?.id && router.push(`/system-management/automation/${t.id}`)}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      rowPending={rowPending}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={queryInput}
                onChange={setQueryInput}
                isSettling={isSearchInFlight(querySettling, isFetching, query)}
                placeholder="Search by name"
                className="w-64"
              />
            }
            exportConfig={{ filename: 'automations_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter className="flex justify-between border-t px-4 py-3">
          <DataGridPagination />
        </CardFooter>
      </Card>

      <AutomationForm
        open={showForm}
        onOpenChange={(o) => {
          setShowForm(o);
          if (!o) setEditing(null);
        }}
        automation={editing}
      />
    </DataGrid>
  );
}
