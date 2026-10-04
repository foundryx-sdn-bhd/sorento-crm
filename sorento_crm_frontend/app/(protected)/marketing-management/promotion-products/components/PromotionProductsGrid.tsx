'use client';

import { useMemo } from 'react';
import {
  ColumnDef,
  useReactTable,
  getCoreRowModel,
} from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Columns3 } from 'lucide-react';
import { usePromotionProducts } from '../../promotions/hooks/usePromotions';
import type { PromotionProduct } from '../../promotions/types/promotion.types';

interface PromotionProductsGridProps {
  promotionId: string;
}

export default function PromotionProductsGrid({ promotionId }: PromotionProductsGridProps) {
  const { data: products, isLoading, error, refetch } = usePromotionProducts(promotionId);

  const columns = useMemo<ColumnDef<PromotionProduct>[]>(
    () => [
      {
        accessorKey: 'product.product_code',
        header: 'Product Code',
        cell: ({ row }) => row.original.product?.product_code || '-',
      },
      {
        accessorKey: 'product.product_name',
        header: 'Product Name',
        cell: ({ row }) => row.original.product?.product_name || '-',
      },
      {
        accessorKey: 'product.list_price',
        header: 'List Price',
        cell: ({ row }) => {
          const price = row.original.product?.list_price || 0;
          return new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(price);
        },
      },
      {
        accessorKey: 'promotion_price',
        header: 'Promo Price',
        cell: ({ row }) => {
          const price = row.original.promotion_price || row.original.product?.list_price || 0;
          return new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(price);
        },
      },
      {
        accessorKey: 'discount_amount',
        header: 'Discount',
        cell: ({ row }) => {
          const discount = row.original.discount_amount || 0;
          return discount > 0 ? (
            <Badge variant="success">{new Intl.NumberFormat('en-MY', { style: 'currency', currency: 'MYR' }).format(discount)}</Badge>
          ) : (
            '-'
          );
        },
      },
      {
        accessorKey: 'discount_percent',
        header: 'Discount %',
        cell: ({ row }) => {
          const percent = row.original.discount_percent || 0;
          return percent > 0 ? <Badge variant="info">{percent}%</Badge> : '-';
        },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: products || [],
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle>Products in Promotion</CardTitle>
        <DataGridColumnVisibility
          table={table}
          trigger={
            <Button variant="outline" size="sm" className="gap-1">
              <Columns3 className="size-4" />
              Columns
            </Button>
          }
        />
      </CardHeader>
      <CardContent>
        <DataGrid
          table={table}
          recordCount={products?.length || 0}
          isLoading={isLoading}
          error={error}
          onRetry={() => void refetch()}
          tableLayout={{ columnsVisibility: true }}
        >
          <DataGridTable />
        </DataGrid>
      </CardContent>
    </Card>
  );
}
