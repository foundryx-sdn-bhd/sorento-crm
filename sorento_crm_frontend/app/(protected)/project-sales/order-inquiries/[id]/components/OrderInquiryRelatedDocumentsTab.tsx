'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateInMalaysia } from '@/lib/helpers';
import { spoDetailHref } from '@/lib/spo-detail';
import { formatInquiryQty } from '../../../_shared/lib/orderInquiryWorklist';
import type {
  OrderInquiryRelatedPurchaseOrder,
  OrderInquiryRelatedSpo,
} from '../../../_shared/types/orderInquiry.types';

/**
 * Related PO and Related SPO (AC-DP-08, owner markup 21 Sep): one shared `DataGrid` over
 * whichever half of `GET .../related-documents` the caller passes - client-side, since
 * both lists are already the whole answer in one response, no server page behind them.
 */

function relatedPoColumns(): ColumnDef<OrderInquiryRelatedPurchaseOrder>[] {
  return [
    {
      accessorKey: 'po_number',
      header: ({ column }) => <DataGridColumnHeader title="PO no" column={column} />,
      size: 170,
      meta: { headerTitle: 'PO no', skeleton: <Skeleton className="h-4 w-24" /> },
      cell: ({ row }) => (
        <Link
          href={`/scm/purchase-orders/${row.original.po_id}`}
          onClick={(e) => e.stopPropagation()}
          className="block truncate font-medium text-primary hover:underline"
          title={row.original.po_number}
        >
          {row.original.po_number}
        </Link>
      ),
    },
    {
      accessorKey: 'supplier_name',
      header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
      size: 220,
      meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-32" /> },
      cell: ({ row }) =>
        row.original.supplier_name ? (
          <span className="block truncate" title={row.original.supplier_name}>
            {row.original.supplier_name}
          </span>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
    },
    {
      accessorKey: 'po_date',
      header: ({ column }) => <DataGridColumnHeader title="PO date" column={column} />,
      size: 130,
      meta: { headerTitle: 'PO date' },
      cell: ({ row }) =>
        row.original.po_date ? (
          formatDateInMalaysia(row.original.po_date)
        ) : (
          <span className="text-muted-foreground">No date</span>
        ),
    },
    {
      accessorKey: 'lines_linked',
      header: ({ column }) => (
        <DataGridColumnHeader title="Lines linked" column={column} className="justify-end" />
      ),
      size: 120,
      meta: {
        headerTitle: 'Lines linked',
        headerClassName: 'text-right',
        cellClassName: 'text-right tabular-nums',
      },
      cell: ({ row }) => row.original.lines_linked,
    },
    {
      accessorKey: 'qty_linked',
      header: ({ column }) => (
        <DataGridColumnHeader title="Qty linked" column={column} className="justify-end" />
      ),
      size: 130,
      meta: {
        headerTitle: 'Qty linked',
        headerClassName: 'text-right',
        cellClassName: 'text-right tabular-nums',
      },
      cell: ({ row }) => formatInquiryQty(row.original.qty_linked),
      footer: ({ table }) => {
        const total = table
          .getPrePaginationRowModel()
          .rows.reduce((sum, r) => sum + Number(r.original.qty_linked || '0'), 0);
        return <span className="tabular-nums">{formatInquiryQty(String(total))}</span>;
      },
    },
  ];
}

function relatedSpoColumns(): ColumnDef<OrderInquiryRelatedSpo>[] {
  return [
    {
      accessorKey: 'spo_number',
      header: ({ column }) => <DataGridColumnHeader title="SPO no" column={column} />,
      size: 190,
      meta: { headerTitle: 'SPO no', skeleton: <Skeleton className="h-4 w-24" /> },
      // AC-DP-08: the SPO document page (`procurement-management/spo-allocations/
      // [spoNumber]`), addressed the one way every other SPO reference in the app is -
      // `spoDetailHref`, never a hand-encoded string (an SPO number carries a literal
      // `/`, e.g. `SPO-2026/08-0061`).
      cell: ({ row }) => (
        <Link
          href={spoDetailHref(row.original.spo_number)}
          onClick={(e) => e.stopPropagation()}
          className="block truncate font-medium tabular-nums text-primary hover:underline"
          title={row.original.spo_number}
        >
          {row.original.spo_number}
        </Link>
      ),
    },
    {
      accessorKey: 'supplier_name',
      header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
      size: 220,
      meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-32" /> },
      cell: ({ row }) =>
        row.original.supplier_name ? (
          <span className="block truncate" title={row.original.supplier_name}>
            {row.original.supplier_name}
          </span>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
    },
    {
      accessorKey: 'lines_linked',
      header: ({ column }) => (
        <DataGridColumnHeader title="Lines linked" column={column} className="justify-end" />
      ),
      size: 120,
      meta: {
        headerTitle: 'Lines linked',
        headerClassName: 'text-right',
        cellClassName: 'text-right tabular-nums',
      },
      cell: ({ row }) => row.original.lines_linked,
    },
    {
      accessorKey: 'qty_linked',
      header: ({ column }) => (
        <DataGridColumnHeader title="Qty linked" column={column} className="justify-end" />
      ),
      size: 130,
      meta: {
        headerTitle: 'Qty linked',
        headerClassName: 'text-right',
        cellClassName: 'text-right tabular-nums',
      },
      cell: ({ row }) => formatInquiryQty(row.original.qty_linked),
      footer: ({ table }) => {
        const total = table
          .getPrePaginationRowModel()
          .rows.reduce((sum, r) => sum + Number(r.original.qty_linked || '0'), 0);
        return <span className="tabular-nums">{formatInquiryQty(String(total))}</span>;
      },
    },
  ];
}

interface RelatedDocumentsGridProps<TRow extends object> {
  rows: TRow[];
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
  columns: ColumnDef<TRow>[];
  getRowId: (row: TRow) => string;
  searchOf: (row: TRow) => string;
  searchPlaceholder: string;
  emptyTitle: string;
  listingKey: string;
}

function RelatedDocumentsGrid<TRow extends object>({
  rows,
  isLoading,
  error,
  onRetry,
  columns,
  getRowId,
  searchOf,
  searchPlaceholder,
  emptyTitle,
  listingKey,
}: RelatedDocumentsGridProps<TRow>) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [search, setSearch] = useState('');

  const table = useReactTable({
    columns,
    data: rows,
    getRowId,
    state: { pagination, sorting, globalFilter: search },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getColumnCanGlobalFilter: () => true,
    globalFilterFn: (row, _columnId, value) =>
      searchOf(row.original).toLowerCase().includes(String(value ?? '').toLowerCase()),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={table.getFilteredRowModel().rows.length}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      emptyMessage={rows.length === 0 ? emptyTitle : 'No document matches that search.'}
      listingKey={listingKey}
    >
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={search}
                onChange={setSearch}
                placeholder={searchPlaceholder}
                className="w-56"
              />
            }
            exportConfig={false}
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

export function OrderInquiryRelatedPurchaseOrdersTab({
  rows,
  isLoading,
  error,
  onRetry,
}: {
  rows: OrderInquiryRelatedPurchaseOrder[];
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  const columns = useMemo(() => relatedPoColumns(), []);
  return (
    <RelatedDocumentsGrid
      rows={rows}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      columns={columns}
      getRowId={(row) => row.po_id}
      searchOf={(row) => `${row.po_number} ${row.supplier_name ?? ''}`}
      searchPlaceholder="Search purchase order..."
      emptyTitle="No purchase orders linked yet"
      listingKey="projects.projects.view::order-inquiry-related-po"
    />
  );
}

export function OrderInquiryRelatedSposTab({
  rows,
  isLoading,
  error,
  onRetry,
}: {
  rows: OrderInquiryRelatedSpo[];
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  const columns = useMemo(() => relatedSpoColumns(), []);
  return (
    <RelatedDocumentsGrid
      rows={rows}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      columns={columns}
      getRowId={(row) => row.spo_number}
      searchOf={(row) => `${row.spo_number} ${row.supplier_name ?? ''}`}
      searchPlaceholder="Search SPO..."
      emptyTitle="No SPOs linked yet"
      listingKey="projects.projects.view::order-inquiry-related-spo"
    />
  );
}
