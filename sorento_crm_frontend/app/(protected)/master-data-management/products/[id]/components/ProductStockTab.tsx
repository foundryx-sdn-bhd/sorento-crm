'use client';

import { useMemo } from 'react';
import { useRouter } from 'next/navigation';
import type { ColumnDef } from '@tanstack/react-table';
import { ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { useStockBalance } from '@/app/(protected)/inventory-management/stock/hooks/useStock';
import type { Stock } from '@/app/(protected)/inventory-management/stock/types/stock.types';

interface ProductStockTabProps {
  productId: string;
}

const STATUS_VARIANTS: Record<string, 'destructive' | 'warning' | 'success' | 'secondary'> = {
  low: 'warning',
  critical: 'destructive',
  normal: 'success',
  overstock: 'secondary',
};

/** Same thresholds the raw table computed inline, per row, against its own reorder level. */
function stockStatus(stock: Stock): 'low' | 'critical' | 'normal' | 'overstock' {
  const available = stock.quantity_available ?? stock.available ?? 0;
  const reorderLevel = stock.product?.reorder_level ?? 0;
  if (available <= 0) return 'critical';
  if (available <= reorderLevel * 0.5) return 'critical';
  if (available <= reorderLevel) return 'low';
  if (available > reorderLevel * 2) return 'overstock';
  return 'normal';
}

export default function ProductStockTab({ productId }: ProductStockTabProps) {
  const router = useRouter();
  const { data, isLoading, error, refetch } = useStockBalance({
    pageIndex: 0,
    pageSize: 100,
    product_id: productId,
    sorting: [],
  });

  const stockItems = (data?.data || []) as Stock[];

  const columns = useMemo<ColumnDef<Stock>[]>(
    () => [
      {
        id: 'warehouse',
        accessorFn: (row) => row.warehouse?.warehouse_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => (
          <span className="font-medium">
            {row.original.warehouse?.warehouse_name || 'Unknown Warehouse'}
          </span>
        ),
        size: 220,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        id: 'available',
        accessorFn: (row) => row.quantity_available ?? row.available ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Available" column={column} />,
        size: 110,
        meta: { headerTitle: 'Available' },
      },
      {
        id: 'reserved',
        accessorFn: (row) => row.quantity_reserved ?? row.reserved_quantity ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Reserved" column={column} />,
        size: 110,
        meta: { headerTitle: 'Reserved' },
      },
      {
        id: 'total',
        accessorFn: (row) => row.quantity_on_hand ?? row.quantity ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
        size: 110,
        meta: { headerTitle: 'Total' },
      },
      {
        id: 'status',
        accessorFn: (row) => stockStatus(row),
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const status = stockStatus(row.original);
          return (
            <Badge variant={STATUS_VARIANTS[status] || 'secondary'}>
              {status.charAt(0).toUpperCase() + status.slice(1)}
            </Badge>
          );
        },
        size: 110,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'chevron',
        header: () => <span className="sr-only">Open</span>,
        cell: () => <ChevronRight className="size-4 text-muted-foreground" />,
        size: 40,
        enableResizing: false,
        meta: { headerTitle: 'Open' },
      },
    ],
    [],
  );

  return (
    <>
      <PanelDataGrid<Stock>
        title={
          <div className="space-y-0.5">
            <div>Stock Information</div>
            <p className="text-sm font-normal text-muted-foreground">
              {stockItems.length === 0
                ? 'Stock levels across warehouses. Ledger entries available from stock detail.'
                : 'Warehouse quantities and ledger. Click a row to view ledger entries.'}
            </p>
          </div>
        }
        columns={columns}
        rows={stockItems}
        getRowId={(row) => row.id}
        listingKey="master_data.products.view::stock"
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        onRowClick={(row) =>
          row.product_id &&
          row.warehouse_id &&
          router.push(`/inventory-management/stock/${row.product_id}/${row.warehouse_id}`)
        }
        emptyTitle="No stock information available for this product."
        emptyBody="Stock records will appear here once inventory is added."
      />
      {data?.pagination && data.pagination.total > 100 && (
        <p className="mt-3 text-sm text-muted-foreground text-center">
          Showing 100 of {data.pagination.total} warehouses
        </p>
      )}
    </>
  );
}
