'use client';

import * as React from 'react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTable, CardTitle, CardToolbar } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateInMalaysia } from '@/lib/helpers';
import type { AutocountChangeListRow } from '../../_shared/types/projectSalesOrder.types';
import { formatQty } from './SalesOrderMoney';
import { distinctName } from '@/lib/product-display';

/**
 * The AutoCount change list (section 9.4): the accepted rows of an amendment, in the export's
 * own column order, so keying the change into AutoCount is a copy rather than a re-derivation.
 * Declined rows are never in it - they were never in the amendment either.
 */
export function AmendmentAutocountChangeList({
  rows,
  declinedCount,
  isLoading,
  onExport,
  exporting,
}: {
  rows: AutocountChangeListRow[];
  declinedCount: number;
  isLoading: boolean;
  onExport: () => void;
  exporting: boolean;
}) {
  const columns = React.useMemo<ColumnDef<AutocountChangeListRow>[]>(
    () => [
      {
        id: 'so_number',
        header: ({ column }) => <DataGridColumnHeader title="S/O NO" column={column} />,
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.so_number}>
            {row.original.so_number}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'S/O NO' },
      },
      {
        id: 'line_no',
        header: ({ column }) => <DataGridColumnHeader title="LINE" column={column} />,
        cell: ({ row }) => <span className="tabular-nums">{row.original.line_no}</span>,
        size: 70,
        meta: { headerTitle: 'LINE' },
      },
      {
        id: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="ITEM CODE" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.item_code ?? ''}>
            {row.original.item_code ?? '-'}
          </span>
        ),
        size: 150,
        meta: { headerTitle: 'ITEM CODE' },
      },
      {
        id: 'product_name',
        header: ({ column }) => <DataGridColumnHeader title="PRODUCT" column={column} />,
        cell: ({ row }) => (
          <span
            className="block truncate"
            title={distinctName(row.original.item_code, row.original.product_name) ?? ''}
          >
            {distinctName(row.original.item_code, row.original.product_name) ?? '-'}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'PRODUCT' },
      },
      {
        id: 'verb',
        header: ({ column }) => <DataGridColumnHeader title="VERB" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.verb}>
            {row.original.verb}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'VERB' },
      },
      {
        id: 'old_qty',
        header: ({ column }) => <DataGridColumnHeader title="OLD QTY" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate tabular-nums text-end">
            {row.original.old_qty ? formatQty(row.original.old_qty) : '-'}
          </span>
        ),
        size: 90,
        meta: { headerTitle: 'OLD QTY' },
      },
      {
        id: 'new_qty',
        header: ({ column }) => <DataGridColumnHeader title="NEW QTY" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate tabular-nums text-end font-medium">
            {row.original.new_qty ? formatQty(row.original.new_qty) : '-'}
          </span>
        ),
        size: 90,
        meta: { headerTitle: 'NEW QTY' },
      },
      {
        id: 'old_date',
        header: ({ column }) => <DataGridColumnHeader title="OLD DATE" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate">
            {row.original.old_date ? formatDateInMalaysia(row.original.old_date) : '-'}
          </span>
        ),
        size: 120,
        meta: { headerTitle: 'OLD DATE' },
      },
      {
        id: 'new_date',
        header: ({ column }) => <DataGridColumnHeader title="NEW DATE" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate font-medium">
            {row.original.new_date ? formatDateInMalaysia(row.original.new_date) : '-'}
          </span>
        ),
        size: 120,
        meta: { headerTitle: 'NEW DATE' },
      },
      {
        id: 'new_so_number',
        header: ({ column }) => <DataGridColumnHeader title="NEW S/O NO" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.new_so_number ?? ''}>
            {row.original.new_so_number ?? '-'}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'NEW S/O NO' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row, index) => `${row.so_number}:${row.line_no}:${index}`,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      listingKey="projects.projects.view::project-so-amendment-autocount-change-list"
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <Card>
        <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 break-words">
            <CardTitle className="text-sm">AutoCount change list</CardTitle>
            {isLoading ? (
              <Skeleton className="mt-1 h-3.5 w-48" />
            ) : (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {`${rows.length.toLocaleString()} accepted row${rows.length === 1 ? '' : 's'} ready for AutoCount` +
                  (declinedCount > 0 ? ` · ${declinedCount} declined and excluded` : '')}
              </p>
            )}
          </div>
          <CardToolbar>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={rows.length === 0 || exporting}
              onClick={onExport}
            >
              <Download className="size-4" aria-hidden />
              {exporting ? 'Exporting…' : 'Export for AutoCount'}
            </Button>
          </CardToolbar>
        </CardHeader>

        <CardTable>
          {isLoading ? (
            <div className="space-y-2 p-4" aria-hidden>
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
            </div>
          ) : rows.length === 0 ? (
            <div className="px-6 py-10 text-center">
              <h3 className="text-sm font-semibold">Nothing to export yet</h3>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                {declinedCount > 0
                  ? 'Every row of this amendment was declined.'
                  : 'Decide the rows above; accepted ones will show here.'}
              </p>
            </div>
          ) : (
            <DataGridTable />
          )}
        </CardTable>
      </Card>
    </DataGrid>
  );
}
