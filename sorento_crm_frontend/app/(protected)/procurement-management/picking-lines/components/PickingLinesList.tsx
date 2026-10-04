'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
} from '@tanstack/react-table';
import {
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { SpoAllocationCell } from '@/components/common/SpoAllocationCell';
import { useQuery } from '@tanstack/react-query';
import { getPickingLines, type PickingLinesListParams } from '../services/pickingLineService';
import type { PickingLineListItem } from '../types/pickingLine.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

type SortField = 'spo_allocation' | 'product' | 'quantity_expected' | 'quantity_picked';

export default function PickingLinesList() {
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 20,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'spo_allocation', desc: false },
  ]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const sortField = (sorting[0]?.id as SortField) || 'spo_allocation';
  const sortDir = sorting[0]?.desc ? 'desc' : 'asc';

  const params: PickingLinesListParams = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      sort: sortField,
      dir: sortDir,
      query: searchQuery.trim() || undefined,
    }),
    [pagination.pageIndex, pagination.pageSize, sortField, sortDir, searchQuery],
  );

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['picking-lines', params],
    queryFn: () => getPickingLines(params),
  });

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  const columns = useMemo<ColumnDef<PickingLineListItem>[]>(
    () => [
      buildSelectColumn<PickingLineListItem>(),
      {
        id: 'spo_allocation',
        accessorFn: (row) => row.spo_allocation?.spo_number ?? row.spo_number_raw ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="SPO Allocation" column={column} />
        ),
        cell: ({ row }) => (
          <SpoAllocationCell
            allocation={row.original.spo_allocation}
            statedSpoNumber={row.original.spo_number_raw}
          />
        ),
        size: 280,
        meta: { headerTitle: 'SPO Allocation', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'product',
        accessorFn: (row) => row.product?.product_code ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Product" column={column} />
        ),
        cell: ({ row }) => row.original.product?.product_code ?? '-',
        size: 140,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'location',
        accessorFn: (row) =>
          row.source_warehouse?.warehouse_code ??
          row.destination_warehouse?.warehouse_code ??
          '',
        header: 'Location',
        cell: ({ row }) =>
          row.original.source_warehouse?.warehouse_code ??
          row.original.destination_warehouse?.warehouse_code ??
          '-',
        size: 120,
        meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'quantity_expected',
        accessorKey: 'quantity_expected',
        header: ({ column }) => (
          <DataGridColumnHeader title="Expected" column={column} />
        ),
        cell: ({ row }) => row.original.quantity_expected,
        size: 100,
        meta: { headerTitle: 'Expected', skeleton: <Skeleton className="h-4 w-12" /> },
      },
      {
        id: 'quantity_picked',
        accessorKey: 'quantity_picked',
        header: ({ column }) => (
          <DataGridColumnHeader title="Picked" column={column} />
        ),
        cell: ({ row }) => row.original.quantity_picked,
        size: 100,
        meta: { headerTitle: 'Picked', skeleton: <Skeleton className="h-4 w-12" /> },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.ceil((data?.pagination?.total ?? 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination?.total ?? 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      standardToolbar={false}
      tableLayout={{ columnsVisibility: true }}
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
                placeholder="Search by SPO allocation or product..."
                className="w-72"
              />
            }
            exportConfig={{ filename: 'picking_lines_export.xlsx' }}
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
