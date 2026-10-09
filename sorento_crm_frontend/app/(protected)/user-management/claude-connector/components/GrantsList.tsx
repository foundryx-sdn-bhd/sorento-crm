'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { formatDateTimeSafe } from '@/lib/helpers';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useGrantRevocation, useGrantsQuery } from '../hooks/useClaudeConnector';
import type { GrantRow } from '../types/claudeConnector.types';

const DETAIL_PATH = '/user-management/claude-connector';
const STATUS_OPTIONS = ['Active', 'Revoked', 'Expired'].map((s) => ({ value: s, label: s }));

export default function GrantsList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [status, setStatus] = useState('');
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setStatus(state.filters.status ?? '');
  });
  useResetPageOnFilterChange(setPagination, [searchQuery, status]);

  const { data, isLoading, isPlaceholderData, isError, refetch, isFetching } = useGrantsQuery(
    { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, sorting, searchQuery },
    { status: status || undefined },
  );
  const revocation = useGrantRevocation();

  const rowHref = (row: GrantRow) => {
    const search = buildDetailSearch(
      { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, sorting, searchQuery },
      { status: status || undefined },
    );
    return `${DETAIL_PATH}/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<GrantRow>[]>(
    () => [
      {
        accessorKey: 'user_name',
        header: ({ column }) => <DataGridColumnHeader title="User" column={column} />,
        cell: ({ row }) => (
          <Link
            href={`${DETAIL_PATH}/${row.original.id}`}
            onClick={(e) => e.stopPropagation()}
            className="block truncate font-medium text-primary hover:underline"
            title={row.original.user_name}
          >
            {row.original.user_name}
          </Link>
        ),
        size: 240,
        meta: { headerTitle: 'User', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created" column={column} />,
        cell: ({ row }) => formatDateTimeSafe(row.original.created_at),
        size: 180,
        meta: { headerTitle: 'Created', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'last_used_at',
        header: ({ column }) => <DataGridColumnHeader title="Last used" column={column} />,
        cell: ({ row }) => formatDateTimeSafe(row.original.last_used_at),
        size: 180,
        meta: { headerTitle: 'Last used', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'calls_30d',
        header: ({ column }) => <DataGridColumnHeader title="Calls (30 d)" column={column} />,
        size: 130,
        meta: { headerTitle: 'Calls (30 d)', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        enableSorting: false,
        cell: ({ row }) => (
          <Badge status={row.original.status} appearance="light">
            {row.original.status}
          </Badge>
        ),
        size: 130,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-5 w-16" /> },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        enableSorting: false,
        enableResizing: false,
        cell: ({ row }) => {
          if (revocation.targetId === row.original.id && revocation.countdown) {
            return (
              <div role="presentation" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                {revocation.countdown}
              </div>
            );
          }
          if (row.original.status !== 'Active') return null;
          return (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive"
              disabled={revocation.isPending}
              onClick={(e) => {
                e.stopPropagation();
                revocation.run({ id: row.original.id, subject: row.original.user_name });
              }}
            >
              Revoke
            </Button>
          );
        },
        size: 240,
        meta: { headerTitle: 'Actions', skeleton: <Skeleton className="h-8 w-20" /> },
      },
    ],
    [revocation],
  );

  const total = data?.pagination.total ?? 0;
  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.ceil(total / pagination.pageSize),
    rowCount: total,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  if (isError) {
    return (
      <Card className="flex flex-col items-center gap-2 p-10 text-center">
        <div className="text-sm font-semibold">Claude connector grants could not be loaded</div>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </Card>
    );
  }

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      listingKey="analytics.connector.manage::claude_connector_grants"
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={
        <div className="py-4 text-center">
          <p className="text-sm font-medium text-foreground">No Claude connector grants</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Staff connections to Claude appear here.
          </p>
        </div>
      }
      rowHref={rowHref}
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
                placeholder="Search user..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: !!status,
              activeCount: status ? 1 : 0,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="grant-status-filter">Status</Label>
                    <SearchableSelect
                      id="grant-status-filter"
                      value={status}
                      onChange={setStatus}
                      options={STATUS_OPTIONS}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                      clearable
                    />
                  </div>
                </div>
              ),
            }}
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
