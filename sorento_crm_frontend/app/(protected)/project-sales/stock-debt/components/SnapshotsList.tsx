'use client';

import * as React from 'react';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { SNAPSHOTS_KEY, useSnapshotsQuery } from '../hooks/useStockDebtSnapshots';
import type { SnapshotSummary } from '../types/stockDebtSnapshot.types';

/** Delete is the deferred action `stock_debt_snapshot.delete`: no confirm dialog, a countdown
 *  toast over the list, and the server deletes when the window lapses. */
function SnapshotDeleteButton({ snapshot }: { snapshot: SnapshotSummary }) {
  const deletion = useDeferredAction({
    actionKey: 'stock_debt_snapshot.delete',
    entityType: 'stock_debt_snapshot',
    entityId: snapshot.id,
    verb: 'Deleting',
    subject: snapshot.name,
    surface: 'toast',
    successMessage: 'Snapshot deleted',
    invalidateKeys: [[...SNAPSHOTS_KEY]],
  });
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={`Delete ${snapshot.name}`}
      disabled={deletion.isPending}
      onClick={(event) => {
        event.stopPropagation();
        deletion.start();
      }}
    >
      <Trash2 className="size-4" />
    </Button>
  );
}

const COLUMNS: ColumnDef<SnapshotSummary>[] = [
  {
    id: 'name',
    header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
    cell: ({ row }) => (
      <span className="block truncate font-medium" title={row.original.name}>
        {row.original.name}
      </span>
    ),
    size: 180,
    enableSorting: false,
    meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-40" /> },
  },
  {
    id: 'filters',
    header: ({ column }) => <DataGridColumnHeader title="Filters" column={column} />,
    cell: ({ row }) => (
      <span
        className="block truncate text-muted-foreground"
        title={row.original.filters_label}
      >
        {row.original.filters_label}
      </span>
    ),
    size: 155,
    enableSorting: false,
    meta: { headerTitle: 'Filters', skeleton: <Skeleton className="h-4 w-56" /> },
  },
  {
    id: 'row_count',
    header: ({ column }) => <DataGridColumnHeader title="Products" column={column} />,
    cell: ({ row }) => <span className="tabular-nums">{row.original.row_count}</span>,
    size: 70,
    enableSorting: false,
    meta: {
      headerTitle: 'Products',
      headerClassName: 'text-end',
      cellClassName: 'text-end',
      skeleton: <Skeleton className="h-4 w-8" />,
    },
  },
  {
    id: 'decided_count',
    header: ({ column }) => <DataGridColumnHeader title="Decided" column={column} />,
    cell: ({ row }) => <span className="tabular-nums">{row.original.decided_count}</span>,
    size: 70,
    enableSorting: false,
    meta: {
      headerTitle: 'Decided',
      headerClassName: 'text-end',
      cellClassName: 'text-end',
      skeleton: <Skeleton className="h-4 w-8" />,
    },
  },
  {
    id: 'created_by_name',
    header: ({ column }) => <DataGridColumnHeader title="Created by" column={column} />,
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.created_by_name ?? undefined}>
        {row.original.created_by_name ?? '-'}
      </span>
    ),
    size: 110,
    enableSorting: false,
    meta: { headerTitle: 'Created by', skeleton: <Skeleton className="h-4 w-24" /> },
  },
  {
    id: 'created_at',
    header: ({ column }) => <DataGridColumnHeader title="Created at" column={column} />,
    cell: ({ row }) => <span>{formatDateTimeInMalaysia(row.original.created_at)}</span>,
    size: 165,
    enableSorting: false,
    meta: { headerTitle: 'Created at', skeleton: <Skeleton className="h-4 w-28" /> },
  },
  {
    id: 'actions',
    header: () => <span className="sr-only">Actions</span>,
    cell: ({ row }) =>
      row.original.can_delete ? <SnapshotDeleteButton snapshot={row.original} /> : null,
    size: 70,
    enableSorting: false,
    enableResizing: false,
    meta: { headerTitle: 'Actions', skeleton: <Skeleton className="size-6" /> },
  },
];

/** AC-F2: the snapshots list. A row opens the snapshot. */
export function SnapshotsList() {
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling,
  } = useDebouncedSearch();
  useResetPageOnFilterChange(setPagination, [debounced]);

  const list = useSnapshotsQuery({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    query: debounced,
  });
  const total = list.data?.pagination.total ?? 0;

  const table = useReactTable({
    columns: COLUMNS,
    data: list.data?.data ?? [],
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    manualPagination: true,
    enableSorting: false,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={list.isLoading}
      isPlaceholderData={list.isPlaceholderData}
      error={list.error}
      onRetry={() => void list.refetch()}
      listingKey="projects.stock_debt.view::snapshots"
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      rowHref={(row) => `/project-sales/stock-debt/snapshots/${row.id}`}
      emptyMessage={
        <div className="px-6 py-10 text-center">
          <p className="text-sm font-semibold">
            {debounced ? 'No snapshot matches' : 'No snapshots yet'}
          </p>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {debounced
              ? 'Clear the search to see every snapshot.'
              : 'Save one from the Stock Debt board.'}
          </p>
        </div>
      }
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            showColumns={false}
            exportConfig={false}
            searchSlot={
              <ListSearchInput
                value={search}
                onChange={setSearch}
                isSettling={isSearchInFlight(isSettling, list.isFetching, debounced)}
                placeholder="Search snapshots…"
                aria-label="Search snapshots"
                className="w-full max-w-xs"
              />
            }
            onRefresh={() => void list.refetch()}
            isRefreshing={list.isFetching && !list.isLoading}
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
