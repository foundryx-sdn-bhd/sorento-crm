'use client';

import { useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
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
import { ChevronRight, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useIntegrationLogs, useRetryIntegrationLog } from '../hooks/useIntegrationLogs';
import type { IntegrationLog } from '../types/integrationLog.types';
import { formatDistanceToNow } from 'date-fns';
import { toast } from '@/lib/toast';
import { formatDateTimeInMalaysia, parseDateTimeAsUTC } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function IntegrationLogsList() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  // Seed filters from the URL so a System Health drill-down (channel + failed +
  // last-24h) lands here pre-filtered.
  const [statusFilter, setStatusFilter] = useState<string>(
    () => searchParams.get('status') ?? 'all',
  );
  const [channelFilter, setChannelFilter] = useState<string>(
    () => searchParams.get('integration_channel') ?? 'all',
  );
  const [tableFilter, setTableFilter] = useState<string>('all');
  const [createdFrom, setCreatedFrom] = useState<string>(
    () => searchParams.get('created_from') ?? '',
  );
  const [createdTo, setCreatedTo] = useState<string>(
    () => searchParams.get('created_to') ?? '',
  );

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
    setStatusFilter(state.filters.status ?? 'all');
    setChannelFilter(state.filters.integration_channel ?? 'all');
    setTableFilter(state.filters.business_table ?? 'all');
    setCreatedFrom(state.filters.created_from ?? '');
    setCreatedTo(state.filters.created_to ?? '');
  });
  // A failure-cause drill-down from System Health. These have no control in the
  // filter panel - they are set by the link and cleared as a unit, so the banner
  // below is the only place they are visible. Without it the list would look
  // inexplicably short.
  const [statusCode, setStatusCode] = useState<string>(
    () => searchParams.get('status_code') ?? '',
  );
  const [errorContains, setErrorContains] = useState<string[]>(
    () => searchParams.getAll('error_contains'),
  );
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, isFetching, refetch, isRefetching, error } = useIntegrationLogs({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    integration_channel: channelFilter !== 'all' ? channelFilter : undefined,
    business_table: tableFilter !== 'all' ? tableFilter : undefined,
    created_from: createdFrom || undefined,
    created_to: createdTo || undefined,
    status_code: statusCode || undefined,
    error_contains: errorContains.length ? errorContains : undefined,
  });

  // Day-granular date inputs; widen "to" to end-of-day so the chosen day is inclusive.
  const setCreatedFromDay = (v: string) => {
    setCreatedFrom(v ? `${v}T00:00:00` : '');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };
  const setCreatedToDay = (v: string) => {
    setCreatedTo(v ? `${v}T23:59:59` : '');
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  };

  const retryMutation = useRetryIntegrationLog();

  const columns = useMemo<ColumnDef<IntegrationLog>[]>(
    () => [
      buildSelectColumn<IntegrationLog>(),
      {
        accessorKey: 'integration_channel',
        header: ({ column }) => <DataGridColumnHeader title="Channel" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.integration_channel}
          </Badge>
        ),
        size: 120,
        meta: { headerTitle: 'Channel', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'business_table',
        header: ({ column }) => <DataGridColumnHeader title="Business Table" column={column} />,
        cell: ({ row }) => row.original.business_table,
        size: 150,
        meta: { headerTitle: 'Business Table' },
      },
      {
        accessorKey: 'business_id',
        header: ({ column }) => <DataGridColumnHeader title="Business ID" column={column} />,
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.business_id.substring(0, 8)}...</span>
        ),
        size: 120,
        meta: { headerTitle: 'Business ID' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = (row.original.status || 'pending') as string;
          return (
            <Badge
              status={status}
              className="capitalize"
            >
              {status.charAt(0).toUpperCase() + status.slice(1)}
            </Badge>
          );
        },
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'retry_count',
        header: ({ column }) => <DataGridColumnHeader title="Retries" column={column} />,
        cell: ({ row }) => `${row.original.retry_count}/${row.original.max_retry_allowed}`,
        size: 100,
        meta: { headerTitle: 'Retries' },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created" column={column} />,
        cell: ({ row }) => {
          const utcDate = parseDateTimeAsUTC(row.original.created_at);
          const malaysiaTime = formatDateTimeInMalaysia(row.original.created_at);
          const relative = formatDistanceToNow(utcDate, { addSuffix: true });
          return (
            <span title={relative}>
              {malaysiaTime}
            </span>
          );
        },
        size: 180,
        meta: { headerTitle: 'Created' },
      },
      {
        accessorKey: 'processed_at',
        header: ({ column }) => <DataGridColumnHeader title="Processed" column={column} />,
        cell: ({ row }) => {
          if (!row.original.processed_at) return '-';
          const malaysiaTime = formatDateTimeInMalaysia(row.original.processed_at);
          return malaysiaTime;
        },
        size: 180,
        meta: { headerTitle: 'Processed' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            {row.original.status === 'failed' && row.original.retry_count < row.original.max_retry_allowed && (
              <Button
                mode="icon"
                variant="ghost"
                size="sm"
                aria-label="Retry"
                onClick={(e) => {
                  e.stopPropagation();
                  retryMutation.mutate(row.original.id, {
                    onSuccess: () => {
                      toast.success('Retry initiated');
                      refetch();
                    },
                    onError: (error: Error) => {
                      toast.error(error.message || 'Failed to retry');
                    },
                  });
                }}
                disabled={retryMutation.isPending}
              >
                <RefreshCw className="size-4" />
              </Button>
            )}
            <ChevronRight className="text-muted-foreground/70 size-3.5" />
          </div>
        ),
        size: 80,
        enableHiding: false,
      },
    ],
    [retryMutation, refetch],
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

  const filtersActiveCount =
    (statusFilter !== 'all' ? 1 : 0) +
    (channelFilter !== 'all' ? 1 : 0) +
    (tableFilter !== 'all' ? 1 : 0) +
    (createdFrom || createdTo ? 1 : 0) +
    (statusCode || errorContains.length ? 1 : 0);

  const clearCauseFilter = () => {
    setStatusCode('');
    setErrorContains([]);
  };

  const handleClearFilters = () => {
    setStatusFilter('all');
    setChannelFilter('all');
    setTableFilter('all');
    setCreatedFrom('');
    setCreatedTo('');
    clearCauseFilter();
    setPagination((p) => ({ ...p, pageIndex: 0 }));
    router.replace(pathname);
  };

  // A drill-down may target a channel not in the fixed option list (e.g. respond_io);
  // surface it so the Select shows the active value rather than a blank trigger.
  const KNOWN_CHANNELS = [
    'n8n',
    'sla_management',
    'sla_tracking_creation',
    'sla_tracking_update',
    'sla_escalation',
  ];
  const extraChannel =
    channelFilter !== 'all' && !KNOWN_CHANNELS.includes(channelFilter) ? channelFilter : null;

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      rowHref={(row) => {
        // Carries the list query so the detail pager walks the same page.
        const search = buildDetailSearch(
          {
            pageIndex: pagination.pageIndex,
            pageSize: pagination.pageSize,
            sorting,
            searchQuery,
          },
          {
            status: statusFilter !== 'all' ? statusFilter : undefined,
            integration_channel: channelFilter !== 'all' ? channelFilter : undefined,
            business_table: tableFilter !== 'all' ? tableFilter : undefined,
            created_from: createdFrom || undefined,
            created_to: createdTo || undefined,
            status_code: statusCode || undefined,
            error_contains: errorContains.length ? errorContains.join(',') : undefined,
          },
        );
        return `/integration-management/integration-logs/${row.id}${search ? `?${search}` : ''}`;
      }}
    >
      <Card>
        {(statusCode || errorContains.length > 0) && (
          <div
            data-testid="integration-logs-cause-filter"
            className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-5 py-3 text-xs"
          >
            <span className="text-muted-foreground">Showing one failure cause:</span>
            {statusCode && (
              <Badge variant="secondary" appearance="light" size="sm">
                HTTP {statusCode}
              </Badge>
            )}
            {errorContains.map((term) => (
              <code
                key={term}
                className="max-w-md truncate rounded bg-background px-1.5 py-0.5"
                title={term}
              >
                {term}
              </code>
            ))}
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2"
              data-testid="integration-logs-cause-filter-clear"
              onClick={() => {
                clearCauseFilter();
                setPagination((p) => ({ ...p, pageIndex: 0 }));
              }}
            >
              Show all failures
            </Button>
          </div>
        )}
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search logs..."
                className="w-full max-w-xs"
              />
            }
            filters={{
              kind: 'custom',
              active: filtersActiveCount > 0,
              activeCount: filtersActiveCount,
              content: (
                <div className="space-y-3">
                  <SearchableSelect
                    value={statusFilter}
                    onChange={setStatusFilter}
                    options={[
                      { value: 'all', label: 'All Status' },
                      { value: 'pending', label: 'Pending' },
                      { value: 'processing', label: 'Processing' },
                      { value: 'success', label: 'Success' },
                      { value: 'failed', label: 'Failed' },
                    ]}
                    placeholder="Status"
                    triggerClassName="w-full"
                  />
                  <SearchableSelect
                    value={channelFilter}
                    onChange={setChannelFilter}
                    options={[
                      { value: 'all', label: 'All Channels' },
                      { value: 'n8n', label: 'n8n' },
                      { value: 'sla_management', label: 'SLA Management' },
                      { value: 'sla_tracking_creation', label: 'SLA Tracking (create)' },
                      { value: 'sla_tracking_update', label: 'SLA Tracking (update)' },
                      { value: 'sla_escalation', label: 'SLA Escalation' },
                      ...(extraChannel ? [{ value: extraChannel, label: extraChannel }] : []),
                    ]}
                    placeholder="Channel"
                    triggerClassName="w-full"
                  />
                  <SearchableSelect
                    value={tableFilter}
                    onChange={setTableFilter}
                    options={[
                      { value: 'all', label: 'All Tables' },
                      { value: 'attachments', label: 'Attachments' },
                      { value: 'conversation_sla_tracking', label: 'Conversation SLA Tracking' },
                      { value: 'conversation_sla_event_log', label: 'Conversation SLA Event Log' },
                    ]}
                    placeholder="Table"
                    triggerClassName="w-full"
                  />
                  {(createdFrom || createdTo) && (
                    // A drill-down seeds a date window from the URL. The date
                    // inputs are day-granular, so a window like 09:05 renders as
                    // a bare date and looks like the user set it - this says the
                    // range is active and where it came from.
                    <Badge
                      variant="secondary"
                      appearance="light"
                      size="sm"
                      data-testid="integration-created-from-active"
                    >
                      Date range active
                    </Badge>
                  )}
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">From date</label>
                      <Input
                        type="date"
                        data-testid="integration-created-from"
                        value={createdFrom ? createdFrom.slice(0, 10) : ''}
                        max={createdTo ? createdTo.slice(0, 10) : undefined}
                        onChange={(e) => setCreatedFromDay(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">To date</label>
                      <Input
                        type="date"
                        data-testid="integration-created-to"
                        value={createdTo ? createdTo.slice(0, 10) : ''}
                        min={createdFrom ? createdFrom.slice(0, 10) : undefined}
                        onChange={(e) => setCreatedToDay(e.target.value)}
                      />
                    </div>
                  </div>
                  {filtersActiveCount > 0 && (
                    <Button variant="outline" size="sm" onClick={handleClearFilters} className="w-full">
                      Clear Filters
                    </Button>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'integration_logs_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isRefetching}
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
