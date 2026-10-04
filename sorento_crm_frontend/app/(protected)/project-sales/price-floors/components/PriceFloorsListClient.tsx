'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Plus } from 'lucide-react';
import {
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { usePriceFloors } from '../../_shared/hooks/useProjects';
import type { PriceFloorRule } from '../../_shared/types/project.types';

const LEVEL_LABEL: Record<string, string> = {
  product: 'Product',
  category: 'Category',
  system: 'System',
};

/** What a floor applies TO, in words rather than a UUID. */
function target(row: PriceFloorRule): string {
  if (row.product_code) return row.product_code;
  if (row.category_name) return row.category_name;
  return 'Everything';
}

/**
 * The price floor list, in the standard list layout - same shape as the users list and the
 * series list beside it, so the three cannot drift.
 */
export function PriceFloorsListClient() {
  const router = useRouter();
  const floors = usePriceFloors();
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 15,
  });
  const [sorting, setSorting] = React.useState<SortingState>([{ id: 'level', desc: false }]);

  const rows = React.useMemo(() => {
    const all = floors.data ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((row) => target(row).toLowerCase().includes(needle));
  }, [floors.data, search]);

  const columns = React.useMemo<ColumnDef<PriceFloorRule>[]>(
    () => [
      {
        accessorKey: 'level',
        header: ({ column }) => <DataGridColumnHeader title="Level" column={column} />,
        size: 130,
        meta: { headerTitle: 'Level', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) => <span>{LEVEL_LABEL[row.original.level] ?? row.original.level}</span>,
      },
      {
        id: 'target',
        header: ({ column }) => <DataGridColumnHeader title="Applies to" column={column} />,
        size: 260,
        meta: { headerTitle: 'Applies to', skeleton: <Skeleton className="h-4 w-40" /> },
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={target(row.original)}>
            {target(row.original)}
          </span>
        ),
      },
      {
        accessorKey: 'value',
        header: ({ column }) => <DataGridColumnHeader title="Floor" column={column} />,
        size: 150,
        meta: { headerTitle: 'Floor', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => (
          <span className="tabular-nums">
            {row.original.mode === 'percent'
              ? `${row.original.value}% of list`
              : `RM ${row.original.value}`}
          </span>
        ),
      },
      {
        accessorKey: 'notes',
        header: ({ column }) => <DataGridColumnHeader title="Notes" column={column} />,
        size: 260,
        meta: { headerTitle: 'Notes', skeleton: <Skeleton className="h-4 w-40" /> },
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.notes ?? ''}>
            {row.original.notes || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'is_active',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 110,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="outline">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    defaultColumn: { minSize: 60, maxSize: 800, size: 150 },
  });

  // The one offer this listing makes, in the toolbar only: the empty state
  // does not repeat it (#1335).
  const listPrimaryAction = (
    <Button onClick={() => router.push('/project-sales/price-floors/new')}>
      <Plus />
      Add floor
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={floors.isLoading}
      error={floors.error}
      onRetry={() => void floors.refetch()}
      onRowClick={(row: PriceFloorRule) =>
        router.push(`/project-sales/price-floors/${row.id}`)
      }
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      tableClassNames={{ edgeCell: 'px-5' }}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder="Search floors"
                className="w-full sm:w-56 md:w-64"
              />
            }
            exportConfig={{ filename: 'price_floors_export.xlsx' }}
            onRefresh={() => void floors.refetch()}
            isRefreshing={floors.isFetching}
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
