'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Link2Off, LoaderCircleIcon, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Skeleton } from '@/components/ui/skeleton';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useAddProductCompatibility,
  useProductCompatibility,
  useProductCompatibilityUnlink,
} from '../../hooks/useProductCompatibility';
import type {
  CompatibilityKind,
  CompatibilityRole,
  ProductCompatibilityRow,
} from '../../types/productCompatibility.types';
import ProductVariantPickerDialog from './ProductVariantPickerDialog';
import { distinctName } from '@/lib/product-display';

const PRODUCT_BASE_PATH = '/master-data-management/products';

const KINDS: CompatibilityKind[] = ['seat_cover', 'mirror', 'shelf'];

/** Link type picker label and the two nouns of a kind: accessory end, host end. */
const KIND_LABELS: Record<CompatibilityKind, string> = {
  seat_cover: 'Seat cover / WC',
  mirror: 'Mirror / cabinet',
  shelf: 'Shelf / cabinet',
};
const KIND_NOUNS: Record<CompatibilityKind, [string, string]> = {
  seat_cover: ['Seat cover', 'WC'],
  mirror: ['Mirror', 'Cabinet'],
  shelf: ['Shelf', 'Cabinet'],
};
const COVERS_HEADINGS: Record<CompatibilityKind, string> = {
  seat_cover: 'Seat covers that fit',
  mirror: 'Mirrors that fit',
  shelf: 'Shelves that fit',
};

/** A seat cover code (`SRTSC...`) or a seat cover sold under a WC code (`...-SC`). */
function looksLikeSeatCover(code: string | undefined): boolean {
  if (!code) return false;
  const upper = code.toUpperCase().replace(/\s/g, '');
  return upper.startsWith('SRTSC') || upper.split('-').slice(1).includes('SC');
}

function LinkRows({
  rows,
  canEdit,
  unlink,
}: {
  rows: ProductCompatibilityRow[];
  canEdit: boolean;
  unlink: ReturnType<typeof useProductCompatibilityUnlink>;
}) {
  return (
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
                href={`${PRODUCT_BASE_PATH}/${row.product_id}`}
                className="block truncate font-medium hover:underline"
                title={row.product_code}
              >
                {row.product_code}
              </Link>
              {distinctName(row.product_code, row.product_name) ? (
                <div
                  className="truncate text-xs text-muted-foreground"
                  title={row.product_name}
                >
                  {row.product_name}
                </div>
              ) : null}
            </div>
            {row.is_discontinued && (
              <Badge variant="destructive" appearance="light" size="sm">
                Discontinued
              </Badge>
            )}
            <Badge
              variant={row.source === 'flyer' ? 'primary' : 'warning'}
              appearance="light"
              size="sm"
            >
              {row.source === 'flyer' ? 'Flyer' : 'Manual'}
            </Badge>
            {canEdit && (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0"
                disabled={unlinking}
                onClick={() =>
                  unlink.run({
                    id: row.id,
                    subject: `${row.product_code} link`,
                  })
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
  );
}

function EmptyList({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="rounded-md border border-dashed px-3 py-4 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="text-sm text-muted-foreground">{hint}</p>
    </div>
  );
}

export default function ProductCompatibilitySection({
  productId,
  productCode,
}: {
  productId: string;
  productCode?: string;
}) {
  const canEdit = useHasPermission('master_data.products.edit');
  const { data, isLoading, isError } = useProductCompatibility(productId);
  const add = useAddProductCompatibility(productId);
  const unlink = useProductCompatibilityUnlink();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [kind, setKind] = useState<CompatibilityKind>('seat_cover');
  const [otherRole, setOtherRole] = useState<CompatibilityRole>('accessory');

  const covers = data?.covers ?? [];
  const fits = data?.fits ?? [];
  const total = covers.length + fits.length;
  const excludeIds = [
    productId,
    ...covers.map((r) => r.product_id),
    ...fits.map((r) => r.product_id),
  ];

  // Only seat cover links: today's layout, both groups with their empty states.
  const seatCoverOnly = [...covers, ...fits].every(
    (r) => r.kind === 'seat_cover',
  );
  const coverGroups = KINDS.map((k) => ({
    kind: k,
    rows: covers.filter((r) => r.kind === k),
  })).filter((g) => (seatCoverOnly ? g.kind === 'seat_cover' : g.rows.length));
  const fitsGroups = [
    {
      heading: 'Fits these WCs',
      rows: fits.filter((r) => r.kind === 'seat_cover'),
    },
    {
      heading: 'Fits these cabinets',
      rows: fits.filter((r) => r.kind !== 'seat_cover'),
    },
  ].filter((g, i) => (seatCoverOnly ? i === 0 : g.rows.length));

  function openPicker() {
    // This product's side decides the other's: whatever it already fits is a host
    // link away, a seat cover code is linked to WCs.
    if (fits.length > 0) {
      setKind(fits[0].kind);
      setOtherRole('host');
    } else if (covers.length > 0) {
      setKind(covers[0].kind);
      setOtherRole('accessory');
    } else {
      setKind('seat_cover');
      setOtherRole(looksLikeSeatCover(productCode) ? 'host' : 'accessory');
    }
    setPickerOpen(true);
  }

  return (
    <div className="mt-6">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">
          Compatible{total ? ` (${total})` : ''}
        </h3>
        {canEdit && (
          <Button
            variant="outline"
            size="sm"
            className="ms-auto"
            onClick={openPicker}
          >
            <Plus className="size-4" />
            Add compatible
          </Button>
        )}
      </div>
      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isError ? (
        <p className="text-sm text-destructive">
          Could not load compatible products. Try reloading the page.
        </p>
      ) : (
        <div className="space-y-4">
          {coverGroups.map((g) => (
            <div key={g.kind}>
              <h4 className="mb-2 text-sm font-medium text-muted-foreground">
                {COVERS_HEADINGS[g.kind]}
              </h4>
              {g.rows.length ? (
                <LinkRows rows={g.rows} canEdit={canEdit} unlink={unlink} />
              ) : (
                <EmptyList
                  title="No seat covers"
                  hint="None linked to this product."
                />
              )}
            </div>
          ))}
          {fitsGroups.map((g) => (
            <div key={g.heading}>
              <h4 className="mb-2 text-sm font-medium text-muted-foreground">
                {g.heading}
              </h4>
              {g.rows.length ? (
                <LinkRows rows={g.rows} canEdit={canEdit} unlink={unlink} />
              ) : (
                <EmptyList
                  title="No WCs"
                  hint="This product is not linked as a seat cover."
                />
              )}
            </div>
          ))}
        </div>
      )}
      <ProductVariantPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        title="Add compatible"
        description="Pick the other product."
        confirmLabel="Add"
        excludeIds={excludeIds}
        submitting={add.isPending}
        onConfirm={(id) =>
          add.mutate(
            { otherProductId: id, otherRole, kind },
            { onSuccess: () => setPickerOpen(false) },
          )
        }
      >
        <div className="space-y-4 py-1">
          <div className="space-y-2">
            <Label>Link type</Label>
            <RadioGroup
              value={kind}
              onValueChange={(value) => setKind(value as CompatibilityKind)}
              className="flex flex-wrap gap-x-6 gap-y-2"
            >
              {KINDS.map((k) => (
                <label key={k} className="flex items-center gap-2 text-sm">
                  <RadioGroupItem value={k} aria-label={KIND_LABELS[k]} />
                  {KIND_LABELS[k]}
                </label>
              ))}
            </RadioGroup>
          </div>
          <div className="space-y-2">
            <Label>The other product is a</Label>
            <RadioGroup
              value={otherRole}
              onValueChange={(value) =>
                setOtherRole(value as CompatibilityRole)
              }
              className="flex flex-wrap gap-x-6 gap-y-2"
            >
              {(['accessory', 'host'] as const).map((role, i) => (
                <label key={role} className="flex items-center gap-2 text-sm">
                  <RadioGroupItem
                    value={role}
                    aria-label={KIND_NOUNS[kind][i]}
                  />
                  {KIND_NOUNS[kind][i]}
                </label>
              ))}
            </RadioGroup>
          </div>
        </div>
      </ProductVariantPickerDialog>
    </div>
  );
}
