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
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { formatDateTimeSafe } from '@/lib/helpers';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useBranchBooks, useBranches } from '../hooks/useBranches';
import type { Branch } from '../types/branch.types';

export const BRANCHES_LISTING_KEY = 'order_management.branches.view';

function Truncated({ value }: { value: string | null | undefined }) {
  return value ? (
    <span className="block truncate" title={value}>
      {value}
    </span>
  ) : (
    <span className="text-muted-foreground">-</span>
  );
}

/** Read only: AutoCount is the source of truth for branches (plan 1.11, ruling Q2). */
export default function BranchesList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'last_synced_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [book, setBook] = useState<string>('');
  const [inCrm, setInCrm] = useState<string>('all');

  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
  });
  useResetPageOnFilterChange(setPagination, [searchQuery, book, inCrm]);

  const { data: books } = useBranchBooks();
  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useBranches({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
    book: book || undefined,
    inCrm: inCrm === 'all' ? undefined : (inCrm as 'yes' | 'no'),
  });

  const columns = useMemo<ColumnDef<Branch>[]>(
    () => [
      {
        accessorKey: 'acc_no',
        header: ({ column }) => <DataGridColumnHeader title="Customer Code" column={column} />,
        cell: ({ row }) => <Truncated value={row.original.acc_no} />,
        size: 150,
        meta: { headerTitle: 'Customer Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => <DataGridColumnHeader title="Customer Name" column={column} />,
        // Matched in the service per page, not a column of the table: not sortable.
        enableSorting: false,
        cell: ({ row }) => {
          const { customer_id, customer_name, acc_no } = row.original;
          if (customer_id && customer_name) {
            return (
              <Link
                href={`/order-management/customers/${customer_id}`}
                className="block truncate text-primary hover:underline"
                title={customer_name}
              >
                {customer_name}
              </Link>
            );
          }
          return acc_no ? (
            <Badge variant="warning" appearance="light">
              Not in CRM
            </Badge>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 260,
        meta: { headerTitle: 'Customer Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'branch_code',
        header: ({ column }) => <DataGridColumnHeader title="Branch Code" column={column} />,
        cell: ({ row }) => <Truncated value={row.original.branch_code} />,
        size: 140,
        meta: { headerTitle: 'Branch Code', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        accessorKey: 'branch_name',
        header: ({ column }) => <DataGridColumnHeader title="Branch Name" column={column} />,
        cell: ({ row }) => <Truncated value={row.original.branch_name} />,
        size: 260,
        meta: { headerTitle: 'Branch Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'source_book',
        header: ({ column }) => <DataGridColumnHeader title="Book" column={column} />,
        cell: ({ row }) => <Truncated value={row.original.source_book} />,
        size: 110,
        meta: { headerTitle: 'Book', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        accessorKey: 'last_synced_at',
        header: ({ column }) => <DataGridColumnHeader title="Last Synced" column={column} />,
        cell: ({ row }) => <Truncated value={formatDateTimeSafe(row.original.last_synced_at)} />,
        size: 180,
        meta: { headerTitle: 'Last Synced', skeleton: <Skeleton className="h-4 w-28" /> },
      },
    ],
    [],
  );

  const total = data?.pagination.total || 0;
  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
  });

  const activeFilters = (book ? 1 : 0) + (inCrm !== 'all' ? 1 : 0);
  const clearFilters = () => {
    setBook('');
    setInCrm('all');
  };

  if (!isLoading && total === 0 && !searchQuery && activeFilters === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <p className="font-medium">No branches yet</p>
          <p className="text-sm text-muted-foreground">
            Branches appear after the next AutoCount sync.
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
      listingKey={BRANCHES_LISTING_KEY}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
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
                placeholder="Search branches..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: activeFilters > 0,
              activeCount: activeFilters,
              content: (
                <div className="space-y-4">
                  <div>
                    <Label>Book</Label>
                    <SearchableSelect
                      value={book}
                      onChange={setBook}
                      options={(books ?? []).map((b) => ({ value: b, label: b }))}
                      placeholder="All books"
                      clearable
                      triggerClassName="mt-1"
                    />
                  </div>
                  <div>
                    <Label>In CRM</Label>
                    <SearchableSelect
                      value={inCrm}
                      onChange={(v) => setInCrm(v || 'all')}
                      options={[
                        { value: 'all', label: 'All branches' },
                        { value: 'yes', label: 'Customer in CRM' },
                        { value: 'no', label: 'Customer not in CRM' },
                      ]}
                      placeholder="All branches"
                      triggerClassName="mt-1"
                    />
                  </div>
                  {activeFilters > 0 && (
                    <div className="flex justify-end">
                      <Button variant="ghost" size="sm" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    </div>
                  )}
                </div>
              ),
            }}
            exportConfig={{ filename: 'customer_branches_export.xlsx' }}
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
