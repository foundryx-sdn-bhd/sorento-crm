'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
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
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { ChevronRight, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useIntegrationLogs, useRetryIntegrationLog } from '@/app/(protected)/integration-management/integration-logs/hooks/useIntegrationLogs';
import type { IntegrationLog } from '@/app/(protected)/integration-management/integration-logs/types/integrationLog.types';
import { formatDistanceToNow } from 'date-fns';
import { toast } from '@/lib/toast';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function SmartLinkageList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [channelFilter, setChannelFilter] = useState<string>('all');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Always filter by business_table = 'attachments'
  const { data, isLoading, isPlaceholderData, isFetching, refetch, error } = useIntegrationLogs({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    integration_channel: channelFilter !== 'all' ? channelFilter : undefined,
    business_table: 'attachments', // Always filter by attachments
  });

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
        accessorKey: 'business_id',
        header: ({ column }) => <DataGridColumnHeader title="Attachment ID" column={column} />,
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.business_id.substring(0, 8)}...</span>
        ),
        size: 120,
        meta: { headerTitle: 'Attachment ID' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = (row.original.status || 'pending') as string;
          return (
            <Badge status={status} className="capitalize">
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
          const date = new Date(row.original.created_at);
          return formatDistanceToNow(date, { addSuffix: true });
        },
        size: 150,
        meta: { headerTitle: 'Created' },
      },
      {
        accessorKey: 'processed_at',
        header: ({ column }) => <DataGridColumnHeader title="Processed" column={column} />,
        cell: ({ row }) => {
          if (!row.original.processed_at) return '-';
          const date = new Date(row.original.processed_at);
          return formatDistanceToNow(date, { addSuffix: true });
        },
        size: 150,
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
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  const filtersActiveCount =
    (statusFilter !== 'all' ? 1 : 0) + (channelFilter !== 'all' ? 1 : 0);

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ columnsVisibility: true }}
      onRowClick={(row) => {
        router.push(`/integration-management/integration-logs/${row.id}`);
      }}
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
                placeholder="Search logs..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: filtersActiveCount > 0,
              activeCount: filtersActiveCount,
              content: (
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium">Status</p>
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
                  </div>
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium">Channel</p>
                    <SearchableSelect
                      value={channelFilter}
                      onChange={setChannelFilter}
                      options={[
                        { value: 'all', label: 'All Channels' },
                        { value: 'n8n', label: 'n8n' },
                      ]}
                      placeholder="Channel"
                      triggerClassName="w-full"
                    />
                  </div>
                </div>
              ),
            }}
            exportConfig={{ filename: 'smart_linkage_export.xlsx' }}
            onRefresh={() => {
              refetch();
              toast.success('List refreshed');
            }}
            isRefreshing={isLoading}
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
