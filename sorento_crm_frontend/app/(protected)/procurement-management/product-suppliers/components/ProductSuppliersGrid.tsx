'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
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
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import type { ProductSupplier } from '../types/productSupplier.types';
import { getProductSuppliers } from '../services/productSupplierService';
import { useQuery } from '@tanstack/react-query';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export default function ProductSuppliersGrid() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['product-suppliers', pagination, sorting, searchQuery],
    queryFn: () => getProductSuppliers({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
    }),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });

  const columns = useMemo<ColumnDef<ProductSupplier>[]>(
    () => [
      buildSelectColumn<ProductSupplier>(),
      {
        accessorKey: 'product.product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product Code" column={column} />,
        size: 150,
        cell: ({ row }) => row.original.product?.product_code || '-',
        meta: { headerTitle: 'Product Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'product.product_name',
        header: ({ column }) => <DataGridColumnHeader title="Product Name" column={column} />,
        size: 250,
        cell: ({ row }) => row.original.product?.product_name || '-',
        meta: { headerTitle: 'Product Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'supplier.supplier_code',
        header: ({ column }) => <DataGridColumnHeader title="Supplier Code" column={column} />,
        size: 150,
        cell: ({ row }) => row.original.supplier?.supplier_code || '-',
        meta: { headerTitle: 'Supplier Code' },
      },
      {
        accessorKey: 'supplier.supplier_name',
        header: ({ column }) => <DataGridColumnHeader title="Supplier Name" column={column} />,
        size: 200,
        cell: ({ row }) => row.original.supplier?.supplier_name || '-',
        meta: { headerTitle: 'Supplier Name' },
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
      recordCount={data?.pagination.total || 0}
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
                placeholder="Search product suppliers..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'product_suppliers_export.xlsx' }}
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
