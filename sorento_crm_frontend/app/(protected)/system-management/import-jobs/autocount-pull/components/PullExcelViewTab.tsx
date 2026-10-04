'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { usePullRows } from '../hooks/useAutocountPull';
import {
  isDocumentEntity,
  type AutocountPullEntity,
  type AutocountPullExcelRow,
  type DeliveryOrderExcelRow,
  type GoodsReceiveNoteExcelRow,
  type ProductExcelRow,
  type StockExcelRow,
} from '../types/autocountPull.types';

export interface PullExcelViewTabProps {
  jobId: string;
  entity: AutocountPullEntity;
}

/** D4 (small-fix track): the shared `DataGrid` falls back to the current pathname when no
 *  `listingKey` is given - which, on this page, embeds the job id, so every pull minted its
 *  OWN never-reused `UserListColumnConfig` row and a first-time insert raced the page's own
 *  column-hook write into it (a 500 from the unique-constraint loser, then a 200 once the row
 *  existed). One stable key per entity, not per record. */
const EXCEL_VIEW_LISTING_KEY: Record<AutocountPullEntity, string> = {
  products: 'master_data.products.autocount_pull::excel-view',
  stock_balances: 'inventory.stock.autocount_pull::excel-view',
  delivery_orders: 'order_management.orders.autocount_pull::excel-view',
  goods_receive_notes: 'procurement.grn.autocount_pull::excel-view',
};

function textCell<T>(pick: (row: T) => string | null | undefined) {
  return function TextCell({ row }: { row: { original: AutocountPullExcelRow } }) {
    const value = pick(row.original as T) || '-';
    return (
      <span className="block truncate" title={value}>
        {value}
      </span>
    );
  };
}

/** `digits` null = the number at its own precision (a quantity: 2.5 m stays "2.5", never a
 *  rounded "3" that no longer matches the checker's sheet, review S5); a money column keeps
 *  two decimals. */
function numberCell<T extends { qty: number | null } = DeliveryOrderExcelRow>(
  pick: (row: T) => number | null | undefined,
  digits: number | null = 2,
) {
  return function NumberCell({ row }: { row: { original: AutocountPullExcelRow } }) {
    const value = pick(row.original as unknown as T);
    if (value == null) return <span className="tabular-nums">-</span>;
    return <span className="tabular-nums">{digits == null ? String(value) : value.toFixed(digits)}</span>;
  };
}

/** `YYYY-MM-DD` -> dd/MM/yyyy, the one date format this page uses (the scope line above
 *  the tabs is dd/MM/yyyy too, review N2); anything else passes through as text. */
export function formatExcelDay(day: string | null | undefined): string {
  if (!day) return '-';
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : day;
}

/** One row per DO LINE, the "Import delivery order lines" sheet's columns in its order
 *  (AC-DP-30), so the pull reads side by side with the sheet the checker uploads today. */
export const DELIVERY_ORDER_COLUMNS: ColumnDef<AutocountPullExcelRow>[] = [
  {
    id: 'doc_no',
    header: ({ column }) => <DataGridColumnHeader title="Doc No" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.doc_no),
    size: 140,
    meta: { headerTitle: 'Doc No', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'doc_date',
    header: ({ column }) => <DataGridColumnHeader title="Doc Date" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => formatExcelDay(r.doc_date)),
    size: 110,
    meta: { headerTitle: 'Doc Date', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'debtor_code',
    header: ({ column }) => <DataGridColumnHeader title="Debtor Code" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.debtor_code),
    size: 120,
    meta: { headerTitle: 'Debtor Code', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'debtor_name',
    header: ({ column }) => <DataGridColumnHeader title="Debtor Name" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.debtor_name),
    size: 220,
    meta: { headerTitle: 'Debtor Name', skeleton: <Skeleton className="h-4 w-32" /> },
  },
  {
    id: 'item_code',
    header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.item_code),
    size: 140,
    meta: { headerTitle: 'Item Code', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'description',
    header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.description),
    size: 260,
    meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-40" /> },
  },
  {
    id: 'location',
    header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.location),
    size: 110,
    meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-14" /> },
  },
  {
    id: 'qty',
    header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
    cell: numberCell((r) => r.qty, null),
    size: 90,
    meta: { headerTitle: 'Qty', skeleton: <Skeleton className="h-4 w-10" /> },
  },
  {
    id: 'uom',
    header: ({ column }) => <DataGridColumnHeader title="UOM" column={column} />,
    cell: textCell<DeliveryOrderExcelRow>((r) => r.uom),
    size: 80,
    meta: { headerTitle: 'UOM', skeleton: <Skeleton className="h-4 w-10" /> },
  },
  {
    id: 'unit_price',
    header: ({ column }) => <DataGridColumnHeader title="Unit Price" column={column} />,
    cell: numberCell((r) => r.unit_price),
    size: 110,
    meta: { headerTitle: 'Unit Price', skeleton: <Skeleton className="h-4 w-14" /> },
  },
  {
    id: 'sub_total',
    header: ({ column }) => <DataGridColumnHeader title="Sub Total" column={column} />,
    cell: numberCell((r) => r.sub_total),
    size: 110,
    meta: { headerTitle: 'Sub Total', skeleton: <Skeleton className="h-4 w-14" /> },
  },
];

/** One row per GRN LINE, the "DETAIL LISTING" sheet's columns in its order (AC-GP-50);
 *  "Our PO No." is the PO or SPO the line was received against. */
export const GOODS_RECEIVE_NOTE_COLUMNS: ColumnDef<AutocountPullExcelRow>[] = [
  {
    id: 'doc_no',
    header: ({ column }) => <DataGridColumnHeader title="Doc No" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.doc_no),
    size: 140,
    meta: { headerTitle: 'Doc No', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'doc_date',
    header: ({ column }) => <DataGridColumnHeader title="Doc Date" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => formatExcelDay(r.doc_date)),
    size: 110,
    meta: { headerTitle: 'Doc Date', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'creditor_code',
    header: ({ column }) => <DataGridColumnHeader title="Creditor Code" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.creditor_code),
    size: 120,
    meta: { headerTitle: 'Creditor Code', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'creditor_name',
    header: ({ column }) => <DataGridColumnHeader title="Creditor Name" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.creditor_name),
    size: 220,
    meta: { headerTitle: 'Creditor Name', skeleton: <Skeleton className="h-4 w-32" /> },
  },
  {
    id: 'from_doc_no',
    header: ({ column }) => <DataGridColumnHeader title="Our PO No." column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.from_doc_no),
    size: 150,
    meta: { headerTitle: 'Our PO No.', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'item_code',
    header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.item_code),
    size: 140,
    meta: { headerTitle: 'Item Code', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'description',
    header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.description),
    size: 260,
    meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-40" /> },
  },
  {
    id: 'location',
    header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.location),
    size: 110,
    meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-14" /> },
  },
  {
    id: 'qty',
    header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
    cell: numberCell<GoodsReceiveNoteExcelRow>((r) => r.qty, null),
    size: 90,
    meta: { headerTitle: 'Qty', skeleton: <Skeleton className="h-4 w-10" /> },
  },
  {
    id: 'uom',
    header: ({ column }) => <DataGridColumnHeader title="UOM" column={column} />,
    cell: textCell<GoodsReceiveNoteExcelRow>((r) => r.uom),
    size: 80,
    meta: { headerTitle: 'UOM', skeleton: <Skeleton className="h-4 w-10" /> },
  },
];

export const PRODUCT_COLUMNS: ColumnDef<AutocountPullExcelRow>[] = [
  {
    id: 'item_code',
    header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.item_code}>
        {row.original.item_code}
      </span>
    ),
    size: 140,
    meta: { headerTitle: 'Item Code', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'description',
    header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as ProductExcelRow).description;
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 320,
    meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-48" /> },
  },
  {
    id: 'desc_2',
    header: ({ column }) => <DataGridColumnHeader title="Desc 2" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as ProductExcelRow).desc_2 || '-';
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 220,
    meta: { headerTitle: 'Desc 2', skeleton: <Skeleton className="h-4 w-32" /> },
  },
  {
    id: 'item_group',
    header: ({ column }) => <DataGridColumnHeader title="Item Group" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as ProductExcelRow).item_group || '-';
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 130,
    meta: { headerTitle: 'Item Group', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'item_brand',
    header: ({ column }) => <DataGridColumnHeader title="Item Brand" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as ProductExcelRow).item_brand || '-';
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 130,
    meta: { headerTitle: 'Item Brand', skeleton: <Skeleton className="h-4 w-20" /> },
  },
  {
    id: 'price',
    header: ({ column }) => <DataGridColumnHeader title="Price" column={column} />,
    cell: ({ row }) => (
      <span className="tabular-nums">{(row.original as ProductExcelRow).price.toFixed(2)}</span>
    ),
    size: 100,
    meta: { headerTitle: 'Price', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'is_active',
    header: ({ column }) => <DataGridColumnHeader title="Is Active" column={column} />,
    cell: ({ row }) => <span>{(row.original as ProductExcelRow).is_active ? 'T' : 'F'}</span>,
    size: 90,
    meta: { headerTitle: 'Is Active', skeleton: <Skeleton className="h-4 w-8" /> },
  },
];

export const STOCK_COLUMNS: ColumnDef<AutocountPullExcelRow>[] = [
  {
    id: 'item_code',
    header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.item_code}>
        {row.original.item_code}
      </span>
    ),
    size: 160,
    meta: { headerTitle: 'Item Code', skeleton: <Skeleton className="h-4 w-24" /> },
  },
  {
    id: 'item_description',
    header: ({ column }) => <DataGridColumnHeader title="Item Description" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as StockExcelRow).item_description;
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 360,
    meta: { headerTitle: 'Item Description', skeleton: <Skeleton className="h-4 w-56" /> },
  },
  {
    id: 'location',
    header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
    cell: ({ row }) => {
      const value = (row.original as StockExcelRow).location;
      return (
        <span className="block truncate" title={value}>
          {value}
        </span>
      );
    },
    size: 130,
    meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-16" /> },
  },
  {
    id: 'on_hand_qty',
    header: ({ column }) => <DataGridColumnHeader title="On Hand Qty" column={column} />,
    cell: ({ row }) => (
      <span className="tabular-nums">
        {(row.original as StockExcelRow).on_hand_qty.toLocaleString()}
      </span>
    ),
    size: 120,
    meta: { headerTitle: 'On Hand Qty', skeleton: <Skeleton className="h-4 w-16" /> },
  },
];

/**
 * The whole pull laid out exactly like the manual template - same columns, same order, so it
 * can be read side by side with the macro workbook (AC-RV-3/4). Server-style paging + search,
 * fixed/resizable layout per the DataGrid listing contract.
 */
export function PullExcelViewTab({ jobId, entity }: PullExcelViewTabProps) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
    isSettling: searchSettling,
  } = useDebouncedSearch();

  const query = { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, query: search || undefined };
  const { data, isLoading, isPlaceholderData, isFetching, isError, error } = usePullRows(jobId, query);

  const columns = useMemo(
    () =>
      entity === 'products'
        ? PRODUCT_COLUMNS
        : entity === 'delivery_orders'
          ? DELIVERY_ORDER_COLUMNS
          : entity === 'goods_receive_notes'
            ? GOODS_RECEIVE_NOTE_COLUMNS
            : STOCK_COLUMNS,
    [entity],
  );
  const total = data?.pagination?.total ?? 0;

  const table = useReactTable({
    columns,
    data: data?.data ?? [],
    pageCount: Math.ceil(total / pagination.pageSize) || 0,
    // A DO line is unique by (doc no, item, location) plus its position: the same item can
    // sit twice on one document (two batches), so the index keeps the two apart.
    getRowId: (row, index) =>
      ('doc_no' in row ? `${row.doc_no}-` : '') +
      row.item_code +
      ('location' in row ? `-${row.location}` : '') +
      ('doc_no' in row ? `-${index}` : ''),
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={total}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      listingKey={EXCEL_VIEW_LISTING_KEY[entity]}
    >
      <Card>
        <CardHeader className="block space-y-3">
          <div className="w-full sm:w-80">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              isSettling={isSearchInFlight(searchSettling, isFetching, search)}
              placeholder={
                isDocumentEntity(entity) ? 'Search by doc no or item code…' : 'Search by item code…'
              }
              className="w-full"
            />
          </div>
        </CardHeader>
        <CardTable>
          {isError ? (
            <div className="px-6 py-10 text-center text-sm text-destructive">
              {error instanceof Error ? error.message : 'Failed to load the pull rows'}
            </div>
          ) : !isLoading && total === 0 ? (
            <div className="px-6 py-10 text-center text-sm text-muted-foreground">
              {search ? 'No rows match that search.' : 'Nothing to show yet.'}
            </div>
          ) : (
            <DataGridTable />
          )}
        </CardTable>
        {total > 0 && (
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        )}
      </Card>
    </DataGrid>
  );
}

export default PullExcelViewTab;
