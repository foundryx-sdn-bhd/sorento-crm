'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  VisibilityState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { AlertCircle, CheckCircle, ChevronRight, Clock } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { useFormSLATracking } from '../hooks/useFormSLATracking';
import type { FormSLATracking } from '../types/formSLATracking.types';
import { formatDateTime, formatDuration, formatDurationWithSeconds, parseDateTimeAsUTC } from '@/lib/helpers';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

const ENTITY_TYPE_LABELS: Record<string, string> = {
  stock_inquiry: 'Stock Inquiry',
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
  complaint: 'Complaint',
  ticket: 'Ticket',
};

export default function FormSLATrackingList() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    team_set_code: false,
  });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [searchQuery]);

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useFormSLATracking({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  const handleRefresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['form-sla-tracking'] });
  };

  const handleRowClick = (row: FormSLATracking) => {
    router.push(`/sla-management/form-sla-tracking/${row.id}`);
  };

  const getTimeRemaining = (dueAt: string | Date, backendSeconds?: number | null) => {
    if (backendSeconds !== undefined && backendSeconds !== null) {
      return formatDuration(Math.abs(backendSeconds) * 1000);
    }
    const now = Date.now();
    const due = parseDateTimeAsUTC(dueAt).getTime();
    return formatDuration(Math.abs(due - now));
  };

  const getTimeRemainingPercent = (dueAt: string | Date, currentTierStartedAt: string | Date) => {
    const now = Date.now();
    const due = parseDateTimeAsUTC(dueAt).getTime();
    const started = parseDateTimeAsUTC(currentTierStartedAt).getTime();
    const total = due - started;
    const remaining = due - now;
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, (remaining / total) * 100));
  };

  const isResponseOverdue = (o: FormSLATracking) => {
    const due = parseDateTimeAsUTC(o.due_at).getTime();
    const now = Date.now();
    if (o.is_responded && o.responded_at) {
      return parseDateTimeAsUTC(o.responded_at).getTime() > due;
    }
    return now > due;
  };

  const isResolutionOverdue = (o: FormSLATracking) => {
    const dueRes = o.due_at_resolution ?? o.resolution_due_at;
    if (!dueRes) return false;
    const due = parseDateTimeAsUTC(dueRes).getTime();
    const now = Date.now();
    if (o.is_resolved && o.resolved_at) {
      return parseDateTimeAsUTC(o.resolved_at).getTime() > due;
    }
    return now > due;
  };

  const getTimeElapsed = (o: FormSLATracking): string => {
    if (o.is_resolved && o.resolved_at && o.initiated_at) {
      const ms = parseDateTimeAsUTC(o.resolved_at).getTime() - parseDateTimeAsUTC(o.initiated_at).getTime();
      return formatDurationWithSeconds(ms);
    }
    const ms = Date.now() - parseDateTimeAsUTC(o.initiated_at).getTime();
    return formatDurationWithSeconds(ms);
  };

  const getResponseDuration = (o: FormSLATracking): string | null => {
    if (!o.is_responded || !o.responded_at || !o.initiated_at) return null;
    const ms = parseDateTimeAsUTC(o.responded_at).getTime() - parseDateTimeAsUTC(o.initiated_at).getTime();
    return formatDurationWithSeconds(ms);
  };

  const formatSecondsToDuration = (seconds: number | null | undefined) => {
    if (seconds == null) return '-';
    return formatDuration(seconds * 1000);
  };
  const formatSecondsToDurationWithSeconds = (seconds: number | null | undefined) => {
    if (seconds == null) return '-';
    return formatDurationWithSeconds(seconds * 1000);
  };

  const columns = useMemo<ColumnDef<FormSLATracking>[]>(
    () => [
      buildSelectColumn<FormSLATracking>(),
      {
        accessorKey: 'reference',
        header: ({ column }) => <DataGridColumnHeader title="Reference" column={column} />,
        cell: ({ row }) => row.original.reference || '-',
        size: 180,
        meta: { headerTitle: 'Reference', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'source_entity_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => {
          const et = row.original.source_entity_type;
          return et ? ENTITY_TYPE_LABELS[et] || et : '-';
        },
        size: 160,
        meta: { headerTitle: 'Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'policy_name',
        header: ({ column }) => <DataGridColumnHeader title="Policy" column={column} />,
        cell: ({ row }) =>
          row.original.policy?.name ||
          row.original.policy_name ||
          row.original.policy?.code ||
          row.original.policy_code ||
          '-',
        size: 180,
        meta: { headerTitle: 'Policy', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'current_tier',
        header: ({ column }) => <DataGridColumnHeader title="Current Tier" column={column} />,
        cell: ({ row }) => <Badge variant="secondary">Tier {row.original.current_tier}</Badge>,
        size: 120,
        meta: { headerTitle: 'Current Tier' },
      },
      {
        accessorKey: 'next_action',
        header: ({ column }) => <DataGridColumnHeader title="Next action" column={column} />,
        cell: ({ row }) => row.original.next_action || '-',
        size: 180,
        meta: { headerTitle: 'Next action', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'assigned_user_name',
        header: ({ column }) => <DataGridColumnHeader title="Assigned To" column={column} />,
        cell: ({ row }) =>
          row.original.assigned_user_name ||
          row.original.assigned_user?.name ||
          row.original.assigned_user?.email ||
          row.original.assigned_to ||
          '-',
        size: 160,
        meta: { headerTitle: 'Assigned To', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'initiated_at',
        header: ({ column }) => <DataGridColumnHeader title="Initiated At" column={column} />,
        cell: ({ row }) => formatDateTime(parseDateTimeAsUTC(row.original.initiated_at)),
        size: 180,
        meta: { headerTitle: 'Initiated At' },
      },
      {
        accessorKey: 'due_at',
        header: ({ column }) => <DataGridColumnHeader title="Due at (response)" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          const overdue = isResponseOverdue(o);
          return (
            <div className="space-y-1">
              <div className={overdue ? 'text-destructive font-medium' : ''}>
                {formatDateTime(parseDateTimeAsUTC(o.due_at))}
              </div>
              {overdue && (
                <div className="text-xs text-destructive flex items-center gap-1">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 180,
        meta: { headerTitle: 'Due at (response)' },
      },
      {
        accessorKey: 'due_at_resolution',
        header: ({ column }) => <DataGridColumnHeader title="Due at (resolution)" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          const dueRes = o.due_at_resolution ?? o.resolution_due_at;
          if (!dueRes) return '-';
          const overdue = isResolutionOverdue(o);
          return (
            <div className="space-y-1">
              <div className={overdue ? 'text-destructive font-medium' : ''}>
                {formatDateTime(parseDateTimeAsUTC(dueRes))}
              </div>
              {overdue && (
                <div className="text-xs text-destructive flex items-center gap-1">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 180,
        meta: { headerTitle: 'Due at (resolution)' },
      },
      {
        accessorKey: 'time_elapsed',
        header: ({ column }) => <DataGridColumnHeader title="Time elapsed" column={column} />,
        cell: ({ row }) => <span className="text-sm">{getTimeElapsed(row.original)}</span>,
        size: 120,
        meta: { headerTitle: 'Time elapsed' },
      },
      {
        accessorKey: 'time_remaining_response',
        header: ({ column }) => <DataGridColumnHeader title="Response" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_responded) {
            const overdue = isResponseOverdue(o);
            const str =
              getResponseDuration(o) ??
              (o.response_time != null
                ? formatDurationWithSeconds(Number(o.response_time) * 3600 * 1000)
                : '-');
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">Response time</div>
                <span className={`text-sm font-medium ${overdue ? 'text-destructive' : 'text-green-600'}`}>{str}</span>
              </div>
            );
          }
          const overdue = isResponseOverdue(o);
          const str =
            o.time_remaining_response_seconds != null
              ? formatDuration(Math.abs(o.time_remaining_response_seconds) * 1000)
              : getTimeRemaining(o.due_at);
          return (
            <div className="space-y-1 w-40">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <div className="flex items-center gap-2">
                <Clock className={`size-4 ${overdue ? 'text-destructive' : 'text-muted-foreground'}`} />
                <span className={`text-sm ${overdue ? 'text-destructive font-medium' : ''}`}>
                  {overdue ? `${str} overdue` : `${str} left`}
                </span>
              </div>
              <Progress value={getTimeRemainingPercent(o.due_at, o.current_tier_started_at)} className="h-2" />
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Response' },
      },
      {
        accessorKey: 'time_remaining_resolution',
        header: ({ column }) => <DataGridColumnHeader title="Resolution" column={column} />,
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_resolved) {
            const overdue = isResolutionOverdue(o);
            const hours = Number(o.resolution_duration) || 0;
            const str = hours < 1 ? formatSecondsToDurationWithSeconds(hours * 3600) : `${hours.toFixed(1)}h`;
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">Resolution duration</div>
                <span className={`text-sm font-medium ${overdue ? 'text-destructive' : 'text-green-600'}`}>{str}</span>
              </div>
            );
          }
          const sec = o.time_remaining_resolution_seconds;
          const str = formatSecondsToDuration(sec ?? undefined);
          const overdue = isResolutionOverdue(o);
          return (
            <div className="space-y-0.5">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <span className={`text-sm ${overdue ? 'text-destructive font-medium' : ''}`}>
                {overdue ? 'Overdue' : `${str} left`}
              </span>
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Resolution' },
      },
      {
        accessorKey: 'team_set_code',
        header: ({ column }) => <DataGridColumnHeader title="Team Set Code" column={column} />,
        cell: ({ row }) => row.original.team_set_code || '-',
        size: 160,
        meta: { headerTitle: 'Team Set Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'is_resolved',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          if (row.original.is_resolved) {
            return (
              <Badge variant="success">
                <CheckCircle className="size-3 mr-1" />
                Resolved
              </Badge>
            );
          }
          if (row.original.escalated_at) {
            return (
              <Badge variant="warning">
                <AlertCircle className="size-3 mr-1" />
                Escalated
              </Badge>
            );
          }
          return (
            <Badge variant="info">
              <Clock className="size-3 mr-1" />
              Pending
            </Badge>
          );
        },
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: () => <ChevronRight className="text-muted-foreground/70 size-3.5" />,
        size: 60,
        enableHiding: false,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, columnVisibility, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  return (
    <DataGrid
      table={table}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      onRowClick={handleRowClick}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search by reference, type, or policy..."
                className="w-64 min-w-[140px] max-w-[280px]"
              />
            }
            exportConfig={{ filename: 'form_sla_tracking_export.xlsx' }}
            onRefresh={handleRefresh}
            isRefreshing={isFetching && !isLoading}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
