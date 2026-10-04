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
import { Plus, ChevronRight, Trash2 } from 'lucide-react';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { useUOMs } from '../hooks/useUOM';
import type { UnitOfMeasure } from '../types/uom.types';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

export default function UOMList() {
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInputValue,
    setValue: setSearchInputValue,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list has to read it (S3-01).
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearchQuery(state.searchQuery);
  });
  // Delete asks nothing (D7): the row dims, a toast counts down, and Cancel is
  // the way back. A unit still on a product is refused by the server, and that
  // refusal now arrives as the toast's own error rather than as a warning
  // nobody could act on inside the dialog.
  const deletion = useDeferredRowAction({
    actionKey: 'uom.delete',
    entityType: 'uom',
    successMessage: 'Unit of measure deleted',
    invalidateKeys: [['uoms'], ['uom-select']],
  });
  const rowPending = useRowPending<UnitOfMeasure>('uom');

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useUOMs({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  // D3: the row opens the unit's page. The chevron button was the only way in,
  // which meant hitting a 20px target to read a record.
  const rowHref = (row: UnitOfMeasure) => {
    const search = buildDetailSearch({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    });
    return `/master-data-management/units-of-measure/${row.id}${search ? `?${search}` : ''}`;
  };

  const columns = useMemo<ColumnDef<UnitOfMeasure>[]>(
    () => [
      buildSelectColumn<UnitOfMeasure>(),
      {
        accessorKey: 'uom_code',
        header: ({ column }) => <DataGridColumnHeader title="UOM Code" column={column} />,
        size: 150,
        meta: { headerTitle: 'UOM Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'uom_name',
        header: ({ column }) => <DataGridColumnHeader title="UOM Name" column={column} />,
        size: 200,
        meta: { headerTitle: 'UOM Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'base_uom_id',
        header: ({ column }) => <DataGridColumnHeader title="Base UOM" column={column} />,
        size: 150,
        cell: ({ row }) => row.original.base_uom?.uom_name || '-',
        meta: { headerTitle: 'Base UOM' },
      },
      {
        accessorKey: 'conversion_factor',
        header: ({ column }) => <DataGridColumnHeader title="Conversion Factor" column={column} />,
        size: 150,
        cell: ({ row }) => row.original.conversion_factor || '-',
        meta: { headerTitle: 'Conversion Factor' },
      },
      {
        accessorKey: 'decimal_places',
        header: ({ column }) => <DataGridColumnHeader title="Decimal Places" column={column} />,
        size: 130,
        // 0 is a real answer here - whole units only - so it prints as 0 rather than
        // falling through a truthiness check to a dash the way the columns above do.
        cell: ({ row }) =>
          row.original.decimal_places === null || row.original.decimal_places === undefined
            ? '-'
            : row.original.decimal_places,
        meta: { headerTitle: 'Decimal Places' },
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 100,
        cell: ({ row }) => (
          <Badge
            variant={row.original.is_active ? 'success' : 'secondary'}
          >
            <BadgeDot />
            {row.original.is_active ? 'Active' : 'Inactive'}
          </Badge>
        ),
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-14" /> },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                deletion.run({
                  id: row.original.id,
                  subject: `${row.original.uom_name} (${row.original.uom_code})`,
                });
              }}
              title="Delete"
            >
              <Trash2 className="size-4 text-muted-foreground" />
            </Button>
            {/* The row is the way in now, so this says so rather than being it. */}
            <ChevronRight className="text-muted-foreground/70 size-3.5 shrink-0" />
          </div>
        ),
        size: 80,
        enableHiding: false,
      },
    ],
    [deletion],
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

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/master-data-management/units-of-measure/new')}>
      <Plus />
      Create UOM
    </Button>
  );

  return (
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      rowPending={rowPending}
      tableLayout={{ columnsVisibility: true }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInputValue}
                onChange={setSearchInputValue}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search UOMs..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'units_of_measure_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
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
