'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname } from 'next/navigation';
import {
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { readableValue } from '@/lib/spec-readable';
import { useSpecKeyProductsQuery } from '../../hooks/useSpecKeyProductsQuery';
import type { SpecKeyProduct } from '../../services/productSpecService';

/** Where a value came from, in the words the person reading this uses. */
const SOURCE_LABEL: Record<string, string> = {
  derived: 'Description',
  flyer: 'Flyer',
  code: 'Product code',
  category: 'Category',
  human: 'Set by hand',
};

const PAGE_SIZE = 25;

/**
 * Every product carrying this specification, and the words each value was read
 * from (AC-B.5). Replaces the hand-rolled `SpecKeyProducts` table and its own
 * Prev/Next pager with the shared `DataGrid` + `DataGridPagination` (D10, G.2).
 */
export function SeenInProductsTab({
  specKey,
  label,
  unit,
  valueLabels,
}: {
  specKey: string;
  label: string;
  unit?: string | null;
  /** The key's value_labels (#423), so a value already reads the same label here
   *  as it does on the record's own Values and words tab (item 6). */
  valueLabels?: Record<string, string>;
}) {
  const pathname = usePathname();
  const [valueFilter, setValueFilter] = useState<string | undefined>();
  const [classFilter, setClassFilter] = useState<string | undefined>();
  const [sourceFilter, setSourceFilter] = useState<string | undefined>();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: PAGE_SIZE,
  });
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: query,
    isSettling,
  } = useDebouncedSearch();

  useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [query, valueFilter, classFilter, sourceFilter]);

  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } = useSpecKeyProductsQuery(specKey, {
    value: valueFilter,
    q: query || undefined,
    classLabel: classFilter,
    source: sourceFilter,
    limit: pagination.pageSize,
    offset: pagination.pageIndex * pagination.pageSize,
  });

  const total = data?.total ?? 0;
  const products = data?.products ?? [];

  const productHref = (row: SpecKeyProduct) =>
    `/master-data-management/products/${row.id}?tab=specifications&back=${encodeURIComponent(pathname)}`;

  const columns = useMemo<ColumnDef<SpecKeyProduct>[]>(
    () => [
      {
        accessorKey: 'product_code',
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        size: 140,
        meta: { headerTitle: 'Code' },
        cell: ({ row }) => (
          <span className="truncate font-mono text-xs" title={row.original.product_code}>
            {row.original.product_code}
          </span>
        ),
      },
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 260,
        meta: { headerTitle: 'Description' },
        cell: ({ row }) => (
          <span className="truncate" title={row.original.description ?? ''}>
            {row.original.description ?? '-'}
          </span>
        ),
      },
      {
        accessorKey: 'class',
        header: ({ column }) => <DataGridColumnHeader title="Class" column={column} />,
        size: 140,
        meta: { headerTitle: 'Class' },
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground" title={row.original.class ?? ''}>
            {row.original.class ?? '-'}
          </span>
        ),
      },
      {
        accessorKey: 'value',
        header: ({ column }) => <DataGridColumnHeader title="Value" column={column} />,
        size: 130,
        meta: { headerTitle: 'Value' },
        cell: ({ row }) =>
          row.original.value === null || row.original.value === undefined ? (
            <span className="truncate text-xs">-</span>
          ) : (
            <span
              className="truncate text-xs"
              title={readableValue(row.original.value, unit ?? undefined, valueLabels)}
            >
              {readableValue(row.original.value, unit ?? undefined, valueLabels)}
            </span>
          ),
      },
      {
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        size: 130,
        meta: { headerTitle: 'Source' },
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground">
            {SOURCE_LABEL[String(row.original.source)] ?? row.original.source ?? '-'}
          </span>
        ),
      },
      {
        accessorKey: 'evidence',
        header: ({ column }) => <DataGridColumnHeader title="Evidence" column={column} />,
        size: 220,
        meta: { headerTitle: 'Evidence' },
        cell: ({ row }) => (
          <span
            className="truncate font-mono text-xs text-muted-foreground"
            title={row.original.evidence ?? ''}
          >
            {row.original.evidence ?? '-'}
          </span>
        ),
      },
    ],
    [unit, valueLabels],
  );

  const table = useReactTable({
    columns,
    data: products,
    getRowId: (row) => row.id,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    state: { pagination },
    onPaginationChange: setPagination,
    manualPagination: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const emptyState = (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <p className="text-sm font-medium">Not seen on any product yet</p>
    </div>
  );

  const filtersActiveCount =
    (valueFilter !== undefined ? 1 : 0) +
    (classFilter !== undefined ? 1 : 0) +
    (sourceFilter !== undefined ? 1 : 0);

  return (
    <div className="flex flex-col gap-3">
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        rowHref={productHref}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={total === 0 && !isLoading ? emptyState : undefined}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  className="w-full sm:w-72"
                  value={search}
                  onChange={setSearch}
                  isSettling={isSearchInFlight(isSettling, isFetching, query)}
                  placeholder={`Find a code or description in ${label}`}
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActiveCount > 0,
                activeCount: filtersActiveCount,
                content: (
                  <div className="space-y-3">
                    <SearchableSelect
                      value={valueFilter ?? ''}
                      onChange={(v) => setValueFilter(v || undefined)}
                      clearable
                      placeholder="Value"
                      options={(data?.by_value ?? []).map((row) => ({
                        value: String(row.value),
                        label: `${readableValue(row.value, unit ?? undefined, valueLabels)} (${row.count.toLocaleString()})`,
                      }))}
                    />
                    <SearchableSelect
                      value={classFilter ?? ''}
                      onChange={(v) => setClassFilter(v || undefined)}
                      clearable
                      placeholder="Class"
                      options={(data?.by_class ?? []).map((row) => ({
                        value: String(row.class ?? ''),
                        label: `${row.class ?? 'unclassed'} (${row.count.toLocaleString()})`,
                      }))}
                    />
                    <SearchableSelect
                      value={sourceFilter ?? ''}
                      onChange={(v) => setSourceFilter(v || undefined)}
                      clearable
                      placeholder="Source"
                      options={(data?.by_source ?? []).map((row) => ({
                        value: String(row.source ?? ''),
                        label: `${SOURCE_LABEL[String(row.source)] ?? row.source ?? 'unknown'} (${row.count.toLocaleString()})`,
                      }))}
                    />
                  </div>
                ),
              }}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            <DataGridPagination sizes={[25, 50, 100]} />
          </CardFooter>
        </Card>
      </DataGrid>
    </div>
  );
}

export default SeenInProductsTab;
