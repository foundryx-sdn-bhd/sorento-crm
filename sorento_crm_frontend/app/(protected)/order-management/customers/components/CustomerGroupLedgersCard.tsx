'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { useCustomerGroupCustomers } from '../../customer-groups/hooks/useCustomerGroups';
import type { GroupLedger } from '../../customer-groups/types/customerGroup.types';

const SORTING = [{ id: 'customer_code', desc: false }];

/**
 * The Details tab's "Group ledgers" card: the sibling ledgers of this customer's group,
 * this one marked, paged by the server total. Not fetched while the customer has no group.
 */
export default function CustomerGroupLedgersCard({
  customerId,
  groupId,
}: {
  customerId: string;
  groupId: string | null;
}) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const { data, isLoading, isPlaceholderData } = useCustomerGroupCustomers(groupId, {
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting: SORTING,
    searchQuery: '',
  });
  const rows = useMemo(() => data?.data ?? [], [data]);
  const total = data?.pagination.total ?? 0;

  const columns = useMemo<ColumnDef<GroupLedger>[]>(
    () => [
      {
        accessorKey: 'customer_code',
        header: 'Code',
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.customer_code}>
            {row.original.customer_code}
          </span>
        ),
        enableSorting: false,
        size: 140,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'customer_name',
        header: 'Name',
        cell: ({ row }) => (
          <div className="min-w-0">
            <span className="block truncate" title={row.original.customer_name}>
              {row.original.customer_name}
            </span>
            {row.original.id === customerId ? (
              <span className="text-xs text-muted-foreground">(this ledger)</span>
            ) : null}
          </div>
        ),
        enableSorting: false,
        size: 320,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'account_level',
        header: 'Account level',
        cell: ({ row }) => (row.original.account_level ? `Account ${row.original.account_level}` : '-'),
        enableSorting: false,
        size: 140,
        meta: { headerTitle: 'Account level', skeleton: <Skeleton className="h-4 w-16" /> },
      },
    ],
    [customerId],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    rowCount: total,
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Group ledgers</CardTitle>
        {groupId ? (
          <Link
            href={`/order-management/customer-groups/${groupId}`}
            className="text-sm text-primary hover:underline"
          >
            Open group
          </Link>
        ) : null}
      </CardHeader>
      {!groupId ? (
        <div className="px-5 pb-5">
          <p className="text-sm font-medium">Not in a group</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Set a group on the edit form to list its ledgers here
          </p>
        </div>
      ) : (
        <DataGrid
          table={table}
          recordCount={total}
          isLoading={isLoading}
          isPlaceholderData={isPlaceholderData}
          // A small panel: no saved column layout of its own.
          listingKey={null}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          emptyMessage="No ledgers in this group"
        >
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </DataGrid>
      )}
    </Card>
  );
}
