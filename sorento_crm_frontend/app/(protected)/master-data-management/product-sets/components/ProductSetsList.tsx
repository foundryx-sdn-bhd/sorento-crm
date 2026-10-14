'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Layers, Plus, Sparkles, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { Skeleton } from '@/components/ui/skeleton';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useProductSets } from '../hooks/useProductSets';
import type { ProductSet } from '../types/productSet.types';
import { ProductSetFormModal } from './ProductSetFormModal';
import { distinctName } from '@/lib/product-display';

/** RM, or an explicit absence. A price of zero and a missing price differ. */
function priceCell(set: ProductSet) {
  const { resolved, is_overridden, computed } = set.price;
  if (resolved === null) {
    return (
      <span className="text-muted-foreground" title="No member sets the price yet">
        Not set
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5">
      <span className="tabular-nums">RM {resolved.toLocaleString('en-MY')}</span>
      {is_overridden ? (
        <Badge variant="warning" size="sm" title={`Computed: RM ${computed?.toLocaleString('en-MY')}`}>
          Override
        </Badge>
      ) : null}
    </span>
  );
}

export default function ProductSetsList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'set_code', desc: false }]);
  const {
    value: searchQuery,
    setValue: setSearchQuery,
    debouncedValue: debouncedSearch,
    isSettling: debouncedSearchSettling,
  } = useDebouncedSearch();
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [debouncedSearch, sorting]);

  const { data, isLoading, isPlaceholderData, isError, error, refetch, isFetching } = useProductSets({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery: debouncedSearch,
  });
  // Delete asks nothing (D7): the row dims and a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'product_set.delete',
    entityType: 'product_set',
    successMessage: 'Product set deleted',
    invalidateKeys: [['product-sets']],
  });
  const rowPending = useRowPending<ProductSet>('product_set');

  const rows = useMemo<ProductSet[]>(() => data?.data ?? [], [data]);
  const total = data?.pagination.total ?? 0;
  /** No rows AND no search: the difference between "none exist" and "none match". */
  const isTrulyEmpty = !isLoading && !isError && total === 0 && debouncedSearch === '';

  // The whole row opens the set, carrying the list query the pager rebuilds its
  // key from. Only the set-code link opened it before, so most of the row was dead.
  // Memoised, and in the columns' deps: a columns memo that captured the first
  // `rowHref` would keep linking every row to page 1 of an unfiltered list.
  const rowHref = useCallback(
    (row: ProductSet) => {
      const search = buildDetailSearch({
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery: debouncedSearch,
      });
      return `/master-data-management/product-sets/${row.id}${search ? `?${search}` : ''}`;
    },
    [pagination.pageIndex, pagination.pageSize, sorting, debouncedSearch],
  );

  const columns = useMemo<ColumnDef<ProductSet>[]>(
    () => [
      {
        accessorKey: 'set_code',
        header: ({ column }) => <DataGridColumnHeader title="Set code" column={column} />,
        cell: ({ row }) => (
          <Link
            href={rowHref(row.original)}
            className="truncate font-medium text-primary hover:underline"
            title={row.original.set_code}
            onClick={(event) => event.stopPropagation()}
          >
            {row.original.set_code}
          </Link>
        ),
        size: 200,
        meta: { headerTitle: 'Set code', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={distinctName(row.original.set_code, row.original.name) ?? undefined}>
            {distinctName(row.original.set_code, row.original.name)}
          </span>
        ),
        size: 280,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        accessorKey: 'member_count',
        header: ({ column }) => <DataGridColumnHeader title="Members" column={column} />,
        cell: ({ row }) =>
          row.original.member_count === 0 ? (
            <Badge variant="secondary" size="sm">
              None yet
            </Badge>
          ) : (
            <span className="tabular-nums">{row.original.member_count}</span>
          ),
        size: 110,
        meta: { headerTitle: 'Members', skeleton: <Skeleton className="h-4 w-8" /> },
      },
      {
        id: 'price',
        header: ({ column }) => <DataGridColumnHeader title="Price" column={column} />,
        cell: ({ row }) => priceCell(row.original),
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Price', skeleton: <Skeleton className="h-4 w-20" /> },
      },
      {
        id: 'complete_sets',
        header: ({ column }) => <DataGridColumnHeader title="Complete sets" column={column} />,
        cell: ({ row }) => {
          const { complete_sets, limiting_member_code } = row.original;
          if (complete_sets === null) return <span className="text-muted-foreground">-</span>;
          // A zero is only useful with the part that caused it. "0" alone reads
          // as a bug when there is stock on the shelf.
          return complete_sets === 0 && limiting_member_code ? (
            <span className="flex items-center gap-1.5">
              <span className="tabular-nums font-medium text-destructive">0</span>
              <span className="truncate text-xs text-muted-foreground" title={`Short on ${limiting_member_code}`}>
                short on {limiting_member_code}
              </span>
            </span>
          ) : (
            <span className="tabular-nums">{complete_sets}</span>
          );
        },
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Complete sets', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      {
        id: 'actions',
        header: '',
        // The row opens the record, so the cell carries only the secondary and
        // destructive actions, in the "..." menu the record page's gear mirrors
        // (D15).
        cell: ({ row }) => (
          <RowActionsMenu
            ariaLabel={`product set ${row.original.set_code}`}
            actions={[
              {
                key: 'product_set.delete',
                label: 'Delete product set',
                icon: Trash2,
                kind: 'destructive',
                run: () =>
                  deletion.run({
                    id: row.original.id,
                    subject: `${row.original.name} (${row.original.set_code})`,
                  }),
              },
            ]}
          />
        ),
        size: 70,
        enableSorting: false,
        enableHiding: false,
        meta: { headerTitle: 'Actions', cellClassName: 'text-right' },
      },
    ],
    [rowHref, deletion],
  );

  const table = useReactTable({
    columns,
    data: rows,
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

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" asChild>
        <Link href="/master-data-management/product-sets/proposals">
          <Sparkles className="size-4" /> Propose
        </Link>
      </Button>
      <Button onClick={() => setCreating(true)}>
        <Plus className="size-4" /> Add set
      </Button>
    </div>
  );

  return (
    <div className="space-y-3">
      {isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {error instanceof Error ? error.message : 'Failed to load product sets.'}
        </div>
      ) : null}

      {isTrulyEmpty ? (
        <Card>
          <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
            <div className="rounded-full bg-muted p-3">
              <Layers className="size-6 text-muted-foreground" />
            </div>
            <p className="max-w-md text-sm text-muted-foreground">
              A set gives one code to products sold together, so a code printed on a flyer can
              be found and priced.
            </p>
            <div className="flex flex-wrap justify-center gap-2 pt-1">
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" /> Add set
              </Button>
              <Button variant="outline" asChild>
                <Link href="/master-data-management/product-sets/proposals">
                  <Sparkles className="size-4" /> Propose from catalogue
                </Link>
              </Button>
            </div>
          </div>
        </Card>
      ) : (
        <DataGrid
          table={table}
          recordCount={total}
          isLoading={isLoading}
          isPlaceholderData={isPlaceholderData}
          rowHref={rowHref}
          rowPending={rowPending}
          listingKey="master_data.product_sets.view"
          tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
          emptyMessage="No product sets match that search."
        >
          <Card>
            <CardHeader className="block">
              <DataGridListToolbar
                table={table}
                searchSlot={
                  <ListSearchInput
                    value={searchQuery}
                    onChange={setSearchQuery}
                    isSettling={isSearchInFlight(debouncedSearchSettling, isFetching, debouncedSearch)}
                    placeholder="Search set code or name..."
                    className="w-64"
                  />
                }
                exportConfig={false}
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
      )}

      <ProductSetFormModal open={creating} onOpenChange={setCreating} />
    </div>
  );
}
