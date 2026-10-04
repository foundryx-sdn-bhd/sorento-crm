'use client';

import { use } from 'react';
import { useRouter } from 'next/navigation';
import RecordNavigation from '@/components/common/RecordNavigation';
import DetailActions from '@/components/common/DetailActions';
import BackToList from '@/components/common/BackToList';
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  PaginationState,
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardContent, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { getStockDetail, getStockLedgerByStock, exportStockBalance } from '../../services/stockService';
import type { Stock } from '../../types/stock.types';
import {
  stockLedgerTypeLabel,
  type StockLedgerEntry,
} from '../../../stock-ledger/types/stockLedger.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

type StockDetailPageProps = {
  params: Promise<{ productId: string; warehouseId: string }>;
};

export default function StockDetailPage({ params }: StockDetailPageProps) {
  const { productId, warehouseId } = use(params);
  const router = useRouter();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 20 });

  const stockQuery = useQuery({
    queryKey: ['stock-detail', productId, warehouseId],
    queryFn: () => getStockDetail(productId, warehouseId),
    staleTime: 1000 * 60 * 5,
    retry: 1,
  });

  const ledgerQuery = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['stock-ledger-detail', productId, warehouseId, pagination.pageIndex, pagination.pageSize],
    queryFn: () => getStockLedgerByStock(productId, warehouseId, pagination),
    staleTime: 1000 * 60 * 5,
    retry: 1,
  });

  // Get navigation data for next/previous using export endpoint (no pagination limit)
  const navigationQuery = useQuery({
    queryKey: ['stock-navigation'],
    queryFn: () => exportStockBalance(),
    staleTime: 1000 * 60 * 5,
    retry: 1,
  });
  const navigationItems = navigationQuery.data ?? [];
  // Sort navigation items by product_code for consistent ordering
  const sortedNavigationItems = useMemo(() => {
    return [...navigationItems].sort((a, b) => {
      const codeA = a.product?.product_code || '';
      const codeB = b.product?.product_code || '';
      return codeA.localeCompare(codeB);
    });
  }, [navigationItems]);
  const stock = stockQuery.data as Stock | null;
  const navigationItemsForRecordNav = useMemo(
    () => sortedNavigationItems.map((s) => ({ id: `${s.product_id}/${s.warehouse_id}` })),
    [sortedNavigationItems],
  );
  const currentRecordId = `${productId}/${warehouseId}`;
  // A stock record is a product AND a warehouse, so its "id" is the pair. The
  // pager is presentational here: the walk is this in-memory balance, not a
  // paged list URL, so there is no list query to rebuild.
  const stockIndex = navigationItemsForRecordNav.findIndex(
    (item) => item.id === currentRecordId,
  );
  const goToStock = (item: { id: string } | undefined) => {
    if (item) router.push(`/inventory-management/stock/${item.id}`);
  };

  const columns = useMemo<ColumnDef<StockLedgerEntry>[]>(
    () => [
      {
        accessorKey: 'transaction_type',
        header: ({ column }) => <DataGridColumnHeader title="Type" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary">
            {stockLedgerTypeLabel(row.original.transaction_type)}
          </Badge>
        ),
        size: 140,
        meta: { skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'quantity_change',
        header: ({ column }) => <DataGridColumnHeader title="Change" column={column} />,
        cell: ({ row }) => {
          const value = row.original.quantity_change;
          const isPositive = value > 0;
          return (
            <span className={isPositive ? 'text-emerald-600 font-medium' : 'text-red-600 font-medium'}>
              {isPositive ? `+${value}` : value}
            </span>
          );
        },
        size: 120,
      },
      {
        accessorKey: 'previous_quantity',
        header: ({ column }) => <DataGridColumnHeader title="Previous" column={column} />,
        size: 120,
      },
      {
        accessorKey: 'new_quantity',
        header: ({ column }) => <DataGridColumnHeader title="New" column={column} />,
        size: 120,
      },
      {
        accessorKey: 'created_by_name',
        header: ({ column }) => <DataGridColumnHeader title="Created By" column={column} />,
        cell: ({ row }) => row.original.created_by_name || row.original.created_by || '-',
        size: 180,
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.created_at),
        size: 200,
      },
      {
        accessorKey: 'notes',
        header: ({ column }) => <DataGridColumnHeader title="Notes" column={column} />,
        cell: ({ row }) => row.original.notes || '-',
        size: 200,
      },
    ],
    [],
  );

  const recordTitle = stock
    ? [
        stock.product?.product_code || stock.product?.product_name,
        stock.warehouse?.warehouse_name,
      ]
        .filter(Boolean)
        .join(' at ') || 'Stock Balance'
    : 'Stock Balance';

  const table = useReactTable({
    columns,
    data: ledgerQuery.data?.data || [],
    pageCount: Math.ceil((ledgerQuery.data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
  });

  if (stockQuery.isLoading) {
    return (
      <>
        <Container>
          <PageHeader
            title="Stock"
            actions={
              <BackToList listPath="/inventory-management/stock" label="Back to stock" />
            }
          />
        </Container>
        <Container>
          <div className="space-y-6">
            <Skeleton className="h-10 w-64" />
            <Skeleton className="h-96 w-full" />
          </div>
        </Container>
      </>
    );
  }

  if (!stock) {
    return (
      <>
        <Container>
          <PageHeader
            title="Stock"
            actions={
              <BackToList listPath="/inventory-management/stock" label="Back to stock" />
            }
          />
        </Container>
        <Container>
          <div className="text-center py-12">
            <p className="text-muted-foreground">Stock record not found</p>
            <div className="mt-4 flex justify-center">
              <BackToList listPath="/inventory-management/stock" label="Back to stock" />
            </div>
          </div>
        </Container>
      </>
    );
  }

  return (
    <>
      <Container>
        {/* The title names the record the way a reader would: the product's own
            code and the warehouse it sits in. Neither is an id (S5-05). */}
        <PageHeader
          title={recordTitle}
          crumbTitle={recordTitle}
          actions={
            <>
              {/* One Back, and nothing else on this row (D6, S3-01). The pager moved
                  down onto the record card, where every other detail page keeps it. */}
              <BackToList listPath="/inventory-management/stock" label="Back to stock" />
            </>
          }
        />
      </Container>

      <Container>
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-3">
                    <h2 className="text-2xl font-bold break-words min-w-0">
                      {stock.product?.product_code || '-'}
                    </h2>
                    {stock.product?.product_name && (
                      <Badge variant="secondary">
                        {stock.product.product_name}
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {stock.warehouse?.warehouse_name || 'No warehouse'} • Available: {stock.quantity_available ?? 0}
                  </p>
                </div>
                {/* Pager, gear, primary (D6). This record has neither a gear nor a
                    primary: a stock balance is derived, so there is nothing to edit
                    or delete here. The whole set it walks is already in memory, so
                    it hands over a presentational RecordNavigation. */}
                <DetailActions
                  pagerNode={
                    <RecordNavigation
                      index={stockIndex >= 0 ? stockIndex + 1 : null}
                      total={navigationItemsForRecordNav.length}
                      hasPrevious={stockIndex > 0}
                      hasNext={
                        stockIndex >= 0 &&
                        stockIndex < navigationItemsForRecordNav.length - 1
                      }
                      onPrevious={() =>
                        goToStock(navigationItemsForRecordNav[stockIndex - 1])
                      }
                      onNext={() => goToStock(navigationItemsForRecordNav[stockIndex + 1])}
                      ariaLabel="stock"
                    />
                  }
                />
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 text-sm">
                <div>
                  <div className="text-muted-foreground">Product</div>
                  <div className="font-medium">{stock.product?.product_name || '-'}</div>
                  <div className="text-muted-foreground">{stock.product?.product_code || '-'}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Warehouse</div>
                  <div className="font-medium">{stock.warehouse?.warehouse_name || '-'}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Quantities</div>
                  <div className="font-medium">
                    On Hand: {stock.quantity_on_hand ?? 0} | Available: {stock.quantity_available ?? 0}
                  </div>
                  <div className="text-muted-foreground">
                    Reserved: {stock.quantity_reserved ?? 0} | Damaged: {stock.quantity_damaged ?? 0}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          <DataGrid
            table={table}
            recordCount={ledgerQuery.data?.pagination.total || 0}
            isLoading={ledgerQuery.isLoading}
            error={ledgerQuery.error}
            onRetry={() => void ledgerQuery.refetch()}
            isPlaceholderData={ledgerQuery.isPlaceholderData}
          >
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <div className="text-lg font-semibold">Stock Ledger</div>
              </CardHeader>
              <CardTable>
                <DataGridTable />
              </CardTable>
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </Card>
          </DataGrid>
        </div>
      </Container>
    </>
  );
}
