'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import { PackageOpen } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { formatDate } from '@/lib/helpers';
import { useComplaintFulfilmentOrders } from '../hooks/useComplaintFulfilmentOrders';
import type { FulfilmentOrder, FulfilmentOrderItem } from '../services/complaintFulfilmentService';

interface ComplaintFulfilmentOrdersSectionProps {
  complaintId: string;
}

/** Items carry no id of their own - the popover's rows are keyed by product code + position,
 * the same pair the raw table's key already used. */
type FulfilmentItemRow = FulfilmentOrderItem & { _rowId: string };

const ITEM_COLUMNS: ColumnDef<FulfilmentItemRow>[] = [
  {
    id: 'product_code',
    accessorFn: (row) => row.product_code,
    header: ({ column }) => <DataGridColumnHeader title="Product code" column={column} />,
    cell: ({ row }) => (
      <div className="text-left font-medium">
        <span className="block truncate" title={row.original.product_code}>
          {row.original.product_code}
        </span>
        {row.original.product_type && (
          <span
            className="block truncate text-xs text-muted-foreground"
            title={row.original.product_type}
          >
            {row.original.product_type}
          </span>
        )}
      </div>
    ),
    size: 180,
    meta: { headerTitle: 'Product code' },
  },
  {
    id: 'qty',
    accessorFn: (row) => row.qty ?? '',
    header: ({ column }) => (
      <DataGridColumnHeader title="Qty" column={column} className="justify-end" />
    ),
    cell: ({ row }) => <span className="block text-right">{row.original.qty ?? '-'}</span>,
    size: 64,
    meta: { headerTitle: 'Qty' },
  },
];

function FulfilmentItemsPopover({ order }: { order: FulfilmentOrder }) {
  const itemCount = order.items.length;
  const rows = useMemo<FulfilmentItemRow[]>(
    () => order.items.map((item, i) => ({ ...item, _rowId: `${item.product_code}-${i}` })),
    [order.items],
  );
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`View items for delivery order ${order.order_number}`}
          title="View delivery order items"
        >
          <PackageOpen className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        <div className="border-b px-3 py-2">
          <p className="text-sm font-semibold truncate" title={order.order_number}>
            {order.order_number}
          </p>
          <p className="text-xs text-muted-foreground">
            {itemCount} item{itemCount === 1 ? '' : 's'}
          </p>
        </div>
        {itemCount === 0 ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">
            No line items recorded.
          </p>
        ) : (
          <PanelDataGrid<FulfilmentItemRow>
            columns={ITEM_COLUMNS}
            rows={rows}
            getRowId={(row) => row._rowId}
            listingKey={`complaint_management.complaints.view::fulfilment-items-${order.order_id}`}
            emptyTitle="No line items recorded."
            scrollerMaxHeight="16rem"
          />
        )}
      </PopoverContent>
    </Popover>
  );
}

const ORDER_COLUMNS: ColumnDef<FulfilmentOrder>[] = [
  {
    id: 'order_number',
    accessorFn: (row) => row.order_number,
    header: ({ column }) => <DataGridColumnHeader title="DO Number" column={column} />,
    cell: ({ row }) => (
      <Link
        href={`/order-management/orders/${row.original.order_id}`}
        className="block max-w-[14rem] truncate font-medium text-primary hover:underline underline-offset-2"
        title={row.original.order_number}
        onClick={(e) => e.stopPropagation()}
      >
        {row.original.order_number}
      </Link>
    ),
    size: 200,
    meta: { headerTitle: 'DO Number' },
  },
  {
    id: 'status',
    accessorFn: (row) => row.status,
    header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
    cell: ({ row }) => <Badge status={row.original.status}>{row.original.status_label}</Badge>,
    size: 140,
    meta: { headerTitle: 'Status' },
  },
  {
    id: 'actual_delivery_date',
    accessorFn: (row) => row.actual_delivery_date ?? '',
    header: ({ column }) => <DataGridColumnHeader title="Delivery Date" column={column} />,
    cell: ({ row }) =>
      row.original.actual_delivery_date
        ? formatDate(new Date(row.original.actual_delivery_date))
        : '-',
    size: 150,
    meta: { headerTitle: 'Delivery Date' },
  },
  {
    id: 'items',
    header: ({ column }) => <DataGridColumnHeader title="Items" column={column} />,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <FulfilmentItemsPopover order={row.original} />
      </div>
    ),
    size: 90,
    enableResizing: false,
    meta: { headerTitle: 'Items' },
  },
];

export default function ComplaintFulfilmentOrdersSection({
  complaintId,
}: ComplaintFulfilmentOrdersSectionProps) {
  const { data: orders, isLoading, error, refetch } = useComplaintFulfilmentOrders(complaintId);

  return (
    <PanelDataGrid<FulfilmentOrder>
      title="Fulfilment Delivery Orders"
      columns={ORDER_COLUMNS}
      rows={orders ?? []}
      getRowId={(row) => row.order_id}
      listingKey="complaint_management.complaints.view::fulfilment-orders"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      emptyTitle="No replacement delivery order linked yet."
    />
  );
}
