'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Search } from 'lucide-react';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { getUserLookup, type UserLookupItem } from '@/services/userSelectService';
import { formatDateTime } from '@/lib/helpers';
import { useEventLogs } from '../hooks/useEventLogs';
import type { ConversationSLAEventLog } from '../types/eventLog.types';

export default function EventLogList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'event_at', desc: true }]);
  const [trackingId, setTrackingId] = useState('');
  const [eventType, setEventType] = useState('__all__');
  const [assignedTo, setAssignedTo] = useState('__all__');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useEventLogs({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    tracking_id: trackingId || undefined,
    event_type: eventType && eventType !== '__all__' ? eventType : undefined,
    assigned_to: assignedTo && assignedTo !== '__all__' ? assignedTo : undefined,
  });

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [trackingId, eventType, assignedTo]);

  const {
    data: respondUsers,
    error: respondUsersError,
    refetch: refetchRespondUsers,
  } = useQuery({
    queryKey: ['user-lookup', 'respond-synced', 'with-inactive'],
    queryFn: () => getUserLookup({ respond_synced: true, include_inactive: true }),
    staleTime: 1000 * 60 * 5,
  });

  const columns = useMemo<ColumnDef<ConversationSLAEventLog>[]>(
    () => [
      buildSelectColumn<ConversationSLAEventLog>(),
      {
        accessorKey: 'event_type',
        header: ({ column }) => <DataGridColumnHeader title="Event Type" column={column} />,
        size: 140,
        meta: { headerTitle: 'Event Type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'sla_tracking_id',
        header: ({ column }) => <DataGridColumnHeader title="Tracking ID" column={column} />,
        size: 220,
        meta: { headerTitle: 'Tracking ID', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'from_tier',
        header: ({ column }) => <DataGridColumnHeader title="From Tier" column={column} />,
        cell: ({ row }) => row.original.from_tier ? `Tier ${row.original.from_tier}` : '-',
        size: 120,
        meta: { headerTitle: 'From Tier' },
      },
      {
        accessorKey: 'to_tier',
        header: ({ column }) => <DataGridColumnHeader title="To Tier" column={column} />,
        cell: ({ row }) => row.original.to_tier ? `Tier ${row.original.to_tier}` : '-',
        size: 120,
        meta: { headerTitle: 'To Tier' },
      },
      {
        accessorKey: 'event_at',
        header: ({ column }) => <DataGridColumnHeader title="Event At" column={column} />,
        cell: ({ row }) => formatDateTime(new Date(row.original.event_at)),
        size: 200,
        meta: { headerTitle: 'Event At' },
      },
      {
        accessorKey: 'assigned_to',
        header: ({ column }) => <DataGridColumnHeader title="Assigned To" column={column} />,
        cell: ({ row }) => row.original.assigned_to || '-',
        size: 160,
        meta: { headerTitle: 'Assigned To' },
      },
      {
        accessorKey: 'response_time',
        header: ({ column }) => <DataGridColumnHeader title="Response Time" column={column} />,
        cell: ({ row }) => row.original.response_time ?? '-',
        size: 140,
        meta: { headerTitle: 'Response Time' },
      },
      {
        accessorKey: 'resolution_time',
        header: ({ column }) => <DataGridColumnHeader title="Resolution Time" column={column} />,
        cell: ({ row }) => row.original.resolution_time ?? '-',
        size: 140,
        meta: { headerTitle: 'Resolution Time' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
  });

  const advancedFiltersActive = eventType !== '__all__' || assignedTo !== '__all__';

  return (
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      error={error}
      onRetry={() => void refetch()}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <div className="relative">
                <Search className="absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Filter by tracking ID..."
                  value={trackingId}
                  onChange={(e) => setTrackingId(e.target.value)}
                  className="w-64 ps-9"
                />
              </div>
            }
            filters={{
              kind: 'custom',
              active: advancedFiltersActive,
              activeCount:
                (eventType !== '__all__' ? 1 : 0) + (assignedTo !== '__all__' ? 1 : 0),
              content: (
                <div className="space-y-3">
                  <p className="text-sm font-medium">Advanced filters</p>
                  <SearchableSelect
                    value={eventType}
                    onChange={(value) => setEventType(value)}
                    options={[
                      { value: '__all__', label: 'All events' },
                      { value: 'escalation', label: 'Escalation' },
                      { value: 'reassignment', label: 'Reassignment' },
                      { value: 'response', label: 'Response' },
                      { value: 'resolution', label: 'Resolution' },
                    ]}
                    placeholder="Event type"
                    triggerClassName="w-full"
                  />
                  <SearchableSelect
                    value={assignedTo}
                    onChange={(value) => setAssignedTo(value)}
                    loadError={respondUsersError}
                    onRetry={() => void refetchRespondUsers()}
                    options={[
                      { value: '__all__', label: 'All assignees' },
                      ...(respondUsers || []).map((user: UserLookupItem) => ({
                        value: user.respond_user_id || user.id,
                        label: user.name || 'Unnamed user',
                      })),
                    ]}
                    placeholder="Assigned to"
                    triggerClassName="w-full"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      setEventType('__all__');
                      setAssignedTo('__all__');
                    }}
                  >
                    Clear advanced filters
                  </Button>
                </div>
              ),
            }}
            exportConfig={{ filename: 'sla_event_logs_export.xlsx' }}
            onRefresh={() => void refetch()}
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
