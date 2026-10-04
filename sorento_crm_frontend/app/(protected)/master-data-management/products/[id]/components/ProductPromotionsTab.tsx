'use client';

import { useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { Eye } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { getPromotionsByProductId } from '@/app/(protected)/marketing-management/promotions/services/promotionService';
import type { PromotionProduct } from '@/app/(protected)/marketing-management/promotions/types/promotion.types';

interface ProductPromotionsTabProps {
  productId: string;
  listPrice?: number | null;
}

const fmtMyr = (v: number | null | undefined) =>
  v == null
    ? '-'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'MYR' }).format(Number(v));

function formatDiscount(line: PromotionProduct, listPrice?: number | null): string {
  const pct = line.discount_percent != null ? Number(line.discount_percent) : null;
  if (pct != null && pct > 0) return `${pct.toFixed(2)}%`;

  const amt = line.discount_amount != null ? Number(line.discount_amount) : null;
  if (amt != null && amt > 0) return fmtMyr(amt);

  const sell = line.promotion_price != null ? Number(line.promotion_price) : null;
  const lp = listPrice != null ? Number(listPrice) : null;
  if (sell != null && lp != null && lp > 0 && sell < lp) {
    const derived = ((lp - sell) / lp) * 100;
    return `${derived.toFixed(2)}%`;
  }
  return '-';
}

export default function ProductPromotionsTab({ productId, listPrice }: ProductPromotionsTabProps) {
  const router = useRouter();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['product-promotions', productId],
    queryFn: () => getPromotionsByProductId(productId),
    enabled: !!productId,
  });

  const lines = data ?? [];

  const columns = useMemo<ColumnDef<PromotionProduct>[]>(
    () => [
      {
        id: 'promotion_code',
        // Faithful to the original: this column has always shown the
        // promotion's description too, not a code - it is not fixed here.
        accessorFn: (row) => row.promotion?.description?.trim() || '-',
        header: ({ column }) => <DataGridColumnHeader title="Promotion Code" column={column} />,
        cell: ({ row }) => {
          const promo = row.original.promotion;
          const desc = promo?.description?.trim() || '-';
          return (
            <div className="flex items-center gap-2">
              <span className="font-medium">{desc}</span>
              {promo?.is_active === false && <Badge variant="secondary">Inactive</Badge>}
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Promotion Code' },
      },
      {
        id: 'description',
        accessorFn: (row) => row.promotion?.description?.trim() || '-',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => {
          const desc = row.original.promotion?.description?.trim() || '-';
          return (
            <span className="block truncate" title={desc}>
              {desc}
            </span>
          );
        },
        size: 280,
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'list_price',
        header: ({ column }) => (
          <DataGridColumnHeader title="List Price" column={column} className="justify-end" />
        ),
        cell: () => <span className="block text-right">{fmtMyr(listPrice)}</span>,
        size: 130,
        meta: { headerTitle: 'List Price' },
      },
      {
        id: 'discount',
        header: ({ column }) => (
          <DataGridColumnHeader title="Discount" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-right">{formatDiscount(row.original, listPrice)}</span>
        ),
        size: 110,
        meta: { headerTitle: 'Discount' },
      },
      {
        id: 'selling_price',
        header: ({ column }) => (
          <DataGridColumnHeader title="Selling Price" column={column} className="justify-end" />
        ),
        cell: ({ row }) => {
          const sell =
            row.original.promotion_price != null ? Number(row.original.promotion_price) : null;
          return <span className="block text-right">{fmtMyr(sell)}</span>;
        },
        size: 150,
        meta: { headerTitle: 'Selling Price' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Action</span>,
        cell: ({ row }) => {
          const promo = row.original.promotion;
          return (
            <div className="flex justify-end">
              <Button
                size="sm"
                variant="outline"
                disabled={!promo?.id}
                onClick={(e) => {
                  e.stopPropagation();
                  if (promo?.id) router.push(`/marketing-management/promotions/${promo.id}`);
                }}
              >
                <Eye className="size-4" />
                View
              </Button>
            </div>
          );
        },
        size: 100,
        enableResizing: false,
        meta: { headerTitle: 'Action' },
      },
    ],
    [listPrice, router],
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Promotions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <PanelDataGrid<PromotionProduct>
      title={
        <div className="space-y-0.5">
          <div>Promotions</div>
          <p className="text-sm font-normal text-muted-foreground">
            Promotions that include this product.
          </p>
        </div>
      }
      columns={columns}
      error={error}
      onRetry={() => void refetch()}
      rows={lines}
      getRowId={(row) => row.id}
      listingKey="master_data.products.view::promotions"
      emptyTitle="No promotions linked to this product."
      emptyBody="Add this product to a promotion from Marketing → Promotions."
    />
  );
}
