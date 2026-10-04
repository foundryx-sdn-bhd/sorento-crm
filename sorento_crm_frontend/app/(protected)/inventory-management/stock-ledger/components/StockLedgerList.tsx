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
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useStockLedger } from '../hooks/useStockLedger';
import {
  STOCK_LEDGER_TRANSACTION_TYPE_OPTIONS,
  stockLedgerTypeLabel,
  type StockLedgerEntry,
} from '../types/stockLedger.types';

export default function StockLedgerList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [productId, setProductId] = useState('');
  const [warehouseId, setWarehouseId] = useState('');
  const [transactionType, setTransactionType] = useState('');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useStockLedger({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    product_id: productId || undefined,
    warehouse_id: warehouseId || undefined,
    transaction_type: transactionType || undefined,
  });

  useEffect(() => {
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  }, [productId, warehouseId, transactionType]);

  const columns = useMemo<ColumnDef<StockLedgerEntry>[]>(
    () => [
      buildSelectColumn<StockLedgerEntry>(),
      {
        accessorKey: 'product.product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium">{row.original.product?.product_code || row.original.product_id}</span>
            <span className="text-muted-foreground text-xs">{row.original.product?.product_name || '-'}</span>
          </div>
        ),
        size: 220,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'warehouse.warehouse_name',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => row.original.warehouse?.warehouse_name || row.original.warehouse_id,
        size: 200,
        meta: { headerTitle: 'Warehouse', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'transaction_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        size: 140,
        cell: ({ row }) => (
          <span className="text-xs font-medium text-muted-foreground">
            {stockLedgerTypeLabel(row.original.transaction_type)}
          </span>
        ),
        meta: { headerTitle: 'Type' },
      },
      {
        accessorKey: 'quantity_change',
        header: ({ column }) => <DataGridColumnHeader title="Change" column={column} />,
        cell: ({ row }) => {
          const value = row.original.quantity_change;
          const isPositive = value > 0;
          return (
            <span className={isPositive ? 'text-emerald-600 font-medium' : 'text-red-600 font-medium'}>
              {isPositive ? `+${value}` : value}
            </span>
          );
        },
        size: 120,
        meta: { headerTitle: 'Change' },
      },
      {
        accessorKey: 'previous_quantity',
        header: ({ column }) => <DataGridColumnHeader title="Previous" column={column} />,
        size: 120,
        meta: { headerTitle: 'Previous' },
      },
      {
        accessorKey: 'new_quantity',
        header: ({ column }) => <DataGridColumnHeader title="New" column={column} />,
        size: 120,
        meta: { headerTitle: 'New' },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 200,
        meta: { headerTitle: 'Created At' },
      },
      {
        accessorKey: 'created_by_name',
        header: ({ column }) => <DataGridColumnHeader title="Created By" column={column} />,
        cell: ({ row }) => row.original.created_by_name || row.original.created_by || '-',
        size: 180,
        meta: { headerTitle: 'Created By' },
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
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ columnsVisibility: true }}
      standardToolbar={false}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            filters={{
              kind: 'custom',
              active: Boolean(productId || warehouseId || transactionType),
              activeCount:
                (productId ? 1 : 0) + (warehouseId ? 1 : 0) + (transactionType ? 1 : 0),
              content: (
                <div className="space-y-3">
                  <div className="space-y-1">
                    <Label>Product ID</Label>
                    <Input
                      placeholder="Product ID"
                      value={productId}
                      onChange={(e) => setProductId(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>Warehouse ID</Label>
                    <Input
                      placeholder="Warehouse ID"
                      value={warehouseId}
                      onChange={(e) => setWarehouseId(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="stock-ledger-type">Transaction type</Label>
                    <SearchableSelect
                      id="stock-ledger-type"
                      value={transactionType}
                      onChange={setTransactionType}
                      options={STOCK_LEDGER_TRANSACTION_TYPE_OPTIONS}
                      placeholder="All types"
                      clearable
                    />
                  </div>
                  {(productId || warehouseId || transactionType) && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={() => {
                        setProductId('');
                        setWarehouseId('');
                        setTransactionType('');
                      }}
                    >
                      Clear filters
                    </Button>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'stock_ledger_export.xlsx' }}
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
