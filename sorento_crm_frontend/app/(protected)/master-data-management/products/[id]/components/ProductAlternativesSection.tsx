'use client';

import { useState } from 'react';
import Link from 'next/link';
import { LoaderCircleIcon, Plus, RotateCcw, Link2Off } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useAddProductAlternative,
  useProductAlternatives,
  useProductAlternativeUnlink,
  useResetProductAlternativesAuto,
} from '../../hooks/useProductAlternatives';
import ProductVariantPickerDialog from './ProductVariantPickerDialog';

const PRODUCT_BASE_PATH = '/master-data-management/products';

export default function ProductAlternativesSection({ productId }: { productId: string }) {
  const canEdit = useHasPermission('master_data.products.edit');
  const { data, isLoading, isError } = useProductAlternatives(productId);
  const add = useAddProductAlternative(productId);
  const reset = useResetProductAlternativesAuto(productId);
  const unlink = useProductAlternativeUnlink(productId);
  const [pickerOpen, setPickerOpen] = useState(false);

  const rows = data?.alternatives ?? [];
  const excludeIds = [productId, ...rows.map((r) => r.alternative_product_id)];

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">Alternatives{rows.length ? ` (${rows.length})` : ''}</h3>
        {canEdit && (
          <div className="ms-auto flex flex-wrap items-center gap-2">
            {data?.alternatives_manual && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => reset.run({ id: productId, subject: 'alternatives' })}
                disabled={reset.isPending}
              >
                <RotateCcw className="size-4" />
                Reset to auto
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <Plus className="size-4" />
              Add alternative
            </Button>
          </div>
        )}
      </div>
      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isError ? (
        <p className="text-sm text-destructive">
          Could not load alternatives. Try reloading the page.
        </p>
      ) : rows.length === 0 ? (
        <div className="rounded-md border border-dashed px-3 py-6 text-center">
          <p className="text-sm font-medium">No alternatives</p>
          <p className="text-sm text-muted-foreground">None linked yet.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((row) => {
            const unlinking = unlink.targetId === row.id && unlink.isPending;
            return (
              <div
                key={row.id}
                className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 sm:flex-nowrap"
              >
                <div className="min-w-0 flex-1">
                  <Link
                    href={`${PRODUCT_BASE_PATH}/${row.alternative_product_id}`}
                    className="block truncate font-medium hover:underline"
                    title={row.product_code}
                  >
                    {row.product_code}
                  </Link>
                  <div
                    className="truncate text-xs text-muted-foreground"
                    title={row.product_name}
                  >
                    {row.product_name}
                  </div>
                </div>
                {row.is_discontinued && (
                  <Badge variant="destructive" appearance="light" size="sm">
                    Discontinued
                  </Badge>
                )}
                <Badge
                  variant={row.source === 'manual' ? 'warning' : 'secondary'}
                  size="sm"
                >
                  {row.source === 'manual' ? 'Manual' : 'Auto'}
                </Badge>
                {canEdit && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0"
                    disabled={unlinking}
                    onClick={() =>
                      unlink.run({ id: row.id, subject: `${row.product_code} alternative` })
                    }
                    aria-label={`Unlink ${row.product_code}`}
                  >
                    {unlinking ? (
                      <LoaderCircleIcon className="size-4 animate-spin" />
                    ) : (
                      <Link2Off className="size-4" />
                    )}
                    Unlink
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
      <ProductVariantPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        title="Add alternative"
        description="Pick a product."
        confirmLabel="Add alternative"
        excludeIds={excludeIds}
        submitting={add.isPending}
        onConfirm={(id) =>
          add.mutate(id, { onSuccess: () => setPickerOpen(false) })
        }
      />
    </div>
  );
}
