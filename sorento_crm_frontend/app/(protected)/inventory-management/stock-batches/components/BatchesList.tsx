'use client';

import { useEffect, useMemo, useState } from 'react';
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
import { ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { useStockBatches } from '../hooks/useStockBatches';
import type { StockBatch } from '../types/batch.types';
import { formatDate } from '@/lib/helpers';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

const BATCH_STATUSES: { value: string; label: string }[] = [
  { value: 'available', label: 'Available' },
  { value: 'reserved', label: 'Reserved' },
  { value: 'damaged', label: 'Damaged' },
  { value: 'expired', label: 'Expired' },
  { value: 'returned', label: 'Returned' },
];

export default function BatchesList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useStockBatches({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    status: statusFilter || undefined,
  });

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [statusFilter, searchQuery]);

  const columns = useMemo<ColumnDef<StockBatch>[]>(
    () => [
      buildSelectColumn<StockBatch>(),
      {
        accessorKey: 'product.product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product Code" column={column} />,
        cell: ({ row }) => row.original.product?.product_code || '-',
        size: 150,
        meta: { headerTitle: 'Product Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'batch_code',
        header: ({ column }) => <DataGridColumnHeader title="Batch Code" column={column} />,
        size: 150,
        meta: { headerTitle: 'Batch Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'quantity',
        header: ({ column }) => <DataGridColumnHeader title="Quantity" column={column} />,
        size: 100,
        meta: { headerTitle: 'Quantity' },
      },
      {
        accessorKey: 'manufactured_date',
        header: ({ column }) => <DataGridColumnHeader title="Manufactured Date" column={column} />,
        cell: ({ row }) => row.original.manufactured_date ? formatDate(new Date(row.original.manufactured_date)) : '-',
        size: 150,
        meta: { headerTitle: 'Manufactured Date' },
      },
      {
        accessorKey: 'expiry_date',
        header: ({ column }) => <DataGridColumnHeader title="Expiry Date" column={column} />,
        cell: ({ row }) => {
          const expiryDate = row.original.expiry_date;
          if (!expiryDate) return '-';
          const date = new Date(expiryDate);
          const daysUntilExpiry = Math.ceil((date.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
          return (
            <div className="flex items-center gap-2">
              <span>{formatDate(date)}</span>
              {daysUntilExpiry <= 30 && daysUntilExpiry > 0 && (
                <Badge variant="destructive" size="sm">Expires in {daysUntilExpiry} days</Badge>
              )}
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Expiry Date' },
      },
      {
        accessorKey: 'warehouse.warehouse_name',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => row.original.warehouse?.warehouse_name || '-',
        size: 150,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = row.original.status;
          return (
            <Badge status={status}>
              {status ? status.charAt(0).toUpperCase() + status.slice(1) : '-'}
            </Badge>
          );
        },
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: () => <ChevronRight className="text-muted-foreground/70 size-3.5" />,
        size: 40,
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

  return (
    <DataGrid
      table={table}
      tableLayout={{ columnsVisibility: true }}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      standardToolbar={false}
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
                placeholder="Search batches..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: Boolean(statusFilter),
              activeCount: statusFilter ? 1 : 0,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Status</Label>
                    <SearchableSelect
                      value={statusFilter || 'all'}
                      onChange={(v) => setStatusFilter(v === 'all' ? '' : v)}
                      options={[
                        { value: 'all', label: 'All statuses' },
                        ...BATCH_STATUSES.map((s) => ({ value: s.value, label: s.label })),
                      ]}
                      placeholder="All statuses"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {statusFilter && (
                    <div className="flex justify-end">
                      <Button variant="ghost" size="sm" onClick={() => setStatusFilter('')}>
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'stock_batches_export.xlsx' }}
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
