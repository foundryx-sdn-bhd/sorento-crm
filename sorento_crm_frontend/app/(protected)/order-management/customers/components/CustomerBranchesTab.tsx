'use client';

import { useMemo, useState } from 'react';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Store } from 'lucide-react';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { formatDateTimeSafe } from '@/lib/helpers';
import { useBranches } from '../../branches/hooks/useBranches';
import type { Branch } from '../../branches/types/branch.types';

/**
 * #1356: this customer's AutoCount branches (AccNo = the customer code), read only in view
 * and edit alike; AutoCount is the source of truth.
 */
export function CustomerBranchesTab({ customerId }: { customerId: string }) {
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 20 });
  const { data, isLoading, isPlaceholderData, error, refetch } = useBranches({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting: [{ id: 'branch_code', desc: false }],
    searchQuery: '',
    customerId,
  });
  const rows = data?.data ?? [];
  const total = data?.pagination?.total ?? 0;

  const columns = useMemo<ColumnDef<Branch>[]>(
    () => [
      {
        id: 'branch_code',
        header: 'Branch Code',
        size: 160,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.branch_code}>
            {row.original.branch_code}
          </span>
        ),
      },
      {
        id: 'branch_name',
        header: 'Branch Name',
        size: 360,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.branch_name ?? undefined}>
            {row.original.branch_name || '-'}
          </span>
        ),
      },
      {
        id: 'source_book',
        header: 'Book',
        size: 120,
        cell: ({ row }) => row.original.source_book,
      },
      {
        id: 'last_synced_at',
        header: 'Last Synced',
        size: 190,
        cell: ({ row }) => formatDateTimeSafe(row.original.last_synced_at),
      },
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { pagination },
    onPaginationChange: (updater) =>
      setPagination((prev) => (typeof updater === 'function' ? updater(prev) : updater)),
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    manualPagination: true,
    pageCount: Math.ceil(total / pagination.pageSize) || 1,
    columnResizeMode: 'onChange',
  });

  if (!isLoading && total === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <Store className="size-8 text-muted-foreground" />
          <p className="font-medium">No branches</p>
          <p className="text-sm text-muted-foreground">
            This customer has no AutoCount branches.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      listingKey="order_management.branches.view::customer"
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <Card>
        <CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </CardHeader>
        <CardFooter className="flex justify-between border-t px-4 py-3">
          <DataGridPagination />
        </CardFooter>
      </Card>
    </DataGrid>
  );
}
