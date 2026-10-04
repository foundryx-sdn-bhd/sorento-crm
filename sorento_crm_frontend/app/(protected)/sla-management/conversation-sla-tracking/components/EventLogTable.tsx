'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  Row,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Card, CardContent, CardFooter, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Trash2 } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { DatePicker } from '@/components/ui/date-picker';
import { Label } from '@/components/ui/label';
import { toast } from '@/lib/toast';
import { useConversationSLAEventLogs, useDeleteConversationSLAEventLog } from '../hooks/useConversationSLATracking';
import { formatDateTime, formatDuration, parseDateTimeAsUTC } from '@/lib/helpers';
import type { ConversationSLAEventLog } from '../types/conversationSLATracking.types';
import { useQuery } from '@tanstack/react-query';
import { getUserLookup } from '@/services/userSelectService';
import { apiFetch } from '@/lib/api';

function toYYYYMMDD(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export const EVENT_TYPE_OPTIONS = [
  { value: '__all__', label: 'All' },
  { value: 'assign', label: 'Assign' },
  { value: 'adjust', label: 'Adjust' },
  { value: 'escalation', label: 'Escalation' },
  { value: 'reassignment', label: 'Reassignment' },
  { value: 'response', label: 'Response' },
  { value: 'resolution', label: 'Resolution' },
  { value: 'extend', label: 'Extend' },
];

/**
 * Human label for the Duration column, per event type.
 * - extend: `duration` is WORKING DAYS added, and from_time is the PREVIOUS (future)
 *   due - so event_at - from_time would be negative. Show "+N working day(s)".
 * - response/resolution: elapsed time from from_time (initiated_at) to event_at.
 * - escalation/assign/adjust/reassignment: no meaningful elapsed span -> em dash.
 */
export function formatEventDuration(log: {
  event_type?: string | null;
  from_time?: Date | string | null;
  event_at?: Date | string | null;
  duration?: number | null;
}): string {
  const eventType = (log.event_type ?? '').toLowerCase();

  if (eventType === 'extend') {
    if (log.duration !== null && log.duration !== undefined) {
      const days = Number(log.duration);
      return `+${days} working day${days === 1 ? '' : 's'}`;
    }
    return '-';
  }

  if (log.from_time && log.event_at) {
    const fromTime = parseDateTimeAsUTC(log.from_time);
    const eventAt = parseDateTimeAsUTC(log.event_at);
    const diff = eventAt.getTime() - fromTime.getTime();
    if (diff >= 0) return formatDuration(diff);
  }

  // Stored-duration fallback (hours) for response/resolution rows lacking from_time.
  if (
    eventType !== 'escalation' &&
    eventType !== 'assign' &&
    eventType !== 'adjust' &&
    eventType !== 'reassignment' &&
    log.duration !== null &&
    log.duration !== undefined
  ) {
    return formatDuration(log.duration * 3600 * 1000);
  }
  return '-';
}

interface EventLogTableProps {
  trackingId: string;
  agentCode?: string | null;
  teamSetCode?: string | null;
}

export default function EventLogTable({ trackingId, agentCode, teamSetCode }: EventLogTableProps) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'event_at', desc: true }]);
  const [eventTypeFilter, setEventTypeFilter] = useState('__all__');
  const [dateFrom, setDateFrom] = useState<Date | undefined>(undefined);
  const [dateTo, setDateTo] = useState<Date | undefined>(undefined);
  const [assignedToFilter, setAssignedToFilter] = useState('__all__');
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [logToDelete, setLogToDelete] = useState<ConversationSLAEventLog | null>(null);
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const deleteMutation = useDeleteConversationSLAEventLog();

  const {
    data: eventLogsResponse,
    isLoading,
    isPlaceholderData,
    error,
    refetch,
  } = useConversationSLAEventLogs(trackingId, {
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
    sort: sorting?.[0]?.id || 'event_at',
    dir: sorting?.[0]?.desc ? 'desc' : 'asc',
    event_type: eventTypeFilter && eventTypeFilter !== '__all__' ? eventTypeFilter : undefined,
    date_from: dateFrom ? toYYYYMMDD(dateFrom) : undefined,
    date_to: dateTo ? toYYYYMMDD(dateTo) : undefined,
    assigned_to_id: assignedToFilter && assignedToFilter !== '__all__' ? assignedToFilter : undefined,
  });

  const eventLogs = eventLogsResponse?.data ?? [];
  const totalCount = eventLogsResponse?.pagination?.total ?? 0;

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [eventTypeFilter, dateFrom, dateTo, assignedToFilter]);

  const {
    data: respondUsers,
    error: respondUsersError,
    refetch: refetchRespondUsers,
  } = useQuery({
    queryKey: ['user-lookup', 'respond-synced', 'with-inactive'],
    queryFn: () => getUserLookup({ respond_synced: true, include_inactive: true }),
    staleTime: 1000 * 60 * 5,
  });

  const users = respondUsers ?? [];

  const { data: currentUser } = useQuery({
    queryKey: ['account-profile'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/account/');
      if (!response.ok) return null;
      return response.json();
    },
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
  });

  const isAdmin = currentUser?.role === 'admin';

  const hasActiveFilters = Boolean(
    (eventTypeFilter && eventTypeFilter !== '__all__') ||
    dateFrom != null ||
    dateTo != null ||
    (assignedToFilter && assignedToFilter !== '__all__'),
  );

  const handleClearFilters = () => {
    setEventTypeFilter('__all__');
    setDateFrom(undefined);
    setDateTo(undefined);
    setAssignedToFilter('__all__');
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  const handleDeleteClick = (log: ConversationSLAEventLog) => {
    setLogToDelete(log);
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = async () => {
    if (!logToDelete) return;
    
    try {
      await deleteMutation.mutateAsync(logToDelete.id);
      toast.success('Event log deleted successfully');
      setDeleteDialogOpen(false);
      setLogToDelete(null);
    } catch (error: any) {
      toast.error(error.message || 'Failed to delete event log');
    }
  };

  const columns = useMemo<ColumnDef<ConversationSLAEventLog>[]>(
    () => [
      buildSelectColumn<ConversationSLAEventLog>(),
      {
        accessorKey: 'event_type',
        header: ({ column }) => <DataGridColumnHeader title="Event Type" column={column} />,
        cell: ({ row }) => {
          const eventType = row.original.event_type;
          return <span className="capitalize">{eventType}</span>;
        },
        size: 120,
        meta: { skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'from_tier',
        header: ({ column }) => <DataGridColumnHeader title="From Tier" column={column} />,
        cell: ({ row }) => row.original.from_tier ? `Tier ${row.original.from_tier}` : '-',
        size: 100,
        meta: { skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'to_tier',
        header: ({ column }) => <DataGridColumnHeader title="To Tier" column={column} />,
        cell: ({ row }) => row.original.to_tier ? `Tier ${row.original.to_tier}` : '-',
        size: 100,
        meta: { skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'event_at',
        header: ({ column }) => <DataGridColumnHeader title="Event At" column={column} />,
        cell: ({ row }) => formatDateTime(parseDateTimeAsUTC(row.original.event_at)),
        size: 200,
      },
      {
        accessorKey: 'from_time',
        header: ({ column }) => <DataGridColumnHeader title="From Time" column={column} />,
        cell: ({ row }) => row.original.from_time ? formatDateTime(parseDateTimeAsUTC(row.original.from_time)) : '-',
        size: 200,
      },
      {
        accessorKey: 'duration',
        header: ({ column }) => <DataGridColumnHeader title="Duration" column={column} />,
        cell: ({ row }) => formatEventDuration(row.original),
        size: 120,
      },
      {
        accessorKey: 'reason',
        header: ({ column }) => <DataGridColumnHeader title="Reason" column={column} />,
        size: 300,
        meta: { skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        id: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent Code" column={column} />,
        cell: () => agentCode || '-',
        size: 150,
        meta: { skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'team_set_code',
        header: ({ column }) => <DataGridColumnHeader title="Team Set Code" column={column} />,
        cell: () => teamSetCode || '-',
        size: 160,
        meta: { skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'assigned_user_name',
        header: ({ column }) => <DataGridColumnHeader title="Assigned To" column={column} />,
        cell: ({ row }) => {
          const userName = row.original.assigned_user_name || 
                          row.original.assigned_user?.name || 
                          row.original.assigned_user?.email || 
                          row.original.assigned_to || 
                          '-';
          return userName;
        },
        size: 150,
      },
      ...(isAdmin ? [{
        id: 'actions',
        header: () => <div className="text-right">Actions</div>,
        cell: ({ row }: { row: Row<ConversationSLAEventLog> }) => (
          <div className="flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleDeleteClick(row.original)}
              className="h-8 w-8 p-0 text-destructive hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ),
        size: 80,
      }] : []),
    ],
    [isAdmin, agentCode, teamSetCode],
  );

  const table = useReactTable({
    columns,
    data: eventLogs,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    pageCount: Math.ceil(totalCount / pagination.pageSize) || 0,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    getRowId: (row, index) => (row as { id?: string }).id ?? String(index),
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    manualSorting: true,
  });

  if (isLoading && eventLogs.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Event Log</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">Loading event log...</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <DataGrid
        table={table}
        recordCount={totalCount}
        isLoading={isLoading}
        isPlaceholderData={isPlaceholderData}
        error={error}
        onRetry={() => void refetch()}
        tableLayout={{ columnsVisibility: true }}
        standardToolbar={false}
      >
        <Card>
          <CardHeader className="block space-y-3">
            <CardTitle>Event Log</CardTitle>
            <DataGridListToolbar
              table={table}
              filters={{
                kind: 'custom',
                active: hasActiveFilters,
                content: (
                  <div className="space-y-4">
                    <h4 className="text-sm font-medium">Advanced filters</h4>
                    <div className="space-y-2">
                      <Label className="text-xs">Event Type</Label>
                      <SearchableSelect
                        value={eventTypeFilter}
                        onChange={(v) => {
                          setEventTypeFilter(v);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        options={EVENT_TYPE_OPTIONS.map((opt) => ({
                          value: opt.value,
                          label: opt.label,
                        }))}
                        placeholder="All"
                        triggerClassName="w-full"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs">Date range (Event At)</Label>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-xs text-muted-foreground">From</Label>
                          <DatePicker
                            value={dateFrom}
                            onChange={(d) => {
                              setDateFrom(d ?? undefined);
                              setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                            }}
                            placeholder="From"
                          />
                        </div>
                        <div>
                          <Label className="text-xs text-muted-foreground">To</Label>
                          <DatePicker
                            value={dateTo}
                            onChange={(d) => {
                              setDateTo(d ?? undefined);
                              setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                            }}
                            placeholder="To"
                          />
                        </div>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs">Assigned To</Label>
                      <SearchableSelect
                        value={assignedToFilter}
                        onChange={(v) => {
                          setAssignedToFilter(v);
                          setPagination((prev) => ({ ...prev, pageIndex: 0 }));
                        }}
                        loadError={respondUsersError}
                        onRetry={() => void refetchRespondUsers()}
                        options={[
                          { value: '__all__', label: 'All' },
                          ...users.map((u) => ({
                            value: u.id,
                            label: u.name || 'Unnamed user',
                          })),
                        ]}
                        placeholder="All"
                        triggerClassName="w-full"
                      />
                    </div>
                    {hasActiveFilters && (
                      <Button variant="outline" size="sm" onClick={handleClearFilters} className="w-full">
                        Clear Filters
                      </Button>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: `conversation_sla_event_log_${trackingId}.xlsx` }}
            />
          </CardHeader>
          {eventLogs.length > 0 ? (
            <>
              <CardTable>
                <DataGridTable />
              </CardTable>
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </>
          ) : (
            <CardContent>
              <div className="text-center py-8 text-muted-foreground">
                {hasActiveFilters ? 'No event logs match the filters.' : 'No event logs found'}
              </div>
            </CardContent>
          )}
        </Card>
      </DataGrid>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Event Log</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this event log? This action cannot be undone.
              {logToDelete && (
                <div className="mt-2 text-sm">
                  <strong>Event Type:</strong> {logToDelete.event_type}
                  <br />
                  <strong>Event At:</strong> {formatDateTime(parseDateTimeAsUTC(logToDelete.event_at))}
                </div>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
