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
import { useProjectSeries } from '../../_shared/hooks/useProjects';
import type { ProjectSeries } from '../../_shared/types/project.types';

/**
 * The series list, in the standard list layout.
 *
 * Copied in SHAPE from the users list, which the client named as the reference: one
 * `DataGridListToolbar` inside a `CardHeader className="block"`, so search, Columns, Export and
 * Refresh sit inline on the left and the primary Add sits right. Nothing is hand-rolled here -
 * the previous screen grew its own button row under the heading, which is the thing being fixed.
 *
 * A row opens a PAGE, never a dialog. That is the client's other instruction and the reason
 * this file has no create/edit modal in it at all.
 */
export function SeriesListClient() {
  const router = useRouter();
  const series = useProjectSeries(true);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 15,
  });
  const [sorting, setSorting] = React.useState<SortingState>([{ id: 'name', desc: false }]);

  const rows = React.useMemo(() => {
    const all = series.data ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) return all;
    return all.filter(
      (row) =>
        row.name.toLowerCase().includes(needle) ||
        (row.brand_name ?? '').toLowerCase().includes(needle),
    );
  }, [search, series.data]);

  const columns = React.useMemo<ColumnDef<ProjectSeries>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Series" column={column} />,
        size: 260,
        meta: { headerTitle: 'Series', skeleton: <Skeleton className="h-4 w-40" /> },
        cell: ({ row }) => (
          <span className="block truncate font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'brand_name',
        header: ({ column }) => <DataGridColumnHeader title="Brand" column={column} />,
        size: 150,
        meta: { headerTitle: 'Brand', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.brand_name ?? ''}>
            {row.original.brand_name || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'product_count',
        header: ({ column }) => <DataGridColumnHeader title="Products" column={column} />,
        size: 130,
        meta: { headerTitle: 'Products', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.product_count === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            <span>{row.original.product_count}</span>
          ),
      },
      {
        accessorKey: 'covered_category_count',
        header: ({ column }) => <DataGridColumnHeader title="Categories" column={column} />,
        size: 130,
        meta: { headerTitle: 'Categories', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) =>
          row.original.category_ids.length === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            <span
              title={row.original.category_names.join(', ')}
            >{`${row.original.category_ids.length}`}</span>
          ),
      },
      {
        accessorKey: 'quotation_count',
        header: ({ column }) => <DataGridColumnHeader title="Used by" column={column} />,
        size: 150,
        meta: { headerTitle: 'Used by', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) =>
          row.original.quotation_count > 0 ? (
            <span className="block truncate">{`${row.original.quotation_count} quotation${
              row.original.quotation_count === 1 ? '' : 's'
            }`}</span>
          ) : (
            <span className="text-muted-foreground">Not quoted yet</span>
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
    <Button onClick={() => router.push('/project-sales/series/new')}>
      <Plus />
      Add series
    </Button>
  );

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={series.isLoading}
      error={series.error}
      onRetry={() => void series.refetch()}
      onRowClick={(row: ProjectSeries) => router.push(`/project-sales/series/${row.id}`)}
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
                placeholder="Search series"
                className="w-full sm:w-56 md:w-64"
              />
            }
            exportConfig={{ filename: 'series_export.xlsx' }}
            onRefresh={() => void series.refetch()}
            isRefreshing={series.isFetching}
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
