'use client';

import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  VisibilityState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import {
  AlertCircle,
  CheckCircle,
  ChevronRight,
  Clock,
  Columns3,
  RefreshCw,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import {
  formatDateTime,
  formatDuration,
  formatDurationWithSeconds,
  parseDateTimeAsUTC,
} from '@/lib/helpers';
import {
  getFormSLATrackers,
  type FormSLASourceType,
} from './formSLAService';
import type { ConversationSLATrackingDetail } from '@/app/(protected)/sla-management/conversation-sla-tracking/types/conversationSLATracking.types';
import FormSLATrackerDetail from './FormSLATrackerDetail';

interface FormSLATrackingTabProps {
  sourceEntityType: FormSLASourceType;
  sourceEntityId: string;
}

function isResponseOverdue(o: ConversationSLATrackingDetail) {
  const due = parseDateTimeAsUTC(o.due_at).getTime();
  const now = Date.now();
  if (o.is_responded && o.responded_at) {
    return parseDateTimeAsUTC(o.responded_at).getTime() > due;
  }
  return now > due;
}

function isResolutionOverdue(o: ConversationSLATrackingDetail) {
  const dueRes = o.due_at_resolution ?? o.resolution_due_at;
  if (!dueRes) return false;
  const due = parseDateTimeAsUTC(dueRes).getTime();
  const now = Date.now();
  if (o.is_resolved && o.resolved_at) {
    return parseDateTimeAsUTC(o.resolved_at).getTime() > due;
  }
  return now > due;
}

function getTimeElapsed(o: ConversationSLATrackingDetail): string {
  if (o.is_resolved && o.resolved_at && o.initiated_at) {
    const ms =
      parseDateTimeAsUTC(o.resolved_at).getTime() -
      parseDateTimeAsUTC(o.initiated_at).getTime();
    return formatDurationWithSeconds(ms);
  }
  const ms = Date.now() - parseDateTimeAsUTC(o.initiated_at).getTime();
  return formatDurationWithSeconds(ms);
}

function getResponseDuration(o: ConversationSLATrackingDetail): string | null {
  if (!o.is_responded || !o.responded_at || !o.initiated_at) return null;
  const ms =
    parseDateTimeAsUTC(o.responded_at).getTime() -
    parseDateTimeAsUTC(o.initiated_at).getTime();
  return formatDurationWithSeconds(ms);
}

function getTimeRemaining(
  dueAt: string | Date,
  backendSeconds?: number | null,
) {
  if (backendSeconds !== undefined && backendSeconds !== null) {
    return formatDuration(Math.abs(backendSeconds) * 1000);
  }
  const now = Date.now();
  const due = parseDateTimeAsUTC(dueAt).getTime();
  return formatDuration(Math.abs(due - now));
}

function getTimeRemainingPercent(
  dueAt: string | Date,
  currentTierStartedAt: string | Date,
) {
  const now = Date.now();
  const due = parseDateTimeAsUTC(dueAt).getTime();
  const started = parseDateTimeAsUTC(currentTierStartedAt).getTime();
  const total = due - started;
  const remaining = due - now;
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (remaining / total) * 100));
}

function formatSecondsToDuration(seconds: number | null | undefined) {
  if (seconds == null) return '-';
  return formatDuration(seconds * 1000);
}

function formatSecondsToDurationWithSeconds(
  seconds: number | null | undefined,
) {
  if (seconds == null) return '-';
  return formatDurationWithSeconds(seconds * 1000);
}

export default function FormSLATrackingTab({
  sourceEntityType,
  sourceEntityId,
}: FormSLATrackingTabProps) {
  const queryClient = useQueryClient();
  const [selectedTrackerId, setSelectedTrackerId] = useState<string | null>(null);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'initiated_at', desc: false },
  ]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    agent_code: false,
    team_set_code: true,
  });

  const queryKey = useMemo(
    () => ['form-sla-trackers', sourceEntityType, sourceEntityId] as const,
    [sourceEntityType, sourceEntityId],
  );
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey,
    queryFn: () => getFormSLATrackers(sourceEntityType, sourceEntityId),
    enabled: !!sourceEntityId,
  });

  const handleRowClick = (row: ConversationSLATrackingDetail) => {
    setSelectedTrackerId(row.id);
  };

  const handleRefresh = async () => {
    await queryClient.invalidateQueries({ queryKey });
  };

  const columns = useMemo<ColumnDef<ConversationSLATrackingDetail>[]>(
    () => [
      {
        accessorKey: 'team_set_code',
        header: ({ column }) => (
          <DataGridColumnHeader title="Stage" column={column} />
        ),
        cell: ({ row }) => row.original.team_set_code || '-',
        size: 140,
      },
      {
        accessorKey: 'policy_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Policy" column={column} />
        ),
        cell: ({ row }) =>
          row.original.policy?.name ||
          row.original.policy_name ||
          row.original.policy?.code ||
          row.original.policy_code ||
          '-',
        size: 180,
      },
      {
        accessorKey: 'current_tier',
        header: ({ column }) => (
          <DataGridColumnHeader title="Tier" column={column} />
        ),
        cell: ({ row }) => (
          <Badge variant="secondary">Tier {row.original.current_tier}</Badge>
        ),
        size: 100,
      },
      {
        accessorKey: 'assigned_user_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Assigned To" column={column} />
        ),
        cell: ({ row }) =>
          row.original.assigned_user_name ||
          row.original.assigned_user?.name ||
          row.original.assigned_user?.email ||
          row.original.assigned_to ||
          '-',
        size: 160,
        meta: { skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'initiated_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Initiated At" column={column} />
        ),
        cell: ({ row }) =>
          formatDateTime(parseDateTimeAsUTC(row.original.initiated_at)),
        size: 170,
      },
      {
        accessorKey: 'due_at',
        header: ({ column }) => (
          <DataGridColumnHeader title="Due at (response)" column={column} />
        ),
        cell: ({ row }) => {
          const o = row.original;
          const overdue = isResponseOverdue(o);
          return (
            <div className="space-y-1">
              <div className={overdue ? 'text-destructive font-medium' : ''}>
                {formatDateTime(parseDateTimeAsUTC(o.due_at))}
              </div>
              {overdue && (
                <div className="flex items-center gap-1 text-xs text-destructive">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 170,
      },
      {
        accessorKey: 'due_at_resolution',
        header: ({ column }) => (
          <DataGridColumnHeader title="Due at (resolution)" column={column} />
        ),
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
                <div className="flex items-center gap-1 text-xs text-destructive">
                  <AlertCircle className="size-3" />
                  Overdue
                </div>
              )}
            </div>
          );
        },
        size: 180,
      },
      {
        accessorKey: 'time_elapsed',
        header: ({ column }) => (
          <DataGridColumnHeader title="Time elapsed" column={column} />
        ),
        cell: ({ row }) => (
          <span className="text-sm">{getTimeElapsed(row.original)}</span>
        ),
        size: 120,
      },
      {
        accessorKey: 'time_remaining_response',
        header: ({ column }) => (
          <DataGridColumnHeader title="Response" column={column} />
        ),
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_responded) {
            const overdue = isResponseOverdue(o);
            const str =
              getResponseDuration(o) ??
              (o.response_time != null
                ? formatDurationWithSeconds(
                    Number(o.response_time) * 3600 * 1000,
                  )
                : '-');
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">Response time</div>
                <span
                  className={`text-sm font-medium ${
                    overdue ? 'text-destructive' : 'text-green-600'
                  }`}
                >
                  {str}
                </span>
              </div>
            );
          }
          const overdue = isResponseOverdue(o);
          const str =
            o.time_remaining_response_seconds != null
              ? formatDuration(
                  Math.abs(o.time_remaining_response_seconds) * 1000,
                )
              : getTimeRemaining(o.due_at);
          return (
            <div className="w-40 space-y-1">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <div className="flex items-center gap-2">
                <Clock
                  className={`size-4 ${
                    overdue ? 'text-destructive' : 'text-muted-foreground'
                  }`}
                />
                <span
                  className={`text-sm ${
                    overdue ? 'text-destructive font-medium' : ''
                  }`}
                >
                  {overdue ? `${str} overdue` : `${str} left`}
                </span>
              </div>
              <Progress
                value={getTimeRemainingPercent(
                  o.due_at,
                  o.current_tier_started_at,
                )}
                className="h-2"
              />
            </div>
          );
        },
        size: 220,
      },
      {
        accessorKey: 'time_remaining_resolution',
        header: ({ column }) => (
          <DataGridColumnHeader title="Resolution" column={column} />
        ),
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_resolved) {
            const overdue = isResolutionOverdue(o);
            const hours = Number(o.resolution_duration) || 0;
            const str =
              hours < 1
                ? formatSecondsToDurationWithSeconds(hours * 3600)
                : `${hours.toFixed(1)}h`;
            return (
              <div className="space-y-0.5">
                <div className="text-xs text-muted-foreground">
                  Resolution duration
                </div>
                <span
                  className={`text-sm font-medium ${
                    overdue ? 'text-destructive' : 'text-green-600'
                  }`}
                >
                  {str}
                </span>
              </div>
            );
          }
          const sec = o.time_remaining_resolution_seconds;
          const str = formatSecondsToDuration(sec ?? undefined);
          const overdue = isResolutionOverdue(o);
          return (
            <div className="space-y-0.5">
              <div className="text-xs text-muted-foreground">Time remaining</div>
              <span
                className={`text-sm ${
                  overdue ? 'text-destructive font-medium' : ''
                }`}
              >
                {overdue ? 'Overdue' : `${str} left`}
              </span>
            </div>
          );
        },
        size: 200,
      },
      {
        accessorKey: 'agent_code',
        header: ({ column }) => (
          <DataGridColumnHeader title="Agent" column={column} />
        ),
        cell: ({ row }) => row.original.agent_code || '-',
        size: 140,
      },
      {
        accessorKey: 'is_resolved',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        cell: ({ row }) => {
          const o = row.original;
          if (o.is_resolved) {
            return (
              <Badge variant="success">
                <CheckCircle className="mr-1 size-3" />
                Resolved
              </Badge>
            );
          }
          if (o.escalated_at) {
            return (
              <Badge variant="warning">
                <AlertCircle className="mr-1 size-3" />
                Escalated
              </Badge>
            );
          }
          return (
            <Badge variant="info">
              <Clock className="mr-1 size-3" />
              Pending
            </Badge>
          );
        },
        size: 120,
      },
      {
        accessorKey: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: () => (
          <div className="flex items-center justify-end">
            <ChevronRight className="size-3.5 text-muted-foreground/70" />
          </div>
        ),
        size: 60,
      },
    ],
    [],
  );

  const trackers = data || [];
  const table = useReactTable({
    columns,
    data: trackers,
    pageCount: Math.ceil(trackers.length / pagination.pageSize) || 1,
    getRowId: (row) => row.id,
    state: { pagination, sorting, columnVisibility },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  if (selectedTrackerId) {
    return (
      <FormSLATrackerDetail
        trackingId={selectedTrackerId}
        onBack={() => setSelectedTrackerId(null)}
      />
    );
  }

  if (!isLoading && trackers.length === 0) {
    return (
      <Card>
        <CardHeader className="py-6">
          <div className="text-sm text-muted-foreground">
            No SLA trackers attached to this{' '}
            {sourceEntityType.replace('_', ' ')}. Configure form SLA stages in{' '}
            <strong>SLA Management → Form SLA Configuration</strong> and trigger
            the form&apos;s start event (e.g. submit) to spawn the first tracker.
          </div>
        </CardHeader>
      </Card>
    );
  }

  return (
    <DataGrid
      table={table}
      tableLayout={{ columnsVisibility: true }}
      recordCount={trackers.length}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      onRowClick={handleRowClick}
      onRefresh={() => void refetch()}
      isRefreshing={isFetching && !isLoading}
    >
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2 py-4">
          <div className="text-sm text-muted-foreground">
            {trackers.length} tracker{trackers.length === 1 ? '' : 's'} for this{' '}
            {sourceEntityType.replace('_', ' ')}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={isFetching}
            >
              <RefreshCw
                className={`mr-2 size-4 ${isFetching ? 'animate-spin' : ''}`}
              />
              Refresh
            </Button>
            <DataGridColumnVisibility
              table={table}
              trigger={
                <Button variant="outline" size="sm" className="gap-1">
                  <Columns3 className="size-4" />
                  Columns
                </Button>
              }
            />
          </div>
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
